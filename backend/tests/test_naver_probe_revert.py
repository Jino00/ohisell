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
    NaverAdgroupHourlyToday,
    NaverAdgroupProduct,
    NaverCampaignSettings,
    NaverChangeLog,
    NaverHourlySnapshot,
    NaverProposal,
    Order,
    OpsDiaryEntry,
)
from app.services.naver_ad import (
    auto_operator,
    diary,
    load_window,
    naver_execution_harness as harness,
    naver_sa_writer,
    probe_revert,
)

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


def _hour(h, *, imp=10, clk=0, cost=100, avg_rank=3.0, conv_cnt=0):
    """fetch_entity_hh24의 반환 모양 그대로 — **conv_cnt를 반드시 포함한다.**

    ★2026-08-18 적대 리뷰에서 드러난 것: 이 헬퍼가 conv_cnt를 빼먹고 있었고, 밸브의 주신호를
      곡선으로 옮기자 모든 밸브 테스트가 «미관측»으로 떨어졌다. prod의 fetch_entity_hh24는
      항상 conv_cnt를 채운다(naver_sa_ad_fetcher._STATS_HH24_FIELDS에 ccnt 포함) —
      픽스처가 prod보다 **빈약**해도 결함을 못 잡는다([[test-fixture-must-match-prod-session]])."""
    return {"hour": h, "imp": imp, "clk": clk, "cost": cost, "avg_rank": avg_rank,
            "conv_cnt": conv_cnt}


def _curve(*, hours=8, cost=250, conv_cnt=0):
    """spike를 만드는 표준 곡선(8시간×cost). conv_cnt는 시간당 전환 건수."""
    return [_hour(h, imp=20, clk=1, cost=cost, conv_cnt=conv_cnt) for h in range(hours)]


ADGROUP = "grp-1"


def _hourly_today(db, *, adgroup_id=ADGROUP, ad_date=TODAY, hours=(0, 1, 2), conv_cnt=0,
                  clk=1, cost=250, campaign_type="WEB_SITE"):
    """당일 그룹 시간별 관측 시드 — 밸브의 **주신호**(naver_adgroup_hourly_today).

    ★prod에서 이 테이블이 진짜 당일 행을 갖는다(2026-08-18 라이브: 08-18분 1,164행/184그룹).
      반대로 naver_ad_daily는 D−1까지만이라 당일 행이 원리적으로 0이다 — 픽스처가 prod보다
      관대하면 결함을 못 잡는다([[test-fixture-must-match-prod-session]])."""
    for h in hours:
        db.add(NaverAdgroupHourlyToday(
            ad_date=ad_date, hour=h, adgroup_id=adgroup_id, campaign_id=CAMPAIGN,
            campaign_type=campaign_type, imp=20, clk=clk, cost=cost, conv_cnt=conv_cnt,
        ))
    db.commit()


def _mapped_product(db, *, adgroup_id=ADGROUP, product_id="pid-1"):
    db.add(NaverAdgroupProduct(
        adgroup_id=adgroup_id, campaign_id=CAMPAIGN, mall_product_id=product_id,
        product_name="테스트상품", synced_at=NOW,
    ))
    db.commit()


def _order(db, *, product_id="pid-1", day=TODAY, amount=30000, status="결제완료"):
    db.add(Order(
        channel_id=probe_revert.today_proxy_revenue.NAVER_CHANNEL_ID,
        order_number=f"ord-{product_id}-{day}-{amount}", platform_product_id=product_id,
        selling_price=Decimal(str(amount)), status=status,
        order_date=datetime.combine(day, time(9, 30)),
    ))
    db.commit()


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


def test_bleed_valve_certain_zero_reverts(db):
    """spike ∧ 광고전환 0 ∧ 상품매출 0(= 확정 0) → 즉시 되돌림."""
    _bleed_settings_and_baseline(db)
    _probe(db, probe_date=TODAY, changed_hour=6, adgroup_id=ADGROUP)  # 당일 탐침
    _mapped_product(db)  # 보강 신호 조회 가능 · 당일 주문 없음 → 매출 0
    now_midday = datetime(2026, 7, 20, 10, 20, 0)  # now.hour=10
    # 완료 버킷 [0..9] 소진 합 2000 → 시간당 200 > 125 → spike. 곡선 전환 0(관측됨).
    curve = _curve(conv_cnt=0)  # 8×250=2000
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


