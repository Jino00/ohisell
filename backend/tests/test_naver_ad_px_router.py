# test_naver_ad_px_router.py — 파워링크 검색어 자동 제외 드릴다운 라우터 HTTP 왕복
#   (스프린트 PX4 §4 3, docs/PLAN_naver-ad-powerlink-autoexclude.md). 원칙22: SA 단위테스트는
#   라우터를 안 거치므로 라우터 레이어 500을 못 잡는다(test_naver_ad_bm_router.py와 동일 전례).
# 커버: GET /search-term/exclusions(status/campaign_id 필터·페이징·summary_by_status·오늘
#   제외/개방/복귀 카운트·campaign_name 해석).
from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import NaverEntity, NaverSearchTermExclusion
from app.routers import naver_ad as naver_ad_router

TODAY = date(2026, 7, 22)
NOW = datetime(2026, 7, 22, 9, 0, 0)
YESTERDAY_NOW = datetime(2026, 7, 21, 9, 0, 0)


@pytest.fixture
def client_and_session(monkeypatch):
    monkeypatch.setattr(naver_ad_router, "kst_today", lambda: TODAY)
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


def _row(db, *, term, status, adgroup_id="grp-1", campaign_id="cmp-1",
         last_transition_at=NOW, cost=15000, cycle=1, **kw):
    row = NaverSearchTermExclusion(
        campaign_id=campaign_id, adgroup_id=adgroup_id, search_term=term, status=status,
        cycle=cycle, excluded_at=last_transition_at, last_transition_at=last_transition_at,
        cost_at_exclusion=cost, **kw,
    )
    db.add(row)
    db.commit()
    return row


def test_exclusions_summary_and_today_counts(client, db):
    db.add(NaverEntity(entity_type="campaign", entity_id="cmp-1", campaign_id="cmp-1",
                        campaign_type="WEB_SITE", name="04.아이폰_지문방지필름", status="on"))
    db.commit()
    _row(db, term="오늘제외", status="excluded")
    _row(db, term="오늘복귀", status="probation")
    _row(db, term="오늘확정", status="restored")
    _row(db, term="어제제외", status="excluded", last_transition_at=YESTERDAY_NOW)

    r = client.get("/api/naver/ad/search-term/exclusions")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 4
    assert body["summary_by_status"] == {"excluded": 2, "probation": 1, "restored": 1}
    assert body["today_excluded"] == 1  # 어제제외는 오늘 카운트에서 빠짐
    assert body["today_opened"] == 1
    assert body["today_restored"] == 1

    names = {row["search_term"]: row["campaign_name"] for row in body["rows"]}
    assert names["오늘제외"] == "04.아이폰_지문방지필름"


def test_exclusions_filters_by_status_and_campaign(client, db):
    _row(db, term="A", status="excluded", campaign_id="cmp-1")
    _row(db, term="B", status="probation", campaign_id="cmp-1")
    _row(db, term="C", status="excluded", campaign_id="cmp-2")

    r = client.get("/api/naver/ad/search-term/exclusions", params={"status": "excluded"})
    assert r.json()["total"] == 2

    r2 = client.get("/api/naver/ad/search-term/exclusions", params={"campaign_id": "cmp-1"})
    assert r2.json()["total"] == 2

    r3 = client.get(
        "/api/naver/ad/search-term/exclusions",
        params={"status": "excluded", "campaign_id": "cmp-2"},
    )
    assert r3.json()["total"] == 1
    assert r3.json()["rows"][0]["search_term"] == "C"


def test_exclusions_pagination(client, db):
    for i in range(5):
        _row(db, term=f"검색어{i}", status="excluded", cost=1000 + i)
    r = client.get("/api/naver/ad/search-term/exclusions", params={"limit": 2, "offset": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    assert len(body["rows"]) == 2


def test_exclusions_empty_returns_zeroed_summary(client, db):
    r = client.get("/api/naver/ad/search-term/exclusions")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["summary_by_status"] == {}
    assert body["today_excluded"] == 0 and body["today_opened"] == 0 and body["today_restored"] == 0
    assert body["rows"] == []


def test_exclusions_row_shape_exposes_state_machine_fields(client, db):
    _row(db, term="필드검증", status="excluded", cost=7000, cycle=2,
         restrict_kwd_id="rkw-9", next_review_at=date(2026, 8, 21))
    r = client.get("/api/naver/ad/search-term/exclusions")
    row = r.json()["rows"][0]
    assert row["restrict_kwd_id"] == "rkw-9"
    assert row["cycle"] == 2
    assert row["cost_at_exclusion"] == 7000
    assert row["next_review_at"] == "2026-08-21"
    assert row["probation_until"] is None
