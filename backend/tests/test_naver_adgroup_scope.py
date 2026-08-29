# test_naver_adgroup_scope.py — 자동운영 스코프의 «광고그룹» 축 (D-NAO-244)
#
# Jino 원문 2026-08-24: *"우리 엔진의 스코프는 캠페인, 광고그룹 모두 포함해야해"*
#
# 이 파일이 지키는 것 넷:
#   ①진리표 4행 — 특히 「캠페인 ON + 스코프 행 있음 + g ∉ 목록 → OFF」
#   ②캠페인 레벨 액션(예산) hold — 예산은 그룹 귀속이 원리적으로 불가능한 누수구다
#   ③실행 직전 재확인 — 레인이 도는 중 행이 사라지면 그 다음 실행이 막힌다
#   ④★생성·실행 «두 지점이 같은 리졸버를 읽는다» — 한쪽만 고치면 깨지는 테스트
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdgroupScope,
    NaverCampaignSettings,
    NaverProposal,
    OpsDiaryEntry,
)
from app.services.naver_ad import adgroup_scope, naver_execution_harness

CAMPAIGN = "cmp-tpu"
IN_GROUP = "grp-in"       # 스코프 안
OUT_GROUP = "grp-out"     # 스코프 밖
NOW = datetime(2026, 8, 25, 8, 50, 0)


@pytest.fixture
def db():
    # prod 세션 규격과 동일(autoflush=False) — 픽스처가 prod와 다르면 잡아야 할 결함을 못 잡는다.
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _settings(db, *, auto_operate=True, optimizer="ours"):
    db.add(NaverCampaignSettings(
        campaign_id=CAMPAIGN, optimizer=optimizer, auto_operate=auto_operate,
    ))
    db.commit()


def _scope(db, adgroup_id, *, role="accel", enabled=True):
    db.add(NaverAdgroupScope(
        campaign_id=CAMPAIGN, adgroup_id=adgroup_id, role=role, enabled=enabled,
    ))
    db.commit()


# ──────────────────────────────────────────────────────────────────────────
# ① 진리표 4행
# ──────────────────────────────────────────────────────────────────────────

def test_truth_row1_campaign_off_beats_everything(db):
    """캠페인 OFF면 스코프에 있든 없든 전 그룹 OFF — 마스터 킬 불변.

    07-30 "우리가 진행중인 광고 모두 정지 시켜줘"의 집행 경로가 이것이다. 스코프가 이 경로를
    가리면 킬스위치의 의미가 흐려진다."""
    _settings(db, auto_operate=False)
    _scope(db, IN_GROUP)  # 스코프에 «있어도»
    assert adgroup_scope.in_scope_now(db, CAMPAIGN, IN_GROUP) is False
    assert adgroup_scope.in_scope_now(db, CAMPAIGN, OUT_GROUP) is False


def test_truth_row2_no_scope_rows_means_unrestricted(db):
    """스코프 행이 없으면 전 그룹 ON — ★기존 캠페인의 행위가 소급해서 바뀌지 않는다.

    이 행이 「배포 즉시 행위 변화 0」의 코드적 근거다(B3 카나리 게이트의 「기본 빈 집합」 원칙)."""
    _settings(db, auto_operate=True)
    assert adgroup_scope.scoped_adgroup_ids(db, CAMPAIGN) is None  # None = 제한 없음
    assert adgroup_scope.in_scope_now(db, CAMPAIGN, IN_GROUP) is True
    assert adgroup_scope.in_scope_now(db, CAMPAIGN, "아무-그룹이나") is True


def test_truth_row3_in_list_is_on(db):
    _settings(db, auto_operate=True)
    _scope(db, IN_GROUP)
    assert adgroup_scope.in_scope_now(db, CAMPAIGN, IN_GROUP) is True


def test_truth_row4_not_in_list_is_off(db):
    """★계약이 명시적으로 지목한 행 — 스코프를 «쓰는» 이유 그 자체."""
    _settings(db, auto_operate=True)
    _scope(db, IN_GROUP)
    assert adgroup_scope.in_scope_now(db, CAMPAIGN, OUT_GROUP) is False


