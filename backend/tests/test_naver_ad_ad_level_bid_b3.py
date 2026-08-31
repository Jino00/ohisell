# test_naver_ad_ad_level_bid_b3.py — 스프린트 B Phase 3(B3: 소재-레벨 입찰 제어 개방·카나리, D-NAO-65)
# 커버:
#   (1) naver_sa_writer.update_ad_bid — PUT /ncc/ads?fields=adAttr(bidAmt만·useGroupBidAmt 불변)·
#       useGroupBidAmt=true/불명 거부·부모 ML 거부·404 fail-closed·재조회 검증(bidAmt·ugba 이중)
#   (2) harness _execute_update_bid 'ad' 분기 — 가드레일 통과 후만 실쓰기·킬스위치·change_log
#       entity_type='ad'·up BEP 컨텍스트 완전성 fail-closed·real_write_blocker
#   (3) auto_operator 시간당 레인 라우팅 — 카나리 미연결→ad 제안 / 비카나리→hold / 연결→그룹경로
#   (4) proposal_writer 라우팅 — 밴드/스톱로스 미연결 카나리→ad 제안 / 비카나리→기존(skip/대기)
#   (5) 카나리 빈 집합 기본값 = 전면 hold(현행 동작 보존)
#   (6) account_diagnosis 미연결 창이 ad change_log 인식(B3 이월)
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAgencyOp,
    NaverAdDaily,
    NaverAdgroupProduct,
    NaverCampaignSettings,
    NaverChangeLog,
    NaverEntity,
    NaverHourlySnapshot,
    NaverProposal,
)
from app.services.naver_ad import account_diagnosis as diag
from app.services.naver_ad import auto_operator
from app.services.naver_ad import exploration
from app.services.naver_ad import naver_execution_harness as harness
from app.services.naver_ad import naver_sa_writer as writer
from app.services.naver_ad import proposal_writer
from app.services.naver_ad.naver_sa_writer import (
    WriteError,
    WriteResult,
    WriteValidationError,
    WriteVerificationError,
)


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


# ══════════════════════════════════════════════════════════════════
# (1) naver_sa_writer.update_ad_bid — 소재 bidAmt 직접 수정 어댑터
# ══════════════════════════════════════════════════════════════════
class FakeResp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text}")


AD_ID = "nad-000000000123"
GRP_ID = "grp-000000000001"


def _ad_resp(bid_amt=800, ugba=False, adgroup_id=GRP_ID, ad_id=AD_ID, has_attr=True):
    body = {"nccAdId": ad_id, "nccAdgroupId": adgroup_id, "type": "SHOPPING_PRODUCT_AD", "userLock": False}
    if has_attr:
        body["adAttr"] = json.dumps({"bidAmt": bid_amt, "useGroupBidAmt": ugba})
    return FakeResp(200, body)


def _manual_parent(system_bidding_type="NONE", is_autobid_active=False):
    return FakeResp(200, {
        "nccAdgroupId": GRP_ID, "adgroupType": "SHOPPING", "systemBiddingType": system_bidding_type,
        "autobidStrategy": {"isAutobidActive": is_autobid_active},
    })


def test_update_ad_bid_success_roundtrip():
    """정상: bidAmt만 변경(800→680), useGroupBidAmt=false 불변. before/after 재조회 검증."""
    before, parent, after = _ad_resp(800), _manual_parent(), _ad_resp(680)
    put_resp = FakeResp(200, after.json())
    with patch.object(writer.fetcher, "_get", side_effect=[before, parent, after]) as mg, \
         patch.object(writer.requests, "put", return_value=put_resp) as mp:
        r = writer.update_ad_bid(AD_ID, 680)
    assert isinstance(r, WriteResult)
    assert r.action == "update_ad_bid"
    assert r.before == before.json()
    assert r.after == after.json()
    assert r.created_ids == []
    assert mg.call_count == 3  # before ad + parent adgroup + after ad
    assert mp.call_count == 1


def test_update_ad_bid_body_is_full_object_adattr_dict_ugba_false():
    """body = GET 원본 **전체 객체**(type 포함, update_adgroup_bid 동형) + adAttr만 JSON **객체**
    {"bidAmt":N,"useGroupBidAmt":false}로 교체 + fields=adAttr. 라이브 실측(2026-07-21):
    문자열 adAttr·최소 body({nccAdId,nccAdgroupId,adAttr}) 둘 다 400 code 3830, 전체 객체만 200."""
    before, parent, after = _ad_resp(800), _manual_parent(), _ad_resp(680)
    put_resp = FakeResp(200, after.json())
    with patch.object(writer.fetcher, "_get", side_effect=[before, parent, after]), \
         patch.object(writer.requests, "put", return_value=put_resp) as mp:
        writer.update_ad_bid(AD_ID, 680)
    _, kwargs = mp.call_args
    assert kwargs["params"] == {"fields": "adAttr"}
    assert kwargs["json"]["nccAdId"] == AD_ID
    assert kwargs["json"]["nccAdgroupId"] == GRP_ID  # 전체 객체라 before의 그룹 id 포함
    assert kwargs["json"]["type"] == "SHOPPING_PRODUCT_AD"  # 전체 객체 마커(type 없으면 400 3830)
    assert kwargs["json"]["userLock"] is False  # before의 여타 필드 그대로 동반 전송
    ad_attr = kwargs["json"]["adAttr"]
    assert isinstance(ad_attr, dict)  # JSON 객체(문자열 아님)
    assert ad_attr["bidAmt"] == 680
    assert ad_attr["useGroupBidAmt"] is False


def test_update_ad_bid_body_preserves_other_adattr_subfields():
    """before adAttr의 기타 서브필드(bidAmt/useGroupBidAmt 외)는 병합 보존된 채 bidAmt만 갱신."""
    before = FakeResp(200, {
        "nccAdId": AD_ID, "nccAdgroupId": GRP_ID, "type": "SHOPPING_PRODUCT_AD",
        "adAttr": json.dumps({"bidAmt": 800, "useGroupBidAmt": False, "someOtherField": "keep-me"}),
    })
    parent, after = _manual_parent(), _ad_resp(680)
    put_resp = FakeResp(200, after.json())
    with patch.object(writer.fetcher, "_get", side_effect=[before, parent, after]), \
         patch.object(writer.requests, "put", return_value=put_resp) as mp:
        writer.update_ad_bid(AD_ID, 680)
    ad_attr = mp.call_args.kwargs["json"]["adAttr"]
    assert ad_attr["bidAmt"] == 680
    assert ad_attr["useGroupBidAmt"] is False
    assert ad_attr["someOtherField"] == "keep-me"  # 기타 서브필드 보존


def test_update_ad_bid_rejects_use_group_bid_amt_true(db=None):
    """useGroupBidAmt=true 소재는 그룹입찰이 실효 — 개별 bidAmt 수정 무의미 → fail-closed 거부(PUT 없음)."""
    with patch.object(writer.fetcher, "_get", side_effect=[_ad_resp(800, ugba=True)]) as mg, \
         patch.object(writer.requests, "put") as mp:
        with pytest.raises(WriteValidationError):
            writer.update_ad_bid(AD_ID, 680)
    mp.assert_not_called()
    assert mg.call_count == 1  # before만(parent·put 없음)


def test_update_ad_bid_rejects_ugba_missing_fail_closed():
    """adAttr 부재(파싱 None) → useGroupBidAmt 불명 = fail-closed 거부(추정 금지)."""
    with patch.object(writer.fetcher, "_get", side_effect=[_ad_resp(has_attr=False)]), \
         patch.object(writer.requests, "put") as mp:
        with pytest.raises(WriteValidationError):
            writer.update_ad_bid(AD_ID, 680)
    mp.assert_not_called()


def test_update_ad_bid_rejects_parent_ml_autobid():
    """부모 그룹이 ML 자동입찰이면 소재 bidAmt도 무시 → fail-closed 거부(PUT 없음)."""
    with patch.object(writer.fetcher, "_get",
                      side_effect=[_ad_resp(800), _manual_parent(system_bidding_type="ML")]) as mg, \
         patch.object(writer.requests, "put") as mp:
        with pytest.raises(WriteValidationError):
            writer.update_ad_bid(AD_ID, 680)
    mp.assert_not_called()
    assert mg.call_count == 2  # before ad + parent(ML) — put 없음


def test_update_ad_bid_rejects_parent_autobid_active():
    """부모 isAutobidActive=True → 차단(explicit False만 수동 인정)."""
    with patch.object(writer.fetcher, "_get",
                      side_effect=[_ad_resp(800), _manual_parent(is_autobid_active=True)]), \
         patch.object(writer.requests, "put") as mp:
        with pytest.raises(WriteValidationError):
            writer.update_ad_bid(AD_ID, 680)
    mp.assert_not_called()


def test_update_ad_bid_put_4xx_raises_write_error_no_after_refetch():
    with patch.object(writer.fetcher, "_get", side_effect=[_ad_resp(800), _manual_parent()]) as mg, \
         patch.object(writer.requests, "put", return_value=FakeResp(404, {"message": "not found"})):
        with pytest.raises(WriteError):
            writer.update_ad_bid(AD_ID, 680)
    assert mg.call_count == 2  # before + parent만, after 재조회 없음


def test_update_ad_bid_verification_mismatch_raises():
    """PUT 2xx인데 재조회 bidAmt 미반영 → WriteVerificationError(fail-closed)."""
    before, parent, after = _ad_resp(800), _manual_parent(), _ad_resp(800)  # 미반영
    with patch.object(writer.fetcher, "_get", side_effect=[before, parent, after]), \
         patch.object(writer.requests, "put", return_value=FakeResp(200, _ad_resp(680).json())):
        with pytest.raises(WriteVerificationError):
            writer.update_ad_bid(AD_ID, 680)


def test_update_ad_bid_ugba_flipped_true_raises_verification():
    """bidAmt는 반영됐으나 useGroupBidAmt가 true로 전환됨(강제 전환 감지) → fail-closed."""
    before, parent, after = _ad_resp(800), _manual_parent(), _ad_resp(680, ugba=True)
    with patch.object(writer.fetcher, "_get", side_effect=[before, parent, after]), \
         patch.object(writer.requests, "put", return_value=FakeResp(200, after.json())):
        with pytest.raises(WriteVerificationError):
            writer.update_ad_bid(AD_ID, 680)


# VT4 P1-1: ad grain 하한 = 50원 → 40원은 여전히 차단(50 미만), 685=10원 단위 아님, 100_010=상한 초과.
@pytest.mark.parametrize("bad_bid", [40, 100_010, 685])
def test_update_ad_bid_invalid_bid_no_http(bad_bid):
    with patch.object(writer.fetcher, "_get") as mg, patch.object(writer.requests, "put") as mp:
        with pytest.raises(WriteValidationError):
            writer.update_ad_bid(AD_ID, bad_bid)
    mg.assert_not_called()
    mp.assert_not_called()


def test_update_ad_bid_50_passes_min_bid():
    """VT4 P1-1: ad grain 하한 50원 — 50원 발사가 검증 통과(prod 쇼핑검색 50원 유효입찰)."""
    before, parent, after = _ad_resp(800), _manual_parent(), _ad_resp(50)
    put_resp = FakeResp(200, after.json())
    with patch.object(writer.fetcher, "_get", side_effect=[before, parent, after]), \
         patch.object(writer.requests, "put", return_value=put_resp) as mp:
        r = writer.update_ad_bid(AD_ID, 50)
    assert r.action == "update_ad_bid"
    assert mp.call_count == 1


def test_get_ad_bid_parses_adattr():
    with patch.object(writer.fetcher, "_get", return_value=_ad_resp(1990)):
        assert writer.get_ad_bid(AD_ID) == 1990


# ══════════════════════════════════════════════════════════════════
# (2) harness _execute_update_bid — 'ad' 분기
# ══════════════════════════════════════════════════════════════════
def _ad_proposal(db, *, proposal_type="bid_down", target_bid=680, adgroup_id=GRP_ID,
                 campaign_id="cmp1", status="approved", approval_source=None, ceiling=100_000):
    # BX3(P2①·codex P1): 탐색 스텝은 ceiling 마커 + base_bid 마커(TOCTOU, step_base=800=ctx current)를
    # 요구(성공 경로는 넉넉한 ceiling·일치 base_bid).
    from app.services.naver_ad.bid_step_types import encode_base_bid, encode_exploration_ceiling
    if proposal_type == "bid_up_explore":
        effect = encode_base_bid(encode_exploration_ceiling("테스트", ceiling), 800)
    else:
        effect = "테스트"
    p = NaverProposal(
        proposal_type=proposal_type, target_type="ad", target_id=AD_ID,
        campaign_id=campaign_id, adgroup_id=adgroup_id, rationale="테스트", expected_effect=effect,
        status=status, target_bid=target_bid, approval_source=approval_source,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _settings(db, campaign_id="cmp1", optimizer="ours", auto_operate=False):
    db.add(NaverCampaignSettings(campaign_id=campaign_id, optimizer=optimizer, auto_operate=auto_operate))
    db.commit()


def _ad_write_result(before_bid=800, after_bid=680):
    return WriteResult(
        action="update_ad_bid",
        before={"nccAdId": AD_ID, "adAttr": json.dumps({"bidAmt": before_bid, "useGroupBidAmt": False})},
        response=None,
        after={"nccAdId": AD_ID, "adAttr": json.dumps({"bidAmt": after_bid, "useGroupBidAmt": False})},
        created_ids=[],
    )


def test_harness_ad_bid_down_dispatches_to_update_ad_bid(db):
    """target_type='ad' + bid_down → update_ad_bid로 디스패치(update_adgroup/keyword 아님).
    change_log entity_type='ad'·action='update_bid'·before/after 기록."""
    p = _ad_proposal(db, proposal_type="bid_down", target_bid=680)
    _settings(db, optimizer="ours")
    with patch.object(auto_operator, "AD_BID_ROUTING_FALLBACK_CAMPAIGNS", frozenset({"cmp1"})), \
         patch.object(harness, "_build_guardrail_context", return_value={"current_bid": 800}), \
         patch.object(harness.guardrail_gate, "check", return_value=None) as mgate, \
         patch.object(harness.naver_sa_writer, "update_ad_bid", return_value=_ad_write_result()) as mad, \
         patch.object(harness.naver_sa_writer, "update_adgroup_bid") as magp, \
         patch.object(harness.naver_sa_writer, "update_keyword_bid") as mkw:
        log_entry = harness.execute(db, p.id, dry_run=False)
    mgate.assert_called_once()
    assert mgate.call_args[0][0] == {"proposal_type": "bid_down", "target_bid": 680, "target_lock": None}
    mad.assert_called_once_with(AD_ID, 680, expected_before_bid=800)  # CAS 기준가 전달
    magp.assert_not_called()
    mkw.assert_not_called()
    assert log_entry.action == "update_bid"
    assert log_entry.entity_type == "ad"
    assert log_entry.entity_id == AD_ID
    assert json.loads(log_entry.after_value)["adAttr"]
    db.refresh(p)
    assert p.executed_change_log_id == log_entry.id


def test_harness_ad_bid_down_guardrail_block_no_write(db):
    """가드레일 차단(쿨다운 등) → update_ad_bid 미호출·failed·[실행 불가] 감사(우회 금지)."""
    p = _ad_proposal(db, proposal_type="bid_down", target_bid=680)
    _settings(db, optimizer="ours")
    with patch.object(auto_operator, "AD_BID_ROUTING_FALLBACK_CAMPAIGNS", frozenset({"cmp1"})), \
         patch.object(harness, "_build_guardrail_context", return_value={"current_bid": 800}), \
         patch.object(harness.guardrail_gate, "check", return_value="쿨다운 중"), \
         patch.object(harness.naver_sa_writer, "update_ad_bid") as mad:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)
    mad.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"


def test_harness_ad_kill_switch_blocks_write(db):
    """approval_source=auto_op_hr + auto_operate OFF → KillSwitchEngagedError, 쓰기 없음(3중 방어)."""
    _settings(db, optimizer="ours", auto_operate=False)
    p = _ad_proposal(db, proposal_type="bid_down", approval_source=auto_operator.APPROVAL_SOURCE_HOURLY)
    with patch.object(harness.naver_sa_writer, "update_ad_bid") as mad:
        with pytest.raises(harness.KillSwitchEngagedError):
            harness.execute(db, p.id, dry_run=False)
    mad.assert_not_called()


