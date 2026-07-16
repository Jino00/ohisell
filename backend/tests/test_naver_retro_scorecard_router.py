# test_naver_retro_scorecard_router.py — GET /api/naver/ad/retro-scorecard HTTP 라운드트립
# (TestClient, D-NAO-45). SA/harness 단위 테스트로는 라우터 레이어 자체 버그(정의 안 된
# 참조로 인한 500 등)를 못 잡는다 — test_naver_ad_diagnosis_router.py와 동일 근거.
#
# ⚠️ 로컬(homebrew python3, venv 없음) 환경에서는 bcrypt import 에러로 test collection이
# 실패하는 기존 이슈가 있어(다른 라우터 테스트 파일도 동일) 이 파일의 실행 확인은 로컬에서
# 스킵될 수 있다 — 코드 자체는 test_naver_ad_diagnosis_router.py와 동일 패턴으로 작성.
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import NaverRetroPacingScore, NaverRetroSignal
from app.utils.kst import kst_now, kst_today


@pytest.fixture
def client():
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
    app.state._test_session_factory = TestingSession  # 픽스처 데이터 주입용
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed(client, *, signals=(), pacing=()):
    Session = client.app.state._test_session_factory
    db = Session()
    try:
        for s in signals:
            db.add(s)
        for p in pacing:
            db.add(p)
        db.commit()
    finally:
        db.close()


def test_retro_scorecard_returns_200_empty(client):
    resp = client.get("/api/naver/ad/retro-scorecard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["window_days"] == 28
    assert body["boards"] == {}
    assert body["pacing"] == {}


def test_retro_scorecard_rollups_board_and_pacing(client):
    today = kst_today()
    _seed(
        client,
        signals=[
            NaverRetroSignal(
                created_at=kst_now(), asof_date=today - timedelta(days=5),
                board="bleeding_keywords", direction="down", grain="keyword",
                target_id="nkw-1", campaign_id="cmp1",
                cf_asof=1.0, bep_asof=2.0, target_asof=4.0, cost_asof=1000,
                verdict_d3="correct", bleed_post3=500,
            ),
        ],
        pacing=[
            NaverRetroPacingScore(
                proposal_id=1, alert_date=today - timedelta(days=2), campaign_id="cmp1",
                kind="저속", verdict="correct", scored_at=kst_now(),
            ),
        ],
    )
    resp = client.get("/api/naver/ad/retro-scorecard", params={"days": 28})
    assert resp.status_code == 200
    body = resp.json()
    assert body["boards"]["bleeding_keywords"]["d3"]["correct"] == 1
    assert body["boards"]["bleeding_keywords"]["d3"]["bleed_sum"] == 500
    assert body["boards"]["bleeding_keywords"]["d7"]["n"] == 0  # 아직 d7 미채점
    assert body["pacing"]["저속"]["correct"] == 1


def test_retro_scorecard_excludes_rows_outside_window(client):
    today = kst_today()
    _seed(
        client,
        signals=[
            NaverRetroSignal(
                created_at=kst_now(), asof_date=today - timedelta(days=40),
                board="bleeding_keywords", direction="down", grain="keyword",
                target_id="nkw-old", campaign_id="cmp1",
                cf_asof=1.0, bep_asof=2.0, target_asof=4.0, cost_asof=1000,
                verdict_d3="correct", bleed_post3=500,
            ),
        ],
    )
    resp = client.get("/api/naver/ad/retro-scorecard", params={"days": 28})
    assert resp.status_code == 200
    assert resp.json()["boards"] == {}


def test_retro_scorecard_rejects_invalid_days(client):
    resp = client.get("/api/naver/ad/retro-scorecard", params={"days": 0})
    assert resp.status_code == 422
