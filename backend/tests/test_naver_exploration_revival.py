# test_naver_exploration_revival.py — D-NAO-289 「탐색소생 목표」 회귀 고정 테스트.
# 계약 CONTRACT_exploration_revival.md §4-B ⓐ~ⓕ. 대상: auto_operator._exploration_daily_loss_reason
# (가드5)에 얹은 «표본 부족» 예외 — bleeding 판정이라도 14일 비용이 실효입찰×LOW_CLICK_THRESHOLD
# 미만이면 손실 확정이 아니라 표본 부족이므로 탐색을 연다. 스톱로스 보드(두 번째 조건)·
# retro stale/부재 fail-closed·상수 재사용·스코프 밖 실쓰기 0은 그대로다(계약 §3 금지선).
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdDaily,
    NaverAdgroupProduct,
    NaverAdgroupScope,
    NaverCampaignSettings,
    NaverEntity,
    NaverHourlySnapshot,
    NaverProductBep,
    NaverProposal,
    NaverRetroSignal,
)
from app.services.naver_ad import account_diagnosis, auto_operator, effective_bid

# §0 실측 구조 재현 — 실 계정 id는 노출 규칙상 접미만 남기고 로컬 식별자로 둔다.
ADGROUP_A = "adgroup-59832147"  # §0: 그룹입찰 50 · 소재 bidAmt 150 · cost_asof 1,117(표본 부족)
ADGROUP_B = "adgroup-59832150"  # §0: 그룹입찰 1000 · 소재 bidAmt 460 · cost_asof 17,617(출혈확정)
CAMP = "cmp-shop-n89"

TODAY = date(2026, 7, 21)
NOW = datetime(2026, 7, 21, 8, 20, 0)  # 08:30 이전 → 기대 asof=today-2, ASOF(today-1)는 그보다 신선
ASOF = date(2026, 7, 20)  # today-1 — 신선(§0 실측과 동일 asof 배치)


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


def _seed_retro(db, *, target_id, board="shopping_group_bep", asof=ASOF, cost_asof,
                campaign_id=CAMP, direction="down"):
    """소급채점 신호 1행(retro_snapshotter._BOARDS 스냅 형태) — cost_asof는 보드가 본 14일 비용
    그대로(§10: -14d~-1d 근사창과 다르다·구현·테스트 둘 다 이 컬럼을 쓴다)."""
    db.add(NaverRetroSignal(
        created_at=NOW, asof_date=asof, board=board, direction=direction, grain="adgroup",
        target_id=target_id, campaign_id=campaign_id, cf_asof=1.0, bep_asof=1.5, target_asof=2.0,
        cost_asof=cost_asof,
    ))
    db.commit()


def _seed_ad_bid(db, *, adgroup_id, ad_id, ad_bid_amt, mall_product_id="pidX",
                 use_group_bid_amt=False, campaign_id=CAMP):
    """소재-레벨 입찰 1행(NaverAdgroupProduct, B1) — effective_bid._derive 원료. SQL 근사가
    아니라 실제 SA(`effective_bid.adgroup_effective_bid`)가 이 행을 읽어 실효입찰을 판정한다."""
    db.add(NaverAdgroupProduct(
        adgroup_id=adgroup_id, campaign_id=campaign_id, mall_product_id=mall_product_id,
        product_name="p", ad_id=ad_id, ad_bid_amt=ad_bid_amt, use_group_bid_amt=use_group_bid_amt,
        ad_user_lock=False,
    ))
    db.commit()


# ══════════════════════ ⓐ~ⓔ: 가드5 판정 단위 테스트(직접 호출) ══════════════════════
# _bleeding_hold_reason은 NaverRetroSignal만 읽고, effective_bid SA는 NaverAdgroupProduct만
# 읽는다 — 캠페인/엔티티 전체 배선 없이도 가드5 판정을 정확히 재현할 수 있다(ⓕ만 전체 레인).

def test_a_bleeding_sample_scarce_allows_exploration(db):
    """§4-B ⓐ: bleeding ∧ cost_asof(1,117) < 실효입찰(150)×10(=1,500) → 가드5 통과(None).
    실효입찰은 SA(effective_bid.adgroup_effective_bid) 호출로 얻는다(SQL 근사 금지)."""
    _seed_ad_bid(db, adgroup_id=ADGROUP_A, ad_id="ad-A", ad_bid_amt=150)
    _seed_retro(db, target_id=ADGROUP_A, cost_asof=1117)

    eff = effective_bid.adgroup_effective_bid(db, ADGROUP_A, 50)  # 그룹입찰 50(§0 구조)
    assert eff["effective_bid"] == 150
    assert eff["source"] == "ad"

    reason = auto_operator._exploration_daily_loss_reason(
        db, ADGROUP_A, TODAY, NOW, effective_bid_value=eff["effective_bid"])
    assert reason is None


