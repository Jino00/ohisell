"""add all_day_cost to coupang_ad_cost_daily (S5a 전체 광고비)

Revision ID: l6m7n8o9p0q1
Revises: k5l6m7n8o9p0
Create Date: 2026-06-14 11:30:00.000000

매출·광고 정합성 트랙 S5a — 비-PA 광고비 전체 전환(D-15).
report/SALES 응답의 ALL_DELIVERED_AD_COST(전체 광고비, 비-PA 포함)를 적재할 컬럼 신설.
기존 day_cost(=DELIVERED_AD_COST, 집행/PA)는 유지. 전체≥집행, 차 = 비-PA.

기존 행 백필: all_day_cost = day_cost (재fetch 전까지 비-PA=0으로 안전 — 전체=집행 가정).
다음 페처 run(ALL_DELIVERED push)부터 실제 전체값으로 교정.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'l6m7n8o9p0q1'
down_revision: Union[str, None] = 'k5l6m7n8o9p0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('coupang_ad_cost_daily', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('all_day_cost', sa.Integer(), nullable=False, server_default='0')
        )
    # 기존 행 백필: 비-PA 데이터 없던 과거는 전체=집행으로 둠(비-PA=0, 안전).
    op.execute('UPDATE coupang_ad_cost_daily SET all_day_cost = day_cost')


def downgrade() -> None:
    with op.batch_alter_table('coupang_ad_cost_daily', schema=None) as batch_op:
        batch_op.drop_column('all_day_cost')
