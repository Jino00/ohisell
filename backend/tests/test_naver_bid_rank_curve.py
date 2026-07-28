# test_naver_bid_rank_curve.py — IU-R R3 반응 곡선 학습 bid_rank_curve 단위 테스트(PLAN §2 R3·§3-2).
#   조인 버킷 결정론(h−1/h+1·부분버킷 h 제외)·인과 오염 제외·실패 writer 제외·기울기 적합(양수
#   단위·개선폭>0만)·최소 관측쌍 미달 None·NaverLearningState upsert(scope=entity 규약)·무반응
#   관측 유지·콜드스타트 폴백·서보 prior 주입 차등을 못박는다.
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverChangeLog, NaverKeywordHourly, NaverLearningState, NaverProposal
from app.services.naver_ad import bid_rank_curve, rank_servo

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


# ── 픽스처 헬퍼 ──
def _add_bucket(db, *, entity_id, entity_type="adgroup", ad_date, hour, avg_rank, campaign_id="cmp-1"):
    db.add(NaverKeywordHourly(
        ad_date=ad_date, hour=hour, entity_type=entity_type, entity_id=entity_id,
        adgroup_id=entity_id if entity_type == "adgroup" else "grp-1",
        campaign_id=campaign_id, campaign_type="SHOPPING" if entity_type == "adgroup" else "WEB_SITE",
        imp=100, clk=5, cost=1000,
        avg_rank=Decimal(str(avg_rank)) if avg_rank is not None else None,
    ))


def _add_change(
    db, *, entity_id, entity_type="adgroup", ad_date, hour, minute=20, before_bid, after_bid,
    proposal_type="bid_up_servo", action="update_bid", dry_run=False, outcome=None,
    rationale=None, with_proposal=True, campaign_id="cmp-1", after_value_override="__unset__",
):
    """rank-step 실집행 change_log 1건(+연결 proposal). after_value_override로 실패 시나리오 표현."""
    proposal_id = None
    if with_proposal:
        p = NaverProposal(
            proposal_type=proposal_type, target_type=entity_type, target_id=entity_id,
            campaign_id=campaign_id, status="approved",
        )
        db.add(p)
        db.flush()
        proposal_id = p.id
    after_value = (
        json.dumps({"bidAmt": after_bid}) if after_value_override == "__unset__" else after_value_override
    )
    cl = NaverChangeLog(
        entity_type=entity_type, entity_id=entity_id, campaign_id=campaign_id, action=action,
        before_value=json.dumps({"bidAmt": before_bid}), after_value=after_value,
        changed_at=datetime(ad_date.year, ad_date.month, ad_date.day, hour, minute),
        dry_run=dry_run, outcome=outcome, rationale=rationale, proposal_id=proposal_id,
    )
    db.add(cl)
    db.flush()
    return cl


def _clean_obs(db, *, entity_id="grp-1", ad_date, hour, before_bid, after_bid,
               rank_before, rank_after, rank_partial=None, **kw):
    """h−1(rank_before)·h(부분·rank_partial)·h+1(rank_after) 버킷 + 변경 1건을 한 세트로 심는다."""
    _add_bucket(db, entity_id=entity_id, ad_date=ad_date, hour=hour - 1, avg_rank=rank_before)
    if rank_partial is not None:
        _add_bucket(db, entity_id=entity_id, ad_date=ad_date, hour=hour, avg_rank=rank_partial)
    _add_bucket(db, entity_id=entity_id, ad_date=ad_date, hour=hour + 1, avg_rank=rank_after)
    return _add_change(db, entity_id=entity_id, ad_date=ad_date, hour=hour,
                       before_bid=before_bid, after_bid=after_bid, **kw)


# ── 조인 버킷 결정론: rank_before=h−1, rank_after=h+1, 부분버킷 h 제외 ──
def test_bucket_before_h_minus_1_after_h_plus_1_partial_excluded(db):
    d = date(2026, 7, 10)
    # h−1=5.0, h(부분)=2.0(교란값 — 쓰이면 안 됨), h+1=4.0 → 개선폭 1.0, Δbid 500.
    _clean_obs(db, ad_date=d, hour=10, before_bid=1000, after_bid=1500,
               rank_before=5.0, rank_after=4.0, rank_partial=2.0)
    db.commit()
    obs = bid_rank_curve.build_observations(db, today=TODAY)
    pairs = obs[("adgroup", "grp-1")]
    assert len(pairs) == 1
    assert pairs[0]["delta_bid"] == 500
    # 부분버킷 h(2.0)를 rank_after로 썼다면 개선폭=3.0. h+1(4.0)을 써야 1.0.
    assert pairs[0]["improvement"] == Decimal("1.0")


