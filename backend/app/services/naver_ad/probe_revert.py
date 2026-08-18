# probe_revert.py — probe_revert SA (D-NAO-58 CD3, docs/PLAN_naver-ad-click-discovery.md)
# 역할: CD2가 능동 상향한 클릭 탐침(approval_source=probe_op)이 성적을 냈는지 사후 판정하고,
#   근거 없이 올려둔 입찰가는 before_value로 원위치(되돌림)한다. 두 단계:
#   ①Stage 1 실시간 출혈 밸브(run_bleed_valve, D-58-8) — 시간당 레인 말미에 당일 standing
#     probe의 소진이 정착창 시간당평균×3을 넘고(비용 급등) 당일 전환이 0이면 즉시 회수.
#     "돈 새는 걸 하루 내내 방치하지 않는다"의 안전판(보수적 되돌림 — 애매하면 hold, 확실한
#     출혈만 회수).
#     ★2026-08-18 수리(D-NAO-193, ref 72 §2-①): "당일 전환 0" 판정이 **구조적으로 항상 참**
#       이었다 — 옛 `_conv_direct_today`가 **D−1까지만 적재되는** naver_ad_daily에서
#       `ad_date == 오늘`을 읽어 언제나 0을 돌려줬다(라이브 확인 2026-08-18: 그 테이블의
#       MAX(ad_date)=08-17, 당일 행 0건). 즉 밸브는 19일간 "비용만 보고" 회수하고 있었다.
#       → **2신호 2단 판정**(_today_conversion_verdict)으로 교체: 주신호는 매시 적재되는
#       naver_adgroup_hourly_today의 당일 광고 전환(광고 귀속 신호), 보강은 그 그룹이 광고하는
#       상품들의 당일 매출(상한 프록시). 어휘는 «확정 0 / 잠정 0 / 전환 있음 / 미관측» 4값.
#   ②Stage 2 성과 정산 판정(run_settlement, D-58-9) — 매일 08:55, D+1 정산 완료 데이터로
#     standing probe를 유지(kept)/되돌림(reverted)/보류(deferred) 판정. 선행지표(즉시구매 +
#     장바구니×전환율, probe_signal SA)와 보정 ROAS로 "클릭 살았나·전환 났나"를 본다.
#
# 되돌림 = NaverProposal(bid_down, target_bid=원래_before_bid, approval_source=revert_op)을
#   생성해 naver_execution_harness.execute()로 집행(초크포인트 유지·가드레일 전량 통과·킬스위치
#   3중 방어). 되돌림 자체가 SA를 직접 호출하지 않고 harness를 거친다(원칙18-7). 상태는 전부
#   change_log에서 파생(마이그레이션 불요) — probe의 executed_change_log_id → 그 change_log의
#   before/after bidAmt로 "얼마를→얼마로 올렸나"를 읽는다.
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import (
    NaverAdDaily,
    NaverAdgroupHourlyToday,
    NaverAdgroupProduct,
    NaverChangeLog,
    NaverProposal,
    OpsDiaryEntry,
)
from app.services.naver_ad import (
    auto_operator,
    cart_conversion_rate,
    diagnosis,
    diary,
    load_window,
    naver_execution_harness,
    probe_signal,
    today_proxy_revenue,
)
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.services.naver_sa_ad_fetcher import fetch_entity_hh24
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# standing probe 조회 창(created_at UTC 하한) — 이보다 오래된 탐침은 되돌림 대상에서 제외.
# 정산은 D+1~D+2에 판정되므로 7일이면 충분(diary_outcome._MAX_LOOKBACK_DAYS(60일)보다 짧게 —
# 되돌림은 시의성이 있어 오래된 미판정 탐침을 되살리지 않는다).
_LOOKBACK_DAYS = 7


def _mapped_product_ids(db: Session, adgroup_id: str) -> list[str]:
    """그 광고그룹이 광고하는 판매상품 id 전부(정렬·중복 제거, 없으면 빈 리스트) — 1쿼리."""
    rows = db.query(NaverAdgroupProduct.mall_product_id).filter(
        NaverAdgroupProduct.adgroup_id == adgroup_id
    ).all()
    return sorted({r[0] for r in rows if r[0]})


def _one_to_one_product(db: Session, adgroup_id: str) -> str | None:
    """adgroup_id가 정확히 하나의 mall_product_id에 매핑될 때만 그 상품 id 반환(다상품/미매핑은
    None). cart_conversion_rate._one_to_one_adgroup_product와 동일 판정(1:1만) — 그 함수는
    전체 스캔이라 단건 조회로 국소화(같은 규약, 결합 회피)."""
    pids = _mapped_product_ids(db, adgroup_id)
    return pids[0] if len(pids) == 1 else None


