# test_naver_vitality_signal.py — VT1 vitality_signal SA 단위테스트 (D-NAO-81 B축).
# 커버: S1/S2 신호 궤적·충돌 방지 게이트(GATE ① A축 정지 개체 소생 0)·ours 스코프
#   (GATE ⑤ 스코프 밖 0)·소생 대상 선정/우선순위 + 03 실데이터 백테스트(§검증 1).
# 실 API 0 — 순수 read-only SA라 mock 불필요(naver_ad_daily/entity/change_log 시드만).
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
    NaverCampaignSettings,
    NaverChangeLog,
    NaverEntity,
)
from app.services.naver_ad import vitality_signal

CAMPAIGN = "cmp-03"
GROUP = "grp-03"


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


def _settings(db, *, campaign_id=CAMPAIGN, auto_operate=True):
    db.add(NaverCampaignSettings(campaign_id=campaign_id, auto_operate=auto_operate, optimizer="ours"))
    db.commit()


def _entity(db, *, entity_id=GROUP, campaign_id=CAMPAIGN, status="on", entity_type="adgroup",
            parent_id="", qi_grade=None):
    db.add(NaverEntity(entity_type=entity_type, entity_id=entity_id, parent_id=parent_id,
                       campaign_id=campaign_id, status=status, qi_grade=qi_grade))
    db.commit()


def _daily(db, *, ad_date, imp, rank, campaign_id=CAMPAIGN, adgroup_id=GROUP,
           clk=20, conv_cnt=2, conv_amt=50000):
    """naver_ad_daily 1행 — rank_sum=round(rank*imp)(캠페인 avg_rank=rank_sum/imp 역산)."""
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type="SHOPPING",
        adgroup_id=adgroup_id, keyword_id="", imp=imp, clk=clk, cost=10000,
        rank_sum=round(rank * imp),
        conv_direct_cnt=conv_cnt, conv_indirect_cnt=0,
        conv_direct_amt=conv_amt, conv_indirect_amt=0,
    ))
    db.commit()


# 03 실측(PLAN §검증 1): 노출 2023→2103→1812→1559→924→1175→796(07-12~18),
# avg_rank 07-16=3.5(재구성·라이브 수집 07-17 개시)·07-17=3.9·07-18=4.7.
_IMP = {
    date(2026, 7, 12): 2023, date(2026, 7, 13): 2103, date(2026, 7, 14): 1812,
    date(2026, 7, 15): 1559, date(2026, 7, 16): 924, date(2026, 7, 17): 1175,
    date(2026, 7, 18): 796,
}
_RANK = {
    date(2026, 7, 12): 3.0, date(2026, 7, 13): 3.0,
    date(2026, 7, 14): 3.0, date(2026, 7, 15): 3.2, date(2026, 7, 16): 3.5,
    date(2026, 7, 17): 3.9, date(2026, 7, 18): 4.7,
}


def _dec(x) -> Decimal:
    return Decimal(str(x))


def _seed_03(db, *, conv_days=None):
    _settings(db)
    _entity(db)
    conv_days = conv_days if conv_days is not None else set(_IMP)
    for d, imp in _IMP.items():
        _daily(db, ad_date=d, imp=imp, rank=_RANK[d],
               conv_cnt=(2 if d in conv_days else 0),
               conv_amt=(50000 if d in conv_days else 0))


# ══════════════════════════ S1/S2 신호 ══════════════════════════

def test_s1_fires_on_sustained_drop_robust_to_blip(db):
    # as_of=07-16: 924 < 1559·1812, 3일전(07-13=2103) 대비 −56% → S1 True.
    imp_by = {d: _IMP[d] for d in _IMP}
    fired, detail = vitality_signal._signal_s1(imp_by, date(2026, 7, 16))
    assert fired is True and detail["cum_drop_pct"] >= 40.0
    # as_of=07-18: 796 < 1175(07-17 반등)·924, 3일전(07-15=1559) 대비 −49% → 블립에도 True.
    fired2, _ = vitality_signal._signal_s1(imp_by, date(2026, 7, 18))
    assert fired2 is True


def test_s1_false_when_not_below_recent(db):
    imp_by = {d: _IMP[d] for d in _IMP}
    # as_of=07-17(반등일): 1175 > 924 → 직전일보다 높음 → S1 False.
    fired, _ = vitality_signal._signal_s1(imp_by, date(2026, 7, 17))
    assert fired is False


def test_s2_needs_three_day_rank_and_above_band(db):
    rank_by = {d: (None if d not in _RANK else _dec(_RANK[d])) for d in _IMP}
    # as_of=07-18: 4.7>3.9>3.5 ∧ 4.7>4.0 → S2 True.
    fired, detail = vitality_signal._signal_s2(rank_by, date(2026, 7, 18))
    assert fired is True and detail["avg_rank"] == 4.7
    # as_of=07-16: 3.5>3.2>3.0 악화지만 3.5 ≤ 4.0(밴드 안) → S2 False.
    fired2, _ = vitality_signal._signal_s2(rank_by, date(2026, 7, 16))
    assert fired2 is False


