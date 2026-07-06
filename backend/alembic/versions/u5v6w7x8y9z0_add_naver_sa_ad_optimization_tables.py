"""add naver SA ad optimization tables (네이버 SA 광고 최적화 트랙 P0)

Revision ID: u5v6w7x8y9z0
Revises: t4u5v6w7x8y9
Create Date: 2026-07-07 00:00:00.000000

트랙 docs/tracks/active/track_naver-ad-optimization.md / 계획서 §2.
신규 8테이블(전부 additive, 기존 데이터 무영향):
  naver_ad_daily          — 일별 광고성과(P0 필수)
  naver_product_bep       — 상품별 BEP ROAS(P0 필수)
  naver_campaign_settings — 캠페인 관리주체/모드(D-NAO-13)
  naver_hourly_snapshot   — 시간별 스냅샷(빠른 루프)
  naver_change_log        — 변경 전건 기록(스키마만, 쓰기는 P3)
  naver_proposals         — 제안(스키마만, 생성은 P2)
  naver_keyword_candidates— 발굴 후보(스키마만, P4)
  naver_learning_state    — 자율학습 파라미터(스키마만, 환류는 P3/P5)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'u5v6w7x8y9z0'
down_revision: Union[str, None] = 't4u5v6w7x8y9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'naver_ad_daily',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ad_date', sa.Date(), nullable=False),
        sa.Column('campaign_id', sa.String(length=50), nullable=False),
        sa.Column('campaign_type', sa.String(length=20), nullable=False, server_default=''),
        sa.Column('adgroup_id', sa.String(length=50), nullable=False, server_default=''),
        sa.Column('keyword_id', sa.String(length=50), nullable=False, server_default=''),
        sa.Column('imp', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('clk', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cost', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('rank_sum', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('conv_direct_cnt', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('conv_indirect_cnt', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('conv_direct_amt', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('conv_indirect_amt', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('ad_date', 'campaign_id', 'adgroup_id', 'keyword_id', name='uq_naver_ad_daily'),
    )
    op.create_index('ix_naver_ad_daily_ad_date', 'naver_ad_daily', ['ad_date'])
    op.create_index('ix_naver_ad_daily_campaign_id', 'naver_ad_daily', ['campaign_id'])

    op.create_table(
        'naver_product_bep',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('channel_id', sa.Integer(), sa.ForeignKey('channels.id'), nullable=False),
        sa.Column('channel_product_id', sa.String(length=50), nullable=False),
        sa.Column('product_master_id', sa.Integer(), sa.ForeignKey('product_master.id'), nullable=True),
        sa.Column('product_name', sa.String(length=300), nullable=False, server_default=''),
        sa.Column('selling_price', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('cost_price', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('commission_rate', sa.Numeric(6, 4), nullable=False, server_default='0'),
        sa.Column('logistics_cost', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('contribution_margin', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('bep_roas', sa.Numeric(10, 4), nullable=True),
        sa.Column('aggressiveness', sa.String(length=12), nullable=False, server_default='standard'),
        sa.Column('target_roas', sa.Numeric(10, 4), nullable=True),
        sa.Column('has_cost', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('calculated_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('channel_id', 'channel_product_id', name='uq_naver_product_bep'),
    )
    op.create_index('ix_naver_product_bep_channel_id', 'naver_product_bep', ['channel_id'])
    op.create_index('ix_naver_product_bep_channel_product_id', 'naver_product_bep', ['channel_product_id'])

    op.create_table(
        'naver_campaign_settings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('campaign_id', sa.String(length=50), nullable=False, unique=True),
        sa.Column('optimizer', sa.String(length=8), nullable=False, server_default='none'),
        sa.Column('mode', sa.String(length=12), nullable=True),
        sa.Column('target_roas_override', sa.Numeric(10, 4), nullable=True),
        sa.Column('memo', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        'naver_hourly_snapshot',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('snapshot_at', sa.DateTime(), nullable=False),
        sa.Column('ad_date', sa.Date(), nullable=False),
        sa.Column('snapshot_hour', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('campaign_id', sa.String(length=50), nullable=False),
        sa.Column('campaign_type', sa.String(length=20), nullable=False, server_default=''),
        sa.Column('cost', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('clk', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('imp', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('daily_budget', sa.Integer(), nullable=True),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('ad_date', 'campaign_id', 'snapshot_hour', name='uq_naver_hourly_snapshot'),
    )
    op.create_index('ix_naver_hourly_snapshot_snapshot_at', 'naver_hourly_snapshot', ['snapshot_at'])
    op.create_index('ix_naver_hourly_snapshot_ad_date', 'naver_hourly_snapshot', ['ad_date'])
    op.create_index('ix_naver_hourly_snapshot_campaign_id', 'naver_hourly_snapshot', ['campaign_id'])

    op.create_table(
        'naver_change_log',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('changed_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('entity_type', sa.String(length=20), nullable=False, server_default=''),
        sa.Column('entity_id', sa.String(length=50), nullable=False, server_default=''),
        sa.Column('campaign_id', sa.String(length=50), nullable=False, server_default=''),
        sa.Column('action', sa.String(length=40), nullable=False),
        sa.Column('before_value', sa.Text(), nullable=True),
        sa.Column('after_value', sa.Text(), nullable=True),
        sa.Column('rationale', sa.Text(), nullable=True),
        sa.Column('predicted_json', sa.Text(), nullable=True),
        sa.Column('verify_date', sa.Date(), nullable=True),
        sa.Column('actual_json', sa.Text(), nullable=True),
        sa.Column('outcome', sa.String(length=12), nullable=True),
        sa.Column('proposal_id', sa.Integer(), nullable=True),
    )
    op.create_index('ix_naver_change_log_changed_at', 'naver_change_log', ['changed_at'])
    op.create_index('ix_naver_change_log_campaign_id', 'naver_change_log', ['campaign_id'])

    op.create_table(
        'naver_proposals',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('proposal_type', sa.String(length=24), nullable=False),
        sa.Column('target_type', sa.String(length=20), nullable=False, server_default=''),
        sa.Column('target_id', sa.String(length=50), nullable=False, server_default=''),
        sa.Column('campaign_id', sa.String(length=50), nullable=False, server_default=''),
        sa.Column('rationale', sa.Text(), nullable=True),
        sa.Column('expected_effect', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=12), nullable=False, server_default='pending'),
        sa.Column('slack_ts', sa.String(length=30), nullable=True),
        sa.Column('executed_change_log_id', sa.Integer(), nullable=True),
    )
    op.create_index('ix_naver_proposals_created_at', 'naver_proposals', ['created_at'])
    op.create_index('ix_naver_proposals_campaign_id', 'naver_proposals', ['campaign_id'])
    op.create_index('ix_naver_proposals_status', 'naver_proposals', ['status'])

    op.create_table(
        'naver_keyword_candidates',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('keyword', sa.String(length=100), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('monthly_volume', sa.Integer(), nullable=True),
        sa.Column('competition', sa.String(length=10), nullable=True),
        sa.Column('explore_started_at', sa.Date(), nullable=True),
        sa.Column('explore_result_json', sa.Text(), nullable=True),
        sa.Column('verdict', sa.String(length=12), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('keyword', 'source', name='uq_naver_keyword_candidate'),
    )
    op.create_index('ix_naver_keyword_candidates_keyword', 'naver_keyword_candidates', ['keyword'])

    op.create_table(
        'naver_learning_state',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('scope', sa.String(length=16), nullable=False),
        sa.Column('scope_key', sa.String(length=60), nullable=False, server_default=''),
        sa.Column('metric', sa.String(length=30), nullable=False),
        sa.Column('sample_n', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('current_value', sa.Numeric(14, 4), nullable=True),
        sa.Column('confidence', sa.Numeric(6, 4), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('scope', 'scope_key', 'metric', name='uq_naver_learning_state'),
    )


def downgrade() -> None:
    op.drop_table('naver_learning_state')
    op.drop_index('ix_naver_keyword_candidates_keyword', 'naver_keyword_candidates')
    op.drop_table('naver_keyword_candidates')
    op.drop_index('ix_naver_proposals_status', 'naver_proposals')
    op.drop_index('ix_naver_proposals_campaign_id', 'naver_proposals')
    op.drop_index('ix_naver_proposals_created_at', 'naver_proposals')
    op.drop_table('naver_proposals')
    op.drop_index('ix_naver_change_log_campaign_id', 'naver_change_log')
    op.drop_index('ix_naver_change_log_changed_at', 'naver_change_log')
    op.drop_table('naver_change_log')
    op.drop_index('ix_naver_hourly_snapshot_campaign_id', 'naver_hourly_snapshot')
    op.drop_index('ix_naver_hourly_snapshot_ad_date', 'naver_hourly_snapshot')
    op.drop_index('ix_naver_hourly_snapshot_snapshot_at', 'naver_hourly_snapshot')
    op.drop_table('naver_hourly_snapshot')
    op.drop_table('naver_campaign_settings')
    op.drop_index('ix_naver_product_bep_channel_product_id', 'naver_product_bep')
    op.drop_index('ix_naver_product_bep_channel_id', 'naver_product_bep')
    op.drop_table('naver_product_bep')
    op.drop_index('ix_naver_ad_daily_campaign_id', 'naver_ad_daily')
    op.drop_index('ix_naver_ad_daily_ad_date', 'naver_ad_daily')
    op.drop_table('naver_ad_daily')
