# search_term_px_briefing.py — 파워링크 자동 제외·복귀 예외 브리핑 + 대행사 고비용 검색어
#   주간 브리핑 (스프린트 PX4, docs/PLAN_naver-ad-powerlink-autoexclude.md §4). 역할(SA):
#   ①run_exclusion_exception_briefing — search_term_ss_lane(PX2·PX3)의 실행 결과(ss dict,
#   원칙18-8 optional 파라미터)를 소비해 diary(observe)+Slack으로 표면화한다. 실행(제외·개방·
#   재제외·restored 전이)이 있었던 날만 — 없으면 완전 침묵(예외 브리핑 원칙 D-NAO-79, bm_briefing.py
#   P5 관례와 달리 diary조차 남기지 않는다, PLAN §4 1 "없던 날은 침묵" 명문).
#   ②run_agency_powerlink_weekly_briefing — 스코프 밖(대행사) 고비용 파워링크 검색어를 주간
#   (일요일, bm_deep과 같은 리듬)에만 diary로 전달(실쓰기 0). 신규 진입만 추적하려면 "이전에
#   브리핑된 (adgroup,term)" 이력을 남기는 신규 상태가 필요해 스키마 변경이 생긴다(PX4는 마이그
#   레이션 없음, PLAN §5) — PLAN §4 2가 명시적으로 허용한 단순화(주 1회 전체 브리핑)를 택했다.
#   관찰·열람 전용 — naver_execution_harness/naver_sa_writer는 import조차 하지 않는다(bm_briefing.py
#   §0 금지선 5 관례 계승). fail-open: 이 모듈의 실패가 이미 커밋된 PX2/PX3 실쓰기를 되돌리지
#   않는다 — write_diary_entry·slack_notifier.notify_text는 각자 이미 fail-open이고(내부
#   try/except), 호출부(scheduler_service)가 이 함수 자체 호출도 독립 try로 감싼다.
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import NaverSearchTermExclusion
from app.services.naver_ad import search_term_judge
from app.services.naver_ad.diary import ACTOR_DAILY, write_diary_entry
from app.services.naver_ad.slack_notifier import notify_text
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# ops_diary_entries.action 값 — vault_export가 observe 행의 action을 해석문 섹션 제목으로
# 그대로 쓴다(bm_briefing.py:28 관례와 동형, 신규 렌더 로직 불필요).
ACTION_EXCLUSION_BRIEFING = "search_term_exclude_exception"
ACTION_AGENCY_WEEKLY_BRIEFING = "agency_powerlink_weekly"

_TOP_N = 10  # 브리핑 상위 나열 건수(초과분은 "외 N건"으로 압축, 콘솔/diary 범람 방지 관례 계승)
_WEEKLY_WEEKDAY = 6  # 일요일(datetime.weekday(): 0=월…6=일) — bm_deep과 같은 리듬(bm_snapshot.py:230)


def _today_bounds(now: datetime) -> tuple[datetime, datetime]:
    """KST 자정 경계 [오늘 00:00, 내일 00:00) — last_transition_at(naive KST)과 비교용."""
    start = datetime.combine(now.date(), datetime.min.time())
    return start, start + timedelta(days=1)


def _rows_transitioned_today(
    db: Session, status: str, start: datetime, end: datetime,
) -> list[NaverSearchTermExclusion]:
    """오늘(KST) 그 상태로 전이된 행 — cost_at_exclusion 내림차순(브리핑 상위 나열용)."""
    return (
        db.query(NaverSearchTermExclusion)
        .filter(
            NaverSearchTermExclusion.status == status,
            NaverSearchTermExclusion.last_transition_at >= start,
            NaverSearchTermExclusion.last_transition_at < end,
        )
        .order_by(NaverSearchTermExclusion.cost_at_exclusion.desc())
        .all()
    )


def _fmt_rows(rows: list[NaverSearchTermExclusion]) -> str:
    top = rows[:_TOP_N]
    lines = [
        f"- {r.search_term}(그룹={r.adgroup_id}·cycle={r.cycle}·30d cost={r.cost_at_exclusion}원)"
        for r in top
    ]
    remainder = len(rows) - len(top)
    if remainder > 0:
        lines.append(f"- 외 {remainder}건")
    return "\n".join(lines)


