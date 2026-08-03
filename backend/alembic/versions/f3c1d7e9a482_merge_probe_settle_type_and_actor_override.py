"""갈래 합류 — 프로브 settle_type 정정(e7b2c9d4a610) + 수정주체 정정 테이블(d4f6b8c0e2a5)

두 세션이 같은 부모(c3e5a7b9d1f4)에서 각각 갈라져 head가 둘이 됐다. 둘은 서로 다른 테이블만
건드리므로 순서 의존이 없다 — DDL 없는 순수 합류점이다.

Revision ID: f3c1d7e9a482
Revises: e7b2c9d4a610, d4f6b8c0e2a5
"""
from typing import Sequence, Union

revision: str = "f3c1d7e9a482"
down_revision: Union[str, Sequence[str], None] = ("e7b2c9d4a610", "d4f6b8c0e2a5")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
