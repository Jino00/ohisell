# learning_loops.py — learning_loops Harness (듀얼모드 스프린트 Phase 6, D-NAO-14/23)
# 역할: 학습루프 1(proposal_scoreboard)·2(estimate_calibrator)·3(conversion_maturity)·
#   5(hourly_pattern)·6(bid_rank_curve, IU-R R3) SA를 매일 순서 무관하게 조합 실행한다.
#   proposal_pipeline.run_daily와 동일한 단계격리 원칙(원칙18-6, codex #11/#12 전례) — 한 루프가
#   실패해도 나머지는 계속 진행하고 stage_status로 관측 가능하게 남긴다.
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.services.naver_ad import (
    bid_rank_curve, conversion_maturity, estimate_calibrator, hourly_pattern, proposal_scoreboard,
)
from app.utils.kst import kst_today

log = logging.getLogger(__name__)


def run_all(db: Session, *, today: date | None = None) -> dict:
    """4개 학습루프를 순서대로 실행 — 각각 독립 try/except로 격리.

    today 미지정 시 kst_today()로 해석한다(codex 지적 — 각 SA의 기본값 date.today()는
    서버 로컬 TZ를 쓰는데 prod는 UTC라 KST 08:10 실행 시각에도 "어제" 날짜로 잘못 해석될
    수 있다). estimate_calibrator만 as_of=today-1일(마지막 완결일)을 쓴다 — 오늘자
    naver_ad_daily는 아직 수집 전(07:30 크론이 어제치까지만 채움)이라 오늘을 포함하면
    트레일링 평균이 낮게 왜곡된다(proposal_pipeline.run_daily의 as_of 관례와 동일).
    """
    today = today or kst_today()
    result: dict = {"stage_status": {}, "errors": []}

    # IU-R R3: bid_rank_curve — 입찰→순위 반응 곡선. 원료 시계는 일 단위(NaverKeywordHourly D-1
    # 스윕)라 08:10 일 1회 크론과 정합하고, 서보 첫 스텝 정교화는 "익일 반영"이다(당일 변경의
    # h+1 버킷은 아직 미스윕 → 자연 제외, 다음 날 관측쌍화, PLAN §1-5). today 미지정 시 kst_today.
    for name, fn, kwargs in (
        ("proposal_scoreboard", proposal_scoreboard.run_daily, {"today": today}),
        ("estimate_calibrator", estimate_calibrator.run_daily, {"as_of": today - timedelta(days=1)}),
        ("conversion_maturity", conversion_maturity.run_daily, {"today": today}),
        ("hourly_pattern", hourly_pattern.run_daily, {"today": today}),
        ("bid_rank_curve", bid_rank_curve.run_daily, {"today": today}),
    ):
        try:
            result[name] = fn(db, **kwargs)
            result["stage_status"][name] = "ok"
        except Exception as e:  # noqa: BLE001 — 한 루프 실패가 나머지를 막지 않음
            db.rollback()
            log.exception("learning_loops: %s 단계 실패: %s", name, e)
            result["stage_status"][name] = "failed"
            result["errors"].append(f"{name}: {e}")

    log.info("learning_loops run_all: %s", result["stage_status"])
    return result
