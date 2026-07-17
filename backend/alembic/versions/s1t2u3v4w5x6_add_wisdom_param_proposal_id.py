"""add ops_wisdom_entries.param_proposal_id (D-NAO-54 P4 소비층)

Revision ID: s1t2u3v4w5x6
Revises: r0s1t2u3v4w5
Create Date: 2026-07-18 10:00:00.000000

docs/PLAN_naver-ad-diary-wisdom.md §P4 — additive 1건:

  ops_wisdom_entries.param_proposal_id: 이 지혜가 judge의 param_suggestion을 담고 있어
    param_change 제안(NaverProposal)을 냈다면 그 제안 id를 새긴다. wisdom_apply.
    propose_param_changes의 멱등 키 — 같은 지혜로 param_change 제안을 1회만 생성한다
    (rationale 텍스트 매칭 대신 전용 컬럼 추적). nullable(대다수 지혜는 param_suggestion이
    없어 항상 None). 실행 payload가 아니라 추적용이라 인덱스는 두지 않는다(스캔 대상 소수).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 's1t2u3v4w5x6'
down_revision: Union[str, None] = 'r0s1t2u3v4w5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'ops_wisdom_entries',
        sa.Column('param_proposal_id', sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('ops_wisdom_entries', 'param_proposal_id')
