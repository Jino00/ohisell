# bm_briefing.py — 예외 브리핑 표면화 (BM 벤치마크 레이어 Phase 5, D-NAO-78·79)
# 역할: SA-2(bm_diff)가 산출한 당일 naver_agency_op(is_exception=True)를 사람이 보는 채널로
#   표면화한다 — ①ops_diary_entries(event_type='observe')에 1행 기록 → 기존 vault_export가
#   당일 노트 "해석문" 섹션에 그대로 렌더(신규 렌더러 없음, diary_reflection.py 관례 편승)
#   ②slack_notifier로 아침 푸시(예외 0건인 날은 발송 생략 — 노이즈 방지, Obsidian에는
#   "예외 없음" 1줄만 남긴다). SA-3(bm_benchmark) 산출물의 주간(일요일) 요약도 같은 채널에
#   편승한다(run_weekly_benchmark_summary). D-NAO-79: "예외 브리핑이 주 UX, 전체 리포트는
#   온디맨드"— 이 파일은 그 주 UX를 만든다. 관찰·열람 전용 — 네이버 API 호출 0, 전체
#   fail-open(브리핑 실패가 SA-1/2/3·아침배치 체인을 못 막게 bm_harness가 독립 try로 감싼다,
#   §0 금지선 5). 실행 손(naver_execution_harness/naver_sa_writer)은 import조차 하지 않는다.
from __future__ import annotations

import json
import logging
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.models import NaverAgencyOp, NaverBmBenchmark, NaverEntity
from app.services.naver_ad import slack_notifier
from app.services.naver_ad.diary import write_diary_entry
from app.utils.kst import kst_now, kst_today

log = logging.getLogger(__name__)

# ops_diary_entries.action 값 — vault_export._render_diary_day가 observe 행의 action을
# 해석문 섹션 제목으로 그대로 쓴다(신규 렌더 로직 불필요, diary_reflection.py:155 관례와 동형).
ACTION_DAILY_BRIEFING = "agency_op"
ACTION_WEEKLY_SUMMARY = "bm_weekly_benchmark"

_MAX_SUMMARY_LINES = 20  # 요약 최대 건수(초과분은 "외 N건" 1줄로 압축, 콘솔 범람 방지 관례 계승)

# op_type → 한글 라벨(브리핑 가독성). bm_diff.py + ad_external_change.py(D-NAO-127)의 op_type
# 어휘와 1:1 대응. 없는 op_type은 원문 코드를 그대로 쓴다(지어내지 않는다).
_OP_TYPE_KR = {
    "bid_change": "입찰변경", "status_flip": "상태전환",
    "bid_mode_flip": "소재입찰모드전환", "ad_edit": "소재편집",
    "keyword_add": "키워드추가", "keyword_remove": "키워드삭제",
    "negative_add": "제외추가", "negative_remove": "제외삭제",
    "creative_change": "소재변경", "budget_change": "예산변경",
    "extended_toggle": "확장검색토글",
    "campaign_add": "캠페인신설", "adgroup_add": "그룹신설",
    "campaign_remove": "캠페인삭제", "adgroup_remove": "그룹삭제",
}


def _campaign_names(db: Session, campaign_ids: set[str]) -> dict[str, str]:
    """campaign_id → naver_entity.name. 이름 없으면 매핑에서 빠짐(호출부가 id로 폴백)."""
    if not campaign_ids:
        return {}
    return {
        e.entity_id: e.name
        for e in db.query(NaverEntity.entity_id, NaverEntity.name)
        .filter(NaverEntity.entity_type == "campaign", NaverEntity.entity_id.in_(campaign_ids))
        .all()
        if e.name
    }


def _fmt(v: str | None) -> str:
    return v if v not in (None, "") else "-"


def _op_line(op: NaverAgencyOp, names: dict[str, str]) -> str:
    """캠페인명·op_type·before→after·magnitude 압축 1줄(요청 스펙 §브리핑 형식).

    ★D-NAO-127: 소재 grain은 (a) 어느 소재인지와 (b) **언제 손댔는지**를 함께 낸다.
    occurred_at은 editTm으로 확정된 실제 편집 시각이라 "오늘 아침에 감지"보다 훨씬 쓸모가 있다
    (2026-07-29 실사고: 15:39:05에 되돌려진 것을 다음날 알았다). 값이 없는 grain(스냅샷 diff)은
    종전대로 시각 없이 출력한다 — 모르는 시각을 지어내지 않는다.
    """
    camp = names.get(op.campaign_id, op.campaign_id or "(계정 전체)")
    label = _OP_TYPE_KR.get(op.op_type, op.op_type)
    mag = f" ({op.magnitude:+.1f}%)" if op.magnitude is not None else ""
    who = f" · 소재 …{op.entity_id[-9:]}" if op.entity_type == "ad" and op.entity_id else ""
    when = f" · {op.occurred_at:%m-%d %H:%M}" if op.occurred_at is not None else ""
    return f"- {camp}{who} · {label}: {_fmt(op.before_value)}→{_fmt(op.after_value)}{mag}{when}"