def test_all_rows_disabled_means_all_off_not_fallback(db):
    """행은 있는데 전부 disabled면 전 그룹 OFF — 「전체로 폴백」이 아니다.

    5개로 좁혀 둔 캠페인에서 5개를 다 끄면 의도는 「아무것도 안 돎」이지 「58개 복귀」가
    아니다. 되돌리기 사다리의 첫 칸(UPDATE 1문·배포 불요)이 여기 선다."""
    _settings(db, auto_operate=True)
    _scope(db, IN_GROUP, enabled=False)
    assert adgroup_scope.scoped_adgroup_ids(db, CAMPAIGN) == frozenset()  # None이 아니다
    assert adgroup_scope.in_scope_now(db, CAMPAIGN, IN_GROUP) is False
    assert adgroup_scope.in_scope_now(db, CAMPAIGN, OUT_GROUP) is False


def test_none_vs_empty_set_are_different(db):
    """None(제한 없음)과 빈 집합(전부 꺼짐)을 섞으면 판정이 통째로 뒤집힌다."""
    _settings(db, auto_operate=True)
    assert adgroup_scope.scoped_adgroup_ids(db, CAMPAIGN) is None
    _scope(db, IN_GROUP, enabled=False)
    assert adgroup_scope.scoped_adgroup_ids(db, CAMPAIGN) == frozenset()


# ──────────────────────────────────────────────────────────────────────────
# ② 캠페인 레벨 액션(예산) hold — 그룹 귀속 불가 누수구
# ──────────────────────────────────────────────────────────────────────────

def test_campaign_level_allowed_when_no_scope(db):
    _settings(db, auto_operate=True)
    assert adgroup_scope.campaign_level_allowed_now(db, CAMPAIGN) is True


def test_campaign_level_held_when_scope_exists(db):
    """★스코프를 쓰는 동안 예산 레버는 사람 몫이다.

    처치군이 5그룹인데 엔진이 캠페인 일예산을 올리면 스코프 «밖» 53그룹의 노출도 같이
    움직인다 — 코드가 막는다고 선언한 경계를 이 레버 하나가 뚫는다."""
    _settings(db, auto_operate=True)
    _scope(db, IN_GROUP)
    assert adgroup_scope.campaign_level_allowed_now(db, CAMPAIGN) is False


def test_adgroup_none_falls_to_hold_when_scope_exists(db):
    """adgroup_id=None(캠페인 레벨 제안)은 스코프가 있으면 안전한 쪽으로 떨어진다."""
    _settings(db, auto_operate=True)
    _scope(db, IN_GROUP)
    assert adgroup_scope.in_scope_now(db, CAMPAIGN, None) is False


def test_adgroup_none_passes_when_no_scope(db):
    """스코프가 없으면 캠페인 레벨 액션은 기존대로 통과 — 소급 0."""
    _settings(db, auto_operate=True)
    assert adgroup_scope.in_scope_now(db, CAMPAIGN, None) is True


# ──────────────────────────────────────────────────────────────────────────
# ③ 실행 직전 재확인 — 레인이 도는 중 행이 사라지면 다음 실행이 막힌다
# ──────────────────────────────────────────────────────────────────────────

def test_scope_is_reread_each_call_not_cached(db):
    """레인 시작 스냅샷만 믿으면 도중에 스코프를 좁혀도 남은 실쓰기가 나간다.

    ⚠️이 테스트가 증명하는 것은 «매 호출 재조회»까지다 — WAL 스냅샷 격리(타 프로세스 커밋이
    보이는가)는 인메모리 StaticPool에서 재현할 수 없다. 그 성질은 `db.get_bind().connect()`
    독립 커넥션 사용으로 확보하며, 근거는 `_auto_operate_now`의 codex 6R[P1] 주석과 같다."""
    _settings(db, auto_operate=True)
    _scope(db, IN_GROUP)
    assert adgroup_scope.in_scope_now(db, CAMPAIGN, IN_GROUP) is True

    db.query(NaverAdgroupScope).filter(
        NaverAdgroupScope.adgroup_id == IN_GROUP
    ).delete()
    db.commit()

    # 행이 전부 사라졌으므로 「스코프 미설정」으로 되돌아간다(제한 없음)
    assert adgroup_scope.scoped_adgroup_ids(db, CAMPAIGN) is None


