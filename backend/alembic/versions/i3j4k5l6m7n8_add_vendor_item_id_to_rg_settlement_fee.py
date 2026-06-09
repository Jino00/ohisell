"""add vendor_item_id to coupang_rg_settlement_fee (S6 옵션 단위 입자도)

Revision ID: i3j4k5l6m7n8
Revises: h2i3j4k5l6m7
Create Date: 2026-06-09 15:00:00.000000

RG 수수료 회계 자동화 트랙 S6 — 옵션 단위 수집(D-2).
종류별 엑셀(WAREHOUSING_SHIPPING 등)의 주문/SKU 상세를 vendor_item_id(옵션) 단위로 적재하기 위해
grain을 (account_key, from, to, fee_type) → (..., vendor_item_id)로 확장.

★vendor_item_id NOT NULL + default='' (빈문자열 sentinel):
  - 계정 단위(status/api, Phase 1) row는 vendor_item_id=''.
  - 옵션 단위(엑셀, S6) row는 vendor_item_id=실제 옵션ID.
  NULL이 아닌 '' sentinel을 쓰는 이유: SQLite/Postgres unique 제약은 NULL을 distinct로 취급해
  중복을 못 막음. 기존 NULL 없이 server_default=''로 채워 grain 안정화.

SQLite는 unique 제약 변경에 테이블 재생성 필요 → batch_alter_table(테이블 copy_from)로 처리.
기존 행은 server_default=''로 자동 채워지므로 (account, from, to, fee_type, '') 그대로 유지 → 충돌 없음.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'i3j4k5l6m7n8'
down_revision: Union[str, None] = 'h2i3j4k5l6m7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('coupang_rg_settlement_fee', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('vendor_item_id', sa.String(30), nullable=False, server_default='')
        )
        batch_op.drop_constraint('uq_coupang_rg_settlement_fee', type_='unique')
        batch_op.create_unique_constraint(
            'uq_coupang_rg_settlement_fee',
            ['account_key', 'recognition_date_from', 'recognition_date_to',
             'fee_type', 'vendor_item_id'],
        )
        batch_op.create_index(
            'ix_coupang_rg_settlement_fee_vendor_item_id',
            ['vendor_item_id'],
        )


def downgrade() -> None:
    with op.batch_alter_table('coupang_rg_settlement_fee', schema=None) as batch_op:
        batch_op.drop_index('ix_coupang_rg_settlement_fee_vendor_item_id')
        batch_op.drop_constraint('uq_coupang_rg_settlement_fee', type_='unique')
        batch_op.create_unique_constraint(
            'uq_coupang_rg_settlement_fee',
            ['account_key', 'recognition_date_from', 'recognition_date_to', 'fee_type'],
        )
        batch_op.drop_column('vendor_item_id')
