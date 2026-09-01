# test_naver_ad_d_nao_57.py — D-NAO-57 상품별 원가 구조 기반 BEP·타겟 정밀화
# 커버: (A) shopping_ad_product_sync 수집·매핑 + campaign_target_resolver 우선순위 ②(상품 파생,
#   가중/폴백/override불변) / (B) ad_commission_rate 분해·언디루션·가드·calculate_bep 폴백 표기 /
#   (C) 배송비 단가당 환산·평균수량·폴백·VAT 정합·config 상수.
from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    Channel, NaverAdDaily, NaverAdgroupProduct, NaverCampaignSettings, NaverEntity,
    NaverProductBep, NaverProductMetaCurrent, NaverSettlementCase, NaverSettlementDaily, Order,
    ProductChannelMapping, ProductMaster,
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


def test_collect_scopes_shopping_active_adgroups_regardless_of_optimizer(db):
    """★2026-07-30 사고 회귀: 스코프는 optimizer가 아니라 **관측 스코프**다.

    구 계약은 `optimizer='ours'`만 대상으로 삼았고, 긴급정지(D-NAO-132)로 전 캠페인이
    optimizer='none'이 되자 수집이 통째로 죽었다. 이제 settings 행이 있으면(값 무관)
    대상이고, 모듈 고유 필터(SHOPPING · status='on')만 남는다.
    """
    db.add(NaverCampaignSettings(campaign_id="c-none", optimizer="none"))   # 정지 중 — 그래도 대상
    db.add(NaverCampaignSettings(campaign_id="c-mop", optimizer="mop"))     # 대행사 — 그래도 대상
    db.add(NaverCampaignSettings(campaign_id="c-web", optimizer="ours"))    # 파워링크 → 그룹이 제외
    db.add_all([
        NaverEntity(entity_type="adgroup", entity_id="g-none", campaign_id="c-none", campaign_type="SHOPPING", status="on"),
        NaverEntity(entity_type="adgroup", entity_id="g-mop", campaign_id="c-mop", campaign_type="SHOPPING", status="on"),
        NaverEntity(entity_type="adgroup", entity_id="g-web", campaign_id="c-web", campaign_type="WEB_SITE", status="on"),
        NaverEntity(entity_type="adgroup", entity_id="g-off", campaign_id="c-none", campaign_type="SHOPPING", status="off"),
        # 스코프 밖(설정 행 없음·최근 광고비 없음) → 제외
        NaverEntity(entity_type="adgroup", entity_id="g-out", campaign_id="c-out", campaign_type="SHOPPING", status="on"),
    ])
    db.commit()
    ags = shopping_ad_product_sync._observed_shopping_adgroups(db, as_of=kst_today())
    assert {a.entity_id for a in ags} == {"g-none", "g-mop"}


def test_collect_scope_includes_recent_spender_without_settings(db):
    """설정 행이 없어도 **최근 7일 광고비>0**이면 관측한다(사고 증상 '01·03이 돈을 쓰는데
    우리가 못 본다'의 직접 회귀)."""
    db.add(NaverEntity(entity_type="adgroup", entity_id="g-spend", campaign_id="c-spend",
                       campaign_type="SHOPPING", status="on"))
    db.add(NaverAdDaily(ad_date=kst_today(), campaign_id="c-spend", adgroup_id="g-spend",
                        keyword_id="", campaign_type="SHOPPING", cost=1234, clk=1, imp=10))
    db.commit()
    ags = shopping_ad_product_sync._observed_shopping_adgroups(db, as_of=kst_today())
    assert {a.entity_id for a in ags} == {"g-spend"}


