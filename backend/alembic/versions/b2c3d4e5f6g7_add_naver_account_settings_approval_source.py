"""add naver_account_settings + naver_proposals.approval_source (X1a T5 — E2 위임 스위치)

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-10 12:00:00.000000

D-NAO-25 부분 게이트 구현: 계정 단위 설정 KV 테이블(naver_account_settings, key 예
'expert_delegated_types' — Jino가 명시 위임한 proposal_type 집합을 JSON 배열로 저장) +
naver_proposals.approval_source(콘솔 사람 승인 'console' vs 위임 자동승인 'delegation' 감사용).
둘 다 additive(신규 테이블 + nullable 컬럼) — 기존 행 무영향.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6g7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'naver_account_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=40), nullable=False),
        sa.Column('value_json', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key'),
    )
    op.add_column('naver_proposals', sa.Column('approval_source', sa.String(length=12), nullable=True))


def downgrade() -> None:
    op.drop_column('naver_proposals', 'approval_source')
    op.drop_table('naver_account_settings')
