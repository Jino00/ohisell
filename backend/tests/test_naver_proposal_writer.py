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


def test_build_bleeding_keyword_appends_forecast_evidence_when_available(db):
    """F2b ⓐ(D-NAO-26): 예측치가 있으면 rationale/expected_effect에 병기만(입찰산식 불변)."""
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis(bleeding_keywords=[_bleeding_row()])
    forecast_data = {("keyword", "nkw-1"): {"pred_clk": 40, "pred_cost": 90000, "pred_conv_amt": 15000}}

    out = proposal_writer.build(
        db, diagnosis, bid_sims={("keyword", "nkw-1"): _sim(direction="down")},
        forecast_data=forecast_data, as_of=AS_OF,
    )
    assert len(out) == 1
    assert "예측(오늘)" in out[0]["rationale"]
    assert "clk=40" in out[0]["rationale"]
    assert "cost=90000원" in out[0]["rationale"]


def test_build_bleeding_keyword_no_forecast_evidence_when_unavailable(db):
    """예측 없는(fallback/미가동) 타겟은 억지로 예측 텍스트를 붙이지 않는다(정직 경계)."""
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis(bleeding_keywords=[_bleeding_row()])

    out = proposal_writer.build(
        db, diagnosis, bid_sims={("keyword", "nkw-1"): _sim(direction="down")},
        forecast_data={}, as_of=AS_OF,
    )
    assert len(out) == 1
    assert "예측(오늘)" not in out[0]["rationale"]


def test_build_bleeding_keyword_wrong_direction_skipped(db):
    """codex 지적(라이브검증 후속): bleeding_keywords는 bid_down만 허용 — 표본이 얇아
    계층 수축으로 direction='up'이 나오면 억지 제안 대신 건너뛴다."""
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis(bleeding_keywords=[_bleeding_row()])

    out = proposal_writer.build(
        db, diagnosis, bid_sims={("keyword", "nkw-1"): _sim(direction="up", ceiling=500, recommended=500)},
        as_of=AS_OF,
    )
    assert out == []


def test_build_starving_winner_wrong_direction_skipped(db):
    """codex 지적: starving_winners는 bid_up만 허용 — direction='down'이면 건너뛴다
    (rank estimate를 걸러도 economic_ceiling 자체가 표본이 얇아 down이 나올 수 있음)."""
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    row = {"campaign_id": "cmp-ours", "adgroup_id": "grp-1", "keyword_id": "nkw-2",
           "cost": 5000, "clk": 3, "conv_amt": 50_000, "roas_corrected": 10.0, "avg_daily_clk": 0.1}
    diagnosis = _diagnosis(starving_winners=[row])

    out = proposal_writer.build(
        db, diagnosis, bid_sims={("keyword", "nkw-2"): _sim(direction="down", ceiling=50, recommended=50)},
        as_of=AS_OF,
    )
    assert out == []


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


def test_build_exclusion_includes_adgroup_id_and_persist_stores_it(db):
    """X1a T3: restricted-keywords API는 adgroupId 필수(ref 27 §8-1) — exclusion 제안 dict에
    adgroup_id가 실려 persist(NaverProposal(**p))로 컬럼까지 통과해야 실행 시점 재해석이 없다."""
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    row = {"campaign_id": "cmp-ours", "adgroup_id": "grp-1", "search_term": "무관검색어",
           "source": "expkeyword", "cost": 30000, "clk": 20, "imp": 400}
    diagnosis = _diagnosis(exclusion_candidates=[row])

    out = proposal_writer.build(db, diagnosis, as_of=AS_OF)
    assert out[0]["adgroup_id"] == "grp-1"

    saved = proposal_writer.persist(db, out)
    assert saved[0].adgroup_id == "grp-1"


def test_persist_other_types_store_null_adgroup_id(db):
    """adgroup_id 없는 제안 유형(bid 등)은 컬럼에 None 저장 — 실행 시 MissingExecutionTargetError로 fail-closed."""
    candidates = [{"proposal_type": "bid_down", "target_type": "keyword", "target_id": "nkw-1",
                   "campaign_id": "cmp-ours", "rationale": "r", "expected_effect": "e", "status": "pending"}]
    saved = proposal_writer.persist(db, candidates)
    assert saved[0].adgroup_id is None


# ── X1b T3: target_bid 구조화 저장 (D-NAO-38 갭①) ────────────────────────


