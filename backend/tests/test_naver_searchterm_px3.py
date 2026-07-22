# test_naver_searchterm_px3.py — PX3 in-out 재심사 루프(search_term_ss_lane._run_reexamination) 테스트
# 커버(PLAN_naver-ad-powerlink-autoexclude.md §2·§3, GATE): 개방(excluded→probation·delete·복귀
#   change_log)·일일 복귀 캡·킬스위치 존중(제외·복귀 양쪽)·재판정(probation→재제외 cycle 승계 /
#   restored)·백오프 30→60→90 cap·restrict_kwd_id 부재 개방 불가.
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
    NaverCampaignSettings,
    NaverChangeLog,
    NaverEntity,
    NaverProposal,
    NaverSearchTermExclusion,
)
from app.services.naver_ad import naver_sa_writer
from app.services.naver_ad import search_term_judge as judge
from app.services.naver_ad import search_term_ss_lane as lane

_NOW = datetime(2026, 7, 22, 9, 0, 0)  # today = 2026-07-22


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


def _scope(db, campaign_id="cmp1", adgroup_id="grp-web", auto_operate=True):
    db.add(NaverCampaignSettings(campaign_id=campaign_id, optimizer="ours", auto_operate=auto_operate))
    db.add(NaverEntity(entity_type="adgroup", entity_id=adgroup_id, parent_id=campaign_id,
                       campaign_id=campaign_id, campaign_type="WEB_SITE", name="grp", status="on"))
    db.commit()


def _excl(db, *, term, adgroup_id="grp-web", campaign_id="cmp1", status="excluded", cycle=1,
          restrict_kwd_id="rkw-1", next_review_at=date(2026, 7, 22), probation_until=None):
    row = NaverSearchTermExclusion(
        campaign_id=campaign_id, adgroup_id=adgroup_id, search_term=term, status=status, cycle=cycle,
        restrict_kwd_id=restrict_kwd_id, excluded_at=_NOW, last_transition_at=_NOW,
        next_review_at=next_review_at, probation_until=probation_until,
    )
    db.add(row)
    db.commit()
    return row


def _del_result():
    return naver_sa_writer.WriteResult(
        action="delete_restricted_keywords", before=[], response=None, after=[], created_ids=[],
    )


def _add_result(term, kwd_id="rkw-new"):
    after = [{"nccAdgroupRestrictKwdId": kwd_id, "keyword": term}]
    return naver_sa_writer.WriteResult(
        action="add_restricted_keywords", before=[], response=after, after=after, created_ids=[kwd_id],
    )


# ── ① 개방(excluded → probation): delete + 복귀 change_log + probation_until=+14 ──
def test_open_due_exclusion_to_probation(db):
    _scope(db)
    row = _excl(db, term="복귀후보", next_review_at=date(2026, 7, 22))
    with patch.object(lane.naver_sa_writer, "delete_restricted_keywords",
                      return_value=_del_result()) as mock_del:
        res = lane._run_reexamination(db, [], _NOW)
    mock_del.assert_called_once_with("grp-web", ["rkw-1"])
    assert res["opened"] == 1
    db.refresh(row)
    assert row.status == "probation"
    assert row.probation_until == date(2026, 8, 5)  # today + 14
    cl = db.query(NaverChangeLog).filter(NaverChangeLog.action == "restore_search_term").one()
    assert cl.dry_run is False and cl.after_value is not None
    assert lane._RETURN_MARKER in cl.rationale


def test_not_yet_due_not_opened(db):
    _scope(db)
    _excl(db, term="아직", next_review_at=date(2026, 7, 25))  # 미래
    with patch.object(lane.naver_sa_writer, "delete_restricted_keywords") as mock_del:
        res = lane._run_reexamination(db, [], _NOW)
    mock_del.assert_not_called()
    assert res["opened"] == 0


def _restore_log(db, n, *, changed_at=_NOW):
    """오늘 성공한 복귀(restore_search_term) change_log n건 시드 — _count_returns_today 카운트."""
    for i in range(n):
        db.add(NaverChangeLog(
            entity_type="search_term", entity_id=f"r{i}", campaign_id="cmp1",
            action=lane._RESTORE_ACTION, dry_run=False, after_value='{"after": []}',
            changed_at=changed_at, executed_at=changed_at,
        ))
    db.commit()