def test_bucket_hour_rollover_h0_and_h23(db):
    d = date(2026, 7, 10)
    # h=0 → before는 전일(7/9) 23시.
    _add_bucket(db, entity_id="grp-1", ad_date=d - timedelta(days=1), hour=23, avg_rank=6.0)
    _add_bucket(db, entity_id="grp-1", ad_date=d, hour=1, avg_rank=5.0)
    _add_change(db, entity_id="grp-1", ad_date=d, hour=0, before_bid=1000, after_bid=1200)
    db.commit()
    obs = bid_rank_curve.build_observations(db, today=TODAY)
    pairs = obs[("adgroup", "grp-1")]
    assert pairs[0]["improvement"] == Decimal("1.0")  # 6.0 − 5.0


def test_missing_bucket_excludes_pair(db):
    d = date(2026, 7, 10)
    # h+1 버킷 없음 → 쌍 제외.
    _add_bucket(db, entity_id="grp-1", ad_date=d, hour=9, avg_rank=5.0)
    _add_change(db, entity_id="grp-1", ad_date=d, hour=10, before_bid=1000, after_bid=1500)
    db.commit()
    # 스캔된 유닛은 쌍 전탈락이어도 키 유지(무효 마킹 원료, codex R3 2R P1) — 쌍만 비어야 한다.
    assert bid_rank_curve.build_observations(db, today=TODAY) == {("adgroup", "grp-1"): []}


def test_null_avg_rank_bucket_excludes_pair(db):
    d = date(2026, 7, 10)
    _add_bucket(db, entity_id="grp-1", ad_date=d, hour=9, avg_rank=5.0)
    _add_bucket(db, entity_id="grp-1", ad_date=d, hour=11, avg_rank=None)  # NULL → 부재 취급
    _add_change(db, entity_id="grp-1", ad_date=d, hour=10, before_bid=1000, after_bid=1500)
    db.commit()
    # 스캔된 유닛은 쌍 전탈락이어도 키 유지(무효 마킹 원료, codex R3 2R P1) — 쌍만 비어야 한다.
    assert bid_rank_curve.build_observations(db, today=TODAY) == {("adgroup", "grp-1"): []}


# ── 인과 오염 제외 ──
def test_contamination_second_update_bid_in_window_excludes(db):
    d = date(2026, 7, 10)
    _clean_obs(db, ad_date=d, hour=10, before_bid=1000, after_bid=1500, rank_before=5.0, rank_after=4.0)
    # 같은 유닛·같은 창(h+1=11시)에 다른 update_bid → 오염.
    _add_change(db, entity_id="grp-1", ad_date=d, hour=11, minute=5, before_bid=1500, after_bid=1600)
    db.commit()
    # 스캔된 유닛은 쌍 전탈락이어도 키 유지(무효 마킹 원료, codex R3 2R P1) — 쌍만 비어야 한다.
    assert bid_rank_curve.build_observations(db, today=TODAY) == {("adgroup", "grp-1"): []}


def test_contamination_external_bid_change_excludes(db):
    d = date(2026, 7, 10)
    _clean_obs(db, ad_date=d, hour=10, before_bid=1000, after_bid=1500, rank_before=5.0, rank_after=4.0)
    # 외부 입찰 변경이 창에 겹침(h−1=9시) → 오염.
    _add_change(db, entity_id="grp-1", ad_date=d, hour=9, minute=30, before_bid=900, after_bid=1000,
                action="external_bid_change", with_proposal=False)
    db.commit()
    # 스캔된 유닛은 쌍 전탈락이어도 키 유지(무효 마킹 원료, codex R3 2R P1) — 쌍만 비어야 한다.
    assert bid_rank_curve.build_observations(db, today=TODAY) == {("adgroup", "grp-1"): []}


