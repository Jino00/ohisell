# S1 commission_vat_resolver SA fixture 테스트 (D-18, D-12)
from decimal import Decimal

import pytest

from app.services.coupang.commission_vat_resolver import resolve_2p, resolve_3p

_Z = Decimal("0")


# ─── 3P(Wing) 캐스케이드 ─────────────────────────────────────────────

class Test3pActual:
    """실측 service_fee+vat 우선 경로."""

    def test_uses_actual_fee_as_is(self):
        # 정산 실측액(VAT 포함) → 그대로 반환. ×1.1 금지.
        r = resolve_3p(Decimal("10000"), actual_fee=Decimal("858.00"))
        assert r.commission == Decimal("858.00")
        assert r.basis == "actual_3p"

    def test_actual_fee_zero_falls_through(self):
        # actual_fee=0 → 다음 캐스케이드(option_rate)
        r = resolve_3p(
            Decimal("10000"),
            actual_fee=Decimal("0"),
            service_fee_ratio=Decimal("0.105"),
        )
        assert r.basis == "option_rate_3p"
        # 10000 × 0.105 × 1.1 = 1155.00
        assert r.commission == Decimal("1155.00")


class Test3pOptionRate:
    """옵션 service_fee_ratio×1.1 경로."""

    def test_rate_7_8pct(self):
        r = resolve_3p(Decimal("10000"), service_fee_ratio=Decimal("0.078"))
        # 10000 × 0.078 × 1.1 = 858.00
        assert r.commission == Decimal("858.00")
        assert r.basis == "option_rate_3p"

    def test_rate_10_5pct(self):
        r = resolve_3p(Decimal("10000"), service_fee_ratio=Decimal("0.105"))
        # 10000 × 0.105 × 1.1 = 1155.00
        assert r.commission == Decimal("1155.00")
        assert r.basis == "option_rate_3p"


class Test3pDefault:
    """기본 7.8% × 1.1 폴백."""

    def test_no_data_uses_default(self):
        r = resolve_3p(Decimal("10000"))
        # 10000 × 0.078 × 1.1 = 858.00
        assert r.commission == Decimal("858.00")
        assert r.basis == "default_3p"


# ─── 2P(RG) 캐스케이드 ─────────────────────────────────────────────

class Test2pSettled:
    """RG정산 sale_fee(VAT前) × 1.1 경로."""

    def test_settled_vat_applied(self):
        # RG정산 옵션행 sale_fee = VAT前 780 → × 1.1 = 858
        r = resolve_2p(Decimal("10000"), rg_sale_fee_settled=Decimal("780.00"))
        assert r.commission == Decimal("858.00")
        assert r.basis == "rg_settled"

    def test_settled_zero_falls_through(self):
        # rg_sale_fee_settled=0 → 추정 경로
        r = resolve_2p(Decimal("10000"), rg_sale_fee_settled=_Z)
        assert r.basis == "rg_estimated"


class Test2pEstimated:
    """미정산 기본율 추정 경로."""

    def test_default_rate(self):
        r = resolve_2p(Decimal("10000"))
        # 10000 × 0.078 × 1.1 = 858.00
        assert r.commission == Decimal("858.00")
        assert r.basis == "rg_estimated"


# ─── 이중계상 0 검산 ─────────────────────────────────────────────────

class TestNoDualCounting:
    """2P 옵션이 coupang_revenue_fee에 매칭돼도 3P 경로 사용 금지 확인."""

    def test_2p_ignores_actual_fee_and_ratio(self):
        """resolve_2p는 actual_fee·service_fee_ratio 파라미터 자체가 없다.
        3P 정보가 있어도 2P SA는 받지 않음 → 인터페이스 레벨에서 이중계상 0 보장.
        """
        # resolve_2p에 revenue_fee 계열 파라미터를 넘길 방법이 없음을 타입 레벨로 확인
        import inspect
        sig = inspect.signature(resolve_2p)
        assert "actual_fee" not in sig.parameters
        assert "service_fee_ratio" not in sig.parameters

    def test_2p_and_3p_same_vid_no_overlap(self):
        """같은 vid가 양 경로에 있을 때 호출부가 ch_type으로 분기하면 수수료가 1회만 산출됨."""
        revenue = Decimal("10000")
        # 3P 실측 매칭 있어도 2P 계산은 독립 (분기는 호출부 책임)
        r_3p = resolve_3p(revenue, actual_fee=Decimal("858.00"))
        r_2p = resolve_2p(revenue, rg_sale_fee_settled=Decimal("780.00"))
        # 둘 다 계산했을 때 합산이 아니라 각각의 단독값임을 확인
        assert r_3p.commission == Decimal("858.00")
        assert r_2p.commission == Decimal("858.00")
        # 두 값을 더하면 이중계상 — 호출부는 ch_type에 따라 하나만 골라야 한다
        # 이 테스트는 "선택 안 하면 이중계상 위험" 문서화 목적
        assert r_3p.commission + r_2p.commission == Decimal("1716.00")  # 이중계상 예시값
