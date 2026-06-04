"""add_naver_settlement_daily

Revision ID: c3d5e7f9a1b2
Revises: b1c2d3e4f5a6
Create Date: 2026-06-04 21:00:00.000000

네이버 트랙 N1 정산 — 일별 정산 내역(/v1/pay-settle/settle/daily).
실측 정산금액·수수료(음수)·혜택·지급보류. settle_expect_date 그레인.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d5e7f9a1b2'
down_revision: Union[str, None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'naver_settlement_daily',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('settle_basis_start', sa.String(length=10), nullable=True),
        sa.Column('settle_basis_end', sa.String(length=10), nullable=True),
        sa.Column('settle_expect_date', sa.String(length=10), nullable=False),
        sa.Column('settle_complete_date', sa.String(length=10), nullable=True),
        sa.Column('settle_amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('pay_settle_amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('commission_amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('benefit_amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('payholdback_amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('settle_method', sa.String(length=20), nullable=True),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('settle_expect_date', name='uq_naver_settle_daily'),
    )
    op.create_index('ix_naver_settlement_daily_settle_expect_date', 'naver_settlement_daily', ['settle_expect_date'])


def downgrade() -> None:
    op.drop_index('ix_naver_settlement_daily_settle_expect_date', table_name='naver_settlement_daily')
    op.drop_table('naver_settlement_daily')
