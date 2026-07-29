# test_naver_conversion_maturity.py — 듀얼모드 스프린트 Phase 6 conversion_maturity 단위테스트
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverConversionMaturitySnapshot, NaverLearningState
from app.services.naver_ad import conversion_maturity as cm

TODAY = date(2026, 7, 8)


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


def _ad_row(ad_date, direct_amt, indirect_amt):
    return NaverAdDaily(
        ad_date=ad_date, campaign_id="cmp1", campaign_type="WEB_SITE",
        adgroup_id="grp1", keyword_id="nkw-1", imp=10, clk=5, cost=1000,
        conv_direct_amt=direct_amt, conv_indirect_amt=indirect_amt,
    )


# ── take_daily_snapshot ──
def test_take_daily_snapshot_records_today_even_if_zero(db):
    db.add(_ad_row(TODAY, 0, 0))
    db.commit()
    result = cm.take_daily_snapshot(db, today=TODAY)
    row = db.query(NaverConversionMaturitySnapshot).filter(
        NaverConversionMaturitySnapshot.ad_date == TODAY, NaverConversionMaturitySnapshot.days_since == 0,
    ).first()
    assert row is not None
    assert row.total_amt == 0
    assert result["rows_upserted"] >= 1


def test_take_daily_snapshot_skips_dates_with_no_data_at_all(db):
    # naver_ad_daily에 해당 날짜 행 자체가 없음(아직 미수집) — 스킵돼야 함
    result = cm.take_daily_snapshot(db, today=TODAY)
    rows = db.query(NaverConversionMaturitySnapshot).all()
    # days_since=0(오늘)만 예외적으로 기록 시도하지만 그마저 행이 없으므로 전부 스킵
    assert len(rows) == 0
    assert result["rows_upserted"] == 0


def test_take_daily_snapshot_is_idempotent_same_day_rerun(db):
    db.add(_ad_row(TODAY, 1000, 500))
    db.commit()
    cm.take_daily_snapshot(db, today=TODAY)
    cm.take_daily_snapshot(db, today=TODAY)  # 재실행
    rows = db.query(NaverConversionMaturitySnapshot).filter(
        NaverConversionMaturitySnapshot.ad_date == TODAY,
    ).all()
    assert len(rows) == 1  # 중복 생성 없음(upsert)
    assert rows[0].total_amt == 1500


def test_take_daily_snapshot_captures_multiple_days_since_for_same_cohort(db):
    """같은 ad_date를 여러 날에 걸쳐 관측하면 서로 다른 days_since로 누적된다(곡선 재료)."""
    ad_date = TODAY - timedelta(days=5)
    db.add(_ad_row(ad_date, 1000, 200))
    db.commit()
    cm.take_daily_snapshot(db, today=ad_date + timedelta(days=5))  # days_since=5
    cm.take_daily_snapshot(db, today=ad_date + timedelta(days=10))  # days_since=10 (다음날 실행 시뮬)
    rows = db.query(NaverConversionMaturitySnapshot).filter(
        NaverConversionMaturitySnapshot.ad_date == ad_date,
    ).order_by(NaverConversionMaturitySnapshot.days_since).all()
    assert [r.days_since for r in rows] == [5, 10]


# ── compute_curve ──
def test_compute_curve_requires_minimum_cohorts(db):
    # 성숙 코호트(days_since=MATURITY_DAYS) 1개뿐 — MIN_COHORTS_FOR_CURVE(3) 미달
    ad_date = TODAY - timedelta(days=cm.MATURITY_DAYS)
    db.add(NaverConversionMaturitySnapshot(
        ad_date=ad_date, days_since=cm.MATURITY_DAYS, direct_amt=800, indirect_amt=200, total_amt=1000,
    ))
    db.commit()
    result = cm.compute_curve(db)
    assert result["curve"] == {}
    assert result["cohort_n"] == 1


