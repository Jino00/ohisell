"""reclaim_stale_running_sync_logs

Revision ID: j4k5l6m7n8o9
Revises: i3j4k5l6m7n8
Create Date: 2026-06-14 12:00:00.000000

sync_service 동시 실행 방지 가드 stale-timeout 패치(task_f1f36f02)의 짝 마이그레이션.

문제: 동기화 크래시/타임아웃/강제종료로 except가 못 돌면 SyncLog가 'running'으로 영구히 남아
채널의 모든 향후 동기화를 영구 차단한다(라이브 발견 2026-06-14, 쿠팡 오하이테크 channel_id=2).

이 마이그레이션이 필요한 이유(Codex P1#2 수용): 기존 'running' 행의 started_at은 구 코드가
server_default(func.now())로 **UTC** 기록했는데, 신규 stale 비교는 kst_now()(KST) 기준이라
9시간 어긋난다. 배포 직후 구 UTC 행을 그대로 두면 새 코드가 '약 9시간 전'으로 오인해 진짜
진행 중일 수 있는 동기화를 stale로 잘못 회수할 수 있다. 배포 시점에 앱이 재시작되며 진행 중
동기화는 어차피 죽으므로, 잔존 'running' 행을 일괄 'error'로 회수해 UTC↔KST 모호성을 제거한다.
배포 후 생성되는 행은 전부 kst_now()라 비교 단위가 일치한다.

멱등: 'running'이 아닌 행은 건드리지 않음. 재실행해도 안전.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'j4k5l6m7n8o9'
down_revision: Union[str, None] = 'i3j4k5l6m7n8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 잔존 'running'(=죽은 락)을 'error'로 회수. completed_at은 started_at으로 근사(없으면 NULL 유지).
    op.execute(
        "UPDATE sync_log "
        "SET status='error', "
        "    error_message=COALESCE(error_message, '') || ' [stale running cleared on deploy]', "
        "    completed_at=COALESCE(completed_at, started_at) "
        "WHERE status='running'"
    )


def downgrade() -> None:
    # 어떤 행이 원래 running이었는지 복원 불가하고, running으로 되돌리면 영구 차단 버그가 재현되므로 no-op.
    pass
