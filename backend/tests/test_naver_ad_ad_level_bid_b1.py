# test_naver_ad_ad_level_bid_b1.py — 스프린트 B Phase 1(B1: 소재-레벨 입찰 인식·저장, D-NAO-65)
# 커버: (1) get_ads의 adAttr(JSON 문자열/dict/깨짐/부재) 방어적 파싱 + userLock
#   (2) shopping_ad_product_sync가 소재 입찰 4컬럼을 적재·스냅샷 교체 시 갱신
#   (3) 기존 매핑·상품명 동작 회귀 0(입찰 필드 미주입 시 NULL)
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdgroupProduct, NaverCampaignSettings, NaverEntity
from app.services import naver_sa_ad_fetcher as fetcher
from app.services.naver_ad import shopping_ad_product_sync


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


def _ours_shopping_adgroup(db, campaign_id="cmp-shop", adgroup_id="grp-1"):
    db.add(NaverCampaignSettings(campaign_id=campaign_id, optimizer="ours"))
    db.add(NaverEntity(entity_type="adgroup", entity_id=adgroup_id, parent_id=campaign_id,
                       campaign_id=campaign_id, campaign_type="SHOPPING", status="on"))
    db.commit()


# ══════════════════════════════════════════════════════════════════
# (1) get_ads adAttr 방어적 파싱
# ══════════════════════════════════════════════════════════════════
def _fake_ads_resp(ads):
    class FakeResp:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return ads
    return FakeResp()


def test_get_ads_parses_adattr_json_string(monkeypatch):
    """adAttr가 JSON 문자열이면 bidAmt·useGroupBidAmt 파싱, userLock 반영(B1 공식 스펙)."""
    ads = [{
        "nccAdId": "nad-1", "nccAdgroupId": "grp-1", "type": "SHOPPING_PRODUCT_AD",
        "userLock": False,
        "referenceData": {"mallProductId": "13365319468", "productTitle": "17E"},
        "adAttr": '{"bidAmt": 810, "useGroupBidAmt": false}',
    }]
    monkeypatch.setattr(fetcher, "_get", lambda path, params=None: _fake_ads_resp(ads))
    out = fetcher.get_ads("grp-1")
    assert len(out) == 1
    a = out[0]
    assert a["mall_product_id"] == "13365319468"  # 기존 필드 불변(하위호환)
    assert a["product_name"] == "17E"
    assert a["ad_id"] == "nad-1"
    assert a["ad_bid_amt"] == 810
    assert a["use_group_bid_amt"] is False
    assert a["ad_user_lock"] is False


def test_get_ads_parses_adattr_dict(monkeypatch):
    """adAttr가 이미 dict여도 파싱(문자열/dict 양쪽 방어)."""
    ads = [{
        "nccAdId": "nad-2", "nccAdgroupId": "grp-1", "type": "SHOPPING_PRODUCT_AD",
        "userLock": True,
        "referenceData": {"mallProductId": "999"},
        "adAttr": {"bidAmt": 50, "useGroupBidAmt": True},
    }]
    monkeypatch.setattr(fetcher, "_get", lambda path, params=None: _fake_ads_resp(ads))
    out = fetcher.get_ads("grp-1")
    a = out[0]
    assert a["ad_bid_amt"] == 50
    assert a["use_group_bid_amt"] is True
    assert a["ad_user_lock"] is True


def test_get_ads_broken_adattr_string_yields_none(monkeypatch):
    """비JSON 문자열 → 파싱 실패 시 입찰 필드 None(크래시 아님, 부분응답 견고)."""
    ads = [{
        "nccAdId": "nad-3", "nccAdgroupId": "grp-1", "type": "SHOPPING_PRODUCT_AD",
        "referenceData": {"mallProductId": "111"},
        "adAttr": "not json{",
    }]
    monkeypatch.setattr(fetcher, "_get", lambda path, params=None: _fake_ads_resp(ads))
    a = fetcher.get_ads("grp-1")[0]
    assert a["ad_bid_amt"] is None
    assert a["use_group_bid_amt"] is None
    assert a["ad_user_lock"] is False  # userLock 부재 → 기본 False


def test_get_ads_absent_adattr_yields_none(monkeypatch):
    """adAttr 키 부재 → 입찰 필드 None(비쇼핑 소재/부분응답)."""
    ads = [{
        "nccAdId": "nad-4", "nccAdgroupId": "grp-1", "type": "SHOPPING_PRODUCT_AD",
        "referenceData": {"mallProductId": "222"},
    }]
    monkeypatch.setattr(fetcher, "_get", lambda path, params=None: _fake_ads_resp(ads))
    a = fetcher.get_ads("grp-1")[0]
    assert a["ad_bid_amt"] is None
    assert a["use_group_bid_amt"] is None


