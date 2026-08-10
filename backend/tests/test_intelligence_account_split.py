# test_intelligence_account_split.py — S1 계정 분리 뷰 (트랙 reconciliation D-4)
# 인메모리 SQLite로 compute_command_center(account=...) 통합 검증.
# 핵심 계약:
#   ① 오픽스(WING1)/오하이(WING2) 따로 — orders/ads/fees/returns/RG정산 전 소스
#   ② account=None == 두 계정 합 (등가성, 회귀·누락 가드) — 모든 머니 필드
#   ③ account=None 응답은 기존 형태 보존(period.account 키 없음, Codex S1 P1#1)
#   ④ orders는 법인(company) 단위 채널 매핑 — ROCKET(1P) 주문도 같은 법인 계정에 귀속(Codex P1#2)
#   ⑤ env 미설정 시 vendor_id를 상품 스냅샷에서 폴백(Codex P2)
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    Channel, CoupangAdOptionDaily, CoupangProductItem, CoupangReturnItem,
    CoupangRevenueFee, CoupangRgSettlementFee, Order,
)
from app.services.coupang.intelligence import compute_command_center

_Z = Decimal(0)
WIN = (date(2026, 6, 1), date(2026, 6, 30))
OD = datetime(2026, 6, 5, 12, 0, 0)
VENDOR = {"COUPANG_WING1": "A01564720", "COUPANG_WING2": "A01029796"}


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _ch(db, cid, code, company):
    db.add(Channel(id=cid, name=f"쿠팡_{code}", code=code, platform="coupang", company=company))


def _product(db, vid, account_key):
    db.add(CoupangProductItem(
        vendor_item_id=vid, account_key=account_key, vendor_id=VENDOR[account_key],
        seller_product_id=f"SP_{vid}", item_name=f"옵션{vid}", sale_price=Decimal("0")))


def _order(db, channel_id, order_number, vid, price, qty=1):
    db.add(Order(channel_id=channel_id, order_number=order_number, platform_product_id=vid,
                 quantity=qty, selling_price=Decimal(str(price)), order_date=OD, status="delivered"))


def _ad(db, account_key, vid, spend):
    db.add(CoupangAdOptionDaily(
        report_date=date(2026, 6, 5), vendor_id=VENDOR[account_key], sell_type="3P",
        ad_option_id=vid, conv_option_id=vid, ad_spend=Decimal(str(spend))))


def _fee(db, account_key, vid, fee, vat, order_id):
    db.add(CoupangRevenueFee(
        order_id=order_id, vendor_item_id=vid, recognition_date=date(2026, 6, 6),
        sale_type="SALE", account_key=account_key, vendor_id=VENDOR[account_key],
        service_fee=Decimal(str(fee)), service_fee_vat=Decimal(str(vat))))


def _return(db, account_key, vid, qty, receipt_id, order_id=None):
    """order_id를 안 주면 «고아»(원주문 없음)가 된다 — D-CPP-33 이후 차감되지 않는다.
    진짜 반품을 표현하려면 실제 주문번호를 넘겨야 한다."""
    db.add(CoupangReturnItem(
        receipt_id=receipt_id, vendor_item_id=vid, account_key=account_key,
        vendor_id=VENDOR[account_key], order_id=order_id or f"O_{receipt_id}",
        receipt_type="RETURN",
        cancel_count=qty, withdrawn=False, requested_at=OD))


def _rg(db, account_key, fee_type, amount):
    db.add(CoupangRgSettlementFee(
        account_key=account_key, recognition_date_from=date(2026, 6, 1),
        recognition_date_to=date(2026, 6, 7), fee_type=fee_type, vendor_item_id="",
        amount=Decimal(str(amount))))


