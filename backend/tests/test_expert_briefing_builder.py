# test_expert_briefing_builder.py — E1a T2 expert_briefing_builder(SA1, 결정적) 단위테스트
from __future__ import annotations

import logging
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverEntity, NaverForecastDaily, NaverLearningState, NaverProposal
from app.services.naver_ad import expert_briefing_builder
from app.services.naver_ad.proposal_scoreboard import METRIC as PROPOSAL_ACCURACY_METRIC

AS_OF = date(2026, 7, 9)


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


def _add_proposal(db, **kw) -> NaverProposal:
    defaults = dict(proposal_type="bid_down", target_type="keyword", target_id="nkw-1", campaign_id="cmp-1", status="pending")
    defaults.update(kw)
    p = NaverProposal(**defaults)
    db.add(p)
    db.commit()
    return p


def test_build_is_deterministic(db):
    """같은 입력(DB 상태·as_of)이면 두 번 호출해도 완전히 같은 브리핑을 만든다."""
    _add_proposal(db, proposal_type="bid_down", target_id="nkw-1")
    _add_proposal(db, proposal_type="negative_keyword", target_id="nkw-2")

    b1 = expert_briefing_builder.build(db, as_of=AS_OF)
    b2 = expert_briefing_builder.build(db, as_of=AS_OF)

    assert b1 == b2


def test_defaults_as_of_to_kst_today_when_omitted(db, monkeypatch):
    monkeypatch.setattr(expert_briefing_builder, "kst_today", lambda: AS_OF)
    briefing = expert_briefing_builder.build(db)
    assert briefing["as_of"] == AS_OF.isoformat()


def test_only_pending_proposals_included(db):
    pending = _add_proposal(db, proposal_type="bid_down", target_id="nkw-1", status="pending")
    _add_proposal(db, proposal_type="bid_up", target_id="nkw-2", status="approved")
    _add_proposal(db, proposal_type="budget", target_id="nkw-3", status="rejected")

    briefing = expert_briefing_builder.build(db, as_of=AS_OF)

    ids = [p["id"] for p in briefing["pending_proposals"]]
    assert ids == [pending.id]


# ── X1a T6(D-NAO-37 ②) 브리핑 접기 ──
def test_pending_proposals_excludes_informational_types(db):
    """정보성 5종(anomaly/anomaly_freshness/account_brief/trigger_pacing/trigger_cpc_spike)은
    pending_proposals(실행형 카드 목록)에서 빠져야 한다 — informational_pending 몫."""
    executable = _add_proposal(db, proposal_type="bid_down", target_id="nkw-1")
    negative = _add_proposal(db, proposal_type="negative_keyword", target_id="nkw-2")
    for t in ("anomaly", "anomaly_freshness", "account_brief", "trigger_pacing", "trigger_cpc_spike"):
        _add_proposal(db, proposal_type=t, target_type="account", target_id="")

    briefing = expert_briefing_builder.build(db, as_of=AS_OF)

    ids = {p["id"] for p in briefing["pending_proposals"]}
    assert ids == {executable.id, negative.id}
    types = {p["proposal_type"] for p in briefing["pending_proposals"]}
    assert types == {"bid_down", "negative_keyword"}


def test_informational_pending_aggregates_count_and_campaign_count(db):
    """유형별 집계: count=건수, campaign_count=고유 campaign_id 수."""
    _add_proposal(db, proposal_type="anomaly", target_type="account", target_id="", campaign_id="cmp-1")
    _add_proposal(db, proposal_type="anomaly", target_type="account", target_id="", campaign_id="cmp-2")
    _add_proposal(db, proposal_type="anomaly", target_type="account", target_id="", campaign_id="cmp-2")
    _add_proposal(db, proposal_type="trigger_pacing", target_type="campaign", target_id="cmp-9", campaign_id="cmp-9")

    briefing = expert_briefing_builder.build(db, as_of=AS_OF)

    by_type = {row["proposal_type"]: row for row in briefing["informational_pending"]}
    assert by_type["anomaly"] == {"proposal_type": "anomaly", "count": 3, "campaign_count": 2}
    assert by_type["trigger_pacing"] == {"proposal_type": "trigger_pacing", "count": 1, "campaign_count": 1}


