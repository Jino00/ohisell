# test_sync_stale_running_lock.py — stale 'running' SyncLog 자동 회수 (task_f1f36f02)
# 동기화가 크래시/타임아웃/강제종료되면 except가 못 돌아 SyncLog가 'running'으로 영구히 남아
# 채널의 모든 향후 동기화를 영구 차단한다. started_at이 STALE_RUNNING_TIMEOUT 이상 경과한
# 'running'은 죽은 락으로 보고 회수한 뒤 진행해야 한다. 쿠팡/네이버/cafe24 공통 경로.
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Channel, SyncLog
from app.services import sync_service
from app.services.sync_service import STALE_RUNNING_TIMEOUT, sync_channel_orders
from app.utils.kst import kst_now

# 가드를 통과해도 client가 없어 "API 키 없음"으로 끝나는 채널 — 가드 통과/차단을 구분하기 위함.
_BLOCKED_MSG = "이미 동기화가 진행 중입니다"
_NO_CLIENT_MSG = "API 키가 설정되지 않았습니다. .env를 확인하세요."


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _ch(db, cid=1, platform="coupang"):
    # api_type="hmac" + api_config_key=None → _get_client_for_channel가 None 반환(가드는 통과).
    db.add(Channel(id=cid, name=f"c{cid}", code=f"COUPANG_W{cid}",
                   platform=platform, api_type="hmac", api_config_key=None))


def _running_log(db, cid=1, age=timedelta(minutes=1)):
    log = SyncLog(channel_id=cid, sync_type="orders", status="running",
                  started_at=kst_now() - age)
    db.add(log)
    db.commit()
    return log.id


def test_fresh_running_lock_blocks(db):
    """정상 동기화 진행 중(started_at 최근) → 차단 유지(동시 실행 방지)."""
    _ch(db)
    log_id = _running_log(db, age=timedelta(minutes=2))
    db.commit()
    result = sync_channel_orders(db, 1)
    assert _BLOCKED_MSG in result["errors"]
    # 멀쩡한 running 락은 건드리지 않는다.
    assert db.get(SyncLog, log_id).status == "running"


def test_stale_running_lock_reclaimed(db):
    """타임아웃 경과한 죽은 'running' 락 → 회수(error)하고 새 동기화 진행."""
    _ch(db)
    log_id = _running_log(db, age=STALE_RUNNING_TIMEOUT + timedelta(minutes=1))
    db.commit()
    result = sync_channel_orders(db, 1)
    # 가드를 통과(차단 메시지 아님) — client 없어 다른 사유로 끝남.
    assert _BLOCKED_MSG not in result["errors"]
    assert _NO_CLIENT_MSG in result["errors"]
    # stale 락은 회수되어 더 이상 'running'이 아니다(영구 차단 해소).
    assert db.get(SyncLog, log_id).status != "running"


# NOTE(2026-06-14, task_dd560245): channel별 'running' 1건을 강제하는 partial unique index
# (uq_sync_log_one_running_per_channel)가 추가되면서, 같은 채널에 'running' 2건이 공존하는
# 시나리오(과거 "stale 여러 개"·"stale+fresh 공존")는 DB 레벨에서 물리적으로 불가능해졌다.
# 따라서 아래 두 테스트는 회수 루프의 다건 처리 대신, unique index와의 상호작용을 검증하도록
# 갱신했다. 단건 stale 회수는 test_stale_running_lock_reclaimed가 그대로 커버한다.
# (인덱스 자체는 test_sync_one_running_per_channel_lock.py에서 검증.)


def test_stale_reclaim_unblocks_new_sync_under_unique_index(db, monkeypatch):
    """죽은 'running' 락 회수 후 새 동기화가 unique index 아래서도 정상 INSERT된다.
    회수(status='error')가 새 INSERT보다 먼저 commit되므로 partial unique index와 충돌하지 않는다
    — reclaim-before-insert 순서가 깨지면 IntegrityError로 동기화가 막힌다."""
    _ch(db)
    stale = _running_log(db, age=STALE_RUNNING_TIMEOUT + timedelta(minutes=5))
    db.commit()
    monkeypatch.setattr(sync_service, "_get_client_for_channel", lambda ch, db: _StubClient())
    result = sync_channel_orders(db, 1)
    assert result["status"] == "success"          # unique index 위반 없이 완주
    assert _BLOCKED_MSG not in result["errors"]
    assert db.get(SyncLog, stale).status != "running"   # 죽은 락 회수됨
    # 새 동기화는 running→success로 끝나 채널에 'running'이 남지 않는다(중복 running 0건).
    assert db.query(SyncLog).filter(SyncLog.status == "running").count() == 0


def test_fresh_lock_blocks_without_inserting_new_log(db):
    """진짜 진행 중(fresh) 락이 있으면 INSERT 시도 전에 차단 — 새 SyncLog가 생기지 않는다.
    차단이 INSERT보다 먼저 일어나 unique index를 건드리지 않고 고아 running 행도 안 남긴다."""
    _ch(db)
    fresh = _running_log(db, age=timedelta(minutes=1))
    db.commit()
    result = sync_channel_orders(db, 1)
    assert _BLOCKED_MSG in result["errors"]
    assert db.get(SyncLog, fresh).status == "running"   # 정상 락 불변
    # 차단은 INSERT 전이므로 새 행이 추가되지 않는다(running 여전히 1건).
    assert db.query(SyncLog).count() == 1


class _StubClient:
    """주문 0건 반환하는 최소 클라이언트 — SyncLog가 실제로 생성/완료되게 한다."""
    last_fetch_complete = True

    def fetch_orders(self, date_from, date_to):
        return []


def test_started_at_recorded_in_kst(db, monkeypatch):
    """새 SyncLog의 started_at은 KST로 기록 — stale 비교(kst_now)와 단위 일치 필수.
    started_at이 UTC(server_default)면 모든 running 락이 ~9h 전으로 보여 가드가
    정상 동기화도 stale로 오인 회수 → 동시 실행 방지가 완전히 무력화된다."""
    _ch(db)
    monkeypatch.setattr(sync_service, "_get_client_for_channel", lambda ch, db: _StubClient())
    result = sync_channel_orders(db, 1)
    assert result["status"] == "success"
    log = db.query(SyncLog).order_by(SyncLog.id.desc()).first()
    assert log is not None and log.started_at is not None
    drift = abs((kst_now() - log.started_at).total_seconds())
    assert drift < 60, f"started_at이 KST가 아님(drift={drift}s, UTC면 ~32400s)"
