# wisdom_candidates.py — candidate_sa (D-NAO-54 P3 승격층, docs/PLAN_naver-ad-diary-wisdom.md §P3)
# 역할: 결과가 기입된 diary 행(execute/blocked + outcome_json에 d1|d7)을 스캔해 (캠페인×액션×
#   환경) 조건 시그니처로 반복 패턴 후보(ops_wisdom_candidates)를 뽑는다. 결과 방향은 시그니처가
#   아니라 good_count/bad_count로 세고 occurrences=good+bad로 정의한다(같은 조건의 good·bad를 한
#   후보에 모아 승률을 판단 — 리뷰 P2-2). 같은 시그니처 재등장은 방향 tally++·last_seen_at 갱신
#   (중복 entry id는 미가산), 신규는 후보 생성(observation은 규칙 기반 요약 — LLM 아님). cost=0/
#   결측 관찰은 중립이라 tally에 안 넣고 후보 생성/갱신도 skip(리뷰 P2-3). promoted/rejected
#   시그니처는 재수확하지 않지만, hidden은 재등장 시 pending으로 부활한다(리뷰 P2-1 — Ebbinghaus
#   재노출 강화). 읽기(diary·campaign_target_resolver) + wisdom_candidates 쓰기만(원칙18-1).
#   ★target_type=="search_term" 행은 수확 대상에서 제외한다(D-NAO-178) — 그 행의 d1은 검색어가
#   아니라 캠페인 grain이라 승률이 남의 성적표로 쌓인다. 해제는 S8(d1_st 소비 전환)에서.
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

# 판사가 이미 판정한 시그니처는 재수확 금지(tally조차 갱신 안 함). hidden은 제외 — 재등장하면
# 부활시킨다(리뷰 P2-1: 망각↔TTL 데드락 해소 + Ebbinghaus 재노출 강화).
_TERMINAL_STATUSES = frozenset({"promoted", "rejected"})

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


def _outcome_direction(
    db: Session, entry: OpsDiaryEntry, window: dict, *, bep_cache: dict | None = None,
) -> str | None:
    """결과 방향 3분류. cost가 0/None/결측이면 'neutral'(돈 안 쓴 관찰 = 패턴 증거 아님 →
    tally 미기여·후보 skip, 리뷰 P2-3). 비용이 있으면 **손익분기(BEP) 대비** good/bad —
    good = roas_c >= 캠페인 bep_roas. 해석 실패/미확보(None)면 None 반환(후보 생성 skip,
    neutral과 구분).

    ★D-NAO-223(M3-b 축 ⓑ, 2026-08-22) — 기준자를 `target_roas` → `bep_roas`로 교정했다.
      `models.py`가 `target_roas = bep_roas x 공격성 배수`로 정의하므로 `target >= bep`이고,
      target을 기준자로 쓰면 **본전을 넘겨 실제로 총이익을 낸 구간(bep <= roas < target)이
      통째로 `bad`로 떨어진다.** 그 구간이 정확히 트랙 목표(D-NAO-59) 원문
      *"Roas는 떨어지지만 매출이 늘어서 총 이익이 늘어나는 경우"*가 사는 자리다.
      그리고 이 tally가 그대로 패턴 후보의 승률이 되어 **지혜 승격 게이트**로 올라가는데
      (`_observation`), 북극성 M5의 성공 정의는 「지혜 -> 총이익 기여 양수」다 —
      승격 게이트와 그 층의 성공 정의가 다른 것을 재고 있었다(ref 90 §3).
    ★`target_roas_override`가 개입하지 않는 것도 의도다 — 본전은 사람이 덮어쓰는 값이 아니다
      (`campaign_target_resolver.resolve_bep_roas` 참조).
    ★`bep_cache`: 회차 내 캠페인별 캐시. 없으면 이 호출 한정(기존 호출부 호환).
    """
    cost = window.get("cost")
    if not cost:  # 0·None·결측 모두 중립 — 돈을 안 쓴 관찰은 원칙 증거가 아님
        return "neutral"
    bep_cache = bep_cache if bep_cache is not None else {}
    if entry.campaign_id in bep_cache:
        bep = bep_cache[entry.campaign_id]
    else:
        try:
            bep = campaign_target_resolver.resolve_bep_roas(db, entry.campaign_id).get("bep_roas")
        except Exception as e:  # noqa: BLE001 — 해석 실패는 후보 생성 skip(부풀림 방지)
            log.warning("wisdom_candidates: BEP 해석 실패(후보 skip): campaign=%s: %s", entry.campaign_id, e)
            bep = None
        bep_cache[entry.campaign_id] = bep
    if bep is None:
        return None
    roas_c = window.get("roas_c")
    good = roas_c is not None and roas_c >= float(bep)
    return "good" if good else "bad"


