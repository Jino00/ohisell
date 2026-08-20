# test_dashboard_net_scope_http.py — D-22 적대 리뷰 1R P2 (2026-08-19)
#
# 왜 서비스층 테스트로 안 되나: 이 repo는 **서비스층 dict는 맞는데 `response_model`이 그 키를
# HTTP 응답에서 지워** 화면 판정이 통째로 사라진 사고를 겪었다(교훈 #223·#321). 그때도 서비스층
# 테스트 9건은 전부 초록이었다. 그래서 「경계를 넘는가」는 경계에서만 잴 수 있다.
#
# 적대 리뷰 변이 주입에서 살아남은 것 둘을 여기서 죽인다:
#   #3 `_kpi_totals`의 로켓 제외 조건을 뒤집어도(주문건수 카드가 "로켓 제외"→"로켓만") 전부 초록
#   #6 `schemas.GroupedSummaryRow.net_scope`를 통째로 지워도 전부 초록
from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.routers import dashboard as dash

ROCKET_ID = 5

# 라이브 2026-08-18 재현값
_ROWS = [
    {"channel_id": 6, "channel_name": "네이버 스마트스토어", "revenue": "1755400.00",
     "product_revenue": "1539400.00", "shipping_revenue": "216000.00",
     "ad_spend": "606874.54", "net_profit": "382809.51", "order_count": 102,
     "unmapped_revenue": "0"},
    {"channel_id": 7, "channel_name": "자사몰(cafe24)", "revenue": "277300.00",
     "product_revenue": "277300.00", "shipping_revenue": "0",
     "ad_spend": "31174.0", "net_profit": "186825.36", "order_count": 7,
     "unmapped_revenue": "180000"},
    {"channel_id": ROCKET_ID, "channel_name": "쿠팡 로켓배송", "revenue": "0",
     "product_revenue": "0", "shipping_revenue": "0", "ad_spend": "597888.00",
     "net_profit": "-597888.00", "net_scope": "ad_only", "net_basis_revenue": "0",
     "order_count": 0, "unmapped_revenue": "0", "revenue_basis": "settlement"},
]
_CMAP = {
    6: ("주식회사 오하이", "주식회사 오하이 · 네이버 스마트스토어", True),
    7: ("주식회사 오하이테크", "주식회사 오하이테크 · 자사몰(cafe24)", True),
    ROCKET_ID: ("주식회사 오하이테크", "주식회사 오하이테크 · 쿠팡 로켓배송", True),
}


class _Ch:
    id = ROCKET_ID


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(dash, "_channel_rows", lambda *a, **k: [dict(r) for r in _ROWS])
    monkeypatch.setattr(dash, "get_channel_company_map", lambda db: _CMAP)
    monkeypatch.setattr(dash, "rocket_1p_channel", lambda db: _Ch())
    monkeypatch.setattr(dash, "_resolve_ad_db", lambda: None)
    monkeypatch.setattr(dash, "_sync_orders_recent", lambda db: None)
    monkeypatch.setattr(dash, "_sync_ad_costs_for_period", lambda db, a, b: None)

    app = FastAPI()
    app.include_router(dash.router)
    app.dependency_overrides[get_db] = lambda: None
    return TestClient(app)


_Q = "date_from=2026-08-18&date_to=2026-08-18&rocket_basis=settlement"


def _rows(client):
    r = client.get(f"/api/dashboard/channel-breakdown?{_Q}")
    assert r.status_code == 200, r.text
    return {(x["kind"], x["label"]): x for x in r.json()}


def test_new_fields_survive_the_http_boundary(client):
    """★response_model이 새 필드를 지우면 화면 경고가 통째로 사라진다(교훈 #321)."""
    body = _rows(client)
    for key in ("net_scope", "net_floor_ad", "net_basis_revenue", "unmapped_revenue"):
        assert key in body[("total", "전체")], f"{key}가 HTTP 응답에서 사라졌다"

    rocket = body[("leaf", "주식회사 오하이테크 · 쿠팡 로켓배송")]
    assert rocket["net_scope"] == "ad_only"
    assert Decimal(rocket["net_profit"]) == Decimal("-597888.00")

    tech = body[("company", "주식회사 오하이테크")]
    assert tech["net_scope"] == "partial"
    assert Decimal(tech["net_floor_ad"]) == Decimal("597888.00")
    assert Decimal(tech["net_profit"]) < 0            # 흑자로 보이던 것이 적자다

    cafe = body[("leaf", "주식회사 오하이테크 · 자사몰(cafe24)")]
    assert Decimal(cafe["unmapped_revenue"]) == Decimal("180000")


def test_kpi_card_and_summary_table_cannot_disagree(client):
    """카드와 표가 **같은 값**을 말해야 한다 — 경로가 갈리면 언젠가 반드시 갈라진다."""
    kpi = client.get(f"/api/dashboard/kpi?{_Q}")
    assert kpi.status_code == 200, kpi.text
    k, total = kpi.json(), _rows(client)[("total", "전체")]

    assert Decimal(k["net_profit"]) == Decimal(total["net_profit"])
    assert Decimal(k["total_revenue"]) == Decimal(total["revenue"])
    assert Decimal(k["profit_rate"]) == Decimal(total["profit_rate"])
    assert k["net_scope"] == "partial" and Decimal(k["net_floor_ad"]) == Decimal("597888.00")


def test_order_count_excludes_rocket_because_that_column_is_not_orders(client):
    """로켓1P의 order_count 칸은 주문 건수가 아니라 **판매수량**이다(주문 개념이 없는 매입 구조).

    더하면 「주문 건수」 카드의 뜻이 축에 따라 바뀐다. 변이 #3(제외 조건 반전)을 여기서 잡는다.
    """
    k = client.get(f"/api/dashboard/kpi?{_Q}").json()
    assert k["order_count"] == 109      # 102 + 7, 로켓 제외
