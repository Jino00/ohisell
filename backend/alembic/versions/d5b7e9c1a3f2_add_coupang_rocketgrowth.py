"""add_coupang_rocketgrowth_size_inventory_order

Revision ID: d5b7e9c1a3f2
Revises: c8f1a3b5d7e9
Create Date: 2026-06-03 21:00:00.000000

P3 로켓그로스 도메인 (트랙 D-14).
명세: docs/references/05_coupang_rocketgrowth_api_specs.md
- coupang_product_item에 사이즈 컬럼 추가(width/length/height_mm·weight/net_weight_g·cbm) — 보관비 CBM 토대
- coupang_rg_inventory: 로켓창고 옵션별 실재고(주문가능수량·30일판매)
- coupang_rg_order_item: RG 주문 옵션 그레인(기존 Order와 분리 — 이중계산 방지)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5b7e9c1a3f2'
down_revision: Union[str, None] = 'c8f1a3b5d7e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ① coupang_product_item 사이즈 컬럼 추가 (RG 상품조회 skuInfo → 보관비 CBM 토대)
    op.add_column('coupang_product_item', sa.Column('width_mm', sa.Integer(), nullable=True))
    op.add_column('coupang_product_item', sa.Column('length_mm', sa.Integer(), nullable=True))
    op.add_column('coupang_product_item', sa.Column('height_mm', sa.Integer(), nullable=True))
    op.add_column('coupang_product_item', sa.Column('weight_g', sa.Integer(), nullable=True))
    op.add_column('coupang_product_item', sa.Column('net_weight_g', sa.Integer(), nullable=True))
    op.add_column('coupang_product_item', sa.Column('cbm', sa.Numeric(12, 6), nullable=True))

    # ② 로켓창고 재고 (옵션 그레인 — 주문가능수량·30일판매)
    op.create_table(
        'coupang_rg_inventory',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('vendor_item_id', sa.String(length=20), nullable=False),
        sa.Column('account_key', sa.String(length=20), nullable=False),
        sa.Column('vendor_id', sa.String(length=20), nullable=False),
        sa.Column('external_sku_id', sa.String(length=50), nullable=True),
        sa.Column('orderable_qty', sa.Integer(), nullable=True),
        sa.Column('sold_30d', sa.Integer(), nullable=True),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('vendor_item_id', name='uq_coupang_rg_inventory_vii'),
    )
    op.create_index('ix_coupang_rg_inventory_vendor_item_id', 'coupang_rg_inventory', ['vendor_item_id'])

    # ③ RG 주문 (옵션 그레인 — 기존 Order와 분리)
    op.create_table(
        'coupang_rg_order_item',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.String(length=30), nullable=False),
        sa.Column('vendor_item_id', sa.String(length=20), nullable=False),
        sa.Column('account_key', sa.String(length=20), nullable=False),
        sa.Column('vendor_id', sa.String(length=20), nullable=False),
        sa.Column('product_name', sa.String(length=300), nullable=True),
        sa.Column('sales_quantity', sa.Integer(), nullable=True),
        sa.Column('unit_sales_price', sa.Numeric(12, 2), nullable=True),
        sa.Column('currency', sa.String(length=8), nullable=True),
        sa.Column('paid_at', sa.DateTime(), nullable=True),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('order_id', 'vendor_item_id', name='uq_coupang_rg_order_item'),
    )
    op.create_index('ix_coupang_rg_order_item_order_id', 'coupang_rg_order_item', ['order_id'])
    op.create_index('ix_coupang_rg_order_item_vendor_item_id', 'coupang_rg_order_item', ['vendor_item_id'])
    op.create_index('ix_coupang_rg_order_item_paid_at', 'coupang_rg_order_item', ['paid_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_coupang_rg_order_item_paid_at', table_name='coupang_rg_order_item')
    op.drop_index('ix_coupang_rg_order_item_vendor_item_id', table_name='coupang_rg_order_item')
    op.drop_index('ix_coupang_rg_order_item_order_id', table_name='coupang_rg_order_item')
    op.drop_table('coupang_rg_order_item')

    op.drop_index('ix_coupang_rg_inventory_vendor_item_id', table_name='coupang_rg_inventory')
    op.drop_table('coupang_rg_inventory')

    op.drop_column('coupang_product_item', 'cbm')
    op.drop_column('coupang_product_item', 'net_weight_g')
    op.drop_column('coupang_product_item', 'weight_g')
    op.drop_column('coupang_product_item', 'height_mm')
    op.drop_column('coupang_product_item', 'length_mm')
    op.drop_column('coupang_product_item', 'width_mm')
