# vitality_signal.py — 스파이럴 조기 경보 SA (스프린트 VT1, D-NAO-81 B축 "살리기").
#   역할(순수 SA·DB read-only): auto_operate=1 캠페인의 흐름 붕괴(스파이럴)를 궤적으로 조기
#   감지한다. A축(ROAS 자르기)이 놓치는 "노출·순위가 미끄러지며 검증된 그룹이 조용히 죽는"
#   구간을 잡아, 소생 대상 그룹(revive_targets)까지 골라 harness(auto_operator.run_hourly_lane)에
#   넘긴다. 이 모듈은 네이버 API·DB에 절대 쓰지 않는다(관찰 전용) — 실행(입찰 UP)·브리핑은
#   전부 harness 몫(원칙18 — SA 독립·단일 책임, SA 간 직접 호출 금지).
#
#   ★데이터 소스 선택(근거): S1(노출)·S2(순위) 모두 **naver_ad_daily**(as_of=D-1 확정치)를
#   캠페인 grain으로 집계해 쓴다. naver_hourly_snapshot(캠페인 avg_rank 보유)도 후보였으나
#   7일 롤링 보관이라 크론 1회 누락 시 S1의 4일 창(D0-3..D0)이 깨질 수 있고, 당일 행은 진행중
#   부분치라 "일 최종"을 뽑는 추가 로직이 필요하다. naver_ad_daily는 (a) 영구 보존(창 결손
#   위험 0) (b) 확정 정본 (c) rank_sum/imp로 노출가중 캠페인 순위를 그대로 산출 (d) 코드베이스
#   전반의 as_of=D-1 관례와 일치 — 더 안정적이라 이쪽을 택했다(PLAN §1 "코드 실측 후 안정적인
#   쪽 선택" 지시).
#
#   ★신호 정의(첫 주 캘리브레이션 대상 = 가설, PLAN §1): "3일 연속 하락/악화"는 **최근 완료일이
#   직전 2일보다 나빠진 지속 궤적**으로 구현한다(엄격 단조 X). 03 실측(07-12~18)에서 07-17에
#   노출이 924→1175(+27%)로 하루 반등했는데, 엄격 단조(v0<v1<v2<v3)면 이 한 번의 반등이
#   스파이럴 경보를 리셋해 버린다 — 조기 경보의 목적(붕괴를 놓치지 않음)에 반한다. 그래서
#   블립에 견고한 "직전 2일 대비 악화 + 크기 게이트"로 정의한다.
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc, or_
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverCampaignSettings, NaverChangeLog, NaverEntity
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# ── 신호 상수(첫 주 캘리브레이션 = 가설, PLAN §1) ──
_CUM_DROP_THRESHOLD = Decimal("0.40")  # S1 누적 −40%(3일 전 D0-3 대비)
_RANK_BAND_TOP = Decimal("4.0")        # S2 목표 밴드 상단 — 초과(순위 숫자 > 4.0 = 밴드 밖)
_CONV_LOOKBACK_DAYS = 30               # 충돌 게이트·우선순위 과거 전환 창
_MIN_CLICK_SAMPLE = 10                 # 판정 표본 최소 클릭(미만=표본 미달, 소생 허용)
_QI_LOW_GRADE = 3                      # S3 보조: qi_grade ≤3 = 저품질
_REVIVE_WINDOW_DAYS = 7                # 소생 대상 "최근 노출 하락" 판정 창


def _to_date(d) -> date:
    """SQLite가 ad_date를 str로 돌려주는 환경 방어(대개 date지만 안전 정규화)."""
    return d if isinstance(d, date) else date.fromisoformat(str(d))


