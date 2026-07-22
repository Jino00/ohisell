# test_naver_searchterm_ss3.py — SS3 실행 배선(harness 분기 + 생성 레인) 테스트
# 커버(PLAN §검증 3·경계 차등): ①쇼핑 실쓰기 부재(SHOPPING 명시 거부) ②일일캡 ③Confirm 없는
#   자동 발사 0(승인원 비활성·delegation/일레인 제외) ④WEB_SITE 실행 분기 정상(모킹) ⑤중복
#   제안 방지 ⑥킬스위치 사전등록(미래 자동 활성화 대비) ⑦브리핑 diary.
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
    NaverAdgroupProduct,
    NaverCampaignSettings,
    NaverChangeLog,
    NaverEntity,
    NaverProductBep,
    NaverProposal,
    NaverSearchTermDaily,
    OpsDiaryEntry,
)
from app.services.naver_ad import auto_operator, delegation_gate
from app.services.naver_ad import naver_execution_harness as harness
from app.services.naver_ad import naver_sa_writer
from app.services.naver_ad import search_term_judge as judge
from app.services.naver_ad import search_term_ss_lane as lane
from app.services.naver_ad.campaign_target_resolver import NAVER_CHANNEL_ID

_NOW = datetime(2026, 7, 22, 9, 0, 0)
# NaverProposal.created_at은 server_default=func.now()로 UTC 저장([[sqlite-server-default-now-is-utc]]).
# _NOW(KST) 달력일에 해당하는 UTC 값 — 캡 예약 오늘-한정 필터(created_at 창) 안에 들도록 명시 세팅.
_TODAY_UTC = _NOW - timedelta(hours=9)
_TYPE = judge.SEARCH_TERM_EXCLUDE_TYPE


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


def _settings(db, campaign_id="cmp1", optimizer="ours", auto_operate=False):
    db.add(NaverCampaignSettings(campaign_id=campaign_id, optimizer=optimizer, auto_operate=auto_operate))
    db.commit()


def _entity(db, adgroup_id, campaign_type):
    db.add(NaverEntity(
        entity_type="adgroup", entity_id=adgroup_id, parent_id="cmp1", campaign_id="cmp1",
        campaign_type=campaign_type, name="grp", status="on",
    ))
    db.commit()


def _proposal(db, *, adgroup_id="grp-web", term="무관검색어", status="approved",
              approval_source=None, created_at=None):
    p = NaverProposal(
        proposal_type=_TYPE, target_type="search_term", target_id=term,
        campaign_id="cmp1", adgroup_id=adgroup_id, rationale="[검색어제외] 근거",
        expected_effect="효과", status=status, approval_source=approval_source,
    )
    if created_at is not None:  # 캡 예약 오늘-한정 필터 검증용(server_default UTC 대신 명시 세팅)
        p.created_at = created_at
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _write_result(term):
    after = [{"nccAdgroupRestrictKwdId": "rkw-1", "keyword": term}]
    return naver_sa_writer.WriteResult(
        action="add_restricted_keywords", before=[], response=after, after=after, created_ids=["rkw-1"],
    )


# ── ④ WEB_SITE 실행 분기 정상(모킹) ──
def test_websites_exclude_success_records_change_log(db):
    _settings(db)
    _entity(db, "grp-web", "WEB_SITE")
    p = _proposal(db, adgroup_id="grp-web", term="무관검색어")
    with patch.object(harness.naver_sa_writer, "add_restricted_keywords",
                      return_value=_write_result("무관검색어")) as mock_write:
        log_entry = harness.execute(db, p.id, dry_run=False, now=_NOW)
    mock_write.assert_called_once_with("grp-web", ["무관검색어"])
    assert log_entry.action == "exclude_search_term"
    assert log_entry.dry_run is False
    assert json.loads(log_entry.after_value)["created_ids"] == ["rkw-1"]
    db.refresh(p)
    assert p.executed_change_log_id == log_entry.id
    assert p.status == "approved"


# ── ① 쇼핑 실쓰기 부재(SHOPPING 명시 거부, fail-closed) ──
def test_shopping_exclude_rejected_before_writer(db):
    _settings(db)
    _entity(db, "grp-shop", "SHOPPING")
    p = _proposal(db, adgroup_id="grp-shop", term="쇼핑검색어")
    with patch.object(harness.naver_sa_writer, "add_restricted_keywords") as mock_write:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False, now=_NOW)
    mock_write.assert_not_called()  # writer 미호출 = 실쓰기 부재
    db.refresh(p)
    assert p.status == "failed"
    row = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).one()
    assert harness.GUARD_BLOCK_MARKER in row.rationale
    assert "SHOPPING" in row.rationale
    assert row.after_value is None  # 실쓰기 없음(광고 확실히 안 바뀜)


