# test_kpi_evidence.py — KPI 카드 근거 (계약 docs/contracts/CONTRACT_kpi_evidence_page.md)
#
# 지키는 계약 세 줄:
#   ① 근거 합계는 **카드가 쓴 값**(`_kpi_totals`)과 원 단위로 같다 — 갈라지면 근거가 아니다.
#   ② 행이 «안 가진» 항목은 0이 아니라 **None**(모른다)이다 — 0으로 채우면 「원가 0원」 거짓말.
#   ③ 설명 못 한 금액은 숨기지 않고 **잔차(residual)**로 이름을 붙인다.
#
# 왜 HTTP 경계 테스트가 따로 있나: 서비스층이 정직하게 계산해도 `response_model`에 칸이 없으면
# 키가 **조용히 사라진다**(교훈 #321·#223 — 그 사고로 배너가 통째로 숨었다). dict 단위 테스트는
# 그 결함을 원리적으로 못 잡는다.
from __future__ import annotations

from decimal import Decimal

import pytest

from app.routers.dashboard import _kpi_totals
from app.services.kpi_evidence import DEDUCTION_KEYS, build_kpi_evidence, build_row_evidence

ROCKET_ID = 5

CMAP = {
    6: ("주식회사 오하이", "주식회사 오하이 · 네이버 스마트스토어", True),
    7: ("주식회사 오하이테크", "주식회사 오하이테크 · 자사몰(cafe24)", True),
    ROCKET_ID: ("주식회사 오하이테크", "주식회사 오하이테크 · 쿠팡 로켓배송(1P)", False),
}


def _full_row():
    """분해 항목을 전부 가진 행 — `calculate_channel_summary` 산출과 같은 모양.

    매출 1,000,000 − (원가 400,000 + 수수료 100,000 + 광고비 50,000 + 배송비 30,000
                      + 고정비 20,000 + 부가세 36,363.64) = 363,636.36
    """
    return {
        "channel_id": 6, "channel_name": "네이버",
        "revenue": "1000000", "product_revenue": "950000", "shipping_revenue": "50000",
        "cost": "400000", "commission": "100000", "ad_spend": "50000",
        "shipping": "30000", "fixed_cost": "20000",
        "payable_vat": "36363.63636363636363636363636",
        "unmapped_revenue": "0",
        "net_profit": "363636.3636363636363636363636",
        "order_count": 40,
    }


def _ad_only_row():
    """로켓1P 계산서 축 — 손익을 못 재고 광고비만 확정 비용이라 −광고비 하한이 된다(D-22)."""
    return {
        "channel_id": ROCKET_ID, "channel_name": "쿠팡 로켓배송",
        "revenue": "0", "product_revenue": "0", "shipping_revenue": "0",
        "cost": "0", "commission": "0", "ad_spend": "597888", "shipping": "0",
        "net_profit": None, "net_scope": "ad_only", "net_basis_revenue": "0",
        "unmapped_revenue": "0", "order_count": 0, "revenue_basis": "settlement",
    }


def _partial_row():
    """RG 평행 엔진 행 — 고정비·부가세 칸을 **원래 안 가진다**(0이 아니라 모른다)."""
    return {
        "channel_id": 7, "channel_name": "오픽스 RG",
        "revenue": "500000", "product_revenue": "500000", "shipping_revenue": "0",
        "cost": "200000", "commission": "80000", "ad_spend": "40000",
        "net_profit": "180000", "unmapped_revenue": "0", "order_count": 12,
    }


# ── ① 검산 ────────────────────────────────────────────────────────────────
def test_full_row_decomposes_to_net_exactly():
    ev = build_row_evidence(_full_row(), ROCKET_ID)
    assert ev["residual"] == "0.00", "부가세 나눗셈 먼지(0E-22)가 화면에 새면 안 된다"
    assert ev["explains_net"] is True
    # 분담금은 로켓1P 판매 축에만 있는 항목이라 이 행엔 없다 — 「없음」이지 미달이 아니다.
    assert ev["missing"] == ["promo_burden"]


def test_totals_equal_the_card_numbers():
    """근거 합계 ↔ 카드 = 같은 함수에서 나오므로 원 단위로 같아야 한다."""
    rows = [_full_row(), _partial_row(), _ad_only_row()]
    totals = _kpi_totals(None, rows, ROCKET_ID)
    ev = build_kpi_evidence(rows, totals, ROCKET_ID, CMAP)

    assert ev["checks"]["revenue_matches"] is True
    assert ev["checks"]["net_matches"] is True
    assert ev["checks"]["order_count_matches"] is True
    assert Decimal(ev["totals"]["revenue"]) == Decimal("1500000")
    # 순이익 = 363,636.36… + 180,000 − 597,888 (하한)
    assert Decimal(ev["totals"]["net_profit"]) == totals["net_profit"]


def test_order_count_excludes_rocket_1p():
    """1P의 그 칸은 주문 건수가 아니라 판매수량이라 카드에서 빠진다 — 화면이 그 사실을 말해야 한다."""
    rows = [_full_row(), dict(_ad_only_row(), order_count=99)]
    totals = _kpi_totals(None, rows, ROCKET_ID)
    ev = build_kpi_evidence(rows, totals, ROCKET_ID, CMAP)

    rocket = next(r for r in ev["rows"] if r["channel_id"] == ROCKET_ID)
    assert rocket["counted_in_order_card"] is False
    assert ev["order_count_excluded"] == 99
    assert ev["totals"]["order_count"] == 40
    assert ev["checks"]["order_count_matches"] is True


