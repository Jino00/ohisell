# diary_outcome.py — outcome_backfill_sa (D-NAO-54 P2 해석층, docs/PLAN_naver-ad-diary-wisdom.md §P2)
# 역할: "한 일↔결과" 고리를 사후 소급으로 잇는다. 어제/그제/D-8 diary 행(execute/blocked/
#   reject/kill_switch)의 outcome_json에 D+1(완결 다음날 1일)·D+7(사후 7일) 실적을
#   naver_ad_daily에서 집계해 기입하고, 같은 target·같은 날 NaverRetroSignal이 있으면 채점
#   결과(board/direction/verdict)를 연결한다. Jino 문제의식("한 일만 적고 결과가 없다")의
#   직접 해소 지점. 읽기(diary·naver_ad_daily·retro) + diary 테이블 쓰기만(원칙18-1) —
#   제안 생성·실행 경로 접근 없음.
#
# ★완전성 금지(P1 리뷰 P3-4): diary는 best-effort(fail-open 소실 가능)라 "행 없음=아무 일도
#   없었음"이 아니다. 이 SA는 "남아 있는 diary 행"에만 결과를 소급 기입할 뿐, 전건 진실은
#   change_log가 소스다. avg_rank는 D-1 스윕 기준(스테일)·iphone offset은 caveat(P2 §env).
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverRetroSignal, OpsDiaryEntry
from app.services.naver_ad.bid_step_types import direction_of
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.services.naver_ad.diagnosis import correction_factor
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# 결과를 소급 채울 대상 이벤트(관찰 이벤트 observe는 제외 — 해석문 자기참조 방지).
EVENT_TYPES = ("execute", "blocked", "reject", "kill_switch")

# created_at(UTC) 스캔 하한 — 이보다 오래된 미완 행은 소급 대상에서 제외(무한 재스캔 방지).
_MAX_LOOKBACK_DAYS = 60


def _kst_date(created_at: datetime) -> date:
    """created_at은 UTC 저장([[sqlite-server-default-now-is-utc]]) → +9h 해서 KST 날짜."""
    return (created_at + timedelta(hours=9)).date()


def _grain_and_target(entry: OpsDiaryEntry) -> tuple[str, str | None]:
    """diary 행의 집계 grain. keyword/adgroup은 target_id 우선, 그 외(campaign·search_term·
    미지정)는 campaign 수준으로 폴백(PLAN §P2 대상 해상도)."""
    if entry.target_type == "keyword" and entry.target_id:
        return "keyword", entry.target_id
    if entry.target_type == "adgroup" and entry.target_id:
        return "adgroup", entry.target_id
    return "campaign", None


def _window_agg(
    db: Session, grain: str, target_id: str | None, campaign_id: str, date_from: date, date_to: date,
) -> tuple[int, int, int]:
    """사후창 (cost, clk, conv매출) 합계. retro_scorer._post_agg의 상세행 필터·conv 정의
    (직접+간접 매출)를 그대로 따르되 clk와 campaign grain을 확장한다(BACKFILL 센티넬 롤업
    행 제외 → 상세행과 이중계상 방지)."""
    q = db.query(
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0)
        + sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
    ).filter(
        NaverAdDaily.ad_date >= date_from, NaverAdDaily.ad_date <= date_to,
        NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
    )
    if grain == "keyword":
        q = q.filter(NaverAdDaily.keyword_id == target_id)
    elif grain == "adgroup":
        q = q.filter(NaverAdDaily.adgroup_id == target_id)
    else:
        q = q.filter(NaverAdDaily.campaign_id == campaign_id)
    row = q.one()
    return int(row[0]), int(row[1]), int(row[2])


def _window_metrics(
    db: Session, entry: OpsDiaryEntry, date_from: date, date_to: date, cf: float,
) -> dict:
    """한 사후창의 결과 metrics. roas_c = (conv매출/cost)×보정계수 — retro_scorer._judge와
    동일한 보정 ROAS 정의(cost=0이면 None)."""
    grain, target_id = _grain_and_target(entry)
    cost, clk, conv = _window_agg(db, grain, target_id, entry.campaign_id, date_from, date_to)
    roas_c = round((conv / cost) * cf, 4) if cost > 0 else None
    return {"cost": cost, "clk": clk, "conv": conv, "roas_c": roas_c}


def _retro_dict(sig: NaverRetroSignal) -> dict:
    """NaverRetroSignal → outcome_json['retro'] 서브딕(있는 값만). board/direction은 non-null."""
    d: dict = {"board": sig.board, "direction": sig.direction}
    if sig.verdict_d3 is not None:
        d["verdict_d3"] = sig.verdict_d3
    if sig.verdict_d7 is not None:
        d["verdict_d7"] = sig.verdict_d7
    return d


