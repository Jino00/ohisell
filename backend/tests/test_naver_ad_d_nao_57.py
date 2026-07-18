# test_naver_ad_d_nao_57.py — D-NAO-57 상품별 원가 구조 기반 BEP·타겟 정밀화
# 커버: (A) shopping_ad_product_sync 수집·매핑 + campaign_target_resolver 우선순위 ②(상품 파생,
#   가중/폴백/override불변) / (B) ad_commission_rate 분해·언디루션·가드·calculate_bep 폴백 표기 /
#   (C) 배송비 단가당 환산·평균수량·폴백·VAT 정합·config 상수.
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    Channel, NaverAdgroupProduct, NaverCampaignSettings, NaverEntity, NaverProductBep,
    NaverSettlementCase, NaverSettlementDaily, Order, ProductChannelMapping, ProductMaster,
)
from app.services.naver_ad import bep_calculator, campaign_target_resolver, shopping_ad_product_sync
from app.utils.kst import kst_today


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


# ══════════════════════════════════════════════════════════════════
# (A) shopping_ad_product_sync + resolver ②
# ══════════════════════════════════════════════════════════════════
def _ours_shopping_adgroup(db, campaign_id="cmp-shop", adgroup_id="grp-1"):
    db.add(NaverCampaignSettings(campaign_id=campaign_id, optimizer="ours"))
    db.add(NaverEntity(entity_type="adgroup", entity_id=adgroup_id, parent_id=campaign_id,
                       campaign_id=campaign_id, campaign_type="SHOPPING", status="on"))
    db.commit()


def test_collect_only_ours_shopping_active_adgroups(db):
    # ours 쇼핑(대상) + mop 쇼핑(제외) + ours 파워링크(제외) + ours 쇼핑 off(제외)
    db.add(NaverCampaignSettings(campaign_id="c-ours", optimizer="ours"))
    db.add(NaverCampaignSettings(campaign_id="c-mop", optimizer="mop"))
    db.add(NaverCampaignSettings(campaign_id="c-web", optimizer="ours"))
    db.add_all([
        NaverEntity(entity_type="adgroup", entity_id="g-ours", campaign_id="c-ours", campaign_type="SHOPPING", status="on"),
        NaverEntity(entity_type="adgroup", entity_id="g-mop", campaign_id="c-mop", campaign_type="SHOPPING", status="on"),
        NaverEntity(entity_type="adgroup", entity_id="g-web", campaign_id="c-web", campaign_type="WEB_SITE", status="on"),
        NaverEntity(entity_type="adgroup", entity_id="g-off", campaign_id="c-ours", campaign_type="SHOPPING", status="off"),
    ])
    db.commit()
    ags = shopping_ad_product_sync._ours_shopping_adgroups(db)
    assert {a.entity_id for a in ags} == {"g-ours"}


def test_sync_adgroup_products_snapshot_replace_and_dedup(db):
    _ours_shopping_adgroup(db)
    ads = {"grp-1": [
        {"mall_product_id": "13365319468", "product_name": "17E"},
        {"mall_product_id": "13365319468", "product_name": "dup"},  # 같은 상품 중복 소재 → dedup
        {"mall_product_id": "999", "product_name": "other"},
    ]}
    res = shopping_ad_product_sync.sync_adgroup_products(db, ads_by_adgroup=ads)
    assert res == {"adgroups": 1, "mappings": 2, "products": 2, "removed": 0, "failed_adgroups": 0}
    rows = db.query(NaverAdgroupProduct).filter(NaverAdgroupProduct.adgroup_id == "grp-1").all()
    assert {r.mall_product_id for r in rows} == {"13365319468", "999"}
    assert all(r.campaign_id == "cmp-shop" for r in rows)

    # 재실행 — 상품 하나 이탈 → 스냅샷 교체(멱등, 잔여 없음)
    res2 = shopping_ad_product_sync.sync_adgroup_products(db, ads_by_adgroup={"grp-1": [{"mall_product_id": "13365319468", "product_name": "17E"}]})
    assert res2["mappings"] == 1
    rows2 = db.query(NaverAdgroupProduct).filter(NaverAdgroupProduct.adgroup_id == "grp-1").all()
    assert {r.mall_product_id for r in rows2} == {"13365319468"}