@pytest.fixture
def seeded(db):
    """오픽스(WING1)·오하이(WING2) 전 소스 시드. ROCKET 채널(오하이 법인)은 company 매핑으로 WING2 귀속."""
    _ch(db, 1, "COUPANG_WING1", "개인회사 오픽스")
    _ch(db, 2, "COUPANG_WING2", "주식회사 오하이테크")
    _ch(db, 5, "COUPANG_ROCKET", "주식회사 오하이테크")  # 1P — company 매핑 검증용
    _product(db, "V1", "COUPANG_WING1")
    _product(db, "V2", "COUPANG_WING2")
    # 매출: 오픽스 10,000 / 오하이 20,000(Wing) + 7,000(ROCKET, 같은 법인) = 27,000
    _order(db, 1, "O1", "V1", 10000)
    _order(db, 2, "O2", "V2", 20000)
    _order(db, 5, "O3", "V2", 7000)
    # 광고: 오픽스 3,000 / 오하이 5,000
    _ad(db, "COUPANG_WING1", "V1", 3000)
    _ad(db, "COUPANG_WING2", "V2", 5000)
    # 매출내역 수수료: 오픽스 550(500+50) / 오하이 880(800+80)
    _fee(db, "COUPANG_WING1", "V1", 500, 50, "O1")
    _fee(db, "COUPANG_WING2", "V2", 800, 80, "O2")
    # 반품: 오픽스 V1 1개(단가 10,000 → 차감 10,000) / 오하이 V2 1개(단가 (20000+7000)/2=13500)
    # ★원주문을 명시한다 — 안 하면 고아가 되어 차감 대상이 아니다(D-CPP-33).
    _return(db, "COUPANG_WING1", "V1", 1, "R1", order_id="O1")
    _return(db, "COUPANG_WING2", "V2", 1, "R2", order_id="O2")
    # RG 정산: 오픽스 400 / 오하이 600 전액차감
    _rg(db, "COUPANG_WING1", "sale_fee", 400)
    _rg(db, "COUPANG_WING2", "sale_fee", 600)
    db.commit()
    return db


def _sum(db, acc):
    return compute_command_center(db, WIN[0], WIN[1], acc)


def test_opix_only(seeded):
    s = _sum(seeded, "COUPANG_WING1")["account"]["summary"]
    assert s["revenue"] == Decimal("10000")
    assert s["ad_spend"] == Decimal("3000")
    # D-CPP-32: 수수료 = 과세표준 × 요율 × 1.1. V1은 3P매출 10,000 − 반품차감 10,000 = 0 →
    # 수수료 0. 전량 반품이면 쿠팡이 수수료도 환급하므로 0이 사실이다(정산에 REFUND 음수 행).
    assert s["fee_base_total"] == _Z
    assert s["total_fee"] == _Z
    assert s["rg_settlement_total"] == Decimal("400")


def test_ohi_only_includes_rocket_via_company(seeded):
    """ROCKET(1P) 주문이 같은 법인(오하이) 계정 WING2에 귀속 — company 매핑(Codex P1#2)."""
    s = _sum(seeded, "COUPANG_WING2")["account"]["summary"]
    assert s["revenue"] == Decimal("27000")  # 20,000 Wing + 7,000 ROCKET
    assert s["ad_spend"] == Decimal("5000")
    # ★1P(ROCKET) 7,000은 과세표준에서 빠진다 — 쿠팡이 사입해 파는 것이라 우리 판매수수료가 없다.
    #   과세표준 = 3P 20,000 − 반품차감 13,500 = 6,500 (매출 27,000이 아니다)
    assert s["fee_base_total"] == Decimal("6500")
    assert s["total_fee"] == Decimal("6500") * Decimal("0.078") * Decimal("1.1")
    assert s["total_fee"] == Decimal("557.700000")
    assert s["rg_settlement_total"] == Decimal("600")


def test_fee_uses_that_options_settled_rate_not_flat_78(seeded):
    """★D-CPP-32의 요점: 옵션마다 요율이 다르면 그 옵션의 요율을 쓴다.

    라이브 실측(2026-08-10): WING2에 6.4% 옵션이 275,720원어치, WING1에 10.5%·10.8%가
    1,320,600원어치 있다. 단일 7.8% 폴백은 전자를 과대, 후자를 과소 계상한다.
    """
    db = seeded
    # V2(오하이)의 과거 정산이 6.4%였다고 기록 — 금액이 아니라 «요율»만이 쓰인다.
    row = db.query(CoupangRevenueFee).filter_by(vendor_item_id="V2").first()
    row.service_fee_ratio = Decimal("6.4")
    db.commit()

    s = _sum(db, "COUPANG_WING2")["account"]["summary"]
    assert s["fee_base_total"] == Decimal("6500")
    assert s["total_fee"] == Decimal("6500") * Decimal("0.064") * Decimal("1.1")
    assert s["total_fee"] == Decimal("457.600000"), "7.8%(557.70)로 계상하면 100원 넘게 과대"
    assert s["fee_rate_known_options"] == 1
    assert s["fee_rate_default_options"] == 0