def test_sync_adgroup_products_upsert_and_dedup(db):
    _ours_shopping_adgroup(db)
    ads = {"grp-1": [
        {"mall_product_id": "13365319468", "product_name": "17E"},
        {"mall_product_id": "13365319468", "product_name": "dup"},  # 같은 상품 중복 소재 → dedup
        {"mall_product_id": "999", "product_name": "other"},
    ]}
    res = shopping_ad_product_sync.sync_adgroup_products(db, as_of=kst_today(), ads_by_adgroup=ads)
    assert res == {"adgroups": 1, "mappings": 2, "products": 2,
                   "inserted": 2, "updated": 0,
                   "failed_adgroups": 0, "external_ad_changes": 0,  # D-NAO-127 additive
                   # 2026-07-30 사고 수정 + codex 1R·2R 대응 additive(수집 신뢰도 표면화)
                   "observation_blind": False, "truncated": False, "cursor": None,
                   "elapsed_s": res["elapsed_s"]}
    rows = db.query(NaverAdgroupProduct).filter(NaverAdgroupProduct.adgroup_id == "grp-1").all()
    assert {r.mall_product_id for r in rows} == {"13365319468", "999"}
    assert all(r.campaign_id == "cmp-shop" for r in rows)

    # 재실행 — 관측된 상품은 **갱신**된다(멱등: 행이 늘지 않는다).
    # ★계약 변경(codex 2R): 이 잡은 삭제하지 않으므로 이번에 안 보인 "999"는 남는다.
    #   stale 판별은 synced_at 신선도가 담당한다(모듈 docstring의 의도된 트레이드오프).
    res2 = shopping_ad_product_sync.sync_adgroup_products(
        db, as_of=kst_today(),
        ads_by_adgroup={"grp-1": [{"mall_product_id": "13365319468", "product_name": "17E"}]},
    )
    assert res2 == {**res2, "inserted": 0, "updated": 1}
    rows2 = db.query(NaverAdgroupProduct).filter(NaverAdgroupProduct.adgroup_id == "grp-1").all()
    assert {r.mall_product_id for r in rows2} == {"13365319468", "999"}
    fresh = {r.mall_product_id: r.synced_at for r in rows2}
    assert fresh["13365319468"] > fresh["999"], "관측된 행만 신선도가 올라간다"


def test_sync_never_deletes_rows_of_campaign_outside_scope(db):
    """★계약(codex 2R): 스코프를 벗어난 캠페인의 매핑도 **지우지 않는다**.

    구 계약(1R까지)은 '관측 스코프 이탈 = 삭제'였다. 그 판정은 "정말 이탈했다"와 "수집 축이
    침묵한다"를 구분할 수 없어 삭제 계층을 통째로 들어냈다(모듈 docstring 참조).
    """
    _ours_shopping_adgroup(db)  # cmp-shop
    db.add(NaverAdDaily(ad_date=kst_today(), campaign_id="cmp-shop", adgroup_id="grp-1",
                        keyword_id="", campaign_type="SHOPPING", cost=500, clk=1, imp=10))
    # cmp-left: settings 없음 · 최근 광고비 없음 → 스코프 밖. 그래도 보존된다.
    db.add(NaverAdgroupProduct(adgroup_id="grp-old", campaign_id="cmp-left", mall_product_id="111"))
    db.commit()
    shopping_ad_product_sync.sync_adgroup_products(
        db, as_of=kst_today(), ads_by_adgroup={"grp-1": []},
    )
    assert db.query(NaverAdgroupProduct).filter(
        NaverAdgroupProduct.campaign_id == "cmp-left").count() == 1


def test_sync_never_deletes_stale_adgroup_rows(db):
    """★계약(codex 2R): 활성 엔티티에 없는 그룹(삭제/off)의 행도 지우지 않는다."""
    _ours_shopping_adgroup(db)  # 활성 그룹 = grp-1만
    db.add(NaverAdgroupProduct(adgroup_id="grp-gone", campaign_id="cmp-shop", mall_product_id="222"))
    db.commit()
    shopping_ad_product_sync.sync_adgroup_products(
        db, as_of=kst_today(),
        ads_by_adgroup={"grp-1": [{"mall_product_id": "333", "product_name": "x"}]},
    )
    assert db.query(NaverAdgroupProduct).filter(
        NaverAdgroupProduct.adgroup_id == "grp-gone").count() == 1
    assert db.query(NaverAdgroupProduct).filter(
        NaverAdgroupProduct.adgroup_id == "grp-1").count() == 1


def test_sync_preserves_mappings_when_fetch_fails(db, monkeypatch):
    """get_ads 실패 그룹은 그 그룹만 skip하고 기존 매핑은 그대로 남는다(fail-open)."""
    _ours_shopping_adgroup(db)  # grp-1 활성
    db.add(NaverEntity(entity_type="adgroup", entity_id="grp-2", parent_id="cmp-shop",
                       campaign_id="cmp-shop", campaign_type="SHOPPING", status="on"))
    db.add(NaverAdgroupProduct(adgroup_id="grp-2", campaign_id="cmp-shop", mall_product_id="444"))
    db.add(NaverAdgroupProduct(adgroup_id="grp-gone", campaign_id="cmp-shop", mall_product_id="555"))
    db.commit()

    def fake_get_ads(aid):
        if aid == "grp-2":
            raise RuntimeError("일시 API 장애")
        return [{"mall_product_id": "333", "product_name": "x", "adgroup_id": aid}]

    monkeypatch.setattr(shopping_ad_product_sync, "get_ads", fake_get_ads)
    monkeypatch.setattr(shopping_ad_product_sync, "_MIN_CALL_INTERVAL_S", 0)
    res = shopping_ad_product_sync.sync_adgroup_products(db, as_of=kst_today())  # 실 경로
    assert res["failed_adgroups"] == 1
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
    assert bep_calculator.SHIPPING_COST_NBAESONG == Decimal("3377")
    # order_row 없음 → 일반배송 폴백(fail-safe)
    assert bep_calculator._order_shipping_cost(None) == Decimal("1900")


