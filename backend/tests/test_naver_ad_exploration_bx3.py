# test_naver_ad_exploration_bx3.py — 스프린트 B-X BX3(탐색-UP 레인 배선, D-NAO-70·71).
# 커버(PLAN §검증 1 차등): 콜드(imp=0) 소폭 스텝 · 순위 피드백 적응 스텝 · 밴드 도달 정지 ·
#   rank≤2.5 진단 종료 · ceiling 클램프+쓰기경계 하드게이트 · 손실고삐 skip · 핫셋 상호배타 ·
#   WEB_SITE 제외 · 소재 1개만 · 쿨다운 2h(KST) · 재가동 태그.
# 실 네이버 API 0 — naver_sa_writer(라이브 재조회)·harness.execute는 mock.
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
    NaverCampaignSettings,
    NaverChangeLog,
    NaverEntity,
    NaverHourlySnapshot,
    NaverKeywordHourly,
    NaverProductBep,
    NaverProposal,
    NaverRetroSignal,
)
from app.services.naver_ad import auto_operator, exploration
from app.services.naver_ad import naver_execution_harness as harness
from app.services.naver_ad.bid_step_types import (
    decode_exploration_ceiling,
    encode_base_bid,
    encode_exploration_ceiling,
)


def _explore_effect(ceiling, base_bid):
    """탐색 expected_effect(ceiling 먼저·base_bid 끝) — 레인 마커 조립과 동일."""
    return encode_base_bid(encode_exploration_ceiling("x", ceiling), base_bid)

CAMP = "cmp-shop"
GRP = "ag-explore"
TODAY = date(2026, 7, 21)
NOW = datetime(2026, 7, 21, 8, 20, 0)  # 시간당 레인 크론(KST naive), now.hour=8


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


def _hour(h, *, imp, clk, cost, avg_rank=None, conv_cnt=0):
    return {"hour": h, "imp": imp, "clk": clk, "cost": cost, "avg_rank": avg_rank, "conv_cnt": conv_cnt}


ASOF_FRESH = date(2026, 7, 20)  # 기대 asof = today-1 = NOW.date()-1 (일 레인 신선도 계약)


def _retro(db, *, target_id, board="shopping_group_bep", asof=ASOF_FRESH, direction="down"):
    """소급채점 신호 1행(retro_snapshotter._BOARDS 스냅 형태) — daily 손실 보드 상태 시드."""
    db.add(NaverRetroSignal(
        created_at=NOW, asof_date=asof, board=board, direction=direction, grain="adgroup",
        target_id=target_id, campaign_id=CAMP, cf_asof=1.0, bep_asof=1.5, target_asof=2.0, cost_asof=0,
    ))
    db.commit()


YESTERDAY = date(2026, 7, 20)  # NOW.date()-1 (롤링 24h 창의 어제 부분)


def _hourly(db, *, adgroup_id, hour, clk, ad_date=YESTERDAY, imp=20, cost=0, avg_rank=None):
    """어제 시간별 1행(NaverKeywordHourly, SHOPPING 그룹=entity_type='adgroup') — 롤링 24h 원료."""
    db.add(NaverKeywordHourly(
        ad_date=ad_date, hour=hour, entity_type="adgroup", entity_id=adgroup_id, adgroup_id="",
        campaign_id=CAMP, campaign_type="SHOPPING", imp=imp, clk=clk, cost=cost, conv_cnt=0,
        avg_rank=avg_rank,
    ))
    db.commit()


def _yesterday_daily(db, *, adgroup_id, clk, campaign_id=CAMP, ad_date=YESTERDAY):
    """어제 naver_ad_daily 1행(유닛-레벨 flow 교차확인 원료 — 시간별 부재 시 이 증거로 판정)."""
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type="SHOPPING",
        adgroup_id=adgroup_id, keyword_id="-", imp=50, clk=clk, cost=100,
        conv_direct_amt=0, conv_indirect_amt=0,
    ))
    db.commit()


def _setup(db, *, campaign_type="SHOPPING", settle_clk=3, settle_conv=30000, group_bid=1000,
           adgroup_id=GRP, bep_roas="0.5", yesterday_daily=True, yesterday_daily_clk=0):
    """탐색 후보 SHOPPING 그룹 시드 — 캠페인/그룹 엔티티(on)·정착창 실적(clk<10=핫셋 미달)·
    BEP 매핑(경제성 상한)·신선 스냅샷(서킷브레이커 통과)."""
    db.add(NaverCampaignSettings(campaign_id=CAMP, auto_operate=True, optimizer="ours"))
    db.add(NaverEntity(entity_type="campaign", entity_id=CAMP, campaign_id=CAMP,
                       campaign_type=campaign_type, status="on"))
    db.add(NaverEntity(entity_type="adgroup", entity_id=adgroup_id, parent_id=CAMP,
                       campaign_id=CAMP, campaign_type=campaign_type, status="on"))
    db.add(NaverAdDaily(  # 정착창(7/15 ∈ [7/13,7/19]) — 후보(clk<10)·경제성 상한 rpc 원료
        ad_date=date(2026, 7, 15), campaign_id=CAMP, campaign_type=campaign_type,
        adgroup_id=adgroup_id, keyword_id="-", imp=100, clk=settle_clk, cost=1000,
        conv_direct_amt=settle_conv, conv_indirect_amt=0,
    ))
    db.add(NaverProductBep(channel_id=6, channel_product_id="pidX", product_name="p",
                           selling_price=Decimal("10000"), has_cost=True,
                           contribution_margin=Decimal("4000"), bep_roas=Decimal(str(bep_roas))))
    db.add(NaverAdgroupProduct(adgroup_id=adgroup_id, campaign_id=CAMP, mall_product_id="pidX"))
    db.add(NaverHourlySnapshot(  # 신선(hour 23) → 서킷브레이커 통과
        snapshot_at=NOW, ad_date=TODAY, snapshot_hour=23, campaign_id=CAMP,
        campaign_type=campaign_type, cost=0, clk=0, imp=0, daily_budget=100000,
    ))
    db.commit()
    # GATE P2-risk6: daily 손실 게이트(_bleeding_hold_reason)는 소급채점 신선도(latest_asof≥today-1)를
    # 요구한다 — 신선 더미 신호(우리 그룹 아님)를 심어 신선도 통과 + 우리 그룹은 손실 보드 밖(=정상 탐색).
    _retro(db, target_id="dummy-fresh")
    # codex 신규 P1: 롤링 24h 어제 부분의 유닛-레벨 flow 확정 원료 = 어제 naver_ad_daily(이 그룹).
    # 기본 clk=0(어제 무클릭 확정) → flow_available=True·recent_flow 0. 흐름 시나리오는 테스트별로
    # _hourly(시간별)·yesterday_daily_clk로 제어. yesterday_daily=False면 어제 daily 미수집(새벽) 재현.
    if yesterday_daily:
        _yesterday_daily(db, adgroup_id=adgroup_id, clk=yesterday_daily_clk)


