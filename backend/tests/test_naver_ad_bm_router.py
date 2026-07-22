# test_naver_ad_bm_router.py — BM 온디맨드 드릴다운 라우터 HTTP 왕복 (Phase 5, D-NAO-79 ③)
# 원칙22: SA 단위테스트는 라우터를 안 거치므로 라우터 레이어 500을 못 잡는다(P2-S2 사고 전례).
# 커버: GET /bm/agency-ops(필터·페이징) · /bm/snapshot(요약+원자료) · /bm/benchmark(파싱).
from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import NaverAgencyOp, NaverBmBenchmark, NaverEntity, NaverEntitySnapshot

OP_DATE = date(2026, 7, 22)
NOW = datetime(2026, 7, 22, 7, 37, 0)


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


# ── /bm/agency-ops ──────────────────────────────────────────────────

def test_agency_ops_filters_by_date_and_exception(client, db):
    db.add(NaverEntity(entity_type="campaign", entity_id="cmp-1", campaign_id="cmp-1",
                        campaign_type="WEB_SITE", name="01.갤럭시_TPU", status="on"))
    db.add(NaverAgencyOp(
        op_date=OP_DATE, detected_at=NOW, entity_type="adgroup", entity_id="grp-1",
        campaign_id="cmp-1", optimizer="none", op_type="bid_change",
        before_value="700", after_value="900", magnitude=28.6, is_exception=True,
    ))
    db.add(NaverAgencyOp(
        op_date=OP_DATE, detected_at=NOW, entity_type="adgroup", entity_id="grp-2",
        campaign_id="cmp-1", optimizer="none", op_type="keyword_add",
        before_value="10", after_value="12", magnitude=2.0, is_exception=False,
    ))
    db.commit()

    r = client.get("/api/naver/ad/bm/agency-ops", params={"date": "2026-07-22"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert len(body["rows"]) == 2
    assert body["rows"][0]["campaign_name"] == "01.갤럭시_TPU"

    r2 = client.get("/api/naver/ad/bm/agency-ops", params={"date": "2026-07-22", "is_exception": True})
    assert r2.json()["total"] == 1
    assert r2.json()["rows"][0]["op_type"] == "bid_change"


def test_agency_ops_days_window_and_pagination(client, db):
    for i, d in enumerate([date(2026, 7, 20), date(2026, 7, 21), date(2026, 7, 22)]):
        db.add(NaverAgencyOp(
            op_date=d, detected_at=NOW, entity_type="adgroup", entity_id=f"grp-{i}",
            campaign_id="cmp-1", optimizer="none", op_type="bid_change",
            before_value="1", after_value="2", magnitude=100.0, is_exception=True,
        ))
    db.commit()

    r = client.get("/api/naver/ad/bm/agency-ops", params={"date": "2026-07-22", "days": 2})
    assert r.json()["total"] == 2  # 07-21·07-22만(07-20 제외)

    r_paged = client.get(
        "/api/naver/ad/bm/agency-ops", params={"date": "2026-07-22", "days": 3, "limit": 1, "offset": 1},
    )
    assert r_paged.json()["total"] == 3
    assert len(r_paged.json()["rows"]) == 1


def test_agency_ops_empty_day_returns_empty_rows(client, db):
    r = client.get("/api/naver/ad/bm/agency-ops", params={"date": "2026-07-22"})
    assert r.status_code == 200
    assert r.json() == {"date_from": "2026-07-22", "date_to": "2026-07-22", "total": 0, "rows": []}


# ── /bm/snapshot ─────────────────────────────────────────────────────

def test_snapshot_summary_and_rows(client, db):
    db.add(NaverEntitySnapshot(
        snapshot_date=OP_DATE, entity_type="campaign", entity_id="cmp-1", campaign_id="cmp-1",
        campaign_type="WEB_SITE", optimizer="ours", name="04.아이폰_지문방지필름", status="on",
        daily_budget=50000,
    ))
    db.add(NaverEntitySnapshot(
        snapshot_date=OP_DATE, entity_type="adgroup", entity_id="grp-1", parent_id="cmp-1",
        campaign_id="cmp-1", campaign_type="WEB_SITE", optimizer="ours", name="그룹1", status="on",
        bid_amt=350, keyword_count=12, keyword_avg_bid=320,
    ))
    db.add(NaverEntitySnapshot(
        snapshot_date=OP_DATE, entity_type="adgroup", entity_id="grp-2", parent_id="cmp-2",
        campaign_id="cmp-2", campaign_type="WEB_SITE", optimizer="none", name="그룹2", status="on",
        bid_amt=500, keyword_count=8,
    ))
    db.commit()

    r = client.get("/api/naver/ad/bm/snapshot", params={"date": "2026-07-22"})
    assert r.status_code == 200
    body = r.json()
    assert body["summary_by_type_optimizer"] == {
        "campaign": {"ours": 1}, "adgroup": {"ours": 1, "none": 1},
    }
    assert body["total"] == 3

    r_filtered = client.get(
        "/api/naver/ad/bm/snapshot", params={"date": "2026-07-22", "entity_type": "adgroup"},
    )
    assert r_filtered.json()["total"] == 2
    # 요약은 필터와 무관하게 당일 전체 기준(불변)
    assert r_filtered.json()["summary_by_type_optimizer"]["campaign"] == {"ours": 1}


def test_snapshot_missing_date_returns_empty(client, db):
    r = client.get("/api/naver/ad/bm/snapshot", params={"date": "2026-01-01"})
    assert r.status_code == 200
    assert r.json()["total"] == 0
    assert r.json()["summary_by_type_optimizer"] == {}


# ── /bm/benchmark ────────────────────────────────────────────────────

def test_benchmark_parses_dict_and_list_values(client, db):
    db.add(NaverBmBenchmark(
        bench_kind="keyword_verified", bench_key="WEB_SITE",
        value_json='["아이폰케이스", "갤럭시필름"]', sample_n=2, confidence=None, computed_at=NOW,
    ))
    db.add(NaverBmBenchmark(
        bench_kind="bid_band", bench_key="WEB_SITE",
        value_json='{"min": 100, "p50": 300, "max": 900, "n": 5}', sample_n=5, confidence=0.5,
        computed_at=NOW,
    ))
    db.commit()

    r = client.get("/api/naver/ad/bm/benchmark")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    by_kind = {row["bench_kind"]: row for row in body["rows"]}
    assert by_kind["keyword_verified"]["value"] == ["아이폰케이스", "갤럭시필름"]
    assert by_kind["bid_band"]["value"] == {"min": 100, "p50": 300, "max": 900, "n": 5}


def test_benchmark_filters_by_kind_and_key(client, db):
    db.add(NaverBmBenchmark(
        bench_kind="bid_band", bench_key="WEB_SITE", value_json='{"p50": 300}',
        sample_n=5, confidence=0.5, computed_at=NOW,
    ))
    db.add(NaverBmBenchmark(
        bench_kind="bid_band", bench_key="SHOPPING", value_json='{"p50": 700}',
        sample_n=5, confidence=0.5, computed_at=NOW,
    ))
    db.commit()

    r = client.get("/api/naver/ad/bm/benchmark", params={"bench_kind": "bid_band", "bench_key": "SHOPPING"})
    assert r.json()["total"] == 1
    assert r.json()["rows"][0]["value"] == {"p50": 700}


def test_benchmark_bad_json_returns_none_value_not_500(client, db):
    db.add(NaverBmBenchmark(
        bench_kind="bid_band", bench_key="WEB_SITE", value_json="not-json{{",
        sample_n=0, confidence=None, computed_at=NOW,
    ))
    db.commit()
    r = client.get("/api/naver/ad/bm/benchmark")
    assert r.status_code == 200
    assert r.json()["rows"][0]["value"] is None
