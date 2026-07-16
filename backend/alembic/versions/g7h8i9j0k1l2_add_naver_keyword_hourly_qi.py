"""add naver keyword hourly + avg_rank/qi_grade (키워드 시간별 축적, D-NAO-46②)

Revision ID: g7h8i9j0k1l2
Revises: f6g7h8i9j0k1
Create Date: 2026-07-17 00:00:00.000000

PLAN_naver-ad-keyword-hourly-accrual.md §3 — additive:

  naver_keyword_hourly: 키워드/쇼핑그룹 grain 시간별 imp/clk/cost/avgRnk 영구 축적
    (일 1회 D-1 hh24 스윕, keyword_hourly_sweep.py). hh24 상세는 네이버가 최근 7일만
    보존해 배포가 늦은 만큼 시간대별 순위 이력이 영구 소실된다.

  naver_hourly_snapshot.avg_rank: 캠페인 grain 당일 누적 순위(빠른 루프 원료).
  naver_entity.qi_grade: 품질지수 1~7(entity_sync 일일 열거에 무상 편승).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'g7h8i9j0k1l2'
down_revision: Union[str, None] = 'f6g7h8i9j0k1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'naver_keyword_hourly',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ad_date', sa.Date(), nullable=False),
        sa.Column('hour', sa.Integer(), nullable=False),
        sa.Column('entity_type', sa.String(10), nullable=False),
        sa.Column('entity_id', sa.String(50), nullable=False),
        sa.Column('adgroup_id', sa.String(50), nullable=False, server_default=''),
        sa.Column('campaign_id', sa.String(50), nullable=False),
        sa.Column('campaign_type', sa.String(20), nullable=False, server_default=''),
        sa.Column('imp', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('clk', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cost', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('avg_rank', sa.Numeric(6, 2), nullable=True),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('ad_date', 'entity_id', 'hour', name='uq_naver_keyword_hourly'),
    )
    op.create_index('ix_naver_keyword_hourly_ad_date', 'naver_keyword_hourly', ['ad_date'])
    op.create_index('ix_naver_keyword_hourly_entity_id', 'naver_keyword_hourly', ['entity_id'])
    op.create_index('ix_naver_keyword_hourly_campaign_id', 'naver_keyword_hourly', ['campaign_id'])

    op.add_column('naver_hourly_snapshot', sa.Column('avg_rank', sa.Numeric(6, 2), nullable=True))
    op.add_column('naver_entity', sa.Column('qi_grade', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('naver_entity', 'qi_grade')
    op.drop_column('naver_hourly_snapshot', 'avg_rank')

    op.drop_index('ix_naver_keyword_hourly_campaign_id', table_name='naver_keyword_hourly')
    op.drop_index('ix_naver_keyword_hourly_entity_id', table_name='naver_keyword_hourly')
    op.drop_index('ix_naver_keyword_hourly_ad_date', table_name='naver_keyword_hourly')
    op.drop_table('naver_keyword_hourly')
