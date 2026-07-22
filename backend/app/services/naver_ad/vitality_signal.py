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

import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc
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
# C3(codex 1R): D-1 부분적재 오발 차단 — as_of 행수가 직전 창 평균 대비 이 비율 미만이면
# 부분적재 의심(발사 전면 보류). anomaly_feed.freshness_partial_load와 동일 로직·임계값.
_PARTIAL_LOAD_LOOKBACK_DAYS = 3
_PARTIAL_LOAD_MIN_RATIO = Decimal("0.5")
# C6(codex 1R): 소생 발사 허용 캠페인유형 — 발사는 adgroup grain(update_adgroup_bid)이라
# 쇼핑/브랜드검색만(파워링크 WEB_SITE=keyword grain, 시간당 레인 grain 계약과 충돌).
_REVIVE_ALLOWED_CAMPAIGN_TYPES = ("SHOPPING", "BRAND_SEARCH")


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


def _latest_lock_stopped(
    db: Session, entity_id: str, *, entity_type: str = "adgroup",
) -> bool | None:
    """엔티티의 **최신 성공 set_user_lock 이벤트**의 최종 잠금 상태(GATE P1-1 구조화 판정).

    권고안(GATE 적대 리뷰): rationale 부분 매칭(포맷 변화 취약·죽은 토큰) 대신 change_log의
    구조화 필드로 판정한다. 성공 실쓰기 행(dry_run=False ∧ after_value 존재 — 실패·가드거부·
    dry-run 행은 after_value를 안 채움)만 후보로, 가장 최근 이벤트의 after_value에서 userLock을
    파싱한다(set_user_lock의 after_value = writer 재조회 dict, 최상위 userLock 키. pause↔resume
    구분은 이 값 — bm_diff.py status_flip 관례와 동일: pause→true / resume→false).

    entity_type: 'adgroup'(그룹 소생 게이트) 또는 'campaign'(C2 부모 체인 게이트) — set_user_lock
    change_log는 target_type/target_id를 그대로 심으므로(naver_execution_harness) 캠페인 잠금도
    entity_type='campaign'·entity_id=campaign_id로 동일 구조에서 재사용된다.

    반환: None=그런 이벤트 없음(우리가 정지·재개한 적 없음 — 판정 근거 없음).
          True=최종 잠금(정지) 또는 파싱 불가(fail-closed — 정지로 간주).
          False=최종 재개(resume, userLock=false — 소생 허용 가능)."""
    ev = (
        db.query(NaverChangeLog.after_value)
        .filter(
            NaverChangeLog.entity_type == entity_type,
            NaverChangeLog.entity_id == entity_id,
            NaverChangeLog.action == "set_user_lock",
            NaverChangeLog.dry_run.is_(False),
            NaverChangeLog.after_value.isnot(None),
        )
        .order_by(NaverChangeLog.changed_at.desc(), NaverChangeLog.id.desc())
        .first()
    )
    if ev is None:
        return None
    try:
        parsed = json.loads(ev[0])
    except (ValueError, TypeError):
        return True  # 파싱 불가 = fail-closed(정지로 간주 → 배제)
    if not isinstance(parsed, dict):
        return True
    lock = parsed.get("userLock")
    if isinstance(lock, bool):
        return lock
    return True  # userLock 키 부재/비불리언 = fail-closed


def _conflict_gate_pass(db: Session, adgroup_id: str, d0: date) -> bool:
    """충돌 방지 게이트(PLAN §0 2 — GATE 최우선). A축 확정 손실 개체는 소생 금지.
    fail-closed: 판단 불가(예외·엔티티 부재)는 전부 제외(False).

    ①naver_entity status≠'on'(off/deleted=userLock 병합) 제외 — 행 부재도 제외(fail-closed:
      on을 확인 못 하면 소생 금지). 단 sync가 하루 1회(07:35)라 A축 08:50 정지 후 ~23시간
      'on'으로 stale일 수 있다.
    ②change_log 잠금 이벤트를 **stale entity.status보다 우선하는 권위 소스**로 쓴다(GATE P1-1)
      — 최신 성공 set_user_lock의 최종 userLock이 true(정지)면 사유 불문 배제. 정지↔재개
      구분은 rationale 텍스트가 아니라 구조화 필드로(포맷 변화 취약성 원천 제거). ①②는
      보수적 OR: 둘 중 하나라도 "정지"를 가리키면 배제. resume(userLock=false) 후이고
      entity status도 'on'이면 둘 다 정지 아님 → 통과(소생 허용).
    ③과거 30일 전환 ≥1 또는 클릭 표본 미달(<10)인 그룹만 통과(충분 표본 무전환=A축이 자른
      것, 소생 금지 — §0 2 무전환 확정 게이트, ①②와 별개로 유지)."""
    try:
        row = (
            db.query(NaverEntity.status)
            .filter(NaverEntity.entity_type == "adgroup", NaverEntity.entity_id == adgroup_id)
            .first()
        )
        entity_stopped = row is None or row[0] != "on"
        changelog_stopped = _latest_lock_stopped(db, adgroup_id)  # None=이력 없음
        # 보수적 OR: entity(stale 가능) 또는 change_log 권위 소스 중 하나라도 정지면 배제.
        if entity_stopped or changelog_stopped is True:
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


