# test_product_pnl.py — S3 트랙 T3: 대조원장(reconciliation ledger) 보존 법칙 테스트
# 이 트랙의 배포 게이트(원칙22): 채널·컴포넌트마다
#   Σ(SKU 귀속) + Σ(잔차 버킷) == 기존 권위 엔진 계정 소계  (정확 성립, tolerance 아님)
# 성립 안 하면 조인/기준 버그 → SKU행 신뢰 불가. 이 파일이 그 불변식을 fixture로 못박는다.
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    Channel, CoupangAdOptionDaily, CoupangAdReport, CoupangProductItem,
    CoupangReturnItem, CoupangRevenueFee, CoupangRgOrderItem,
    CoupangRgSettlementFee, CoupangRocketPurchaseOrder,
    CoupangRocketPurchaseOrderItem, CoupangVendorItemSalesDaily,
    Order, ProductChannelMapping,
    ProductMaster, RocketProductCostMap,
)
from app.services.coupang.intelligence import compute_command_center
from app.services.coupang.rocket_intelligence import compute_rocket_overview
from app.services.product_pnl import compute_pnl_reconciliation

_Z = Decimal(0)
WIN = (date(2026, 6, 1), date(2026, 6, 30))
OD = datetime(2026, 6, 5, 12, 0, 0)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _ch(db, cid, code, platform="coupang", company="ofix"):
    db.add(Channel(id=cid, name=f"ch_{code}", code=code, platform=platform, company=company))


def _pm(db, pid, sku, cost=Decimal("0")):
    db.add(ProductMaster(id=pid, internal_sku=sku, product_name=f"prod_{sku}", cost_price=cost))


def _map(db, pid, cid, vid, active=True):
    db.add(ProductChannelMapping(product_id=pid, channel_id=cid, channel_product_id=vid,
                                 selling_price=Decimal("10000"), is_active=active))


def _prod(db, vid, account_key="COUPANG_WING1", vendor_id="A01564720"):
    """CoupangProductItem — _resolve_account가 account_key→vendor_id를 도출하는 경로(광고·상품 필터).
    실제 시스템은 상품 스냅샷이 있어 vendor_id가 해결된다(fixture도 현실과 동일하게 맞춤)."""
    db.add(CoupangProductItem(
        vendor_item_id=vid, account_key=account_key, vendor_id=vendor_id,
        seller_product_id=f"SP_{vid}", sale_price=Decimal("10000")))


def _component(result, channel, component):
    return next(c for c in result["ledger"]["components"]
                if c["channel"] == channel and c["component"] == component)


def _conserved(comp):
    """보존 법칙: allocated + Σ잔차 == authoritative (정확)."""
    residual = sum(comp["residuals"].values(), _Z)
    return comp["allocated_to_sku"] + residual == comp["authoritative_total"]


# ─────────────────────────────────────────────────────────────
# T3-1: 쿠팡 3P 매출 — 매핑된 옵션은 SKU 귀속, 미매핑은 unmapped 잔차
# ─────────────────────────────────────────────────────────────
def test_coupang_3p_revenue_conservation(db):
    _ch(db, 1, "COUPANG_WING1")
    _pm(db, 10, "OHI-0001")
    _map(db, 10, 1, "V1")  # V1 → OHI-0001
    # V2는 매핑 없음(미매핑 잔차로 가야 함)
    db.add(Order(channel_id=1, order_number="O1", platform_product_id="V1",
                 quantity=1, selling_price=Decimal("33800"), order_date=OD, status="delivered"))
    db.add(Order(channel_id=1, order_number="O2", platform_product_id="V2",
                 quantity=1, selling_price=Decimal("5000"), order_date=OD, status="delivered"))
    db.commit()

    result = compute_pnl_reconciliation(db, *WIN, account="COUPANG_WING1")
    cc = compute_command_center(db, *WIN, account="COUPANG_WING1")

    comp = _component(result, "coupang_3p", "revenue")
    assert comp["authoritative_total"] == cc["account"]["summary"]["revenue_3p"]
    assert comp["authoritative_total"] == Decimal("38800")
    assert comp["allocated_to_sku"] == Decimal("33800")  # V1→OHI-0001
    assert comp["residuals"]["unmapped_coupang"] == Decimal("5000")  # V2 미매핑
    assert _conserved(comp)
    assert comp["conservation_ok"] is True