def test_bleed_valve_ignores_naver_ad_daily_today_rows(db):
    """★회귀 방어(D-NAO-193): 판정이 naver_ad_daily의 **당일** 행에 좌우되면 안 된다.

    prod에서 그 테이블은 D−1까지만 적재돼 당일 행이 원리적으로 0이므로(라이브 2026-08-18
    MAX(ad_date)=08-17), 그 행을 읽는 판정은 «항상 0»으로 굳는다. 테스트 DB는 아무 날짜나
    넣을 수 있어 **픽스처가 prod보다 관대**하다 — 그래서 여기서 일부러 당일 행에 즉시구매를
    넣고도 확정 0 판정이 유지되는지 못박는다(옛 코드였다면 hold로 갈라졌다)."""
    _bleed_settings_and_baseline(db)
    _probe(db, probe_date=TODAY, changed_hour=6, adgroup_id=ADGROUP)
    _mapped_product(db)
    _ad_row(db, keyword_id="nkw-p", ad_date=TODAY, clk=3, cost=2000, conv_direct_cnt=7)
    now_midday = datetime(2026, 7, 20, 10, 20, 0)
    curve = _curve(conv_cnt=0)
    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid",
                      return_value=_revert_writer()):
        result = probe_revert.run_bleed_valve(db, now=now_midday, fetch_intraday=lambda tid, d: curve)
    assert result["reverted"] == 1


def test_bleed_valve_curve_without_conv_field_is_unknown_not_zero(db):
    """★곡선에 conv_cnt **필드가 없으면** 0이 아니라 «미관측» — 이번에 고치는 사고의 부류 그 자체.

    폴백 테이블에도 당일 전환이 없으니 회수 근거가 없다 → skip(fail-open). 상품 매출이 0이어도
    «확정 0»으로 승격되면 안 된다(광고 귀속 신호가 없기 때문)."""
    _bleed_settings_and_baseline(db)
    _probe(db, probe_date=TODAY, changed_hour=6, adgroup_id=ADGROUP)
    _mapped_product(db)
    curve = [{"hour": h, "imp": 20, "clk": 1, "cost": 250, "avg_rank": 3.0} for h in range(8)]
    with patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_write:
        result = probe_revert.run_bleed_valve(
            db, now=datetime(2026, 7, 20, 10, 20, 0), fetch_intraday=lambda tid, d: curve)
    mock_write.assert_not_called()
    assert result["reverted"] == 0
    assert result["skipped"] == 1


def test_bleed_valve_empty_curve_is_unknown(db):
    """곡선 자체가 비면(창 안 실적 0) 미관측 — cost_spike도 성립 못 하지만 판정 어휘를 못박는다."""
    _bleed_settings_and_baseline(db)
    _probe(db, probe_date=TODAY, changed_hour=6, adgroup_id=ADGROUP)
    _mapped_product(db)
    with patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_write:
        result = probe_revert.run_bleed_valve(
            db, now=datetime(2026, 7, 20, 10, 20, 0), fetch_intraday=lambda tid, d: [])
    mock_write.assert_not_called()
    assert result["reverted"] == 0


def test_bleed_valve_conv_window_matches_cost_window(db):
    """★전환 창 = 비용 창 (적대 리뷰 변이 M2 방어 · P1-2가 고친 것).

    진행 중인 시간(now.hour) 버킷은 비용에서 제외되므로 전환에서도 제외해야 한다. 여기 전환을
    실어 두고 «전환 있음»으로 넘어가면 두 창이 어긋난 것이다."""
    _bleed_settings_and_baseline(db)
    _probe(db, probe_date=TODAY, changed_hour=6, adgroup_id=ADGROUP)
    _mapped_product(db)  # 당일 주문 없음
    now_h10 = datetime(2026, 7, 20, 10, 20, 0)  # now.hour=10 → 완결 창 [0..9]
    curve = _curve(conv_cnt=0) + [_hour(10, imp=5, clk=1, cost=100, conv_cnt=9)]  # 진행 중 버킷
    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid",
                      return_value=_revert_writer()) as mock_write:
        result = probe_revert.run_bleed_valve(db, now=now_h10, fetch_intraday=lambda tid, d: curve)
    # 진행 중 버킷의 전환 9건은 세지 않는다 → 완결 창 전환 0 ∧ 상품매출 0 = 확정 0 → 회수
    assert result["reverted"] == 1
    mock_write.assert_called_once_with("nkw-p", 1000)


