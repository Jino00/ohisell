"""add orders 배송 구분 컬럼 (Jino 지시 2026-07-28 — 판매 내역에서 배송 구분 조회)

ADD COLUMN 5개(전부 nullable). 기존 컬럼·기존 행의 의미는 변경하지 않는다:
  · orders.shipping_cost = **고객이 낸 배송비**(deliveryFeeAmount). NULL = 배송비 포함 상품.
  · 신규 orders.shipping_cost_paid = **우리가 지불한 배송비**(건별 스냅샷).
    단가가 코드 상수라 개정 시 과거 원가가 소급 왜곡되는 것을 막으려고 행에 박아 둔다.
  · 실부담(=paid − COALESCE(shipping_cost,0))은 파생값이라 컬럼으로 두지 않는다(중복 저장 금지).

기존 행은 NULL로 남고 백필 스크립트가 채운다:
  backend/scripts/backfill_order_delivery_fields.py
BEP 계산은 컬럼이 NULL이면 종전대로 raw_data 파싱으로 폴백하므로, 마이그레이션만 적용하고
백필을 아직 안 돌린 상태에서도 산출값이 달라지지 않는다(회귀 0).

인덱스: 배송 구분 집계(교차표)용 (channel_id, delivery_attribute_type).

Revision ID: f6a8c0e2b4d6
Revises: e5f7a9c1b3d5
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f6a8c0e2b4d6'
down_revision: Union[str, None] = 'e5f7a9c1b3d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('orders', sa.Column('delivery_attribute_type', sa.String(40), nullable=True))
    op.add_column('orders', sa.Column('delivery_policy_type', sa.String(20), nullable=True))
    op.add_column('orders', sa.Column('shipping_fee_type', sa.String(20), nullable=True))
    op.add_column('orders', sa.Column('logistics_company_id', sa.String(20), nullable=True))
    op.add_column('orders', sa.Column('shipping_cost_paid', sa.Numeric(12, 2), nullable=True))
    op.create_index(
        'ix_orders_channel_delivery_attr', 'orders',
        ['channel_id', 'delivery_attribute_type'],
    )


def downgrade() -> None:
    op.drop_index('ix_orders_channel_delivery_attr', table_name='orders')
    op.drop_column('orders', 'shipping_cost_paid')
    op.drop_column('orders', 'logistics_company_id')
    op.drop_column('orders', 'shipping_fee_type')
    op.drop_column('orders', 'delivery_policy_type')
    op.drop_column('orders', 'delivery_attribute_type')
