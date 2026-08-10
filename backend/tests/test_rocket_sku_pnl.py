# test_rocket_sku_pnl.py — 1P 상품(SKU)별 손익 (트랙 ohitech-ad D-16)
#
# 지키는 불변식 둘:
#   ① **추정이 들어가는 자리가 없다.** 매출·원가는 상품번호 그레인 그대로 쓰고, 광고비만
#      옵션→SKU로 **더한다**. sku→option이 1:N(실측 최대 3, 기간 겹침)이라 옵션 축으로 내리면
#      매출·원가를 안분해야 하는데 그건 추정이다 — 그래서 그레인을 SKU로 올렸다.
#   ② **모르는 건 내지 않는다.** 원가 매핑이 없으면 net_profit은 None이다. 0으로 계산하면
#      과대 순이익이 정상값과 섞여 구분이 안 된다(원칙22).
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    CoupangAdOptionDaily,
    CoupangRocketOptionSku,
    CoupangRocketPurchaseOrder,
    CoupangRocketPurchaseOrderItem,
    ProductMaster,
    RocketProductCostMap,
)
from app.services.coupang.rocket_intelligence import _rocket_sku_pnl

VENDOR = "A01029796"
D = date(2026, 7, 27)
# 발주일 KST 2026-07-27 → UTC 저장값은 -9h. 윈도우 경계 계산을 테스트가 되풀이하지 않도록
# 한낮(KST 12:00 = UTC 03:00)을 쓴다.
PO_AT = datetime(2026, 7, 27, 3, 0, 0)


def _ad(db, option_id: str, spend: str, clicks: int = 10, conv: str = "0"):
    db.add(CoupangAdOptionDaily(
        report_date=D, vendor_id=VENDOR, sell_type="Retail",
        ad_option_id=option_id, conv_option_id=option_id,
        impressions=100, clicks=clicks, ad_spend=Decimal(spend),
        orders=0, sales_qty=0, conversion_revenue=Decimal(conv)))


def _bridge(db, option_id: str, sku: str):
    db.add(CoupangRocketOptionSku(option_id=option_id, sku_id=sku, vendor_id=VENDOR))


def _po(db, seq: int, sku: str, qty: int, amount: str, name: str = "테스트 상품"):
    # PO 헤더의 발주금액은 Integer 컬럼(원 단위) — 라인 금액만 Numeric이다.
    db.add(CoupangRocketPurchaseOrder(
        purchase_order_seq=seq, vendor_id=VENDOR, po_created_at=PO_AT,
        sum_of_order_amount=int(Decimal(amount)), order_qty=qty))
    db.add(CoupangRocketPurchaseOrderItem(
        purchase_order_seq=seq, vendor_id=VENDOR, line_no=1, product_number=sku,
        product_name=name, order_qty=qty, line_order_amount=Decimal(amount)))


def _cost(db, sku: str, internal_sku: str, cost_price: str, status: str = "confirmed"):
    db.add(RocketProductCostMap(product_number=sku, internal_sku=internal_sku, status=status))
    if cost_price is not None:
        db.add(ProductMaster(internal_sku=internal_sku, product_name=f"master {internal_sku}",
                             cost_price=Decimal(cost_price)))


@pytest.fixture()
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    try:
        yield s
    finally:
        s.close()