def test_bleed_valve_table_fallback_only_blocks_never_triggers(db):
    """곡선에 전환 정보가 없을 때 폴백 테이블은 «전환 있음»만 받는다(hold 방향 전용).

    같은 테이블의 0은 스윕 지연(:57 vs 밸브 :20)일 수 있어 되돌림 근거로 쓰지 않는다 —
    위 테스트가 그 절반(0 → skip)이고, 이 테스트가 나머지 절반(>0 → hold)이다."""
    _bleed_settings_and_baseline(db)
    _probe(db, probe_date=TODAY, changed_hour=6, adgroup_id=ADGROUP)
    _hourly_today(db, hours=(0,), conv_cnt=4)  # 테이블엔 당일 전환이 있다
    curve = [{"hour": h, "imp": 20, "clk": 1, "cost": 250, "avg_rank": 3.0} for h in range(8)]
    with patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_write:
        result = probe_revert.run_bleed_valve(
            db, now=datetime(2026, 7, 20, 10, 20, 0), fetch_intraday=lambda tid, d: curve)
    mock_write.assert_not_called()
    assert result["reverted"] == 0 and result["skipped"] == 0
    assert "전환 있음" in result["held"][0]["reason"]


def test_bleed_valve_no_adgroup_on_keyword_probe_is_tentative(db):
    """keyword 탐침에 adgroup_id가 없으면 상품 매핑을 찾을 키가 없다 → 확정 0 승격 금지."""
    _bleed_settings_and_baseline(db)
    _probe(db, probe_date=TODAY, changed_hour=6, adgroup_id=None)
    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid",
                      return_value=_revert_writer()) as mock_write:
        result = probe_revert.run_bleed_valve(
            db, now=datetime(2026, 7, 20, 10, 20, 0),
            fetch_intraday=lambda tid, d: _curve(conv_cnt=0))
    mock_write.assert_not_called()
    assert result["reverted"] == 0
    assert "잠정 0" in result["held"][0]["reason"]


def test_bleed_valve_multi_product_union_blocks_certain_zero(db):
    """★상한 프록시는 그 그룹이 광고하는 상품 **전부**의 합이다(적대 리뷰 변이 M8 방어).

    상품 하나만 봤다면 «매출 0 → 확정 0 → 즉시 회수»로 갈라졌을 상황에서, 합집합을 보면
    다른 상품이 팔렸으므로 잠정 0에 머물러야 한다(거짓 정지 방지)."""
    _bleed_settings_and_baseline(db)
    _probe(db, probe_date=TODAY, changed_hour=6, adgroup_id=ADGROUP)
    _mapped_product(db, product_id="pid-1")   # 당일 주문 없음
    _mapped_product(db, product_id="pid-2")   # 당일 주문 있음
    _order(db, product_id="pid-2", amount=21000)
    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid",
                      return_value=_revert_writer()) as mock_write:
        result = probe_revert.run_bleed_valve(
            db, now=datetime(2026, 7, 20, 10, 20, 0),
            fetch_intraday=lambda tid, d: _curve(conv_cnt=0))
    mock_write.assert_not_called()  # 확정 0이었다면 여기서 PUT이 나갔다
    assert result["reverted"] == 0
    assert "잠정 0" in result["held"][0]["reason"]