def test_get_ads_partial_adattr_fields(monkeypatch):
    """adAttr에 bidAmt만 있고 useGroupBidAmt 부재 → 각 필드 독립 None."""
    ads = [{
        "nccAdId": "nad-5", "nccAdgroupId": "grp-1", "type": "SHOPPING_PRODUCT_AD",
        "referenceData": {"mallProductId": "333"},
        "adAttr": '{"bidAmt": 700}',
    }]
    monkeypatch.setattr(fetcher, "_get", lambda path, params=None: _fake_ads_resp(ads))
    a = fetcher.get_ads("grp-1")[0]
    assert a["ad_bid_amt"] == 700
    assert a["use_group_bid_amt"] is None


# ══════════════════════════════════════════════════════════════════
# (2) sync가 소재 입찰 4컬럼 적재 + 스냅샷 교체 시 갱신
# ══════════════════════════════════════════════════════════════════
def test_sync_stores_ad_bid_columns(db):
    _ours_shopping_adgroup(db)
    ads = {"grp-1": [
        {"mall_product_id": "13365319468", "product_name": "17E", "ad_id": "nad-1",
         "ad_bid_amt": 810, "use_group_bid_amt": False, "ad_user_lock": False},
    ]}
    shopping_ad_product_sync.sync_adgroup_products(db, ads_by_adgroup=ads)
    row = db.query(NaverAdgroupProduct).filter(NaverAdgroupProduct.adgroup_id == "grp-1").one()
    assert row.ad_id == "nad-1"
    assert row.ad_bid_amt == 810
    assert row.use_group_bid_amt is False
    assert row.ad_user_lock is False


def test_sync_snapshot_replace_updates_ad_bid(db):
    """스냅샷 교체 시 소재 입찰도 갱신(그룹입찰 무시하는 소재 bidAmt 변경 추적)."""
    _ours_shopping_adgroup(db)
    shopping_ad_product_sync.sync_adgroup_products(db, ads_by_adgroup={"grp-1": [
        {"mall_product_id": "13365319468", "product_name": "17E", "ad_id": "nad-1",
         "ad_bid_amt": 810, "use_group_bid_amt": False, "ad_user_lock": False},
    ]})
    # 다음 sync: 소재 입찰 상향
    shopping_ad_product_sync.sync_adgroup_products(db, ads_by_adgroup={"grp-1": [
        {"mall_product_id": "13365319468", "product_name": "17E", "ad_id": "nad-1",
         "ad_bid_amt": 1200, "use_group_bid_amt": False, "ad_user_lock": True},
    ]})
    row = db.query(NaverAdgroupProduct).filter(NaverAdgroupProduct.adgroup_id == "grp-1").one()
    assert row.ad_bid_amt == 1200
    assert row.ad_user_lock is True


def test_sync_ad_bid_columns_null_when_absent(db):
    """입찰 필드 미주입(기존 소비자 형식) → NULL 유지(하위호환·회귀 0)."""
    _ours_shopping_adgroup(db)
    ads = {"grp-1": [{"mall_product_id": "13365319468", "product_name": "17E"}]}
    res = shopping_ad_product_sync.sync_adgroup_products(db, ads_by_adgroup=ads)
    # 기존 반환 통계 불변
    assert res == {"adgroups": 1, "mappings": 1, "products": 1, "removed": 0, "failed_adgroups": 0}
    row = db.query(NaverAdgroupProduct).filter(NaverAdgroupProduct.adgroup_id == "grp-1").one()
    assert row.mall_product_id == "13365319468"  # 매핑 회귀 0
    assert row.product_name == "17E"
    assert row.ad_id is None
    assert row.ad_bid_amt is None
    assert row.use_group_bid_amt is None
    assert row.ad_user_lock is None


def test_sync_dedup_keeps_first_ad_bid(db):
    """같은 mall_product_id 중복 소재 dedup 시 첫 소재의 입찰 필드 채택(product_name과 동형)."""
    _ours_shopping_adgroup(db)
    ads = {"grp-1": [
        {"mall_product_id": "13365319468", "product_name": "17E", "ad_id": "nad-1",
         "ad_bid_amt": 810, "use_group_bid_amt": False, "ad_user_lock": False},
        {"mall_product_id": "13365319468", "product_name": "dup", "ad_id": "nad-dup",
         "ad_bid_amt": 999, "use_group_bid_amt": True, "ad_user_lock": True},
    ]}
    shopping_ad_product_sync.sync_adgroup_products(db, ads_by_adgroup=ads)
    row = db.query(NaverAdgroupProduct).filter(NaverAdgroupProduct.adgroup_id == "grp-1").one()
    assert row.ad_id == "nad-1"
    assert row.ad_bid_amt == 810
    assert row.use_group_bid_amt is False