def _prior_step(db, *, adgroup_id=GRP, target_type="adgroup", target_id=GRP,
                before_bid=900, after_bid=1000, changed_at=None):
    """직전 탐색 UP 성공 실쓰기(change_log) 시드 — last_probe·쿨다운·기울기 원료."""
    import json
    p = NaverProposal(
        proposal_type="bid_up_explore", target_type=target_type, target_id=target_id,
        campaign_id=CAMP, adgroup_id=adgroup_id, rationale="[탐색UP] 직전",
        expected_effect=encode_exploration_ceiling("직전", 100000),
        status="approved", target_bid=after_bid, approval_source=exploration.APPROVAL_SOURCE_EXPLORE,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    if target_type == "ad":
        before_v = json.dumps({"nccAdId": target_id, "adAttr": json.dumps({"bidAmt": before_bid})})
        after_v = json.dumps({"nccAdId": target_id, "adAttr": json.dumps({"bidAmt": after_bid})})
    else:
        before_v = json.dumps({"bidAmt": before_bid})
        after_v = json.dumps({"bidAmt": after_bid})
    db.add(NaverChangeLog(
        entity_type=target_type, entity_id=target_id, campaign_id=CAMP, action="update_bid",
        rationale="[탐색UP] 직전", proposal_id=p.id, dry_run=False,
        before_value=before_v, after_value=after_v,
        changed_at=changed_at or (NOW - timedelta(days=1)), executed_at=changed_at or (NOW - timedelta(days=1)),
    ))
    db.commit()


def _run(db, curve, *, eff=None):
    """탐색 레인만 관측(harness.execute·라이브 재조회 mock). 반환 (result, mock_exec)."""
    eff = eff or {"source": "group", "effective_bid": 1000, "max_ad_id": None,
                  "has_ad_data": False, "group_bid": 1000, "ad_count": 0}
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.naver_sa_writer, "get_ad_bid", return_value=900), \
         patch.object(auto_operator.effective_bid, "adgroup_effective_bid", return_value=eff), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda tid, d: curve)
    return result, mock_exec


# ══════════════════════ 스텝 발사(step_up / cold / adaptive) ══════════════════════

def test_lane_step_up_below_band_fires(db):
    """밴드 밖(rank 6)·무클릭·첫 스텝 → 탐색 UP 발사(explore_op·bid_up_explore·ceiling 마커)."""
    _setup(db)
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=6.0),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=6.0),
             _hour(7, imp=20, clk=0, cost=0, avg_rank=6.0)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 1
    mock_exec.assert_called_once()
    p = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert p.proposal_type == "bid_up_explore"
    assert p.approval_source == exploration.APPROVAL_SOURCE_EXPLORE
    assert p.target_type == "adgroup" and p.target_id == GRP
    assert p.target_bid == 1100  # 첫 스텝 보수 +10%(1000→1100), ceiling 2000 이내
    assert p.rationale.startswith("[탐색UP]")
    assert decode_exploration_ceiling(p.expected_effect) is not None  # 쓰기경계 하드게이트 원료


