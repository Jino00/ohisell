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
    NaverAdDaily, NaverCampaignSettings, NaverEntity, NaverForecastDaily, NaverHourlySnapshot,
    NaverProductBep, NaverProposal, Order,
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
        "freshness": "ok", "diagnosis": "ok", "bid_simulator": "ok", "growth_sweeper": "ok",
        "budget_allocator": "ok", "anomaly_feed": "ok",
        "proposal_writer": "ok", "slack": "ok", "expiry": "ok",
    }
    assert out["generated"] >= 1
    saved = db.query(NaverProposal).filter(NaverProposal.target_id == "nkw-1").all()
    assert len(saved) == 1
    assert saved[0].proposal_type in ("bid_down", "negative_keyword")
    # 계정 브리프 싱글톤도 같이 생성됨
    assert db.query(NaverProposal).filter(NaverProposal.proposal_type == "account_brief").count() == 1


def test_run_daily_uses_campaign_target_roas_override_not_account_default(db, monkeypatch):
    """codex 지적(라이브검증 후속): 계정 기본 target_roas(=2.5, 낮음)로는 출혈 판정되는
    키워드도, 캠페인에 훨씬 높은 override(=100)가 걸려 있으면 그 override로 economic_ceiling이
    계산돼야 한다 — override가 rationale 라벨에만 쓰이고 계산에 반영 안 되던 버그의 회귀테스트."""
    _seed_bep(db)
    db.add(NaverCampaignSettings(campaign_id="cmp1", optimizer="ours", target_roas_override=Decimal("100")))
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-1", campaign_id="cmp1",
                        campaign_type="WEB_SITE", name="출혈키워드", status="on", bid_amt=500))
    _row(db, AS_OF, "cmp1", "WEB_SITE", "grp1", "nkw-1", 500, 50, 10000, direct=1000)
    db.commit()

    monkeypatch.setattr(
        proposal_pipeline.fetcher, "estimate_average_position_bid",
        lambda device, items: [{"nccKeywordId": it["key"], "bid": 300} for it in items],
    )
    captured_target_roas = {}
    original = proposal_pipeline.campaign_target_resolver.resolve_target_roas

    def spy(db_, campaign_id):
        result = original(db_, campaign_id)
        captured_target_roas[campaign_id] = result
        return result

    monkeypatch.setattr(proposal_pipeline.campaign_target_resolver, "resolve_target_roas", spy)

    proposal_pipeline.run_daily(db)
    assert captured_target_roas["cmp1"] == {"target_roas": Decimal("100"), "source": "override"}


def test_run_daily_slack_failure_reflected_as_failed_stage(db, monkeypatch):
    """codex 지적: slack_notifier.notify()가 예외 없이 {"sent": False, "reason": "HTTP 500..."}를
    반환해도 run_daily는 그 반환값을 확인해 stage_status를 failed로 남겨야 한다."""
    _seed_bep(db)
    db.add(NaverCampaignSettings(campaign_id="cmp1", optimizer="ours"))
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-1", campaign_id="cmp1",
                        campaign_type="WEB_SITE", name="출혈키워드", status="on", bid_amt=500))
    _row(db, AS_OF, "cmp1", "WEB_SITE", "grp1", "nkw-1", 500, 50, 10000, direct=1000)
    db.commit()

    monkeypatch.setattr(
        proposal_pipeline.fetcher, "estimate_average_position_bid",
        lambda device, items: [{"nccKeywordId": it["key"], "bid": 300} for it in items],
    )
    monkeypatch.setattr(
        proposal_pipeline.slack_notifier, "notify",
        lambda proposals: {"sent": False, "reason": "HTTP 500: server error", "slack_ts": None, "proposal_count": len(proposals)},
    )

    out = proposal_pipeline.run_daily(db)
    assert out["stage_status"]["slack"] == "failed"
    assert any("slack:" in e for e in out["errors"])


