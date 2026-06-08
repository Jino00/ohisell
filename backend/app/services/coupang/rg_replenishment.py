# rg_replenishment.py — RG 발송관제 Harness (트랙 RG-Replenishment S5, 원칙 18-6 정보유통 허브)
# 단일 책임: 현재고 보유 옵션 모집단에 대해 SA들을 묶어 "권장 발송일·수량"을 배치 역산한다.
# 흐름(정보 유통):
#   estimate_sales_velocities(1회) ─┐
#   estimate_lead_times(1회)        ├─→ 옵션별 calc_replenishment(velocity=, lead_time=, current_stock= 주입)
#   _load_inventory(1회, 모집단)    ─┘     → status별 집계 + 발송 권장 목록
# ★배치 효율(원칙 18-8): 속도·리드타임은 전체 1회만 산출하고 옵션별로 주입 → N×전체스캔 방지.
#   데이터 없는 옵션은 명시적 None 주입(_UNSET 아님) → calc가 재계산 없이 insufficient 처리.
# ★등가성 계약: 주입 결과는 calc_replenishment를 미주입(_UNSET, 단일 SA 직접 호출)으로 부른 것과
#   '정확히 동일'해야 한다. 단일↔배치 SA 출력 형태 차이를 _velocity_for/_lead_for가 메운다(아래 주석).
# SA 직접 호출이 아니라 이 Harness를 라우터가 호출한다(원칙 18-7, 3개 SA 가로지르는 오케스트레이션).
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.models import CoupangProductItem, CoupangRgInventory
from app.services.coupang.lead_time_estimator import estimate_lead_times
from app.services.coupang.replenishment_calc import DEFAULT_TARGET_DAYS, calc_replenishment
from app.services.coupang.sales_velocity_estimator import estimate_sales_velocities
from app.utils.kst import kst_today

log = logging.getLogger(__name__)

# status별 정렬·집계 우선순위(긴급한 것 먼저). UI 상단에 발송 임박이 오도록.
_STATUS_ORDER = {"reorder_now": 0, "ok": 1, "insufficient_data": 2, "well_stocked": 3}


# ════════════════════════════════════════════════
# 모집단 조회 (현재고 보유 옵션) — 읽기 전용 1회
# ════════════════════════════════════════════════
def _load_inventory(db: Session, account_key: str | None = None) -> dict[str, int | None]:
    """모집단 = rg_inventory에 행이 있는 옵션(vendor_item_id) → orderable_qty.

    orderable_qty가 NULL이면 값도 None(calc가 insufficient no_inventory 처리 — _load_stock과 동일).
    이 1회 조회가 옵션별 _load_stock(N회 쿼리)를 대체한다."""
    q = db.query(CoupangRgInventory.vendor_item_id, CoupangRgInventory.orderable_qty)
    if account_key:
        q = q.filter(CoupangRgInventory.account_key == account_key)
    return {str(vii): qty for vii, qty in q.all()}


# ════════════════════════════════════════════════
# 등가성 어댑터 — 배치 SA 출력 → 단일 SA 출력과 동일한 주입값
# ════════════════════════════════════════════════
def _velocity_for(vii: str, velocities: dict) -> dict | None:
    """배치 estimate_sales_velocities → 옵션 vii의 velocity 주입값(단일 estimate_sales_velocity와 등가).

    ★배치 options[vii] est에는 segment_factors·trust_days가 없다(단일 함수는 반환 직전 붙임).
      calc의 _confidence가 segment_factors로 신뢰도를 판정하므로, 붙이지 않으면 항상 low로 오판된다.
      → 글로벌 segment_factors·trust_days를 병합해 단일 호출과 정확히 동일한 dict로 만든다.
    ★단일 함수는 base_source=='none'이면 None을 반환 → 같은 규칙 적용(배치 trust_days==0 엣지 방어)."""
    est = velocities["options"].get(vii)
    if not est or est.get("base_source") == "none":
        return None
    return {
        **est,
        "segment_factors": velocities["segment_factors"],
        "trust_days": velocities["trust_days"],
    }


