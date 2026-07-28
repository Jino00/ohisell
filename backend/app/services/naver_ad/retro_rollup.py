# retro_rollup.py — SA 소급 채점 rollup (D-NAO-45, PLAN_naver-ad-retro-scoring.md §5)
"""역할(SA·순수 함수): 진단 보드 as-of 스냅샷 행들을 지평(d3/d7)별 방향 정밀도로 접는다.

★DB를 읽지 않는다 — 행은 호출부가 넘긴다. 그래서 라우터(/retro-scorecard)와 성과뷰
  타임라인 하니스가 **같은 한 벌**을 쓴다. 종전엔 라우터 안의 비공개 함수였고, 그대로 두면
  타임라인이 같은 산식을 두 번째로 적어 넣게 된다(정의 중복 = 미래의 불일치 사고).

★정직 경계(ref 31 · 원칙22): 이건 **방향 정확도 계기판**이지 인과 성과 검증이 아니다.
  "우리 판단이 맞았나"까지고 "그래서 이익이 늘었나"는 카나리 몫이다.
"""
from __future__ import annotations

from app.models import NaverRetroSignal

VERDICTS = ("correct", "gray", "wrong", "no_spend")


def board_rollup(rows: list[NaverRetroSignal], horizon: int) -> dict:
    """단일 보드·단일 지평(d3/d7)의 rollup.

    n, correct/gray/wrong/no_spend,
    precision_spenders(= correct/(correct+gray+wrong) — no_spend 제외, 지출 지속 타깃 기준),
    bleed_sum(down/pause & verdict=correct 행의 양수 bleed 합, ref 31 §1-c와 동일 산식).
    """
    verdict_attr, bleed_attr = f"verdict_d{horizon}", f"bleed_post{horizon}"
    counts = dict.fromkeys(VERDICTS, 0)
    bleed_sum = 0
    for row in rows:
        verdict = getattr(row, verdict_attr)
        if verdict is None:  # 아직 채점 전(사후창 미도달) — rollup 대상 아님
            continue
        counts[verdict] = counts.get(verdict, 0) + 1
        if row.direction in ("down", "pause") and verdict == "correct":
            bleed_sum += max(0, getattr(row, bleed_attr) or 0)
    spenders = counts["correct"] + counts["gray"] + counts["wrong"]
    precision = round(counts["correct"] / spenders, 4) if spenders else None
    return {
        "n": spenders + counts["no_spend"],
        "correct": counts["correct"], "gray": counts["gray"],
        "wrong": counts["wrong"], "no_spend": counts["no_spend"],
        "precision_spenders": precision, "bleed_sum": bleed_sum,
    }