# ══════════════════ 당일 전환 판정 — 2신호 2단 (D-NAO-193, ref 72 §2-①) ══════════════════
#
# ★원리(ref 72 §1): 스마트스토어 상품 매출은 **광고 귀속 매출의 상한**이다. 그러므로
#   «상한이 0이면 참값도 0»은 추정이 아니라 산술이고, 반대로 «상한이 양수»는 광고가 벌었다는
#   증거가 되지 못한다. 그래서 이 프록시는 **손절(0쪽) 판정에만** 쓴다 — 확장 판정에 쓰면 안 된다.
#
# 신호 둘:
#   주신호 = naver_adgroup_hourly_today의 당일 광고 전환(conv_cnt). **광고 귀속 신호**이고
#            매시 수집된다(today_hourly_sweep). ⚠️conv_cnt는 전체 전환 건수라 즉시구매
#            (conv_direct)의 **상위집합**이다 → conv_cnt==0 ⇒ 즉시구매도 0(옛 조건보다 강하다).
#   보강   = 그 그룹이 광고하는 상품들의 당일 매출 합(today_proxy_revenue.revenue_by_product).
#            네이버 자체 당일 집계 지연에 의한 «거짓 0»을 걸러내는 교차 확인용.
#
# 4값 어휘:
#   확정 0(certain_zero)   광고 전환 0 ∧ 상품 매출 0 — 산술적으로 확실 → 즉시 회수 가능
#   잠정 0(tentative_zero) 광고 전환 0인데 상품은 팔렸다(또는 매핑이 없어 교차 확인 불가)
#                          → 거짓 0 가능 → **다음 시각 재확인 후 발화**(_bleed_watch)
#   전환 있음(positive)     광고 전환 > 0 → 출혈 아님(hold)
#   미관측(unknown)        주신호 행 자체가 없다(콜 상한 회전·스윕 실패·광고그룹 미상)
#                          → 근거 없이 회수하지 않는다(fail-open, skip)
_VERDICT_CERTAIN_ZERO = "certain_zero"
_VERDICT_TENTATIVE_ZERO = "tentative_zero"
_VERDICT_POSITIVE = "positive"
_VERDICT_UNKNOWN = "unknown"


def _probe_adgroup_id(probe: dict) -> str | None:
    """주신호(그룹 grain)를 조회할 광고그룹 id. keyword 탐침은 제안의 adgroup_id를 쓴다 —
    그룹 전환은 그 안의 키워드 전환의 **상위집합**이라 «그룹 0 ⇒ 키워드 0»이 성립한다
    (0쪽으로만 쓰는 이 판정에서 상위집합은 안전한 방향이다). 없으면 None."""
    if probe["target_type"] == "adgroup":
        return probe["target_id"] or None
    return probe.get("adgroup_id") or None


def _conv_from_curve(curve, *, before_hour: int) -> int | None:
    """★주신호 — 밸브가 **이미 받아 둔** hh24 곡선의 당일 전환 합. 없으면 None(미관측).

    ★왜 이것이 주신호인가(적대 리뷰 P1-1·P1-2, 2026-08-18): 이 곡선은
      fetch_entity_hh24(probe.target_id)의 결과라 **탐침과 정확히 같은 grain**(키워드 탐침이면
      키워드, 그룹 탐침이면 그룹)이고, 비용(cost_accrued)을 재는 **바로 그 자료·바로 그 창**이다.
      초판은 주신호를 naver_adgroup_hourly_today로 잡았는데 그 테이블은
      ①`today_hourly_sweep.build_today_targets`가 keyword grain을 버려 **파워링크 그룹 행이
        원리적으로 없고**(prod 실측: 어제 imp>0 키워드 유닛 1,439건 중 오늘 그룹 행 16건)
      ②스윕(:57)이 밸브(:20)보다 늦어 **최신 완결 시간이 한 칸 빠진다**(비용은 세고 전환은 안 셈).
      둘 다 «구조적 상수»를 만드는 부류라 이번에 고치는 결함과 같은 모양이었다.
    ★conv_cnt 키가 없는 행이 창 안에 하나라도 있으면 **0이 아니라 None**을 돌려준다 —
      «필드 없음»을 «전환 0»으로 읽는 것이 정확히 이 사고의 부류다."""
    rows = [h for h in (curve or []) if int(h["hour"]) < before_hour]
    if not rows:
        return None
    if any("conv_cnt" not in h for h in rows):
        return None
    return sum(int(h["conv_cnt"] or 0) for h in rows)


