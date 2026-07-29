# CS 스프린트 단위 테스트 — SA1 상한/CVR 사다리, SA2 floor 감지, SA3 판정, Harness 콜드·첫1회.
#   실 API 호출 없음(SA2는 _estimate_post를 monkeypatch). 인메모리 sqlite.
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import (
    Base, NaverAdDaily, NaverAdgroupProduct, NaverBidEstimateDaily, NaverCampaignSettings,
    NaverChangeLog, NaverEntity, NaverProductBep,
)
from app.services.naver_ad import (
    bid_ceiling_calculator as sa1, cold_start_bid_decider as sa3, cold_start_bid_lane as lane,
    market_bid_probe as sa2,
)

TODAY = date(2026, 7, 27)
CID = "cmp-a001-02-000000008514959"
GID = "grp-1"
AD = "nad-a001-02-000000455468669"
PID = "13684462601"


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


def _seed(db, *, bep_roas="1.5921", cm="9986.76", ad_bid=300, use_group=False, clk=100, amt=222400):
    db.add(NaverCampaignSettings(campaign_id=CID, optimizer="ours", auto_operate=True))
    db.add(NaverAdgroupProduct(
        adgroup_id=GID, campaign_id=CID, mall_product_id=PID, product_name="테스트 필름",
        ad_id=AD, ad_bid_amt=ad_bid, use_group_bid_amt=use_group, ad_user_lock=False,
    ))
    db.add(NaverEntity(entity_type="adgroup", entity_id=GID, campaign_id=CID, bid_amt=500))
    if bep_roas is not None:
        db.add(NaverProductBep(
            channel_id=6, channel_product_id=PID, product_name="테스트 필름",
            selling_price=Decimal("15900"), cost_price=Decimal("3000"),
            commission_rate=Decimal("0.0780"), logistics_cost=Decimal("1900"),
            contribution_margin=Decimal(cm), bep_roas=Decimal(bep_roas), has_cost=True,
        ))
    if clk:
        db.add(NaverAdDaily(
            ad_date=TODAY - timedelta(days=3), campaign_id=CID, adgroup_id=GID, keyword_id="",
            imp=1000, clk=clk, cost=10000, conv_direct_amt=amt, conv_indirect_amt=0,
        ))
    db.commit()


# ══════════════════════════════════════════════════════════════════
# SA1 — 이익 상한 + RPC(CVR) 사다리
# ══════════════════════════════════════════════════════════════════
def test_sa1_ceiling_equals_cvr_times_contribution(db):
    """상한 = RPC/BEP 가 CVR×공헌이익과 수치적으로 같은지(스프린트 지시의 정합 검증)."""
    _seed(db, clk=100, amt=222400)  # RPC = 2224원
    out = sa1.compute_ceiling(db, AD, GID, CID, TODAY)
    # RPC 2224 / BEP 1.5921 = 1396.9 → 10원 내림 = 1390
    assert out["ceiling_cpc"] == 1390
    # ★D-NAO-119(수축) 의도된 변경: 이 시드는 conv_direct_cnt를 안 넣어(0) prior CVR이 0이
    # 되고, _prior_clicks가 _DEFAULT_PRIOR_CLICKS(150)로 폴백한다. 자기 클릭(100) < 150이라
    # own_dominant가 아니라 prior(campaign 층)가 지배로 표기된다 — 값(1390)은 그대로다.
    assert out["rpc_source"] == "campaign"
    # 동치 확인: CVR × 공헌이익. CVR = RPC / 판매가 = 2224/15900.
    cvr = Decimal("2224") / Decimal("15900")
    assert abs(cvr * Decimal("9986.76") - Decimal("1396.9")) < Decimal("1.0")


def test_sa1_rpc_ladder_falls_back_to_campaign_then_account(db):
    """그룹 표본 미달이면 캠페인, 캠페인도 미달이면 계정으로 내려간다 + 라벨/신뢰도 전달."""
    _seed(db, clk=0)
    # 그룹은 클릭 5(<10 미달), 같은 캠페인의 다른 그룹이 50클릭(≥30 충족)
    db.add(NaverAdDaily(ad_date=TODAY - timedelta(days=2), campaign_id=CID, adgroup_id=GID,
                        keyword_id="", imp=10, clk=5, cost=100, conv_direct_amt=1000,
                        conv_indirect_amt=0))
    db.add(NaverAdDaily(ad_date=TODAY - timedelta(days=2), campaign_id=CID, adgroup_id="grp-2",
                        keyword_id="", imp=100, clk=50, cost=1000, conv_direct_amt=100000,
                        conv_indirect_amt=0))
    db.commit()
    out = sa1.compute_ceiling(db, AD, GID, CID, TODAY)
    assert out["rpc_source"] == "campaign"
    assert out["confident"] is True
    assert out["sample_clk"] == 55


def test_sa1_account_fallback_is_not_confident(db):
    """계정 층 폴백은 confident=False — 표본 빈약이 호출부까지 전달돼야 한다."""
    _seed(db, clk=0)
    db.add(NaverAdDaily(ad_date=TODAY - timedelta(days=2), campaign_id="cmp-other",
                        adgroup_id="grp-9", keyword_id="", imp=1000, clk=150, cost=1000,
                        conv_direct_amt=300000, conv_indirect_amt=0))
    db.commit()
    out = sa1.compute_ceiling(db, AD, GID, CID, TODAY)
    assert out["rpc_source"] == "account"
    assert out["confident"] is False