def test_unsettled_orders_still_charged_a_fee(db):
    """★회귀 방지: 정산 통보(D+9~10) 전이라도 수수료가 붙어야 한다.

    옛 동작은 _agg_fees(정산 인식일 축)에 행이 없으면 수수료 0원이었다 — 라이브 2026-08-10
    WING2 30일 창에서 49라인 중 25라인·450,700원이 그렇게 «수수료 공짜»로 계산돼 순이익이
    약 29,000원 과대했다.
    """
    _ch(db, 1, "COUPANG_WING1", "개인회사 오픽스")
    _product(db, "V9", "COUPANG_WING1")
    _order(db, 1, "O9", "V9", 100000)  # 정산 행 없음(미정산)
    db.commit()
    s = _sum(db, "COUPANG_WING1")["account"]["summary"]
    assert s["revenue"] == Decimal("100000")
    assert s["total_fee"] == Decimal("100000") * Decimal("0.078") * Decimal("1.1")
    assert s["total_fee"] == Decimal("8580.000000"), "미정산이라고 0원이면 안 된다"
    # 요율은 «모른다» — 화면이 실토할 수 있게 근거 등급이 실린다
    assert s["fee_rate_default_options"] == 1
    assert s["fee_default_revenue"] == Decimal("100000")


def test_none_shape_preserved(seeded):
    """account=None은 period에 account 키 없음(기존 응답 형태 보존, Codex P1#1)."""
    r = _sum(seeded, None)
    assert "account" not in r["period"]
    s = r["account"]["summary"]
    assert s["revenue"] == Decimal("37000")
    assert s["ad_spend"] == Decimal("8000")


def test_account_key_echoed_when_filtered(seeded):
    assert _sum(seeded, "COUPANG_WING1")["period"]["account"] == "COUPANG_WING1"


def test_equivalence_sum_equals_total(seeded):
    """등가성: 오픽스 + 오하이 == 전체(None) — 전 머니 필드. orphan·누수·이중계상 가드."""
    w1 = _sum(seeded, "COUPANG_WING1")["account"]["summary"]
    w2 = _sum(seeded, "COUPANG_WING2")["account"]["summary"]
    tot = _sum(seeded, None)["account"]["summary"]
    for k in ("revenue", "ad_spend", "total_fee", "return_deduction",
              "cost", "rg_settlement_total"):
        assert w1[k] + w2[k] == tot[k], f"{k}: {w1[k]}+{w2[k]} != {tot[k]}"
    # net_profit만 «원 단위» 허용오차를 둔다(D-CPP-33): 납부세액은 계정 «합계»에 대해
    # 한 번 계산하고 한 번 반올림하므로, 계정별로 쪼개 더하면 반올림이 두 번 일어난다.
    # 항목별 축(매출·수수료·원가·광고)은 여전히 «정확히» 일치해야 한다 — 위 루프가 지킨다.
    gap = abs((w1["net_profit"] + w2["net_profit"]) - tot["net_profit"])
    assert gap <= Decimal("0.01"), f"net_profit 등가성 이탈 {gap} (반올림 범위 초과)"
    assert abs((w1["payable_vat"] + w2["payable_vat"]) - tot["payable_vat"]) <= Decimal("0.01")


def test_unknown_account_empty(seeded):
    """알 수 없는 account → 빈집합 필터 → 빈 뷰(예외 아님, 전체 누수 없음)."""
    s = _sum(seeded, "COUPANG_UNKNOWN")["account"]["summary"]
    assert s["revenue"] == _Z
    assert s["ad_spend"] == _Z
    assert s["total_fee"] == _Z


def test_qty_gt1_no_double_count(db):
    """S2: selling_price=orderPrice(라인총액)인 qty>1 주문은 ×수량 곱하면 안 됨(2중계상).

    raw 실증: salesPrice 16,900 × 수량 2 = orderPrice 33,800. 매출 = 33,800 (67,600 아님)."""
    _ch(db, 1, "COUPANG_WING1", "개인회사 오픽스")
    _product(db, "V1", "COUPANG_WING1")
    _order(db, 1, "O1", "V1", 33800, qty=2)   # orderPrice(라인총액) 33,800, 수량 2
    _order(db, 1, "O2", "V1", 16900, qty=1)   # 단건 16,900
    db.commit()
    s = _sum(db, "COUPANG_WING1")["account"]["summary"]
    assert s["revenue"] == Decimal("50700")   # 33,800 + 16,900 (NOT 33,800*2+16,900=84,500)
    p = _sum(db, "COUPANG_WING1")["product"]["summary"]
    assert p["order_qty"] == 3                  # 수량은 그대로 2+1


def test_vendor_id_fallback_no_env(seeded, monkeypatch):
    """env(COUPANG_WING1_VENDOR_ID) 미설정 시 상품 스냅샷 vendor_id로 폴백 → 광고 여전히 격리(Codex P2)."""
    monkeypatch.delenv("COUPANG_WING1_VENDOR_ID", raising=False)
    s = _sum(seeded, "COUPANG_WING1")["account"]["summary"]
    assert s["ad_spend"] == Decimal("3000")  # 폴백으로 A01564720 해석 → 오픽스 광고만


