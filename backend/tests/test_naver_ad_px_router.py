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


def test_today_counts_exclude_console_imports(client, db):
    """★콘솔 편입분은 「오늘 자른 조치」가 아니다(D-NAO-177, D-NAO-176 리뷰 P2-3).

    편입분의 `last_transition_at`은 편입 시각(=오늘)이라 그냥 세면 43건을 부은 날 이 API가
    「오늘 43건 제외」라고 답한다. D-NAO-176의 1번 금지선은 그 거짓 표상이 **일기**로 나가는
    것을 막았는데, 같은 표상이 이 문으로도 나가면 막은 의미가 없다.

    ★그리고 이 단언은 NULL 안전성을 같이 지킨다: `source != 'console_import'`로 짜면 우리
    행(source IS NULL)이 SQL에서 전부 탈락해 today_excluded가 0이 된다.
    """
    _row(db, term="오늘_우리가잘랐다", status="excluded")
    _row(db, term="오늘_편입분", status="excluded", source="console_import",
         console_excluded_at=datetime(2024, 12, 26, 0, 0))
    # ★출처가 셋 이상이 될 때를 같이 지킨다: 라우터가 `source IS NULL`로만 좁히면 이 행이
    #   조용히 사라지는데 위 두 줄만으로는 그 변이가 안 잡힌다(적대 리뷰 변이 생존 M11).
    _row(db, term="오늘_자동발견", status="excluded", source="detected")

    body = client.get("/api/naver/ad/search-term/exclusions").json()

    assert body["today_excluded"] == 2, "편입분«만» 빠진다 — 우리 행과 다른 출처는 남아야 한다"
    assert body["summary_by_status"] == {"excluded": 3}, "장부에는 셋 다 있다(숨기는 게 아니다)"
    rows = {r["search_term"]: r for r in body["rows"]}
    assert rows["오늘_편입분"]["source"] == "console_import"
    assert rows["오늘_편입분"]["console_excluded_at"] == "2024-12-26T00:00:00", (
        "콘솔이 알려준 실제 시각이 화면까지 가야 편입분의 excluded_at을 오늘 자른 것으로 안 읽는다"
    )
    assert rows["오늘_우리가잘랐다"]["source"] is None
    assert rows["오늘_우리가잘랐다"]["console_excluded_at"] is None, "우리 행엔 콘솔 시각이 없다"


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


# ── P3-2: summary_by_status를 GROUP BY 집계로 대체 — 상태별 다건에서도 정확 카운트(동작 불변) ──
def test_exclusions_summary_group_by_counts_multiple_rows(client, db):
    for i in range(5):
        _row(db, term=f"e{i}", status="excluded")
    for i in range(3):
        _row(db, term=f"p{i}", status="probation")
    for i in range(2):
        _row(db, term=f"r{i}", status="restored")
    body = client.get("/api/naver/ad/search-term/exclusions").json()
    assert body["summary_by_status"] == {"excluded": 5, "probation": 3, "restored": 2}
    assert body["total"] == 10


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