class TestNoAllocation:
    """1:N을 합산으로 흡수한다 — 나누는 순간 추정이 회계 표에 들어온다."""

    def test_한_SKU에_옵션_셋이면_광고비는_더해진다(self, db):
        for opt, spend in (("O1", "1000"), ("O2", "2000"), ("O3", "500")):
            _ad(db, opt, spend)
            _bridge(db, opt, "SKU_A")
        _po(db, 1, "SKU_A", qty=10, amount="50000")
        _cost(db, "SKU_A", "INT-A", "1000")
        db.commit()

        blk = _rocket_sku_pnl(db, D, D, VENDOR)
        row = blk["skus"][0]
        assert row["sku_id"] == "SKU_A"
        assert row["ad_spend"] == Decimal("3500"), "옵션 광고비를 SKU로 합산해야 한다"
        assert row["option_count"] == 3
        # ★매출·원가는 나뉘지 않는다 — 원본 그레인 그대로다
        assert row["revenue"] == Decimal("50000")
        assert row["cost"] == Decimal("10000")           # 10개 × 1,000원
        assert row["net_profit"] == Decimal("36500")     # 50,000 − 3,500 − 10,000

    def test_옵션이_여럿이어도_매출이_중복_계상되지_않는다(self, db):
        """안분 대신 합산을 택한 진짜 이유 — 같은 발주 라인을 옵션 수만큼 세면 매출이 부푼다."""
        for opt in ("O1", "O2"):
            _ad(db, opt, "1000")
            _bridge(db, opt, "SKU_A")
        _po(db, 1, "SKU_A", qty=1, amount="10000")
        _cost(db, "SKU_A", "INT-A", "100")
        db.commit()
        blk = _rocket_sku_pnl(db, D, D, VENDOR)
        assert blk["skus"][0]["revenue"] == Decimal("10000"), "발주 라인이 옵션 수만큼 곱해졌다"


class TestUnknownStaysUnknown:
    def test_원가_매핑이_없으면_순이익을_내지_않는다(self, db):
        _ad(db, "O1", "1000"); _bridge(db, "O1", "SKU_A")
        _po(db, 1, "SKU_A", qty=5, amount="20000")       # cost_map 없음
        db.commit()
        row = _rocket_sku_pnl(db, D, D, VENDOR)["skus"][0]
        assert row["cost"] is None
        assert row["net_profit"] is None, "원가 0으로 계산하면 순이익이 과대해진다"
        assert "원가" in row["profit_basis"]
        assert row["revenue"] == Decimal("20000"), "매출은 아는 값이니 그대로 낸다"

    def test_excluded_매핑도_원가_미상이다(self, db):
        """★'excluded'는 원가 0이 **아니다** — 순이익을 내지 않는다 (2026-08-10 뒤집힘).

        종전 이 테스트는 `cost=0`·`net_profit=19,000`을 지켰다. 그 해석이 «후보를 못 찾은»
        매핑 실패를 전액 이익으로 만들었다. 제외는 «연결을 안 한다»는 뜻이지 «원가가 0이다»는
        사실 주장이 아니다 — 바로 위 테스트(원가 없음)와 **같은 결론**이어야 한다.
        """
        _ad(db, "O1", "1000"); _bridge(db, "O1", "SKU_A")
        _po(db, 1, "SKU_A", qty=5, amount="20000")
        _cost(db, "SKU_A", "INT-A", None, status="excluded")
        db.commit()
        row = _rocket_sku_pnl(db, D, D, VENDOR)["skus"][0]
        assert row["cost"] is None
        assert row["net_profit"] is None, "원가 0으로 계산하면 순이익이 과대해진다"

    def test_기간에_발주가_없으면_순이익_대신_사유를_낸다(self, db):
        """1P 매출은 발주일 귀속이라 발주가 없는 기간엔 매출이 0이다 — −광고비를 손실로 내면 오해다."""
        _ad(db, "O1", "1000"); _bridge(db, "O1", "SKU_A")
        _cost(db, "SKU_A", "INT-A", "100")
        db.commit()
        row = _rocket_sku_pnl(db, D, D, VENDOR)["skus"][0]
        assert row["revenue"] == Decimal("0")
        assert row["net_profit"] is None
        assert "발주" in row["profit_basis"]


