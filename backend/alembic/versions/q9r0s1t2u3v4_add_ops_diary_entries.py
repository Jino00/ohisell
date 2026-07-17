"""add ops_diary_entries (D-NAO-54 P1 운영 일기 기록층)

Revision ID: q9r0s1t2u3v4
Revises: p8q9r0s1t2u3
Create Date: 2026-07-17 20:00:00.000000

docs/PLAN_naver-ad-diary-wisdom.md §P1 — additive 1건:

  ops_diary_entries: "무엇을 했나(집행/차단/거부/킬스위치)"를 그 순간의 환경 스냅샷
    (요일·휴일·계절·아이폰 출시 오프셋·소진 페이싱·avg_rank)과 함께 남기는 기록층.
    P2 해석층이 outcome_json에 D+1/D+7 결과를 소급 기입한다. 신규 테이블만 추가하므로
    기존 데이터 백필 불요.

  created_at은 server_default=func.now()라 UTC로 찍힌다([[sqlite-server-default-now-is-utc]]
  교훈, NaverChangeLog.changed_at 동일 관례) — 환경 스냅샷 weekday/season(KST)과는 별개 필드.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'q9r0s1t2u3v4'
down_revision: Union[str, None] = 'p8q9r0s1t2u3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ops_diary_entries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('event_type', sa.String(16), nullable=False),
        sa.Column('campaign_id', sa.String(50), nullable=False, server_default=''),
        sa.Column('adgroup_id', sa.String(50), nullable=True),
        sa.Column('target_type', sa.String(20), nullable=True),
        sa.Column('target_id', sa.String(50), nullable=True),
        sa.Column('actor', sa.String(12), nullable=False, server_default='system'),
        sa.Column('action', sa.String(40), nullable=True),
        sa.Column('before_value', sa.Text(), nullable=True),
        sa.Column('after_value', sa.Text(), nullable=True),
        sa.Column('rationale', sa.Text(), nullable=True),
        sa.Column('weekday', sa.Integer(), nullable=True),
        sa.Column('is_kr_holiday', sa.Boolean(), nullable=True),
        sa.Column('season', sa.String(6), nullable=True),
        sa.Column('iphone_launch_offset_days', sa.Integer(), nullable=True),
        sa.Column('spend_pacing_pct', sa.Float(), nullable=True),
        sa.Column('avg_rank', sa.Float(), nullable=True),
        sa.Column('outcome_json', sa.Text(), nullable=True),
        sa.Column('source_ref', sa.Integer(), nullable=True),
    )
    op.create_index('ix_ops_diary_entries_created_at', 'ops_diary_entries', ['created_at'])
    op.create_index('ix_ops_diary_entries_campaign_id', 'ops_diary_entries', ['campaign_id'])
    op.create_index(
        'ix_ops_diary_entries_campaign_created', 'ops_diary_entries', ['campaign_id', 'created_at']
    )


def downgrade() -> None:
    op.drop_index('ix_ops_diary_entries_campaign_created', table_name='ops_diary_entries')
    op.drop_index('ix_ops_diary_entries_campaign_id', table_name='ops_diary_entries')
    op.drop_index('ix_ops_diary_entries_created_at', table_name='ops_diary_entries')
    op.drop_table('ops_diary_entries')
