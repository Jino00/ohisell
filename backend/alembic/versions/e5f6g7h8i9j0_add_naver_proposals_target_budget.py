"""add naver_proposals.target_budget + budget_auto_eligible (P1 budget control)

Revision ID: e5f6g7h8i9j0
Revises: d4e5f6g7h8i9
Create Date: 2026-07-13 00:00:00.000000

D-NAO-42-f: 예산 통제 개방(우리 MOP = MOP Pro+ 무제한, 단 이익하한 유지)의 스키마 절반.
budget_up/budget_down 제안이 목표 일예산(dailyBudget)을 rationale 텍스트에만 담지 않고
target_budget 컬럼으로 구조화(target_bid와 완전 병렬 — 실행자는 이 컬럼만 읽는다).
budget_auto_eligible은 라운드 봉투 분류(회당 총 증가액 ≤10만원 자율, Jino "넣어"
2026-07-13) — 위임 켜질 때 소비되는 분류 메타. 둘 다 additive nullable 컬럼 —
기존 행 무영향.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f6g7h8i9j0'
down_revision: Union[str, None] = 'd4e5f6g7h8i9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('naver_proposals', sa.Column('target_budget', sa.Integer(), nullable=True))
    op.add_column('naver_proposals', sa.Column('budget_auto_eligible', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('naver_proposals', 'budget_auto_eligible')
    op.drop_column('naver_proposals', 'target_budget')
