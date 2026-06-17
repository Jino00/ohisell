"""add coupang rocket 1p po-item table (발주상세 per-SKU — 트랙 rocket-1p S4.5a)

Revision ID: q1r2s3t4u5v6
Revises: p0q1r2s3t4u5
Create Date: 2026-06-18 07:00:00.000000

쿠팡 로켓배송(1P) 발주상세 per-SKU 라인아이템 모델 신설(ref 20b, D-13).
소스: supplier.coupang.com GET /scm/purchase/order/get/{seq} SSR Table[7]. 1PO=N SKU.
Akamai 봇방어(D-1) → Mac 헤드풀 페처가 DOM rows 추출→raw push→백엔드 위치파서 정규화→
해당 PO snapshot replace(멱등). 용도: PO그레인 원가분해 불가(multi-SKU 61%) →
per-SKU 수량으로 1P net_profit cost 산정(S4.5c, 상품번호→internal_sku 매핑 S4.5b).
grain: (purchase_order_seq, product_number).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'q1r2s3t4u5v6'
down_revision: Union[str, None] = 'p0q1r2s3t4u5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'coupang_rocket_purchase_order_item',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('purchase_order_seq', sa.Integer(), nullable=False),
        sa.Column('vendor_id', sa.String(length=20), nullable=False),
        sa.Column('line_no', sa.Integer(), nullable=False),
        sa.Column('product_number', sa.String(length=30), nullable=False),
        sa.Column('barcode', sa.String(length=40), nullable=True),
        sa.Column('product_name', sa.String(length=300), nullable=True),
        sa.Column('purchase_type', sa.String(length=30), nullable=True),
        sa.Column('order_qty', sa.Integer(), nullable=False),
        sa.Column('unit_purchase_price', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('line_order_amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('line_supply_amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('line_vat', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('purchase_order_seq', 'product_number', name='uq_rocket_po_item'),
    )
    op.create_index('ix_coupang_rocket_purchase_order_item_purchase_order_seq',
                    'coupang_rocket_purchase_order_item', ['purchase_order_seq'], unique=False)
    op.create_index('ix_coupang_rocket_purchase_order_item_vendor_id',
                    'coupang_rocket_purchase_order_item', ['vendor_id'], unique=False)
    op.create_index('ix_coupang_rocket_purchase_order_item_product_number',
                    'coupang_rocket_purchase_order_item', ['product_number'], unique=False)
    op.create_index('ix_coupang_rocket_purchase_order_item_barcode',
                    'coupang_rocket_purchase_order_item', ['barcode'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_coupang_rocket_purchase_order_item_barcode',
                  table_name='coupang_rocket_purchase_order_item')
    op.drop_index('ix_coupang_rocket_purchase_order_item_product_number',
                  table_name='coupang_rocket_purchase_order_item')
    op.drop_index('ix_coupang_rocket_purchase_order_item_vendor_id',
                  table_name='coupang_rocket_purchase_order_item')
    op.drop_index('ix_coupang_rocket_purchase_order_item_purchase_order_seq',
                  table_name='coupang_rocket_purchase_order_item')
    op.drop_table('coupang_rocket_purchase_order_item')
