# test_rg_fee_axis_http.py — RG 정산공제 자백 칸(CONTRACT_rg_sales_date_axis §4 ⓒⓓⓔ)이
# HTTP 경계를 실제로 넘는지 못 박는다.
#
# 왜 서비스층 테스트로 안 되나: 이 repo는 서비스층 dict는 맞는데 `response_model`이 그 키를
# HTTP 응답에서 지워 화면 판정이 통째로 사라진 사고를 세 번 겪었다(교훈 #223·#321·#346계열).
# `profit_calculator._LEAF_PASSTHROUGH`(집계층 전달)와 `schemas.GroupedSummaryRow`(HTTP 스키마)가
# **둘 다** 그 칸의 이름을 갖고 있어야 화면까지 닿는다 — 한쪽만 있으면 여전히 안 나온다.
# `tests/test_rg_sales_date_fees.py`가 그 값을 **계산**하는 쪽을 잡고, 이 파일은 그 값이
# **경계를 넘는가**만 잡는다(모양은 `test_dashboard_net_scope_http.py`를 그대로 베꼈다).
from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.routers import dashboard as dash

RG_ID = 9
_Q = "date_from=2026-08-18&date_to=2026-08-18&rocket_basis=settlement"


class _Ch:
    id = 5  # 로켓1P(버튼) 채널 — 이 테스트의 관심사가 아니다


def _make_client(monkeypatch, rows: list[dict], cmap: dict):
    monkeypatch.setattr(dash, "_channel_rows", lambda *a, **k: [dict(r) for r in rows])
    monkeypatch.setattr(dash, "get_channel_company_map", lambda db: cmap)
    monkeypatch.setattr(dash, "rocket_1p_channel", lambda db: _Ch())
    monkeypatch.setattr(dash, "_resolve_ad_db", lambda: None)
    monkeypatch.setattr(dash, "_sync_orders_recent", lambda db: None)
    monkeypatch.setattr(dash, "_sync_ad_costs_for_period", lambda db, a, b: None)

    app = FastAPI()
    app.include_router(dash.router)
    app.dependency_overrides[get_db] = lambda: None
    return TestClient(app)


def _leaf_rows(client) -> dict:
    r = client.get(f"/api/dashboard/channel-breakdown?{_Q}")
    assert r.status_code == 200, r.text
    return {(x["kind"], x["label"]): x for x in r.json()}


# ── 시나리오 ①: 판매일 축을 낼 수 있는 경우(fee_trustworthy=True, D-CPP-47) ──
_CMAP = {RG_ID: ("주식회사 오하이테크", "주식회사 오하이테크 · 쿠팡 로켓그로스", True)}

_ROW_SALES_DATE = {
    "channel_id": RG_ID, "channel_name": "쿠팡 로켓그로스",
    "revenue": "5000000.00", "product_revenue": "5000000.00", "shipping_revenue": "0",
    "ad_spend": "100000.00", "net_profit": "1234567.00",
    "net_scope": "full", "net_basis_revenue": "5000000.00",
    "order_count": 50, "unmapped_revenue": "0",
    "revenue_basis": "console_net",
    "commission_axis": "sales_date",
    "commission_basis": "settled_rate",
    "commission_rate": "10.4500",
    "commission_rate_cycles": "2026-08-03~2026-08-16",
    "commission_logistics": "123456",
    "commission_sale_fee": "234567",
    "commission_period": "3456",
    "fee_coverage": "0.9800",
    "fee_unmapped_revenue": "12000",
    "settlement_reconcile_cycle": "2026-08-03~2026-08-16",
    "settlement_reconcile_computed": "500000",
    "settlement_reconcile_actual": "501000",
    "settlement_reconcile_diff": "-1000",
    "settlement_reconcile_pct": "-0.20",
}

# ── 시나리오 ②: 완결 정산주기가 없어 요율을 못 재는 경우 → 원장 축(recognition_date)으로 물러섬 ──
_ROW_RECOGNITION_DATE = {
    "channel_id": RG_ID, "channel_name": "쿠팡 로켓그로스",
    "revenue": "5000000.00", "product_revenue": "5000000.00", "shipping_revenue": "0",
    "ad_spend": "100000.00", "net_profit": None,   # cost_trustworthy 조건도 같이 실패한 흔한 모양
    "order_count": 0, "unmapped_revenue": "0",
    "revenue_basis": "console_net",
    "commission_axis": "recognition_date",
    "commission_basis": "rate_unknown",
    # commission_rate·commission_logistics·commission_sale_fee·commission_period —
    # 원장 축에선 "분해가 저 셋으로 이뤄진다"는 거짓말을 안 하려고 **안 싣는다**(None).
    "fee_coverage": "0.4200",           # coverage 자체는 축과 무관하게 항상 낸다
    "fee_unmapped_revenue": "2900000",
}


@pytest.fixture
def client_sales_date(monkeypatch):
    return _make_client(monkeypatch, [_ROW_SALES_DATE], _CMAP)


@pytest.fixture
def client_recognition_date(monkeypatch):
    return _make_client(monkeypatch, [_ROW_RECOGNITION_DATE], _CMAP)


def test_sales_date_axis_and_its_breakdown_survive_the_http_boundary(client_sales_date):
    """①②: 판매일 축이면 axis·basis·rate·coverage·보존식 넷이 전부 body에 실제로 있다."""
    rg = _leaf_rows(client_sales_date)[("leaf", "주식회사 오하이테크 · 쿠팡 로켓그로스")]

    assert rg["commission_axis"] == "sales_date"
    assert rg["commission_basis"] == "settled_rate"
    assert rg.get("commission_rate") is not None
    assert rg.get("fee_coverage") is not None
    assert rg.get("settlement_reconcile_diff") is not None

    # 값 자체도 왜곡 없이 그대로 건너왔는지(경계를 넘으며 반올림·타입변환으로 틀어지지 않았는지)
    assert Decimal(rg["commission_rate"]) == Decimal("10.4500")
    assert Decimal(rg["fee_coverage"]) == Decimal("0.9800")
    assert Decimal(rg["settlement_reconcile_diff"]) == Decimal("-1000")
    assert rg["commission_rate_cycles"] == "2026-08-03~2026-08-16"


def test_recognition_date_fallback_hides_the_untrustworthy_breakdown(client_recognition_date):
    """③: 원장 축으로 물러선 행은 axis가 그렇다고 말하고, 분해 칸(commission_logistics)은 안 싣는다."""
    rg = _leaf_rows(client_recognition_date)[("leaf", "주식회사 오하이테크 · 쿠팡 로켓그로스")]

    assert rg["commission_axis"] == "recognition_date"
    assert rg.get("commission_logistics") is None
    assert rg.get("commission_sale_fee") is None
    assert rg.get("commission_period") is None
    # coverage는 축과 무관하게 여전히 실린다(자백 칸 자체가 사라지는 건 아니다)
    assert rg.get("fee_coverage") is not None
