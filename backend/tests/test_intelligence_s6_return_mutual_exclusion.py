# test_intelligence_s6_return_mutual_exclusion.py — S6 reconcile↔return_deduction 이중차감 방지 (트랙 D-12)
# 머니코드 fixture(인메모리 SQLite). 라이브 API 없음.
# D-12(머니룰, 2026-06-14 라이브 확정): reconcile-by-absence가 '전체취소'를 status=cancelled로
#   매출에서 제외(권위)하므로, return_deduction이 같은 취소를 또 빼면 이중계상이 된다.
#   불변식: return_deduction은 '매출에 잡힌(status 활성) 주문'의 반품에만 적용된다.
#   - 전체취소(status∈REVENUE_EXCLUDED) + 반품기록 → _agg_returns에서 제외(차감 0)
#   - 부분반품(주문 status 활성/delivered) → 계속 차감(기존 동작 보존)
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Channel, CoupangProductItem, CoupangReturnItem, Order
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


def _product(db, vid, account_key, vendor_id, sale_price=0):
    db.add(CoupangProductItem(
        vendor_item_id=vid, account_key=account_key, vendor_id=vendor_id,
        seller_product_id=f"SP_{vid}", item_name=f"opt{vid}",
        sale_price=Decimal(str(sale_price))))


def _order(db, channel_id, oid, vid, price, qty=1, status="delivered"):
    db.add(Order(channel_id=channel_id, order_number=oid, platform_product_id=vid,
                 quantity=qty, selling_price=Decimal(str(price)), order_date=OD, status=status))


def _ret(db, account_key, vendor_id, oid, vid, cancel_count=1):
    db.add(CoupangReturnItem(
        receipt_id=f"R_{oid}", vendor_item_id=vid, account_key=account_key,
        vendor_id=vendor_id, order_id=oid, receipt_type="RETURN",
        cancel_count=cancel_count, withdrawn=False, requested_at=OD))


def _cc(db, acc=None):
    return compute_command_center(db, WIN[0], WIN[1], acc)["account"]["summary"]


def test_cancelled_order_return_excluded_no_double_count(db):
    """전체취소(status=cancelled) + 반품기록 → 매출제외 + return_deduction 제외(이중차감 없음).

    핵심 회귀: reconcile가 status=cancelled로 뒤집은 주문의 반품행을 빼지 않으면,
    매출에서 한 번(제외) + return_deduction에서 또 한 번(차감) = 같은 취소 2번 반영."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _product(db, "V1", "COUPANG_WING1", "A01564720", sale_price=10000)  # master 단가>0
    _order(db, 1, "O1", "V1", 10000, qty=1, status="cancelled")        # reconcile가 뒤집음
    _ret(db, "COUPANG_WING1", "A01564720", "O1", "V1", cancel_count=1)  # 같은 주문 반품기록
    db.commit()
    s = _cc(db)
    assert s["revenue"] == _Z                 # 취소 → 매출 제외(권위)
    assert s["return_deduction"] == _Z        # ★수정 전엔 10,000(master 단가) → 이중차감
    assert s["net_profit"] == _Z              # 0 − 0 (이중차감 없음). 수정 전: −10,000


def test_returned_status_order_return_excluded(db):
    """status=returned(전체반품)도 매출제외 → 반품행 제외(REVENUE_EXCLUDED 전체 대칭)."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _product(db, "V1", "COUPANG_WING1", "A01564720", sale_price=8000)
    _order(db, 1, "O1", "V1", 8000, qty=1, status="returned")
    _ret(db, "COUPANG_WING1", "A01564720", "O1", "V1", cancel_count=1)
    db.commit()
    s = _cc(db)
    assert s["revenue"] == _Z
    assert s["return_deduction"] == _Z


def test_active_order_partial_return_still_deducted(db):
    """부분반품: 주문이 활성(delivered)으로 남음(쿠팡 ordersheets 유지) → return_deduction 계속 적용.

    수정이 전체취소만 제외하고 활성주문 반품은 건드리지 않음을 보장(기존 동작 보존)."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _product(db, "V1", "COUPANG_WING1", "A01564720", sale_price=10000)
    _order(db, 1, "O1", "V1", 20000, qty=2, status="delivered")        # 2개 주문(라인총액 20,000)
    _ret(db, "COUPANG_WING1", "A01564720", "O1", "V1", cancel_count=1)  # 1개만 반품
    db.commit()
    s = _cc(db)
    assert s["revenue"] == Decimal("20000")            # 활성 → 매출 포함
    assert s["return_deduction"] == Decimal("10000")   # 단가(20,000/2)×1 반품 = 10,000 차감


def test_active_order_full_return_record_still_deducted(db):
    """활성주문(delivered)인데 반품기록 존재(반품 sync는 됐으나 reconcile 미적용/미발생) →
    return_deduction이 stale 매출을 차감해 보정(reconcile 적용 전 안전망)."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _product(db, "V1", "COUPANG_WING1", "A01564720", sale_price=10000)
    _order(db, 1, "O1", "V1", 10000, qty=1, status="delivered")
    _ret(db, "COUPANG_WING1", "A01564720", "O1", "V1", cancel_count=1)
    db.commit()
    s = _cc(db)
    assert s["revenue"] == Decimal("10000")
    assert s["return_deduction"] == Decimal("10000")   # 매출 포함분을 차감 → net 0