def _entry_raw(delivery_attr=None, *, extra_po=None) -> str:
    """네이버 주문 entry raw_data JSON 문자열(prod id 11929 실측 구조 미러).

    저장 형태는 json.dumps(entry) — entry = {"productOrder": {...}, "order": {...}}.
    """
    po: dict = {"productOrderId": "po-1", "quantity": 1}
    if delivery_attr is not None:
        po["deliveryAttributeType"] = delivery_attr
    if extra_po:
        po.update(extra_po)
    return json.dumps({"productOrder": po, "order": {"orderId": "o-1"}}, ensure_ascii=False)


def test_order_shipping_cost_nbaesong_when_arrival_guarantee():
    # D-NAO-84 실측: productOrder.deliveryAttributeType == "ARRIVAL_GUARANTEE" → N배송 3,377
    row = {"raw_data": _entry_raw("ARRIVAL_GUARANTEE", extra_po={
        "logisticsCompanyId": "PG", "deliveryTagType": "TOMORROW"})}
    assert bep_calculator._order_shipping_cost(row) == Decimal("3377")


def test_order_shipping_cost_normal_when_today():
    # 과거 전 건 "TODAY" → 일반배송 1,900
    row = {"raw_data": _entry_raw("TODAY")}
    assert bep_calculator._order_shipping_cost(row) == Decimal("1900")


def test_order_shipping_cost_fallbacks_to_normal():
    # raw_data 부재/None/비JSON/키 부재 → 모두 일반배송 폴백(fail-safe)
    assert bep_calculator._order_shipping_cost({"quantity": 1}) == Decimal("1900")          # raw_data 키 없음
    assert bep_calculator._order_shipping_cost({"raw_data": None}) == Decimal("1900")       # None
    assert bep_calculator._order_shipping_cost({"raw_data": "not json{"}) == Decimal("1900")  # 잘림/비JSON
    assert bep_calculator._order_shipping_cost({"raw_data": _entry_raw(None)}) == Decimal("1900")  # 필드 부재
    assert bep_calculator._order_shipping_cost({"raw_data": "[]"}) == Decimal("1900")       # dict 아님
    # dict 형태 raw_data(직접 dict 저장 경로)도 지원
    assert bep_calculator._order_shipping_cost(
        {"raw_data": {"productOrder": {"deliveryAttributeType": "ARRIVAL_GUARANTEE"}}}
    ) == Decimal("3377")


def test_avg_logistics_nbaesong_used_in_computation(db):
    # N배송 주문 1건(수량 1, 수취 0) → 지불 3,377 → net 3,377 → logistics 3,377
    db.add(Order(channel_id=6, platform_product_id="p1", selling_price=Decimal("3000"), quantity=1,
                 raw_data=_entry_raw("ARRIVAL_GUARANTEE"), order_date=date(2026, 7, 1), order_number="o1"))
    db.commit()
    out = bep_calculator._avg_qty_and_logistics(db)
    assert out["p1"]["shipping"] == Decimal("3377")
    assert out["p1"]["net_ship"] == Decimal("3377")
    assert out["p1"]["logistics"] == Decimal("3377.00")


def test_avg_logistics_mixed_shipping_weighted_average(db):
    # 혼재: N배송 1건(3,377) + 일반 1건(TODAY, 1,900) → 지불 평균 (3377+1900)/2 = 2,638.5
    db.add(Order(channel_id=6, platform_product_id="p1", selling_price=Decimal("3000"), quantity=1,
                 raw_data=_entry_raw("ARRIVAL_GUARANTEE"), order_date=date(2026, 7, 1), order_number="o1"))
    db.add(Order(channel_id=6, platform_product_id="p1", selling_price=Decimal("3000"), quantity=1,
                 raw_data=_entry_raw("TODAY"), order_date=date(2026, 7, 2), order_number="o2"))
    db.commit()
    out = bep_calculator._avg_qty_and_logistics(db)
    assert out["p1"]["avg_qty"] == Decimal("1")
    assert out["p1"]["shipping"] == Decimal("2638.5")   # (3377+1900)/2
    assert out["p1"]["collected"] == Decimal("0")
    assert out["p1"]["net_ship"] == Decimal("2638.5")
    assert out["p1"]["logistics"] == Decimal("2638.50")


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


