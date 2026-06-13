# 이 파일은 profit_calculator의 채널별 라인매출 헬퍼(_line_revenue) 머니코드 테스트입니다.
# selling_price 의미가 채널마다 달라(쿠팡/네이버=라인총액, cafe24=단가) qty>1에서 2중계상되던 버그 가드.
# 트랙: track_coupang-revenue-ad-reconciliation S2.
from decimal import Decimal

import pytest

from app.models import Channel, Order
from app.services.profit_calculator import _line_revenue

D = Decimal


def _ch(code: str | None) -> Channel | None:
    return Channel(code=code) if code is not None else None


def _order(selling_price: str, quantity: int) -> Order:
    return Order(selling_price=D(selling_price), quantity=quantity)


# ── cafe24: selling_price=단가(product_price) → ×수량이 정상 ──────────────
def test_cafe24_qty1_unit_price():
    assert _line_revenue(_ch("CAFE24"), _order("10000", 1)) == D("10000")


def test_cafe24_qty3_unit_price_multiplies():
    # 단가 10,000 × 3 = 30,000 (cafe24는 ×수량 유지)
    assert _line_revenue(_ch("CAFE24"), _order("10000", 3)) == D("30000")


# ── 쿠팡: selling_price=orderPrice(라인총액=salesPrice×shippingCount) → 그대로 ──
def test_coupang_qty1_line_total():
    assert _line_revenue(_ch("COUPANG_OPIX"), _order("10000", 1)) == D("10000")


def test_coupang_qty3_line_total_no_multiply():
    # 라인총액 30,000(이미 ×3) — ×수량 또 곱하면 90,000 2중계상. 그대로 30,000.
    assert _line_revenue(_ch("COUPANG_OPIX"), _order("30000", 3)) == D("30000")


# 실 seeded 채널 코드(_CHANNEL_META: WING1/WING2/RG1/RG2/ROCKET) 전부 가드 — codex P2
@pytest.mark.parametrize(
    "code",
    [
        "COUPANG",
        "COUPANG_WING1",
        "COUPANG_WING2",
        "COUPANG_RG1",
        "COUPANG_RG2",
        "COUPANG_ROCKET",
    ],
)
def test_coupang_all_account_codes_treated_as_line_total(code):
    assert _line_revenue(_ch(code), _order("25000", 5)) == D("25000")


# ── 네이버: selling_price=totalPaymentAmount(라인총액) → 그대로 ───────────
def test_naver_qty1_line_total():
    assert _line_revenue(_ch("NAVER"), _order("10000", 1)) == D("10000")


def test_naver_qty2_line_total_no_multiply():
    # 라인총액 20,000(이미 ×2) — ×수량이면 40,000 2중계상. 그대로 20,000.
    assert _line_revenue(_ch("NAVER"), _order("20000", 2)) == D("20000")


# ── 방어: 미지정/미지의 채널은 단가 가정(×수량) — 기존 동작 보존 ──────────
def test_none_channel_falls_back_to_multiply():
    assert _line_revenue(None, _order("10000", 3)) == D("30000")


def test_unknown_code_falls_back_to_multiply():
    assert _line_revenue(_ch("ELEVENST"), _order("10000", 3)) == D("30000")


def test_empty_code_falls_back_to_multiply():
    assert _line_revenue(_ch(""), _order("10000", 2)) == D("20000")
