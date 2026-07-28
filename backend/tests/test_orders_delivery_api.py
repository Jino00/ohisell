# test_orders_delivery_api.py — 판매 내역 API의 배송 구분 표면화 (Jino 지시 2026-07-28)
#
# 검증: ①기존 응답 필드가 그대로 살아있고 배송 구분이 **추가**됐는가
#       ②배송방식 × 배송비 부담 교차표가 라이브 실측 4조합을 그대로 재현하는가
#       ③실부담 합계가 조회되는가(N배송 3,020−3,000=20 / 일반 1,900−0=1,900)
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Channel, Order
from app.services import order_delivery

NAVER = 6


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    db.add(Channel(id=NAVER, name="스마트스토어", code="NAVER", platform="naver",
                   channel_type="open"))
    db.commit()

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    yield TestClient(app), db
    app.dependency_overrides.pop(get_db, None)
    db.close()


def _raw(attr, policy, fee_type, fee_amount, logi=None):
    po = {
        "productOrderId": "p", "deliveryAttributeType": attr, "deliveryPolicyType": policy,
        "shippingFeeType": fee_type, "deliveryFeeAmount": fee_amount,
    }
    if logi:
        po["logisticsCompanyId"] = logi
    return json.dumps({"order": {"orderId": "o"}, "productOrder": po}, ensure_ascii=False)


def _add(db, n, attr, policy, fee_type, collected, logi=None, seq=[0]):
    """라이브 실측 조합대로 주문 n건 생성 + 동기화와 같은 경로로 배송 컬럼 채움."""
    for _ in range(n):
        seq[0] += 1
        raw = _raw(attr, policy, fee_type, int(collected or 0), logi)
        o = Order(
            channel_id=NAVER, order_number="ord%d" % seq[0], platform_product_id="cp1",
            platform_order_line_id="l%d" % seq[0], quantity=1,
            selling_price=Decimal("10000"),
            shipping_cost=Decimal(str(collected)) if collected else None,
            order_date=datetime(2026, 7, 1, 12, 0), status="delivered", raw_data=raw,
        )
        order_delivery.apply_delivery_fields(o, raw)
        db.add(o)
    db.commit()


def _seed_live_shape(db):
    # prod 최근 30일 실측 분포(축소 비율 유지): TODAY/무료 · N배송/유료 · NORMAL 두 갈래
    _add(db, 6, "TODAY", "무료", "무료", None)
    _add(db, 3, "ARRIVAL_GUARANTEE", "유료", "선결제", 3000, logi="PG")
    _add(db, 2, "NORMAL", "조건부무료", "무료", None)
    _add(db, 1, "NORMAL", "유료", "선결제", 2500)


def test_order_list_adds_delivery_fields_without_breaking_shape(client):
    c, db = client
    _seed_live_shape(db)
    r = c.get("/api/orders", params={"channel_id": NAVER, "page_size": 50})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 12
    # 기존 필드 보존
    assert {"id", "order_number", "selling_price", "shipping_cost", "status"} <= set(items[0])
    nb = [i for i in items if i["is_nbaesong"]]
    assert len(nb) == 3
    assert nb[0]["delivery_attribute_type"] == "ARRIVAL_GUARANTEE"
    assert nb[0]["delivery_policy_type"] == "유료"
    assert nb[0]["shipping_fee_type"] == "선결제"
    assert nb[0]["logistics_company_id"] == "PG"
    assert Decimal(nb[0]["shipping_cost_paid"]) == Decimal("3020")
    assert Decimal(nb[0]["shipping_cost"]) == Decimal("3000")
    assert Decimal(nb[0]["shipping_cost_net"]) == Decimal("20")   # 실부담


def test_delivery_breakdown_reproduces_four_combinations(client):
    c, db = client
    _seed_live_shape(db)
    r = c.get("/api/orders/delivery-breakdown", params={"channel_id": NAVER})
    assert r.status_code == 200
    d = r.json()
    got = {
        (row["delivery_attribute_type"], row["customer_paid"]): row
        for row in d["rows"]
    }
    # ★배송방식과 배송비 부담은 독립 축 — 4조합이 각각 살아있어야 한다
    assert got[("TODAY", False)]["orders"] == 6
    assert got[("ARRIVAL_GUARANTEE", True)]["orders"] == 3
    assert got[("NORMAL", False)]["orders"] == 2
    assert got[("NORMAL", True)]["orders"] == 1

    # 실부담: N배송 3건 × (3020−3000)=60, 일반 TODAY 6건 × 1900=11,400
    assert Decimal(got[("ARRIVAL_GUARANTEE", True)]["net_total"]) == Decimal("60")
    assert Decimal(got[("TODAY", False)]["net_total"]) == Decimal("11400")
    # 수취가 지불을 넘는 칸은 음수 그대로(리포트는 사실 그대로, BEP의 보수 클램프와 별개)
    assert Decimal(got[("NORMAL", True)]["net_total"]) == Decimal("-600")

    assert d["total_orders"] == 12
    assert Decimal(d["total_paid"]) == Decimal("3") * Decimal("3020") + Decimal("9") * Decimal("1900")
    assert Decimal(d["total_collected"]) == Decimal("3") * Decimal("3000") + Decimal("2500")
    assert Decimal(d["total_net"]) == Decimal(d["total_paid"]) - Decimal(d["total_collected"])
    assert d["unresolved_orders"] == 0


def test_breakdown_marks_unresolved_rows_instead_of_guessing(client):
    c, db = client
    o = Order(
        channel_id=NAVER, order_number="x1", platform_product_id="cp1",
        platform_order_line_id="lx", quantity=1, selling_price=Decimal("1000"),
        order_date=datetime(2026, 7, 1), status="delivered",
        raw_data='{"order":{}...(truncated)',   # JSON 잘림 → 판별 불가
    )
    assert order_delivery.apply_delivery_fields(o, o.raw_data) is False
    db.add(o)
    db.commit()
    d = c.get("/api/orders/delivery-breakdown", params={"channel_id": NAVER}).json()
    assert d["unresolved_orders"] == 1
    row = d["rows"][0]
    assert row["delivery_attribute_type"] is None
    assert row["unresolved_paid"] == 1          # 지불 미판별 → paid/net 해석 주의 신호
    assert Decimal(row["paid_total"]) == Decimal("0")
