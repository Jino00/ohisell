"""add scheduler_state watchdog columns (스케줄러 워치독 — S1)

Revision ID: s3t4u5v6w7x8
Revises: r2s3t4u5v6w7
Create Date: 2026-06-20 16:10:00.000000

잡 실패 무알림 사각지대 제거(트랙 revenue-wing-truth D-10 returns/settlement 16일 침묵 사고 발단).
scheduler_state grain 불변. 컬럼 3개 추가(D-F):
  last_status     : 마지막 실행 결과 enum 문자열(ok|error|missed). NULL=미관측.
  last_error      : 마지막 에러 traceback(≤2000자, DB에만 저장 — /health는 sanitized 요약만 D-E).
  last_status_at  : 마지막 상태 기록 시각(성공/실패 무관). last_run_at='마지막 성공' 의미는 유지.
SQLite ADD COLUMN 네이티브 지원(batch_alter 불필요). 전부 nullable → 기존 row 무영향.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 's3t4u5v6w7x8'
down_revision: Union[str, None] = 'r2s3t4u5v6w7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('scheduler_state', sa.Column('last_status', sa.String(length=16), nullable=True))
    op.add_column('scheduler_state', sa.Column('last_error', sa.Text(), nullable=True))
    op.add_column('scheduler_state', sa.Column('last_status_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('scheduler_state', 'last_status_at')
    op.drop_column('scheduler_state', 'last_error')
    op.drop_column('scheduler_state', 'last_status')