# ── P2-1 GATE: 복귀 캡 재카운트 백스톱 — delete 호출 직전 재카운트가 캡 도달이면 개방 0(fail-closed).
#   동시 실행(크론+catch-up 데몬)에서 소프트 remaining_return이 놓치는 2배 개방을 차단한다. ──
def test_open_exclusion_recount_backstop_blocks_at_cap(db):
    _scope(db)
    row = _excl(db, term="복귀후보", next_review_at=date(2026, 7, 22))
    _restore_log(db, lane._SS_DAILY_RETURN_CAP)  # 이미 오늘 캡 도달
    with patch.object(lane.naver_sa_writer, "delete_restricted_keywords") as mock_del:
        assert lane._open_exclusion(db, row, _NOW) is False
    mock_del.assert_not_called()  # 쓰기 직전 재카운트 백스톱이 delete 차단
    db.refresh(row)
    assert row.status == "excluded"  # 상태 유지(다음 레인 재시도)


# ── C1①(codex 1R[P1-1]): 킬스위치 delete 직전 재확인 — 루프 진입 후 OFF돼도 개방 0(fail-closed) ──
def test_open_exclusion_killswitch_recheck_before_delete_blocks(db):
    _scope(db, auto_operate=False)  # delete 직전 킬스위치 OFF 상황(직접 호출로 TOCTOU 재현)
    row = _excl(db, term="복귀후보", next_review_at=date(2026, 7, 22))
    with patch.object(lane.naver_sa_writer, "delete_restricted_keywords") as mock_del:
        assert lane._open_exclusion(db, row, _NOW) is False
    mock_del.assert_not_called()  # 킬스위치 재확인이 delete 차단
    db.refresh(row)
    assert row.status == "excluded"  # 상태 유지


# ── C1②(codex 1R[P1-3]): adgroup 소속 미검증(상태 행 campaign_id 오염) → 개방 0(대행사 그룹 delete 차단) ──
def test_open_exclusion_adgroup_membership_mismatch_blocks(db):
    _scope(db)  # naver_entity: grp-web → cmp1
    # 상태 행의 campaign_id가 실제 소속(cmp1)과 다른 오염 상황.
    row = _excl(db, term="소속불일치", campaign_id="cmp-대행사", next_review_at=date(2026, 7, 22))
    with patch.object(lane.naver_sa_writer, "delete_restricted_keywords") as mock_del:
        assert lane._open_exclusion(db, row, _NOW) is False
    mock_del.assert_not_called()  # parent_id(cmp1) ≠ campaign_id(cmp-대행사) → fail-closed
    db.refresh(row)
    assert row.status == "excluded"


def test_open_exclusion_adgroup_entity_absent_blocks(db):
    _scope(db)
    # naver_entity에 없는 그룹(인벤토리 부재) — 소속 증명 불가 = fail-closed.
    row = _excl(db, term="엔티티없음", adgroup_id="grp-미동기화", next_review_at=date(2026, 7, 22))
    with patch.object(lane.naver_sa_writer, "delete_restricted_keywords") as mock_del:
        assert lane._open_exclusion(db, row, _NOW) is False
    mock_del.assert_not_called()
    db.refresh(row)
    assert row.status == "excluded"


# ── C2(codex 1R[P1-2]): 클레임 경합(두 세션 시뮬) — 한쪽만 delete, 선점당한 러너는 skip ──
def test_open_exclusion_claim_race_only_one_proceeds(db):
    _scope(db)
    row = _excl(db, term="경합개방", next_review_at=date(2026, 7, 22))

    def steal(db_, adgroup_id, campaign_id):
        # 소속 검증(claim 직전) 도중 다른 러너가 먼저 claim(excluded→probation)한 상황 재현.
        db_.query(NaverSearchTermExclusion).filter(
            NaverSearchTermExclusion.id == row.id
        ).update({"status": "probation"}, synchronize_session=False)
        db_.commit()
        return True  # 소속은 정상(True) — 오직 claim rowcount만 0이 되게 한다

    with patch.object(lane, "_adgroup_belongs_to_campaign", side_effect=steal):
        with patch.object(lane.naver_sa_writer, "delete_restricted_keywords") as mock_del:
            assert lane._open_exclusion(db, row, _NOW) is False
    mock_del.assert_not_called()  # 선점당함 → 이 러너는 delete 안 함(이중 delete 방지)


