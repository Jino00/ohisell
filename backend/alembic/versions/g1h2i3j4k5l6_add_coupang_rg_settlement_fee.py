"""add_coupang_rg_settlement_fee

Revision ID: g1h2i3j4k5l6
Revises: f2a4c6e8b0d1
Create Date: 2026-06-08 23:00:00.000000

RG 수수료 회계 자동화 트랙 S2 — CoupangRgSettlementFee 테이블 신설.
윙 내부 API(status/api) 수집 결과 저장.
grain=(account_key, recognition_date_from, recognition_date_to, fee_type).
Phase 1: 계정 단위 대조뷰. Phase 2(S6): vendor_item_id 컬럼 추가 예정.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'g1h2i3j4k5l6'
down_revision: Union[str, None] = 'b2d4f6082ace'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'coupang_rg_settlement_fee',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_key', sa.String(20), nullable=False),
        sa.Column('recognition_date_from', sa.Date(), nullable=False),
        sa.Column('recognition_date_to', sa.Date(), nullable=False),
        sa.Column('fee_type', sa.String(30), nullable=False),
        sa.Column('raw_type', sa.String(100), nullable=True),
        sa.Column('amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'account_key', 'recognition_date_from', 'recognition_date_to', 'fee_type',
            name='uq_coupang_rg_settlement_fee',
        ),
    )
    op.create_index('ix_coupang_rg_settlement_fee_account_key', 'coupang_rg_settlement_fee', ['account_key'])
    op.create_index('ix_coupang_rg_settlement_fee_recognition_date_from', 'coupang_rg_settlement_fee', ['recognition_date_from'])
    op.create_index('ix_coupang_rg_settlement_fee_recognition_date_to', 'coupang_rg_settlement_fee', ['recognition_date_to'])


def downgrade() -> None:
    op.drop_index('ix_coupang_rg_settlement_fee_recognition_date_to', table_name='coupang_rg_settlement_fee')
    op.drop_index('ix_coupang_rg_settlement_fee_recognition_date_from', table_name='coupang_rg_settlement_fee')
    op.drop_index('ix_coupang_rg_settlement_fee_account_key', table_name='coupang_rg_settlement_fee')
    op.drop_table('coupang_rg_settlement_fee')
