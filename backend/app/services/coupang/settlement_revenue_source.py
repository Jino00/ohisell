# settlement_revenue_source.py — 쿠팡 3P 정산 매출(실지급 기준) 단일 소스 Sub-Agent
# coupang_revenue_fee(매출내역, 옵션 그레인)의 net 정산매출과 일자별 정산 주문번호 집합을
# (account_key, sale_date)별로 제공한다. net_profit 매출기준 정산화(S4 D-11~D-13)의 가산 보정 입력.
# 순수 읽기 SA — 조합/판정/차감 로직은 Harness가 담당한다(HTTP 없음, 부작용 없음).
#
# ★net 정의(라이브 확정 2026-06-20): net = Σ(SALE sale_amount) − Σ(REFUND sale_amount).
#   REFUND 행은 원판매와 동일한 **양수 미러**로 저장된다(전액환불 주문에서 SALE 179,000 ↔
#   REFUND 179,000 → net 0 실증). revenue_fee_source의 "REFUND 음수" 주석은 service_fee에만
#   해당하며 sale_amount엔 적용되지 않으므로 여기서 부호를 명시적으로 뺀다(추측 금지, 원칙22).
# ★일자 그레인 = sale_date(매출 발생일=주문일 축). recognition/settlement_date 아님 — net_profit이
#   주문일(order_date) 축으로 집계하므로 동일 축으로 맞춰 성숙도·보정 비교가 정렬된다.
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


def settlement_net_revenue_by_day(
    db: Session,
    dfrom: date,
    dto: date,
    account_keys: list[str] | None = None,
) -> dict[tuple[str, date], Decimal]:
    """(account_key, sale_date)별 net 정산매출 = Σ(SALE sale_amount) − Σ(REFUND sale_amount).

    REFUND는 양수 미러 저장이므로 sale_type=='REFUND'일 때 부호를 반전해 합산한다.
    account_keys 미지정 시 3P 마켓플레이스 계정(WING1·WING2)만(RG/로켓 제외, D-13).
    sale_date가 NULL인 행은 일자 그레인 부재로 제외(성숙 비교 불가).
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
            CoupangRevenueFee.account_key,
            CoupangRevenueFee.sale_date,
            func.sum(signed),
        )
        .filter(
            CoupangRevenueFee.account_key.in_(keys),
            CoupangRevenueFee.sale_date.isnot(None),
            CoupangRevenueFee.sale_date >= dfrom,
            CoupangRevenueFee.sale_date <= dto,
        )
        .group_by(CoupangRevenueFee.account_key, CoupangRevenueFee.sale_date)
        .all()
    )
    return {(ak, sd): _f(amt) for ak, sd, amt in rows}


def settled_order_ids_by_day(
    db: Session,
    dfrom: date,
    dto: date,
    account_keys: list[str] | None = None,
) -> dict[tuple[str, date], set[str]]:
    """(account_key, sale_date)별 정산 잡힌 주문번호 집합 — 성숙도(커버리지) 판정 입력(maturity_gate).

    그 날 우리 활성주문이 전부 이 집합에 들어오면 정산 성숙(D-11). SALE/REFUND 무관하게
    정산내역에 등장한 주문은 '쿠팡이 정산 인식함'으로 본다(부분환불 주문도 성숙으로 카운트).
    """
    keys = account_keys if account_keys is not None else list(COUPANG_3P_CODES)
    if not keys:
        return {}
    rows = (
        db.query(
            CoupangRevenueFee.account_key,
            CoupangRevenueFee.sale_date,
            CoupangRevenueFee.order_id,
        )
        .filter(
            CoupangRevenueFee.account_key.in_(keys),
            CoupangRevenueFee.sale_date.isnot(None),
            CoupangRevenueFee.sale_date >= dfrom,
            CoupangRevenueFee.sale_date <= dto,
        )
        .distinct()
        .all()
    )
    out: dict[tuple[str, date], set[str]] = {}
    for ak, sd, oid in rows:
        out.setdefault((ak, sd), set()).add(str(oid))
    return out