def test_run_daily_wires_performance_estimate_into_expected_effect(db, monkeypatch):
    """codex 지적: estimate_performance가 파이프라인에서 전혀 연결 안 돼 expected_effect가
    항상 '미조회'였다 — 확정된 recommended_bid로 performance-bulk를 호출해 예측클릭을 채운다."""
    _seed_bep(db)
    db.add(NaverCampaignSettings(campaign_id="cmp1", optimizer="ours"))
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-1", campaign_id="cmp1",
                        campaign_type="WEB_SITE", name="출혈키워드", status="on", bid_amt=500))
    _row(db, AS_OF, "cmp1", "WEB_SITE", "grp1", "nkw-1", 500, 50, 10000, direct=1000)
    db.commit()

    monkeypatch.setattr(
        proposal_pipeline.fetcher, "estimate_average_position_bid",
        lambda device, items: [{"nccKeywordId": it["key"], "bid": 300} for it in items],
    )
    perf_calls = []

    def fake_perf(items):
        perf_calls.extend(items)
        return [{"keyword": it["keyword"], "bid": it["bid"], "clicks": 42, "impressions": 999, "cost": 1234}
                for it in items]

    monkeypatch.setattr(proposal_pipeline.fetcher, "estimate_performance", fake_perf)

    proposal_pipeline.run_daily(db)
    assert any(c["keyword"] == "출혈키워드" for c in perf_calls)
    saved = db.query(NaverProposal).filter(NaverProposal.target_id == "nkw-1").one()
    assert "추정클릭 42" in saved.expected_effect


def test_run_daily_starving_winner_never_gets_bid_down_from_rank_estimate(db, monkeypatch):
    """라이브검증(2026-07-07) 회귀 재현: 이미 목표순위보다 잘 나오는 고ROAS 저노출 키워드에
    고정 목표순위(position=2) rank_bid를 적용하면 bid_down이 나와 육성 의도(D-NAO-18)와
    정반대로 뒤집힌다. starving_winners는 rank estimate를 아예 조회하지 않아야 한다."""
    _seed_bep(db)
    db.add(NaverCampaignSettings(campaign_id="cmp1", optimizer="ours"))
    # 현재 입찰가 100원인데도 클릭당매출이 매우 높은(=경제성 상한이 훨씬 큰) 저노출 승자.
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-star", campaign_id="cmp1",
                        campaign_type="WEB_SITE", name="굶는승자", status="on", bid_amt=100))
    # 15일 창 전체에 클릭 1건뿐(굶는승자 조건 avg_daily_clk<1) + 매우 높은 전환매출.
    _row(db, AS_OF, "cmp1", "WEB_SITE", "grp1", "nkw-star", 500, 1, 1000, direct=100_000)
    db.commit()

    queried_ids = []

    def fake_avg_pos_bid(device, items):
        queried_ids.extend(it["key"] for it in items)
        return [{"keyword": "굶는승자", "position": 2, "nccKeywordId": it["key"], "bid": 50} for it in items]

    monkeypatch.setattr(proposal_pipeline.fetcher, "estimate_average_position_bid", fake_avg_pos_bid)

    out = proposal_pipeline.run_daily(db)
    assert "nkw-star" not in queried_ids  # starving_winners는 rank estimate 대상에서 제외

    saved = db.query(NaverProposal).filter(NaverProposal.target_id == "nkw-star").one()
    assert saved.proposal_type == "bid_up"  # economic_ceiling만으로 판단 → 고ROAS라 인상 추천