def test_sa1_no_bep_yields_no_ceiling(db):
    _seed(db, bep_roas=None)
    out = sa1.compute_ceiling(db, AD, GID, CID, TODAY)
    assert out["ceiling_cpc"] == 0
    assert "BEP" in out["reason"]


def test_sa1_no_rpc_sample_yields_no_ceiling(db):
    _seed(db, clk=0)
    out = sa1.compute_ceiling(db, AD, GID, CID, TODAY)
    assert out["ceiling_cpc"] == 0
    assert out["rpc_source"] == "none"


# ══════════════════════════════════════════════════════════════════
# SA2 — floor 감지 / 사다리 축적
# ══════════════════════════════════════════════════════════════════
def test_sa2_detect_floor_on_min_bid():
    is_floor, reason = sa2.detect_floor({1: 50, 2: 50, 3: 50, 4: 50})
    assert is_floor is True
    assert "최소입찰가" in reason


def test_sa2_detect_floor_on_flat_ladder():
    """순위별 차등이 없으면 경쟁 정보 부재 = floor(진짜 시장가로 쓰면 안 됨)."""
    is_floor, reason = sa2.detect_floor({1: 800, 2: 800, 3: 800, 4: 800})
    assert is_floor is True
    assert "동일값" in reason


def test_sa2_real_ladder_is_not_floor():
    """라이브 실측값(2026-07-27 nad-…455468669 MOBILE)은 floor가 아니다."""
    is_floor, _ = sa2.detect_floor({1: 3870, 2: 3800, 3: 3440, 4: 3010})
    assert is_floor is False


def test_sa2_empty_ladder_is_floor():
    assert sa2.detect_floor({})[0] is True


def test_sa2_valid_positions_exclude_5():
    """라이브 실측: position 5 이상은 400. 사다리는 1~4가 전부."""
    assert sa2.NPLA_VALID_POSITIONS == (1, 2, 3, 4)
    assert 5 not in sa2.NPLA_VALID_POSITIONS
    assert sa2.NPLA_MAX_ITEMS == 200  # 실측 200 OK / 201 fail


def test_sa2_collect_daily_persists_and_is_idempotent(db, monkeypatch):
    """수집이 사다리+최소노출을 적재하고, 같은 날 재실행 시 중복 없이 교체된다."""
    class _Resp:
        status_code = 200

        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    def fake_post(path, body):
        if "average-position-bid" in path:
            pos = body["items"][0]["position"]
            bid = {1: 3870, 2: 3800, 3: 3440, 4: 3010}[pos]
            return _Resp({"estimate": [{"nccAdId": a["key"], "bid": bid, "position": pos}
                                       for a in body["items"]]})
        return _Resp({"estimate": [{"nccAdId": a, "bid": 4940} for a in body["items"]]})

    monkeypatch.setattr(sa2, "_estimate_post", fake_post)
    monkeypatch.setattr(sa2, "_SLEEP_BETWEEN_CALLS", 0)

    rows = [(AD, GID, CID)]
    r1 = sa2.collect_daily(db, rows, TODAY, devices=("MOBILE",))
    assert r1["rows"] == 5  # 순위 4개 + 최소노출 1개
    assert r1["floor_ads"] == 0
    r2 = sa2.collect_daily(db, rows, TODAY, devices=("MOBILE",))
    assert r2["rows"] == 5
    assert db.query(NaverBidEstimateDaily).count() == 5  # 교체됨(중복 누적 아님)

    loaded = sa2.load_today_ladder(db, AD, "MOBILE", TODAY)
    assert loaded["ladder"] == {1: 3870, 2: 3800, 3: 3440, 4: 3010}
    assert loaded["exposure_min"] == 4940
    assert loaded["is_floor"] is False


def test_sa2_partial_failure_does_not_abort(db, monkeypatch):
    """한 순위 조회가 실패해도 나머지는 수집된다(전체 중단 금지)."""
    class _Resp:
        def __init__(self, code, payload=None):
            self.status_code = code
            self._p = payload or {}
            self.text = "boom"

        def json(self):
            return self._p

    def fake_post(path, body):
        if "average-position-bid" in path:
            pos = body["items"][0]["position"]
            if pos == 2:
                return _Resp(500)
            return _Resp(200, {"estimate": [{"nccAdId": AD, "bid": 1000 + pos, "position": pos}]})
        return _Resp(200, {"estimate": []})

    monkeypatch.setattr(sa2, "_estimate_post", fake_post)
    monkeypatch.setattr(sa2, "_SLEEP_BETWEEN_CALLS", 0)
    out = sa2.probe_market_bids([AD], "MOBILE")
    assert set(out[AD]["ladder"].keys()) == {1, 3, 4}


# ══════════════════════════════════════════════════════════════════
# SA3 — 첫 입찰 판정
# ══════════════════════════════════════════════════════════════════
def _ceil(v, **kw):
    base = {"ceiling_cpc": v, "rpc": Decimal("2224"), "rpc_source": "adgroup",
            "sample_clk": 100, "confident": True, "bep_roas": Decimal("1.5921"), "reason": ""}
    base.update(kw)
    return base


