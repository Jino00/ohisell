# test_migration_anomaly_flag_width.py — `anomw1s2a` 마이그레이션 자동 검증
#
# 왜 이 파일이 있나 (적대 리뷰 1R P2-2): 이 마이그레이션은 스키마만 바꾸는 게 아니라
# **데이터를 되살린다**. 그런데 그 복원 SQL은 자동 커버리지가 0이었다 — 리뷰어가
# ①복원 SQL 통째 삭제 ②접두사 가드 삭제 두 변이를 넣었는데 관련 스위트가 **전건 초록**이었고,
# ②는 합성 DB에서 **무관한 행 2개를 엉뚱한 값으로 덮었다**.
#
# 즉 접두사 가드는 load-bearing인데 아무도 안 쟀다. 이 변경에서 **되돌릴 수 없는 절반**이
# 정확히 거기라서, 시나리오를 여기 고정한다.
#
# 방법은 `test_migration_one_running_index.py`의 선례를 따른다: 이 repo는 초기 create
# 마이그레이션이 없어(초기 리비전이 `oauth_tokens` 부재로 base부터 리플레이 불가) create_all로
# 스키마를 세우고 직전 리비전으로 stamp한 뒤 대상 리비전 하나만 실제로 돌린다.
from __future__ import annotations

import os
import tempfile

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.database import Base

_PREV_REV = "grainv1s1a"
_TARGET_REV = "anomw1s2a"

#: prod r45·r97이 든 실제 문자열(2026-09-04 실측). 100자.
_FULL = (
    "price_conflict:부착 안내문:55.0≠30.0,"
    "price_conflict:비닐 (16*23+4):15.0≠10.0,"
    "price_conflict:패키지:320.0≠171.0"
)
_TRUNCATED = _FULL[:40]