# ── growth_sweeper 연동 (듀얼모드 스프린트 Phase 2, D-NAO-22-①) ──
def test_run_daily_generates_growth_bid_up_for_underbid_healthy_keyword(db, monkeypatch):
    """예외 보드(출혈/굶는승자)에 안 걸리는 건강한 키워드도 현재 입찰이 경제성 상한보다
    훨씬 낮으면 growth_sweeper 전수 스윕이 잡아 growth_bid_up을 만든다."""
    _seed_bep(db)
    db.add(NaverCampaignSettings(campaign_id="cmp1", optimizer="ours"))
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-grow", campaign_id="cmp1",
                        campaign_type="WEB_SITE", name="성장후보", status="on", bid_amt=70))
    # avg_daily_clk=100/15≈6.67(굶는승자 아님) + 높은 전환매출(출혈 아님) + 최저입찰가(70원)로
    # 방치돼 갭이 크게 벌어진 상태. D-NAO-21 보정계수(실주문매출÷네이버convAmt)가 이 keyword의
    # 과대 convAmt(100만원)를 상쇄하지 않도록 실주문(Order)도 같은 규모로 맞춰 factor≈1 유지
    # (그렇지 않으면 factor가 과소해져 economic_ceiling이 0에 가까워짐 — 실측 함정, ref D-NAO-21).
    _row(db, AS_OF, "cmp1", "WEB_SITE", "grp1", "nkw-grow", 1000, 100, 5000, direct=1_000_000)
    db.add(Order(channel_id=6, platform_product_id="cp-1", order_number="ORD-2",
                  order_date=AS_OF, status="정상", selling_price=Decimal("990000")))
    db.commit()

    monkeypatch.setattr(
        proposal_pipeline.fetcher, "estimate_average_position_bid",
        lambda device, items: [{"nccKeywordId": it["key"], "bid": 100_000} for it in items],
    )

    out = proposal_pipeline.run_daily(db)
    assert out["stage_status"]["growth_sweeper"] == "ok"
    saved = db.query(NaverProposal).filter(
        NaverProposal.target_id == "nkw-grow", NaverProposal.proposal_type == "growth_bid_up",
    ).all()
    assert len(saved) == 1
    assert "D-NAO-20 스톱로스=" in saved[0].rationale
    assert "갭=" in saved[0].rationale


def test_growth_sweeper_agg_excludes_other_campaign_types(db, monkeypatch):
    """codex 지적(리뷰 발견, 라이브검증 없음): growth_sweeper는 WEB_SITE 전용인데
    compute_bid_sims용 전체-캠페인유형 집계를 그대로 넘기면, 클릭이 전혀 없는 WEB_SITE
    신규 키워드가 SHOPPING 매출로 부풀려진 계정 prior를 그대로 물려받아 근거 없는
    growth_bid_up이 나올 수 있다. run_daily이 growth_sweeper에는 WEB_SITE로 스코프된
    별도 집계를 넘겨야 한다."""
    _seed_bep(db)
    db.add(NaverCampaignSettings(campaign_id="cmp1", optimizer="ours"))
    # SHOPPING 캠페인에 막대한 클릭·매출(계정 전체 집계엔 잡히지만 WEB_SITE 스코프에선 0이어야 함).
    _row(db, AS_OF, "cmp-shop", "SHOPPING", "grp-shop", "", 100_000, 10_000, 5_000_000, direct=50_000_000)
    # WEB_SITE 키워드는 등록만 돼 있고 naver_ad_daily에 단 1행도 없음(클릭 0).
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-cold", campaign_id="cmp1",
                        campaign_type="WEB_SITE", name="콜드스타트", status="on", bid_amt=70))
    # D-NAO-21 보정계수를 1에 가깝게 유지 — 그렇지 않으면 factor가 과소해져 ceiling이 스코프
    # 버그와 무관하게 0으로 눌려 이 테스트가 실제로는 아무것도 검증하지 못하게 된다.
    db.add(Order(channel_id=6, platform_product_id="cp-1", order_number="ORD-2",
                  order_date=AS_OF, status="정상", selling_price=Decimal("45000000")))
    db.commit()

    monkeypatch.setattr(
        proposal_pipeline.fetcher, "estimate_average_position_bid",
        lambda device, items: [{"nccKeywordId": it["key"], "bid": 100_000} for it in items],
    )

    out = proposal_pipeline.run_daily(db)
    assert out["stage_status"]["growth_sweeper"] == "ok"
    saved = db.query(NaverProposal).filter(
        NaverProposal.target_id == "nkw-cold", NaverProposal.proposal_type == "growth_bid_up",
    ).all()
    assert saved == []  # WEB_SITE 스코프에선 계정 전체 클릭이 0 — 부트스트랩 게이트가 막아야 함