def _campaign_daily_series(
    db: Session, campaign_id: str, start_date: date, end_date: date,
) -> tuple[dict[date, int], dict[date, Decimal | None]]:
    """캠페인 일별 (노출, 노출가중 avg_rank) 시계열 — naver_ad_daily 집계(sentinel 제외).

    rank = sum(rank_sum)/sum(imp)(노출가중, imp=0이면 None=순위 무의미). 반환 (imp_by_day,
    rank_by_day). 한 쿼리로 두 신호 원료를 동시에 뽑는다(S1·S2 공용)."""
    rows = (
        db.query(
            NaverAdDaily.ad_date,
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.imp), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.rank_sum), 0),
        )
        .filter(
            NaverAdDaily.campaign_id == campaign_id,
            NaverAdDaily.ad_date >= start_date,
            NaverAdDaily.ad_date <= end_date,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .group_by(NaverAdDaily.ad_date)
        .all()
    )
    imp_by: dict[date, int] = {}
    rank_by: dict[date, Decimal | None] = {}
    for d, imp, rsum in rows:
        dd = _to_date(d)
        imp_by[dd] = int(imp)
        rank_by[dd] = (Decimal(int(rsum)) / Decimal(int(imp))) if int(imp) > 0 else None
    return imp_by, rank_by


def _signal_s1(imp_by: dict[date, int], d0: date) -> tuple[bool, dict]:
    """S1 노출 하락 궤적: 최근 완료일(D0)이 직전 2일 모두보다 낮음(지속 하락) ∧ 3일 전(D0-3)
    대비 누적 −40%↑. 4일 창(D0-3..D0) 데이터가 하나라도 없으면 판정 불가(False, fail-closed)."""
    days = [d0 - timedelta(days=k) for k in range(4)]  # D0, D0-1, D0-2, D0-3
    if any(d not in imp_by for d in days):
        return False, {"s1": False, "s1_reason": "노출 4일 창 데이터 부족"}
    v0, v1, v2, v3 = (imp_by[days[0]], imp_by[days[1]], imp_by[days[2]], imp_by[days[3]])
    if v3 <= 0:
        return False, {"s1": False, "s1_reason": "3일 전(D0-3) 노출 0 — 기준 없음"}
    cum_drop = Decimal(v3 - v0) / Decimal(v3)
    fired = (v0 < v1) and (v0 < v2) and (cum_drop >= _CUM_DROP_THRESHOLD)
    return fired, {
        "s1": fired,
        "imp_traj": [v3, v2, v1, v0],
        "cum_drop_pct": float(round(cum_drop * 100, 1)),
    }


def _signal_s2(rank_by: dict[date, Decimal | None], d0: date) -> tuple[bool, dict]:
    """S2 순위 이탈 궤적: 최근 완료일(D0) avg_rank가 직전 2일 모두보다 악화(숫자 증가=순위 하락)
    ∧ 목표 밴드 상단(4.0) 밖. 3일 창(D0-2..D0) 순위가 하나라도 None/부재면 판정 불가(False)."""
    days = [d0 - timedelta(days=k) for k in range(3)]  # D0, D0-1, D0-2
    if any(rank_by.get(d) is None for d in days):
        return False, {"s2": False, "s2_reason": "순위 3일 창 데이터 부족(07-17부터 수집)"}
    r0, r1, r2 = rank_by[days[0]], rank_by[days[1]], rank_by[days[2]]
    fired = (r0 > r1) and (r0 > r2) and (r0 > _RANK_BAND_TOP)
    return fired, {
        "s2": fired,
        "rank_traj": [float(r2), float(r1), float(r0)],
        "avg_rank": float(r0),
    }


def _campaign_adgroups(db: Session, campaign_id: str, d0: date) -> list[str]:
    """최근 30일 활동한 광고그룹 id(sentinel·빈값 제외) — 소생 후보 모집단."""
    start = d0 - timedelta(days=_CONV_LOOKBACK_DAYS - 1)
    rows = (
        db.query(NaverAdDaily.adgroup_id)
        .filter(
            NaverAdDaily.campaign_id == campaign_id,
            NaverAdDaily.ad_date >= start,
            NaverAdDaily.ad_date <= d0,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
            NaverAdDaily.adgroup_id != "",
        )
        .distinct()
        .all()
    )
    return [r[0] for r in rows]