# ── ①-b target_id 길이 초과 방어(codex 2R[P1]) ──
def test_exclude_over_maxlen_target_id_blocked_before_writer(db):
    # SQLite는 VARCHAR 길이를 강제하지 않아 초과 저장이 물리적으로 가능 → executor가 fail-closed.
    _settings(db)
    _entity(db, "grp-web", "WEB_SITE")
    long_term = "가" * (harness._TARGET_ID_MAXLEN + 1)  # 51자
    p = _proposal(db, adgroup_id="grp-web", term=long_term)
    with patch.object(harness.naver_sa_writer, "add_restricted_keywords") as mock_write:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False, now=_NOW)
    mock_write.assert_not_called()  # 잘린/초과 텍스트로 실쓰기 없음
    db.refresh(p)
    assert p.status == "failed"
    row = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).one()
    assert harness.GUARD_BLOCK_MARKER in row.rationale
    assert str(harness._TARGET_ID_MAXLEN) in row.rationale
    assert row.after_value is None


# ── ② 일일 캡(§1 4) ──
def test_daily_cap_blocks_over_limit(db):
    _settings(db)
    _entity(db, "grp-web", "WEB_SITE")
    # 오늘 이미 성공한 exclude 10건(cap 도달) 선삽입
    for i in range(harness._SS_DAILY_EXCLUDE_CAP):
        db.add(NaverChangeLog(
            entity_type="search_term", entity_id=f"t{i}", campaign_id="cmp1",
            action="exclude_search_term", dry_run=False, after_value="{}",
            changed_at=_NOW, executed_at=_NOW,
        ))
    db.commit()
    p = _proposal(db, adgroup_id="grp-web", term="11번째")
    with patch.object(harness.naver_sa_writer, "add_restricted_keywords") as mock_write:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False, now=_NOW)
    mock_write.assert_not_called()
    db.refresh(p)
    assert p.status == "failed"
    row = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).one()
    assert "일일 캡" in row.rationale


def test_daily_cap_counts_only_successful_writes(db):
    _settings(db)
    _entity(db, "grp-web", "WEB_SITE")
    # 실패행(after_value None)·dry-run행은 캡 카운트에서 제외 — 20건 있어도 통과해야 한다
    for i in range(20):
        db.add(NaverChangeLog(
            entity_type="search_term", entity_id=f"f{i}", campaign_id="cmp1",
            action="exclude_search_term", dry_run=False, after_value=None,  # 실패행
            changed_at=_NOW, executed_at=_NOW,
        ))
    db.commit()
    assert harness._count_search_term_excludes_today(db, _NOW) == 0


# ── ②-b 일일 캡 TOCTOU 2단 검증(codex[P2]) ──
def _committed_exclude(db, term, when=_NOW):
    db.add(NaverChangeLog(
        entity_type="search_term", entity_id=term, campaign_id="cmp1",
        action="exclude_search_term", dry_run=False, after_value="{}",
        changed_at=when, executed_at=when,
    ))


def test_daily_cap_toctou_rollback_on_concurrent_commit(db):
    # 사전 검사(9<10)는 통과하지만, 실쓰기 API 왕복 도중 동시 Confirm 2건이 먼저 커밋되어
    # 재카운트 시점엔 11(≥10)이 된다 — 방금 등록한 키워드를 즉시 롤백하고 failed 종결해야 한다.
    _settings(db)
    _entity(db, "grp-web", "WEB_SITE")
    for i in range(9):
        _committed_exclude(db, f"seed{i}")
    db.commit()
    p = _proposal(db, adgroup_id="grp-web", term="경합후보")

    def _write_then_race(adgroup_id, keywords):
        # add_restricted_keywords가 "성공"한 바로 그 순간, 동시 Confirm 2건이 먼저 커밋된 것을
        # 재현(사전 검사 통과 후 API 왕복 사이의 경합 창).
        _committed_exclude(db, "race1")
        _committed_exclude(db, "race2")
        db.commit()
        return _write_result(keywords[0])

    with patch.object(harness.naver_sa_writer, "add_restricted_keywords",
                       side_effect=_write_then_race) as mock_write, \
         patch.object(harness.naver_sa_writer, "delete_restricted_keywords") as mock_delete:
        with pytest.raises(naver_sa_writer.WriteError):
            harness.execute(db, p.id, dry_run=False, now=_NOW)
    mock_write.assert_called_once_with("grp-web", ["경합후보"])
    mock_delete.assert_called_once_with("grp-web", ["rkw-1"])  # 방금 등록한 키워드 원복
    db.refresh(p)
    assert p.status == "failed"
    row = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).one()
    assert "동시성 초과 롤백" in row.rationale
    assert "롤백 성공" in row.rationale
    assert row.after_value is None  # 원복된 행은 캡 카운트에 다시 잡히면 안 됨(불변식 유지)
    # 캡 카운트 자체는 오염 없이 실재 성공행(9 seed + race1 + race2)만 반영
    assert harness._count_search_term_excludes_today(db, _NOW) == 11