def test_compute_curve_averages_ratio_across_mature_cohorts(db):
    for i in range(3):
        ad_date = TODAY - timedelta(days=cm.MATURITY_DAYS + i)  # 서로 다른 코호트 날짜
        db.add(NaverConversionMaturitySnapshot(
            ad_date=ad_date, days_since=0, direct_amt=400, indirect_amt=0, total_amt=400,
        ))
        db.add(NaverConversionMaturitySnapshot(
            ad_date=ad_date, days_since=cm.MATURITY_DAYS, direct_amt=800, indirect_amt=200, total_amt=1000,
        ))
    db.commit()
    result = cm.compute_curve(db)
    assert result["cohort_n"] == 3
    assert result["curve"][0] == 0.4  # 400/1000 — 모든 코호트 동일 비율
    assert result["curve"][cm.MATURITY_DAYS] == 1.0


def test_compute_curve_excludes_cohort_with_zero_mature_amount(db):
    for i in range(3):
        ad_date = TODAY - timedelta(days=cm.MATURITY_DAYS + i)
        mature_amt = 0 if i == 0 else 1000  # 첫 코호트만 성숙시점 매출 0(신규저볼륨) — 제외돼야 함
        db.add(NaverConversionMaturitySnapshot(
            ad_date=ad_date, days_since=0, direct_amt=400, indirect_amt=0, total_amt=400,
        ))
        db.add(NaverConversionMaturitySnapshot(
            ad_date=ad_date, days_since=cm.MATURITY_DAYS, direct_amt=mature_amt, indirect_amt=0, total_amt=mature_amt,
        ))
    db.commit()
    result = cm.compute_curve(db)
    assert result["curve"][0] == 0.4  # 유효한 2개 코호트만으로 계산(400/1000 동일)


# ── run_daily ──
def test_run_daily_keeps_previous_state_when_insufficient_cohorts(db):
    db.add(NaverLearningState(scope="global", scope_key="day_0", metric="conv_delay", current_value=0.3, sample_n=10, confidence=1))
    db.commit()
    cm.run_daily(db, today=TODAY)
    row = db.query(NaverLearningState).filter(NaverLearningState.scope_key == "day_0").first()
    assert float(row.current_value) == 0.3  # 기존값 보존


def test_run_daily_writes_curve_when_sufficient_cohorts(db):
    for i in range(3):
        ad_date = TODAY - timedelta(days=cm.MATURITY_DAYS + i)
        db.add(NaverConversionMaturitySnapshot(
            ad_date=ad_date, days_since=0, direct_amt=400, indirect_amt=0, total_amt=400,
        ))
        db.add(NaverConversionMaturitySnapshot(
            ad_date=ad_date, days_since=cm.MATURITY_DAYS, direct_amt=800, indirect_amt=200, total_amt=1000,
        ))
    db.commit()
    cm.run_daily(db, today=TODAY)
    row = db.query(NaverLearningState).filter(
        NaverLearningState.scope_key == "day_0", NaverLearningState.metric == "conv_delay",
    ).first()
    assert row is not None
    assert float(row.current_value) == 0.4


# ── 전환 정착 보정(2026-07-28): m(d) 곡선 로드·환산 배수·상한 계산기 배선 ──
from decimal import Decimal  # noqa: E402

from app.services.naver_ad import bid_ceiling_calculator as bcc  # noqa: E402


def _seed_curve(db, mapping):
    """{days_since: m(d)} 를 NaverLearningState(global/conv_delay/day_N)에 심는다."""
    for d, m in mapping.items():
        db.add(NaverLearningState(
            scope="global", scope_key=f"day_{d}", metric=cm.METRIC,
            current_value=Decimal(str(m)), sample_n=5, confidence=Decimal("1"),
        ))
    db.commit()