def test_bleed_watch_from_another_day_does_not_escalate(db):
    """★어제의 잠정 0 기록으로 오늘 첫 관측에 발화하면 안 된다(적대 리뷰 변이 M6 방어)."""
    _bleed_settings_and_baseline(db)
    _, cl = _probe(db, probe_date=TODAY, changed_hour=6, adgroup_id=ADGROUP)
    _mapped_product(db)
    _order(db, amount=30000)  # 상품은 팔림 → 잠정 0
    entry = db.query(OpsDiaryEntry).filter(OpsDiaryEntry.source_ref == cl.id).first()
    entry.outcome_json = json.dumps(
        {"bleed_watch": {"date": (TODAY - timedelta(days=1)).isoformat(), "hours": [3, 4]}})
    db.commit()
    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid",
                      return_value=_revert_writer()) as mock_write:
        result = probe_revert.run_bleed_valve(
            db, now=datetime(2026, 7, 20, 10, 20, 0),
            fetch_intraday=lambda tid, d: _curve(conv_cnt=0))
    mock_write.assert_not_called()
    assert result["reverted"] == 0
    watch = json.loads(
        db.query(OpsDiaryEntry).filter(OpsDiaryEntry.source_ref == cl.id).first().outcome_json
    )["bleed_watch"]
    assert watch == {"date": TODAY.isoformat(), "hours": [10]}  # 어제 기록은 승계되지 않는다


def test_bleed_valve_tentative_zero_holds_then_reverts_next_hour(db):
    """광고전환 0인데 상품은 팔림 = 잠정 0 → 첫 시각 hold, 더 이른 시각 기록이 생긴 뒤 발화."""
    _bleed_settings_and_baseline(db)
    _probe(db, probe_date=TODAY, changed_hour=6, adgroup_id=ADGROUP)
    _mapped_product(db)
    _order(db, amount=30000)  # 당일 상품 매출 > 0 → 거짓 0 가능
    curve = _curve(conv_cnt=0)

    with patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_write:
        first = probe_revert.run_bleed_valve(
            db, now=datetime(2026, 7, 20, 10, 20, 0), fetch_intraday=lambda tid, d: curve)
    mock_write.assert_not_called()
    assert first["reverted"] == 0
    assert "잠정 0" in first["held"][0]["reason"]

    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid",
                      return_value=_revert_writer()) as mock_write2:
        second = probe_revert.run_bleed_valve(
            db, now=datetime(2026, 7, 20, 11, 20, 0), fetch_intraday=lambda tid, d: curve)
    assert second["reverted"] == 1
    mock_write2.assert_called_once_with("nkw-p", 1000)


def test_bleed_valve_tentative_zero_same_hour_does_not_escalate(db):
    """같은 시각에 두 번 돌아도 발화하지 않는다(«더 이른 시각» 조건 — 재확인의 의미 보존)."""
    _bleed_settings_and_baseline(db)
    _probe(db, probe_date=TODAY, changed_hour=6, adgroup_id=ADGROUP)
    _mapped_product(db)
    _order(db, amount=30000)
    curve = _curve(conv_cnt=0)
    now_midday = datetime(2026, 7, 20, 10, 20, 0)
    # ★가드레일을 열어 둔다 — 안 그러면 «되돌림이 막혀서» 0인지 «판정이 hold라서» 0인지
    #   구별이 안 된다(적대 리뷰의 변이 M10이 정확히 이 구멍으로 살아남았다).
    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid",
                      return_value=_revert_writer()) as mock_write:
        probe_revert.run_bleed_valve(db, now=now_midday, fetch_intraday=lambda tid, d: curve)
        again = probe_revert.run_bleed_valve(db, now=now_midday, fetch_intraday=lambda tid, d: curve)
    mock_write.assert_not_called()
    assert again["reverted"] == 0


def test_bleed_valve_no_product_mapping_is_tentative_not_certain(db):
    """매핑이 없으면 상한 프록시로 교차 확인을 못 하므로 확정 0으로 승격하지 않는다."""
    _bleed_settings_and_baseline(db)
    _probe(db, probe_date=TODAY, changed_hour=6, adgroup_id=ADGROUP)
    curve = _curve(conv_cnt=0)  # 매핑 시드 없음
    with patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_write:
        result = probe_revert.run_bleed_valve(
            db, now=datetime(2026, 7, 20, 10, 20, 0), fetch_intraday=lambda tid, d: curve)
    mock_write.assert_not_called()
    assert result["reverted"] == 0
    assert "매핑" in result["held"][0]["reason"]


