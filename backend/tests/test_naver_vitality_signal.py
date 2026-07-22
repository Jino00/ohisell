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


def _campaign_entity(db, *, campaign_id=CAMPAIGN, status="on", campaign_type="SHOPPING"):
    """C2(codex 1R) 캠페인 레벨 게이트가 요구하는 campaign NaverEntity(status='on')."""
    db.add(NaverEntity(entity_type="campaign", entity_id=campaign_id, parent_id="",
                       campaign_id=campaign_id, status=status, campaign_type=campaign_type))
    db.commit()


def _seed_03(db, *, conv_days=None):
    _settings(db)
    _campaign_entity(db)  # C2: 캠페인 부모 체인 'on'
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


def test_gate_excludes_policy_pause_lock(db):
    # GATE P1-1: 실 policy_pause 사유문(proposal_writer._terminal_pause의
    # '[스톱로스정지 — 캠페인 정책]' 접두)으로 시드된 성공 set_user_lock(after_value userLock:true)
    # → 구조화 필드 판정으로 배제(사유 불문). rationale LIKE 매칭이 아닌 최종 잠금 상태로 판단.
    _seed_03(db)
    db.add(NaverChangeLog(
        entity_type="adgroup", entity_id=GROUP, campaign_id=CAMPAIGN, action="set_user_lock",
        rationale=(
            "[스톱로스정지 — 캠페인 정책] 스톱로스 발동 → 고삐 대신 정지"
            "(loss_policy=stoploss_pause, D-NAO-65 UI1) clk=30."
        ),
        dry_run=False, after_value='{"userLock": true}',
        changed_at=datetime(2026, 7, 10, 9, 0), executed_at=datetime(2026, 7, 10, 9, 0),
    ))
    db.commit()
    res = vitality_signal.detect_spirals(db, now=datetime(2026, 7, 19, 8, 20))
    assert res["revive_targets"] == []


def test_gate_excludes_stale_status_on_with_recent_lock(db):
    # GATE P1-1 sync-lag 창 재현: entity.status='on'(하루 1회 07:35 sync라 A축 08:50 정지 후
    # ~23시간 stale) ∧ 최신 change_log 잠금(userLock:true) → change_log 권위 소스가 stale
    # entity보다 우선(보수적 OR)해 배제. 캠페인 경보 자체는 유지(그룹 선정만 걸러짐).
    _seed_03(db)  # entity status='on'(stale)
    db.add(NaverChangeLog(
        entity_type="adgroup", entity_id=GROUP, campaign_id=CAMPAIGN, action="set_user_lock",
        rationale="[shopping_pause_candidates] 바닥 창 무전환 출혈 지속 → 정지(D-NAO-65 지속 밸브)",
        dry_run=False, after_value='{"userLock": true}',
        changed_at=datetime(2026, 7, 18, 9, 0), executed_at=datetime(2026, 7, 18, 9, 0),
    ))
    db.commit()
    res = vitality_signal.detect_spirals(db, now=datetime(2026, 7, 19, 8, 20))
    assert len(res["alerts"]) == 1
    assert res["revive_targets"] == []


def test_gate_allows_after_resume_when_status_on(db):
    # GATE P1-1: 정지(pause userLock:true) 후 최신 이벤트가 재개(resume userLock:false) ∧
    # entity status='on'이면 둘 다 정지 아님 → 통과(소생 허용). 구조화 판정이 최신 잠금 이벤트를
    # 본다(오래된 정지 이벤트가 아니라).
    _seed_03(db)
    db.add(NaverChangeLog(
        entity_type="adgroup", entity_id=GROUP, campaign_id=CAMPAIGN, action="set_user_lock",
        rationale="[스톱로스정지 — 캠페인 정책] 정지", dry_run=False, after_value='{"userLock": true}',
        changed_at=datetime(2026, 7, 15, 9, 0), executed_at=datetime(2026, 7, 15, 9, 0),
    ))
    db.add(NaverChangeLog(
        entity_type="adgroup", entity_id=GROUP, campaign_id=CAMPAIGN, action="set_user_lock",
        rationale="[재개] 소생 재개", dry_run=False, after_value='{"userLock": false}',
        changed_at=datetime(2026, 7, 18, 9, 0), executed_at=datetime(2026, 7, 18, 9, 0),
    ))
    db.commit()
    res = vitality_signal.detect_spirals(db, now=datetime(2026, 7, 19, 8, 20))
    assert any(t["adgroup_id"] == GROUP for t in res["revive_targets"])


def test_gate_ignores_failed_lock_attempt(db):
    # 실패·가드거부 set_user_lock 행(dry_run=False ∧ after_value=None)은 네이버 상태를 안
    # 바꿨으므로 잠금 이벤트로 세지 않는다(after_value 존재만 후보). status='on'이라 통과.
    _seed_03(db)
    db.add(NaverChangeLog(
        entity_type="adgroup", entity_id=GROUP, campaign_id=CAMPAIGN, action="set_user_lock",
        rationale="[스톱로스정지 — 캠페인 정책] 정지 [실쓰기 실패] WriteError",
        dry_run=False, after_value=None,
        changed_at=datetime(2026, 7, 18, 9, 0), executed_at=datetime(2026, 7, 18, 9, 0),
    ))
    db.commit()
    res = vitality_signal.detect_spirals(db, now=datetime(2026, 7, 19, 8, 20))
    assert any(t["adgroup_id"] == GROUP for t in res["revive_targets"])


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
    assert res["alerts"] == [] and res["revive_targets"] == []


