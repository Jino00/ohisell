"""add_naver_settlement_case

Revision ID: d4f6a8c0e2b3
Revises: c3d5e7f9a1b2
Create Date: 2026-06-04 22:30:00.000000

네이버 트랙 N1·D-6 이익 정밀화 — 건별 정산 내역(/v1/pay-settle/settle/case).
productOrderId 그레인 실측 수수료(음수 보존). orders와 (order_id, product_id) 매칭.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4f6a8c0e2b3'
down_revision: Union[str, None] = 'c3d5e7f9a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'naver_settlement_case',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('product_order_id', sa.String(length=40), nullable=False),
        sa.Column('order_id', sa.String(length=40), nullable=False),
        sa.Column('product_id', sa.String(length=40), nullable=True),
        sa.Column('product_order_type', sa.String(length=20), nullable=False),
        sa.Column('settle_type', sa.String(length=30), nullable=True),
        sa.Column('product_name', sa.String(length=255), nullable=True),
        sa.Column('pay_settle_amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('total_pay_commission', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('selling_interlock_commission', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('free_installment_commission', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('benefit_amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('settle_expect_amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('pay_date', sa.String(length=10), nullable=True),
        sa.Column('settle_expect_date', sa.String(length=10), nullable=True),
        sa.Column('settle_complete_date', sa.String(length=10), nullable=True),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('product_order_id', name='uq_naver_settle_case'),
    )
    op.create_index('ix_naver_settlement_case_order_id', 'naver_settlement_case', ['order_id'])
    op.create_index('ix_naver_settlement_case_product_id', 'naver_settlement_case', ['product_id'])
    op.create_index('ix_naver_settlement_case_pay_date', 'naver_settlement_case', ['pay_date'])


def downgrade() -> None:
    op.drop_index('ix_naver_settlement_case_pay_date', table_name='naver_settlement_case')
    op.drop_index('ix_naver_settlement_case_product_id', table_name='naver_settlement_case')
    op.drop_index('ix_naver_settlement_case_order_id', table_name='naver_settlement_case')
    op.drop_table('naver_settlement_case')
