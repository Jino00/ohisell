# test_rg_order_sync_dedupe.py — RG 주문 동기화 중복 라인 회귀 테스트 (2026-07-11~17 prod 6일 침묵 사고)
# 사고: 주문 24101555557691이 같은 vendorItemId를 두 라인(qty 2+2)으로 반환(목록·단건 API 실측 일치)
#   → autoflush=False 세션에서 query-then-add upsert가 pending INSERT를 못 보고 이중 INSERT
#   → commit에서 UNIQUE(order_id, vendor_item_id) 위반 → 그날 배치 전체 롤백 + WING2까지 전멸.
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import CoupangRgOrderItem
from app.services.coupang import rg_order_sync


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    # prod과 동일: autoflush=False (database.py:16) — 사고 재현 조건
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


class _FakeConfig:
    vendor_id = "A01564720"


class _FakeClient:
    """iter_rg_orders만 흉내 — orders 리스트를 주입받아 그대로 yield."""

    def __init__(self, orders):
        self._orders = orders

    def iter_rg_orders(self, *, paid_date_from, paid_date_to):
        yield from self._orders


def _dup_line_order():
    """prod 실측(주문 24101555557691) 재현: 같은 vendorItemId 두 라인, 각 qty 2."""
    line = {
        "vendorItemId": 95521944481,
        "productName": "오하이 풀커버 강화유리 액정보호필름",
        "salesQuantity": 2,
        "unitSalesPrice": "16900.0",
        "currency": "KRW",
    }
    return {
        "orderId": 24101555557691,
        "paidAt": "1783784103000",
        "orderItems": [dict(line), dict(line)],
    }


def _patch_client(monkeypatch, orders_by_account):
    """계정별 주문 리스트로 클라이언트·설정 몽키패치."""
    monkeypatch.setattr(rg_order_sync, "get_coupang_config", lambda key: _FakeConfig())

    calls = {"i": 0}
    keys = list(orders_by_account.keys())

    def _client_factory(cfg):
        key = keys[calls["i"] % len(keys)]
        calls["i"] += 1
        return _FakeClient(orders_by_account[key])

    monkeypatch.setattr(rg_order_sync, "CoupangRocketGrowthClient", _client_factory)


def test_duplicate_vendor_item_lines_commit_ok(db, monkeypatch):
    """같은 주문 안 중복 vendorItemId 라인 → IntegrityError 없이 커밋, 수량 합산(2+2=4)."""
    _patch_client(monkeypatch, {"COUPANG_WING1": [_dup_line_order()]})

    stats = rg_order_sync.sync_account_rg_orders(db, "COUPANG_WING1", days=3)

    assert "error" not in stats
    rows = db.query(CoupangRgOrderItem).all()
    assert len(rows) == 1
    assert rows[0].order_id == "24101555557691"
    assert rows[0].vendor_item_id == "95521944481"
    assert rows[0].sales_quantity == 4  # 2+2 합산 — 덮어쓰기(2)면 매출 과소기록


def test_resync_idempotent(db, monkeypatch):
    """재동기화해도 수량이 누적되지 않는다(4 유지, 8 아님)."""
    _patch_client(monkeypatch, {"COUPANG_WING1": [_dup_line_order()]})
    rg_order_sync.sync_account_rg_orders(db, "COUPANG_WING1", days=3)

    _patch_client(monkeypatch, {"COUPANG_WING1": [_dup_line_order()]})
    rg_order_sync.sync_account_rg_orders(db, "COUPANG_WING1", days=3)

    rows = db.query(CoupangRgOrderItem).all()
    assert len(rows) == 1
    assert rows[0].sales_quantity == 4


def test_same_order_repeated_in_stream(db, monkeypatch):
    """페이지네이션 등으로 같은 주문이 스트림에 두 번 와도 커밋 성공·이중 집계 없음."""
    _patch_client(
        monkeypatch, {"COUPANG_WING1": [_dup_line_order(), _dup_line_order()]}
    )

    stats = rg_order_sync.sync_account_rg_orders(db, "COUPANG_WING1", days=3)

    assert "error" not in stats
    rows = db.query(CoupangRgOrderItem).all()
    assert len(rows) == 1
    assert rows[0].sales_quantity == 4


