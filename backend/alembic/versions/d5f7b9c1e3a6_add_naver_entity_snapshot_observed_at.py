"""add entity_observed_at·p3_observed_at to naver_entity_snapshot (D-NAO-93)

Revision ID: d5f7b9c1e3a6
Revises: c4e6a8b0d2f4
Create Date: 2026-07-27

bm_diff의 change_log 대조창 상한은 지금까지 synced_at(스냅샷 **복사** 시각) 하나였다. 그러나
필드마다 실제 관측 시각이 다르다 — 입찰·상태·키워드집계는 앞선 entity_sync가 본 값(실측상
D-1 07:35 관측)이고, 예산·확장검색은 스냅샷 시작 몇 분 뒤 P3 GET 값이다. 창이 실관측과
어긋난 만큼 미탐(우연히 값이 겹치는 대행사 조작을 echo로 삭제)·오탐(우리 쓰기를 대행사
조작으로 기록)이 남는다. 관측 시각을 필드 출처별로 저장해 op_type별 창을 맞춘다.

  entity_observed_at : bid_amt/status/name/keyword_* 관측 시각(= NaverEntity.synced_at 복사)
  p3_observed_at     : daily_budget/extended_search 관측 시각(P3 GET 직후 kst_now)

둘 다 nullable — 기존 행은 NULL이고 bm_diff가 synced_at으로 폴백하므로 동작 불변(backfill
불필요). 값은 전부 KST naive(★server_default 없음 — func.now()는 UTC라 시간 비교가 깨진다).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd5f7b9c1e3a6'
down_revision: Union[str, None] = 'c4e6a8b0d2f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'naver_entity_snapshot',
        sa.Column('entity_observed_at', sa.DateTime(), nullable=True),
    )
    op.add_column(
        'naver_entity_snapshot',
        sa.Column('p3_observed_at', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table('naver_entity_snapshot') as batch:
        batch.drop_column('p3_observed_at')
        batch.drop_column('entity_observed_at')
