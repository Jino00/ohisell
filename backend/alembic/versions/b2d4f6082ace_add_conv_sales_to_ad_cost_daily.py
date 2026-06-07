"""add conv_sales to coupang_ad_cost_daily (report/SALES 광고전환매출)

Revision ID: b2d4f6082ace
Revises: a1c3e5079bdf
Create Date: 2026-06-07

페처가 report/SALES로 받는 날짜별 광고전환매출(AD_ATTRIBUTED_SALES). ROAS 산출용.

★체인 구멍 보강: coupang_ad_cost_daily는 88f0ebf에서 모델만 추가되고 생성 마이그레이션이
없었다(prod는 out-of-band 생성, 신규/로컬 DB엔 부재). 이 마이그레이션을 조건부로 만들어
- 테이블 부재(신규/로컬) → conv_sales 포함 전체 생성
- 테이블 존재(prod) → conv_sales 컬럼만 추가
둘 다 안전하게 처리한다. NOT NULL default 0 → 기존 행/동작 불변.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = 'b2d4f6082ace'
down_revision: Union[str, None] = 'a1c3e5079bdf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if 'coupang_ad_cost_daily' not in insp.get_table_names():
        op.create_table(
            'coupang_ad_cost_daily',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('cost_date', sa.Date(), nullable=False),
            sa.Column('vendor_id', sa.String(length=30), nullable=False),
            sa.Column('day_cost', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('conv_sales', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('month_cost', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now()),
            sa.UniqueConstraint('cost_date', 'vendor_id', name='uq_coupang_ad_cost_daily'),
        )
        op.create_index('ix_coupang_ad_cost_daily_cost_date',
                        'coupang_ad_cost_daily', ['cost_date'])
        return
    cols = [c['name'] for c in insp.get_columns('coupang_ad_cost_daily')]
    if 'conv_sales' not in cols:
        op.add_column(
            'coupang_ad_cost_daily',
            sa.Column('conv_sales', sa.Integer(), nullable=False, server_default='0'),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = [c['name'] for c in insp.get_columns('coupang_ad_cost_daily')]
    if 'conv_sales' in cols:
        with op.batch_alter_table('coupang_ad_cost_daily') as batch:
            batch.drop_column('conv_sales')
