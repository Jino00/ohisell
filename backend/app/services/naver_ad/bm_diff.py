# bm_diff.py — SA-2 조작 감지기 (BM 벤치마크 레이어, D-NAO-78)
# 역할: naver_entity_snapshot의 D-1 vs D 두 날짜 셋을 diff(DB-to-DB, 0 GET·결정적·리플레이)
#   → naver_agency_op 이벤트 생성. 계정 전체 45캠페인(대행사 포함)의 일일 조작을 관찰 전용
#   피드로 축적한다(§3). 노이즈 필터 4종(ours 자기변경 제외·deleted 가드·bootstrap 가드·
#   is_exception 판정). 네이버 API 호출 0 — 실행 손(naver_execution_harness/naver_sa_writer)을
#   import조차 안 한다(§0 금지선 1 · 원칙18-1 단일 책임).
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import NaverAgencyOp, NaverChangeLog, NaverEntitySnapshot
from app.utils.kst import kst_now, kst_today

log = logging.getLogger(__name__)

# ── 노이즈·예외 임계(§3 공통 필터, §9-3 초기 캘리브레이션 대상) ──
BID_JITTER_PCT = 3.0        # |Δ%| < 3% = 입찰 반올림 지터 무시
BUDGET_JITTER_PCT = 5.0     # |Δ%| < 5% = 예산 지터 무시
BID_EXCEPTION_PCT = 20.0    # 입찰 |Δ%| ≥ 20% → is_exception
BUDGET_EXCEPTION_PCT = 30.0  # 예산 |Δ%| ≥ 30% → is_exception
OURS_MATCH_WINDOW_H = 48    # ours 자기변경 매칭 창(최근 48h change_log)

# op_type → 우리(ours)가 API로 쓰는 change_log.action 집합(매칭 대상). 여기 없는 op_type은
# 우리가 API로 안 쓰는 종류 → ours여도 매칭 불가 = 외부 개입으로 간주(§3-1 후단).
_OURS_ACTION_MATCH = {
    "bid_change": {"update_bid"},
    "status_flip": {"set_user_lock", "external_status_change"},
    "budget_change": {"update_budget"},
    "negative_add": {"add_negative_keyword"},
}


def _pct(before: int, after: int) -> float:
    """Δ% (before 기준). before=0이면 0(분모 방어 — 지터/예외 판정 모두 통과 안 됨)."""
    return (after - before) / before * 100 if before else 0.0


def _snapshot_map(db: Session, sdate: date) -> dict[tuple[str, str], NaverEntitySnapshot]:
    """(entity_type, entity_id) → 스냅샷 행. 특정 날짜 셋을 dict로 적재."""
    return {
        (r.entity_type, r.entity_id): r
        for r in db.query(NaverEntitySnapshot).filter(
            NaverEntitySnapshot.snapshot_date == sdate).all()
    }


def _op(row, op_type: str, *, before, after, magnitude, force_exc: bool = False) -> dict:
    """diff 1건 → 내부 op dict(분류/영속화 전 중간표현). before/after는 텍스트로 정규화."""
    return {
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "campaign_id": row.campaign_id or "",
        "optimizer": row.optimizer or "none",
        "op_type": op_type,
        "before_value": None if before is None else str(before),
        "after_value": None if after is None else str(after),
        "magnitude": magnitude,
        "force_exc": force_exc,
    }


def _detect_added(curr: dict, prev: dict) -> list[dict]:
    """새 entity_id 등장 = 대행사 구조 신설(campaign_add/adgroup_add). 항상 is_exception=True(§3).

    신규 등장인데 이미 deleted면 무시(구조로 등장하지 않은 잔여 상태)."""
    ops = []
    for key, c in curr.items():
        if key in prev or (c.status or "") == "deleted":
            continue
        op_type = "campaign_add" if c.entity_type == "campaign" else "adgroup_add"
        ops.append(_op(c, op_type, before=None, after=(c.status or "on"), magnitude=None, force_exc=True))
    return ops


def _detect_removed(curr: dict, prev: dict) -> list[dict]:
    """소실 또는 deleted 전이 = 구조 소멸(campaign_remove/adgroup_remove). 항상 is_exception=True.

    ★deleted 가드(§3-2): D-1에서 이미 deleted였던 엔티티는 재발화 금지(일 레인 deleted 404
    반복 사고 교훈). on/off였던 것이 이번에 소실·deleted로 바뀔 때 1회만 기록한다."""
    ops = []
    for key, p in prev.items():
        if (p.status or "") == "deleted":
            continue  # 이미 이전에 remove 기록됨 — 재발화 금지
        c = curr.get(key)
        became_deleted = c is not None and (c.status or "") == "deleted"
        if c is not None and not became_deleted:
            continue  # 여전히 존재(변화는 _detect_changed가 처리)
        op_type = "campaign_remove" if p.entity_type == "campaign" else "adgroup_remove"
        after = "deleted" if became_deleted else None
        ops.append(_op(p, op_type, before=(p.status or "on"), after=after, magnitude=None, force_exc=True))
    return ops