# ── ② 모르는 것을 0으로 채우지 않는다 ──────────────────────────────────────
def test_missing_components_stay_none_not_zero():
    ev = build_row_evidence(_partial_row(), ROCKET_ID)
    assert ev["deductions"]["fixed_cost"] is None, "모르는 항목을 0으로 채우면 「고정비 0원」 거짓말"
    assert ev["deductions"]["payable_vat"] is None
    assert "fixed_cost" in ev["missing"] and "payable_vat" in ev["missing"]


def test_unknown_rows_are_counted_per_component():
    rows = [_full_row(), _partial_row()]
    totals = _kpi_totals(None, rows, ROCKET_ID)
    ev = build_kpi_evidence(rows, totals, ROCKET_ID, CMAP)
    assert ev["deduction_unknown_rows"]["payable_vat"] == 1
    assert ev["deduction_unknown_rows"]["cost"] == 0


# ── ③ 설명 못 한 금액엔 이름을 붙인다 ──────────────────────────────────────
def test_residual_names_the_unexplained_amount():
    """RG 행은 부가세를 안 실어 잔차가 남는다 — 그 금액이 숫자로 나와야 한다."""
    ev = build_row_evidence(_partial_row(), ROCKET_ID)
    assert ev["residual"] is not None

    # 항목은 그대로인데 순이익만 10,000원 더 낮은 행 — 그 10,000원이 **이름 없이 사라지면 안 된다**.
    off = dict(_partial_row(), net_profit="170000")
    ev_off = build_row_evidence(off, ROCKET_ID)
    assert Decimal(ev_off["residual"]) == Decimal("-10000")
    assert ev_off["explains_net"] is False, "잔차가 남았는데 「검산 통과」라고 말하면 안 된다"


def test_ad_only_row_reports_floor_not_full_profit():
    ev = build_row_evidence(_ad_only_row(), ROCKET_ID)
    assert Decimal(ev["net_profit"]) == Decimal("-597888")
    assert Decimal(ev["net_floor_ad"]) == Decimal("597888")
    # 이 행의 숫자는 「손익」이 아니라 「하한」이다 — 화면이 그렇게 말할 수 있어야 한다.
    assert ev["net_scope"] == "ad_only"
    assert Decimal(ev["net_basis_revenue"]) == Decimal("0"), "원가 미상 매출은 이익률 분모에 안 들어간다"


def test_unmeasured_revenue_explains_the_rate_denominator():
    """이익률 분모가 총매출과 다른 몫 = 「손익을 못 잰 매출」. 이걸 안 보이면 사용자가 손으로
    계산해 보고 화면이 틀렸다고 결론 내린다."""
    rows = [_full_row(), _ad_only_row()]
    totals = _kpi_totals(None, rows, ROCKET_ID)
    ev = build_kpi_evidence(rows, totals, ROCKET_ID, CMAP)
    assert Decimal(ev["totals"]["basis_revenue"]) == Decimal("1000000")
    assert Decimal(ev["totals"]["unmeasured_revenue"]) == Decimal("0")


def test_rows_carry_company_labels():
    rows = [_full_row()]
    ev = build_kpi_evidence(rows, _kpi_totals(None, rows, ROCKET_ID), ROCKET_ID, CMAP)
    assert ev["rows"][0]["label"] == "주식회사 오하이 · 네이버 스마트스토어"
    assert ev["rows"][0]["company"] == "주식회사 오하이"


def test_deduction_keys_include_every_subtrahend_of_the_net_formula():
    """검산식에서 빼는 항목이 하나라도 목록에서 빠지면 화면의 「= 순이익」이 안 맞는다."""
    for key in ("cost", "commission", "ad_spend", "shipping", "fixed_cost", "payable_vat"):
        assert key in DEDUCTION_KEYS


# ── HTTP 경계 — 서비스층 테스트가 원리적으로 못 잡는 자리 ────────────────────
def test_response_model_keeps_the_evidence_fields():
    """`KpiEvidence` 스키마가 근거 칸을 HTTP 경계에서 지우지 않는지 — 교훈 #321."""
    from app.schemas import KpiEvidence

    rows = [_full_row(), _partial_row()]
    totals = _kpi_totals(None, rows, ROCKET_ID)
    payload = build_kpi_evidence(rows, totals, ROCKET_ID, CMAP)
    body = KpiEvidence(
        date_from="2026-08-22", date_to="2026-08-22", rocket_basis="settlement", **payload
    ).model_dump()

    assert "deductions" in body["rows"][0]
    assert body["rows"][0]["deductions"]["cost"] is not None
    partial = next(r for r in body["rows"] if r["channel_id"] == 7)
    assert partial["deductions"]["payable_vat"] is None, "None이 0으로 강제되면 거짓말이 된다"
    assert "residual" in body["rows"][0]
    assert set(body["checks"]) == {
        "revenue_matches", "net_matches", "order_count_matches", "net_fully_explained",
    }
    assert body["totals"]["unmeasured_revenue"] is not None
    assert body["deduction_totals"]["cost"] == "600000"  # 아는 행만 더한 합(400,000 + 200,000)


def test_payable_vat_is_exposed_by_channel_summary_rows():
    """행에 부가세가 안 실리면 검산식이 화면에서 원 단위로 안 맞는다 — 그리고 **안 맞는 이유가
    부가세라는 사실 자체가 화면 밖**이 된다."""
    import inspect

    from app.services import profit_calculator

    src = inspect.getsource(profit_calculator.calculate_channel_summary)
    assert '"payable_vat": str(vat_due)' in src
    # net과 출력이 **같은 값**을 쓰는지 — 두 번 계산하면 언젠가 갈라진다.
    assert "- b[\"fixed_cost\"] - vat_due" in src
