"""add ops_wisdom_candidates/entries (D-NAO-54 P3 승격·망각층)

Revision ID: r0s1t2u3v4w5
Revises: q9r0s1t2u3v4
Create Date: 2026-07-18 09:00:00.000000

docs/PLAN_naver-ad-diary-wisdom.md §P3 — additive 2건:

  ops_wisdom_candidates: 결과 기입된 diary 행에서 (캠페인×액션×환경) 조건 시그니처로 뽑은 반복
    패턴 후보. 결과 방향은 시그니처가 아니라 good_count/bad_count로 세고(occurrences=good+bad),
    TTL 14일 숙성 or occurrences≥3이면 독립 LLM 판사가 승률·표본까지 보고 promote/reject. 미승격
    후보는 Ebbinghaus 망각(soft-hide=status hidden, 시그니처 재등장 시 부활), 승격분은 불망각.
  ops_wisdom_entries: 승격된 재사용 판단원칙(지혜). writer_sa가 promoted 후보에서 1:1 생성하고
    Jino 보고는 정보성 NaverProposal(wisdom_promoted)로 낸다. 지혜는 감쇠하지 않는다.

  created_at은 server_default=func.now()라 UTC로 찍힌다([[sqlite-server-default-now-is-utc]]
  교훈, OpsDiaryEntry.created_at 동일 관례). first_seen_at/last_seen_at/promoted_at은 SA가
  KST now로 명시 기입한다(server_default 없음 — 감쇠 Δt 계산의 진실 소스라 UTC 혼입 금지).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'r0s1t2u3v4w5'
down_revision: Union[str, None] = 'q9r0s1t2u3v4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ops_wisdom_candidates',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('signature', sa.String(200), nullable=False),
        sa.Column('campaign_id', sa.String(50), nullable=False, server_default=''),
        sa.Column('action', sa.String(40), nullable=True),
        sa.Column('env_bucket_json', sa.Text(), nullable=True),
        sa.Column('observation', sa.Text(), nullable=True),
        sa.Column('occurrences', sa.Integer(), nullable=False, server_default='1'),  # = good_count+bad_count
        sa.Column('good_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('bad_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('first_seen_at', sa.DateTime(), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(), nullable=True),
        sa.Column('source_entry_ids_json', sa.Text(), nullable=True),
        sa.Column('status', sa.String(12), nullable=False, server_default='pending'),
        sa.Column('judge_verdict_json', sa.Text(), nullable=True),
        sa.Column('importance', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('strength', sa.Float(), nullable=False, server_default='7.0'),
    )
    op.create_index(
        'ux_ops_wisdom_candidates_signature', 'ops_wisdom_candidates', ['signature'], unique=True
    )
    op.create_index('ix_ops_wisdom_candidates_status', 'ops_wisdom_candidates', ['status'])

    op.create_table(
        'ops_wisdom_entries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('wisdom_text', sa.Text(), nullable=False),
        sa.Column('source_candidate_id', sa.Integer(), nullable=False),
        sa.Column('judge_rationale', sa.Text(), nullable=True),
        sa.Column('status', sa.String(8), nullable=False, server_default='active'),
        sa.Column('promoted_at', sa.DateTime(), nullable=True),
    )
    op.create_index(
        'ux_ops_wisdom_entries_source_candidate', 'ops_wisdom_entries', ['source_candidate_id'],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index('ux_ops_wisdom_entries_source_candidate', table_name='ops_wisdom_entries')
    op.drop_table('ops_wisdom_entries')
    op.drop_index('ix_ops_wisdom_candidates_status', table_name='ops_wisdom_candidates')
    op.drop_index('ux_ops_wisdom_candidates_signature', table_name='ops_wisdom_candidates')
    op.drop_table('ops_wisdom_candidates')