def test_lane_cold_imp0_small_step(db):
    """콜드(imp=0=rank 관측 불가) → 보수적 소폭(+10%) blind 스텝."""
    _setup(db)
    curve = [_hour(5, imp=0, clk=0, cost=0, avg_rank=None),
             _hour(6, imp=0, clk=0, cost=0, avg_rank=None),
             _hour(7, imp=0, clk=0, cost=0, avg_rank=None)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 1
    p = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert p.target_bid == 1100  # blind +10%


def test_lane_adaptive_slope_step(db):
    """순위 피드백 적응 스텝(기울기 有): 직전 900→1000에 rank 8→6(어제 스텝), 오늘 rank 6 →
    목표 rank4까지 필요 증분 = (100원/rank2)×(6-4)=100 → 1100. last_probe.rank는 스텝 前 순위."""
    _setup(db)
    # 직전 스텝은 어제(step_hour=0 분기) → last_probe.rank=None이 아니라, 오늘 관측만으로 기울기가
    # 필요하므로 직전 스텝을 오늘 hour3에 두고 그 前(hour<3) rank=8, 그 後(hour>=3) rank=6로 관측.
    _prior_step(db, before_bid=900, after_bid=1000,
                changed_at=datetime(2026, 7, 21, 3, 30, 0))
    curve = [_hour(1, imp=20, clk=0, cost=0, avg_rank=8.0),  # 스텝 前
             _hour(2, imp=20, clk=0, cost=0, avg_rank=8.0),
             _hour(4, imp=20, clk=0, cost=0, avg_rank=6.0),  # 스텝 後(현 사이클)
             _hour(5, imp=20, clk=0, cost=0, avg_rank=6.0)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 1
    p = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert p.target_bid == 1100  # 1000 + (100/2)×(6-4)=1100 (눈먼 30%=1300 아님)


def test_lane_reactivate_tag(db):
    """④ 밴드 밖·무클릭·상한 미도달·과거 클릭 이력(정착 clk>0)·24h 정체 → [탐색UP·재가동]."""
    _setup(db, settle_clk=4)  # 정착 clk=4>0 = 과거 클릭 이력(핫셋 미달)
    _prior_step(db, changed_at=NOW - timedelta(days=1))  # 어제 스텝(쿨다운 통과)
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=5.0),  # 밴드 밖·무클릭·flow 0
             _hour(6, imp=20, clk=0, cost=0, avg_rank=5.0),
             _hour(7, imp=20, clk=0, cost=0, avg_rank=5.0)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 1
    p = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert p.rationale.startswith("[탐색UP·재가동]")


# ══════════════════════ 상향 정지/종료(held / capped / not_rank) ══════════════════════

def test_lane_band_reached_holds(db):
    """③ 밴드 도달(rank 3.5)·무클릭 → 상향 정지·관측(제안 생성 없음, explored_held)."""
    _setup(db)
    _prior_step(db, changed_at=NOW - timedelta(days=1))  # 직전 스텝 존재 → ladder 밴드 분기
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=3.5),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=3.5),
             _hour(7, imp=20, clk=0, cost=0, avg_rank=3.5)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 0 and result["explored_held"] == 1
    mock_exec.assert_not_called()


def test_lane_not_rank_diagnosis_end(db):
    """② 과열밴드(rank 2.3)인데 클릭0 지속 → 순위 병리 아님 진단 종료(explored_not_rank)."""
    _setup(db)
    _prior_step(db, changed_at=NOW - timedelta(days=1))
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=2.3),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=2.3),
             _hour(7, imp=20, clk=0, cost=0, avg_rank=2.3)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 0 and result["explored_not_rank"] == 1
    mock_exec.assert_not_called()


def test_lane_click_arrived_holds(db):
    """① 이번 사이클 클릭 발생 → 상향 정지·관측 인계(explored_held, 제안 없음)."""
    _setup(db)
    _prior_step(db, changed_at=NOW - timedelta(days=1))
    curve = [_hour(5, imp=20, clk=1, cost=100, avg_rank=5.0),
             _hour(6, imp=20, clk=1, cost=100, avg_rank=5.0),
             _hour(7, imp=20, clk=0, cost=0, avg_rank=5.0)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 0 and result["explored_held"] == 1
    mock_exec.assert_not_called()


def test_lane_capped_when_ceiling_reached(db):
    """④ 경제성 상한 도달(현 입찰≥상한)·무클릭 → 종료(explored_capped). 낮은 heuristic 상한 유도:
    bep 큰값으로 economic 낮춰 min 상한이 현재입찰 근처 → capped."""
    _setup(db, settle_clk=3, settle_conv=3000, bep_roas="10.0")  # rpc=1000, economic=1000/10=100
    _prior_step(db, changed_at=NOW - timedelta(days=1))
    # ceiling = min(economic 100, heuristic 2000)=100 < current 1000 → 밴드 밖 무클릭 → capped
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=6.0),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=6.0)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 0 and result["explored_capped"] == 1
    mock_exec.assert_not_called()


# ══════════════════════ 라우팅·격리·경계 ══════════════════════

def test_lane_source_ad_updates_single_ad(db):
    """소재-레벨(source='ad') → 소재입찰 1개만(max 실효) update. target_type='ad'·단일 소재."""
    _setup(db)
    eff = {"source": "ad", "effective_bid": 900, "max_ad_id": "ad-max",
           "has_ad_data": True, "group_bid": 1000, "ad_count": 3}
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=6.0),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=6.0)]
    result, mock_exec = _run(db, curve, eff=eff)
    assert result["explored"] == 1
    p = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert p.target_type == "ad" and p.target_id == "ad-max"  # 단일 max 소재만
    assert p.adgroup_id == GRP  # 소재 제안 필수 컨텍스트
    assert p.target_bid == 990  # 900×1.10 blind 첫 스텝


def test_lane_web_site_excluded(db):
    """파워링크(WEB_SITE) 캠페인 → 탐색 후보 0(exploration_candidates가 SHOPPING만)."""
    _setup(db, campaign_type="WEB_SITE")
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=6.0)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 0 and result["explored_held"] == 0
    # WEB_SITE adgroup은 핫셋 grain(키워드)도 아니라 어떤 레인도 타지 않음
    mock_exec.assert_not_called()


def test_lane_hotset_mutually_exclusive(db):
    """핫셋(정착 clk≥10)은 탐색 후보 아님 — 탐색 발사 0(핫셋 경로만)."""
    _setup(db, settle_clk=12)  # clk≥10 = 핫셋
    curve = [_hour(5, imp=0, clk=0, cost=0)]  # 당일 imp 0 → 핫셋 hold, 탐색 후보엔 애초 없음
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 0


