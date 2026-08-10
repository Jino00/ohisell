# test_intelligence_rg_revenue.py — S3 RG 매출 편입 + S4 net_profit 머니룰 (트랙 reconciliation D-2/D-3)
# 머니코드 fixture(인메모리 SQLite). 라이브 API 없음.
# D-2: RG 매출(CoupangRgOrderItem)을 종합조망 매출에 옵션ID로 편입.
# D-3: net_profit = 3P_net + (RG_rev − RG_cost − rg_total)  [RG 정산 전액차감, D-16 일관]
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    Channel, CoupangProductItem, CoupangReturnItem, CoupangRgOrderItem,
    CoupangRgSettlementFee, Order,
)
from app.services.coupang.intelligence import compute_command_center

_Z = Decimal(0)
WIN = (date(2026, 6, 1), date(2026, 6, 30))
OD = datetime(2026, 6, 5, 12, 0, 0)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _ch(db, cid, code, company):
    db.add(Channel(id=cid, name=f"c{cid}", code=code, platform="coupang", company=company))


def _product(db, vid, account_key, vendor_id, supply=None):
    db.add(CoupangProductItem(
        vendor_item_id=vid, account_key=account_key, vendor_id=vendor_id,
        seller_product_id=f"SP_{vid}", item_name=f"opt{vid}", sale_price=_Z,
        supply_price=(Decimal(str(supply)) if supply is not None else None)))


def _rg_order(db, account_key, vendor_id, vid, unit, qty, oid):
    db.add(CoupangRgOrderItem(
        order_id=oid, vendor_item_id=vid, account_key=account_key, vendor_id=vendor_id,
        sales_quantity=qty, unit_sales_price=Decimal(str(unit)), paid_at=OD))


def _three_p(db, channel_id, oid, vid, price):
    db.add(Order(channel_id=channel_id, order_number=oid, platform_product_id=vid,
                 quantity=1, selling_price=Decimal(str(price)), order_date=OD, status="delivered"))


def _rg_fee(db, account_key, fee_type, amount):
    db.add(CoupangRgSettlementFee(
        account_key=account_key, recognition_date_from=date(2026, 6, 1),
        recognition_date_to=date(2026, 6, 7), fee_type=fee_type, vendor_item_id="",
        amount=Decimal(str(amount))))


def _cc(db, acc=None):
    return compute_command_center(db, WIN[0], WIN[1], acc)["account"]["summary"]


def test_rg_revenue_merged_into_total(db):
    """RG 매출이 summary 매출에 편입되고 revenue_rg/revenue_3p로 분해된다(D-2)."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _product(db, "V1", "COUPANG_WING1", "A01564720")
    _rg_order(db, "COUPANG_WING1", "A01564720", "V1", 5000, 2, "RG1")  # RG 10,000
    db.commit()
    s = _cc(db)
    assert s["revenue"] == Decimal("10000")
    assert s["revenue_rg"] == Decimal("10000")
    assert s["revenue_3p"] == _Z


def test_net_profit_d3_formula(db):
    """net_profit = RG_rev − RG_cost − rg_total (D-3). 단독 RG 옵션."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _product(db, "V1", "COUPANG_WING1", "A01564720", supply=1000)  # 원가 1,000/개
    _rg_order(db, "COUPANG_WING1", "A01564720", "V1", 5000, 2, "RG1")  # RG 10,000, 수량 2
    _rg_fee(db, "COUPANG_WING1", "sale_fee", 800)  # RG 정산 800
    db.commit()
    s = _cc(db)
    # RG_rev 10,000 − RG_cost(1,000×2=2,000) − rg_total 800 = 7,200
    assert s["revenue"] == Decimal("10000")
    assert s["cost"] == Decimal("2000")
    assert s["net_profit_pre_rg"] == Decimal("8000")   # 10,000 − 2,000 (정산 전)
    assert s["rg_settlement_total"] == Decimal("800")
    # D-CPP-33: 납부세액 = 매출VAT(10,000×10/110=909.09) − 매입세액(원가 2,000×10/110=181.82)
    #           = 727.27 → net = 7,200 − 727.27
    assert s["payable_vat"] == Decimal("727.27")
    assert s["net_profit"] == Decimal("6472.73")       # 전액차감 후 납부세액까지


