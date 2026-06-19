# S2 rg_cost_reader fixture 테스트 (D-18, D-12)
# 순수함수 compute_2p_cost 경로: 정산/미정산/보관 account-level
from decimal import Decimal

import pytest

from app.services.coupang.commission_vat_resolver import resolve_2p
from app.services.coupang.rg_cost_reader import Rg2pCost, compute_2p_cost

_Z = Decimal("0")


def _settled(**kwargs):
    """settled dict 헬퍼 (defaults to 0)."""
    base = {"sale_fee": _Z, "delivery": _Z, "warehousing": _Z,
            "storage": _Z, "ad_sales": _Z, "return": _Z, "total_pre_vat": _Z}
    base.update(kwargs)
    return base


# ─── 정산 경로 ──────────────────────────────────────────────────────

class TestSettled:
    def test_all_settled_components(self):
        """정산 옵션 행 전부 존재 — fulfillment/storage/ad_sales 전부 settled × 1.1."""
        r = compute_2p_cost(
            Decimal("10000"), 1,
            settled=_settled(
                sale_fee=Decimal("780"),       # 판매수수료 VAT前
                delivery=Decimal("1000"),
                warehousing=Decimal("500"),
                storage=Decimal("200"),
                ad_sales=Decimal("300"),
            ),
            commission_result=resolve_2p(Decimal("10000"), rg_sale_fee_settled=Decimal("780")),
        )
        # commission: 780 × 1.1 = 858
        assert r.commission == Decimal("858.00")
        assert r.commission_basis == "rg_settled"
        # fulfillment: (1000+500) × 1.1 = 1650
        assert r.fulfillment == Decimal("1650.00")
        assert r.fulfillment_basis == "settled"
        # storage: 200 × 1.1 = 220
        assert r.storage == Decimal("220.00")
        assert r.storage_basis == "settled"
        # rg_ad: 300 × 1.1 = 330
        assert r.rg_ad == Decimal("330.00")
        assert r.rg_ad_basis == "settled"
        # total: 858 + 1650 + 220 + 330 = 3058
        assert r.total == Decimal("3058.00")

    def test_vat_applied_once_on_pre_vat_amounts(self):
        """옵션 행(VAT前) × 1.1 정확히 1회."""
        r = compute_2p_cost(
            Decimal("5000"), 1,
            settled=_settled(delivery=Decimal("500"), sale_fee=Decimal("390")),
            commission_result=resolve_2p(Decimal("5000"), rg_sale_fee_settled=Decimal("390")),
        )
        # commission 390 × 1.1 = 429
        assert r.commission == Decimal("429.00")
        # fulfillment 500 × 1.1 = 550
        assert r.fulfillment == Decimal("550.00")


# ─── 미정산 경로 ─────────────────────────────────────────────────────

class TestUnsettled:
    def test_fulfillment_per_unit_estimate(self):
        """풀필먼트 미정산 → per-unit × qty."""
        r = compute_2p_cost(
            Decimal("10000"), 3,
            settled=None,
            ff_per_unit=Decimal("1500"),
        )
        # fulfillment: 1500 × 3 = 4500
        assert r.fulfillment == Decimal("4500.00")
        assert r.fulfillment_basis == "per_unit_estimate"

    def test_fulfillment_fallback(self):
        """per_unit=0 → fallback 사용."""
        r = compute_2p_cost(
            Decimal("10000"), 2,
            settled=None,
            ff_per_unit=_Z,
            ff_fallback=Decimal("800"),
        )
        assert r.fulfillment == Decimal("1600.00")
        assert r.fulfillment_basis == "per_unit_estimate"

    def test_commission_estimated_no_settled(self):
        """settled=None → commission 기본율 추정."""
        r = compute_2p_cost(Decimal("10000"), 1)
        # 10000 × 0.078 × 1.1 = 858
        assert r.commission == Decimal("858.00")
        assert r.commission_basis == "rg_estimated"

    def test_ad_rate_estimate(self):
        """RG광고 미정산 → ad_rate × revenue."""
        r = compute_2p_cost(
            Decimal("10000"), 1,
            settled=None,
            ad_rate=Decimal("0.05"),  # 5%
        )
        # 10000 × 0.05 = 500
        assert r.rg_ad == Decimal("500.00")
        assert r.rg_ad_basis == "rate_estimate"

    def test_ad_overlap_warning(self):
        """overlap_rg_ad > 0 → rg_ad_basis = overlap_warning."""
        r = compute_2p_cost(
            Decimal("10000"), 1,
            ad_rate=Decimal("0.05"),
            overlap_rg_ad=Decimal("1000"),  # 광고XLSX에 2P 잡힘(이중계상 경고 신호)
        )
        assert r.rg_ad_basis == "overlap_warning"


# ─── 보관 account-level ───────────────────────────────────────────────

class TestStorage:
    def test_unsettled_storage_account_level(self):
        """미정산 보관: storage=0, basis=account_level, storage_account_level=참고값."""
        r = compute_2p_cost(
            Decimal("10000"), 1,
            settled=None,
            account_storage=Decimal("5000"),  # 계정 전체 보관료
        )
        # 옵션에 배분 불가 → storage=0, 참고값만 보존
        assert r.storage == _Z
        assert r.storage_basis == "account_level"
        assert r.storage_account_level == Decimal("5000")

    def test_no_storage_data_excluded(self):
        """보관 데이터 전무 → excluded."""
        r = compute_2p_cost(Decimal("10000"), 1, settled=None)
        assert r.storage == _Z
        assert r.storage_basis == "excluded"
        assert r.storage_account_level == _Z

    def test_settled_storage_preferred(self):
        """정산 storage 있으면 account_level 무시."""
        r = compute_2p_cost(
            Decimal("10000"), 1,
            settled=_settled(storage=Decimal("200")),
            account_storage=Decimal("99999"),
        )
        assert r.storage == Decimal("220.00")  # 200 × 1.1
        assert r.storage_basis == "settled"
