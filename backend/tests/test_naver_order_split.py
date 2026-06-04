# 네이버 분할 productOrderId 회귀 테스트 (트랙 N1·D-6)
# 같은 (주문, 상품)이 2개 productOrderId로 분할될 때 수량·매출이 누락되지 않는지 검증.
from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.clients.naver import NaverClient
from app.database import Base
from app.models import Channel, Order


# ── 1단계/2단계 네이버 API 모킹 응답 ────────────────────────────────
_STATUS_RESP = {
    "data": {
        "lastChangeStatuses": [
            {"productOrderId": "PO_A"},
            {"productOrderId": "PO_B"},
        ]
    }
}
# 같은 주문(ORD1) + 같은 상품(9810)인데 productOrderId가 2개로 분할된 케이스
_DETAIL_RESP = {
    "data": [
        {
            "order": {"orderId": "ORD1", "paymentDate": "2026-06-01T10:00:00+09:00"},
            "productOrder": {
                "productOrderId": "PO_A", "productId": "9810", "productName": "상품X",
                "quantity": 1, "totalPaymentAmount": 10000, "productOrderStatus": "PAYED",
            },
        },
        {
            "order": {"orderId": "ORD1", "paymentDate": "2026-06-01T10:00:00+09:00"},
            "productOrder": {
                "productOrderId": "PO_B", "productId": "9810", "productName": "상품X",
                "quantity": 2, "totalPaymentAmount": 20000, "productOrderStatus": "PAYED",
            },
        },
    ]
}


def _make_client() -> NaverClient:
    cfg = SimpleNamespace(client_id="x", client_secret="y")
    client = NaverClient(cfg, access_token="dummy")
    client._request = lambda method, path, params=None: _STATUS_RESP  # type: ignore[assignment]
    client._request_post = lambda path, body: _DETAIL_RESP            # type: ignore[assignment]
    return client


def test_naver_fetch_orders_preserves_split_product_orders():
    """분할 productOrderId가 각각 RawOrder로 보존되는지 (버그: 2번째가 드롭됐었음)."""
    client = _make_client()
    orders = client.fetch_orders(date(2026, 6, 1), date(2026, 6, 1))

    # 2개 productOrder가 모두 보존돼야 한다 (이전 코드는 1개만 반환).
    assert len(orders) == 2
    line_ids = {o.platform_order_line_id for o in orders}
    assert line_ids == {"PO_A", "PO_B"}

    # 같은 (주문, 상품)로 묶이지만 라인은 분리.
    assert {o.order_number for o in orders} == {"ORD1"}
    assert {o.platform_product_id for o in orders} == {"9810"}

    # 수량·매출 합계가 온전해야 한다 (분모/분자 비대칭 방지).
    assert sum(o.quantity for o in orders) == 3
    assert sum(o.selling_price for o in orders) == Decimal("30000")


def test_order_unique_constraint_allows_split_line_ids():
    """uq_order_item이 line_id를 포함 → 같은 (주문,상품) 다른 productOrderId 2행 허용,
    같은 line_id 중복은 거부."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    ch = Channel(name="네이버", code="NAVER", platform="naver")
    db.add(ch)
    db.commit()

    def _mk(line_id: str, qty: int, price: str) -> Order:
        return Order(
            channel_id=ch.id, order_number="ORD1", platform_product_id="9810",
            platform_order_line_id=line_id, quantity=qty, selling_price=Decimal(price),
            shipping_cost=None, order_date=date(2026, 6, 1), status="confirmed",
        )

    db.add(_mk("PO_A", 1, "10000"))
    db.add(_mk("PO_B", 2, "20000"))
    db.commit()  # 분할 라인 2행 — 통과해야 함

    rows = db.query(Order).all()
    assert len(rows) == 2
    assert sum(r.quantity for r in rows) == 3

    # 같은 line_id 중복 적재는 제약 위반
    db.add(_mk("PO_A", 9, "99999"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.close()


def test_legacy_empty_line_id_row_is_promoted_not_duplicated(monkeypatch):
    """마이그레이션 호환 브리지: line_id="" 레거시 행이 재동기화 시 승격되고,
    분할 라인이 이중 적재되지 않는지 (Codex P1 — 중복 적재 위험)."""
    from app.clients.base import RawOrder
    from app.services import sync_service

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    ch = Channel(name="네이버", code="NAVER", platform="naver",
                 api_type="oauth2_bcrypt", api_config_key="NAVER")
    db.add(ch)
    db.commit()

    # 마이그레이션 백필이 못 채운 레거시 행(line_id="") — 첫 productOrder만 저장돼 있던 상태
    db.add(Order(
        channel_id=ch.id, order_number="ORD1", platform_product_id="9810",
        platform_order_line_id="", quantity=1, selling_price=Decimal("10000"),
        shipping_cost=None, order_date=date(2026, 6, 1), status="confirmed",
    ))
    db.commit()

    # 재동기화: 같은 (주문, 상품)이 2개 productOrderId로 분할돼 들어온다
    fake_orders = [
        RawOrder(order_number="ORD1", platform_product_id="9810", platform_order_line_id="PO_A",
                 platform_product_name="상품X", quantity=1, selling_price=Decimal("10000"),
                 shipping_cost=None, order_date="2026-06-01T10:00:00", status="confirmed", raw_data={}),
        RawOrder(order_number="ORD1", platform_product_id="9810", platform_order_line_id="PO_B",
                 platform_product_name="상품X", quantity=2, selling_price=Decimal("20000"),
                 shipping_cost=None, order_date="2026-06-01T10:00:00", status="confirmed", raw_data={}),
    ]

    class _FakeClient:
        def test_connection(self): return {"status": "ok"}
        def fetch_orders(self, date_from, date_to): return fake_orders

    monkeypatch.setattr(sync_service, "_get_client_for_channel", lambda channel, db=None: _FakeClient())

    result = sync_service.sync_channel_orders(db, ch.id, date(2026, 6, 1), date(2026, 6, 1))
    assert result["status"] == "success"

    rows = db.query(Order).all()
    # 레거시 행 승격 + 나머지 1행 insert = 총 2행 (3행이면 이중 적재 버그)
    assert len(rows) == 2
    assert {r.platform_order_line_id for r in rows} == {"PO_A", "PO_B"}
    assert "" not in {r.platform_order_line_id for r in rows}  # 빈 line_id 잔존 없음
    assert sum(r.quantity for r in rows) == 3
    db.close()