def test_no_contamination_when_other_change_outside_window(db):
    d = date(2026, 7, 10)
    _clean_obs(db, ad_date=d, hour=10, before_bid=1000, after_bid=1500, rank_before=5.0, rank_after=4.0)
    # 창 밖(h+2=12:30, 창은 [9:00,12:00)) → 오염 아님.
    _add_change(db, entity_id="grp-1", ad_date=d, hour=12, minute=30, before_bid=1500, after_bid=1600)
    db.commit()
    obs = bid_rank_curve.build_observations(db, today=TODAY)
    assert len(obs[("adgroup", "grp-1")]) == 1


def test_dry_run_and_failed_neighbor_do_not_contaminate(db):
    """codex R3 P2 — 우리 실집행 오염은 '성공 실쓰기'만. dry-run·실패 이웃은 순위 미교란이라
    정상 관측쌍이 살아남아야 한다."""
    d = date(2026, 7, 10)
    _clean_obs(db, ad_date=d, hour=10, before_bid=1000, after_bid=1500, rank_before=5.0, rank_after=4.0)
    # 창 안(h+1=11시) dry-run 이웃 update_bid → 실제 변경 아님(제외).
    _add_change(db, entity_id="grp-1", ad_date=d, hour=11, minute=5, before_bid=1500, after_bid=1600,
                dry_run=True)
    # 창 안 실패 이웃(dry_run=False·after_value None·outcome=failed) → 실반영 없음(제외).
    _add_change(db, entity_id="grp-1", ad_date=d, hour=9, minute=40, before_bid=900, after_bid=1000,
                after_value_override=None, outcome="failed")
    db.commit()
    obs = bid_rank_curve.build_observations(db, today=TODAY)
    assert len(obs[("adgroup", "grp-1")]) == 1  # 정상 쌍 생존(오탐 배제 안 됨)


# ── 실패 writer 제외(§codex P2-5 — 적합 함수 내부 규칙) ──
def test_failed_writer_missing_after_value_excluded(db):
    d = date(2026, 7, 10)
    _add_bucket(db, entity_id="grp-1", ad_date=d, hour=9, avg_rank=5.0)
    _add_bucket(db, entity_id="grp-1", ad_date=d, hour=11, avg_rank=4.0)
    # after_value None(실패 writer) → 쿼리 필터에서 제외.
    _add_change(db, entity_id="grp-1", ad_date=d, hour=10, before_bid=1000, after_bid=1500,
                after_value_override=None, outcome="failed")
    db.commit()
    assert bid_rank_curve.build_observations(db, today=TODAY) == {}


def test_failed_marker_in_rationale_excluded(db):
    d = date(2026, 7, 10)
    # after_value가 남아있더라도 실패 마커/ outcome=failed면 방어적으로 제외.
    _clean_obs(db, ad_date=d, hour=10, before_bid=1000, after_bid=1500, rank_before=5.0, rank_after=4.0,
               outcome="failed", rationale="뭔가 [실행 실패] ValueError")
    db.commit()
    # 스캔된 유닛은 쌍 전탈락이어도 키 유지(무효 마킹 원료, codex R3 2R P1) — 쌍만 비어야 한다.
    assert bid_rank_curve.build_observations(db, today=TODAY) == {("adgroup", "grp-1"): []}


# ── 기울기 적합 ──
def test_slope_fit_positive_unit_and_upsert(db):
    d0 = date(2026, 7, 8)
    # 3개 관측쌍 전부 Δbid/개선폭 = 500/1.0 = 500 → 중앙값 500.
    for i in range(3):
        _clean_obs(db, ad_date=d0 + timedelta(days=i), hour=10, before_bid=1000, after_bid=1500,
                   rank_before=5.0, rank_after=4.0)
    db.commit()
    res = bid_rank_curve.run_daily(db, today=TODAY)
    assert res["upserted"] == 1
    row = db.query(NaverLearningState).filter(
        NaverLearningState.scope == "entity",
        NaverLearningState.scope_key == "adgroup:grp-1",
        NaverLearningState.metric == "bid_rank_slope",
    ).first()
    assert row is not None
    assert row.current_value == Decimal("500.0000")
    assert row.sample_n == 3
    assert row.confidence == Decimal("0.3000")  # 3/10


