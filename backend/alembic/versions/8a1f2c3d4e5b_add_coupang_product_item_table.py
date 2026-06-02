"""add_coupang_product_item_table

Revision ID: 8a1f2c3d4e5b
Revises: 79c5bf56a7eb
Create Date: 2026-06-02 20:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8a1f2c3d4e5b'
down_revision: Union[str, None] = '79c5bf56a7eb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'coupang_product_item',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('vendor_item_id', sa.String(length=20), nullable=False),
        sa.Column('account_key', sa.String(length=20), nullable=False),
        sa.Column('vendor_id', sa.String(length=20), nullable=False),
        sa.Column('seller_product_id', sa.String(length=20), nullable=False),
        sa.Column('seller_product_name', sa.String(length=300), nullable=True),
        sa.Column('item_name', sa.String(length=300), nullable=True),
        sa.Column('external_vendor_sku', sa.String(length=100), nullable=True),
        sa.Column('sale_price', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('supply_price', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('sale_agent_commission', sa.Numeric(precision=7, scale=2), nullable=True),
        sa.Column('max_buy_count', sa.Integer(), nullable=True),
        sa.Column('amount_in_stock', sa.Integer(), nullable=True),
        sa.Column('on_sale', sa.Boolean(), nullable=True),
        sa.Column('status_name', sa.String(length=20), nullable=True),
        sa.Column('brand', sa.String(length=100), nullable=True),
        sa.Column('category_id', sa.Integer(), nullable=True),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('vendor_item_id', name='uq_coupang_product_item_vendor_item'),
    )
    op.create_index('ix_coupang_product_item_vendor_item_id', 'coupang_product_item', ['vendor_item_id'])
    op.create_index('ix_coupang_product_item_seller_product_id', 'coupang_product_item', ['seller_product_id'])
    op.create_index('ix_coupang_product_item_external_vendor_sku', 'coupang_product_item', ['external_vendor_sku'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_coupang_product_item_external_vendor_sku', table_name='coupang_product_item')
    op.drop_index('ix_coupang_product_item_seller_product_id', table_name='coupang_product_item')
    op.drop_index('ix_coupang_product_item_vendor_item_id', table_name='coupang_product_item')
    op.drop_table('coupang_product_item')
