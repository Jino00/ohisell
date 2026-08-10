# option_fee_rate.py — 쿠팡 3P 옵션별 판매수수료 «요율» 단일 소스(SoT) Sub-Agent
#
# ★왜 «금액»이 아니라 «요율»인가 (라이브 실측 2026-08-10, prod):
#   쿠팡 정산 행에서 service_fee = round(sale_amount × service_fee_ratio/100)이 661건 전수 성립하고
#   (어긋난 2건도 반올림 1원 미만, 최대 0.8원), service_fee_vat = round(service_fee × 0.1)은 661/661
#   정확히 성립한다. 즉 정산 통보는 «금액»을 알려주는 게 아니라 «요율»을 알려줄 뿐이다.
#   → 요율만 알면 정산(D+9~10)을 기다리지 않고 주문 시점에 수수료를 확정할 수 있다.
#   Jino 확정 원문(2026-08-10): "수수료는 정해진 수수료가 있으니까 그걸 떼면 되는거 아니야?"
#
# ★왜 옵션(vendor_item_id) 키인가:
#   같은 옵션이 시기에 따라 다른 요율로 정산된 사례가 전 계정 전 기간 **0건**이다(라이브 실측).
#   요율은 상품 카테고리가 정하므로 옵션당 상수로 취급해도 안전하다. 그래도 최신 정산 행을 쓴다
#   (쿠팡이 요율을 바꾸면 다음 정산부터 자동 반영 — 우리가 갱신할 것이 없다).
#
# ★실측 분포(2026-08-10): WING2 7.8%·6.4% / WING1 7.8%·10.5%·10.8%.
#   단일 7.8% 폴백은 WING2에선 과대, WING1에선 과소다. 계정 총액이 우연히 맞는다고 옵션이 맞는 게 아니다.
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import CoupangRevenueFee

# 요율을 모르는 옵션(첫 정산 전 신제품 등)에 쓰는 기본값. channels.commission_rate 시드와 같은 값이며
# 근거 등급이 다르므로 화면은 이 값을 쓴 라인을 «요율 미확인»으로 실토해야 한다(basis=default_rate).
_Z = Decimal("0")
DEFAULT_FEE_RATE = Decimal("0.078")
# 판매수수료에만 붙는 VAT. 풀필먼트·보관·RG광고 정산액엔 금지(이미 실청구) — commission_vat_resolver와 동일 규약.
FEE_VAT_MULT = Decimal("1.1")

BASIS_SETTLED = "settled_rate"   # 그 옵션의 실제 정산 요율을 알고 있다
BASIS_DEFAULT = "default_rate"   # 정산 이력이 없어 기본 7.8%를 썼다


def option_fee_rates(
    db: Session,
    account_keys: list[str] | None = None,
) -> dict[tuple[str, str], Decimal]:
    """(account_key, vendor_item_id) → 판매수수료 요율(소수. 7.8% → Decimal("0.078")).

    출처는 coupang_revenue_fee.service_fee_ratio(쿠팡이 준 값, VAT 제외율)뿐이다 — 우리가 계산하거나
    추론한 요율은 절대 넣지 않는다. 같은 옵션에 행이 여럿이면 recognition_date가 가장 최근인 것을 쓴다.
    account_key를 키에 포함해 계정 간 교차 오염을 막는다(revenue_fee_source와 같은 규약).
    """
    sub = (
        db.query(
            CoupangRevenueFee.account_key.label("ak"),
            CoupangRevenueFee.vendor_item_id.label("vid"),
            func.max(CoupangRevenueFee.recognition_date).label("mx"),
        )
        .filter(CoupangRevenueFee.service_fee_ratio.isnot(None))
    )
    if account_keys:
        sub = sub.filter(CoupangRevenueFee.account_key.in_(account_keys))
    sub = sub.group_by(CoupangRevenueFee.account_key, CoupangRevenueFee.vendor_item_id).subquery()

    rows = (
        db.query(
            CoupangRevenueFee.account_key,
            CoupangRevenueFee.vendor_item_id,
            func.max(CoupangRevenueFee.service_fee_ratio),
        )
        .join(
            sub,
            (CoupangRevenueFee.account_key == sub.c.ak)
            & (CoupangRevenueFee.vendor_item_id == sub.c.vid)
            & (CoupangRevenueFee.recognition_date == sub.c.mx),
        )
        .filter(CoupangRevenueFee.service_fee_ratio.isnot(None))
        .group_by(CoupangRevenueFee.account_key, CoupangRevenueFee.vendor_item_id)
        .all()
    )
    out: dict[tuple[str, str], Decimal] = {}
    for ak, vid, ratio in rows:
        if ratio is None:
            continue
        out[(str(ak), str(vid))] = Decimal(str(ratio)) / Decimal("100")
    return out