def test_only_positive_improvement_pairs_fit(db):
    d0 = date(2026, 7, 8)
    # 3개 유효쌍(개선폭>0) + 1개 무반응쌍(개선폭 0) — 무반응은 관측 유지되나 적합엔 미기여.
    for i in range(3):
        _clean_obs(db, ad_date=d0 + timedelta(days=i), hour=10, before_bid=1000, after_bid=1500,
                   rank_before=5.0, rank_after=4.0)
    _clean_obs(db, ad_date=d0 + timedelta(days=5), hour=14, before_bid=1000, after_bid=2000,
               rank_before=4.0, rank_after=4.0)  # 개선폭 0(무반응)
    db.commit()
    obs = bid_rank_curve.build_observations(db, today=TODAY)
    pairs = obs[("adgroup", "grp-1")]
    assert len(pairs) == 4  # 무반응쌍도 관측엔 포함(제외 아님)
    fit = bid_rank_curve._fit_slope(pairs)
    assert fit is not None
    slope, n, _ = fit
    assert n == 3  # 무반응쌍(2000/0)은 적합 미기여
    assert slope == Decimal("500.0000")  # 무반응쌍이 끼면 곡선이 왜곡됨 — 배제 실증


def test_min_pairs_not_met_invalid_marks_and_prior_not_loaded(db):
    """유효쌍 < 최소 → skip 보존이 아니라 무효 마킹 upsert(codex R3 P1-a) → 프라이어 미로드."""
    d0 = date(2026, 7, 8)
    for i in range(2):  # 유효쌍 2개 < 최소 3
        _clean_obs(db, ad_date=d0 + timedelta(days=i), hour=10, before_bid=1000, after_bid=1500,
                   rank_before=5.0, rank_after=4.0)
    db.commit()
    res = bid_rank_curve.run_daily(db, today=TODAY)
    assert res["upserted"] == 0
    assert res["invalidated"] == 1
    row = db.query(NaverLearningState).filter(NaverLearningState.scope_key == "adgroup:grp-1").first()
    assert row is not None  # 스킵 보존 아님 — 행은 존재하되 무효로 마킹.
    assert row.current_value is None
    assert row.confidence == Decimal("0")
    assert row.sample_n == 2  # 관측 근거 보존.
    assert bid_rank_curve.load_response_priors(db) == {}  # 서보에 주입 안 됨.


def test_stale_prior_from_slide_out_expires_on_read(db):
    """표본이 30일 밖으로 밀려 더는 스캔 안 되는 유닛의 옛 행(sample_n=5·updated_at 5일 전)은
    신선도 만료로 읽기에서 배제된다(codex R3 P1-b)."""
    db.add(NaverLearningState(
        scope="entity", scope_key="adgroup:grp-old", metric="bid_rank_slope",
        current_value=Decimal("500"), sample_n=5, confidence=Decimal("0.5"),
        updated_at=bid_rank_curve._utc_now() - timedelta(days=5),
    ))
    db.commit()
    assert bid_rank_curve.load_response_priors(db) == {}


def test_healthy_unit_refreshed_daily_stays_loaded(db):
    """매일 스캔·재산정되는 건강 유닛은 updated_at이 새로 찍혀 만료에 안 걸린다(P1-b 대칭)."""
    d0 = date(2026, 7, 8)
    for i in range(3):
        _clean_obs(db, ad_date=d0 + timedelta(days=i), hour=10, before_bid=1000, after_bid=1500,
                   rank_before=5.0, rank_after=4.0)
    db.commit()
    bid_rank_curve.run_daily(db, today=TODAY)  # updated_at=utcnow로 명시 갱신.
    assert bid_rank_curve.load_response_priors(db) == {"adgroup:grp-1": Decimal("500.0000")}


def test_negative_delta_bid_invalid_marks(db):
    d0 = date(2026, 7, 8)
    # Δbid≤0 이상쌍(입찰이 오히려 내려감) — 적합 배제 → 유효쌍 0 → 무효 마킹.
    for i in range(3):
        _clean_obs(db, ad_date=d0 + timedelta(days=i), hour=10, before_bid=1500, after_bid=1000,
                   rank_before=5.0, rank_after=4.0)
    db.commit()
    res = bid_rank_curve.run_daily(db, today=TODAY)
    assert res["upserted"] == 0
    assert res["invalidated"] == 1
    assert bid_rank_curve.load_response_priors(db) == {}