def test_sync_reconciles_non_ours_campaign_rows(db):
    """리뷰 P2-3 ①: optimizer가 ours가 아니게 된 캠페인의 매핑 행 삭제."""
    _ours_shopping_adgroup(db)  # cmp-shop=ours
    db.add(NaverCampaignSettings(campaign_id="cmp-left", optimizer="mop"))  # ours 이탈
    db.add(NaverAdgroupProduct(adgroup_id="grp-old", campaign_id="cmp-left", mall_product_id="111"))
    db.commit()
    res = shopping_ad_product_sync.sync_adgroup_products(db, ads_by_adgroup={"grp-1": []})
    assert res["removed"] == 1
    assert db.query(NaverAdgroupProduct).filter(NaverAdgroupProduct.campaign_id == "cmp-left").count() == 0


def test_sync_reconciles_stale_adgroup_when_fully_enumerated(db):
    """리뷰 P2-3 ②: 전체 그룹 열거 성공 캠페인의, 수집에 없는 그룹(삭제/off) 행 삭제."""
    _ours_shopping_adgroup(db)  # 활성 그룹 = grp-1만
    # grp-gone: 과거 매핑 행은 있으나 활성 엔티티에 없음(그룹 삭제/off) → stale
    db.add(NaverAdgroupProduct(adgroup_id="grp-gone", campaign_id="cmp-shop", mall_product_id="222"))
    db.commit()
    res = shopping_ad_product_sync.sync_adgroup_products(
        db, ads_by_adgroup={"grp-1": [{"mall_product_id": "333", "product_name": "x"}]}
    )
    assert res["removed"] == 1
    assert db.query(NaverAdgroupProduct).filter(NaverAdgroupProduct.adgroup_id == "grp-gone").count() == 0
    assert db.query(NaverAdgroupProduct).filter(NaverAdgroupProduct.adgroup_id == "grp-1").count() == 1


def test_sync_preserves_mappings_when_fetch_fails(db, monkeypatch):
    """리뷰 P2-3 ② 안전변: get_ads 실패 그룹이 있는 캠페인은 stale 정리 유보(매핑 소실 금지)."""
    _ours_shopping_adgroup(db)  # grp-1 활성
    db.add(NaverEntity(entity_type="adgroup", entity_id="grp-2", parent_id="cmp-shop",
                       campaign_id="cmp-shop", campaign_type="SHOPPING", status="on"))
    # 기존 매핑: grp-2(이번에 실패할 그룹) + grp-gone(진짜 stale이지만 실패 캠페인이라 유보)
    db.add(NaverAdgroupProduct(adgroup_id="grp-2", campaign_id="cmp-shop", mall_product_id="444"))
    db.add(NaverAdgroupProduct(adgroup_id="grp-gone", campaign_id="cmp-shop", mall_product_id="555"))
    db.commit()

    def fake_get_ads(aid):
        if aid == "grp-2":
            raise RuntimeError("일시 API 장애")
        return [{"mall_product_id": "333", "product_name": "x", "adgroup_id": aid}]

    monkeypatch.setattr(shopping_ad_product_sync, "get_ads", fake_get_ads)
    res = shopping_ad_product_sync.sync_adgroup_products(db)  # 실 경로(주입 없음)
    assert res["failed_adgroups"] == 1
    # 실패 그룹 매핑 보존 + 같은 캠페인의 stale 후보도 유보(removed 0)
    assert res["removed"] == 0
    assert db.query(NaverAdgroupProduct).filter(NaverAdgroupProduct.adgroup_id == "grp-2").count() == 1
    assert db.query(NaverAdgroupProduct).filter(NaverAdgroupProduct.adgroup_id == "grp-gone").count() == 1