def test_daily_cap_toctou_rollback_failure_records_honestly(db):
    # 롤백(delete_restricted_keywords) 자체가 실패해도 fail-open 하지 않는다 — proposal은
    # failed로 종결하고, 네이버 측에 키워드가 남아있을 수 있다는 사실을 change_log에 정직히 남긴다.
    _settings(db)
    _entity(db, "grp-web", "WEB_SITE")
    for i in range(9):
        _committed_exclude(db, f"seed{i}")
    db.commit()
    p = _proposal(db, adgroup_id="grp-web", term="경합후보2")

    def _write_then_race(adgroup_id, keywords):
        _committed_exclude(db, "race1")
        _committed_exclude(db, "race2")
        db.commit()
        return _write_result(keywords[0])

    with patch.object(harness.naver_sa_writer, "add_restricted_keywords",
                       side_effect=_write_then_race), \
         patch.object(harness.naver_sa_writer, "delete_restricted_keywords",
                       side_effect=naver_sa_writer.WriteVerificationError("삭제 후 재조회에 잔존")):
        with pytest.raises(naver_sa_writer.WriteError):
            harness.execute(db, p.id, dry_run=False, now=_NOW)
    db.refresh(p)
    assert p.status == "failed"
    row = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).one()
    assert "롤백 실패" in row.rationale
    assert "수동 확인 필요" in row.rationale
    assert row.after_value is None


# ── ②-c 실행 시점 전환 재검증(GATE ⑥, codex[P1] stale) ──
def test_stale_conversion_reverify_rejects_before_writer(db):
    # 판정(SS2) 후 Confirm 사이에 purchase 전환이 붙은 stale 후보 — 쓰기 직전 재조회로 §1-1
    # 전환 보호 게이트를 다시 걸어 거부(writer 미호출).
    _settings(db)
    _entity(db, "grp-web", "WEB_SITE")
    db.add(NaverSearchTermDaily(
        ad_date=date(2026, 7, 22), campaign_id="cmp1", adgroup_id="grp-web",
        search_term="전환붙은검색어", source="shopping", imp=100, clk=20, cost=6000, rank_sum=0,
        conv_purchase_cnt=1, conv_direct_cnt=1, conv_purchase_amt=15900, cart_cnt=0, cart_amt=0,
    ))
    db.commit()
    p = _proposal(db, adgroup_id="grp-web", term="전환붙은검색어")
    with patch.object(harness.naver_sa_writer, "add_restricted_keywords") as mock_write:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False, now=_NOW)
    mock_write.assert_not_called()  # 쓰기 미호출(전환 보호)
    db.refresh(p)
    assert p.status == "failed"
    row = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).one()
    assert "실행 시점 전환 발생" in row.rationale
    assert row.after_value is None


def test_stale_conversion_reverify_allows_when_no_conversion(db):
    # 창 내 전환 0(성과행만)이면 재검증 통과 — 정상 제외 실쓰기.
    _settings(db)
    _entity(db, "grp-web", "WEB_SITE")
    db.add(NaverSearchTermDaily(
        ad_date=date(2026, 7, 22), campaign_id="cmp1", adgroup_id="grp-web",
        search_term="무전환검색어", source="expkeyword", imp=100, clk=20, cost=6000, rank_sum=0,
    ))
    db.commit()
    p = _proposal(db, adgroup_id="grp-web", term="무전환검색어")
    with patch.object(harness.naver_sa_writer, "add_restricted_keywords",
                      return_value=_write_result("무전환검색어")) as mock_write:
        log_entry = harness.execute(db, p.id, dry_run=False, now=_NOW)
    mock_write.assert_called_once()
    assert log_entry.action == "exclude_search_term"


