# test_naver_entity_sync_device_bid_weight.py — D-NAO-218(M2-b2)
# entity_sync.collect_entities/sync_entities가 기기 입찰가중치(pc_bid_weight·mobile_bid_weight)를
# naver_entity에 적재하는지 — 신규 크론 없이 **기존** sync_naver_entity 경로만으로.
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverEntity
from app.services.naver_ad import entity_sync


@pytest.fixture
def db():
    # ★autoflush=False — prod SessionLocal(app/database.py)과 동일 설정. 픽스처가 prod와
    # 다르면 "query→add" 결함을 원리적으로 못 잡는다(계약 스펙 지시 사항 재확인).
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _campaign(cid="cmp-1", name="캠페인1"):
    return {
        "campaign_id": cid, "campaign_type": "SHOPPING", "name": name,
        "status": "ELIGIBLE", "user_lock": False,
    }


def test_collect_entities_carries_device_bid_weight_from_adgroup_dict():
    """①collect_entities가 get_adgroups 산출(ag dict)의 pc/mobile_bid_weight를 그대로 옮긴다."""
    ags = {"cmp-1": [{
        "adgroup_id": "grp-1", "name": "그룹1", "status": "ELIGIBLE", "user_lock": False,
        "bid_amt": 500, "pc_bid_weight": 70, "mobile_bid_weight": 80,
    }]}
    rows = entity_sync.collect_entities(campaigns=[_campaign()], adgroups_by_campaign=ags,
                                         keywords_by_adgroup={})
    ag_row = next(r for r in rows if r["entity_type"] == "adgroup")
    assert ag_row["pc_bid_weight"] == 70
    assert ag_row["mobile_bid_weight"] == 80


def test_collect_entities_campaign_rows_have_no_device_weight_key():
    """campaign 행엔 기기가중치 개념이 없다(그룹 전용 필드) — 키 자체가 없어야 sync_entities의
    .get()이 None을 정직하게 낸다(가짜 100을 만들지 않는다)."""
    ags = {"cmp-1": [{
        "adgroup_id": "grp-1", "name": "그룹1", "status": "ELIGIBLE", "user_lock": False,
        "bid_amt": 500, "pc_bid_weight": 70, "mobile_bid_weight": 80,
    }]}
    rows = entity_sync.collect_entities(campaigns=[_campaign()], adgroups_by_campaign=ags,
                                         keywords_by_adgroup={})
    campaign_row = next(r for r in rows if r["entity_type"] == "campaign")
    assert "pc_bid_weight" not in campaign_row
    assert "mobile_bid_weight" not in campaign_row


def test_sync_entities_insert_persists_device_bid_weight(db):
    """②신규 그룹 insert — 기기가중치가 naver_entity에 그대로 실린다."""
    rows = [{
        "entity_type": "adgroup", "entity_id": "grp-1", "parent_id": "cmp-1",
        "campaign_id": "cmp-1", "campaign_type": "SHOPPING", "name": "그룹1",
        "status": "on", "bid_amt": 500, "pc_bid_weight": 70, "mobile_bid_weight": 80,
    }]
    entity_sync.sync_entities(db, rows=rows)
    e = db.query(NaverEntity).filter_by(entity_type="adgroup", entity_id="grp-1").one()
    assert e.pc_bid_weight == 70
    assert e.mobile_bid_weight == 80


def test_sync_entities_insert_missing_weight_key_stores_null_not_100(db):
    """②NULL과 100을 구분한다는 스펙의 적재 측 전제 — 키가 없으면 DB에도 NULL로 정직하게
    남는다(100을 지어내지 않는다)."""
    rows = [{
        "entity_type": "adgroup", "entity_id": "grp-null", "parent_id": "cmp-1",
        "campaign_id": "cmp-1", "campaign_type": "SHOPPING", "name": "그룹널",
        "status": "on", "bid_amt": 500,
    }]
    entity_sync.sync_entities(db, rows=rows)
    e = db.query(NaverEntity).filter_by(entity_type="adgroup", entity_id="grp-null").one()
    assert e.pc_bid_weight is None
    assert e.mobile_bid_weight is None


def test_sync_entities_update_refreshes_device_bid_weight(db):
    """②기존 그룹 갱신 — 대행사가 콘솔에서 가중치를 바꾸면 다음 sync가 최신값으로 덮는다
    (bid_amt와 같은 '항상 최신 관측' 규약 — last-known 보존 아님)."""
    entity_sync.sync_entities(db, rows=[{
        "entity_type": "adgroup", "entity_id": "grp-1", "parent_id": "cmp-1",
        "campaign_id": "cmp-1", "campaign_type": "SHOPPING", "name": "그룹1",
        "status": "on", "bid_amt": 500, "pc_bid_weight": 70, "mobile_bid_weight": 70,
    }])
    entity_sync.sync_entities(db, rows=[{
        "entity_type": "adgroup", "entity_id": "grp-1", "parent_id": "cmp-1",
        "campaign_id": "cmp-1", "campaign_type": "SHOPPING", "name": "그룹1",
        "status": "on", "bid_amt": 500, "pc_bid_weight": 100, "mobile_bid_weight": 105,
    }])
    e = db.query(NaverEntity).filter_by(entity_type="adgroup", entity_id="grp-1").one()
    assert e.pc_bid_weight == 100
    assert e.mobile_bid_weight == 105  # ★100 초과도 그대로(잘리지 않는다)


def test_sync_entities_existing_status_and_bid_flow_unchanged(db):
    """④회귀 0 — 기존 status/bid_amt 갱신 흐름이 기기가중치 추가로 깨지지 않는다."""
    entity_sync.sync_entities(db, rows=[{
        "entity_type": "adgroup", "entity_id": "grp-1", "parent_id": "cmp-1",
        "campaign_id": "cmp-1", "campaign_type": "SHOPPING", "name": "그룹1",
        "status": "on", "bid_amt": 500,
    }])
    entity_sync.sync_entities(db, rows=[{
        "entity_type": "adgroup", "entity_id": "grp-1", "parent_id": "cmp-1",
        "campaign_id": "cmp-1", "campaign_type": "SHOPPING", "name": "그룹1",
        "status": "off", "bid_amt": 600,
    }])
    e = db.query(NaverEntity).filter_by(entity_type="adgroup", entity_id="grp-1").one()
    assert e.status == "off"
    assert e.bid_amt == 600
