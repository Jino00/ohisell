# test_naver_probe_revert.py — probe_revert SA 단위/통합테스트 (D-NAO-58 CD3)
# 실 API 0 — naver_sa_writer(라이브 재조회·쓰기)는 mock, 되돌림 판정·standing 탐지·정산
# 5갈래 판정·킬스위치 가드·출혈 밸브를 검증. harness.execute는 대부분 실제로 태워
# _claim_executing 킬스위치·change_log 기록까지 관통(guardrail context/check만 스텁).
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import patch

from app.database import Base
from app.models import (
    NaverAdDaily,
    NaverCampaignSettings,
    NaverChangeLog,
    NaverHourlySnapshot,
    NaverProposal,
    OpsDiaryEntry,
)
from app.services.naver_ad import auto_operator, diary, naver_execution_harness as harness, naver_sa_writer, probe_revert

CAMPAIGN = "cmp-04"
TODAY = date(2026, 7, 20)
NOW = datetime(2026, 7, 20, 8, 55, 0)  # 정산 크론 시각(KST naive)


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


@pytest.fixture(autouse=True)
def _stub_correction_factor():
    """보정계수를 결정적으로 고정(cf=1.0) — roas_corrected 판정을 실주문 데이터 부재에
    좌우되지 않게(test_naver_auto_operator와 동일 관례)."""
    with patch.object(probe_revert.diagnosis, "correction_factor",
                      return_value={"factor": Decimal("1"), "source": "actual_revenue_ratio"}):
        yield


def _settings(db, *, auto_operate=True, optimizer="ours", target_roas_override=None):
    db.add(NaverCampaignSettings(
        campaign_id=CAMPAIGN, auto_operate=auto_operate, optimizer=optimizer,
        target_roas_override=target_roas_override,
    ))
    db.commit()


def _change_log(db, *, entity_type, entity_id, before_bid, probed_bid, changed_at,
                action="update_bid", dry_run=False, after_present=True):
    cl = NaverChangeLog(
        entity_type=entity_type, entity_id=entity_id, campaign_id=CAMPAIGN, action=action,
        before_value=json.dumps({"bidAmt": before_bid, "userLock": False}, ensure_ascii=False),
        after_value=(json.dumps({"bidAmt": probed_bid, "userLock": False}, ensure_ascii=False)
                     if after_present else None),
        dry_run=dry_run, changed_at=changed_at, executed_at=changed_at,
    )
    db.add(cl)
    db.commit()
    db.refresh(cl)
    return cl


def _probe(db, *, target_type="keyword", target_id="nkw-p", before_bid=1000, probed_bid=1150,
           probe_date=TODAY, changed_hour=8, adgroup_id=None, seed_diary=True,
           approval_source=None):
    """standing probe 1건 시드: change_log(update_bid) + proposal(probe_op·executed 연결) +
    (선택) 원 탐침 execute 일기 행. approval_source override로 non-probe 케이스도 구성."""
    changed_at = datetime.combine(probe_date, time(changed_hour, 0))
    cl = _change_log(db, entity_type=target_type, entity_id=target_id,
                     before_bid=before_bid, probed_bid=probed_bid, changed_at=changed_at)
    p = NaverProposal(
        proposal_type="bid_up", target_type=target_type, target_id=target_id,
        campaign_id=CAMPAIGN, adgroup_id=adgroup_id, rationale="[클릭탐침] 밴드 사각지대",
        expected_effect="탐침", status="approved",
        approval_source=(approval_source or auto_operator.APPROVAL_SOURCE_PROBE),
        target_bid=probed_bid, executed_change_log_id=cl.id,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    # created_at을 lookback 창 안으로 명시 고정(server_default UTC 벽시계 의존 제거)
    db.query(NaverProposal).filter(NaverProposal.id == p.id).update(
        {"created_at": datetime.combine(probe_date, time(0, 0))}
    )
    db.commit()
    if seed_diary:
        db.add(OpsDiaryEntry(
            event_type="execute", campaign_id=CAMPAIGN, actor=diary.ACTOR_PROBE,
            target_type=target_type, target_id=target_id, action="update_bid",
            source_ref=cl.id, created_at=changed_at,
        ))
        db.commit()
    return p, cl


def _ad_row(db, *, campaign_type="WEB_SITE", adgroup_id="grp-1", keyword_id="nkw-p", ad_date,
            imp=10, clk=5, cost=1000, conv_direct_cnt=0, conv_indirect_cnt=0,
            conv_direct_amt=0, conv_indirect_amt=0, cart_direct_cnt=0, cart_indirect_cnt=0):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=CAMPAIGN, campaign_type=campaign_type,
        adgroup_id=adgroup_id, keyword_id=keyword_id, imp=imp, clk=clk, cost=cost,
        conv_direct_cnt=conv_direct_cnt, conv_indirect_cnt=conv_indirect_cnt,
        conv_direct_amt=conv_direct_amt, conv_indirect_amt=conv_indirect_amt,
        cart_direct_cnt=cart_direct_cnt, cart_indirect_cnt=cart_indirect_cnt,
    ))
    db.commit()