def _positive_conv_from_table(db: Session, adgroup_id: str, today, *, before_hour: int) -> bool:
    """폴백 — naver_adgroup_hourly_today에 당일 전환이 **있는지만** 본다(D-NAO-192 C-2 배선).

    ★«전환 있음»만 받아들이고 «0»은 미관측으로 취급하는 이유: 이 테이블은 곡선보다 한 시간
      뒤처지고(스윕 :57 vs 밸브 :20) 그룹 grain이라, 여기서 읽는 0은 «정말 0»이 아니라
      «아직 안 들어옴»일 수 있다. 뒤처진 0을 되돌림 근거로 쓰면 **거짓 정지**(계약이 감수하기로
      한 방향의 반대)가 된다. 반대로 여기서 전환이 보이면 그건 이미 확정된 사실이라 hold
      쪽으로만 쓰면 안전하다 — 폴백이 오직 «회수를 막는» 방향으로만 작동한다."""
    load_window.require_loaded(
        "naver_adgroup_hourly_today", today, today, reader="probe_revert._positive_conv_from_table"
    )
    conv = (
        db.query(sqlfunc.coalesce(sqlfunc.sum(NaverAdgroupHourlyToday.conv_cnt), 0))
        .filter(
            NaverAdgroupHourlyToday.ad_date == today,
            NaverAdgroupHourlyToday.adgroup_id == adgroup_id,
            NaverAdgroupHourlyToday.hour < before_hour,
        )
        .scalar()
    )
    return int(conv or 0) > 0


def _today_proxy_revenue(db: Session, adgroup_id: str, today) -> tuple[int | None, int]:
    """(그 그룹이 광고하는 상품들의 당일 매출 합, 상품 수). 매핑이 없으면 (None, 0).

    ★캠페인 균등 분할을 **하지 않는다**(today_proxy_revenue.build과 다른 점): 여기서 필요한 건
      «이 그룹의 광고 전환 상한»이라 분할 전 원값이 맞다. 분할은 캠페인별 배분용이고, 나누면
      상한이 실제보다 낮아져 «확정 0»이 잘못 뜰 수 있다.
    ★회계 규약(채널6·매출제외 상태·selling_price)은 today_proxy_revenue 한 곳에서만 온다 —
      여기서 Order를 직접 쿼리하면 규약이 갈라진다(ref 72 §0 행 D의 budget_pacing 전례)."""
    pids = _mapped_product_ids(db, adgroup_id)
    if not pids:
        return None, 0
    load_window.require_loaded("orders", today, today, reader="probe_revert._today_proxy_revenue")
    by_pid = today_proxy_revenue.revenue_by_product(db, pids, today)
    return int(sum(by_pid.values())), len(pids)


def _today_conversion_verdict(db: Session, probe: dict, now: datetime, curve) -> dict:
    """당일 전환 2신호 2단 판정 — {"verdict", "ad_conv", "signal", "proxy_revenue",
    "product_count", "adgroup_id", "note"}. 판정만 하고 집행하지 않는다."""
    today = now.date()
    adgroup_id = _probe_adgroup_id(probe)
    out: dict = {
        "verdict": _VERDICT_UNKNOWN, "adgroup_id": adgroup_id, "ad_conv": None,
        "signal": "none", "proxy_revenue": None, "product_count": 0, "note": "",
    }

    ad_conv = _conv_from_curve(curve, before_hour=now.hour)
    if ad_conv is not None:
        out["ad_conv"], out["signal"] = ad_conv, "hh24_curve"
        if ad_conv > 0:
            out["verdict"] = _VERDICT_POSITIVE
            return out
    else:
        # 곡선에 전환 정보가 없다 → 테이블 폴백은 «전환 있음»만 받는다(hold 방향 전용).
        if adgroup_id and _positive_conv_from_table(db, adgroup_id, today, before_hour=now.hour):
            out["verdict"], out["signal"] = _VERDICT_POSITIVE, "adgroup_hourly_today"
            out["note"] = "곡선에 전환 정보 없음 — 테이블에서 당일 전환 확인"
            return out
        out["note"] = ("hh24 곡선에 conv_cnt 없음(창 안 행 없음 또는 필드 부재) — "
                       "되돌림 근거로 쓸 당일 전환 신호가 없다")
        return out

    if not adgroup_id:
        # 곡선 전환 0은 잡았으나 상품 매핑을 찾을 키(광고그룹)가 없다 → 교차 확인 불가.
        out["verdict"] = _VERDICT_TENTATIVE_ZERO
        out["note"] = "광고그룹 미상(제안에 adgroup_id 없음) — 상한 프록시로 교차 확인 불가"
        return out

    proxy, product_count = _today_proxy_revenue(db, adgroup_id, today)
    out["proxy_revenue"], out["product_count"] = proxy, product_count
    if proxy is None:
        out["verdict"] = _VERDICT_TENTATIVE_ZERO
        out["note"] = "판매상품 매핑 없음 — 상한 프록시로 교차 확인 불가"
    elif proxy == 0:
        out["verdict"] = _VERDICT_CERTAIN_ZERO
    else:
        out["verdict"] = _VERDICT_TENTATIVE_ZERO
        out["note"] = "상품은 팔렸는데 광고 전환 0 — 당일 집계 지연에 의한 거짓 0 가능"
    return out