def test_harness_ad_bid_up_blocked_when_bep_context_incomplete(db):
    """ad 증액(bid_up)도 adgroup처럼 roas_corrected/target_roas 완전성 요구 — 불완전 시
    guardrail_gate 호출 전 fail-closed(D-NAO-1 이익하한 우회 방지)."""
    p = _ad_proposal(db, proposal_type="bid_up", target_bid=920)
    _settings(db, optimizer="ours")
    # 카나리 2단계(up 개방) 가정 — 방향 게이트를 열어야 BEP 완전성 가드 자체를 검증 가능
    # (codex 소급[P2] 최종 경계 가드가 방향을 먼저 막으면 이 가드에 도달 못 함).
    with patch.object(auto_operator, "AD_BID_ROUTING_FALLBACK_CAMPAIGNS", frozenset({"cmp1"})), \
         patch.object(auto_operator, "_AD_BID_CANARY_PROPOSAL_TYPES", frozenset({"bid_down", "bid_up"})), \
         patch.object(harness, "_build_guardrail_context",
                      return_value={"current_bid": 800, "roas_corrected": None, "target_roas": 2.0}), \
         patch.object(harness.guardrail_gate, "check") as mgate, \
         patch.object(harness.naver_sa_writer, "update_ad_bid") as mad:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)
    mgate.assert_not_called()
    mad.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"


def test_harness_ad_bid_up_explore_success_when_gates_pass(db):
    """BX2(D-NAO-70·71): 소재 UP은 explore_op + bid_up_explore 탐색 경로로만 실쓰기 성공(전 캠페인·
    카나리 무관). ★ctx의 BEP 원료가 전부 None인데도 성공 = 탐색이 표본-기반 BEP 완전성 게이트에서
    면제됨을 고정(콜드 그룹은 표본 없음 — 비탐색 UP이면 여기서 fail-closed 됐을 것)."""
    p = _ad_proposal(db, proposal_type="bid_up_explore", target_bid=920,
                     approval_source=exploration.APPROVAL_SOURCE_EXPLORE)
    _settings(db, optimizer="ours", auto_operate=True)  # explore_op = 킬스위치 화이트리스트
    ctx = {"current_bid": 800, "roas_corrected": None, "target_roas": None, "unconverted_spend": None,
           "cost_today": None, "daily_budget": None, "last_change_at": None, "changes_today_count": 0}
    with patch.object(harness, "_build_guardrail_context", return_value=ctx), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_ad_bid",
                      return_value=_ad_write_result(800, 920)) as mad:
        log_entry = harness.execute(db, p.id, dry_run=False)
    mad.assert_called_once_with(AD_ID, 920, expected_before_bid=800)  # CAS 기준가 전달
    assert log_entry.entity_type == "ad"


def test_real_write_blocker_ad_executable(db):
    p = _ad_proposal(db, proposal_type="bid_down", target_bid=680)
    with patch.object(auto_operator, "AD_BID_ROUTING_FALLBACK_CAMPAIGNS", frozenset({"cmp1"})):
        assert harness.real_write_blocker(p) is None


def test_real_write_blocker_ad_no_target_bid(db):
    p = _ad_proposal(db, proposal_type="bid_down", target_bid=None)
    assert "target_bid" in (harness.real_write_blocker(p) or "")


# ══════════════════════════════════════════════════════════════════
# (2b) codex 소급[P2] 2026-07-20 — 최종 쓰기 경계 가드(카나리·방향·adgroup_id)
# ══════════════════════════════════════════════════════════════════
def test_harness_ad_bid_down_executes_all_campaigns_canary_lifted(db):
    """BX2(D-NAO-70②): 소재 bid_down은 카나리 1호(맥세이프) 제한 해제 — 전 캠페인 Confirm 실쓰기.
    (B3에선 비카나리 캠페인이 최종 경계에서 fail-closed 차단됐으나 D-NAO-70으로 전 캠페인 개방.)"""
    p = _ad_proposal(db, proposal_type="bid_down", target_bid=680)  # cmp1 = 과거 비카나리
    _settings(db, optimizer="ours")
    with patch.object(harness, "_build_guardrail_context", return_value={"current_bid": 800}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_ad_bid",
                      return_value=_ad_write_result(800, 680)) as mad:
        log_entry = harness.execute(db, p.id, dry_run=False)
    mad.assert_called_once_with(AD_ID, 680, expected_before_bid=800)  # CAS 기준가 전달
    db.refresh(p)
    assert p.status == "approved" and p.executed_change_log_id == log_entry.id


def test_harness_ad_blocks_undirectional_bid_up(db):
    """카나리 캠페인이어도 ad bid_up은 1단계 미개방 방향 → 최종 경계 차단(개방은 상수 확장)."""
    p = _ad_proposal(db, proposal_type="bid_up", target_bid=920)
    _settings(db, optimizer="ours")
    with patch.object(auto_operator, "AD_BID_ROUTING_FALLBACK_CAMPAIGNS", frozenset({"cmp1"})), \
         patch.object(harness.naver_sa_writer, "update_ad_bid") as mad:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)
    mad.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"


def test_harness_ad_blocks_missing_adgroup_id(db):
    p = _ad_proposal(db, proposal_type="bid_down", target_bid=680, adgroup_id=None)
    _settings(db, optimizer="ours")
    with patch.object(auto_operator, "AD_BID_ROUTING_FALLBACK_CAMPAIGNS", frozenset({"cmp1"})), \
         patch.object(harness.naver_sa_writer, "update_ad_bid") as mad:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)
    mad.assert_not_called()


def test_real_write_blocker_ad_bid_down_executable_all_campaigns(db):
    """BX2(D-NAO-70②): 카나리 제한 해제 — 소재 bid_down은 전 캠페인에서 실행 버튼 활성(None)."""
    p = _ad_proposal(db, proposal_type="bid_down", target_bid=680)  # cmp1 = 과거 비카나리
    assert harness.real_write_blocker(p) is None


def test_real_write_blocker_matches_executor_for_ad_up(db):
    """★D-NAO-129 codex 적대[P2]: 콘솔 판정과 executor 판정은 **같아야** 한다.

    종전엔 이 함수만 explore/cold 화이트리스트에 머물러, 사람이 승인한 소재 bid_up이 콘솔에선
    "실행 불가"인데 내부 harness.execute()로는 나가는 이중 계약이 생겼다. 쓰기 경계는 하나다.
    """
    open_up = _ad_proposal(db, proposal_type="bid_up", target_bid=920)  # approval_source=None(콘솔)
    assert harness.real_write_blocker(open_up) is None  # 개방 타입 = 콘솔에서도 실행 가능
    # 면제 타입은 종전대로 자기 승인원에서만(잠금 불변).
    exempt = _ad_proposal(db, proposal_type="bid_up_cold", target_bid=5000)
    blocked = harness.real_write_blocker(exempt)
    assert blocked is not None and "explore_op" in blocked


def test_build_guardrail_context_ad_current_bid_from_live_ad(db):
    """ad 컨텍스트의 current_bid = 라이브 소재 bidAmt(get_ad_bid) — 쿨다운 시계는 ad entity_id."""
    _settings(db, optimizer="ours")
    p = _ad_proposal(db, proposal_type="bid_down", target_bid=680)
    with patch.object(harness.naver_sa_writer, "get_ad_bid", return_value=800):
        ctx = harness._build_guardrail_context(db, p, datetime(2026, 7, 20, 9, 0))
    assert ctx["current_bid"] == 800
    assert ctx["last_change_at"] is None


# ══════════════════════════════════════════════════════════════════
# (3) auto_operator 시간당 레인 — B3 라우팅(카나리)
# ══════════════════════════════════════════════════════════════════
CAMP = "cmp-shop"
TODAY = date(2026, 7, 20)
NOW = datetime(2026, 7, 20, 8, 50, 0)


@pytest.fixture(autouse=True)
def _stub_live_ads(db):
    """★D-NAO-171: auto_operator가 집행 대상을 **라이브 `get_ads` 1콜**로 잡는다(종전엔 max 소재
    하나를 `get_ad_bid`로 재조회). 이 픽스처는 그 라이브 응답을 **DB 시드와 같은 값**으로 스텁한다.

    왜 DB에서 파생하나: 값을 테스트마다 두 번(시드 + 스텁) 적으면 둘이 갈라졌을 때 테스트가
    조용히 무의미해진다. 라이브와 DB가 **일부러** 어긋나는 상황(D-NAO-170의 시험 대상)은
    개별 테스트에서 `patch.object(auto_operator, "get_ads", ...)`로 덮어써 명시한다.
    """
    def _fake_get_ads(adgroup_id):
        rows = (db.query(NaverAdgroupProduct)
                  .filter(NaverAdgroupProduct.adgroup_id == adgroup_id).all())
        return [{
            "ad_id": r.ad_id, "ad_bid_amt": r.ad_bid_amt,
            "use_group_bid_amt": r.use_group_bid_amt,
            "ad_user_lock": bool(r.ad_user_lock),
        } for r in rows]
    with patch.object(auto_operator, "get_ads", side_effect=_fake_get_ads):
        yield


def _ad(db, adgroup_id, mall_product_id, *, ad_id, ad_bid_amt, use_group, user_lock=False,
        campaign_id=CAMP):
    db.add(NaverAdgroupProduct(
        adgroup_id=adgroup_id, campaign_id=campaign_id, mall_product_id=mall_product_id,
        product_name=mall_product_id, ad_id=ad_id, ad_bid_amt=ad_bid_amt,
        use_group_bid_amt=use_group, ad_user_lock=user_lock,
    ))


def _seed_hourly_shopping(db, *, adgroup_id="grp-hot"):
    db.add(NaverCampaignSettings(campaign_id=CAMP, optimizer="ours", auto_operate=True))
    db.add(NaverEntity(entity_type="campaign", entity_id=CAMP, campaign_id=CAMP, status="on"))
    db.add(NaverEntity(entity_type="adgroup", entity_id=adgroup_id, parent_id=CAMP,
                       campaign_id=CAMP, campaign_type="SHOPPING", status="on"))
    db.add(NaverHourlySnapshot(snapshot_at=NOW, ad_date=TODAY, snapshot_hour=23,
                               campaign_id=CAMP, campaign_type="", cost=0, clk=0, imp=0))
    window_from, _ = auto_operator._settlement_window(TODAY)
    db.add(NaverAdDaily(ad_date=window_from, campaign_id=CAMP, campaign_type="SHOPPING",
                        adgroup_id=adgroup_id, keyword_id="", imp=200, clk=20, cost=2000))
    db.commit()


def _overheat_curve():
    # ★D-NAO-66: 과열밴드 DOWN(rank<2.5) 폐지 → CPC 급등으로 DOWN 유발(baseline CPC 100원 =
    # cost2000/clk20 → 당일 CPC 250원 > 100×2). DOWN 방향 소재-레벨 라우팅이 관심사(트리거 무관).
    return [
        {"hour": 6, "imp": 15, "clk": 2, "cost": 500, "avg_rank": 2.0},
        {"hour": 7, "imp": 15, "clk": 2, "cost": 500, "avg_rank": 2.0},
        {"hour": 8, "imp": 15, "clk": 2, "cost": 500, "avg_rank": 2.0},
    ]


def test_hourly_lane_canary_routes_disconnected_to_ad_pending_only(db):
    """[GATE P2-2 → D-NAO-125 supersede] 카나리 + 미연결 그룹 밴드 DOWN → ad-레벨 bid_down
    제안은 이제 **레인이 인라인 자동 실행**한다(D-NAO-125, 2026-07-29 — 종전 전면 Confirm-only
    규칙의 유일한 해제 방향, _AD_AUTO_EXEC_PROPOSAL_TYPES={"bid_down"}). 종전 pending-only
    회귀는 이 시나리오에서 더 이상 참이 아니다(하향은 어차피 카나리 상수와 무관하게 열림 —
    test_canary_default_empty_set·(c) 회귀 참조). target_type='ad'·target_id=max_ad_id·
    adgroup_id=그룹·target_bid=소재 스텝(800→680)."""
    _seed_hourly_shopping(db)
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)
    db.commit()
    with patch.object(auto_operator, "AD_BID_ROUTING_FALLBACK_CAMPAIGNS", frozenset({CAMP})), \
         patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_sa_writer, "get_ad_bid", return_value=800), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    mock_exec.assert_called_once()  # D-NAO-125: 하향은 인라인 실행(Confirm-only 아님)
    assert result["approved"] == 1
    assert result["ad_confirm_pending"] == 0
    saved = db.query(NaverProposal).filter(NaverProposal.target_type == "ad").one()
    assert saved.status == "approved"                                  # 자동 승인(D-NAO-125)
    assert saved.approval_source == auto_operator.APPROVAL_SOURCE_HOURLY  # 시간당 레인 소속
    assert saved.target_id == "nad-1"
    assert saved.adgroup_id == "grp-hot"
    assert saved.proposal_type == "bid_down"
    assert saved.target_bid == 680  # 소재 800의 하향 스텝(그룹 200 아님)


def test_hourly_lane_canary_probe_up_holds_no_ad_routing(db):
    """[GATE P2-1] 카나리 + 미연결 그룹 + 탐침 UP verdict → ad 라우팅 금지(hold) —
    CD3 되돌림 기계가 'ad'를 처리 못 함(before_value adAttr 중첩 미파싱·grain 필터 부재).
    탐침의 ad 확장은 별도 페이즈."""
    _seed_hourly_shopping(db)
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)
    db.commit()
    with patch.object(auto_operator, "AD_BID_ROUTING_FALLBACK_CAMPAIGNS", frozenset({CAMP})), \
         patch.object(auto_operator, "_judge_hourly", return_value={"direction": "hold", "reason": "판단보류"}), \
         patch.object(auto_operator, "_probe_trigger", return_value=(True, "imp 있음·클릭0")), \
         patch.object(auto_operator, "_learned_optimal_skip", return_value=(False, "학습밴드 미도달")), \
         patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    mock_exec.assert_not_called()
    assert result["approved"] == 0
    assert db.query(NaverProposal).count() == 0  # ad 제안 0
    assert any("탐침" in h["reason"] and "[레버 미연결]" in h["reason"] for h in result["held"])


def test_hourly_lane_ad_up_executes_inline(db):
    """★D-NAO-130: 미연결 그룹의 UP도 소재 레버로 **인라인 자동 실행**된다(하향과 대칭).

    Jino 확정 근거: 올리고 → 실측하고 → 손실이면 자동 하향이 깎는 되먹임 루프가 이미 돈다.
    ★타입은 반드시 `bid_up`이어야 한다 — 소재는 rank-step 분기(bid_up_servo/bid_up_rank)에
    안 걸려 ±15% 클램프가 적용되는 레거시 폴백을 탄다. 면제 타입이 소재로 새면 안 된다.
    """
    _seed_hourly_shopping(db)
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)
    db.commit()
    with patch.object(auto_operator, "_judge_hourly", return_value={"direction": "up", "reason": "저순위"}), \
         patch.object(auto_operator, "_learned_optimal_skip", return_value=(False, "학습밴드 미도달")), \
         patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_sa_writer, "get_ad_bid", return_value=800), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    mock_exec.assert_called_once()
    assert result["executed"] == 1
    assert result["ad_confirm_pending"] == 0
    saved = db.query(NaverProposal).filter(NaverProposal.target_type == "ad").one()
    assert (saved.proposal_type, saved.status, saved.target_bid) == ("bid_up", "approved", 920)


def test_daily_lane_excludes_ad_proposals_from_auto_approval(db):
    """[GATE P2-2 Confirm-only] 일 레인은 target_type='ad' pending을 자동승인·집행하지 않고
    말미 stale 정리(rejected)에서도 제외 — 콘솔 Confirm 대기 상태를 보존한다."""
    db.add(NaverCampaignSettings(campaign_id=CAMP, optimizer="ours", auto_operate=True))
    p = NaverProposal(
        proposal_type="bid_down", target_type="ad", target_id="nad-1", campaign_id=CAMP,
        adgroup_id="grp-hot", rationale="[소재입찰] 테스트", expected_effect="테스트",
        status="pending", target_bid=680,
        created_at=datetime(2026, 7, 19, 23, 0),  # KST 07-20 08:00 상당(UTC) — 당일 창 내
    )
    db.add(p)
    db.commit()
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)
    mock_exec.assert_not_called()
    assert result["reviewed"] == 0
    assert result["approved"] == 0
    assert result["rejected_stale"] == 0  # stale 정리에서도 제외(Confirm 대기 보존)
    db.refresh(p)
    assert p.status == "pending"
    assert p.approval_source is None