def test_informational_pending_blank_campaign_id_not_counted_as_campaign(db):
    """계정 단위 정보성(campaign_id='' — account_brief 등)은 캠페인 수에 안 센다(codex 지적):
    account_brief 1건뿐이면 count=1, campaign_count=0."""
    _add_proposal(db, proposal_type="account_brief", target_type="account", target_id="", campaign_id="")
    _add_proposal(db, proposal_type="anomaly", target_type="account", target_id="", campaign_id="")
    _add_proposal(db, proposal_type="anomaly", target_type="account", target_id="", campaign_id="cmp-1")

    briefing = expert_briefing_builder.build(db, as_of=AS_OF)

    by_type = {row["proposal_type"]: row for row in briefing["informational_pending"]}
    assert by_type["account_brief"] == {"proposal_type": "account_brief", "count": 1, "campaign_count": 0}
    assert by_type["anomaly"] == {"proposal_type": "anomaly", "count": 2, "campaign_count": 1}


def test_informational_pending_empty_when_no_informational_types(db):
    _add_proposal(db, proposal_type="bid_down", target_id="nkw-1")

    briefing = expert_briefing_builder.build(db, as_of=AS_OF)

    assert briefing["informational_pending"] == []


def test_informational_pending_excludes_non_pending_status(db):
    _add_proposal(db, proposal_type="anomaly", target_type="account", target_id="", status="expired")

    briefing = expert_briefing_builder.build(db, as_of=AS_OF)

    assert briefing["informational_pending"] == []


def test_informational_pending_sorted_by_proposal_type_asc(db):
    _add_proposal(db, proposal_type="trigger_pacing", target_type="account", target_id="")
    _add_proposal(db, proposal_type="account_brief", target_type="account", target_id="")
    _add_proposal(db, proposal_type="anomaly", target_type="account", target_id="")

    briefing = expert_briefing_builder.build(db, as_of=AS_OF)

    types = [row["proposal_type"] for row in briefing["informational_pending"]]
    assert types == sorted(types)


def test_token_guard_ignores_informational_pending(db, monkeypatch, caplog):
    """토큰가드는 실행형(pending_proposals)에만 적용 — 정보성이 많아도 절삭 대상이 아니다."""
    monkeypatch.setattr(expert_briefing_builder, "_MAX_PROPOSALS_CHARS", 500)
    long_rationale = "근거 " * 100
    ids = []
    for i in range(5):
        p = _add_proposal(db, proposal_type="bid_down", target_id=f"nkw-{i}", rationale=long_rationale)
        ids.append(p.id)
    for i in range(10):  # 정보성 다건 — 토큰가드 절삭 대상 계산에 영향을 주면 안 됨
        _add_proposal(db, proposal_type="anomaly", target_type="account", target_id="", campaign_id=f"cmp-{i}")

    with caplog.at_level(logging.WARNING):
        briefing = expert_briefing_builder.build(db, as_of=AS_OF)

    dropped_ids = briefing["truncated"]["pending_proposals_dropped_ids"]
    assert dropped_ids, "실행형은 여전히 예산 초과로 절삭돼야 함"
    assert all(pid in ids for pid in dropped_ids), "절삭 대상은 실행형 id만이어야 함(정보성 섞이면 안 됨)"
    assert len(briefing["informational_pending"]) == 1
    assert briefing["informational_pending"][0]["count"] == 10, "정보성은 절삭 대상이 아니라 집계 그대로 유지"


def test_proposal_carries_resolved_entity_name(db):
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-1", campaign_id="cmp-1", name="가을 신상 원피스"))
    db.commit()
    _add_proposal(db, target_type="keyword", target_id="nkw-1")

    briefing = expert_briefing_builder.build(db, as_of=AS_OF)

    assert briefing["pending_proposals"][0]["target_name"] == "가을 신상 원피스"


def test_proposal_target_name_is_none_when_entity_not_synced(db):
    """SHOPPING 키워드 등 NaverEntity에 없는 대상은 target_name=None(정직 경계, 추정 금지)."""
    _add_proposal(db, target_type="keyword", target_id="nkw-unsynced")

    briefing = expert_briefing_builder.build(db, as_of=AS_OF)

    assert briefing["pending_proposals"][0]["target_name"] is None


