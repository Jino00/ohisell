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
    # 시간 고정(freeze): 싱글톤은 date.today()로 '오늘 자정' 컷오프를 만들고 created_at >= 컷오프로
    # 오늘자 기존 브리프를 찾는다. 그러나 created_at은 func.now()(SQLite에선 UTC)라 KST 00:00~09:00
    # 사이엔 first.created_at이 '어제(UTC)'로 찍혀 재호출 시 기존 것을 못 찾고 중복 생성한다(날짜
    # 드리프트). date.today()를 AS_OF로 고정하고 created_at을 같은 날 정오로 결정적으로 찍어 제거한다.
    class _FrozenDate(date):
        @classmethod
        def today(cls):
            return AS_OF

    monkeypatch.setattr(proposal_writer, "date", _FrozenDate)

    diagnosis = _diagnosis(
        expansion_bucket={"cost_share": 0.3, "roas_corrected": 1.2},
        keyword_triage={"judgeable": 10, "growth_candidate": 2, "dead": 5},
        bleeding_keywords=[_bleeding_row()],
        starving_winners=[],
    )
    first = proposal_writer.account_brief_singleton(db, diagnosis, AS_OF)
    first.created_at = datetime.combine(AS_OF, datetime.min.time()).replace(hour=12)  # func.now()(UTC) 덮어쓰기
    db.commit()
    assert first.proposal_type == "account_brief"
    assert "as_of=2026-07-06" in first.rationale

    second = proposal_writer.account_brief_singleton(db, diagnosis, AS_OF)
    assert second.id == first.id  # 재호출해도 오늘자 기존 것 재사용(중복 생성 없음)
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