def test_hourly_lane_non_canary_holds_disconnected(db):
    """비카나리(빈 집합 기본값) → 미연결 그룹은 기존 [레버 미연결] hold(현행 동작 보존).

    D-NAO-125(2026-07-29): 스코프 판정이 AD_BID_CANARY_CAMPAIGNS 상수에서 킬스위치
    (AD_BID_ROUTING_ENABLED)로 옮겨졌다 — 기본 상태(킬스위치 on)에서는 이 캠페인도 이제
    ad-레벨로 열린다((c) 회귀가 그 경로를 커버). 이 테스트는 킬스위치 off로 좁혀
    "카나리 상수 미포함 → hold" 종전 회귀를 보존한다."""
    _seed_hourly_shopping(db)
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)
    db.commit()
    with patch.object(auto_operator, "ad_bid_routing_enabled", lambda db: False), \
         patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_sa_writer, "get_ad_bid") as mget_ad, \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    mock_exec.assert_not_called()
    mget_ad.assert_not_called()  # 비카나리(킬스위치 off)는 소재 재조회조차 안 함
    assert result["approved"] == 0
    assert any("[레버 미연결]" in h["reason"] for h in result["held"])


def test_hourly_lane_canary_connected_group_uses_group_path(db):
    """카나리여도 연결 그룹(전 소재 useGroupBidAmt=true)은 종전 그룹입찰 경로(회귀 0)."""
    _seed_hourly_shopping(db)
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=None, use_group=True)
    db.commit()
    with patch.object(auto_operator, "AD_BID_ROUTING_FALLBACK_CAMPAIGNS", frozenset({CAMP})), \
         patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    mock_exec.assert_called_once()
    saved = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert saved.target_type == "adgroup"
    assert saved.target_id == "grp-hot"


# ══════════════════════════════════════════════════════════════════
# (4) proposal_writer — 스톱로스 & 밴드 라우팅(_stop_loss_proposal 직접)
# ══════════════════════════════════════════════════════════════════
def test_stop_loss_disconnected_canary_routes_to_ad_bid_down():
    """카나리 + 레버 미연결(effective_source='ad') → 소재-레벨 bid_down(그룹 대기 아님).
    target_type='ad'·target_id=effective_ad_id·adgroup_id 병기·target_bid=소재 스텝(800→680)."""
    row = {"campaign_id": "cmp-ours", "adgroup_id": "grp-1", "cost": 20400, "conv_amt": 0,
           "current_bid": 200, "effective_bid": 800, "effective_source": "ad",
           "effective_ad_id": "nad-1", "stop_loss_amount": 8000, "clk": 12}
    p = proposal_writer._stop_loss_proposal(row, target_type="adgroup", manual_bid=True, ad_bid_canary=True)
    assert p["proposal_type"] == "bid_down"
    assert p["target_type"] == "ad"
    assert p["target_id"] == "nad-1"
    assert p["adgroup_id"] == "grp-1"
    assert p["target_bid"] == 680


def test_stop_loss_disconnected_non_canary_waits_none():
    """비카나리는 기존 동작 — 미연결 = 바닥 대기(None), 소재-레벨 제어 안 함."""
    row = {"campaign_id": "cmp-ours", "adgroup_id": "grp-1", "cost": 20400, "conv_amt": 0,
           "current_bid": 200, "effective_bid": 800, "effective_source": "ad",
           "effective_ad_id": "nad-1", "stop_loss_amount": 8000}
    p = proposal_writer._stop_loss_proposal(row, target_type="adgroup", manual_bid=True, ad_bid_canary=False)
    assert p is None


def test_stop_loss_disconnected_canary_lever_broken_still_pauses():
    """카나리여도 진짜 lever_broken(소재 바닥+CPC 폭등)은 B3에서 은퇴 안 함(B4) → pause 유지."""
    row = {"campaign_id": "cmp-ours", "adgroup_id": "grp-1", "cost": 20400, "conv_amt": 0,
           "current_bid": 200, "effective_bid": 70, "effective_source": "ad",
           "effective_ad_id": "nad-1", "stop_loss_amount": 700, "lever_broken": True, "chronic_cpc": 5000}
    p = proposal_writer._stop_loss_proposal(row, target_type="adgroup", manual_bid=True, ad_bid_canary=True)
    assert p["proposal_type"] == "pause"


def test_stop_loss_disconnected_canary_ad_at_floor_falls_back():
    """카나리 + 미연결이지만 소재 bidAmt가 이미 하한(70) → ad 스텝 소실 → 기존 경로(대기 None)."""
    row = {"campaign_id": "cmp-ours", "adgroup_id": "grp-1", "cost": 20400, "conv_amt": 0,
           "current_bid": 200, "effective_bid": 70, "effective_source": "ad",
           "effective_ad_id": "nad-1", "stop_loss_amount": 700}
    p = proposal_writer._stop_loss_proposal(row, target_type="adgroup", manual_bid=True, ad_bid_canary=True)
    assert p is None


def test_stop_loss_connected_unchanged_by_canary_flag():
    """연결 그룹(effective_source='group')은 카나리 플래그와 무관하게 종전 그룹 bid_down."""
    row = {"campaign_id": "cmp-ours", "adgroup_id": "grp-1", "cost": 5000, "conv_amt": 0,
           "current_bid": 200, "effective_bid": 200, "effective_source": "group",
           "stop_loss_amount": 2000}
    p = proposal_writer._stop_loss_proposal(row, target_type="adgroup", manual_bid=True, ad_bid_canary=True)
    assert p["proposal_type"] == "bid_down"
    assert p["target_type"] == "adgroup"
    assert p["target_bid"] == 170


# ── 밴드 라우팅(build) ──
def _sim(direction="down", ceiling=100, recommended=100, current_bid=None):
    return {
        "recommended_bid": recommended, "economic_ceiling": ceiling, "rank_bid": None,
        "direction": direction, "basis": "economic_ceiling", "current_bid": current_bid,
        "expected_effect_text": "테스트", "capability_flags": {},
    }


def _bep_board_row(**overrides):
    row = {"campaign_id": "cmp-ours", "adgroup_id": "grp-d", "cost": 8000,
           "conv_amt": 1000, "roas_naver": 0.125, "roas_corrected": 0.125}
    row.update(overrides)
    return row


def _diagnosis(**boards):
    return {"window": {}, "correction_factor": {"factor": 1.0, "factor_low": 1.0, "factor_high": 1.0, "factor_point": 1.0}, "boards": boards}


def test_build_band_bep_canary_routes_disconnected_to_ad(db):
    """카나리 + 미연결 밴드 그룹(bep, down) → 그룹 skip 대신 ad-레벨 bid_down 제안."""
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    _ad(db, "grp-d", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False, campaign_id="cmp-ours")
    db.commit()
    diagnosis = _diagnosis(shopping_group_bep=[_bep_board_row()])
    with patch.object(auto_operator, "AD_BID_ROUTING_FALLBACK_CAMPAIGNS", frozenset({"cmp-ours"})):
        out = proposal_writer.build(
            db, diagnosis, bid_sims={("adgroup", "grp-d"): _sim(direction="down", current_bid=200)},
            as_of=TODAY,
        )
    assert len(out) == 1
    assert out[0]["target_type"] == "ad"
    assert out[0]["target_id"] == "nad-1"
    assert out[0]["proposal_type"] == "bid_down"
    assert out[0]["target_bid"] == 680  # 소재 800 하향 스텝


def test_build_band_bep_non_canary_skips_disconnected(db):
    """비카나리(기본) → 미연결 그룹 skip(B2 억제, 현행 동작 보존).

    D-NAO-125(2026-07-29): 기본 상태(킬스위치 on)에서는 이 캠페인도 이제 ad-레벨로 열린다
    (의도된 변경). 이 테스트는 킬스위치 off로 좁혀 종전 skip 회귀를 보존한다."""
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    _ad(db, "grp-d", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False, campaign_id="cmp-ours")
    db.commit()
    diagnosis = _diagnosis(shopping_group_bep=[_bep_board_row()])
    with patch.object(auto_operator, "ad_bid_routing_enabled", lambda db: False):
        out = proposal_writer.build(
            db, diagnosis, bid_sims={("adgroup", "grp-d"): _sim(direction="down", current_bid=200)},
            as_of=TODAY,
        )
    assert out == []


def test_build_band_growth_routes_up_to_ad(db):
    """★D-NAO-129: 일 레인 성장(up)도 미연결 그룹이면 소재 레버로 라우팅된다(종전 skip 대체)."""
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    _ad(db, "grp-g", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False, campaign_id="cmp-ours")
    db.commit()
    diagnosis = _diagnosis(shopping_group_growth=[
        _bep_board_row(adgroup_id="grp-g", conv_amt=40000, roas_naver=5.0, roas_corrected=5.0),
    ])
    with patch.object(auto_operator, "AD_BID_ROUTING_FALLBACK_CAMPAIGNS", frozenset({"cmp-ours"})):
        out = proposal_writer.build(
            db, diagnosis,
            bid_sims={("adgroup", "grp-g"): _sim(direction="up", recommended=1300, ceiling=1300,
                                                 current_bid=200)},
            as_of=TODAY,
        )
    assert len(out) == 1
    assert (out[0]["target_type"], out[0]["proposal_type"]) == ("ad", "bid_up")
    assert out[0]["target_bid"] == 920  # 소재 800 +15%(그룹입찰 200 기준이 아니다)


# ══════════════════════════════════════════════════════════════════
# (5) 카나리 빈 집합 기본값 = 전면 hold(배포 즉시 행위 변화 0) + DOWN 한정 상수
# ══════════════════════════════════════════════════════════════════
def test_canary_default_empty_set(db):
    # 카나리 1호 개방(Jino 2026-07-20): 맥세이프만. 다른 캠페인이 상수에 소리 없이 추가되면
    # 이 테스트가 잡는다.
    # ★D-NAO-282: 한 이름이 겸하던 **정반대 두 의미**를 각자 이름으로 갈랐다. 값은 같지만
    #   가리키는 것이 다르므로 **둘 다** 못박는다 — 한쪽만 검사하면 다른 쪽이 조용히 바뀐다.
    #   · FALLBACK  = 킬스위치 OFF 시 되돌아갈 «개방(allowlist)» 집합
    #   · CONFIRM_ONLY = delegation_gate·expert_briefing_builder의 «제한» 집합
    #     (건드리면 계정 전체 위임이 죽는다)
    assert auto_operator.AD_BID_ROUTING_FALLBACK_CAMPAIGNS == frozenset({"cmp-a001-02-000000010769985"})
    assert auto_operator.AD_BID_CONFIRM_ONLY_CAMPAIGNS == frozenset({"cmp-a001-02-000000010769985"})
    # D-NAO-125(2026-07-29): _ad_bid_canary는 이제 스코프(=위 상수)가 아니라 킬스위치만 본다 —
    # 기본(킬스위치 on) 상태에서는 상수 무관 True다.
    assert auto_operator._ad_bid_canary(db, "any-campaign") is True
    with patch.object(auto_operator, "ad_bid_routing_enabled", lambda db: False):
        # 킬스위치 off → 종전처럼 상수 멤버십으로 폴백(회귀 보존).
        assert auto_operator._ad_bid_canary(db, "any-campaign") is False
        # ★OFF는 «전면 정지»가 아니다 — FALLBACK 집합에 든 캠페인은 계속 열린다.
        #   화면 경고문(`warn`)이 말하는 그 사실을 코드로도 못박는다.
        assert auto_operator._ad_bid_canary(db, "cmp-a001-02-000000010769985") is True


def test_canary_generation_and_autofire_both_directions():
    """D-NAO-130: 생성·자동발사 모두 양방향. 두 상수는 항상 같이 움직인다.

    한쪽만 열면 실쓰기 경계에서 죽는 카드가 Confirm 큐에 쌓인다(죽은 카드 금지).
    되돌리려면 _AD_AUTO_EXEC_PROPOSAL_TYPES에서 bid_up을 빼면 Confirm 큐로 복귀한다.
    """
    assert auto_operator._AD_BID_CANARY_PROPOSAL_TYPES == frozenset({"bid_down", "bid_up"})
    assert auto_operator._AD_AUTO_EXEC_PROPOSAL_TYPES == frozenset({"bid_down", "bid_up"})


# ══════════════════════════════════════════════════════════════════
# GATE 2R P2-A — delegation_gate·expert 브리핑에서 ad 제외(Confirm-only 마지막 구멍)
# ══════════════════════════════════════════════════════════════════
def _delegation_skipped():
    return {"not_delegated": 0, "not_pending": 0, "blocked": 0, "optimizer": 0,
            "budget_envelope": 0, "ad_confirm_only": 0}


def test_delegation_eligible_excludes_ad_target(db):
    """[GATE 2R P2-A] target_type='ad' pending은 bid_down 위임이 켜져 있어도 delegation
    자동승인 자격에서 제외 — Ava 재가동 시 5번째 실행 경로로 자동발사되는 구멍 봉쇄
    (D-NAO-5 카나리 Confirm-only, 2단계 개방 전까지)."""
    from app.services.naver_ad import delegation_gate
    _settings(db, campaign_id="cmp1", optimizer="ours")
    p = _ad_proposal(db, proposal_type="bid_down", target_bid=680, status="pending")
    skipped = _delegation_skipped()
    assert delegation_gate._eligible(db, p, {"bid_down"}, skipped) is False
    assert skipped["ad_confirm_only"] == 1


def test_delegation_eligible_adgroup_still_passes(db):
    """회귀 0 — 같은 bid_down이라도 target_type='adgroup'은 종전대로 eligible."""
    from app.services.naver_ad import delegation_gate
    _settings(db, campaign_id="cmp1", optimizer="ours")
    p = NaverProposal(
        proposal_type="bid_down", target_type="adgroup", target_id="grp-1", campaign_id="cmp1",
        rationale="테스트", expected_effect="테스트", status="pending", target_bid=170,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    skipped = _delegation_skipped()
    assert delegation_gate._eligible(db, p, {"bid_down"}, skipped) is True
    assert skipped["ad_confirm_only"] == 0


def test_expert_briefing_excludes_ad_pending(db):
    """[GATE 2R P2-A] 브리핑 pending_proposals에 target_type='ad' 제안 미포함 — Ava 검토
    대상(expected_ids)에 실리면 혼란(Confirm-only 카나리라 위임 실행도 불가한 카드)."""
    from app.services.naver_ad import expert_briefing_builder
    ad_p = _ad_proposal(db, proposal_type="bid_down", target_bid=680, status="pending")
    kw_p = NaverProposal(
        proposal_type="bid_down", target_type="keyword", target_id="nkw-1", campaign_id="cmp1",
        rationale="테스트", expected_effect="테스트", status="pending", target_bid=170,
    )
    db.add(kw_p)
    db.commit()
    db.refresh(kw_p)
    briefing = expert_briefing_builder.build(db, as_of=TODAY)
    ids = {p["id"] for p in briefing["pending_proposals"]}
    assert kw_p.id in ids
    assert ad_p.id not in ids


# ══════════════════════════════════════════════════════════════════
# GATE 2R P2-B — 시간당 ad pending dedup(콘솔 홍수 방지)
# ══════════════════════════════════════════════════════════════════
def test_hourly_lane_ad_pending_dedup_across_runs(db):
    """[GATE 2R P2-B] 같은 유닛 2회 연속 hourly run → ad pending 1건 유지 + 2회차는
    dup skip 카운터(매시간 동일 pending 누적 → Confirm 큐 매몰 방지).

    D-NAO-125(2026-07-29): 이 dedup 분기는 이제 `_AD_AUTO_EXEC_PROPOSAL_TYPES`에 든 타입
    (기본 bid_down)에는 적용되지 않는다 — 그 타입은 pending으로 남지 않고 인라인 실행되므로
    dedup 자체가 무의미해진다(신규 회귀 (e) 참조: stale pending이 하향을 막지 않음, 이 테스트와
    정확히 대칭). 이 테스트는 `_AD_AUTO_EXEC_PROPOSAL_TYPES`를 빈 집합으로 되돌려 "아직
    자동실행으로 승격 안 된 ad-레벨 타입"의 Confirm-only dedup 회귀를 그대로 보존한다."""
    _seed_hourly_shopping(db)
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)
    db.commit()
    patches = dict(
        _get_adgroup={"bidAmt": 200},
        get_ad_bid=800,
    )
    with patch.object(auto_operator, "AD_BID_ROUTING_FALLBACK_CAMPAIGNS", frozenset({CAMP})), \
         patch.object(auto_operator, "_AD_AUTO_EXEC_PROPOSAL_TYPES", frozenset()), \
         patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value=patches["_get_adgroup"]), \
         patch.object(auto_operator.naver_sa_writer, "get_ad_bid", return_value=patches["get_ad_bid"]), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        r1 = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
        r2 = auto_operator.run_hourly_lane(
            db, now=NOW + timedelta(hours=1), fetch_intraday=lambda t, d: _overheat_curve(),
        )
    mock_exec.assert_not_called()
    assert r1["ad_confirm_pending"] == 1
    assert r2["ad_confirm_pending"] == 0
    assert r2["ad_confirm_pending_dup_skipped"] == 1
    ad_rows = db.query(NaverProposal).filter(NaverProposal.target_type == "ad").all()
    assert len(ad_rows) == 1  # 동일 유닛 pending 누적 없음
    assert ad_rows[0].status == "pending"