def test_avg_logistics_credits_delivery_margin(db):
    """★D-NAO-283: 수취가 지불을 넘으면 **배송 마진을 이익으로 인정**한다(클램프 폐지).

    종전엔 max(0, 지불−수취)로 이 경우를 0으로 지웠고 그것을 "보수 클램프"라 불렀다.
    그러나 그 「보수」는 실제 구조와 어긋난다 — Jino 원문(2026-08-31): 내일배송은 한진택배에
    1,900원을 내고 고객에게 3,000원을 받아 **"1100원이 남는"** 구조다. 클램프는 부호가 다른
    두 배송방식 중 **이익 쪽만 골라 0으로** 만들어, 한 방향으로만 틀렸다.
    여기 수취 5,000은 그 구조의 과장판이다: net = 1,900 − 5,000 = **−3,100**."""
    db.add(Order(channel_id=6, platform_product_id="p1", selling_price=Decimal("3000"), quantity=1,
                 shipping_cost=Decimal("5000"), order_date=date(2026, 7, 1), order_number="o1"))
    db.commit()
    out = bep_calculator._avg_qty_and_logistics(db)
    assert out["p1"]["collected"] == Decimal("5000")
    assert out["p1"]["net_ship"] == Decimal("-3100")   # 클램프가 되살아나면 0이 되어 여기서 죽는다
    assert out["p1"]["logistics"] == Decimal("-3100.00")
    assert out["p1"]["basis"] == "orders"


def test_avg_logistics_matches_jino_tomorrow_delivery_structure(db):
    """★Jino가 말한 구조 그대로가 숫자로 나오는가 — 내일배송 한 건당 +1,100원이 남는다.

    지불 1,900(한진) · 수취 3,000(고객) ⇒ net_ship = −1,100. 수량 1이므로 logistics도 −1,100.
    이 테스트가 «계약의 원문»이다 — 상수가 바뀌어도 「1,100원이 남는다」는 관계가 남아야 한다."""
    for i in range(3):
        db.add(Order(channel_id=6, platform_product_id="tmr", selling_price=Decimal("10000"),
                     quantity=1, shipping_cost=Decimal("3000"),
                     order_date=date(2026, 7, 1 + i), order_number=f"tmr{i}"))
    db.commit()
    out = bep_calculator._avg_qty_and_logistics(db)["tmr"]
    assert out["nb_share"] == Decimal("0")                          # 전부 일반(내일)배송
    assert out["shipping"] == bep_calculator.SHIPPING_COST_NORMAL   # 1,900 지불
    assert out["collected"] == Decimal("3000")                      # 3,000 수취
    assert out["net_ship"] == Decimal("-1100")                      # ★"1100원이 남는 거야"
    assert out["logistics"] == Decimal("-1100.00")


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


# ── D-NAO-95: 주문 이력 0건 신규 상품의 판매가 폴백(매핑 selling_price) ──

def test_calculate_bep_no_orders_falls_back_to_mapping_price(db):
    """주문 0건이어도 매핑에 판매가가 적혀 있으면 그 값으로 BEP를 산출한다(D-NAO-95).

    신규 상품 온보딩 경로 — 종전엔 sp=0 → has_cost=False → bep_roas=NULL로 남아
    상한 산출이 계정 평균 BEP로 내려앉았다. 손계산 대조(거울 방지):
      sp=15,800 / rate=0.05(채널 5%) / cost=4,300 / logistics=1,900(주문 없음 → 전액 폴백)
      contribution = (15800 − 790 − 4300 − 1900)/1.1 = 8810/1.1 = 8009.09...→ 8009.09
      bep = 15800/8009.09 = 1.97276... → 1.9728
    """
    db.add(Channel(id=6, name="네이버", code="NAVER", platform="naver", commission_rate=Decimal("5.0")))
    pm = ProductMaster(internal_sku="SKU-NEW8", product_name="폴드8", cost_price=Decimal("4300"))
    db.add(pm)
    db.flush()
    db.add(ProductChannelMapping(product_id=pm.id, channel_id=6, channel_product_id="new8",
                                 channel_product_name="폴드8", selling_price=Decimal("15800"),
                                 is_active=True))
    db.commit()
    res = bep_calculator.calculate_bep(db)
    row = db.query(NaverProductBep).filter(NaverProductBep.channel_product_id == "new8").one()
    assert row.selling_price == Decimal("15800.00")
    assert row.logistics_cost == Decimal("1900.00")  # 주문 없음 → 배송비 전액(보수)
    assert row.has_cost is True
    assert row.contribution_margin == Decimal("8009.09")
    assert row.bep_roas == Decimal("1.9728")
    assert res["mapped_price_rows"] == 1