def _mkt(ladder, expmin=None, is_floor=False):
    return {"ladder": ladder, "exposure_min": expmin, "is_floor": is_floor}


def test_sa3_takes_min_of_ceiling_and_market():
    """상한 > 시장가면 시장가가 결정한다."""
    d = sa3.decide_cold_start_bid(ceiling=_ceil(5000), market=_mkt({1: 3870, 3: 3440, 4: 3010}),
                                  current_bid=300)
    assert d["decision"] == sa3.DECISION_PROPOSE
    assert d["target_bid"] == 3440  # 3위 시장가
    assert "시장가(3위)이 결정" in d["reason"]


def test_sa3_ceiling_binds_when_market_is_higher():
    """시장가 > 상한이면 상한에서 시작(D-NAO-91: BEP 우선, 순위는 시장이 주는 대로)."""
    d = sa3.decide_cold_start_bid(ceiling=_ceil(3200), market=_mkt({1: 3870, 3: 3440, 4: 3010}),
                                  current_bid=300)
    assert d["decision"] == sa3.DECISION_PROPOSE
    assert d["target_bid"] == 3200
    assert "상한이 결정" in d["reason"]


def test_sa3_not_viable_when_ceiling_below_ladder_min():
    """상한이 사다리 최저가보다 낮으면 경제성 없음 — 제안 없이 경보.

    ★라이브 실측 사례: 캠페인 08514959 상한 1,397원 vs 4위 시장가 2,630원."""
    d = sa3.decide_cold_start_bid(ceiling=_ceil(1390), market=_mkt({1: 3420, 2: 3100, 3: 2810, 4: 2630}),
                                  current_bid=300)
    assert d["decision"] == sa3.DECISION_NOT_VIABLE
    assert d["target_bid"] is None
    assert "경제성 없음" in d["reason"]


def test_sa3_exposure_min_is_not_a_gate():
    """최소노출가가 목표순위 시장가보다 높아도 제안을 막지 않는다(실측: expmin이 사다리 중간).

    nad-…558730 실측: pos3=2810인데 expmin=3270. expmin을 하드 게이트로 쓰면 현재 정상
    노출 중인 소재까지 전부 차단된다."""
    d = sa3.decide_cold_start_bid(
        ceiling=_ceil(3000), market=_mkt({1: 3420, 2: 3100, 3: 2810, 4: 2630}, expmin=3270),
        current_bid=300,
    )
    assert d["decision"] == sa3.DECISION_PROPOSE
    assert d["target_bid"] == 2810
    assert d["exposure_min"] == 3270  # 참고로는 실려 나간다


def test_sa3_holds_when_market_is_floor():
    """시세 무의미면 임의값 넣지 않고 보류(이 스프린트의 존재 이유)."""
    d = sa3.decide_cold_start_bid(ceiling=_ceil(2000), market=_mkt({1: 50, 2: 50}, is_floor=True),
                                  current_bid=300)
    assert d["decision"] == sa3.DECISION_HOLD_NO_MARKET
    assert d["target_bid"] is None


def test_sa3_holds_when_no_ceiling():
    d = sa3.decide_cold_start_bid(ceiling=_ceil(0, reason="상품 BEP 없음"),
                                  market=_mkt({1: 3000, 3: 2500}), current_bid=300)
    assert d["decision"] == sa3.DECISION_HOLD_NO_CEILING


def test_sa3_holds_when_not_an_increase():
    """산출값이 현재 입찰 이하면 상향 없음(CS는 첫 상향 전용)."""
    d = sa3.decide_cold_start_bid(ceiling=_ceil(5000), market=_mkt({3: 2500}), current_bid=3000)
    assert d["decision"] == sa3.DECISION_HOLD_NO_CHANGE
    assert d["target_bid"] is None


def test_sa3_target_bid_is_10won_multiple():
    """입찰가는 반드시 10원 배수 — 아니면 네이버 API가 400으로 거부한다."""
    d = sa3.decide_cold_start_bid(ceiling=_ceil(2837), market=_mkt({3: 9999, 4: 2000}),
                                  current_bid=100)
    assert d["decision"] == sa3.DECISION_PROPOSE
    assert d["target_bid"] == 2830  # min(2837, 9999) → 10원 내림
    assert d["target_bid"] % 10 == 0


def test_sa3_single_position_ladder_uses_that_price_as_min():
    """사다리에 순위가 하나뿐이면 그것이 곧 최저가 — 상한이 그보다 낮으면 경제성 없음."""
    d = sa3.decide_cold_start_bid(ceiling=_ceil(2837), market=_mkt({3: 9999}), current_bid=100)
    assert d["decision"] == sa3.DECISION_NOT_VIABLE


def test_sa3_falls_back_to_nearest_position():
    """목표 3위 시세가 없으면 최근접 순위 시세를 쓴다."""
    d = sa3.decide_cold_start_bid(ceiling=_ceil(9000), market=_mkt({1: 3870, 4: 3010}),
                                  current_bid=300)
    assert d["decision"] == sa3.DECISION_PROPOSE
    assert d["target_bid"] == 3010  # 4위가 3위에 더 가까움
    assert "최근접 4위" in d["reason"]