# ══════════════════════════════════════════════════════════════════
# D-NAO-125(2026-07-29) — 소재 하향 인라인 자동실행 + 킬스위치(AD_BID_ROUTING_ENABLED) 회귀
#
# 배경: AD_BID_CANARY_CAMPAIGNS는 delegation_gate·expert_briefing_builder가 "Confirm-only
# 제외" 판별에도 재사용하는 상수라, 그 상수를 채워 스코프를 넓히면 계정 전체 위임이 죽는다
# (07-21 적대 리뷰 P2가 지적한 채로 8일 미이행 → 07-29 07:37 인수 캠페인이 상수 누락으로
# [레버 미연결] hold에 묶여 손실이 시간당 쌓인 실사고). D-NAO-125는 스코프를 이 상수에서
# 떼어 상위 auto_operate 게이트(두 겹)에 위임하고, _ad_bid_canary는 새 킬스위치
# AD_BID_ROUTING_ENABLED만 본다. 실행 여부(인라인 자동실행 vs Confirm-only pending)는
# 별도 상수 _AD_AUTO_EXEC_PROPOSAL_TYPES={"bid_down"}가 결정 — 하향만 연다(상향은 새 승인원
# 설계가 필요한 별도 스프린트).
# ══════════════════════════════════════════════════════════════════
def test_hourly_lane_ad_bid_down_executes_inline(db):
    """(a) 소재 하향이 인라인 자동 실행된다(D-NAO-125) — 레버 미연결(source='ad') 그룹에서
    손실 판정(밴드 DOWN)이 나면 레인이 ad-레벨 bid_down 제안을 Confirm 큐에 세워두지 않고
    그 자리에서 승인·실행한다. executed 카운터 증가·제안 status=approved·
    ad_confirm_pending은 늘지 않는다(Confirm 큐로 안 감 — 이게 D-NAO-125의 핵심 변화)."""
    _seed_hourly_shopping(db)
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)  # 미연결
    db.commit()
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_sa_writer, "get_ad_bid", return_value=800), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    mock_exec.assert_called_once()
    assert result["executed"] == 1
    assert result["approved"] == 1
    assert result["ad_confirm_pending"] == 0
    saved = db.query(NaverProposal).filter(NaverProposal.target_type == "ad").one()
    assert saved.status == "approved"
    assert saved.proposal_type == "bid_down"
    assert saved.approval_source == auto_operator.APPROVAL_SOURCE_HOURLY


def test_ad_up_open_types_are_never_clamp_exempt():
    """★D-NAO-129 불변식: 소재 UP 개방 집합에 **변경폭 면제 타입**이 절대 들어가지 않는다.

    쌍방향 (승인원⟺타입) 잠금의 존재 이유가 "±15%가 면제된 타입이 아무 승인원으로 발사되는
    것"을 막는 데 있다(적대 리뷰 재현: 300원→90,000원). bid_up을 승인원 무관으로 연 근거가
    바로 "그 타입은 클램프가 걸린다"이므로, 이 불변식이 깨지면 개방 근거 자체가 사라진다.
    """
    from app.services.naver_ad.bid_step_types import (
        CHANGE_PCT_EXEMPT_TYPES,
        EXPLORATION_STEP_TYPES,
    )
    assert harness._AD_UP_OPEN_TYPES & (CHANGE_PCT_EXEMPT_TYPES | EXPLORATION_STEP_TYPES) == frozenset()
    assert harness._AD_UP_OPEN_TYPES <= harness.BID_UP_TYPES


def test_ad_up_exempt_types_still_locked_to_their_source(db):
    """회귀 0 — 면제 타입(bid_up_cold)은 여전히 자기 승인원에서만 나간다.

    D-NAO-129가 연 것은 bid_up 하나뿐이다. 콘솔·시간당 레인 승인원이 bid_up_cold를 태우면
    ±15% 완전 면제를 무제한 상향으로 쓰는 구멍이 되므로 종전대로 fail-closed여야 한다.
    """
    db.add(NaverCampaignSettings(campaign_id=CAMP, optimizer="ours", auto_operate=True))
    p = NaverProposal(
        proposal_type="bid_up_cold", target_type="ad", target_id="nad-1", campaign_id=CAMP,
        adgroup_id="grp-hot", status="approved", target_bid=5000,
        approval_source=auto_operator.APPROVAL_SOURCE_HOURLY,  # cold_op가 아니다
    )
    db.add(p)
    db.commit()
    with patch.object(writer, "get_ad_bid", return_value=800), \
         patch.object(writer, "update_ad_bid") as mock_write:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False, now=NOW)
    mock_write.assert_not_called()


def test_hourly_lane_ad_bid_down_opens_for_non_canary_campaign(db):
    """(c) 비카나리 캠페인도 이제 열린다 — 이게 D-NAO-125의 본체다. 07-29 07:37 인수 캠페인이
    AD_BID_CANARY_CAMPAIGNS 상수 누락으로 죽어 있던 사고의 회귀 방지: CAMP("cmp-shop")는
    AD_BID_CANARY_CAMPAIGNS(맥세이프 전용)에 한 번도 없었던 캠페인인데도, 상수를 전혀
    건드리지 않고(패치 없이) (a)와 동일하게 인라인 자동 실행된다."""
    assert CAMP not in auto_operator.AD_BID_ROUTING_FALLBACK_CAMPAIGNS  # 전제 확인 — 진짜 비카나리
    _seed_hourly_shopping(db)
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)  # 미연결
    db.commit()
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_sa_writer, "get_ad_bid", return_value=800), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    mock_exec.assert_called_once()
    assert result["executed"] == 1
    assert result["ad_confirm_pending"] == 0
    saved = db.query(NaverProposal).filter(NaverProposal.target_type == "ad").one()
    assert saved.status == "approved"


def test_hourly_lane_kill_switch_off_restores_full_hold(db):
    """(d) 킬스위치 — AD_BID_ROUTING_ENABLED=False면 종전 "[레버 미연결] … B3 대기" hold로
    복귀하고 실행 0(상수 폴백 — 이 캠페인은 AD_BID_CANARY_CAMPAIGNS에도 없어 전면 hold)."""
    _seed_hourly_shopping(db)
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)  # 미연결
    db.commit()
    with patch.object(auto_operator, "ad_bid_routing_enabled", lambda db: False), \
         patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_sa_writer, "get_ad_bid") as mget_ad, \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    mock_exec.assert_not_called()
    mget_ad.assert_not_called()  # 킬스위치 off → 소재 재조회조차 안 함(hold가 그 전에 걸림)
    assert result["executed"] == 0
    assert result["approved"] == 0
    reasons = [h["reason"] for h in result["held"]]
    assert any("[레버 미연결]" in r and "B3 대기" in r for r in reasons)


def test_kill_switch_off_also_stops_auto_exec_on_canary_campaign(db):
    """(g) ★킬스위치는 두 게이트를 모두 지배해야 한다 — 롤백 보장.

    생성 게이트(AD_BID_ROUTING_ENABLED)와 실행 게이트(_AD_AUTO_EXEC_PROPOSAL_TYPES)가 독립
    축이면, 킬스위치를 내려도 **AD_BID_CANARY_CAMPAIGNS에 남아 있는 캠페인**은 상수 폴백으로
    ad 라우팅을 통과한 뒤 하향이 계속 자동 실행된다 = "되돌리는 스위치가 완전히 되돌리지
    않는다". 현재 맥세이프엔 ad-레버 유닛이 없어 도달 불가능하지만, 롤백 보장은 도달
    가능성과 무관하게 성립해야 한다(사고 시 한 줄 원복이 이 스위치의 존재 이유).
    → _ad_auto_exec()가 킬스위치를 함께 보므로 Confirm 대기로 남아야 한다.
    """
    _seed_hourly_shopping(db)
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)  # 미연결
    db.commit()
    with patch.object(auto_operator, "ad_bid_routing_enabled", lambda db: False), \
         patch.object(auto_operator, "AD_BID_ROUTING_FALLBACK_CAMPAIGNS", frozenset({CAMP})), \
         patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_sa_writer, "get_ad_bid", return_value=800), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())

    mock_exec.assert_not_called()  # ★핵심: 킬스위치가 실행 게이트까지 덮는다
    assert result["executed"] == 0
    assert result["ad_confirm_pending"] == 1  # 라우팅은 되지만 Confirm 대기로만 남음
    pending = db.query(NaverProposal).filter(NaverProposal.target_type == "ad").all()
    assert [p.status for p in pending] == ["pending"]


def test_hourly_lane_stale_ad_pending_does_not_block_bid_down(db):
    """(e) stale pending이 하향을 막지 않는다 — 배포 전에 쌓여 있던 같은 (bid_down, ad,
    target_id) pending 제안이 이미 DB에 있어도, 새 하향 판정은 dedup skip되지 않고
    그대로 인라인 실행된다(_AD_AUTO_EXEC_PROPOSAL_TYPES는 dedup 분기에서 제외되므로).
    회귀 대상: 이 dedup을 그대로 뒀다면 stale 카드 한 장이 이후 모든 하향을 영구 skip시켰다."""
    _seed_hourly_shopping(db)
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)  # 미연결
    db.commit()
    stale = NaverProposal(
        proposal_type="bid_down", target_type="ad", target_id="nad-1", campaign_id=CAMP,
        adgroup_id="grp-hot", rationale="[소재입찰] 배포 전 stale pending", expected_effect="x",
        status="pending", target_bid=680,
    )
    db.add(stale)
    db.commit()
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_sa_writer, "get_ad_bid", return_value=800), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    mock_exec.assert_called_once()  # stale pending에 안 막힘
    assert result["executed"] == 1
    assert result.get("ad_confirm_pending_dup_skipped", 0) == 0
    ad_rows = db.query(NaverProposal).filter(NaverProposal.target_type == "ad").all()
    assert len(ad_rows) == 2  # stale 1건 + 새로 실행된 1건(dedup skip 안 됨 — 이게 핵심)
    statuses = sorted(r.status for r in ad_rows)
    # ★codex 2R[P2] 이후 변경: stale pending은 남겨두지 않고 **만료 교체**한다. 남겨두면
    #   레인 캡 강등분이 매시간 새 pending을 낳아 Confirm 큐가 같은 소재로 매몰된다.
    #   큐에는 항상 지금 값 기준 1장만 존재한다.
    assert statuses == ["approved", "expired"]


def test_hourly_lane_probe_still_holds_no_ad_routing(db):
    """(f) 탐침은 여전히 ad 라우팅 금지 — D-NAO-125는 탐침 경로를 건드리지 않았다. 미연결
    그룹 + 탐침 트리거면 킬스위치 on(기본)이어도 "[레버 미연결] 탐침은 ad 미지원" hold."""
    _seed_hourly_shopping(db)
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)  # 미연결
    db.commit()
    with patch.object(auto_operator, "_judge_hourly",
                      return_value={"direction": "hold", "reason": "판단보류"}), \
         patch.object(auto_operator, "_probe_trigger", return_value=(True, "imp 있음·클릭0")), \
         patch.object(auto_operator, "_learned_optimal_skip", return_value=(False, "학습밴드 미도달")), \
         patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_sa_writer, "get_ad_bid") as mget_ad, \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    mock_exec.assert_not_called()
    mget_ad.assert_not_called()
    assert db.query(NaverProposal).count() == 0
    assert result["approved"] == 0
    assert any("탐침" in h["reason"] and "[레버 미연결]" in h["reason"] for h in result["held"])


# ══════════════════════════════════════════════════════════════════
# (6) account_diagnosis — 미연결 창이 ad change_log 인식(B3 이월)
# ══════════════════════════════════════════════════════════════════
D0 = date(2026, 7, 1)
D_TO = date(2026, 7, 15)


def _shop_entity(db, adgroup_id, *, bid_amt, campaign_id="cmp-shop"):
    db.add(NaverEntity(entity_type="adgroup", entity_id=adgroup_id, parent_id=campaign_id,
                       campaign_id=campaign_id, campaign_type="SHOPPING", name=adgroup_id,
                       status="on", bid_amt=bid_amt))


def _active_campaign(db, campaign_id="cmp-shop"):
    db.add(NaverEntity(entity_type="campaign", entity_id=campaign_id, parent_id="",
                       campaign_id=campaign_id, campaign_type="SHOPPING", name=campaign_id, status="on"))


def _daily(db, ad_date, adgroup_id, *, clk, cost, direct=0, campaign_id="cmp-shop"):
    db.add(NaverAdDaily(ad_date=ad_date, campaign_id=campaign_id, campaign_type="SHOPPING",
                        adgroup_id=adgroup_id, keyword_id="", imp=clk * 20, clk=clk, cost=cost,
                        rank_sum=clk * 60, conv_direct_cnt=1 if direct else 0, conv_direct_amt=direct))


def _ad_bid_change_log(db, ad_id, changed_at):
    """우리 소재입찰 변경(성공) change_log 1건 — entity_type='ad'·action='update_bid'·after_value."""
    db.add(NaverChangeLog(
        entity_type="ad", entity_id=ad_id, action="update_bid", dry_run=False,
        before_value=json.dumps({"bidAmt": 1990}), after_value=json.dumps({"bidAmt": 800}),
        changed_at=changed_at, executed_at=changed_at,
    ))


def test_last_ad_bid_change_by_group_maps_ad_to_group(db):
    """소재 change_log를 그룹으로 매핑(그룹별 최신 소재입찰 변경일)."""
    _ad(db, "grp-mo", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)
    _ad_bid_change_log(db, "nad-1", datetime(2026, 7, 12, 10, 0))
    db.commit()
    out = diag._last_ad_bid_change_by_group(db, {"grp-mo"})
    assert out == {"grp-mo": date(2026, 7, 12)}


def test_last_ad_bid_change_empty_before_b3(db):
    """B3 실집행 전(ad change_log 없음) → 빈 dict(종전 만성 7일 창 동작 보존)."""
    _ad(db, "grp-mo", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)
    db.commit()
    assert diag._last_ad_bid_change_by_group(db, {"grp-mo"}) == {}


def test_shopping_pause_disconnected_window_cut_after_ad_change(db):
    """미연결 유닛의 증거 창이 마지막 소재입찰 변경(D_TO-1) 이후로 절체 — 변경 전 비용 제외.
    7일 전체(21,000)면 임계 19,900 진입이나, 변경 후 2일 창(6,000)이면 미진입(시점 정합)."""
    _active_campaign(db)
    _shop_entity(db, "grp-mo", bid_amt=50)
    _ad(db, "grp-mo", "p1", ad_id="nad-1", ad_bid_amt=1990, use_group=False)
    _ad_bid_change_log(db, "nad-1", datetime(2026, 7, 14, 9, 0))  # D_TO-1
    for i in range(7):
        _daily(db, D_TO - timedelta(days=i), "grp-mo", clk=2, cost=3000, direct=0)
    db.commit()
    out = diag.shopping_pause_candidates(db, D0, D_TO, bep_roas=Decimal("1.5"),
                                         correction_factor=Decimal("1.0"))
    # 변경일(07-14)~D_TO(07-15) = 2일 창 = 6,000 < 19,900 → 미진입(변경 전 고비용 배제).
    assert out == []


def test_shopping_pause_disconnected_no_ad_change_uses_chronic_window(db):
    """소재입찰 변경 없으면(B3 전) 종전 만성 7일 창 그대로(21,000 ≥ 19,900 → 진입) — 회귀 0."""
    _active_campaign(db)
    _shop_entity(db, "grp-mo", bid_amt=50)
    _ad(db, "grp-mo", "p1", ad_id="nad-1", ad_bid_amt=1990, use_group=False)
    for i in range(7):
        _daily(db, D_TO - timedelta(days=i), "grp-mo", clk=2, cost=3000, direct=0)
    db.commit()
    out = diag.shopping_pause_candidates(db, D0, D_TO, bep_roas=Decimal("1.5"),
                                         correction_factor=Decimal("1.0"))
    assert len(out) == 1
    assert out[0]["cost"] == 21000
    assert out[0]["effective_ad_id"] == "nad-1"  # B3: 소재-레벨 제어 대상 노출


