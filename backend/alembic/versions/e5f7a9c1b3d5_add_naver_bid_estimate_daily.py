"""add naver_bid_estimate_daily (CS 스프린트 SA2 — 쇼핑 소재 시장가 사다리 일별 축적)

CREATE TABLE만(기존 행·기존 소비자 무영향, 회귀 0). naver_ad_daily 스키마는 건드리지 않는다 —
새 grain(date, ad_id, device, position)은 새 테이블로 간다.

position: 1~4 = 평균 노출순위별 필요 입찰가(/npla-estimate/average-position-bid/id).
          0    = 최소노출입찰가(/npla-estimate/exposure-minimum-bid/id)를 같은 grain에 담기 위한
                 가상 position(실제 API position 아님).

Revision ID: e5f7a9c1b3d5
Revises: c4e6a8b0d2f4
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e5f7a9c1b3d5'
down_revision: Union[str, None] = 'c4e6a8b0d2f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'naver_bid_estimate_daily',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('ad_id', sa.String(50), nullable=False),
        sa.Column('adgroup_id', sa.String(50), nullable=False, server_default=''),
        sa.Column('campaign_id', sa.String(50), nullable=False, server_default=''),
        sa.Column('device', sa.String(8), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('bid', sa.Integer(), nullable=False),
        sa.Column('is_floor', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('collected_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('date', 'ad_id', 'device', 'position',
                            name='uq_naver_bid_estimate_daily'),
    )
    op.create_index('ix_naver_bid_estimate_daily_date', 'naver_bid_estimate_daily', ['date'])
    op.create_index('ix_naver_bid_estimate_daily_ad_id', 'naver_bid_estimate_daily', ['ad_id'])
    op.create_index('ix_naver_bid_estimate_daily_adgroup_id', 'naver_bid_estimate_daily', ['adgroup_id'])
    op.create_index('ix_naver_bid_estimate_daily_campaign_id', 'naver_bid_estimate_daily', ['campaign_id'])


def downgrade() -> None:
    op.drop_index('ix_naver_bid_estimate_daily_campaign_id', table_name='naver_bid_estimate_daily')
    op.drop_index('ix_naver_bid_estimate_daily_adgroup_id', table_name='naver_bid_estimate_daily')
    op.drop_index('ix_naver_bid_estimate_daily_ad_id', table_name='naver_bid_estimate_daily')
    op.drop_index('ix_naver_bid_estimate_daily_date', table_name='naver_bid_estimate_daily')
    op.drop_table('naver_bid_estimate_daily')