def test_lane_cooldown_2h_kst_blocks(db):
    """쿨다운 2h(KST) — 직전 [탐색UP] change_log가 1.5h 전(<2h) → 미발동."""
    _setup(db)
    _prior_step(db, changed_at=NOW - timedelta(hours=1, minutes=30))  # 1.5h < 2h
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=6.0),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=6.0)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 0 and result["explored_held"] == 0
    mock_exec.assert_not_called()


def test_lane_cooldown_2h_kst_passes_after(db):
    """쿨다운 경계: 직전 스텝 2h+ 전 → 발동(같은 KST tz 비교)."""
    _setup(db)
    _prior_step(db, changed_at=NOW - timedelta(hours=2, minutes=1))
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=6.0),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=6.0)]
    result, _mock = _run(db, curve)
    assert result["explored"] == 1


# ── 봉투 파라미터 배선(D-NAO-172 적대 리뷰 P1-2) ────────────────────────────────────────
# ★이 두 테스트가 지키는 것: 탐색 레인의 실효 쿨다운 = **게이트와 같은 파라미터**.
#   안 지키면 DB로 쿨다운을 1h로 «풀어도» 이 레인만 2h를 고수하고, 반대로 6h로 «조이면»
#   레인은 3h에 제안을 만들어 게이트가 전건 되돌려 죽인다(제안 낭비). 어느 쪽이든
#   현황판이 말하는 값 ≠ 실효값 — 이 기능이 막으려던 착시 그 자체다.

def _kv_cooldown(db, hours):
    """봉투 파라미터 KV에 쿨다운만 심는다(콘솔 PUT과 같은 저장 형태)."""
    import json

    from app.models import NaverAccountSettings
    from app.services.naver_ad import guardrail_params
    db.add(NaverAccountSettings(key=guardrail_params.SETTINGS_KEY,
                                value_json=json.dumps({"cooldown_hours": str(hours)})))
    db.commit()


def test_lane_cooldown_follows_db_param_when_tightened(db):
    """DB 쿨다운 6h → 직전 스텝 3h 전이면 **레인이 발동하지 않는다**(상수 2h면 발동했다).

    적대 리뷰 P1-2의 재현 그대로다: 이 배선이 없으면 생성기는 3h에 쏘고 게이트가 6h로 막아
    제안이 전건 죽는다.
    """
    _setup(db)
    _kv_cooldown(db, 6)
    _prior_step(db, changed_at=NOW - timedelta(hours=3))
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=6.0),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=6.0)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 0 and result["explored_held"] == 0
    mock_exec.assert_not_called()


def test_lane_cooldown_follows_db_param_when_loosened(db):
    """DB 쿨다운 1h → 직전 스텝 1.5h 전이면 **레인이 발동한다**(상수 2h면 미발동했다).

    풀기 방향이 현황판 거짓말이 나는 쪽이다 — 화면은 「1시간」이라 말하는데 레인만 2시간을
    고수하면, 「지금 무슨 값으로 돌고 있나」를 보여준다는 현황판의 존재 이유가 깨진다.
    """
    _setup(db)
    _kv_cooldown(db, 1)
    _prior_step(db, changed_at=NOW - timedelta(hours=1, minutes=30))
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=6.0),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=6.0)]
    result, _mock = _run(db, curve)
    assert result["explored"] == 1


# ══════════════════════ 손실고삐 캠페인 제외(봉투#5) ══════════════════════

def test_lane_leash_skips_exploration(db):
    """손실고삐(is_leash DOWN) 발동 캠페인 → 탐색 레인 skip(UP·DOWN 충돌 금지, 봉투#5).
    _judge_hourly가 leash verdict를 내면 campaign_leashed=True → 그 캠페인의 탐색 후보(ag-explore)는
    발사되지 않는다(같은 캠페인 순회 안에서 핫셋 먼저·탐색 나중)."""
    _setup(db)  # 탐색 후보 ag-explore(clk<10)
    # 같은 캠페인 핫셋 그룹(clk≥10) — _judge_hourly를 patch해 leash DOWN을 확정 주입.
    db.add(NaverEntity(entity_type="adgroup", entity_id="ag-hot", parent_id=CAMP,
                       campaign_id=CAMP, campaign_type="SHOPPING", status="on"))
    db.add(NaverAdDaily(ad_date=date(2026, 7, 15), campaign_id=CAMP, campaign_type="SHOPPING",
                        adgroup_id="ag-hot", keyword_id="-", imp=500, clk=20, cost=700,
                        conv_direct_amt=0, conv_indirect_amt=0))  # 핫셋(clk20)
    db.commit()

    def fetch(tid, d):
        if tid == "ag-hot":
            return [_hour(6, imp=40, clk=2, cost=200, avg_rank=3.0, conv_cnt=0)]
        return [_hour(5, imp=20, clk=0, cost=0, avg_rank=6.0)]  # 탐색 후보(leash 없었으면 발사됐을 것)

    def fake_judge(db_, *, target_type, target_id, campaign_id, curve, now):
        return {"direction": "down", "reason": "장중 loss", "leash": True}

    with patch.object(auto_operator, "_judge_hourly", side_effect=fake_judge), \
         patch.object(auto_operator, "_live_current_bid", return_value=1000), \
         patch.object(auto_operator.effective_bid, "adgroup_effective_bid",
                      return_value={"source": "group", "effective_bid": 1000, "max_ad_id": None}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=fetch)

    # 핫셋 leash DOWN은 나가되(approved 1) 탐색은 skip(explored 0).
    assert result["explored"] == 0 and result["approved"] == 1
    saved = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert saved.rationale.startswith("[순위고삐]") and saved.proposal_type == "bid_down"


