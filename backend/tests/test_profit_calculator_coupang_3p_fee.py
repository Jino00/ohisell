# test_profit_calculator_coupang_3p_fee.py — 쿠팡 3P 판매수수료 실측화 머니 테스트
# D-CPP-30(2026-08-10, 옛 PLAN_coupang-3p-fee-actualization D-A/D-B/D-E를 대체):
#   3P(WING1/2) 라인 수수료 = 매출 × «그 옵션의 정산 실측 요율» × 1.1,
#   요율 미상이면 채널 정률(7.8%) × 1.1. RG/네이버/카페24 불변.
#   ★왜 실측 «금액» 우선을 그만뒀나: 정산 행에서 service_fee = sale_amount×service_fee_ratio가
#     라이브 661건 전수 성립한다 — 즉 실측 금액과 요율 계산은 같은 값이고, 다른 건 커버리지뿐이다
#     (정산은 D+9~10 지연되므로 실측 금액만 쓰면 최근 주문이 계정 단일 정률로 떨어진다).
# 라이브 호출 없음. 순수함수 + 인메모리 SQLite.
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Channel, CoupangRevenueFee, Order
from app.services.coupang.option_fee_rate import fee_reconciliation, option_fee_rates
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


def test_3p_uses_that_options_settled_rate():
    # D-CPP-30: 룩업은 «금액»이 아니라 «요율»이다. 키=(account_key, vendor_item_id).
    # 6.4% 옵션 → 10,000 × 0.064 × 1.1 = 704
    rates = {("COUPANG_WING1", "V1"): D("0.064")}
    assert _line_commission(_ch("COUPANG_WING1"), _ord("ORD1"), D("10000"), rates) == D("704.000")


def test_3p_high_rate_option_is_not_flattened_to_78():
    # WING1엔 10.5%·10.8% 옵션이 실재한다(라이브 2026-08-10, 1,320,600원어치).
    # 7.8%로 뭉개면 과소계상 — 그걸 막는 테스트.
    rates = {("COUPANG_WING1", "V1"): D("0.108")}
    got = _line_commission(_ch("COUPANG_WING1", "7.8"), _ord("ORD1"), D("10000"), rates)
    assert got == D("1188.000")
    assert got > D("858.00"), "7.8% 폴백보다 커야 한다(과소계상 방지)"


def test_3p_cross_account_key_not_matched():
    # 같은 vid라도 다른 계정 키면 매칭 안 됨 → 채널 정률 (codex P1 #2 가드 유지)
    rates = {("COUPANG_WING2", "V1"): D("0.064")}  # WING2 행
    got = _line_commission(_ch("COUPANG_WING1", "7.8"), _ord("ORD1"), D("10000"), rates)
    assert got == D("858.00")  # WING1 조회 → WING2 행 미매칭 → 채널 정률


def test_3p_unknown_option_falls_back_to_channel_rate():
    # 요율 미상(첫 정산 전 신제품 등) → 채널 정률 7.8% × 1.1 = 8.58%
    rates = {("COUPANG_WING1", "OTHER_VID"): D("0.064")}
    got = _line_commission(_ch("COUPANG_WING1", "7.8"), _ord("ORD1"), D("10000"), rates)
    assert got == D("858.00")


def test_3p_fallback_when_lookup_none():
    got = _line_commission(_ch("COUPANG_WING1", "7.8"), _ord("ORD1"), D("10000"), None)
    assert got == D("858.00")


def test_3p_empty_option_id_falls_back():
    o = _ord("ORD1", vid="")
    got = _line_commission(_ch("COUPANG_WING1", "7.8"), o, D("10000"), {})
    assert got == D("858.00")


def test_rg_unchanged_bare_rate():
    # RG는 3P 아님 → 정률 그대로(×11/10 없음). 요율 룩업 무시.
    rates = {("COUPANG_RG1", "V1"): D("0.064")}
    got = _line_commission(_ch("COUPANG_RG1", "10.8"), _ord("ORD1"), D("10000"), rates)
    assert got == D("10000") * D("10.8") / D("100")  # 1080, no VAT gross-up