# ─────────────────────────────────────────────────────────────
# T3-2: 쿠팡 RG 매출 — 3P와 별개 컴포넌트, unit_sales_price×qty
# ─────────────────────────────────────────────────────────────
def test_coupang_rg_revenue_conservation(db):
    _ch(db, 1, "COUPANG_WING1")
    _ch(db, 2, "COUPANG_RG1", company="ofix")
    _pm(db, 10, "OHI-0001")
    _map(db, 10, 1, "V1")  # V1 매핑(3P 채널 경유지만 vid 전역 고유)
    # ★D-CPP-49: RG 매출 원천이 gross 주문 원장 → 콘솔 net 옵션축으로 바뀌었다(계약 ⓑ).
    #   대조원장은 command_center의 revenue_rg를 «권위값»으로 쓰므로 **같은 축을 읽어야** 한다 —
    #   여기만 gross로 두면 보존식이 정확히 그 차액만큼 깨지고, trustworthy=false가 되어
    #   SKU 손익 표가 렌더 자체가 안 된다(라이브 전례: :369 D-CPP-43 P1-1 주석).
    db.add(CoupangVendorItemSalesDaily(
        sale_date=OD.date(), account_key="COUPANG_WING1", vendor_item_id="V1",
        registration_type="RFM", item_name="optV1", gmv=10000, units_sold=2, total_orders=2))
    db.add(CoupangVendorItemSalesDaily(
        sale_date=OD.date(), account_key="COUPANG_WING1", vendor_item_id="V9",
        registration_type="RFM", item_name="optV9", gmv=3000, units_sold=1,
        total_orders=1))  # V9 미매핑
    # gross 원장도 같이 둔다 — 이게 매출로 «새지 않는지»까지 이 테스트가 지킨다.
    db.add(CoupangRgOrderItem(
        order_id="RG1", vendor_item_id="V1", account_key="COUPANG_WING1", vendor_id="A01564720",
        sales_quantity=3, unit_sales_price=Decimal("5000"), paid_at=OD))  # gross 15,000(≠net)
    db.commit()

    result = compute_pnl_reconciliation(db, *WIN, account="COUPANG_WING1")
    cc = compute_command_center(db, *WIN, account="COUPANG_WING1")

    comp = _component(result, "coupang_rg", "revenue")
    assert comp["authoritative_total"] == cc["account"]["summary"]["revenue_rg"]
    assert comp["authoritative_total"] == Decimal("13000")  # 콘솔 net 10,000 + 3,000
    # gross(15,000)가 권위값으로 새지 않았다 — 축 전환의 본체.
    assert cc["account"]["summary"]["revenue_rg_gross"] == Decimal("15000")
    assert comp["allocated_to_sku"] == Decimal("10000")  # V1→OHI-0001
    assert comp["residuals"]["unmapped_coupang"] == Decimal("3000")  # V9
    assert _conserved(comp)


# ─────────────────────────────────────────────────────────────
# T3-3: per-vid 컴포넌트(수수료·광고·원가·반품차감) — command_center by_option 소비
# ─────────────────────────────────────────────────────────────
def test_coupang_per_vid_components_conservation(db):
    _ch(db, 1, "COUPANG_WING1")
    _pm(db, 10, "OHI-0001", cost=Decimal("3000"))
    _map(db, 10, 1, "V1")  # V1 매핑 + 원가 3000
    _prod(db, "V1")
    _prod(db, "V2")  # vendor_id 해결(광고 필터)
    # V1: 주문 2개(qty2), 수수료, 광고, 반품 1개
    db.add(Order(channel_id=1, order_number="O1", platform_product_id="V1",
                 quantity=2, selling_price=Decimal("33800"), order_date=OD, status="delivered"))
    db.add(CoupangRevenueFee(
        order_id="O1", vendor_item_id="V1", recognition_date=date(2026, 6, 6),
        sale_type="SALE", account_key="COUPANG_WING1", vendor_id="A01564720",
        service_fee=Decimal("780"), service_fee_vat=Decimal("78")))
    db.add(CoupangAdOptionDaily(
        report_date=date(2026, 6, 5), vendor_id="A01564720", sell_type="3P",
        ad_option_id="V1", conv_option_id="V1", ad_spend=Decimal("1000")))
    db.add(CoupangReturnItem(
        receipt_id="RC1", vendor_item_id="V1", account_key="COUPANG_WING1", vendor_id="A01564720",
        order_id="O1", receipt_type="RETURN", cancel_count=1, withdrawn=False, requested_at=OD))
    # V2: 미매핑 옵션(광고만) — 미매핑 잔차로 가야
    db.add(CoupangAdOptionDaily(
        report_date=date(2026, 6, 5), vendor_id="A01564720", sell_type="3P",
        ad_option_id="V2", conv_option_id="V2", ad_spend=Decimal("500")))
    db.commit()

    result = compute_pnl_reconciliation(db, *WIN, account="COUPANG_WING1")
    ccs = compute_command_center(db, *WIN, account="COUPANG_WING1")["account"]["summary"]

    for component, auth_key in [("commission", "total_fee"), ("ad", "ad_spend"),
                                ("cost", "cost"), ("return_deduction", "return_deduction")]:
        comp = _component(result, "coupang", component)
        assert comp["authoritative_total"] == ccs[auth_key], component
        assert _conserved(comp), component
        assert comp["conservation_ok"] is True, component

    # V1 원가 = 3000 × 순수량(2−1 반품) = 3000, V1에 귀속
    cost_comp = _component(result, "coupang", "cost")
    assert cost_comp["allocated_by_sku"]["OHI-0001"] == Decimal("3000")
    # V2 광고 500은 미매핑 잔차
    ad_comp = _component(result, "coupang", "ad")
    assert ad_comp["residuals"]["unmapped_coupang"] == Decimal("500")