def test_s2_false_when_rank_history_missing(db):
    # 순위 창 일부 None → 판정 불가(False).
    rank_by = {date(2026, 7, 18): _dec(4.7), date(2026, 7, 17): _dec(3.9), date(2026, 7, 16): None}
    fired, detail = vitality_signal._signal_s2(rank_by, date(2026, 7, 18))
    assert fired is False and "데이터 부족" in detail["s2_reason"]


# ══════════════════════════ 03 백테스트(§검증 1) ══════════════════════════

def test_backtest_03_alert_fires_at_0718(db):
    """S1∧S2 결합 경보 = as_of=07-18(평가 now=2026-07-19)에 발화. S1은 07-16에도 서지만
    순위 수집이 07-17 개시라 S2(밴드 밖 4.0)는 07-18에야 성립(PLAN 허용 — 판정일 명시)."""
    _seed_03(db)
    res = vitality_signal.detect_spirals(db, now=datetime(2026, 7, 19, 8, 20))
    assert len(res["alerts"]) == 1
    a = res["alerts"][0]
    assert a["campaign_id"] == CAMPAIGN
    assert a["imp_traj"] == [1559, 924, 1175, 796]  # D0-3..D0 = 07-15..07-18
    assert round(a["avg_rank"], 1) == 4.7  # rank_sum 정수화로 4.6997… (노출가중)
    # 소생 대상: 검증 그룹 grp-03(충돌 게이트 통과·최근 하락) 포함.
    assert any(t["adgroup_id"] == GROUP for t in res["revive_targets"])


def test_backtest_03_no_alert_at_0717_s2_not_yet(db):
    """as_of=07-16(now=07-17): S1은 서지만 순위 3.5(밴드 안)라 S2 미성립 → 결합 경보 없음."""
    _seed_03(db)
    res = vitality_signal.detect_spirals(db, now=datetime(2026, 7, 17, 8, 20))
    assert res["alerts"] == []


# ══════════════════════════ GATE ① 충돌 방지 ══════════════════════════

def test_gate_excludes_paused_entity(db):
    # A축이 정지(status=off)시킨 그룹은 소생 대상 0(GATE ①).
    _seed_03(db)
    db.query(NaverEntity).filter(NaverEntity.entity_id == GROUP).update({"status": "off"})
    db.commit()
    res = vitality_signal.detect_spirals(db, now=datetime(2026, 7, 19, 8, 20))
    assert len(res["alerts"]) == 1  # 캠페인 경보는 그대로(그룹 선정만 걸러짐)
    assert res["revive_targets"] == []


def test_gate_excludes_stoploss_history(db):
    # change_log에 set_user_lock + [터미널정지] 이력 → 소생 금지(GATE ①②).
    _seed_03(db)
    db.add(NaverChangeLog(
        entity_type="adgroup", entity_id=GROUP, campaign_id=CAMPAIGN, action="set_user_lock",
        rationale="[터미널정지] 스톱로스 하드 정지", dry_run=False, after_value='{"userLock": true}',
        changed_at=datetime(2026, 7, 10, 9, 0),
    ))
    db.commit()
    res = vitality_signal.detect_spirals(db, now=datetime(2026, 7, 19, 8, 20))
    assert res["revive_targets"] == []


def test_gate_excludes_sufficient_sample_zero_conversion(db):
    # 충분 표본(clk≥10)인데 30일 전환 0 = A축이 자른 것 → 소생 금지(GATE ①③).
    _seed_03(db, conv_days=set())  # 전 기간 전환 0
    res = vitality_signal.detect_spirals(db, now=datetime(2026, 7, 19, 8, 20))
    assert res["revive_targets"] == []


def test_gate_absent_entity_fail_closed(db):
    # naver_entity 행 자체가 없으면(on 확인 불가) fail-closed 제외.
    _settings(db)
    for d, imp in _IMP.items():
        _daily(db, ad_date=d, imp=imp, rank=_RANK[d])
    # 엔티티 미시드
    res = vitality_signal.detect_spirals(db, now=datetime(2026, 7, 19, 8, 20))
    assert res["revive_targets"] == []


# ══════════════════════════ GATE ⑤ ours 스코프 ══════════════════════════

def test_gate_scope_non_auto_operate_ignored(db):
    # auto_operate=False 캠페인은 스파이럴이어도 감지 0(스코프 밖).
    _seed_03(db)
    db.query(NaverCampaignSettings).filter(
        NaverCampaignSettings.campaign_id == CAMPAIGN
    ).update({"auto_operate": False})
    db.commit()
    res = vitality_signal.detect_spirals(db, now=datetime(2026, 7, 19, 8, 20))
    assert res == {"alerts": [], "revive_targets": []}