def test_disabling_mid_run_blocks_next_execution(db):
    """되돌리기 사다리 첫 칸 — enabled=False UPDATE 1문이 즉시 먹는다(배포 불요)."""
    _settings(db, auto_operate=True)
    _scope(db, IN_GROUP)
    assert adgroup_scope.in_scope_now(db, CAMPAIGN, IN_GROUP) is True

    db.query(NaverAdgroupScope).filter(
        NaverAdgroupScope.adgroup_id == IN_GROUP
    ).update({"enabled": False})
    db.commit()

    assert adgroup_scope.in_scope_now(db, CAMPAIGN, IN_GROUP) is False


# ──────────────────────────────────────────────────────────────────────────
# ④ ★생성·실행 두 지점이 «같은 리졸버»를 읽는다 (한쪽만 고치면 깨지는 테스트)
# ──────────────────────────────────────────────────────────────────────────

def _approved_proposal(db, *, adgroup_id, proposal_type="bid_down"):
    p = NaverProposal(
        proposal_type=proposal_type, target_type="adgroup", target_id=adgroup_id or "",
        campaign_id=CAMPAIGN, adgroup_id=adgroup_id, status="approved",
        approval_source="auto_op",  # 엔진 승인분 — 스코프 게이트 적용 대상
        rationale="테스트", target_bid=100,
    )
    db.add(p)
    db.commit()
    return p


def test_harness_execute_blocks_out_of_scope_adgroup(db):
    """실행 초크포인트가 스코프 밖을 막는다 — «계약의 증거»가 서는 자리."""
    _settings(db, auto_operate=True, optimizer="ours")
    _scope(db, IN_GROUP)
    p = _approved_proposal(db, adgroup_id=OUT_GROUP)

    with pytest.raises(naver_execution_harness.ScopeGuardError):
        naver_execution_harness.execute(db, p.id, dry_run=False, now=NOW)


def test_harness_execute_records_blocked_diary(db):
    """막았다는 사실이 운영 일기에 남는다 — 막힌 것과 «애초에 안 만들어진 것»의 구별."""
    _settings(db, auto_operate=True, optimizer="ours")
    _scope(db, IN_GROUP)
    p = _approved_proposal(db, adgroup_id=OUT_GROUP)

    with pytest.raises(naver_execution_harness.ScopeGuardError):
        naver_execution_harness.execute(db, p.id, dry_run=False, now=NOW)

    entries = db.query(OpsDiaryEntry).filter(OpsDiaryEntry.event_type == "blocked").all()
    assert len(entries) == 1
    assert "스코프" in (entries[0].rationale or "")


def test_harness_execute_allows_in_scope_adgroup(db):
    """스코프 «안»은 스코프 게이트를 통과한다(이후 단계는 별개)."""
    _settings(db, auto_operate=True, optimizer="ours")
    _scope(db, IN_GROUP)
    p = _approved_proposal(db, adgroup_id=IN_GROUP)

    # 스코프 게이트를 통과하면 ScopeGuardError는 안 난다. 이후 실쓰기 단계는 이 테스트 범위 밖.
    try:
        naver_execution_harness.execute(db, p.id, dry_run=True, now=NOW)
    except naver_execution_harness.ScopeGuardError:  # pragma: no cover
        pytest.fail("스코프 안인데 ScopeGuardError가 났다")


