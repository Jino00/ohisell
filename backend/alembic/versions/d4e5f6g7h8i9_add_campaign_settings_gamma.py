"""add naver_campaign_settings.gamma (X3 T2 — GAVE penalty score γ dial)

Revision ID: d4e5f6g7h8i9
Revises: c3d4e5f6g7h8
Create Date: 2026-07-11 12:00:00.000000

GAVE 페널티 점수 S = min{(ROAS/BEP)^γ, 1} × 매출의 γ(공격성 다이얼).
γ↑ BEP 미달 벌칙 강화, γ=0 벌칙 없음. D-NAO-1·2 "이익률=다이얼 하한"의 학술 정식화.
기본값 1.0 — additive nullable 컬럼, 기존 행 무영향.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6g7h8i9'
down_revision: Union[str, None] = 'c3d4e5f6g7h8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('naver_campaign_settings',
                  sa.Column('gamma', sa.Numeric(4, 2), nullable=True))


def downgrade() -> None:
    op.drop_column('naver_campaign_settings', 'gamma')
