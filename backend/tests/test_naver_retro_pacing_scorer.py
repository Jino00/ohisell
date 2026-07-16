# test_naver_retro_pacing_scorer.py — D-NAO-45 retro_pacing_scorer SA 단위 테스트
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverProposal, NaverRetroPacingScore
from app.services.naver_ad import retro_pacing_scorer
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.services.naver_ad.trigger_watch import PROPOSAL_TYPE_PACING

ALERT_DATE = date(2026, 7, 16)


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


def _pacing_proposal(db, *, campaign_id="cmp1", ad_date=ALERT_DATE, hour=12, spent=5000,
                      budget=50000, ratio=0.5, kind="저속"):
    """trigger_watch._pacing_proposal이 실제로 남기는 rationale 텍스트 형식(ref 31 §2)."""
    kind_label = "과속(예산 조기소진 우려)" if kind == "과속" else "저속(예산 소진 지연)"
    rationale = (
        f"[trigger_watch] 페이싱 이탈 {kind_label} — {ad_date.isoformat()} {hour}시 기준 "
        f"소진 {spent}원/{budget}원(실제페이스=0.1, 기대페이스=0.2(예측곡선 있으면 그걸, "
        f"없으면 선형폴백), 배수={ratio})."
    )
    p = NaverProposal(
        proposal_type=PROPOSAL_TYPE_PACING, target_type="campaign", target_id=campaign_id,
        campaign_id=campaign_id, rationale=rationale, status="pending",
    )
    db.add(p)
    db.flush()
    return p


def _sentinel(db, campaign_id, ad_date, cost):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type="WEB_SITE",
        adgroup_id=BACKFILL_SENTINEL_ADGROUP, keyword_id="",
        imp=1, clk=1, cost=cost, rank_sum=0,
    ))


# ── 정규식 파싱 ──
def test_score_alerts_parses_real_rationale_format(db):
    _pacing_proposal(db, campaign_id="cmp1", ad_date=ALERT_DATE, hour=12, spent=5000, budget=50000, ratio=0.5)
    _sentinel(db, "cmp1", ALERT_DATE, 10000)  # 최종 10000/50000 = 0.2 < 0.5 → correct
    db.commit()

    result = retro_pacing_scorer.score_alerts(db, ALERT_DATE)
    assert result == {"scored": 1, "unparsed": 0, "skipped": 0}
    row = db.query(NaverRetroPacingScore).one()
    assert row.alert_date == ALERT_DATE
    assert row.campaign_id == "cmp1"
    assert row.kind == "저속"
    assert row.alert_hour == 12
    assert row.spent == 5000
    assert row.budget == 50000
    assert row.multiple == 0.5
    assert row.final_cost == 10000
    assert row.final_ratio == pytest.approx(0.2)
    assert row.verdict == "correct"
    assert row.scored_at is not None


# ── 버킷 경계(ref 31 §2) ──
@pytest.mark.parametrize("kind,final_cost,budget,expected", [
    ("저속", 45000, 50000, "false_alarm"),  # 0.9 정확히 → false_alarm(>=0.9)
    ("저속", 44999, 50000, "partial"),      # 0.89998 → partial
    ("저속", 25000, 50000, "partial"),      # 0.5 정확히 → partial(>=0.5)
    ("저속", 24999, 50000, "correct"),      # <0.5 → correct
    ("과속", 55000, 50000, "correct"),      # 1.1 정확히 → correct(>=1.1)
    ("과속", 54999, 50000, "partial"),      # <1.1 → partial
    ("과속", 45000, 50000, "partial"),      # 0.9 정확히 → partial(>=0.9)
    ("과속", 44999, 50000, "false_alarm"),  # <0.9 → false_alarm
])
def test_score_alerts_bucket_boundaries(db, kind, final_cost, budget, expected):
    _pacing_proposal(db, campaign_id="cmp1", ad_date=ALERT_DATE, budget=budget, kind=kind)
    _sentinel(db, "cmp1", ALERT_DATE, final_cost)
    db.commit()

    retro_pacing_scorer.score_alerts(db, ALERT_DATE)
    row = db.query(NaverRetroPacingScore).one()
    assert row.verdict == expected


