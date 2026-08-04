# test_payable_vat.py — 순이익의 부가세 축 계약 (2026-08-04 Jino 결정)
#
# ★무엇을 고정하는가: 순이익에서 빼는 부가세는 **납부세액**(매출VAT − 매입세액공제)이다.
#   후보가 셋이었고 셋 다 다른 숫자를 낸다(7월 네이버 순이익 6,649,114 기준):
#     ⓐ 미차감(2026-06-15 종전 계약)      → 6,649,114
#     ⓑ 매출VAT 전액 차감                 → 2,452,464  (매입세액공제 무시 = 과다차감)
#     ⓒ 납부세액 차감  ← **채택**          → 6,044,649  (실제 국세청 납부액에 대응)
#   이 파일이 없으면 나중에 누가 ⓐ나 ⓑ로 되돌려도 조용히 지나간다.
from __future__ import annotations

from decimal import Decimal as D

from app.services.profit_calculator import payable_vat


def test_payable_vat_is_sales_minus_input():
    """매출 11,000(VAT 1,000) · 비용 5,500(VAT 500) → 납부 500."""
    assert payable_vat(D("11000"), D("5500")) == D("500")


def test_payable_vat_sums_all_cost_axes():
    """원가·수수료·배송비·광고비 넷 다 매입세액공제 대상이다(전부 VAT 포함 축)."""
    got = payable_vat(D("110000"), D("11000"), D("22000"), D("33000"), D("11000"))
    # 매출VAT 10,000 − 매입VAT 7,000 = 3,000
    assert got == D("3000")


def test_payable_vat_not_full_sales_vat():
    """★매출VAT 전액이 아니다 — 매입세액공제를 무시하면 과다차감이다."""
    sales_only = D("110000") * D("10") / D("110")
    got = payable_vat(D("110000"), D("55000"))
    assert got < sales_only
    assert got == D("5000") and sales_only == D("10000")


def test_payable_vat_zero_when_no_margin():
    """매출 = 비용이면 낼 세금이 없다(부가가치가 0)."""
    assert payable_vat(D("50000"), D("50000")) == D("0")


def test_payable_vat_negative_when_loss():
    """비용이 매출보다 크면 환급 방향(음수) — clamp하지 않는다.

    손실 구간에서 0으로 막으면 적자가 실제보다 커 보인다. 사실 그대로 둔다.
    """
    assert payable_vat(D("11000"), D("22000")) == D("-1000")


def test_payable_vat_no_costs_is_full_sales_vat():
    """매입이 없으면 매출VAT 전액이 납부세액이다(경계)."""
    assert payable_vat(D("11000")) == D("1000")


def test_net_profit_identity_equals_supply_price_basis():
    """모든 축이 VAT 포함이면 `(매출−비용) − 납부세액 == (매출−비용)/1.1`.

    이 항등식이 성립하는 것은 우연이 아니라 정의의 결과다. 항등식이 깨지면 어느 비용 축이
    매입세액공제에서 빠졌다는 뜻이라, 회귀 감지기로 쓸 수 있다.
    """
    rev, cost, comm, ship, ad = D("46163150"), D("9253254"), D("2024475"), D("5433038"), D("22803269")
    gross = rev - cost - comm - ship - ad
    net = gross - payable_vat(rev, cost, comm, ship, ad)
    assert abs(net - gross / D("1.1")) < D("0.01")
    # 7월 네이버 실측: 6,649,114 → 6,044,649 (납부세액 604,465)
    assert abs(gross - D("6649114")) < D("1")
    assert abs(net - D("6044649")) < D("1")
