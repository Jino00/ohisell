# test_naver_expansion.py — 확장 압력 판정·배분 순수 SA 단위테스트 (스프린트 EX, D-NAO-85)
# 실 API 0·실쓰기 0(순수 판정/랭킹만). DB 픽스처는 test_exploration/test_naver_auto_operator 관례.
# 보정계수·BEP 해석은 diagnosis.correction_factor·campaign_target_resolver를 patch로 고정한다
# (test_naver_auto_operator의 patch.object(...diagnosis, "correction_factor") 관례 미러).
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverEntity, NaverKeywordHourly
from app.services.naver_ad import auto_operator, exploration, expansion_allocator, expansion_pressure
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

CAMPAIGN = "cmp-shop"
TODAY = date(2026, 7, 23)
WINDOW = expansion_pressure._settlement_window(TODAY)  # [오늘-8, 오늘-2]
IN_WINDOW = TODAY - timedelta(days=5)  # 정착창 안
ACTUAL_FACTOR = {"factor": Decimal("1"), "factor_low": Decimal("1"), "factor_high": Decimal("1"), "factor_point": Decimal("1"), "source": "actual_revenue_ratio"}
UNAVAILABLE_FACTOR = {"factor": Decimal("1"), "factor_low": Decimal("1"), "factor_high": Decimal("1"), "factor_point": Decimal("1"), "source": "unavailable"}


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


def _campaign(db, *, campaign_id=CAMPAIGN, campaign_type="SHOPPING", status="on"):
    db.add(NaverEntity(entity_type="campaign", entity_id=campaign_id,
                       campaign_id=campaign_id, campaign_type=campaign_type, status=status))
    db.commit()


def _adgroup(db, *, adgroup_id, campaign_id=CAMPAIGN, campaign_type="SHOPPING", status="on", bid_amt=100):
    db.add(NaverEntity(entity_type="adgroup", entity_id=adgroup_id, parent_id=campaign_id,
                       campaign_id=campaign_id, campaign_type=campaign_type, status=status, bid_amt=bid_amt))
    db.commit()


def _daily(db, *, adgroup_id, ad_date, clk=0, imp=100, cost=1000, conv_amt=0,
           campaign_id=CAMPAIGN, campaign_type="SHOPPING", keyword_id="-"):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type=campaign_type,
        adgroup_id=adgroup_id, keyword_id=keyword_id, imp=imp, clk=clk, cost=cost,
        conv_direct_amt=conv_amt, conv_indirect_amt=0,
    ))
    db.commit()


def _hourly(db, *, adgroup_id, avg_rank, imp=100, ad_date=None, hour=10, campaign_id=CAMPAIGN):
    db.add(NaverKeywordHourly(
        ad_date=ad_date or (TODAY - timedelta(days=3)), hour=hour,
        entity_type="adgroup", entity_id=adgroup_id, adgroup_id=adgroup_id,
        campaign_id=campaign_id, campaign_type="SHOPPING", imp=imp, clk=0, cost=0,
        avg_rank=avg_rank,
    ))
    db.commit()


def _patch_bep(value):
    """캠페인 BEP 원값을 고정 — 캠페인 매핑 BEP=None, 계정 기본 BEP=value로 폴백 사다리 통과."""
    return patch.multiple(
        expansion_pressure.campaign_target_resolver,
        weighted_product_value_for_campaign=lambda db, cid, col: None,
        account_default_bep_roas=lambda db: Decimal(str(value)),
    )


# ═══════════════════════════ expansion_pressure ═══════════════════════════

def _seed_campaign_settlement(db, *, clk, cost, conv_amt, adgroup_id="ag-1"):
    """정착창 안에 캠페인 집계 표본을 심는다(단일 그룹 1행)."""
    _campaign(db)
    _adgroup(db, adgroup_id=adgroup_id)
    _daily(db, adgroup_id=adgroup_id, ad_date=IN_WINDOW, clk=clk, cost=cost, conv_amt=conv_amt)


def test_pressure_expansion_mode_when_gap_met(db):
    """갭 충족(보정ROAS/BEP≥1.25)·표본 충분 → expansion_mode=True."""
    # conv_amt/cost = 4500/1000 = 4.5 ROAS; /BEP 1.5 = 3.0 ≥ 1.25
    _seed_campaign_settlement(db, clk=40, cost=1000, conv_amt=4500)
    with _patch_bep("1.5"), patch.object(expansion_pressure.diagnosis, "correction_factor",
                                          return_value=ACTUAL_FACTOR):
        got = expansion_pressure.judge_campaign_pressure(db, CAMPAIGN, today=TODAY)
    assert got["expansion_mode"] is True
    assert got["roas_ratio"] == Decimal("3.0000")
    assert got["window_clk"] == 40
    assert got["bep_roas"] == Decimal("1.5")


