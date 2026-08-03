# probe_revert.py — probe_revert SA (D-NAO-58 CD3, docs/PLAN_naver-ad-click-discovery.md)
# 역할: CD2가 능동 상향한 클릭 탐침(approval_source=probe_op)이 성적을 냈는지 사후 판정하고,
#   근거 없이 올려둔 입찰가는 before_value로 원위치(되돌림)한다. 두 단계:
#   ①Stage 1 실시간 출혈 밸브(run_bleed_valve, D-58-8) — 시간당 레인 말미에 당일 standing
#     probe의 소진이 정착창 시간당평균×3을 넘고(비용 급등) 당일 즉시구매가 0이면 즉시 회수.
#     "돈 새는 걸 하루 내내 방치하지 않는다"의 안전판(보수적 되돌림 — 애매하면 hold, 확실한
#     출혈만 회수).
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

from app.models import NaverAdDaily, NaverAdgroupProduct, NaverChangeLog, NaverProposal, OpsDiaryEntry
from app.services.naver_ad import adgroup_product_freshness
from app.services.naver_ad import (
    auto_operator,
    cart_conversion_rate,
    diagnosis,
    diary,
    naver_execution_harness,
    probe_signal,
)
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.services.naver_sa_ad_fetcher import fetch_entity_hh24
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# standing probe 조회 창(created_at UTC 하한) — 이보다 오래된 탐침은 되돌림 대상에서 제외.
# 정산은 D+1~D+2에 판정되므로 7일이면 충분(diary_outcome._MAX_LOOKBACK_DAYS(60일)보다 짧게 —
# 되돌림은 시의성이 있어 오래된 미판정 탐침을 되살리지 않는다).
_LOOKBACK_DAYS = 7


def _one_to_one_product(db: Session, adgroup_id: str) -> str | None:
    """adgroup_id가 정확히 하나의 mall_product_id에 매핑될 때만 그 상품 id 반환(다상품/미매핑은
    None). cart_conversion_rate._one_to_one_adgroup_product와 동일 판정(1:1만) — 그 함수는
    전체 스캔이라 단건 조회로 국소화(같은 규약, 결합 회피)."""
    rows = db.query(NaverAdgroupProduct.mall_product_id).filter(
        NaverAdgroupProduct.adgroup_id == adgroup_id,
        adgroup_product_freshness.fresh_condition()
    ).all()
    pids = {r[0] for r in rows}
    return next(iter(pids)) if len(pids) == 1 else None


def _conv_direct_today(db: Session, target_type: str, target_id: str, today) -> int:
    """당일 즉시구매(conv_direct_cnt) 합 — grain 필터는 probe_signal/_settlement_agg와 동일
    (BACKFILL 센티넬 제외, keyword grain은 WEB_SITE 한정). 행 없으면 0."""
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
    즉시구매가 0인 것을 즉시 되돌림(보수적 — 확실한 출혈만 회수, 애매하면 hold).

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
            conv_today = _conv_direct_today(db, probe["target_type"], probe["target_id"], today)

            if cost_spike and conv_today == 0:
                reason = (
                    f"실시간 출혈 — 시간당소진 {hourly_rate:.0f}원 > 정착창 시간당평균"
                    f"×{auto_operator._PROBE_BLEED_COST_MULTIPLE}({settlement_hourly_avg:.0f}원)"
                    f"∧당일즉시구매0"
                )
                if _execute_revert(db, probe, now, reason=reason, stage="bleed"):
                    result["reverted"] += 1
                else:
                    result["held"].append({
                        "target_id": probe["target_id"], "reason": "되돌림 skip(킬스위치/가드레일)"
                    })
            else:
                result["held"].append({
                    "target_id": probe["target_id"],
                    "reason": f"출혈 아님(cost_spike={cost_spike}, conv_today={conv_today})",
                })
        except Exception as e:  # noqa: BLE001 — 한 유닛 실패가 밸브를 못 죽인다
            result["errors"] += 1
            log.exception("probe_revert: 출혈밸브 유닛 실패 target=%s: %s", probe["target_id"], e)
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
        entry = (
            db.query(OpsDiaryEntry)
            .filter(
                OpsDiaryEntry.source_ref == probe["change_log_id"],
                OpsDiaryEntry.event_type == "execute",
                OpsDiaryEntry.actor == diary.ACTOR_PROBE,
            )
            .first()
        )
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