def test_calculate_bep_orders_win_over_mapping_price(db):
    """주문 실거래가가 있으면 매핑 판매가는 무시된다(폴백은 신규 상품 전용, 스스로 은퇴)."""
    pm = ProductMaster(internal_sku="SKU-BOTH", product_name="both", cost_price=Decimal("5000"))
    db.add(pm)
    db.flush()
    db.add(ProductChannelMapping(product_id=pm.id, channel_id=6, channel_product_id="both1",
                                 channel_product_name="both", selling_price=Decimal("99000"),
                                 is_active=True))
    db.add(Order(channel_id=6, platform_product_id="both1", selling_price=Decimal("10000"),
                 quantity=1, order_date=kst_today() - timedelta(days=1), order_number="ob1"))
    db.commit()
    res = bep_calculator.calculate_bep(db)
    row = db.query(NaverProductBep).filter(NaverProductBep.channel_product_id == "both1").one()
    assert row.selling_price == Decimal("10000.00")  # 매핑 99,000이 아니라 주문 실거래가
    assert res["mapped_price_rows"] == 0


def test_calculate_bep_duplicate_mapping_prefers_priced_row(db):
    """같은 cpid에 중복 매핑이 있고 원가가 동률이면 **판매가가 적힌 행**이 이긴다.

    리뷰 P2: 종전 타이브레이크는 product_id 최솟값이라, 사람이 판매가를 적어 넣어도 값이 없는
    쪽 행이 이기면 조용히 무시됐다(신규 상품 온보딩이 이유 없이 실패). product_id가 더 큰 쪽에
    판매가를 넣어 "최솟값 우선"이었다면 실패하도록 배치한다.
    """
    pm_a = ProductMaster(internal_sku="SKU-DUP-A", product_name="dupA", cost_price=Decimal("4300"))
    pm_b = ProductMaster(internal_sku="SKU-DUP-B", product_name="dupB", cost_price=Decimal("4300"))
    db.add_all([pm_a, pm_b])
    db.flush()
    lo, hi = sorted([pm_a.id, pm_b.id])
    # 판매가는 product_id가 큰 쪽(hi)에만 — 최솟값 우선이면 0이 이겨 has_cost False가 된다.
    db.add(ProductChannelMapping(product_id=lo, channel_id=6, channel_product_id="dup1",
                                 channel_product_name="dup", selling_price=Decimal("0"),
                                 is_active=True))
    db.add(ProductChannelMapping(product_id=hi, channel_id=6, channel_product_id="dup1",
                                 channel_product_name="dup", selling_price=Decimal("15800"),
                                 is_active=True))
    db.commit()
    res = bep_calculator.calculate_bep(db)
    row = db.query(NaverProductBep).filter(NaverProductBep.channel_product_id == "dup1").one()
    assert row.selling_price == Decimal("15800.00")
    assert row.has_cost is True
    assert res["mapped_price_rows"] == 1
    assert res["mapped_price_with_bep"] == 1


def test_calculate_bep_mapped_price_without_cost_counts_separately(db):
    """판매가만 있고 원가가 없으면 BEP는 안 나온다 — 두 카운터가 그 차이를 드러낸다(리뷰 P3)."""
    pm = ProductMaster(internal_sku="SKU-NOCOST", product_name="nocost", cost_price=Decimal("0"))
    db.add(pm)
    db.flush()
    db.add(ProductChannelMapping(product_id=pm.id, channel_id=6, channel_product_id="nocost1",
                                 channel_product_name="nocost", selling_price=Decimal("15800"),
                                 is_active=True))
    db.commit()
    res = bep_calculator.calculate_bep(db)
    row = db.query(NaverProductBep).filter(NaverProductBep.channel_product_id == "nocost1").one()
    assert row.has_cost is False
    assert row.bep_roas is None
    assert res["mapped_price_rows"] == 1        # 판매가는 매핑에서 왔지만
    assert res["mapped_price_with_bep"] == 0    # BEP까지 간 건 0 — "3개 넣었는데 왜 0개?"가 보인다