def test_pressure_gap_insufficient(db):
    """갭 미달(보정ROAS/BEP<1.25) → expansion_mode=False, roas_ratio는 채워짐."""
    # 1600/1000 = 1.6 ROAS; /BEP 1.5 = 1.0667 < 1.25
    _seed_campaign_settlement(db, clk=40, cost=1000, conv_amt=1600)
    with _patch_bep("1.5"), patch.object(expansion_pressure.diagnosis, "correction_factor",
                                          return_value=ACTUAL_FACTOR):
        got = expansion_pressure.judge_campaign_pressure(db, CAMPAIGN, today=TODAY)
    assert got["expansion_mode"] is False
    assert got["roas_ratio"] < expansion_pressure.EX_PRESSURE_RATIO
    assert "갭 부족" in got["reason"]


def test_pressure_sample_deficient(db):
    """정착창 clk < 30 → expansion_mode=False(표본 미달), 갭 계산 전 차단(roas_ratio None)."""
    _seed_campaign_settlement(db, clk=20, cost=1000, conv_amt=4500)
    with _patch_bep("1.5"), patch.object(expansion_pressure.diagnosis, "correction_factor",
                                          return_value=ACTUAL_FACTOR):
        got = expansion_pressure.judge_campaign_pressure(db, CAMPAIGN, today=TODAY)
    assert got["expansion_mode"] is False
    assert got["roas_ratio"] is None
    assert "표본 미달" in got["reason"]


def test_pressure_correction_unavailable_fail_closed(db):
    """보정계수 unavailable → fail-closed expansion_mode=False."""
    _seed_campaign_settlement(db, clk=40, cost=1000, conv_amt=4500)
    with _patch_bep("1.5"), patch.object(expansion_pressure.diagnosis, "correction_factor",
                                          return_value=UNAVAILABLE_FACTOR):
        got = expansion_pressure.judge_campaign_pressure(db, CAMPAIGN, today=TODAY)
    assert got["expansion_mode"] is False
    assert got["roas_ratio"] is None
    assert "보정계수 unavailable" in got["reason"]


def test_pressure_bep_unresolved_fail_closed(db):
    """BEP 해석 불가 → fail-closed expansion_mode=False."""
    _seed_campaign_settlement(db, clk=40, cost=1000, conv_amt=4500)
    with patch.multiple(
        expansion_pressure.campaign_target_resolver,
        weighted_product_value_for_campaign=lambda db, cid, col: None,
        account_default_bep_roas=lambda db: None,
    ), patch.object(expansion_pressure.diagnosis, "correction_factor", return_value=ACTUAL_FACTOR):
        got = expansion_pressure.judge_campaign_pressure(db, CAMPAIGN, today=TODAY)
    assert got["expansion_mode"] is False
    assert "BEP" in got["reason"]


def test_pressure_sentinel_excluded_from_clk(db):
    """backfill sentinel 행은 캠페인 clk 집계에서 제외(2배 집계 함정 방지)."""
    _campaign(db)
    _adgroup(db, adgroup_id="ag-1")
    _daily(db, adgroup_id="ag-1", ad_date=IN_WINDOW, clk=40, cost=1000, conv_amt=4500)
    # sentinel 행(거대 clk) — 집계에 새면 window_clk이 오염된다.
    _daily(db, adgroup_id=BACKFILL_SENTINEL_ADGROUP, ad_date=IN_WINDOW, clk=9999, cost=9999, conv_amt=0)
    with _patch_bep("1.5"), patch.object(expansion_pressure.diagnosis, "correction_factor",
                                          return_value=ACTUAL_FACTOR):
        got = expansion_pressure.judge_campaign_pressure(db, CAMPAIGN, today=TODAY)
    assert got["window_clk"] == 40  # sentinel 9999 제외


def test_pressure_boundary_clk_exactly_30_passes_sample(db):
    """경계: clk==30은 표본 게이트 통과(≥30)."""
    _seed_campaign_settlement(db, clk=30, cost=1000, conv_amt=4500)
    with _patch_bep("1.5"), patch.object(expansion_pressure.diagnosis, "correction_factor",
                                          return_value=ACTUAL_FACTOR):
        got = expansion_pressure.judge_campaign_pressure(db, CAMPAIGN, today=TODAY)
    assert got["expansion_mode"] is True


