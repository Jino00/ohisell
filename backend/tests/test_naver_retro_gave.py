# test_naver_retro_gave.py — D-NAO-72 GAVE 페널티 점수 소급 채점 배선 테스트 (ref 26 ⑤)
# retro_scorer가 방향 채점(verdict)과 병렬로 GAVE 점수를 기록하고 제안 유형별로 롤업하는지 검증.
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdDaily,
    NaverCampaignSettings,
    NaverLearningState,
    NaverRetroSignal,
)
from app.services.naver_ad import retro_scorer

TODAY = date(2026, 7, 20)


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


def _signal(db, *, asof, board="bleeding_keywords", direction="down", grain="keyword",
            target_id="nkw-1", campaign_id="cmp1", cf=1.0, bep=2.0, target=4.0, cost_asof=1000):
    sig = NaverRetroSignal(
        created_at=TODAY, asof_date=asof, board=board, direction=direction, grain=grain,
        target_id=target_id, campaign_id=campaign_id, cf_asof=cf, bep_asof=bep, target_asof=target,
        cost_asof=cost_asof, roas_c_asof=None,
    )
    db.add(sig)
    db.flush()
    return sig


def _detail(db, ad_date, target_id, cost, conv, *, grain="keyword", campaign_id="cmp1"):
    kwargs = dict(ad_date=ad_date, campaign_id=campaign_id, campaign_type="WEB_SITE",
                  imp=max(cost // 10, 1), clk=max(cost // 500, 1), cost=cost, rank_sum=0,
                  conv_direct_amt=conv, conv_indirect_amt=0)
    if grain == "keyword":
        kwargs.update(adgroup_id="grp1", keyword_id=target_id)
    else:
        kwargs.update(adgroup_id=target_id, keyword_id="")
    db.add(NaverAdDaily(**kwargs))


def test_gave_recorded_parallel_with_verdict(db):
    """GAVE 점수가 방향 채점(verdict)과 같은 행에 병렬로 기록된다."""
    asof = TODAY - timedelta(days=4)
    _signal(db, asof=asof, direction="down", cf=1.0, bep=2.0, target=4.0)
    _detail(db, asof + timedelta(days=1), "nkw-1", 1000, 6000)  # roas 6.0 >= bep → full credit
    db.commit()

    result = retro_scorer.score_due(db, TODAY)
    assert result["scored_d3"] == 1
    row = db.query(NaverRetroSignal).one()
    assert row.verdict_d3 is not None  # 방향 채점 유지
    assert row.gave_score_d3 is not None  # GAVE 병렬 기록
    # roas 6.0 >= bep 2.0 → penalty=1 → score = 매출 전액(cf=1.0) = 6000
    assert row.gave_score_d3 == pytest.approx(6000.0)


def test_gave_no_spend_scores_zero(db):
    """사후창 무지출(cost_post=0)이면 GAVE 점수 0(방향 no_spend와 정합)."""
    asof = TODAY - timedelta(days=4)
    _signal(db, asof=asof, direction="down")
    # 사후창에 상세행 없음 → cost_post=0
    db.commit()

    retro_scorer.score_due(db, TODAY)
    row = db.query(NaverRetroSignal).one()
    assert row.verdict_d3 == "no_spend"
    assert row.gave_score_d3 == 0.0


def test_gave_full_credit_when_roas_above_bep(db):
    """roas_c >= bep_asof면 penalty=1 → 매출 전액 인정."""
    asof = TODAY - timedelta(days=4)
    _signal(db, asof=asof, direction="down", cf=2.0, bep=2.0)
    _detail(db, asof + timedelta(days=1), "nkw-1", 1000, 2000)
    # roas_c = (2000/1000)*2.0 = 4.0 >= bep 2.0 → penalty 1; 매출 = conv*cf = 4000
    db.commit()

    retro_scorer.score_due(db, TODAY)
    row = db.query(NaverRetroSignal).one()
    assert row.gave_score_d3 == pytest.approx(4000.0)


def test_gave_penalized_when_roas_below_bep(db):
    """roas_c < bep_asof면 γ 벌칙으로 매출보다 낮은 점수."""
    asof = TODAY - timedelta(days=4)
    _signal(db, asof=asof, direction="down", cf=1.0, bep=4.0)
    _detail(db, asof + timedelta(days=1), "nkw-1", 1000, 2000)
    # roas = 2.0 < bep 4.0 → ratio 0.5, γ=1 → penalty 0.5 → score = 0.5*2000 = 1000
    db.commit()

    retro_scorer.score_due(db, TODAY)
    row = db.query(NaverRetroSignal).one()
    assert row.gave_score_d3 == pytest.approx(1000.0)
    assert row.gave_score_d3 < 2000.0  # 매출(2000) 미만 = 벌칙 적용


def test_gamma_from_campaign_settings_harsher(db):
    """γ 소스 = naver_campaign_settings.gamma. γ↑이면 BEP 미달 벌칙이 강해져 점수↓."""
    asof = TODAY - timedelta(days=4)
    # γ=3 캠페인 설정
    db.add(NaverCampaignSettings(campaign_id="cmpG", optimizer="ours", gamma=Decimal("3")))
    _signal(db, asof=asof, direction="down", campaign_id="cmpG", cf=1.0, bep=4.0)
    _detail(db, asof + timedelta(days=1), "nkw-1", 1000, 2000, campaign_id="cmpG")
    db.commit()

    retro_scorer.score_due(db, TODAY)
    row = db.query(NaverRetroSignal).one()
    # ratio 0.5, γ=3 → penalty 0.125 → score = 0.125*2000 = 250 (γ=1일 때 1000보다 낮음)
    assert row.gave_score_d3 == pytest.approx(250.0)


def test_gamma_defaults_when_no_settings(db):
    """캠페인 설정 없으면 DEFAULT_GAMMA(=1)."""
    asof = TODAY - timedelta(days=4)
    _signal(db, asof=asof, direction="down", campaign_id="no-settings", cf=1.0, bep=4.0)
    _detail(db, asof + timedelta(days=1), "nkw-1", 1000, 2000, campaign_id="no-settings")
    db.commit()

    retro_scorer.score_due(db, TODAY)
    row = db.query(NaverRetroSignal).one()
    assert row.gave_score_d3 == pytest.approx(1000.0)  # γ=1 벌칙


def test_gave_rollup_by_board(db):
    """제안 유형(board)별 GAVE 롤업이 NaverLearningState(scope=retro_board)에 기록된다."""
    asof = TODAY - timedelta(days=4)
    # board A: 두 신호 (매출 전액 6000, 3000) → avg 4500
    _signal(db, asof=asof, board="bleeding_keywords", target_id="nkw-a1", cf=1.0, bep=2.0)
    _detail(db, asof + timedelta(days=1), "nkw-a1", 1000, 6000)
    _signal(db, asof=asof, board="bleeding_keywords", target_id="nkw-a2", cf=1.0, bep=2.0)
    _detail(db, asof + timedelta(days=1), "nkw-a2", 1000, 3000)
    # board B: 한 신호 (벌칙 후 1000)
    _signal(db, asof=asof, board="pause_candidates", direction="pause", target_id="nkw-b1", cf=1.0, bep=4.0)
    _detail(db, asof + timedelta(days=1), "nkw-b1", 1000, 2000)
    db.commit()

    result = retro_scorer.score_due(db, TODAY)
    rollup = result["gave_rollup"]["d3"]
    assert rollup["bleeding_keywords"]["n"] == 2
    assert rollup["bleeding_keywords"]["avg"] == pytest.approx(4500.0)
    assert rollup["pause_candidates"]["n"] == 1

    # NaverLearningState 상시 공개
    state = db.query(NaverLearningState).filter(
        NaverLearningState.scope == "retro_board",
        NaverLearningState.scope_key == "bleeding_keywords",
        NaverLearningState.metric == "gave_score_d3",
    ).one()
    assert state.sample_n == 2
    assert float(state.current_value) == pytest.approx(4500.0)
