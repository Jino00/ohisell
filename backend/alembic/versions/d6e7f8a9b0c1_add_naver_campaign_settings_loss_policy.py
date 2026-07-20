"""add naver_campaign_settings.loss_policy (D-NAO-65 UI1)

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-07-20 16:00:00.000000

트랙 docs/tracks/active/track_naver-ad-optimization.md D-NAO-65 (스프린트 UI). UI1
캠페인별 loss 대응 정책 스위치의 데이터층. loss_policy 컬럼 1개만 추가한다:

  loss_policy — NULL/'leash'=기본(고삐-일일리셋, DL 스프린트 불변) /
                'stoploss_pause'=종전 하드 정지로 회귀 (String 16, nullable)

★신규 테이블 0·additive nullable(기존 행 무영향, backfill 불필요 — 전역 기본값이 여전히
고삐라 NULL이 곧 leash로 해석된다, LESSONS #14 additive). 쓰기 경로는 Router
PUT /campaign-settings/loss-policy 하나뿐(§0 금지선).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd6e7f8a9b0c1'
down_revision: Union[str, None] = 'c5d6e7f8a9b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('naver_campaign_settings', sa.Column('loss_policy', sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column('naver_campaign_settings', 'loss_policy')