def test_compute_growth_sims_filters_non_ours_before_estimate_budget(db, monkeypatch):
    """codex 지적(2차 리뷰): non-ours 캠페인의 갭이 커도 estimate 예산 슬롯을 차지하면 안 됨 —
    ours 캠페인 후보가 예산 밖으로 밀려 estimate조차 못 받는 사태를 막는다."""
    monkeypatch.setattr(proposal_pipeline.growth_sweeper, "ESTIMATE_BUDGET", 1)

    _seed_bep(db)
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.add(NaverCampaignSettings(campaign_id="cmp-none", optimizer="none"))
    # non-ours: 훨씬 큰 갭(더 낮은 입찰가) — ESTIMATE_BUDGET=1이면 ours보다 먼저 슬롯을 차지할 것.
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-none", campaign_id="cmp-none",
                        campaign_type="WEB_SITE", name="비대상", status="on", bid_amt=10))
    _row(db, AS_OF, "cmp-none", "WEB_SITE", "grp1", "nkw-none", 1000, 100, 5000, direct=1_000_000)
    # ours: 갭은 더 작지만 실제 제안 대상.
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-ours", campaign_id="cmp-ours",
                        campaign_type="WEB_SITE", name="대상", status="on", bid_amt=500))
    _row(db, AS_OF, "cmp-ours", "WEB_SITE", "grp2", "nkw-ours", 1000, 100, 5000, direct=1_000_000)
    db.add(Order(channel_id=6, platform_product_id="cp-1", order_number="ORD-2",
                  order_date=AS_OF, status="정상", selling_price=Decimal("47000000")))
    db.commit()

    monkeypatch.setattr(
        proposal_pipeline.fetcher, "estimate_average_position_bid",
        lambda device, items: [{"nccKeywordId": it["key"], "bid": 100_000} for it in items],
    )

    diag = proposal_pipeline.diagnosis.build_diagnosis(db, D_FROM, AS_OF)
    result = proposal_pipeline.compute_growth_sims(db, diag, D_FROM, AS_OF)
    ids = [c["keyword_id"] for c in result["candidates"]]
    assert ids == ["nkw-ours"]  # non-ours(nkw-none)는 갭이 더 커도 예산에서 배제됨


def test_compute_growth_sims_respects_estimate_budget(db, monkeypatch):
    """전수(89K) estimate 금지 — 갭 상위 ESTIMATE_BUDGET개만 rank estimate 조회 대상(계획서 §4-Phase2)."""
    monkeypatch.setattr(proposal_pipeline.growth_sweeper, "ESTIMATE_BUDGET", 2)

    _seed_bep(db)
    db.add(NaverCampaignSettings(campaign_id="cmp1", optimizer="ours"))
    for i in range(5):
        db.add(NaverEntity(entity_type="keyword", entity_id=f"nkw-g{i}", campaign_id="cmp1",
                            campaign_type="WEB_SITE", name=f"kw{i}", status="on", bid_amt=10))
        _row(db, AS_OF, "cmp1", "WEB_SITE", "grp1", f"nkw-g{i}", 1000, 100, 1000, direct=100_000 + i * 1000)
    # 보정계수를 1에 가깝게 유지(위 test_run_daily_generates_growth_bid_up_for_underbid_healthy_keyword
    # 와 동일 이유, D-NAO-21) — 그렇지 않으면 ceiling이 0으로 눌려 후보 자체가 안 나온다.
    db.add(Order(channel_id=6, platform_product_id="cp-1", order_number="ORD-2",
                  order_date=AS_OF, status="정상", selling_price=Decimal("500000")))
    db.commit()

    queried: list[str] = []

    def fake_avg_pos_bid(device, items):
        queried.extend(it["key"] for it in items)
        return [{"nccKeywordId": it["key"], "bid": 100} for it in items]

    monkeypatch.setattr(proposal_pipeline.fetcher, "estimate_average_position_bid", fake_avg_pos_bid)

    diag = proposal_pipeline.diagnosis.build_diagnosis(db, D_FROM, AS_OF)
    result = proposal_pipeline.compute_growth_sims(db, diag, D_FROM, AS_OF)
    assert len(result["candidates"]) == 2  # ESTIMATE_BUDGET=2로 상한
    assert len(queried) == 2  # estimate API 콜도 예산만큼만