# ══════════════ 상수 정합(로컬 복제 ↔ 원본) ══════════════

def test_settlement_window_constants_parity():
    """정착창 상수 복제본 == auto_operator 원본(조용한 divergence 차단)."""
    assert expansion_pressure._SETTLEMENT_WINDOW_START_DAYS == auto_operator._SETTLEMENT_WINDOW_START_DAYS
    assert expansion_pressure._SETTLEMENT_WINDOW_END_DAYS == auto_operator._SETTLEMENT_WINDOW_END_DAYS
    assert expansion_allocator._SETTLEMENT_WINDOW_START_DAYS == auto_operator._SETTLEMENT_WINDOW_START_DAYS
    assert expansion_allocator._SETTLEMENT_WINDOW_END_DAYS == auto_operator._SETTLEMENT_WINDOW_END_DAYS
    assert expansion_pressure._settlement_window(TODAY) == auto_operator._settlement_window(TODAY)
    assert expansion_allocator._settlement_window(TODAY) == auto_operator._settlement_window(TODAY)


def test_allocator_band_and_sample_constants_parity():
    """배분 밴드·표본 상수 복제본 == exploration 원본."""
    assert expansion_allocator._BAND_LOW == exploration._EXPLORATION_BAND_LOW
    assert expansion_allocator._BAND_HIGH == exploration._EXPLORATION_BAND_HIGH
    assert expansion_allocator._OWN_SAMPLE_CLK == exploration._MIN_CLICK_FOR_EXPLORATION


# ═══════════════════════════ expansion_allocator ═══════════════════════════

# 확장 모드 pressure 스텁(harness가 넘기는 형태) — bep_roas 1.5, 갭 통과 상태.
def _pressure(bep="1.5", ratio="2.0"):
    return {
        "campaign_id": CAMPAIGN, "expansion_mode": True,
        "roas_ratio": Decimal(ratio), "corrected_roas": Decimal("3.0"),
        "bep_roas": Decimal(bep), "window_clk": 40, "reason": "확장 모드",
    }


def test_allocator_returns_empty_when_not_expansion_mode(db):
    """expansion_mode=False pressure → 빈 리스트."""
    _campaign(db)
    _adgroup(db, adgroup_id="ag-1")
    got = expansion_allocator.allocate_expansion(
        db, CAMPAIGN, today=TODAY, pressure={"expansion_mode": False, "bep_roas": Decimal("1.5")},
    )
    assert got == []


def test_allocator_three_tier_ordering(db):
    """3층 랭킹 순서: 1층(밴드내) → 2층(고수요·밴드밖) → 3층(증거창)."""
    _campaign(db)
    # 1층: 순위 3.0(밴드내), 표본 미달(clk 0 정착창) → campaign_prior
    _adgroup(db, adgroup_id="ag-t1")
    _hourly(db, adgroup_id="ag-t1", avg_rank=Decimal("3.0"), imp=100)
    _daily(db, adgroup_id="ag-t1", ad_date=TODAY - timedelta(days=2), imp=100, clk=0, cost=0)
    # 2층: 순위 6.0(밴드밖·관측됨), 7일 노출≥100
    _adgroup(db, adgroup_id="ag-t2")
    _hourly(db, adgroup_id="ag-t2", avg_rank=Decimal("6.0"), imp=100)
    _daily(db, adgroup_id="ag-t2", ad_date=TODAY - timedelta(days=2), imp=200, clk=0, cost=0)
    # 3층: 순위 미상(시간별 없음)·고수요(노출 200)·표본미달·예산여유·클릭미달 → 증거창 활성
    _adgroup(db, adgroup_id="ag-t3")
    _daily(db, adgroup_id="ag-t3", ad_date=TODAY - timedelta(days=2), imp=200, clk=1, cost=1000)

    with patch.object(expansion_allocator.diagnosis, "correction_factor", return_value=ACTUAL_FACTOR):
        got = expansion_allocator.allocate_expansion(db, CAMPAIGN, today=TODAY, pressure=_pressure())
    tiers = [(r["adgroup_id"], r["rank_tier"]) for r in got]
    assert tiers == [("ag-t1", 1), ("ag-t2", 2), ("ag-t3", 3)]