# ─────────────────────────────────────────────────────────────
# T3-4: net_profit 보존 — 옵션 순익 + 계정 조정 버킷(정산화·비-PA·RG플립·판매자배송)
# ─────────────────────────────────────────────────────────────
def test_coupang_net_profit_conservation_with_adjustments(db):
    _ch(db, 1, "COUPANG_WING1")
    _pm(db, 10, "OHI-0001", cost=Decimal("3000"))
    _map(db, 10, 1, "V1")
    _prod(db, "V1")
    db.add(Order(channel_id=1, order_number="O1", platform_product_id="V1",
                 quantity=2, selling_price=Decimal("33800"), order_date=OD, status="delivered"))
    db.add(CoupangRevenueFee(
        order_id="O1", vendor_item_id="V1", recognition_date=date(2026, 6, 6),
        sale_type="SALE", account_key="COUPANG_WING1", vendor_id="A01564720",
        service_fee=Decimal("780"), service_fee_vat=Decimal("78")))
    # RG 정산(계정 grain, VAT後) — net_profit 플립(전액 차감) 발동
    db.add(CoupangRgSettlementFee(
        account_key="COUPANG_WING1", recognition_date_from=date(2026, 6, 1),
        recognition_date_to=date(2026, 6, 30), fee_type="sale_fee", vendor_item_id="",
        amount=Decimal("50000")))
    db.commit()

    result = compute_pnl_reconciliation(db, *WIN, account="COUPANG_WING1")
    ccs = compute_command_center(db, *WIN, account="COUPANG_WING1")["account"]["summary"]

    comp = _component(result, "coupang", "net_profit")
    assert comp["authoritative_total"] == ccs["net_profit"]  # 최종 net_profit(전 조정 반영)
    assert _conserved(comp)
    assert comp["conservation_ok"] is True
    # 계정 조정 버킷이 권위 값과 정확 일치(부호 포함)
    # ★D-CPP-43: 잔차는 **실제 차감액**(광고 제외)이다. 종전엔 rg_settlement_total을 못 박았는데
    #   이 픽스처엔 ad_sales가 없어 **어느 쪽으로 고쳐도 통과했다** — 통과하는데 아무것도 안 지키는
    #   테스트였다(적대 리뷰 P1-1). 아래 전용 테스트가 ad_sales를 넣어 그 구멍을 막는다.
    assert comp["residuals"]["rg_settlement_flip"] == -ccs["rg_settlement_deducted"]
    assert comp["residuals"]["seller_shipping_3p"] == -ccs["seller_shipping_3p"]
    assert comp["residuals"]["settlement_revenue_adjustment"] == ccs["settlement_revenue_adjustment"]
    assert comp["residuals"]["account_only_ad_nonpa"] == -ccs["ad_nonpa_deducted"]
    # V1 순익은 SKU 귀속
    assert "OHI-0001" in comp["allocated_by_sku"]


# ─────────────────────────────────────────────────────────────
# T3-5: 1P(로켓배송) — 별도 엔진 compute_rocket_overview, RocketProductCostMap 브리지
# ─────────────────────────────────────────────────────────────
def _rocket_po(db, seq, order_amt, qty, vendor="A01029796"):
    # 발주 금액 컬럼은 INTEGER(원 단위 정수) — int로 넣는다.
    db.add(CoupangRocketPurchaseOrder(
        purchase_order_seq=seq, vendor_id=vendor, po_created_at=datetime(2026, 6, 15, 0, 0),
        sum_of_order_amount=int(order_amt), sum_of_receiving_amount=int(order_amt),
        sum_of_vendor_confirmed_amount=int(order_amt), order_qty=qty, receiving_qty=qty,
        vendor_confirmed_qty=qty, sku_count=1))


def _rocket_line(db, seq, line_no, pnum, qty, line_amt, vendor="A01029796"):
    db.add(CoupangRocketPurchaseOrderItem(
        purchase_order_seq=seq, vendor_id=vendor, line_no=line_no, product_number=pnum,
        order_qty=qty, unit_purchase_price=Decimal("0"), line_order_amount=Decimal(line_amt),
        line_supply_amount=Decimal(line_amt), line_vat=Decimal("0")))


def test_coupang_1p_conservation(db):
    _pm(db, 10, "OHI-0001", cost=Decimal("3000"))
    _rocket_po(db, 1, "100000", 10)
    _rocket_line(db, 1, 1, "P1", 6, "60000")  # P1 → OHI-0001(confirmed)
    _rocket_line(db, 1, 2, "P2", 4, "40000")  # P2 → 미매핑
    db.add(RocketProductCostMap(product_number="P1", internal_sku="OHI-0001", status="confirmed"))
    db.add(CoupangAdReport(
        report_date=date(2026, 6, 15), sell_type="Retail", vendor_id="A01029796",
        impressions=0, clicks=0, ad_spend=Decimal("5000"), orders=0, sales_qty=0,
        conversion_revenue=Decimal("0")))
    db.commit()

    result = compute_pnl_reconciliation(db, *WIN, account=None)
    ro = compute_rocket_overview(db, *WIN, vendor_id=None)

    rev = _component(result, "coupang_1p", "revenue")
    assert rev["authoritative_total"] == ro["revenue"] == Decimal("100000")
    assert rev["allocated_by_sku"]["OHI-0001"] == Decimal("60000")
    assert rev["residuals"]["unmapped_1p"] == Decimal("40000")
    assert _conserved(rev)

    cost = _component(result, "coupang_1p", "cost")
    assert cost["authoritative_total"] == ro["cost"] == Decimal("18000")  # 6×3000
    assert cost["allocated_by_sku"]["OHI-0001"] == Decimal("18000")
    assert _conserved(cost)

    ad = _component(result, "coupang_1p", "ad")
    assert ad["authoritative_total"] == ro["ad_spend"] == Decimal("5000")
    assert ad["allocated_to_sku"] == _Z  # 1P 광고는 계정 단위(옵션 귀속 불가)
    assert ad["residuals"]["account_only_ad"] == Decimal("5000")
    assert _conserved(ad)

    np = _component(result, "coupang_1p", "net_profit")
    assert np["authoritative_total"] == ro["net_profit"] == Decimal("77000")  # 100000−5000−18000
    assert np["allocated_by_sku"]["OHI-0001"] == Decimal("42000")  # 60000−18000
    assert _conserved(np)