# ══════════════════════════════════════════════════════════════════
# D-NAO-125 codex[P1] 4건 회귀 (2026-07-29 리뷰 → 수정)
#   1) 자동 하향에만 일일 상한(DL3 면제는 사람 눈 전제였다)
#   2) 같은 소재가 실행 중이면 중복 발사 금지
#   3) 킬스위치가 잔존 approved 행까지 되돌린다(writer 직전 최종 확인)
#   4) 레인당 blast radius 상한 — 초과분은 드롭이 아니라 Confirm 대기로 강등
# ══════════════════════════════════════════════════════════════════
def test_auto_bid_down_has_daily_cap_but_human_down_does_not(db):
    """(1) 같은 하향이라도 **무인 실행**이면 일일 상한이 걸린다."""
    from app.services.naver_ad import guardrail_gate

    # ★codex 2R[P1]: 상한의 원료는 "그 소재의 오늘 모든 변경"이 아니라 **자동 하향 성공 수**다.
    #   사람이 오늘 손으로 5번 만졌어도 자동 하향이 0회면 손실 하향은 나가야 한다.
    ctx = {"current_bid": 800, "changes_today_count": 5, "auto_bid_down_today": 3,
           "campaign_type": "SHOPPING"}
    auto = guardrail_gate.check({"proposal_type": "bid_down", "target_bid": 680},
                                {**ctx, "auto_exec": True}, now=NOW)
    human = guardrail_gate.check({"proposal_type": "bid_down", "target_bid": 680},
                                 {**ctx, "auto_exec": False}, now=NOW)
    assert auto is not None and "자동 하향 일일 상한" in auto
    assert human is None  # DL3 면제 유지 — 사람이 승인한 "쭉 낮추다가"는 그대로


def test_auto_bid_down_under_cap_passes(db):
    from app.services.naver_ad import guardrail_gate

    assert guardrail_gate.check(
        {"proposal_type": "bid_down", "target_bid": 680},
        {"current_bid": 800, "changes_today_count": 9, "auto_bid_down_today": 2,
         "auto_exec": True, "campaign_type": "SHOPPING"},
        now=NOW,
    ) is None


def test_kill_switch_blocks_residual_approved_ad_proposal(db):
    """(3) approved 커밋 후 크래시로 남은 행도 킬스위치가 막는다(쓰기·change_log 0)."""
    db.add(NaverCampaignSettings(campaign_id=CAMP, optimizer="ours", auto_operate=True))
    p = NaverProposal(
        proposal_type="bid_down", target_type="ad", target_id="nad-1", campaign_id=CAMP,
        adgroup_id="grp-hot", status="approved", target_bid=680,
        approval_source=auto_operator.APPROVAL_SOURCE_HOURLY,
    )
    db.add(p)
    db.commit()
    with patch.object(auto_operator, "ad_bid_routing_enabled", lambda db: False), \
         patch.object(writer, "get_ad_bid") as mock_get, \
         patch.object(writer, "update_ad_bid") as mock_write:
        with pytest.raises(harness.KillSwitchEngagedError):
            harness.execute(db, p.id, dry_run=False, now=NOW)
    mock_write.assert_not_called()
    mock_get.assert_not_called()  # 진입 지점에서 막혀 라이브 재조회조차 안 나간다
    db.refresh(p)
    assert p.status == "approved"  # 미실행 정직 상태(executing 잔존 금지)
    assert db.query(NaverChangeLog).count() == 0


def test_inflight_ad_proposal_blocks_duplicate_fire(db):
    """(2) 같은 소재에 executing 제안이 있으면 새 하향을 만들지 않는다(동시 실행 중복 쓰기)."""
    _seed_hourly_shopping(db)
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)
    db.add(NaverProposal(
        proposal_type="bid_down", target_type="ad", target_id="nad-1", campaign_id=CAMP,
        adgroup_id="grp-hot", status="executing", target_bid=680,
    ))
    db.commit()
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_sa_writer, "get_ad_bid", return_value=800), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    mock_exec.assert_not_called()
    assert result["ad_auto_exec_inflight_skipped"] == 1
    assert result["approved"] == 0


def test_lane_cap_degrades_excess_to_confirm_pending(db):
    """(4) 레인당 상한 초과분은 **드롭이 아니라** Confirm 대기로 남는다."""
    n_groups = auto_operator._MAX_AD_AUTO_EXEC_PER_LANE + 2
    db.add(NaverCampaignSettings(campaign_id=CAMP, optimizer="ours", auto_operate=True))
    db.add(NaverEntity(entity_type="campaign", entity_id=CAMP, campaign_id=CAMP, status="on"))
    db.add(NaverHourlySnapshot(snapshot_at=NOW, ad_date=TODAY, snapshot_hour=23,
                               campaign_id=CAMP, campaign_type="", cost=0, clk=0, imp=0))
    window_from, _ = auto_operator._settlement_window(TODAY)
    for i in range(n_groups):
        gid = f"grp-{i}"
        db.add(NaverEntity(entity_type="adgroup", entity_id=gid, parent_id=CAMP,
                           campaign_id=CAMP, campaign_type="SHOPPING", status="on"))
        db.add(NaverAdDaily(ad_date=window_from, campaign_id=CAMP, campaign_type="SHOPPING",
                            adgroup_id=gid, keyword_id="", imp=200, clk=20, cost=2000))
        _ad(db, gid, f"p{i}", ad_id=f"nad-{i}", ad_bid_amt=800, use_group=False)
    db.commit()
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_sa_writer, "get_ad_bid", return_value=800), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    assert result["approved"] == auto_operator._MAX_AD_AUTO_EXEC_PER_LANE
    assert mock_exec.call_count == auto_operator._MAX_AD_AUTO_EXEC_PER_LANE
    assert result["ad_auto_exec_capped"] == n_groups - auto_operator._MAX_AD_AUTO_EXEC_PER_LANE
    assert result["ad_confirm_pending"] == n_groups - auto_operator._MAX_AD_AUTO_EXEC_PER_LANE
    # 강등분은 사라지지 않고 큐에 보인다
    assert db.query(NaverProposal).filter(NaverProposal.status == "pending").count() == \
        n_groups - auto_operator._MAX_AD_AUTO_EXEC_PER_LANE


# ── codex 2R 회귀(2026-07-29) — 1R 수정이 만든 결함 3건 + 누적 방지 ──────────────
def test_kill_switch_does_not_block_human_console_approval(db):
    """★codex 2R[P1] 회귀: 킬스위치는 **자동 발사**를 끄는 것이지 사람 손을 묶는 게 아니다.

    사고 대응 중 자동화를 내린 상태에서 Jino가 콘솔로 직접 하향을 승인하면 실행돼야 한다.
    1R은 조건이 `approval_source is not None`이라 'console'까지 막았다.
    """
    db.add(NaverCampaignSettings(campaign_id=CAMP, optimizer="ours", auto_operate=True))
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)
    p = NaverProposal(
        proposal_type="bid_down", target_type="ad", target_id="nad-1", campaign_id=CAMP,
        adgroup_id="grp-hot", status="approved", target_bid=760, approval_source="console",
    )
    db.add(p)
    db.commit()
    after = {"nccAdId": "nad-1", "adAttr": {"bidAmt": 760, "useGroupBidAmt": False}}
    with patch.object(auto_operator, "ad_bid_routing_enabled", lambda db: False), \
         patch.object(writer, "get_ad_bid", return_value=800), \
         patch.object(writer, "update_ad_bid",
                      return_value=WriteResult(action="update_ad_bid", before={"bidAmt": 800},
                                               response=None, after=after, created_ids=[])) as mock_write:
        harness.execute(db, p.id, dry_run=False, now=NOW)
    mock_write.assert_called_once()  # 사람 승인은 스위치와 무관하게 나간다


def test_target_level_claim_blocks_second_proposal_on_same_ad(db):
    """★codex 2R[P1] 회귀: 같은 소재에 executing 제안이 있으면 **다른 proposal도** 클레임 실패.

    레인의 사전 조회는 조회-생성 사이가 열려 있어 경쟁을 못 막는다. 진짜 방어는 클레임
    UPDATE 한 문장 안에서 대상 단위로 판정하는 것이다.
    """
    db.add(NaverCampaignSettings(campaign_id=CAMP, optimizer="ours", auto_operate=True))
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)
    first = NaverProposal(  # 다른 실행자가 이미 클레임한 상태
        proposal_type="bid_down", target_type="ad", target_id="nad-1", campaign_id=CAMP,
        adgroup_id="grp-hot", status="executing", target_bid=680,
        approval_source=auto_operator.APPROVAL_SOURCE_HOURLY,
    )
    second = NaverProposal(
        proposal_type="bid_down", target_type="ad", target_id="nad-1", campaign_id=CAMP,
        adgroup_id="grp-hot", status="approved", target_bid=680,
        approval_source=auto_operator.APPROVAL_SOURCE_HOURLY,
    )
    db.add_all([first, second])
    db.commit()
    with patch.object(writer, "get_ad_bid", return_value=800), \
         patch.object(writer, "update_ad_bid") as mock_write:
        with pytest.raises(harness.AlreadyExecutedError):
            harness.execute(db, second.id, dry_run=False, now=NOW)
    mock_write.assert_not_called()
    db.refresh(second)
    assert second.status == "approved"  # 미실행 정직 상태


def test_auto_down_cap_counts_only_auto_downs(db):
    """★codex 2R[P1] 회귀: 상한 원료는 '오늘 모든 변경'이 아니라 '자동 하향 성공'이다.

    같은 소재에 오늘 사람 하향 1 + 자동 상향 2가 있어도 자동 하향은 0회 → 상한 미도달.
    """
    def _log(pt, src, action="update_bid"):
        pr = NaverProposal(proposal_type=pt, target_type="ad", target_id="nad-1",
                           campaign_id=CAMP, status="approved", approval_source=src)
        db.add(pr)
        db.flush()
        db.add(NaverChangeLog(
            entity_type="ad", entity_id="nad-1", campaign_id=CAMP, action=action,
            dry_run=False, changed_at=NOW, executed_at=NOW, proposal_id=pr.id,
            after_value=json.dumps({"bidAmt": 700}),
        ))

    _log("bid_down", "console")                              # 사람 하향
    _log("bid_up_servo", auto_operator.APPROVAL_SOURCE_HOURLY)  # 자동 상향
    _log("bid_up_servo", auto_operator.APPROVAL_SOURCE_HOURLY)
    db.commit()
    assert harness.count_auto_bid_down_today(db, "ad", "nad-1", NOW) == 0
    _log("bid_down", auto_operator.APPROVAL_SOURCE_HOURLY)   # 자동 하향 1회
    db.commit()
    assert harness.count_auto_bid_down_today(db, "ad", "nad-1", NOW) == 1


def test_capped_pending_is_replaced_not_accumulated(db):
    """★codex 2R[P2] 회귀: 캡 강등 카드가 매시간 쌓이지 않는다(같은 소재는 항상 1장)."""
    n_groups = auto_operator._MAX_AD_AUTO_EXEC_PER_LANE + 1
    db.add(NaverCampaignSettings(campaign_id=CAMP, optimizer="ours", auto_operate=True))
    db.add(NaverEntity(entity_type="campaign", entity_id=CAMP, campaign_id=CAMP, status="on"))
    db.add(NaverHourlySnapshot(snapshot_at=NOW, ad_date=TODAY, snapshot_hour=23,
                               campaign_id=CAMP, campaign_type="", cost=0, clk=0, imp=0))
    window_from, _ = auto_operator._settlement_window(TODAY)
    for i in range(n_groups):
        gid = f"grp-{i}"
        db.add(NaverEntity(entity_type="adgroup", entity_id=gid, parent_id=CAMP,
                           campaign_id=CAMP, campaign_type="SHOPPING", status="on"))
        db.add(NaverAdDaily(ad_date=window_from, campaign_id=CAMP, campaign_type="SHOPPING",
                            adgroup_id=gid, keyword_id="", imp=200, clk=20, cost=2000))
        _ad(db, gid, f"p{i}", ad_id=f"nad-{i}", ad_bid_amt=800, use_group=False)
    db.commit()
    for _ in range(2):  # 두 회차 연속
        with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
             patch.object(auto_operator.naver_sa_writer, "get_ad_bid", return_value=800), \
             patch.object(auto_operator.naver_execution_harness, "execute"):
            auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    pendings = db.query(NaverProposal).filter(
        NaverProposal.target_type == "ad", NaverProposal.status == "pending"
    ).all()
    assert len({p.target_id for p in pendings}) == len(pendings)  # 소재당 1장


def test_claim_race_across_separate_connections(tmp_path):
    """★codex 3R[P2]: **서로 다른 커넥션** 두 개가 같은 소재를 클레임하면 하나만 성공한다.

    앞선 회귀는 이미 executing인 정적 상태만 봤다. 이 테스트는 파일 DB + 독립 세션 2개로,
    NOT EXISTS 판정이 UPDATE 실행 시점에 **상대 커넥션이 커밋한 최신 상태**를 보는지 확인한다
    (ORM 아이덴티티 맵의 낡은 스냅샷이 아니라). 그게 이 방어의 유일한 근거다.
    """
    url = f"sqlite:///{tmp_path}/race.db"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    setup = Session()
    setup.add(NaverCampaignSettings(campaign_id=CAMP, optimizer="ours", auto_operate=True))
    setup.add(NaverAdgroupProduct(adgroup_id="grp-hot", campaign_id=CAMP, mall_product_id="p1",
                                  product_name="p1", ad_id="nad-1", ad_bid_amt=800,
                                  use_group_bid_amt=False))
    ids = []
    for _ in range(2):
        pr = NaverProposal(
            proposal_type="bid_down", target_type="ad", target_id="nad-1", campaign_id=CAMP,
            adgroup_id="grp-hot", status="approved", target_bid=680,
            approval_source=auto_operator.APPROVAL_SOURCE_HOURLY,
        )
        setup.add(pr)
        setup.flush()
        ids.append(pr.id)
    setup.commit()
    setup.close()

    s_a, s_b = Session(), Session()
    try:
        # A가 클레임만 하고 아직 쓰기 중(= executing 상태로 커밋). 이게 동시 실행 구간이다.
        p_a = s_a.query(NaverProposal).filter(NaverProposal.id == ids[0]).one()
        harness._claim_executing(s_a, p_a)
        assert p_a.status == "executing"
        # B는 **다른 커넥션**에서 자기 proposal(id가 다르다)을 실행하려 한다.
        with patch.object(writer, "get_ad_bid", return_value=800), \
             patch.object(writer, "update_ad_bid") as mock_write:
            with pytest.raises(harness.AlreadyExecutedError):
                harness.execute(s_b, ids[1], dry_run=False, now=NOW)
        mock_write.assert_not_called()  # 같은 소재에 두 번째 PUT이 나가지 않는다
        assert s_b.query(NaverProposal).filter(
            NaverProposal.id == ids[1]).one().status == "approved"
    finally:
        s_a.close()
        s_b.close()


def test_pending_replacement_policy_covers_all_producers(db):
    """★codex 3R[P2] 정책 명문화: 자동 하향 카드를 만들 때 같은 (타입, 소재)의 pending은
    **생산자와 무관하게** 만료된다.

    의도된 동작이다 — 레인이 지금 값으로 판정해 새 카드를 만드는 시점에서, 같은 소재·같은
    방향의 옛 카드는 어느 경로가 만들었든 이미 낡았다(입찰가·근거가 그때 값이다). 다만
    '조용히 사라지는' 것이 아니라 status='expired'로 남아 이력에서 보인다.
    """
    _seed_hourly_shopping(db)
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)
    other = NaverProposal(  # 다른 생산자(일 레인 proposal_writer 등)가 만든 카드
        proposal_type="bid_down", target_type="ad", target_id="nad-1", campaign_id=CAMP,
        adgroup_id="grp-hot", status="pending", target_bid=700, rationale="[일레인] 옛 판정",
    )
    db.add(other)
    db.commit()
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_sa_writer, "get_ad_bid", return_value=800), \
         patch.object(auto_operator.naver_execution_harness, "execute"):
        auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    db.refresh(other)
    assert other.status == "expired"  # 사라지지 않고 만료로 남는다(이력 보존)