# ══════════════════════════════════════════════════════════════════
# Harness — 콜드 판정 / 첫 1회 제한 / dry-run
# ══════════════════════════════════════════════════════════════════
def test_lane_selects_cold_ad(db):
    _seed(db)
    cold = lane.select_cold_ads(db, TODAY)
    assert len(cold) == 1
    assert cold[0]["ad_id"] == AD
    assert cold[0]["current_bid"] == 300
    assert "실집행 이력 없음" in cold[0]["cold_reason"]


def test_lane_uses_group_bid_when_use_group_true(db):
    _seed(db, use_group=True)
    cold = lane.select_cold_ads(db, TODAY)
    assert cold[0]["current_bid"] == 500  # naver_entity 그룹 입찰


def test_lane_skips_non_ours_campaign(db):
    _seed(db)
    db.query(NaverCampaignSettings).update({"optimizer": "mop"})
    db.commit()
    assert lane.select_cold_ads(db, TODAY) == []


def test_lane_skips_killswitch_off_campaign(db):
    _seed(db)
    db.query(NaverCampaignSettings).update({"auto_operate": False})
    db.commit()
    assert lane.select_cold_ads(db, TODAY) == []


def test_lane_skips_paused_ad(db):
    _seed(db)
    db.query(NaverAdgroupProduct).update({"ad_user_lock": True})
    db.commit()
    assert lane.select_cold_ads(db, TODAY) == []


def _cold_changelog(db, *, dry_run=False, after_value="900", proposal_type="bid_up_cold"):
    """CS 집행 흔적 1건(제안 조인 포함) — 첫 1회 판정은 proposal_type + after_value로 한다."""
    from app.models import NaverProposal
    p = NaverProposal(
        proposal_type=proposal_type, target_type="ad", target_id=AD, campaign_id=CID,
        adgroup_id=GID, rationale="x", expected_effect="x", status="approved",
        target_bid=900, approval_source=lane.APPROVAL_SOURCE_COLD,
    )
    db.add(p)
    db.commit()
    db.add(NaverChangeLog(
        entity_type="ad", entity_id=AD, campaign_id=CID, action="update_bid",
        rationale=f"{lane.RATIONALE_PREFIX} 발사", dry_run=dry_run,
        after_value=after_value, proposal_id=p.id,
    ))
    db.commit()


def test_lane_first_time_only(db):
    """CS가 이미 실집행(쓰기 확정)한 소재는 다시 잡히지 않는다."""
    _seed(db)
    _cold_changelog(db)
    assert lane.select_cold_ads(db, TODAY) == []


def test_lane_dry_run_row_does_not_consume_the_one_shot(db):
    """dry-run 기록은 '첫 1회'를 소진하지 않는다(배포 절차상 첫 회차는 항상 dry-run)."""
    _seed(db)
    _cold_changelog(db, dry_run=True)
    assert len(lane.select_cold_ads(db, TODAY)) == 1


def test_lane_guard_blocked_row_does_not_consume_the_one_shot(db):
    """★P1-2: 가드 차단 행(after_value=None)도 '첫 1회'를 소진하지 않는다."""
    _seed(db)
    _cold_changelog(db, after_value=None)
    assert len(lane.select_cold_ads(db, TODAY)) == 1


def test_lane_not_cold_when_touched_and_high_imp(db):
    """우리가 실제로 입찰을 바꿨고 노출도 충분하면 콜드가 아니다."""
    _seed(db)
    db.add(NaverChangeLog(entity_type="ad", entity_id=AD, campaign_id=CID,
                          action="update_bid", rationale="[순위서보] x", dry_run=False,
                          after_value="1200"))
    db.add(NaverAdDaily(ad_date=TODAY - timedelta(days=1), campaign_id=CID, adgroup_id=GID,
                        keyword_id="", imp=5000, clk=10, cost=100))
    db.commit()
    assert lane.select_cold_ads(db, TODAY) == []


def test_lane_dry_run_writes_nothing(db, monkeypatch):
    """dry_run=True는 제안 레코드도 만들지 않는다(순수 관측)."""
    from app.models import NaverProposal
    _seed(db)
    db.add(NaverBidEstimateDaily(date=TODAY, ad_id=AD, adgroup_id=GID, campaign_id=CID,
                                 device="MOBILE", position=3, bid=1000, is_floor=False))
    db.add(NaverBidEstimateDaily(date=TODAY, ad_id=AD, adgroup_id=GID, campaign_id=CID,
                                 device="MOBILE", position=4, bid=900, is_floor=False))
    db.commit()
    out = lane.run_cold_start_lane(db, dry_run=True, today=TODAY)
    assert out["candidates"] == 1
    assert out["proposed"] == 1
    assert out["executed"] == 0
    assert db.query(NaverProposal).count() == 0
    assert out["rows"][0]["target_bid"] == 1000