def _lead_for(vii: str, lead_times: dict) -> dict | None:
    """배치 estimate_lead_times → 옵션 vii의 lead 주입값(단일 estimate_lead_time과 등가).

    ★입고 표본 0개 옵션은 배치 options에 아예 없다. 단일 함수는 이 경우에도 글로벌로 폴백하므로,
      options에 없으면 글로벌(source='global')로 폴백해야 등가가 성립한다(글로벌도 없으면 None).
    표본 있는 옵션(<MIN_SAMPLES 폴백 포함)은 배치가 이미 options에 채워둠(source 필드로 구분)."""
    opt = lead_times["options"].get(vii)
    if opt is not None:
        return opt
    g = lead_times["global"]
    if g is not None:
        return {**g, "source": "global"}
    return None


# ════════════════════════════════════════════════
# 정렬 / 집계
# ════════════════════════════════════════════════
def _sort_key(item: dict) -> tuple:
    """긴급도 정렬: status 우선순위 → 안전재고 도달 임박(days_to_safety 오름차순) → 옵션ID."""
    order = _STATUS_ORDER.get(item.get("status"), 9)
    # insufficient/well_stocked엔 days_to_safety가 없을 수 있음 → 뒤로.
    days = item.get("days_to_safety")
    days = days if isinstance(days, (int, float)) and not isinstance(days, bool) else 10**9
    return (order, days, item.get("vendor_item_id", ""))


def _summarize(items: list[dict]) -> dict:
    """status별 개수 + low confidence 개수 집계."""
    summary = {"total": len(items), "reorder_now": 0, "ok": 0,
               "well_stocked": 0, "insufficient_data": 0, "low_confidence": 0}
    for it in items:
        st = it.get("status")
        if st in summary:
            summary[st] += 1
        if it.get("confidence") == "low":
            summary["low_confidence"] += 1
    return summary


# ════════════════════════════════════════════════
# 공개 API (Harness 본체) — 라우터가 이것만 호출(원칙 18-7)
# ════════════════════════════════════════════════
def build_replenishment_plan(
    db: Session,
    account_key: str | None = None,
    *,
    target_days: int = DEFAULT_TARGET_DAYS,
) -> dict:
    """현재고 보유 옵션 전체에 대한 권장 발송 계획(배치 역산).

    속도·리드타임을 각 1회 산출 후 옵션별 calc_replenishment에 주입(원칙 18-8) → status별 결과·집계 반환.
    반환: {generated_at·account_key·target_days·trust_days·velocity_meta·lead_global·summary·items}."""
    today: date = kst_today()
    velocities = estimate_sales_velocities(db, account_key)
    lead_times = estimate_lead_times(db, account_key)
    inventory = _load_inventory(db, account_key)

    # 상품명·옵션명 1회 조회 (UI 표시용 — 메인 테이블과 동일하게 '상품명 + 옵션명').
    # product_name=seller_product_name, item_name=옵션명. 둘 다 없으면 vendor_item_id 폴백.
    name_rows = (
        db.query(
            CoupangProductItem.vendor_item_id,
            CoupangProductItem.seller_product_name,
            CoupangProductItem.item_name,
        )
        .filter(CoupangProductItem.vendor_item_id.in_(list(inventory.keys())))
        .all()
    )
    names: dict[str, tuple[str | None, str | None]] = {
        str(r[0]): (r[1], r[2]) for r in name_rows
    }

    items: list[dict] = []
    for vii, stock in inventory.items():
        velocity = _velocity_for(vii, velocities)
        lead = _lead_for(vii, lead_times)
        # 셋 다 주입 → calc는 DB 미접근(_UNSET 분기 스킵). 데이터 없는 옵션은 None 주입으로 재계산 없음.
        result = calc_replenishment(
            db, vii, account_key,
            target_days=target_days,
            current_stock=stock,
            velocity=velocity,
            lead_time=lead,
        )
        product_name, option_name = names.get(vii, (None, None))
        result["product_name"] = product_name           # 상품명(없으면 None → 프론트가 옵션명만 표시)
        result["item_name"] = option_name or vii         # 옵션명(없으면 옵션ID 폴백)
        items.append(result)

    items.sort(key=_sort_key)
    return {
        "generated_at": today.isoformat(),
        "account_key": account_key,
        "target_days": target_days,
        "trust_days": velocities.get("trust_days"),
        "velocity_meta": {
            "trust_start": velocities.get("trust_start"),
            "until": velocities.get("until"),
            "segment_days": velocities.get("segment_days"),
            "segment_factors": velocities.get("segment_factors"),
            "global_daily_rate": velocities.get("global_daily_rate"),
        },
        "lead_global": lead_times.get("global"),
        "summary": _summarize(items),
        "items": items,
    }
