"""add_coupang_return_item_and_exchange

Revision ID: a3d7c9e1f2b4
Revises: 9b2e4f6a7c1d
Create Date: 2026-06-03 10:30:00.000000

P2 반품/취소/교환 도메인 — 순매출 차감 회계축.
명세: docs/references/03_coupang_returns_api_specs.md
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3d7c9e1f2b4'
down_revision: Union[str, None] = '9b2e4f6a7c1d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 반품/취소 옵션 그레인 (순매출 차감)
    op.create_table(
        'coupang_return_item',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('receipt_id', sa.String(length=20), nullable=False),
        sa.Column('vendor_item_id', sa.String(length=20), nullable=False),
        sa.Column('account_key', sa.String(length=20), nullable=False),
        sa.Column('vendor_id', sa.String(length=20), nullable=False),
        sa.Column('order_id', sa.String(length=30), nullable=False),
        sa.Column('receipt_type', sa.String(length=10), nullable=False),
        sa.Column('receipt_status', sa.String(length=30), nullable=True),
        sa.Column('cancel_count', sa.Integer(), nullable=False),
        sa.Column('purchase_count', sa.Integer(), nullable=True),
        sa.Column('seller_product_id', sa.String(length=20), nullable=True),
        sa.Column('seller_product_name', sa.String(length=300), nullable=True),
        sa.Column('vendor_item_name', sa.String(length=300), nullable=True),
        sa.Column('fault_by_type', sa.String(length=20), nullable=True),
        sa.Column('cancel_reason', sa.Text(), nullable=True),
        sa.Column('release_status', sa.String(length=2), nullable=True),
        sa.Column('pre_refund', sa.Boolean(), nullable=True),
        sa.Column('withdrawn', sa.Boolean(), nullable=False),
        sa.Column('requested_at', sa.DateTime(), nullable=True),
        sa.Column('modified_at', sa.DateTime(), nullable=True),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('receipt_id', 'vendor_item_id', name='uq_coupang_return_item'),
    )
    op.create_index('ix_coupang_return_item_receipt_id', 'coupang_return_item', ['receipt_id'])
    op.create_index('ix_coupang_return_item_vendor_item_id', 'coupang_return_item', ['vendor_item_id'])
    op.create_index('ix_coupang_return_item_order_id', 'coupang_return_item', ['order_id'])
    op.create_index('ix_coupang_return_item_requested_at', 'coupang_return_item', ['requested_at'])

    # 교환요청 (운영 기록, 회계 차감 없음)
    op.create_table(
        'coupang_exchange',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('exchange_id', sa.String(length=20), nullable=False),
        sa.Column('vendor_item_id', sa.String(length=20), nullable=False),
        sa.Column('account_key', sa.String(length=20), nullable=False),
        sa.Column('vendor_id', sa.String(length=20), nullable=False),
        sa.Column('order_id', sa.String(length=30), nullable=False),
        sa.Column('exchange_status', sa.String(length=20), nullable=True),
        sa.Column('order_delivery_status', sa.String(length=20), nullable=True),
        sa.Column('refer_type', sa.String(length=20), nullable=True),
        sa.Column('vendor_item_name', sa.String(length=300), nullable=True),
        sa.Column('exchange_count', sa.Integer(), nullable=True),
        sa.Column('requested_at', sa.DateTime(), nullable=True),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('exchange_id', 'vendor_item_id', name='uq_coupang_exchange'),
    )
    op.create_index('ix_coupang_exchange_exchange_id', 'coupang_exchange', ['exchange_id'])
    op.create_index('ix_coupang_exchange_vendor_item_id', 'coupang_exchange', ['vendor_item_id'])
    op.create_index('ix_coupang_exchange_order_id', 'coupang_exchange', ['order_id'])
    op.create_index('ix_coupang_exchange_requested_at', 'coupang_exchange', ['requested_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_coupang_exchange_requested_at', table_name='coupang_exchange')
    op.drop_index('ix_coupang_exchange_order_id', table_name='coupang_exchange')
    op.drop_index('ix_coupang_exchange_vendor_item_id', table_name='coupang_exchange')
    op.drop_index('ix_coupang_exchange_exchange_id', table_name='coupang_exchange')
    op.drop_table('coupang_exchange')
    op.drop_index('ix_coupang_return_item_requested_at', table_name='coupang_return_item')
    op.drop_index('ix_coupang_return_item_order_id', table_name='coupang_return_item')
    op.drop_index('ix_coupang_return_item_vendor_item_id', table_name='coupang_return_item')
    op.drop_index('ix_coupang_return_item_receipt_id', table_name='coupang_return_item')
    op.drop_table('coupang_return_item')