def test_both_gates_read_the_same_resolver(db):
    """★★한쪽만 고치면 깨지는 테스트.

    리졸버 하나(adgroup_scope.blocked_by_scope)를 «막힘»으로 패치했을 때 생성측과 실행측이
    **둘 다** 막혀야 한다. 어느 한쪽이 조건을 자기 자리에 다시 구현해 두면 그쪽은 패치의
    영향을 안 받아 이 테스트가 실패한다 — D-NAO-125가 남긴 「두 상수는 항상 같이 움직여야
    한다」를 리졸버 수준에서 강제하는 장치다."""
    from app.services.naver_ad import auto_operator

    _settings(db, auto_operate=True, optimizer="ours")
    p = _approved_proposal(db, adgroup_id=IN_GROUP)

    with patch.object(adgroup_scope, "blocked_by_scope", return_value=True):
        # 생성측 — hold 사유를 낸다
        assert auto_operator._scope_hold_reason(db, p) is not None
        # 실행측 — 같은 패치로 막힌다
        with pytest.raises(naver_execution_harness.ScopeGuardError):
            naver_execution_harness.execute(db, p.id, dry_run=False, now=NOW)

    # 패치를 풀면 둘 다 통과(스코프 행이 없으므로 제한 없음) — 게이트가 상시 막힘이 아님을 확인
    assert auto_operator._scope_hold_reason(db, p) is None


def test_generation_side_hold_reason_names_the_campaign_level_case(db):
    """예산(캠페인 레벨)과 그룹 밖은 사유 문구가 달라야 한다 — 사후 채굴이 둘을 안 섞는다."""
    from app.services.naver_ad import auto_operator

    _settings(db, auto_operate=True, optimizer="ours")
    _scope(db, IN_GROUP)

    p_group = _approved_proposal(db, adgroup_id=OUT_GROUP)
    p_campaign = _approved_proposal(db, adgroup_id=None, proposal_type="budget_up")

    assert "광고그룹" in auto_operator._scope_hold_reason(db, p_group)
    assert "캠페인 레벨" in auto_operator._scope_hold_reason(db, p_campaign)


# ──────────────────────────────────────────────────────────────────────────
# ★회귀 가드 — 이 기능은 «캠페인 축»을 건드리지 않는다
# ──────────────────────────────────────────────────────────────────────────

def test_scope_predicate_ignores_campaign_axis(db):
    """★초판이 여기서 깨졌다 — 전건 회귀에서만 잡힌 것을 국소 가드로 내린다.

    초판은 실행 게이트에 진리표 «전체»(마스터 ∧ 그룹)를 넣었다. 그러자 위임(delegation)·
    expert_desk 경로 4건이 깨졌다 — 그 경로들은 `auto_operate`와 **무관하게** 도는 별도 승인
    경로라, 마스터를 겹쳐 보는 순간 `auto_operate=False`인 캠페인의 기존 위임 실행이 소급해서
    막힌다. 계약의 「행이 0개면 항상 기존 동작 그대로」는 캠페인 축에도 적용된다."""
    _settings(db, auto_operate=False, optimizer="ours")  # 캠페인 축 OFF
    # 스코프 행이 없으므로 스코프는 아무것도 막지 않는다(캠페인 축은 호출부 몫)
    assert adgroup_scope.blocked_by_scope(db, CAMPAIGN, IN_GROUP) is False
    assert adgroup_scope.blocked_by_scope(db, CAMPAIGN, None) is False
    # 반면 진리표 완전판(화면·진단용)은 마스터를 보므로 False다 — 둘은 다른 질문이다
    assert adgroup_scope.in_scope_now(db, CAMPAIGN, IN_GROUP) is False


def test_harness_does_not_block_when_no_scope_rows_even_if_campaign_off(db):
    """실행 게이트 수준의 같은 보증 — 스코프 미설정이면 ScopeGuardError가 나지 않는다.

    (킬스위치 가드는 이 게이트와 별개로 자기 승인원 목록에서 이미 auto_operate를 본다 —
    스코프가 그 판단을 덮어쓰면 안 된다.)"""
    _settings(db, auto_operate=False, optimizer="ours")
    p = _approved_proposal(db, adgroup_id=IN_GROUP)
    try:
        naver_execution_harness.execute(db, p.id, dry_run=True, now=NOW)
    except naver_execution_harness.ScopeGuardError:  # pragma: no cover
        pytest.fail("스코프 행이 0개인데 ScopeGuardError가 났다 — 행위 변화 0 위반")
    except Exception:
        pass  # 다른 게이트(킬스위치 등)에서 걸리는 건 이 테스트의 관심사가 아니다