def _build_ops_summary(ops: list[NaverAgencyOp], names: dict[str, str]) -> str:
    """최대 _MAX_SUMMARY_LINES건 압축, 초과분은 '외 N건' 1줄."""
    shown = ops[:_MAX_SUMMARY_LINES]
    lines = [_op_line(op, names) for op in shown]
    remainder = len(ops) - len(shown)
    if remainder > 0:
        lines.append(f"- 외 {remainder}건")
    return "\n".join(lines)


def run_daily_briefing(db: Session, *, op_date: date | None = None, now: datetime | None = None) -> dict:
    """당일 예외 브리핑 1회 실행 — diary 기록(항상) + Slack 푸시(예외>0일 때만).

    bm_harness.run_bm_layer가 SA-1/2/3 다음 독립 try로 호출한다(fail-open, §0 금지선 5) —
    이 함수 자체는 자기 방어 없이 예외를 그대로 던진다(호출부 계약, bm_snapshot/bm_diff와
    동일 관례). write_diary_entry는 자체가 이미 fail-open(내부 독립 세션·try)이라 diary 기록
    실패가 이 함수를 막지 않는다.
    """
    d = op_date or kst_today()
    n = now or kst_now()
    ops = (
        db.query(NaverAgencyOp)
        .filter(NaverAgencyOp.op_date == d, NaverAgencyOp.is_exception.is_(True))
        .order_by(NaverAgencyOp.detected_at.asc())
        .all()
    )

    if not ops:
        write_diary_entry(
            db, "observe", "", actor="system", action=ACTION_DAILY_BRIEFING,
            rationale=f"{d.isoformat()} 대행사 조작 예외 없음.", now=n,
        )
        log.info("[BM] P5 예외 브리핑: %s 예외 0건(발송 생략)", d)
        return {"op_date": str(d), "exceptions": 0, "slack_sent": False}

    names = _campaign_names(db, {op.campaign_id for op in ops if op.campaign_id})
    text = f"{d.isoformat()} 대행사 오늘 조작 {len(ops)}건\n{_build_ops_summary(ops, names)}"

    write_diary_entry(
        db, "observe", "", actor="system", action=ACTION_DAILY_BRIEFING, rationale=text, now=n,
    )
    slack_result = slack_notifier.notify_text(text, log_label="BM 예외 브리핑")
    result = {"op_date": str(d), "exceptions": len(ops), "slack_sent": bool(slack_result.get("sent"))}
    log.info("[BM] P5 예외 브리핑: %s", result)
    return result


def _bench_value_summary(bench_kind: str, val) -> str:
    """bench_kind별 사람이 읽는 1줄 요약. 모르는 kind·파싱실패는 원문 절단(지어내지 않음)."""
    if val is None:
        return "-"
    if bench_kind == "keyword_verified" and isinstance(val, list):
        return f"검증 키워드 {len(val)}개"
    if bench_kind == "bid_band" and isinstance(val, dict):
        return f"입찰밴드 [{val.get('min')}, {val.get('p50')}, {val.get('max')}]원"
    if bench_kind == "group_structure" and isinstance(val, dict):
        return (
            f"키워드수 p50 {val.get('keyword_count_p50')}"
            f" · 확장검색비율 {val.get('extended_search_ratio')}"
        )
    return json.dumps(val, ensure_ascii=False)[:120]


def run_weekly_benchmark_summary(db: Session, *, now: datetime | None = None) -> dict:
    """주간 벤치마크 요약(§5②) — naver_bm_benchmark 전량을 diary 주간 항목(observe)으로
    기록해 vault가 자연 픽업하게 한다(별도 렌더러 신설 없음). bm_harness.run_bm_deep(일요일
    09:20)이 독립 try로 호출(fail-open) — 이 함수 자체는 자기 방어 없이 예외를 던진다."""
    n = now or kst_now()
    rows = (
        db.query(NaverBmBenchmark)
        .order_by(NaverBmBenchmark.bench_kind, NaverBmBenchmark.bench_key)
        .all()
    )
    if not rows:
        write_diary_entry(
            db, "observe", "", actor="system", action=ACTION_WEEKLY_SUMMARY,
            rationale="이번 주 벤치마크 프라이어 없음(표본 부족 또는 미산출).", now=n,
        )
        return {"benchmarks": 0}

    lines = [f"{n.date().isoformat()} 주간 벤치마크 요약 ({len(rows)}건)"]
    for r in rows:
        try:
            val = json.loads(r.value_json) if r.value_json else None
        except (ValueError, TypeError):
            val = None
        conf = f"{r.confidence:.2f}" if r.confidence is not None else "-"
        lines.append(
            f"- [{r.bench_kind}] {r.bench_key}: {_bench_value_summary(r.bench_kind, val)}"
            f" (n={r.sample_n}, conf={conf})"
        )

    write_diary_entry(
        db, "observe", "", actor="system", action=ACTION_WEEKLY_SUMMARY,
        rationale="\n".join(lines), now=n,
    )
    result = {"benchmarks": len(rows)}
    log.info("[BM] P5 주간 벤치마크 요약: %s", result)
    return result