def test_open_exclusion_claim_rollback_on_delete_failure(db):
    """delete 실패 시 클레임(probation) 롤백 → status='excluded' 복원(다음 레인 재시도)."""
    _scope(db)
    row = _excl(db, term="개방실패", next_review_at=date(2026, 7, 22))
    with patch.object(lane.naver_sa_writer, "delete_restricted_keywords",
                      side_effect=RuntimeError("API 500")):
        assert lane._open_exclusion(db, row, _NOW) is False
    db.refresh(row)
    assert row.status == "excluded"  # 클레임 롤백(probation→excluded 복원)
    # fail change_log는 after_value 없음 → _count_returns_today 미카운트
    fail = db.query(NaverChangeLog).filter(NaverChangeLog.action == "restore_search_term").one()
    assert fail.outcome == "failed" and fail.after_value is None


# ── C5(codex 1R[P1-4]): 크래시 고아(상태 행 없는 확정 제외) 자가 치유 — 상태 행 재생성 ──
def test_reconcile_orphan_exclusion_recreates_state_row(db):
    _scope(db)
    p = NaverProposal(
        proposal_type=judge.SEARCH_TERM_EXCLUDE_TYPE, target_type="search_term",
        target_id="고아검색어", campaign_id="cmp1", adgroup_id="grp-web",
        approval_source=judge.APPROVAL_SOURCE_SS_EXCLUDE, status="approved",
    )
    db.add(p)
    db.commit()
    db.add(NaverChangeLog(
        entity_type="search_term", entity_id="고아검색어", campaign_id="cmp1",
        action="exclude_search_term", dry_run=False,
        after_value='{"after": [], "created_ids": ["rkw-orphan"]}',
        proposal_id=p.id, changed_at=_NOW, executed_at=_NOW,
    ))
    db.commit()  # 상태 행은 없음(=크래시 고아)

    res = lane._run_reexamination(db, [], _NOW)
    assert res["healed"] == 1
    row = db.query(NaverSearchTermExclusion).filter(
        NaverSearchTermExclusion.search_term == "고아검색어").one()
    assert row.status == "excluded"
    assert row.cycle == 1
    assert row.restrict_kwd_id == "rkw-orphan"  # after_value created_ids에서 회수
    assert row.next_review_at == date(2026, 7, 22) + timedelta(days=30)


def test_reconcile_skips_when_state_row_exists(db):
    """이미 상태 행이 있으면(어느 status든) 치유 대상 아님 — 중복 생성 0."""
    _scope(db)
    p = NaverProposal(
        proposal_type=judge.SEARCH_TERM_EXCLUDE_TYPE, target_type="search_term",
        target_id="이미있음", campaign_id="cmp1", adgroup_id="grp-web", status="approved",
    )
    db.add(p)
    db.commit()
    db.add(NaverChangeLog(
        entity_type="search_term", entity_id="이미있음", campaign_id="cmp1",
        action="exclude_search_term", dry_run=False,
        after_value='{"created_ids": ["rkw-1"]}', proposal_id=p.id,
        changed_at=_NOW, executed_at=_NOW,
    ))
    _excl(db, term="이미있음", status="restored", next_review_at=None)  # 이미 상태 행 존재
    res = lane._run_reexamination(db, [], _NOW)
    assert res["healed"] == 0
    assert db.query(NaverSearchTermExclusion).filter(
        NaverSearchTermExclusion.search_term == "이미있음").count() == 1


def test_open_exclusion_recount_backstop_allows_below_cap(db):
    _scope(db)
    row = _excl(db, term="복귀후보", next_review_at=date(2026, 7, 22))
    _restore_log(db, lane._SS_DAILY_RETURN_CAP - 1)  # 캡 미만(1칸 여유)
    with patch.object(lane.naver_sa_writer, "delete_restricted_keywords",
                      return_value=_del_result()) as mock_del:
        assert lane._open_exclusion(db, row, _NOW) is True
    mock_del.assert_called_once()  # 백스톱이 정상 개방은 막지 않음


