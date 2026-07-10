"""add naver_proposals.target_bid + target_lock (X1b T1)

Revision ID: c3d4e5f6g7h8
Revises: b2c3d4e5f6g7
Create Date: 2026-07-10 21:00:00.000000

D-NAO-38 갭①·② 해소: bid_up/bid_down/growth_bid_up 제안이 추천입찰가를 rationale
텍스트에만 담던 것(실행자 텍스트 파싱 금지 원칙 위반 소지)을 target_bid 컬럼으로
구조화. pause/resume 신규 제안유형의 목표 userLock은 target_lock 컬럼(true=정지,
false=재개). 둘 다 additive nullable 컬럼 — 기존 행 무영향.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6g7h8'
down_revision: Union[str, None] = 'b2c3d4e5f6g7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('naver_proposals', sa.Column('target_bid', sa.Integer(), nullable=True))
    op.add_column('naver_proposals', sa.Column('target_lock', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('naver_proposals', 'target_lock')
    op.drop_column('naver_proposals', 'target_bid')
