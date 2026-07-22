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
    NaverSearchTermExclusion,
)
from app.services.naver_ad import naver_sa_writer
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
