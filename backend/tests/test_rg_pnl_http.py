# test_rg_pnl_http.py — RG 상품 단위 일별 손익 HTTP 경계 (D-CPP-54, CONTRACT_2p_own_screens §1-A-4).
#
# ★★이 파일이 지키는 것은 서비스층(`rg_daily_pnl.rg_option_pnl`, 이미 초록)이 아니라
#   **HTTP 응답 본문**이다. 이 저장소는 이 자리에서 세 번 다쳤다(교훈 #319·#321·#223):
#   `response_model`이 선언 안 된 키를 조용히 지운다 — 서비스층은 판정을 내는데 화면엔
#   그 칸이 영영 안 뜬다. 그래서 여기서는 서비스층 dict가 아니라 **TestClient가 받은
#   JSON 본문**을 단언한다.
#
# ★변이 6(«사용자에게 닿는 마지막 표면») 봉쇄용 키 전수 목록도 여기 있다 — 아래
#   `_ALL_TOP_KEYS`·`_ALL_OPTION_KEYS`·`_ALL_ACCOUNT_COMMON_KEYS`·`_ALL_CONSERVATION_KEYS`가
#   그 전수다. 하나라도 스키마·라우터에서 빠지면 이 테스트가 죽는다.
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    Channel,
    CoupangAdOptionDaily,
    CoupangProductItem,
    CoupangRgInventory,
    CoupangRgSettlementFee,
    CoupangVendorItemSalesDaily,
    CoupangVendorSummaryDaily,
    ProductChannelMapping,
    ProductMaster,
)

ACC = "COUPANG_WING1"
VENDOR = "A01564720"
DFROM, DTO = "2026-08-05", "2026-08-06"

