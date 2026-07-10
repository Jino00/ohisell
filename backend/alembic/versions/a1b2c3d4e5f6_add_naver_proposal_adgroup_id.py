"""add naver_proposals.adgroup_id (X1a T3 — 실쓰기 개방: 제외키워드)

Revision ID: a1b2c3d4e5f6
Revises: z0a1b2c3d4e5
Create Date: 2026-07-10 00:00:00.000000

restricted-keywords 쓰기 API는 adgroupId가 필수인데(ref 27 §8-1) negative_keyword 제안은
target_type="search_term", target_id=검색어, campaign_id만 저장하고 있었다. 실행 시점에
naver_search_term_daily로 재해석(resolve)하는 대안보다 제안 생성 시점에 확정 저장하는 쪽이
단순·결정적(ref 27 §8-1 권고 그대로). 다른 제안 유형(bid/budget 등)은 NULL — 실행 시
adgroup_id가 없으면 harness가 MissingExecutionTargetError로 fail-closed.
SQLite ADD COLUMN 네이티브 지원, nullable=True라 기존 행 무영향(additive).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'z0a1b2c3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('naver_proposals', sa.Column('adgroup_id', sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column('naver_proposals', 'adgroup_id')
