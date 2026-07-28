# test_naver_ad_status_reason_valve.py — D-NAO-97 상태 사유(statusReason) 밸브
# 커버: ①캠페인 status를 userLock으로만 판정(예산 소진 오탐 제거) ②시스템 사유 정지의
#   별도 액션 기록(system_status_change) ③레거시(user_lock 키 없음) 입력 하위호환
#   ④2중 안전장치(_log_external_status_change의 시스템 사유 억제).
#
# 근거 데이터는 전부 라이브 실측이다(원칙22, 2026-07-27 22:46 · 2026-07-28 07:10 KST):
#   캠페인 cmp-a001-02-000000008492582(03 아이폰_강화유리)가 일예산 50,000원을 소진해
#   status=PAUSED · statusReason=CAMPAIGN_LIMITED_BY_BUDGET · **userLock=false**가 됐는데,
#   구버전은 이를 `{"userLock": false}→{"userLock": true}` 외부 조작으로 기록했다(id 847).
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverChangeLog, NaverEntity
from app.services.naver_ad import entity_sync


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


CID = "cmp-a001-02-000000008492582"


def _campaign(status, reason, user_lock, *, name="● [P_삭제금지]03. 아이폰_강화유리"):
    """get_campaigns_full()이 내놓는 모양 그대로."""
    return {
        "campaign_id": CID, "name": name, "campaign_type": "SHOPPING",
        "daily_budget": 50000, "status": status,
        "user_lock": user_lock, "status_reason": reason,
    }


def _rows(status, reason, user_lock):
    return entity_sync.collect_entities(
        campaigns=[_campaign(status, reason, user_lock)], adgroups_by_campaign={}, keywords_by_adgroup={},
    )


def _logs(db, action):
    return db.query(NaverChangeLog).filter(NaverChangeLog.action == action).all()


# ── ① 근본 수정: 캠페인 status = userLock (status 역추론 금지) ──
def test_budget_exhausted_pause_is_not_off():
    """라이브 재현: 예산 소진 자동정지는 userLock=false → 우리 status는 'on'을 유지한다."""
    rows = _rows("PAUSED", "CAMPAIGN_LIMITED_BY_BUDGET", False)
    assert rows[0]["status"] == "on"
    assert rows[0]["status_reason"] == "CAMPAIGN_LIMITED_BY_BUDGET"


def test_human_pause_is_off():
    """사람이 콘솔에서 끈 캠페인(userLock=true · CAMPAIGN_PAUSED)은 그대로 off."""
    assert _rows("PAUSED", "CAMPAIGN_PAUSED", True)[0]["status"] == "off"


def test_eligible_campaign_is_on():
    assert _rows("ELIGIBLE", "ELIGIBLE", False)[0]["status"] == "on"


def test_deleted_campaign_is_deleted():
    assert _rows("DELETED", "CAMPAIGN_PAUSED", False)[0]["status"] == "deleted"


def test_missing_status_field_does_not_become_deleted():
    """status 필드 부재를 삭제로 읽지 않는다(구버전 동작 보존 — 로스터에서 통째로 사라짐 방지)."""
    assert _rows("", "", False)[0]["status"] == "on"


def test_legacy_rows_without_user_lock_fall_back_to_old_rule():
    """user_lock 키가 아예 없는 입력(구버전 fetcher)은 옛 PAUSED 규칙으로 되돌아간다.
    없는 정보를 False로 지어내면 사람이 끈 캠페인이 on으로 뒤집혀 유령 '외부 재개'가 생긴다."""
    legacy = {"campaign_id": CID, "name": "x", "campaign_type": "SHOPPING", "status": "PAUSED"}
    rows = entity_sync.collect_entities(campaigns=[legacy], adgroups_by_campaign={}, keywords_by_adgroup={})
    assert rows[0]["status"] == "off"


# ── ② 오탐 재현: 07-27 22:46 이벤트가 재분류된다 ──
def test_budget_exhaustion_no_longer_logs_external_status_change(db):
    """id 847 재현 시나리오: 노출 중 → 예산 소진. 사람 조작 로그가 **한 건도** 남지 않아야 한다."""
    entity_sync.sync_entities(db, rows=_rows("ELIGIBLE", "ELIGIBLE", False))
    entity_sync.sync_entities(db, rows=_rows("PAUSED", "CAMPAIGN_LIMITED_BY_BUDGET", False))

    assert _logs(db, "external_status_change") == []
    e = db.query(NaverEntity).filter(NaverEntity.entity_id == CID).one()
    assert e.status == "on"  # 사람이 끈 것이 아니므로 우리 장부에서 off가 아니다
    assert e.status_reason == "CAMPAIGN_LIMITED_BY_BUDGET"