# ── D-NAO-168 (2026-08-10): 수취 배송비도 «지불과 같은 표본»에서 뽑는다 ──────────
# 라이브 실사고: N배송은 2026-07-22 시작인데 지불은 최근 10건(N배송 반영), 수취는 120일
# 평균(무료였던 과거 포함)이라 순배송원가가 **한 방향으로** 과대해졌다. 실측 상품에서
# 377원이어야 할 자리에 2,782원(7배)이 들어갔고, 물류비 과대 → 공헌이익 과소 → **BEP 과대**
# → 벌 수 있는 광고를 끈다. 계정 매출가중 BEP 1.836 → 1.710.


def test_collected_uses_recent_sample_not_wide_window(db):
    """★레짐 전환 재현 — 과거 11건은 무료, 최근 10건은 3,000원 수취.

    넓은 창이면 수취 평균이 희석돼 순배송원가가 부풀고, 최근 표본이면 실제와 맞는다.
    이 테스트가 «수취를 wide로 되돌리는» 변이를 잡는다(2026-08-10 변이 주입에서 생존했던 것)."""
    # 과거 11건: 일반배송·무료(수취 0)
    for i in range(11):
        db.add(Order(channel_id=6, platform_product_id="p1", selling_price=Decimal("10000"),
                     quantity=1, shipping_cost=Decimal("0"), raw_data=_entry_raw("TODAY"),
                     order_date=date(2026, 6, 1 + i), order_number=f"old{i}"))
    # 최근 10건: N배송·3,000원 수취
    for i in range(10):
        db.add(Order(channel_id=6, platform_product_id="p1", selling_price=Decimal("10000"),
                     quantity=1, shipping_cost=Decimal("3000"),
                     raw_data=_entry_raw("ARRIVAL_GUARANTEE"),
                     order_date=date(2026, 7, 20 + i), order_number=f"new{i}"))
    db.commit()
    out = bep_calculator._avg_qty_and_logistics(db)["p1"]

    assert out["nb_share"] == Decimal("1")            # 최근 10건이 전부 N배송
    assert out["shipping"] == Decimal("3377")         # 지불도 N배송 단가
    # ★핵심: 수취가 최근 표본(3,000)이지 21건 평균(약 1,428)이 아니다.
    assert out["collected"] == Decimal("3000")
    assert out["net_ship"] == Decimal("377")          # 3,377 − 3,000
    # 넓은 창이었다면 net_ship이 약 1,949원(=3,377−1,428)으로 5배 부풀었다.
    assert out["net_ship"] < Decimal("500")


def test_avg_qty_still_uses_wide_window(db):
    """★평균 수량은 넓은 창을 유지한다 — 그건 레짐이 아니라 진짜 표본 민감 항목이다.

    수취를 최근 표본으로 옮기면서 수량까지 같이 옮기지 않았음을 고정한다."""
    # 과거 10건 수량 5 · 최근 10건 수량 1 → 넓은 창 평균 3, 최근 표본만이면 1
    for i in range(10):
        db.add(Order(channel_id=6, platform_product_id="p1", selling_price=Decimal("10000"),
                     quantity=5, shipping_cost=Decimal("0"), order_date=date(2026, 6, 1 + i),
                     order_number=f"q_old{i}"))
    for i in range(10):
        db.add(Order(channel_id=6, platform_product_id="p1", selling_price=Decimal("10000"),
                     quantity=1, shipping_cost=Decimal("0"), order_date=date(2026, 7, 20 + i),
                     order_number=f"q_new{i}"))
    db.commit()
    out = bep_calculator._avg_qty_and_logistics(db)["p1"]
    assert out["avg_qty"] == Decimal("3")   # (5×10 + 1×10)/20 — 넓은 창


def test_net_ship_is_negative_when_collected_exceeds_paid(db):
    """★D-NAO-283 — 수취가 지불을 넘으면 물류비가 **음수**가 된다(클램프 되살리는 변이를 잡는다).

    이 테스트는 원래 반대 방향이었다(`test_net_ship_clamped_at_zero_...`, 2026-08-10 변이 주입에서
    클램프를 지키기 위해 세운 것). 계약이 뒤집혔으므로 **지키는 방향도 뒤집는다** — 테스트를
    지우기만 하면 다음 사람이 「보수적으로」 클램프를 되살려도 아무도 안 잡는다.
    여기서 −3,100이 0이 되면 그 순간 죽는다."""
    for i in range(10):
        db.add(Order(channel_id=6, platform_product_id="p1", selling_price=Decimal("10000"),
                     quantity=1, shipping_cost=Decimal("5000"),      # 지불 1,900보다 큼
                     raw_data=_entry_raw("TODAY"), order_date=date(2026, 7, 1 + i),
                     order_number=f"rich{i}"))
    db.commit()
    out = bep_calculator._avg_qty_and_logistics(db)["p1"]
    assert out["shipping"] == Decimal("1900")
    assert out["collected"] == Decimal("5000")
    assert out["net_ship"] == Decimal("-3100")   # 0이 아니라 −3,100
    assert out["logistics"] == Decimal("-3100.00")
    assert out["logistics"] < 0                  # 배송 마진이 이익으로 인정된다


