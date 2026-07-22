"""add naver_search_term_exclusion (파워링크 검색어 자동 제외 in-out 상태기계, 스프린트 PX)

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-07-22 13:00:00.000000

트랙 docs/tracks/active/track_naver-ad-optimization.md — 스프린트 PX(파워링크 검색어 자동 제외 +
in-out 재심사 루프, docs/PLAN_naver-ad-powerlink-autoexclude.md §2). 파워링크 손실 검색어를
성과(비용) 기반으로 자동 제외하고, 주기 재심사로 오컷을 자가 교정하는 상태기계 원장.

CREATE TABLE만(기존 행 무영향, 회귀 0). Unique(adgroup_id, search_term).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e0f1a2b3c4d5'
down_revision: Union[str, None] = 'd9e0f1a2b3c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'naver_search_term_exclusion',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('campaign_id', sa.String(50), nullable=False, server_default=''),
        sa.Column('adgroup_id', sa.String(50), nullable=False, server_default=''),
        sa.Column('search_term', sa.String(300), nullable=False, server_default=''),
        sa.Column('restrict_kwd_id', sa.String(50), nullable=True),
        sa.Column('status', sa.String(12), nullable=False, server_default='excluded'),
        sa.Column('cycle', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('excluded_at', sa.DateTime(), nullable=False),
        sa.Column('last_transition_at', sa.DateTime(), nullable=False),
        sa.Column('next_review_at', sa.Date(), nullable=True),
        sa.Column('probation_until', sa.Date(), nullable=True),
        sa.Column('cost_at_exclusion', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('adgroup_id', 'search_term', name='uq_naver_search_term_exclusion'),
    )
    op.create_index('ix_naver_search_term_exclusion_campaign_id', 'naver_search_term_exclusion', ['campaign_id'])
    op.create_index('ix_naver_search_term_exclusion_status', 'naver_search_term_exclusion', ['status'])
    op.create_index('ix_naver_search_term_exclusion_next_review_at', 'naver_search_term_exclusion', ['next_review_at'])


def downgrade() -> None:
    op.drop_index('ix_naver_search_term_exclusion_next_review_at', table_name='naver_search_term_exclusion')
    op.drop_index('ix_naver_search_term_exclusion_status', table_name='naver_search_term_exclusion')
    op.drop_index('ix_naver_search_term_exclusion_campaign_id', table_name='naver_search_term_exclusion')
    op.drop_table('naver_search_term_exclusion')