# diary action → retro direction (방향 일치 필터용, P2 리뷰 P3-3). 매핑 없는 action은 무필터.
# ★bid 계열(bid_up→up·bid_down→down)의 방향은 bid_step_types 레지스트리(단일 소스, IU-R R0)의
#   direction_of로 산출한다 — 종전 하드코딩 "up"/"down" 리터럴을 레지스트리로 이관(행위 불변:
#   같은 5개 키·같은 값 유지, growth_bid_up은 여전히 미매핑=무필터). 비-bid 액션(pause/update_bid/
#   set_user_lock)은 direction_of가 None이라 registry로 표현 불가 → 기존 매핑을 그대로 유지한다.
_ACTION_TO_DIRECTION = {"bid_up": direction_of("bid_up"), "bid_down": direction_of("bid_down"),
                        "pause": "pause", "update_bid": None, "set_user_lock": None}


def _find_retro(db: Session, target_id: str, asof: date, action: str | None) -> dict | None:
    """같은 target_id·같은 날(asof_date=diary 날짜) 채점 신호. P2 리뷰 P3-3: retro는 '제안
    방향' 추적이라 diary 행위와 동일 사건 보장이 없음 — action이 방향으로 매핑되면 direction
    일치 신호를 우선하고, 일치가 하나도 없으면 붙이지 않는다(오연결 방지). 매핑 불가 action은
    기존대로 verdict_d7 우선."""
    sigs = db.query(NaverRetroSignal).filter(
        NaverRetroSignal.target_id == target_id, NaverRetroSignal.asof_date == asof,
    ).order_by(NaverRetroSignal.id).all()
    if not sigs:
        return None
    want = _ACTION_TO_DIRECTION.get(action or "")
    if want is not None:
        sigs = [s for s in sigs if s.direction == want]
        if not sigs:
            return None
    best = next((s for s in sigs if s.verdict_d7 is not None), sigs[0])
    return _retro_dict(best)


def _backfill_row(db: Session, entry: OpsDiaryEntry, today: date, cf: float) -> dict:
    """한 diary 행의 outcome_json을 병합 갱신(기존 키 보존). 반환 = 이번에 채운 항목 카운트."""
    action_date = _kst_date(entry.created_at)
    age = (today - action_date).days
    outcome: dict = json.loads(entry.outcome_json) if entry.outcome_json else {}
    counts = {"d1": 0, "d7": 0, "retro": 0}

    if age >= 2 and "d1" not in outcome:  # 사후 D+1(완결 다음날) 단일일 = action_date+1
        d1_day = action_date + timedelta(days=1)
        outcome["d1"] = _window_metrics(db, entry, d1_day, d1_day, cf)
        counts["d1"] = 1
    if age >= 8 and "d7" not in outcome:  # 사후 7일 = action_date+1..action_date+7
        outcome["d7"] = _window_metrics(
            db, entry, action_date + timedelta(days=1), action_date + timedelta(days=7), cf
        )
        counts["d7"] = 1
    if entry.target_id:
        desired = _find_retro(db, entry.target_id, action_date, entry.action)
        if desired is not None and desired != outcome.get("retro"):
            outcome["retro"] = desired
            counts["retro"] = 1

    if any(counts.values()):
        entry.outcome_json = json.dumps(outcome, ensure_ascii=False)
    return counts


def backfill_outcomes(db: Session, *, now: datetime | None = None) -> dict:
    """어제/그제/D-8 diary 행에 D+1/D+7 결과와 retro 채점을 소급 기입(매일 08:35 하니스 호출).

    행별 try/except로 한 행 실패가 스윕을 못 죽이고, 유닛 증분 커밋(D-NAO-46② 쓰기락 교훈)으로
    긴 쓰기락을 피한다. created_at 하한(60일)으로 미완 행 무한 재스캔을 막는다.
    """
    now = now or kst_now()
    today = now.date()
    lower_utc = (now - timedelta(hours=9)) - timedelta(days=_MAX_LOOKBACK_DAYS)
    try:
        cf = float(correction_factor(db, today)["factor"])
    except Exception as e:  # noqa: BLE001 — 보정계수 실패는 1.0 폴백(roas_c만 무보정, 스윕 계속)
        log.warning("diary_outcome: 보정계수 산출 실패(cf=1.0 폴백): %s", e)
        cf = 1.0

    rows = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.event_type.in_(EVENT_TYPES),
        OpsDiaryEntry.created_at.isnot(None),
        OpsDiaryEntry.created_at >= lower_utc,
    ).all()

    totals = {"d1_filled": 0, "d7_filled": 0, "retro_linked": 0, "errors": 0}
    for entry in rows:
        try:
            c = _backfill_row(db, entry, today, cf)
            if any(c.values()):
                db.commit()  # 유닛 증분 커밋
            totals["d1_filled"] += c["d1"]
            totals["d7_filled"] += c["d7"]
            totals["retro_linked"] += c["retro"]
        except Exception as e:  # noqa: BLE001 — 한 행 실패가 스윕을 못 죽인다
            db.rollback()
            totals["errors"] += 1
            log.exception("diary_outcome: 행 소급 실패(id=%s): %s", getattr(entry, "id", "?"), e)
    return totals