def _group_30d_stats(db: Session, adgroup_id: str, d0: date) -> dict:
    """그룹 과거 30일 (클릭, 전환건수, 전환매출) 집계 — 충돌 게이트③·우선순위 원료."""
    start = d0 - timedelta(days=_CONV_LOOKBACK_DAYS - 1)
    clk, conv_cnt, rev = (
        db.query(
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
            sqlfunc.coalesce(
                sqlfunc.sum(NaverAdDaily.conv_direct_cnt + NaverAdDaily.conv_indirect_cnt), 0
            ),
            sqlfunc.coalesce(
                sqlfunc.sum(NaverAdDaily.conv_direct_amt + NaverAdDaily.conv_indirect_amt), 0
            ),
        )
        .filter(
            NaverAdDaily.adgroup_id == adgroup_id,
            NaverAdDaily.ad_date >= start,
            NaverAdDaily.ad_date <= d0,
        )
        .one()
    )
    return {"clk": int(clk), "conv_cnt": int(conv_cnt), "conv_revenue": int(rev)}


def _conflict_gate_pass(db: Session, adgroup_id: str, d0: date) -> bool:
    """충돌 방지 게이트(PLAN §0 2 — GATE 최우선). A축 확정 손실 개체는 소생 금지.
    fail-closed: 판단 불가(예외·엔티티 부재)는 전부 제외(False).

    ①naver_entity status≠'on'(off/deleted=userLock 병합) 제외 — 행 부재도 제외(fail-closed:
      on을 확인 못 하면 소생 금지) ②change_log에 set_user_lock ∧ rationale에
      [shopping_pause_candidates]/[터미널정지] 이력 있으면 제외(A축 스톱로스/터미널정지)
      ③과거 30일 전환 ≥1 또는 클릭 표본 미달(<10)인 그룹만 통과(충분 표본 무전환=A축이 자른
      것, 소생 금지)."""
    try:
        row = (
            db.query(NaverEntity.status)
            .filter(NaverEntity.entity_type == "adgroup", NaverEntity.entity_id == adgroup_id)
            .first()
        )
        if row is None or row[0] != "on":
            return False
        stopped = (
            db.query(NaverChangeLog.id)
            .filter(
                NaverChangeLog.entity_type == "adgroup",
                NaverChangeLog.entity_id == adgroup_id,
                NaverChangeLog.action == "set_user_lock",
                NaverChangeLog.dry_run.is_(False),
                or_(
                    NaverChangeLog.rationale.like("%[shopping_pause_candidates]%"),
                    NaverChangeLog.rationale.like("%[터미널정지]%"),
                ),
            )
            .first()
        )
        if stopped is not None:
            return False
        st = _group_30d_stats(db, adgroup_id, d0)
        return st["conv_cnt"] >= 1 or st["clk"] < _MIN_CLICK_SAMPLE
    except Exception as e:  # noqa: BLE001 — 판단 불가는 fail-closed(제외)
        log.warning("vitality_signal: 충돌 게이트 판정 실패 adgroup=%s(제외): %s", adgroup_id, e)
        return False


def _group_recent_decline(db: Session, adgroup_id: str, d0: date) -> bool:
    """최근 7일 창(D0-6..D0)에서 최근 3일(D0-2..D0) 노출 합 < 이른 3일(D0-6..D0-4) 노출 합
    ∧ 이른 3일 합 > 0. 노출이 있던 그룹이 최근 미끄러진 것만 소생 후보로 남긴다."""
    start = d0 - timedelta(days=_REVIVE_WINDOW_DAYS - 1)
    rows = (
        db.query(NaverAdDaily.ad_date, sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.imp), 0))
        .filter(
            NaverAdDaily.adgroup_id == adgroup_id,
            NaverAdDaily.ad_date >= start,
            NaverAdDaily.ad_date <= d0,
        )
        .group_by(NaverAdDaily.ad_date)
        .all()
    )
    imp_by = {_to_date(d): int(v) for d, v in rows}
    recent = sum(imp_by.get(d0 - timedelta(days=k), 0) for k in range(3))       # D0-2..D0
    earlier = sum(imp_by.get(d0 - timedelta(days=k), 0) for k in range(4, 7))   # D0-6..D0-4
    return earlier > 0 and recent < earlier


