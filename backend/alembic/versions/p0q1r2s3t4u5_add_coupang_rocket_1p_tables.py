"""add coupang rocket 1p tables (로켓배송 발주/정산 — 트랙 rocket-1p S2)

Revision ID: p0q1r2s3t4u5
Revises: o9p0q1r2s3t4
Create Date: 2026-06-17 12:00:00.000000

쿠팡 로켓배송(1P) 발주(PO)·정산(계산서) 저장 모델 신설(ref 20, D-9).
소스: supplier.coupang.com. 발주+납품 = /po-web/app/purchase-order/list JSON,
정산 = /scm/settlement/general/purchase/account SSR DOM. Akamai 봇방어(D-1) →
Mac 헤드풀 CDP 페처가 수집→raw push→백엔드 파서 정규화→적재(snapshot upsert).
grain: 발주=purchase_order_seq, 정산=invoice_seq(=vendorPaymentInfoSeq).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'p0q1r2s3t4u5'
down_revision: Union[str, None] = 'o9p0q1r2s3t4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'coupang_rocket_purchase_order',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('purchase_order_seq', sa.Integer(), nullable=False),
        sa.Column('vendor_id', sa.String(length=20), nullable=False),
        sa.Column('sum_of_order_amount', sa.Integer(), nullable=False),
        sa.Column('sum_of_receiving_amount', sa.Integer(), nullable=False),
        sa.Column('sum_of_vendor_confirmed_amount', sa.Integer(), nullable=False),
        sa.Column('order_qty', sa.Integer(), nullable=False),
        sa.Column('receiving_qty', sa.Integer(), nullable=False),
        sa.Column('vendor_confirmed_qty', sa.Integer(), nullable=False),
        sa.Column('purchase_order_status', sa.String(length=20), nullable=True),
        sa.Column('purchase_order_status_description', sa.String(length=40), nullable=True),
        sa.Column('purchase_type', sa.String(length=30), nullable=True),
        sa.Column('center_code', sa.String(length=20), nullable=True),
        sa.Column('center_name', sa.String(length=40), nullable=True),
        sa.Column('first_sku_name', sa.String(length=300), nullable=True),
        sa.Column('sku_count', sa.Integer(), nullable=False),
        sa.Column('po_created_at', sa.DateTime(), nullable=True),
        sa.Column('expected_delivery_date', sa.DateTime(), nullable=True),
        sa.Column('vendor_payment_seqs', sa.JSON(), nullable=True),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_coupang_rocket_purchase_order_purchase_order_seq',
                    'coupang_rocket_purchase_order', ['purchase_order_seq'], unique=True)
    op.create_index('ix_coupang_rocket_purchase_order_vendor_id',
                    'coupang_rocket_purchase_order', ['vendor_id'], unique=False)
    op.create_index('ix_coupang_rocket_purchase_order_purchase_order_status',
                    'coupang_rocket_purchase_order', ['purchase_order_status'], unique=False)
    op.create_index('ix_coupang_rocket_purchase_order_po_created_at',
                    'coupang_rocket_purchase_order', ['po_created_at'], unique=False)

    op.create_table(
        'coupang_rocket_settlement',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('invoice_seq', sa.Integer(), nullable=False),
        sa.Column('vendor_id', sa.String(length=20), nullable=False),
        sa.Column('supply_amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('vat', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('payment_amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('issue_date', sa.Date(), nullable=True),
        sa.Column('payment_date', sa.Date(), nullable=True),
        sa.Column('tax_invoice_confirmed_date', sa.Date(), nullable=True),
        sa.Column('settlement_type', sa.String(length=20), nullable=True),
        sa.Column('bill_issue_type', sa.String(length=20), nullable=True),
        sa.Column('tax_type', sa.String(length=20), nullable=True),
        sa.Column('first_payment_amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('second_payment_amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_coupang_rocket_settlement_invoice_seq',
                    'coupang_rocket_settlement', ['invoice_seq'], unique=True)
    op.create_index('ix_coupang_rocket_settlement_vendor_id',
                    'coupang_rocket_settlement', ['vendor_id'], unique=False)
    op.create_index('ix_coupang_rocket_settlement_issue_date',
                    'coupang_rocket_settlement', ['issue_date'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_coupang_rocket_settlement_issue_date', table_name='coupang_rocket_settlement')
    op.drop_index('ix_coupang_rocket_settlement_vendor_id', table_name='coupang_rocket_settlement')
    op.drop_index('ix_coupang_rocket_settlement_invoice_seq', table_name='coupang_rocket_settlement')
    op.drop_table('coupang_rocket_settlement')
    op.drop_index('ix_coupang_rocket_purchase_order_po_created_at', table_name='coupang_rocket_purchase_order')
    op.drop_index('ix_coupang_rocket_purchase_order_purchase_order_status', table_name='coupang_rocket_purchase_order')
    op.drop_index('ix_coupang_rocket_purchase_order_vendor_id', table_name='coupang_rocket_purchase_order')
    op.drop_index('ix_coupang_rocket_purchase_order_purchase_order_seq', table_name='coupang_rocket_purchase_order')
    op.drop_table('coupang_rocket_purchase_order')
