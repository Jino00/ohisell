"""add channel.sell_type + product_channel_mapping.mapping_source (상품 연관맵 S1)

Revision ID: t4u5v6w7x8y9
Revises: s3t4u5v6w7x8
Create Date: 2026-07-03 00:00:00.000000

트랙 docs/tracks/active/track_product-connection-map.md D-6.
channels.sell_type   : 3P/RG/1P (네이버·cafe24는 NULL). D-2 대조표를 채널 시드에 반영.
product_channel_mapping.mapping_source : excel_master(엑셀 마스터 진실원천) / auto_sync(쿠팡
  상품동기화 자동생성, 기본값). 신규 엑셀 Harness가 만든 매핑이 스케줄러의 자동 동기화
  (coupang/product_sync.py)에 덮어써지지 않도록 하는 provenance 가드에 사용.
둘 다 SQLite ADD COLUMN 네이티브 지원(batch_alter 불필요). 기존 2,610개 매핑 행은
mapping_source='auto_sync' 기본값으로 채워짐(실제로 대부분 자동 동기화 기원이 맞음).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 't4u5v6w7x8y9'
down_revision: Union[str, None] = 's3t4u5v6w7x8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SELL_TYPE_BY_CODE = {
    "COUPANG_WING1": "3P",
    "COUPANG_WING2": "3P",
    "COUPANG_RG1": "RG",
    "COUPANG_RG2": "RG",
    "COUPANG_ROCKET": "1P",
}


def upgrade() -> None:
    op.add_column('channels', sa.Column('sell_type', sa.String(length=10), nullable=True))
    op.add_column(
        'product_channel_mapping',
        sa.Column('mapping_source', sa.String(length=20), nullable=False, server_default='auto_sync'),
    )

    conn = op.get_bind()
    channels = sa.table(
        'channels',
        sa.column('code', sa.String),
        sa.column('sell_type', sa.String),
    )
    for code, sell_type in _SELL_TYPE_BY_CODE.items():
        conn.execute(channels.update().where(channels.c.code == code).values(sell_type=sell_type))


def downgrade() -> None:
    op.drop_column('product_channel_mapping', 'mapping_source')
    op.drop_column('channels', 'sell_type')
