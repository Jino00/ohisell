# test_profit_calculator_coupang_3p_fee.py — 쿠팡 3P 판매수수료 실측화 머니 테스트
# PLAN_coupang-3p-fee-actualization D-A/D-B/D-E:
#   3P(WING1/2) 라인 수수료 = 실측 total_fee(service_fee+service_fee_vat) 우선,
#   없으면(미정산) 7.8%×수수료VAT(×11/10) 폴백. RG/네이버/카페24 불변.
# 라이브 호출 없음. 순수함수 + 인메모리 SQLite.
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Channel, CoupangRevenueFee, Order
from app.services.coupang.revenue_fee_source import actual_fee_by_order_option
from app.services.profit_calculator import _line_commission, calculate_daily_trend

D = Decimal


# ─── 순수함수: _line_commission ─────────────────────────────────

def _ch(code: str, rate: str = "7.8") -> Channel:
    return Channel(code=code, commission_rate=D(rate))


def _ord(order_number: str, vid: str = "V1", commission_amount=None) -> Order:
    o = Order(order_number=order_number, platform_product_id=vid)
    o.commission_amount = commission_amount
    return o


def test_3p_uses_actual_fee_when_present():
    # 실측 total_fee(예: 1234)가 있으면 정률 무시하고 그대로 차감. 키=(account_key,order,vid).
    lookup = {("COUPANG_WING1", "ORD1", "V1"): D("1234")}
    assert _line_commission(_ch("COUPANG_WING1"), _ord("ORD1"), D("10000"), lookup) == D("1234")


def test_3p_refund_net_negative_actual_fee():
    # SALE/REFUND 순합이 음수면 음수 그대로(사실, D-3)
    lookup = {("COUPANG_WING2", "ORD9", "V1"): D("-300")}
    assert _line_commission(_ch("COUPANG_WING2"), _ord("ORD9"), D("10000"), lookup) == D("-300")


def test_3p_cross_account_key_not_matched():
    # 같은 (order_id, vid)라도 다른 계정 키면 매칭 안 됨 → 폴백 (codex P1 #2 가드)
    lookup = {("COUPANG_WING2", "ORD1", "V1"): D("1234")}  # WING2 행
    got = _line_commission(_ch("COUPANG_WING1", "7.8"), _ord("ORD1"), D("10000"), lookup)
    assert got == D("858.00")  # WING1 조회 → WING2 행 미매칭 → 폴백


def test_3p_fallback_when_key_missing():
    # 미정산(룩업에 키 없음) → 7.8% × 11/10 = 8.58% 폴백
    lookup = {("COUPANG_WING1", "OTHER", "V1"): D("999")}
    got = _line_commission(_ch("COUPANG_WING1", "7.8"), _ord("ORD1"), D("10000"), lookup)
    assert got == D("10000") * D("7.8") / D("100") * D("11") / D("10")  # 858
    assert got == D("858.00")


def test_3p_fallback_when_lookup_none():
    got = _line_commission(_ch("COUPANG_WING1", "7.8"), _ord("ORD1"), D("10000"), None)
    assert got == D("858.00")


def test_3p_empty_option_id_falls_back():
    lookup = {("COUPANG_WING1", "ORD1", ""): D("500")}
    o = _ord("ORD1", vid="")
    got = _line_commission(_ch("COUPANG_WING1", "7.8"), o, D("10000"), lookup)
    # 빈 옵션ID 키가 존재하면 매칭됨 — 사실 그대로(실측 우선). 가드는 SA가 빈 행 미적재로 처리.
    assert got == D("500")


def test_rg_unchanged_bare_rate():
    # RG는 3P 아님 → 정률 그대로(×11/10 없음). 실측 룩업 무시.
    lookup = {("COUPANG_RG1", "ORD1", "V1"): D("1234")}
    got = _line_commission(_ch("COUPANG_RG1", "10.8"), _ord("ORD1"), D("10000"), lookup)
    assert got == D("10000") * D("10.8") / D("100")  # 1080, no VAT gross-up


def test_naver_uses_commission_amount_unaffected():
    lookup = {("COUPANG_WING1", "ORD1", "V1"): D("1234")}
    o = _ord("ORD1", commission_amount=D("550"))
    assert _line_commission(_ch("NAVER", "5.5"), o, D("10000"), lookup) == D("550")


def test_cafe24_uses_commission_amount():
    o = _ord("ORD1", commission_amount=D("330"))
    assert _line_commission(_ch("CAFE24", "0"), o, D("10000"), None) == D("330")


# ─── 인메모리 DB: SA + end-to-end ────────────────────────────────

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _fee(db, order_id, vid, account_key, sale_type, service_fee, vat, vendor_id="A01",
         rec=date(2026, 6, 5)):
    db.add(CoupangRevenueFee(
        order_id=order_id, vendor_item_id=vid, account_key=account_key, vendor_id=vendor_id,
        sale_type=sale_type, service_fee=D(str(service_fee)), service_fee_vat=D(str(vat)),
        recognition_date=rec,
    ))


def test_sa_sums_sale_and_refund_to_net_total_fee(db):
    _fee(db, "ORD1", "V1", "COUPANG_WING1", "SALE", 780, 78)     # +858
    _fee(db, "ORD1", "V1", "COUPANG_WING1", "REFUND", -390, -39)  # -429
    db.commit()
    out = actual_fee_by_order_option(db, {"ORD1"})
    assert out[("COUPANG_WING1", "ORD1", "V1")] == D("429")  # 858 - 429 = 429 net