def run_exclusion_exception_briefing(
    db: Session, ss: dict, *, now: datetime | None = None,
) -> dict:
    """§4 1 — 08:50 레인(search_term_ss_lane) 실행 결과를 소비해 자동 제외·복귀 예외 브리핑.

    ss = run_search_term_ss_lane()의 반환 dict(원칙18-8, SA 간 정보는 harness가 유통 — 이
    함수가 직접 judge/state 테이블 카운트를 재계산하지 않고 그 라운드가 이미 산출한 수치를
    그대로 쓴다). 트리거(§4 1 "실행이 있었던 날만"): 신규 제외(powerlink_fired)·재심사 재제외
    (reexam_reexcluded)·재심사 개방(reexam_opened)·restored 전이(reexam_restored) 중 하나라도
    >0. 전부 0이면 diary조차 남기지 않고 완전 침묵(D-NAO-79 — bm_briefing과 달리 "예외 없음"
    1줄도 안 남긴다, PLAN §4 1 명문 "없던 날은 침묵").

    상위 건 나열은 naver_search_term_exclusion에서 오늘(KST) last_transition_at 행을 다시
    읽는다(ss dict는 카운트만 갖고 있어 검색어·그룹·cost 상세는 상태 테이블에서 조회 — PX2/PX3가
    이미 upsert한 행을 소비만 한다)."""
    n = now or kst_now()
    excluded_ct = int(ss.get("powerlink_fired", 0)) + int(ss.get("reexam_reexcluded", 0))
    opened_ct = int(ss.get("reexam_opened", 0))
    restored_ct = int(ss.get("reexam_restored", 0))

    if excluded_ct == 0 and opened_ct == 0 and restored_ct == 0:
        log.info("[PX4] 파워링크 예외 브리핑: 오늘 실행 없음(완전 침묵, D-NAO-79)")
        return {
            "briefed": False, "excluded": 0, "opened": 0, "restored": 0, "slack_sent": False,
        }

    start, end = _today_bounds(n)
    excluded_rows = _rows_transitioned_today(db, "excluded", start, end) if excluded_ct else []
    opened_rows = _rows_transitioned_today(db, "probation", start, end) if opened_ct else []
    restored_rows = _rows_transitioned_today(db, "restored", start, end) if restored_ct else []

    header = f"{n.date().isoformat()} 파워링크 자동 제외 {excluded_ct}건 · 복귀 {opened_ct}건"
    if restored_ct:
        header += f" · 확정복귀 {restored_ct}건"
    parts = [header]
    if excluded_rows:
        parts.append("[제외]\n" + _fmt_rows(excluded_rows))
    if opened_rows:
        parts.append("[복귀(재노출 관찰 개시)]\n" + _fmt_rows(opened_rows))
    if restored_rows:
        parts.append("[확정복귀(재심사 회복 — 성과 자가 교정)]\n" + _fmt_rows(restored_rows))
    text = "\n".join(parts)

    write_diary_entry(
        db, "observe", "", actor=ACTOR_DAILY, action=ACTION_EXCLUSION_BRIEFING,
        rationale=text, now=n,
    )
    slack_result = notify_text(text, log_label="파워링크 자동 제외 브리핑")
    result = {
        "briefed": True, "excluded": excluded_ct, "opened": opened_ct, "restored": restored_ct,
        "slack_sent": bool(slack_result.get("sent")),
    }
    log.info("[PX4] 파워링크 예외 브리핑: %s", result)
    return result


def run_agency_powerlink_weekly_briefing(db: Session, *, now: datetime | None = None) -> dict:
    """§4 2 — 대행사(스코프 밖) 고비용 파워링크 검색어를 주간(일요일)에만 diary 브리핑.

    ★단순화 선택(PLAN §4 2가 명시 허용): "신규 진입만" 판정은 "이전에 브리핑에 나열된 적
    있는 (adgroup, term)" 이력을 어딘가 저장해야 하는데, PX4는 신규 테이블·컬럼을 만들지
    않는 스코프다(§5 페이즈표에 PX4 마이그레이션이 없다 — 마이그레이션은 GATE 이후 소급이
    아니라 PX2가 이미 끝냄). 대신 일요일(bm_deep 09:20과 같은 리듬, bm_snapshot.py:230)에만
    전체를 diary로 열람시키는 주 1회 리듬으로 노이즈를 억제한다 — 평일엔 완전 침묵(일요일이
    아니면 judge조차 재실행하지 않고 즉시 반환, 읽기 비용 0).

    judge_search_terms를 독립 재호출한다(read-only, BM SA-1/2/3가 서로 독립 재조회하는
    관례와 동형) — search_term_ss_lane이 만든 agency_powerlink 리스트를 이 모듈까지 들고
    오려면 그 함수의 반환 계약을 바꿔야 하는데, ss dict는 요약 카운트만 담는 게 기존 계약
    이라 원자료가 필요한 이 브리핑은 judge를 직접 소비한다(judge_search_terms 자체는 PX1
    산출물 — 소비만, 수정 없음). 실쓰기 없음·대행사 전달용."""
    n = now or kst_now()
    if n.weekday() != _WEEKLY_WEEKDAY:
        return {"briefed": False, "reason": "not_sunday", "agency_candidates": 0}

    judged = search_term_judge.judge_search_terms(db, now=n)
    agency = judged.get("agency_powerlink", [])
    if not agency:
        log.info("[PX4] 대행사 파워링크 주간 브리핑: 이번 주 후보 없음(침묵)")
        return {"briefed": False, "reason": "no_candidates", "agency_candidates": 0}

    top = agency[:_TOP_N]
    lines = [
        f"- {c['search_term']}(그룹={c['adgroup_id']}·30d cost={c['cost']}원·clk={c['clk']})"
        for c in top
    ]
    remainder = len(agency) - len(top)
    if remainder > 0:
        lines.append(f"- 외 {remainder}건")
    text = (
        f"{n.date().isoformat()} 대행사 파워링크 고비용 검색어 주간 브리핑 {len(agency)}건"
        "(30d cost≥30,000원·clk≥10) — 실쓰기 없음·대행사 전달용\n" + "\n".join(lines)
    )

    write_diary_entry(
        db, "observe", "", actor=ACTOR_DAILY, action=ACTION_AGENCY_WEEKLY_BRIEFING,
        rationale=text, now=n,
    )
    result = {"briefed": True, "reason": None, "agency_candidates": len(agency)}
    log.info("[PX4] 대행사 파워링크 주간 브리핑: %s", result)
    return result