# ══════════════════════════════════════════════════════════════════════════════
# D-NAO-283 — 배송비 자(尺) 정합: ⓑ형제 실측 폴백 · ⓒ판매가 폴백 ③(메타 할인적용가)
# 계약 docs/contracts/CONTRACT_shipping_yardstick.md
# ══════════════════════════════════════════════════════════════════════════════

def _meta(cpno, *, group=None, discounted=None, sale=None):
    return NaverProductMetaCurrent(
        channel_product_no=cpno, group_product_no=group,
        discounted_price=discounted, sale_price=sale,
    )


def _mapped(db, cpid, *, cost="3000", mapping_price="0", name="X"):
    pm = ProductMaster(internal_sku=f"SKU-{cpid}", product_name=name, cost_price=Decimal(cost))
    db.add(pm)
    db.flush()
    db.add(ProductChannelMapping(product_id=pm.id, channel_id=6, channel_product_id=cpid,
                                 channel_product_name=name,
                                 selling_price=Decimal(mapping_price), is_active=True))
    return pm


def test_sibling_logistics_used_when_product_has_no_orders(db):
    """★ⓑ 주문 0건 상품은 같은 group_product_no 형제의 실측 배송 구성을 물려받는다.

    종전엔 「수취 0 · 수량 1」 가정으로 1,900원 **전액**이 잡혔다 — 형제가 실제로는 3,000원을
    받고 있는데도. 라이브에서 그 차이가 BEP 약 14% 과대였다(폴드7 2.0366 vs 형제 기준 1.7838).
    형제 3건: 지불 1,900(일반) · 수취 3,000 ⇒ net −1,100, 수량 1 ⇒ logistics −1,100."""
    for i in range(3):
        db.add(Order(channel_id=6, platform_product_id="bro", selling_price=Decimal("10000"),
                     quantity=1, shipping_cost=Decimal("3000"),
                     order_date=kst_today() - timedelta(days=i + 1), order_number=f"b{i}"))
    db.add(_meta("bro", group="G1"))
    db.add(_meta("newbie", group="G1"))       # 주문 0건 형제
    db.commit()

    out = bep_calculator.logistics_by_product(db)
    assert out["bro"]["basis"] == "orders"
    assert out["newbie"]["basis"] == "sibling"
    assert out["newbie"]["logistics"] == Decimal("-1100.00")   # 형제 실측 그대로
    assert out["newbie"]["collected"] == Decimal("3000")


def test_own_orders_beat_siblings(db):
    """★자기 주문이 있으면 형제는 쓰지 않는다 — 형제는 «모를 때»의 답이지 «더 나은» 답이 아니다."""
    db.add(Order(channel_id=6, platform_product_id="bro", selling_price=Decimal("10000"),
                 quantity=1, shipping_cost=Decimal("3000"),
                 order_date=kst_today() - timedelta(days=1), order_number="b1"))
    db.add(Order(channel_id=6, platform_product_id="self", selling_price=Decimal("10000"),
                 quantity=1, shipping_cost=Decimal("0"),      # 자기는 무료배송(수취 0)
                 order_date=kst_today() - timedelta(days=1), order_number="s1"))
    db.add(_meta("bro", group="G1"))
    db.add(_meta("self", group="G1"))
    db.commit()

    out = bep_calculator.logistics_by_product(db)
    assert out["self"]["basis"] == "orders"
    assert out["self"]["logistics"] == Decimal("1900.00")   # 형제의 −1,100을 물려받지 않는다


def test_no_sibling_falls_back_to_default_and_says_so(db):
    """★형제가 없으면 현행 폴백을 유지한다(계약 §2-3) — 그리고 그것이 «모름»이라고 말한다."""
    db.add(_meta("lonely", group="G9"))   # 그룹은 있으나 주문 있는 형제가 없다
    db.add(_meta("groupless"))            # 그룹 자체가 없다
    db.commit()

    out = bep_calculator.logistics_by_product(db)
    for cpid in ("lonely", "groupless"):
        assert out[cpid]["basis"] == "default"
        assert out[cpid]["logistics"] == bep_calculator.SHIPPING_COST_NORMAL
        assert out[cpid]["orders"] == 0