# ══════════════════════════ C2 캠페인 레벨 부모 체인 게이트 ══════════════════════════

def test_c2_excludes_when_campaign_entity_off(db):
    # 부모 캠페인 status=off → 소생 대상 0(경보는 유지, hot-set 관례 동형 fail-closed).
    _seed_03(db)
    db.query(NaverEntity).filter(NaverEntity.entity_type == "campaign").update({"status": "off"})
    db.commit()
    res = vitality_signal.detect_spirals(db, now=datetime(2026, 7, 19, 8, 20))
    assert len(res["alerts"]) == 1  # 경보 유지
    assert res["revive_targets"] == []
    assert "소생 보류" in res["alerts"][0].get("revive_note", "")


def test_c2_excludes_when_campaign_entity_absent(db):
    # 캠페인 엔티티 행 부재 → on 확인 불가 → fail-closed 배제.
    _settings(db)
    _entity(db)  # adgroup만 시드, campaign 엔티티 미시드
    for d, imp in _IMP.items():
        _daily(db, ad_date=d, imp=imp, rank=_RANK[d])
    res = vitality_signal.detect_spirals(db, now=datetime(2026, 7, 19, 8, 20))
    assert len(res["alerts"]) == 1 and res["revive_targets"] == []


def test_c2_excludes_when_campaign_locked(db):
    # 캠페인 대상 최신 성공 set_user_lock(userLock:true) → 배제(캠페인 잠금 권위 소스).
    _seed_03(db)
    db.add(NaverChangeLog(
        entity_type="campaign", entity_id=CAMPAIGN, campaign_id=CAMPAIGN, action="set_user_lock",
        rationale="[캠페인 정책 정지]", dry_run=False, after_value='{"userLock": true}',
        changed_at=datetime(2026, 7, 18, 9, 0), executed_at=datetime(2026, 7, 18, 9, 0),
    ))
    db.commit()
    res = vitality_signal.detect_spirals(db, now=datetime(2026, 7, 19, 8, 20))
    assert res["revive_targets"] == []


# ══════════════════════════ C3 부분적재 오발 차단 ══════════════════════════

def test_c3_partial_load_holds_all_fires(db):
    # D-1 행수가 직전 3일 평균 대비 −50%↓면 부분적재 의심 → 경보 유지·revive_targets 전면 비움.
    # baseline 행수는 별도 캠페인(cmp-other, non-auto)으로 올려 CAMPAIGN의 s1/s2 신호는 불변.
    _seed_03(db)
    for d in (date(2026, 7, 15), date(2026, 7, 16), date(2026, 7, 17)):
        for extra in ("grp-a", "grp-b", "grp-c"):
            _daily(db, ad_date=d, imp=100, rank=3.0, campaign_id="cmp-other", adgroup_id=extra)
    res = vitality_signal.detect_spirals(db, now=datetime(2026, 7, 19, 8, 20))
    assert len(res["alerts"]) == 1  # 스파이럴 경보 자체는 유지
    assert res["revive_targets"] == []
    assert res["revive_hold_reason"] is not None and "부분적재" in res["revive_hold_reason"]


def test_c3_normal_load_does_not_hold(db):
    # 균형 잡힌 행수(D-1도 정상)면 부분적재 아님 → revive_hold_reason None.
    _seed_03(db)
    res = vitality_signal.detect_spirals(db, now=datetime(2026, 7, 19, 8, 20))
    assert res["revive_hold_reason"] is None
    assert any(t["adgroup_id"] == GROUP for t in res["revive_targets"])


# ══════════════════════════ C6 쇼핑/브랜드 한정(WEB_SITE 제외) ══════════════════════════

def test_c6_excludes_web_site_campaign(db):
    # WEB_SITE(파워링크) 캠페인은 소생 대상 0(경보는 유지 — grain 계약 충돌 방지).
    _seed_03(db)
    db.query(NaverAdDaily).update({NaverAdDaily.campaign_type: "WEB_SITE"})
    db.query(NaverEntity).filter(NaverEntity.entity_type == "campaign").update(
        {"campaign_type": "WEB_SITE"}
    )
    db.commit()
    res = vitality_signal.detect_spirals(db, now=datetime(2026, 7, 19, 8, 20))
    assert len(res["alerts"]) == 1  # 경보 유지
    assert res["revive_targets"] == []
    assert "grain" in res["alerts"][0].get("revive_note", "")


def test_c6_allows_brand_search_campaign(db):
    # BRAND_SEARCH도 adgroup grain 발사 허용 → 소생 대상 포함.
    _seed_03(db)
    db.query(NaverAdDaily).update({NaverAdDaily.campaign_type: "BRAND_SEARCH"})
    db.commit()
    res = vitality_signal.detect_spirals(db, now=datetime(2026, 7, 19, 8, 20))
    assert any(t["adgroup_id"] == GROUP for t in res["revive_targets"])
