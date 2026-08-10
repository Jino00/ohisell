# test_costmap_status_migration.py — 「제외」 뜻 통일 마이그레이션(e4c7a1b8d206) 회귀 테스트
#
# 왜 있나(2026-08-10 적대 리뷰 P2-2): 이 마이그레이션은 합격기준 ①(«status가 confirmed/excluded
#   둘로만»)을 지키는 **유일한 코드**인데 커버가 0이었다 — UPDATE를 no-op으로 바꿔도 전 스위트가
#   통과했다(변이 M1 생존). 마이그레이션은 배포 때 한 번 돌고 끝이라 «안 돌았다»가 조용하다.
#
# 방식은 `test_migration_one_running_index.py`의 선례를 따른다: create_all로 스키마를 만들고
#   alembic_version을 **직전 리비전으로 stamp**한 뒤 이 리비전 하나만 실제로 돌린다.
#   (직접 `op` 프록시를 갈아끼우는 방식도 시도했으나 alembic 내부에 의존해 깨졌다 — 선례가 옳다.)
from __future__ import annotations

import os
import tempfile

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.database import Base
# ★모델을 import해야 그 테이블이 Base.metadata에 등록된다 — 안 하면 create_all이
#   조용히 그 테이블만 안 만들고, 테스트는 «없는 테이블»로 죽는다.
from app.models import RocketProductCostMap  # noqa: F401

_PREV_REV = "b7d1e4f92a06"
_TARGET_REV = "e4c7a1b8d206"


def _cfg(db_path: str) -> Config:
    # alembic.ini는 backend/ 기준 상대경로 → pytest rootdir이 backend/라 그대로 쓴다.
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.fixture
def db_at_prev():
    """옛 값(`ignored`)이 섞인 직전 리비전 상태의 파일 DB."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        for pn, st in (("A", "ignored"), ("B", "ignored"), ("C", "confirmed")):
            c.execute(
                text("INSERT INTO rocket_product_cost_map (product_number, status) "
                     "VALUES (:p, :s)"),
                {"p": pn, "s": st},
            )
    engine.dispose()
    command.stamp(_cfg(path), _PREV_REV)
    yield path
    os.remove(path)


def _counts(path: str) -> dict:
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as c:
            return dict(c.execute(text(
                "SELECT status, COUNT(*) FROM rocket_product_cost_map GROUP BY 1")).all())
    finally:
        engine.dispose()


def test_upgrade_moves_ignored_to_excluded(db_at_prev):
    """★핵심: 옛 값이 **실제로** 옮겨진다. UPDATE가 no-op이면 여기서 죽는다."""
    assert _counts(db_at_prev) == {"ignored": 2, "confirmed": 1}
    command.upgrade(_cfg(db_at_prev), _TARGET_REV)
    assert _counts(db_at_prev) == {"excluded": 2, "confirmed": 1}, \
        "ignored가 남으면 신코드가 그 행을 «매핑 없음»으로 읽어 이유를 잃는다"


def test_confirmed_rows_are_untouched(db_at_prev):
    """★confirmed를 건드리면 원가 연결을 지우는 사고다 — 대상이 아닌 행은 그대로 둔다."""
    command.upgrade(_cfg(db_at_prev), _TARGET_REV)
    engine = create_engine(f"sqlite:///{db_at_prev}")
    try:
        with engine.begin() as c:
            assert c.execute(text(
                "SELECT status FROM rocket_product_cost_map WHERE product_number='C'"
            )).scalar() == "confirmed"
    finally:
        engine.dispose()


def test_downgrade_restores_and_upgrade_is_idempotent(db_at_prev):
    """되돌아오고, 다시 올려도 같다 — 재실행이 안전해야 배포 중 중단에서 복구할 수 있다."""
    cfg = _cfg(db_at_prev)
    command.upgrade(cfg, _TARGET_REV)
    command.downgrade(cfg, _PREV_REV)
    assert _counts(db_at_prev) == {"ignored": 2, "confirmed": 1}
    command.upgrade(cfg, _TARGET_REV)
    assert _counts(db_at_prev) == {"excluded": 2, "confirmed": 1}