def _hour(h, *, imp=10, clk=0, cost=100, avg_rank=3.0):
    return {"hour": h, "imp": imp, "clk": clk, "cost": cost, "avg_rank": avg_rank}


def _revert_writer():
    return naver_sa_writer.WriteResult(
        action="update_keyword_bid", before={"bidAmt": 1150, "userLock": False},
        response=None, after={"bidAmt": 1000, "userLock": False}, created_ids=[],
    )


# ══════════════════════════ _standing_probes ══════════════════════════

def test_standing_probes_detects_probe(db):
    _settings(db)
    _probe(db, probe_date=TODAY - timedelta(days=1))
    probes = probe_revert._standing_probes(db, NOW)
    assert len(probes) == 1
    assert probes[0]["before_bid"] == 1000
    assert probes[0]["probed_bid"] == 1150
    assert probes[0]["probe_date"] == TODAY - timedelta(days=1)
    assert probes[0]["target_id"] == "nkw-p"


def test_standing_probes_excludes_superseded(db):
    """탐침 뒤 다른 update_bid(레인 밴드 조정 등)가 최신이면 이미 덮여 되돌림 대상 아님."""
    _settings(db)
    _probe(db, probe_date=TODAY - timedelta(days=1), changed_hour=8)
    # 더 최신 update_bid(같은 엔티티) — probe change_log가 더 이상 latest 아님
    _change_log(db, entity_type="keyword", entity_id="nkw-p", before_bid=1150, probed_bid=1300,
                changed_at=datetime.combine(TODAY - timedelta(days=1), time(10, 0)))
    probes = probe_revert._standing_probes(db, NOW)
    assert probes == []


def test_standing_probes_excludes_non_probe_revert_op(db):
    """approval_source=revert_op(되돌림 자체)은 되돌림 대상이 아니다(probe_op만)."""
    _settings(db)
    _probe(db, probe_date=TODAY - timedelta(days=1),
           approval_source=auto_operator.APPROVAL_SOURCE_REVERT)
    probes = probe_revert._standing_probes(db, NOW)
    assert probes == []


def test_standing_probes_lookback_boundary_excludes_old(db):
    """7일 창 밖(오래된) 탐침은 제외."""
    _settings(db)
    _probe(db, probe_date=TODAY - timedelta(days=9))  # created_at도 9일 전 → 창 밖
    probes = probe_revert._standing_probes(db, NOW)
    assert probes == []


# ══════════════════════════ run_settlement 5갈래 판정 ══════════════════════════

def _run_settlement_with_revert(db, now=NOW):
    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid", return_value=_revert_writer()):
        return probe_revert.run_settlement(db, now=now)


