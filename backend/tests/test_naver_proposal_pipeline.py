# test_naver_proposal_pipeline.py — P2-S3 T5 proposal_pipeline(harness) 단위/통합 테스트
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdDaily, NaverCampaignSettings, NaverEntity, NaverProductBep, NaverProposal, Order,
)
from app.services.naver_ad import proposal_pipeline
from app.utils.kst import kst_today

AS_OF = kst_today() - timedelta(days=1)  # run_daily이 실제로 조회하는 as_of와 동일 기준
D_FROM = AS_OF - timedelta(days=14)


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


def _row(db, ad_date, campaign_id, campaign_type, adgroup_id, keyword_id, imp, clk, cost, direct=0, indirect=0):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type=campaign_type,
        adgroup_id=adgroup_id, keyword_id=keyword_id,
        imp=imp, clk=clk, cost=cost, rank_sum=imp * 3,
        conv_direct_cnt=1 if direct else 0, conv_indirect_cnt=1 if indirect else 0,
        conv_direct_amt=direct, conv_indirect_amt=indirect,
    ))


def _seed_bep(db):
    db.add(NaverProductBep(
        channel_id=6, channel_product_id="cp-1", product_name="테스트상품",
        selling_price=Decimal("10000"), cost_price=Decimal("5000"),
        commission_rate=Decimal("0.05"), logistics_cost=Decimal("1000"),
        contribution_margin=Decimal("3000"), bep_roas=Decimal("2.0"),
        aggressiveness="standard", target_roas=Decimal("2.5"), has_cost=True,
    ))
    db.add(Order(channel_id=6, platform_product_id="cp-1", order_number="ORD-1",
                  order_date=AS_OF, status="정상", selling_price=Decimal("10000")))


# ── freshness gate ──
def test_run_daily_skips_when_stale(db):
    db.commit()  # naver_ad_daily에 as_of 데이터 없음
    out = proposal_pipeline.run_daily(db)
    assert out["stage_status"]["freshness"] == "stale"
    assert out["skipped"] == 1
    assert out["generated"] == 0
    assert "freshness 게이트" in out["errors"][0]


def test_run_daily_degrades_when_bep_unavailable(db):
    _row(db, AS_OF, "cmp1", "WEB_SITE", "grp1", "nkw-1", 100, 10, 10000, direct=1000)
    db.commit()  # BEP/target_roas 산출 불가(NaverProductBep 없음)

    out = proposal_pipeline.run_daily(db)
    assert out["stage_status"]["freshness"] == "ok"
    assert out["stage_status"]["diagnosis"] == "degraded"
    assert out["stage_status"]["bid_simulator"] == "skipped"
    assert out["stage_status"]["proposal_writer"] == "skipped"
    assert out["generated"] == 0


# ── happy path ──
def test_run_daily_full_happy_path_generates_proposal(db, monkeypatch):
    _seed_bep(db)
    db.add(NaverCampaignSettings(campaign_id="cmp1", optimizer="ours"))
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-1", campaign_id="cmp1",
                        campaign_type="WEB_SITE", name="출혈키워드", status="on", bid_amt=500))
    # BEP=2.0 미만 출혈 키워드: roas=1000/10000=0.1 < bep
    _row(db, AS_OF, "cmp1", "WEB_SITE", "grp1", "nkw-1", 500, 50, 10000, direct=1000)
    db.commit()

    def fake_avg_pos_bid(device, items):
        return [{"keyword": "출혈키워드", "position": 2, "nccKeywordId": it["key"], "bid": 300} for it in items]

    monkeypatch.setattr(proposal_pipeline.fetcher, "estimate_average_position_bid", fake_avg_pos_bid)

    out = proposal_pipeline.run_daily(db)
    assert out["stage_status"] == {
        "freshness": "ok", "diagnosis": "ok", "bid_simulator": "ok",
        "proposal_writer": "ok", "slack": "ok", "expiry": "ok",
    }
    assert out["generated"] >= 1
    saved = db.query(NaverProposal).filter(NaverProposal.target_id == "nkw-1").all()
    assert len(saved) == 1
    assert saved[0].proposal_type in ("bid_down", "negative_keyword")
    # 계정 브리프 싱글톤도 같이 생성됨
    assert db.query(NaverProposal).filter(NaverProposal.proposal_type == "account_brief").count() == 1