# ── ②-d 캡 원자적 예약(codex[P1] TOCTOU) ──
def test_atomic_cap_reservation_blocks_on_concurrent_executing(db):
    # 9 커밋 성공 + 다른 executing 제외 제안 1건 존재 → 자기까지 executing이 되면
    # 9 + 2(self + 기존) = 11 > 10 → 쓰기 전 원자 예약 초과로 중단(writer 미호출).
    _settings(db)
    _entity(db, "grp-web", "WEB_SITE")
    for i in range(9):
        _committed_exclude(db, f"seed{i}")
    db.commit()
    # 다른 executing 제안 + 자기 — 둘 다 오늘(_NOW KST일) 생성분(캡 예약 오늘-한정 필터 안)
    _proposal(db, adgroup_id="grp-web", term="동시진행", status="executing", created_at=_TODAY_UTC)
    p = _proposal(db, adgroup_id="grp-web", term="원자예약후보", created_at=_TODAY_UTC)
    with patch.object(harness.naver_sa_writer, "add_restricted_keywords") as mock_write:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False, now=_NOW)
    mock_write.assert_not_called()  # 쓰기 미호출(캡 관통 원천 차단)
    db.refresh(p)
    assert p.status == "failed"
    row = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).one()
    assert "원자 예약 초과" in row.rationale
    assert row.after_value is None


def test_atomic_cap_reservation_allows_exactly_at_cap(db):
    # 9 커밋 + 자기 executing 1 = 10 = 캡 → 초과 아님(> 캡 false) → 10번째 쓰기 허용.
    _settings(db)
    _entity(db, "grp-web", "WEB_SITE")
    for i in range(9):
        _committed_exclude(db, f"seed{i}")
    db.commit()
    p = _proposal(db, adgroup_id="grp-web", term="10번째", created_at=_TODAY_UTC)
    with patch.object(harness.naver_sa_writer, "add_restricted_keywords",
                      return_value=_write_result("10번째")) as mock_write:
        log_entry = harness.execute(db, p.id, dry_run=False, now=_NOW)
    mock_write.assert_called_once()
    assert log_entry.action == "exclude_search_term"


def test_atomic_cap_reservation_ignores_stale_yesterday_executing(db):
    # 어제 생성돼 executing에 잔존한 stale 클레임(크래시 잔존)은 오늘 캡 예약에서 제외한다
    # (codex 2R[P2]) — 이게 없으면 stale 1건이 매일 캡을 1씩 영구 잠식한다. 9 커밋 + 자기(오늘)
    # 1 = 10 = 캡, 어제 stale은 미포함 → 통과(쓰기 허용).
    _settings(db)
    _entity(db, "grp-web", "WEB_SITE")
    for i in range(9):
        _committed_exclude(db, f"seed{i}")
    db.commit()
    # 어제 KST일에 생성된 stale executing(UTC로 -1일)
    _proposal(db, adgroup_id="grp-web", term="어제잔존", status="executing",
              created_at=_TODAY_UTC - timedelta(days=1))
    p = _proposal(db, adgroup_id="grp-web", term="오늘10번째", created_at=_TODAY_UTC)
    with patch.object(harness.naver_sa_writer, "add_restricted_keywords",
                      return_value=_write_result("오늘10번째")) as mock_write:
        log_entry = harness.execute(db, p.id, dry_run=False, now=_NOW)
    mock_write.assert_called_once()  # 어제 stale 미포함 → 캡 여유 있음 → 쓰기 허용
    assert log_entry.action == "exclude_search_term"


# ── ③ Confirm 없는 자동 발사 0 ──
def test_search_term_exclude_not_in_daily_lane_types():
    assert _TYPE not in auto_operator._DAILY_LANE_PROPOSAL_TYPES


def test_search_term_exclude_excluded_from_delegation():
    # exclude_search_term은 OPEN_ACTIONS·_WRITE_EXECUTORS에 있지만 위임 자동승인에선 영구 제외
    assert "exclude_search_term" in harness.open_executable_actions()
    assert _TYPE not in delegation_gate.delegable_types()


# ── ⑥ 킬스위치 사전 등록(미래 자동 활성화 대비) ──
def test_ss_exclude_approval_source_respects_killswitch(db):
    # ss_exclude 승인원(현재 미배선)이라도 킬스위치 OFF면 실행 거부 — 사전 등록 검증
    _settings(db, optimizer="ours", auto_operate=False)
    _entity(db, "grp-web", "WEB_SITE")
    p = _proposal(db, adgroup_id="grp-web", term="자동검색어",
                  approval_source=judge.APPROVAL_SOURCE_SS_EXCLUDE)
    with patch.object(harness.naver_sa_writer, "add_restricted_keywords") as mock_write:
        with pytest.raises(harness.KillSwitchEngagedError):
            harness.execute(db, p.id, dry_run=False, now=_NOW)
    mock_write.assert_not_called()