def test_allocator_within_tier_conv_amt_desc(db):
    """층 내부 정렬 = 정착창 conv_amt 내림차순(볼륨 우선)."""
    _campaign(db)
    for gid, conv in [("ag-lo", 1500), ("ag-hi", 9000)]:
        _adgroup(db, adgroup_id=gid)
        _hourly(db, adgroup_id=gid, avg_rank=Decimal("3.0"), imp=100)
        # clk 20(표본 충분)·자기 보정ROAS = conv/1000 /1.5 = ≥1 → own 채택
        _daily(db, adgroup_id=gid, ad_date=IN_WINDOW, imp=100, clk=20, cost=1000, conv_amt=conv)
    with patch.object(expansion_allocator.diagnosis, "correction_factor", return_value=ACTUAL_FACTOR):
        got = expansion_allocator.allocate_expansion(db, CAMPAIGN, today=TODAY, pressure=_pressure())
    assert [r["adgroup_id"] for r in got] == ["ag-hi", "ag-lo"]


def test_allocator_own_below_bep_excluded(db):
    """자기 표본 충분(clk≥10)·자기 보정ROAS < BEP → 제외(자기 증거 반박)."""
    _campaign(db)
    _adgroup(db, adgroup_id="ag-below")
    _hourly(db, adgroup_id="ag-below", avg_rank=Decimal("3.0"), imp=100)
    # conv 1000/cost 1000 = 1.0 ROAS; /BEP 1.5 = 0.667 < 1 → 제외
    _daily(db, adgroup_id="ag-below", ad_date=IN_WINDOW, imp=100, clk=20, cost=1000, conv_amt=1000)
    with patch.object(expansion_allocator.diagnosis, "correction_factor", return_value=ACTUAL_FACTOR):
        got = expansion_allocator.allocate_expansion(db, CAMPAIGN, today=TODAY, pressure=_pressure())
    assert got == []


def test_allocator_prior_fallback_adopts(db):
    """자기 표본 미달(clk<10) → 캠페인 프라이어 상속 채택(judged_by=campaign_prior)."""
    _campaign(db)
    _adgroup(db, adgroup_id="ag-thin")
    _hourly(db, adgroup_id="ag-thin", avg_rank=Decimal("3.0"), imp=100)
    _daily(db, adgroup_id="ag-thin", ad_date=IN_WINDOW, imp=100, clk=3, cost=200, conv_amt=100)
    with patch.object(expansion_allocator.diagnosis, "correction_factor", return_value=ACTUAL_FACTOR):
        got = expansion_allocator.allocate_expansion(db, CAMPAIGN, today=TODAY, pressure=_pressure())
    assert len(got) == 1
    assert got[0]["judged_by"] == "campaign_prior"
    assert got[0]["marginal_stop"] is False


def test_allocator_cap_five(db):
    """캠페인당 상위 EX_DAILY_GROUP_CAP(=5)개로 컷."""
    _campaign(db)
    for i in range(7):
        gid = f"ag-{i}"
        _adgroup(db, adgroup_id=gid)
        _hourly(db, adgroup_id=gid, avg_rank=Decimal("3.0"), imp=100)
        _daily(db, adgroup_id=gid, ad_date=IN_WINDOW, imp=100, clk=20, cost=1000, conv_amt=3000 + i)
    with patch.object(expansion_allocator.diagnosis, "correction_factor", return_value=ACTUAL_FACTOR):
        got = expansion_allocator.allocate_expansion(db, CAMPAIGN, today=TODAY, pressure=_pressure())
    assert len(got) == expansion_allocator.EX_DAILY_GROUP_CAP == 5


def test_allocator_marginal_stop_flag(db):
    """slope 프라이어 존재 ∧ 자기ROAS BEP 근접(<bep×1.1) → marginal_stop=True."""
    _campaign(db)
    _adgroup(db, adgroup_id="ag-vertex")
    _hourly(db, adgroup_id="ag-vertex", avg_rank=Decimal("3.0"), imp=100)
    # conv 1550/cost 1000 = 1.55 ROAS; /BEP 1.5 = 1.0333 ∈ [1, 1.1) → 근접
    _daily(db, adgroup_id="ag-vertex", ad_date=IN_WINDOW, imp=100, clk=20, cost=1000, conv_amt=1550)
    priors = {"adgroup:ag-vertex": Decimal("120.0")}  # slope 프라이어 존재
    with patch.object(expansion_allocator.diagnosis, "correction_factor", return_value=ACTUAL_FACTOR):
        got = expansion_allocator.allocate_expansion(
            db, CAMPAIGN, today=TODAY, pressure=_pressure(), response_priors=priors,
        )
    assert len(got) == 1
    assert got[0]["marginal_stop"] is True