def test_merged_lines_weighted_price(db, monkeypatch):
    """단가가 다른 중복 라인 → 수량가중 평균 단가로 매출 불변식 보존 (리뷰 F1).

    qty 2 @16,900 + qty 2 @9,900 = 매출 53,600 → 4 × 13,400 (첫 단가 유지면 67,600 과대)."""
    order = _dup_line_order()
    order["orderItems"][1]["unitSalesPrice"] = "9900.0"
    _patch_client(monkeypatch, {"COUPANG_WING1": [order]})

    rg_order_sync.sync_account_rg_orders(db, "COUPANG_WING1", days=3)

    rows = db.query(CoupangRgOrderItem).all()
    assert len(rows) == 1
    assert rows[0].sales_quantity == 4
    assert rows[0].unit_sales_price * rows[0].sales_quantity == 53600


def test_account_isolation_commit_integrity_error(db, monkeypatch):
    """실사고 모드 재현: 계정1이 commit 시점 IntegrityError(pending-rollback 세션)로 죽어도
    rollback 후 계정2가 깨끗한 세션에서 커밋된다 (리뷰 F3 — 6일 침묵 사고의 실제 형태)."""
    good_order = {
        "orderId": 111,
        "paidAt": "1783784103000",
        "orderItems": [
            {
                "vendorItemId": 222,
                "productName": "정상 주문",
                "salesQuantity": 1,
                "unitSalesPrice": "10000.0",
                "currency": "KRW",
            }
        ],
    }
    class _PoisonClient:
        """세션에 중복 키 행 2개를 직접 add → sync의 db.commit()에서 UNIQUE 위반 강제."""

        def iter_rg_orders(self, **kw):
            for _ in range(2):
                db.add(
                    CoupangRgOrderItem(
                        order_id="X", vendor_item_id="Y", account_key="COUPANG_WING1",
                        vendor_id="A0", product_name="poison", sales_quantity=1,
                    )
                )
            yield from ()

    monkeypatch.setattr(rg_order_sync, "get_coupang_config", lambda key: _FakeConfig())
    clients = iter([_PoisonClient(), _FakeClient([good_order])])
    monkeypatch.setattr(
        rg_order_sync, "CoupangRocketGrowthClient", lambda cfg: next(clients)
    )

    results = rg_order_sync.sync_all_rg_orders(db, days=3)

    assert "error" in results[0] and "UNIQUE" in results[0]["error"]
    assert "error" not in results[1]
    rows = db.query(CoupangRgOrderItem).all()
    assert len(rows) == 1
    assert rows[0].order_id == "111"


def test_account_isolation(db, monkeypatch):
    """한 계정의 예기치 못한 실패가 다른 계정 수집을 죽이지 않는다(6일 침묵 사고 회귀 방지)."""
    good_order = {
        "orderId": 111,
        "paidAt": "1783784103000",
        "orderItems": [
            {
                "vendorItemId": 222,
                "productName": "정상 주문",
                "salesQuantity": 1,
                "unitSalesPrice": "10000.0",
                "currency": "KRW",
            }
        ],
    }

    class _BoomClient:
        def iter_rg_orders(self, **kw):
            raise RuntimeError("unexpected boom")
            yield  # pragma: no cover

    monkeypatch.setattr(rg_order_sync, "get_coupang_config", lambda key: _FakeConfig())
    clients = iter([_BoomClient(), _FakeClient([good_order])])
    monkeypatch.setattr(
        rg_order_sync, "CoupangRocketGrowthClient", lambda cfg: next(clients)
    )

    results = rg_order_sync.sync_all_rg_orders(db, days=3)

    assert len(results) == 2
    assert results[0]["account"] == "COUPANG_WING1"
    assert "error" in results[0]  # 실패는 표면화(_coupang_failed가 잡음 — 거짓 성공 금지)
    assert "error" not in results[1]
    rows = db.query(CoupangRgOrderItem).all()
    assert len(rows) == 1  # WING2 데이터는 살아서 커밋됨
    assert rows[0].order_id == "111"