# rg_option_pnl()의 반환 키 전수 — test_rg_daily_pnl.py의 docstring·반환문과 대조해 뽑았다.
_ALL_OPTION_KEYS = {
    "vendor_item_id", "name", "revenue", "units_sold", "order_count",
    "fee_logistics", "fee_sale_fee", "fee_total", "cost", "has_cost",
    "ad_spend", "net_profit",
}
_ALL_ACCOUNT_COMMON_KEYS = {
    "period_fees", "payable_vat", "revenue_axis_gap", "ad_unallocated",
    "ad_unallocated_options", "fee_axis_fallback_gap", "cost_unmapped_revenue",
    "fee_unmapped_revenue",
}
_ALL_CONSERVATION_KEYS = {
    "options_net_sum", "account_common_sum", "computed_total_net",
    "reference_net", "diff", "ok",
}
_ALL_TOP_KEYS = {
    "options", "account_common", "commission_axis", "rate", "rate_basis",
    "rate_cycles", "fee_coverage", "cost_coverage", "option_axis_days",
    "option_axis_complete", "cost_trustworthy", "fee_trustworthy",
    "reconciliation", "conservation",
    # HTTP 경계가 얹는 메타
    "account", "date_from", "date_to", "ad_spend_warning",
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("COUPANG_WING1_VENDOR_ID", VENDOR)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    tc = TestClient(app)
    tc.testing_session = TestingSession
    yield tc
    app.dependency_overrides.clear()


# ── 시딩 헬퍼 — test_rg_daily_pnl.py와 같은 스키마(서비스층 테스트를 그대로 베꼈다) ──
def _seed_channels(db):
    db.add(Channel(id=1, name=ACC, code=ACC, platform="coupang",
                   company="개인회사 오픽스", sell_type="3P", channel_type="marketplace"))
    db.add(Channel(id=3, name="COUPANG_RG1", code="COUPANG_RG1", platform="coupang",
                   company="개인회사 오픽스", sell_type="RG", channel_type="marketplace"))


def _seed_summary(db, day, gmv, units):
    db.add(CoupangVendorSummaryDaily(summary_date=day, account_key=ACC,
                                     registration_type="RFM", gmv=gmv, units_sold=units))


def _seed_option(db, day, vid, gmv, units, orders=1):
    db.add(CoupangVendorItemSalesDaily(sale_date=day, account_key=ACC, vendor_item_id=vid,
                                       registration_type="RFM", gmv=gmv, units_sold=units,
                                       total_orders=orders))


def _seed_catalog(db, vid):
    db.add(CoupangProductItem(vendor_item_id=vid, account_key=ACC, vendor_id=VENDOR,
                              seller_product_id="SP1", sale_price=Decimal("10000")))


def _seed_rg_inventory(db, vid):
    db.add(CoupangRgInventory(vendor_item_id=vid, account_key=ACC, vendor_id=VENDOR))


def _seed_ad(db, vid, spend, day, sell_type="3P"):
    db.add(CoupangAdOptionDaily(report_date=day, vendor_id=VENDOR, sell_type=sell_type,
                                ad_option_id=vid, conv_option_id=vid,
                                ad_spend=Decimal(str(spend))))


def _seed_fee(db, fee_type, amount, dfrom, dto):
    db.add(CoupangRgSettlementFee(account_key=ACC, recognition_date_from=dfrom,
                                  recognition_date_to=dto, fee_type=fee_type,
                                  vendor_item_id="", amount=Decimal(str(amount))))


def _seed_fee_option(db, fee_type, vid, amount, qty, dfrom, dto):
    db.add(CoupangRgSettlementFee(account_key=ACC, recognition_date_from=dfrom,
                                  recognition_date_to=dto, fee_type=fee_type,
                                  vendor_item_id=vid, amount=Decimal(str(amount)),
                                  billed_quantity=qty))


def _seed_cost(db, vid, cost, pid=1):
    db.add(ProductMaster(id=pid, internal_sku=f"SKU{pid}", product_name=f"P{pid}",
                         cost_price=Decimal(str(cost))))
    db.add(ProductChannelMapping(channel_id=3, product_id=pid,
                                 channel_product_id=vid, is_active=True))


def _seed_full_gate_pass(db):
    """test_rg_daily_pnl._seed_full_gate_pass와 동일 — 두 게이트(원가·수수료) 통과."""
    _seed_channels(db)
    for d in (date(2026, 8, 5), date(2026, 8, 6)):
        _seed_summary(db, d, 50_000, 5)
        _seed_option(db, d, "RG1", 50_000, 5)
    _seed_catalog(db, "RG1")
    _seed_rg_inventory(db, "RG1")
    _seed_cost(db, "RG1", 2_000)
    _seed_summary(db, date(2026, 7, 30), 100_000, 10)
    _seed_fee(db, "sale_fee", 10_000, date(2026, 7, 27), date(2026, 8, 3))
    _seed_fee_option(db, "delivery", "RG1", 2_000, 10, date(2026, 7, 27), date(2026, 8, 3))
    _seed_ad(db, "RG1", 1_000, date(2026, 8, 5))


# ════════════════════════════════════════════════
# 1. 기본 왕복 — 모든 키가 실제 JSON 본문에 있다
# ════════════════════════════════════════════════
def test_http_roundtrip_returns_every_confession_key(client):
    with client.testing_session() as db:
        _seed_full_gate_pass(db)
        db.commit()

    r = client.get("/api/coupang/rg/option-pnl",
                    params={"account": ACC, "date_from": DFROM, "date_to": DTO})
    assert r.status_code == 200, r.text
    body = r.json()

    assert set(body.keys()) == _ALL_TOP_KEYS, (
        f"응답 최상위 키가 기대와 다르다 — 빠짐: {_ALL_TOP_KEYS - set(body.keys())}, "
        f"초과: {set(body.keys()) - _ALL_TOP_KEYS}"
    )
    assert set(body["account_common"].keys()) == _ALL_ACCOUNT_COMMON_KEYS
    assert set(body["conservation"].keys()) == _ALL_CONSERVATION_KEYS
    assert len(body["options"]) == 1
    assert set(body["options"][0].keys()) == _ALL_OPTION_KEYS

    assert body["account"] == ACC
    assert body["date_from"] == DFROM
    assert body["date_to"] == DTO
    assert body["ad_spend_warning"] is None
    assert body["cost_trustworthy"] is True
    assert body["fee_trustworthy"] is True
    assert body["commission_axis"] == "sales_date"

    row = body["options"][0]
    assert row["vendor_item_id"] == "RG1"
    assert Decimal(row["revenue"]) == Decimal("100000")
    assert Decimal(row["cost"]) == Decimal("20000")
    assert Decimal(row["net_profit"]) == (
        Decimal("100000") - Decimal("20000") - Decimal("12200") - Decimal("1000")
    )

    cons = body["conservation"]
    assert cons["ok"] is True
    assert Decimal(cons["diff"]) == Decimal("0")


# ════════════════════════════════════════════════
# 2. 검증 — account 필수·유효값·날짜 순서
# ════════════════════════════════════════════════
def test_missing_account_is_422(client):
    r = client.get("/api/coupang/rg/option-pnl", params={"date_from": DFROM, "date_to": DTO})
    assert r.status_code == 422


def test_invalid_account_value_is_422(client):
    r = client.get("/api/coupang/rg/option-pnl",
                    params={"account": "COUPANG_ROCKET", "date_from": DFROM, "date_to": DTO})
    assert r.status_code == 422


def test_date_from_after_date_to_is_422(client):
    r = client.get("/api/coupang/rg/option-pnl",
                    params={"account": ACC, "date_from": DTO, "date_to": DFROM})
    assert r.status_code == 422


# ════════════════════════════════════════════════
# 3. 기본 창 — 생략 시 KST 어제 하루
# ════════════════════════════════════════════════
def test_default_window_is_single_day_yesterday_kst(client, monkeypatch):
    from datetime import timedelta

    from app.utils.kst import kst_today

    r = client.get("/api/coupang/rg/option-pnl", params={"account": ACC})
    assert r.status_code == 200, r.text
    body = r.json()
    expected = (kst_today() - timedelta(days=1)).isoformat()
    assert body["date_from"] == expected
    assert body["date_to"] == expected


# ════════════════════════════════════════════════
# 4. vendor_id 미상 — 500이 아니라 자백 필드
# ════════════════════════════════════════════════
def test_vendor_id_missing_yields_warning_not_500(client, monkeypatch):
    monkeypatch.delenv("COUPANG_WING1_VENDOR_ID", raising=False)
    with client.testing_session() as db:
        _seed_channels(db)
        for d in (date(2026, 8, 5), date(2026, 8, 6)):
            _seed_summary(db, d, 50_000, 5)
            _seed_option(db, d, "RG1", 50_000, 5)
        # ★catalog을 안 심는다 → `_vendor_id_for_account`도 폴백에 실패 → vendor_id 완전 미상
        _seed_cost(db, "RG1", 2_000)
        _seed_summary(db, date(2026, 7, 30), 100_000, 10)
        _seed_fee(db, "sale_fee", 10_000, date(2026, 7, 27), date(2026, 8, 3))
        _seed_fee_option(db, "delivery", "RG1", 2_000, 10, date(2026, 7, 27), date(2026, 8, 3))
        db.commit()

    r = client.get("/api/coupang/rg/option-pnl",
                    params={"account": ACC, "date_from": DFROM, "date_to": DTO})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ad_spend_warning"] is not None, "vendor_id 미상은 500이 아니라 자백 필드로 나와야 한다"
    assert "vendor_id" in body["ad_spend_warning"]
