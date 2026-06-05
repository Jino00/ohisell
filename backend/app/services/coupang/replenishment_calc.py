# replenishment_calc.py — RG 권장 발송 역산 SA (트랙 RG-Replenishment S4, D-2·D-4)
# 단일 책임: 옵션(vendor_item_id)별 현재고 + 일판매속도(S3) + 리드타임(S2) + 목표 2~3일치(D-2)를
#            모아 "며칠치 남음 · 권장 발송일 · 권장 수량"을 역산한다(읽기전용, 새 테이블 없음).
# 설계 결정(Jino 승인 2026-06-05):
#   ① 속도(S3) None 또는 리드타임(S2) None → status=insufficient_data, 추천 보류(정직, Jino 수동).
#   ② base_source가 sold_30d/order_item_low거나 요일계수 collecting이면 추천은 하되 confidence=low.
#   ③ 발송수량 목표일수 = 3일(D-2 "2~3일치"의 상한 — 과소발송보다 품절 회피, 보관료는 3일이면 짧음).
# 핵심: S3 segments(평일/주말/휴일 예상 일판매)로 오늘부터 하루씩 재고를 깎는 "요일 인지 forward 투영"
#       → 단순 평균 나눗셈이 아니라 실제 요일 믹스를 반영(S3 작업의 실사용 지점).
# 안전재고 = (p90리드 − mean리드) × 대표일판매 (리드 변동성 흡수분만, D-2 "최소로").
from __future__ import annotations

import logging
import math
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import CoupangRgInventory
from app.services.coupang.lead_time_estimator import estimate_lead_time
from app.services.coupang.sales_velocity_estimator import (
    SEGMENTS,
    _classify_day,  # 단일 진실 원천(holidays 라이브러리) 재사용 — 요일/휴일 분류 중복 방지
    estimate_sales_velocity,
)
from app.utils.kst import kst_today

log = logging.getLogger(__name__)

# ════════════════════════════════════════════════
# 상수
# ════════════════════════════════════════════════
DEFAULT_TARGET_DAYS = 3  # ③ D-2 "2~3일치" 상한. 발송수량 산정 기준 일수.
HORIZON_CAP = 120  # forward 투영 최대 일수(일판매 매우 작아도 무한루프 방지). 초과 시 well_stocked.
_EPS = 0.0001  # 소진 판정 epsilon. threshold 0(safety=0)도 '재고 ≤0'을 정확히 잡도록 하한.

_UNSET = object()  # "미주입"과 "계산했으나 데이터 없음(None)"을 구분하는 센티넬(S5 주입용, codex S4 리뷰).


# ════════════════════════════════════════════════
# forward 투영 헬퍼 (요일 인지)
# ════════════════════════════════════════════════
def _days_until_below(
    stock: float, segments: dict[str, float], threshold: float, start: date
) -> tuple[int, bool]:
    """오늘(start)부터 하루씩 segments[그날 구간]만큼 깎아 재고가 threshold 미만이 되는 첫 날까지의 일수.

    반환: (days, crossed). crossed=False면 HORIZON_CAP 내 미도달(사실상 '충분').
    threshold는 _EPS로 하한 처리 — safety=0이어도 '소진(≤0)'을 정확히 잡음(codex S4 리뷰)."""
    thr = max(threshold, _EPS)
    proj = stock
    d = start
    days = 0
    while proj >= thr:
        if days >= HORIZON_CAP:
            return days, False  # CAP까지 깎아도 threshold 위 → 미교차(충분)
        proj -= segments[_classify_day(d)]
        d += timedelta(days=1)
        days += 1
    return days, True


def _stock_after_days(stock: float, segments: dict[str, float], start: date, n_days: int) -> float:
    """오늘(start)부터 n_days일 뒤의 투영 재고(요일별 예상판매 차감). 음수 가능(품절 깊이)."""
    proj = stock
    d = start
    for _ in range(n_days):
        proj -= segments[_classify_day(d)]
        d += timedelta(days=1)
    return proj


# ════════════════════════════════════════════════
# 현재고 조회 (읽기 전용)
# ════════════════════════════════════════════════
def _load_stock(db: Session, vii: str, account_key: str | None = None) -> int | None:
    """옵션 현재 주문가능재고(orderable_qty). 재고 행 없으면 None(추정 불가)."""
    q = db.query(CoupangRgInventory.orderable_qty).filter(
        CoupangRgInventory.vendor_item_id == vii
    )
    if account_key:
        q = q.filter(CoupangRgInventory.account_key == account_key)
    row = q.first()
    if row is None:
        return None
    return row[0]


# ════════════════════════════════════════════════
# 신뢰도 판정
# ════════════════════════════════════════════════
def _confidence(velocity: dict, lead: dict) -> str:
    """추천 신뢰도. ② sold_30d/order_item_low·collecting·글로벌 리드 폴백이면 low, 아니면 ok."""
    if velocity.get("base_source") != "order_item":
        return "low"
    if lead.get("source") == "global":
        return "low"
    factors = velocity.get("segment_factors")
    if not factors:  # 요일계수 정보 자체가 없으면 모름 → 보수적으로 low(codex S4 리뷰)
        return "low"
    if any(f.get("confidence") == "collecting" for f in factors.values()):
        return "low"
    return "ok"


