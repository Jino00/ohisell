"""add naver expert review tables (예측·전문가 스프린트 E1a — expert_desk, D-NAO-30/31/32)

Revision ID: z0a1b2c3d4e5
Revises: y9z0a1b2c3d4
Create Date: 2026-07-09 00:00:00.000000

E1a expert_desk(전문가 조언자 모드, PLAN §8)를 위한 신규 테이블 2개(additive):

  naver_expert_review_run: 배치 검토 1콜(claude -p) = 1행(run 원장). 재실행은 같은
    as_of라도 새 run(덮어쓰기 아님) — provenance·프롬프트버전·재실행 이력 보존.

  naver_expert_review: 평결 1건(run의 child, run_id FK). proposal_id는
    naver_proposals.id FK(nullable — NULL=하루 총평). checkable_prediction을
    D+7/14에 채점해 로컬 성적표(NaverLearningState 재사용)를 쌓는다.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'z0a1b2c3d4e5'
down_revision: Union[str, None] = 'y9z0a1b2c3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'naver_expert_review_run',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('as_of', sa.Date(), nullable=False),
        sa.Column('model', sa.String(30), nullable=False),
        sa.Column('prompt_version', sa.String(20), nullable=False),
        sa.Column('briefing_hash', sa.String(64), nullable=True),
        sa.Column('raw', sa.Text(), nullable=True),
        sa.Column('usage_json', sa.Text(), nullable=True),
        sa.Column('status', sa.String(16), nullable=False, server_default='ok'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_naver_expert_review_run_as_of', 'naver_expert_review_run', ['as_of'])

    op.create_table(
        'naver_expert_review',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('run_id', sa.Integer(), sa.ForeignKey('naver_expert_review_run.id'), nullable=False),
        sa.Column('as_of', sa.Date(), nullable=False),
        sa.Column('proposal_id', sa.Integer(), sa.ForeignKey('naver_proposals.id'), nullable=True),
        sa.Column('verdict', sa.String(24), nullable=False),  # max: insufficient_evidence=21자 (codex review 발견)
        sa.Column('confidence', sa.Numeric(6, 4), nullable=True),
        sa.Column('reasoning', sa.Text(), nullable=True),
        sa.Column('checkable_prediction', sa.Text(), nullable=True),
        sa.Column('pred_target_type', sa.String(20), nullable=True),
        sa.Column('pred_target_id', sa.String(50), nullable=True),
        sa.Column('pred_metric', sa.String(30), nullable=True),
        sa.Column('pred_direction', sa.String(10), nullable=True),
        sa.Column('verify_date', sa.Date(), nullable=True),
        sa.Column('outcome', sa.String(12), nullable=True),
        sa.Column('source', sa.String(10), nullable=False, server_default='local'),
    )
    op.create_index('ix_naver_expert_review_run_id', 'naver_expert_review', ['run_id'])
    op.create_index('ix_naver_expert_review_as_of', 'naver_expert_review', ['as_of'])
    op.create_index('ix_naver_expert_review_verify_date', 'naver_expert_review', ['verify_date'])


def downgrade() -> None:
    op.drop_index('ix_naver_expert_review_verify_date', table_name='naver_expert_review')
    op.drop_index('ix_naver_expert_review_as_of', table_name='naver_expert_review')
    op.drop_index('ix_naver_expert_review_run_id', table_name='naver_expert_review')
    op.drop_table('naver_expert_review')
    op.drop_index('ix_naver_expert_review_run_as_of', table_name='naver_expert_review_run')
    op.drop_table('naver_expert_review_run')