def test_1p_scope_by_shared_vendor_ohitech_vs_ofix(db):
    """codex T3 P1#2 잠금: D-2 확정사실(vendor_id는 회사별 WING·RG·ROCKET 공유)로 계정별 뷰에서
    1P가 정확 스코프됨. ohitech 계정(WING2, vendor A01029796)은 1P 포함, ofix 계정은 로켓 없어 0."""
    _ch(db, 2, "COUPANG_WING2", company="ohitech")
    _ch(db, 1, "COUPANG_WING1", company="ofix")
    _pm(db, 10, "OHI-0001", cost=Decimal("3000"))
    # ohitech WING2 상품 스냅샷 → _resolve_account가 vendor A01029796 도출(=1P vendor, D-2 공유)
    _prod(db, "W2A", account_key="COUPANG_WING2", vendor_id="A01029796")
    _rocket_po(db, 1, "100000", 10, vendor="A01029796")
    _rocket_line(db, 1, 1, "P1", 6, "60000")
    db.add(RocketProductCostMap(product_number="P1", internal_sku="OHI-0001", status="confirmed"))
    db.commit()

    # ohitech 계정 뷰: 1P 매출 포함(vendor 공유로 정확 스코프)
    ohi = compute_pnl_reconciliation(db, *WIN, account="COUPANG_WING2")
    rev = _component(ohi, "coupang_1p", "revenue")
    assert rev["authoritative_total"] == Decimal("100000")
    assert rev["allocated_by_sku"]["OHI-0001"] == Decimal("60000")
    assert _conserved(rev)

    # ofix 계정 뷰: 로켓 PO 없음 → 1P 0(회귀 가드, 이중계상/오귀속 없음)
    ofx = compute_pnl_reconciliation(db, *WIN, account="COUPANG_WING1")
    ofx_rev = _component(ofx, "coupang_1p", "revenue")
    assert ofx_rev["authoritative_total"] == _Z
    assert ofx_rev["allocated_by_sku"] == {}
    assert _conserved(ofx_rev)


# ─────────────────────────────────────────────────────────────
# T4: RG 옵션수수료 VAT gross-up(×1.1) SKU 귀속 + rg_vat_residual (D-8/D-9)
# ─────────────────────────────────────────────────────────────
def _rg_fee(db, acct, ft, vid, amount, frm=date(2026, 6, 1), to=date(2026, 6, 30)):
    db.add(CoupangRgSettlementFee(
        account_key=acct, recognition_date_from=frm, recognition_date_to=to,
        fee_type=ft, vendor_item_id=vid, amount=Decimal(amount)))


def test_coupang_net_profit_conservation_survives_rg_ad_sales(db):
    """★★D-CPP-43 적대 리뷰 P1-1 회귀 — **ad_sales가 있을 때** 원장 보존식이 유지되는가.

    라이브 실사고(2026-08-12): 플립이 `rg_total − ad_sales`를 차감하도록 바뀌었는데 잔차 버킷
    `rg_settlement_flip`은 여전히 `rg_settlement_total`을 신고했다 → 보존식이 **정확히 ad_sales
    만큼** 깨지고 `trustworthy=false`가 되어, prod에서 **SKU 손익 표가 렌더링 자체가 안 됐다**
    (`ProductConnectionMap.tsx`의 `trustworthy &&` 가드). 숫자가 틀린 게 아니라 탭이 죽었다.

    ★기존 테스트가 왜 못 잡았나: `test_coupang_net_profit_conservation_with_adjustments`는
      픽스처에 `ad_sales`가 **없어서** rg_total이든 rg_deducted든 **어느 쪽으로 고쳐도 통과**했다.
      「통과하는데 아무것도 안 지키는 테스트」다(교훈 #181). ad_sales를 넣는 것이 이 테스트의 전부다.
    """
    _ch(db, 1, "COUPANG_WING1")
    _pm(db, 10, "OHI-0001", cost=Decimal("3000"))
    _map(db, 10, 1, "V1")
    _prod(db, "V1")
    db.add(Order(channel_id=1, order_number="O1", platform_product_id="V1",
                 quantity=2, selling_price=Decimal("33800"), order_date=OD, status="delivered"))
    _rg_fee(db, "COUPANG_WING1", "sale_fee", "", "50000")
    _rg_fee(db, "COUPANG_WING1", "ad_sales", "", "40000")   # ← 이 한 줄이 P1-1을 재현한다
    db.commit()

    result = compute_pnl_reconciliation(db, *WIN, account="COUPANG_WING1")
    ccs = compute_command_center(db, *WIN, account="COUPANG_WING1")["account"]["summary"]
    comp = _component(result, "coupang", "net_profit")

    # 차감은 광고 제외분(50000)이고 정산 총액은 90000이다 — 둘을 혼동하면 40000이 샌다.
    assert ccs["rg_settlement_total"] == Decimal("90000")
    assert ccs["rg_settlement_deducted"] == Decimal("50000")
    assert comp["residuals"]["rg_settlement_flip"] == Decimal("-50000")
    assert _conserved(comp), f"보존식 파손: diff={comp.get('conservation_diff')}"
    assert comp["conservation_ok"] is True
    # 화면 가드가 살아 있어야 SKU 표가 뜬다
    assert result["summary"]["trustworthy"] is True


