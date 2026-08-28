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


# ── D-NAO-47: 저속 롤업에 평균 최종 소진율 노출 ──
def test_pacing_rollup_exposes_avg_final_ratio(client):
    """★D-NAO-45의 정정("노이즈 아님 → 롤업")의 **핵심 숫자**가 평균 최종 소진율이다.
    "저속 경보 769건 correct"만으론 '경보가 맞았다'까지고, "평균 최종 소진율 4.9%"라야
    **하루가 끝나도 일예산의 4.9%만 썼다 = 만성 저소진이 실재한다**는 증거가 된다.
    데이터는 이미 naver_retro_pacing_score.final_ratio에 있는데 엔드포인트가 안 줬다."""
    today = kst_today()
    _seed(client, pacing=[
        NaverRetroPacingScore(
            proposal_id=1, alert_date=today - timedelta(days=2), campaign_id="cmp1",
            kind="저속", verdict="correct", final_ratio=0.04, scored_at=kst_now(),
        ),
        NaverRetroPacingScore(
            proposal_id=2, alert_date=today - timedelta(days=2), campaign_id="cmp2",
            kind="저속", verdict="correct", final_ratio=0.06, scored_at=kst_now(),
        ),
    ])
    body = client.get("/api/naver/ad/retro-scorecard", params={"days": 28}).json()
    assert body["pacing"]["저속"]["correct"] == 2
    assert body["pacing_final_ratio"]["저속"]["correct"] == pytest.approx(0.05)


def test_pacing_avg_final_ratio_is_none_when_all_null(client):
    """final_ratio가 전부 NULL(unparsed)이면 평균은 0이 아니라 **없음**이다.
    0으로 적으면 '소진율 0%'라는 거짓 사실이 된다."""
    today = kst_today()
    _seed(client, pacing=[
        NaverRetroPacingScore(
            proposal_id=1, alert_date=today - timedelta(days=1), campaign_id="cmp1",
            kind="저속", verdict="unparsed", final_ratio=None, scored_at=kst_now(),
        ),
    ])
    body = client.get("/api/naver/ad/retro-scorecard", params={"days": 28}).json()
    assert body["pacing_final_ratio"]["저속"]["unparsed"] is None


def test_pacing_final_ratio_absent_when_no_rows(client):
    body = client.get("/api/naver/ad/retro-scorecard", params={"days": 28}).json()
    assert body["pacing_final_ratio"] == {}


# ── D-NAO-267 (M2 계약 §4-A T1 = ref 65 S2-ⓐ): 평시/주말/공휴일 분리 열 ──

def _weekday_within(days: int) -> date:
    """창 안의 평시(평일 ∧ 비공휴일) 날짜 — 요일 의존 테스트를 결정적으로 만든다."""
    from app.services.naver_ad import probe_cell_aggregate
    d = kst_today() - timedelta(days=1)
    while probe_cell_aggregate.env_cell_of_date(d) != "weekday":
        d -= timedelta(days=1)
    assert (kst_today() - d).days < days
    return d


def test_surface_weekend_holiday_column_reaches_the_http_body(client):
    """★★표면 절단 변이 대상 (M2 계약 §4-C 공통).

    ref 63 §4-1이 확정한 주말 −8,020,470원·공휴일 −915,912원(둘 다 홀드아웃 통과·부호
    안정)을 판정면에 «분리»해 싣는 게 T1의 산출물이다. 그런데 하니스가 아무리 잘 갈라도
    **HTTP 응답 본문에 안 실리면 Jino는 여전히 섞인 숫자를 본다.**

    그래서 서비스층이 아니라 **응답 본문**에서 확인한다 — 교훈 #321: `response_model`이
    키를 조용히 지우는 전례가 이 저장소에 있다. 라우터에서 `weekend_holiday` 키를 빼는
    변이는 여기서 죽어야 한다.
    """
    asof = _weekday_within(28)
    _seed(client, signals=[
        NaverRetroSignal(
            created_at=kst_now(), asof_date=asof,
            board="bleeding_keywords", direction="down", grain="keyword",
            target_id="nkw-1", campaign_id="cmp1",
            cf_asof=1.0, bep_asof=2.0, target_asof=4.0, cost_asof=1000,
            verdict_d3="correct", bleed_post3=500,
        ),
    ])
    body = client.get("/api/naver/ad/retro-scorecard", params={"days": 28}).json()
    board = body["boards"]["bleeding_keywords"]

    assert "weekend_holiday" in board, "분리 열이 응답에 없다 — 화면은 여전히 섞인 값만 본다"
    split = board["weekend_holiday"]["d3"]
    for bucket in ("weekday", "weekend", "holiday"):
        assert bucket in split, f"{bucket} 칸이 없다"
    assert split["weekday"]["correct"] == 1
    assert split["weekend"]["n"] == 0
    assert split["holiday"]["n"] == 0


def test_surface_identity_check_reaches_the_http_body(client):
    """항등식 검산 결과도 응답까지 간다 — 「분리해 놨는데 합이 안 맞는」 결함은 화면을
    봐선 안 보인다. 계약 §4-C S2-①이 요구하는 검산이 그 자리에서 읽혀야 한다."""
    asof = _weekday_within(28)
    _seed(client, signals=[
        NaverRetroSignal(
            created_at=kst_now(), asof_date=asof,
            board="bleeding_keywords", direction="down", grain="keyword",
            target_id="nkw-1", campaign_id="cmp1",
            cf_asof=1.0, bep_asof=2.0, target_asof=4.0, cost_asof=1000,
            verdict_d3="correct", bleed_post3=500,
        ),
    ])
    split = client.get("/api/naver/ad/retro-scorecard", params={"days": 28}).json()[
        "boards"]["bleeding_keywords"]["weekend_holiday"]["d3"]

    assert split["identity"]["ok"] is True
    assert split["identity"]["sum_of_parts"]["n"] == split["identity"]["total"]["n"]


def test_existing_d3_d7_keys_are_untouched(client):
    """★기존 shape을 안 깼다 — `weekend_holiday`는 **덧붙인** 키다.

    이 응답의 소비처(커맨드 센터·성과뷰 타임라인)가 d3/d7을 읽고 있어서, 갈아치우면
    분리를 얻고 기존 표면을 잃는다.
    """
    asof = _weekday_within(28)
    _seed(client, signals=[
        NaverRetroSignal(
            created_at=kst_now(), asof_date=asof,
            board="bleeding_keywords", direction="down", grain="keyword",
            target_id="nkw-1", campaign_id="cmp1",
            cf_asof=1.0, bep_asof=2.0, target_asof=4.0, cost_asof=1000,
            verdict_d3="correct", bleed_post3=500,
        ),
    ])
    board = client.get("/api/naver/ad/retro-scorecard", params={"days": 28}).json()[
        "boards"]["bleeding_keywords"]
    assert board["d3"]["correct"] == 1
    assert board["d3"]["bleed_sum"] == 500
    assert board["d7"]["n"] == 0
