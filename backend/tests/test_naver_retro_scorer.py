# test_naver_retro_scorer.py — D-NAO-45 retro_scorer SA 단위 테스트
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverRetroSignal
from app.services.naver_ad import retro_scorer
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

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


def _sentinel(db, ad_date, campaign_id, cost):
    """post_agg가 캠페인 sentinel 행까지 합산하면 안 됨(2배 함정) — 회귀 방지용 픽스처."""
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type="WEB_SITE",
        adgroup_id=BACKFILL_SENTINEL_ADGROUP, keyword_id="",
        imp=1, clk=1, cost=cost, rank_sum=0,
    ))


# ── 판정 매트릭스 (PLAN §6-C2) ──
@pytest.mark.parametrize("direction,conv,expected", [
    ("down", 500, "correct"),    # roas 0.5 < bep 2.0
    ("down", 3000, "gray"),      # roas 3.0 in [2.0, 4.0)
    ("down", 6000, "wrong"),     # roas 6.0 >= target 4.0
    ("pause", 0, "correct"),     # 무전환
    ("pause", 500, "gray"),      # roas 0.5 < bep(전환은 있음)
    ("pause", 3000, "wrong"),    # roas 3.0 >= bep(전환 있고 개선)
    ("up", 5000, "correct"),     # roas 5.0 >= target 4.0
    ("up", 3000, "gray"),        # roas 3.0 in [2.0, 4.0)
    ("up", 500, "wrong"),        # roas 0.5 < bep 2.0
])
def test_score_due_d3_verdict_matrix(db, direction, conv, expected):
    asof = TODAY - timedelta(days=4)  # d3 창 경계값(정확히 today-4)
    _signal(db, asof=asof, direction=direction, cf=1.0, bep=2.0, target=4.0)
    _detail(db, asof + timedelta(days=1), "nkw-1", 1000, conv)
    db.commit()

    result = retro_scorer.score_due(db, TODAY)
    assert result["scored_d3"] == 1
    row = db.query(NaverRetroSignal).one()
    assert row.verdict_d3 == expected
    assert row.cost_post3 == 1000
    assert row.conv_post3 == conv


def test_score_due_no_spend_when_zero_cost_in_post_window(db):
    asof = TODAY - timedelta(days=4)
    _signal(db, asof=asof, direction="down")
    # 사후창에 데이터 자체가 없음(cost_post=0)
    db.commit()

    result = retro_scorer.score_due(db, TODAY)
    assert result["scored_d3"] == 1
    row = db.query(NaverRetroSignal).one()
    assert row.verdict_d3 == "no_spend"
    assert row.cost_post3 == 0
    assert row.conv_post3 == 0
    assert row.roas_c_post3 is None
    assert row.bleed_post3 == 0


# ── d3/d7 창 경계 ──
def test_score_due_d3_boundary_not_yet_due(db):
    """asof = today-3(아직 today-4 미만 경과) — d3 채점 대상 아님."""
    asof = TODAY - timedelta(days=3)
    _signal(db, asof=asof, direction="down")
    _detail(db, asof + timedelta(days=1), "nkw-1", 1000, 500)
    db.commit()

    result = retro_scorer.score_due(db, TODAY)
    assert result["scored_d3"] == 0
    row = db.query(NaverRetroSignal).one()
    assert row.verdict_d3 is None


def test_score_due_d7_boundary_exact_and_not_yet(db):
    asof_due = TODAY - timedelta(days=8)
    asof_not_due = TODAY - timedelta(days=7)
    _signal(db, asof=asof_due, target_id="nkw-due", direction="down")
    _signal(db, asof=asof_not_due, target_id="nkw-notdue", direction="down")
    _detail(db, asof_due + timedelta(days=1), "nkw-due", 1000, 500)
    _detail(db, asof_not_due + timedelta(days=1), "nkw-notdue", 1000, 500)
    db.commit()

    result = retro_scorer.score_due(db, TODAY)
    assert result["scored_d7"] == 1
    due = db.query(NaverRetroSignal).filter_by(target_id="nkw-due").one()
    not_due = db.query(NaverRetroSignal).filter_by(target_id="nkw-notdue").one()
    assert due.verdict_d7 == "correct"
    assert not_due.verdict_d7 is None