def test_console_approval_not_killswitch_gated(db):
    # 콘솔 Confirm(approval_source=None→console 아님, 여기선 None)은 킬스위치 게이트 대상 아님
    _settings(db, optimizer="ours", auto_operate=False)
    _entity(db, "grp-web", "WEB_SITE")
    p = _proposal(db, adgroup_id="grp-web", term="수동검색어", approval_source=None)
    with patch.object(harness.naver_sa_writer, "add_restricted_keywords",
                      return_value=_write_result("수동검색어")):
        log_entry = harness.execute(db, p.id, dry_run=False, now=_NOW)
    assert log_entry.action == "exclude_search_term"


# ── real_write_blocker 구조 판정 ──
def test_real_write_blocker_structural(db):
    ok = _proposal(db, adgroup_id="grp-web", term="정상", status="pending")
    assert harness.real_write_blocker(ok) is None
    bad_type = NaverProposal(proposal_type=_TYPE, target_type="keyword", target_id="nkw-1",
                             campaign_id="cmp1", adgroup_id="grp-web", status="pending")
    assert harness.real_write_blocker(bad_type) is not None
    no_grp = NaverProposal(proposal_type=_TYPE, target_type="search_term", target_id="x",
                           campaign_id="cmp1", adgroup_id=None, status="pending")
    assert harness.real_write_blocker(no_grp) is not None


# ══════════════════════════════════════════════════════════════════
# SS3 생성 레인 (쇼핑 브리핑만 — 파워링크 자동 발사는 test_naver_searchterm_px2.py)
# ══════════════════════════════════════════════════════════════════
def _map_product(db, adgroup_id, cpid, margin="500"):
    db.add(NaverProductBep(
        channel_id=NAVER_CHANNEL_ID, channel_product_id=cpid, has_cost=True, product_name="무관템",
        selling_price=Decimal("1000"), contribution_margin=Decimal(margin), cost_price=0,
        commission_rate=0, logistics_cost=0,
    ))
    db.add(NaverAdgroupProduct(adgroup_id=adgroup_id, mall_product_id=cpid, campaign_id="cmp1"))
    db.commit()


def _term(db, *, term, source, adgroup_id, clk=20, cost=6000, ad_date=date(2026, 7, 20)):
    db.add(NaverSearchTermDaily(
        ad_date=ad_date, campaign_id="cmp1", adgroup_id=adgroup_id, search_term=term, source=source,
        imp=100, clk=clk, cost=cost, rank_sum=0,
        conv_purchase_cnt=0, conv_direct_cnt=0, conv_purchase_amt=0, cart_cnt=0, cart_amt=0,
    ))
    db.commit()


def test_lane_shopping_candidates_never_create_proposals(db):
    # 쇼핑 후보만 있어도(파워링크 0건) 제안·자동 발사 0건 — briefing diary만 산출(SS3-B, §실측-0).
    _map_product(db, "grp-shop", "PS")
    _term(db, term="쇼핑손실", source="shopping", adgroup_id="grp-shop")
    res = lane.run_search_term_ss_lane(db, now=_NOW)
    assert res["shopping_candidates"] == 1
    assert res["proposals_created"] == 0
    assert res["powerlink_fired"] == 0
    assert db.query(NaverProposal).filter(NaverProposal.proposal_type == _TYPE).count() == 0
    briefs = db.query(OpsDiaryEntry).filter(OpsDiaryEntry.event_type == "observe").all()
    assert len(briefs) == 1
    assert "검색어제외 브리핑" in briefs[0].rationale
    assert "쇼핑손실" in briefs[0].rationale


def test_lane_powerlink_out_of_scope_never_fires(db):
    # 파워링크 후보라도 auto_operate 스코프 밖(settings 없음)이면 자동 발사 0(§0 3 대행사 무실쓰기).
    _map_product(db, "grp-web", "PW")
    _term(db, term="파워손실", source="expkeyword", adgroup_id="grp-web")
    with patch.object(harness.naver_sa_writer, "add_restricted_keywords") as mock_write:
        res = lane.run_search_term_ss_lane(db, now=_NOW)
    mock_write.assert_not_called()
    assert res["powerlink_fired"] == 0
    assert res["powerlink_candidates"] == 0  # judge가 스코프 밖 후보를 powerlink에 넣지 않음
    assert db.query(NaverProposal).filter(NaverProposal.proposal_type == _TYPE).count() == 0