def test_keyword_grain_scope_key_prefix(db):
    d0 = date(2026, 7, 8)
    for i in range(3):
        _add_bucket(db, entity_id="nkw-9", entity_type="keyword", ad_date=d0 + timedelta(days=i), hour=9, avg_rank=5.0)
        _add_bucket(db, entity_id="nkw-9", entity_type="keyword", ad_date=d0 + timedelta(days=i), hour=11, avg_rank=4.0)
        _add_change(db, entity_id="nkw-9", entity_type="keyword", ad_date=d0 + timedelta(days=i), hour=10,
                    before_bid=1000, after_bid=1500, proposal_type="bid_up_rank")
    db.commit()
    bid_rank_curve.run_daily(db, today=TODAY)
    row = db.query(NaverLearningState).filter(NaverLearningState.scope_key == "keyword:nkw-9").first()
    assert row is not None
    assert row.metric == "bid_rank_slope"


# ── 윈도우 경계: 오늘(D) 변경은 익일 반영(제외) ──
def test_today_changes_excluded_from_window(db):
    # TODAY 당일 변경 → window_end=오늘 00:00 미만 필터로 제외(h+1 버킷 미스윕).
    _clean_obs(db, ad_date=TODAY, hour=10, before_bid=1000, after_bid=1500, rank_before=5.0, rank_after=4.0)
    db.commit()
    assert bid_rank_curve.build_observations(db, today=TODAY) == {}


def test_beyond_lookback_excluded(db):
    d = TODAY - timedelta(days=bid_rank_curve._LOOKBACK_DAYS + 2)
    _clean_obs(db, ad_date=d, hour=10, before_bid=1000, after_bid=1500, rank_before=5.0, rank_after=4.0)
    db.commit()
    assert bid_rank_curve.build_observations(db, today=TODAY) == {}


# ── load_response_priors + 서보 prior 주입 차등 ──
def test_load_response_priors_returns_slopes(db):
    db.add(NaverLearningState(scope="entity", scope_key="adgroup:grp-1", metric="bid_rank_slope",
                              current_value=Decimal("500"), sample_n=3, confidence=Decimal("0.3")))
    db.add(NaverLearningState(scope="entity", scope_key="keyword:nkw-9", metric="bid_rank_slope",
                              current_value=Decimal("300"), sample_n=4, confidence=Decimal("0.4")))
    # 다른 scope/metric은 섞이지 않아야 함.
    db.add(NaverLearningState(scope="global", scope_key="day_3", metric="conv_delay",
                              current_value=Decimal("1.2"), sample_n=5))
    db.commit()
    priors = bid_rank_curve.load_response_priors(db)
    assert priors == {"adgroup:grp-1": Decimal("500"), "keyword:nkw-9": Decimal("300")}


def test_prior_makes_servo_step_differ_from_cold_start(db):
    # 콜드스타트(prior 없음) = +15% → 1000×1.15=1150.
    cold = rank_servo.decide_servo_step(Decimal("4.9"), 1000, imp_sum=45, economic_ceiling=5000, response_prior=None)
    assert cold["target_bid"] == 1150
    # prior 주입(기울기 500원/rank) = current + 500×0.9 = 1450(±15% 초과 차등).
    priored = rank_servo.decide_servo_step(
        Decimal("4.9"), 1000, imp_sum=45, economic_ceiling=5000, response_prior=Decimal("500"),
    )
    assert priored["target_bid"] == 1450
    assert priored["target_bid"] != cold["target_bid"]