# ── budget_allocator + anomaly_feed 연동 (듀얼모드 스프린트 Phase 3, D-NAO-22-③/④) ──
def _hourly_snapshot(campaign_id, cost, daily_budget, ad_date, hour=10, campaign_type="WEB_SITE"):
    return NaverHourlySnapshot(
        snapshot_at=datetime.combine(ad_date, datetime.min.time()).replace(hour=hour),
        ad_date=ad_date, snapshot_hour=hour, campaign_id=campaign_id, campaign_type=campaign_type,
        cost=cost, clk=0, imp=0, daily_budget=daily_budget,
    )


def test_run_daily_generates_budget_up_when_exhausted_and_growth_exists(db, monkeypatch):
    """예산 소진(오늘 hourly_snapshot) + growth_sweeper 잔존볼륨(어제 확정치) 동시 충족 시
    budget_up 제안 생성 — compute_growth_sims의 all_candidates를 재사용(estimate 재조회 없음)."""
    _seed_bep(db)
    db.add(NaverCampaignSettings(campaign_id="cmp1", optimizer="ours"))
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-grow", campaign_id="cmp1",
                        campaign_type="WEB_SITE", name="성장후보", status="on", bid_amt=70))
    _row(db, AS_OF, "cmp1", "WEB_SITE", "grp1", "nkw-grow", 1000, 100, 5000, direct=1_000_000)
    db.add(Order(channel_id=6, platform_product_id="cp-1", order_number="ORD-2",
                  order_date=AS_OF, status="정상", selling_price=Decimal("990000")))
    # 오늘(kst_today()) 실시간 예산 소진 스냅샷 — budget_allocator는 as_of(어제)가 아니라 오늘 기준.
    db.add(_hourly_snapshot("cmp1", cost=10000, daily_budget=10000, ad_date=kst_today()))
    db.commit()

    monkeypatch.setattr(
        proposal_pipeline.fetcher, "estimate_average_position_bid",
        lambda device, items: [{"nccKeywordId": it["key"], "bid": 100_000} for it in items],
    )

    out = proposal_pipeline.run_daily(db)
    assert out["stage_status"]["budget_allocator"] == "ok"
    saved = db.query(NaverProposal).filter(NaverProposal.proposal_type == "budget_up").all()
    assert len(saved) == 1
    assert saved[0].target_id == "cmp1"
    assert "인과추정 없음" in saved[0].expected_effect
    assert "일예산 10000원 소진" in saved[0].rationale


def test_run_daily_no_budget_up_when_not_exhausted(db, monkeypatch):
    _seed_bep(db)
    db.add(NaverCampaignSettings(campaign_id="cmp1", optimizer="ours"))
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-grow", campaign_id="cmp1",
                        campaign_type="WEB_SITE", name="성장후보", status="on", bid_amt=70))
    _row(db, AS_OF, "cmp1", "WEB_SITE", "grp1", "nkw-grow", 1000, 100, 5000, direct=1_000_000)
    db.add(Order(channel_id=6, platform_product_id="cp-1", order_number="ORD-2",
                  order_date=AS_OF, status="정상", selling_price=Decimal("990000")))
    db.add(_hourly_snapshot("cmp1", cost=100, daily_budget=10000, ad_date=kst_today()))  # 미소진
    db.commit()

    monkeypatch.setattr(
        proposal_pipeline.fetcher, "estimate_average_position_bid",
        lambda device, items: [{"nccKeywordId": it["key"], "bid": 100_000} for it in items],
    )

    out = proposal_pipeline.run_daily(db)
    assert db.query(NaverProposal).filter(NaverProposal.proposal_type == "budget_up").count() == 0