def test_load_curve_reads_only_conv_delay_day_rows(db):
    _seed_curve(db, {0: 0.5, 3: 0.9})
    db.add(NaverLearningState(scope="global", scope_key="day_0", metric="other_metric",
                              current_value=Decimal("0.1"), sample_n=5))
    db.add(NaverLearningState(scope="global", scope_key="not_a_day", metric=cm.METRIC,
                              current_value=Decimal("0.1"), sample_n=5))
    db.commit()
    assert cm.load_curve(db) == {0: Decimal("0.5"), 3: Decimal("0.9")}


@pytest.mark.parametrize(("curve", "d", "expected"), [
    ({}, 0, Decimal("1")),                       # 곡선 없음 → 보정 없음(fail-safe)
    ({0: Decimal("0.5")}, 0, Decimal("2")),      # 절반만 보임 → 2배
    ({0: Decimal("0.5")}, 1, Decimal("1")),      # 해당 일 없음 → 보정 없음
    ({0: Decimal("0.1")}, 0, Decimal("3")),      # 1/0.1=10 이나 상한 3으로 클램프
    ({0: Decimal("0")}, 0, Decimal("1")),        # m=0 → 정의 불가 → 보정 없음
    ({0: Decimal("1")}, 0, Decimal("1")),        # 이미 다 참 → 보정 없음
    ({0: Decimal("1.4")}, 0, Decimal("1")),      # 이상값(>1) → 보정 없음
])
def test_maturity_multiplier_failsafes(curve, d, expected):
    assert cm.maturity_multiplier(curve, d) == expected


def test_maturity_multiplier_ignores_settled_days():
    curve = {cm.MATURITY_DAYS: Decimal("0.5")}
    assert cm.maturity_multiplier(curve, cm.MATURITY_DAYS) == Decimal("1")
    assert cm.maturity_multiplier(curve, -1) == Decimal("1")


def _seed_ad_day(db, *, ad_date, clk, direct):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id="cmp1", campaign_type="SHOPPING", adgroup_id="grp1",
        imp=100, clk=clk, cost=1000, conv_direct_amt=direct, conv_indirect_amt=0,
    ))


def test_rpc_unchanged_when_no_curve(db):
    """곡선이 없으면 종전과 동일한 RPC를 낸다(fail-safe — 회귀 방어)."""
    _seed_ad_day(db, ad_date=TODAY - timedelta(days=1), clk=10, direct=10_000)
    db.commit()
    clk, rpc = bcc._rpc_for(db, TODAY, adgroup_id="grp1")
    assert clk == 10 and rpc == Decimal(1000)


def test_rpc_projects_unsettled_recent_days_upward(db):
    """정착 중인 최근 날짜는 성숙 추정치로 환산돼 RPC가 올라간다.

    ★D-NAO-119 의도된 변경: MATURITY_CORRECTION_ENABLED가 기본 False로 게이트됐다(보류
    결정 — 모듈 docstring 참조). 이 테스트는 그 스위치가 켜졌을 때의 배선을 검증하는
    것이므로 명시적으로 켠다(기능이 지워진 게 아니라 게이트됐을 뿐 — 대응 회귀는
    test_naver_coldstart_rpc_shrinkage.py::test_maturity_correction_still_works_when_enabled).
    """
    _seed_curve(db, {1: 0.5})  # D+1 시점엔 최종 매출의 절반만 보인다
    _seed_ad_day(db, ad_date=TODAY - timedelta(days=1), clk=10, direct=10_000)
    db.commit()
    orig = bcc.MATURITY_CORRECTION_ENABLED
    bcc.MATURITY_CORRECTION_ENABLED = True
    try:
        clk, rpc = bcc._rpc_for(db, TODAY, adgroup_id="grp1")
    finally:
        bcc.MATURITY_CORRECTION_ENABLED = orig
    assert clk == 10
    assert rpc == Decimal(2000)  # 10,000 × 2 ÷ 10클릭 — 보정 없으면 1,000


