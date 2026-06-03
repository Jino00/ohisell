"""add_coupang_coupon_tables

Revision ID: f2a4c6e8b0d1
Revises: d5b7e9c1a3f2
Create Date: 2026-06-03 19:00:00.000000

P5 쿠폰/캐시백 도메인 — 쿠폰 운영 현황(보조축, 회계는 정산 P4가 진실).
명세: docs/references/06_coupang_coupon_api_specs.md §E (응답 스키마 전수 수집).
- coupang_coupon: 즉시할인+다운로드쿠폰 통합(couponId 그레인)
- coupang_coupon_item: 옵션(vendorItemId)별 쿠폰 적용(D-8 결합축)
- coupang_coupon_budget: 예산현황+계약 메타(contract_id+target_month 그레인)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2a4c6e8b0d1'
down_revision: Union[str, None] = 'd5b7e9c1a3f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ① 쿠폰 운영 현황 (즉시할인+다운로드 통합, couponId 그레인)
    op.create_table(
        'coupang_coupon',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_key', sa.String(length=20), nullable=False),
        sa.Column('vendor_id', sa.String(length=20), nullable=False),
        sa.Column('coupon_kind', sa.String(length=12), nullable=False),
        sa.Column('coupon_id', sa.String(length=30), nullable=False),
        sa.Column('contract_id', sa.String(length=20), nullable=True),
        sa.Column('promotion_name', sa.String(length=300), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('discount_type', sa.String(length=30), nullable=True),
        sa.Column('discount', sa.Numeric(12, 2), nullable=True),
        sa.Column('max_discount_price', sa.Numeric(12, 2), nullable=True),
        sa.Column('start_at', sa.DateTime(), nullable=True),
        sa.Column('end_at', sa.DateTime(), nullable=True),
        sa.Column('wow_exclusive', sa.Boolean(), nullable=True),
        sa.Column('applied_option_count', sa.Integer(), nullable=True),
        sa.Column('usage_amount', sa.Numeric(14, 2), nullable=True),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'account_key', 'coupon_kind', 'coupon_id', name='uq_coupang_coupon'
        ),
    )
    op.create_index('ix_coupang_coupon_vendor_id', 'coupang_coupon', ['vendor_id'])
    op.create_index('ix_coupang_coupon_coupon_id', 'coupang_coupon', ['coupon_id'])
    op.create_index('ix_coupang_coupon_status', 'coupang_coupon', ['status'])

    # ② 쿠폰 아이템 (옵션별 적용, D-8 결합축)
    op.create_table(
        'coupang_coupon_item',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_key', sa.String(length=20), nullable=False),
        sa.Column('vendor_id', sa.String(length=20), nullable=False),
        sa.Column('coupon_item_id', sa.String(length=30), nullable=False),
        sa.Column('coupon_id', sa.String(length=30), nullable=False),
        sa.Column('vendor_item_id', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('start_at', sa.DateTime(), nullable=True),
        sa.Column('end_at', sa.DateTime(), nullable=True),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'account_key', 'coupon_item_id', name='uq_coupang_coupon_item'
        ),
    )
    op.create_index('ix_coupang_coupon_item_vendor_id', 'coupang_coupon_item', ['vendor_id'])
    op.create_index('ix_coupang_coupon_item_coupon_item_id', 'coupang_coupon_item', ['coupon_item_id'])
    op.create_index('ix_coupang_coupon_item_coupon_id', 'coupang_coupon_item', ['coupon_id'])
    op.create_index('ix_coupang_coupon_item_vendor_item_id', 'coupang_coupon_item', ['vendor_item_id'])

    # ③ 예산/계약 현황 (contract_id + target_month 그레인)
    op.create_table(
        'coupang_coupon_budget',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_key', sa.String(length=20), nullable=False),
        sa.Column('vendor_id', sa.String(length=20), nullable=False),
        sa.Column('contract_id', sa.String(length=20), nullable=False),
        sa.Column('target_month', sa.String(length=7), nullable=False),
        sa.Column('vendor_share_ratio', sa.Numeric(7, 2), nullable=True),
        sa.Column('total_budget_amount', sa.Numeric(14, 2), nullable=True),
        sa.Column('used_budget_amount', sa.Numeric(14, 2), nullable=True),
        sa.Column('vendor_contract_id', sa.String(length=20), nullable=True),
        sa.Column('seller_share_ratio', sa.Numeric(7, 2), nullable=True),
        sa.Column('coupang_share_ratio', sa.Numeric(7, 2), nullable=True),
        sa.Column('contract_type', sa.String(length=30), nullable=True),
        sa.Column('contract_start', sa.DateTime(), nullable=True),
        sa.Column('contract_end', sa.DateTime(), nullable=True),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'account_key', 'contract_id', 'target_month',
            name='uq_coupang_coupon_budget',
        ),
    )
    op.create_index('ix_coupang_coupon_budget_vendor_id', 'coupang_coupon_budget', ['vendor_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_coupang_coupon_budget_vendor_id', table_name='coupang_coupon_budget')
    op.drop_table('coupang_coupon_budget')
    op.drop_index('ix_coupang_coupon_item_vendor_item_id', table_name='coupang_coupon_item')
    op.drop_index('ix_coupang_coupon_item_coupon_id', table_name='coupang_coupon_item')
    op.drop_index('ix_coupang_coupon_item_coupon_item_id', table_name='coupang_coupon_item')
    op.drop_index('ix_coupang_coupon_item_vendor_id', table_name='coupang_coupon_item')
    op.drop_table('coupang_coupon_item')
    op.drop_index('ix_coupang_coupon_status', table_name='coupang_coupon')
    op.drop_index('ix_coupang_coupon_coupon_id', table_name='coupang_coupon')
    op.drop_index('ix_coupang_coupon_vendor_id', table_name='coupang_coupon')
    op.drop_table('coupang_coupon')