def test_compute_pre_exhaustion_signals_wraps_budget_allocator(db):
    """F2b ⓑ: compute_pre_exhaustion_signals가 budget_allocator.find_pre_exhaustion_signals를 그대로 위임."""
    db.add(_hourly_snapshot("cmp1", cost=3000, daily_budget=10000, ad_date=kst_today()))
    db.add(NaverForecastDaily(
        target_date=kst_today(), grain="campaign", scope_key="cmp1",
        pred_clk=0, pred_cost=12000, pred_conv_amt=0,
    ))
    db.commit()

    out = proposal_pipeline.compute_pre_exhaustion_signals(db)
    assert len(out) == 1
    assert out[0]["campaign_id"] == "cmp1"
    assert out[0]["pred_cost"] == 12000


def test_run_daily_generates_budget_pre_exhaustion_proposal(db, monkeypatch):
    """F2b ⓑ 통합: 아직 미소진이나 오늘 예측이 예산 초과면 budget_pre_exhaustion 정보성 제안 생성."""
    _seed_bep(db)
    db.add(NaverCampaignSettings(campaign_id="cmp1", optimizer="ours"))
    _row(db, AS_OF, "cmp1", "WEB_SITE", "grp1", "nkw-1", 1000, 100, 5000, direct=1_000_000)  # freshness 게이트
    db.add(_hourly_snapshot("cmp1", cost=3000, daily_budget=10000, ad_date=kst_today()))  # 아직 미소진
    db.add(NaverForecastDaily(
        target_date=kst_today(), grain="campaign", scope_key="cmp1",
        pred_clk=0, pred_cost=12000, pred_conv_amt=0,
    ))
    db.commit()

    monkeypatch.setattr(
        proposal_pipeline.fetcher, "estimate_average_position_bid",
        lambda device, items: [{"nccKeywordId": it["key"], "bid": 100_000} for it in items],
    )

    out = proposal_pipeline.run_daily(db)
    assert out["stage_status"]["budget_allocator"] == "ok"
    saved = db.query(NaverProposal).filter(NaverProposal.proposal_type == "budget_pre_exhaustion").all()
    assert len(saved) == 1
    assert saved[0].target_id == "cmp1"
    assert "실행 대상 아님" in saved[0].expected_effect


def test_compute_forecast_evidence_looks_up_by_grain_and_scope_key(db):
    """F2b ⓐ: keyword/adgroup grain 예측을 (target_type,target_id) 목록으로 배치 조회."""
    db.add(NaverForecastDaily(
        target_date=kst_today(), grain="keyword", scope_key="nkw-1",
        pred_clk=40, pred_cost=90000, pred_conv_amt=15000,
    ))
    db.add(NaverForecastDaily(
        target_date=kst_today(), grain="adgroup", scope_key="adg-other",  # targets에 없음 — 제외
        pred_clk=1, pred_cost=1, pred_conv_amt=0,
    ))
    db.commit()

    out = proposal_pipeline.compute_forecast_evidence(db, [("keyword", "nkw-1"), ("adgroup", "adg-1")])
    assert out == {("keyword", "nkw-1"): {"pred_clk": 40, "pred_cost": 90000, "pred_conv_amt": 15000}}


def test_compute_forecast_evidence_empty_targets_returns_empty(db):
    assert proposal_pipeline.compute_forecast_evidence(db, []) == {}