def _observation(campaign_id: str, action: str | None, env: dict, good_count: int, bad_count: int) -> str:
    """규칙 기반 요약문(LLM 아님) — 시그니처(조건)가 무엇을 묶는지 + 현재 good/bad 성적 한 줄.
    방향을 고정 서술하지 않고 tally로 서술한다(리뷰 P2-2: 조건 후보가 승률을 담는다)."""
    total = good_count + bad_count
    return (
        f"[패턴] 캠페인 {campaign_id}의 {action or '(액션미상)'} — "
        f"{env['day_class']}·{env['season'] or '계절미상'}·아이폰 {env['iphone_window']} 조건에서 "
        f"관찰 {total}회(good {good_count}/bad {bad_count})."
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
    bep_cache: dict[str, object] = {}  # 회차 내 캠페인별 BEP 캐시(행마다 재해석하지 않는다)
    totals = {"scanned": 0, "new": 0, "updated": 0, "revived": 0,
              "skipped_no_outcome": 0, "skipped_no_target": 0, "skipped_neutral": 0,
              "skipped_terminal": 0, "skipped_search_term_grain": 0, "errors": 0}
    for entry in rows:
        try:
            # ★검색어 제외 행은 수확하지 않는다(D-NAO-178). 이 행들의 outcome["d1"]은
            #   `_grain_and_target`의 campaign 폴백 탓에 **그 캠페인 전체의 하루 성과**이지
            #   조치의 성적이 아니다(2026-08-13 라이브: d1 43,084원 vs 「골프」 30일 31,411원).
            #   진실을 d1_st에 적으면서 거짓 d1을 계속 먹이면 「측정 정합」이 아니다.
            #   판정 규칙(_outcome_window/_outcome_direction) 변경이 아니라 **알려진 거짓 입력의
            #   차단**이고, S8에서 이 skip을 걷으면 그대로 복원된다(가역).
            if entry.target_type == "search_term":
                totals["skipped_search_term_grain"] += 1
                continue
            outcome = json.loads(entry.outcome_json) if entry.outcome_json else {}
            window = _outcome_window(outcome) if outcome else None
            if window is None:
                totals["skipped_no_outcome"] += 1
                continue
            totals["scanned"] += 1
            direction = _outcome_direction(db, entry, window, bep_cache=bep_cache)
            if direction is None:
                totals["skipped_no_target"] += 1
                continue
            if direction == "neutral":  # cost=0 — tally·후보 미기여(리뷰 P2-3)
                totals["skipped_neutral"] += 1
                continue

            env = {
                "day_class": _day_class(entry),
                "season": entry.season,
                "iphone_window": _iphone_window(entry.iphone_launch_offset_days),
            }
            # 시그니처 = 조건만(방향 제외). 결과는 good_count/bad_count로 센다(리뷰 P2-2).
            signature = (
                f"{entry.campaign_id}|{entry.action}"
                f"|{env['day_class']}|{env['season']}|{env['iphone_window']}"
            )

            cand = (
                db.query(OpsWisdomCandidate)
                .filter(OpsWisdomCandidate.signature == signature)
                .first()
            )
            if cand is not None:
                if cand.status in _TERMINAL_STATUSES:  # promoted/rejected — 판사 판정 완료
                    totals["skipped_terminal"] += 1
                    continue
                ids = json.loads(cand.source_entry_ids_json or "[]")
                if entry.id in ids:  # 같은 행 재스캔 — 부풀림·부활 금지
                    continue
                if cand.status == "hidden":  # 망각분 재등장 → 부활(Ebbinghaus 재노출, 리뷰 P2-1)
                    cand.status = "pending"
                    totals["revived"] += 1
                ids.append(entry.id)
                cand.source_entry_ids_json = json.dumps(ids)
                if direction == "good":
                    cand.good_count = (cand.good_count or 0) + 1
                else:
                    cand.bad_count = (cand.bad_count or 0) + 1
                cand.occurrences = (cand.good_count or 0) + (cand.bad_count or 0)  # good+bad 합
                cand.observation = _observation(
                    entry.campaign_id, entry.action, env, cand.good_count or 0, cand.bad_count or 0
                )
                cand.last_seen_at = now
                db.commit()
                totals["updated"] += 1
            else:
                good_count = 1 if direction == "good" else 0
                bad_count = 1 if direction == "bad" else 0
                cand = OpsWisdomCandidate(
                    signature=signature, campaign_id=entry.campaign_id, action=entry.action,
                    env_bucket_json=json.dumps(env, ensure_ascii=False),
                    observation=_observation(entry.campaign_id, entry.action, env, good_count, bad_count),
                    occurrences=1, good_count=good_count, bad_count=bad_count,
                    first_seen_at=now, last_seen_at=now,
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