def test_settlement_clk_zero_reverts(db):
    _settings(db, target_roas_override=2.0)
    yday = TODAY - timedelta(days=1)
    _probe(db, probe_date=yday)
    _ad_row(db, keyword_id="nkw-p", ad_date=yday, clk=0, cost=1000, conv_direct_amt=0)
    result = _run_settlement_with_revert(db)
    assert result["checked"] == 1
    assert result["reverted"] == 1
    # 되돌림 제안 생성 + 새 change_log
    rev = db.query(NaverProposal).filter(
        NaverProposal.approval_source == auto_operator.APPROVAL_SOURCE_REVERT
    ).all()
    assert len(rev) == 1
    assert rev[0].proposal_type == "bid_down"
    assert rev[0].target_bid == 1000  # before_bid로 원위치
    entry = db.query(OpsDiaryEntry).filter(OpsDiaryEntry.event_type == "execute",
                                           OpsDiaryEntry.actor == diary.ACTOR_PROBE,
                                           OpsDiaryEntry.source_ref.isnot(None)).first()
    outcome = json.loads(entry.outcome_json)
    assert outcome["probe"]["result"] == "reverted"
    assert outcome["probe"]["stage"] == "settle"


def test_settlement_roas_meets_target_keeps(db):
    _settings(db, target_roas_override=2.0)
    yday = TODAY - timedelta(days=1)
    _, cl = _probe(db, probe_date=yday)
    _ad_row(db, keyword_id="nkw-p", ad_date=yday, clk=5, cost=1000, conv_direct_amt=3000)  # roas 3.0
    result = probe_revert.run_settlement(db, now=NOW)
    assert result["kept"] == 1
    assert result["reverted"] == 0
    entry = db.get(OpsDiaryEntry, db.query(OpsDiaryEntry.id).filter(
        OpsDiaryEntry.source_ref == cl.id).scalar())
    assert json.loads(entry.outcome_json)["probe"]["result"] == "kept"
    # 되돌림 제안 없음
    assert db.query(NaverProposal).filter(
        NaverProposal.approval_source == auto_operator.APPROVAL_SOURCE_REVERT).count() == 0


def test_settlement_roas_below_and_no_conversion_reverts(db):
    _settings(db, target_roas_override=2.0)
    yday = TODAY - timedelta(days=1)
    _probe(db, probe_date=yday)
    # clk>0, roas 0.01<2, 전환 없음(adjusted<1)
    _ad_row(db, keyword_id="nkw-p", ad_date=yday, clk=5, cost=10000, conv_direct_amt=100,
            conv_direct_cnt=0)
    result = _run_settlement_with_revert(db)
    assert result["reverted"] == 1
    rev = db.query(NaverProposal).filter(
        NaverProposal.approval_source == auto_operator.APPROVAL_SOURCE_REVERT).one()
    assert "클릭 살았으나 전환 부족" in rev.rationale


def test_settlement_roas_below_but_adjusted_ge1_defers_when_young(db):
    _settings(db, target_roas_override=2.0)
    yday = TODAY - timedelta(days=1)  # age=1 < 3 → DEFER
    _, cl = _probe(db, probe_date=yday)
    # clk>0, roas 0.01<2, 즉시구매 1(adjusted=1.0>=1) → DEFER
    _ad_row(db, keyword_id="nkw-p", ad_date=yday, clk=5, cost=10000, conv_direct_amt=100,
            conv_direct_cnt=1)
    result = probe_revert.run_settlement(db, now=NOW)
    assert result["deferred"] == 1
    assert result["reverted"] == 0
    entry = db.query(OpsDiaryEntry).filter(OpsDiaryEntry.source_ref == cl.id).first()
    assert json.loads(entry.outcome_json)["probe"]["result"] == "deferred"


def test_settlement_age_ge3_default_reverts(db):
    _settings(db, target_roas_override=2.0)
    old = TODAY - timedelta(days=3)  # age=3 → 안전 default REVERT
    _probe(db, probe_date=old)
    _ad_row(db, keyword_id="nkw-p", ad_date=old, clk=5, cost=10000, conv_direct_amt=100,
            conv_direct_cnt=1)  # adjusted>=1 이지만 age>=3
    result = _run_settlement_with_revert(db)
    assert result["reverted"] == 1
    rev = db.query(NaverProposal).filter(
        NaverProposal.approval_source == auto_operator.APPROVAL_SOURCE_REVERT).one()
    assert "age≥3" in rev.rationale


def test_settlement_ignores_same_day_probe(db):
    """age<1(당일 탐침)은 정산 대상 아님(D+1부터)."""
    _settings(db, target_roas_override=2.0)
    _probe(db, probe_date=TODAY)
    result = probe_revert.run_settlement(db, now=NOW)
    assert result["checked"] == 0


