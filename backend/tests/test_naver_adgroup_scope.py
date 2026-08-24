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
