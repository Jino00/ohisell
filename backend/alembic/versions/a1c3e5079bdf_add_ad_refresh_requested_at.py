"""add refresh_requested_at to coupang_wing_cookie (광고비 버튼 트리거 갱신)

Revision ID: a1c3e5079bdf
Revises: e1f3a5c7b9d2
Create Date: 2026-06-06

대시보드 "광고비 갱신" 버튼이 set, Mac 페처 데몬이 claim(=None)으로 소비하는
갱신 요청 플래그. nullable → 기존 행/동작 불변. 신규 컬럼만 추가.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1c3e5079bdf'
down_revision: Union[str, None] = 'e1f3a5c7b9d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'coupang_wing_cookie',
        sa.Column('refresh_requested_at', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table('coupang_wing_cookie') as batch:
        batch.drop_column('refresh_requested_at')
