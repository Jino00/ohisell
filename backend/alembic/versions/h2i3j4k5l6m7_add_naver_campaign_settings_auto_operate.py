"""add naver_campaign_settings.auto_operate (자동 운영 스위치, D-NAO-49)

Revision ID: h2i3j4k5l6m7
Revises: g7h8i9j0k1l2
Create Date: 2026-07-17 00:00:00.000000

docs/PLAN_naver-ad-auto-operator.md §2 — additive:

  naver_campaign_settings.auto_operate: Boolean NOT NULL default False. 자동 운영
  (auto_operator 일/시간당 레인) 대상 스위치. 배포 시 04(cmp-a001-02-000000008514959)만
  True로 수동 시드(이 마이그레이션은 컬럼만 추가 — 시드는 별도 운영 작업). 킬스위치 =
  이 플래그 OFF(Jino "04 자동운영 중지" → 로컬 루틴/콘솔이 즉시 UPDATE).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'h2i3j4k5l6m7'
down_revision: Union[str, None] = 'g7h8i9j0k1l2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'naver_campaign_settings',
        sa.Column('auto_operate', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column('naver_campaign_settings', 'auto_operate')