def test_lane_reports_not_viable_with_live_shaped_numbers(db):
    """라이브 실측 형태(상한 1,390 < 사다리 최저 2,630)에서 경제성 없음으로 집계된다."""
    _seed(db)
    for pos, bid in ((1, 3420), (2, 3100), (3, 2810), (4, 2630)):
        db.add(NaverBidEstimateDaily(date=TODAY, ad_id=AD, adgroup_id=GID, campaign_id=CID,
                                     device="MOBILE", position=pos, bid=bid, is_floor=False))
    db.commit()
    out = lane.run_cold_start_lane(db, dry_run=True, today=TODAY)
    assert out["not_viable"] == 1
    assert out["proposed"] == 0
    assert "경제성 없음" in out["rows"][0]["reason"]


def test_lane_holds_when_no_market_rows(db):
    """시장가 수집이 실패해 오늘자 행이 없으면 전건 보류(안전한 no-op)."""
    _seed(db)
    out = lane.run_cold_start_lane(db, dry_run=True, today=TODAY)
    assert out["held"] == 1
    assert out["proposed"] == 0


def test_cold_proposal_type_is_registered_as_bid_up():
    """CS 타입이 UP 레지스트리에 등록돼 모든 가드가 UP으로 인식한다(부분 등록 시 fail-open 우회)."""
    from app.services.naver_ad.bid_step_types import (
        BID_UP_TYPES, CHANGE_PCT_EXEMPT_TYPES, COLD_START_STEP_TYPES, EXPLORATION_STEP_TYPES,
        RANK_STEP_TYPES,
    )
    assert lane.PROPOSAL_TYPE_COLD in BID_UP_TYPES
    assert lane.PROPOSAL_TYPE_COLD in CHANGE_PCT_EXEMPT_TYPES
    assert lane.PROPOSAL_TYPE_COLD not in RANK_STEP_TYPES
    assert lane.PROPOSAL_TYPE_COLD not in EXPLORATION_STEP_TYPES
    assert COLD_START_STEP_TYPES == {lane.PROPOSAL_TYPE_COLD}
    assert COLD_START_STEP_TYPES <= BID_UP_TYPES


def test_cold_type_is_never_delegable():
    """★보안 불변: CS는 위임 경로에서 영구 제외.

    이 타입은 ±15% 변경폭이 완전 면제라 유일한 상한이 레인이 산정한 min(이익상한, 시장가)이다.
    위임(Ava agree 자동승인) 경로는 그 산정을 거치지 않으므로, 새면 무제한 상향 + 킬스위치
    화이트리스트(cold_op) 우회가 된다. rank-step/explore와 동일 봉쇄."""
    from app.services.naver_ad import delegation_gate
    assert lane.PROPOSAL_TYPE_COLD not in delegation_gate.delegable_types()


def test_cold_approval_source_is_killswitch_guarded(db, writer_stub):
    """★보안 불변(행위 검증): 킬스위치 OFF면 콜드 제안이 쓰기까지 못 간다.

    cold_op가 harness 킬스위치 화이트리스트에 없으면 auto_operate=False인데도 CS가 쓰기를
    계속한다 — 메모리에 기록된 '킬스위치 사각' 재발 방지. 소스 문자열 세기가 아니라
    실제 실행으로 못박는다(import 방식이 바뀌어도 계약은 유지)."""
    from app.models import NaverProposal
    from app.services.naver_ad import naver_execution_harness as h
    from app.services.naver_ad.bid_step_types import encode_cold_ceiling
    _seed(db)
    db.query(NaverCampaignSettings).update({"auto_operate": False})
    db.commit()
    p = NaverProposal(
        proposal_type="bid_up_cold", target_type="ad", target_id=AD, campaign_id=CID,
        adgroup_id=GID, rationale="킬스위치 테스트",
        expected_effect=encode_cold_ceiling("x", 5000),
        status="approved", target_bid=900, approval_source=lane.APPROVAL_SOURCE_COLD,
    )
    db.add(p); db.commit()
    with pytest.raises(h.KillSwitchEngagedError):
        h.execute(db, p.id, dry_run=False)
    assert writer_stub == []


def test_cold_killswitch_second_guard_point_at_writer(db, writer_stub, monkeypatch):
    """★리뷰 3R P3: 킬스위치 **두 번째** 지점(writer 직전 최종 확인)도 cold_op를 막는가.

    첫 지점(진입 가드)만 덮으면 codex 8R이 두 번째 지점을 넣은 이유 — 진입 체크와 PUT 사이
    수백 ms 레이스(그 사이 Jino가 스위치를 내림) — 가 회귀로 안 잡힌다.
    `_auto_operate_now`를 '첫 호출 True → 이후 False'로 만들어 진입은 통과시키고 최종 확인에서
    잡히게 한다."""
    from app.models import NaverProposal
    from app.services.naver_ad import auto_operator, naver_execution_harness as h
    from app.services.naver_ad.bid_step_types import encode_cold_ceiling
    _seed(db)
    p = NaverProposal(
        proposal_type="bid_up_cold", target_type="ad", target_id=AD, campaign_id=CID,
        adgroup_id=GID, rationale="TOCTOU 킬스위치",
        expected_effect=encode_cold_ceiling("x", 5000),
        status="approved", target_bid=900, approval_source=lane.APPROVAL_SOURCE_COLD,
    )
    db.add(p); db.commit()

    seen = {"n": 0}

    def flaky(_db, _campaign_id):
        seen["n"] += 1
        return seen["n"] == 1  # 진입은 통과, 그 다음(최종 확인)부터 OFF

    monkeypatch.setattr(auto_operator, "_auto_operate_now", flaky)
    with pytest.raises(h.KillSwitchEngagedError):
        h.execute(db, p.id, dry_run=False)
    assert seen["n"] >= 2, "두 번째 가드 지점에 도달하지 않았다(첫 지점만 덮인 테스트)"
    assert writer_stub == []            # 쓰기 없음
    db.refresh(p)
    assert p.status == "approved"       # 클레임 원복(executing 잔존 없음)