# ── unparsed 기록 ──
def test_score_alerts_records_unparsed_on_regex_mismatch(db):
    p = NaverProposal(
        proposal_type=PROPOSAL_TYPE_PACING, target_type="campaign", target_id="cmp1",
        campaign_id="cmp1", rationale="형식이 전혀 다른 텍스트", status="pending",
    )
    db.add(p)
    db.commit()

    result = retro_pacing_scorer.score_alerts(db, ALERT_DATE)
    assert result == {"scored": 0, "unparsed": 1, "skipped": 0}
    row = db.query(NaverRetroPacingScore).one()
    assert row.verdict == "unparsed"
    assert row.kind is None
    assert row.alert_date is None


def test_score_alerts_unparsed_not_retried_on_rerun(db):
    p = NaverProposal(
        proposal_type=PROPOSAL_TYPE_PACING, target_type="campaign", target_id="cmp1",
        campaign_id="cmp1", rationale="형식이 전혀 다른 텍스트", status="pending",
    )
    db.add(p)
    db.commit()

    retro_pacing_scorer.score_alerts(db, ALERT_DATE)
    second = retro_pacing_scorer.score_alerts(db, ALERT_DATE)
    assert second == {"scored": 0, "unparsed": 0, "skipped": 0}
    assert db.query(NaverRetroPacingScore).count() == 1


# ── 최종치 없음 skip 후 재시도 ──
def test_score_alerts_skips_when_sentinel_final_missing_then_retries(db):
    _pacing_proposal(db, campaign_id="cmp1", ad_date=ALERT_DATE)
    db.commit()

    first = retro_pacing_scorer.score_alerts(db, ALERT_DATE)
    assert first == {"scored": 0, "unparsed": 0, "skipped": 1}
    assert db.query(NaverRetroPacingScore).count() == 0

    # 다음날 sentinel 최종치가 늦게 들어옴
    _sentinel(db, "cmp1", ALERT_DATE, 10000)
    db.commit()

    second = retro_pacing_scorer.score_alerts(db, ALERT_DATE)
    assert second == {"scored": 1, "unparsed": 0, "skipped": 0}
    assert db.query(NaverRetroPacingScore).count() == 1


def test_score_alerts_skips_alert_date_after_upto(db):
    """경보일이 upto보다 미래면(아직 그날이 안 지남) 채점하지 않고 skip."""
    _pacing_proposal(db, campaign_id="cmp1", ad_date=ALERT_DATE)
    _sentinel(db, "cmp1", ALERT_DATE, 10000)
    db.commit()

    result = retro_pacing_scorer.score_alerts(db, ALERT_DATE - timedelta(days=1))
    assert result == {"scored": 0, "unparsed": 0, "skipped": 1}
    assert db.query(NaverRetroPacingScore).count() == 0


def test_score_alerts_idempotent_excludes_already_scored_proposal(db):
    p = _pacing_proposal(db, campaign_id="cmp1", ad_date=ALERT_DATE)
    _sentinel(db, "cmp1", ALERT_DATE, 10000)
    db.commit()

    retro_pacing_scorer.score_alerts(db, ALERT_DATE)
    # 사후 sentinel 값이 바뀌어도 이미 채점된 proposal_id는 재조회 대상에서 제외
    db.query(NaverAdDaily).delete()
    _sentinel(db, "cmp1", ALERT_DATE, 999_999)
    db.commit()

    second = retro_pacing_scorer.score_alerts(db, ALERT_DATE)
    assert second == {"scored": 0, "unparsed": 0, "skipped": 0}
    row = db.query(NaverRetroPacingScore).filter_by(proposal_id=p.id).one()
    assert row.final_cost == 10000