def _changed_bid(p, c) -> list[dict]:
    """그룹 기본입찰 Δ. |Δ%| < 3% 지터 무시. magnitude=Δ%(방향 포함)."""
    if c.entity_type != "adgroup" or p.bid_amt is None or c.bid_amt is None or p.bid_amt == c.bid_amt:
        return []
    pct = _pct(p.bid_amt, c.bid_amt)
    if abs(pct) < BID_JITTER_PCT:
        return []
    return [_op(c, "bid_change", before=p.bid_amt, after=c.bid_amt, magnitude=round(pct, 2))]


def _changed_status(p, c) -> list[dict]:
    """상태 on↔off 전이. deleted 전이는 _detect_removed가 처리(여기선 제외).

    ★캠페인 grain의 status_flip은 항상 is_exception=True로 승격(P2 리뷰 지적 반영, 2026-07-21
    실사례 근거): 대행사가 맥세이프쇼검·폴드8/플립8 캠페인을 통째로 잠그는 것은 그날 최대
    조작이었다 — 캠페인 정지/재개는 그룹 개별 flip과 무게가 다른 구조적 사건이라 대형변화
    임계(§3-4a) 유무와 무관하게 예외 브리핑에 올린다. 그룹 grain flip은 기존대로 비예외
    유지(대형변화·외부개입 판정에서만 예외 승격, §3 불변)."""
    ps, cs = (p.status or "on"), (c.status or "on")
    if ps == cs or "deleted" in (ps, cs):
        return []
    is_campaign = c.entity_type == "campaign"
    return [_op(c, "status_flip", before=ps, after=cs, magnitude=None, force_exc=is_campaign)]


def _changed_keyword_count(p, c) -> list[dict]:
    """그룹 활성 키워드 수 증감(집계 이벤트, entity=그룹·before/after=count). 개별 키워드
    grain은 entity_sync가 이미 로깅 — §9-1 병존. 어느 쪽이든 NULL(미집계)이면 스킵."""
    if c.entity_type != "adgroup" or p.keyword_count is None or c.keyword_count is None:
        return []
    if p.keyword_count == c.keyword_count:
        return []
    op_type = "keyword_add" if c.keyword_count > p.keyword_count else "keyword_remove"
    return [_op(c, op_type, before=p.keyword_count, after=c.keyword_count,
                magnitude=float(abs(c.keyword_count - p.keyword_count)))]


def _changed_budget(p, c) -> list[dict]:
    """일예산 Δ. |Δ%| < 5% 무시. ★어느 쪽이든 NULL이면 스킵 — P1/P2는 양쪽 NULL(자연 비활성),
    P3 최초 적재일의 NULL↔값 전이는 '미수집→수집' 아티팩트라 조작이 아니다(양쪽 값이 찬 P3
    2일차부터 실제 Δ 발화). §6 '양쪽 NULL 스킵'을 either-NULL로 확장(근거=None은 미수집 센티넬)."""
    if p.daily_budget is None or c.daily_budget is None or p.daily_budget == c.daily_budget:
        return []
    pct = _pct(p.daily_budget, c.daily_budget)
    if abs(pct) < BUDGET_JITTER_PCT:
        return []
    return [_op(c, "budget_change", before=p.daily_budget, after=c.daily_budget, magnitude=round(pct, 2))]


def _changed_extended(p, c) -> list[dict]:
    """확장검색 on↔off 토글. 어느 쪽이든 NULL(미수집)이면 스킵(_changed_budget과 같은 근거)."""
    if p.extended_search is None or c.extended_search is None or p.extended_search == c.extended_search:
        return []
    return [_op(c, "extended_toggle", before=p.extended_search, after=c.extended_search, magnitude=None)]


def _changed_negative(p, c) -> list[dict]:
    """제외키워드 수 증감(주간 grain). 어느 쪽이든 NULL(미수집)이면 스킵."""
    if p.negative_kw_count is None or c.negative_kw_count is None or p.negative_kw_count == c.negative_kw_count:
        return []
    op_type = "negative_add" if c.negative_kw_count > p.negative_kw_count else "negative_remove"
    return [_op(c, op_type, before=p.negative_kw_count, after=c.negative_kw_count,
                magnitude=float(abs(c.negative_kw_count - p.negative_kw_count)))]


def _changed_creative(p, c) -> list[dict]:
    """소재 수 증감(주간 grain). 어느 쪽이든 NULL(미수집)이면 스킵."""
    if p.ad_count is None or c.ad_count is None or p.ad_count == c.ad_count:
        return []
    return [_op(c, "creative_change", before=p.ad_count, after=c.ad_count,
                magnitude=float(abs(c.ad_count - p.ad_count)))]


def _detect_changed(curr: dict, prev: dict) -> list[dict]:
    """양쪽 스냅샷에 모두 존재하는 엔티티의 차원별 변화. deleted 관여 엔티티는 remove 담당."""
    ops: list[dict] = []
    for key, c in curr.items():
        p = prev.get(key)
        if p is None or (c.status or "") == "deleted" or (p.status or "") == "deleted":
            continue
        ops += _changed_bid(p, c)
        ops += _changed_status(p, c)
        ops += _changed_keyword_count(p, c)
        ops += _changed_budget(p, c)
        ops += _changed_extended(p, c)
        ops += _changed_negative(p, c)
        ops += _changed_creative(p, c)
    return ops