# ════════════════════════════════════════════════
# 추정 핵심 (옵션 1개)
# ════════════════════════════════════════════════
def _calc(
    vii: str,
    current_stock: int | None,
    velocity: dict | None,
    lead: dict | None,
    target_days: int,
    today: date,
) -> dict:
    """순수 계산(조회 분리). 입력이 부족하면 insufficient_data."""
    # ① 데이터 전무 → 추천 보류(정직).
    if current_stock is None:
        return {"vendor_item_id": vii, "status": "insufficient_data", "reason": "no_inventory"}
    if velocity is None:
        return {
            "vendor_item_id": vii,
            "status": "insufficient_data",
            "reason": "no_velocity",
            "current_stock": current_stock,
        }
    if lead is None:
        return {
            "vendor_item_id": vii,
            "status": "insufficient_data",
            "reason": "no_lead_time",
            "current_stock": current_stock,
        }

    base_rate = float(velocity["base_rate"])  # velocity가 not None이면 base_source != none → >0
    segments = {s: float(velocity["segments"][s]) for s in SEGMENTS}
    mean_lead = float(lead["mean"])
    p90_lead = float(lead["p90"])
    lead_days = math.ceil(p90_lead)  # 보수적: 발송일 산정·도착 투영에 p90(올림) 사용

    # 안전재고 = (p90 − mean) × 대표일판매. 리드 변동성 흡수분만(D-2).
    safety_stock = max(0.0, (p90_lead - mean_lead) * base_rate)

    days_to_safety, safety_crossed = _days_until_below(current_stock, segments, safety_stock, today)
    days_to_zero, _ = _days_until_below(current_stock, segments, 0.0, today)

    confidence = _confidence(velocity, lead)
    base = {
        "vendor_item_id": vii,
        "current_stock": current_stock,
        "daily_base_rate": round(base_rate, 3),
        "base_source": velocity["base_source"],
        "segments": {s: round(segments[s], 3) for s in SEGMENTS},
        "lead_mean": round(mean_lead, 2),
        "lead_p90": round(p90_lead, 2),
        "lead_source": lead.get("source"),
        "safety_stock": round(safety_stock, 1),
        "days_to_zero": days_to_zero,
        "days_to_safety": days_to_safety,
        "target_days": target_days,
        "confidence": confidence,
    }

    # 재고가 충분 — 투영 한도(HORIZON_CAP) 내에 안전재고선 도달 안 함 → 발송 불필요.
    if not safety_crossed:
        return {**base, "status": "well_stocked", "ship_by_date": None, "recommend_qty": 0}

    # 도착 마감일(재고가 안전재고 밑으로 떨어지는 날)에 입고가 도착해야 함.
    deadline = today + timedelta(days=days_to_safety)
    ship_by = deadline - timedelta(days=lead_days)
    urgent = ship_by <= today

    # 권장 수량 = 목표레벨 − (실제 발송분 도착 시점의 투영 재고).
    #   목표레벨 = target_days × 일판매 + 안전재고. 급한 경우 오늘 발송 → 도착이 마감보다 늦어 투영재고↓(품절) → 수량↑.
    actual_ship = today if urgent else ship_by
    arrival_offset = (actual_ship - today).days + lead_days
    proj_at_arrival = _stock_after_days(current_stock, segments, today, arrival_offset)
    target_level = target_days * base_rate + safety_stock
    qty = math.ceil(target_level - proj_at_arrival)
    qty = max(0, qty)

    return {
        **base,
        "status": "reorder_now" if urgent else "ok",
        "ship_by_date": ship_by.isoformat(),
        "recommend_qty": qty,
    }


# ════════════════════════════════════════════════
# 공개 API
# ════════════════════════════════════════════════
def calc_replenishment(
    db: Session,
    vendor_item_id: str,
    account_key: str | None = None,
    *,
    target_days: int = DEFAULT_TARGET_DAYS,
    current_stock=_UNSET,
    velocity=_UNSET,
    lead_time=_UNSET,
) -> dict:
    """단일 옵션 권장 발송 역산 (S5 Harness가 호출 — 원칙 18-8 optional 입력 설계).

    velocity/lead_time/current_stock를 미리 받으면 재계산 생략(Harness가 배치로 1회 산출해 주입).
    ★주입값으로 명시적 None을 넘기면 "계산했으나 데이터 없음"으로 해석(재계산 안 함) — _UNSET(미주입)과 구분.
    미주입 시 각 SA를 직접 호출. 반환은 status별 dict(insufficient_data/well_stocked/ok/reorder_now)."""
    vii = str(vendor_item_id)
    today = kst_today()
    if current_stock is _UNSET:
        current_stock = _load_stock(db, vii, account_key)
    if velocity is _UNSET:
        velocity = estimate_sales_velocity(db, vii, account_key)
    if lead_time is _UNSET:
        lead_time = estimate_lead_time(db, vii, account_key)
    return _calc(vii, current_stock, velocity, lead_time, target_days, today)


# 참고: 전체 옵션 배치 역산(속도·리드타임 1회 산출 후 옵션별 주입)은 3개 SA를 가로지르는
#       오케스트레이션이므로 SA가 아닌 S5 rg_replenishment Harness의 책임(원칙18-7).
