# diary_reflection.py — daily_reflection_sa (D-NAO-54 P2 해석층, docs/PLAN_naver-ad-diary-wisdom.md §P2)
# 역할: 어제(결과 대기)·그제(+D+1 결과)·D-8(+D+7 결과) diary 행을 환경 조건(요일·휴일·계절·
#   아이폰 오프셋·페이싱·순위)과 함께 모아 독립 LLM(expert_llm, expert_desk와 동일 wrapper·
#   모델·타임아웃 관례)에게 "한 일↔결과"를 관찰·서술만 시킨다(전략 지시·실행 제안 금지). 결과는
#   ops_diary_entries에 event_type='observe' 1행으로 저장(마이그레이션 불필요, PLAN §P2 확정).
#   읽기(diary) + diary 테이블 쓰기(diary.write_diary_entry 재사용)만.
from __future__ import annotations

import json
import logging
from collections import OrderedDict
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import OpsDiaryEntry
from app.services.naver_ad.diary import write_diary_entry
from app.services.naver_ad.diary_outcome import EVENT_TYPES, _kst_date
from app.services.naver_ad.expert_llm import _invoke_claude
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# expert_desk(ava_reviewer)와 동일 관례 — 배치 1콜 기준(D-NAO-30 초깃값).
_REFLECTION_MODEL = "opus"
_REFLECTION_TIMEOUT_S = 120

_SYSTEM = (
    "당신은 오하이의 네이버 SA 광고 운영 일기 해석자입니다. 아래 JSON은 우리가 최근에 한 일"
    "(입찰 집행·가드레일 차단·거부·킬스위치)과 그 사후 결과(D+1/D+7 비용·클릭·전환·보정ROAS),"
    "그리고 각 행위 시점의 환경 조건(요일 weekday 0=월, 휴일 is_kr_holiday, 계절 season, 아이폰"
    "출시 오프셋 iphone_launch_offset_days, 소진 페이싱 spend_pacing_pct, 순위 avg_rank)입니다.\n"
    "규칙: ①한 일과 결과를 환경 조건과 연결해 '관찰'만 서술합니다. 전략 지시·실행 제안·입찰"
    "변경 권고는 절대 하지 마세요. ②환경 조건(요일·휴일·계절·아이폰 오프셋)이 결과와 같이"
    "움직인 것으로 보이면 그 관찰을 명시하되, 단정적 인과가 아니라 '같은 조건에서 반복 관찰'의"
    "언어로 적으세요. ③avg_rank는 D-1 스윕 기준(집행 시점이 아니라 최대 하루 스테일)임을"
    "전제하세요. ④d1 결과의 전환·보정ROAS는 간접전환(~1일 정착)이 아직 덜 여문 시점의 캡처라"
    "구조적으로 저평가될 수 있습니다 — 낮은 d1 roas_c를 확정 결과로 단정하지 말고, d7이 있으면"
    "d7을 우선하세요. ⑤outcome의 d1_st는 검색어 제외 조치의 **검색어 단위** 사후 결과입니다 — 같은"
"행의 d1은 캠페인 전체 배경 성과이므로 둘을 같은 대상의 값으로 섞지 마세요. status는 stopped"
"(제외가 비용을 끊었다=의도된 성공, 비용 0을 실패로 서술하지 마세요)·leaking(비용이 계속 나감)"
"·ambiguous(앞 50자가 같은 검색어가 섞여 귀속 불명)·no_data(원료 부재로 판단 불가, 0이 아님)"
"입니다. ⑤-1 outcome의 **probation**은 «복귀(재개방) 실험»의 사후 결과이고 d1_st와 **자가**"
"다릅니다 — 제외는 비용 정지가 성공이지만 복귀는 돈이 다시 나가야 실험이 성립합니다. 그러니"
"probation이 있는 행에 stopped/leaking 어휘를 쓰지 말고, 그 행의 d7(캠페인 배경)을 조치 성적으로"
"우선하지도 마세요(규칙 ④는 캠페인 grain 조치용입니다). status는 profitable(창 RoAS가 BEP 이상"
"=제외가 틀렸다는 실측)·unprofitable(제외가 옳았다)·silent(열었는데 노출·클릭·비용 0 — **성공이"
"아니라 실험이 정보를 못 낸 것**입니다, 좋게 서술하지 마세요)·unverified(판정 근거 부족, 사유는"
"unverified_reason)·ambiguous·no_data입니다. ⑥JSON에 없는 수치를 지어내지 마세요. 한국어 자유"
"서술로 답하세요."
)


def _already_reflected_today(db: Session, today: date) -> bool:
    """같은 날 reflection 행이 이미 있으면 재생성 안 함(catch-up 이중 방지). 최신 observe/
    daily_reflection 행의 created_at(UTC)을 KST 날짜로 환산해 today와 비교."""
    row = (
        db.query(OpsDiaryEntry)
        .filter(OpsDiaryEntry.event_type == "observe", OpsDiaryEntry.action == "daily_reflection")
        .order_by(OpsDiaryEntry.created_at.desc())
        .first()
    )
    return row is not None and row.created_at is not None and _kst_date(row.created_at) == today