# ──────────────────────────────────────────────────────────────────────────
# 역할 라벨
# ──────────────────────────────────────────────────────────────────────────

def test_role_of_returns_label(db):
    _settings(db, auto_operate=True)
    _scope(db, IN_GROUP, role=adgroup_scope.ROLE_BRAKE)
    assert adgroup_scope.role_of(db, CAMPAIGN, IN_GROUP) == "brake"
    assert adgroup_scope.role_of(db, CAMPAIGN, OUT_GROUP) is None


# ──────────────────────────────────────────────────────────────────────────
# ★D-NAO-244 부수 결함 — 「죽은 카드」와 그것을 「실행 가능」이라 말하던 화면
#
# 2026-08-29 prod 실측: status='approved'인데 스코프 밖이라 harness가 영원히 거부하는
# 제안이 **119건**(auto_op_hr 92 · explore_op 19 · auto_op 4 · cold_op 4, 07-28~).
# 손해는 0(실쓰기 없음)이지만 콘솔은 그 119건을 전부 「실행 가능」으로 표시하고 있었다
# (real_write_blocker가 전건 None). 원인은 하나 — 스코프 검사가 «레인마다 각자» 있었고
# 실제로는 일 레인 2곳에만 있었다.
#
# 그래서 두 가지를 잠근다:
#   ⓐ 승인하는 «문»이 하나다(engine_approve) — 새 레인이 생겨도 그 문을 지나야 approved가 된다
#   ⓑ 사람이 읽는 층(real_write_blocker → 콘솔 executable)이 값이 도는 층과 같은 답을 한다
# ──────────────────────────────────────────────────────────────────────────

def _pending_proposal(db, *, adgroup_id, proposal_type="bid_down"):
    p = NaverProposal(
        proposal_type=proposal_type, target_type="adgroup", target_id=adgroup_id or "",
        campaign_id=CAMPAIGN, adgroup_id=adgroup_id, status="pending",
        rationale="테스트", target_bid=100,
    )
    db.add(p)
    db.flush()          # ★일부러 커밋하지 않는다 — 레인들이 이 상태로 문을 두드린다
    return p


def test_engine_approve_refuses_out_of_scope_and_leaves_no_dead_card(db):
    """★스코프 밖이면 approved로 만들지 않는다 — 죽은 카드가 생기는 자리를 막는다."""
    from app.services.naver_ad import auto_operator

    _settings(db, auto_operate=True, optimizer="ours")
    _scope(db, IN_GROUP)
    p = _pending_proposal(db, adgroup_id=OUT_GROUP)

    assert auto_operator.engine_approve(db, p, source="auto_op_hr", now=NOW) is False
    db.refresh(p)
    assert p.status == "pending"          # approved가 아니다 = 죽은 카드 아님
    assert p.approval_source is None
    # 재료는 잃지 않는다 — blocked 일기 1행(C7 학습 사슬)
    entries = db.query(OpsDiaryEntry).filter(OpsDiaryEntry.event_type == "blocked").all()
    assert len(entries) == 1
    assert "스코프" in (entries[0].rationale or "")


def test_engine_approve_lets_in_scope_through(db):
    """★반대 방향 — 스코프 «안»이면 종전과 완전히 같이 승인된다(이 문은 좁히기만 한다)."""
    from app.services.naver_ad import auto_operator

    _settings(db, auto_operate=True, optimizer="ours")
    _scope(db, IN_GROUP)
    p = _pending_proposal(db, adgroup_id=IN_GROUP)

    assert auto_operator.engine_approve(db, p, source="auto_op_hr", now=NOW) is True
    db.refresh(p)
    assert p.status == "approved"
    assert p.approval_source == "auto_op_hr"