def _revive_candidates(db: Session, campaign_id: str, d0: date) -> list[dict]:
    """경보 캠페인 내 소생 대상 그룹 — 충돌 게이트 통과 ∧ 최근 7일 노출 하락, 과거 30일
    전환매출 내림차순(살릴 가치 순, PLAN §1)."""
    out: list[dict] = []
    for gid in _campaign_adgroups(db, campaign_id, d0):
        if not _conflict_gate_pass(db, gid, d0):
            continue
        if not _group_recent_decline(db, gid, d0):
            continue
        st = _group_30d_stats(db, gid, d0)
        out.append({
            "campaign_id": campaign_id,
            "adgroup_id": gid,
            "conv_revenue_30d": st["conv_revenue"],
            "reason": (
                f"최근7일 노출 하락·충돌게이트 통과·30d 전환매출 {st['conv_revenue']}원·"
                f"clk {st['clk']}·conv {st['conv_cnt']}"
            ),
        })
    out.sort(key=lambda x: x["conv_revenue_30d"], reverse=True)
    return out


def _low_qi_ratio(db: Session, adgroup_ids: list[str]) -> float | None:
    """S3(보조): 소생 대상 그룹 키워드의 qi_grade ≤3 비중 — 브리핑 표기·경보 가중용(단독
    트리거 아님). 키워드 qi_grade는 WEB_SITE만 존재(쇼핑 그룹=키워드 행 없음) → 없으면 None."""
    if not adgroup_ids:
        return None
    rows = (
        db.query(NaverEntity.qi_grade)
        .filter(
            NaverEntity.entity_type == "keyword",
            NaverEntity.parent_id.in_(adgroup_ids),
            NaverEntity.qi_grade.isnot(None),
        )
        .all()
    )
    if not rows:
        return None
    low = sum(1 for (q,) in rows if q <= _QI_LOW_GRADE)
    return float(round(low / len(rows), 3))


def _auto_operate_campaign_ids(db: Session) -> list[str]:
    """auto_operate=1 캠페인 id — vitality 스코프(ours 자동운영만). harness 헬퍼를 import하지
    않고 독립 조회한다(원칙18 — SA는 harness에 결합되지 않는다)."""
    rows = (
        db.query(NaverCampaignSettings.campaign_id)
        .filter(NaverCampaignSettings.auto_operate.is_(True))
        .all()
    )
    return [r[0] for r in rows]


def detect_spirals(db: Session, *, now: datetime | None = None) -> dict:
    """스파이럴 경보 + 소생 대상 산출(PLAN §1). 순수 SA·read-only.

    경보 = S1 ∧ S2(auto_operate=1 캠페인만). as_of=D0=today-1(최근 완료일 — 당일은 진행중
    부분치라 제외, 코드베이스 as_of=D-1 관례). S3는 경보 dict에 부가 정보로만 싣는다.

    반환: {"alerts": [{campaign_id, imp_traj, cum_drop_pct, rank_traj, avg_rank,
                       s3_low_qi_ratio, revive_group_count}],
           "revive_targets": [{campaign_id, adgroup_id, conv_revenue_30d, reason}]}"""
    now = now or kst_now()
    d0 = now.date() - timedelta(days=1)
    alerts: list[dict] = []
    revive_targets: list[dict] = []
    for cid in _auto_operate_campaign_ids(db):
        imp_by, rank_by = _campaign_daily_series(db, cid, d0 - timedelta(days=3), d0)
        s1_fired, s1d = _signal_s1(imp_by, d0)
        s2_fired, s2d = _signal_s2(rank_by, d0)
        if not (s1_fired and s2_fired):
            continue
        cands = _revive_candidates(db, cid, d0)
        s3 = _low_qi_ratio(db, [c["adgroup_id"] for c in cands])
        alerts.append({
            "campaign_id": cid,
            "imp_traj": s1d["imp_traj"],
            "cum_drop_pct": s1d["cum_drop_pct"],
            "rank_traj": s2d["rank_traj"],
            "avg_rank": s2d["avg_rank"],
            "s3_low_qi_ratio": s3,
            "revive_group_count": len(cands),
        })
        revive_targets.extend(cands)
    log.info(
        "vitality_signal: detect_spirals as_of=%s 경보=%s 소생대상=%s",
        d0.isoformat(), len(alerts), len(revive_targets),
    )
    return {"alerts": alerts, "revive_targets": revive_targets}
