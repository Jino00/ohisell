# settlement_revenue_source.py — 쿠팡 3P 정산 매출(실지급 기준) 단일 소스 Sub-Agent
# coupang_revenue_fee(매출내역)의 net 정산매출을 옵션라인 (order_id, vendor_item_id) 그레인으로
# 제공한다. net_profit 매출기준 정산화(S4 D-11~D-13)의 가산 보정 입력. 순수 읽기 SA(HTTP·부작용 없음).
#
# ★라인 그레인(codex P1#2 수용): 정산 그레인은 (order_id, vendor_item_id, recognition_date, sale_type)
#   이라 한 주문에 옵션이 여러 개면 일부만 정산될 수 있다. 주문번호 단위 성숙 판정은 이를 오판하므로
#   (order_id, vendor_item_id) 라인 단위로 제공한다. 반환 dict의 키 집합 = '정산 인식된 라인'(=성숙).
# ★net 정의(라이브 확정 2026-06-20): net = Σ(SALE sale_amount) − Σ(REFUND sale_amount).
#   REFUND 행은 원판매와 동일한 **양수 미러**로 저장된다(전액환불 SALE 179,000 ↔ REFUND 179,000 →
#   net 0 실증; prod 음수 sale_amount REFUND 0건 확인). revenue_fee_source의 "REFUND 음수" 주석은
#   service_fee(수수료 netting)에만 해당하며 sale_amount엔 적용되지 않으므로 부호를 명시적으로 뺀다.
# ★윈도우 = sale_date(매출 발생일=주문일 축). net_profit이 order_date로 집계하므로 동일 축 정렬.
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models import CoupangRevenueFee
from app.services.coupang.revenue_fee_source import COUPANG_3P_CODES

_Z = Decimal("0")


def _f(v) -> Decimal:
    """None/숫자 → Decimal. 필드 부재(None)는 정상 0."""
    if v is None:
        return _Z
    return v if isinstance(v, Decimal) else Decimal(str(v))


def settlement_net_by_line(
    db: Session,
    dfrom: date,
    dto: date,
    account_keys: list[str] | None = None,
) -> dict[tuple[str, str], Decimal]:
    """(order_id, vendor_item_id)별 net 정산매출 = Σ(SALE sale_amount) − Σ(REFUND sale_amount).

    REFUND는 양수 미러 저장이므로 sale_type=='REFUND'일 때 부호를 반전해 합산한다.
    account_keys 미지정 시 3P 마켓플레이스 계정(WING1·WING2)만(RG/로켓 제외, D-13).
    sale_date NULL 행은 일자 축 부재로 제외. 반환 키 = 정산 인식된 라인(성숙 판정의 진실).
    """
    keys = account_keys if account_keys is not None else list(COUPANG_3P_CODES)
    if not keys:
        return {}
    signed = case(
        (CoupangRevenueFee.sale_type == "REFUND", -CoupangRevenueFee.sale_amount),
        else_=CoupangRevenueFee.sale_amount,
    )
    rows = (
        db.query(
            CoupangRevenueFee.order_id,
            CoupangRevenueFee.vendor_item_id,
            func.sum(signed),
        )
        .filter(
            CoupangRevenueFee.account_key.in_(keys),
            CoupangRevenueFee.sale_date.isnot(None),
            CoupangRevenueFee.sale_date >= dfrom,
            CoupangRevenueFee.sale_date <= dto,
        )
        .group_by(CoupangRevenueFee.order_id, CoupangRevenueFee.vendor_item_id)
        .all()
    )
    return {(str(oid), str(vid)): _f(amt) for oid, vid, amt in rows}
