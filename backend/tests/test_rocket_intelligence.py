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
    CoupangRocketPurchaseOrderItem,
    CoupangRocketSettlement,
    ProductMaster,
    RocketProductCostMap,
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


# ═══════════════════════════════════════════════════════════════════
# ⑤ 원가 결합 (S4.5c, D-13) — _rocket_cost via compute_rocket_overview
# ═══════════════════════════════════════════════════════════════════
def _master(db, sku, cost, name="x"):
    db.add(ProductMaster(internal_sku=sku, product_name=name, cost_price=Decimal(str(cost))))


def _item(db, po_seq, pn, qty, line_amt, *, vendor_id=VID):
    db.add(CoupangRocketPurchaseOrderItem(
        purchase_order_seq=po_seq, vendor_id=vendor_id, line_no=1,
        product_number=pn, order_qty=qty,
        unit_purchase_price=Decimal(str(line_amt)) / qty if qty else Decimal(0),
        line_order_amount=Decimal(str(line_amt)),
        line_supply_amount=Decimal(str(line_amt)), line_vat=Decimal(0)))


def _map(db, pn, sku=None, status="confirmed"):
    db.add(RocketProductCostMap(product_number=pn, internal_sku=sku, status=status))


def test_cost_confirmed_mapping_reduces_net_profit(db):
    # PO1(발주 100000) 발주상세 1 SKU(수량 10) → 매핑 OHI-1(원가 3000) → 원가 30000
    _po(db, 1, 100000, datetime(2026, 6, 5, 3, 0, 0), qty=10)
    _item(db, 1, "PN-1", 10, 100000)
    _master(db, "OHI-1", 3000)
    _map(db, "PN-1", "OHI-1")
    _ad(db, date(2026, 6, 5), 8000)
    db.commit()
    r = compute_rocket_overview(db, *WIN)
    assert r["has_cost"] is True
    assert r["cost"] == Decimal(30000)        # 10 × 3000
    assert r["net_profit"] == Decimal(62000)  # 100000 − 8000 − 30000
    cc = r["cost_coverage"]
    assert cc["coverage_pct"] == Decimal("1.0000")  # 전 매출분 매핑됨
    assert cc["pos_without_detail_count"] == 0


def test_cost_partial_coverage_is_transparent(db):
    # PO1: 발주상세 2 SKU — 하나만 매핑(60000분), 하나 미매핑(40000분). PO2: 발주상세 미수집.
    _po(db, 1, 100000, datetime(2026, 6, 5, 3, 0, 0), qty=10)
    _po(db, 2, 50000, datetime(2026, 6, 6, 3, 0, 0), qty=5)  # 발주상세 없음
    _item(db, 1, "PN-A", 6, 60000)
    _item(db, 1, "PN-B", 4, 40000)
    _master(db, "OHI-A", 2000)
    _map(db, "PN-A", "OHI-A")  # PN-B는 미매핑
    db.commit()
    r = compute_rocket_overview(db, *WIN)
    assert r["cost"] == Decimal(12000)  # 6 × 2000 (PN-A만)
    cc = r["cost_coverage"]
    # 분모 = 전체 발주 150000, 해결분 = 60000(PN-A confirmed) → 0.4
    assert cc["coverage_pct"] == Decimal("0.4000")
    assert cc["unmapped_order_amount"] == Decimal(40000)   # PN-B
    assert cc["unmapped_sku_count"] == 1
    assert cc["pos_without_detail_count"] == 1             # PO2 발주상세 미수집
    assert r["net_profit"] == Decimal(138000)              # 150000 − 0 − 12000