# ── D-NAO-129 상향 안전장치 회귀 ────────────────────────────────────────────
def test_ad_up_fails_closed_without_bep_context(db):
    """★소재 상향은 근거(BEP·일예산) 없이는 못 나간다 — 컨텍스트 불완전이면 fail-closed.

    guardrail_gate의 up 전용 검사(BEP·스톱로스·일예산)는 원료가 None이면 **검사를 건너뛴다**
    (fail-open 성질). 그래서 executor가 실행 직전 완전성을 종합 확인한다. 상향은 현금이
    나가는 방향이라 이 게이트가 하향보다 중요하다.
    """
    db.add(NaverCampaignSettings(campaign_id=CAMP, optimizer="ours", auto_operate=True))
    p = NaverProposal(
        proposal_type="bid_up", target_type="ad", target_id="nad-1", campaign_id=CAMP,
        adgroup_id="grp-hot", status="approved", target_bid=920,
        approval_source=auto_operator.APPROVAL_SOURCE_HOURLY,
    )
    db.add(p)
    db.commit()
    with patch.object(writer, "get_ad_bid", return_value=800), \
         patch.object(writer, "update_ad_bid") as mock_write:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False, now=NOW)
    mock_write.assert_not_called()
    log = db.query(NaverChangeLog).one()
    assert "증액 가드 컨텍스트 불완전" in (log.rationale or "")


def test_ad_up_blocked_below_bep_and_capped_daily(db):
    """BEP 미달 증액 금지(D-NAO-1)와 일일 상한 3회가 소재 상향에도 그대로 걸린다.

    ★하향과 달리 bid_up은 DL3(일일상한 면제) 대상이 아니다 — 올리는 쪽은 횟수 제한이 있다.
    """
    from app.services.naver_ad import guardrail_gate

    base = {"current_bid": 800, "campaign_type": "SHOPPING", "changes_today_count": 0,
            "unconverted_spend": 0, "cost_today": 0, "daily_budget": 300000}
    below_bep = guardrail_gate.check(
        {"proposal_type": "bid_up", "target_bid": 920},
        {**base, "roas_corrected": 1.2, "target_roas": 1.9611}, now=NOW,
    )
    assert below_bep is not None and "BEP 미달 증액 금지" in below_bep

    capped = guardrail_gate.check(
        {"proposal_type": "bid_up", "target_bid": 920},
        {**base, "changes_today_count": 3, "roas_corrected": 3.0, "target_roas": 1.9611,
         "auto_exec": True}, now=NOW,
    )
    assert capped is not None and "일일 변경 건수 상한" in capped

    ok = guardrail_gate.check(
        {"proposal_type": "bid_up", "target_bid": 920},
        {**base, "roas_corrected": 3.0, "target_roas": 1.9611, "auto_exec": True}, now=NOW,
    )
    assert ok is None  # 이익 구간·상한 미도달이면 통과


def test_ad_up_step_is_clamped_to_15pct(db):
    """소재 상향 스텝은 ±15% 클램프를 넘지 않는다(면제 타입이 아니라는 것의 실효 확인)."""
    from app.services.naver_ad import guardrail_gate

    blocked = guardrail_gate.check(
        {"proposal_type": "bid_up", "target_bid": 1600},  # 800 → +100%
        {"current_bid": 800, "campaign_type": "SHOPPING", "changes_today_count": 0,
         "roas_corrected": 5.0, "target_roas": 1.9611, "unconverted_spend": 0,
         "cost_today": 0, "daily_budget": 300000}, now=NOW,
    )
    assert blocked is not None and "변경폭" in blocked


# ── D-NAO-129 codex 적대 리뷰 P1 2건 회귀 ────────────────────────────────────
def test_cas_blocks_write_when_bid_changed_after_judgement(db):
    """★codex 적대[P1] TOCTOU: 판정 이후 입찰이 바뀌면 PUT하지 않는다.

    재현(오늘 실제로 일어난 종류의 사건 — 대행사가 15:39에 소재를 되돌렸다):
    executor가 800원을 읽고 920원(+15%)을 승인 → 그 사이 외부가 400원으로 내림 →
    종전 코드는 새 before(400)를 읽고도 재검증 없이 920을 PUT했다(= 실제 +130%).
    """
    from app.services.naver_ad import naver_sa_writer as w

    live_800 = {"nccAdId": "nad-1", "nccAdgroupId": "grp-hot", "userLock": False,
                "adAttr": {"bidAmt": 800, "useGroupBidAmt": False}}
    live_400 = {**live_800, "adAttr": {"bidAmt": 400, "useGroupBidAmt": False}}  # 외부가 내림
    # ★잔여 창을 재현한다(codex 적대 2R[P1]): 첫 GET에서는 800이라 통과하고, **PUT 직전**
    #   마지막 확인에서 400으로 바뀐 것을 잡아야 한다. 초판은 첫 GET에서만 비교해 이 창을
    #   놓쳤다(부모그룹 조회·body 조립 구간이 통째로 열려 있었다).
    with patch.object(w, "get_ad", side_effect=[live_800, live_400]), \
         patch.object(w, "_get_adgroup", return_value={"systemBiddingType": "NONE",
                                                      "autobidStrategy": {"isAutobidActive": False}}), \
         patch("requests.put") as mput:
        with pytest.raises(w.WriteValidationError) as ei:
            w.update_ad_bid("nad-1", 920, expected_before_bid=800)  # 800 기준으로 판정했었다
    mput.assert_not_called()  # ★PUT이 아예 나가지 않는다
    assert "기준가 불일치" in str(ei.value)


def test_cas_allows_write_when_bid_unchanged(db):
    """CAS는 기준가가 그대로일 때만 통과시킨다(정상 경로 회귀 0)."""
    from app.services.naver_ad import naver_sa_writer as w

    before_live = {"nccAdId": "nad-1", "nccAdgroupId": "grp-hot", "userLock": False,
                   "adAttr": {"bidAmt": 800, "useGroupBidAmt": False}}
    after_live = {**before_live, "adAttr": {"bidAmt": 920, "useGroupBidAmt": False}}
    resp = type("R", (), {"status_code": 200, "json": lambda self: {}, "text": ""})()
    # GET 3회: ①before ②PUT 직전 기준가 재확인 ③after 검증
    with patch.object(w, "get_ad", side_effect=[before_live, before_live, after_live]), \
         patch.object(w, "_get_adgroup", return_value={"systemBiddingType": "NONE",
                                                      "autobidStrategy": {"isAutobidActive": False}}), \
         patch("requests.put", return_value=resp):
        result = w.update_ad_bid("nad-1", 920, expected_before_bid=800)
    assert result.action == "update_ad_bid"


def test_auto_up_cumulative_multiple_cap(db):
    """★codex 적대[P1]: 자동 상향은 기준가의 2배에서 멈춘다.

    BEP는 소재가 아니라 **부모 그룹 30일 집계**라, 같은 그룹에 매출 내는 소재가 있으면
    전환 0인 소재도 계속 통과한다. 그때 남는 절대 상한은 100,000원뿐이라 ±15%×3회/일 복리로
    12일이면 거기 닿는다. 이 상한이 "언제 멈추나"에 답한다.
    """
    from app.services.naver_ad import guardrail_gate

    base = {"current_bid": 1500, "campaign_type": "SHOPPING", "changes_today_count": 0,
            "roas_corrected": 5.0, "target_roas": 1.9611, "unconverted_spend": 0,
            "cost_today": 0, "daily_budget": 300000, "auto_exec": True}
    # 기준가 800 → 상한 1600. 1,720원(1500의 +15%)은 상한 초과라 차단.
    blocked = guardrail_gate.check(
        {"proposal_type": "bid_up", "target_bid": 1720}, {**base, "auto_up_base_bid": 800}, now=NOW,
    )
    assert blocked is not None and "자동 상향 누적 상한" in blocked
    # 사람 승인(auto_exec=False)은 이 상한 대상이 아니다 — 사람이 새 기준점을 잡는다.
    assert guardrail_gate.check(
        {"proposal_type": "bid_up", "target_bid": 1720},
        {**base, "auto_up_base_bid": 800, "auto_exec": False}, now=NOW,
    ) is None
    # 기준점이 없으면(첫 스텝) 미적용.
    assert guardrail_gate.check(
        {"proposal_type": "bid_up", "target_bid": 1720},
        {**base, "auto_up_base_bid": None}, now=NOW,
    ) is None


def test_auto_up_base_bid_is_anchored_to_human_not_a_time_window(db):
    """★codex 적대 2R[P1] 회귀: 기준점은 **사람이 마지막으로 정한 값**이고 시간이 지나도 안 풀린다.

    초판은 "최근 7일"이라 창이 만료되면 기준점이 사라지고 그때의 높은 값이 새 기준이 됐다 —
    800→1,600에서 막혀도 일주일 뒤 1,600→3,200 → … 계단식으로 100,000에 닿는다. 상한이
    상승을 늦출 뿐 멈추지 못했고, 그 우회를 테스트가 계약으로 고정하고 있었다.
    """
    def _log(bid_before, bid_after, src, at, ptype="bid_up"):
        pr = NaverProposal(proposal_type=ptype, target_type="ad", target_id="nad-1",
                           campaign_id=CAMP, status="approved", approval_source=src)
        db.add(pr)
        db.flush()
        db.add(NaverChangeLog(
            entity_type="ad", entity_id="nad-1", campaign_id=CAMP, action="update_bid",
            dry_run=False, changed_at=at, executed_at=at, proposal_id=pr.id,
            before_value=json.dumps({"adAttr": {"bidAmt": bid_before, "useGroupBidAmt": False}}),
            after_value=json.dumps({"adAttr": {"bidAmt": bid_after, "useGroupBidAmt": False}}),
        ))

    _log(800, 920, auto_operator.APPROVAL_SOURCE_HOURLY, NOW - timedelta(days=40))
    _log(920, 1060, auto_operator.APPROVAL_SOURCE_HOURLY, NOW - timedelta(days=39))
    db.commit()
    # 40일이 지나도 기준점은 그대로 800이다(자동 만료 없음 = 계단식 상승 봉쇄).
    assert harness.auto_up_base_bid(db, "ad", "nad-1", NOW) == 800

    # 사람이 콘솔에서 2,000으로 올리면 **그 값이 새 기준점**이 된다(상한 4,000).
    _log(1060, 2000, "console", NOW - timedelta(days=1))
    db.commit()
    assert harness.auto_up_base_bid(db, "ad", "nad-1", NOW) == 2000

    # 사람이 내린 경우도 같다 — 기준점은 사람이 정한 값을 따라간다(위로만 유리하게 남지 않는다).
    _log(2000, 400, "console", NOW)
    db.commit()
    assert harness.auto_up_base_bid(db, "ad", "nad-1", NOW) == 400


def test_auto_up_anchor_ignores_other_lane_types(db):
    """탐색·콜드 이력은 이 기준점에 섞이지 않는다(각자 경제성 상한을 가진 별도 레인)."""
    pr = NaverProposal(proposal_type="bid_up_cold", target_type="ad", target_id="nad-1",
                       campaign_id=CAMP, status="approved", approval_source="cold_op")
    db.add(pr)
    db.flush()
    db.add(NaverChangeLog(
        entity_type="ad", entity_id="nad-1", campaign_id=CAMP, action="update_bid",
        dry_run=False, changed_at=NOW, executed_at=NOW, proposal_id=pr.id,
        before_value=json.dumps({"adAttr": {"bidAmt": 300, "useGroupBidAmt": False}}),
        after_value=json.dumps({"adAttr": {"bidAmt": 3000, "useGroupBidAmt": False}}),
    ))
    db.commit()
    assert harness.auto_up_base_bid(db, "ad", "nad-1", NOW) is None


def test_cumulative_cap_types_match_ad_up_open_types():
    """두 모듈의 상수는 같은 값이어야 한다(역방향 import를 피해 값으로 고정한 자리)."""
    from app.services.naver_ad import guardrail_gate

    assert guardrail_gate._CUMULATIVE_CAP_TYPES == harness._AD_UP_OPEN_TYPES


def test_console_ad_up_passes_blocker_and_reaches_writer(db):
    """★codex 적대 2R[P2]: 콘솔 계약과 executor 계약을 **한 테스트에서** 함께 고정한다.

    blocker가 None을 준다는 것만으로는 "콘솔에서 승인하면 실제로 나간다"가 보장되지 않는다.
    """
    db.add(NaverCampaignSettings(campaign_id=CAMP, optimizer="ours", auto_operate=True))
    p = NaverProposal(
        proposal_type="bid_up", target_type="ad", target_id="nad-1", campaign_id=CAMP,
        adgroup_id="grp-hot", status="approved", target_bid=920, approval_source="console",
    )
    db.add(p)
    db.commit()
    assert harness.real_write_blocker(p) is None
    # 상향은 사람 승인이어도 BEP·일예산 근거가 있어야 나간다(완전성 게이트는 승인원 무관).
    full_ctx = {"current_bid": 800, "auto_exec": False, "roas_corrected": 5.0,
                "target_roas": 1.9611, "unconverted_spend": 0, "cost_today": 0,
                "daily_budget": 300000}
    with patch.object(harness, "_build_guardrail_context", return_value=full_ctx), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_ad_bid",
                      return_value=_ad_write_result()) as mad:
        harness.execute(db, p.id, dry_run=False, now=NOW)
    mad.assert_called_once_with("nad-1", 920, expected_before_bid=800)


# ── codex 적대 3R P1 2건 회귀 ────────────────────────────────────────────────
def test_agency_lowering_bid_resets_the_anchor(db):
    """★codex 적대 3R[P1]: 대행사가 콘솔에서 내린 값이 자동화의 새 기준점이 된다.

    재현(오늘 15:39 대행사가 한 일): 우리 사람이 2,000으로 정함 → 대행사가 400으로 내림.
    우리 change_log엔 그 하향이 없으므로 종전 정의("우리 앱에서 사람이 정한 값")로는 기준점이
    2,000에 머물고, 자동화가 400 → 4,000까지 **10배로 되돌린다**. D-NAO-127이 남긴 소재
    외부변경 기록을 사람 개입과 같은 급의 재설정 사건으로 쓴다.
    ★min(기준점, 현재값)으로 푸는 것은 틀렸다 — 기준점이 현재값을 따라 올라가(400→460→529…)
      천장이 언제나 현재값의 2배가 되어 상한이 영영 안 걸린다(방어를 넣으면서 방어를 끄는 셈).
    """
    db.add(NaverChangeLog(  # 우리 사람이 2,000으로 설정
        entity_type="ad", entity_id="nad-1", campaign_id=CAMP, action="update_bid",
        dry_run=False, changed_at=NOW - timedelta(hours=5), executed_at=NOW - timedelta(hours=5),
        proposal_id=None, after_value=json.dumps({"adAttr": {"bidAmt": 2000}}),
    ))
    db.commit()
    assert harness.auto_up_base_bid(db, "ad", "nad-1", NOW) == 2000

    db.add(NaverAgencyOp(  # 대행사가 400으로 내린 것을 D-NAO-127이 관측
        op_date=NOW.date(), detected_at=NOW, entity_type="ad", entity_id="nad-1",
        campaign_id=CAMP, optimizer="ours", op_type="bid_change",
        before_value="2000", after_value="400", is_exception=True,
        occurred_at=NOW - timedelta(hours=1),
    ))
    db.commit()
    assert harness.auto_up_base_bid(db, "ad", "nad-1", NOW) == 400  # 새 기준점 = 대행사가 세운 값


def test_cumulative_cap_binds_across_steps(db):
    """상한이 실제로 **누적**을 막는다 — 한 스텝이 아니라 기준점 대비 총 상승폭이 기준이다."""
    from app.services.naver_ad import guardrail_gate

    ctx = {"current_bid": 1500, "campaign_type": "SHOPPING", "changes_today_count": 0,
           "roas_corrected": 5.0, "target_roas": 1.9611, "unconverted_spend": 0,
           "cost_today": 0, "daily_budget": 300000, "auto_exec": True,
           "auto_up_base_bid": 800}  # 사람이 정한 출발점 800 → 천장 1,600
    assert guardrail_gate.check({"proposal_type": "bid_up", "target_bid": 1600}, ctx, now=NOW) is None
    blocked = guardrail_gate.check({"proposal_type": "bid_up", "target_bid": 1720}, ctx, now=NOW)
    assert blocked is not None and "자동 상향 누적 상한" in blocked