def test_rg_settlement_fee_vat_grossup_conservation(db):
    _ch(db, 1, "COUPANG_WING1")
    _pm(db, 10, "OHI-0001")
    _map(db, 10, 1, "V1")
    _prod(db, "V1")
    # 옵션 row(VAT前 A−B): V1(매핑) 입출고 10000, V9(미매핑) 입출고 2000
    _rg_fee(db, "COUPANG_WING1", "warehousing", "V1", "10000")
    _rg_fee(db, "COUPANG_WING1", "warehousing", "V9", "2000")
    # 계정 row(VAT後, vendor_item_id=''): 입출고 13200(=(10000+2000)×1.1), ad_sales 5000(옵션 없음)
    _rg_fee(db, "COUPANG_WING1", "warehousing", "", "13200")
    _rg_fee(db, "COUPANG_WING1", "ad_sales", "", "5000")
    db.commit()

    result = compute_pnl_reconciliation(db, *WIN, account="COUPANG_WING1")
    ccs = compute_command_center(db, *WIN, account="COUPANG_WING1")["account"]["summary"]

    comp = _component(result, "coupang_rg", "settlement_fee")
    # 권위 = 계정 RG 플립 총액(VAT後) = 13200 + 5000 = 18200
    assert comp["authoritative_total"] == ccs["rg_settlement_total"] == Decimal("18200")
    # V1 귀속 = 10000 × 1.1(gross-up) = 11000
    assert comp["allocated_by_sku"]["OHI-0001"] == Decimal("11000")
    # V9 미매핑 = 2000 × 1.1 = 2200
    assert comp["residuals"]["rg_unmapped"] == Decimal("2200")
    # ad_sales(옵션 row 없음) 5000 → 계정전용 잔차
    assert comp["residuals"]["rg_account_only_fees"] == Decimal("5000")
    # 입출고 gross-up gap = 13200 − (12000×1.1) = 0 (정상)
    assert comp["residuals"]["rg_vat_grossup_gap"] == Decimal("0")
    assert _conserved(comp)
    assert comp["conservation_ok"] is True


def test_rg_sale_fee_option_rows_attributed_to_sku(db):
    """codex T4 P1: 판매수수료(sale_fee)도 엑셀 옵션 row가 있으면 SKU 귀속(입출고 전용 아님).
    ×1.1이 틀리거나 already-VAT row가 섞이면 rg_vat_grossup_gap이 0이 아니게 되어 표면화."""
    _ch(db, 1, "COUPANG_WING1")
    _pm(db, 10, "OHI-0001")
    _map(db, 10, 1, "V1")
    _prod(db, "V1")
    _rg_fee(db, "COUPANG_WING1", "sale_fee", "V1", "20000")   # 판매수수료 옵션 row(VAT前)
    _rg_fee(db, "COUPANG_WING1", "sale_fee", "", "22000")     # 계정 row(VAT後=20000×1.1)
    db.commit()

    result = compute_pnl_reconciliation(db, *WIN, account="COUPANG_WING1")
    comp = _component(result, "coupang_rg", "settlement_fee")
    assert comp["allocated_by_sku"]["OHI-0001"] == Decimal("22000")  # 20000×1.1, SKU 귀속
    assert comp["residuals"]["rg_account_only_fees"] == Decimal("0")  # 계정전용 없음
    assert comp["residuals"]["rg_vat_grossup_gap"] == Decimal("0")   # ×1.1 정확
    assert _conserved(comp)


# ─────────────────────────────────────────────────────────────
# T5: 날짜기준 명시(date_basis) + partial_period_settlement 경고 (D-10)
# ─────────────────────────────────────────────────────────────
def test_date_basis_present_per_component(db):
    _ch(db, 1, "COUPANG_WING1")
    db.add(Order(channel_id=1, order_number="O1", platform_product_id="V1",
                 quantity=1, selling_price=Decimal("1000"), order_date=OD, status="delivered"))
    db.commit()
    result = compute_pnl_reconciliation(db, *WIN, account="COUPANG_WING1")
    # 각 컴포넌트가 자기 날짜기준을 명시(교차검증 시 각 소스 기준으로 대조, D-10)
    for c in result["ledger"]["components"]:
        assert c.get("date_basis"), (c["channel"], c["component"])
    assert "order_date" in _component(result, "coupang_3p", "revenue")["date_basis"]
    assert "paid_at" in _component(result, "coupang_rg", "revenue")["date_basis"]
    assert "po_created" in _component(result, "coupang_1p", "revenue")["date_basis"]


def test_partial_period_settlement_warning(db):
    """RG 정산 주기가 조회 윈도우에 완전 포함 안 되면 경고(일할 배분 안 함, codex #10)."""
    _ch(db, 1, "COUPANG_WING1")
    # 정산 주기 5/28~6/3 → 윈도우 6/1~6/30 시작 경계를 넘어 걸침(부분 포함)
    _rg_fee(db, "COUPANG_WING1", "warehousing", "", "1000",
            frm=date(2026, 5, 28), to=date(2026, 6, 3))
    db.commit()
    result = compute_pnl_reconciliation(db, *WIN, account="COUPANG_WING1")
    warns = result["ledger"]["warnings"]
    assert any(w["type"] == "partial_period_settlement" for w in warns)

    # 완전 포함(6/8~6/14)이면 경고 없음
    db.query(CoupangRgSettlementFee).delete()
    _rg_fee(db, "COUPANG_WING1", "warehousing", "", "1000",
            frm=date(2026, 6, 8), to=date(2026, 6, 14))
    db.commit()
    result2 = compute_pnl_reconciliation(db, *WIN, account="COUPANG_WING1")
    assert not any(w["type"] == "partial_period_settlement" for w in result2["ledger"]["warnings"])


