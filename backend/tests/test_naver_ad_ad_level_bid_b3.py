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
    with patch.object(auto_operator, "AD_BID_CANARY_CAMPAIGNS", frozenset({"cmp1"})), \
         patch.object(harness, "_build_guardrail_context", return_value={"current_bid": 800}), \
         patch.object(harness.guardrail_gate, "check", return_value=None) as mgate, \
         patch.object(harness.naver_sa_writer, "update_ad_bid", return_value=_ad_write_result()) as mad, \
         patch.object(harness.naver_sa_writer, "update_adgroup_bid") as magp, \
         patch.object(harness.naver_sa_writer, "update_keyword_bid") as mkw:
        log_entry = harness.execute(db, p.id, dry_run=False)
    mgate.assert_called_once()
    assert mgate.call_args[0][0] == {"proposal_type": "bid_down", "target_bid": 680, "target_lock": None}
    mad.assert_called_once_with(AD_ID, 680)
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
    with patch.object(auto_operator, "AD_BID_CANARY_CAMPAIGNS", frozenset({"cmp1"})), \
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
    with patch.object(auto_operator, "AD_BID_CANARY_CAMPAIGNS", frozenset({"cmp1"})), \
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
    mad.assert_called_once_with(AD_ID, 920)
    assert log_entry.entity_type == "ad"


def test_real_write_blocker_ad_executable(db):
    p = _ad_proposal(db, proposal_type="bid_down", target_bid=680)
    with patch.object(auto_operator, "AD_BID_CANARY_CAMPAIGNS", frozenset({"cmp1"})):
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
    mad.assert_called_once_with(AD_ID, 680)
    db.refresh(p)
    assert p.status == "approved" and p.executed_change_log_id == log_entry.id


def test_harness_ad_blocks_undirectional_bid_up(db):
    """카나리 캠페인이어도 ad bid_up은 1단계 미개방 방향 → 최종 경계 차단(개방은 상수 확장)."""
    p = _ad_proposal(db, proposal_type="bid_up", target_bid=920)
    _settings(db, optimizer="ours")
    with patch.object(auto_operator, "AD_BID_CANARY_CAMPAIGNS", frozenset({"cmp1"})), \
         patch.object(harness.naver_sa_writer, "update_ad_bid") as mad:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)
    mad.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"


def test_harness_ad_blocks_missing_adgroup_id(db):
    p = _ad_proposal(db, proposal_type="bid_down", target_bid=680, adgroup_id=None)
    _settings(db, optimizer="ours")
    with patch.object(auto_operator, "AD_BID_CANARY_CAMPAIGNS", frozenset({"cmp1"})), \
         patch.object(harness.naver_sa_writer, "update_ad_bid") as mad:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)
    mad.assert_not_called()


def test_real_write_blocker_ad_bid_down_executable_all_campaigns(db):
    """BX2(D-NAO-70②): 카나리 제한 해제 — 소재 bid_down은 전 캠페인에서 실행 버튼 활성(None)."""
    p = _ad_proposal(db, proposal_type="bid_down", target_bid=680)  # cmp1 = 과거 비카나리
    assert harness.real_write_blocker(p) is None


def test_real_write_blocker_ad_bid_up_non_explore_not_executable(db):
    """BX2: 비탐색 승인원(콘솔 NULL)의 소재 UP은 미개방 — 실행 버튼 비활성(탐색 explore_op만)."""
    p = _ad_proposal(db, proposal_type="bid_up", target_bid=920)  # approval_source=None
    blocked = harness.real_write_blocker(p)
    assert blocked is not None and "탐색" in blocked


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
    """[GATE P2-2 Confirm-only] 카나리 + 미연결 그룹 밴드 DOWN → ad-레벨 제안을
    **pending으로 생성만**(자동발사 0, D-NAO-5 — 실행은 Jino 콘솔 Confirm 경로만).
    target_type='ad'·target_id=max_ad_id·adgroup_id=그룹·target_bid=소재 스텝(800→680)."""
    _seed_hourly_shopping(db)
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)
    db.commit()
    with patch.object(auto_operator, "AD_BID_CANARY_CAMPAIGNS", frozenset({CAMP})), \
         patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_sa_writer, "get_ad_bid", return_value=800), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    mock_exec.assert_not_called()  # 인라인 실행 금지(Confirm-only)
    assert result["approved"] == 0
    assert result["ad_confirm_pending"] == 1
    saved = db.query(NaverProposal).filter(NaverProposal.target_type == "ad").one()
    assert saved.status == "pending"          # 자동 승인 금지
    assert saved.approval_source is None      # 승인 이력 없음(콘솔 Confirm 대기)
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
    with patch.object(auto_operator, "AD_BID_CANARY_CAMPAIGNS", frozenset({CAMP})), \
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