# ══════════════════════════ _execute_revert 킬스위치 ══════════════════════════

def test_execute_revert_killswitch_off_skips(db):
    _settings(db, auto_operate=False)  # 킬스위치 OFF
    yday = TODAY - timedelta(days=1)
    _probe(db, probe_date=yday)
    _ad_row(db, keyword_id="nkw-p", ad_date=yday, clk=0, cost=1000)  # clk=0 → REVERT 시도
    with patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_write:
        result = probe_revert.run_settlement(db, now=NOW)
    mock_write.assert_not_called()
    assert result["reverted"] == 0
    # 제안 생성 자체 안 함(pre-check)
    assert db.query(NaverProposal).filter(
        NaverProposal.approval_source == auto_operator.APPROVAL_SOURCE_REVERT).count() == 0


def test_execute_revert_killswitch_on_executes(db):
    _settings(db, auto_operate=True)
    yday = TODAY - timedelta(days=1)
    _probe(db, probe_date=yday)
    _ad_row(db, keyword_id="nkw-p", ad_date=yday, clk=0, cost=1000)
    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid",
                      return_value=_revert_writer()) as mock_write:
        result = probe_revert.run_settlement(db, now=NOW)
    mock_write.assert_called_once_with("nkw-p", 1000)  # before_bid로 원위치
    assert result["reverted"] == 1


def test_harness_refuses_revert_proposal_when_kill_switch_off(db):
    """되돌림 우회 금지: revert_op 제안도 harness 쓰기 직전 킬스위치 최종 가드를 받는다."""
    _settings(db, auto_operate=False)
    p = NaverProposal(
        proposal_type="bid_down", target_type="keyword", target_id="nkw-rev",
        campaign_id=CAMPAIGN, rationale="[탐침되돌림·settle] x", status="approved",
        approval_source=auto_operator.APPROVAL_SOURCE_REVERT, target_bid=1000,
    )
    db.add(p)
    db.commit()
    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_write:
        with pytest.raises(harness.KillSwitchEngagedError):
            harness.execute(db, p.id, dry_run=False)
    mock_write.assert_not_called()
    db.refresh(p)
    assert p.status == "approved"  # 미실행 정직 상태
    assert db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).count() == 0


# ══════════════════════════ run_bleed_valve ══════════════════════════

def _bleed_settings_and_baseline(db):
    _settings(db, auto_operate=True, target_roas_override=2.0)
    window_from, window_to = auto_operator._settlement_window(TODAY)
    # 정착창 총 소진 7000 → 일평균 1000 → 시간당 41.7 → ×3 ≈ 125
    _ad_row(db, keyword_id="nkw-p", ad_date=window_to, clk=10, cost=7000)


def test_bleed_valve_cost_spike_and_no_conv_reverts(db):
    _bleed_settings_and_baseline(db)
    _probe(db, probe_date=TODAY, changed_hour=6)  # 당일 탐침
    now_midday = datetime(2026, 7, 20, 10, 20, 0)  # now.hour=10
    # 완료 버킷 [0..9] 소진 합 2000 → 시간당 200 > 125 → spike. 당일 즉시구매 0.
    curve = [_hour(h, imp=20, clk=1, cost=250) for h in range(8)]  # 8×250=2000
    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid",
                      return_value=_revert_writer()) as mock_write:
        result = probe_revert.run_bleed_valve(db, now=now_midday, fetch_intraday=lambda tid, d: curve)
    assert result["checked"] == 1
    assert result["reverted"] == 1
    mock_write.assert_called_once_with("nkw-p", 1000)
    assert db.query(NaverProposal).filter(
        NaverProposal.approval_source == auto_operator.APPROVAL_SOURCE_REVERT).count() == 1


def test_bleed_valve_no_spike_holds(db):
    _bleed_settings_and_baseline(db)
    _probe(db, probe_date=TODAY, changed_hour=6)
    now_midday = datetime(2026, 7, 20, 10, 20, 0)
    # 낮은 소진: 8×50=400 → 시간당 40 < 125 → spike 아님
    curve = [_hour(h, imp=20, clk=1, cost=50) for h in range(8)]
    with patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_write:
        result = probe_revert.run_bleed_valve(db, now=now_midday, fetch_intraday=lambda tid, d: curve)
    mock_write.assert_not_called()
    assert result["reverted"] == 0
    assert len(result["held"]) == 1