def resolve_rate(
    rates: dict[tuple[str, str], Decimal],
    account_key: str | None,
    vendor_item_id: str | None,
    default: Decimal | None = None,
) -> tuple[Decimal, str]:
    """옵션 요율과 그 근거 등급을 함께 돌려준다. 값만 주면 화면이 실토할 수가 없다."""
    if account_key and vendor_item_id:
        hit = rates.get((str(account_key), str(vendor_item_id)))
        if hit is not None:
            return hit, BASIS_SETTLED
    return (DEFAULT_FEE_RATE if default is None else default), BASIS_DEFAULT


def fee_reconciliation(
    db: Session,
    dfrom,
    dto,
    account_keys: list[str] | None = None,
) -> dict:
    """이 설계의 «전제»를 라이브에서 계속 검증한다 — 계산한 수수료가 실측과 같은가.

    ★왜 필요한가: 「수수료 = 매출 × 요율 × 1.1」은 2026-08-10 실측 661건으로 확인한 사실이지
    쿠팡이 보증한 계약이 아니다. 쿠팡이 쿠폰·프로모션 정산을 다르게 하기 시작하면 우리 계산은
    조용히 틀리고, 정산 실측을 net_profit에서 뺐으니 그걸 알아챌 표면이 없어진다.
    그래서 «이미 정산된 주문라인»에서만 계산값과 실측값을 대조해 어긋남을 화면으로 올린다.
    (미정산 라인은 대조 대상이 아니다 — 비교할 실측이 없다.)

    반환: checked_lines / computed / actual / diff / max_line_diff.
    diff가 반올림 범위(라인당 1원)를 넘으면 화면이 경고해야 한다.
    """
    from app.models import Channel, Order  # 순환 임포트 회피(이 함수만 ORM 엔티티가 필요)
    from app.services.cafe24_status_mapper import REVENUE_EXCLUDED
    from app.services.coupang.revenue_fee_source import actual_fee_by_order_option

    q = (
        db.query(Order.order_number, Order.platform_product_id, Order.selling_price, Channel.code)
        .join(Channel, Order.channel_id == Channel.id)
        .filter(
            Channel.code.in_(list(account_keys) if account_keys else ["COUPANG_WING1", "COUPANG_WING2"]),
            Order.status.notin_(tuple(REVENUE_EXCLUDED)),
            Order.order_date >= dfrom,
            Order.order_date <= dto,
        )
    )
    lines = q.all()
    if not lines:
        return {"checked_lines": 0, "computed": _Z, "actual": _Z, "diff": _Z, "max_line_diff": _Z}

    actual_map = actual_fee_by_order_option(db, {r[0] for r in lines})
    rates = option_fee_rates(db, list(account_keys) if account_keys else None)

    checked = 0
    computed_sum = _Z
    actual_sum = _Z
    max_line_diff = _Z
    for onum, vid, price, code in lines:
        actual = actual_map.get((str(code), str(onum), str(vid)))
        if actual is None:  # 미정산 — 대조할 실측이 없다
            continue
        rate, _basis = resolve_rate(rates, code, vid)
        computed = commission_for(Decimal(str(price or 0)), rate)
        checked += 1
        computed_sum += computed
        actual_sum += actual
        gap = abs(computed - actual)
        if gap > max_line_diff:
            max_line_diff = gap
    return {
        "checked_lines": checked,
        "computed": computed_sum,
        "actual": actual_sum,
        "diff": computed_sum - actual_sum,
        "max_line_diff": max_line_diff,
    }


def commission_for(
    net_revenue: Decimal,
    rate: Decimal,
) -> Decimal:
    """판매수수료+VAT = 순매출 × 요율 × 1.1.

    ★순매출인 이유: 반품·취소분은 쿠팡이 수수료도 함께 환급한다(정산에 REFUND 음수 행으로 온다).
    총매출에 요율을 곱하면 반품된 건의 수수료를 우리만 계속 물게 된다.
    """
    return net_revenue * rate * FEE_VAT_MULT