def test_sa_separates_same_order_option_across_accounts(db):
    # 같은 (order_id, vid)가 두 계정에 공존(다른 recognition_date — UNIQUE 그레인 회피)해도
    # account_key로 분리(codex P1 #2). 실데이터에선 order_id 계정유일이라 희박하나 명시 가드.
    _fee(db, "ORD1", "V1", "COUPANG_WING1", "SALE", 800, 80, rec=date(2026, 6, 5))
    _fee(db, "ORD1", "V1", "COUPANG_WING2", "SALE", 500, 50, rec=date(2026, 6, 6))
    db.commit()
    out = actual_fee_by_order_option(db, {"ORD1"})
    assert out[("COUPANG_WING1", "ORD1", "V1")] == D("880")
    assert out[("COUPANG_WING2", "ORD1", "V1")] == D("550")


def test_sa_account_filter(db):
    _fee(db, "ORD1", "V1", "COUPANG_WING1", "SALE", 800, 80)
    _fee(db, "ORD2", "V2", "COUPANG_WING2", "SALE", 600, 60)
    db.commit()
    only1 = actual_fee_by_order_option(db, {"ORD1", "ORD2"}, account_keys=["COUPANG_WING1"])
    assert ("COUPANG_WING1", "ORD1", "V1") in only1
    assert ("COUPANG_WING2", "ORD2", "V2") not in only1


def test_sa_empty_ids_returns_empty(db):
    assert actual_fee_by_order_option(db, set()) == {}


def _seed_channel(db, code, rate, ctype="marketplace"):
    ch = Channel(name=code, code=code, platform="coupang", channel_type=ctype,
                 commission_rate=D(str(rate)))
    db.add(ch)
    db.flush()
    return ch.id


def _seed_order(db, channel_id, order_number, vid, price, qty=1, day=5):
    db.add(Order(
        channel_id=channel_id, order_number=order_number, platform_product_id=vid,
        selling_price=D(str(price)), quantity=qty,
        order_date=datetime(2026, 6, day, 10, 0), status="delivered",
    ))


def test_end_to_end_daily_trend_actual_vs_fallback(db):
    wing = _seed_channel(db, "COUPANG_WING1", "7.8")
    # 주문A: 실측 fee 있음(858). 주문B: 실측 없음 → 폴백 858.
    _seed_order(db, wing, "ORDA", "V1", "10000", day=5)
    _seed_order(db, wing, "ORDB", "V2", "10000", day=6)
    _fee(db, "ORDA", "V1", "COUPANG_WING1", "SALE", 1000, 100)  # 실측 1100 (정률과 다른 값)
    db.commit()

    trend = calculate_daily_trend(db, None, wing, date(2026, 6, 1), date(2026, 6, 30))
    by_date = {r["date"]: r for r in trend}
    # 6/5 주문A → 실측 1100
    assert D(by_date["2026-06-05"]["commission"]) == D("1100")
    # 6/6 주문B → 폴백 858 (10000 × 7.8% × 1.1)
    assert D(by_date["2026-06-06"]["commission"]) == D("858.00")


def test_daily_trend_net_deducts_payable_vat(db):
    """순이익에서 **납부세액**(매출VAT − 매입세액공제)을 뺀다 (Jino 2026-08-04).

    ★종전 계약을 뒤집은 것이다. 2026-06-15에는 "판매자 VAT는 매입세액공제로 상당부분
      상쇄되는 통과분"이라며 아예 안 뺐다. 맞는 말이지만 '상당부분'이 전부는 아니라서
      실제 납부액만큼 이익이 부풀었다(7월 네이버 실측 604,465원).
      반대로 매출VAT 전액을 빼면 매입세액공제를 무시해 과다차감이라, 두 안 중 납부세액을 골랐다.
    """
    wing = _seed_channel(db, "COUPANG_WING1", "7.8")
    _seed_order(db, wing, "ORDV", "V1", "10000", day=5)  # 실측 없음 → 폴백 858, 배송 1건(1,900)
    db.commit()
    trend = calculate_daily_trend(db, None, wing, date(2026, 6, 1), date(2026, 6, 30))
    r = {x["date"]: x for x in trend}["2026-06-05"]

    # 매출 10,000 · 원가 0 · 수수료 858 · 배송 1,900 · 광고 0
    #   매출VAT = 10,000 ×10/110 = 909.0909…
    #   매입VAT = (858+1,900)×10/110 = 250.7272…
    #   납부세액 = 658.3636…  → net = 7,242 − 658.3636… = 6,583.6363…
    gross = D("10000") - D("858") - D("1900")
    assert gross == D("7242")  # VAT 차감 전(=종전 계약 값)
    net = D(r["net_profit"])
    assert net < gross, "납부세액이 차감돼야 한다"
    # 모든 축이 VAT 포함이면 결과는 공급가 기준 이익과 같다 — 그 항등식으로 검산한다
    assert abs(net - gross / D("1.1")) < D("0.01")
    assert abs(net - D("6583.6363")) < D("0.01")


def test_end_to_end_naver_uses_commission_amount(db):
    naver = _seed_channel(db, "NAVER", "5.5")
    db.add(Order(
        channel_id=naver, order_number="N1", platform_product_id="NV1",
        selling_price=D("10000"), quantity=1, order_date=datetime(2026, 6, 5, 10, 0),
        status="delivered", commission_amount=D("550"),
    ))
    db.commit()
    trend = calculate_daily_trend(db, None, naver, date(2026, 6, 1), date(2026, 6, 30))
    assert D(trend[0]["commission"]) == D("550")  # 실측 commission_amount, 3P 로직 무관