def _partial_load_suspected(db: Session, d0: date) -> tuple[bool, str | None]:
    """C3(codex 1R): D-1(d0) 실적재 행수가 직전 창 일평균 대비 −50%↓면 부분적재 의심.

    anomaly_feed.freshness_partial_load(:53-102)와 **동일 로직**의 경량 재구현이다 — SA간 직접
    import는 계층 결합(원칙18-6 SA 간 직접 호출 금지)이라, 같은 판정(as_of 행수 vs 직전 창
    일평균, 비율 <0.5 = partial)을 이 SA 안에 독립 구현한다(sentinel 제외·baseline 없음이면
    추정 금지 False). 근거: 부분적재는 D-1 sync가 절반만 돌고 죽은 경우 — 행 자체가 사라지므로
    행수 비교로 잡힌다(스파이럴은 행수 유지·imp만 하락이라 이와 직교, 오검출 없음). partial이면
    D-1 신호가 통째로 왜곡되므로 발사를 전면 보류(경보는 유지).

    반환: (partial 여부, 사유 문자열|None)."""
    window_from = d0 - timedelta(days=_PARTIAL_LOAD_LOOKBACK_DAYS)
    window_to = d0 - timedelta(days=1)
    as_of_count = int(
        db.query(sqlfunc.count(NaverAdDaily.id))
        .filter(NaverAdDaily.ad_date == d0, NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP)
        .scalar() or 0
    )
    daily_counts = dict(
        db.query(NaverAdDaily.ad_date, sqlfunc.count(NaverAdDaily.id))
        .filter(
            NaverAdDaily.ad_date >= window_from, NaverAdDaily.ad_date <= window_to,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .group_by(NaverAdDaily.ad_date)
        .all()
    )
    days_with_data = len(daily_counts)
    if days_with_data == 0:
        return False, None  # baseline 없음 = 비교 불가(추정 금지)
    baseline_avg = Decimal(sum(daily_counts.values())) / Decimal(days_with_data)
    if baseline_avg == 0:
        return False, None
    ratio = Decimal(as_of_count) / baseline_avg
    if ratio < _PARTIAL_LOAD_MIN_RATIO:
        return True, (
            f"D-1({d0.isoformat()}) 부분적재 의심 — 행수 {as_of_count} vs 직전"
            f"{days_with_data}일 평균 {float(round(baseline_avg, 1))}행"
            f"({float(round(ratio * 100, 1))}%) → 발사 전면 보류"
        )
    return False, None


def _campaign_type_from_daily(db: Session, campaign_id: str, d0: date) -> str | None:
    """C6(codex 1R): 캠페인 campaign_type — naver_ad_daily 최근 실행 행(sentinel 제외·빈값 제외).
    SA가 신뢰하는 확정 정본에서 읽는다(NaverEntity.campaign_type 동기화 의존 회피). None=미확보."""
    start = d0 - timedelta(days=_CONV_LOOKBACK_DAYS - 1)
    row = (
        db.query(NaverAdDaily.campaign_type)
        .filter(
            NaverAdDaily.campaign_id == campaign_id,
            NaverAdDaily.ad_date >= start,
            NaverAdDaily.ad_date <= d0,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
            NaverAdDaily.campaign_type != "",
        )
        .order_by(NaverAdDaily.ad_date.desc())
        .first()
    )
    return row[0] if row else None


def _campaign_revive_gate(db: Session, campaign_id: str, d0: date) -> tuple[bool, str | None]:
    """C2+C6(codex 1R) 캠페인 레벨 소생 게이트 — fail-closed. 통과(True, None) 또는 (False, 사유).
    경보(스파이럴 관찰)는 유형·정지와 무관하게 유지하되, **소생 발사 대상**만 이 게이트로 거른다.

    C2 부모 체인 정지(hot-set _hot_set_candidates 관례 동형 — 부모가 off/잠금이면 자식 소생 금지):
      ①NaverEntity(campaign) 행 부재 또는 status≠'on' → 배제(fail-closed: on 확인 못 하면 소생 금지).
      ②campaign 대상 최신 성공 set_user_lock userLock=true(잠금) → 배제(_latest_lock_stopped 캠페인
        재사용, change_log 권위 소스). ①②는 보수적 OR.
    C6 grain 계약(③): campaign_type ∈ {SHOPPING, BRAND_SEARCH}만 통과 — 발사가 adgroup grain이라
      WEB_SITE(파워링크=keyword grain)·미확보는 배제(시간당 레인 grain 계약과 충돌 방지, 실측
      스파이럴 표본 전부 쇼핑·파워링크 키워드-grain 소생은 후속)."""
    try:
        row = (
            db.query(NaverEntity.status)
            .filter(NaverEntity.entity_type == "campaign", NaverEntity.entity_id == campaign_id)
            .first()
        )
        if row is None or row[0] != "on":
            return False, "캠페인 엔티티 정지/행부재 — 소생 보류(fail-closed)"
        if _latest_lock_stopped(db, campaign_id, entity_type="campaign") is True:
            return False, "캠페인 잠금(set_user_lock) — 소생 보류"
        ctype = _campaign_type_from_daily(db, campaign_id, d0)
        if ctype not in _REVIVE_ALLOWED_CAMPAIGN_TYPES:
            return False, f"campaign_type={ctype or '미확보'} — 소생 보류(쇼핑/브랜드 grain 한정)"
        return True, None
    except Exception as e:  # noqa: BLE001 — 판단 불가는 fail-closed(소생 보류)
        log.warning("vitality_signal: 캠페인 소생 게이트 판정 실패 campaign=%s(보류): %s", campaign_id, e)
        return False, "캠페인 게이트 판정 예외 — 소생 보류(fail-closed)"


def detect_spirals(db: Session, *, now: datetime | None = None) -> dict:
    """스파이럴 경보 + 소생 대상 산출(PLAN §1). 순수 SA·read-only.

    경보 = S1 ∧ S2(auto_operate=1 캠페인만). as_of=D0=today-1(최근 완료일 — 당일은 진행중
    부분치라 제외, 코드베이스 as_of=D-1 관례). S3는 경보 dict에 부가 정보로만 싣는다.

    반환: {"alerts": [{campaign_id, imp_traj, cum_drop_pct, rank_traj, avg_rank,
                       s3_low_qi_ratio, revive_group_count, revive_note?}],
           "revive_targets": [{campaign_id, adgroup_id, conv_revenue_30d, reason}],
           "revive_hold_reason": None|str}  # C3 부분적재 전면 보류 사유(전역)

    C3(codex 1R): D-1 부분적재 의심이면 발사를 전면 보류 — 경보(관찰)는 그대로 산출하되
    revive_targets를 비우고 revive_hold_reason에 사유를 싣는다(브리핑 표기용).
    C2/C6(codex 1R): 캠페인 레벨 게이트(부모 체인 정지·잠금·grain 유형)를 통과한 캠페인만
    소생 후보를 만든다. 게이트 탈락 캠페인은 경보에 revive_note를 달고 후보 0."""
    now = now or kst_now()
    d0 = now.date() - timedelta(days=1)
    partial, partial_reason = _partial_load_suspected(db, d0)  # C3: 전역 부분적재 판정 1회
    alerts: list[dict] = []
    revive_targets: list[dict] = []
    for cid in _auto_operate_campaign_ids(db):
        imp_by, rank_by = _campaign_daily_series(db, cid, d0 - timedelta(days=3), d0)
        s1_fired, s1d = _signal_s1(imp_by, d0)
        s2_fired, s2d = _signal_s2(rank_by, d0)
        if not (s1_fired and s2_fired):
            continue
        # C2+C6 캠페인 레벨 게이트(경보는 유지, 소생 대상만 거른다). 부분적재면 게이트 판정도
        # 생략하고 후보 0(C3 전면 보류 — 아래 revive_hold_reason이 사유를 대표).
        if partial:
            cands, revive_note = [], None
        else:
            gate_ok, revive_note = _campaign_revive_gate(db, cid, d0)
            cands = _revive_candidates(db, cid, d0) if gate_ok else []
        s3 = _low_qi_ratio(db, [c["adgroup_id"] for c in cands])
        alert = {
            "campaign_id": cid,
            "imp_traj": s1d["imp_traj"],
            "cum_drop_pct": s1d["cum_drop_pct"],
            "rank_traj": s2d["rank_traj"],
            "avg_rank": s2d["avg_rank"],
            "s3_low_qi_ratio": s3,
            "revive_group_count": len(cands),
        }
        if revive_note:
            alert["revive_note"] = revive_note
        alerts.append(alert)
        revive_targets.extend(cands)
    log.info(
        "vitality_signal: detect_spirals as_of=%s 경보=%s 소생대상=%s%s",
        d0.isoformat(), len(alerts), len(revive_targets),
        f" (부분적재 보류: {partial_reason})" if partial else "",
    )
    return {
        "alerts": alerts,
        "revive_targets": revive_targets,
        "revive_hold_reason": partial_reason if partial else None,
    }
