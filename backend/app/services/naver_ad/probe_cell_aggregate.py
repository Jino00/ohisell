# probe_cell_aggregate.py — probe_cell_aggregate SA (단일 책임: 환경 셀 × 순위 밴드 클릭곡선 집계)
"""D-NAO-58 CD4(환경별 학습·세분화층)의 재료 SA. NaverKeywordHourly의 시간당 imp/clk/avg_rank를
환경 셀(day_class 등)×순위 밴드로 버킷팅해 EB 축소추정(hierarchical_pooling.shrink)한 CTR 곡선을
만든다. 마이그레이션 없음 — 상태는 매 호출 재계산(순수 조회, 쓰기 0, 원칙18-1).

cell_leading_indicator는 NaverAdDaily에서 같은 환경 셀 단위(순위 무관)로 즉시구매/장바구니
원시 카운트만 뽑는다(가중·roas는 CD5). cart_*는 이 판정 지표에만 쓰고 매출/ROAS엔 절대 안
섞는다(CD1 회계 불변 원칙 계승).
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverKeywordHourly
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.services.naver_ad.diary import _KR_HOLIDAYS, _iphone_offset_days, _season_of
from app.services.naver_ad.hierarchical_pooling import shrink
from app.services.naver_ad.wisdom_candidates import _iphone_window
from app.utils.kst import kst_today

_Q4 = Decimal("0.0001")
_ZERO = Decimal("0")

# 순위 밴드 경계 — [lo, hi) 반개구간, 마지막(hi=None)은 lo 이상 전부. avg_rank None 행은 제외.
_RANK_BANDS: tuple[tuple[Decimal, Decimal | None, str], ...] = (
    (Decimal("1"), Decimal("2"), "1.0-2.0"),
    (Decimal("2"), Decimal("2.5"), "2.0-2.5"),
    (Decimal("2.5"), Decimal("3"), "2.5-3.0"),
    (Decimal("3"), Decimal("4"), "3.0-4.0"),
    (Decimal("4"), None, "4.0+"),
)

_MIN_CELL_IMP = 100    # 셀 판정/승격 최소 노출 합
_MIN_BAND_IMP = 30     # 밴드가 optimal_band 후보가 되려면 최소 노출
_MIN_CELL_DAYS = 3     # "3회 반복" = 창 안 서로 다른 ad_date ≥3
_CTR_SIGNAL_MIN = 0.01  # 최적 밴드 ctr_shrunk 하한 — 이하면 신호 없음(승격 안 함)
_WINDOW_DAYS = 30      # 기본 학습 창


def env_cell_of_date(d: date, *, axes: tuple[str, ...] = ("day_class",)) -> str:
    """axes 순서대로 조합한 환경 셀 라벨("|" 구분). day_class=휴일>주말>평일 우선순위,
    iphone_window=_iphone_window(_iphone_offset_days(d)), season=_season_of(d.month)."""
    parts: list[str] = []
    for axis in axes:
        if axis == "day_class":
            if d in _KR_HOLIDAYS:
                parts.append("holiday")
            elif d.weekday() >= 5:
                parts.append("weekend")
            else:
                parts.append("weekday")
        elif axis == "iphone_window":
            parts.append(_iphone_window(_iphone_offset_days(d)))
        elif axis == "season":
            parts.append(_season_of(d.month))
        else:
            raise ValueError(f"probe_cell_aggregate: 알 수 없는 axis '{axis}'")
    return "|".join(parts)


def rank_band_of(avg_rank) -> str | None:
    """avg_rank(Decimal/float/None) → 순위 밴드 라벨. None이거나 정의된 밴드 밖(<1)이면 None."""
    if avg_rank is None:
        return None
    r = Decimal(str(avg_rank))
    for lo, hi, label in _RANK_BANDS:
        if hi is None:
            if r >= lo:
                return label
        elif lo <= r < hi:
            return label
    return None


def _bucket_hourly_rows(rows, axes: tuple[str, ...]) -> dict:
    """행 리스트 → {cell: {"imp","clk","cost","dates"(set),"bands":{band:{"imp","clk","cost"}}}}."""
    cells: dict[str, dict] = {}
    for row in rows:
        band = rank_band_of(row.avg_rank)
        if band is None:
            continue
        cell = env_cell_of_date(row.ad_date, axes=axes)
        c = cells.setdefault(cell, {"imp": 0, "clk": 0, "cost": 0, "dates": set(), "bands": {}})
        c["imp"] += row.imp
        c["clk"] += row.clk
        c["cost"] += row.cost
        c["dates"].add(row.ad_date)
        b = c["bands"].setdefault(band, {"imp": 0, "clk": 0, "cost": 0})
        b["imp"] += row.imp
        b["clk"] += row.clk
        b["cost"] += row.cost
    return cells


def _finalize_cell(agg: dict) -> dict:
    """버킷 원시 집계 1셀 → 최종 뷰(ctr·ctr_shrunk·optimal_band 계산)."""
    cell_imp, cell_clk = agg["imp"], agg["clk"]
    cell_ctr_raw = (Decimal(cell_clk) / Decimal(cell_imp)) if cell_imp else _ZERO
    bands_out: dict[str, dict] = {}
    for band, bagg in agg["bands"].items():
        b_imp, b_clk = bagg["imp"], bagg["clk"]
        ctr_raw = (Decimal(b_clk) / Decimal(b_imp)) if b_imp else _ZERO
        ctr_shrunk = shrink(n=b_imp, raw=ctr_raw, prior=cell_ctr_raw)
        bands_out[band] = {
            "imp": b_imp, "clk": b_clk, "cost": bagg["cost"],
            "ctr_raw": ctr_raw.quantize(_Q4), "ctr_shrunk": ctr_shrunk.quantize(_Q4),
        }
    optimal_band = _optimal_band(bands_out)
    return {
        "imp": cell_imp, "clk": cell_clk, "cost": agg["cost"],
        "ctr": cell_ctr_raw.quantize(_Q4), "days": len(agg["dates"]),
        "bands": bands_out, "optimal_band": optimal_band,
    }


def _optimal_band(bands_out: dict) -> str | None:
    """imp≥_MIN_BAND_IMP 밴드 중 ctr_shrunk argmax, 단 ctr_shrunk≥_CTR_SIGNAL_MIN 일 때만."""
    candidates = [(b, v["ctr_shrunk"]) for b, v in bands_out.items() if v["imp"] >= _MIN_BAND_IMP]
    if not candidates:
        return None
    best_band, best_ctr = max(candidates, key=lambda x: x[1])
    return best_band if best_ctr >= Decimal(str(_CTR_SIGNAL_MIN)) else None


def aggregate_cells(
    db: Session, *, as_of: date | None = None, window_days: int = _WINDOW_DAYS,
    axes: tuple[str, ...] = ("day_class",), campaign_id: str | None = None,
) -> dict:
    """NaverKeywordHourly 창[as_of-window_days+1, as_of](avg_rank NOT NULL만) → 환경 셀×순위
    밴드 집계 + EB 축소 CTR. 빈 데이터면 cells={}(fabricate 금지)."""
    as_of = as_of or kst_today()
    date_from = as_of - timedelta(days=window_days - 1)
    q = db.query(NaverKeywordHourly).filter(
        NaverKeywordHourly.ad_date >= date_from,
        NaverKeywordHourly.ad_date <= as_of,
        NaverKeywordHourly.avg_rank.isnot(None),
    )
    if campaign_id:
        q = q.filter(NaverKeywordHourly.campaign_id == campaign_id)
    cells_raw = _bucket_hourly_rows(q.all(), axes)
    cells_out = {cell: _finalize_cell(agg) for cell, agg in cells_raw.items()}
    return {
        "cells": cells_out,
        "window": {"from": date_from.isoformat(), "to": as_of.isoformat()},
        "axes": list(axes),
    }


def cell_leading_indicator(
    db: Session, *, as_of: date | None = None, window_days: int = _WINDOW_DAYS,
    axes: tuple[str, ...] = ("day_class",), campaign_id: str | None = None,
) -> dict:
    """NaverAdDaily 창 → 환경 셀 단위(순위 무관) 즉시구매/장바구니 원시 카운트.
    BACKFILL_SENTINEL_ADGROUP 롤업 행 제외. {cell: {"immediate","carts"}}(가중·roas는 CD5)."""
    as_of = as_of or kst_today()
    date_from = as_of - timedelta(days=window_days - 1)
    q = db.query(NaverAdDaily).filter(
        NaverAdDaily.ad_date >= date_from,
        NaverAdDaily.ad_date <= as_of,
        NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
    )
    if campaign_id:
        q = q.filter(NaverAdDaily.campaign_id == campaign_id)
    out: dict[str, dict] = {}
    for row in q.all():
        cell = env_cell_of_date(row.ad_date, axes=axes)
        acc = out.setdefault(cell, {"immediate": 0, "carts": 0})
        acc["immediate"] += row.conv_direct_cnt + row.conv_indirect_cnt
        acc["carts"] += row.cart_direct_cnt + row.cart_indirect_cnt
    return out
