"""add naver_entity + naver_search_term_daily tables (P2-S1 데이터 기반)

Revision ID: v6w7x8y9z0a1
Revises: u5v6w7x8y9z0
Create Date: 2026-07-07 00:00:00.000000

트랙 docs/tracks/active/track_naver-ad-optimization.md / 계획서 §P2-S1.
신규 2테이블(전부 additive, 기존 데이터 무영향):
  naver_entity            — 캠페인/그룹/키워드 이름·상태 인벤토리
  naver_search_term_daily — 검색어 단위 일별 성과(쇼핑+파워링크 확장검색)
실측 근거: docs/references/22_naver_sa_p2s1_recon.md
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'v6w7x8y9z0a1'
down_revision: Union[str, None] = 'u5v6w7x8y9z0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'naver_entity',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('entity_type', sa.String(length=10), nullable=False),
        sa.Column('entity_id', sa.String(length=50), nullable=False),
        sa.Column('parent_id', sa.String(length=50), nullable=False, server_default=''),
        sa.Column('campaign_id', sa.String(length=50), nullable=False, server_default=''),
        sa.Column('campaign_type', sa.String(length=20), nullable=False, server_default=''),
        sa.Column('name', sa.String(length=300), nullable=False, server_default=''),
        sa.Column('status', sa.String(length=10), nullable=False, server_default='on'),
        sa.Column('bid_amt', sa.Integer(), nullable=True),
        sa.Column('monthly_volume', sa.Integer(), nullable=True),
        sa.Column('competition', sa.String(length=10), nullable=True),
        sa.Column('volume_updated_at', sa.DateTime(), nullable=True),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('entity_type', 'entity_id', name='uq_naver_entity'),
    )
    op.create_index('ix_naver_entity_entity_type', 'naver_entity', ['entity_type'])
    op.create_index('ix_naver_entity_entity_id', 'naver_entity', ['entity_id'])
    op.create_index('ix_naver_entity_campaign_id', 'naver_entity', ['campaign_id'])

    op.create_table(
        'naver_search_term_daily',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ad_date', sa.Date(), nullable=False),
        sa.Column('campaign_id', sa.String(length=50), nullable=False),
        sa.Column('adgroup_id', sa.String(length=50), nullable=False, server_default=''),
        sa.Column('search_term', sa.String(length=300), nullable=False, server_default=''),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('imp', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('clk', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cost', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('rank_sum', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint(
            'ad_date', 'campaign_id', 'adgroup_id', 'search_term', 'source',
            name='uq_naver_search_term_daily',
        ),
    )
    op.create_index('ix_naver_search_term_daily_ad_date', 'naver_search_term_daily', ['ad_date'])
    op.create_index('ix_naver_search_term_daily_campaign_id', 'naver_search_term_daily', ['campaign_id'])


def downgrade() -> None:
    op.drop_index('ix_naver_search_term_daily_campaign_id', 'naver_search_term_daily')
    op.drop_index('ix_naver_search_term_daily_ad_date', 'naver_search_term_daily')
    op.drop_table('naver_search_term_daily')
    op.drop_index('ix_naver_entity_campaign_id', 'naver_entity')
    op.drop_index('ix_naver_entity_entity_id', 'naver_entity')
    op.drop_index('ix_naver_entity_entity_type', 'naver_entity')
    op.drop_table('naver_entity')
