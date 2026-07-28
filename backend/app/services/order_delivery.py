# order_delivery.py — SA(단일 책임): 주문 원천 응답 → 배송 구분 판별 (유일한 판별 지점)
#
# 존재 이유(Jino 지시 2026-07-28: "이 배송비에 대해서 너가 판매된 내역에서 구분할 수 있어야해"):
# 배송 구분 정보가 orders.raw_data(JSON) 안에만 있고 컬럼이 없어, 계산할 때마다 파싱해 쓰고
# 버렸다(bep_calculator 전용). 그래서 판매 내역·리포트 어디서도 "이 주문이 N배송인가, 배송비를
# 고객이 냈나"를 조회·집계할 수 없었다. 이 모듈이 **판별을 한 곳에 모으고**, 동기화·백필·BEP가
# 전부 여기를 통해 같은 답을 얻는다(예전처럼 bep_calculator 안에만 두면 또 갈라진다).
#
# ★판별 계약(D-NAO-84 · 원칙22, 변경 금지):
#   productOrder.deliveryAttributeType == "ARRIVAL_GUARANTEE" → N배송(3,020)
#   그 외 값·필드 부재·파싱 실패·raw_data 부재                → 일반배송(1,900) fail-safe
#   동반 신호(logisticsCompanyId=="PG", logisticsCenterId, arrivalGuaranteeDate,
#   deliveryTagType=="TOMORROW")는 **참고만**, 판별에 쓰지 않는다(표본이 얇을 때 판별자를
#   늘리면 오탐 위험). 새 규칙을 임의로 추가하지 말 것 — 단일 판별자 원칙.
#
# ★배송방식(N배송/일반)과 배송비 부담(고객/우리)은 **독립 축**이다(라이브 실측 4조합 관측).
#   "N배송이면 항상 유료"가 아니다 — 두 축을 각각 저장하고 각각 조회한다.
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

# ── 배송방식 판별자 ──
NBAESONG_ATTR = "ARRIVAL_GUARANTEE"  # 품고 내일도착/도착보장
METHOD_NORMAL = "normal"
METHOD_NBAESONG = "nbaesong"

# ── D-NAO-57 (C) 우리가 지불하는 배송비: 건당 단가(부가세포함, Jino 확정 2026-07-18) ──
# ★단가는 상수(단가 개정 대비), 적용은 주문 건별 배송방식 판별 기반.
#   개정 시 과거 주문 원가가 소급 왜곡되지 않도록 **주문 행에 스냅샷으로 박아** 둔다
#   (orders.shipping_cost_paid) — 이 상수는 "지금 들어오는 주문"에만 적용된다.
SHIPPING_COST_NORMAL = Decimal("1900")     # 일반배송 건당
SHIPPING_COST_NBAESONG = Decimal("3020")   # N배송 건당 — D-NAO-84 실배선
SHIPPING_COST_BY_METHOD = {
    METHOD_NORMAL: SHIPPING_COST_NORMAL,
    METHOD_NBAESONG: SHIPPING_COST_NBAESONG,
}

# 영속 컬럼명(모델·백필·동기화가 공유 — 오타로 갈라지는 것을 막는다)
DELIVERY_COLUMNS = (
    "delivery_attribute_type",
    "delivery_policy_type",
    "shipping_fee_type",
    "logistics_company_id",
    "shipping_cost_paid",
)