# ─────────────────────────────────────────────────────────────
# T6: Harness 3b SKU행 — net_profit_allocated_only·reconciled·account_adjustment_residual 분리
# ─────────────────────────────────────────────────────────────
def test_sku_rows_net_profit_split(db):
    _ch(db, 1, "COUPANG_WING1")
    _pm(db, 10, "OHI-0001", cost=Decimal("3000"))
    _map(db, 10, 1, "V1")
    _prod(db, "V1")
    db.add(Order(channel_id=1, order_number="O1", platform_product_id="V1",
                 quantity=2, selling_price=Decimal("33800"), order_date=OD, status="delivered"))
    db.add(CoupangRevenueFee(
        order_id="O1", vendor_item_id="V1", recognition_date=date(2026, 6, 6),
        sale_type="SALE", account_key="COUPANG_WING1", vendor_id="A01564720",
        service_fee=Decimal("780"), service_fee_vat=Decimal("78")))
    # 계정 조정(RG 플립) — SKU에 안 붙는 잔차
    _rg_fee(db, "COUPANG_WING1", "sale_fee", "", "50000")
    db.commit()

    result = compute_pnl_reconciliation(db, *WIN, account="COUPANG_WING1")
    ccs = compute_command_center(db, *WIN, account="COUPANG_WING1")["account"]["summary"]

    # 3b는 원장 균형 후에만
    assert result["ledger"]["conservation_ok"] is True
    rows = result["by_sku"]
    row = next(r for r in rows if r["internal_sku"] == "OHI-0001")
    # SKU 귀속 순익 = command_center by_option net_profit(V1) — 계정조정 제외
    assert row["net_profit_allocated_only"] == _component(
        result, "coupang", "net_profit")["allocated_by_sku"]["OHI-0001"]
    # 채널 분해 존재
    assert "coupang_3p" in row["channels"]

    summ = result["summary"]
    # 계정 단위 화해: reconciled_net_profit == 엔진 총액(command_center net_profit, RG 플립 반영)
    assert summ["reconciled_net_profit"] == ccs["net_profit"]
    # account_adjustment_residual = reconciled − Σ(SKU 귀속 순익) (미매핑+계정조정, 안분 안 함)
    sku_net_sum = sum((r["net_profit_allocated_only"] for r in rows), _Z)
    assert summ["account_adjustment_residual"] == summ["reconciled_net_profit"] - sku_net_sum


# ─────────────────────────────────────────────────────────────
# T6: 라우터 GET /api/products/pnl-reconciliation (account 파라미터 D-11 + Decimal→str)
# ─────────────────────────────────────────────────────────────
def test_pnl_reconciliation_endpoint():
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool
    from app.main import app
    from app.database import Base, get_db

    # StaticPool + 단일 커넥션 in-memory (요청/시드가 같은 DB 공유, 기존 라우터 테스트 패턴)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TS = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = TS()
    s.add(Channel(id=1, name="w", code="COUPANG_WING1", platform="coupang", company="ofix"))
    s.add(ProductMaster(id=10, internal_sku="OHI-0001", product_name="p", cost_price=Decimal("3000")))
    s.add(ProductChannelMapping(product_id=10, channel_id=1, channel_product_id="V1",
                                selling_price=Decimal("10000"), is_active=True))
    s.add(Order(channel_id=1, order_number="O1", platform_product_id="V1", quantity=1,
                selling_price=Decimal("10000"), order_date=OD, status="delivered"))
    s.commit()
    s.close()

    def _override():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    try:
        client = TestClient(app)
        r = client.get("/api/products/pnl-reconciliation",
                       params={"from": "2026-06-01", "to": "2026-06-30", "account": "COUPANG_WING1"})
        assert r.status_code == 200
        body = r.json()
        assert body["ledger"]["conservation_ok"] is True
        # Decimal이 str로 직렬화(머니 정밀도)
        comp = next(c for c in body["ledger"]["components"]
                    if c["channel"] == "coupang_3p" and c["component"] == "revenue")
        assert Decimal(comp["authoritative_total"]) == Decimal("10000")  # str 직렬화(스케일 무관)
        assert isinstance(comp["authoritative_total"], str)
        # 잘못된 account → 422
        bad = client.get("/api/products/pnl-reconciliation", params={"account": "NOPE"})
        assert bad.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_rg_settlement_fee_absent_is_zero(db):
    """RG 정산 데이터 없으면 settlement_fee 컴포넌트 0, 보존 유지(회귀 가드)."""
    _ch(db, 1, "COUPANG_WING1")
    db.commit()
    result = compute_pnl_reconciliation(db, *WIN, account="COUPANG_WING1")
    comp = _component(result, "coupang_rg", "settlement_fee")
    assert comp["authoritative_total"] == _Z
    assert comp["allocated_by_sku"] == {}
    assert _conserved(comp)


# ─────────────────────────────────────────────────────────────
# T3-6: 네이버/cafe24 — _line_revenue/_line_commission 재사용, product_id→internal_sku 그룹
# ─────────────────────────────────────────────────────────────
def test_marketplace_naver_cafe24_conservation(db):
    _ch(db, 5, "NAVER", platform="naver", company=None)
    _ch(db, 6, "CAFE24", platform="cafe24", company=None)
    _pm(db, 10, "OHI-0001", cost=Decimal("2000"))
    # 네이버: 매핑 주문 + 미매핑 주문
    db.add(Order(channel_id=5, order_number="N1", platform_product_id="NP1", product_id=10,
                 quantity=2, selling_price=Decimal("20000"), order_date=OD, status="delivered",
                 commission_amount=Decimal("2000")))
    db.add(Order(channel_id=5, order_number="N2", platform_product_id="NP2", product_id=None,
                 quantity=1, selling_price=Decimal("5000"), order_date=OD, status="delivered",
                 commission_amount=Decimal("500")))
    # cafe24: 단가×수량 (매핑)
    db.add(Order(channel_id=6, order_number="C1", platform_product_id="CP1", product_id=10,
                 quantity=3, selling_price=Decimal("1000"), order_date=OD, status="delivered",
                 commission_amount=Decimal("300")))
    # 취소 주문은 제외돼야
    db.add(Order(channel_id=5, order_number="N3", platform_product_id="NP1", product_id=10,
                 quantity=1, selling_price=Decimal("99999"), order_date=OD, status="cancelled",
                 commission_amount=Decimal("0")))
    db.commit()

    result = compute_pnl_reconciliation(db, *WIN, account=None)

    # 네이버 매출: 20000(NP1) + 5000(NP2 미매핑), 취소 N3 제외
    nrev = _component(result, "naver", "product_revenue")
    assert nrev["authoritative_total"] == Decimal("25000")
    assert nrev["allocated_by_sku"]["OHI-0001"] == Decimal("20000")
    assert nrev["residuals"]["unmapped_naver"] == Decimal("5000")
    assert _conserved(nrev)

    ncomm = _component(result, "naver", "commission")
    assert ncomm["authoritative_total"] == Decimal("2500")  # 2000+500
    assert _conserved(ncomm)

    ncost = _component(result, "naver", "cost")
    assert ncost["allocated_by_sku"]["OHI-0001"] == Decimal("4000")  # 2000×2
    assert _conserved(ncost)

    # cafe24 매출 = 단가×수량 = 1000×3 = 3000 (라인 헬퍼 채널별 의미차 반영)
    crev = _component(result, "cafe24", "product_revenue")
    assert crev["authoritative_total"] == Decimal("3000")
    assert crev["allocated_by_sku"]["OHI-0001"] == Decimal("3000")
    assert _conserved(crev)