def test_bleed_valve_spike_but_conversion_present_holds(db):
    _bleed_settings_and_baseline(db)
    _probe(db, probe_date=TODAY, changed_hour=6)
    _ad_row(db, keyword_id="nkw-p", ad_date=TODAY, clk=3, cost=2000, conv_direct_cnt=1)  # 당일 구매 있음
    now_midday = datetime(2026, 7, 20, 10, 20, 0)
    curve = [_hour(h, imp=20, clk=1, cost=250) for h in range(8)]  # spike
    with patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_write:
        result = probe_revert.run_bleed_valve(db, now=now_midday, fetch_intraday=lambda tid, d: curve)
    mock_write.assert_not_called()
    assert result["reverted"] == 0


def test_bleed_valve_midnight_early_return(db):
    _bleed_settings_and_baseline(db)
    _probe(db, probe_date=TODAY, changed_hour=0)
    midnight = datetime(2026, 7, 20, 0, 20, 0)  # now.hour=0
    result = probe_revert.run_bleed_valve(db, now=midnight, fetch_intraday=lambda tid, d: [])
    assert result == {"checked": 0, "reverted": 0, "held": [], "skipped": 0, "errors": 0}


def test_bleed_valve_missing_baseline_skips(db):
    _settings(db, auto_operate=True)  # 정착창 소진 시드 없음 → baseline 0
    _probe(db, probe_date=TODAY, changed_hour=6)
    now_midday = datetime(2026, 7, 20, 10, 20, 0)
    curve = [_hour(h, imp=20, clk=1, cost=250) for h in range(8)]
    with patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_write:
        result = probe_revert.run_bleed_valve(db, now=now_midday, fetch_intraday=lambda tid, d: curve)
    mock_write.assert_not_called()
    assert result["skipped"] == 1
    assert result["reverted"] == 0


# ══════════════════════════ run_hourly_lane 통합(Stage 1 배선) ══════════════════════════

def test_hourly_lane_populates_bleed_and_reverts_same_day_probe(db):
    """run_hourly_lane 말미가 probe_revert.run_bleed_valve를 태워 result['bleed']를 채우고
    당일 출혈 탐침을 되돌린다(핫셋은 비워 레인 자체 제안은 0)."""
    _bleed_settings_and_baseline(db)
    _probe(db, probe_date=TODAY, changed_hour=6)  # 핫셋 엔티티는 시드 안 함 → 레인 제안 0
    # 당일 소진 스냅샷(서킷브레이커 신선도 통과) — bleed valve와 무관하나 레인 캠페인 루프용
    db.add(NaverHourlySnapshot(snapshot_at=NOW, ad_date=TODAY, snapshot_hour=23,
                               campaign_id=CAMPAIGN, campaign_type="", cost=0, clk=0, imp=0))
    db.commit()
    now_midday = datetime(2026, 7, 20, 10, 20, 0)
    curve = [_hour(h, imp=20, clk=1, cost=250) for h in range(8)]  # spike
    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid", return_value=_revert_writer()):
        result = auto_operator.run_hourly_lane(db, now=now_midday, fetch_intraday=lambda tid, d: curve)
    assert result["reviewed"] == 0  # 핫셋 없음(레인 제안 0)
    assert "bleed" in result
    assert result["bleed"]["reverted"] == 1
    assert db.query(NaverProposal).filter(
        NaverProposal.approval_source == auto_operator.APPROVAL_SOURCE_REVERT).count() == 1


# ══════════════════════════ 실 guardrail 관통(머니-세이프티, 스텁 금지) ══════════════════════════
# 이 두 테스트만 guardrail_gate.check/_build_guardrail_context를 스텁하지 않는다 — 되돌림이
# 실제 돈 경로에서 가드레일에 막히는지를 증명한다(mock은 get_keyword 라이브 재조회와
# update_keyword_bid PUT 뿐).