def test_engine_approve_unchanged_when_no_scope_rows(db):
    """스코프 행이 0개면 전부 통과 — 「행이 없으면 기존 동작 그대로」(소급 0)."""
    from app.services.naver_ad import auto_operator

    _settings(db, auto_operate=True, optimizer="ours")
    p = _pending_proposal(db, adgroup_id="아무-그룹이나")

    assert auto_operator.engine_approve(db, p, source="explore_op", now=NOW) is True
    db.refresh(p)
    assert p.status == "approved"


def test_engine_approve_survives_uncommitted_insert(db):
    """★회귀 — 스코프 리졸버는 «독립 커넥션»으로 읽는다.

    세션이 아직 커밋 안 한 INSERT를 들고 있는 채로 그 커넥션을 열고 닫으면 SQLite에서
    열린 트랜잭션이 되감겨 방금 flush한 제안 행이 사라진다(StaleDataError: "expected to
    update 1 row(s); 0 were matched"). engine_approve가 스코프 조회 «전»에 커밋하는 것이
    그 방어다 — 순서를 되돌리면 이 테스트가 죽는다."""
    from app.services.naver_ad import auto_operator

    _settings(db, auto_operate=True, optimizer="ours")
    _scope(db, IN_GROUP)
    p = _pending_proposal(db, adgroup_id=IN_GROUP)   # flush만, 커밋 안 함

    assert auto_operator.engine_approve(db, p, source="auto_op_hr", now=NOW) is True
    # 행이 실제로 살아 있어야 한다(독립 커넥션으로 재확인 — 세션 캐시가 아니라 DB를 본다)
    with db.get_bind().connect() as conn:
        from sqlalchemy import text
        row = conn.execute(
            text("SELECT status, approval_source FROM naver_proposals WHERE id = :i"), {"i": p.id}
        ).first()
    assert row is not None and row[0] == "approved"


def test_real_write_blocker_tells_the_truth_about_scope(db):
    """★ⓑ 사람이 읽는 층 — 엔진 승인분이 스코프 밖이면 콘솔이 「실행 가능」이라 하지 않는다."""
    _settings(db, auto_operate=True, optimizer="ours")
    _scope(db, IN_GROUP)
    p = _approved_proposal(db, adgroup_id=OUT_GROUP)

    reason = naver_execution_harness.real_write_blocker(p, db)
    assert reason is not None and "스코프" in reason
    # db 없이 부르면 종전 그대로(구조 판정만) — 기존 호출부 행위 불변
    assert naver_execution_harness.real_write_blocker(p) is None


def test_real_write_blocker_does_not_bind_the_human_hand(db):
    """★사람 손(approval_source=NULL)에는 스코프 사유를 붙이지 않는다.

    execute()가 그 경로를 스코프에서 면제하므로(«스코프는 엔진의 자율 범위를 좁히는 장치이지
    사람의 손을 묶는 게이트가 아니다»), 붙이면 콘솔이 «실제로 실행되는 것»을 못 한다고
    거짓말한다 — 방향만 다른 같은 병이다."""
    _settings(db, auto_operate=True, optimizer="ours")
    _scope(db, IN_GROUP)
    p = _pending_proposal(db, adgroup_id=OUT_GROUP)
    db.commit()

    assert p.approval_source is None
    assert naver_execution_harness.real_write_blocker(p, db) is None


