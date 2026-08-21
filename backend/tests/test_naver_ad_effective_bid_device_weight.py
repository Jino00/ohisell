# test_naver_ad_effective_bid_device_weight.py — D-NAO-218(M2-b2)
# effective_bid의 기기 입찰가중치 소비(ref 65 정정 #2 effective_bid:58 소비처) — 명목 입찰과
# 실효(기기가중치 반영) 입찰이 배선 지점에서 실제로 달라짐을 고정한다(합격②의 핵심 증거).
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverEntity
from app.services.naver_ad import effective_bid


@pytest.fixture
def db():
    # ★autoflush=False — prod SessionLocal과 동일(app/database.py). 계약 스펙이 명시적으로
    # 요구한 확인 사항.
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _seed_weight(db, adgroup_id, pc, mobile):
    db.add(NaverEntity(
        entity_type="adgroup", entity_id=adgroup_id, parent_id="cmp-1",
        campaign_id="cmp-1", campaign_type="SHOPPING", name="그룹",
        status="on", pc_bid_weight=pc, mobile_bid_weight=mobile,
    ))
    db.commit()


# ── device_weight_multiplier / apply_device_weight (순수 계산) ──

def test_multiplier_both_100_is_identity():
    assert effective_bid.device_weight_multiplier(100, 100) == Decimal(1)


def test_multiplier_null_both_defaults_to_100_identity():
    """②NULL은 100으로 취급 — 배율 1(명목=실효)."""
    assert effective_bid.device_weight_multiplier(None, None) == Decimal(1)


def test_multiplier_partial_null_defaults_only_missing_side():
    """pc만 관측되고 mobile이 NULL이면 mobile만 100 취급."""
    # pc=70·mobile=None(→100) → max(70,100)=100 → 배율 1
    assert effective_bid.device_weight_multiplier(70, None) == Decimal(1)
    # pc=None(→100)·mobile=130 → max(100,130)=130 → 배율 1.3
    assert effective_bid.device_weight_multiplier(None, 130) == Decimal("1.3")


def test_multiplier_picks_max_of_pc_and_mobile_conservative():
    """PC/모바일 미구분 소비처를 위한 보수 선택 — 둘 중 큰 쪽(모듈 헤더 근거)."""
    assert effective_bid.device_weight_multiplier(70, 80) == Decimal("0.8")


def test_multiplier_above_100_not_clamped():
    """★스펙 ④: 100 초과(range 10~500 공식 확정)도 잘리지 않는다 — AG5054=130 실재."""
    assert effective_bid.device_weight_multiplier(130, 100) == Decimal("1.3")


def test_apply_device_weight_scales_nominal_bid():
    assert effective_bid.apply_device_weight(1000, 70, 70) == 700
    assert effective_bid.apply_device_weight(1000, 130, 100) == 1300
    assert effective_bid.apply_device_weight(1000, None, None) == 1000


def test_apply_device_weight_rounds_half_up_not_down():
    """반올림 규격 고정 — 105×0.33=34.65원. HALF_UP=35, DOWN=34(잘림)를 가른다."""
    assert effective_bid.apply_device_weight(105, 33, 33) == 35


# ── adgroup_device_weights (배치 조회) ──

def test_adgroup_device_weights_reads_seeded_row(db):
    _seed_weight(db, "grp-1", 70, 80)
    out = effective_bid.adgroup_device_weights(db, ["grp-1"])
    assert out["grp-1"] == {"pc": 70, "mobile": 80}


def test_adgroup_device_weights_missing_row_absent_not_faked(db):
    """entity sync 전(행 자체가 없음) — dict에 키가 아예 없다(100을 지어내지 않는다).
    호출부가 .get(id, {"pc": None, "mobile": None})으로 명시 처리한다."""
    out = effective_bid.adgroup_device_weights(db, ["grp-unknown"])
    assert "grp-unknown" not in out


