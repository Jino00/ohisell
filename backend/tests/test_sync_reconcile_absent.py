# test_sync_reconcile_absent.py — S6 reconcile-by-absence (트랙 reconciliation D-10)
# 쿠팡에서 사라진(취소) 주문을 cancelled 처리. 안전장치(쿠팡만·빈집합·블라스트캡·윈도우·활성만) 가드.
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Channel, Order
from app.services.sync_service import _reconcile_absent_orders

WIN = (date(2026, 6, 1), date(2026, 6, 30))
IN = datetime(2026, 6, 10, 12, 0, 0)       # 윈도우 내
OUT = datetime(2026, 5, 1, 12, 0, 0)        # 윈도우 밖


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _ch(db, cid=1, platform="coupang"):
    db.add(Channel(id=cid, name=f"c{cid}", code=f"COUPANG_W{cid}", platform=platform))


def _order(db, oid, status="delivered", when=IN, cid=1, price=10000):
    db.add(Order(channel_id=cid, order_number=oid, platform_product_id="V1",
                 quantity=1, selling_price=Decimal(str(price)), order_date=when, status=status))


def _status(db, oid):
    return db.query(Order).filter(Order.order_number == oid).first().status


def test_absent_order_marked_cancelled(db):
    _ch(db)
    _order(db, "A"); _order(db, "B"); _order(db, "C")
    db.commit()
    n = _reconcile_absent_orders(db, 1, "coupang", WIN[0], WIN[1], {"A", "B"})
    db.commit()
    assert n == 1
    assert _status(db, "C") == "cancelled"   # 조회에 없음 → 취소
    assert _status(db, "A") == "delivered"   # 조회에 있음 → 유지
    assert _status(db, "B") == "delivered"


def test_non_coupang_skipped(db):
    _ch(db, platform="naver")
    _order(db, "A"); _order(db, "B")
    db.commit()
    n = _reconcile_absent_orders(db, 1, "naver", WIN[0], WIN[1], {"A"})
    assert n == 0
    assert _status(db, "B") == "delivered"   # 타 채널은 reconcile 안 함


def test_empty_fetched_skipped(db):
    """전체조회 0건(실패 의심) → mass-cancel 방지로 건너뜀."""
    _ch(db)
    _order(db, "A"); _order(db, "B")
    db.commit()
    n = _reconcile_absent_orders(db, 1, "coupang", WIN[0], WIN[1], set())
    assert n == 0
    assert _status(db, "A") == "delivered"


def test_blast_radius_cap(db):
    """누락이 활성의 30% 초과(부분조회 실패 의심) → 중단(거짓취소 방지)."""
    for i in range(10):
        _order(db, f"O{i}")
    _ch(db)
    db.commit()
    # 10건 중 1건(O0)만 조회됨 → 9건 누락 > 캡(max(5, 3)=5) → 중단
    n = _reconcile_absent_orders(db, 1, "coupang", WIN[0], WIN[1], {"O0"})
    assert n == 0
    assert all(_status(db, f"O{i}") == "delivered" for i in range(10))


def test_within_cap_applies(db):
    """누락이 캡 이내면 적용. 10건 중 6건 조회 → 4건 누락 <= 캡 5 → 취소."""
    for i in range(10):
        _order(db, f"O{i}")
    _ch(db)
    db.commit()
    fetched = {f"O{i}" for i in range(6)}   # O0~O5 존재, O6~O9 누락(4건)
    n = _reconcile_absent_orders(db, 1, "coupang", WIN[0], WIN[1], fetched)
    db.commit()
    assert n == 4
    assert _status(db, "O9") == "cancelled"
    assert _status(db, "O0") == "delivered"


def test_already_cancelled_not_touched(db):
    _ch(db)
    _order(db, "A", status="cancelled"); _order(db, "B", status="returned")
    _order(db, "C", status="pending"); _order(db, "D", status="delivered")
    db.commit()
    n = _reconcile_absent_orders(db, 1, "coupang", WIN[0], WIN[1], {"Z"})  # 아무것도 조회 안됨
    db.commit()
    # 활성(delivered) D만 후보. cancelled/returned/pending은 후보 아님.
    assert n == 1
    assert _status(db, "D") == "cancelled"
    assert _status(db, "A") == "cancelled"   # 불변
    assert _status(db, "C") == "pending"     # 불변


def test_incomplete_fetch_skipped(db):
    """Codex P1#1: fetch_complete=False(부분조회)면 거짓취소 방지로 건너뜀."""
    _ch(db)
    _order(db, "A"); _order(db, "B")
    db.commit()
    n = _reconcile_absent_orders(db, 1, "coupang", WIN[0], WIN[1], {"A"}, fetch_complete=False)
    assert n == 0
    assert _status(db, "B") == "delivered"   # 부분조회 → 취소 안 함


def test_grace_inset_protects_boundary(db):
    """Codex P1#2: grace_days inset 하한 아래(결제지연 경계) 주문은 거짓취소 안 함."""
    _ch(db)
    _order(db, "EARLY", when=datetime(2026, 6, 3, 12, 0))   # date_from(6/1)+grace(7)=6/8 미만
    _order(db, "LATE", when=datetime(2026, 6, 20, 12, 0))   # grace 이후
    db.commit()
    # 둘 다 조회에 없음(fetched_ids={"Z"}). grace=7 → EARLY는 보호, LATE만 취소.
    n = _reconcile_absent_orders(db, 1, "coupang", WIN[0], WIN[1], {"Z"}, grace_days=7)
    db.commit()
    assert n == 1
    assert _status(db, "LATE") == "cancelled"
    assert _status(db, "EARLY") == "delivered"   # grace 보호


def test_outside_window_not_touched(db):
    _ch(db)
    _order(db, "A", when=IN); _order(db, "OLD", when=OUT)
    db.commit()
    n = _reconcile_absent_orders(db, 1, "coupang", WIN[0], WIN[1], {"Z"})
    db.commit()
    assert n == 1
    assert _status(db, "A") == "cancelled"
    assert _status(db, "OLD") == "delivered"  # 윈도우 밖 → 불변
