"""merge — 병렬 세션 두 갈래를 단일 head로 합류 (DDL 없음)

두 마이그레이션이 같은 부모(e5f7a9c1b3d5)에서 동시에 갈라졌다:
  · a7b9c1d3e5f7 — naver_entity.status_reason (D-NAO-97, 다른 세션)
  · f6a8c0e2b4d6 — orders 배송 구분 컬럼 (2026-07-28, 이 세션)
서로 다른 테이블이라 충돌은 없고, 순서 의존도 없다. 이 리비전은 **DDL을 하지 않고**
체인만 다시 하나로 모은다(tests/test_alembic_revision_integrity.py의 단일 head 규칙 복원).

★prod는 두 갈래를 이미 각각 적용한 상태(alembic_version 2행)라, 여기서 upgrade하면
alembic_version이 이 리비전 1행으로 정리된다.

Revision ID: a1c3e5f7b9d1
Revises: a7b9c1d3e5f7, f6a8c0e2b4d6
"""
from typing import Sequence, Union

revision: str = 'a1c3e5f7b9d1'
down_revision: Union[str, Sequence[str], None] = ('a7b9c1d3e5f7', 'f6a8c0e2b4d6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