def test_cold_approval_source_single_source():
    """승인원 상수의 단일 소스는 bid_step_types(리뷰 P3-12) — lane은 재수출만."""
    from app.services.naver_ad import bid_step_types
    assert lane.APPROVAL_SOURCE_COLD is bid_step_types.APPROVAL_SOURCE_COLD


def test_cold_action_maps_to_update_bid():
    from app.services.naver_ad.naver_execution_harness import _ACTION_BY_PROPOSAL_TYPE
    assert _ACTION_BY_PROPOSAL_TYPE[lane.PROPOSAL_TYPE_COLD] == "update_bid"


# ══════════════════════════════════════════════════════════════════
# 실집행 경로(dry_run=False) — ★적대적 리뷰 P1-1/P1-2/P1-3/P1-4 회귀 방어.
#   리뷰 지적: 이 경로를 harness까지 태우는 테스트가 하나도 없어서, CS가 소재 UP 쓰기 경계에
#   전건 fail-closed로 막히는데도 98건이 전부 통과했다.
# ══════════════════════════════════════════════════════════════════
def _market_rows(db, ladder=((1, 3420), (2, 3100), (3, 900), (4, 800))):
    for pos, bid in ladder:
        db.add(NaverBidEstimateDaily(date=TODAY, ad_id=AD, adgroup_id=GID, campaign_id=CID,
                                     device="MOBILE", position=pos, bid=bid, is_floor=False))
    db.commit()


@pytest.fixture
def writer_stub(monkeypatch):
    """naver_sa_writer.update_ad_bid 스텁 — 실제 HTTP 없이 호출 인자만 기록."""
    from app.services.naver_ad import naver_execution_harness as h

    calls = []

    def fake_update_ad_bid(ncc_ad_id, bid_amt, **kwargs):  # kwargs=CAS 기준가(D-NAO-129)
        calls.append((ncc_ad_id, bid_amt))
        return h.naver_sa_writer.WriteResult(
            action="update_ad_bid",
            before={"nccAdId": ncc_ad_id, "adAttr": {"bidAmt": 300}},
            response={"ok": True},
            after={"nccAdId": ncc_ad_id, "adAttr": {"bidAmt": bid_amt}},
            created_ids=[],
        )

    def fake_get_ad_bid(ncc_ad_id):
        return 300

    monkeypatch.setattr(h.naver_sa_writer, "update_ad_bid", fake_update_ad_bid, raising=False)
    monkeypatch.setattr(h.naver_sa_writer, "get_ad_bid", fake_get_ad_bid, raising=False)
    return calls


def test_lane_real_execution_reaches_writer(db, writer_stub):
    """★P1-1 회귀: 콜드 제안이 소재 UP 쓰기 경계를 통과해 실제 writer까지 도달해야 한다.

    회귀 전에는 'ad UP은 explore_op 전용' 경계에 막혀 executed=0·failed=1이었다."""
    from app.models import NaverProposal
    _seed(db)
    _market_rows(db)
    out = lane.run_cold_start_lane(db, dry_run=False, today=TODAY)
    assert out["proposed"] == 1, out
    assert out["executed"] == 1, out["rows"]
    assert out["failed"] == 0
    assert writer_stub == [(AD, 900)]  # 3위 시장가 900 (상한 1390보다 낮음)
    p = db.query(NaverProposal).one()
    assert p.proposal_type == "bid_up_cold"
    assert p.approval_source == lane.APPROVAL_SOURCE_COLD
    assert p.executed_change_log_id is not None


def test_ceiling_marker_carries_ceiling_not_target_bid(db, writer_stub):
    """★P2-B 회귀: 마커에는 **경제 상한**이 담겨야 한다(target_bid가 아니라).

    target_bid = min(상한, 시장가)이므로 마커에 target_bid를 담으면 쓰기 경계 검사
    `target_bid > ceiling`이 `X > X` = 항상 False인 **동어반복**이 된다 — 게이트가 실제로
    거르는 건 '마커 유무'뿐이고 레인의 클램프는 아무것도 재검증되지 않는다."""
    from app.models import NaverProposal
    from app.services.naver_ad.bid_step_types import decode_cold_ceiling
    _seed(db)
    _market_rows(db)  # 3위 시장가 900, 상한 1390 → target_bid=900
    lane.run_cold_start_lane(db, dry_run=False, today=TODAY)
    p = db.query(NaverProposal).one()
    marker = decode_cold_ceiling(p.expected_effect)
    assert marker == 1390, marker          # 경제 상한
    assert p.target_bid == 900             # min(상한, 시장가)
    assert marker != p.target_bid          # ★동어반복이 아니어야 한다
    assert p.target_bid <= marker          # 경계 검사가 실질 단언