# ══════════════════════ 롤링 24h 흐름 창(codex P1 자정 리셋) ══════════════════════

def test_lane_rolling_flow_holds_when_yesterday_late_click(db):
    """codex P1: 어제 23시 클릭·오늘 0클릭 → 00:20에 hold(step_up 아님). 자정 리셋 오탐 차단."""
    _setup(db)
    _prior_step(db, changed_at=NOW - timedelta(days=1, hours=2))  # 그저께 스텝(쿨다운 통과·오늘 아님)
    _hourly(db, adgroup_id=GRP, hour=23, clk=2)  # 어제 23시 클릭(롤링 24h 안)
    now0 = datetime(2026, 7, 21, 0, 20, 0)  # 00:20
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.effective_bid, "adgroup_effective_bid",
                      return_value={"source": "group", "effective_bid": 1000, "max_ad_id": None}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=now0, fetch_intraday=lambda tid, d: [])
    assert result["explored"] == 0 and result["explored_held"] == 1
    mock_exec.assert_not_called()


def test_lane_rolling_flow_reactivates_when_truly_24h_idle(db):
    """codex P1 대조: 어제 시간별 있으나 24h 진짜 무클릭(어제 clk 0) → 재가동(정체)."""
    _setup(db, settle_clk=4)
    _prior_step(db, changed_at=NOW - timedelta(days=1))
    _hourly(db, adgroup_id=GRP, hour=23, clk=0)  # 어제 클릭 0
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=5.0),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=5.0)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 1
    p = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert p.rationale.startswith("[탐색UP·재가동]")


# ── codex 신규 P1: 유닛-레벨 flow 교차확인(어제 daily 증거, 날짜-레벨 오독 차단) ──

def test_lane_partial_sweep_failure_holds_via_daily_evidence(db):
    """codex 신규 P1: 부분 스윕 실패 — 타 유닛 어제 시간별 존재·이 그룹 시간별 부재·이 그룹 어제
    daily clk>0 → 날짜-레벨이면 'flow 0' 오독하나, 유닛-레벨 교차확인으로 flow 살아있음 → hold."""
    _setup(db, settle_clk=4, yesterday_daily_clk=5)  # 이 그룹 어제 daily clk=5(클릭 실재)
    _prior_step(db, changed_at=NOW - timedelta(days=1))
    _hourly(db, adgroup_id="other-unit", hour=10, clk=3)  # 타 유닛만 스윕됨(이 그룹 시간별 없음)
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=5.0),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=5.0)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 0 and result["explored_held"] == 1  # 시간별만 누락 → hold
    mock_exec.assert_not_called()


def test_lane_daily_clk_zero_steps_normally(db):
    """codex 신규 P1: 이 그룹 어제 daily clk=0(일별 grain 확정 무클릭)·시간별 부재 → 어제 0 정상
    사용 → 스텝 발사(정체 재가동)."""
    _setup(db, settle_clk=4, yesterday_daily_clk=0)  # 어제 daily clk=0
    _prior_step(db, changed_at=NOW - timedelta(days=1))
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=5.0),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=5.0)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 1  # 확정 무클릭 → 정상 스텝
    p = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert p.rationale.startswith("[탐색UP·재가동]")


def test_lane_dawn_no_daily_yet_fail_toward_hold(db):
    """codex 신규 P1: 새벽(07:30 전) — 어제 daily 미수집(전무)·이 그룹 시간별도 부재 →
    확인 불가 → fail-toward-hold(관측 유지)."""
    _setup(db, settle_clk=4, yesterday_daily=False)  # 어제 daily 미수집
    _prior_step(db, changed_at=NOW - timedelta(days=1))
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=5.0),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=5.0)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 0 and result["explored_held"] == 1  # fail-toward-hold
    mock_exec.assert_not_called()


# ══════════════════════ 기울기 연속성·TOCTOU (codex P1) ══════════════════════

_SLOPE_CURVE = [  # 스텝 前(hour1,2) rank 10 · 스텝 後(hour4,5) rank 8 → 기울기 100원/rank
    _hour(1, imp=20, clk=0, cost=0, avg_rank=10.0),
    _hour(2, imp=20, clk=0, cost=0, avg_rank=10.0),
    _hour(4, imp=20, clk=0, cost=0, avg_rank=8.0),
    _hour(5, imp=20, clk=0, cost=0, avg_rank=8.0),
]


def test_lane_slope_used_on_continuous_step(db):
    """codex P1: 직전 스텝 대상=이번 대상·라이브==after_bid → 기울기 사용. 기울기=Δbid100/Δrank2=
    50원/rank, 현 rank8·목표4 → 필요증분 50×(8-4)=200 → 1200. 보수 10%(1100)와 구별되는 값."""
    _setup(db)
    _prior_step(db, target_type="adgroup", target_id=GRP, before_bid=900, after_bid=1000,
                changed_at=datetime(2026, 7, 21, 3, 30, 0))  # after 1000 == 라이브 1000(연속)
    result, mock_exec = _run(db, _SLOPE_CURVE)
    assert result["explored"] == 1
    p = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert p.target_bid == 1200  # 기울기 50원/rank × (8-4)=200 → 1200 (≠보수 1100)