def _load_ours_change_logs(db: Session, ops: list[dict]) -> dict[tuple[str, str], list[tuple[str, datetime]]]:
    """ops 중 optimizer='ours' 엔티티에 한해 우리 실집행 change_log(dry_run=False)를 적재.

    ours 엔티티만 대상이라 소량(하루 변경 건). 시간창은 파이썬에서 판정(changed_at은 우리
    실집행이 kst_now 명시 기록 — naver_execution_harness). (entity_type, entity_id)→[(action, at)]."""
    ids = {o["entity_id"] for o in ops if o["optimizer"] == "ours"}
    if not ids:
        return {}
    rows = (
        db.query(NaverChangeLog)
        .filter(NaverChangeLog.dry_run.is_(False), NaverChangeLog.entity_id.in_(ids))
        .all()
    )
    out: dict[tuple[str, str], list[tuple[str, datetime]]] = defaultdict(list)
    for r in rows:
        out[(r.entity_type, r.entity_id)].append((r.action, r.changed_at))
    return out


def _matches_our_write(op: dict, ours_logs: dict, now: datetime) -> bool:
    """이 조작이 최근 48h 우리 실집행 change_log와 매칭되는가(= 우리 손, 대행사 아님)."""
    actions = _OURS_ACTION_MATCH.get(op["op_type"])
    if not actions:
        return False  # 우리가 API로 안 쓰는 종류 → 매칭 불가(외부로 간주)
    cutoff = now - timedelta(hours=OURS_MATCH_WINDOW_H)
    for action, changed_at in ours_logs.get((op["entity_type"], op["entity_id"]), ()):
        if action in actions and changed_at is not None and changed_at >= cutoff:
            return True
    return False


def _magnitude_exception(op: dict) -> bool:
    """대형 변화 예외 판정(§3-4a): 입찰 |Δ%|≥20% · 예산 |Δ%|≥30%."""
    m = op["magnitude"]
    if m is None:
        return False
    if op["op_type"] == "bid_change":
        return abs(m) >= BID_EXCEPTION_PCT
    if op["op_type"] == "budget_change":
        return abs(m) >= BUDGET_EXCEPTION_PCT
    return False


def _classify(op: dict, ours_logs: dict, now: datetime) -> tuple[bool, bool]:
    """(기록 여부, is_exception). §3-1: ours 자기변경은 매칭 시 제외, 미매칭 시 외부 개입 예외."""
    if op["optimizer"] == "ours":
        if _matches_our_write(op, ours_logs, now):
            return False, False  # 우리 손 — agency_op에서 제외
        return True, True        # 매칭 안 됨 = 우리 캠페인에 대한 외부 개입 → 예외 승격
    return True, bool(op["force_exc"] or _magnitude_exception(op))


def detect_agency_ops(db: Session, *, op_date: date | None = None) -> dict:
    """스냅샷 D-1 vs D를 diff해 naver_agency_op 이벤트를 산출(멱등·리플레이, 0 GET).

    bootstrap 가드: D-1 스냅샷이 없으면 전건 add 폭주를 막기 위해 스킵. 멱등: 같은 op_date
    기존 이벤트를 삭제 후 재생성(결정적이라 재도출=동일 결과, §9-1 병존 정책).
    """
    d = op_date or kst_today()
    prev = _snapshot_map(db, d - timedelta(days=1))
    if not prev:  # bootstrap 가드(§3-3) — 최초 실행/전일 스냅샷 부재
        log.info("[BM] SA-2 diff bootstrap 스킵(op_date=%s, D-1 스냅샷 없음)", d)
        return {"op_date": str(d), "bootstrap": True, "events": 0, "exceptions": 0}

    curr = _snapshot_map(db, d)
    now = kst_now()
    raw = _detect_added(curr, prev) + _detect_removed(curr, prev) + _detect_changed(curr, prev)
    ours_logs = _load_ours_change_logs(db, raw)

    db.query(NaverAgencyOp).filter(NaverAgencyOp.op_date == d).delete()  # 멱등 재생성
    n_event = n_exc = 0
    for op in raw:
        keep, is_exc = _classify(op, ours_logs, now)
        if not keep:
            continue
        db.add(NaverAgencyOp(
            op_date=d, detected_at=now,
            entity_type=op["entity_type"], entity_id=op["entity_id"],
            campaign_id=op["campaign_id"], optimizer=op["optimizer"],
            op_type=op["op_type"], before_value=op["before_value"],
            after_value=op["after_value"], magnitude=op["magnitude"], is_exception=is_exc,
        ))
        n_event += 1
        n_exc += int(is_exc)
    db.commit()

    result = {"op_date": str(d), "bootstrap": False, "events": n_event, "exceptions": n_exc}
    log.info("[BM] SA-2 조작 감지: %s", result)
    return result
