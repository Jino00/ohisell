# test_naver_campaigns_router.py — D-NAO-48: GET /api/naver/ad/campaigns HTTP 왕복
# 원칙22: SA 단위테스트는 라우터를 안 거치므로 라우터 레이어 500을 못 잡는다(P2-S2 사고 전례).
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import NaverAdDaily, NaverCampaignSettings, NaverEntity
from app.utils.kst import kst_now, kst_today


@pytest.fixture
def client_and_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    seed = TestingSession()
    yield TestClient(app), seed
    seed.close()
    app.dependency_overrides.clear()


@pytest.fixture
def client(client_and_session):
    return client_and_session[0]


@pytest.fixture
def db(client_and_session):
    return client_and_session[1]


def _seed(db, *, cid="cmp-1", name="● 03. 아이폰_강화유리", ctype="SHOPPING", cost=1000, optimizer=None):
    db.add(NaverEntity(
        entity_type="campaign", entity_id=cid, parent_id="", campaign_id=cid,
        campaign_type=ctype, name=name, status="on", bid_amt=None, synced_at=kst_now(),
    ))
    db.add(NaverAdDaily(
        ad_date=kst_today() - timedelta(days=1), campaign_id=cid, campaign_type=ctype,
        adgroup_id="grp-1", keyword_id="-", imp=0, clk=5, cost=cost, rank_sum=0,
        conv_direct_cnt=0, conv_indirect_cnt=0, conv_direct_amt=cost * 2, conv_indirect_amt=0,
        synced_at=kst_now(),
    ))
    if optimizer:
        db.add(NaverCampaignSettings(campaign_id=cid, optimizer=optimizer))
    db.commit()


def test_campaigns_returns_name_type_optimizer(client, db):
    _seed(db, optimizer="mop")
    body = client.get("/api/naver/ad/campaigns").json()
    assert body["total"] == 1
    r = body["rows"][0]
    assert r["name"] == "● 03. 아이폰_강화유리"     # ★내부 ID 대신 이름
    assert r["campaign_type"] == "SHOPPING"
    assert r["optimizer"] == "mop"
    assert r["roas_naver"] == pytest.approx(2.0)


def test_campaigns_empty_is_200(client):
    assert client.get("/api/naver/ad/campaigns").json() == {"total": 0, "rows": []}


def test_campaigns_days_param_validated(client):
    assert client.get("/api/naver/ad/campaigns?days=0").status_code == 422
    assert client.get("/api/naver/ad/campaigns?days=400").status_code == 422
    assert client.get("/api/naver/ad/campaigns?days=30").status_code == 200


def test_campaigns_filter_by_type(client, db):
    _seed(db, cid="c-shop", name="쇼핑", ctype="SHOPPING")
    _seed(db, cid="c-web", name="파워링크", ctype="WEB_SITE")
    rows = client.get("/api/naver/ad/campaigns?campaign_type=SHOPPING").json()["rows"]
    assert len(rows) == 1
    assert rows[0]["campaign_id"] == "c-shop"


def test_campaigns_filter_by_optimizer(client, db):
    _seed(db, cid="c-ours", name="우리", optimizer="ours")
    _seed(db, cid="c-none", name="수동")
    rows = client.get("/api/naver/ad/campaigns?optimizer=ours").json()["rows"]
    assert len(rows) == 1
    assert rows[0]["campaign_id"] == "c-ours"


def test_campaigns_optimizer_filter_rejects_unknown(client):
    assert client.get("/api/naver/ad/campaigns?optimizer=bogus").status_code == 422