# ── 일일 복귀 캡 ──
def test_daily_return_cap(db):
    _scope(db)
    for i in range(lane._SS_DAILY_RETURN_CAP + 3):  # 13건 due
        _excl(db, term=f"복귀{i}", restrict_kwd_id=f"rkw-{i}", next_review_at=date(2026, 7, 22))
    with patch.object(lane.naver_sa_writer, "delete_restricted_keywords", return_value=_del_result()):
        res = lane._run_reexamination(db, [], _NOW)
    assert res["opened"] == lane._SS_DAILY_RETURN_CAP  # 10만 개방
    assert db.query(NaverSearchTermExclusion).filter(
        NaverSearchTermExclusion.status == "probation").count() == lane._SS_DAILY_RETURN_CAP


# ── ② 킬스위치 OFF: 제외·복귀 양쪽 정지(개방 안 함) ──
def test_killswitch_off_freezes_open(db):
    _scope(db, auto_operate=False)  # 킬스위치 OFF
    row = _excl(db, term="복귀후보", next_review_at=date(2026, 7, 22))
    with patch.object(lane.naver_sa_writer, "delete_restricted_keywords") as mock_del:
        res = lane._run_reexamination(db, [], _NOW)
    mock_del.assert_not_called()
    assert res["opened"] == 0
    db.refresh(row)
    assert row.status == "excluded"  # 미실행 정직 상태(다음 레인 재시도)


# ── restrict_kwd_id 부재 → 개방 불가(상태 유지) ──
def test_open_without_restrict_kwd_id_skips(db):
    _scope(db)
    row = _excl(db, term="아이디없음", restrict_kwd_id=None, next_review_at=date(2026, 7, 22))
    with patch.object(lane.naver_sa_writer, "delete_restricted_keywords") as mock_del:
        res = lane._run_reexamination(db, [], _NOW)
    mock_del.assert_not_called()
    assert res["opened"] == 0
    db.refresh(row)
    assert row.status == "excluded"


# ── ③ probation 만료 재판정: 여전히 후보 → 재제외(cycle 승계·백오프 60d) ──
def _cand(term, adgroup_id="grp-web", campaign_id="cmp1", cost=6000):
    return {"adgroup_id": adgroup_id, "search_term": term, "campaign_id": campaign_id,
            "cost": cost, "reason": "[검색어제외] 재판정 재제외"}


def test_probation_still_candidate_reexcludes_with_cycle_succession(db):
    _scope(db)
    row = _excl(db, term="여전히손실", status="probation", cycle=1, restrict_kwd_id=None,
                next_review_at=None, probation_until=date(2026, 7, 22))
    with patch.object(lane.naver_sa_writer, "add_restricted_keywords",
                      return_value=_add_result("여전히손실", kwd_id="rkw-9")) as mock_add:
        res = lane._run_reexamination(db, [_cand("여전히손실")], _NOW)
    mock_add.assert_called_once_with("grp-web", ["여전히손실"])
    assert res["reexcluded"] == 1
    db.refresh(row)
    assert row.status == "excluded"
    assert row.cycle == 2  # 승계 +1
    assert row.restrict_kwd_id == "rkw-9"
    assert row.next_review_at == date(2026, 7, 22) + timedelta(days=60)  # min(30×2,90)=60


# ── probation 만료 재판정: 후보 아님 → restored(행 보존, 실쓰기 0) ──
def test_probation_not_candidate_restores(db):
    _scope(db)
    row = _excl(db, term="회복됨", status="probation", cycle=1, restrict_kwd_id=None,
                next_review_at=None, probation_until=date(2026, 7, 22))
    with patch.object(lane.naver_sa_writer, "add_restricted_keywords") as mock_add:
        res = lane._run_reexamination(db, [], _NOW)  # powerlink 후보 아님
    mock_add.assert_not_called()
    assert res["restored"] == 1
    db.refresh(row)
    assert row.status == "restored"


def test_probation_not_expired_untouched(db):
    _scope(db)
    row = _excl(db, term="관찰중", status="probation", restrict_kwd_id=None,
                next_review_at=None, probation_until=date(2026, 7, 25))  # 미래
    res = lane._run_reexamination(db, [_cand("관찰중")], _NOW)
    assert res["reexcluded"] == 0 and res["restored"] == 0
    db.refresh(row)
    assert row.status == "probation"