def parse_raw_data(raw: Any) -> dict | None:
    """raw_data(dict | JSON str | None) → dict. 잘림/비JSON/None은 전부 None."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            return None  # 잘림(MAX_RAW_DATA_SIZE 초과)·비JSON
        return obj if isinstance(obj, dict) else None
    return None


def product_order_of(raw: Any) -> dict | None:
    """raw_data → productOrder dict. 네이버 주문이 아니면(키 부재) None.

    쿠팡/cafe24 raw_data엔 productOrder가 없다 → None → 배송 구분 컬럼은 NULL로 남는다
    (추정으로 채우지 않는다)."""
    parsed = parse_raw_data(raw)
    if not isinstance(parsed, dict):
        return None
    po = parsed.get("productOrder")
    return po if isinstance(po, dict) else None


def shipping_method_of(delivery_attribute_type: Any) -> str:
    """배송방식 판별 — 단일 판별자(deliveryAttributeType). 그 외 전부 일반배송."""
    return METHOD_NBAESONG if delivery_attribute_type == NBAESONG_ATTR else METHOD_NORMAL


def shipping_cost_paid_of(method: str) -> Decimal:
    """배송방식 → 우리가 지불하는 건당 배송비(현재 단가표)."""
    return SHIPPING_COST_BY_METHOD.get(method, SHIPPING_COST_NORMAL)


def delivery_fields(raw: Any) -> dict | None:
    """raw_data → 영속용 배송 구분 필드 dict. 판별 불가면 None(=컬럼 전부 NULL).

    반환 키 = DELIVERY_COLUMNS. shipping_cost_paid는 **판별 시점 단가 스냅샷**이다.
    ★None을 돌려주는 경우(네이버 주문 아님·raw_data 부재·JSON 잘림)는 컬럼을 NULL로 두고
      건수를 보고한다 — 추정으로 채우면 나중에 실측과 구분이 안 된다.
    """
    po = product_order_of(raw)
    if po is None:
        return None
    attr = po.get("deliveryAttributeType")
    method = shipping_method_of(attr)
    logi = po.get("logisticsCompanyId")
    return {
        "delivery_attribute_type": str(attr) if attr else None,
        "delivery_policy_type": _str_or_none(po.get("deliveryPolicyType")),
        "shipping_fee_type": _str_or_none(po.get("shippingFeeType")),
        "logistics_company_id": _str_or_none(logi),
        "shipping_cost_paid": shipping_cost_paid_of(method),
    }


def apply_delivery_fields(order, raw: Any) -> bool:
    """Order ORM 행에 배송 구분 컬럼을 반영. 판별 가능했으면 True.

    ★판별 불가(None)면 **기존 값을 지우지 않는다** — 재동기화 시 응답이 일시적으로 얇아도
      이미 확보한 실측을 잃지 않는다(백필도 같은 규칙)."""
    fields = delivery_fields(raw)
    if fields is None:
        return False
    for k, v in fields.items():
        setattr(order, k, v)
    return True


def order_shipping_cost(order_row: Any = None) -> Decimal:
    """주문 1건에 우리가 지불한 배송비(건당, 부가세포함).

    ★우선순위: ①영속 컬럼 shipping_cost_paid(주문 시점 단가 스냅샷) → ②raw_data 파싱 폴백
      → ③일반배송. ②는 백필 이전 행·판별 불가 행을 위한 안전망이라 종전 산출값과 동일하다
      (백필이 쓰는 값 = ②가 내는 값 → 두 경로가 같은 답).

    order_row: {"shipping_cost_paid": ..., "raw_data": ...} dict 또는 같은 속성을 가진 ORM 행.
    None이면 일반배송."""
    if order_row is None:
        return SHIPPING_COST_NORMAL
    if isinstance(order_row, dict):
        paid = order_row.get("shipping_cost_paid")
        raw = order_row.get("raw_data")
    else:
        paid = getattr(order_row, "shipping_cost_paid", None)
        raw = getattr(order_row, "raw_data", None)
    if paid is not None:
        try:
            return Decimal(str(paid))
        except (ValueError, TypeError, ArithmeticError):
            pass  # 이상값 → 파싱 폴백
    po = product_order_of(raw)
    if po is None:
        return SHIPPING_COST_NORMAL
    return shipping_cost_paid_of(shipping_method_of(po.get("deliveryAttributeType")))


def net_shipping_burden(paid: Any, collected: Any) -> Decimal:
    """실부담 배송비 = 우리 지불 − 고객 수취(clamp 없음, 조회·리포트용).

    ★BEP 계산은 별도로 max(0,·) 보수 클램프를 적용한다(배송 마진을 이익으로 인정하지 않음).
      리포트는 사실 그대로(음수 가능)를 보여준다 — 두 목적이 다르다."""
    p = Decimal(str(paid)) if paid is not None else Decimal("0")
    c = Decimal(str(collected)) if collected is not None else Decimal("0")
    return p - c


def _str_or_none(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None