def test_run_daily_passes_forecast_evidence_to_proposal_writer(db, monkeypatch):
    """F2b ⓐ 통합: run_daily이 bid_sims/growth_sims 대상의 예측을 조회해 proposal_writer.build에 넘긴다."""
    _seed_bep(db)
    db.add(NaverCampaignSettings(campaign_id="cmp1", optimizer="ours"))
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-grow", campaign_id="cmp1",
                        campaign_type="WEB_SITE", name="성장후보", status="on", bid_amt=70))
    _row(db, AS_OF, "cmp1", "WEB_SITE", "grp1", "nkw-grow", 1000, 100, 5000, direct=1_000_000)
    db.add(Order(channel_id=6, platform_product_id="cp-1", order_number="ORD-2",
                  order_date=AS_OF, status="정상", selling_price=Decimal("990000")))
    db.add(NaverForecastDaily(
        target_date=kst_today(), grain="keyword", scope_key="nkw-grow",
        pred_clk=45, pred_cost=95000, pred_conv_amt=18000,
    ))
    db.commit()

    monkeypatch.setattr(
        proposal_pipeline.fetcher, "estimate_average_position_bid",
        lambda device, items: [{"nccKeywordId": it["key"], "bid": 100_000} for it in items],
    )

    captured = {}
    real_build = proposal_pipeline.proposal_writer.build

    def _spy_build(*args, **kwargs):
        captured.update(kwargs)
        return real_build(*args, **kwargs)

    monkeypatch.setattr(proposal_pipeline.proposal_writer, "build", _spy_build)

    proposal_pipeline.run_daily(db)
    assert captured.get("forecast_data", {}).get(("keyword", "nkw-grow")) == {
        "pred_clk": 45, "pred_cost": 95000, "pred_conv_amt": 18000,
    }


def test_run_daily_generates_anomaly_spend_spike_proposal(db, monkeypatch):
    _seed_bep(db)
    db.add(NaverCampaignSettings(campaign_id="cmp1", optimizer="ours"))
    _row(db, AS_OF - timedelta(days=1), "cmp1", "WEB_SITE", "grp1", "nkw-1", 100, 10, 1000, direct=2000)
    _row(db, AS_OF, "cmp1", "WEB_SITE", "grp1", "nkw-1", 500, 50, 10000, direct=20000)  # 10배 급증
    db.commit()

    monkeypatch.setattr(
        proposal_pipeline.fetcher, "estimate_average_position_bid",
        lambda device, items: [{"nccKeywordId": it["key"], "bid": 300} for it in items],
    )

    out = proposal_pipeline.run_daily(db)
    assert out["stage_status"]["anomaly_feed"] == "ok"
    saved = db.query(NaverProposal).filter(NaverProposal.proposal_type == "anomaly").all()
    assert len(saved) == 1
    assert saved[0].target_id == "cmp1"
    assert "급증" in saved[0].rationale


def test_run_daily_generates_anomaly_freshness_proposal(db, monkeypatch):
    """S3a codex 연기분 해소 — row_count>0(기존 freshness_gate 통과)이라도 baseline 대비
    너무 적으면(부분적재 의심) anomaly_freshness 정보성 제안이 생성돼야 한다."""
    _seed_bep(db)
    db.add(NaverCampaignSettings(campaign_id="cmp1", optimizer="ours"))
    for i in range(7):
        for kw in range(10):
            _row(db, AS_OF - timedelta(days=i + 1), "cmp1", "WEB_SITE", "grp1", f"nkw-{kw}", 10, 1, 100)
    for kw in range(2):  # 오늘은 10개 중 2개만(부분적재)
        _row(db, AS_OF, "cmp1", "WEB_SITE", "grp1", f"nkw-{kw}", 10, 1, 100)
    db.commit()

    monkeypatch.setattr(
        proposal_pipeline.fetcher, "estimate_average_position_bid",
        lambda device, items: [{"nccKeywordId": it["key"], "bid": 300} for it in items],
    )

    out = proposal_pipeline.run_daily(db)
    saved = db.query(NaverProposal).filter(NaverProposal.proposal_type == "anomaly_freshness").all()
    assert len(saved) == 1
    assert "부분적재 의심" in saved[0].rationale


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

    def boom(db_, diagnosis_dict, *, bid_sims=None, growth_candidates=None, growth_sims=None,
             budget_signals=None, pre_exhaustion_signals=None, forecast_data=None, anomalies=None, as_of=None):
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