def test_hourly_lane_canary_up_holds_stage2(db):
    """[GATE P2-2 DOWN 한정] 카나리 + 미연결 그룹 + 비탐침 UP verdict → ad UP은 카나리
    2단계(_AD_BID_CANARY_PROPOSAL_TYPES 밖) → hold, 제안 0."""
    _seed_hourly_shopping(db)
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)
    db.commit()
    with patch.object(auto_operator, "AD_BID_CANARY_CAMPAIGNS", frozenset({CAMP})), \
         patch.object(auto_operator, "_judge_hourly", return_value={"direction": "up", "reason": "저순위"}), \
         patch.object(auto_operator, "_learned_optimal_skip", return_value=(False, "학습밴드 미도달")), \
         patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    mock_exec.assert_not_called()
    assert db.query(NaverProposal).count() == 0
    assert any("2단계" in h["reason"] for h in result["held"])


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
    """비카나리(빈 집합 기본값) → 미연결 그룹은 기존 [레버 미연결] hold(현행 동작 보존)."""
    _seed_hourly_shopping(db)
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)
    db.commit()
    with patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_sa_writer, "get_ad_bid") as mget_ad, \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    mock_exec.assert_not_called()
    mget_ad.assert_not_called()  # 비카나리는 소재 재조회조차 안 함
    assert result["approved"] == 0
    assert any("[레버 미연결]" in h["reason"] for h in result["held"])


def test_hourly_lane_canary_connected_group_uses_group_path(db):
    """카나리여도 연결 그룹(전 소재 useGroupBidAmt=true)은 종전 그룹입찰 경로(회귀 0)."""
    _seed_hourly_shopping(db)
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=None, use_group=True)
    db.commit()
    with patch.object(auto_operator, "AD_BID_CANARY_CAMPAIGNS", frozenset({CAMP})), \
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
    return {"window": {}, "correction_factor": {"factor": 1.0}, "boards": boards}


def test_build_band_bep_canary_routes_disconnected_to_ad(db):
    """카나리 + 미연결 밴드 그룹(bep, down) → 그룹 skip 대신 ad-레벨 bid_down 제안."""
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    _ad(db, "grp-d", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False, campaign_id="cmp-ours")
    db.commit()
    diagnosis = _diagnosis(shopping_group_bep=[_bep_board_row()])
    with patch.object(auto_operator, "AD_BID_CANARY_CAMPAIGNS", frozenset({"cmp-ours"})):
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
    """비카나리(기본) → 미연결 그룹 skip(B2 억제, 현행 동작 보존)."""
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    _ad(db, "grp-d", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False, campaign_id="cmp-ours")
    db.commit()
    diagnosis = _diagnosis(shopping_group_bep=[_bep_board_row()])
    out = proposal_writer.build(
        db, diagnosis, bid_sims={("adgroup", "grp-d"): _sim(direction="down", current_bid=200)},
        as_of=TODAY,
    )
    assert out == []


def test_build_band_growth_canary_up_not_routed_stage2(db):
    """[GATE P2-2 DOWN 한정] 성장(up)은 카나리 1단계에서 ad 라우팅 금지
    (_AD_BID_CANARY_PROPOSAL_TYPES={"bid_down"}) — 카나리여도 미연결 up은 기존 skip(제안 0)."""
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    _ad(db, "grp-g", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False, campaign_id="cmp-ours")
    db.commit()
    diagnosis = _diagnosis(shopping_group_growth=[
        _bep_board_row(adgroup_id="grp-g", conv_amt=40000, roas_naver=5.0, roas_corrected=5.0),
    ])
    with patch.object(auto_operator, "AD_BID_CANARY_CAMPAIGNS", frozenset({"cmp-ours"})):
        out = proposal_writer.build(
            db, diagnosis,
            bid_sims={("adgroup", "grp-g"): _sim(direction="up", recommended=1300, ceiling=1300,
                                                 current_bid=200)},
            as_of=TODAY,
        )
    assert out == []


# ══════════════════════════════════════════════════════════════════
# (5) 카나리 빈 집합 기본값 = 전면 hold(배포 즉시 행위 변화 0) + DOWN 한정 상수
# ══════════════════════════════════════════════════════════════════
def test_canary_default_empty_set():
    # 카나리 1호 개방(Jino 2026-07-20): 맥세이프만. 다른 캠페인이 소리 없이 추가되면 이 테스트가 잡는다.
    assert auto_operator.AD_BID_CANARY_CAMPAIGNS == frozenset({"cmp-a001-02-000000010769985"})
    assert auto_operator._ad_bid_canary("any-campaign") is False


def test_canary_directions_down_only():
    """[GATE P2-2] 카나리 1단계 방향 = bid_down만(2단계 개방 시 상수 확장)."""
    assert auto_operator._AD_BID_CANARY_PROPOSAL_TYPES == frozenset({"bid_down"})


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
    dup skip 카운터(매시간 동일 pending 누적 → Confirm 큐 매몰 방지)."""
    _seed_hourly_shopping(db)
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)
    db.commit()
    patches = dict(
        _get_adgroup={"bidAmt": 200},
        get_ad_bid=800,
    )
    with patch.object(auto_operator, "AD_BID_CANARY_CAMPAIGNS", frozenset({CAMP})), \
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
