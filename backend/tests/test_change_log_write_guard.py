# test_change_log_write_guard.py — B-1 change_log 쓰기 가드 (D-NAO-169, 교훈 #196·#209)
#
# 2026-08-10 실사고 재현: 임시 스크립트로 정지 2건을 기록하면서 ①없는 action 이름
# (`manual_loss_stop`)을 지어내고 ②`changed_at`에 네이버 editTm의 **UTC** 시각을 그대로
# 넣었다(9시간 어긋남). 둘 다 내 합격기준 안에서는 초록이었고 발견은 우연이었다.
#
# ★`prove-the-guard-catches-this-input` 패턴 준수 — 「막을 것 같은」 입력이 아니라
#   **실제 사고 입력**(change_log 5854·5855의 값)으로 돌린다.
from __future__ import annotations

import logging
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import KNOWN_CHANGE_LOG_ACTIONS, NaverChangeLog


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _row(**kw):
    base = dict(entity_type="campaign", entity_id="cmp-1", campaign_id="cmp-1",
                action="set_user_lock", dry_run=False)
    base.update(kw)
    return NaverChangeLog(**base)


# ── 시각 규약: 하드 거부 ─────────────────────────────────────────────────────


def test_utc_changed_at_with_kst_executed_at_is_rejected(db):
    """★사고 입력 재현 — change_log 5854의 실제 값(UTC changed_at + KST executed_at)."""
    db.add(_row(
        changed_at=datetime(2026, 8, 10, 0, 57, 33),        # 네이버 editTm(UTC) 그대로 = 사고
        executed_at=datetime(2026, 8, 10, 9, 57, 33, 805030),  # 실제 집행 시각(KST)
        after_value='{"userLock": true}',
    ))
    with pytest.raises(ValueError) as exc:
        db.commit()
    msg = str(exc.value)
    assert "KST" in msg
    assert "editTm" in msg          # 원인을 지목해야 다음 사람이 안 밟는다
    assert "kst_now" in msg         # 대안을 안내해야 한다


def test_both_kst_passes(db):
    """정상 경로 — 두 칸 모두 KST면 통과(초 단위 차이는 정상)."""
    db.add(_row(
        changed_at=datetime(2026, 8, 10, 9, 57, 33),
        executed_at=datetime(2026, 8, 10, 9, 57, 33, 805030),
        after_value='{"userLock": true}',
    ))
    db.commit()
    assert db.query(NaverChangeLog).count() == 1


def test_executed_at_null_is_not_checked(db):
    """`executed_at`이 없으면 대조할 짝이 없다 — 검사하지 않는다(대다수 행이 이 모양)."""
    db.add(_row(changed_at=datetime(2026, 8, 10, 0, 57, 33), executed_at=None))
    db.commit()
    assert db.query(NaverChangeLog).count() == 1


def test_skew_just_under_threshold_passes(db):
    """29분 차이는 통과 — 임계(30분) 경계를 고정한다."""
    db.add(_row(changed_at=datetime(2026, 8, 10, 9, 0, 0),
                executed_at=datetime(2026, 8, 10, 9, 29, 0)))
    db.commit()
    assert db.query(NaverChangeLog).count() == 1


def test_skew_just_over_threshold_rejected(db):
    """31분 차이는 거부 — 반대쪽 경계."""
    db.add(_row(changed_at=datetime(2026, 8, 10, 9, 0, 0),
                executed_at=datetime(2026, 8, 10, 9, 31, 0)))
    with pytest.raises(ValueError):
        db.commit()


# ── action 이름: 경고이지 거부가 아니다 ──────────────────────────────────────


def test_invented_action_warns_but_does_not_block(db, caplog):
    """★사고 입력 재현 — 내가 지어낸 `manual_loss_stop`.

    경고는 나오되 **쓰기는 통과한다.** 하드 거부하지 않는 이유는 change_log에 실제로
    도달하는 action 전수 인벤토리를 확신할 수 없기 때문이다(확신 없는 거부 = 돈 경로 파손)."""
    with caplog.at_level(logging.WARNING):
        db.add(_row(action="manual_loss_stop", after_value='"userLock=True (paused)"'))
        db.commit()
    assert db.query(NaverChangeLog).count() == 1          # 막지 않는다
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "manual_loss_stop" in joined                    # 어떤 이름이 문제인지 지목
    assert "KNOWN_CHANGE_LOG_ACTIONS" in joined            # 고치는 법을 알려야 한다


def test_known_action_does_not_warn(db, caplog):
    with caplog.at_level(logging.WARNING):
        db.add(_row(action="set_user_lock"))
        db.commit()
    assert not [r for r in caplog.records if "미등록 action" in r.getMessage()]


def test_registry_covers_the_actions_actually_used(db):
    """라이브 prod에서 관측된 action이 레지스트리에 있는지 — 빠지면 매 쓰기가 경고를 뱉는다.

    (2026-08-10 prod 실측 전수. 새 action을 만들면 여기와 레지스트리 둘 다 갱신한다.)"""
    observed_in_prod = {
        "update_bid", "external_bid_change", "external_keyword_added",
        "external_status_change", "set_user_lock", "update_budget",
        "manual_emergency_stop", "create_adgroup", "create_ad",
        "budget_up_pacing", "budget_down_pacing", "optimizer_change",
        "external_keyword_removed", "exclude_search_term", "create_campaign",
        "auto_operate_change", "external_qi_change", "flight_pacing",
        "flight_pacing_silent", "set_loss_policy",
    }
    assert observed_in_prod <= KNOWN_CHANGE_LOG_ACTIONS