def test_allocator_no_marginal_stop_without_prior(db):
    """slope 프라이어 없으면 BEP 근접이어도 marginal_stop=False(정지 판정 불가 → 관측)."""
    _campaign(db)
    _adgroup(db, adgroup_id="ag-vertex")
    _hourly(db, adgroup_id="ag-vertex", avg_rank=Decimal("3.0"), imp=100)
    _daily(db, adgroup_id="ag-vertex", ad_date=IN_WINDOW, imp=100, clk=20, cost=1000, conv_amt=1550)
    with patch.object(expansion_allocator.diagnosis, "correction_factor", return_value=ACTUAL_FACTOR):
        got = expansion_allocator.allocate_expansion(
            db, CAMPAIGN, today=TODAY, pressure=_pressure(), response_priors={},
        )
    assert got[0]["marginal_stop"] is False


def test_allocator_ctr_alert_group_excluded(db):
    """CTR 경보 그룹은 확장 후보에서 제외(소재 처방 대상 — UP 무의미)."""
    _campaign(db)
    _adgroup(db, adgroup_id="ag-ctr")
    _hourly(db, adgroup_id="ag-ctr", avg_rank=Decimal("3.0"), imp=100)
    _daily(db, adgroup_id="ag-ctr", ad_date=IN_WINDOW, imp=100, clk=20, cost=1000, conv_amt=3000)
    with patch.object(expansion_allocator.diagnosis, "correction_factor", return_value=ACTUAL_FACTOR), \
         patch.object(expansion_allocator.ctr_alert, "detect_ctr_alerts",
                       return_value={"alerts": [{"adgroup_id": "ag-ctr"}]}):
        got = expansion_allocator.allocate_expansion(db, CAMPAIGN, today=TODAY, pressure=_pressure())
    assert got == []


# ══════════ codex P2-1: 과열밴드(rank≤2.5) fail-closed ══════════

def test_allocator_overheated_band_excluded_before_evidence(db):
    """관측된 순위 ≤ 2.5(과열밴드)는 증거창 평가 전에 제외(fail-closed) — evidence 조건을 모두
    갖춰도 채택되지 않는다(exploration 밴드 의미론: 상향 여지 없음)."""
    _campaign(db)
    _adgroup(db, adgroup_id="ag-hot")
    _hourly(db, adgroup_id="ag-hot", avg_rank=Decimal("2.0"), imp=100)  # 과열밴드
    # 고수요·표본미달·예산여유·클릭미달 = 증거창 조건 충족(수정 전이라면 tier3로 채택됐을 것)
    _daily(db, adgroup_id="ag-hot", ad_date=TODAY - timedelta(days=2), imp=200, clk=1, cost=1000, conv_amt=0)
    with patch.object(expansion_allocator.diagnosis, "correction_factor", return_value=ACTUAL_FACTOR):
        got = expansion_allocator.allocate_expansion(db, CAMPAIGN, today=TODAY, pressure=_pressure())
    assert got == []


# ══════════ codex P2-2: CTR 경보 판정 시각 정합 ══════════

def test_allocator_ctr_alert_called_with_today_based_now(db):
    """detect_ctr_alerts에 today 기반 now를 명시 전달(now.date()==today) — 재현 결정성."""
    _campaign(db)
    _adgroup(db, adgroup_id="ag-1")
    _hourly(db, adgroup_id="ag-1", avg_rank=Decimal("3.0"), imp=100)
    _daily(db, adgroup_id="ag-1", ad_date=IN_WINDOW, imp=100, clk=20, cost=1000, conv_amt=3000)
    spy = MagicMock(return_value={"alerts": []})
    with patch.object(expansion_allocator.diagnosis, "correction_factor", return_value=ACTUAL_FACTOR), \
         patch.object(expansion_allocator.ctr_alert, "detect_ctr_alerts", spy):
        expansion_allocator.allocate_expansion(db, CAMPAIGN, today=TODAY, pressure=_pressure())
    spy.assert_called_once()
    passed_now = spy.call_args.kwargs.get("now")
    assert passed_now is not None, "now를 명시 전달해야 한다(기본 kst_now() 금지)"
    assert passed_now.date() == TODAY


# ══════════ codex P2-3: 보정계수 unavailable 시 자기표본 그룹 fail-closed ══════════