def test_lane_slope_discarded_on_lever_switch(db):
    """codex P1 대조: 직전 스텝이 소재(ad) 대상인데 이번은 그룹 발사 → 연속성 불일치 → 기울기 폐기·
    보수 10%(=1100). 같은 곡선인데 위 연속 케이스(1300)와 다름 = 기울기 오염 차단 실증."""
    _setup(db)
    _prior_step(db, target_type="ad", target_id="ad-old", before_bid=900, after_bid=1000,
                changed_at=datetime(2026, 7, 21, 3, 30, 0))  # ad 대상(이번 group과 불일치)
    result, mock_exec = _run(db, _SLOPE_CURVE)  # group 발사(step_base=1000)
    assert result["explored"] == 1
    p = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert p.target_bid == 1100 and p.target_type == "adgroup"  # 보수 10%(기울기 폐기)


# ══════════════════════ 스텝 시각 버킷 경계 (codex P2) ══════════════════════

def test_observe_excludes_step_hour_bucket(db):
    """codex P2 단위: _exploration_observe의 since가 step_hour 버킷을 제외(clk_cycle 오염 방지)."""
    _hourly(db, adgroup_id="x", hour=1, clk=0)  # flow_available용 어제 행
    curve = [_hour(3, imp=20, clk=5, cost=100, avg_rank=6.0),   # step_hour 버킷(제외 대상)
             _hour(4, imp=20, clk=0, cost=0, avg_rank=5.0),     # 스텝 後
             _hour(5, imp=20, clk=0, cost=0, avg_rank=5.0)]
    last_step = {"changed_at": datetime(2026, 7, 21, 3, 30, 0), "before_bid": 900,
                 "after_bid": 1000, "target_type": "adgroup", "target_id": GRP}
    obs = auto_operator._exploration_observe(db, GRP, curve, NOW, last_step)
    assert obs["since"]["clk"] == 0  # hour3 클릭 5 제외 → since 무클릭
    assert float(obs["since"]["avg_rank"]) == 5.0  # 스텝 後(hour4,5)만


# ══════════════════════ daily 손실상태 제외(GATE P2-risk6, 가드5 완성) ══════════════════════

def test_lane_daily_bleeding_group_skipped(db):
    """어제 daily 바닥손실(shopping_group_bep) 보드에 오른 그룹 → 오늘 탐색 UP 제외(역전 금지)."""
    _setup(db)
    _retro(db, target_id=GRP, board="shopping_group_bep")  # 우리 그룹이 손실 보드에
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=6.0),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=6.0)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 0
    mock_exec.assert_not_called()
    assert any("daily 손실상태" in h.get("reason", "") for h in result["held"])


def test_lane_daily_stoploss_group_skipped(db):
    """어제 daily 스톱로스/floored(shopping_pause_candidates) 후보 그룹 → 탐색 UP 제외."""
    _setup(db)
    _retro(db, target_id=GRP, board="shopping_pause_candidates", direction="pause")
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=6.0)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 0
    mock_exec.assert_not_called()


def test_lane_non_loss_group_proceeds(db):
    """대조: 손실 보드에 없고 소급채점 신선 → 정상 탐색(_setup의 더미 신선 신호만, 우리 그룹 밖)."""
    _setup(db)
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=6.0),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=6.0)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 1
    mock_exec.assert_called_once()


def test_lane_stale_retro_fail_closed(db):
    """소급채점 stale(latest_asof < today-1) → fail-closed 제외(손실 여부 검증 불가)."""
    # _setup 없이 직접 시드(신선 더미 없음) — stale asof(today-2)만 존재.
    db.add(NaverCampaignSettings(campaign_id=CAMP, auto_operate=True, optimizer="ours"))
    db.add(NaverEntity(entity_type="campaign", entity_id=CAMP, campaign_id=CAMP,
                       campaign_type="SHOPPING", status="on"))
    db.add(NaverEntity(entity_type="adgroup", entity_id=GRP, parent_id=CAMP,
                       campaign_id=CAMP, campaign_type="SHOPPING", status="on"))
    db.add(NaverAdDaily(ad_date=date(2026, 7, 15), campaign_id=CAMP, campaign_type="SHOPPING",
                        adgroup_id=GRP, keyword_id="-", imp=100, clk=3, cost=1000,
                        conv_direct_amt=30000, conv_indirect_amt=0))
    db.add(NaverHourlySnapshot(snapshot_at=NOW, ad_date=TODAY, snapshot_hour=23, campaign_id=CAMP,
                               campaign_type="SHOPPING", cost=0, clk=0, imp=0, daily_budget=100000))
    db.commit()
    _retro(db, target_id="other", asof=date(2026, 7, 19))  # stale(< 기대 7/20)
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=6.0)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 0  # stale → fail-closed 제외
    mock_exec.assert_not_called()


# ══════════════════════ real_write_blocker ceiling 대칭(GATE P2-risk3, 4분면) ══════════════════════

def _blocker_proposal(target_bid, expected_effect):
    return NaverProposal(
        proposal_type="bid_up_explore", target_type="adgroup", target_id=GRP,
        campaign_id=CAMP, adgroup_id=GRP, rationale="탐색", expected_effect=expected_effect,
        status="approved", target_bid=target_bid,
        approval_source=exploration.APPROVAL_SOURCE_EXPLORE,
    )