def _cfg(db_path: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.fixture
def at_prev_revision():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    # ★모델은 이미 String(200)이므로 «마이그레이션 이전»을 진짜로 재현하려면 선언 타입을
    #   40으로 되돌려야 한다. 안 그러면 이 테스트가 「폭이 넓어졌다」를 원리적으로 못 잰다.
    with engine.begin() as c:
        c.execute(text("PRAGMA writable_schema=ON"))
        c.execute(
            text(
                "UPDATE sqlite_master SET sql = replace(sql, "
                "'anomaly_flag VARCHAR(200)', 'anomaly_flag VARCHAR(40)') "
                "WHERE type='table' AND name='cost_recipe'"
            )
        )
        c.execute(text("PRAGMA writable_schema=OFF"))
    engine.dispose()
    command.stamp(_cfg(path), _PREV_REV)
    engine = create_engine(f"sqlite:///{path}")
    yield engine, path
    engine.dispose()
    os.remove(path)


def _seed(engine):
    """복원돼야 할 행 하나와, **손대면 안 되는** 행 넷을 함께 심는다."""

    with engine.begin() as c:
        def item(iid, anomalies):
            c.execute(
                text(
                    "INSERT INTO cost_table_item (id, section, item_name, recipe_kind, "
                    "row_number, anomalies) VALUES (:i,'s',:n,'assembly',:i,:a)"
                ),
                {"i": iid, "n": f"item{iid}", "a": anomalies},
            )

        def recipe(rid, flag, pid):
            c.execute(
                text(
                    "INSERT INTO cost_recipe (id, product_name, form_factor, status, source, "
                    "recipe_kind, variant, anomaly_flag, picked_item_id) "
                    "VALUES (:i,:n,'flip','draft','excel','assembly','',:f,:p)"
                ),
                {"i": rid, "n": f"r{rid}", "f": flag, "p": pid},
            )

        item(1, _FULL)
        item(2, "완전히 다른 문구라 저장값의 접두사가 아니다 — 손대면 안 된다")
        item(3, None)

        recipe(10, _TRUNCATED, 1)      # ✅복원 대상 — 저장값이 원천의 접두사다
        recipe(11, _TRUNCATED, None)   # ⛔픽 없음(prod r81 유형) — 원본이 DB에 없다
        recipe(12, _TRUNCATED, 2)      # ⛔접두사가 아니다 — 「잘렸다」가 증명 안 된다
        recipe(13, "", 1)              # ⛔빈 문자열 — substr(x,1,0)='' 는 항상 참이다(P2-3)
        recipe(14, None, 1)            # ⛔깃발 자체가 없다


def _flags(engine):
    with engine.connect() as c:
        return dict(
            c.execute(text("SELECT id, anomaly_flag FROM cost_recipe ORDER BY id")).all()
        )


def test_upgrade_restores_only_the_rows_it_can_prove_were_truncated(at_prev_revision):
    """★복원은 «접두사»라는 증명이 선 행에만 일어난다.

    ★변이 시험 ①복원 SQL을 지우면 r10이 40자로 남아 첫 단언이 죽는다.
             ②접두사 가드(`substr(...) = anomaly_flag`)를 지우면 r12·r13이 덮여
               「손대면 안 되는 행」 단언들이 죽는다.
    """

    engine, path = at_prev_revision
    _seed(engine)

    command.upgrade(_cfg(path), _TARGET_REV)

    flags = _flags(engine)
    assert flags[10] == _FULL, "접두사가 증명된 행은 원천 전문으로 복원돼야 한다"
    assert flags[11] == _TRUNCATED, "픽된 항목이 없으면 지어내지 않는다(prod r81)"
    assert flags[12] == _TRUNCATED, "접두사가 아니면 손대지 않는다"
    assert flags[13] == "", "빈 문자열은 «아무것도 증명하지 않는다» — 덮으면 안 된다"
    assert flags[14] is None


def test_upgrade_widens_the_declared_column(at_prev_revision):
    """★선언 폭이 실제로 넓어진다 — SQLite가 길이를 안 재므로 값만 봐선 못 가른다."""

    engine, path = at_prev_revision
    _seed(engine)
    with engine.connect() as c:
        before = [
            r[2] for r in c.execute(text("PRAGMA table_info(cost_recipe)")) if r[1] == "anomaly_flag"
        ]
    assert before == ["VARCHAR(40)"]

    command.upgrade(_cfg(path), _TARGET_REV)

    with engine.connect() as c:
        after = [
            r[2] for r in c.execute(text("PRAGMA table_info(cost_recipe)")) if r[1] == "anomaly_flag"
        ]
    assert after == ["VARCHAR(200)"]


def test_batch_recreate_keeps_constraints_indexes_and_foreign_keys(at_prev_revision):
    """★`batch_alter_table`은 테이블을 **재생성**한다 — 잃은 것이 없어야 한다.

    SQLite batch 모드의 알려진 함정이다. 자식 3테이블이 `cost_recipe`를 FK로 가리키고
    유니크 제약 `uq_cost_recipe_name_form_variant`가 걸려 있다.
    """

    engine, path = at_prev_revision
    _seed(engine)
    command.upgrade(_cfg(path), _TARGET_REV)

    with engine.connect() as c:
        ddl = c.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='cost_recipe'")
        ).scalar()
        assert "uq_cost_recipe_name_form_variant" in ddl
        assert "FOREIGN KEY(picked_item_id)" in ddl.replace("FOREIGN KEY (", "FOREIGN KEY(")

        idx = {
            r[0]
            for r in c.execute(
                text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='cost_recipe'")
            )
        }
        assert "ix_cost_recipe_product_name" in idx
        assert "ix_cost_recipe_picked_item_id" in idx

        assert c.execute(text("PRAGMA foreign_key_check")).fetchall() == []
        assert c.execute(text("PRAGMA integrity_check")).scalar() == "ok"


def test_downgrade_narrows_the_column_without_cutting_restored_data(at_prev_revision):
    """★되돌리기는 폭만 되돌리고 **되살린 문자열을 다시 자르지 않는다.**

    자르는 downgrade는 「되돌리기」가 아니라 손실이다. 재실행해도 멱등이어야 한다.
    """

    engine, path = at_prev_revision
    _seed(engine)
    command.upgrade(_cfg(path), _TARGET_REV)
    command.downgrade(_cfg(path), _PREV_REV)

    assert _flags(engine)[10] == _FULL
    with engine.connect() as c:
        typ = [
            r[2] for r in c.execute(text("PRAGMA table_info(cost_recipe)")) if r[1] == "anomaly_flag"
        ]
    assert typ == ["VARCHAR(40)"]

    command.upgrade(_cfg(path), _TARGET_REV)
    assert _flags(engine)[10] == _FULL