def _conv_direct_today_legacy(db: Session, target_type: str, target_id: str, today) -> int:
    """★폐기 예정 — 판정에 쓰지 않는다. 배포 후 «구경로 vs 신경로» 1회 대조 로그 전용이다.

    naver_ad_daily는 D−1까지만 적재되므로(load_window 레지스트리) `ad_date == 오늘` 조회는
    **구조적으로 항상 0**이다. 그 0이 19일간 밸브의 «당일 즉시구매 0» 조건을 영원히 참으로
    만들었다(D-NAO-192 발견 → D-NAO-193 수리). 라이브 대조가 끝나면 이 함수와 호출부를 지운다.
    grain 필터는 옛 동작을 그대로 보존한다(대조가 의미를 가지려면 옛 쿼리 그대로여야 한다)."""
    q = db.query(sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_cnt), 0)).filter(
        NaverAdDaily.ad_date == today,
        NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
    )
    if target_type == "keyword":
        q = q.filter(NaverAdDaily.keyword_id == target_id, NaverAdDaily.campaign_type == "WEB_SITE")
    elif target_type == "adgroup":
        q = q.filter(NaverAdDaily.adgroup_id == target_id)
    return int(q.scalar() or 0)


def _standing_probes(db: Session, now: datetime) -> list[dict]:
    """지금도 유효한(= 이후 다른 update_bid로 덮이지 않은) 탐침 되돌림 후보 목록.

    조회: approval_source=probe_op ∧ executed_change_log_id 존재 ∧ created_at이 최근 7일(KST
    환산: created_at UTC ≥ (now-9h)-7d, auto_operator._day_bounds_utc/diary_outcome._kst_date
    변환 관례). 각 후보의 change_log에서 before_bid=before_value['bidAmt'],
    probed_bid=after_value['bidAmt'], probe_date=changed_at.date()(시간당 레인이 now=kst_now()로
    execute→_execute_update_bid가 changed_at=now 기입 → KST-naive)를 읽는다.

    ★standing 판정(이중 되돌림·stale 되돌림 방지): 이 대상(entity_type/id)의 최신 성공 update_bid
    change_log(dry_run=False∧after_value 존재)가 바로 이 탐침의 change_log여야 한다. 그 사이
    다른 입찰 변경(레인 밴드 조정·되돌림 자체·다른 탐침)이 있었으면 이미 덮여 순위·입찰가
    맥락이 달라졌으므로 되돌림하지 않는다(superseded — skip). 행별 try/except로 한 행 오류가
    스윕을 죽이지 않는다."""
    lookback_start_utc = (now - timedelta(hours=9)) - timedelta(days=_LOOKBACK_DAYS)
    proposals = (
        db.query(NaverProposal)
        .filter(
            NaverProposal.approval_source == auto_operator.APPROVAL_SOURCE_PROBE,
            NaverProposal.executed_change_log_id.isnot(None),
            NaverProposal.created_at >= lookback_start_utc,
        )
        .order_by(NaverProposal.id.asc())
        .all()
    )
    out: list[dict] = []
    for p in proposals:
        try:
            cl = db.get(NaverChangeLog, p.executed_change_log_id)
            if cl is None or not cl.before_value or not cl.after_value:
                continue
            before = json.loads(cl.before_value)
            after = json.loads(cl.after_value)
            before_bid = before.get("bidAmt") if isinstance(before, dict) else None
            probed_bid = after.get("bidAmt") if isinstance(after, dict) else None
            if before_bid is None or probed_bid is None:
                continue

            latest = (
                db.query(NaverChangeLog)
                .filter(
                    NaverChangeLog.entity_type == p.target_type,
                    NaverChangeLog.entity_id == p.target_id,
                    NaverChangeLog.action == "update_bid",
                    NaverChangeLog.dry_run.is_(False),
                    NaverChangeLog.after_value.isnot(None),
                )
                .order_by(NaverChangeLog.changed_at.desc())
                .first()
            )
            if latest is None or latest.id != cl.id:
                continue  # superseded(그 사이 다른 입찰 변경) — 되돌림 대상 아님

            out.append({
                "proposal_id": p.id,
                "change_log_id": cl.id,
                "target_type": p.target_type,
                "target_id": p.target_id,
                "campaign_id": p.campaign_id,
                "adgroup_id": p.adgroup_id,
                "before_bid": int(before_bid),
                "probed_bid": int(probed_bid),
                "probe_date": cl.changed_at.date(),
            })
        except Exception as e:  # noqa: BLE001 — 한 행 실패가 스윕을 못 죽인다(나머지 후보 계속)
            log.warning("probe_revert: standing 후보 파싱 실패 proposal_id=%s: %s", p.id, e)
            continue
    return out