# ── ④ 백오프 30→60→90 cap ──
def test_backoff_caps_at_90(db):
    _scope(db)
    row = _excl(db, term="반복손실", status="probation", cycle=3, restrict_kwd_id=None,
                next_review_at=None, probation_until=date(2026, 7, 22))
    with patch.object(lane.naver_sa_writer, "add_restricted_keywords",
                      return_value=_add_result("반복손실")):
        lane._run_reexamination(db, [_cand("반복손실")], _NOW)
    db.refresh(row)
    assert row.cycle == 4
    assert row.next_review_at == date(2026, 7, 22) + timedelta(days=90)  # min(30×4,90)=90 cap


# ── 킬스위치 OFF: 재판정(재제외·restored)도 동결 ──
def test_killswitch_off_freezes_reexamination(db):
    _scope(db, auto_operate=False)
    row_c = _excl(db, term="손실여전", status="probation", restrict_kwd_id=None,
                  next_review_at=None, probation_until=date(2026, 7, 22))
    row_r = _excl(db, term="회복됨2", adgroup_id="grp-web", status="probation", restrict_kwd_id=None,
                  next_review_at=None, probation_until=date(2026, 7, 22))
    with patch.object(lane.naver_sa_writer, "add_restricted_keywords") as mock_add:
        res = lane._run_reexamination(db, [_cand("손실여전")], _NOW)
    mock_add.assert_not_called()
    assert res["reexcluded"] == 0 and res["restored"] == 0
    db.refresh(row_c)
    db.refresh(row_r)
    assert row_c.status == "probation" and row_r.status == "probation"  # 전면 동결


# ── F2(codex 2R[P1-a]): 복귀 클레임 크래시 창(status='probation' ∧ probation_until IS NULL) 치유.
#   _open_exclusion이 excluded→probation 클레임 커밋 후 ①delete 전 ②probation_until 세팅 전에
#   크래시하면 개방·재판정·기존 고아 reconcile 어디에도 안 잡히는 좀비가 된다. change_log 증거로
#   두 창을 구분해 복귀시킨다. ──
def _prob_orphan(db, *, term, last_transition_at, adgroup_id="grp-web", campaign_id="cmp1"):
    """probation ∧ probation_until=NULL 고아 1행(last_transition_at 지정)."""
    row = _excl(db, term=term, adgroup_id=adgroup_id, campaign_id=campaign_id,
                status="probation", restrict_kwd_id="rkw-1", next_review_at=date(2026, 7, 22),
                probation_until=None)
    row.last_transition_at = last_transition_at
    db.commit()
    return row


def test_probation_orphan_window2_restore_log_backfills_probation_until(db):
    """창②(delete·복귀 change_log 커밋 후 probation_until 세팅 전 크래시): 클레임 이후 커밋된
    복귀 change_log(delete 성공 증거) 존재 → probation_until = last_transition_at + 14 소급 복원.
    _run_reexamination 경유로 반환 dict 카운트(probation_healed)까지 검증."""
    _scope(db)
    lt = _NOW - timedelta(hours=1)  # 30분 창 밖(고아 확정)
    row = _prob_orphan(db, term="창2고아", last_transition_at=lt)
    db.add(NaverChangeLog(  # 클레임(lt) 이후 커밋된 복귀 성공 로그(after_value 존재)
        entity_type="search_term", entity_id="창2고아", campaign_id="cmp1",
        action=lane._RESTORE_ACTION, dry_run=False, after_value='{"after": []}',
        changed_at=lt, executed_at=lt,
    ))
    db.commit()
    res = lane._run_reexamination(db, [], _NOW)
    assert res["probation_healed"] == 1
    db.refresh(row)
    assert row.status == "probation"  # delete는 성공했으므로 probation 유지
    assert row.probation_until == lt.date() + timedelta(days=lane._PROBATION_DAYS)


def test_probation_orphan_window1_no_restore_log_restores_excluded(db):
    """창①(클레임 커밋 후 delete 전 크래시): 복귀 change_log 없음 → delete 미실행(키워드 등록
    유지) → status='excluded' 복원(다음 재심사가 정상 개방 재시도). 직접 호출로 치유만 격리 검증."""
    _scope(db)
    row = _prob_orphan(db, term="창1고아", last_transition_at=_NOW - timedelta(hours=1))
    healed = lane._reconcile_probation_orphans(db, _NOW)
    assert healed == 1
    db.refresh(row)
    assert row.status == "excluded"
    assert row.probation_until is None