def test_budget_exhaustion_logs_system_status_change_instead(db):
    """대신 별도 액션으로 관찰 기록된다 — 사건 자체는 사라지지 않는다."""
    entity_sync.sync_entities(db, rows=_rows("ELIGIBLE", "ELIGIBLE", False))
    entity_sync.sync_entities(db, rows=_rows("PAUSED", "CAMPAIGN_LIMITED_BY_BUDGET", False))

    logs = _logs(db, "system_status_change")
    assert len(logs) == 1
    assert logs[0].dry_run is True  # 관찰(네이버 쓰기 아님) — 기본 화면 집계에 안 섞인다
    assert logs[0].proposal_id is None
    assert json.loads(logs[0].after_value) == {"statusReason": "CAMPAIGN_LIMITED_BY_BUDGET"}
    assert "일예산 소진" in logs[0].rationale


def test_budget_reset_next_morning_logs_release_not_resume(db):
    """다음날 예산 리셋(해제)도 사람의 '재개'가 아니다 — 유령 쌍이 생기지 않는지 확인."""
    entity_sync.sync_entities(db, rows=_rows("ELIGIBLE", "ELIGIBLE", False))
    entity_sync.sync_entities(db, rows=_rows("PAUSED", "CAMPAIGN_LIMITED_BY_BUDGET", False))
    entity_sync.sync_entities(db, rows=_rows("ELIGIBLE", "ELIGIBLE", False))

    assert _logs(db, "external_status_change") == []
    logs = _logs(db, "system_status_change")
    assert len(logs) == 2
    assert "해제" in logs[1].rationale


def test_human_pause_still_logs_external_status_change(db):
    """진짜 사람 조작은 그대로 잡힌다 — 오탐을 없애다 진짜 신호까지 죽이면 안 된다."""
    entity_sync.sync_entities(db, rows=_rows("ELIGIBLE", "ELIGIBLE", False))
    entity_sync.sync_entities(db, rows=_rows("PAUSED", "CAMPAIGN_PAUSED", True))

    logs = _logs(db, "external_status_change")
    assert len(logs) == 1
    assert json.loads(logs[0].after_value) == {"userLock": True}
    assert _logs(db, "system_status_change") == []  # 사람 조작은 시스템 사유가 아니다


def test_system_pause_then_human_pause_logs_both(db):
    """예산 소진 상태에서 사람이 추가로 OFF를 누르면 두 사건이 각자 남는다."""
    entity_sync.sync_entities(db, rows=_rows("ELIGIBLE", "ELIGIBLE", False))
    entity_sync.sync_entities(db, rows=_rows("PAUSED", "CAMPAIGN_LIMITED_BY_BUDGET", False))
    entity_sync.sync_entities(db, rows=_rows("PAUSED", "CAMPAIGN_PAUSED", True))

    assert len(_logs(db, "external_status_change")) == 1
    assert len(_logs(db, "system_status_change")) == 2  # 시작 → (사람 OFF로) 해제


def test_no_log_when_reason_unchanged(db):
    """무변동은 기록하지 않는다(91,005행 크론의 쓰기 폭증 방어 — 다른 밸브와 동일 규약).

    신규 행(insert)도 기록하지 않는다 — 다른 밸브와 같은 계약(변화 없는 첫 관측은 '변화'가
    아니다). 단, 이미 존재하던 행의 status_reason이 NULL(마이그레이션 직후)이면 update 경로라
    첫 sync에서 '시작' 1건이 남는다 — 그건 실제로 시스템 정지 중인 상태의 최초 관측이다."""
    rows = _rows("PAUSED", "CAMPAIGN_LIMITED_BY_BUDGET", False)
    entity_sync.sync_entities(db, rows=rows)
    entity_sync.sync_entities(db, rows=rows)
    entity_sync.sync_entities(db, rows=rows)

    assert _logs(db, "system_status_change") == []


