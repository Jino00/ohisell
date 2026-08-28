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
    NaverAdgroupScope,
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


def _scope(db, campaign_id="cmp1", adgroup_id="grp-web", auto_operate=True, campaign_type="WEB_SITE"):
    db.add(NaverCampaignSettings(campaign_id=campaign_id, optimizer="ours", auto_operate=auto_operate))
    db.add(NaverEntity(entity_type="adgroup", entity_id=adgroup_id, parent_id=campaign_id,
                       campaign_id=campaign_id, campaign_type=campaign_type, name="grp", status="on"))
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
    with patch.object(lane.naver_sa_writer, "get_adgroup_type", return_value="WEB_SITE"), \
         patch.object(lane.naver_sa_writer, "delete_restricted_keywords",
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


# ── ★D-NAO-244(적대 리뷰 PR #422 P1-1 상환): 자동운영 스코프 밖 그룹은 개방하지 않는다 ──
#
# 이 레인은 **harness.execute()를 안 거치고** naver_sa_writer.delete_restricted_keywords를
# 직접 부르는 예외 경로라, harness에 단 스코프 게이트가 여기엔 안 걸렸다. 적대 리뷰가 재현했다:
# 스코프를 grp-A로 좁혀도 grp-B의 제외키워드 삭제가 그대로 나갔다.
#
# ★방향이 «복귀»(제외 삭제)라 더 위험했다 — 재제외(_autofire_exclude)는 harness를 타서 이미
#   막히는데 개방만 안 막히면, 스코프 밖 그룹에서 **우리가 검색어를 다시 열어 주는** 비대칭이
#   된다(브레이크는 스코프를 지키는데 그 해제는 안 지키는 꼴).
def test_open_exclusion_out_of_scope_adgroup_blocks(db):
    _scope(db)  # cmp1 / grp-web, auto_operate=True
    # 스코프를 «다른 그룹»으로 좁힌다 → grp-web은 스코프 밖이 된다
    db.add(NaverAdgroupScope(campaign_id="cmp1", adgroup_id="grp-other", enabled=True))
    db.commit()
    row = _excl(db, term="복귀후보", next_review_at=date(2026, 7, 22))
    with patch.object(lane.naver_sa_writer, "delete_restricted_keywords") as mock_del:
        assert lane._open_exclusion(db, row, _NOW) is False
    mock_del.assert_not_called()  # 스코프 게이트가 delete 차단
    db.refresh(row)
    assert row.status == "excluded"  # 상태 유지(fail-closed)


def test_open_exclusion_in_scope_adgroup_proceeds(db):
    """스코프 «안»이면 그대로 개방된다 — 게이트가 상시 막힘이 아님을 확인."""
    _scope(db)
    db.add(NaverAdgroupScope(campaign_id="cmp1", adgroup_id="grp-web", enabled=True))
    db.commit()
    row = _excl(db, term="복귀후보", next_review_at=date(2026, 7, 22))
    with patch.object(lane.naver_sa_writer, "get_adgroup_type", return_value="WEB_SITE"), \
         patch.object(lane.naver_sa_writer, "delete_restricted_keywords",
                      return_value=_del_result()) as mock_del:
        assert lane._open_exclusion(db, row, _NOW) is True
    mock_del.assert_called_once_with("grp-web", ["rkw-1"])


def test_open_exclusion_unaffected_when_no_scope_rows(db):
    """★행이 0개면 기존 동작 그대로 — 「소급 0」이 이 레인에도 적용된다."""
    _scope(db)
    row = _excl(db, term="복귀후보", next_review_at=date(2026, 7, 22))
    with patch.object(lane.naver_sa_writer, "get_adgroup_type", return_value="WEB_SITE"), \
         patch.object(lane.naver_sa_writer, "delete_restricted_keywords",
                      return_value=_del_result()) as mock_del:
        assert lane._open_exclusion(db, row, _NOW) is True
    mock_del.assert_called_once()


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
    with patch.object(lane.naver_sa_writer, "get_adgroup_type", return_value="WEB_SITE"), \
         patch.object(lane.naver_sa_writer, "delete_restricted_keywords",
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
    with patch.object(lane.naver_sa_writer, "get_adgroup_type", return_value="WEB_SITE"), \
         patch.object(lane.naver_sa_writer, "delete_restricted_keywords",
                      return_value=_del_result()) as mock_del:
        assert lane._open_exclusion(db, row, _NOW) is True
    mock_del.assert_called_once()  # 백스톱이 정상 개방은 막지 않음


# ── D-NAO-271: 유형별 라우팅(delete_restricted_keywords 직접호출 → rollback_exclusions 경유) ──
#
# ★2차 수정(코디네이터, 위임문 원문 참조): _open_exclusion이 클레임 **전**에
#   naver_sa_writer.get_adgroup_type(adgroup_id)로 광고그룹 축을 먼저 해결하고, 그 값을
#   rollback_exclusions(..., adgroup_type=adgroup_type)로 명시 전달한다 — rollback_exclusions
#   자신은 더 이상 라이브를 재조회하지 않는다(왕복 1회 유지, 클레임 전 fail-closed로 순서도
#   개선). get_adgroup_type은 내부적으로 _get_adgroup을 호출하므로 두 지점 중 하나만 mock해도
#   되지만, 여기서는 위임문이 지목한 get_adgroup_type을 직접 mock한다.
def test_open_exclusion_powerlink_routes_through_rollback_exclusions(db):
    """파워링크 회귀: campaign_type=WEB_SITE + restrict_kwd_id 있음 → get_adgroup_type이
    클레임 전에 유형을 해결하고, rollback_exclusions가 adgroup_type=WEB_SITE로 호출되며,
    그 안에서 delete_restricted_keywords(adgroup_id, [restrict_kwd_id])가 종전과 같은
    인자로 불린다(인자는 불변, 유형 해결 지점만 한 겹 늘었다)."""
    _scope(db, campaign_type="WEB_SITE")
    row = _excl(db, term="복귀후보", restrict_kwd_id="rkw-1", next_review_at=date(2026, 7, 22))
    with patch.object(lane.naver_sa_writer, "get_adgroup_type",
                       return_value="WEB_SITE") as mock_type, \
         patch.object(lane.naver_sa_writer, "rollback_exclusions",
                       wraps=lane.naver_sa_writer.rollback_exclusions) as mock_rollback, \
         patch.object(lane.naver_sa_writer, "delete_restricted_keywords",
                       return_value=_del_result()) as mock_del:
        assert lane._open_exclusion(db, row, _NOW) is True
    mock_type.assert_called_once_with("grp-web")
    mock_rollback.assert_called_once_with(
        "grp-web", ["복귀후보"], ["rkw-1"], adgroup_type="WEB_SITE",
    )
    mock_del.assert_called_once_with("grp-web", ["rkw-1"])  # 종전과 100% 같은 인자


def test_open_exclusion_shopping_removes_by_keyword(db):
    """쇼핑 개방: campaign_type=SHOPPING + restrict_kwd_id=None → 초기 가드에 안 걸리고
    remove_shopping_exclusions(adgroup_id, [search_term])가 불린다 — 개방이 원리적으로
    0건이던 결함(D-NAO-271)의 수리 지점."""
    _scope(db, adgroup_id="grp-shop", campaign_type="SHOPPING")
    row = _excl(db, term="복귀후보", adgroup_id="grp-shop", restrict_kwd_id=None,
                next_review_at=date(2026, 7, 22))
    shop_result = naver_sa_writer.WriteResult(
        action="remove_shopping_exclusions", before=[], response=None, after=[], created_ids=[],
    )
    with patch.object(lane.naver_sa_writer, "get_adgroup_type",
                       return_value="SHOPPING"), \
         patch.object(lane.naver_sa_writer, "remove_shopping_exclusions",
                       return_value=shop_result) as mock_remove, \
         patch.object(lane.naver_sa_writer, "delete_restricted_keywords") as mock_del:
        assert lane._open_exclusion(db, row, _NOW) is True
    mock_remove.assert_called_once_with("grp-shop", ["복귀후보"])
    mock_del.assert_not_called()  # 파워링크 경로는 안 탐
    db.refresh(row)
    assert row.status == "probation"


def test_open_exclusion_powerlink_without_id_still_blocks(db):
    """파워링크 + id 없음 → 종전대로 거부(False, 쓰기 0회) — 초기 가드가 여전히 막는다.
    (회귀 커버는 이미 test_open_without_restrict_kwd_id_skips가 _run_reexamination 경유로
    지키고 있다 — 이 테스트는 _open_exclusion을 직접 불러 같은 경계를 못 박는다.)"""
    _scope(db, campaign_type="WEB_SITE")
    row = _excl(db, term="아이디없음", restrict_kwd_id=None, next_review_at=date(2026, 7, 22))
    with patch.object(lane.naver_sa_writer, "get_adgroup_type") as mock_get, \
         patch.object(lane.naver_sa_writer, "delete_restricted_keywords") as mock_del, \
         patch.object(lane.naver_sa_writer, "remove_shopping_exclusions") as mock_remove:
        assert lane._open_exclusion(db, row, _NOW) is False
    mock_get.assert_not_called()  # 초기 가드에서 이미 차단 — 라이브 조회조차 안 감
    mock_del.assert_not_called()
    mock_remove.assert_not_called()
    db.refresh(row)
    assert row.status == "excluded"


def test_open_exclusion_unknown_type_without_id_blocks_not_optimistic_shopping(db):
    """유형 모름(None: naver_entity에 그 광고그룹 행 자체가 없음) + id 없음 → 거부.
    ★모름을 쇼핑으로 낙관하지 않는다 — _campaign_type_of_adgroup이 None을 돌리면 초기 가드의
    `!= SHOPPING_ADGROUP_TYPE` 비교가 True가 되어(None != "SHOPPING") 그대로 막힌다."""
    _scope(db, campaign_type="WEB_SITE")  # grp-web만 등록 — grp-미동기화는 인벤토리에 없음
    row = _excl(db, term="유형모름", adgroup_id="grp-미동기화", restrict_kwd_id=None,
                next_review_at=date(2026, 7, 22))
    with patch.object(lane.naver_sa_writer, "get_adgroup_type") as mock_get, \
         patch.object(lane.naver_sa_writer, "delete_restricted_keywords") as mock_del, \
         patch.object(lane.naver_sa_writer, "remove_shopping_exclusions") as mock_remove:
        assert lane._open_exclusion(db, row, _NOW) is False
    mock_get.assert_not_called()
    mock_del.assert_not_called()
    mock_remove.assert_not_called()
    db.refresh(row)
    assert row.status == "excluded"


# ── D-NAO-271 2차 수정: 클레임 «전» 광고그룹 축 게이트(코디네이터 위임문 5·6) ──
#
# ★요점은 **클레임이 아예 일어나지 않았는가**다. 유형을 클레임 뒤에 알았다면(1차 수정 방식)
#   여기서 막혀도 상태는 이미 probation으로 넘어갔다가 롤백되는 왕복을 거친다 — 그 경우
#   change_log에 「쓰다 실패」가 남는데, 실제로는 «시도조차 못 한 것»이라 사실과 다르다.
#   그래서 이 두 테스트는 반드시 status=="excluded"(클레임이 없었다는 증거)를 단언한다.
def test_open_exclusion_axis_mismatch_blocks_before_claim(db):
    """축 불일치 — 캠페인 축(_campaign_type_of_adgroup)은 SHOPPING인데 광고그룹 축
    (get_adgroup_type, 권위 있는 판정)은 WEB_SITE고 restrict_kwd_id가 None → 초기 가드
    (캠페인 축, 쇼핑이라 안 막힘)는 통과하지만 광고그룹 축 게이트가 클레임 전에 막는다."""
    _scope(db, campaign_type="SHOPPING")  # 캠페인 축: 쇼핑으로 등록 — 초기 가드 통과
    row = _excl(db, term="축불일치", restrict_kwd_id=None, next_review_at=date(2026, 7, 22))
    with patch.object(lane.naver_sa_writer, "get_adgroup_type",
                       return_value="WEB_SITE") as mock_type, \
         patch.object(lane.naver_sa_writer, "delete_restricted_keywords") as mock_del, \
         patch.object(lane.naver_sa_writer, "remove_shopping_exclusions") as mock_remove:
        assert lane._open_exclusion(db, row, _NOW) is False
    mock_type.assert_called_once_with("grp-web")  # 광고그룹 축은 실제로 조회됨(권위 있는 판정)
    mock_del.assert_not_called()
    mock_remove.assert_not_called()
    db.refresh(row)
    assert row.status == "excluded"  # ★클레임이 안 일어났다 — probation을 거치지 않았다
    assert db.query(NaverChangeLog).filter(
        NaverChangeLog.action == lane._RESTORE_ACTION).count() == 0  # 시도 자체가 없었다


def test_open_exclusion_type_unknown_blocks_before_claim(db):
    """유형 모름 — get_adgroup_type(광고그룹 축, 라이브 조회 실패 등)이 None → 클레임 전에
    거부(모름을 아무 유형으로도 낙관하지 않는다). restrict_kwd_id는 있어서 초기(캠페인 축)
    가드는 통과한 뒤, 광고그룹 축 게이트에서 막히는 경로를 고정한다."""
    _scope(db, campaign_type="WEB_SITE")
    row = _excl(db, term="광고그룹유형모름", restrict_kwd_id="rkw-1", next_review_at=date(2026, 7, 22))
    with patch.object(lane.naver_sa_writer, "get_adgroup_type",
                       return_value=None) as mock_type, \
         patch.object(lane.naver_sa_writer, "delete_restricted_keywords") as mock_del, \
         patch.object(lane.naver_sa_writer, "remove_shopping_exclusions") as mock_remove:
        assert lane._open_exclusion(db, row, _NOW) is False
    mock_type.assert_called_once_with("grp-web")
    mock_del.assert_not_called()
    mock_remove.assert_not_called()
    db.refresh(row)
    assert row.status == "excluded"  # ★클레임이 안 일어났다
    # ★M3(가드 제거) 대비 강화: 가드가 없으면 클레임 후 try 블록에서 실패해도 return
    # False·status=excluded는 «우연히 같게» 관측된다(둘 다 network 403이 except를 태움) —
    # 그 경우와 갈라내는 유일한 신호는 «시도 자체를 안 해 change_log가 0건」이다. 가드 제거
    # 시나리오는 실패 change_log(outcome=failed)를 반드시 남기므로 이 단언이 그 변이를 잡는다.
    assert db.query(NaverChangeLog).filter(
        NaverChangeLog.action == lane._RESTORE_ACTION).count() == 0


# ── 일일 복귀 캡 ──
def test_daily_return_cap(db):
    _scope(db)
    for i in range(lane._SS_DAILY_RETURN_CAP + 3):  # 13건 due
        _excl(db, term=f"복귀{i}", restrict_kwd_id=f"rkw-{i}", next_review_at=date(2026, 7, 22))
    with patch.object(lane.naver_sa_writer, "get_adgroup_type", return_value="WEB_SITE"), \
         patch.object(lane.naver_sa_writer, "delete_restricted_keywords", return_value=_del_result()):
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
    with patch.object(lane.naver_sa_writer, "add_exclusions",
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
    with patch.object(lane.naver_sa_writer, "add_exclusions") as mock_add:
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
    with patch.object(lane.naver_sa_writer, "add_exclusions",
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
    with patch.object(lane.naver_sa_writer, "add_exclusions") as mock_add:
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
    db.add(NaverChangeLog(  # 클레임(lt) 이후 커밋된 복귀 성공 로그(after_value에 adgroup_id 병기)
        entity_type="search_term", entity_id="창2고아", campaign_id="cmp1",
        action=lane._RESTORE_ACTION, dry_run=False,
        after_value='{"after": [], "adgroup_id": "grp-web"}',
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
    db.add(NaverChangeLog(  # 클레임보다 앞선(2시간 전) 복귀 로그 — adgroup은 맞지만 시각이 이전이라
        entity_type="search_term", entity_id="과거로그", campaign_id="cmp1",  # 이번 클레임 증거 아님
        action=lane._RESTORE_ACTION, dry_run=False,
        after_value='{"after": [], "adgroup_id": "grp-web"}',
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


def test_probation_orphan_adgroup_match_blocks_cross_group_same_term(db):
    """codex 3R: 같은 캠페인 두 그룹에 동일 검색어. A그룹 복귀 로그(delete 성공)가 B그룹 행의
    증거가 되면 안 된다 — after_value.adgroup_id 매칭으로 교차 오인 차단. A는 자기 로그로 정상
    치유(창②→probation_until 소급), B는 자기 증거 없음으로 창①→excluded 보수 복원."""
    _scope(db)  # grp-web → cmp1
    db.add(NaverEntity(entity_type="adgroup", entity_id="grp-web2", parent_id="cmp1",
                       campaign_id="cmp1", campaign_type="WEB_SITE", name="grp2", status="on"))
    db.commit()
    lt = _NOW - timedelta(hours=1)
    row_a = _prob_orphan(db, term="공통검색어", adgroup_id="grp-web", last_transition_at=lt)
    row_b = _prob_orphan(db, term="공통검색어", adgroup_id="grp-web2", last_transition_at=lt)
    # A그룹 복귀 로그만 존재(delete 성공·adgroup_id=grp-web) — 같은 campaign+term이지만 B 증거 아님.
    db.add(NaverChangeLog(
        entity_type="search_term", entity_id="공통검색어", campaign_id="cmp1",
        action=lane._RESTORE_ACTION, dry_run=False,
        after_value='{"after": [], "adgroup_id": "grp-web"}',
        changed_at=lt, executed_at=lt,
    ))
    db.commit()
    healed = lane._reconcile_probation_orphans(db, _NOW)
    assert healed == 2  # 둘 다 치유(A=창②, B=창①) — 판정만 다르다
    db.refresh(row_a)
    db.refresh(row_b)
    assert row_a.status == "probation"  # 자기 그룹 로그 → 창② 정상 치유
    assert row_a.probation_until == lt.date() + timedelta(days=lane._PROBATION_DAYS)
    assert row_b.status == "excluded"  # A 로그는 B 증거 아님 → 창① 보수 복원
    assert row_b.probation_until is None


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
