"""add naver_entity.status_reason (D-NAO-97 — 상태 사유 저장, 외부변경 오탐 수정)

ADD COLUMN 하나(nullable). 기존 행은 NULL로 남고 다음 entity_sync가 채운다 — 백필 불필요.
NULL은 `_is_system_reason()`에서 False(=시스템 사유 아님)로 읽히므로, 마이그레이션 직후
첫 sync가 유령 이벤트를 쏟지 않는다.

왜 필요한가: naver_entity.status는 사람의 On/Off 스위치(userLock)만 담는다. "켜져 있는데
안 도는 이유"(일예산 소진·검수 중·상위 정지)는 지금까지 어디에도 저장되지 않아, 캠페인 행에서
status=PAUSED를 userLock으로 역추론하다 예산 소진을 사람 조작으로 오분류했다(change_log id 847).

Revision ID: a7b9c1d3e5f7
Revises: e5f7a9c1b3d5
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a7b9c1d3e5f7'
down_revision: Union[str, None] = 'e5f7a9c1b3d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('naver_entity', sa.Column('status_reason', sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column('naver_entity', 'status_reason')
