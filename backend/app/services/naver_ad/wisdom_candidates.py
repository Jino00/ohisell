# wisdom_candidates.py — candidate_sa (D-NAO-54 P3 승격층, docs/PLAN_naver-ad-diary-wisdom.md §P3)
# 역할: 결과가 기입된 diary 행(execute/blocked + outcome_json에 d1|d7)을 스캔해 (캠페인×액션×
#   환경버킷) 시그니처로 반복 패턴 후보(ops_wisdom_candidates)를 뽑는다. 같은 시그니처 재등장은
#   occurrences++·last_seen_at 갱신(중복 entry id는 카운트 안 함), 신규는 후보 생성(observation은
#   규칙 기반 요약 — LLM 아님). promoted/rejected/hidden 시그니처는 재수확하지 않는다(판사 판정
#   완료 또는 망각). 읽기(diary·campaign_target_resolver) + wisdom_candidates 쓰기만(원칙18-1).
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import OpsDiaryEntry, OpsWisdomCandidate
from app.services.naver_ad import campaign_target_resolver
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# 수확 대상 이벤트 — 실집행(execute)과 가드레일/구조 차단(blocked)만. reject(말미 stale 정리)·
# kill_switch는 제외(P2 리뷰 P3-2: reject는 blocked와 같은 제안의 2행이라 포함하면 이중계상).
HARVEST_EVENT_TYPES = ("execute", "blocked")

# 판사가 이미 판정했거나 망각된 시그니처는 재수확 금지(occurrences조차 갱신 안 함).
_TERMINAL_STATUSES = frozenset({"promoted", "rejected", "hidden"})

# 아이폰 출시 전후 ±N일을 launch_window로 본다(그 외 normal, offset None은 unknown).
_IPHONE_WINDOW_DAYS = 14

# created_at(UTC) 스캔 하한 — diary_outcome가 60일까지만 결과를 채우므로(그 뒤 소급 없음)
# 여유를 둔 90일. 시그니처 dedup(entry id 중복 제외)이 있어 재스캔은 멱등이지만, 무한히 커지는
# 쿼리를 막는 안전 상한이다.
_HARVEST_LOOKBACK_DAYS = 90


def _day_class(entry: OpsDiaryEntry) -> str:
    """휴일 우선 → 주말 → 평일. is_kr_holiday True면 holiday(요일 무관), weekday 5/6=weekend,
    0~4=weekday, weekday None(스냅샷 결측)=unknown."""
    if entry.is_kr_holiday:
        return "holiday"
    if entry.weekday is None:
        return "unknown"
    return "weekend" if entry.weekday >= 5 else "weekday"


def _iphone_window(offset: int | None) -> str:
    """출시 오프셋 → 3버킷. None=unknown, |offset|≤14=launch_window, 그 외=normal
    (env 캐비어트 P2 §env: 미래 출시일 미등록 시 큰 양수 → normal로 흡수)."""
    if offset is None:
        return "unknown"
    return "launch_window" if abs(offset) <= _IPHONE_WINDOW_DAYS else "normal"


def _outcome_window(outcome: dict) -> dict | None:
    """d7 우선(정착 성숙), 없으면 d1. 둘 다 없으면 None(스캔 대상 아님)."""
    if outcome.get("d7"):
        return outcome["d7"]
    if outcome.get("d1"):
        return outcome["d1"]
    return None


def _outcome_direction(db: Session, entry: OpsDiaryEntry, window: dict) -> str | None:
    """결과 방향 2버킷 good/bad. good = (roas_c ≥ 캠페인 target) or (cost=0). target은
    campaign_target_resolver 재사용 — 조회 실패/미확보(None)면 None 반환(후보 생성 skip)."""
    try:
        resolved = campaign_target_resolver.resolve_target_roas(db, entry.campaign_id)
        target = resolved.get("target_roas")
    except Exception as e:  # noqa: BLE001 — 해석 실패는 후보 생성 skip(부풀림 방지)
        log.warning("wisdom_candidates: target 해석 실패(후보 skip): campaign=%s: %s", entry.campaign_id, e)
        return None
    if target is None:
        return None
    cost = window.get("cost") or 0
    roas_c = window.get("roas_c")
    good = (roas_c is not None and roas_c >= float(target)) or cost == 0
    return "good" if good else "bad"


