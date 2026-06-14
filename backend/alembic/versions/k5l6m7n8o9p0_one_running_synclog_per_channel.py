"""one running synclog per channel (partial unique index)

Revision ID: k5l6m7n8o9p0
Revises: j4k5l6m7n8o9
Create Date: 2026-06-14 13:00:00.000000

sync_service 동시 실행 방지 가드의 check-then-create 레이스를 DB 레벨로 막는다
(Codex 교차검증 2026-06-14 P1, task_dd560245).

문제: 기존 가드는 (1) 'running' SyncLog를 SELECT로 확인 → (2) 없으면 별도 트랜잭션에서
'running' SyncLog를 INSERT 한다. 이 SELECT↔INSERT가 비원자적이라 같은 channel_id로 동시
요청 2건이 모두 "running 없음"을 읽고 둘 다 동기화를 시작할 수 있다(FastAPI sync 라우트는
threadpool에서 실행 → 동시 스레드 가능). j4k5l6m7n8o9의 stale 회수 commit이 추가되며 윈도가
약간 더 보였다.

해결: channel별 'running' 1건만 허용하는 partial unique index. status!='running' 행
(success/error/partial)은 인덱스 대상이 아니라 채널당 무제한 누적 가능. SQLite·PostgreSQL
둘 다 partial index를 지원한다.

정리 UPDATE 선행(필수): 인덱스 생성 시점에 같은 channel에 'running'이 2건 이상 있으면
인덱스 생성이 IntegrityError로 실패한다. 따라서 channel별 running 행을 가장 최근(=MAX(id))
1건만 남기고 나머지를 'error'로 회수한 뒤 인덱스를 만든다. (선행 마이그레이션
j4k5l6m7n8o9가 잔존 running을 전부 회수하므로 정상 배포 순서에선 보통 0건이지만, 그 사이
새 행이 생겼거나 j4가 변경될 경우를 대비한 방어적 정리.)

멱등: 정리 UPDATE는 'running'이 아닌 행을 건드리지 않고, 남길 1건도 그대로 둔다. 재실행 안전.

동시성 전제(Codex P2-2): 이 마이그레이션은 cleanup↔CREATE INDEX 사이에 동기화 writer가
없다고 가정하는 maintenance-mode 마이그레이션이다. 현재 prod는 SQLite 단일 writer·단일
프로세스이고 배포 시 앱이 재시작돼 in-flight 동기화가 죽으므로(선행 j4k5l6m7n8o9와 동일 전제)
안전하다. **향후 PostgreSQL로 이전 시** writer가 cleanup과 CREATE INDEX 사이에 끼어들면
인덱스 생성이 실패할 수 있으니, 그때는 배포 전 스케줄러 중단(maintenance-mode) 또는
PG advisory lock으로 보호한다(현 시점엔 PG가 없어 미구현 — 추측성 코드 회피).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'k5l6m7n8o9p0'
down_revision: Union[str, None] = 'j4k5l6m7n8o9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) channel별 중복 'running' 정리 — 최신(MAX(id)) 1건만 남기고 나머지를 'error'로 회수.
    #    이 단계 없이 인덱스를 만들면 기존 중복 running 행 때문에 생성이 실패한다.
    op.execute(
        "UPDATE sync_log "
        "SET status='error', "
        "    error_message=COALESCE(error_message, '') "
        "      || ' [dup running reclaimed for unique index]', "
        "    completed_at=COALESCE(completed_at, started_at) "
        "WHERE status='running' "
        "  AND id NOT IN ("
        "    SELECT MAX(id) FROM sync_log WHERE status='running' GROUP BY channel_id"
        "  )"
    )
    # 2) channel별 'running' 1건만 허용하는 partial unique index 생성.
    #    dialect별 where 절을 모두 전달 — 현재 배포는 SQLite, 향후 PostgreSQL 이전 대비.
    op.create_index(
        "uq_sync_log_one_running_per_channel",
        "sync_log",
        ["channel_id"],
        unique=True,
        sqlite_where=sa.text("status = 'running'"),
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("uq_sync_log_one_running_per_channel", table_name="sync_log")