def test_lane_clamp_bug_would_be_caught_by_write_boundary(db, writer_stub):
    """★P2-B의 목적: '레인이 상한을 잘못 씌웠다'를 쓰기 경계가 잡아낸다.

    레인 버그를 흉내내 target_bid만 상한 위로 올린 제안을 만들면 경계가 죽여야 한다."""
    from app.models import NaverProposal
    from app.services.naver_ad import naver_execution_harness as h
    from app.services.naver_ad.bid_step_types import encode_cold_ceiling
    _seed(db)
    p = NaverProposal(
        proposal_type="bid_up_cold", target_type="ad", target_id=AD, campaign_id=CID,
        adgroup_id=GID, rationale="레인 클램프 버그 흉내",
        expected_effect=encode_cold_ceiling("x", 1390),  # 상한 1390
        status="approved", target_bid=3000,               # 그런데 3000을 쓰려 함
        approval_source=lane.APPROVAL_SOURCE_COLD,
    )
    db.add(p); db.commit()
    assert h.real_write_blocker(p) is not None      # 콘솔에도 비활성으로 보여야 한다
    with pytest.raises(Exception):
        h.execute(db, p.id, dry_run=False)
    assert writer_stub == []


def test_cold_without_ceiling_marker_is_fail_closed(db, writer_stub):
    """★P1-3: 마커 없는(경로 밖 생성/변조) 콜드 제안은 쓰기 경계에서 죽는다."""
    from app.models import NaverProposal
    from app.services.naver_ad import naver_execution_harness as h
    _seed(db)
    p = NaverProposal(
        proposal_type="bid_up_cold", target_type="ad", target_id=AD, campaign_id=CID,
        adgroup_id=GID, rationale="손으로 만든 제안", expected_effect="마커 없음",
        status="approved", target_bid=90000, approval_source=lane.APPROVAL_SOURCE_COLD,
    )
    db.add(p); db.commit()
    with pytest.raises(Exception):
        h.execute(db, p.id, dry_run=False)
    assert writer_stub == []


def test_cold_on_adgroup_target_is_fail_closed(db, writer_stub):
    """★P1-3 재현 회귀: target_type='adgroup'으로 소재 경계를 우회하려는 시도는 차단된다.

    리뷰가 재현한 사고: 그룹 300원 → 90,000원(300배)이 아무 가드 없이 통과했다."""
    from app.models import NaverProposal
    from app.services.naver_ad import naver_execution_harness as h
    from app.services.naver_ad.bid_step_types import encode_cold_ceiling
    _seed(db)
    p = NaverProposal(
        proposal_type="bid_up_cold", target_type="adgroup", target_id=GID, campaign_id=CID,
        adgroup_id=GID, rationale="우회 시도",
        expected_effect=encode_cold_ceiling("x", 90000),
        status="approved", target_bid=90000, approval_source=lane.APPROVAL_SOURCE_COLD,
    )
    db.add(p); db.commit()
    with pytest.raises(Exception):
        h.execute(db, p.id, dry_run=False)
    assert writer_stub == []


def test_cold_type_with_foreign_approval_source_is_fail_closed(db, writer_stub):
    """★P1-3 쌍방향 잠금: bid_up_cold를 콘솔(NULL)/타 승인원으로 태우려 하면 차단."""
    from app.models import NaverProposal
    from app.services.naver_ad import naver_execution_harness as h
    from app.services.naver_ad.bid_step_types import encode_cold_ceiling
    _seed(db)
    p = NaverProposal(
        proposal_type="bid_up_cold", target_type="ad", target_id=AD, campaign_id=CID,
        adgroup_id=GID, rationale="콘솔 승인 시도",
        expected_effect=encode_cold_ceiling("x", 90000),
        status="approved", target_bid=90000, approval_source=None,
    )
    db.add(p); db.commit()
    with pytest.raises(Exception):
        h.execute(db, p.id, dry_run=False)
    assert writer_stub == []


def test_explore_source_cannot_carry_cold_type(db, writer_stub):
    """★P1-3 쌍방향 잠금 반대 방향: explore_op가 bid_up_cold를 태울 수 없다."""
    from app.models import NaverProposal
    from app.services.naver_ad import naver_execution_harness as h
    from app.services.naver_ad.bid_step_types import encode_cold_ceiling
    from app.services.naver_ad.exploration import APPROVAL_SOURCE_EXPLORE
    _seed(db)
    p = NaverProposal(
        proposal_type="bid_up_cold", target_type="ad", target_id=AD, campaign_id=CID,
        adgroup_id=GID, rationale="교차 승인원",
        expected_effect=encode_cold_ceiling("x", 5000),
        status="approved", target_bid=5000, approval_source=APPROVAL_SOURCE_EXPLORE,
    )
    db.add(p); db.commit()
    with pytest.raises(Exception):
        h.execute(db, p.id, dry_run=False)
    assert writer_stub == []


