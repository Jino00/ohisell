# test_migration_one_running_index.py — k5l6m7n8o9p0 마이그레이션 자동 검증 (task_dd560245)
# Codex 교차검증 P2-1: 머니/데이터 신선도 경로이므로 alembic cleanup/index/downgrade 경로를
# 영구 회귀 테스트로 고정한다(과거엔 prod DB 사본에 일회용 스크립트로만 검증).
#
# 이 repo는 base 테이블을 create_all로 만들고 alembic은 증분 ALTER만 보유한다(초기 create
# 마이그레이션 없음). 따라서 'j4 스키마'를 재현하기 위해 create_all로 전체 스키마를 만든 뒤
# 신규 partial index만 떼어내고 alembic_version을 j4k5l6m7n8o9로 stamp한 다음, head로
# 업그레이드해 신규 마이그레이션 k5l6m7n8o9p0 하나만 실제로 돌린다.
from __future__ import annotations

import os
import tempfile

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Channel, SyncLog
from app.utils.kst import kst_now

_INDEX = "uq_sync_log_one_running_per_channel"
_PREV_REV = "j4k5l6m7n8o9"


def _alembic_cfg(db_path: str) -> Config:
    # alembic.ini는 backend/ 기준 상대경로 → 테스트는 backend/에서 실행됨(pytest rootdir).
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.fixture
def at_prev_revision():
    """신규 인덱스가 없는 j4k5l6m7n8o9 스키마 상태의 임시 파일 DB를 만든다.
    (alembic은 별도 커넥션으로 붙으므로 :memory:가 아닌 파일 DB가 필요하다.)"""
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    # 모델에 추가된 신규 partial index를 떼어내 j4(인덱스 이전) 스키마로 되돌린다.
    with engine.begin() as c:
        c.execute(text(f"DROP INDEX IF EXISTS {_INDEX}"))
    engine.dispose()
    # alembic_version을 j4로 고정 → head 업그레이드 시 k5l6m7n8o9p0 하나만 실행.
    command.stamp(_alembic_cfg(path), _PREV_REV)
    engine = create_engine(f"sqlite:///{path}")
    yield engine, path
    engine.dispose()
    os.remove(path)


def _seed_channel(engine, cid=1):
    s = sessionmaker(bind=engine)()
    s.add(Channel(id=cid, name=f"c{cid}", code=f"COUPANG_W{cid}",
                  platform="coupang", api_type="hmac", api_config_key=None))
    s.commit()
    s.close()


def _index_sql(engine):
    with engine.connect() as c:
        row = c.execute(text(
            f"SELECT sql FROM sqlite_master WHERE type='index' AND name='{_INDEX}'"
        )).fetchone()
    return row[0] if row else None


def test_upgrade_creates_partial_unique_index(at_prev_revision):
    """j4 → head 업그레이드가 partial unique index를 생성한다(running만 대상, UNIQUE)."""
    engine, path = at_prev_revision
    assert _index_sql(engine) is None  # 업그레이드 전엔 없음
    command.upgrade(_alembic_cfg(path), "head")
    sql = _index_sql(engine)
    assert sql is not None, "인덱스가 생성되지 않음"
    assert "status = 'running'" in sql, f"partial WHERE 절 누락: {sql}"
    assert "UNIQUE" in sql.upper(), f"UNIQUE 아님: {sql}"


def test_upgrade_reclaims_duplicate_running_keeping_max_id(at_prev_revision):
    """업그레이드 cleanup이 channel별 running을 MAX(id) 1건만 남기고 나머지를 error로 회수한다."""
    engine, path = at_prev_revision
    _seed_channel(engine, 1)
    with engine.begin() as c:
        # 같은 채널 running 3건(인덱스 이전이라 삽입 가능) + success 1건.
        for i in range(3):
            c.execute(text(
                "INSERT INTO sync_log (channel_id, sync_type, status, records_synced, started_at) "
                f"VALUES (1, 'orders', 'running', 0, '2026-06-14 00:0{i}:00')"
            ))
        c.execute(text(
            "INSERT INTO sync_log (channel_id, sync_type, status, records_synced, started_at) "
            "VALUES (1, 'orders', 'success', 5, '2026-06-13 00:00:00')"
        ))
        max_running_id = c.execute(text(
            "SELECT MAX(id) FROM sync_log WHERE status='running'"
        )).scalar()

    command.upgrade(_alembic_cfg(path), "head")

    with engine.connect() as c:
        running = c.execute(text(
            "SELECT id FROM sync_log WHERE channel_id=1 AND status='running'"
        )).fetchall()
        reclaimed = c.execute(text(
            "SELECT COUNT(*) FROM sync_log WHERE channel_id=1 AND status='error' "
            "AND error_message LIKE '%dup running reclaimed%'"
        )).scalar()
        success = c.execute(text(
            "SELECT COUNT(*) FROM sync_log WHERE channel_id=1 AND status='success'"
        )).scalar()
    assert len(running) == 1, f"running 1건만 남아야 함: {running}"
    assert running[0][0] == max_running_id, "가장 최근(MAX id) running이 유지돼야 함"
    assert reclaimed == 2, f"나머지 2건이 회수돼야 함: {reclaimed}"
    assert success == 1, "success 행은 영향받지 않아야 함"


def test_index_enforces_after_upgrade(at_prev_revision):
    """업그레이드 후 같은 채널 running 2건 INSERT는 인덱스가 막는다(IntegrityError)."""
    engine, path = at_prev_revision
    _seed_channel(engine, 1)
    command.upgrade(_alembic_cfg(path), "head")
    s = sessionmaker(bind=engine)()
    s.add(SyncLog(channel_id=1, sync_type="orders", status="running", started_at=kst_now()))
    s.commit()
    s.add(SyncLog(channel_id=1, sync_type="orders", status="running", started_at=kst_now()))
    with pytest.raises(IntegrityError):
        s.commit()
    s.rollback()
    s.close()


def test_downgrade_drops_index_and_upgrade_recreates(at_prev_revision):
    """downgrade는 인덱스를 제거하고, 재업그레이드는 다시 생성한다(라운드트립 무오류)."""
    engine, path = at_prev_revision
    cfg = _alembic_cfg(path)
    command.upgrade(cfg, "head")
    assert _index_sql(engine) is not None
    command.downgrade(cfg, "-1")
    assert _index_sql(engine) is None, "downgrade 후 인덱스가 남아있음"
    command.upgrade(cfg, "head")
    assert _index_sql(engine) is not None, "재업그레이드 후 인덱스 없음"