def test_resolver_priority2_adgroup_product_derived(db):
    # 그룹에 상품 2개 매핑, 각 상품 BEP target 상이 → 매출가중
    db.add(NaverAdgroupProduct(adgroup_id="grp-1", campaign_id="cmp-shop", mall_product_id="p1"))
    db.add(NaverAdgroupProduct(adgroup_id="grp-1", campaign_id="cmp-shop", mall_product_id="p2"))
    db.add(NaverProductBep(channel_id=6, channel_product_id="p1", has_cost=True, target_roas=Decimal("150")))
    db.add(NaverProductBep(channel_id=6, channel_product_id="p2", has_cost=True, target_roas=Decimal("250")))
    db.add(Order(channel_id=6, platform_product_id="p1", selling_price=Decimal("3000"), quantity=1,
                 order_date=date(2026, 7, 1), order_number="o1"))
    db.add(Order(channel_id=6, platform_product_id="p2", selling_price=Decimal("1000"), quantity=1,
                 order_date=date(2026, 7, 1), order_number="o2"))
    db.commit()
    # adgroup grain: (150*3000 + 250*1000)/4000 = 175
    r = campaign_target_resolver.resolve_adgroup_target_roas(db, "grp-1")
    assert r["source"] == "product_bep"
    assert r["target_roas"] == Decimal("175")
    # campaign grain: 같은 상품 풀 → 동일
    rc = campaign_target_resolver.resolve_target_roas(db, "cmp-shop")
    assert rc["source"] == "product_bep"
    assert rc["target_roas"] == Decimal("175")


def test_resolver_priority2_simple_average_when_no_orders(db):
    db.add(NaverAdgroupProduct(adgroup_id="grp-1", campaign_id="cmp-shop", mall_product_id="p1"))
    db.add(NaverAdgroupProduct(adgroup_id="grp-1", campaign_id="cmp-shop", mall_product_id="p2"))
    db.add(NaverProductBep(channel_id=6, channel_product_id="p1", has_cost=True, target_roas=Decimal("150")))
    db.add(NaverProductBep(channel_id=6, channel_product_id="p2", has_cost=True, target_roas=Decimal("250")))
    db.commit()
    r = campaign_target_resolver.resolve_adgroup_target_roas(db, "grp-1")
    assert r["target_roas"] == Decimal("200")  # 단순평균


def test_resolver_priority2_ignores_no_cost_product(db):
    # 매핑 상품이 원가미확인(has_cost=False)뿐이면 ② 성립 안 함 → 계정기본 폴백
    db.add(NaverAdgroupProduct(adgroup_id="grp-1", campaign_id="cmp-shop", mall_product_id="p1"))
    db.add(NaverProductBep(channel_id=6, channel_product_id="p1", has_cost=False, target_roas=None))
    db.add(NaverProductBep(channel_id=6, channel_product_id="acc", has_cost=True, target_roas=Decimal("300")))
    db.commit()
    r = campaign_target_resolver.resolve_target_roas(db, "cmp-shop")
    assert r["source"] == "account_default"
    assert r["target_roas"] == Decimal("300")