class TestCoverageIsVisible:
    def test_브리지_없는_광고비는_숨지_않는다(self, db):
        _ad(db, "O1", "1000"); _bridge(db, "O1", "SKU_A")
        _po(db, 1, "SKU_A", qty=1, amount="5000"); _cost(db, "SKU_A", "INT-A", "100")
        _ad(db, "O_NOBRIDGE", "9000")                     # 브리지 없음
        db.commit()
        cov = _rocket_sku_pnl(db, D, D, VENDOR)["coverage"]
        assert cov["ad_total"] == Decimal("10000")
        assert cov["ad_unbridged"] == Decimal("9000")
        assert cov["unbridged_option_count"] == 1
        assert cov["ad_bridged_pct"] == Decimal("10")     # 1,000 / 10,000
        assert cov["ad_with_cost_pct"] == Decimal("10")

    def test_광고비가_0이면_비율은_None이고_안_터진다(self, db):
        cov = _rocket_sku_pnl(db, D, D, VENDOR)["coverage"]
        assert cov["ad_total"] == Decimal("0")
        assert cov["ad_bridged_pct"] is None


class TestCostSourcePriority:
    """원가는 sellc 등록값이 정본, 이름 유사도 자동매핑은 폴백이다(D-19).

    ★두 값의 신뢰 등급이 다르다: sellc는 쿠팡 externalVendorSku ↔ internal_sku **코드 정확일치**,
      자동매핑은 이름 유사도 score 0.56~0.78의 추정(실제로 아이폰13을 아이폰17 Pro에 붙였다).
      같은 이름으로 저장해 구분을 잃은 것이 2026-08-03 사고의 구조적 원인이라 출처를 남긴다.
    """

    def _sellc(self, db, option_id: str, internal_sku: str, cost: str):
        from app.models import Channel, ProductChannelMapping
        db.add(Channel(id=1, code="COUPANG_WING1", name="윙", platform="coupang",
                       channel_type="marketplace"))
        pm = ProductMaster(internal_sku=internal_sku, product_name=f"master {internal_sku}",
                           cost_price=Decimal(cost))
        db.add(pm); db.flush()
        db.add(ProductChannelMapping(product_id=pm.id, channel_id=1,
                                     channel_product_id=option_id, is_active=True))

    def test_sellc가_자동매핑을_이긴다(self, db):
        _ad(db, "O1", "1000"); _bridge(db, "O1", "SKU_A")
        _po(db, 1, "SKU_A", qty=10, amount="50000")
        _cost(db, "SKU_A", "INT-AUTO", "9999")          # 자동매핑(비싼 오답)
        self._sellc(db, "O1", "INT-SELLC", "3300")      # sellc 등록원가
        db.commit()
        row = _rocket_sku_pnl(db, D, D, VENDOR)["skus"][0]
        assert row["cost"] == Decimal("33000"), "sellc 3,300×10이어야 한다"
        assert row["cost_source"] == "sellc"
        assert "sellc" in row["profit_basis"]

    def test_sellc가_없으면_자동매핑으로_폴백하되_추정임을_말한다(self, db):
        _ad(db, "O1", "1000"); _bridge(db, "O1", "SKU_A")
        _po(db, 1, "SKU_A", qty=10, amount="50000")
        _cost(db, "SKU_A", "INT-AUTO", "2500")
        db.commit()
        row = _rocket_sku_pnl(db, D, D, VENDOR)["skus"][0]
        assert row["cost"] == Decimal("25000")
        assert row["cost_source"] == "auto_map"
        assert "자동매핑" in row["profit_basis"], "추정임을 행이 스스로 말해야 한다"

    def test_ignored보다_sellc가_우선이다(self, db):
        """ignored 22건도 자동매핑과 같은 6분 배치 산물이라 사람의 제외 결정이라는 보장이 없다."""
        _ad(db, "O1", "1000"); _bridge(db, "O1", "SKU_A")
        _po(db, 1, "SKU_A", qty=10, amount="50000")
        _cost(db, "SKU_A", "INT-IGN", None, status="ignored")
        self._sellc(db, "O1", "INT-SELLC", "3300")
        db.commit()
        row = _rocket_sku_pnl(db, D, D, VENDOR)["skus"][0]
        assert row["cost"] == Decimal("33000")
        assert row["cost_source"] == "sellc"

    def test_커버리지가_sellc와_추정을_갈라_낸다(self, db):
        _ad(db, "O1", "1000"); _bridge(db, "O1", "SKU_A")
        _po(db, 1, "SKU_A", qty=1, amount="5000"); self._sellc(db, "O1", "INT-SELLC", "100")
        _ad(db, "O2", "3000"); _bridge(db, "O2", "SKU_B")
        _po(db, 2, "SKU_B", qty=1, amount="5000"); _cost(db, "SKU_B", "INT-AUTO", "100")
        db.commit()
        cov = _rocket_sku_pnl(db, D, D, VENDOR)["coverage"]
        assert cov["ad_cost_sellc"] == Decimal("1000")
        assert cov["ad_cost_auto"] == Decimal("3000")
        assert cov["ad_cost_sellc_pct"] == Decimal("25")

    def test_비활성_채널매핑은_안_쓴다(self, db):
        from app.models import Channel, ProductChannelMapping
        _ad(db, "O1", "1000"); _bridge(db, "O1", "SKU_A")
        _po(db, 1, "SKU_A", qty=1, amount="5000")
        db.add(Channel(id=1, code="COUPANG_WING1", name="윙", platform="coupang",
                       channel_type="marketplace"))
        pm = ProductMaster(internal_sku="INT-OFF", product_name="비활성", cost_price=Decimal("999"))
        db.add(pm); db.flush()
        db.add(ProductChannelMapping(product_id=pm.id, channel_id=1,
                                     channel_product_id="O1", is_active=False))
        db.commit()
        row = _rocket_sku_pnl(db, D, D, VENDOR)["skus"][0]
        assert row["cost"] is None, "is_active=False 매핑을 원가로 쓰면 안 된다"


