# test_naver_proposal_writer.py — P2-S3 T3 proposal_writer 단위테스트
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverCampaignSettings, NaverProposal
from app.services.naver_ad import proposal_writer


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


AS_OF = date(2026, 7, 6)


def _bleeding_row(**overrides):
    row = {"campaign_id": "cmp-ours", "adgroup_id": "grp-1", "keyword_id": "nkw-1",
           "imp": 1000, "clk": 50, "cost": 100_000, "conv_amt": 20_000, "roas_naver": 0.2,
           "roas_corrected": 0.4}
    row.update(overrides)
    return row


def _diagnosis(**boards):
    return {"window": {}, "correction_factor": {"factor": 2.0}, "boards": boards}


def _sim(direction="down", ceiling=100, recommended=100, basis="economic_ceiling"):
    return {
        "recommended_bid": recommended, "economic_ceiling": ceiling, "rank_bid": None,
        "direction": direction, "basis": basis,
        "expected_effect_text": "테스트 expected_effect",
        "capability_flags": {"estimate_ok": False, "performance_estimate_ok": False,
                              "is_new_or_growth": False, "keyword_sample_thin": False},
    }


def test_build_skips_non_ours_campaigns(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-none", optimizer="none"))
    db.commit()
    diagnosis = _diagnosis(bleeding_keywords=[_bleeding_row(campaign_id="cmp-none")])

    out = proposal_writer.build(db, diagnosis, bid_sims={("keyword", "nkw-1"): _sim()}, as_of=AS_OF)
    assert out == []


def test_build_bleeding_keyword_produces_bid_down(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis(bleeding_keywords=[_bleeding_row()])

    out = proposal_writer.build(db, diagnosis, bid_sims={("keyword", "nkw-1"): _sim(direction="down")}, as_of=AS_OF)
    assert len(out) == 1
    assert out[0]["proposal_type"] == "bid_down"
    assert out[0]["target_type"] == "keyword"
    assert out[0]["target_id"] == "nkw-1"
    assert "target_roas 근거=" in out[0]["rationale"]


def test_build_bleeding_keyword_zero_ceiling_escalates_to_negative(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis(bleeding_keywords=[_bleeding_row()])

    out = proposal_writer.build(
        db, diagnosis,
        bid_sims={("keyword", "nkw-1"): _sim(direction="down", ceiling=0, recommended=0)},
        as_of=AS_OF,
    )
    assert out[0]["proposal_type"] == "negative_keyword"


def test_build_starving_winner_produces_bid_up(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    row = {"campaign_id": "cmp-ours", "adgroup_id": "grp-1", "keyword_id": "nkw-2",
           "cost": 5000, "clk": 3, "conv_amt": 50_000, "roas_corrected": 10.0, "avg_daily_clk": 0.1}
    diagnosis = _diagnosis(starving_winners=[row])

    out = proposal_writer.build(db, diagnosis, bid_sims={("keyword", "nkw-2"): _sim(direction="up")}, as_of=AS_OF)
    assert out[0]["proposal_type"] == "bid_up"


def test_build_no_sim_available_skips_row(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis(bleeding_keywords=[_bleeding_row()])
    out = proposal_writer.build(db, diagnosis, bid_sims={}, as_of=AS_OF)  # sim 없음
    assert out == []


def test_build_hold_direction_produces_no_proposal(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis(bleeding_keywords=[_bleeding_row()])
    out = proposal_writer.build(db, diagnosis, bid_sims={("keyword", "nkw-1"): _sim(direction="hold")}, as_of=AS_OF)
    assert out == []


def test_build_exclusion_candidates_labeled_negative_no_conversion_claim(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    row = {"campaign_id": "cmp-ours", "adgroup_id": "grp-1", "search_term": "무관검색어",
           "source": "expkeyword", "cost": 30000, "clk": 20, "imp": 400}
    diagnosis = _diagnosis(exclusion_candidates=[row])

    out = proposal_writer.build(db, diagnosis, as_of=AS_OF)
    assert len(out) == 1
    assert out[0]["proposal_type"] == "negative_keyword"
    assert out[0]["target_type"] == "search_term"
    assert "전환귀속 데이터 없음" in out[0]["rationale"]
    assert "전환" in out[0]["expected_effect"]  # 정밀예측 불가 명시


def test_persist_dedups_existing_pending_same_type_and_target(db):
    db.add(NaverProposal(proposal_type="bid_down", target_type="keyword", target_id="nkw-1",
                          campaign_id="cmp-ours", status="pending"))
    db.commit()

    candidates = [{"proposal_type": "bid_down", "target_type": "keyword", "target_id": "nkw-1",
                   "campaign_id": "cmp-ours", "rationale": "r", "expected_effect": "e", "status": "pending"}]
    saved = proposal_writer.persist(db, candidates)
    assert saved == []
    assert db.query(NaverProposal).filter(NaverProposal.target_id == "nkw-1").count() == 1


def test_persist_allows_new_target_and_allows_after_previous_resolved(db):
    db.add(NaverProposal(proposal_type="bid_down", target_type="keyword", target_id="nkw-1",
                          campaign_id="cmp-ours", status="approved"))  # 더 이상 pending 아님
    db.commit()

    candidates = [{"proposal_type": "bid_down", "target_type": "keyword", "target_id": "nkw-1",
                   "campaign_id": "cmp-ours", "rationale": "r", "expected_effect": "e", "status": "pending"}]
    saved = proposal_writer.persist(db, candidates)
    assert len(saved) == 1  # 기존 건은 approved라 dedup 대상 아님


def test_account_brief_singleton_created_once_per_day(db):
    diagnosis = _diagnosis(
        expansion_bucket={"cost_share": 0.3, "roas_corrected": 1.2},
        keyword_triage={"judgeable": 10, "growth_candidate": 2, "dead": 5},
        bleeding_keywords=[_bleeding_row()],
        starving_winners=[],
    )
    first = proposal_writer.account_brief_singleton(db, diagnosis, AS_OF)
    db.commit()
    assert first.proposal_type == "account_brief"
    assert "as_of=2026-07-06" in first.rationale

    second = proposal_writer.account_brief_singleton(db, diagnosis, AS_OF)
    assert second.id == first.id  # 재호출해도 오늘자 기존 것 재사용(중복 생성 없음)
    assert db.query(NaverProposal).filter(NaverProposal.proposal_type == "account_brief").count() == 1


def test_account_brief_singleton_new_day_creates_new_row(db):
    diagnosis = _diagnosis()
    yesterday_brief = NaverProposal(
        proposal_type="account_brief", target_type="account", target_id="",
        campaign_id="", rationale="어제자", expected_effect="e", status="pending",
    )
    db.add(yesterday_brief)
    db.commit()
    # created_at을 어제로 되돌려 '오늘 이미 생성됨' 조건을 회피(달력일 경계 테스트)
    yesterday_brief.created_at = datetime.combine(date.today() - timedelta(days=1), datetime.min.time())
    db.commit()

    today_brief = proposal_writer.account_brief_singleton(db, diagnosis, AS_OF)
    assert today_brief.id != yesterday_brief.id
    assert db.query(NaverProposal).filter(NaverProposal.proposal_type == "account_brief").count() == 2
