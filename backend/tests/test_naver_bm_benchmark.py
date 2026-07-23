# test_naver_bm_benchmark.py — BM 벤치마크 레이어 Phase 4 단위 테스트 (SA-3 + 프라이어 배선, D-NAO-78)
# 커버: ①compute_benchmarks(keyword_verified 대행사만·ours/mop 제외, bid_band [min,p50,max]
#   고성과 그룹, group_structure, 멱등 교체) ②loaders fail-open(verified_keyword_set/bid_band_anchor
#   부재=빈셋/None) ③SS4 교차(bm_prior 있음=rationale 플래그+promote_bm_crossed / 없음=기존 동일)
#   ④B-X adaptive_step None 폴백(회귀 0)·앵커 시드·실기울기 구간 앵커 미적용 ⑤harness fail-open.
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdDaily,
    NaverBmBenchmark,
    NaverCampaignSettings,
    NaverEntity,
    NaverEntitySnapshot,
    NaverProductBep,
    NaverProposal,
    NaverSearchTermDaily,
)
from app.services.naver_ad import bm_benchmark, exploration, search_term_ss_lane as lane
from app.services.naver_ad import search_term_judge as judge
from app.services.naver_ad.bm_benchmark import bid_band_anchor, compute_benchmarks, verified_keyword_set
from app.services.naver_ad.campaign_target_resolver import NAVER_CHANNEL_ID

SDATE = date(2026, 7, 22)
_NOW = datetime(2026, 7, 22, 9, 0, 0)


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


# ── 시더 ──────────────────────────────────────────────────────────
def _snap_group(db, entity_id, *, campaign_id, campaign_type, bid_amt=None,
                keyword_count=None, extended_search=None, status="on"):
    db.add(NaverEntitySnapshot(
        snapshot_date=SDATE, entity_type="adgroup", entity_id=entity_id, parent_id=campaign_id,
        campaign_id=campaign_id, campaign_type=campaign_type, optimizer="none",
        name=entity_id, status=status, bid_amt=bid_amt,
        keyword_count=keyword_count, extended_search=extended_search,
    ))


def _perf(db, adgroup_id, *, cost, conv_amt, ad_date=date(2026, 7, 15), keyword_id=""):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id="c", campaign_type="SHOPPING", adgroup_id=adgroup_id,
        keyword_id=keyword_id, imp=100, clk=10, cost=cost, rank_sum=0,
        conv_direct_amt=conv_amt, conv_indirect_amt=0,
    ))


def _kw(db, entity_id, name, *, campaign_id, campaign_type="WEB_SITE", status="on"):
    db.add(NaverEntity(
        entity_type="keyword", entity_id=entity_id, parent_id="g", campaign_id=campaign_id,
        campaign_type=campaign_type, name=name, status=status,
    ))


def _seed_high_perf_shopping(db):
    """SHOPPING 5그룹 — ROAS [0.1,0.5,2.0,3.0,5.0]·입찰 [70,200,300,500,900]. 중앙값 ROAS=2.0
    → 고성과(>=중앙값)=입찰 [300,500,900] 3그룹 → bid_band min=300,p50=500,max=900."""
    specs = [
        ("s1", 70, 100), ("s2", 200, 500), ("s3", 300, 2000), ("s4", 500, 3000), ("s5", 900, 5000),
    ]
    for gid, bid, conv in specs:
        _snap_group(db, gid, campaign_id="cmp-shop", campaign_type="SHOPPING", bid_amt=bid)
        _perf(db, gid, cost=1000, conv_amt=conv)
    db.commit()


# ── ① compute_benchmarks ─────────────────────────────────────────
def test_bid_band_from_high_performers(db):
    _seed_high_perf_shopping(db)
    compute_benchmarks(db, snapshot_date=SDATE)
    row = db.query(NaverBmBenchmark).filter_by(bench_kind="bid_band", bench_key="SHOPPING").one()
    import json
    val = json.loads(row.value_json)
    assert val == {"min": 300, "p50": 500, "max": 900, "n": 3}
    assert row.sample_n == 3
    assert bid_band_anchor(db, "SHOPPING") == 500