class TestScope:
    def test_3P_광고는_1P_표에_안_섞인다(self, db):
        db.add(CoupangAdOptionDaily(
            report_date=D, vendor_id=VENDOR, sell_type="3P",
            ad_option_id="O1", conv_option_id="O1", impressions=1, clicks=1,
            ad_spend=Decimal("777"), orders=0, sales_qty=0, conversion_revenue=Decimal("0")))
        _bridge(db, "O1", "SKU_A")
        db.commit()
        blk = _rocket_sku_pnl(db, D, D, VENDOR)
        assert blk["skus"] == []
        assert blk["coverage"]["ad_total"] == Decimal("0")

    def test_윈도우_밖_발주는_매출에_안_들어온다(self, db):
        _ad(db, "O1", "1000"); _bridge(db, "O1", "SKU_A")
        _cost(db, "SKU_A", "INT-A", "100")
        db.add(CoupangRocketPurchaseOrder(
            purchase_order_seq=9, vendor_id=VENDOR,
            po_created_at=datetime(2026, 6, 1, 3, 0, 0),   # 윈도우 밖
            sum_of_order_amount=99999, order_qty=1))
        db.add(CoupangRocketPurchaseOrderItem(
            purchase_order_seq=9, vendor_id=VENDOR, line_no=1, product_number="SKU_A",
            order_qty=1, line_order_amount=Decimal("99999")))
        db.commit()
        assert _rocket_sku_pnl(db, D, D, VENDOR)["skus"][0]["revenue"] == Decimal("0")

    def test_광고를_안_돌린_상품은_행을_만들지_않는다(self, db):
        """이 표는 광고 축 조망이다 — 광고비 0행이 섞이면 상위 N이 의미를 잃는다."""
        _po(db, 1, "SKU_NOAD", qty=1, amount="5000")
        _cost(db, "SKU_NOAD", "INT-N", "100")
        db.commit()
        assert _rocket_sku_pnl(db, D, D, VENDOR)["skus"] == []