def test_account_split_preserved_with_exclusion(db):
    """상호배타 필터가 계정 분리/등가성을 깨지 않음(sum==전체)."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _ch(db, 2, "COUPANG_WING2", "오하이테크")
    _product(db, "V1", "COUPANG_WING1", "A01564720", sale_price=10000)
    _product(db, "V2", "COUPANG_WING2", "A01029796", sale_price=5000)
    _order(db, 1, "O1", "V1", 10000, status="cancelled")               # 오픽스 전체취소
    _ret(db, "COUPANG_WING1", "A01564720", "O1", "V1")
    _order(db, 2, "O2", "V2", 5000, status="delivered")                # 오하이 활성
    db.commit()
    w1 = _cc(db, "COUPANG_WING1")
    w2 = _cc(db, "COUPANG_WING2")
    tot = _cc(db, None)
    assert w1["return_deduction"] == _Z                # 취소 제외
    assert w1["revenue"] == _Z
    assert w2["revenue"] == Decimal("5000")
    assert w1["revenue"] + w2["revenue"] == tot["revenue"]
    assert w1["return_deduction"] + w2["return_deduction"] == tot["return_deduction"]


def test_same_orderid_different_accounts_no_cross_suppression(db):
    """Codex P1#1: 같은 order_id가 두 계정에 존재 — WING1 전체취소, WING2 활성+반품.

    상관 차원이 order_number뿐이면 WING1의 취소가 WING2의 반품차감을 억제해 WING2
    net_profit이 틀어진다. vendor_item_id는 전역 UNIQUE(계정마다 다른 vid)이므로 라인 상관이
    교차계정 억제를 막는다. (account 상관도 함께 두어 D-8 매핑으로 이중 방어.)"""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _ch(db, 2, "COUPANG_WING2", "오하이테크")
    _product(db, "V1", "COUPANG_WING1", "A01564720", sale_price=10000)
    _product(db, "V2", "COUPANG_WING2", "A01029796", sale_price=7000)  # vid 전역 UNIQUE → 계정별 다른 vid
    _order(db, 1, "O1", "V1", 10000, status="cancelled")              # WING1: order_id O1, 전체취소
    _ret(db, "COUPANG_WING1", "A01564720", "O1", "V1")
    _order(db, 2, "O1", "V2", 7000, status="delivered")              # WING2: 같은 order_id O1, 활성
    _ret(db, "COUPANG_WING2", "A01029796", "O1", "V2")               # WING2 반품(차감돼야 함)
    db.commit()
    w2 = _cc(db, "COUPANG_WING2")
    assert w2["revenue"] == Decimal("7000")
    assert w2["return_deduction"] == Decimal("7000")  # ★order_number만 상관하면 0(WING1 취소가 억제)
    w1 = _cc(db, "COUPANG_WING1")
    assert w1["return_deduction"] == _Z               # WING1은 취소 → 제외


def test_same_orderid_different_vid_line_precision(db):
    """Codex P1#2: 같은 주문번호의 다른 옵션라인 — 취소라인 vid만 억제, 활성라인 vid 반품은 유지.

    (reconcile는 주문 전체를 뒤집지만, 라인 상관이 grain 정합·증명가능성을 보장.)"""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _product(db, "VC", "COUPANG_WING1", "A01564720", sale_price=10000)  # 취소될 라인
    _product(db, "VA", "COUPANG_WING1", "A01564720", sale_price=8000)   # 활성 라인
    _order(db, 1, "O1", "VC", 10000, status="cancelled")               # 같은 주문번호, 취소 라인
    _ret(db, "COUPANG_WING1", "A01564720", "O1", "VC")
    _order(db, 1, "O1", "VA", 8000, status="delivered")                # 같은 주문번호, 활성 라인
    _ret(db, "COUPANG_WING1", "A01564720", "O1", "VA")                  # 활성 라인 반품(차감돼야 함)
    db.commit()
    s = _cc(db)
    assert s["revenue"] == Decimal("8000")            # 활성 라인만 매출
    assert s["return_deduction"] == Decimal("8000")   # 활성 라인 반품만 차감(취소 라인 vid 제외)
