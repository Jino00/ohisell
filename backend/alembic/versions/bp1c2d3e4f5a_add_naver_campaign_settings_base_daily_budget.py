"""add naver_campaign_settings.base_daily_budget (BP 예산 페이싱 base 예산, D-NAO-102)

Revision ID: bp1c2d3e4f5a
Revises: f6a8c0b2d4e6
Create Date: 2026-07-28 00:00:00.000000

docs 트랙 D-NAO-102(BP 레인) — additive nullable:

  naver_campaign_settings.base_daily_budget: Integer NULL. BP(예산 페이싱) 레인이 장중
  증액하기 **직전의 그날 기준 일예산**. 두 역할:
    ① 증액 캡 — 하루 총 증액은 base × 2를 넘지 못한다(같은 날 여러 번 증액해도 복리 금지).
    ② 익일 원복 목표 — 00:05 리셋 잡이 이 값으로 되돌린다.
  기존 행은 NULL(하위호환) — BP 레인이 그날 첫 평가에서 현재 예산으로 시드한다.
  ★사람이 콘솔에서 예산을 바꾸면 다음 날 BP 첫 평가가 그 값을 새 base로 재시드한다
  (BP 증액이 없던 날의 current = 그날의 사람 기준값).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bp1c2d3e4f5a'
down_revision: Union[str, None] = 'f6a8c0b2d4e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'naver_campaign_settings',
        sa.Column('base_daily_budget', sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('naver_campaign_settings', 'base_daily_budget')
