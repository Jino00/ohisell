# test_naver_ad_raw_router.py — D-NAO-47 T5: GET /api/naver/ad/raw/* HTTP 왕복
# ★페이지네이션 상한이 이 API의 핵심 계약: naver_entity 키워드 91,005행 · search_term 114,285행.
#   상한 없이 열면 프론트가 죽는다(§9 라이브: 489행 무페이징 → 스크롤 27,305px).
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import NaverEntity, NaverHourlySnapshot, NaverSearchTermDaily
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
    session_for_seed = TestingSession()
    yield TestClient(app), session_for_seed
    session_for_seed.close()
    app.dependency_overrides.clear()


@pytest.fixture
def client(client_and_session):
    return client_and_session[0]


@pytest.fixture
def db(client_and_session):
    return client_and_session[1]


def _seed_keyword(db, *, entity_id="nkw-1", name="필름", bid=700, status="on", campaign_id="cmp-1"):
    db.add(NaverEntity(
        entity_type="keyword", entity_id=entity_id, parent_id="grp-1",
        campaign_id=campaign_id, campaign_type="WEB_SITE", name=name,
        status=status, bid_amt=bid, synced_at=kst_now(),
    ))
    db.commit()


# ── raw/keywords ──
def test_raw_keywords_returns_only_keyword_rows(client, db):
    _seed_keyword(db)
    db.add(NaverEntity(
        entity_type="campaign", entity_id="cmp-1", parent_id="",
        campaign_id="cmp-1", campaign_type="WEB_SITE", name="캠페인",
        status="on", bid_amt=None, synced_at=kst_now(),
    ))
    db.commit()
    items = client.get("/api/naver/ad/raw/keywords").json()["items"]
    assert len(items) == 1
    assert items[0]["entity_id"] == "nkw-1"
    assert items[0]["bid_amt"] == 700


def test_raw_keywords_limit_is_capped_at_200(client, db):
    """★91,005행짜리 테이블이다. 상한 없이 열면 프론트가 죽는다."""
    assert client.get("/api/naver/ad/raw/keywords?limit=201").status_code == 422
    assert client.get("/api/naver/ad/raw/keywords?limit=200").status_code == 200


def test_raw_keywords_returns_total_for_pagination(client, db):
    for i in range(5):
        _seed_keyword(db, entity_id=f"nkw-{i}", name=f"kw{i}")
    body = client.get("/api/naver/ad/raw/keywords?limit=2").json()
    assert body["total"] == 5
    assert len(body["items"]) == 2


def test_raw_keywords_search_by_name(client, db):
    _seed_keyword(db, entity_id="nkw-1", name="아이폰 필름")
    _seed_keyword(db, entity_id="nkw-2", name="갤럭시 케이스")
    items = client.get("/api/naver/ad/raw/keywords?q=필름").json()["items"]
    assert len(items) == 1
    assert items[0]["name"] == "아이폰 필름"


def test_raw_keywords_filters_by_campaign_and_status(client, db):
    _seed_keyword(db, entity_id="nkw-1", campaign_id="cmp-1", status="on")
    _seed_keyword(db, entity_id="nkw-2", campaign_id="cmp-2", status="on")
    _seed_keyword(db, entity_id="nkw-3", campaign_id="cmp-1", status="off")

    assert len(client.get("/api/naver/ad/raw/keywords?campaign_id=cmp-1").json()["items"]) == 2
    assert len(client.get("/api/naver/ad/raw/keywords?campaign_id=cmp-1&status=on").json()["items"]) == 1


def test_raw_keywords_excludes_deleted_by_default(client, db):
    _seed_keyword(db, entity_id="nkw-1", status="on")
    _seed_keyword(db, entity_id="nkw-2", status="deleted")
    items = client.get("/api/naver/ad/raw/keywords").json()["items"]
    assert len(items) == 1
    assert items[0]["entity_id"] == "nkw-1"


# ── raw/search-terms ──
def test_raw_search_terms_returns_rows(client, db):
    db.add(NaverSearchTermDaily(
        ad_date=kst_today(), campaign_id="cmp-1", adgroup_id="grp-1",
        search_term="아이폰 필름", source="shopping", imp=100, clk=5, cost=1000,
    ))
    db.commit()
    items = client.get("/api/naver/ad/raw/search-terms").json()["items"]
    assert len(items) == 1
    assert items[0]["search_term"] == "아이폰 필름"


