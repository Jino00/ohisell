# test_sync_one_running_per_channel_lock.py — channel별 'running' SyncLog 1건 보장 (task_dd560245)
# 동시 동기화 방지의 check-then-create 레이스를 DB 레벨로 막는다(Codex 교차검증 2026-06-14 P1).
# 기존 가드는 running SyncLog를 SELECT로 확인 → 없으면 별도로 'running' INSERT 하는데, 이 둘이
# 비원자적이라 같은 channel_id로 동시 요청 2건이 모두 "running 없음"을 읽고 둘 다 시작할 수 있다.
# partial unique index `WHERE status='running'`가 channel별 running 행을 1개로 강제하고,
# INSERT 충돌(레이스 패자)은 IntegrityError로 "이미 동기화가 진행 중입니다" 반환으로 처리한다.
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Channel, SyncLog
from app.services import sync_service
from app.services.sync_service import sync_channel_orders
from app.utils.kst import kst_now

_BLOCKED_MSG = "이미 동기화가 진행 중입니다"


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _ch(db, cid=1, platform="coupang"):
    # api_config_key=None → 정상 흐름에선 client 없음. 레이스 테스트는 stub client를 주입한다.
    db.add(Channel(id=cid, name=f"c{cid}", code=f"COUPANG_W{cid}",
                   platform=platform, api_type="hmac", api_config_key=None))
    db.commit()


def _running(cid):
    return SyncLog(channel_id=cid, sync_type="orders", status="running", started_at=kst_now())


class _StubClient:
    """주문 0건 반환하는 최소 클라이언트."""
    last_fetch_complete = True

    def fetch_orders(self, date_from, date_to):
        return []


# ── (a) DB 레벨 불변식: channel별 running 1건 ──────────────────────────────

def test_partial_unique_index_blocks_two_running_same_channel(db):
    """같은 channel_id로 'running' 2건 INSERT → partial unique index가 막는다(IntegrityError)."""
    _ch(db, 1)
    db.add(_running(1))
    db.commit()
    db.add(_running(1))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_running_allowed_for_different_channels(db):
    """다른 channel_id면 각자 'running' 1건씩 허용(인덱스가 channel별로만 막음)."""
    _ch(db, 1)
    _ch(db, 2)
    db.add(_running(1))
    db.add(_running(2))
    db.commit()  # 충돌 없음
    assert db.query(SyncLog).filter(SyncLog.status == "running").count() == 2


def test_non_running_duplicates_allowed(db):
    """partial index는 status='running'만 대상 → 같은 채널의 success/error 여러 건은 허용."""
    _ch(db, 1)
    for st in ("success", "success", "error"):
        log = _running(1)
        log.status = st
        db.add(log)
    db.commit()  # running이 아니므로 충돌 없음
    assert db.query(SyncLog).filter(SyncLog.channel_id == 1).count() == 3


# ── (b) 코드 레벨: INSERT 충돌 → 레이스 패자 처리 ────────────────────────────

def test_insert_conflict_returns_blocked(db, monkeypatch):
    """가드 SELECT 통과 후(running 없음) 경쟁자가 running을 선점하면, 우리 INSERT가
    IntegrityError → rollback → '이미 동기화가 진행 중입니다' 반환(레이스 패자)."""
    _ch(db, 1)

    def racer_then_client(ch, db_):
        # 가드 SELECT는 이미 통과(running 0건)한 시점. 경쟁 요청이 running 락을 선점한 상황을 모사.
        db_.add(_running(ch.id))
        db_.commit()
        return _StubClient()

    monkeypatch.setattr(sync_service, "_get_client_for_channel", racer_then_client)
    result = sync_channel_orders(db, 1)
    assert result["status"] == "error"
    assert _BLOCKED_MSG in result["errors"]
    # 경쟁자가 선점한 running 1건만 남고, 우리 INSERT는 롤백되어 중복 running이 생기지 않는다.
    assert db.query(SyncLog).filter(SyncLog.status == "running").count() == 1