def _observation(entry: OpsDiaryEntry, env: dict, window: dict, target_note: str) -> str:
    """규칙 기반 요약문(LLM 아님) — 시그니처가 무엇을 묶는지 사람이 읽을 한 줄."""
    return (
        f"[패턴] 캠페인 {entry.campaign_id}의 {entry.action or '(액션미상)'} — "
        f"{env['day_class']}·{env['season'] or '계절미상'}·아이폰 {env['iphone_window']}에서 "
        f"결과 {env['outcome_direction']}"
        f"(roas_c={window.get('roas_c')}, cost={window.get('cost')}, {target_note})."
    )


def harvest_candidates(db: Session, *, now: datetime | None = None) -> dict:
    """결과 기입된 diary 행에서 반복 패턴 후보를 수확(매일 wisdom_loop이 호출).

    행별 try/except + 유닛 증분 커밋(D-NAO-46② 쓰기락 교훈). 시그니처 dedup은 entry id 단위라
    재스캔이 카운트를 부풀리지 않는다(같은 행 무시).
    """
    now = now or kst_now()
    lower_utc = (now - timedelta(hours=9)) - timedelta(days=_HARVEST_LOOKBACK_DAYS)
    rows = (
        db.query(OpsDiaryEntry)
        .filter(
            OpsDiaryEntry.event_type.in_(HARVEST_EVENT_TYPES),
            OpsDiaryEntry.outcome_json.isnot(None),
            OpsDiaryEntry.created_at.isnot(None),
            OpsDiaryEntry.created_at >= lower_utc,
        )
        .all()
    )
    totals = {"scanned": 0, "new": 0, "updated": 0,
              "skipped_no_outcome": 0, "skipped_no_target": 0, "skipped_terminal": 0, "errors": 0}
    for entry in rows:
        try:
            outcome = json.loads(entry.outcome_json) if entry.outcome_json else {}
            window = _outcome_window(outcome) if outcome else None
            if window is None:
                totals["skipped_no_outcome"] += 1
                continue
            totals["scanned"] += 1
            direction = _outcome_direction(db, entry, window)
            if direction is None:
                totals["skipped_no_target"] += 1
                continue

            env = {
                "day_class": _day_class(entry),
                "season": entry.season,
                "iphone_window": _iphone_window(entry.iphone_launch_offset_days),
                "outcome_direction": direction,
            }
            env_bucket = f"{env['day_class']}|{env['season']}|{env['iphone_window']}|{direction}"
            signature = f"{entry.campaign_id}|{entry.action}|{env_bucket}"

            cand = (
                db.query(OpsWisdomCandidate)
                .filter(OpsWisdomCandidate.signature == signature)
                .first()
            )
            if cand is not None:
                if cand.status in _TERMINAL_STATUSES:
                    totals["skipped_terminal"] += 1
                    continue
                ids = json.loads(cand.source_entry_ids_json or "[]")
                if entry.id in ids:  # 같은 행 재스캔 — 카운트 부풀림 금지
                    continue
                ids.append(entry.id)
                cand.source_entry_ids_json = json.dumps(ids)
                cand.occurrences = len(ids)
                cand.last_seen_at = now
                db.commit()
                totals["updated"] += 1
            else:
                cand = OpsWisdomCandidate(
                    signature=signature, campaign_id=entry.campaign_id, action=entry.action,
                    env_bucket_json=json.dumps(env, ensure_ascii=False),
                    observation=_observation(entry, env, window, "target 대비"),
                    occurrences=1, first_seen_at=now, last_seen_at=now,
                    source_entry_ids_json=json.dumps([entry.id]), status="pending",
                    importance=5, strength=7.0,
                )
                db.add(cand)
                db.commit()
                totals["new"] += 1
        except Exception as e:  # noqa: BLE001 — 한 행 실패가 스윕을 못 죽인다
            db.rollback()
            totals["errors"] += 1
            log.exception("wisdom_candidates: 행 수확 실패(id=%s): %s", getattr(entry, "id", "?"), e)
    return totals