def test_token_guard_drops_oldest_proposals_when_over_budget(db, monkeypatch, caplog):
    """토큰가드: 예산 초과 시 오래된(id 작은) 제안부터 절삭 + 로깅 + truncated 필드 노출(no silent cap)."""
    monkeypatch.setattr(expert_briefing_builder, "_MAX_PROPOSALS_CHARS", 500)
    long_rationale = "근거 " * 100  # 각 제안이 예산을 넘기도록 충분히 긴 텍스트
    ids = []
    for i in range(5):
        p = _add_proposal(db, proposal_type="bid_down", target_id=f"nkw-{i}", rationale=long_rationale)
        ids.append(p.id)

    with caplog.at_level(logging.WARNING):
        briefing = expert_briefing_builder.build(db, as_of=AS_OF)

    kept_ids = [p["id"] for p in briefing["pending_proposals"]]
    dropped_ids = briefing["truncated"]["pending_proposals_dropped_ids"]

    assert dropped_ids, "예산 초과인데 아무것도 절삭되지 않음"
    assert kept_ids, "전부 절삭되어 하나도 안 남음 — 예산이 너무 작게 설정된 테스트"
    assert dropped_ids == sorted(dropped_ids), "오래된(id 작은) 순으로 절삭돼야 함"
    assert min(dropped_ids) < min(kept_ids), "가장 오래된 제안부터 제거돼야 함"
    assert any("토큰가드" in r.message for r in caplog.records)


def test_no_truncation_when_under_budget(db):
    _add_proposal(db, proposal_type="bid_down", target_id="nkw-1", rationale="짧은 근거")

    briefing = expert_briefing_builder.build(db, as_of=AS_OF)

    assert briefing["truncated"] == {}


def test_forecast_rollup_is_scored_only_and_recent_n_bounded(db, monkeypatch):
    monkeypatch.setattr(expert_briefing_builder, "_FORECAST_ROLLUP_LIMIT", 3)
    for i in range(10):
        db.add(NaverForecastDaily(
            target_date=date(2026, 7, 1 + i), grain="campaign", scope_key="cmp-1",
            pred_clk=10, pred_cost=1000, actual_clk=9, actual_cost=950,
            scored_at=datetime(2026, 7, 2 + i, 6, 0, 0),
        ))
    # 아직 채점 안 된(익일 백필 전) 행 — 롤업에서 제외돼야 함
    db.add(NaverForecastDaily(target_date=date(2026, 7, 20), grain="campaign", scope_key="cmp-1", pred_clk=5, pred_cost=500))
    db.commit()

    briefing = expert_briefing_builder.build(db, as_of=AS_OF)

    rollup = briefing["forecast_rollup"]
    assert len(rollup) == 3
    assert all(r["actual_clk"] is not None for r in rollup)
    dates = [r["target_date"] for r in rollup]
    assert dates == sorted(dates, reverse=True), "최근(target_date 내림차순)부터 담겨야 함"


def test_recent_triggers_filtered_to_trigger_types_and_bounded(db, monkeypatch):
    monkeypatch.setattr(expert_briefing_builder, "_TRIGGER_RECENT_LIMIT", 2)
    for i in range(4):
        db.add(NaverProposal(
            proposal_type="trigger_pacing", target_type="campaign", target_id=f"cmp-{i}", campaign_id=f"cmp-{i}",
            status="pending", created_at=datetime(2026, 7, 1, 0, i, 0),
        ))
    db.add(NaverProposal(
        proposal_type="bid_down", target_type="keyword", target_id="nkw-1", campaign_id="cmp-1",
        status="pending", created_at=datetime(2026, 7, 1, 0, 10, 0),
    ))
    db.commit()

    briefing = expert_briefing_builder.build(db, as_of=AS_OF)

    triggers = briefing["recent_triggers"]
    assert len(triggers) == 2
    assert all(t["proposal_type"] == "trigger_pacing" for t in triggers)
    created = [t["created_at"] for t in triggers]
    assert created == sorted(created, reverse=True), "최근 발생순(내림차순)이어야 함"


def test_scoreboard_summary_reads_learning_state_without_mutating(db):
    db.add(NaverLearningState(scope="action_type", scope_key="bid_down", metric=PROPOSAL_ACCURACY_METRIC, sample_n=12, current_value=0.83))
    db.commit()
    before = db.query(NaverLearningState).count()

    briefing = expert_briefing_builder.build(db, as_of=AS_OF)

    after = db.query(NaverLearningState).count()
    assert after == before, "브리핑 조립은 읽기 전용이어야 한다(부수효과 없음)"
    assert briefing["scoreboard_summary"] == [{"action_type": "bid_down", "sample_n": 12, "accuracy": 0.83}]


def test_diagnosis_summary_reports_unavailable_when_no_bep_data(db):
    """BEP 산출 불가(D-3 읽기전용 진단) 상태도 정직하게 그대로 노출 — 추정 금지."""
    briefing = expert_briefing_builder.build(db, as_of=AS_OF)

    diag = briefing["diagnosis_summary"]
    assert diag["boards"] is None
    assert "error" in diag
    assert diag["window"]["date_to"] == AS_OF.isoformat()
