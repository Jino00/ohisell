# test_mapping_coverage.py — 상품 연관맵 S2 커버리지 리포트 테스트
# 트랙 docs/tracks/active/track_product-connection-map.md S2.
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Channel, Order, ProductChannelMapping, ProductMaster
from app.services.mapping_coverage import compute_mapping_coverage


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _order(channel_id, pid, product_id=None, order_number="o1"):
    return Order(
        channel_id=channel_id,
        product_id=product_id,
        order_number=order_number,
        platform_product_id=pid,
        selling_price=Decimal("1000"),
        order_date=datetime(2026, 7, 1),
    )


def test_compute_mapping_coverage_full_coverage(db):
    ch = Channel(name="cafe24", code="CAFE24", platform="cafe24", channel_type="own")
    db.add(ch)
    db.commit()
    p = ProductMaster(internal_sku="OHI-0001", product_name="A", cost_price=Decimal("100"))
    db.add(p)
    db.flush()
    db.add(ProductChannelMapping(product_id=p.id, channel_id=ch.id, channel_product_id="P001", is_active=True))
    db.add(_order(ch.id, "P001", product_id=p.id))
    db.commit()

    [cov] = compute_mapping_coverage(db)
    assert cov.mapped_option_count == 1
    assert cov.order_option_count == 1
    assert cov.unmapped_order_options == []
    assert cov.order_option_coverage == 1.0
    assert cov.total_orders == 1
    assert cov.unlinked_orders == 0


def test_compute_mapping_coverage_detects_unmapped_option(db):
    ch = Channel(name="naver", code="NAVER", platform="naver", channel_type="marketplace")
    db.add(ch)
    db.commit()
    p = ProductMaster(internal_sku="OHI-0001", product_name="A", cost_price=Decimal("100"))
    db.add(p)
    db.flush()
    db.add(ProductChannelMapping(product_id=p.id, channel_id=ch.id, channel_product_id="P001", is_active=True))
    db.add(_order(ch.id, "P001", product_id=p.id, order_number="o1"))
    db.add(_order(ch.id, "P999", product_id=None, order_number="o2"))  # 매핑 없는 옵션
    db.add(_order(ch.id, "P999", product_id=None, order_number="o3"))
    db.commit()

    [cov] = compute_mapping_coverage(db)
    assert cov.order_option_count == 2  # distinct: P001, P999
    assert cov.unmapped_order_options == [{"option_id": "P999", "order_count": 2}]
    assert cov.order_option_coverage == 0.5
    assert cov.total_orders == 3
    assert cov.unlinked_orders == 2


def test_compute_mapping_coverage_no_orders_defaults_to_full(db):
    ch = Channel(name="rocket", code="COUPANG_ROCKET", platform="coupang", channel_type="consignment")
    db.add(ch)
    db.commit()

    [cov] = compute_mapping_coverage(db)
    assert cov.order_option_count == 0
    assert cov.order_option_coverage == 1.0
    assert cov.unmapped_order_options == []


def test_compute_mapping_coverage_blank_option_id_not_hidden_by_full_coverage(db):
    # codex P2: platform_product_id="" 주문은 order_option_count에서 제외되므로
    # coverage=1.0으로 보일 수 있음 — blank_option_id_orders로 별도 노출해야 한다.
    ch = Channel(name="cafe24", code="CAFE24", platform="cafe24", channel_type="own")
    db.add(ch)
    db.commit()
    db.add(_order(ch.id, "", order_number="o1"))
    db.add(_order(ch.id, "", order_number="o2"))
    db.commit()

    [cov] = compute_mapping_coverage(db)
    assert cov.order_option_count == 0
    assert cov.order_option_coverage == 1.0  # 지표 자체는 그대로(문서화된 한계)
    assert cov.blank_option_id_orders == 2  # 하지만 이 필드로 문제가 드러남


# ── 라우터 통합 ──
@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSession()
    ch = Channel(name="cafe24", code="CAFE24", platform="cafe24", channel_type="own")
    session.add(ch)
    session.commit()
    p = ProductMaster(internal_sku="OHI-0001", product_name="A", cost_price=Decimal("100"))
    session.add(p)
    session.flush()
    session.add(ProductChannelMapping(product_id=p.id, channel_id=ch.id, channel_product_id="P001", is_active=True))
    session.add(_order(ch.id, "P999", order_number="o1"))
    session.commit()
    session.close()

    def _override_get_db():
        s = TestingSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_mapping_coverage_route(client):
    resp = client.get("/api/products/mapping-coverage")
    assert resp.status_code == 200
    body = resp.json()
    cafe24 = next(c for c in body if c["channel_code"] == "CAFE24")
    assert cafe24["mapped_option_count"] == 1
    assert cafe24["unmapped_order_options"] == [{"option_id": "P999", "order_count": 1}]
    assert cafe24["unmapped_order_options_truncated"] == 0


def test_mapping_coverage_route_limit_param_truncates_and_is_requeryable(client):
    # limit=0 → 전부 잘림(codex P2: 그래도 truncated 필드로 나머지 건수는 알 수 있음)
    resp = client.get("/api/products/mapping-coverage?limit=0")
    body = resp.json()
    cafe24 = next(c for c in body if c["channel_code"] == "CAFE24")
    assert cafe24["unmapped_order_options"] == []
    assert cafe24["unmapped_order_options_truncated"] == 1

    # limit을 키워 재요청하면 나머지를 그대로 받을 수 있다(API 계약이 긴 꼬리를 숨기지 않음)
    resp2 = client.get("/api/products/mapping-coverage?limit=100")
    body2 = resp2.json()
    cafe24_2 = next(c for c in body2 if c["channel_code"] == "CAFE24")
    assert cafe24_2["unmapped_order_options"] == [{"option_id": "P999", "order_count": 1}]
    assert cafe24_2["unmapped_order_options_truncated"] == 0