# ── adgroup_effective_bids 통합: 배선 전(명목) vs 배선 후(실효) 산출 차이 ──

def test_adgroup_effective_bids_applies_device_weight_to_group_fallback(db):
    """★핵심 증거(합격②): 그룹입찰 폴백 경로(소재 데이터 없음)에서 기기가중치 70% →
    effective_bid가 명목 1000원의 700원으로 달라진다. effective_bid_nominal에 배선 전
    값(1000)이 그대로 보존된다(대조용)."""
    _seed_weight(db, "grp-1", 70, 70)
    out = effective_bid.adgroup_effective_bids(db, {"grp-1": 1000})
    assert out["grp-1"]["effective_bid_nominal"] == 1000  # 배선 전
    assert out["grp-1"]["effective_bid"] == 700            # 배선 후(실효)
    assert out["grp-1"]["device_pc_weight"] == 70
    assert out["grp-1"]["device_mobile_weight"] == 70


def test_adgroup_effective_bids_null_weight_leaves_value_unchanged(db):
    """②미관측(NULL) 그룹은 100 취급 — effective_bid가 배선 전과 동일(회귀 0 보장 축)."""
    out = effective_bid.adgroup_effective_bids(db, {"grp-no-entity": 500})
    assert out["grp-no-entity"]["effective_bid_nominal"] == 500
    assert out["grp-no-entity"]["effective_bid"] == 500
    assert out["grp-no-entity"]["device_pc_weight"] is None
    assert out["grp-no-entity"]["device_mobile_weight"] is None


def test_adgroup_effective_bids_above_100_weight_increases_effective_bid(db):
    """★스펙 ④: 100 초과 가중치는 실효 입찰을 명목보다 **높인다**(과대평가 방향만이 아니다 —
    ref 65 "명목>실효" 단정을 이 슬라이스에서 재확인·수정)."""
    _seed_weight(db, "grp-up", 130, 100)
    out = effective_bid.adgroup_effective_bids(db, {"grp-up": 1000})
    assert out["grp-up"]["effective_bid"] == 1300


# ── nominal_ceiling_for_device (rank_servo:49 소비처 전용 변환) ──

def test_nominal_ceiling_for_device_no_weight_is_identity(db):
    nominal, w = effective_bid.nominal_ceiling_for_device(db, "grp-none", 1100)
    assert nominal == 1100
    assert w == {"pc": None, "mobile": None}


def test_nominal_ceiling_for_device_scales_up_when_weight_below_100(db):
    """★핵심 증거(rank_servo 소비처): 가중치 50% → 실효 상한 1100원을 명목 스케일로 되돌리면
    2200원(=1100/0.5) — 명목 기준으로 비교하는 rank_servo가 상한을 과소평가하지 않는다."""
    _seed_weight(db, "grp-half", 50, 50)
    nominal, w = effective_bid.nominal_ceiling_for_device(db, "grp-half", 1100)
    assert nominal == 2200
    assert w == {"pc": 50, "mobile": 50}


def test_nominal_ceiling_for_device_scales_down_when_weight_above_100(db):
    """가중치 130% → 명목 상한이 실효 상한보다 **작아야** 한다(같은 명목을 써도 실제로 더
    나가므로 상한을 좁혀야 안전)."""
    _seed_weight(db, "grp-over", 130, 100)
    nominal, w = effective_bid.nominal_ceiling_for_device(db, "grp-over", 1300)
    assert nominal == 1000  # 1300 / 1.3


def test_nominal_ceiling_for_device_zero_ceiling_passthrough(db):
    """economic_ceiling=0(입찰 근거 없음)은 나눗셈 없이 그대로 0 — rank_servo의 fail-closed
    hold 경로를 건드리지 않는다."""
    _seed_weight(db, "grp-zero", 50, 50)
    nominal, _w = effective_bid.nominal_ceiling_for_device(db, "grp-zero", 0)
    assert nominal == 0
