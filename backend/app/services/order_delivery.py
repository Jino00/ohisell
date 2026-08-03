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
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

_KST = timezone(timedelta(hours=9))

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

# ── 반품 회수비: 우리가 택배사에 지불하는 회수 1건당 단가(부가세포함) ──
# ⚠️ **미확정 추정치**(Jino 2026-08-03: "아마 우리도 2500원을 지급할꺼야"). 한진 청구서·계약
#   단가를 확보하면 이 상수 한 줄만 고치면 된다 — 그래서 계산 지점에 흩지 않고 여기 모은다.
# 배송방식이 아니라 **회수 택배사** 축인 이유: 라이브 반품 86건의 collectDeliveryCompany가
#   N배송 건 포함 전부 HANJIN이다(출고는 품고여도 회수는 한진). 배송방식으로 단가를 갈랐으면
#   N배송 반품에 품고 단가 3,020을 잘못 붙였을 것이다.
RETURN_PICKUP_COST_HANJIN = Decimal("2500")
RETURN_PICKUP_COST_BY_COMPANY = {"HANJIN": RETURN_PICKUP_COST_HANJIN}

# 영속 컬럼명(모델·백필·동기화가 공유 — 오타로 갈라지는 것을 막는다)
DELIVERY_COLUMNS = (
    "delivery_attribute_type",
    "delivery_policy_type",
    "shipping_fee_type",
    "logistics_company_id",
    "shipping_cost_paid",
)

# 반품 배송 손익용 영속 컬럼(같은 이유로 한 곳에 모음)
RETURN_COLUMNS = (
    "return_completed_at",
    "return_fee_demand_amount",
    "return_collect_company",
    "return_fee_support_amount",
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


def return_of(raw: Any) -> dict | None:
    """raw_data → return dict(반품 클레임 정보). 반품 아님·네이버 아님·파싱 실패는 None.

    ★`return` 키는 반품이 실제로 발생한 주문에만 붙는다(교환은 `exchange`). 여기서 None이면
      반품 배송 손익 컬럼을 **NULL로 남긴다** — 0으로 채우면 "미청구(실측 0)"와 구분이 사라진다.
    """
    parsed = parse_raw_data(raw)
    if not isinstance(parsed, dict):
        return None
    ret = parsed.get("return")
    return ret if isinstance(ret, dict) else None


def _kst_naive(value: Any) -> datetime | None:
    """ISO8601(오프셋 포함) → **KST 벽시계 naive** datetime. 파싱 불가는 None.

    ★naive KST로 떨어뜨리는 이유: orders.order_date가 이미 그 표현이다(라이브 실측
      `2026-08-03 14:36:12.456000` — 오프셋 없이 KST 벽시계). 여기서만 aware로 저장하면
      같은 테이블 안에서 두 시간 표현이 섞여 날짜 버킷이 9시간 어긋난다.
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(_KST).replace(tzinfo=None)
    return dt


def _decimal_or_zero(v: Any) -> Decimal:
    """금액 필드 → Decimal. 부재·이상값은 0(실측된 0과 같은 취급)."""
    try:
        return Decimal(str(v or 0))
    except (ValueError, TypeError, ArithmeticError):
        return Decimal(0)


def return_fields(raw: Any) -> dict | None:
    """raw_data → 반품 배송 손익용 영속 필드 dict. 반품 정보가 없으면 None(=컬럼 NULL 유지).

    반환 키 = RETURN_COLUMNS.
      return_completed_at       반품 완료일(귀속일) — 없으면 회수 완료일, 그것도 없으면 요청일.
      return_fee_demand_amount  고객에게 실제 청구한 반품비(**건별 실측**). 필드 부재 = 0(미청구).
      return_collect_company    회수 택배사(회수비 단가 판별자).
      return_fee_support_amount 네이버가 대신 부담하는 반품 배송비(claimDeliveryFeeSupportAmount).

    ★★지원액을 빼먹으면 안 되는 이유(2026-08-03 실제로 빼먹었다): N배송 반품 2건은 고객 청구가
      0인데, 그건 우리가 손실을 떠안았다는 뜻이 **아니라** 네이버가 `MEMBERSHIP_ARRIVAL_GUARANTEE`
      유형으로 5,500원을 지원하기 때문이다. 청구액만 보면 정확히 반대로 읽힌다.

    ★귀속일 폴백 순서의 근거: 라이브 86건은 returnCompletedDate가 100% 있다. 폴백은 미래에
      응답이 얇아졌을 때 **행을 통째로 잃지 않기 위한** 안전망이지, 평상시 경로가 아니다.
    ★청구액이 없으면 0인 이유: 라이브 20건이 `claimDeliveryFeePayMethod` 자체가 비어 있는
      **미청구** 건이다(우리가 회수비를 다 떠안은 반품). 여기서 정액을 넣으면 그 20건이
      통째로 틀린 수입이 된다 — 반품이 이익 나는 일처럼 보이게 만드는 정확히 그 실패다.
    """
    ret = return_of(raw)
    if ret is None:
        return None
    completed = (
        _kst_naive(ret.get("returnCompletedDate"))
        or _kst_naive(ret.get("collectCompletedDate"))
        or _kst_naive(ret.get("claimRequestDate"))
    )
    if completed is None:
        return None   # 귀속일이 없으면 어느 날에 실을지 정할 수 없다 → 추정하지 않는다
    return {
        "return_completed_at": completed,
        "return_fee_demand_amount": _decimal_or_zero(ret.get("claimDeliveryFeeDemandAmount")),
        "return_collect_company": _str_or_none(ret.get("collectDeliveryCompany")),
        "return_fee_support_amount": _decimal_or_zero(ret.get("claimDeliveryFeeSupportAmount")),
    }


def apply_return_fields(order, raw: Any) -> bool:
    """Order ORM 행에 반품 배송 손익 컬럼을 반영. 판별 가능했으면 True.

    ★apply_delivery_fields와 같은 규칙 — 판별 불가면 **기존 값을 지우지 않는다**.
      재동기화 응답이 일시적으로 얇아도 이미 확보한 실측을 잃지 않는다."""
    fields = return_fields(raw)
    if fields is None:
        return False
    for k, v in fields.items():
        setattr(order, k, v)
    return True


def return_pickup_cost(collect_company: Any = None) -> Decimal:
    """반품 회수 1건에 우리가 택배사에 지불하는 비용(부가세포함).

    ⚠️ 단가 자체는 **미확정 추정치**다(RETURN_PICKUP_COST_HANJIN 주석 참조).
    회수사 미상은 한진 단가로 폴백한다 — 라이브 86건이 전부 한진이라 그게 가장 가까운 답이고,
    0으로 두면 회수비가 통째로 사라져 반품이 이익 나는 일처럼 보인다(수입만 계상되므로)."""
    if not collect_company:
        return RETURN_PICKUP_COST_HANJIN
    return RETURN_PICKUP_COST_BY_COMPANY.get(str(collect_company), RETURN_PICKUP_COST_HANJIN)


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
