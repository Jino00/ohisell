"""add naver_ad_daily cart_* columns (D-NAO-58 CD1)

Revision ID: u3v4w5x6y7z8
Revises: t2u3v4w5x6y7
Create Date: 2026-07-18 18:00:00.000000

트랙 docs/tracks/active/track_naver-ad-optimization.md D-NAO-58 (클릭 탐침 루프 —
환경 조건별 최적 순위 학습). CD1 선행지표 데이터층: AD_CONVERSION 보고서의
add_to_cart 전환(현재 버려짐)을 naver_ad_daily에 별도 컬럼으로 수집한다. 전부
additive(기존 데이터 무영향):

  cart_direct_cnt / cart_indirect_cnt   — 직접/간접 장바구니 전환 수
  cart_direct_amt / cart_indirect_amt   — 장바구니 전환가치(원, ★매출 아님)

★회계 불변: BEP·ROAS·매출 집계 코드는 conv_* 컬럼만 읽으므로 이 4개 컬럼은
회계에 대해 완전히 불활성(inert)이다. cart_*_amt는 리포트 전환가치 컬럼의 원값
저장용일 뿐 어떤 매출 합계에도 더해지지 않는다(D-58-1 선행지표 = 즉시구매 +
장바구니 × 상품별 장바구니→구매 전환율).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'u3v4w5x6y7z8'
down_revision: Union[str, None] = 't2u3v4w5x6y7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'naver_ad_daily',
        sa.Column('cart_direct_cnt', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'naver_ad_daily',
        sa.Column('cart_indirect_cnt', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'naver_ad_daily',
        sa.Column('cart_direct_amt', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'naver_ad_daily',
        sa.Column('cart_indirect_amt', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('naver_ad_daily', 'cart_indirect_amt')
    op.drop_column('naver_ad_daily', 'cart_direct_amt')
    op.drop_column('naver_ad_daily', 'cart_indirect_cnt')
    op.drop_column('naver_ad_daily', 'cart_direct_cnt')