def test_score_due_d7_post_window_spans_seven_days(db):
    """사후창 asof+1..asof+7 — 그 이후(asof+8) 지출은 합산되면 안 됨."""
    asof = TODAY - timedelta(days=8)
    _signal(db, asof=asof, direction="down", cf=1.0, bep=2.0, target=4.0)
    for offset in range(1, 8):  # asof+1..asof+7
        _detail(db, asof + timedelta(days=offset), "nkw-1", 100, 50)
    _detail(db, asof + timedelta(days=8), "nkw-1", 999_999, 1)  # 창 밖 — 누출되면 안 됨
    db.commit()

    result = retro_scorer.score_due(db, TODAY)
    assert result["scored_d7"] == 1
    row = db.query(NaverRetroSignal).one()
    assert row.cost_post7 == 700, "창 밖(asof+8) 지출이 섞이면 안 됨"
    assert row.conv_post7 == 350


# ── 이중 채점 방지 ──
def test_score_due_does_not_rescore_already_scored_row(db):
    asof = TODAY - timedelta(days=4)
    sig = _signal(db, asof=asof, direction="down")
    _detail(db, asof + timedelta(days=1), "nkw-1", 1000, 500)
    db.commit()

    first = retro_scorer.score_due(db, TODAY)
    assert first["scored_d3"] == 1

    # 재실행 전 사후 데이터를 바꿔도(이미 채점된 행은 verdict IS NULL 가드에 걸려 재조회 대상 아님)
    _detail(db, asof + timedelta(days=2), "nkw-1", 999_999, 1)
    db.commit()

    second = retro_scorer.score_due(db, TODAY)
    assert second["scored_d3"] == 0
    row = db.query(NaverRetroSignal).one()
    assert row.cost_post3 == 1000, "이미 채점된 행이 재집계되면 안 됨"


# ── 밀린 as-of catch-up ──
def test_score_due_catches_up_stale_asof_in_single_run(db):
    """오래 방치된 as-of(today-30)도 한 번의 score_due 호출로 catch-up 채점된다."""
    asof = TODAY - timedelta(days=30)
    _signal(db, asof=asof, direction="down")
    _detail(db, asof + timedelta(days=1), "nkw-1", 1000, 500)
    db.commit()

    result = retro_scorer.score_due(db, TODAY)
    assert result["scored_d3"] == 1
    assert result["scored_d7"] == 1
    row = db.query(NaverRetroSignal).one()
    assert row.verdict_d3 == "correct"
    assert row.verdict_d7 == "correct"


# ── bleed 계산 ──
def test_score_due_bleed_calculation(db):
    asof = TODAY - timedelta(days=4)
    _signal(db, asof=asof, direction="down", cf=1.0, bep=2.0, target=4.0)
    _detail(db, asof + timedelta(days=1), "nkw-1", 1000, 500)  # roas=0.5
    db.commit()

    retro_scorer.score_due(db, TODAY)
    row = db.query(NaverRetroSignal).one()
    # bleed = round(cost_post - conv_post*cf/bep) = round(1000 - 500*1.0/2.0) = round(750)
    assert row.bleed_post3 == 750


# ── 상세행만 집계(sentinel 이중계산 회귀) ──
def test_score_due_excludes_sentinel_rows(db):
    asof = TODAY - timedelta(days=4)
    _signal(db, asof=asof, direction="down", grain="adgroup", target_id="grp-1")
    _detail(db, asof + timedelta(days=1), "grp-1", 1000, 500, grain="adgroup")
    _sentinel(db, asof + timedelta(days=1), "cmp1", 1000)  # 같은 캠페인 sentinel — 합산되면 2배
    db.commit()

    retro_scorer.score_due(db, TODAY)
    row = db.query(NaverRetroSignal).one()
    assert row.cost_post3 == 1000, "sentinel 행이 상세행과 합산되면 2000이 되어 2배 버그가 됨"
