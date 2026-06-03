"""add_coupang_settlement_revenue_fee_and_change_log

Revision ID: c8f1a3b5d7e9
Revises: a3d7c9e1f2b4
Create Date: 2026-06-03 17:00:00.000000

P4 정산 도메인 — 회계 진짜 순이익(D-10/D-11).
명세: docs/references/04_coupang_fees_map.md §6 (라이브 실응답 확정).
- coupang_revenue_fee: 매출내역 옵션 그레인(serviceFeeRatio 실측 — 수수료 비교축)
- coupang_settlement_payout: 지급내역 정산 단위(서비스이용료·차감·최종지급액)
- coupang_fee_change_log: 수수료율 불일치 감사 로그(정당변동/이상)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8f1a3b5d7e9'
down_revision: Union[str, None] = 'a3d7c9e1f2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ① 매출내역 옵션 그레인 (실제 판매수수료율 — D-10/D-11 비교축)
    op.create_table(
        'coupang_revenue_fee',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.String(length=30), nullable=False),
        sa.Column('vendor_item_id', sa.String(length=20), nullable=False),
        sa.Column('account_key', sa.String(length=20), nullable=False),
        sa.Column('vendor_id', sa.String(length=20), nullable=False),
        sa.Column('sale_type', sa.String(length=20), nullable=False),
        sa.Column('sale_date', sa.Date(), nullable=True),
        sa.Column('recognition_date', sa.Date(), nullable=True),
        sa.Column('settlement_date', sa.Date(), nullable=True),
        sa.Column('final_settlement_date', sa.Date(), nullable=True),
        sa.Column('product_id', sa.String(length=20), nullable=True),
        sa.Column('product_name', sa.String(length=300), nullable=True),
        sa.Column('vendor_item_name', sa.String(length=300), nullable=True),
        sa.Column('sale_price', sa.Numeric(14, 2), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('sale_amount', sa.Numeric(14, 2), nullable=False),
        sa.Column('service_fee', sa.Numeric(14, 2), nullable=False),
        sa.Column('service_fee_vat', sa.Numeric(14, 2), nullable=False),
        sa.Column('service_fee_ratio', sa.Numeric(7, 2), nullable=True),
        sa.Column('settlement_amount', sa.Numeric(14, 2), nullable=False),
        sa.Column('coupang_discount_coupon', sa.Numeric(12, 2), nullable=False),
        sa.Column('seller_discount_coupon', sa.Numeric(12, 2), nullable=False),
        sa.Column('downloadable_coupon', sa.Numeric(12, 2), nullable=False),
        sa.Column('courantee_fee', sa.Numeric(12, 2), nullable=False),
        sa.Column('store_fee_discount', sa.Numeric(12, 2), nullable=False),
        sa.Column('external_seller_sku_code', sa.String(length=100), nullable=True),
        sa.Column('delivery_fee_amount', sa.Numeric(12, 2), nullable=True),
        sa.Column('delivery_fee_fee', sa.Numeric(12, 2), nullable=True),
        sa.Column('delivery_fee_ratio', sa.Numeric(7, 2), nullable=True),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'order_id', 'vendor_item_id', 'recognition_date', 'sale_type',
            name='uq_coupang_revenue_fee',
        ),
    )
    op.create_index('ix_coupang_revenue_fee_order_id', 'coupang_revenue_fee', ['order_id'])
    op.create_index('ix_coupang_revenue_fee_vendor_item_id', 'coupang_revenue_fee', ['vendor_item_id'])
    op.create_index('ix_coupang_revenue_fee_recognition_date', 'coupang_revenue_fee', ['recognition_date'])

    # ② 지급내역 정산 단위 (서비스이용료·차감·최종지급액)
    op.create_table(
        'coupang_settlement_payout',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_key', sa.String(length=20), nullable=False),
        sa.Column('vendor_id', sa.String(length=20), nullable=False),
        sa.Column('settlement_type', sa.String(length=20), nullable=False),
        sa.Column('settlement_date', sa.Date(), nullable=True),
        sa.Column('revenue_recognition_year_month', sa.String(length=7), nullable=True),
        sa.Column('revenue_recognition_date_from', sa.Date(), nullable=True),
        sa.Column('revenue_recognition_date_to', sa.Date(), nullable=True),
        sa.Column('total_sale', sa.Numeric(14, 2), nullable=False),
        sa.Column('service_fee', sa.Numeric(14, 2), nullable=False),
        sa.Column('settlement_target_amount', sa.Numeric(14, 2), nullable=False),
        sa.Column('settlement_amount', sa.Numeric(14, 2), nullable=False),
        sa.Column('last_amount', sa.Numeric(14, 2), nullable=False),
        sa.Column('pending_released_amount', sa.Numeric(14, 2), nullable=False),
        sa.Column('seller_discount_coupon', sa.Numeric(12, 2), nullable=False),
        sa.Column('downloadable_coupon', sa.Numeric(12, 2), nullable=False),
        sa.Column('dedicated_delivery_amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('seller_service_fee', sa.Numeric(12, 2), nullable=False),
        sa.Column('courantee_fee', sa.Numeric(12, 2), nullable=False),
        sa.Column('courantee_customer_reward', sa.Numeric(12, 2), nullable=False),
        sa.Column('deduction_amount', sa.Numeric(14, 2), nullable=False),
        sa.Column('debt_of_last_week', sa.Numeric(14, 2), nullable=False),
        sa.Column('final_amount', sa.Numeric(14, 2), nullable=False),
        sa.Column('store_fee_discount', sa.Numeric(12, 2), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'vendor_id', 'settlement_type', 'settlement_date',
            'revenue_recognition_date_from', 'revenue_recognition_date_to',
            name='uq_coupang_settlement_payout',
        ),
    )
    op.create_index('ix_coupang_settlement_payout_vendor_id', 'coupang_settlement_payout', ['vendor_id'])
    op.create_index('ix_coupang_settlement_payout_settlement_date', 'coupang_settlement_payout', ['settlement_date'])

    # ③ 수수료율 불일치 감사 로그 (D-11)
    op.create_table(
        'coupang_fee_change_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('vendor_item_id', sa.String(length=20), nullable=False),
        sa.Column('account_key', sa.String(length=20), nullable=True),
        sa.Column('vendor_id', sa.String(length=20), nullable=True),
        sa.Column('registered_ratio', sa.Numeric(7, 2), nullable=True),
        sa.Column('observed_ratio', sa.Numeric(7, 2), nullable=True),
        sa.Column('reauthored_ratio', sa.Numeric(7, 2), nullable=True),
        sa.Column('change_type', sa.String(length=20), nullable=False),
        sa.Column('order_id', sa.String(length=30), nullable=True),
        sa.Column('recognition_date', sa.Date(), nullable=True),
        sa.Column('resolved', sa.Boolean(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('detected_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'vendor_item_id', 'observed_ratio', 'registered_ratio',
            name='uq_coupang_fee_change_log',
        ),
    )
    op.create_index('ix_coupang_fee_change_log_vendor_item_id', 'coupang_fee_change_log', ['vendor_item_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_coupang_fee_change_log_vendor_item_id', table_name='coupang_fee_change_log')
    op.drop_table('coupang_fee_change_log')
    op.drop_index('ix_coupang_settlement_payout_settlement_date', table_name='coupang_settlement_payout')
    op.drop_index('ix_coupang_settlement_payout_vendor_id', table_name='coupang_settlement_payout')
    op.drop_table('coupang_settlement_payout')
    op.drop_index('ix_coupang_revenue_fee_recognition_date', table_name='coupang_revenue_fee')
    op.drop_index('ix_coupang_revenue_fee_vendor_item_id', table_name='coupang_revenue_fee')
    op.drop_index('ix_coupang_revenue_fee_order_id', table_name='coupang_revenue_fee')
    op.drop_table('coupang_revenue_fee')
