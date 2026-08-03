# test_naver_order_status_mapping.py — 주문상태 → 매출 인식 계약 (2026-08-03 14일 대사 발견).
#
# 이 매핑은 "이 주문의 돈이 우리 것인가"를 가른다 — REVENUE_EXCLUDED에 걸리는 상태로
# 매핑되면 매출·수수료·원가가 통째로 빠진다. 그래서 표 하나가 곧 회계 계약이다.
#
# 정정 2건:
#   ① EXCHANGED → 종전 "returned"(매출 제외)였다. 교환은 환불이 아니라 상품 교체이고 돈은
#      우리 것이다. 정산 실증: 교환 라인 33건이 NORMAL_SETTLE_ORIGINAL로 정산 완료됐고
#      차감 행은 3개월째 오지 않았다. 라이브 49건 826,500원 과소계상.
#   ② CANCELED_BY_NOPAYMENT → 매핑 부재 → 폴백 .lower() → REVENUE_EXCLUDED에 없어 통과.
#      결제조차 안 된 주문이 매출로 잡혔다. 라이브 10건 163,000원 과대계상.
from __future__ import annotations

import pytest

from app.clients.naver import NaverClient
from app.services.cafe24_status_mapper import REVENUE_EXCLUDED

_m = NaverClient._map_status

# 라이브 실측(2026-08-03, 전 기간 raw_data 전수): 우리 계정에 실제로 나타나는 8종.
LIVE_STATUSES = [
    "PURCHASE_DECIDED", "CANCELED", "DELIVERED", "DELIVERING",
    "RETURNED", "EXCHANGED", "PAYED", "CANCELED_BY_NOPAYMENT",
]


# ── ① 교환은 매출 유지 ─────────────────────────────────────────────────────
def test_exchanged_is_not_returned():
    assert _m("EXCHANGED") == "exchanged"
    assert _m("EXCHANGED") != "returned"


def test_exchanged_counts_as_revenue():
    # ★핵심 계약: 교환은 매출에서 빠지면 안 된다(826,500원 과소계상 회귀 가드).
    assert _m("EXCHANGED") not in REVENUE_EXCLUDED


def test_real_return_is_still_excluded():
    assert _m("RETURNED") == "returned"
    assert _m("RETURNED") in REVENUE_EXCLUDED


# ── ② 미입금 취소는 매출 제외 ──────────────────────────────────────────────
def test_nopayment_cancel_is_excluded_from_revenue():
    # ★결제 자체가 없었던 주문 — 매출로 잡히면 안 된다(163,000원 과대계상 회귀 가드).
    assert _m("CANCELED_BY_NOPAYMENT") in REVENUE_EXCLUDED


def test_nopayment_cancel_no_longer_leaks_via_lowercase_fallback():
    assert _m("CANCELED_BY_NOPAYMENT") != "canceled_by_nopayment"


# ── 매출 인식 상태 ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("naver,expected", [
    ("PAYED", "confirmed"),
    ("DELIVERING", "shipped"),
    ("DELIVERED", "delivered"),
    ("PURCHASE_DECIDED", "delivered"),
])
def test_revenue_bearing_statuses(naver, expected):
    assert _m(naver) == expected
    assert _m(naver) not in REVENUE_EXCLUDED


@pytest.mark.parametrize("naver", ["CANCELED", "CANCELED_BY_NOPAYMENT", "RETURNED"])
def test_revenue_excluded_statuses(naver):
    assert _m(naver) in REVENUE_EXCLUDED


# ── 폴백 방어 ──────────────────────────────────────────────────────────────
def test_all_live_statuses_are_explicitly_mapped(caplog):
    """라이브 8종은 전부 명시 매핑이어야 한다 — 폴백을 타면 경고가 남는다."""
    with caplog.at_level("WARNING"):
        for s in LIVE_STATUSES:
            _m(s)
    assert "미지의 주문상태" not in caplog.text


def test_unknown_status_warns_instead_of_silent_pass(caplog):
    # ②의 실체가 '조용한 폴백'이었다 — 새 상태는 반드시 로그에 남아야 한다.
    with caplog.at_level("WARNING"):
        out = _m("SOME_NEW_STATUS")
    assert out == "some_new_status"          # 기존 동작 보존(주문을 버리지 않는다)
    assert "미지의 주문상태" in caplog.text   # 다만 조용하지 않다