def run_bleed_valve(db: Session, *, now: datetime | None = None, fetch_intraday=None) -> dict:
    """Stage 1 실시간 출혈 밸브(D-58-8) — 시간당 레인 말미 호출. 당일(probe_date==today)
    standing probe 중 소진이 정착창 시간당평균×_PROBE_BLEED_COST_MULTIPLE(=3)을 넘고 당일
    전환이 0인 것을 즉시 되돌림(보수적 — 확실한 출혈만 회수, 애매하면 hold).

    ★당일 전환 판정은 _today_conversion_verdict의 2신호 2단(D-NAO-193) — 4값 분기:
      확정 0 → 즉시 회수 · 잠정 0 → **오늘 더 이른 시각에도 같은 조건이 관측됐을 때만** 회수
      (그 전엔 _bleed_watch에 기록하고 hold) · 전환 있음 → hold · 미관측 → skip(fail-open).
    ★「잠정 0」의 재확인을 «직전 시각»이 아니라 «오늘 더 이른 아무 시각»으로 둔 이유: 레인이
      매시 반드시 도는 보장이 없어(실패·배포·정지) 엄격한 연속 조건은 밸브를 조용히 죽인다.
      하루 안에 출혈 조건이 두 번 관측되면 지연에 의한 거짓 0로 보기 어렵다.

    fetch_intraday 미주입 시 fetch_entity_hh24(테스트 주입, 원칙18-8). now.hour==0이면 완료
    버킷이 없어 조기 반환(_probe_trigger/_is_pacing_slow의 자정 경계 처리와 동일 철학).
    정착창 소진 기준(settlement_daily_avg)이 0이면 비교 불가 → 되돌림 없음(fail-open — 근거
    없이 회수하지 않음, skip 집계). 반환: {"checked","reverted","held":[...],"skipped","errors"}.
    """
    now = now or kst_now()
    fetch_intraday = fetch_intraday or fetch_entity_hh24
    today = now.date()
    result: dict = {"checked": 0, "reverted": 0, "held": [], "skipped": 0, "errors": 0}
    if now.hour == 0:
        return result  # 완료 버킷 없음(자정 직후)

    for probe in _standing_probes(db, now):
        if probe["probe_date"] != today:
            continue
        result["checked"] += 1
        try:
            try:
                curve = fetch_intraday(probe["target_id"], today)
            except Exception as e:  # noqa: BLE001 — intraday 조회 실패 → 해당 유닛 skip
                result["skipped"] += 1
                log.warning("probe_revert: 출혈밸브 intraday 조회 실패 target=%s: %s",
                            probe["target_id"], e)
                continue

            cost_accrued = sum(h["cost"] for h in (curve or []) if h["hour"] < now.hour)
            hourly_rate = cost_accrued / now.hour

            window_from, window_to = auto_operator._settlement_window(today)
            settle = auto_operator._settlement_agg(
                db, probe["target_type"], probe["target_id"], window_from, window_to
            )
            settlement_daily_avg = settle["cost"] / auto_operator._HOURLY_BASELINE_DAYS
            if settlement_daily_avg <= 0:
                result["skipped"] += 1  # 비교 기준 없음 — fail-open(되돌림 없음)
                continue
            settlement_hourly_avg = settlement_daily_avg / 24
            cost_spike = hourly_rate > settlement_hourly_avg * auto_operator._PROBE_BLEED_COST_MULTIPLE

            verdict = _today_conversion_verdict(db, probe, now, curve)
            _log_path_comparison(db, probe, now, verdict, cost_spike=cost_spike,
                                 hourly_rate=hourly_rate, baseline_hourly=settlement_hourly_avg)

            spike_note = (
                f"시간당소진 {hourly_rate:.0f}원 > 정착창 시간당평균"
                f"×{auto_operator._PROBE_BLEED_COST_MULTIPLE}({settlement_hourly_avg:.0f}원)"
            )
            v = verdict["verdict"]
            if not cost_spike:
                result["held"].append({
                    "target_id": probe["target_id"],
                    "reason": f"출혈 아님(cost_spike=False, 당일전환판정={v})",
                })
            elif v == _VERDICT_POSITIVE:
                result["held"].append({
                    "target_id": probe["target_id"],
                    "reason": f"당일 광고 전환 있음(ad_conv={verdict['ad_conv']}) — 출혈 아님",
                })
            elif v == _VERDICT_UNKNOWN:
                # fail-open: 근거 없이 회수하지 않는다(settlement_daily_avg<=0 skip과 같은 규약).
                result["skipped"] += 1
                log.info("probe_revert: 당일 전환 미관측 — 되돌림 보류 target=%s: %s",
                         probe["target_id"], verdict["note"])
            elif v == _VERDICT_CERTAIN_ZERO:
                _revert_or_hold(db, probe, now, result,
                                reason=f"실시간 출혈 — {spike_note}∧당일전환0(확정 — 상품매출도 0)")
            else:  # _VERDICT_TENTATIVE_ZERO — 다음 시각 재확인 후 발화
                seen = _bleed_watch_hours(db, probe, today)
                earlier = [h for h in seen if h < now.hour]
                if earlier:
                    _revert_or_hold(
                        db, probe, now, result,
                        reason=(f"실시간 출혈 — {spike_note}∧당일전환0(잠정, {verdict['note']}) "
                                f"— {earlier[-1]}시에도 같은 조건 관측되어 재확인 완료"),
                    )
                else:
                    _bleed_watch_record(db, probe, today, now.hour)
                    result["held"].append({
                        "target_id": probe["target_id"],
                        "reason": (f"잠정 0 — 다음 시각 재확인 대기({verdict['note']}, "
                                   f"proxy_revenue={verdict['proxy_revenue']})"),
                    })
        except Exception as e:  # noqa: BLE001 — 한 유닛 실패가 밸브를 못 죽인다
            result["errors"] += 1
            log.exception("probe_revert: 출혈밸브 유닛 실패 target=%s: %s", probe["target_id"], e)

    # ★후보 0건이면 위 루프가 한 번도 안 돌아 «밸브가 돌았다»는 흔적이 남지 않는다.
    #   실행 사실과 후보 수는 항상 남긴다(라이브에서 «안 돌았다»와 «후보가 없었다»를 구별).
    log.info("[출혈밸브] now=%s KST 후보=%d %s", now.isoformat(), result["checked"], result)
    return result


