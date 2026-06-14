"""add coupang_vendor_summary_daily (Wing 세션 자동화 S2 매출 대조 저장)

Revision ID: m7n8o9p0q1r2
Revises: l6m7n8o9p0q1
Create Date: 2026-06-14 18:30:00.000000

Wing 세션 자동화 트랙 S2 — 쿠팡 공식 판매분석 GMV(vendor-summary, ref 18) 저장 모델 신설.
Mac 헤드풀 페처가 브라우저측 fetch → prod push → 이 테이블에 적재. revenue_reconcile
Harness가 우리 revenue_3p/revenue_rg와 닫힌일 드리프트% 대조(읽기전용, net_profit 불변).
grain: (summary_date, account_key, registration_type=NORMAL(3P)/RFM(RG)).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'm7n8o9p0q1r2'
down_revision: Union[str, None] = 'l6m7n8o9p0q1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'coupang_vendor_summary_daily',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('summary_date', sa.Date(), nullable=False),
        sa.Column('account_key', sa.String(length=20), nullable=False),
        sa.Column('registration_type', sa.String(length=10), nullable=False),
        sa.Column('gmv', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('units_sold', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_refresh', sa.String(length=30), nullable=True),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'summary_date', 'account_key', 'registration_type',
            name='uq_coupang_vendor_summary_daily',
        ),
    )
    op.create_index(
        op.f('ix_coupang_vendor_summary_daily_summary_date'),
        'coupang_vendor_summary_daily', ['summary_date'], unique=False,
    )
    op.create_index(
        op.f('ix_coupang_vendor_summary_daily_account_key'),
        'coupang_vendor_summary_daily', ['account_key'], unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_coupang_vendor_summary_daily_account_key'),
        table_name='coupang_vendor_summary_daily',
    )
    op.drop_index(
        op.f('ix_coupang_vendor_summary_daily_summary_date'),
        table_name='coupang_vendor_summary_daily',
    )
    op.drop_table('coupang_vendor_summary_daily')