def test_naver_uses_commission_amount_unaffected():
    rates = {("COUPANG_WING1", "V1"): D("0.064")}
    o = _ord("ORD1", commission_amount=D("550"))
    assert _line_commission(_ch("NAVER", "5.5"), o, D("10000"), rates) == D("550")


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
         rec=date(2026, 6, 5), ratio="7.8"):
    db.add(CoupangRevenueFee(
        order_id=order_id, vendor_item_id=vid, account_key=account_key, vendor_id=vendor_id,
        sale_type=sale_type, service_fee=D(str(service_fee)), service_fee_vat=D(str(vat)),
        recognition_date=rec,
        service_fee_ratio=D(str(ratio)) if ratio is not None else None,
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


def test_end_to_end_daily_trend_option_rate_vs_channel_rate(db):
    """D-CPP-30: 옵션 요율을 아는 것과 모르는 것이 각각 제 값으로 계산된다.

    ★핵심은 «주문 A의 정산이 이 창에 있느냐»가 아니라 «그 옵션의 요율을 아느냐»다.
    옛 계약에선 정산 행이 없는 주문이 계정 단일 정률로 떨어졌고, 그래서 6.4%·10.8% 옵션이
    최근 열흘 동안은 7.8%로 계산됐다.
    """
    wing = _seed_channel(db, "COUPANG_WING1", "7.8")
    _seed_order(db, wing, "ORDA", "V1", "10000", day=5)
    _seed_order(db, wing, "ORDB", "V2", "10000", day=6)
    # V1은 과거(6/5 창 밖이어도 무방) 정산에서 6.4%로 확인됨 — 금액이 아니라 요율만 쓰인다.
    _fee(db, "OLD_ORDER", "V1", "COUPANG_WING1", "SALE", 1000, 100, ratio="6.4")
    db.commit()

    trend = calculate_daily_trend(db, None, wing, date(2026, 6, 1), date(2026, 6, 30))
    by_date = {r["date"]: r for r in trend}
    # 6/5 V1 → 그 옵션 요율 6.4%: 10,000 × 0.064 × 1.1 = 704
    assert D(by_date["2026-06-05"]["commission"]) == D("704.000")
    # 6/6 V2 → 요율 미상: 채널 정률 7.8% × 1.1 = 858
    assert D(by_date["2026-06-06"]["commission"]) == D("858.00")


def test_rate_lookup_ignores_rows_without_ratio(db):
    """service_fee_ratio가 없는 정산 행은 요율을 «모르는» 것이다 — 금액에서 역산하지 않는다.

    역산하면 부분정산·환불 섞인 행에서 엉뚱한 요율이 나오고, 그건 우리가 지어낸 값이다.
    """
    _fee(db, "ORD1", "V1", "COUPANG_WING1", "SALE", 1000, 100, ratio=None)
    db.commit()
    assert option_fee_rates(db) == {}


def test_rate_lookup_takes_latest_recognition_date(db):
    """요율이 바뀌면 최신 정산 행을 따른다(우리가 갱신할 것이 없다)."""
    _fee(db, "ORD1", "V1", "COUPANG_WING1", "SALE", 780, 78,
         rec=date(2026, 5, 1), ratio="7.8")
    _fee(db, "ORD2", "V1", "COUPANG_WING1", "SALE", 640, 64,
         rec=date(2026, 7, 1), ratio="6.4")
    db.commit()
    assert option_fee_rates(db) == {("COUPANG_WING1", "V1"): D("0.064")}


def test_fee_reconciliation_flags_divergence(db):
    """전제 검증 장치: 계산값이 실측과 어긋나면 diff로 드러난다.

    쿠팡이 쿠폰·프로모션 정산을 다르게 하기 시작하면 우리 계산은 조용히 틀린다 —
    net_profit에서 실측을 뺐으니 이 대조가 유일한 표면이다.
    """
    wing = _seed_channel(db, "COUPANG_WING1", "7.8")
    _seed_order(db, wing, "ORDA", "V1", "10000", day=5)
    # 실측이 요율 계산(858)과 다른 값(500)으로 왔다 = 전제가 깨진 상황
    _fee(db, "ORDA", "V1", "COUPANG_WING1", "SALE", 455, 45, ratio="7.8")
    db.commit()
    r = fee_reconciliation(db, datetime(2026, 6, 1), datetime(2026, 6, 30), ["COUPANG_WING1"])
    assert r["checked_lines"] == 1
    assert r["computed"] == D("858.000")
    assert r["actual"] == D("500")
    assert r["diff"] == D("358.000"), "어긋남이 금액으로 드러나야 한다"


def test_fee_reconciliation_agrees_when_premise_holds(db):
    """정상: 실측 = 매출 × 요율 × 1.1 이면 diff 0 (라이브 661건이 이 상태였다)."""
    wing = _seed_channel(db, "COUPANG_WING1", "7.8")
    _seed_order(db, wing, "ORDA", "V1", "10000", day=5)
    _fee(db, "ORDA", "V1", "COUPANG_WING1", "SALE", 780, 78, ratio="7.8")  # 858
    db.commit()
    r = fee_reconciliation(db, datetime(2026, 6, 1), datetime(2026, 6, 30), ["COUPANG_WING1"])
    assert r["checked_lines"] == 1
    assert r["diff"] == D("0.000")
    assert r["max_line_diff"] == D("0.000")


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