def run_settlement(db: Session, *, now: datetime | None = None) -> dict:
    """Stage 2 성과 정산 판정(D-58-9) — 매일 08:55. D+1 정산 완료 데이터로 standing probe를
    유지/되돌림/보류 판정. age=(today-probe_date).days ≥ 1인 것만(D+1 이상 경과).

    선행지표(probe_signal): adjusted_score = 즉시구매 + 장바구니×전환율(상품→캠페인→global→0.0
    폴백), roas_corrected = (conv매출/cost)×보정계수. 판정(D-58-9):
      · clk==0                          → REVERT(상향해도 클릭 안 살아남 = 순위 병목 아님)
      · clk>0 ∧ roas_c≥target_roas      → KEEP(성공 — 유지)
      · clk>0 ∧ roas 미달/불명 ∧ adjusted<1.0  → REVERT(클릭 살았으나 전환 부족)
      · clk>0 ∧ roas 미달/불명 ∧ adjusted≥1.0  → DEFER(장바구니 경유 지연 가능),
                                                  단 age≥3이면 REVERT(근거 없이 상향 유지 금지)
    반환: {"checked","kept","reverted","deferred","errors"}. 유닛별 try/except."""
    now = now or kst_now()
    today = now.date()
    result: dict = {"checked": 0, "kept": 0, "reverted": 0, "deferred": 0, "errors": 0}

    bundle = cart_conversion_rate.cart_conversion_rates(db, as_of=today)
    try:
        cf = float(diagnosis.correction_factor(db, today)["factor"])
    except Exception as e:  # noqa: BLE001 — 보정계수 실패는 1.0 폴백(diary_outcome와 동일)
        log.warning("probe_revert: 보정계수 산출 실패(cf=1.0 폴백): %s", e)
        cf = 1.0

    for probe in _standing_probes(db, now):
        age = (today - probe["probe_date"]).days
        if age < 1:
            continue
        result["checked"] += 1
        try:
            cart_rate = _resolve_cart_rate(db, probe, bundle)
            sig = probe_signal.probe_signal_score(
                db, grain=probe["target_type"], target_id=probe["target_id"],
                campaign_id=probe["campaign_id"], date_from=probe["probe_date"],
                date_to=probe["probe_date"], cart_rate=cart_rate, correction_factor_value=cf,
            )
            target_roas = auto_operator._resolve_target_roas(db, probe["campaign_id"])
            clk, roas_c, adjusted = sig["clk"], sig["roas_corrected"], sig["adjusted_score"]

            if clk == 0:
                if _execute_revert(db, probe, now,
                                   reason="상향해도 클릭 안 살아남 = 순위 병목 아님", stage="settle"):
                    result["reverted"] += 1
            elif roas_c is not None and target_roas is not None and roas_c >= target_roas:
                _write_probe_outcome(db, probe, now, result="kept", metrics=sig)
                result["kept"] += 1
            elif adjusted < 1.0:
                if _execute_revert(db, probe, now,
                                   reason="클릭 살았으나 전환 부족", stage="settle"):
                    result["reverted"] += 1
            elif age >= 3:
                if _execute_revert(db, probe, now,
                                   reason="근거 없이 상향 유지 금지(age≥3 안전 default)", stage="settle"):
                    result["reverted"] += 1
            else:
                _write_probe_outcome(db, probe, now, result="deferred", metrics=sig)
                result["deferred"] += 1
        except Exception as e:  # noqa: BLE001 — 한 유닛 실패가 정산을 못 죽인다
            result["errors"] += 1
            log.exception("probe_revert: 정산 유닛 실패 target=%s: %s", probe["target_id"], e)
    return result


def _revert_or_hold(db: Session, probe: dict, now: datetime, result: dict, *, reason: str) -> None:
    """되돌림 1건 집행 시도 후 result 집계 — 되돌림/hold 두 갈래를 한 곳에 모은다."""
    if _execute_revert(db, probe, now, reason=reason, stage="bleed"):
        result["reverted"] += 1
    else:
        result["held"].append({
            "target_id": probe["target_id"], "reason": "되돌림 skip(킬스위치/가드레일)",
        })


# 라이브 «구경로 vs 신경로» 1회 대조가 끝나면 False로 내린다(그 다음 배포에서 함수째 삭제).
_LEGACY_PATH_COMPARISON = True