# ─────────────────────────────────────────────────────────────
# T3-7: 옵션ID→복수 internal_sku 충돌 감지(무결성 결손 표면화, 자동병합 금지)
# ─────────────────────────────────────────────────────────────
def test_sku_conflict_surfaced_and_deterministic(db):
    _ch(db, 1, "COUPANG_WING1")
    _pm(db, 10, "OHI-0001")
    _pm(db, 20, "OHI-0002")
    _map(db, 10, 1, "V1")  # V1 → OHI-0001
    _map(db, 20, 1, "V1")  # V1 → OHI-0002 (충돌!)
    _prod(db, "V1")
    db.add(Order(channel_id=1, order_number="O1", platform_product_id="V1",
                 quantity=1, selling_price=Decimal("10000"), order_date=OD, status="delivered"))
    db.commit()

    result = compute_pnl_reconciliation(db, *WIN, account="COUPANG_WING1")
    assert "V1" in result["ledger"]["sku_conflicts"]
    # codex P1#1: 충돌 vid는 어느 SKU로도 귀속하지 않고 미매핑 잔차로(임의 선택이 권위 엔진과
    # 어긋나 SKU별 돈이 틀어지는 것 방지, D-7 no-auto-merge). 총합 보존은 유지.
    rev = _component(result, "coupang_3p", "revenue")
    assert rev["allocated_by_sku"] == {}  # 충돌이라 SKU 귀속 없음
    assert rev["residuals"]["unmapped_coupang"] == Decimal("10000")  # 잔차로
    assert _conserved(rev)


# ─────────────────────────────────────────────────────────────
# T3-8: 전체 원장 균형 — 모든 컴포넌트 보존, 빈 기간도 균형(회귀 가드)
# ─────────────────────────────────────────────────────────────
def test_whole_ledger_conservation_and_empty_window(db):
    # 빈 DB(데이터 0) → 모든 컴포넌트 0, conservation_ok True(회귀 가드)
    empty = compute_pnl_reconciliation(db, *WIN, account=None)
    assert empty["ledger"]["conservation_ok"] is True
    assert all(c["conservation_ok"] for c in empty["ledger"]["components"])

    # 데이터 섞어도 전체 conservation_ok
    _ch(db, 1, "COUPANG_WING1")
    _pm(db, 10, "OHI-0001", cost=Decimal("3000"))
    _map(db, 10, 1, "V1")
    _prod(db, "V1")
    db.add(Order(channel_id=1, order_number="O1", platform_product_id="V1",
                 quantity=2, selling_price=Decimal("33800"), order_date=OD, status="delivered"))
    db.commit()
    full = compute_pnl_reconciliation(db, *WIN, account=None)
    assert full["ledger"]["conservation_ok"] is True
    for c in full["ledger"]["components"]:
        assert c["conservation_ok"] is True, (c["channel"], c["component"])


# ─── 원 단위 판정(라이브 발견, 2026-07-04): 순환소수 나눗셈 잔여 오판 방지 ───
def test_reconcile_component_tolerates_repeating_decimal_noise():
    """prod 라이브에서 실제 관측: unit_price=매출/수량 나눗셈이 만드는 순환소수가 SKU별 분해
    경로와 계정 전체 합산 경로에서 다른 정밀도로 잘려 diff=3E-21 같은 원 미만 잔여를 남긴다.
    이건 진짜 돈 누락이 아니라 Decimal 정밀도 흔적 — conservation_ok는 원 단위(0.01)로
    판정해야 하고, raw conservation_diff는 투명성을 위해 그대로 보존한다."""
    from app.services.product_pnl import _reconcile_component
    authoritative = Decimal("6735174.176470588235294117650")  # 실제 prod 관측치(순환소수)
    allocated_by_sku = {"OHI-0001": Decimal("6735174.176470588235294117647")}  # 21번째 자리 차이
    row = _reconcile_component("coupang", "net_profit", authoritative, allocated_by_sku, {})
    assert row["conservation_diff"] == Decimal("3E-21")  # raw diff는 그대로 노출(투명성)
    assert row["conservation_ok"] is True  # 원 단위론 완전 균형 — 오판 아님


def test_reconcile_component_still_catches_real_won_level_gap():
    """원 단위 미만 반올림 허용이 진짜 1원 이상 누락까지 가려서는 안 된다(D5 회귀 가드)."""
    from app.services.product_pnl import _reconcile_component
    row = _reconcile_component("coupang", "revenue", Decimal("1000.00"),
                               {"OHI-0001": Decimal("998.00")}, {})  # 2원 누락(잔차 0건)
    assert row["conservation_ok"] is False
    assert row["conservation_diff"] == Decimal("2.00")


