"""add naver_entity_snapshot (BM 벤치마크 레이어 SA-1, D-NAO-78)

Revision ID: b7c8d9e0f1a2
Revises: f2a3b4c5d6e7
Create Date: 2026-07-22 08:00:00.000000

트랙 docs/tracks/active/track_naver-ad-optimization.md — 스프린트 BM(벤치마크 학습 레이어,
docs/PLAN_naver-ad-bm-layer.md §2 Phase 1). D-NAO-78: 판단·학습이 ours 4개만 소비하던 갭 →
계정 전체 45캠페인(대행사 포함)의 구조·성과·매일 변화를 관찰·학습해 우리 캠페인에 반영.

naver_entity는 upsert라 '현재 상태'만 남아 역사가 없다. 이 테이블이 매일 07:37(entity_sync
07:35 직후) 캠페인·그룹 grain 구조를 날짜별 스냅샷한다. 키워드 grain은 저장 안 함(3,300만행/년
회피) — 그룹 행에 키워드 집계(keyword_count/avg_bid)만. 관찰 전용(네이버 API 쓰기 0).

CREATE TABLE만(기존 행 무영향, 회귀 0). Phase 1은 name/status/optimizer/bid_amt/keyword_count/
keyword_avg_bid만 채우고, 예산·확장검색·제외수·소재수 차원은 nullable로 남겨 P3에서 채운다.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'naver_entity_snapshot',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('snapshot_date', sa.Date(), nullable=False),
        sa.Column('entity_type', sa.String(10), nullable=False),
        sa.Column('entity_id', sa.String(50), nullable=False),
        sa.Column('parent_id', sa.String(50), nullable=False, server_default=''),
        sa.Column('campaign_id', sa.String(50), nullable=False, server_default=''),
        sa.Column('campaign_type', sa.String(20), nullable=False, server_default=''),
        sa.Column('optimizer', sa.String(8), nullable=False, server_default='none'),
        sa.Column('name', sa.String(300), nullable=False, server_default=''),
        sa.Column('status', sa.String(10), nullable=False, server_default='on'),
        sa.Column('daily_budget', sa.Integer(), nullable=True),
        sa.Column('bid_amt', sa.Integer(), nullable=True),
        sa.Column('extended_search', sa.Boolean(), nullable=True),
        sa.Column('keyword_count', sa.Integer(), nullable=True),
        sa.Column('keyword_avg_bid', sa.Integer(), nullable=True),
        sa.Column('negative_kw_count', sa.Integer(), nullable=True),
        sa.Column('ad_count', sa.Integer(), nullable=True),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('snapshot_date', 'entity_type', 'entity_id', name='uq_naver_entity_snapshot'),
    )
    op.create_index('ix_naver_entity_snapshot_snapshot_date', 'naver_entity_snapshot', ['snapshot_date'])
    op.create_index('ix_naver_entity_snapshot_entity_id', 'naver_entity_snapshot', ['entity_id'])
    op.create_index('ix_naver_entity_snapshot_campaign_id', 'naver_entity_snapshot', ['campaign_id'])


def downgrade() -> None:
    op.drop_index('ix_naver_entity_snapshot_campaign_id', table_name='naver_entity_snapshot')
    op.drop_index('ix_naver_entity_snapshot_entity_id', table_name='naver_entity_snapshot')
    op.drop_index('ix_naver_entity_snapshot_snapshot_date', table_name='naver_entity_snapshot')
    op.drop_table('naver_entity_snapshot')