def test_writer_refuses_to_revert_concurrent_group_bid_flip(db):
    """★codex 적대 3R[P1]: 그 사이 외부가 useGroupBidAmt를 켰으면 되돌리지 않는다.

    body를 첫 GET으로 조립해두면 우리가 false를 다시 써서 **남의 변경을 조용히 되돌린 뒤
    성공으로 기록**한다(after 검증은 false를 성공 조건으로 보고, 최종 editTm이 우리 것이라
    D-NAO-127 사후 탐지도 "우리 쓰기"로 분류한다 = 아무도 못 잡는다).
    """
    from app.services.naver_ad import naver_sa_writer as w

    first = {"nccAdId": "nad-1", "nccAdgroupId": "grp-hot", "userLock": False,
             "adAttr": {"bidAmt": 800, "useGroupBidAmt": False}}
    flipped = {**first, "adAttr": {"bidAmt": 800, "useGroupBidAmt": True}}  # 외부가 그룹입찰로 전환
    with patch.object(w, "get_ad", side_effect=[first, flipped]), \
         patch.object(w, "_get_adgroup", return_value={"systemBiddingType": "NONE",
                                                      "autobidStrategy": {"isAutobidActive": False}}), \
         patch("requests.put") as mput:
        with pytest.raises(w.WriteValidationError) as ei:
            w.update_ad_bid("nad-1", 920, expected_before_bid=800)
    mput.assert_not_called()
    assert "useGroupBidAmt" in str(ei.value)


def test_writer_rebuilds_body_from_latest_response(db):
    """PUT body는 **최신 응답**으로 만든다 — 그 사이 바뀐 다른 필드를 덮지 않기 위해서다."""
    from app.services.naver_ad import naver_sa_writer as w

    first = {"nccAdId": "nad-1", "nccAdgroupId": "grp-hot", "userLock": False, "inspectStatus": "OLD",
             "adAttr": {"bidAmt": 800, "useGroupBidAmt": False}}
    latest = {**first, "inspectStatus": "NEW"}  # 외부/네이버가 다른 필드를 갱신
    after = {**latest, "adAttr": {"bidAmt": 920, "useGroupBidAmt": False}}
    resp = type("R", (), {"status_code": 200, "json": lambda self: {}, "text": ""})()
    with patch.object(w, "get_ad", side_effect=[first, latest, after]), \
         patch.object(w, "_get_adgroup", return_value={"systemBiddingType": "NONE",
                                                      "autobidStrategy": {"isAutobidActive": False}}), \
         patch("requests.put", return_value=resp) as mput:
        w.update_ad_bid("nad-1", 920, expected_before_bid=800)
    sent = mput.call_args.kwargs["json"]
    assert sent["inspectStatus"] == "NEW"  # 낡은 스냅샷으로 덮어쓰지 않는다
    assert sent["adAttr"]["bidAmt"] == 920


def test_writer_refuses_when_ad_moved_to_another_group(db):
    """판정 이후 소재가 다른 그룹으로 옮겨졌으면 쓰지 않는다(부모 ML 가드를 통과한 그룹이 아님)."""
    from app.services.naver_ad import naver_sa_writer as w

    first = {"nccAdId": "nad-1", "nccAdgroupId": "grp-hot", "userLock": False,
             "adAttr": {"bidAmt": 800, "useGroupBidAmt": False}}
    moved = {**first, "nccAdgroupId": "grp-other"}
    with patch.object(w, "get_ad", side_effect=[first, moved]), \
         patch.object(w, "_get_adgroup", return_value={"systemBiddingType": "NONE",
                                                      "autobidStrategy": {"isAutobidActive": False}}), \
         patch("requests.put") as mput:
        with pytest.raises(w.WriteValidationError):
            w.update_ad_bid("nad-1", 920, expected_before_bid=800)
    mput.assert_not_called()


def test_human_anchor_falls_back_when_latest_row_unparseable(db):
    """codex 적대 3R[P2]: 최신 사람 이력이 깨져 있으면 그 이전 유효 행을 쓴다(상한 유지)."""
    for after_val, at in ((json.dumps({"adAttr": {"bidAmt": 400}}), NOW - timedelta(hours=2)),
                          ("깨진 payload", NOW - timedelta(hours=1))):
        db.add(NaverChangeLog(
            entity_type="ad", entity_id="nad-1", campaign_id=CAMP, action="update_bid",
            dry_run=False, changed_at=at, executed_at=at, proposal_id=None,
            after_value=after_val,
        ))
    db.commit()
    assert harness.auto_up_base_bid(db, "ad", "nad-1", NOW) == 400


# ── D-NAO-130 자동 상향 개방의 두 축 회귀 ────────────────────────────────────
def test_write_time_external_touch_resets_anchor(db):
    """★codex 적대 4R[P1] 회귀: 외부 변경을 **쓰기 직전에** 잡아 기준점을 즉시 재설정한다.

    하루 1회 탐지에만 기대면, 우리가 먼저 쓰는 순간 editTm이 우리 것으로 바뀌어 외부 사건이
    영구 소실된다(07:45 기준 2,000 → 10:00 대행사 400 → 11:00 우리 460 → 다음날 "우리 쓰기"로
    분류 → 기준점 영영 2,000 → 자동화가 4,000까지 되돌림). 매 판정마다 대조해서 그 창을 없앤다.
    """
    _settings(db, optimizer="ours")
    db.add(NaverAdgroupProduct(
        adgroup_id="grp-hot", campaign_id=CAMP, mall_product_id="p1", product_name="p1",
        ad_id=AD_ID, ad_bid_amt=2000, use_group_bid_amt=False,
        ad_edit_tm="2026-07-29T01:00:00.000Z",  # 우리가 아는 마지막 editTm
    ))
    db.add(NaverChangeLog(  # 사람이 2,000으로 정했던 이력(종전 기준점)
        entity_type="ad", entity_id=AD_ID, campaign_id=CAMP, action="update_bid",
        dry_run=False, changed_at=NOW - timedelta(hours=5), executed_at=NOW - timedelta(hours=5),
        proposal_id=None, after_value=json.dumps({"adAttr": {"bidAmt": 2000}}),
    ))
    p = _ad_proposal(db, proposal_type="bid_up", target_bid=460)
    p.approval_source = auto_operator.APPROVAL_SOURCE_HOURLY
    db.commit()

    live = {"nccAdId": AD_ID, "editTm": "2026-07-29T06:39:05.000Z",  # 우리가 모르는 editTm
            "adAttr": {"bidAmt": 400, "useGroupBidAmt": False}}
    with patch.object(harness.naver_sa_writer, "get_ad_bid", return_value=400), \
         patch.object(harness.naver_sa_writer, "get_ad", return_value=live):
        ctx = harness._build_guardrail_context(db, p, NOW)
    assert ctx["auto_up_base_bid"] == 400  # 종전 기준점 2,000이 아니라 현재 실측값
    op = db.query(NaverAgencyOp).filter(NaverAgencyOp.entity_type == "ad").one()
    assert (op.op_type, op.after_value) == ("bid_change", "400")  # 사건이 소실되지 않는다


def test_write_time_check_silent_when_edit_tm_is_ours(db):
    """우리가 쓴 editTm이면 외부 변경이 아니다 — 유령 사건을 만들지 않는다."""
    _settings(db, optimizer="ours")
    db.add(NaverAdgroupProduct(
        adgroup_id="grp-hot", campaign_id=CAMP, mall_product_id="p1", product_name="p1",
        ad_id=AD_ID, ad_bid_amt=800, use_group_bid_amt=False,
        ad_edit_tm="2026-07-28T01:00:00.000Z",
    ))
    db.add(NaverChangeLog(  # 우리 쓰기가 남긴 editTm
        entity_type="ad", entity_id=AD_ID, campaign_id=CAMP, action="update_bid",
        dry_run=False, changed_at=NOW - timedelta(hours=1), executed_at=NOW - timedelta(hours=1),
        proposal_id=None, after_value=json.dumps({"editTm": "2026-07-29T02:00:00.000Z",
                                                  "adAttr": {"bidAmt": 800}}),
    ))
    p = _ad_proposal(db, proposal_type="bid_up", target_bid=920)
    p.approval_source = auto_operator.APPROVAL_SOURCE_HOURLY
    db.commit()
    live = {"nccAdId": AD_ID, "editTm": "2026-07-29T02:00:00.000Z",
            "adAttr": {"bidAmt": 800, "useGroupBidAmt": False}}
    with patch.object(harness.naver_sa_writer, "get_ad_bid", return_value=800), \
         patch.object(harness.naver_sa_writer, "get_ad", return_value=live):
        harness._build_guardrail_context(db, p, NOW)
    assert db.query(NaverAgencyOp).count() == 0


def test_down_after_auto_up_is_exempt_from_cooldown(db):
    """★D-NAO-130: 되먹임 루프의 **교정 단계**는 쿨다운에 막히지 않는다.

    올렸는데 손실로 뒤집히면 즉시 되돌려야 한다 — 그게 자동 상향을 켠 근거다.
    교정을 막는 브레이크는 브레이크가 아니라 고장이다.
    """
    from app.services.naver_ad import guardrail_gate

    just_now = {"last_change_at": NOW - timedelta(minutes=10), "changes_today_count": 1,
                "current_bid": 920, "campaign_type": "SHOPPING"}
    # 직전 변경이 우리 자동 상향 → 하향은 쿨다운 면제
    assert guardrail_gate.check(
        {"proposal_type": "bid_down", "target_bid": 790},
        {**just_now, "last_change_was_auto_up": True, "auto_exec": True,
         "auto_bid_down_today": 0}, now=NOW,
    ) is None
    # 그 플래그가 없으면 종전대로 쿨다운에 막힌다(면제는 이 경우 한정)
    blocked = guardrail_gate.check(
        {"proposal_type": "bid_down", "target_bid": 790},
        {**just_now, "auto_exec": True, "auto_bid_down_today": 0}, now=NOW,
    )
    assert blocked is not None and "쿨다운" in blocked
    # 상향에는 면제가 없다 — 진동 방지(올린 뒤 곧바로 또 올리지 않는다)
    up_blocked = guardrail_gate.check(
        {"proposal_type": "bid_up", "target_bid": 1050},
        {**just_now, "last_change_was_auto_up": True, "auto_exec": True,
         "roas_corrected": 5.0, "target_roas": 1.9, "unconverted_spend": 0,
         "cost_today": 0, "daily_budget": 300000}, now=NOW,
    )
    assert up_blocked is not None and "쿨다운" in up_blocked


def test_last_change_was_auto_up_ignores_human(db):
    """사람이 올린 것은 면제 대상이 아니다 — 자동이 2시간 안에 사람 판단을 뒤집지 않는다."""
    for src, expect in (("console", False), (auto_operator.APPROVAL_SOURCE_HOURLY, True)):
        db.query(NaverChangeLog).delete()
        db.query(NaverProposal).delete()
        pr = NaverProposal(proposal_type="bid_up", target_type="ad", target_id="nad-x",
                           campaign_id=CAMP, status="approved", approval_source=src)
        db.add(pr)
        db.flush()
        db.add(NaverChangeLog(
            entity_type="ad", entity_id="nad-x", campaign_id=CAMP, action="update_bid",
            dry_run=False, changed_at=NOW, executed_at=NOW, proposal_id=pr.id,
            after_value=json.dumps({"adAttr": {"bidAmt": 920}}),
        ))
        db.commit()
        assert harness.last_change_was_auto_up(db, "ad", "nad-x") is expect


# ── codex 적대 5R P1 3건 회귀: 외부변경 확인은 fail-CLOSED ────────────────────
def test_external_check_unknown_blocks_auto_up(db):
    """★codex 5R[P1]: 확인이 성립하지 않으면 자동 상향을 **하지 않는다**.

    이 확인이 자동 상향을 켠 선행조건이므로, 실패를 통과시키면 조건 없이 켠 것과 같다.
    재현: 대행사가 2,000→400으로 내린 뒤 우리 editTm 조회만 실패 → 종전 코드는 CAS도
    통과(현재 400 == 판정 400)하고 460을 그대로 PUT했다.
    """
    from app.services.naver_ad import guardrail_gate

    base = {"current_bid": 400, "campaign_type": "SHOPPING", "changes_today_count": 0,
            "roas_corrected": 5.0, "target_roas": 1.9611, "unconverted_spend": 0,
            "cost_today": 0, "daily_budget": 300000, "auto_exec": True,
            "auto_up_base_bid": 400}
    blocked = guardrail_gate.check(
        {"proposal_type": "bid_up", "target_bid": 460},
        {**base, "external_check": harness._EXT_UNKNOWN}, now=NOW,
    )
    assert blocked is not None and "외부 변경 확인 불가" in blocked
    # 확인이 성립하면(우리 상태 그대로 / 외부 감지 후 기준점 재설정) 통과한다.
    for verdict in (harness._EXT_OURS, harness._EXT_TOUCHED):
        assert guardrail_gate.check(
            {"proposal_type": "bid_up", "target_bid": 460},
            {**base, "external_check": verdict}, now=NOW,
        ) is None
    # 사람 승인 상향은 이 게이트 대상이 아니다.
    assert guardrail_gate.check(
        {"proposal_type": "bid_up", "target_bid": 460},
        {**base, "auto_exec": False, "external_check": harness._EXT_UNKNOWN}, now=NOW,
    ) is None
    # 하향은 언제나 나간다 — 출혈을 멈추는 손을 이 게이트가 막으면 안 된다.
    assert guardrail_gate.check(
        {"proposal_type": "bid_down", "target_bid": 350},
        {**base, "external_check": harness._EXT_UNKNOWN, "auto_bid_down_today": 0}, now=NOW,
    ) is None


def test_no_baseline_holds_and_seeds_observation(db):
    """★codex 5R[P1-2]: 관측 기준선이 없으면 '외부 아님'이 아니라 **보류**하고 기준선을 세운다.

    종전 코드는 known이 비면 False("외부 아님")를 반환해, 관측이 없는 신규 소재에서 정확히
    반대로 작동했다 — 대행사가 400으로 내려놔도 그대로 460으로 올린다.
    """
    _settings(db, optimizer="ours")
    db.add(NaverAdgroupProduct(
        adgroup_id="grp-hot", campaign_id=CAMP, mall_product_id="p1", product_name="p1",
        ad_id=AD_ID, ad_bid_amt=400, use_group_bid_amt=False, ad_edit_tm=None,  # 관측 없음
    ))
    p = _ad_proposal(db, proposal_type="bid_up", target_bid=460)
    p.approval_source = auto_operator.APPROVAL_SOURCE_HOURLY
    db.commit()
    live = {"nccAdId": AD_ID, "editTm": "2026-07-29T06:39:05.000Z",
            "adAttr": {"bidAmt": 400, "useGroupBidAmt": False}}
    with patch.object(harness.naver_sa_writer, "get_ad_bid", return_value=400), \
         patch.object(harness.naver_sa_writer, "get_ad", return_value=live):
        ctx = harness._build_guardrail_context(db, p, NOW)
    assert ctx["external_check"] == harness._EXT_UNKNOWN  # 이번 회차 보류
    row = db.query(NaverAdgroupProduct).filter(NaverAdgroupProduct.ad_id == AD_ID).one()
    assert row.ad_edit_tm == "2026-07-29T06:39:05.000Z"  # 다음 회차를 위한 기준선은 세운다


def test_edit_tm_lookup_failure_is_unknown_not_pass(db):
    """editTm 조회가 실패하면 '외부 아님'이 아니라 보류다(fail-open 금지)."""
    _settings(db, optimizer="ours")
    db.add(NaverAdgroupProduct(
        adgroup_id="grp-hot", campaign_id=CAMP, mall_product_id="p1", product_name="p1",
        ad_id=AD_ID, ad_bid_amt=400, use_group_bid_amt=False,
        ad_edit_tm="2026-07-28T01:00:00.000Z",
    ))
    p = _ad_proposal(db, proposal_type="bid_up", target_bid=460)
    p.approval_source = auto_operator.APPROVAL_SOURCE_HOURLY
    db.commit()
    with patch.object(harness.naver_sa_writer, "get_ad_bid", return_value=400), \
         patch.object(harness.naver_sa_writer, "get_ad", side_effect=RuntimeError("timeout")):
        ctx = harness._build_guardrail_context(db, p, NOW)
    assert ctx["external_check"] == harness._EXT_UNKNOWN