def test_cost_excluded_is_unresolved_not_zero_cost(db):
    """★'excluded'는 **원가 0도 해결분도 아니다** (2026-08-10 뒤집힘 — 합격기준 ④).

    종전 이 테스트는 정반대를 지켰다: `has_cost=True` · `cost=0` · `coverage_pct=1.0000`.
    그 해석 때문에 2026-06-17 배치가 «후보를 못 찾아» 제외로 찍은 22건이 **원가 0원 =
    전액 이익**이 되고 **커버리지까지 100%로 부풀었다**(90일 발주 실측: 발주 8,146,140원 /
    진짜 원가 3,311,826원). 커버리지가 부푸는 쪽이 특히 나쁘다 — 「원가가 다 붙었다」는
    안심을 주면서 실제로는 안 붙어 있기 때문이다.
    ★이 테스트가 다시 뒤집히면 그 사고가 되살아난 것이다.
    """
    _po(db, 1, 50000, datetime(2026, 6, 5, 3, 0, 0), qty=5)
    _item(db, 1, "PN-FREE", 5, 50000)
    _map(db, "PN-FREE", status="excluded")
    db.commit()
    r = compute_rocket_overview(db, *WIN)
    assert r["has_cost"] is False, "원가가 붙은 라인이 하나도 없다"
    assert r["cost"] == _Z
    cc = r["cost_coverage"]
    assert cc["coverage_pct"] == Decimal("0.0000"), "제외분을 해결로 세면 커버리지가 부푼다"
    assert cc["excluded_order_amount"] == Decimal(50000)   # 크기는 계속 보인다
    assert cc["resolved_order_amount"] == _Z
    assert cc["unmapped_order_amount"] == _Z               # «매핑 없음»과는 여전히 구분된다


def test_excluded_and_unmapped_are_both_unresolved_but_distinguishable(db):
    """★둘 다 미해결이지만 **할 일이 다르다** — 화면이 가를 수 있어야 한다.

    제외 = 사람이 «연결 안 함»으로 정한 것(다시 묻지 마라) / 미매핑 = 아직 안 이은 것(이어라).
    합쳐서 한 숫자로 만들면 작업 목록이 거짓말한다.
    """
    _po(db, 1, 80000, datetime(2026, 6, 5, 3, 0, 0), qty=8)
    _item(db, 1, "PN-FREE", 5, 50000)
    _item(db, 1, "PN-TODO", 3, 30000)
    _map(db, "PN-FREE", status="excluded")
    db.commit()
    cc = compute_rocket_overview(db, *WIN)["cost_coverage"]
    assert cc["excluded_order_amount"] == Decimal(50000)
    assert cc["unmapped_order_amount"] == Decimal(30000)
    assert cc["resolved_order_amount"] == _Z
    assert cc["coverage_pct"] == Decimal("0.0000")


def test_cost_no_mapping_behaves_like_s4(db):
    # 발주상세/매핑 전혀 없음 → S4와 동일(has_cost=False, cost=0)
    _po(db, 1, 100000, datetime(2026, 6, 5, 3, 0, 0))
    _ad(db, date(2026, 6, 5), 8000)
    db.commit()
    r = compute_rocket_overview(db, *WIN)
    assert r["has_cost"] is False
    assert r["cost"] == _Z
    assert r["net_profit"] == Decimal(92000)
    # 분모(발주 100000)>0·해결분 0 → 0/100000 = 0.0000 (None 아님). 매출 0일 때만 None.
    assert r["cost_coverage"]["coverage_pct"] == Decimal("0.0000")


def test_cost_window_isolation(db):
    # 윈도우 밖 PO의 발주상세는 원가에 포함 안 됨
    _po(db, 1, 100000, datetime(2026, 6, 5, 3, 0, 0), qty=10)
    _po(db, 2, 999999, datetime(2026, 5, 1, 3, 0, 0), qty=10)  # 5월(윈도우 밖)
    _item(db, 1, "PN-1", 10, 100000)
    _item(db, 2, "PN-1", 10, 999999)  # 윈도우 밖 PO → 제외돼야
    _master(db, "OHI-1", 3000)
    _map(db, "PN-1", "OHI-1")
    db.commit()
    r = compute_rocket_overview(db, *WIN)
    assert r["cost"] == Decimal(30000)  # PO1만 (PO2 5월 제외)