def test_meta_discounted_price_is_third_price_fallback(db):
    """★ⓒ orders도 mapping도 없으면 커머스API 할인적용가로 BEP를 산출한다.

    손계산 대조(거울 방지): sp=19,900(meta) / rate=0.05(채널 5%) / cost=6,406 /
      logistics=1,900(형제 없음 → 기본 폴백)
      commission = 19,900 × 0.05 = 995
      contribution = (19900 − 995 − 6406 − 1900)/1.1 = 10599/1.1 = 9635.4545… → 9635.45
      bep = 19900 / 9635.4545… = 2.06531… → 2.0653"""
    db.add(Channel(id=6, name="네이버", code="NAVER", platform="naver", commission_rate=Decimal("5.0")))
    _mapped(db, "13687558209", cost="6406", mapping_price="0", name="4매입 폴드")
    db.add(_meta("13687558209", group="52308509", discounted=19900, sale=27500))
    db.commit()

    bep_calculator.calculate_bep(db)
    row = db.query(NaverProductBep).filter(
        NaverProductBep.channel_product_id == "13687558209").one()
    assert row.selling_price == Decimal("19900.00")
    assert row.price_basis == "meta"
    assert row.logistics_basis == "default"
    assert row.has_cost is True
    assert row.contribution_margin == Decimal("9635.45")
    assert row.bep_roas == Decimal("2.0653")


def test_sale_price_is_never_used_as_selling_price(db):
    """★금지선(계약 §3): 정가(sale_price)를 판매가로 쓰지 않는다.

    그룹 52308509에서 정가 27,500 vs 할인적용가 19,900은 **38% 차이**다. 정가를 쓰면 상한이
    그만큼 부풀어 과지출 방향으로 틀어진다. 할인적용가가 없으면 값을 만들지 않는다."""
    db.add(Channel(id=6, name="네이버", code="NAVER", platform="naver", commission_rate=Decimal("5.0")))
    _mapped(db, "onlysale", cost="6406", mapping_price="0")
    db.add(_meta("onlysale", discounted=None, sale=27500))   # 정가만 있다
    db.commit()

    bep_calculator.calculate_bep(db)
    row = db.query(NaverProductBep).filter(NaverProductBep.channel_product_id == "onlysale").one()
    assert row.selling_price == Decimal("0.00")   # 27,500이 새어 들어오면 여기서 죽는다
    assert row.bep_roas is None
    assert row.has_cost is False


def test_price_fallback_priority_orders_then_mapping_then_meta(db):
    """★폴백은 순서대로 은퇴한다 — 실거래 > 사람 입력 > 메타. 셋이 다 있으면 실거래가 이긴다."""
    db.add(Channel(id=6, name="네이버", code="NAVER", platform="naver", commission_rate=Decimal("5.0")))
    _mapped(db, "all3", cost="1000", mapping_price="15000")
    db.add(Order(channel_id=6, platform_product_id="all3", selling_price=Decimal("12000"),
                 quantity=1, order_date=kst_today() - timedelta(days=1), order_number="a1"))
    db.add(_meta("all3", discounted=19900))
    _mapped(db, "map2", cost="1000", mapping_price="15000")   # 주문 없음, 매핑값 있음
    db.add(_meta("map2", discounted=19900))
    db.commit()

    bep_calculator.calculate_bep(db)
    rows = {r.channel_product_id: r for r in db.query(NaverProductBep).all()}
    assert rows["all3"].selling_price == Decimal("12000.00")   # 실거래
    assert rows["all3"].price_basis == "orders"
    assert rows["map2"].selling_price == Decimal("15000.00")   # 사람 입력이 메타를 이긴다
    assert rows["map2"].price_basis == "mapping"


def test_basis_counts_reported_by_calculate_bep(db):
    """★출처 분포가 반환·로그에 실린다 — 화면 밖에서도 「자가 무엇으로 만들어졌나」를 셀 수 있게."""
    db.add(Channel(id=6, name="네이버", code="NAVER", platform="naver", commission_rate=Decimal("5.0")))
    _mapped(db, "o1", cost="1000")
    db.add(Order(channel_id=6, platform_product_id="o1", selling_price=Decimal("12000"),
                 quantity=1, order_date=kst_today() - timedelta(days=1), order_number="x1"))
    _mapped(db, "m1", cost="1000")
    db.add(_meta("m1", discounted=19900))
    db.commit()

    res = bep_calculator.calculate_bep(db)
    assert res["price_basis_counts"] == {"orders": 1, "meta": 1}
    assert res["logistics_basis_counts"] == {"orders": 1, "default": 1}