def test_real_guardrail_blocks_revert_that_would_raise_bid(db):
    """★머니-세이프티: 되돌림은 절대 입찰가를 올릴 수 없다. 외부 주체가 라이브 입찰을
    before_value(1000) 아래인 800으로 낮췄으면 bid_down(→1000)은 실은 인상이라, 실
    guardrail_gate._check_bid가 방향 불일치로 차단해야 한다(가드 미스텁 — get_keyword만 mock).
    쿨다운은 격리(탐침을 3h55m 전에 올려 cooldown이 아닌 방향체크가 원인임을 고정).

    검증: update_keyword_bid 미호출 · _execute_revert False · guard_failure 행(update_bid·
    failed·after_value None) 기록 · 그 실패행은 after_value None이라 supersede 안 하므로 probe
    여전히 standing(다음 판정에 재수확)."""
    _settings(db, auto_operate=True)  # optimizer='ours'
    _probe(db, probe_date=TODAY, changed_hour=5)  # NOW(08:55)-05:00=3h55m>2h → 쿨다운 배제
    probe = probe_revert._standing_probes(db, NOW)[0]

    with patch.object(harness.naver_sa_writer, "get_keyword",
                       return_value={"bidAmt": 800, "userLock": False}), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_write:
        ok = probe_revert._execute_revert(db, probe, NOW, reason="test", stage="settle")

    assert ok is False
    mock_write.assert_not_called()  # 실 guardrail 방향체크가 writer 전에 차단(PUT 안 나감)
    fails = db.query(NaverChangeLog).filter(
        NaverChangeLog.entity_id == "nkw-p", NaverChangeLog.action == "update_bid",
        NaverChangeLog.outcome == "failed",
    ).all()
    assert len(fails) == 1
    assert fails[0].after_value is None            # 쓰기 없음(광고 확실히 안 바뀜)
    assert "방향 불일치" in (fails[0].rationale or "")
    assert len(probe_revert._standing_probes(db, NOW)) == 1  # 여전히 standing


def test_real_guardrail_cooldown_blocks_same_day_bleed_revert(db):
    """★머니-세이프티: 당일 출혈 밸브가 발동해도 탐침을 방금(<2h) 올렸으면 실 cooldown이
    되돌림 bid_down을 차단 — 강제 재쓰기 대신 안전 보류(익일 Stage 2가 재판정). guardrail
    미스텁(get_keyword만 mock, 방향은 정상(1000<1150)이라 cooldown이 유일 차단 사유).

    검증: bleed 발동(비용×3 급등∧당일구매0)해 되돌림을 시도하나 update_keyword_bid 미호출 ·
    reverted 0(checked 1) · probe 여전히 standing · 원 탐침 diary outcome_json 미기입."""
    _settings(db, auto_operate=True)
    window_from, window_to = auto_operator._settlement_window(TODAY)
    _ad_row(db, keyword_id="nkw-p", ad_date=window_to, clk=10, cost=700)  # 일평균100→시간당4.17→×3≈12.5
    _, cl = _probe(db, probe_date=TODAY, changed_hour=9)  # 오늘 09:00 상향
    now_h10 = datetime(2026, 7, 20, 10, 20, 0)  # 10:20 — 09:00과 1h20m<2h 쿨다운 창
    curve = [_hour(h, imp=20, clk=1, cost=250) for h in range(8)]  # 완료 8h×250=2000 → 시간당200>12.5 spike

    with patch.object(harness.naver_sa_writer, "get_keyword",
                       return_value={"bidAmt": 1150, "userLock": False}), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_write:
        result = probe_revert.run_bleed_valve(db, now=now_h10, fetch_intraday=lambda tid, d: curve)

    assert result["checked"] == 1
    assert result["reverted"] == 0                 # 실 cooldown이 되돌림 차단
    mock_write.assert_not_called()                 # PUT 안 나감(강제 재쓰기 없음)
    assert len(probe_revert._standing_probes(db, now_h10)) == 1  # 여전히 standing
    entry = db.query(OpsDiaryEntry).filter(OpsDiaryEntry.source_ref == cl.id).first()
    assert entry.outcome_json is None              # 되돌림 안 됨 → outcome 미기입
