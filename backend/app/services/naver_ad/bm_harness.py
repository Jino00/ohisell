# bm_harness.py — BM(벤치마크) 학습 레이어 Harness (관찰 전용, D-NAO-78)
# 역할: SA 조합·프라이어 유통 허브(원칙18-6). run_bm_layer(일별 07:37)=SA-1 스냅샷+SA-2 diff+
#   SA-3 벤치마크+예외 브리핑(Phase 5). run_bm_deep(주간 bm_deep 레인, 일요일 09:20)=SA-1의
#   제외키워드·소재수 차원 보강(Phase 3)+주간 벤치마크 요약(Phase 5).
#   전면 fail-open — 관찰·열람 전용 잡이라 어떤 실패도 아침배치 catch-up 체인·집행 잡을
#   막지 않는다(§0 금지선 5). 네이버 API 쓰기 손(naver_execution_harness/naver_sa_writer)은
#   import조차 하지 않는다(§0 금지선 1 · 원칙18-1 단일 책임).
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.services.naver_ad.bm_benchmark import compute_benchmarks
from app.services.naver_ad.bm_briefing import run_daily_briefing, run_weekly_benchmark_summary
from app.services.naver_ad.bm_diff import detect_agency_ops
from app.services.naver_ad.bm_snapshot import snapshot_entities, update_deep_dimensions

log = logging.getLogger(__name__)


def run_bm_layer(db: Session) -> dict:
    """BM 레이어 1회 실행(07:37 KST, entity_sync 07:35 직후). P1 = SA-1 스냅샷(예산·확장검색
    Phase 3 일별 차원 포함), P2 = SA-2 diff.

    전면 fail-open: 각 SA 예외를 로깅 후 삼킨다(관찰 잡이 다른 크론을 못 막게). 각 SA는
    독립 try로 감싸 하나가 실패해도 나머지가 돈다. SA-2는 SA-1(오늘 스냅샷) 다음에 실행돼야
    당일 D 셋이 준비되지만, SA-1 실패 시에도 SA-2는 (전일까지의) D-1 vs D로 독립 시도한다.
    """
    result: dict = {"snapshot": None, "agency_ops": None, "benchmarks": None, "briefing": None}

    try:
        result["snapshot"] = snapshot_entities(db)
    except Exception as e:  # noqa: BLE001 — 관찰 잡 fail-open(§0-5)
        log.exception("[BM] SA-1 구조 스냅샷 실패(fail-open): %s", e)

    try:
        result["agency_ops"] = detect_agency_ops(db)
    except Exception as e:  # noqa: BLE001 — 관찰 잡 fail-open(§0-5)
        log.exception("[BM] SA-2 조작 감지 실패(fail-open): %s", e)

    # SA-3(P4): 오늘 스냅샷+14일 성과로 벤치마크 프라이어 재산출. SA-1(오늘 스냅샷) 다음에
    # 실행돼야 당일 D 셋을 대조하지만, 독립 try로 감싸 SA-1/2 실패와 무관하게(전일 스냅샷으로도)
    # 시도한다 — 프라이어 소비는 전부 fail-open이라 벤치마크가 비어도 소비 SA는 기존대로 돈다.
    try:
        result["benchmarks"] = compute_benchmarks(db)
    except Exception as e:  # noqa: BLE001 — 관찰 잡 fail-open(§0-5)
        log.exception("[BM] SA-3 벤치마크 산출 실패(fail-open): %s", e)

    # P5: 예외 브리핑(bm_briefing) → ops_diary_entries(observe) + Slack 아침 푸시(D-NAO-79 주 UX).
    # SA-2가 만든 오늘자 naver_agency_op를 읽으므로 SA-2 다음이 자연스럽지만, SA-2가 실패해도
    # (전일까지 남은 이벤트로) 독립 시도한다 — 관찰 잡끼리도 서로를 막지 않는다.
    try:
        result["briefing"] = run_daily_briefing(db)
    except Exception as e:  # noqa: BLE001 — 관찰 잡 fail-open(§0-5)
        log.exception("[BM] P5 예외 브리핑 실패(fail-open): %s", e)

    return result


def run_bm_deep(db: Session, *, snapshot_date: date | None = None) -> dict:
    """BM 주간 deep 레인 1회 실행(bm_deep, 일요일 09:20 KST, §7 — Phase 3 무거운 GET 분리).

    오늘 스냅샷 그룹 행에 제외키워드 수·소재 수를 채운다(update_deep_dimensions, ~1,100 GET/주).
    P5: naver_bm_benchmark 주간 요약도 같은 레인에서 diary에 편승(§5②) — 독립 try라 위 갱신이
    실패해도 시도한다. 전면 fail-open — 실패해도 다른 크론(일 레인)을 막지 않는다(§0 금지선 5).
    """
    try:
        result = update_deep_dimensions(db, snapshot_date=snapshot_date)
    except Exception as e:  # noqa: BLE001 — 관찰 잡 fail-open(§0-5)
        log.exception("[BM] SA-1 주간 deep 차원 갱신 실패(fail-open): %s", e)
        result = {"deep": None}

    result["weekly_summary"] = None
    try:
        result["weekly_summary"] = run_weekly_benchmark_summary(db)
    except Exception as e:  # noqa: BLE001 — 관찰 잡 fail-open(§0-5)
        log.exception("[BM] P5 주간 벤치마크 요약 실패(fail-open): %s", e)

    return result
