"""add rocket product cost map (1P 원가 브리지 — 트랙 rocket-1p S4.5b)

Revision ID: r2s3t4u5v6w7
Revises: q1r2s3t4u5v6
Create Date: 2026-06-18 09:00:00.000000

쿠팡 로켓배송(1P) 발주상세 상품번호 → product_master.internal_sku 수동 브리지(ref 20b §3, D-13).
1P supplier 카탈로그 키가 우리 원가 마스터에 0건 매칭 → 일회성 수동 매핑 테이블 신설.
grain: product_number(unique). status=confirmed(internal_sku 채움)|ignored(원가 제외).
용도: S4.5c net_profit cost = Σ(po_item.order_qty × product_master.cost_price[internal_sku]).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'r2s3t4u5v6w7'
down_revision: Union[str, None] = 'q1r2s3t4u5v6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'rocket_product_cost_map',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('product_number', sa.String(length=30), nullable=False),
        sa.Column('internal_sku', sa.String(length=50), nullable=True),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('match_method', sa.String(length=16), nullable=True),
        sa.Column('barcode', sa.String(length=40), nullable=True),
        sa.Column('product_name', sa.String(length=300), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_rocket_product_cost_map_product_number',
                    'rocket_product_cost_map', ['product_number'], unique=True)
    op.create_index('ix_rocket_product_cost_map_internal_sku',
                    'rocket_product_cost_map', ['internal_sku'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_rocket_product_cost_map_internal_sku', table_name='rocket_product_cost_map')
    op.drop_index('ix_rocket_product_cost_map_product_number', table_name='rocket_product_cost_map')
    op.drop_table('rocket_product_cost_map')