def test_allocator_own_sample_excluded_when_correction_unavailable(db):
    """보정계수 unavailable ∧ 자기 표본 충분(clk≥10∧cost>0) → 제외(검증 불가=확장 금지).
    표본 미달 그룹의 campaign_prior 폴백은 이 문맥에서도 유지된다."""
    _campaign(db)
    # own 표본 충분 그룹 — 보정 불가면 제외되어야
    _adgroup(db, adgroup_id="ag-own")
    _hourly(db, adgroup_id="ag-own", avg_rank=Decimal("3.0"), imp=100)
    _daily(db, adgroup_id="ag-own", ad_date=IN_WINDOW, imp=100, clk=20, cost=1000, conv_amt=3000)
    # 표본 미달 그룹 — 보정 불가여도 campaign_prior로 채택 유지
    _adgroup(db, adgroup_id="ag-thin")
    _hourly(db, adgroup_id="ag-thin", avg_rank=Decimal("3.0"), imp=100)
    _daily(db, adgroup_id="ag-thin", ad_date=IN_WINDOW, imp=100, clk=3, cost=200, conv_amt=100)
    with patch.object(expansion_allocator.diagnosis, "correction_factor", return_value=UNAVAILABLE_FACTOR):
        got = expansion_allocator.allocate_expansion(db, CAMPAIGN, today=TODAY, pressure=_pressure())
    ids = [r["adgroup_id"] for r in got]
    assert "ag-own" not in ids  # 자기표본 그룹 fail-closed 제외
    assert ids == ["ag-thin"]   # 표본 미달 그룹은 campaign_prior 채택 유지
    assert got[0]["judged_by"] == "campaign_prior"


def test_allocator_untiered_group_excluded(db):
    """어느 층에도 안 드는 그룹(밴드밖 저수요·증거창 비활성)은 제외."""
    _campaign(db)
    _adgroup(db, adgroup_id="ag-none")
    _hourly(db, adgroup_id="ag-none", avg_rank=Decimal("6.0"), imp=100)  # 밴드밖
    # 7일 노출 30(<100 저수요) → 2층 실패·증거창 비활성(저수요)
    _daily(db, adgroup_id="ag-none", ad_date=TODAY - timedelta(days=2), imp=30, clk=20, cost=1000, conv_amt=3000)
    with patch.object(expansion_allocator.diagnosis, "correction_factor", return_value=ACTUAL_FACTOR):
        got = expansion_allocator.allocate_expansion(db, CAMPAIGN, today=TODAY, pressure=_pressure())
    assert got == []


# ══════════ D-NAO-85 §4 A안: 과열밴드 deep 예외(우량 자기증거는 tier1 유지) ══════════

def test_allocator_deep_exception_overheated_kept(db):
    """과열밴드(rank≤2.5)라도 4조건(자기표본 clk≥10∧cost>0·보정ROAS/BEP≥1.25·slope 프라이어∧
    marginal_stop 아님) 전부 충족 → tier1 채택(deep=True). D-NAO-59 한계 수렴 발동 자리."""
    _campaign(db)
    _adgroup(db, adgroup_id="ag-hotdeep")
    _hourly(db, adgroup_id="ag-hotdeep", avg_rank=Decimal("2.0"), imp=100)  # 과열밴드
    # clk 20(표본 충분)·보정ROAS/BEP = (3000/1000)/1.5 = 2.0 ≥ 1.25 ∧ ≥ 1.1
    _daily(db, adgroup_id="ag-hotdeep", ad_date=IN_WINDOW, imp=100, clk=20, cost=1000, conv_amt=3000)
    priors = {"adgroup:ag-hotdeep": Decimal("120.0")}  # slope 프라이어 존재
    with patch.object(expansion_allocator.diagnosis, "correction_factor", return_value=ACTUAL_FACTOR):
        got = expansion_allocator.allocate_expansion(
            db, CAMPAIGN, today=TODAY, pressure=_pressure(), response_priors=priors,
        )
    assert len(got) == 1
    assert got[0]["adgroup_id"] == "ag-hotdeep"
    assert got[0]["rank_tier"] == 1
    assert got[0]["deep"] is True
    assert got[0]["judged_by"] == "own"
    assert got[0]["marginal_stop"] is False