def test_low_sample_type_skips_band(db):
    # 고성과 그룹이 _MIN_HIGH_PERF_GROUPS 미만이면 밴드 미산출(fail-open).
    _snap_group(db, "s1", campaign_id="c", campaign_type="SHOPPING", bid_amt=300)
    _perf(db, "s1", cost=1000, conv_amt=5000)
    db.commit()
    compute_benchmarks(db, snapshot_date=SDATE)
    assert db.query(NaverBmBenchmark).filter_by(bench_kind="bid_band").count() == 0
    assert bid_band_anchor(db, "SHOPPING") is None


def test_keyword_verified_agency_only(db):
    # cmp-agency(settings 없음=none)=대행사 → 등록, cmp-ours(ours)=제외, off 키워드=제외.
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    _kw(db, "k1", "필름", campaign_id="cmp-agency")
    _kw(db, "k2", "케이스", campaign_id="cmp-agency")
    _kw(db, "k3", "우리키워드", campaign_id="cmp-ours")
    _kw(db, "k4", "꺼진키워드", campaign_id="cmp-agency", status="off")
    db.commit()
    compute_benchmarks(db, snapshot_date=SDATE)
    assert verified_keyword_set(db) == {"필름", "케이스"}
    row = db.query(NaverBmBenchmark).filter_by(bench_kind="keyword_verified", bench_key="WEB_SITE").one()
    assert row.sample_n == 2


def test_compute_is_idempotent_replace(db):
    _seed_high_perf_shopping(db)
    _kw(db, "k1", "필름", campaign_id="cmp-agency")
    db.commit()
    compute_benchmarks(db, snapshot_date=SDATE)
    n1 = db.query(NaverBmBenchmark).count()
    compute_benchmarks(db, snapshot_date=SDATE)  # 재실행 = 교체(중복 없음)
    assert db.query(NaverBmBenchmark).count() == n1
    assert bid_band_anchor(db, "SHOPPING") == 500


# ── ② loaders fail-open ──────────────────────────────────────────
def test_loaders_fail_open_when_absent(db):
    assert verified_keyword_set(db) == set()
    assert bid_band_anchor(db, "SHOPPING") is None


# ── ③ SS4 교차 배선 ──────────────────────────────────────────────
def _seed_promote(db, term="지문방지필름"):
    db.add(NaverCampaignSettings(campaign_id="cmp1", optimizer="ours", auto_operate=False))
    db.add(NaverProductBep(
        channel_id=NAVER_CHANNEL_ID, channel_product_id="P1", has_cost=True, product_name="템",
        selling_price=Decimal("1000"), contribution_margin=Decimal("500"), cost_price=0,
        commission_rate=0, logistics_cost=0,
    ))
    db.add(NaverSearchTermDaily(
        ad_date=date(2026, 7, 20), campaign_id="cmp1", adgroup_id="grp-1", search_term=term,
        source="shopping", imp=100, clk=30, cost=5000, rank_sum=0,
        conv_purchase_cnt=2, conv_direct_cnt=2, conv_purchase_amt=60000, cart_cnt=0, cart_amt=0,
    ))
    db.commit()


def test_ss4_cross_flag_when_verified(db):
    _seed_promote(db, term="지문방지필름")
    res = lane.run_search_term_ss_lane(db, now=_NOW, bm_prior={"지문방지필름"})
    assert res["promote_proposals_created"] == 1
    assert res["promote_bm_crossed"] == 1
    p = db.query(NaverProposal).filter_by(proposal_type=judge.SEARCH_TERM_PROMOTE_TYPE).one()
    assert "대행사 검증 키워드 교차" in p.rationale


def test_ss4_no_flag_when_prior_none_regression_zero(db):
    _seed_promote(db, term="지문방지필름")
    res = lane.run_search_term_ss_lane(db, now=_NOW, bm_prior=None)
    assert res["promote_proposals_created"] == 1
    assert res["promote_bm_crossed"] == 0
    p = db.query(NaverProposal).filter_by(proposal_type=judge.SEARCH_TERM_PROMOTE_TYPE).one()
    assert "대행사 검증 키워드 교차" not in p.rationale