def test_raw_search_terms_limit_is_capped_at_200(client, db):
    """114,285행."""
    assert client.get("/api/naver/ad/raw/search-terms?limit=201").status_code == 422


def test_raw_search_terms_respects_days_window(client, db):
    db.add(NaverSearchTermDaily(
        ad_date=kst_today() - timedelta(days=40), campaign_id="cmp-1", adgroup_id="grp-1",
        search_term="옛날", source="shopping", imp=1, clk=0, cost=0,
    ))
    db.add(NaverSearchTermDaily(
        ad_date=kst_today() - timedelta(days=1), campaign_id="cmp-1", adgroup_id="grp-1",
        search_term="최근", source="shopping", imp=1, clk=0, cost=0,
    ))
    db.commit()
    items = client.get("/api/naver/ad/raw/search-terms?days=7").json()["items"]
    assert len(items) == 1
    assert items[0]["search_term"] == "최근"


# ── raw/hourly ──
def test_raw_hourly_returns_rows_with_budget_and_ratio(client, db):
    """★daily_budget·소진율이 화면에 없던 결함(스펙 §1-4) — 여기서 처음 노출된다.
    ⚠️ 컬럼명은 `snapshot_hour`다(`hour` 아님 — models.py:1553 실측)."""
    db.add(NaverHourlySnapshot(
        ad_date=kst_today(), snapshot_hour=14, snapshot_at=kst_now(),
        campaign_id="cmp-1", campaign_type="WEB_SITE",
        cost=25000, clk=10, imp=100, daily_budget=100000, synced_at=kst_now(),
    ))
    db.commit()
    items = client.get("/api/naver/ad/raw/hourly").json()["items"]
    assert len(items) == 1
    assert items[0]["daily_budget"] == 100000
    assert items[0]["snapshot_hour"] == 14
    assert items[0]["spend_ratio"] == pytest.approx(0.25)


def test_raw_hourly_spend_ratio_is_none_when_budget_missing(client, db):
    """★0으로 나누지 않는다. 예산 미설정은 '소진율 0%'가 아니라 '알 수 없음'이다."""
    db.add(NaverHourlySnapshot(
        ad_date=kst_today(), snapshot_hour=14, snapshot_at=kst_now(),
        campaign_id="cmp-1", campaign_type="WEB_SITE",
        cost=25000, clk=10, imp=100, daily_budget=None, synced_at=kst_now(),
    ))
    db.commit()
    items = client.get("/api/naver/ad/raw/hourly").json()["items"]
    assert items[0]["spend_ratio"] is None


def test_raw_hourly_spend_ratio_is_none_when_budget_zero(client, db):
    db.add(NaverHourlySnapshot(
        ad_date=kst_today(), snapshot_hour=14, snapshot_at=kst_now(),
        campaign_id="cmp-1", campaign_type="WEB_SITE",
        cost=25000, clk=10, imp=100, daily_budget=0, synced_at=kst_now(),
    ))
    db.commit()
    assert client.get("/api/naver/ad/raw/hourly").json()["items"][0]["spend_ratio"] is None


def test_raw_hourly_ordered_by_date_then_hour(client, db):
    for h in (9, 14, 11):
        db.add(NaverHourlySnapshot(
            ad_date=kst_today(), snapshot_hour=h, snapshot_at=kst_now(),
            campaign_id="cmp-1", campaign_type="WEB_SITE",
            cost=100, clk=1, imp=10, daily_budget=1000, synced_at=kst_now(),
        ))
    db.commit()
    items = client.get("/api/naver/ad/raw/hourly").json()["items"]
    assert [i["snapshot_hour"] for i in items] == [14, 11, 9]  # 최신 시각 먼저


def test_raw_endpoints_empty_are_200(client):
    for path in ("keywords", "search-terms", "hourly"):
        body = client.get(f"/api/naver/ad/raw/{path}").json()
        assert body == {"items": [], "total": 0}