def test_b_bleeding_confirmed_loss_stays_excluded(db):
    """§4-B ⓑ: bleeding ∧ cost_asof(17,617) ≥ 실효입찰(460)×10(=4,600) → 제외 유지, 사유문에
    두 값이 원문으로 실린다."""
    _seed_ad_bid(db, adgroup_id=ADGROUP_B, ad_id="ad-B", ad_bid_amt=460)
    _seed_retro(db, target_id=ADGROUP_B, cost_asof=17617)

    eff = effective_bid.adgroup_effective_bid(db, ADGROUP_B, 1000)  # 그룹입찰 1000(§0 구조)
    assert eff["effective_bid"] == 460

    reason = auto_operator._exploration_daily_loss_reason(
        db, ADGROUP_B, TODAY, NOW, effective_bid_value=eff["effective_bid"])
    assert reason is not None
    assert "17617" in reason
    assert "4600" in reason
    assert "D-NAO-289" in reason


def test_c1_missing_retro_data_fail_closed(db):
    """§4-B ⓒ(부재): 소급채점 데이터 전무 → 종전 사유 그대로 제외(fail-closed 불변,
    표본 부족 예외 미적용)."""
    reason = auto_operator._exploration_daily_loss_reason(
        db, ADGROUP_A, TODAY, NOW, effective_bid_value=999_999)
    assert reason is not None
    assert "데이터 없음" in reason


def test_c2_stale_retro_fail_closed(db):
    """§4-B ⓒ(stale): 최신 asof가 기대보다 낡음 → 종전 사유 그대로 제외(fail-closed 불변),
    effective_bid_value가 있어도(=예외 조건이 아니므로) 열리지 않는다."""
    _seed_retro(db, target_id=ADGROUP_A, cost_asof=0, asof=date(2026, 7, 18))  # stale
    reason = auto_operator._exploration_daily_loss_reason(
        db, ADGROUP_A, TODAY, NOW, effective_bid_value=999_999)
    assert reason is not None
    assert "stale" in reason


def test_d_sample_scarce_but_on_stoploss_board_stays_excluded(db):
    """§4-B ⓓ: cost_asof < 임계여도 shopping_pause_candidates(스톱로스 보드)에 있으면
    제외(가드5 두 번째 조건 생존 — §3 금지선: 그 조건 삭제·완화 금지)."""
    _seed_ad_bid(db, adgroup_id=ADGROUP_A, ad_id="ad-A", ad_bid_amt=150)
    _seed_retro(db, target_id=ADGROUP_A, board="shopping_group_bep", cost_asof=1117)
    _seed_retro(db, target_id=ADGROUP_A, board="shopping_pause_candidates", cost_asof=0,
               direction="pause")

    eff = effective_bid.adgroup_effective_bid(db, ADGROUP_A, 50)
    reason = auto_operator._exploration_daily_loss_reason(
        db, ADGROUP_A, TODAY, NOW, effective_bid_value=eff["effective_bid"])
    assert reason is not None
    assert "스톱로스" in reason


def test_e_threshold_is_same_object_as_account_diagnosis_constant(db, monkeypatch):
    """§4-B ⓔ: 가드5가 읽는 임계가 account_diagnosis.LOW_CLICK_THRESHOLD와 «같은 객체» —
    복제 리터럴이면 이 패치가 무효과라 아래 두 번째 단언이 실패한다.
    경계: eff=150 → 10배=1,500(cost_asof=1,500이면 «≥»라 제외) · 11배=1,650(같은 값이 이제
    미만이라 통과)."""
    _seed_ad_bid(db, adgroup_id=ADGROUP_A, ad_id="ad-A", ad_bid_amt=150)
    _seed_retro(db, target_id=ADGROUP_A, cost_asof=1500)
    eff = effective_bid.adgroup_effective_bid(db, ADGROUP_A, 50)
    assert eff["effective_bid"] == 150

    before = auto_operator._exploration_daily_loss_reason(
        db, ADGROUP_A, TODAY, NOW, effective_bid_value=eff["effective_bid"])
    assert before is not None  # 10배 경계 — 1500 >= 1500 → 제외

    monkeypatch.setattr(account_diagnosis, "LOW_CLICK_THRESHOLD", 11)
    after = auto_operator._exploration_daily_loss_reason(
        db, ADGROUP_A, TODAY, NOW, effective_bid_value=eff["effective_bid"])
    assert after is None  # 같은 객체 참조라면 11배(1650) 기준 1500 < 1650 → 통과


# ══════════════════════ ⓕ: 스코프 밖 행위 변화 0(전체 레인) ══════════════════════

CAMP_SCOPE = "cmp-shop-scope-n89"
GRP_SCOPE = ADGROUP_A
YESTERDAY = TODAY - timedelta(days=1)


def _hour(h, *, imp, clk, cost, avg_rank=None, conv_cnt=0):
    return {"hour": h, "imp": imp, "clk": clk, "cost": cost, "avg_rank": avg_rank, "conv_cnt": conv_cnt}