def test_run_daily_rank_estimate_failure_degrades_not_crashes(db, monkeypatch):
    _seed_bep(db)
    db.add(NaverCampaignSettings(campaign_id="cmp1", optimizer="ours"))
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-1", campaign_id="cmp1",
                        campaign_type="WEB_SITE", name="출혈키워드", status="on", bid_amt=500))
    _row(db, AS_OF, "cmp1", "WEB_SITE", "grp1", "nkw-1", 500, 50, 10000, direct=1000)
    db.commit()

    def boom(device, items):
        raise RuntimeError("네트워크 실패")

    monkeypatch.setattr(proposal_pipeline.fetcher, "estimate_average_position_bid", boom)

    out = proposal_pipeline.run_daily(db)
    assert out["stage_status"]["bid_simulator"] == "ok"  # estimate 실패해도 economic_ceiling만으로 진행
    assert out["stage_status"]["proposal_writer"] == "ok"


def test_run_daily_proposal_writer_failure_isolated_other_stages_still_run(db, monkeypatch):
    _seed_bep(db)
    db.add(NaverCampaignSettings(campaign_id="cmp1", optimizer="ours"))
    _row(db, AS_OF, "cmp1", "WEB_SITE", "grp1", "nkw-1", 500, 50, 10000, direct=1000)
    db.commit()

    def boom(db_, diagnosis_dict, *, bid_sims=None, as_of=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(proposal_pipeline.proposal_writer, "build", boom)

    out = proposal_pipeline.run_daily(db)
    assert out["stage_status"]["proposal_writer"] == "failed"
    assert out["stage_status"]["expiry"] == "ok"  # 이후 단계는 계속 진행
    assert "proposal_writer: boom" in out["errors"]


# ── expiry ──
def test_run_daily_expires_stale_pending_proposals(db):
    _seed_bep(db)
    db.add(NaverCampaignSettings(campaign_id="cmp1", optimizer="ours"))
    _row(db, AS_OF, "cmp1", "WEB_SITE", "grp1", "nkw-99", 10, 1, 100, direct=0)
    old = NaverProposal(proposal_type="bid_down", target_type="keyword", target_id="nkw-old",
                          campaign_id="cmp1", status="pending")
    db.add(old)
    db.commit()
    old.created_at = datetime.combine(kst_today() - timedelta(days=20), datetime.min.time())
    db.commit()

    out = proposal_pipeline.run_daily(db)
    assert out["expired"] == 1
    db.refresh(old)
    assert old.status == "expired"


# ── 내부 헬퍼 단위테스트 ──
def test_precompute_aggregates_sums_by_level(db):
    _row(db, AS_OF, "cmp1", "WEB_SITE", "grp1", "nkw-1", 100, 10, 1000, direct=500)
    _row(db, AS_OF, "cmp1", "WEB_SITE", "grp1", "nkw-2", 100, 20, 2000, direct=1000)
    _row(db, AS_OF, "cmp2", "WEB_SITE", "grp2", "nkw-3", 100, 5, 500, direct=250)
    db.commit()

    agg = proposal_pipeline._precompute_aggregates(db, D_FROM, AS_OF)
    assert agg["group"]["grp1"] == {"clk": 30, "conv_amt": 1500}
    assert agg["campaign"]["cmp1"] == {"clk": 30, "conv_amt": 1500}
    assert agg["campaign"]["cmp2"] == {"clk": 5, "conv_amt": 250}
    assert agg["account"] == {"clk": 35, "conv_amt": 1750}


def test_freshness_gate_ok_and_stale(db):
    assert proposal_pipeline._freshness_gate(db, AS_OF)["ok"] is False
    _row(db, AS_OF, "cmp1", "WEB_SITE", "grp1", "nkw-1", 10, 1, 100)
    db.commit()
    assert proposal_pipeline._freshness_gate(db, AS_OF)["ok"] is True
