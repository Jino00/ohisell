# revenue_fee_source.py — 쿠팡 실측 판매수수료 단일 소스(SoT) Sub-Agent
# coupang_revenue_fee(매출내역, 옵션 그레인)의 실제 차감 수수료(service_fee+service_fee_vat=total_fee)를
# (order_id, vendor_item_id) 키로 제공한다. 구 대시보드(profit_calculator)와 종합조망(intelligence)이
# 같은 실측 정의(total_fee)를 공유하기 위한 단일 진실 원천. (PLAN_coupang-3p-fee-actualization D-A/D-B/D-E)
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import CoupangRevenueFee

_Z = Decimal("0")

# 쿠팡 3P 마켓플레이스 계정 — 실측 수수료 적용 대상(RG/로켓 제외, D-C).
COUPANG_3P_CODES = ("COUPANG_WING1", "COUPANG_WING2")


def actual_fee_by_order_option(
    db: Session,
    order_ids,
    account_keys: list[str] | None = None,
) -> dict[tuple[str, str, str], Decimal]:
    """주문번호 집합의 쿠팡 실차감 수수료를 (account_key, order_id, vendor_item_id)별 합산.

    total_fee = service_fee + service_fee_vat (쿠팡 실차감 = 종합조망 _agg_fees와 동일 정의).
    SALE/REFUND 순합(REFUND는 음수로 저장 — 사실 그대로, D-3, models.py 계약).
    grain(order_id, vendor_item_id, recognition_date, sale_type) → (account_key, order_id, vendor_item_id).
    ★account_key를 키에 포함(codex P1 #2 수용): 동일 (order_id, vid)가 타 계정에 있어도 교차합산 방지
      (D-8 전역유일에 암묵 의존하지 않고 명시적 계정 스코프). 호출부는 ch.code(=3P account_key)로 조인.
    recognition_date 무관하게 주문번호로 조인 → 정산 인식일↔주문일 축 어긋남 회피(D-B).
    account_keys 주면 해당 계정만(추가 필터). 데이터 없으면 빈 dict → 호출부가 정률 폴백(D-A).
    """
    ids = list({str(x) for x in order_ids if x})
    if not ids:
        return {}
    out: dict[tuple[str, str, str], Decimal] = {}
    CHUNK = 500
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i : i + CHUNK]
        q = db.query(
            CoupangRevenueFee.account_key,
            CoupangRevenueFee.order_id,
            CoupangRevenueFee.vendor_item_id,
            func.sum(CoupangRevenueFee.service_fee + CoupangRevenueFee.service_fee_vat),
        ).filter(CoupangRevenueFee.order_id.in_(chunk))
        if account_keys:
            q = q.filter(CoupangRevenueFee.account_key.in_(account_keys))
        rows = q.group_by(
            CoupangRevenueFee.account_key,
            CoupangRevenueFee.order_id,
            CoupangRevenueFee.vendor_item_id,
        ).all()
        for ak, oid, vid, fee in rows:
            out[(str(ak), str(oid), str(vid))] = Decimal(str(fee if fee is not None else 0))
    return out