def _setup_scope_lane(db):
    """§4-B ⓕ 전용 최소 레인 배선 — bx3(test_naver_ad_exploration_bx3.py)의 `_setup` 관례를
    재사용(정착창 clk<10=탐색 후보·BEP 매핑·신선 스냅샷·어제 daily flow) + 이번 계약이 더하는
    것: bleeding-표본부족 retro(가드5 예외 대상) + 이 그룹을 스코프 밖으로 두는 scope 행."""
    db.add(NaverCampaignSettings(campaign_id=CAMP_SCOPE, auto_operate=True, optimizer="ours"))
    db.add(NaverEntity(entity_type="campaign", entity_id=CAMP_SCOPE, campaign_id=CAMP_SCOPE,
                       campaign_type="SHOPPING", status="on"))
    db.add(NaverEntity(entity_type="adgroup", entity_id=GRP_SCOPE, parent_id=CAMP_SCOPE,
                       campaign_id=CAMP_SCOPE, campaign_type="SHOPPING", status="on"))
    db.add(NaverAdDaily(  # 정착창(7/15 ∈ [7/13,7/19]) clk=3<10 → 탐색 후보(핫셋 미달)
        ad_date=date(2026, 7, 15), campaign_id=CAMP_SCOPE, campaign_type="SHOPPING",
        adgroup_id=GRP_SCOPE, keyword_id="-", imp=100, clk=3, cost=1000,
        conv_direct_amt=30000, conv_indirect_amt=0,
    ))
    db.add(NaverProductBep(channel_id=6, channel_product_id="pidX", product_name="p",
                           selling_price=Decimal("10000"), has_cost=True,
                           contribution_margin=Decimal("4000"), bep_roas=Decimal("0.5")))
    db.add(NaverAdgroupProduct(adgroup_id=GRP_SCOPE, campaign_id=CAMP_SCOPE, mall_product_id="pidX"))
    db.add(NaverHourlySnapshot(  # 신선(hour 23) → 서킷브레이커 통과
        snapshot_at=NOW, ad_date=TODAY, snapshot_hour=23, campaign_id=CAMP_SCOPE,
        campaign_type="SHOPPING", cost=0, clk=0, imp=0, daily_budget=100000,
    ))
    db.add(NaverAdDaily(  # 어제 daily(롤링 24h flow 확정 원료) — 무클릭 확정
        ad_date=YESTERDAY, campaign_id=CAMP_SCOPE, campaign_type="SHOPPING",
        adgroup_id=GRP_SCOPE, keyword_id="-", imp=50, clk=0, cost=100,
        conv_direct_amt=0, conv_indirect_amt=0,
    ))
    # D-NAO-289 표본 부족 retro — bleeding 보드에 있으나 가드5가 예외로 통과시켜야 후보가 산다.
    db.add(NaverRetroSignal(
        created_at=NOW, asof_date=ASOF, board="shopping_group_bep", direction="down", grain="adgroup",
        target_id=GRP_SCOPE, campaign_id=CAMP_SCOPE, cf_asof=1.0, bep_asof=1.5, target_asof=2.0,
        cost_asof=1117,
    ))
    # 스코프: 다른 그룹만 enabled → GRP_SCOPE는 스코프 밖(§1 결합규칙 "ON∧있음∧g∉enabled→OFF").
    db.add(NaverAdgroupScope(campaign_id=CAMP_SCOPE, adgroup_id="grp-elsewhere-in-scope",
                             enabled=True))
    db.commit()


def test_f_out_of_scope_group_makes_no_live_write(db):
    """§4-B ⓕ: 가드5 예외로 열린 그룹(ⓐ 조건)이라도 스코프 밖이면 engine_approve가 False를
    돌려 naver_execution_harness.execute가 호출되지 않는다(마지막 표면 — 계약 §1·§8 변이②).
    제안 자체는 만들어지되(레인은 열렸다) approved로 커밋되지 않는다(pending에 적체 — §4-C ⓜ)."""
    _setup_scope_lane(db)
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=6.0),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=6.0)]
    eff = {"source": "ad", "effective_bid": 150, "max_ad_id": "ad-A",
          "has_ad_data": True, "group_bid": 50, "ad_count": 1}
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 50}), \
         patch.object(auto_operator.naver_sa_writer, "get_ad_bid", return_value=150), \
         patch.object(auto_operator.effective_bid, "adgroup_effective_bid", return_value=eff), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda tid, d: curve)

    mock_exec.assert_not_called()
    assert result["explored"] == 0
    assert any("스코프" in h.get("reason", "") for h in result["held"])

    proposal = (
        db.query(NaverProposal)
        .filter(NaverProposal.adgroup_id == GRP_SCOPE, NaverProposal.proposal_type == "bid_up_explore")
        .first()
    )
    assert proposal is not None  # 레인은 제안을 만든다(가드5가 열었다는 증거)
    assert proposal.status != "approved"  # 승인은 engine_approve 단일 문에서 막혔다(죽은 카드 0)