def _log_path_comparison(
    db: Session, probe: dict, now: datetime, verdict: dict, *,
    cost_spike: bool, hourly_rate: float, baseline_hourly: float,
) -> None:
    """★배포 후 라이브 합격 증거(ref 72 §2-① 합격기준) — 구경로/신경로 1회 병기 로그.

    구경로는 **판정에 쓰이지 않는다**(대조 표시 전용). 옛 값이 0이고 신 값이 실제 전환을
    가리키는 것이 관측되면 이 함수와 _conv_direct_today_legacy를 함께 지운다.
    fail-open: 로그 산출 실패가 밸브 판정을 되돌리지 않는다.
    ★삭제 트리거: 라이브에서 «구=0 / 신=실제값» 1회 관측이 끝나면 _LEGACY_PATH_COMPARISON을
      False로 내리고, 다음 배포에서 이 함수와 _conv_direct_today_legacy를 함께 지운다."""
    if not _LEGACY_PATH_COMPARISON:
        return
    try:
        today = now.date()
        legacy = _conv_direct_today_legacy(db, probe["target_type"], probe["target_id"], today)
        try:
            load_window.require_loaded("naver_ad_daily", today, today,
                                       reader="probe_revert._conv_direct_today_legacy")
            window_note = "적재창 안"
        except load_window.LoadWindowError as e:
            window_note = f"적재창 밖 — {e}"
        log.info(
            "[출혈밸브·경로대조] now=%s KST target=%s(%s) adgroup=%s | "
            "구경로 naver_ad_daily.conv_direct=%s (%s) | "
            "신경로 판정=%s 신호=%s ad_conv=%s proxy_revenue=%s products=%s %s | "
            "cost_spike=%s(시간당 %.0f원 vs 기준 %.0f원)",
            now.isoformat(), probe["target_id"], probe["target_type"], verdict["adgroup_id"],
            legacy, window_note,
            verdict["verdict"], verdict["signal"], verdict["ad_conv"], verdict["proxy_revenue"],
            verdict["product_count"], verdict["note"],
            cost_spike, hourly_rate, baseline_hourly,
        )
    except Exception as e:  # noqa: BLE001 — 대조 로그 실패가 판정을 오염시키지 않는다
        log.warning("probe_revert: 경로대조 로그 실패(fail-open): %s", e)


def _probe_diary_entry(db: Session, probe: dict) -> OpsDiaryEntry | None:
    """원 탐침의 execute 일기 행(source_ref=probe change_log id) — 없으면 None."""
    return (
        db.query(OpsDiaryEntry)
        .filter(
            OpsDiaryEntry.source_ref == probe["change_log_id"],
            OpsDiaryEntry.event_type == "execute",
            OpsDiaryEntry.actor == diary.ACTOR_PROBE,
        )
        .first()
    )


_BLEED_WATCH_KEY = "bleed_watch"


def _bleed_watch_hours(db: Session, probe: dict, today) -> list[int]:
    """오늘 이미 «출혈 ∧ 잠정 0»으로 관측된 시각 목록(오름차순). 기록이 없거나 날짜가 다르면 [].

    ★저장소를 일기 outcome_json으로 둔 이유: 마이그레이션 없이 시간 간 상태를 나르기 위해서다
      (probe 상태는 원래 change_log/일기에서 파생한다는 이 모듈의 규약을 따른다).
    ★일기 행이 없으면 기록도 조회도 못 하므로 잠정 0은 **영원히 hold**가 된다 —
      fail-toward-hold(보수적 되돌림 계약과 같은 방향)이고, 조용한 오발보다 낫다."""
    try:
        entry = _probe_diary_entry(db, probe)
        if entry is None or not entry.outcome_json:
            return []
        watch = (json.loads(entry.outcome_json) or {}).get(_BLEED_WATCH_KEY) or {}
        if watch.get("date") != today.isoformat():
            return []
        return sorted({int(h) for h in watch.get("hours", [])})
    except Exception as e:  # noqa: BLE001 — fail-toward-hold
        log.warning("probe_revert: bleed_watch 조회 실패(hold): %s", e)
        return []


def _bleed_watch_record(db: Session, probe: dict, today, hour: int) -> None:
    """오늘 시각 `hour`에 «출혈 ∧ 잠정 0»을 관측했음을 기록(같은 시각 중복 기입 없음).
    날짜가 바뀌면 목록을 새로 시작한다. fail-open(기입 실패가 hold 판정을 되돌리지 않는다)."""
    try:
        entry = _probe_diary_entry(db, probe)
        if entry is None:
            log.warning("probe_revert: bleed_watch 기입 대상 일기 없음 target=%s(잠정 0 유지)",
                        probe["target_id"])
            return
        outcome: dict = json.loads(entry.outcome_json) if entry.outcome_json else {}
        watch = outcome.get(_BLEED_WATCH_KEY) or {}
        hours = sorted({int(h) for h in watch.get("hours", [])}) \
            if watch.get("date") == today.isoformat() else []
        if hour not in hours:
            hours.append(hour)
        outcome[_BLEED_WATCH_KEY] = {"date": today.isoformat(), "hours": sorted(hours)}
        entry.outcome_json = json.dumps(outcome, ensure_ascii=False)
        db.commit()
    except Exception as e:  # noqa: BLE001 — fail-open
        log.warning("probe_revert: bleed_watch 기입 실패(fail-open): %s", e)