def test_same_second_collision_is_distinguished_by_value(db):
    """★codex 5R[P2]: 같은 editTm이어도 **값이 다르면** 외부 변경이다.

    editTm만 비교하면 우리 800 쓰기와 대행사 400 하향이 같은 초에 들어올 때 '우리 것'으로
    오인한다.
    """
    db.add(NaverAdgroupProduct(
        adgroup_id="grp-hot", campaign_id=CAMP, mall_product_id="p1", product_name="p1",
        ad_id=AD_ID, ad_bid_amt=800, use_group_bid_amt=False,
        ad_edit_tm="2026-07-29T06:39:05.000Z",
    ))
    db.commit()
    same_tm = "2026-07-29T06:39:05.000Z"
    assert harness._classify_external_touch(db, AD_ID, same_tm, 800) == harness._EXT_OURS
    assert harness._classify_external_touch(db, AD_ID, same_tm, 400) == harness._EXT_TOUCHED


def test_unparseable_edit_tm_event_still_becomes_the_anchor(db):
    """★codex 5R[P1-3]: 파싱 불가 editTm 사건(occurred_at NULL)도 최신 기준점이 된다.

    occurred_at DESC NULLS LAST로 고르면 최신 NULL 사건이 과거 non-NULL에 밀려 천장이 옛
    고가로 복원된다.
    """
    db.add(NaverAgencyOp(  # 과거 외부 사건(정상 시각) — 2,000
        op_date=NOW.date(), detected_at=NOW - timedelta(hours=3), entity_type="ad",
        entity_id=AD_ID, campaign_id=CAMP, optimizer="ours", op_type="bid_change",
        after_value="2000", is_exception=True, occurred_at=NOW - timedelta(hours=3),
    ))
    db.add(NaverAgencyOp(  # 최신 외부 사건인데 editTm 형식 변화로 occurred_at NULL — 400
        op_date=NOW.date(), detected_at=NOW - timedelta(minutes=10), entity_type="ad",
        entity_id=AD_ID, campaign_id=CAMP, optimizer="ours", op_type="bid_change",
        after_value="400", is_exception=True, occurred_at=None,
    ))
    db.commit()
    assert harness.auto_up_base_bid(db, "ad", AD_ID, NOW) == 400


def test_external_check_constants_match_guardrail():
    """두 모듈의 판정값 상수는 같아야 한다(역방향 import 회피로 값 고정한 자리)."""
    from app.services.naver_ad import guardrail_gate

    assert (guardrail_gate._EXT_CHECK_OURS, guardrail_gate._EXT_CHECK_EXTERNAL) == \
        (harness._EXT_OURS, harness._EXT_TOUCHED)


# ══ D-NAO-171(B-3): 그룹 판정 → 활성 false 소재 **전부** 집행 ═══════════════════
# 왜 이 블록이 필요한가: 종전 테스트는 전부 «그룹당 소재 1개»라 max-only와 전-소재 집행이
# **구분되지 않았다**. 즉 팬아웃을 안 해도 전건 통과한다 — 통과하는데 아무것도 안 지키는
# 테스트(교훈 #181)의 정확한 사례라, 아래를 라이브 실측(2026-08-10: 소재 2~10개 + 입찰가
# 제각각인 46그룹)에 맞춰 새로 쓴다.

def _seed_multi_ad_group(db, *, bids=(800, 500)):
    """입찰가가 서로 다른 소재 N개짜리 미연결 그룹(실측 46그룹의 축소판)."""
    _seed_hourly_shopping(db)
    for i, b in enumerate(bids):
        _ad(db, "grp-hot", f"p{i}", ad_id=f"nad-{i}", ad_bid_amt=b, use_group=False)
    db.commit()


def test_group_down_steps_every_active_ad_and_preserves_ratio(db):
    """★핵심: 그룹 DOWN 판정 1건 → 소재 **전부**가 각자 기준 −15%로 내려간다.

    max만 내리면 차순위가 새 max가 되어 그룹 실효는 소재 간 갭만큼만 내려간다(종전 결함).
    승수 스텝이라 소재 간 **비율이 보존**되는 것도 같이 못박는다 — 상품별 차등은 우리가 만든
    구조가 아니므로 평탄화하면 안 된다.
    """
    _seed_multi_ad_group(db, bids=(800, 500))
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    assert mock_exec.call_count == 2, "소재 2개 전부 집행돼야 한다(max-only면 1)"
    assert result["ad_fanout_groups"] == 1
    assert result["ad_writes_reserved"] == 2
    saved = {p.target_id: p.target_bid
             for p in db.query(NaverProposal).filter(NaverProposal.target_type == "ad").all()}
    # 10원 반올림 규약은 방향마다 보수 쪽이다(_clamp_step: down=올림 · up=내림)
    #  → 800×0.85=680 · 500×0.85=425→**430**(올림).
    assert saved == {"nad-0": 680, "nad-1": 430}, "각자 기준 −15%(800→680 · 500→430)"
    # 비율 보존: 800/500 = 1.600 → 680/430 = 1.581 (10원 반올림 오차 내)
    assert abs((680 / 430) - (800 / 500)) < 0.03


def test_group_up_steps_every_active_ad(db):
    """상향도 대칭이다 — 하향만 전 소재면 「브레이크만 자동」이라 D-NAO-59에서 표류한다."""
    _seed_multi_ad_group(db, bids=(800, 500))
    with patch.object(auto_operator, "_judge_hourly", return_value={"direction": "up", "reason": "저순위"}), \
         patch.object(auto_operator, "_learned_optimal_skip", return_value=(False, "학습밴드 미도달")), \
         patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    assert mock_exec.call_count == 2
    saved = {p.target_id: (p.proposal_type, p.target_bid)
             for p in db.query(NaverProposal).filter(NaverProposal.target_type == "ad").all()}
    # ★타입은 반드시 bid_up — 소재는 rank-step 면제 타입(bid_up_servo/bid_up_rank)을 달면 안 된다
    # up은 10원 **내림**: 800×1.15=920 · 500×1.15=575→**570**
    assert saved == {"nad-0": ("bid_up", 920), "nad-1": ("bid_up", 570)}


def test_fanout_excludes_group_bid_ads_and_paused_ads(db):
    """집행 대상은 **활성 ∧ useGroupBidAmt가 명시적 False**인 소재뿐이다.

    ①정지 소재(userLock)는 서빙하지 않는다 ②useGroupBidAmt=True는 그룹입찰이 실효라 개별
    bidAmt 수정이 무의미하고 writer가 어차피 fail-closed로 거부한다 ③None(파싱 실패·부재)은
    「모름」이지 False가 아니다 — 모름을 False로 읽으면 §0 강제 전환 금지를 우회하게 된다.
    """
    _seed_hourly_shopping(db)
    _ad(db, "grp-hot", "p0", ad_id="nad-0", ad_bid_amt=800, use_group=False)
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=700, use_group=False, user_lock=True)
    _ad(db, "grp-hot", "p2", ad_id="nad-2", ad_bid_amt=600, use_group=True)
    _ad(db, "grp-hot", "p3", ad_id="nad-3", ad_bid_amt=500, use_group=None)
    db.commit()
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    assert mock_exec.call_count == 1
    saved = [p.target_id for p in db.query(NaverProposal).filter(NaverProposal.target_type == "ad").all()]
    assert saved == ["nad-0"]


def test_group_decision_consumes_one_decision_slot_not_n(db):
    """★결정 캡과 쓰기 캡은 층이 다르다 — 소재 N개짜리 그룹도 «결정» 캡은 한 자리만 쓴다.

    이게 없으면 5소재 그룹 하나가 결정 캡(5)을 통째로 소진해, 캡의 의미가 조용히
    «그룹 5개»에서 «그룹 1개»로 줄어든다(캡이 지키려던 것은 «되돌릴 시간»이고, 틀리는
    단위는 판정이지 소재가 아니다).
    """
    _seed_multi_ad_group(db, bids=(800, 700, 600))
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_execution_harness, "execute"):
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    assert result["ad_auto_exec_reserved"] == 1, "그룹 1건 = 결정 1자리"
    assert result["ad_writes_reserved"] == 3, "쓰기는 소재마다 센다"
    assert result["ad_auto_exec_capped"] == 0


def test_write_cap_degrades_excess_ads_to_confirm_pending(db):
    """쓰기 상한 초과분은 **드롭이 아니라** Confirm 대기로 강등된다(조용한 드롭 금지)."""
    n = auto_operator._MAX_AD_WRITES_PER_LANE + 2
    _seed_multi_ad_group(db, bids=tuple(800 - 10 * i for i in range(n)))
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    assert mock_exec.call_count == auto_operator._MAX_AD_WRITES_PER_LANE
    assert result["ad_writes_capped"] == n - auto_operator._MAX_AD_WRITES_PER_LANE
    assert db.query(NaverProposal).filter(NaverProposal.status == "pending").count() == \
        n - auto_operator._MAX_AD_WRITES_PER_LANE


def test_group_step_all_ads_switch_reverts_to_max_only(db):
    """되돌림 스위치 — `_GROUP_STEP_ALL_ADS=False`면 D-NAO-171 이전(max 소재 1개)으로 복귀한다.

    사고 났을 때 상수 한 줄로 원복된다는 믿음이 이 스위치의 존재 이유다(`_ad_auto_exec`의
    킬스위치 규율과 동형) — 도달 가능성과 무관하게 성립해야 한다.
    """
    _seed_multi_ad_group(db, bids=(800, 500))
    with patch.object(auto_operator, "_GROUP_STEP_ALL_ADS", False), \
         patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    assert mock_exec.call_count == 1
    assert result["ad_fanout_groups"] == 0
    saved = db.query(NaverProposal).filter(NaverProposal.target_type == "ad").one()
    assert (saved.target_id, saved.target_bid) == ("nad-0", 680)  # max 소재만


def test_single_ad_group_behaviour_is_unchanged(db):
    """소재 1개 그룹(실측 207그룹 중 156개 = 75%)은 종전과 **완전히 같다** — N=1 퇴화 사례."""
    _seed_multi_ad_group(db, bids=(800,))
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    assert mock_exec.call_count == 1
    assert result["ad_fanout_groups"] == 0          # 팬아웃으로 세지 않는다
    assert result["ad_auto_exec_reserved"] == 1
    saved = db.query(NaverProposal).filter(NaverProposal.target_type == "ad").one()
    assert (saved.target_id, saved.target_bid) == ("nad-0", 680)


# ══ 적대 리뷰 P2 채택분 — 살아남은 변이 3건 + 안전 속성 ═══════════════════════════
# 리뷰어가 만든 변이 중 M-B(결정 캡 경계)·M-C(집행 순서)·M-F(rollback)가 **생존**했다.
# 살아남은 변이 = 통과하는데 아무것도 안 지키는 자리다(교훈 #181).

def test_fanout_executes_highest_bid_ad_first(db):
    """★집행 순서는 안전 속성이다(P2-1) — 내림차순이어야 한다.

    쓰기 캡은 레인 **전역 누적**이라 뒤쪽 그룹은 상위 몇 개만 집행될 수 있다. 오름차순이면
    **max 소재가 강등돼 그룹 실효가 전혀 안 움직인다** — D-NAO-171이 고치려던 바로 그 실패를
    캡이 재생산한다. 순서는 취향이 아니라 계약이다.
    """
    _seed_multi_ad_group(db, bids=(300, 800, 500))
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_execution_harness, "execute"):
        auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    order = [p.target_id for p in db.query(NaverProposal)
             .filter(NaverProposal.target_type == "ad").order_by(NaverProposal.id).all()]
    assert order == ["nad-1", "nad-2", "nad-0"], "800 → 500 → 300 순으로 집행돼야 한다"


def test_group_spanning_decision_cap_boundary_is_not_split(db):
    """★결정 캡 경계에서 그룹이 쪼개지지 않는다(P2-2).

    결정 자리가 1칸 남았을 때 3소재 그룹이 오면, 그 그룹은 **한 자리를 쓰고 3소재 전부**
    집행돼야 한다. 소재마다 자리를 세면 첫 소재만 나가고 나머지 2개가 Confirm으로 강등돼
    그룹 실효가 반쪽만 움직인다(하향 국면에서 이건 「덜 내려간 채로 계속 태우는」 상태다).
    """
    cap = auto_operator._MAX_AD_AUTO_EXEC_PER_LANE
    db.add(NaverCampaignSettings(campaign_id=CAMP, optimizer="ours", auto_operate=True))
    db.add(NaverEntity(entity_type="campaign", entity_id=CAMP, campaign_id=CAMP, status="on"))
    db.add(NaverHourlySnapshot(snapshot_at=NOW, ad_date=TODAY, snapshot_hour=23,
                               campaign_id=CAMP, campaign_type="", cost=0, clk=0, imp=0))
    window_from, _ = auto_operator._settlement_window(TODAY)
    # 앞선 그룹 (cap-1)개 = 소재 1개씩 → 결정 자리 1칸만 남는다. 마지막 그룹만 소재 3개.
    for i in range(cap):
        gid = f"grp-{i}"
        db.add(NaverEntity(entity_type="adgroup", entity_id=gid, parent_id=CAMP,
                           campaign_id=CAMP, campaign_type="SHOPPING", status="on"))
        db.add(NaverAdDaily(ad_date=window_from, campaign_id=CAMP, campaign_type="SHOPPING",
                            adgroup_id=gid, keyword_id="", imp=200, clk=20, cost=2000))
        n_ads = 3 if i == cap - 1 else 1
        for j in range(n_ads):
            _ad(db, gid, f"p{i}-{j}", ad_id=f"nad-{i}-{j}", ad_bid_amt=800 - 50 * j, use_group=False)
    db.commit()
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    assert result["ad_auto_exec_reserved"] == cap        # 그룹 수 = 결정 자리 수
    assert result["ad_writes_reserved"] == cap + 2       # 소재 수 = (cap-1)×1 + 3
    assert result["ad_auto_exec_capped"] == 0            # 아무도 강등되지 않았다
    assert mock_exec.call_count == cap + 2
    last = [p.target_id for p in db.query(NaverProposal)
            .filter(NaverProposal.target_id.like(f"nad-{cap - 1}-%")).all()]
    assert len(last) == 3, "마지막 그룹의 3소재가 통째로 집행돼야 한다"


def test_kill_switch_mid_fanout_stops_remaining_ads(db):
    """★P2-10: 팬아웃 도중 킬스위치가 내려가면 **남은 소재는 안 나간다**.

    종전 재확인(codex 5R[P1-2])은 유닛당 1회였다. 팬아웃 이후 한 유닛이 최대 15회 쓰기가
    되므로 그 계약이 조용히 약해졌다 — «즉시 정지»는 쓰기 단위로 성립해야 한다.
    여기선 첫 쓰기 직후 사람이 스위치를 내린 상황을 재현한다.
    """
    _seed_multi_ad_group(db, bids=(800, 700, 600))
    state = {"on": True}

    def _exec_then_kill(*a, **k):
        state["on"] = False  # 첫 쓰기가 나간 직후 사람이 껐다

    with patch.object(auto_operator, "_auto_operate_now", side_effect=lambda _db, _c: state["on"]), \
         patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_execution_harness, "execute",
                      side_effect=_exec_then_kill) as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    assert mock_exec.call_count == 1, "스위치를 내린 뒤엔 한 건도 더 나가면 안 된다"
    assert any("킬스위치 OFF" in h["reason"] for h in result["held"])


def test_hold_reason_distinguishes_lookup_failure_from_empty_targets(db):
    """★P2-4: 「조회 실패」와 「조회는 됐는데 집행 대상 0」을 한 문구로 뭉치지 않는다.

    원인도 대응도 다르다(전자는 장애, 후자는 그 그룹에 소재 레버가 없다는 사실). 이 리포는
    거짓 라벨에 반복해서 데였다 — `ignored`를 「원가 제외 결정」으로 읽었는데 실은 매칭
    실패였던 건과 같은 모양이다. 라벨이 거짓이면 진단이 통째로 틀어진다.
    """
    _seed_multi_ad_group(db, bids=(800,))          # DB는 미연결(source='ad') → 라우팅은 일어난다
    live_all_group_bid = [{"ad_id": "nad-0", "adgroup_id": "grp-hot", "mall_product_id": "p0",
                           "use_group_bid_amt": True, "ad_bid_amt": 800, "ad_user_lock": False}]
    with patch.object(auto_operator, "get_ads", return_value=live_all_group_bid), \
         patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    mock_exec.assert_not_called()
    reasons = [h["reason"] for h in result["held"]]
    assert any("집행 대상 0" in r for r in reasons), f"실제 사유: {reasons}"
    assert not any("재조회 실패" in r for r in reasons), "조회는 성공했는데 실패라고 말하면 안 된다"