# ── ③ 사유 분류: 모르는 값은 '사람 탓'으로 돌리지 않는다 ──
@pytest.mark.parametrize("etype,reason,expected", [
    ("campaign", "CAMPAIGN_LIMITED_BY_BUDGET", True),
    ("campaign", "CAMPAIGN_PENDING", True),
    ("campaign", "CAMPAIGN_ENDED", True),
    ("campaign", "CAMPAIGN_PAUSED", False),      # 캠페인 자신의 OFF 스위치
    ("campaign", "ELIGIBLE", False),
    ("campaign", "", False),
    ("campaign", None, False),
    ("adgroup", "CAMPAIGN_PAUSED", True),        # ★그룹에서는 부모 정지 '상속'이다(실측 331건)
    ("adgroup", "GROUP_PAUSED", False),          # 그룹 자신의 OFF 스위치
    ("adgroup", "GROUP_LIMITED_BY_BUDGET", True),
    ("adgroup", "BUSINESS_CHANNEL_ABNORMAL_INTERLOCK", True),  # 공식 문서에 없는 실측 값
    ("keyword", "KEYWORD_PAUSED", False),
    ("keyword", "KEYWORD_UNDER_REVIEW", True),
    ("campaign", "SOMETHING_NAVER_ADDS_LATER", True),  # 미지의 사유 → 사람 조작 아님으로 실패
])
def test_is_system_reason_classification(etype, reason, expected):
    assert entity_sync._is_system_reason(etype, reason) is expected


# ── ④ 2중 안전장치: userLock을 못 받는 경로에서도 유령을 만들지 않는다 ──
def test_status_change_suppressed_when_reason_is_system(db):
    """collect_entities를 우회해 status='off'가 직접 주입돼도(구버전 fetcher 경로),
    사유가 시스템이면 '사람이 잠갔다'로 기록하지 않는다."""
    base = {"entity_type": "campaign", "entity_id": CID, "parent_id": "", "campaign_id": CID,
            "campaign_type": "SHOPPING", "name": "03", "bid_amt": None}
    entity_sync.sync_entities(db, rows=[{**base, "status": "on", "status_reason": "ELIGIBLE"}])
    entity_sync.sync_entities(
        db, rows=[{**base, "status": "off", "status_reason": "CAMPAIGN_LIMITED_BY_BUDGET"}]
    )

    assert _logs(db, "external_status_change") == []


def test_resume_direction_is_never_suppressed_by_reason(db):
    """재개 방향(off→on)에는 사유 가드를 걸지 않는다 — 잠금 해제는 실제 상태 변화다."""
    base = {"entity_type": "adgroup", "entity_id": "grp-1", "parent_id": CID, "campaign_id": CID,
            "campaign_type": "SHOPPING", "name": "그룹", "bid_amt": 500}
    entity_sync.sync_entities(db, rows=[{**base, "status": "off", "status_reason": "GROUP_PAUSED"}])
    entity_sync.sync_entities(
        db, rows=[{**base, "status": "on", "status_reason": "CAMPAIGN_LIMITED_BY_BUDGET"}]
    )

    logs = _logs(db, "external_status_change")
    assert len(logs) == 1
    assert json.loads(logs[0].after_value) == {"userLock": False}


# ── ⑤ 그룹·키워드는 사유를 저장만 한다(쓰기 폭증 방어) ──
def test_adgroup_system_pause_stores_reason_without_event(db):
    """부모 캠페인 정지 하나로 그룹 수백 개가 동시에 바뀐다 — 이벤트는 캠페인 행만 남긴다."""
    base = {"entity_type": "adgroup", "entity_id": "grp-1", "parent_id": CID, "campaign_id": CID,
            "campaign_type": "SHOPPING", "name": "그룹", "bid_amt": 500, "status": "on"}
    entity_sync.sync_entities(db, rows=[{**base, "status_reason": "ELIGIBLE"}])
    entity_sync.sync_entities(db, rows=[{**base, "status_reason": "CAMPAIGN_PAUSED"}])

    assert _logs(db, "system_status_change") == []
    e = db.query(NaverEntity).filter(NaverEntity.entity_id == "grp-1").one()
    assert e.status_reason == "CAMPAIGN_PAUSED"  # 사유는 현재값으로 조회 가능


def test_legacy_rows_without_reason_key_keep_last_known_reason(db):
    """status_reason 키가 없는 레거시 주입은 마지막 관측 사유를 지우지 않는다(qi와 같은 규약)."""
    entity_sync.sync_entities(db, rows=_rows("PAUSED", "CAMPAIGN_LIMITED_BY_BUDGET", False))
    legacy = {"entity_type": "campaign", "entity_id": CID, "parent_id": "", "campaign_id": CID,
              "campaign_type": "SHOPPING", "name": "03", "status": "on", "bid_amt": None}
    entity_sync.sync_entities(db, rows=[legacy])

    e = db.query(NaverEntity).filter(NaverEntity.entity_id == CID).one()
    assert e.status_reason == "CAMPAIGN_LIMITED_BY_BUDGET"