# ─────────────────────────────────────────────────────────────
# 원가 «모름» 표면화 (2026-08-06) — 합계는 불변, 보존법칙 불변
# ★배경: `cost = pm.cost_price * qty if pm is not None else 0` — 마스터는 붙었는데
#   cost_price가 0/NULL이면 원가를 **모르는** 것인데 0원으로 계산돼 그 SKU 순익이 과대였다.
#   `ProductMaster.cost_price`가 `nullable=False, default=0`이라 미입력이 조용히 0이 된다.
#   라이브(2026-08-06): 네이버 1개 상품·30일 44,500원.
# ★금액을 바꾸지 않는 이유: 여기서 cost를 빼면 conservation_ok(Σ귀속+잔차==총계)와
#   권위 엔진(profit_calculator) 정합이 **동시에** 깨진다. 사실만 따로 싣고 화면이 비운다.
# ─────────────────────────────────────────────────────────────
def test_zero_cost_master_is_surfaced_without_touching_totals(db):
    _ch(db, 5, "NAVER", platform="naver", company=None)
    _pm(db, 10, "OHI-COST", cost=Decimal("2000"))   # 원가 있음
    _pm(db, 20, "OHI-ZERO", cost=Decimal("0"))      # ★미입력 = 모름
    db.add(Order(channel_id=5, order_number="N1", platform_product_id="NP1", product_id=10,
                 quantity=2, selling_price=Decimal("20000"), order_date=OD, status="delivered",
                 commission_amount=Decimal("2000")))
    db.add(Order(channel_id=5, order_number="N2", platform_product_id="NP2", product_id=20,
                 quantity=1, selling_price=Decimal("44500"), order_date=OD, status="delivered",
                 commission_amount=Decimal("1000")))
    db.commit()

    result = compute_pnl_reconciliation(db, *WIN, account=None)

    # ★보존법칙이 살아 있다 — 이게 이 변경의 유일한 금지선이다.
    assert result["ledger"]["conservation_ok"] is True
    ncost = _component(result, "naver", "cost")
    assert _conserved(ncost)
    # 금액 불변: 원가 총계는 여전히 아는 것만(2,000×2), 미상은 0으로 접힌 채 남는다.
    assert ncost["authoritative_total"] == Decimal("4000")
    assert ncost["allocated_by_sku"]["OHI-COST"] == Decimal("4000")
    assert ncost["allocated_by_sku"]["OHI-ZERO"] == _Z

    rows = {r["internal_sku"]: r for r in result["by_sku"]}
    assert rows["OHI-COST"]["cost_known"] is True
    assert rows["OHI-ZERO"]["cost_known"] is False
    assert rows["OHI-ZERO"]["cost_unknown_revenue"] == Decimal("44500")
    assert result["summary"]["cost_unknown_skus"] == 1
    assert result["summary"]["cost_unknown_revenue"] == Decimal("44500")


def test_unmapped_lines_do_not_count_as_cost_unknown_sku(db):
    """마스터 자체가 없는 라인은 이미 «미매핑 잔차»다 — 원가 미상 SKU로 이중 계상하지 않는다."""
    _ch(db, 5, "NAVER", platform="naver", company=None)
    _pm(db, 10, "OHI-COST", cost=Decimal("2000"))
    db.add(Order(channel_id=5, order_number="N1", platform_product_id="NP1", product_id=10,
                 quantity=1, selling_price=Decimal("10000"), order_date=OD, status="delivered",
                 commission_amount=Decimal("1000")))
    db.add(Order(channel_id=5, order_number="N2", platform_product_id="NP2", product_id=None,
                 quantity=1, selling_price=Decimal("5000"), order_date=OD, status="delivered",
                 commission_amount=Decimal("500")))
    db.commit()

    result = compute_pnl_reconciliation(db, *WIN, account=None)

    assert result["summary"]["cost_unknown_skus"] == 0
    assert _component(result, "naver", "product_revenue")["residuals"]["unmapped_naver"] == Decimal("5000")


def test_all_costs_known_reports_zero(db):
    """전부 원가가 있으면 0 — 거짓 경보를 내지 않는다(회귀 가드)."""
    _ch(db, 5, "NAVER", platform="naver", company=None)
    _pm(db, 10, "OHI-COST", cost=Decimal("2000"))
    db.add(Order(channel_id=5, order_number="N1", platform_product_id="NP1", product_id=10,
                 quantity=1, selling_price=Decimal("10000"), order_date=OD, status="delivered",
                 commission_amount=Decimal("1000")))
    db.commit()

    result = compute_pnl_reconciliation(db, *WIN, account=None)

    assert result["summary"]["cost_unknown_skus"] == 0
    assert result["summary"]["cost_unknown_revenue"] == _Z
    assert all(r["cost_known"] for r in result["by_sku"])
    assert result["summary"]["cost_unknown_scoped"] is True


def test_account_scoped_query_marks_cost_unknown_as_not_evaluated(db):
    """계정 지정(쿠팡 전용) 조회는 마켓플레이스가 스코프 밖 — 0을 «없음»으로 읽지 않게 표시한다."""
    _ch(db, 1, "COUPANG_WING1")
    _ch(db, 5, "NAVER", platform="naver", company=None)
    _pm(db, 20, "OHI-ZERO", cost=Decimal("0"))
    db.add(Order(channel_id=5, order_number="N2", platform_product_id="NP2", product_id=20,
                 quantity=1, selling_price=Decimal("44500"), order_date=OD, status="delivered",
                 commission_amount=Decimal("1000")))
    db.commit()

    result = compute_pnl_reconciliation(db, *WIN, account="COUPANG_WING1")

    assert result["summary"]["cost_unknown_scoped"] is False   # 판정 자체를 안 했다
    assert result["summary"]["cost_unknown_skus"] == 0
