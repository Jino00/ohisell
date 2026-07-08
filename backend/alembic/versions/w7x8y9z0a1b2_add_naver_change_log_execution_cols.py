"""add naver_change_log execution columns (듀얼모드 스프린트 Phase 5 — execution_harness 골격)

Revision ID: w7x8y9z0a1b2
Revises: v6w7x8y9z0a1
Create Date: 2026-07-08 00:00:00.000000

naver_execution_harness(D-NAO-12 쓰기 유일 초크포인트)가 실행 시도마다(dry-run 포함) 남기는
전건 기록에 필요한 컬럼 2개 추가. naver_change_log는 P0에서 스키마만 만들어졌고(쓰기는
아직 없었음) dry-run 여부를 구분할 필드가 없었다.
  dry_run     : True=실제 네이버 API 쓰기 없이 기록만(D-NAO-5 영구 게이트 — 이번 스프린트는
                항상 True). False는 실제 집행(향후 D-NAO-16 개방 이후만 가능).
  executed_at : 이 harness가 실행을 "시도"한 시각(dry-run도 포함) — changed_at(생성시각)과
                별도로 둔 이유: 제안 생성(created_at)-실행 시도-검증(verify_date)의 3개
                타임라인이 서로 달라 혼동 방지.
SQLite ADD COLUMN 네이티브 지원. dry_run은 server_default로 기존 행(P0~P3에서 만들어졌을
가상의 행)도 안전한 기본값(True)을 갖도록 함 — additive, 기존 row 무영향.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'w7x8y9z0a1b2'
down_revision: Union[str, None] = 'v6w7x8y9z0a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'naver_change_log',
        sa.Column('dry_run', sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column('naver_change_log', sa.Column('executed_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('naver_change_log', 'executed_at')
    op.drop_column('naver_change_log', 'dry_run')