def test_resolver_override_absolute_over_product(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-shop", optimizer="ours", target_roas_override=Decimal("999")))
    db.add(NaverAdgroupProduct(adgroup_id="grp-1", campaign_id="cmp-shop", mall_product_id="p1"))
    db.add(NaverProductBep(channel_id=6, channel_product_id="p1", has_cost=True, target_roas=Decimal("150")))
    db.commit()
    r = campaign_target_resolver.resolve_target_roas(db, "cmp-shop")
    assert r == {"target_roas": Decimal("999"), "source": "override"}


def test_resolver_falls_back_when_no_mapping(db):
    db.add(NaverProductBep(channel_id=6, channel_product_id="acc", has_cost=True, target_roas=Decimal("222")))
    db.commit()
    r = campaign_target_resolver.resolve_target_roas(db, "cmp-none")
    assert r["source"] == "account_default"
    assert r["target_roas"] == Decimal("222")


# ══════════════════════════════════════════════════════════════════
# (B) ad_commission_rate — 정산 유형별 분해·언디루션
# ══════════════════════════════════════════════════════════════════
def _daily_blend(db, settle: str, comm: str):
    """effective_commission_rate = |comm|/(settle+|comm|) 를 만드는 일별 정산 1행."""
    db.add(NaverSettlementDaily(settle_amount=Decimal(settle), commission_amount=Decimal(comm),
                                settle_expect_date="2026-07-01"))
    db.commit()


def _case(db, i, pay, total_pay, interlock, otype="PROD_ORDER"):
    db.add(NaverSettlementCase(
        product_order_id=f"po{i}", order_id=f"o{i}", product_order_type=otype,
        pay_settle_amount=Decimal(str(pay)),
        total_pay_commission=Decimal(str(total_pay)),
        selling_interlock_commission=Decimal(str(interlock)),
        free_installment_commission=Decimal("0"),
    ))


def test_ad_commission_rate_undilutes_interlock(db):
    # 블렌드 B=0.04(전체 40주문 커미션 1600/gross 40000). 쇼핑 20건 5%(3%+2%)·직접 20건 3%.
    _daily_blend(db, "38400", "-1600")
    for i in range(20):  # 쇼핑: 매출연동 붙음
        _case(db, i, 1000, -30, -20)
    for i in range(20, 40):  # 직접: 매출연동 0
        _case(db, i, 1000, -30, 0)
    db.commit()
    r = bep_calculator.ad_commission_rate(db)
    assert r is not None
    assert r["basis"] == "case_decomposition"
    assert r["order_mgmt_rate"] == Decimal("0.03")
    assert r["full_interlock_rate"] == Decimal("0.02")
    assert r["rate"] == Decimal("0.05")  # 광고=100% 쇼핑 → 5%
    assert r["blended_rate"] == Decimal("0.04")
    assert r["shopping_gross_share"] == Decimal("0.5")


def test_ad_commission_rate_none_when_too_few_rows(db):
    _daily_blend(db, "38400", "-1600")
    for i in range(10):  # <30
        _case(db, i, 1000, -30, -20)
    db.commit()
    assert bep_calculator.ad_commission_rate(db) is None


def test_ad_commission_rate_none_when_no_interlock(db):
    _daily_blend(db, "38800", "-1200")
    for i in range(40):
        _case(db, i, 1000, -30, 0)
    db.commit()
    assert bep_calculator.ad_commission_rate(db) is None


def test_ad_commission_rate_none_when_shopping_share_too_low(db):
    _daily_blend(db, "38400", "-1600")
    _case(db, 0, 1000, -30, -20)  # 쇼핑 1건뿐
    for i in range(1, 40):
        _case(db, i, 1000, -30, 0)
    db.commit()
    # shopping_share = 1000/40000 = 0.025 < 0.05 → None
    assert bep_calculator.ad_commission_rate(db) is None


def test_ad_commission_rate_none_when_implausible(db):
    # 매출연동 비중 크고 쇼핑점유 floor 근처 → full_interlock 폭증 → >20% → None
    _daily_blend(db, "36000", "-4000")  # B=0.1
    _case(db, 0, 1000, -50, -450)  # 쇼핑 1건(pay 1000)
    _case(db, 1, 1000, -50, -450)
    for i in range(2, 40):
        _case(db, i, 480, -50, 0)  # 직접 38건, pay 480 → 쇼핑점유 2000/(2000+18240)=0.099
    db.commit()
    r = bep_calculator.ad_commission_rate(db)
    assert r is None


# ══════════════════════════════════════════════════════════════════
# (C) 배송비 단가당 환산 + VAT 정합 + calculate_bep 통합
# ══════════════════════════════════════════════════════════════════
def test_shipping_config_constants():
    assert bep_calculator.SHIPPING_COST_NORMAL == Decimal("1900")
    assert bep_calculator.SHIPPING_COST_NBAESONG == Decimal("3020")
    # 현시점 판별 훅은 전 건 일반배송(원칙22 — N배송 미시작)
    assert bep_calculator._order_shipping_cost(None) == Decimal("1900")


def test_avg_qty_and_logistics_per_unit(db):
    # p1: 2건(수량 2,4, 수취 0) → 평균수량 3 → logistics = (1900-0)/3 = 633.33
    db.add(Order(channel_id=6, platform_product_id="p1", selling_price=Decimal("3000"), quantity=2,
                 order_date=date(2026, 7, 1), order_number="o1"))
    db.add(Order(channel_id=6, platform_product_id="p1", selling_price=Decimal("3000"), quantity=4,
                 order_date=date(2026, 7, 2), order_number="o2"))
    db.commit()
    out = bep_calculator._avg_qty_and_logistics(db)
    assert out["p1"]["avg_qty"] == Decimal("3")
    assert out["p1"]["shipping"] == Decimal("1900")
    assert out["p1"]["collected"] == Decimal("0")
    assert out["p1"]["net_ship"] == Decimal("1900")
    assert out["p1"]["logistics"] == Decimal("633.33")


def test_avg_logistics_nets_collected_shipping(db):
    """리뷰 P2-1: 고객 수취 배송비(Order.shipping_cost)를 차감한 순배송원가.
    p1: 2건(수취 3000, 0) → 평균수취 1500 → net = 1900-1500 = 400, 수량 평균 1 → logistics 400."""
    db.add(Order(channel_id=6, platform_product_id="p1", selling_price=Decimal("3000"), quantity=1,
                 shipping_cost=Decimal("3000"), order_date=date(2026, 7, 1), order_number="o1"))
    db.add(Order(channel_id=6, platform_product_id="p1", selling_price=Decimal("3000"), quantity=1,
                 shipping_cost=None, order_date=date(2026, 7, 2), order_number="o2"))  # 무료배송=수취0
    db.commit()
    out = bep_calculator._avg_qty_and_logistics(db)
    assert out["p1"]["collected"] == Decimal("1500")
    assert out["p1"]["net_ship"] == Decimal("400")
    assert out["p1"]["logistics"] == Decimal("400.00")


def test_avg_logistics_clamps_net_at_zero(db):
    """리뷰 P2-1 보수 클램프: 수취가 지불(1900)을 초과해도 배송 마진을 이익으로 잡지 않는다."""
    db.add(Order(channel_id=6, platform_product_id="p1", selling_price=Decimal("3000"), quantity=1,
                 shipping_cost=Decimal("5000"), order_date=date(2026, 7, 1), order_number="o1"))
    db.commit()
    out = bep_calculator._avg_qty_and_logistics(db)
    assert out["p1"]["collected"] == Decimal("5000")
    assert out["p1"]["net_ship"] == Decimal("0")   # max(0, 1900-5000)
    assert out["p1"]["logistics"] == Decimal("0.00")


def _bep_fixture(db, *, cost="5000", price="10000", qty=1):
    pm = ProductMaster(internal_sku="SKU-17E", product_name="17E", cost_price=Decimal(cost))
    db.add(pm)
    db.flush()
    db.add(ProductChannelMapping(product_id=pm.id, channel_id=6, channel_product_id="13365319468",
                                 channel_product_name="17E", selling_price=Decimal("0"), is_active=True))
    db.add(Order(channel_id=6, platform_product_id="13365319468", selling_price=Decimal(price),
                 quantity=qty, order_date=kst_today() - timedelta(days=1), order_number="o1"))
    db.commit()
    return pm


def test_calculate_bep_uses_logistics_and_vat(db):
    _bep_fixture(db, cost="5000", price="10000", qty=1)  # 주문 1건 수량1 → logistics=1900
    res = bep_calculator.calculate_bep(db)  # 정산 없음 → 수수료율 채널/상수 폴백, basis=blended
    assert res["commission_basis"] == "blended"
    row = db.query(NaverProductBep).filter(NaverProductBep.channel_product_id == "13365319468").one()
    assert row.logistics_cost == Decimal("1900.00")
    assert row.commission_basis == "blended"
    # VAT 정합: contribution = (sp - sp*rate - cost - logistics)/1.1
    sp, cost, logi = Decimal("10000"), Decimal("5000"), Decimal("1900")
    rate = row.commission_rate
    expected = ((sp - sp * rate - cost - logi) / Decimal("1.1")).quantize(Decimal("0.01"))
    assert row.contribution_margin == expected
    assert row.bep_roas == (sp / expected).quantize(Decimal("0.0001"))


def test_calculate_bep_ad_case_basis_when_settlement_present(db):
    _bep_fixture(db, cost="5000", price="10000", qty=1)
    _daily_blend(db, "38400", "-1600")
    for i in range(20):
        _case(db, i, 1000, -30, -20)
    for i in range(20, 40):
        _case(db, i, 1000, -30, 0)
    db.commit()
    res = bep_calculator.calculate_bep(db)
    assert res["commission_basis"] == "ad_case"
    assert res["commission_rate"] == pytest.approx(0.05)
    row = db.query(NaverProductBep).filter(NaverProductBep.channel_product_id == "13365319468").one()
    assert row.commission_basis == "ad_case"
    assert row.commission_rate == Decimal("0.0500")


def test_calculate_bep_hand_computed_constants(db):
    """리뷰 P3-1 거울 방지: 프로덕션 수식 재구현이 아닌 **손계산 하드코딩 기대값** 대조.

    입력: sp=11,000 / rate=0.05(채널 5%) / cost=2,200 / 지불배송 1,900·수취 800 → net_ship=1,100 / 수량 1
    손계산: contribution = (11000 − 550 − 2200 − 1100) / 1.1 = 7150/1.1 = 6500.00
            bep = 11000/6500 = 1.6923(4자리), target = 1.6923×1.15 = 1.946145 → 1.9461(4자리)
    """
    db.add(Channel(id=6, name="네이버", code="NAVER", platform="naver", commission_rate=Decimal("5.0")))
    pm = ProductMaster(internal_sku="SKU-HC", product_name="손계산", cost_price=Decimal("2200"))
    db.add(pm)
    db.flush()
    db.add(ProductChannelMapping(product_id=pm.id, channel_id=6, channel_product_id="hc-1",
                                 channel_product_name="손계산", selling_price=Decimal("0"), is_active=True))
    db.add(Order(channel_id=6, platform_product_id="hc-1", selling_price=Decimal("11000"), quantity=1,
                 shipping_cost=Decimal("800"), order_date=kst_today() - timedelta(days=1), order_number="ohc"))
    db.commit()

    bep_calculator.calculate_bep(db)
    row = db.query(NaverProductBep).filter(NaverProductBep.channel_product_id == "hc-1").one()
    assert row.commission_rate == Decimal("0.0500")
    assert row.logistics_cost == Decimal("1100.00")
    assert row.contribution_margin == Decimal("6500.00")
    assert row.bep_roas == Decimal("1.6923")
    assert row.target_roas == Decimal("1.9461")


def test_calculate_bep_no_orders_product_gets_full_shipping(db):
    # 주문 없는 상품(단가 폴백 0 → has_cost False) — logistics는 전액(1900) 폴백
    pm = ProductMaster(internal_sku="SKU-X", product_name="X", cost_price=Decimal("3000"))
    db.add(pm)
    db.flush()
    db.add(ProductChannelMapping(product_id=pm.id, channel_id=6, channel_product_id="noorder",
                                 channel_product_name="X", selling_price=Decimal("0"), is_active=True))
    db.commit()
    bep_calculator.calculate_bep(db)
    row = db.query(NaverProductBep).filter(NaverProductBep.channel_product_id == "noorder").one()
    assert row.logistics_cost == Decimal("1900.00")
    assert row.has_cost is False  # 단가 없음