def test_ss4_non_matching_prior_no_flag(db):
    _seed_promote(db, term="지문방지필름")
    res = lane.run_search_term_ss_lane(db, now=_NOW, bm_prior={"전혀다른키워드"})
    assert res["promote_bm_crossed"] == 0
    p = db.query(NaverProposal).filter_by(proposal_type=judge.SEARCH_TERM_PROMOTE_TYPE).one()
    assert "대행사 검증 키워드 교차" not in p.rationale


# ── ④ B-X adaptive_step 프라이어(None 폴백·앵커 시드·실기울기 미적용) ──
_BAND = exploration._EXPLORATION_TARGET_BAND
_CAP = exploration._EXPLORATION_STEP_PCT  # 0.30


def test_adaptive_step_cold_none_prior_unchanged(db):
    # 콜드(rank=None)·bm_prior=None → 기존 보수 소폭(+10%)=110(회귀 0).
    assert exploration.adaptive_step(100, None, _BAND, None, _CAP) == 110
    assert exploration.adaptive_step(100, None, _BAND, None, _CAP, bm_prior=None) == 110


def test_adaptive_step_cold_anchor_seeds_toward_band(db):
    # bm_prior=500(밴드 p50) → 앵커 시드, cap_bid=130으로 클램프 → 130(눈먼 110보다 근거 있는 상향).
    assert exploration.adaptive_step(100, None, _BAND, None, _CAP, bm_prior=500) == 130


def test_adaptive_step_anchor_below_current_ignored(db):
    # 하향 앵커(현재가 이하)는 무시 — 탐색은 UP만.
    assert exploration.adaptive_step(100, None, _BAND, None, _CAP, bm_prior=50) == 110


def test_adaptive_step_real_gradient_ignores_anchor(db):
    # 실관측 기울기 구간 → BM 앵커 미적용(폐루프 우선). bm_prior 유무 무관 동일.
    last = {"bid": 80, "rank": 8.0}
    base = exploration.adaptive_step(100, 6.0, _BAND, last, _CAP)
    with_prior = exploration.adaptive_step(100, 6.0, _BAND, last, _CAP, bm_prior=5000)
    assert base == with_prior == 120


# ── ⑤ harness fail-open(SA-3 예외 삼킴) ──────────────────────────
def test_harness_runs_sa3_and_fail_open(db, monkeypatch):
    from app.services.naver_ad import bm_benchmark, bm_harness
    _seed_high_perf_shopping(db)
    # SA-1/SA-2를 no-op으로 스텁(SA-3 단독 검증). SA-3는 이미 스냅샷을 시드했으므로 산출.
    monkeypatch.setattr(bm_harness, "snapshot_entities", lambda _db: {"ok": True})
    monkeypatch.setattr(bm_harness, "detect_agency_ops", lambda _db: {"ok": True})
    # run_bm_layer→compute_benchmarks(db)는 snapshot_date 미지정 시 bm_benchmark 모듈
    # 네임스페이스의 kst_today()를 기본값으로 쓴다 — SDATE(시드 날짜)와 정렬해야
    # 오늘 날짜와 무관하게 통과한다(날짜 고정 flaky 수정).
    monkeypatch.setattr(bm_benchmark, "kst_today", lambda: SDATE)
    out = bm_harness.run_bm_layer(db)
    assert out["benchmarks"] is not None
    assert db.query(NaverBmBenchmark).filter_by(bench_kind="bid_band").count() == 1


def test_harness_sa3_exception_does_not_break(db, monkeypatch):
    from app.services.naver_ad import bm_harness
    monkeypatch.setattr(bm_harness, "snapshot_entities", lambda _db: {"ok": True})
    monkeypatch.setattr(bm_harness, "detect_agency_ops", lambda _db: {"ok": True})
    monkeypatch.setattr(bm_harness, "compute_benchmarks", lambda _db: (_ for _ in ()).throw(RuntimeError("boom")))
    out = bm_harness.run_bm_layer(db)  # 예외 삼킴(fail-open) — 던지지 않음
    assert out["benchmarks"] is None