def test_probation_orphan_only_stale_restore_log_treated_as_window1(db):
    """복귀 로그가 있어도 클레임(last_transition_at) *이전* 것(과거 사이클 잔재)이면 증거 아님 →
    창①로 판정(excluded 복원). 결정 기준이 changed_at >= last_transition_at임을 못 박는다."""
    _scope(db)
    lt = _NOW - timedelta(hours=1)
    row = _prob_orphan(db, term="과거로그", last_transition_at=lt)
    db.add(NaverChangeLog(  # 클레임보다 앞선(2시간 전) 복귀 로그 — 이번 클레임의 증거가 아님
        entity_type="search_term", entity_id="과거로그", campaign_id="cmp1",
        action=lane._RESTORE_ACTION, dry_run=False, after_value='{"after": []}',
        changed_at=_NOW - timedelta(hours=2), executed_at=_NOW - timedelta(hours=2),
    ))
    db.commit()
    healed = lane._reconcile_probation_orphans(db, _NOW)
    assert healed == 1
    db.refresh(row)
    assert row.status == "excluded"  # 과거 로그는 무시 → 창① 판정
    assert row.probation_until is None


def test_probation_orphan_fresh_claim_within_30min_untouched(db):
    """30분 이내 신선 클레임(진행 중인 정상 개방)은 치유 대상 아님 — 상태 불변(오처리 방지)."""
    _scope(db)
    row = _prob_orphan(db, term="신선클레임", last_transition_at=_NOW - timedelta(minutes=10))
    healed = lane._reconcile_probation_orphans(db, _NOW)
    assert healed == 0
    db.refresh(row)
    assert row.status == "probation"
    assert row.probation_until is None


# ── F1(codex 2R[P1-b]): 소속 검증은 엔진 레벨 독립 커넥션 조회(auto_operator._auto_operate_now
#   관례) — SQLite(WAL)에서 레인 세션이 먼저 읽기 트랜잭션을 연 뒤 entity_sync가 parent_id를
#   커밋해도 스테일 스냅샷이 아니라 신선 값을 본다. ──
def test_adgroup_belongs_reads_fresh_via_independent_connection(tmp_path):
    from sqlalchemy import create_engine as _create_engine, event

    db_file = tmp_path / "membership.db"
    engine = _create_engine(f"sqlite:///{db_file}")

    @event.listens_for(engine, "connect")
    def _set_wal(dbapi_conn, _rec):  # WAL — 스냅샷 격리가 실재하는 모드로 검증
        dbapi_conn.execute("PRAGMA journal_mode=WAL")

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    seed = Session()
    seed.add(NaverEntity(entity_type="adgroup", entity_id="grp-web", parent_id="cmp1",
                         campaign_id="cmp1", campaign_type="WEB_SITE", name="grp", status="on"))
    seed.commit()
    seed.close()

    lane_session = Session()
    try:
        # 레인이 조기 쿼리로 읽기 트랜잭션(스냅샷)을 연 상태 재현 — 처음엔 cmp1 소속.
        assert lane_session.query(NaverEntity).filter(
            NaverEntity.entity_id == "grp-web").one().parent_id == "cmp1"
        assert lane._adgroup_belongs_to_campaign(lane_session, "grp-web", "cmp1") is True

        # 타 프로세스(entity_sync)가 별도 커넥션으로 parent_id를 대행사 캠페인으로 이동 커밋.
        with engine.connect() as other:
            other.execute(
                NaverEntity.__table__.update()
                .where(NaverEntity.entity_id == "grp-web")
                .values(parent_id="cmp-대행사")
            )
            other.commit()

        # 독립 커넥션 조회 — 세션 스냅샷과 무관하게 신선한 소속(cmp1 아님·대행사)이 보여야 함.
        assert lane._adgroup_belongs_to_campaign(lane_session, "grp-web", "cmp1") is False
        assert lane._adgroup_belongs_to_campaign(lane_session, "grp-web", "cmp-대행사") is True
        # 행 부재도 fail-closed(False) 유지.
        assert lane._adgroup_belongs_to_campaign(lane_session, "grp-none", "cmp1") is False
    finally:
        lane_session.close()
