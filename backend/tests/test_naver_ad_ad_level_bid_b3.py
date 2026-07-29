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
    # CS(D-NAO-96)로 소재 UP 자동 경로가 explore_op + cold_op 둘로 늘었다 — 문구가 바뀌었을 뿐
    # 판정은 불변(콘솔 NULL 승인원의 소재 UP은 여전히 미개방).
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
    with patch.object(auto_operator, "AD_BID_CANARY_CAMPAIGNS", frozenset({CAMP})), \
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
    """비카나리(빈 집합 기본값) → 미연결 그룹은 기존 [레버 미연결] hold(현행 동작 보존).

    D-NAO-125(2026-07-29): 스코프 판정이 AD_BID_CANARY_CAMPAIGNS 상수에서 킬스위치
    (AD_BID_ROUTING_ENABLED)로 옮겨졌다 — 기본 상태(킬스위치 on)에서는 이 캠페인도 이제
    ad-레벨로 열린다((c) 회귀가 그 경로를 커버). 이 테스트는 킬스위치 off로 좁혀
    "카나리 상수 미포함 → hold" 종전 회귀를 보존한다."""
    _seed_hourly_shopping(db)
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)
    db.commit()
    with patch.object(auto_operator, "AD_BID_ROUTING_ENABLED", False), \
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
    """비카나리(기본) → 미연결 그룹 skip(B2 억제, 현행 동작 보존).

    D-NAO-125(2026-07-29): 기본 상태(킬스위치 on)에서는 이 캠페인도 이제 ad-레벨로 열린다
    (의도된 변경). 이 테스트는 킬스위치 off로 좁혀 종전 skip 회귀를 보존한다."""
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    _ad(db, "grp-d", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False, campaign_id="cmp-ours")
    db.commit()
    diagnosis = _diagnosis(shopping_group_bep=[_bep_board_row()])
    with patch.object(auto_operator, "AD_BID_ROUTING_ENABLED", False):
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
    # 카나리 1호 개방(Jino 2026-07-20): 맥세이프만 — 상수 자체는 D-NAO-125에서도 불변
    # (delegation_gate·expert_briefing_builder가 이 상수로 Confirm-only 제외 판별을 그대로
    # 한다 — 건드리면 계정 전체 위임이 죽는다). 다른 캠페인이 상수에 소리 없이 추가되면
    # 이 테스트가 잡는다.
    assert auto_operator.AD_BID_CANARY_CAMPAIGNS == frozenset({"cmp-a001-02-000000010769985"})
    # D-NAO-125(2026-07-29): _ad_bid_canary는 이제 스코프(=위 상수)가 아니라 킬스위치
    # (AD_BID_ROUTING_ENABLED)만 본다 — 기본(킬스위치 on) 상태에서는 상수 무관 True다.
    assert auto_operator._ad_bid_canary("any-campaign") is True
    with patch.object(auto_operator, "AD_BID_ROUTING_ENABLED", False):
        # 킬스위치 off → 종전처럼 상수 멤버십으로 폴백(회귀 보존).
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
    with patch.object(auto_operator, "AD_BID_CANARY_CAMPAIGNS", frozenset({CAMP})), \
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


def test_hourly_lane_ad_bid_up_still_pending_confirm(db):
    """(b) 소재 상향은 여전히 열리지 않는다 — 같은 미연결 조건에서 방향만 up이면 생성
    단계에서 그대로 hold("[레버 미연결] ad UP은 카나리 2단계"), 제안 0·실행 0.
    D-NAO-125가 연 것은 하향뿐 — _AD_BID_CANARY_PROPOSAL_TYPES={"bid_down"}는 불변이라
    _ad_bid_proposal이 up 방향에는 여전히 None을 반환한다."""
    _seed_hourly_shopping(db)
    _ad(db, "grp-hot", "p1", ad_id="nad-1", ad_bid_amt=800, use_group=False)  # 미연결
    db.commit()
    with patch.object(auto_operator, "_judge_hourly",
                      return_value={"direction": "up", "reason": "저순위"}), \
         patch.object(auto_operator, "_learned_optimal_skip", return_value=(False, "학습밴드 미도달")), \
         patch.object(auto_operator.naver_sa_writer, "_get_adgroup", return_value={"bidAmt": 200}), \
         patch.object(auto_operator.naver_sa_writer, "get_ad_bid") as mget_ad, \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda t, d: _overheat_curve())
    mock_exec.assert_not_called()
    mget_ad.assert_not_called()  # hold가 생성 이전 단계라 소재 재조회조차 없음
    assert db.query(NaverProposal).count() == 0
    assert result["approved"] == 0
    assert result["executed"] == 0
    assert any("[레버 미연결] ad UP은 카나리 2단계" in h["reason"] for h in result["held"])


def test_hourly_lane_ad_bid_down_opens_for_non_canary_campaign(db):
    """(c) 비카나리 캠페인도 이제 열린다 — 이게 D-NAO-125의 본체다. 07-29 07:37 인수 캠페인이
    AD_BID_CANARY_CAMPAIGNS 상수 누락으로 죽어 있던 사고의 회귀 방지: CAMP("cmp-shop")는
    AD_BID_CANARY_CAMPAIGNS(맥세이프 전용)에 한 번도 없었던 캠페인인데도, 상수를 전혀
    건드리지 않고(패치 없이) (a)와 동일하게 인라인 자동 실행된다."""
    assert CAMP not in auto_operator.AD_BID_CANARY_CAMPAIGNS  # 전제 확인 — 진짜 비카나리
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
    with patch.object(auto_operator, "AD_BID_ROUTING_ENABLED", False), \
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
    with patch.object(auto_operator, "AD_BID_ROUTING_ENABLED", False), \
         patch.object(auto_operator, "AD_BID_CANARY_CAMPAIGNS", frozenset({CAMP})), \
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
    with patch.object(auto_operator, "AD_BID_ROUTING_ENABLED", False), \
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
    with patch.object(auto_operator, "AD_BID_ROUTING_ENABLED", False), \
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