def test_allocator_normal_tier1_deep_false(db):
    """비과열 밴드내(rank∈(2.5,4]) 정상 채택 그룹은 deep=False(deep은 과열밴드 예외 전용 표시)."""
    _campaign(db)
    _adgroup(db, adgroup_id="ag-normal")
    _hourly(db, adgroup_id="ag-normal", avg_rank=Decimal("3.0"), imp=100)
    _daily(db, adgroup_id="ag-normal", ad_date=IN_WINDOW, imp=100, clk=20, cost=1000, conv_amt=3000)
    priors = {"adgroup:ag-normal": Decimal("120.0")}
    with patch.object(expansion_allocator.diagnosis, "correction_factor", return_value=ACTUAL_FACTOR):
        got = expansion_allocator.allocate_expansion(
            db, CAMPAIGN, today=TODAY, pressure=_pressure(), response_priors=priors,
        )
    assert len(got) == 1
    assert got[0]["deep"] is False


def test_allocator_deep_exception_denied_when_thin_sample(db):
    """과열밴드 + 자기표본 미달(clk<10) → deep 불가 → 제외(기존 fail-closed 불변)."""
    _campaign(db)
    _adgroup(db, adgroup_id="ag-hotthin")
    _hourly(db, adgroup_id="ag-hotthin", avg_rank=Decimal("2.0"), imp=100)
    _daily(db, adgroup_id="ag-hotthin", ad_date=IN_WINDOW, imp=200, clk=3, cost=200, conv_amt=3000)
    priors = {"adgroup:ag-hotthin": Decimal("120.0")}
    with patch.object(expansion_allocator.diagnosis, "correction_factor", return_value=ACTUAL_FACTOR):
        got = expansion_allocator.allocate_expansion(
            db, CAMPAIGN, today=TODAY, pressure=_pressure(), response_priors=priors,
        )
    assert got == []


def test_allocator_deep_exception_denied_when_ratio_below_pressure(db):
    """과열밴드 + 자기표본 충분이나 보정ROAS/BEP < 1.25(여유 불충분) → deep 불가 → 제외.
    (BEP 이상이라 비과열이면 tier1 채택됐을 그룹이 과열밴드에선 여유 문턱 미달로 제외)."""
    _campaign(db)
    _adgroup(db, adgroup_id="ag-hotmid")
    _hourly(db, adgroup_id="ag-hotmid", avg_rank=Decimal("2.0"), imp=100)
    # 보정ROAS/BEP = (1700/1000)/1.5 = 1.1333 ∈ [1, 1.25) → ② 미충족(≥1이라 below-BEP는 아님)
    _daily(db, adgroup_id="ag-hotmid", ad_date=IN_WINDOW, imp=100, clk=20, cost=1000, conv_amt=1700)
    priors = {"adgroup:ag-hotmid": Decimal("120.0")}
    with patch.object(expansion_allocator.diagnosis, "correction_factor", return_value=ACTUAL_FACTOR):
        got = expansion_allocator.allocate_expansion(
            db, CAMPAIGN, today=TODAY, pressure=_pressure(), response_priors=priors,
        )
    assert got == []


def test_allocator_deep_exception_denied_without_slope_prior(db):
    """과열밴드 + 자기증거 우량(ROAS/BEP≥1.25)이나 slope 프라이어 없음 → deep 불가(③) → 제외."""
    _campaign(db)
    _adgroup(db, adgroup_id="ag-hotnoslope")
    _hourly(db, adgroup_id="ag-hotnoslope", avg_rank=Decimal("2.0"), imp=100)
    _daily(db, adgroup_id="ag-hotnoslope", ad_date=IN_WINDOW, imp=100, clk=20, cost=1000, conv_amt=3000)
    with patch.object(expansion_allocator.diagnosis, "correction_factor", return_value=ACTUAL_FACTOR):
        got = expansion_allocator.allocate_expansion(
            db, CAMPAIGN, today=TODAY, pressure=_pressure(), response_priors={},
        )
    assert got == []


def test_allocator_deep_exception_denied_when_marginal_stop(db):
    """과열밴드 + 자기ROAS BEP 근접(ROAS/BEP<1.1, marginal_stop 영역) → deep 불가 → 제외
    (② 문턱 1.25가 ④ 문턱 1.1을 포함 — 근접 그룹은 어느 게이트로도 제외)."""
    _campaign(db)
    _adgroup(db, adgroup_id="ag-hotmarg")
    _hourly(db, adgroup_id="ag-hotmarg", avg_rank=Decimal("2.0"), imp=100)
    # 보정ROAS/BEP = (1600/1000)/1.5 = 1.0667 < 1.1
    _daily(db, adgroup_id="ag-hotmarg", ad_date=IN_WINDOW, imp=100, clk=20, cost=1000, conv_amt=1600)
    priors = {"adgroup:ag-hotmarg": Decimal("120.0")}
    with patch.object(expansion_allocator.diagnosis, "correction_factor", return_value=ACTUAL_FACTOR):
        got = expansion_allocator.allocate_expansion(
            db, CAMPAIGN, today=TODAY, pressure=_pressure(), response_priors=priors,
        )
    assert got == []


