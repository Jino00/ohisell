# bm_harness.py — BM(벤치마크) 학습 레이어 Harness (관찰 전용, D-NAO-78)
# 역할: SA 조합·프라이어 유통 허브(원칙18-6). Phase 1은 SA-1(구조 스냅샷)만 호출한다.
#   전면 fail-open — 관찰·열람 전용 잡이라 어떤 실패도 아침배치 catch-up 체인·집행 잡을
#   막지 않는다(§0 금지선 5). 네이버 API 쓰기 손(naver_execution_harness/naver_sa_writer)은
#   import조차 하지 않는다(§0 금지선 1 · 원칙18-1 단일 책임).
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.services.naver_ad.bm_snapshot import snapshot_entities

log = logging.getLogger(__name__)


def run_bm_layer(db: Session) -> dict:
    """BM 레이어 1회 실행(07:37 KST, entity_sync 07:35 직후). Phase 1 = SA-1 스냅샷만.

    전면 fail-open: 각 SA 예외를 로깅 후 삼킨다(관찰 잡이 다른 크론을 못 막게). 각 SA는
    독립 try로 감싸 하나가 실패해도 나머지가 돈다.
    """
    result: dict = {"snapshot": None}

    try:
        result["snapshot"] = snapshot_entities(db)
    except Exception as e:  # noqa: BLE001 — 관찰 잡 fail-open(§0-5)
        log.exception("[BM] SA-1 구조 스냅샷 실패(fail-open): %s", e)

    # ── 후속 Phase 자리(현재 미구현) ──
    # P2: SA-2 조작 감지(bm_diff.detect_agency_ops) — 스냅샷 D-1 vs D diff → naver_agency_op
    # P3: 차원 보강(예산·확장검색·제외키워드·소재수) — 일별/주간 grain
    # P4: SA-3 벤치마크(bm_benchmark) + 프라이어 배선(B-X·IU-R·SS4)
    # P5: 예외 브리핑(bm_briefing) → ops_diary_entries(observe) + Slack 아침 푸시
    return result
