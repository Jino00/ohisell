"""add claimed_at·attempt_count to coupang_wing_cookie (refresh claim lease 재시도 계약)

Revision ID: c4e6a8b0d2f4
Revises: d22ce37d3adb
Create Date: 2026-07-27

버튼 1회 = 재시도 3회까지 포함(PLAN_coupang-claim-retry-lease §0). claim이 요청을 즉시
소비하던 구조를 lease(임대)로 바꾸기 위한 컬럼 2개. 둘 다 신규 추가 — 기존 행/동작 불변.
  claimed_at    : 임대 취득 시각(NULL=임대 없음). TTL 초과 = 데몬이 보고 없이 죽음 → 재claim.
  attempt_count : 이 요청으로 claim된 횟수. 3 도달 후 실패면 요청 소멸(무한 재시도 방지).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4e6a8b0d2f4'
down_revision: Union[str, None] = 'd22ce37d3adb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'coupang_wing_cookie',
        sa.Column('claimed_at', sa.DateTime(), nullable=True),
    )
    # server_default="0" — 기존 행에도 0이 채워져야 NOT NULL이 성립한다(SQLite/PG 공통).
    op.add_column(
        'coupang_wing_cookie',
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    with op.batch_alter_table('coupang_wing_cookie') as batch:
        batch.drop_column('attempt_count')
        batch.drop_column('claimed_at')
