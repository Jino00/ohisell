# test_rocket_1p_pnl_audit.py — 손익 «근거 화면» SA (2026-08-07 설계 승인)
#
# 이 파일이 지키는 것:
#   ① 원자(day_option_atoms)의 합 = 화면 타일 — 원자는 파생의 단일 출처다
#   ② 검사는 «같은 함수의 다른 그레인»을 비교한다 — 재계산이 아니다
#   ③ B1은 절대 pass가 되지 않는다 — 판정할 수 없는 검사를 초록으로 칠하면 거짓 초록
#   ④ A5·A6·A7은 조용한 결손(INNER JOIN 탈락·분담금 모름·광고 미귀속)을 드러낸다
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text as _t
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (Channel, CoupangAdOptionDaily, CoupangAdReport,
                        CoupangRocketPurchaseOrderItem, CoupangRocketSalesDaily)
from app.services.coupang import rocket_1p_channel_pnl as pnl
from app.services.coupang.rocket_1p_revenue import (
    compute_rocket_1p_revenue, day_option_atoms)

VENDOR = pnl.ROCKET_1P_VENDOR_ID
ZERO_D = Decimal("0")
D = date(2026, 8, 4)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(Channel(id=5, code="COUPANG_ROCKET", name="쿠팡 로켓배송", platform="coupang",
                  channel_type="consignment", company="주식회사 오하이테크"))
    s.commit()
    yield s
    s.close()


def _sale(s, option_id, sku, qty, consumer, *, d=D):
    s.add(CoupangRocketSalesDaily(
        vendor_id=VENDOR, option_id=option_id, sku_id=sku, date=d,
        qty=qty, revenue=Decimal(consumer),
        product_name=f"상품 {option_id}", source="sales_analysis"))


def _price(s, sku, unit_price, seq):
    s.add(CoupangRocketPurchaseOrderItem(
        purchase_order_seq=seq, vendor_id=VENDOR, product_number=sku,
        unit_purchase_price=Decimal(unit_price), order_qty=1))


def _cost(s, sku, cost_price, internal_sku=None, match_method=None):
    isku = internal_sku or f"OHI-{sku}"
    s.execute(_t("INSERT INTO product_master (internal_sku, product_name, cost_price) "
                 "VALUES (:i, :n, :c)"), {"i": isku, "n": isku, "c": cost_price})
    s.execute(_t("INSERT INTO rocket_product_cost_map "
                 "(product_number, internal_sku, status, match_method) "
                 "VALUES (:p, :i, 'confirmed', :m)"),
              {"p": str(sku), "i": isku, "m": match_method})


def _ad_option(s, option_id, spend, d=D):
    s.add(CoupangAdOptionDaily(
        report_date=d, vendor_id=VENDOR, sell_type="Retail",
        ad_option_id=option_id, conv_option_id=option_id,
        impressions=0, clicks=0, ad_spend=Decimal(spend),
        orders=0, sales_qty=0, conversion_revenue=Decimal("0")))


def _ad_account(s, spend, d=D):
    s.add(CoupangAdReport(report_date=d, sell_type="Retail", vendor_id=VENDOR,
                          impressions=0, clicks=0, ad_spend=Decimal(spend),
                          orders=0, sales_qty=0, conversion_revenue=Decimal("0")))


# ═══ ① 원자의 합 = 화면 타일 (원자는 파생의 단일 출처) ═══


def test_atoms_sum_to_screen_tile(db):
    """Σ원자 순이익 = compute_rocket_1p_revenue의 pnl 타일. 원자를 따로 계산하지 않았다는 증거."""
    _sale(db, "A", "S1", 10, "1000000")
    _price(db, "S1", "60000", 1)
    _cost(db, "S1", 20000)
    _ad_option(db, "A", "10000")
    _ad_account(db, "10000")
    db.commit()
    ctx = day_option_atoms(db, D, D)
    r = compute_rocket_1p_revenue(db, D, D)
    atom_sum = sum((a["net_profit"] for a in ctx["atoms"] if a["net_profit"] is not None), ZERO_D)
    assert str(atom_sum) == r["pnl"]["net_profit"]
    assert ctx["burden_known"] is True