# ── run_daily 빈 상태 ──
def test_scanned_unit_all_pairs_dropped_still_invalidated(db):
    """스캔은 됐지만 전 쌍이 탈락(버킷 부재)한 유닛도 무효 마킹된다(codex R3 2R P1) —
    전날 유효했던 slope가 신선도 창(3일) 안에서 살아남는 구멍 봉인."""
    # 전날까지 유효했던 프라이어 행(신선) 시드.
    from app.services.naver_ad.bid_rank_curve import _upsert_learning_state, _utc_now
    _upsert_learning_state(db, scope_key="adgroup:grp-1", value=Decimal("500"),
                           sample_n=5, confidence=Decimal("0.5"), now_utc=_utc_now())
    db.commit()
    assert "adgroup:grp-1" in bid_rank_curve.load_response_priors(db)
    # 오늘 스캔: 변경 1건은 있으나 버킷(h−1/h+1)이 전무 → 관측쌍 0개.
    d = date(2026, 7, 10)
    _add_change(db, entity_id="grp-1", ad_date=d, hour=10, before_bid=1000, after_bid=1500)
    db.commit()
    res = bid_rank_curve.run_daily(db, today=TODAY)
    assert res["invalidated"] == 1 and res["upserted"] == 0
    row = db.query(NaverLearningState).filter(NaverLearningState.scope_key == "adgroup:grp-1").first()
    assert row.current_value is None and row.sample_n == 0
    assert bid_rank_curve.load_response_priors(db) == {}  # 옛 slope 주입 차단.


def test_run_daily_no_data(db):
    res = bid_rank_curve.run_daily(db, today=TODAY)
    assert res == {"units": 0, "upserted": 0, "invalidated": 0}


# ── 곡선 원료 타입 회귀(2026-07-28): 원료 필터가 RANK_STEP_TYPES가 아니라 CURVE_SAMPLE_TYPES ──
# 배경: 종전 필터 RANK_STEP_TYPES={bid_up_servo, bid_up_rank}는 prod 실집행 0건인 타입뿐이라,
# 실제로 219건 실행된 bid_up_explore와 평시 주력 bid_up/growth_bid_up이 통째로 원료에서 빠져
# bid_rank_slope가 0행이었다(→ rank_servo 영구 콜드스타트·EX deep 예외 휴면). 아래 테스트는
# 필터를 RANK_STEP_TYPES로 되돌리면 전부 실패한다.
@pytest.mark.parametrize(
    "ptype", ["bid_up_explore", "bid_up", "growth_bid_up", "bid_up_cold", "bid_up_servo", "bid_up_rank"],
)
def test_all_upward_step_types_are_curve_samples(db, ptype):
    d = date(2026, 7, 10)
    _clean_obs(db, ad_date=d, hour=10, before_bid=1000, after_bid=1500,
               rank_before=5.0, rank_after=4.0, proposal_type=ptype)
    db.commit()
    obs = bid_rank_curve.build_observations(db, today=TODAY)
    pairs = obs[("adgroup", "grp-1")]
    assert len(pairs) == 1, f"{ptype} 실집행이 곡선 원료에서 누락됨"
    assert pairs[0]["delta_bid"] == 500
    assert pairs[0]["improvement"] == Decimal("1.0")


def test_explore_steps_alone_can_fit_a_slope(db):
    """탐색 스텝만으로도 유효쌍 _MIN_PAIRS를 채우면 slope가 적립된다(prod 219건이 이 경로)."""
    d = date(2026, 7, 10)
    for i, hour in enumerate((3, 9, 15)):  # 쿨다운·오염창(±2h) 겹치지 않게 이격.
        _clean_obs(db, ad_date=d + timedelta(days=i), hour=hour, before_bid=1000, after_bid=1300,
                   rank_before=5.0, rank_after=4.0, proposal_type="bid_up_explore")
    db.commit()
    res = bid_rank_curve.run_daily(db, today=TODAY)
    assert res["upserted"] == 1 and res["invalidated"] == 0
    priors = bid_rank_curve.load_response_priors(db)
    assert priors["adgroup:grp-1"] == Decimal("300.0000")  # Δbid 300 / 개선폭 1.0


def test_bid_down_is_not_a_curve_sample(db):
    """하향은 아직 원료가 아니다(반대 사분면 — _fit_slope 유효쌍 정의를 함께 넓혀야 함)."""
    d = date(2026, 7, 10)
    _clean_obs(db, ad_date=d, hour=10, before_bid=1500, after_bid=1000,
               rank_before=4.0, rank_after=5.0, proposal_type="bid_down")
    db.commit()
    assert bid_rank_curve.build_observations(db, today=TODAY) == {}