def test_real_write_blocker_ceiling_four_quadrant():
    """4분면: 마커 부재/오염/초과 → 실행 버튼 비활성(not None) · 정상 → None(쓰기경계 대칭)."""
    # ① 마커 부재
    assert harness.real_write_blocker(_blocker_proposal(1100, "마커 없음")) is not None
    # ② 마커 오염(중복 → decode None)
    corrupt = encode_base_bid(encode_exploration_ceiling(encode_exploration_ceiling("x", 2000), 3000), 800)
    assert harness.real_write_blocker(_blocker_proposal(1100, corrupt)) is not None
    # ③ target_bid > 상한
    b = harness.real_write_blocker(_blocker_proposal(1100, _explore_effect(1000, 800)))
    assert b is not None and "상한 초과" in b
    # ④ 정상(target ≤ 상한·ceiling+base_bid 마커)
    assert harness.real_write_blocker(_blocker_proposal(1000, _explore_effect(1500, 800))) is None


def test_real_write_blocker_base_bid_marker_required():
    """codex P1: ceiling은 있으나 base_bid 마커 부재 → 실행 버튼 비활성(정적 TOCTOU 검증)."""
    b = harness.real_write_blocker(_blocker_proposal(1000, encode_exploration_ceiling("x", 1500)))
    assert b is not None and "base_bid" in b


# ══════════════════════ 쓰기-경계 하드 게이트(P2①, harness) ══════════════════════

def _explore_proposal(db, *, target_bid, expected_effect, target_type="adgroup", target_id=GRP,
                      approval_source=None):
    p = NaverProposal(
        proposal_type="bid_up_explore", target_type=target_type, target_id=target_id,
        campaign_id=CAMP, adgroup_id=GRP, rationale="탐색", expected_effect=expected_effect,
        status="approved", target_bid=target_bid,
        approval_source=approval_source or exploration.APPROVAL_SOURCE_EXPLORE,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _settings_only(db):
    db.add(NaverCampaignSettings(campaign_id=CAMP, auto_operate=True, optimizer="ours"))
    db.commit()


def test_harness_ceiling_gate_blocks_missing_marker(db):
    """P2①: 탐색 스텝 expected_effect에 ceiling 마커 부재 → fail-closed(경제 근거 없이 상향 금지)."""
    _settings_only(db)
    p = _explore_proposal(db, target_bid=1100, expected_effect="마커 없음")
    with patch.object(harness.naver_sa_writer, "update_adgroup_bid") as mag:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)
    mag.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"


def test_harness_ceiling_gate_blocks_target_over_ceiling(db):
    """P2①: target_bid > 마커 상한 → fail-closed(레인 클램프 불신·쓰기경계 재검증)."""
    _settings_only(db)
    p = _explore_proposal(db, target_bid=1100, expected_effect=encode_exploration_ceiling("x", 1000))
    with patch.object(harness.naver_sa_writer, "update_adgroup_bid") as mag:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)
    mag.assert_not_called()


def test_harness_ceiling_gate_passes_within_ceiling(db):
    """P2①: target_bid ≤ 상한 ∧ base_bid==라이브 → 게이트 통과(그 후 guardrail mock None → 실쓰기)."""
    _settings_only(db)
    p = _explore_proposal(db, target_bid=1000, expected_effect=_explore_effect(1500, 800))
    ctx = {"current_bid": 800, "roas_corrected": None, "target_roas": None, "unconverted_spend": None,
           "cost_today": None, "daily_budget": None, "last_change_at": None, "changes_today_count": 0}
    from app.services.naver_ad.naver_sa_writer import WriteResult
    wr = WriteResult(action="update_adgroup_bid", before={"bidAmt": 800}, response=None,
                     after={"bidAmt": 1000}, created_ids=[])
    with patch.object(harness, "_build_guardrail_context", return_value=ctx), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_adgroup_bid", return_value=wr) as mag:
        harness.execute(db, p.id, dry_run=False)
    mag.assert_called_once_with(GRP, 1000)


def test_harness_exploration_toctou_blocks_external_change(db):
    """codex P1 TOCTOU: 제안 base_bid(800) ≠ 실행 시점 라이브(850) → fail-closed 중단(외부 변경)."""
    _settings_only(db)
    p = _explore_proposal(db, target_bid=1000, expected_effect=_explore_effect(1500, 800))
    ctx = {"current_bid": 850, "roas_corrected": None, "target_roas": None, "unconverted_spend": None,
           "cost_today": None, "daily_budget": None, "last_change_at": None, "changes_today_count": 0}
    with patch.object(harness, "_build_guardrail_context", return_value=ctx), \
         patch.object(harness.naver_sa_writer, "update_adgroup_bid") as mag:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)
    mag.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"


def test_harness_exploration_missing_base_bid_marker_blocks(db):
    """codex P1: ceiling만 있고 base_bid 마커 부재 → 실행 시점 fail-closed."""
    _settings_only(db)
    p = _explore_proposal(db, target_bid=1000, expected_effect=encode_exploration_ceiling("x", 1500))
    ctx = {"current_bid": 800, "roas_corrected": None, "target_roas": None, "unconverted_spend": None,
           "cost_today": None, "daily_budget": None, "last_change_at": None, "changes_today_count": 0}
    with patch.object(harness, "_build_guardrail_context", return_value=ctx), \
         patch.object(harness.naver_sa_writer, "update_adgroup_bid") as mag:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)
    mag.assert_not_called()


# ══════════════════════ VT3(D-NAO-82②) CTR 경보 → 래더 skip 게이트 ══════════════════════

