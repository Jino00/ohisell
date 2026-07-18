"""add naver_adgroup_product + naver_product_bep.commission_basis (D-NAO-57)

Revision ID: t2u3v4w5x6y7
Revises: s1t2u3v4w5x6
Create Date: 2026-07-18 15:00:00.000000

트랙 docs/tracks/active/track_naver-ad-optimization.md D-NAO-57 (상품별 원가 구조 기반
BEP·타겟 정밀화). 전부 additive(기존 데이터 무영향):

  naver_adgroup_product — (A) 쇼핑 소재 /ncc/ads referenceData.mallProductId로 adgroup↔상품
    매핑을 관찰성 sync(08:20 크론)로 적재. campaign_target_resolver 우선순위 ②(상품 파생
    타겟)가 이 테이블을 소비. unique(adgroup_id, mall_product_id) — 한 그룹에 여러 소재(상품)
    가능. mall_product_id는 naver_product_bep.channel_product_id와 정확히 일치(라이브 실증).

  naver_product_bep.commission_basis — (B) 광고 의사결정 BEP 산출 시 어느 수수료율 기준을
    썼는지 정직 표기(ad_case=정산 유형별 실측 분해, blended=기존 전체 회계 블렌드 폴백).
    nullable(과거 스냅샷·산출 실패 시 None).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 't2u3v4w5x6y7'
down_revision: Union[str, None] = 's1t2u3v4w5x6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'naver_adgroup_product',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('adgroup_id', sa.String(length=50), nullable=False),
        sa.Column('campaign_id', sa.String(length=50), nullable=False, server_default=''),
        sa.Column('mall_product_id', sa.String(length=50), nullable=False),
        sa.Column('product_name', sa.String(length=300), nullable=False, server_default=''),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('adgroup_id', 'mall_product_id', name='uq_naver_adgroup_product'),
    )
    op.create_index('ix_naver_adgroup_product_adgroup_id', 'naver_adgroup_product', ['adgroup_id'])
    op.create_index('ix_naver_adgroup_product_mall_product_id', 'naver_adgroup_product', ['mall_product_id'])
    op.create_index('ix_naver_adgroup_product_campaign_id', 'naver_adgroup_product', ['campaign_id'])

    op.add_column(
        'naver_product_bep',
        sa.Column('commission_basis', sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('naver_product_bep', 'commission_basis')
    op.drop_index('ix_naver_adgroup_product_campaign_id', 'naver_adgroup_product')
    op.drop_index('ix_naver_adgroup_product_mall_product_id', 'naver_adgroup_product')
    op.drop_index('ix_naver_adgroup_product_adgroup_id', 'naver_adgroup_product')
    op.drop_table('naver_adgroup_product')