def test_blocked_attempt_does_not_burn_the_one_shot(db, writer_stub):
    """★P1-2 회귀: 가드에 막힌 시도([실행 불가] 행)는 '첫 1회'를 소진하지 않는다.

    회귀 전에는 _guard_failure가 제안 rationale을 앞에 붙여 기록하는 바람에 접두 LIKE에
    매칭돼, 차단당한 소재가 영구히 CS 대상에서 빠졌다."""
    from app.models import NaverProposal
    from app.services.naver_ad import naver_execution_harness as h
    from app.services.naver_ad.bid_step_types import encode_cold_ceiling
    _seed(db)
    p = NaverProposal(
        proposal_type="bid_up_cold", target_type="ad", target_id=AD, campaign_id=CID,
        adgroup_id=GID, rationale=f"{lane.RATIONALE_PREFIX} 차단될 제안",
        expected_effect=encode_cold_ceiling("x", 100),
        status="approved", target_bid=90000,  # 상한 100 초과 → 쓰기 경계에서 차단
        approval_source=lane.APPROVAL_SOURCE_COLD,
    )
    db.add(p); db.commit()
    with pytest.raises(Exception):
        h.execute(db, p.id, dry_run=False)
    assert writer_stub == []
    # 차단 행이 change_log에 남았어도 첫 1회는 살아 있어야 한다.
    assert db.query(NaverChangeLog).count() >= 1
    assert lane._already_fired_ad_ids(db) == set()
    assert len(lane.select_cold_ads(db, TODAY)) == 1


def test_successful_execution_does_burn_the_one_shot(db, writer_stub):
    """성공 집행은 첫 1회를 소진한다(재발동 방지가 실제로 작동)."""
    _seed(db)
    _market_rows(db)
    out = lane.run_cold_start_lane(db, dry_run=False, today=TODAY)
    assert out["executed"] == 1
    assert lane._already_fired_ad_ids(db) == {AD}
    assert lane.select_cold_ads(db, TODAY) == []


def test_round_cap_counts_attempts_not_successes(db, writer_stub, monkeypatch):
    """★P1-4 회귀: 집행이 전부 실패해도 라운드 캡이 걸려야 한다."""
    from app.services.naver_ad import naver_execution_harness as h
    _seed(db)
    _market_rows(db)
    # 소재 6개(전부 콜드)로 늘린다.
    for i in range(5):
        aid = f"nad-extra-{i}"
        # 같은 상품(PID)을 물려 BEP를 공유시킨다 — 안 그러면 상한이 안 나와 hold로 빠져
        # 캡 검증이 성립하지 않는다.
        db.add(NaverAdgroupProduct(
            adgroup_id=f"{GID}-{i}", campaign_id=CID, mall_product_id=PID,
            product_name="x", ad_id=aid, ad_bid_amt=300,
            use_group_bid_amt=False, ad_user_lock=False,
        ))
        for pos, bid in ((3, 900), (4, 800)):
            db.add(NaverBidEstimateDaily(date=TODAY, ad_id=aid, adgroup_id=GID, campaign_id=CID,
                                         device="MOBILE", position=pos, bid=bid, is_floor=False))
    db.commit()
    monkeypatch.setattr(h, "execute", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = lane.run_cold_start_lane(db, dry_run=False, today=TODAY, max_proposals=2)
    assert out["executed"] == 0
    assert out["failed"] == 2, out  # 캡이 걸려 2건까지만 시도


def test_market_probe_total_failure_preserves_today_rows(db, monkeypatch):
    """★P2-5 회귀: API 전면 실패 시 오늘자 기존 수집분을 지우지 않는다(no-op)."""
    class _Resp:
        status_code = 500
        text = "boom"

        def json(self):
            return {}

    _market_rows(db)
    before = db.query(NaverBidEstimateDaily).count()
    monkeypatch.setattr(sa2, "_estimate_post", lambda p, b: _Resp())
    monkeypatch.setattr(sa2, "_SLEEP_BETWEEN_CALLS", 0)
    out = sa2.collect_daily(db, [(AD, GID, CID)], TODAY, devices=("MOBILE",))
    assert out["rows"] == 0
    assert db.query(NaverBidEstimateDaily).count() == before  # 보존됨


def test_low_confidence_ceiling_is_discounted():
    """★P2-9 회귀: confident=False(계정 폴백)면 상한에 보수 계수가 실제로 적용된다."""
    d = sa3.decide_cold_start_bid(
        ceiling=_ceil(1000, rpc_source="account", confident=False),
        market=_mkt({3: 5000, 4: 4000}), current_bid=100,
    )
    assert d["raw_ceiling_cpc"] == 1000
    assert d["ceiling_cpc"] == 700  # 1000 × 0.7
    assert d["decision"] == sa3.DECISION_NOT_VIABLE  # 700 < 4000


def test_nearest_position_fallback_never_goes_more_expensive():
    """★P2-10 회귀: 목표 순위 부재 시 더 비싼 상위 순위 가격을 쓰지 않는다."""
    d = sa3.decide_cold_start_bid(ceiling=_ceil(9000), market=_mkt({1: 3870, 2: 3800}),
                                  current_bid=300)
    assert d["decision"] == sa3.DECISION_HOLD_NO_MARKET
    assert "더 비싼 자리" in d["reason"]


def test_cold_approval_source_fits_column():
    """approval_source는 String(12) — 넘치면 조용히 잘려 킬스위치 매칭이 깨진다."""
    assert len(lane.APPROVAL_SOURCE_COLD) <= 12