def test_build_bleeding_keyword_stores_target_bid(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis(bleeding_keywords=[_bleeding_row()])

    out = proposal_writer.build(
        db, diagnosis, bid_sims={("keyword", "nkw-1"): _sim(direction="down", recommended=170)}, as_of=AS_OF,
    )
    assert out[0]["target_bid"] == 170

    saved = proposal_writer.persist(db, out)
    assert saved[0].target_bid == 170


def test_build_starving_winner_stores_target_bid(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    row = {"campaign_id": "cmp-ours", "adgroup_id": "grp-1", "keyword_id": "nkw-2",
           "cost": 5000, "clk": 3, "conv_amt": 50_000, "roas_corrected": 10.0, "avg_daily_clk": 0.1}
    diagnosis = _diagnosis(starving_winners=[row])

    out = proposal_writer.build(
        db, diagnosis, bid_sims={("keyword", "nkw-2"): _sim(direction="up", recommended=220)}, as_of=AS_OF,
    )
    assert out[0]["target_bid"] == 220


def test_build_growth_candidate_stores_target_bid(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis()

    out = proposal_writer.build(
        db, diagnosis, growth_candidates=[_growth_candidate()],
        growth_sims={("keyword", "nkw-growth"): _sim(direction="up", ceiling=500, recommended=470)},
        as_of=AS_OF,
    )
    assert out[0]["target_bid"] == 470


def test_build_negative_keyword_escalation_has_no_target_bid(db):
    """economic_ceiling<=0 격상 시엔 입찰 목표 자체가 없음 — target_bid는 None(구조상 당연,
    실행자가 target_bid를 읽지 않는 negative_keyword 액션이라 무해)."""
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis(bleeding_keywords=[_bleeding_row()])

    out = proposal_writer.build(
        db, diagnosis,
        bid_sims={("keyword", "nkw-1"): _sim(direction="down", ceiling=0, recommended=0)},
        as_of=AS_OF,
    )
    assert out[0]["proposal_type"] == "negative_keyword"
    assert out[0]["target_bid"] is None


# ── X1b T3: pause/resume 생성기 (D-NAO-38) ───────────────────────────────


def _pause_row(**overrides):
    row = {"campaign_id": "cmp-ours", "adgroup_id": "grp-1", "keyword_id": "nkw-pause",
           "imp": 500, "clk": 25, "cost": 2500, "conv_amt": 0, "roas_naver": None,
           "current_bid": 200, "stop_loss_amount": 2000}
    row.update(overrides)
    return row


def test_build_pause_candidate_produces_pause_proposal(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis(pause_candidates=[_pause_row()])

    out = proposal_writer.build(db, diagnosis, as_of=AS_OF)
    assert len(out) == 1
    assert out[0]["proposal_type"] == "pause"
    assert out[0]["target_type"] == "keyword"
    assert out[0]["target_id"] == "nkw-pause"
    assert out[0]["adgroup_id"] == "grp-1"
    assert out[0]["target_lock"] is True
    assert "스톱로스" in out[0]["rationale"]


def test_build_pause_candidate_skips_non_ours_campaign(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-none", optimizer="none"))
    db.commit()
    diagnosis = _diagnosis(pause_candidates=[_pause_row(campaign_id="cmp-none")])

    out = proposal_writer.build(db, diagnosis, as_of=AS_OF)
    assert out == []


def test_persist_pause_proposal_stores_target_lock_true(db):
    row = _pause_row()
    p = proposal_writer._pause_proposal(row)
    saved = proposal_writer.persist(db, [p])
    assert saved[0].target_lock is True
    assert saved[0].target_bid is None


def _resume_row(**overrides):
    row = {"campaign_id": "cmp-ours", "adgroup_id": "grp-1", "keyword_id": "nkw-resume",
           "roas_at_pause": 5.0, "paused_at": "2026-07-01T00:00:00"}
    row.update(overrides)
    return row


def test_build_resume_candidate_produces_resume_proposal(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis(resume_candidates=[_resume_row()])

    out = proposal_writer.build(db, diagnosis, as_of=AS_OF)
    assert len(out) == 1
    assert out[0]["proposal_type"] == "resume"
    assert out[0]["target_type"] == "keyword"
    assert out[0]["target_id"] == "nkw-resume"
    assert out[0]["target_lock"] is False
    assert "BEP 개선" in out[0]["rationale"]


def test_build_resume_candidate_skips_non_ours_campaign(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-none", optimizer="none"))
    db.commit()
    diagnosis = _diagnosis(resume_candidates=[_resume_row(campaign_id="cmp-none")])

    out = proposal_writer.build(db, diagnosis, as_of=AS_OF)
    assert out == []


def test_persist_resume_proposal_stores_target_lock_false(db):
    row = _resume_row()
    p = proposal_writer._resume_proposal(row, {"source": "account_default", "target_roas": None})
    saved = proposal_writer.persist(db, [p])
    assert saved[0].target_lock is False
    assert saved[0].target_bid is None


# ── X1b-S S1: 쇼핑 adgroup pause/resume 생성기 (D-NAO-43) ─────────────────


def _shopping_pause_row(**overrides):
    row = {"campaign_id": "cmp-ours", "adgroup_id": "grp-shop-1", "cost": 2500, "conv_amt": 0,
           "current_bid": 200, "stop_loss_amount": 2000}
    row.update(overrides)
    return row


def test_build_shopping_pause_candidate_produces_pause_proposal_targeting_adgroup(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis(shopping_pause_candidates=[_shopping_pause_row()])

    out = proposal_writer.build(db, diagnosis, as_of=AS_OF)
    assert len(out) == 1
    assert out[0]["proposal_type"] == "pause"
    assert out[0]["target_type"] == "adgroup"
    assert out[0]["target_id"] == "grp-shop-1"
    assert out[0]["target_lock"] is True
    assert "스톱로스" in out[0]["rationale"]


def test_build_shopping_pause_candidate_skips_non_ours_campaign(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-none", optimizer="none"))
    db.commit()
    diagnosis = _diagnosis(shopping_pause_candidates=[_shopping_pause_row(campaign_id="cmp-none")])

    out = proposal_writer.build(db, diagnosis, as_of=AS_OF)
    assert out == []


def test_persist_shopping_pause_proposal_stores_adgroup_target_type_and_lock(db):
    row = _shopping_pause_row()
    p = proposal_writer._pause_proposal(row, target_type="adgroup")
    assert p["target_type"] == "adgroup"
    assert p["target_id"] == "grp-shop-1"
    saved = proposal_writer.persist(db, [p])
    assert saved[0].target_type == "adgroup"
    assert saved[0].target_id == "grp-shop-1"
    assert saved[0].target_lock is True
    assert saved[0].target_bid is None


def _shopping_resume_row(**overrides):
    row = {"campaign_id": "cmp-ours", "adgroup_id": "grp-shop-1",
           "roas_at_pause": 5.0, "paused_at": "2026-07-01T00:00:00"}
    row.update(overrides)
    return row


def test_build_shopping_resume_candidate_produces_resume_proposal_targeting_adgroup(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis(shopping_resume_candidates=[_shopping_resume_row()])

    out = proposal_writer.build(db, diagnosis, as_of=AS_OF)
    assert len(out) == 1
    assert out[0]["proposal_type"] == "resume"
    assert out[0]["target_type"] == "adgroup"
    assert out[0]["target_id"] == "grp-shop-1"
    assert out[0]["target_lock"] is False
    assert "BEP 개선" in out[0]["rationale"]


def test_build_shopping_resume_candidate_skips_non_ours_campaign(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-none", optimizer="none"))
    db.commit()
    diagnosis = _diagnosis(shopping_resume_candidates=[_shopping_resume_row(campaign_id="cmp-none")])

    out = proposal_writer.build(db, diagnosis, as_of=AS_OF)
    assert out == []


def test_persist_shopping_resume_proposal_stores_adgroup_target_type_and_lock(db):
    row = _shopping_resume_row()
    p = proposal_writer._resume_proposal(row, {"source": "account_default", "target_roas": None}, target_type="adgroup")
    assert p["target_type"] == "adgroup"
    assert p["target_id"] == "grp-shop-1"
    saved = proposal_writer.persist(db, [p])
    assert saved[0].target_type == "adgroup"
    assert saved[0].target_id == "grp-shop-1"
    assert saved[0].target_lock is False
    assert saved[0].target_bid is None


# ── X1b-S S3: 쇼핑 adgroup 성장(bid_up) 생성기 (D-NAO-43 확장) ─────────────
# shopping_group_bep(적자, down)의 반대 — shopping_group_growth 보드(수익 그룹) →
# bid_up 제안. _bid_proposal 공용 경로(target_type="adgroup")를 그대로 쓴다(shopping_group_bep
# 과 동일 배선, board_name만 다름 — _ALLOWED_DIRECTIONS가 방향을 강제).


def _growth_group_row(**overrides):
    row = {"campaign_id": "cmp-ours", "adgroup_id": "grp-shop-growth", "cost": 8000,
           "conv_amt": 40000, "roas_naver": 5.0, "roas_corrected": 5.0}
    row.update(overrides)
    return row


def test_build_shopping_group_growth_produces_bid_up_targeting_adgroup(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis(shopping_group_growth=[_growth_group_row()])

    out = proposal_writer.build(
        db, diagnosis, bid_sims={("adgroup", "grp-shop-growth"): _sim(direction="up")}, as_of=AS_OF,
    )
    assert len(out) == 1
    assert out[0]["proposal_type"] == "bid_up"
    assert out[0]["target_type"] == "adgroup"
    assert out[0]["target_id"] == "grp-shop-growth"
    assert "target_roas 근거=" in out[0]["rationale"]


def test_build_shopping_group_growth_stores_target_bid(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis(shopping_group_growth=[_growth_group_row()])

    out = proposal_writer.build(
        db, diagnosis,
        bid_sims={("adgroup", "grp-shop-growth"): _sim(direction="up", recommended=1300)}, as_of=AS_OF,
    )
    assert out[0]["target_bid"] == 1300

    saved = proposal_writer.persist(db, out)
    assert saved[0].target_bid == 1300
    assert saved[0].target_type == "adgroup"


def test_build_shopping_group_growth_wrong_direction_skipped(db):
    """shopping_group_growth는 bid_up만 허용(_ALLOWED_DIRECTIONS) — 표본이 얇아 계층
    수축으로 direction='down'이 나오면 성장 의도와 반대라 억지 제안 대신 건너뛴다."""
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis(shopping_group_growth=[_growth_group_row()])

    out = proposal_writer.build(
        db, diagnosis,
        bid_sims={("adgroup", "grp-shop-growth"): _sim(direction="down", ceiling=50, recommended=50)},
        as_of=AS_OF,
    )
    assert out == []


def test_build_shopping_group_growth_skips_non_ours_campaign(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-none", optimizer="none"))
    db.commit()
    diagnosis = _diagnosis(shopping_group_growth=[_growth_group_row(campaign_id="cmp-none")])

    out = proposal_writer.build(
        db, diagnosis, bid_sims={("adgroup", "grp-shop-growth"): _sim(direction="up")}, as_of=AS_OF,
    )
    assert out == []


def test_build_shopping_group_growth_no_sim_available_skips_row(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis(shopping_group_growth=[_growth_group_row()])
    out = proposal_writer.build(db, diagnosis, bid_sims={}, as_of=AS_OF)  # sim 없음
    assert out == []


def test_pause_proposal_defaults_to_keyword_target_type_unchanged(db):
    """회귀 방지 — target_type 인자를 안 넘기면 기존 keyword 경로와 완전히 동일해야 한다."""
    row = _pause_row()
    p = proposal_writer._pause_proposal(row)
    assert p["target_type"] == "keyword"
    assert p["target_id"] == "nkw-pause"
    assert p["adgroup_id"] == "grp-1"


def test_resume_proposal_defaults_to_keyword_target_type_unchanged(db):
    row = _resume_row()
    p = proposal_writer._resume_proposal(row, {"source": "account_default", "target_roas": None})
    assert p["target_type"] == "keyword"
    assert p["target_id"] == "nkw-resume"
    assert p["adgroup_id"] == "grp-1"


def test_persist_dedup_scoped_by_adgroup_same_term_different_adgroup_both_saved(db):
    """[codex P2] 같은 검색어·같은 캠페인이라도 adgroup이 다르면 별개 실행 대상(restricted-
    keywords는 광고그룹 단위 리소스) — dedup 키에 adgroup_id 포함. 같은 adgroup 재실행은 dedup."""
    base = {"proposal_type": "negative_keyword", "target_type": "search_term",
            "target_id": "같은검색어", "campaign_id": "cmp-a",
            "rationale": "r", "expected_effect": "e", "status": "pending"}
    first = proposal_writer.persist(db, [dict(base, adgroup_id="grp-1")])
    assert len(first) == 1
    db.commit()

    # 다른 adgroup — 별개 제안으로 저장돼야 함
    second = proposal_writer.persist(db, [dict(base, adgroup_id="grp-2")])
    assert len(second) == 1
    db.commit()

    # 같은 adgroup 재실행 — dedup
    third = proposal_writer.persist(db, [dict(base, adgroup_id="grp-1")])
    assert third == []
    assert db.query(NaverProposal).filter(NaverProposal.target_id == "같은검색어").count() == 2


def test_persist_dedups_existing_pending_same_type_and_target(db):
    db.add(NaverProposal(proposal_type="bid_down", target_type="keyword", target_id="nkw-1",
                          campaign_id="cmp-ours", status="pending"))
    db.commit()

    candidates = [{"proposal_type": "bid_down", "target_type": "keyword", "target_id": "nkw-1",
                   "campaign_id": "cmp-ours", "rationale": "r", "expected_effect": "e", "status": "pending"}]
    saved = proposal_writer.persist(db, candidates)
    assert saved == []
    assert db.query(NaverProposal).filter(NaverProposal.target_id == "nkw-1").count() == 1


def test_persist_dedup_scoped_by_campaign_not_cross_campaign(db):
    """codex 지적(라이브검증 후속): search_term 같은 target_id가 다른 캠페인에도 나올 수
    있음 — campaign_id를 dedup key에서 빼면 서로 다른 캠페인의 제안이 충돌한다."""
    db.add(NaverProposal(proposal_type="negative_keyword", target_type="search_term", target_id="같은검색어",
                          campaign_id="cmp-a", status="pending"))
    db.commit()

    candidates = [{"proposal_type": "negative_keyword", "target_type": "search_term", "target_id": "같은검색어",
                   "campaign_id": "cmp-b", "rationale": "r", "expected_effect": "e", "status": "pending"}]
    saved = proposal_writer.persist(db, candidates)
    assert len(saved) == 1  # 다른 캠페인이라 dedup 대상 아님 — 정상 저장
    assert db.query(NaverProposal).filter(NaverProposal.target_id == "같은검색어").count() == 2


def test_persist_allows_new_target_and_allows_after_previous_resolved(db):
    db.add(NaverProposal(proposal_type="bid_down", target_type="keyword", target_id="nkw-1",
                          campaign_id="cmp-ours", status="approved"))  # 더 이상 pending 아님
    db.commit()

    candidates = [{"proposal_type": "bid_down", "target_type": "keyword", "target_id": "nkw-1",
                   "campaign_id": "cmp-ours", "rationale": "r", "expected_effect": "e", "status": "pending"}]
    saved = proposal_writer.persist(db, candidates)
    assert len(saved) == 1  # 기존 건은 approved라 dedup 대상 아님


def test_account_brief_singleton_created_once_per_day(db, monkeypatch):
    # 싱글톤은 kst_today()로 'KST 오늘'의 UTC 자정 컷오프를 만들고 created_at(=func.now()=UTC)
    # >= 컷오프로 오늘자 브리프를 찾는다. kst_today를 AS_OF로 고정하고 first.created_at을
    # **08:00 KST 실행 시각(=AS_OF 전날 23:00 UTC, UTC 자정을 넘는 straddle)**으로 찍어,
    # 같은 KST일 재호출이 dedup되는지 검증한다(2026-07-13 실측 UTC/KST 중복 버그 id502·544 회귀:
    # 구 date.today() 컷오프였다면 여기서 못 찾고 중복 생성됐다).
    monkeypatch.setattr(proposal_writer, "kst_today", lambda: AS_OF)

    diagnosis = _diagnosis(
        expansion_bucket={"cost_share": 0.3, "roas_corrected": 1.2},
        keyword_triage={"judgeable": 10, "growth_candidate": 2, "dead": 5},
        bleeding_keywords=[_bleeding_row()],
        starving_winners=[],
    )
    first = proposal_writer.account_brief_singleton(db, diagnosis, AS_OF)
    # 08:00 KST(AS_OF) = AS_OF 전날 23:00 UTC — UTC 자정 이전이라 구버그의 트리거 지점.
    first.created_at = datetime.combine(AS_OF, datetime.min.time()) - timedelta(hours=1)
    db.commit()
    assert first.proposal_type == "account_brief"
    assert "as_of=2026-07-06" in first.rationale

    second = proposal_writer.account_brief_singleton(db, diagnosis, AS_OF)
    assert second.id == first.id  # 같은 KST일 재호출은 기존 것 재사용(중복 생성 없음)
    assert db.query(NaverProposal).filter(NaverProposal.proposal_type == "account_brief").count() == 1


# ── growth_sweeper 연동 (듀얼모드 스프린트 Phase 2, D-NAO-22-①) ──
def _growth_candidate(**overrides):
    row = {"keyword_id": "nkw-growth", "campaign_id": "cmp-ours", "adgroup_id": "grp-1",
           "current_bid": 100, "economic_ceiling": 500, "gap": 400, "clk": 100, "conv_amt": 100_000,
           "sample_thin": False}
    row.update(overrides)
    return row


def test_build_growth_candidate_produces_growth_bid_up(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis()

    out = proposal_writer.build(
        db, diagnosis,
        growth_candidates=[_growth_candidate()],
        growth_sims={("keyword", "nkw-growth"): _sim(direction="up", ceiling=500, recommended=470)},
        as_of=AS_OF,
    )
    assert len(out) == 1
    assert out[0]["proposal_type"] == "growth_bid_up"
    assert out[0]["target_type"] == "keyword"
    assert out[0]["target_id"] == "nkw-growth"
    assert "D-NAO-20 스톱로스=" in out[0]["rationale"]
    assert "갭=400원" in out[0]["rationale"]


def test_build_growth_candidate_appends_forecast_evidence_when_available(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis()
    forecast_data = {("keyword", "nkw-growth"): {"pred_clk": 120, "pred_cost": 45000, "pred_conv_amt": 90000}}

    out = proposal_writer.build(
        db, diagnosis,
        growth_candidates=[_growth_candidate()],
        growth_sims={("keyword", "nkw-growth"): _sim(direction="up", ceiling=500, recommended=470)},
        forecast_data=forecast_data, as_of=AS_OF,
    )
    assert len(out) == 1
    assert "예측(오늘)" in out[0]["rationale"]
    assert "conv_amt=90000원" in out[0]["rationale"]


def test_build_growth_candidate_skips_non_ours_campaign(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-none", optimizer="none"))
    db.commit()
    diagnosis = _diagnosis()

    out = proposal_writer.build(
        db, diagnosis,
        growth_candidates=[_growth_candidate(campaign_id="cmp-none")],
        growth_sims={("keyword", "nkw-growth"): _sim(direction="up")},
        as_of=AS_OF,
    )
    assert out == []


def test_build_growth_candidate_wrong_direction_skipped(db):
    """growth_sweeper는 up만 허용 — 표본이 얇아 계층 수축으로 down/hold가 나오면 건너뜀."""
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis()

    out = proposal_writer.build(
        db, diagnosis,
        growth_candidates=[_growth_candidate()],
        growth_sims={("keyword", "nkw-growth"): _sim(direction="hold")},
        as_of=AS_OF,
    )
    assert out == []


def test_build_growth_candidate_no_sim_skipped(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis()

    out = proposal_writer.build(
        db, diagnosis, growth_candidates=[_growth_candidate()], growth_sims={}, as_of=AS_OF,
    )
    assert out == []


def test_build_growth_candidates_capped_at_growth_proposal_cap(db, monkeypatch):
    from app.services.naver_ad import growth_sweeper
    monkeypatch.setattr(growth_sweeper, "GROWTH_PROPOSAL_CAP", 2)

    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis()
    candidates = [_growth_candidate(keyword_id=f"nkw-{i}") for i in range(5)]
    sims = {("keyword", f"nkw-{i}"): _sim(direction="up") for i in range(5)}

    out = proposal_writer.build(db, diagnosis, growth_candidates=candidates, growth_sims=sims, as_of=AS_OF)
    assert len(out) == 2  # 캡(2)에서 멈춤 — 나머지 3건은 다음 회차로 이월(생성 자체를 안 함)


# ── budget_allocator + anomaly_feed 연동 (듀얼모드 스프린트 Phase 3, D-NAO-22-③/④) ──
def _budget_signal(**overrides):
    row = {"campaign_id": "cmp-ours", "campaign_type": "WEB_SITE", "daily_budget": 10000,
           "cost": 10000, "hour": 14, "growth_candidate_count": 3, "total_gap": 900}
    row.update(overrides)
    return row


def test_build_budget_signal_produces_budget_up(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis()

    out = proposal_writer.build(db, diagnosis, budget_signals=[_budget_signal()], as_of=AS_OF)
    assert len(out) == 1
    assert out[0]["proposal_type"] == "budget_up"
    assert out[0]["target_type"] == "campaign"
    assert out[0]["target_id"] == "cmp-ours"
    assert "인과추정 없음" in out[0]["expected_effect"]


def test_build_budget_signal_skips_non_ours_campaign(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-none", optimizer="none"))
    db.commit()
    diagnosis = _diagnosis()

    out = proposal_writer.build(
        db, diagnosis, budget_signals=[_budget_signal(campaign_id="cmp-none")], as_of=AS_OF,
    )
    assert out == []


def _pre_exhaustion_signal(**overrides):
    row = {"campaign_id": "cmp-ours", "campaign_type": "WEB_SITE", "daily_budget": 10000,
           "cost": 3000, "hour": 10, "pred_cost": 12000, "pred_gap": 2000}
    row.update(overrides)
    return row


def test_build_pre_exhaustion_signal_produces_informational_proposal(db):
    """F2b ⓑ: 사전경보는 정보성 제안(anomaly와 동일 취급) — 실행 대상 아님."""
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis()

    out = proposal_writer.build(db, diagnosis, pre_exhaustion_signals=[_pre_exhaustion_signal()], as_of=AS_OF)
    assert len(out) == 1
    assert out[0]["proposal_type"] == "budget_pre_exhaustion"
    assert out[0]["target_type"] == "campaign"
    assert out[0]["target_id"] == "cmp-ours"
    assert "실행 대상 아님" in out[0]["expected_effect"]
    assert "12000원" in out[0]["rationale"]


def test_build_pre_exhaustion_signal_skips_non_ours_campaign(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-none", optimizer="none"))
    db.commit()
    diagnosis = _diagnosis()

    out = proposal_writer.build(
        db, diagnosis, pre_exhaustion_signals=[_pre_exhaustion_signal(campaign_id="cmp-none")], as_of=AS_OF,
    )
    assert out == []


def test_build_anomaly_spend_proposal_ignores_ours_filter(db):
    """anomaly_feed는 진단 성격이라 optimizer 무관(전 캠페인 대상) — cmp-settings가 아예 없어도 생성돼야 함."""
    diagnosis = _diagnosis()
    anomalies = {"spend": [{"campaign_id": "cmp-any", "as_of": "2026-07-06", "prior_date": "2026-07-05",
                             "cost_today": 50000, "cost_prior": 5000, "ratio": 10.0, "kind": "spike"}]}

    out = proposal_writer.build(db, diagnosis, anomalies=anomalies, as_of=AS_OF)
    assert len(out) == 1
    assert out[0]["proposal_type"] == "anomaly"
    assert "급증" in out[0]["rationale"]


def test_build_anomaly_freshness_only_when_partial(db):
    diagnosis = _diagnosis()
    not_partial = {"freshness": {"partial": False, "as_of": "2026-07-06", "as_of_count": 10,
                                  "baseline_avg": 10.0, "ratio": 1.0, "reason": "정상"}}
    out = proposal_writer.build(db, diagnosis, anomalies=not_partial, as_of=AS_OF)
    assert out == []

    partial = {"freshness": {"partial": True, "as_of": "2026-07-06", "as_of_count": 2,
                              "baseline_avg": 10.0, "ratio": 0.2, "reason": "부분적재 의심"}}
    out2 = proposal_writer.build(db, diagnosis, anomalies=partial, as_of=AS_OF)
    assert len(out2) == 1
    assert out2[0]["proposal_type"] == "anomaly_freshness"


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


# ── P3(D-NAO-42-f): 예산 사이징(_size_budget_up, §5-G) ──────────────────────────


def test_size_budget_up_pred_cost_clamped_to_plus_100_pct():
    """pred_cost가 current×2를 넘으면 +100% 캡(계획서 §2③)에서 클램프된다."""
    assert proposal_writer._size_budget_up(10_000, 50_000) == 20_000


def test_size_budget_up_pred_cost_used_when_within_range():
    """pred_cost가 current+1증분~current×2 사이면 그대로(추세 반영) 채택."""
    assert proposal_writer._size_budget_up(10_000, 15_000) == 15_000


def test_size_budget_up_pred_cost_floored_at_current_plus_one_increment():
    """pred_cost가 current보다 조금만 높으면(증분 미만) 최소 +1증분(100원)까지는 올린다."""
    assert proposal_writer._size_budget_up(10_000, 10_050) == 10_100


def test_size_budget_up_fallback_plus_20_pct_when_no_forecast():
    """예측 없음(fallback) — Jino 확정 +20%(2026-07-13)."""
    assert proposal_writer._size_budget_up(10_000, None) == 12_000


def test_size_budget_up_rounds_to_nearest_100():
    """100원 단위로 반올림(네이버 침묵 반올림 방어) — 10333×1.2=12399.6 → 12400."""
    assert proposal_writer._size_budget_up(10_333, None) == 12_400


def test_size_budget_up_never_leq_current_when_rounding_collapses():
    """200×1.2=240 → 100원 단위 반올림하면 200(=current)으로 떨어짐 — 방향 불일치를
    막기 위해 다음 100원 단위(300)로 올린다."""
    assert proposal_writer._size_budget_up(200, None) == 300


# ── P3(D-NAO-42-f): 라운드 봉투(_classify_budget_round_envelope, §5-E) ──────────


def test_round_envelope_all_fit_within_cap():
    candidates = [{"target_budget": 0} for _ in range(3)]
    deltas = [(30_000, candidates[0]), (30_000, candidates[1]), (30_000, candidates[2])]
    proposal_writer._classify_budget_round_envelope(deltas)
    assert [c["budget_auto_eligible"] for c in candidates] == [True, True, True]


def test_round_envelope_marks_overflow_false_at_boundary():
    """합이 10만원을 넘는 순간부터 False — 그 뒤도 계속 False가 아니라, 남은 여유에
    들어가는 더 작은 후보는 다시 True가 될 수 있다(그리디 순차 소비)."""
    c1, c2, c3 = {}, {}, {}
    deltas = [(90_000, c1), (20_000, c2), (5_000, c3)]
    # c1: 누적 90,000 ≤ 100,000 → True
    # c2: 누적 90,000+20,000=110,000 > 100,000 → False(누적은 갱신되지 않음, 90,000 유지)
    # c3: 90,000+5,000=95,000 ≤ 100,000 → True(뒤가 앞선 거부 후 남은 여유에 들어감)
    proposal_writer._classify_budget_round_envelope(deltas)
    assert c1["budget_auto_eligible"] is True
    assert c2["budget_auto_eligible"] is False
    assert c3["budget_auto_eligible"] is True


def test_round_envelope_exact_boundary_is_eligible():
    """정확히 10만원 도달(초과 아님)은 True."""
    c1 = {}
    proposal_writer._classify_budget_round_envelope([(100_000, c1)])
    assert c1["budget_auto_eligible"] is True


def test_round_envelope_single_over_cap_candidate_is_false():
    c1 = {}
    proposal_writer._classify_budget_round_envelope([(100_001, c1)])
    assert c1["budget_auto_eligible"] is False


# ── Fix 6(codex P2): 라운드 봉투가 caller 순서에 의존하지 않고 self-sort ──────────


def test_round_envelope_self_sorts_by_total_gap_regardless_of_caller_order():
    """caller가 total_gap 오름차순(우선순위 역순)으로 넘겨도, 함수가 스스로 내림차순
    정렬한 후 그리디 누적해야 한다 — test_round_envelope_marks_overflow_false_at_boundary
    와 동일한 90,000/20,000/5,000 경계 수치를 쓰되, 입력 순서만 뒤집는다."""
    c1 = {"total_gap": 900}  # 최우선(가장 큰 gap), delta=90,000
    c2 = {"total_gap": 500}  # 2순위, delta=20,000
    c3 = {"total_gap": 100}  # 최하위(가장 작은 gap), delta=5,000
    # 일부러 우선순위 역순(작은 gap부터)으로 전달
    deltas = [(5_000, c3), (20_000, c2), (90_000, c1)]

    proposal_writer._classify_budget_round_envelope(deltas)

    assert c1["budget_auto_eligible"] is True   # 90,000 ≤ 100,000
    assert c2["budget_auto_eligible"] is False  # 90,000+20,000=110,000 > 100,000
    assert c3["budget_auto_eligible"] is True   # 90,000+5,000=95,000 ≤ 100,000(그리디 소비)


def test_round_envelope_missing_total_gap_defaults_to_zero_preserves_legacy_order():
    """total_gap 키가 없는 legacy 호출(직접 dict 전달)은 0으로 취급 — 전부 동률이라 안정
    정렬로 원래 순서가 보존돼 기존 계약(test_round_envelope_all_fit_within_cap 등)과
    100% 호환된다."""
    candidates = [{} for _ in range(3)]
    deltas = [(30_000, candidates[0]), (30_000, candidates[1]), (30_000, candidates[2])]
    proposal_writer._classify_budget_round_envelope(deltas)
    assert [c["budget_auto_eligible"] for c in candidates] == [True, True, True]


# ── P3(D-NAO-42-f): build() 라운드 봉투 + 사이징 배선 ────────────────────────────


def test_build_budget_up_sets_target_budget_from_sizer(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis()

    out = proposal_writer.build(db, diagnosis, budget_signals=[_budget_signal(daily_budget=10_000)], as_of=AS_OF)
    assert len(out) == 1
    assert out[0]["target_budget"] == 12_000  # fallback +20%(예측 없음)


def test_build_budget_up_uses_campaign_forecast_for_sizing(db):
    """forecast_data에 ("campaign", campaign_id) grain이 있으면 사이징에 반영(§5-G)."""
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis()
    forecast_data = {("campaign", "cmp-ours"): {"pred_clk": 0, "pred_cost": 18_000, "pred_conv_amt": 0}}

    out = proposal_writer.build(
        db, diagnosis, budget_signals=[_budget_signal(daily_budget=10_000)],
        forecast_data=forecast_data, as_of=AS_OF,
    )
    assert len(out) == 1
    assert out[0]["target_budget"] == 18_000
    assert "예측(오늘)" in out[0]["rationale"]


def test_build_budget_up_round_envelope_classifies_by_total_gap_priority(db):
    """budget_signals는 이미 total_gap 내림차순 — build()가 그 순서 그대로 그리디 분류한다.
    daily_budget=90,000(fallback +20%→108,000, delta=18,000)이 1순위, 45,000(→54,000,
    delta=9,000)이 2순위: 18,000+9,000=27,000 ≤ 10만 → 둘 다 True."""
    db.add(NaverCampaignSettings(campaign_id="cmp-a", optimizer="ours"))
    db.add(NaverCampaignSettings(campaign_id="cmp-b", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis()
    signals = [
        _budget_signal(campaign_id="cmp-a", daily_budget=90_000, total_gap=900),
        _budget_signal(campaign_id="cmp-b", daily_budget=45_000, total_gap=100),
    ]

    out = proposal_writer.build(db, diagnosis, budget_signals=signals, as_of=AS_OF)
    by_cid = {p["campaign_id"]: p for p in out}
    assert by_cid["cmp-a"]["budget_auto_eligible"] is True
    assert by_cid["cmp-b"]["budget_auto_eligible"] is True


def test_build_budget_up_round_envelope_overflow_marks_false(db):
    """1순위 캠페인 단독으로 이미 10만원을 넘으면 False — 그 다음 더 작은 후보는 남은
    여유(=10만 그대로, 1순위가 소비 안 됨)에 들어가 True가 될 수 있다."""
    db.add(NaverCampaignSettings(campaign_id="cmp-a", optimizer="ours"))
    db.add(NaverCampaignSettings(campaign_id="cmp-b", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis()
    # cmp-a: daily_budget=600,000 → fallback +20%=720,000, delta=120,000(>10만 단독 초과)
    # cmp-b: daily_budget=10,000 → fallback +20%=12,000, delta=2,000(단독으로는 10만 이내)
    signals = [
        _budget_signal(campaign_id="cmp-a", daily_budget=600_000, total_gap=900),
        _budget_signal(campaign_id="cmp-b", daily_budget=10_000, total_gap=100),
    ]

    out = proposal_writer.build(db, diagnosis, budget_signals=signals, as_of=AS_OF)
    by_cid = {p["campaign_id"]: p for p in out}
    assert by_cid["cmp-a"]["budget_auto_eligible"] is False
    assert by_cid["cmp-b"]["budget_auto_eligible"] is True


def test_build_budget_up_round_envelope_self_sorts_when_signals_passed_unsorted(db):
    """Fix 6(codex P2): build()가 budget_allocator의 정렬을 무조건 신뢰하지 않는다 —
    signals를 일부러 gap 오름차순(최하위 캠페인 먼저)으로 넘겨도
    test_build_budget_up_round_envelope_overflow_marks_false와 동일한 결과가 나와야 한다.
    fallback +20% 사이징: 450,000→540,000(Δ90,000) / 100,000→120,000(Δ20,000) /
    25,000→30,000(Δ5,000) — 90k+20k=110k>10만(cmp-b는 False), 90k+5k=95k≤10만(cmp-c는 True)."""
    for cid in ("cmp-a", "cmp-b", "cmp-c"):
        db.add(NaverCampaignSettings(campaign_id=cid, optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis()
    signals = [
        _budget_signal(campaign_id="cmp-c", daily_budget=25_000, total_gap=100),   # 최하위, 먼저 나열
        _budget_signal(campaign_id="cmp-b", daily_budget=100_000, total_gap=500),
        _budget_signal(campaign_id="cmp-a", daily_budget=450_000, total_gap=900),  # 최우선, 나중에 나열
    ]

    out = proposal_writer.build(db, diagnosis, budget_signals=signals, as_of=AS_OF)
    by_cid = {p["campaign_id"]: p for p in out}
    assert by_cid["cmp-a"]["budget_auto_eligible"] is True
    assert by_cid["cmp-b"]["budget_auto_eligible"] is False
    assert by_cid["cmp-c"]["budget_auto_eligible"] is True
    # threading용 임시 키는 최종 제안 dict에 남으면 안 됨(persist가 NaverProposal(**p)로
    # 그대로 언패킹하는데 total_gap은 컬럼이 아니라서 TypeError가 남).
    assert "total_gap" not in by_cid["cmp-a"]
    assert "total_gap" not in by_cid["cmp-b"]
    assert "total_gap" not in by_cid["cmp-c"]


def test_build_budget_down_leaves_budget_auto_eligible_none(db):
    """budget_down 경로는 아직 생성기가 없음(§5-G "감액은 별도 신호, Phase3 선택") —
    budget_up만 봉투 분류 대상이므로 다른 유형(bid_down 등)은 budget_auto_eligible이
    build() 단계에서 아예 세팅되지 않는다(None, persist가 컬럼 기본값으로 남김)."""
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis(bleeding_keywords=[_bleeding_row()])

    out = proposal_writer.build(
        db, diagnosis, bid_sims={("keyword", "nkw-1"): _sim(direction="down")}, as_of=AS_OF,
    )
    assert len(out) == 1
    assert "budget_auto_eligible" not in out[0]


# ── P3(D-NAO-42-f): persist()가 target_budget/budget_auto_eligible을 그대로 저장 ──


def test_persist_saves_target_budget_and_budget_auto_eligible(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis()

    candidates = proposal_writer.build(
        db, diagnosis, budget_signals=[_budget_signal(daily_budget=10_000)], as_of=AS_OF,
    )
    saved = proposal_writer.persist(db, candidates)
    db.commit()
    assert len(saved) == 1
    assert saved[0].target_budget == 12_000
    assert saved[0].budget_auto_eligible is True


def test_persist_non_budget_proposal_leaves_budget_columns_none(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.commit()
    diagnosis = _diagnosis(bleeding_keywords=[_bleeding_row()])

    candidates = proposal_writer.build(
        db, diagnosis, bid_sims={("keyword", "nkw-1"): _sim(direction="down")}, as_of=AS_OF,
    )
    saved = proposal_writer.persist(db, candidates)
    db.commit()
    assert len(saved) == 1
    assert saved[0].target_budget is None
    assert saved[0].budget_auto_eligible is None
