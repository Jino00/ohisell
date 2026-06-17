# test_rocket_intelligence.py — 쿠팡 로켓배송(1P) 종합조망 편입 머니룰 (트랙 rocket-1p S4, D-11/D-12)
# 머니코드 fixture(인메모리 SQLite). 라이브 API 없음.
#   ① 매출 = Σ 발주 gross, 발주일 KST(po_created_at+9h) 윈도우 경계
#   ② 광고 = Retail sell_type 합(계정단위)
#   ③ 드리프트 = 발주 vs 매핑 계산서 distinct invoice 정산합(중복제거)
#   ④ net_profit = 매출 − 광고, cost 미반영(has_cost=False, D-12)
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    CoupangAdReport,
    CoupangRocketPurchaseOrder,
    CoupangRocketSettlement,
)
from app.services.coupang.rocket_intelligence import (
    ROCKET_AD_SELL_TYPE,
    compute_rocket_overview,
)

_Z = Decimal(0)
WIN = (date(2026, 6, 1), date(2026, 6, 30))
VID = "A01029796"  # 오하이테크


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _po(db, seq, order_amt, created_at, *, recv=0, qty=1, seqs=None, vendor_id=VID):
    db.add(CoupangRocketPurchaseOrder(
        purchase_order_seq=seq, vendor_id=vendor_id,
        sum_of_order_amount=order_amt, sum_of_receiving_amount=recv,
        sum_of_vendor_confirmed_amount=0, order_qty=qty,
        po_created_at=created_at, vendor_payment_seqs=seqs))


def _settle(db, invoice_seq, payment_amount, *, vendor_id=VID):
    db.add(CoupangRocketSettlement(
        invoice_seq=invoice_seq, vendor_id=vendor_id,
        supply_amount=Decimal(str(payment_amount)) / Decimal("1.1"),
        vat=_Z, payment_amount=Decimal(str(payment_amount))))


def _ad(db, report_date, ad_spend, *, sell_type=ROCKET_AD_SELL_TYPE, vendor_id=VID):
    db.add(CoupangAdReport(
        report_date=report_date, sell_type=sell_type, vendor_id=vendor_id,
        ad_spend=Decimal(str(ad_spend))))


# ── ① 매출 = Σ 발주 gross ──────────────────────────────────────────
def test_revenue_is_sum_of_order_amount_gross(db):
    _po(db, 1, 100000, datetime(2026, 6, 5, 3, 0, 0))   # KST 6/5 12:00
    _po(db, 2, 250000, datetime(2026, 6, 10, 0, 0, 0))  # KST 6/10 09:00
    db.commit()
    r = compute_rocket_overview(db, *WIN)
    assert r["revenue"] == Decimal(350000)
    assert r["po_count"] == 2


# ── KST 윈도우 경계: po_created_at(UTC)+9h가 발주일 ────────────────────
def test_kst_window_boundary(db):
    # 5/31 23:00 UTC → KST 6/1 08:00 (윈도우 IN, dfrom=6/1)
    _po(db, 1, 11111, datetime(2026, 5, 31, 23, 0, 0))
    # 6/30 16:00 UTC → KST 7/1 01:00 (윈도우 OUT, dto=6/30)
    _po(db, 2, 22222, datetime(2026, 6, 30, 16, 0, 0))
    # 5/31 14:00 UTC → KST 5/31 23:00 (윈도우 OUT, 전날)
    _po(db, 3, 33333, datetime(2026, 5, 31, 14, 0, 0))
    db.commit()
    r = compute_rocket_overview(db, *WIN)
    assert r["revenue"] == Decimal(11111)  # PO1만 6월 KST
    assert r["po_count"] == 1


def test_no_date_po_excluded_but_counted(db):
    _po(db, 1, 100000, datetime(2026, 6, 5, 3, 0, 0))
    _po(db, 2, 999999, None)  # 발주일 미상 → 윈도우 제외, 카운트만
    db.commit()
    r = compute_rocket_overview(db, *WIN)
    assert r["revenue"] == Decimal(100000)
    assert r["no_date_po_count"] == 1


# ── ② 광고 = Retail 합, 3P/2P 제외 ──────────────────────────────────
def test_ad_only_retail_sell_type(db):
    _ad(db, date(2026, 6, 3), 5000)                       # Retail
    _ad(db, date(2026, 6, 4), 7000)                       # Retail
    _ad(db, date(2026, 6, 4), 99999, sell_type="3P")      # 3P 제외
    _ad(db, date(2026, 6, 4), 88888, sell_type="2P")      # 2P(RG) 제외
    _ad(db, date(2026, 5, 31), 12345)                     # 윈도우 밖 제외
    db.commit()
    r = compute_rocket_overview(db, *WIN)
    assert r["ad_spend"] == Decimal(12000)


# ── ③ 드리프트 = distinct invoice 중복제거 ─────────────────────────────
def test_drift_distinct_invoice_dedup(db):
    # PO1·PO2가 같은 계산서 1001을 공유(1계산서↔N PO). 정산은 한 번만 세야 함.
    _po(db, 1, 60000, datetime(2026, 6, 5, 3, 0, 0), seqs=[1001])
    _po(db, 2, 40000, datetime(2026, 6, 6, 3, 0, 0), seqs=[1001, 1002])
    _settle(db, 1001, 70000)
    _settle(db, 1002, 25000)
    db.commit()
    r = compute_rocket_overview(db, *WIN)
    d = r["drift"]
    assert r["revenue"] == Decimal(100000)
    # distinct invoices {1001,1002} → 70000+25000=95000 (1001 중복 안 셈)
    assert d["settled_amount"] == Decimal(95000)
    assert d["drift_abs"] == Decimal(5000)
    assert d["mapped_invoice_count"] == 2
    assert d["mapped_po_count"] == 2


def test_drift_pct_and_zero_revenue(db):
    db.commit()  # 데이터 없음
    r = compute_rocket_overview(db, *WIN)
    assert r["revenue"] == _Z
    assert r["drift"]["drift_pct"] is None  # 발주 0 → 비율 None
    assert r["net_profit"] == _Z


# ── ④ net_profit = 매출 − 광고, cost 미반영 ─────────────────────────────
def test_net_profit_excludes_cost(db):
    _po(db, 1, 100000, datetime(2026, 6, 5, 3, 0, 0))
    _ad(db, date(2026, 6, 5), 8000)
    db.commit()
    r = compute_rocket_overview(db, *WIN)
    assert r["has_cost"] is False
    assert r["cost"] == _Z
    assert r["net_profit"] == Decimal(92000)  # 100000 − 8000 (원가 빠짐)


# ── vendor_id 필터(계정 분리) ─────────────────────────────────────────
def test_vendor_id_filter(db):
    _po(db, 1, 100000, datetime(2026, 6, 5, 3, 0, 0), vendor_id=VID)
    _po(db, 2, 500000, datetime(2026, 6, 5, 3, 0, 0), vendor_id="OTHER")
    db.commit()
    r = compute_rocket_overview(db, *WIN, vendor_id=VID)
    assert r["revenue"] == Decimal(100000)
    assert r["period"]["vendor_id"] == VID
