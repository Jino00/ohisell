"""add naver_adgroup_product ad-level bid columns (D-NAO-65 B1)

Revision ID: c5d6e7f8a9b0
Revises: b48c2f3bc0a3
Create Date: 2026-07-20 10:00:00.000000

트랙 docs/tracks/active/track_naver-ad-optimization.md D-NAO-65 (소재-레벨 실효입찰
인식·제어, 스프린트 B). B1 인식(읽기+저장) 데이터층: 각 SHOPPING_PRODUCT_AD 소재의
Ad.adAttr({"bidAmt":N,"useGroupBidAmt":bool})·userLock을 naver_adgroup_product에
additive nullable 컬럼으로 적재한다. 전부 additive(기존 데이터 무영향, backfill 불필요 —
다음 08:20 shopping_ad_product_sync가 채운다):

  ad_id             — 소재 nccAdId(String 50)
  ad_bid_amt        — adAttr.bidAmt(소재 개별 입찰, Integer)
  use_group_bid_amt — adAttr.useGroupBidAmt(false면 소재 bidAmt가 실효 입찰)
  ad_user_lock      — 소재 userLock

★신규 테이블 0(§0 금지선: 마이그레이션 최소, LESSONS #14 additive). B1은 읽기 전용 —
파생(effective_bid)·제어(update_ad_bid)는 B2/B3 몫이라 이 컬럼을 쓰는 코드는 아직 없다.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c5d6e7f8a9b0'
down_revision: Union[str, None] = 'b48c2f3bc0a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('naver_adgroup_product', sa.Column('ad_id', sa.String(length=50), nullable=True))
    op.add_column('naver_adgroup_product', sa.Column('ad_bid_amt', sa.Integer(), nullable=True))
    op.add_column('naver_adgroup_product', sa.Column('use_group_bid_amt', sa.Boolean(), nullable=True))
    op.add_column('naver_adgroup_product', sa.Column('ad_user_lock', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('naver_adgroup_product', 'ad_user_lock')
    op.drop_column('naver_adgroup_product', 'use_group_bid_amt')
    op.drop_column('naver_adgroup_product', 'ad_bid_amt')
    op.drop_column('naver_adgroup_product', 'ad_id')
