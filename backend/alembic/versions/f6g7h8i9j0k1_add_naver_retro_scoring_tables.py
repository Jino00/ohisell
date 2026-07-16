"""add naver retro scoring tables (상설 소급 채점, D-NAO-45)

Revision ID: f6g7h8i9j0k1
Revises: e5f6g7h8i9j0
Create Date: 2026-07-16 00:00:00.000000

PLAN_naver-ad-retro-scoring.md §3 — 신규 테이블 2개(additive):

  naver_retro_signal: 진단 보드를 as-of 시점으로 매일 리플레이해(retro_snapshotter)
    지목된 타깃마다 1행 스냅샷(cf/bep/target as-of 고정 렌즈) → D+3/D+7에
    naver_ad_daily 사후 실적으로 방향 채점(retro_scorer, correct/gray/wrong/no_spend).

  naver_retro_pacing_score: trigger_pacing 경보(저속·과속) 1건당 1행 — 그날 캠페인
    sentinel 최종 소진과 대조해 채점(retro_pacing_scorer).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6g7h8i9j0k1'
down_revision: Union[str, None] = 'e5f6g7h8i9j0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'naver_retro_signal',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('asof_date', sa.Date(), nullable=False),
        sa.Column('board', sa.String(32), nullable=False),
        sa.Column('direction', sa.String(8), nullable=False),
        sa.Column('grain', sa.String(8), nullable=False),
        sa.Column('target_id', sa.String(50), nullable=False),
        sa.Column('campaign_id', sa.String(50), nullable=False),
        sa.Column('cf_asof', sa.Float(), nullable=False),
        sa.Column('bep_asof', sa.Float(), nullable=False),
        sa.Column('target_asof', sa.Float(), nullable=False),
        sa.Column('cost_asof', sa.Integer(), nullable=False),
        sa.Column('roas_c_asof', sa.Float(), nullable=True),
        sa.Column('verdict_d3', sa.String(10), nullable=True),
        sa.Column('scored_d3_at', sa.DateTime(), nullable=True),
        sa.Column('cost_post3', sa.Integer(), nullable=True),
        sa.Column('conv_post3', sa.Integer(), nullable=True),
        sa.Column('roas_c_post3', sa.Float(), nullable=True),
        sa.Column('bleed_post3', sa.Integer(), nullable=True),
        sa.Column('verdict_d7', sa.String(10), nullable=True),
        sa.Column('scored_d7_at', sa.DateTime(), nullable=True),
        sa.Column('cost_post7', sa.Integer(), nullable=True),
        sa.Column('conv_post7', sa.Integer(), nullable=True),
        sa.Column('roas_c_post7', sa.Float(), nullable=True),
        sa.Column('bleed_post7', sa.Integer(), nullable=True),
        sa.UniqueConstraint('asof_date', 'board', 'target_id', name='uq_naver_retro_signal'),
    )
    op.create_index('ix_naver_retro_signal_asof_date', 'naver_retro_signal', ['asof_date'])
    op.create_index('ix_naver_retro_signal_campaign_id', 'naver_retro_signal', ['campaign_id'])

    op.create_table(
        'naver_retro_pacing_score',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('proposal_id', sa.Integer(), nullable=False),
        sa.Column('alert_date', sa.Date(), nullable=True),
        sa.Column('campaign_id', sa.String(50), nullable=False),
        sa.Column('kind', sa.String(8), nullable=True),
        sa.Column('alert_hour', sa.Integer(), nullable=True),
        sa.Column('spent', sa.Integer(), nullable=True),
        sa.Column('budget', sa.Integer(), nullable=True),
        sa.Column('multiple', sa.Float(), nullable=True),
        sa.Column('final_cost', sa.Integer(), nullable=True),
        sa.Column('final_ratio', sa.Float(), nullable=True),
        sa.Column('verdict', sa.String(24), nullable=False),
        sa.Column('scored_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('proposal_id', name='uq_naver_retro_pacing_score_proposal'),
    )
    op.create_index('ix_naver_retro_pacing_score_alert_date', 'naver_retro_pacing_score', ['alert_date'])


def downgrade() -> None:
    op.drop_index('ix_naver_retro_pacing_score_alert_date', table_name='naver_retro_pacing_score')
    op.drop_table('naver_retro_pacing_score')
    op.drop_index('ix_naver_retro_signal_campaign_id', table_name='naver_retro_signal')
    op.drop_index('ix_naver_retro_signal_asof_date', table_name='naver_retro_signal')
    op.drop_table('naver_retro_signal')