def test_console_approved_is_scope_blocked_and_the_message_says_so_honestly(db):
    """★적대 리뷰 P1-1 — 「사람 승인은 스코프 면제」는 **코드에 없다**. 그 사실을 못 박는다.

    라우터는 사람이 콘솔에서 승인할 때 `approval_source`에 **NULL이 아니라 'console'**을
    쓴다(`routers/naver_ad.py` status 전이). 그런데 `execute()`의 스코프 가드는
    `approval_source is not None`을 쓰므로 **콘솔 승인분도 막힌다** — 그 가드 주석이 적어 둔
    「NULL은 이 블록 밖」 면제는 실제로 발동한 적이 없다.

    이 테스트가 지키는 것 둘:
      ①`real_write_blocker`가 `execute()`와 **같은 답**을 한다(화면이 진실을 말한다).
         `is_auto_exec`(NULL ∪ 'console' = 사람) 쪽으로 바꾸면 화면은 「실행 가능」인데
         execute()는 거부하는 원래의 병이 되살아난다.
      ②사유 문구가 **승인 주체를 단정하지 않는다** — 사람이 방금 누른 카드에 대고
         「엔진이 승인했어도」라고 쓰면 원인을 엉뚱한 데로 돌린다.
    """
    _settings(db, auto_operate=True, optimizer="ours")
    _scope(db, IN_GROUP)
    p = _approved_proposal(db, adgroup_id=OUT_GROUP)
    p.approval_source = "console"          # ★사람이 콘솔에서 승인한 실제 값
    db.commit()

    # ① 화면과 실행이 같은 답
    reason = naver_execution_harness.real_write_blocker(p, db)
    assert reason is not None and "스코프" in reason
    with pytest.raises(naver_execution_harness.ScopeGuardError):
        naver_execution_harness.execute(db, p.id, dry_run=False, now=NOW)

    # ② 문구가 승인 주체를 단정하지 않는다
    assert "엔진" not in reason

    # 참고 — 이 저장소엔 승인원 술어가 둘이고 서로 다르다(그래서 헷갈린다)
    assert naver_execution_harness.is_auto_exec(p) is False   # 이쪽 기준으론 「사람」
    assert p.approval_source is not None                       # 스코프 가드 기준으론 「엔진」


# ──────────────────────────────────────────────────────────────────────────
# ★★문이 «유일한» 문인지를 구조로 잠근다 (적대 리뷰 P2-3)
#
# 리뷰어가 탐색·스파이럴·예산페이싱 세 레인에서 engine_approve 호출을 지우고 예전처럼
# status="approved"를 직접 커밋하는 변이를 넣었더니 **199개 테스트가 전부 통과**했다.
# 레인마다 회귀 테스트를 하나씩 다는 것으로는 «다음에 생길 레인»을 못 막는다 —
# 그리고 그 「레인마다 각자」가 애초에 죽은 카드 119건을 만든 병이다.
# ⇒ 「approved로 만드는 곳은 engine_approve 하나뿐」을 **구조(AST)로** 단언한다.
# ──────────────────────────────────────────────────────────────────────────

def test_approved_is_only_ever_set_inside_the_single_door():
    """★엔진 모듈에서 `status='approved'`를 쓰는 자리는 engine_approve 하나뿐이어야 한다.

    새 레인이 생겨 문을 우회하면 이 테스트가 즉시 죽는다 — 레인별 테스트를 매번 기억해
    다는 것에 기대지 않는다(그 기대가 실패한 기록이 죽은 카드 119건이다)."""
    import ast
    import pathlib

    targets = [
        "app/services/naver_ad/auto_operator.py",
        "app/services/naver_ad/cold_start_bid_lane.py",
        "app/services/naver_ad/exploration.py",
    ]
    root = pathlib.Path(__file__).resolve().parents[1]
    offenders: list[str] = []

    for rel in targets:
        path = root / rel
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if fn.name == "engine_approve":
                continue  # ★유일하게 허용된 자리
            for node in ast.walk(fn):
                # ① proposal.status = "approved"
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                        and node.value.value == "approved":
                    for tgt in node.targets:
                        if isinstance(tgt, ast.Attribute) and tgt.attr == "status":
                            offenders.append(f"{rel}:{node.lineno} in {fn.name}() — 대입")
                # ② NaverProposal(..., status="approved", ...)
                if isinstance(node, ast.Call):
                    for kw in node.keywords:
                        if kw.arg == "status" and isinstance(kw.value, ast.Constant) \
                                and kw.value.value == "approved":
                            offenders.append(f"{rel}:{node.lineno} in {fn.name}() — 생성 인자")

    assert offenders == [], (
        "엔진 승인 단일문(engine_approve)을 우회해 approved를 만드는 자리가 있다 — "
        "스코프 검사(D-NAO-244)를 건너뛰어 «죽은 카드»가 다시 쌓인다:\n  "
        + "\n  ".join(offenders)
    )