def _resolve_cart_rate(db: Session, probe: dict, bundle: dict) -> float:
    """장바구니→구매 전환율 폴백(cart_conversion_rate SA 출력 소비): adgroup은 1:1 상품 매핑이
    있으면 by_product, 없으면/키워드는 by_campaign → global → 0.0(정직 경계 — 없으면 장바구니
    가중 0)."""
    if probe["target_type"] == "adgroup":
        pid = _one_to_one_product(db, probe["target_id"])
        if pid is not None:
            rate = bundle["by_product"].get(pid)
            if rate is not None:
                return rate
    rate = bundle["by_campaign"].get(probe["campaign_id"])
    if rate is not None:
        return rate
    if bundle["global"] is not None:
        return bundle["global"]
    return 0.0


def _execute_revert(db: Session, probe: dict, now: datetime, *, reason: str, stage: str) -> bool:
    """되돌림 1건 집행 — bid_down 제안(target_bid=원래 before_bid)을 생성해 harness.execute()로
    원위치. 성공 True/실패·거부 False.

    ★킬스위치 pre-check(harness _claim_executing의 최종 가드와 이중 방어): auto_operate=False면
    제안 생성 자체를 하지 않는다(즉시 정지 계약 — 되돌림도 우회 금지). harness가 가드레일
    (쿨다운·클램프 등)로 차단하거나 writer가 실패하면 예외를 던지고 change_log/상태를 스스로
    확정(failed) — 여기선 로그만 남기고 False."""
    if not auto_operator._auto_operate_now(db, probe["campaign_id"]):
        log.warning(
            "probe_revert: 킬스위치 OFF — 되돌림 skip campaign=%s target=%s(stage=%s)",
            probe["campaign_id"], probe["target_id"], stage,
        )
        return False

    proposal = NaverProposal(
        proposal_type="bid_down",
        target_type=probe["target_type"], target_id=probe["target_id"],
        campaign_id=probe["campaign_id"], adgroup_id=probe.get("adgroup_id"),
        target_bid=probe["before_bid"],
        rationale=f"[탐침되돌림·{stage}] {reason}",
        expected_effect="클릭 탐침 되돌림 — before_value로 원위치(D-NAO-58 CD3)",
        status="approved",
        approval_source=auto_operator.APPROVAL_SOURCE_REVERT,
    )
    db.add(proposal)
    db.flush()
    db.commit()

    try:
        naver_execution_harness.execute(db, proposal.id, dry_run=False, now=now)
    except Exception as e:  # noqa: BLE001 — harness가 change_log/상태(failed 등)를 이미 확정
        log.warning(
            "probe_revert: 되돌림 실행 실패 proposal_id=%s target=%s(stage=%s): %s",
            proposal.id, probe["target_id"], stage, e,
        )
        return False

    _write_probe_outcome(db, probe, now, result="reverted", stage=stage, reason=reason)
    return True


def _write_probe_outcome(
    db: Session, probe: dict, now: datetime, *, result: str,
    stage: str | None = None, reason: str | None = None, metrics: dict | None = None,
) -> None:
    """원 탐침의 execute 일기 행(source_ref=probe change_log id, event_type='execute',
    actor='probe')의 outcome_json['probe']에 판정 결과를 병합 기입(기존 키 보존).
    fail-open(일기 미발견·기입 실패가 되돌림/판정을 되돌리지 않는다 — log.warning만).

    ★되돌림 집행 자체가 남기는 새 execute 일기(source_ref=되돌림 change_log id)와는 다른 행이다
    (원 탐침 행만 source_ref로 특정) — 그래서 result='reverted'도 원 탐침 행에 정확히 붙는다."""
    try:
        entry = _probe_diary_entry(db, probe)
        if entry is None:
            return
        outcome: dict = json.loads(entry.outcome_json) if entry.outcome_json else {}
        probe_outcome: dict = {
            "result": result, "stage": stage, "reason": reason,
            "checked_at": now.isoformat(),
        }
        if metrics is not None:
            probe_outcome.update({
                "adjusted_score": metrics.get("adjusted_score"),
                "roas_corrected": metrics.get("roas_corrected"),
                "clk": metrics.get("clk"),
                "cost": metrics.get("cost"),
            })
        outcome["probe"] = probe_outcome
        entry.outcome_json = json.dumps(outcome, ensure_ascii=False)
        db.commit()
    except Exception as e:  # noqa: BLE001 — fail-open
        log.warning("probe_revert: probe outcome 기입 실패(fail-open): %s", e)