def test_3p_plus_rg_combined(db):
    """3P + RG 합산 매출·순이익. 같은 계정 다른 옵션."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _product(db, "V3P", "COUPANG_WING1", "A01564720")
    _product(db, "VRG", "COUPANG_WING1", "A01564720")
    _three_p(db, 1, "O1", "V3P", 30000)                 # 3P 30,000
    _rg_order(db, "COUPANG_WING1", "A01564720", "VRG", 5000, 2, "RG1")  # RG 10,000
    db.commit()
    s = _cc(db)
    assert s["revenue"] == Decimal("40000")
    assert s["revenue_3p"] == Decimal("30000")
    assert s["revenue_rg"] == Decimal("10000")


def test_same_vid_3p_and_rg_additive(db):
    """같은 vendor_item_id가 3P·RG 양쪽 판매 → 가산(이중계상 아님, 누락 아님)."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _product(db, "V1", "COUPANG_WING1", "A01564720")
    _three_p(db, 1, "O1", "V1", 30000)
    _rg_order(db, "COUPANG_WING1", "A01564720", "V1", 5000, 2, "RG1")
    db.commit()
    s = _cc(db)
    assert s["revenue"] == Decimal("40000")  # 30,000 + 10,000
    assert s["revenue_rg"] == Decimal("10000")


def test_return_deduction_uses_3p_unit_price_not_blended(db):
    """Codex S3 P1#1: 같은 vid가 3P·RG 양쪽 판매 + 3P 반품 → 반품차감은 3P 단가(10,000) 기준.

    RG 매출을 섞은 가중평균(20,000/3≈6,667)을 쓰면 안 됨(반품은 3P 전용)."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _product(db, "V1", "COUPANG_WING1", "A01564720")
    _three_p(db, 1, "O1", "V1", 10000)                              # 3P 단가 10,000
    _rg_order(db, "COUPANG_WING1", "A01564720", "V1", 5000, 2, "RG1")  # RG 단가 5,000×2
    db.add(CoupangReturnItem(
        receipt_id="R1", vendor_item_id="V1", account_key="COUPANG_WING1",
        vendor_id="A01564720", order_id="O1", receipt_type="RETURN",
        cancel_count=1, withdrawn=False, requested_at=OD))         # 3P 반품 1개
    db.commit()
    s = _cc(db)
    assert s["return_deduction"] == Decimal("10000")  # 3P 단가×1 (NOT 6,666.67 혼합)


def test_rg_revenue_account_split(db):
    """RG 매출도 계정별 분리(account_key 필터) + 등가성 sum==전체."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _ch(db, 2, "COUPANG_WING2", "오하이테크")
    _product(db, "V1", "COUPANG_WING1", "A01564720")
    _product(db, "V2", "COUPANG_WING2", "A01029796")
    _rg_order(db, "COUPANG_WING1", "A01564720", "V1", 5000, 2, "RG1")  # 오픽스 RG 10,000
    _rg_order(db, "COUPANG_WING2", "A01029796", "V2", 3000, 1, "RG2")  # 오하이 RG 3,000
    db.commit()
    w1 = _cc(db, "COUPANG_WING1")
    w2 = _cc(db, "COUPANG_WING2")
    tot = _cc(db, None)
    assert w1["revenue_rg"] == Decimal("10000")
    assert w2["revenue_rg"] == Decimal("3000")
    assert tot["revenue_rg"] == Decimal("13000")
    assert w1["revenue"] + w2["revenue"] == tot["revenue"]