def test_rpc_corrects_each_day_separately(db):
    """창 안의 날짜별로 서로 다른 배수가 적용된다(단일 SUM이면 불가능한 동작).

    ★D-NAO-119 의도된 변경: 위 테스트와 같은 이유로 MATURITY_CORRECTION_ENABLED를 명시적으로 켠다.
    """
    _seed_curve(db, {1: 0.5, 2: 1.0})   # D+1만 미정착, D+2는 이미 성숙
    _seed_ad_day(db, ad_date=TODAY - timedelta(days=1), clk=10, direct=10_000)  # ×2
    _seed_ad_day(db, ad_date=TODAY - timedelta(days=2), clk=10, direct=10_000)  # ×1
    db.commit()
    orig = bcc.MATURITY_CORRECTION_ENABLED
    bcc.MATURITY_CORRECTION_ENABLED = True
    try:
        clk, rpc = bcc._rpc_for(db, TODAY, adgroup_id="grp1")
    finally:
        bcc.MATURITY_CORRECTION_ENABLED = orig
    assert clk == 20
    assert rpc == Decimal(1500)  # (20,000 + 10,000) / 20 — 일괄 보정이면 2,000, 무보정이면 1,000


# ═══ 스킵 판정 ↔ 금액 집계 필터 정합 (2026-07-29) ═══
# 두 쿼리가 서로 다른 모집단을 보면 "아직 안 채워짐"이 "진짜 0원"으로 기록된다.
# 실측 피해: 07-01~03 코호트가 인위적 계단이 되어 곡선이 days 8~18에서 4/7로 고정됨.

def test_snapshot_skips_ad_date_with_only_backfill_sentinel_rows(db):
    """★회귀: sentinel 행만 있는 ad_date는 '미수집'이므로 0으로 기록하지 않고 스킵한다."""
    from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
    from app.services.naver_ad.conversion_maturity import take_daily_snapshot
    from app.models import NaverAdDaily, NaverConversionMaturitySnapshot

    ad_date = date(2026, 7, 20)
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id="cmp-x", campaign_type="SHOPPING",
        adgroup_id=BACKFILL_SENTINEL_ADGROUP, keyword_id="",
        imp=100, clk=10, cost=1000, conv_direct_amt=0, conv_indirect_amt=0,
    ))
    db.commit()
    take_daily_snapshot(db, today=date(2026, 7, 28))
    rows = db.query(NaverConversionMaturitySnapshot).filter(
        NaverConversionMaturitySnapshot.ad_date == ad_date).all()
    assert rows == [], "sentinel만 있는 날을 '관측된 0'으로 기록했다"


def test_snapshot_records_when_real_rows_exist(db):
    """실단위 행이 있으면 정상 기록 — 위 수정이 정상 경로를 막지 않는다."""
    from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
    from app.services.naver_ad.conversion_maturity import take_daily_snapshot
    from app.models import NaverAdDaily, NaverConversionMaturitySnapshot

    ad_date = date(2026, 7, 20)
    db.add(NaverAdDaily(  # sentinel(금액 집계에서 제외됨)
        ad_date=ad_date, campaign_id="cmp-x", campaign_type="SHOPPING",
        adgroup_id=BACKFILL_SENTINEL_ADGROUP, keyword_id="",
        imp=100, clk=10, cost=1000, conv_direct_amt=999_999, conv_indirect_amt=0,
    ))
    db.add(NaverAdDaily(  # 실단위
        ad_date=ad_date, campaign_id="cmp-x", campaign_type="SHOPPING",
        adgroup_id="grp-1", keyword_id="kw-1",
        imp=100, clk=10, cost=1000, conv_direct_amt=50_000, conv_indirect_amt=10_000,
    ))
    db.commit()
    take_daily_snapshot(db, today=date(2026, 7, 28))
    rows = db.query(NaverConversionMaturitySnapshot).filter(
        NaverConversionMaturitySnapshot.ad_date == ad_date).all()
    assert len(rows) == 1
    assert rows[0].total_amt == 60_000, "sentinel 금액이 섞이면 안 된다"
