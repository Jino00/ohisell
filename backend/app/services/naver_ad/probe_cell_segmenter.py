# probe_cell_segmenter.py — probe_cell_segmenter SA (단일 책임: 셀 세분화 유의성 LLM 판정)
"""D-NAO-58 CD4 세분화 판정층. 표본이 충분한 coarse 환경 셀(day_class만)에 대해, 다음 세분
축(iphone_window·season)으로 쪼갠 하위셀의 클릭곡선이 유의하게 다른지 독립 LLM 판사에게
묻는다.

★판정과 승격의 관계(P3-2 리뷰 정정): 이 판사의 verdict(split/keep)는 산술 optimal_band 승격과
**병렬 advisory**다(계획 D-58-11). CD4 승격 판정(learned_probe_rank)은 산술 조건과 독립이며 이
판사의 verdict에 게이팅되지 않는다(승격을 fail-open LLM에 의존시키면 결정론·견고성 훼손). 판사의
세분 권고는 근거로 observe 일기 rationale에 남아 볼트에 열람되고(P2-1), CD5가 소비한다.

fail-open: LLM 호출 실패/응답 불충분은 verdict="keep"(세분 안 함, 후보 유지). invoke는
LLM 주입경계(기본=expert_llm._invoke_claude, 테스트=stub) — wisdom_judge 전례.
읽기만(naver_keyword_hourly, probe_cell_aggregate 경유) — 쓰기 0(원칙18-1).
"""
from __future__ import annotations

import json
import logging
from datetime import date

from sqlalchemy.orm import Session

from app.services.naver_ad.expert_llm import _invoke_claude
from app.services.naver_ad.probe_cell_aggregate import (
    _MIN_CELL_DAYS,
    _MIN_CELL_IMP,
    _WINDOW_DAYS,
    aggregate_cells,
)

log = logging.getLogger(__name__)

# day_class 다음 세분 후보축(순서) — fine 축 = ("day_class",) + 이것.
_SEG_AXES_NEXT = ("iphone_window", "season")

_SEG_MODEL = "opus"
_SEG_TIMEOUT_S = 120
_MAX_PER_RUN_DEFAULT = 5

_SYSTEM = (
    "당신은 오하이 네이버 SA 광고 운영의 '환경 셀 세분화 판사'입니다. 아래는 요일계층(day_class)"
    "만으로 묶은 coarse 환경 셀 1개의 순위 밴드별 클릭곡선(ctr_shrunk)과, 그 셀을 아이폰 출시창"
    "(iphone_window)·계절(season) 축으로 더 잘게 쪼갠 하위셀들의 같은 곡선입니다. 하위셀 곡선이"
    "coarse 곡선과 유의하게 다른 패턴(예: 최적 순위 밴드 자체가 바뀜)을 보이면 split, 표본이"
    "얇거나 차이가 노이즈 수준이면 keep으로 판정하세요. 반드시 아래 JSON만 응답하세요(다른 텍스트"
    ' 없이): {"verdict": "split" 또는 "keep", "axis": split이면 어느 축이 유의한지(iphone_window|'
    'season|null), "rationale": 판정 근거(필수, 한국어)}.'
)

_SCHEMA = {"verdict": "split|keep", "axis": "iphone_window|season|null", "rationale": "string"}


def _band_curve(cell_view: dict) -> dict:
    """셀 뷰 → 프롬프트용 밴드 곡선 요약(ctr_shrunk·imp만, Decimal은 str로)."""
    return {
        band: {"imp": v["imp"], "ctr_shrunk": str(v["ctr_shrunk"])}
        for band, v in cell_view.get("bands", {}).items()
    }


def _prompt(cell: str, coarse_agg: dict, sub_cells: dict) -> str:
    view = {
        "coarse_cell": cell,
        "coarse_imp": coarse_agg["imp"], "coarse_days": coarse_agg["days"],
        "coarse_bands": _band_curve(coarse_agg),
        "sub_cells": {label: _band_curve(v) for label, v in sub_cells.items()},
    }
    return (
        "아래는 세분화 판정 대상 환경 셀 1건입니다(JSON). 이 안의 정보만 근거로 판정하세요.\n\n"
        f"{json.dumps(view, ensure_ascii=False, default=str)}"
    )


def _sub_cells_of(fine_cells: dict, coarse_cell: str) -> dict:
    """fine(day_class+iphone_window+season) 결과에서 coarse_cell로 시작하는 하위셀만 골라낸다."""
    return {
        label: v for label, v in fine_cells.items()
        if label == coarse_cell or label.startswith(coarse_cell + "|")
    }


def _judge_one(invoke, cell: str, agg: dict, sub_cells: dict) -> dict:
    """후보 1건 LLM 판정. 실패/불충분 응답 → fail-open keep."""
    try:
        res = invoke(_prompt(cell, agg, sub_cells), system=_SYSTEM, schema=_SCHEMA,
                     model=_SEG_MODEL, timeout=_SEG_TIMEOUT_S)
        parsed = (res or {}).get("json") or {}
        verdict = parsed.get("verdict")
        if verdict not in ("split", "keep"):
            verdict = "keep"
        axis, rationale = parsed.get("axis"), parsed.get("rationale") or ""
    except Exception as e:  # noqa: BLE001 — fail-open: 세분 안 함
        log.warning("probe_cell_segmenter: LLM 호출 실패(keep, fail-open): cell=%s: %s", cell, e)
        verdict, axis, rationale = "keep", None, f"LLM 실패(fail-open): {e}"
    return {
        "cell": cell, "verdict": verdict, "axis": axis, "rationale": rationale,
        "optimal_band": agg["optimal_band"], "imp": agg["imp"], "days": agg["days"],
    }


def judge_cell_segmentation(
    db: Session, *, as_of: date | None = None, window_days: int = _WINDOW_DAYS,
    campaign_id: str | None = None, invoke=None, max_per_run: int = _MAX_PER_RUN_DEFAULT,
) -> dict:
    """표본 임계(imp≥_MIN_CELL_IMP·days≥_MIN_CELL_DAYS) 충족 coarse 셀만 후보(회당 max_per_run개)로
    LLM 세분화 판정. 반환 {"judged":[{cell,verdict,axis,rationale,optimal_band,imp,days}...],
    "skipped":int}(skipped=임계 미달로 후보에서 빠진 셀 수)."""
    invoke = invoke or _invoke_claude
    coarse = aggregate_cells(db, as_of=as_of, window_days=window_days,
                             axes=("day_class",), campaign_id=campaign_id)
    fine = aggregate_cells(db, as_of=as_of, window_days=window_days,
                           axes=("day_class",) + _SEG_AXES_NEXT, campaign_id=campaign_id)

    all_cells = coarse["cells"]
    eligible = [
        (cell, agg) for cell, agg in all_cells.items()
        if agg["imp"] >= _MIN_CELL_IMP and agg["days"] >= _MIN_CELL_DAYS
    ]
    skipped = len(all_cells) - len(eligible)
    candidates = eligible[:max_per_run]

    judged = [
        _judge_one(invoke, cell, agg, _sub_cells_of(fine["cells"], cell))
        for cell, agg in candidates
    ]
    return {"judged": judged, "skipped": skipped}