def _entry_view(e: OpsDiaryEntry) -> dict:
    """LLM 프롬프트용 일기 행 뷰(환경 컬럼·outcome 포함)."""
    return {
        "date": _kst_date(e.created_at).isoformat() if e.created_at else None,
        "event_type": e.event_type, "actor": e.actor, "campaign_id": e.campaign_id,
        "target_type": e.target_type, "target_id": e.target_id, "action": e.action,
        "before": e.before_value, "after": e.after_value, "rationale": e.rationale,
        "env": {
            "weekday": e.weekday, "is_kr_holiday": e.is_kr_holiday, "season": e.season,
            "iphone_launch_offset_days": e.iphone_launch_offset_days,
            "spend_pacing_pct": e.spend_pacing_pct, "avg_rank": e.avg_rank,
        },
        "outcome": json.loads(e.outcome_json) if e.outcome_json else None,
    }


def _merged_view(entries: list[OpsDiaryEntry]) -> dict:
    """blocked+reject 2행(같은 제안의 hold 시점+말미 stale 정리, 연결키 없음)을 1사건으로 병합
    (P1 리뷰 P3-2 dedup 계약 — 이중계상 금지). blocked 행을 base로 삼아 환경을 취한다."""
    base = next((e for e in entries if e.event_type == "blocked"), entries[0])
    view = _entry_view(base)
    view["event_type"] = "blocked+reject"
    view["dedup_note"] = "같은 제안의 hold(blocked)+stale정리(reject) 2행을 1사건으로 병합(이중계상 금지)"
    return view


def _dedup(entries: list[OpsDiaryEntry]) -> list[dict]:
    """(campaign_id, target_id, action, KST날짜) 단위 dedup. blocked+reject가 함께 있으면 1건으로
    병합, 그 외는 각각 그대로."""
    groups: OrderedDict[tuple, list[OpsDiaryEntry]] = OrderedDict()
    for e in entries:
        key = (e.campaign_id, e.target_id, e.action, _kst_date(e.created_at) if e.created_at else None)
        groups.setdefault(key, []).append(e)
    views: list[dict] = []
    for es in groups.values():
        types = {e.event_type for e in es}
        # 정확히 blocked+reject 둘 다 있을 때만 병합(P2 리뷰 P3-4: blocked 2행만 있는 키를
        # 병합하면 'blocked+reject' 오라벨 + 별개 사건(일/시간 레인 각각 hold) 과소집계).
        if len(es) > 1 and types == {"blocked", "reject"}:
            views.append(_merged_view(es))
        else:
            views.extend(_entry_view(e) for e in es)
    return views


def _gather(db: Session, today: date, now: datetime) -> dict:
    """어제(D-1)·그제(D-2)·D-8 diary 이벤트 행을 버킷으로 수집(각 버킷 dedup)."""
    targets = {today - timedelta(days=1): "yesterday",
               today - timedelta(days=2): "d2_with_d1_outcome",
               today - timedelta(days=8): "d8_with_d7_outcome"}
    lower_utc = (now - timedelta(hours=9)) - timedelta(days=9)
    rows = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.event_type.in_(EVENT_TYPES),
        OpsDiaryEntry.created_at.isnot(None),
        OpsDiaryEntry.created_at >= lower_utc,
    ).all()
    buckets: dict[str, list[OpsDiaryEntry]] = {v: [] for v in targets.values()}
    for e in rows:
        label = targets.get(_kst_date(e.created_at))
        if label is not None:
            buckets[label].append(e)
    return {label: _dedup(es) for label, es in buckets.items()}


def _build_prompt(buckets: dict) -> str:
    return (
        "아래는 최근 우리 네이버 SA 광고 운영 일기와 사후 결과입니다(JSON). yesterday=어제 한 일"
        "(결과 아직 대기), d2_with_d1_outcome=그제 한 일과 그 다음날(D+1) 결과, "
        "d8_with_d7_outcome=8일 전 한 일과 사후 7일(D+7) 결과입니다.\n\n"
        f"{json.dumps(buckets, ensure_ascii=False, default=str)}\n\n"
        "이 안의 정보만 근거로, 한 일과 결과가 환경 조건(요일·휴일·계절·아이폰 오프셋·페이싱·"
        "순위)과 어떻게 같이 움직였는지 관찰을 한국어로 서술하세요. 전략·실행 제안은 하지 마세요."
    )


def build_reflection(db: Session, *, now: datetime | None = None, invoke=_invoke_claude) -> dict:
    """일일 해석문 생성·저장. 일기 행 0건이면 LLM 미호출 skip, LLM 실패는 fail-open(내일 재시도).

    invoke는 LLM 주입경계(기본=expert_llm._invoke_claude 실 claude, 테스트=가짜) — ava_reviewer 전례.
    """
    now = now or kst_now()
    today = now.date()
    if _already_reflected_today(db, today):
        return {"skipped": "already_exists"}
    buckets = _gather(db, today, now)
    total = sum(len(v) for v in buckets.values())
    if total == 0:
        return {"skipped": "no_entries"}  # 비용·소음 방지

    prompt = _build_prompt(buckets)
    try:
        res = invoke(prompt, system=_SYSTEM, schema=None, model=_REFLECTION_MODEL, timeout=_REFLECTION_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 — fail-open: 내일 크론이 다시
        log.exception("diary_reflection: LLM 호출 실패(fail-open): %s", e)
        return {"error": str(e), "entries": total}

    text = (res.get("text") or "").strip()
    if not text:
        return {"error": "empty_response", "entries": total}
    # diary.write_diary_entry 재사용(독립 세션·fail-open·짧은 트랜잭션). campaign_id=''(계정 전체).
    write_diary_entry(db, "observe", "", actor="system", action="daily_reflection", rationale=text, now=now)
    return {"written": True, "entries": total, "text_len": len(text)}