def test_orphan_return_is_not_deducted(db):
    """★D-CPP-33: 원주문이 orders에 없는 반품은 «차감 대상이 아니다».

    라이브 실측(2026-08-10, 90일·3P 2계정): 반품 53건 중 34건(64%)이 고아였다.
    쿠팡 주문 API가 취소분을 안 주므로 «수집 전에 취소된 주문»은 orders에 영영 안 들어온다.
    매출로 센 적이 없으니 그 원가·매출을 빼면 유령 차감이 된다 — 7월 WING2에서 실제로
    return_deduction 38,800원이 전액 유령이었다.
    """
    _ch(db, 1, "COUPANG_WING1", "개인회사 오픽스")
    _product(db, "V1", "COUPANG_WING1")
    _order(db, 1, "O1", "V1", 10000)
    _return(db, "COUPANG_WING1", "V1", 1, "R9")          # order_id 미지정 → 고아
    db.commit()
    s = _sum(db, "COUPANG_WING1")["account"]["summary"]
    assert s["return_deduction"] == _Z, "매출에 없는 주문의 반품은 빼면 안 된다"
    assert s["fee_base_total"] == Decimal("10000"), "과세표준도 유령만큼 깎이면 안 된다"
    rs = s["return_suppression"]
    assert rs["return_rows"] == 1 and rs["suppressed_orphan_rows"] == 1
    assert rs["deducted_rows"] == 0


def test_real_return_is_still_deducted(db):
    """반대 방향 가드: 원주문이 «매출에 잡힌» 반품은 여전히 차감된다(억제가 과하면 안 된다)."""
    _ch(db, 1, "COUPANG_WING1", "개인회사 오픽스")
    _product(db, "V1", "COUPANG_WING1")
    _order(db, 1, "O1", "V1", 10000, qty=2)
    _return(db, "COUPANG_WING1", "V1", 1, "R1", order_id="O1")
    db.commit()
    s = _sum(db, "COUPANG_WING1")["account"]["summary"]
    assert s["return_deduction"] == Decimal("5000"), "단가 5,000 × 반품 1개"
    assert s["return_suppression"]["deducted_rows"] == 1
    assert s["return_suppression"]["suppressed_orphan_rows"] == 0


def test_shipping_income_is_counted_not_only_cost(db):
    """★D-CPP-33: 배송은 «비용»만 빼고 «수입»을 안 세면 무조건 손해로 잡힌다.

    구 대시보드는 둘 다 센다(shipping_revenue) — 그래서 두 화면이 갈렸다.
    수입은 매출이 아니라 순이익에만 더한다(매출 축은 쿠팡 판매분석과 대조하는 축이라 불변).
    """
    _ch(db, 1, "COUPANG_WING1", "개인회사 오픽스")
    _product(db, "V1", "COUPANG_WING1")
    db.add(Order(channel_id=1, order_number="O1", platform_product_id="V1", quantity=1,
                 selling_price=Decimal("10000"), shipping_cost=Decimal("2500"),
                 order_date=OD, status="delivered"))
    db.commit()
    s = _sum(db, "COUPANG_WING1")["account"]["summary"]
    assert s["shipping_income_3p"] == Decimal("2500")
    assert s["seller_shipping_3p"] == Decimal("1900"), "한진 실단가"
    assert s["revenue"] == Decimal("10000"), "배송 수입은 매출 축에 섞지 않는다"


def test_payable_vat_is_deducted_like_the_other_engine(db):
    """★D-CPP-33: Jino 2026-08-04 「납부세액을 뺀다」 결정이 이 엔진에도 내려앉았는가.

    2026-06-15엔 「VAT는 양쪽 미차감으로 통일」이었고 그 주석이 여기 남아 있었다 —
    결정이 뒤집혔는데 profit_calculator에만 반영돼 두 화면이 32,262.96원(7월 WING2) 갈렸다.
    """
    _ch(db, 1, "COUPANG_WING1", "개인회사 오픽스")
    _product(db, "V1", "COUPANG_WING1")
    _order(db, 1, "O1", "V1", 11000)
    db.commit()
    s = _sum(db, "COUPANG_WING1")["account"]["summary"]
    # 매출 11,000 · 수수료 11,000×7.8%×1.1=943.80 · 배송비 1,900 · 원가 0 · 광고 0
    #   매출VAT 1,000 − 매입VAT (943.80+1,900)×10/110=258.53 → 납부세액 741.47
    assert s["payable_vat"] == Decimal("741.47")
    assert s["net_profit_pre_vat"] - s["payable_vat"] == s["net_profit"]