def test_bleed_valve_logs_legacy_vs_new_path(db, caplog):
    """★라이브 합격 증거의 형식 — 구경로/신경로 병기 로그가 실제로 나온다(ref 72 §2-①)."""
    import logging as _logging
    _bleed_settings_and_baseline(db)
    _probe(db, probe_date=TODAY, changed_hour=6, adgroup_id=ADGROUP)
    curve = [_hour(0, imp=20, clk=1, cost=2000, conv_cnt=3)]  # 신경로는 전환을 본다
    with caplog.at_level(_logging.INFO, logger="app.services.naver_ad.probe_revert"):
        probe_revert.run_bleed_valve(
            db, now=datetime(2026, 7, 20, 10, 20, 0), fetch_intraday=lambda tid, d: curve)
    line = next(r.getMessage() for r in caplog.records if "[출혈밸브·경로대조]" in r.getMessage())
    assert "구경로 naver_ad_daily.conv_direct=0" in line   # 당일 행 없음 → 구조적 0
    assert "적재창 밖" in line                              # load_window가 창 위반을 지목
    assert "신경로 판정=positive 신호=hh24_curve ad_conv=3" in line


def test_bleed_valve_no_spike_holds(db):
    _bleed_settings_and_baseline(db)
    _probe(db, probe_date=TODAY, changed_hour=6)
    now_midday = datetime(2026, 7, 20, 10, 20, 0)
    # 낮은 소진: 8×50=400 → 시간당 40 < 125 → spike 아님
    curve = _curve(cost=50)
    with patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_write:
        result = probe_revert.run_bleed_valve(db, now=now_midday, fetch_intraday=lambda tid, d: curve)
    mock_write.assert_not_called()
    assert result["reverted"] == 0
    assert len(result["held"]) == 1


def test_bleed_valve_spike_but_conversion_present_holds(db):
    """당일 광고 전환이 실재하면(주신호 > 0) 출혈 아님 — 상품 매출을 볼 것도 없다."""
    _bleed_settings_and_baseline(db)
    _probe(db, probe_date=TODAY, changed_hour=6, adgroup_id=ADGROUP)
    now_midday = datetime(2026, 7, 20, 10, 20, 0)
    curve = _curve(conv_cnt=1)  # spike ∧ 당일 광고 전환 있음(주신호 = 곡선)
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
    curve = _curve(conv_cnt=0)
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
    _probe(db, probe_date=TODAY, changed_hour=6, adgroup_id=ADGROUP)  # 핫셋 엔티티 시드 안 함 → 레인 제안 0
    _mapped_product(db)  # 보강: 당일 주문 없음 → 매출 0 → 곡선 전환 0과 합쳐 확정 0
    # 당일 소진 스냅샷(서킷브레이커 신선도 통과) — bleed valve와 무관하나 레인 캠페인 루프용
    db.add(NaverHourlySnapshot(snapshot_at=NOW, ad_date=TODAY, snapshot_hour=23,
                               campaign_id=CAMPAIGN, campaign_type="", cost=0, clk=0, imp=0))
    db.commit()
    now_midday = datetime(2026, 7, 20, 10, 20, 0)
    curve = _curve(conv_cnt=0)  # spike ∧ 곡선 전환 0
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
    _, cl = _probe(db, probe_date=TODAY, changed_hour=9, adgroup_id=ADGROUP)  # 오늘 09:00 상향
    _mapped_product(db)  # 당일 주문 없음 → 상품 매출 0 → 곡선 전환 0과 합쳐 «확정 0»(되돌림 시도)
    now_h10 = datetime(2026, 7, 20, 10, 20, 0)  # 10:20 — 09:00과 1h20m<2h 쿨다운 창
    curve = _curve(conv_cnt=0)  # 완료 8h×250=2000 → 시간당200>12.5 spike

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