def _ctr_alert_daily(db, *, adgroup_id=GRP, campaign_id=CAMP, imp=300, clk=0, rank=3.0):
    """ctr_alert의 W3 창(D0-2..D0, D0=NOW.date()-1)에 들어가는 naver_ad_daily 3일 시드 —
    누적 imp≥200·clk=0·avg_rank≤4.0(밴드 내) → W1·W3 둘 다 발화(스코프 확인은 skip 쪽 관심사
    아님, 발화 자체만 필요)."""
    d0 = NOW.date() - timedelta(days=1)
    for k in range(3):
        db.add(NaverAdDaily(
            ad_date=d0 - timedelta(days=k), campaign_id=campaign_id, campaign_type="SHOPPING",
            adgroup_id=adgroup_id, keyword_id="", imp=imp, clk=clk, cost=0,
            rank_sum=round(rank * imp),
        ))
    db.commit()


def test_ctr_alert_active_group_skips_ladder(db):
    """VT3: 그룹이 CTR 경보 활성이면 탐색 후보 순회에서 즉시 skip — 발사 0·held에
    '[탐색] CTR경보' 기록·diary blocked 1건(사유에 'CTR경보 — 소재 처방 대상, 추가 UP 무의미')."""
    from app.models import OpsDiaryEntry

    _setup(db)
    _ctr_alert_daily(db)  # W1·W3 경보 발화(입찰로 못 푸는 소재 CTR 문제)
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=6.0),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=6.0),
             _hour(7, imp=20, clk=0, cost=0, avg_rank=6.0)]
    result, mock_exec = _run(db, curve)

    assert result["explored"] == 0
    mock_exec.assert_not_called()
    assert any("CTR경보" in h["reason"] for h in result["held"])
    blocked = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.event_type == "blocked", OpsDiaryEntry.adgroup_id == GRP,
    ).all()
    assert any("CTR경보" in (b.rationale or "") for b in blocked)


def test_ctr_alert_inactive_group_proceeds_normally(db):
    """CTR 경보 없음(정상 그룹, naver_ad_daily 추가 시드 없음) → 게이트 미적용, 기존 탐색
    발사 동작 불변(회귀 0 — test_lane_step_up_below_band_fires와 동일 조건)."""
    _setup(db)
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=6.0),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=6.0),
             _hour(7, imp=20, clk=0, cost=0, avg_rank=6.0)]
    result, mock_exec = _run(db, curve)
    assert result["explored"] == 1
    mock_exec.assert_called_once()


def test_ctr_alert_lookup_failure_rests_campaign_lane(db):
    """codex 1R P1-2: ctr_alert.detect_ctr_alerts 예외 시 fail-open(게이트 없이 발사)이 아니라
    캠페인 탐색 레인 보류(fail-soft 전파) — 예외가 _run_exploration_for_campaign 밖으로 나가
    run_hourly_lane의 캠페인 try/except가 잡고 그 캠페인 탐색은 이번 런 실쓰기 0(안전 방향).
    핫셋/시간당 본작업은 오염되지 않는다(run_hourly_lane이 정상 반환)."""
    _setup(db)
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=6.0),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=6.0),
             _hour(7, imp=20, clk=0, cost=0, avg_rank=6.0)]
    with patch.object(auto_operator.ctr_alert, "detect_ctr_alerts", side_effect=RuntimeError("판정 실패")):
        result, mock_exec = _run(db, curve)
    assert result["explored"] == 0  # 경보 산출 실패 → 캠페인 탐색 레인 보류(발사 0)
    mock_exec.assert_not_called()   # 실쓰기 0(안전 방향)


# ══════════════════════ 관측 침묵 분기 저소음 기록(D-NAO-85 관측 갭②) ══════════════════════

def test_lane_stop_observe_records_low_noise_observe(db):
    """stop_observe(밴드 도달·무클릭) 분기가 그동안 diary 무기록이던 것 → observe 1행 기록.
    17프로 그룹이 10회 연속 무기록되던 침묵 분기 해소(라이브 원인 규명에 표적 시뮬 불필요화)."""
    from app.models import OpsDiaryEntry
    from app.services.naver_ad import diary

    _setup(db)
    _prior_step(db, changed_at=NOW - timedelta(days=1))  # 직전 스텝 존재 → 밴드 분기(stop_observe)
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=3.5),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=3.5),
             _hour(7, imp=20, clk=0, cost=0, avg_rank=3.5)]
    result, _ = _run(db, curve)
    assert result["explored_held"] == 1

    obs = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.event_type == "observe", OpsDiaryEntry.adgroup_id == GRP,
    ).all()
    assert len(obs) == 1
    assert obs[0].action == diary.OBSERVE_STOP
    assert obs[0].actor == diary.ACTOR_EXPLORE
    assert "밴드 도달" in (obs[0].rationale or "")


def test_lane_stop_observe_deduped_across_repeated_runs(db):
    """같은 stop_observe 상태로 레인이 반복 발동해도 observe 행은 1개만(매시 소음 폭주 방지).
    상태가 지속되는 한 dedup — 침묵으로 되돌아가지 않으면서도 하루 수십 행 소음도 안 만든다."""
    from app.models import OpsDiaryEntry
    from app.services.naver_ad import diary

    _setup(db)
    _prior_step(db, changed_at=NOW - timedelta(days=1))
    curve = [_hour(5, imp=20, clk=0, cost=0, avg_rank=3.5),
             _hour(6, imp=20, clk=0, cost=0, avg_rank=3.5),
             _hour(7, imp=20, clk=0, cost=0, avg_rank=3.5)]
    for _ in range(4):  # 4시간 연속 stop_observe 재발동
        _run(db, curve)

    obs = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.event_type == "observe", OpsDiaryEntry.adgroup_id == GRP,
        OpsDiaryEntry.action == diary.OBSERVE_STOP,
    ).all()
    assert len(obs) == 1  # 4회 발동 → 1행(상태 지속 dedup)
