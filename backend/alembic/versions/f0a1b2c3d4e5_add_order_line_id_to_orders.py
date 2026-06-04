"""add platform_order_line_id to orders (productOrderId grain)

Revision ID: f0a1b2c3d4e5
Revises: d4f6a8c0e2b3
Create Date: 2026-06-04

네이버 트랙 N1·D-6 이익 정밀화 — 주문 그레인을 productOrderId까지 세분화.
같은 (주문, 상품)이 2개 productOrderId로 분할될 때(부분취소/부분배송, 라이브 1.4%)
2번째 라인의 수량·매출이 누락되던 버그 수정. uq_order_item에 platform_order_line_id 추가.
기존 네이버 주문은 raw_data의 productOrder.productOrderId로 line_id 백필
(미백필 시 다음 재동기화에서 line_id="" 기존행과 새 productOrderId행이 중복 적재될 위험).
쿠팡/cafe24는 line_id=""(원자 그레인이 (주문,상품)) → 동작 불변.
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f0a1b2c3d4e5'
down_revision: Union[str, None] = 'd4f6a8c0e2b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) 컬럼 추가 (기존 행은 server_default='' → 빈값)
    op.add_column(
        'orders',
        sa.Column('platform_order_line_id', sa.String(length=40), nullable=False, server_default=''),
    )

    # 2) 백필: raw_data(JSON)에 productOrder.productOrderId가 있는 행만 line_id 설정.
    #    네이버 주문만 해당(쿠팡/cafe24 raw_data엔 해당 경로 없음 → 자연히 스킵).
    #    json 함수 미가용 SQLite 대비 Python으로 파싱(이식성).
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, raw_data FROM orders WHERE platform_order_line_id = '' AND raw_data IS NOT NULL")
    ).fetchall()
    for rid, rd in rows:
        try:
            obj = rd if isinstance(rd, dict) else json.loads(rd)
            poid = (obj or {}).get("productOrder", {}).get("productOrderId")
        except Exception:
            poid = None
        if poid:
            # 백필은 best-effort. 못 채운 행(truncated/이상 raw_data)은 line_id=""로 남지만,
            # sync_service의 호환 브리지가 재동기화 시 승격해 이중계산을 막는다.
            conn.execute(
                sa.text("UPDATE orders SET platform_order_line_id = :p WHERE id = :i"),
                {"p": str(poid), "i": rid},
            )

    # 3) 유니크 제약 교체: (channel_id, order_number, platform_product_id)
    #    → (channel_id, order_number, platform_product_id, platform_order_line_id)
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_constraint('uq_order_item', type_='unique')
        batch_op.create_unique_constraint(
            'uq_order_item',
            ['channel_id', 'order_number', 'platform_product_id', 'platform_order_line_id'],
        )


def downgrade() -> None:
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_constraint('uq_order_item', type_='unique')
        batch_op.create_unique_constraint(
            'uq_order_item',
            ['channel_id', 'order_number', 'platform_product_id'],
        )
    op.drop_column('orders', 'platform_order_line_id')