def test_allocator_deep_exception_denied_when_correction_unavailable(db):
    """과열밴드 + 자기표본 충분·slope 존재이나 보정계수 unavailable(own_ratio 검증 불가)
    → deep 불가(②) → 제외(fail-closed)."""
    _campaign(db)
    _adgroup(db, adgroup_id="ag-hotnofac")
    _hourly(db, adgroup_id="ag-hotnofac", avg_rank=Decimal("2.0"), imp=100)
    _daily(db, adgroup_id="ag-hotnofac", ad_date=IN_WINDOW, imp=100, clk=20, cost=1000, conv_amt=3000)
    priors = {"adgroup:ag-hotnofac": Decimal("120.0")}
    with patch.object(expansion_allocator.diagnosis, "correction_factor", return_value=UNAVAILABLE_FACTOR):
        got = expansion_allocator.allocate_expansion(
            db, CAMPAIGN, today=TODAY, pressure=_pressure(), response_priors=priors,
        )
    assert got == []


# ══════════ P4 밴드 동적화(D-NAO-85 §4-3) — ladder_judgment/adaptive_step deep_ok ══════════

def test_ladder_band_reached_deep_ok_continues_up():
    """밴드 도달(2.5<rank≤4)·무클릭·deep_ok=True → step_up(정적 밴드 해제). deep_ok=False → stop_observe(회귀)."""
    since = {"clk": 0, "imp": 100, "cost": 500, "avg_rank": Decimal("3.0")}
    probe = {"bid": 100, "rank": Decimal("4.5")}
    v_deep, _ = exploration.ladder_judgment(probe, since, ceiling=1000, current_bid=100, deep_ok=True)
    v_static, _ = exploration.ladder_judgment(probe, since, ceiling=1000, current_bid=100, deep_ok=False)
    assert v_deep == "step_up"
    assert v_static == "stop_observe"


def test_ladder_overheated_deep_ok_continues_up():
    """과열밴드(rank≤2.5)·무클릭·deep_ok=True → step_up. deep_ok=False → not_rank(회귀)."""
    since = {"clk": 0, "imp": 100, "cost": 500, "avg_rank": Decimal("2.0")}
    probe = {"bid": 100, "rank": Decimal("3.0")}
    v_deep, _ = exploration.ladder_judgment(probe, since, ceiling=1000, current_bid=100, deep_ok=True)
    v_static, _ = exploration.ladder_judgment(probe, since, ceiling=1000, current_bid=100, deep_ok=False)
    assert v_deep == "step_up"
    assert v_static == "not_rank"


def test_ladder_deep_ok_capped_when_ceiling_reached():
    """deep_ok=True여도 경제성 상한 도달(current≥ceiling)이면 capped(상한이 유일 브레이크)."""
    since = {"clk": 0, "imp": 100, "cost": 500, "avg_rank": Decimal("3.0")}
    probe = {"bid": 100, "rank": Decimal("4.5")}
    v, _ = exploration.ladder_judgment(probe, since, ceiling=100, current_bid=100, deep_ok=True)
    assert v == "capped"


def test_ladder_deep_ok_does_not_override_click_handoff():
    """clk>0(증거 확보)은 deep_ok여도 stop_observe 유지(밴드 도달 정지 분기만 해제)."""
    since = {"clk": 3, "imp": 100, "cost": 500, "avg_rank": Decimal("3.0")}
    probe = {"bid": 100, "rank": Decimal("4.5")}
    v, _ = exploration.ladder_judgment(probe, since, ceiling=1000, current_bid=100, deep_ok=True)
    assert v == "stop_observe"


def test_adaptive_step_in_band_deep_ok_steps_up():
    """밴드 내(rank≤band_high)에서 deep_ok=True → 상향 스텝 산출, deep_ok=False → None(회귀)."""
    band = exploration._EXPLORATION_TARGET_BAND
    got_deep = exploration.adaptive_step(1000, Decimal("3.0"), band, None, Decimal("0.30"), deep_ok=True)
    got_static = exploration.adaptive_step(1000, Decimal("3.0"), band, None, Decimal("0.30"), deep_ok=False)
    assert got_static is None
    assert got_deep is not None and got_deep > 1000
