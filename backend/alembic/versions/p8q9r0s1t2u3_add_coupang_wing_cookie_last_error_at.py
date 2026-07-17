"""add coupang_wing_cookie.last_error_at (Mac 페처 실패 보고)

Revision ID: p8q9r0s1t2u3
Revises: n4o5p6q7r8s9
Create Date: 2026-07-17 17:30:00.000000

additive 1건:

  coupang_wing_cookie.last_error_at: DateTime NULL. last_success_at의 짝 — 마지막 실패
  시각. Mac 페처가 실패를 prod에 보고할 때 찍고, 다음 성공 push가 클리어한다.

★배경(2026-07-17 13:02 실사고): 페처가 브라우저 에러로 죽어도 prod에 알리는 경로가 없어
last_error가 계속 null이었다. 성공에만 시각(last_success_at)이 있고 실패엔 없어서, UI는
"실패"와 "아직 진행 중"을 구분할 수단이 아예 없었다(215초 헛기다린 뒤 "Mac 응답 없음").
기존 행은 NULL = "실패 기록 없음" — 의미가 그대로 맞아 백필 불요.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'p8q9r0s1t2u3'
down_revision: Union[str, None] = 'n4o5p6q7r8s9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'coupang_wing_cookie',
        sa.Column('last_error_at', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('coupang_wing_cookie', 'last_error_at')
