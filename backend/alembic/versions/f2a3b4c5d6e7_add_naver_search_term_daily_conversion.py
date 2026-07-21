"""add naver_search_term_daily conversion columns (SS1, D-NAO 검색어 ROAS 레이어)

Revision ID: f2a3b4c5d6e7
Revises: e7f8a9b0c1d2
Create Date: 2026-07-21 22:00:00.000000

트랙 docs/tracks/active/track_naver-ad-optimization.md — 스프린트 SS(검색어 ROAS 레이어,
docs/PLAN_naver-ad-searchterm-ss.md §3 SS1). 검색어 성과 수집(naver_search_term_daily)은
이미 가동 중이나 전환 데이터가 공백이었다(전략 v2 §2 감지층 최대 갭). 이 스프린트가
SHOPPINGKEYWORD_CONVERSION_DETAIL 전환을 병합해 그 공백을 메운다. 전부 additive
(기존 imp/clk/cost/rank_sum 행 무영향, server_default '0'):

  conv_purchase_cnt — 구매 전환수(직접+간접 합산). §1 게이트 "전환 있으면 제외 금지"는
                       보수적으로 합산 기준(간접이어도 살아있는 증거 — 오컷 방지 우선).
  conv_direct_cnt   — 구매 전환수(직접만). SS4 승격 신호(품질 있는 전환만).
  conv_purchase_amt — 구매 전환매출(직접+간접 합산). 검색어 ROAS = conv_purchase_amt/cost.
  cart_cnt          — 장바구니 전환수(직접+간접 합산, 선행지표 재료 — 매출 아님).
  cart_amt          — 장바구니 전환가치(직접+간접 합산, ★매출 아님 — 회계 합산에 안 섞임).

★회계 불변: 검색어 ROAS/BEP 판정(SS2)은 conv_purchase_amt만 읽는다. cart_*는 CD1(naver_ad_daily)
관례와 동일하게 완전히 불활성(inert) — 어떤 매출 합계에도 더해지지 않는다.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, None] = 'e7f8a9b0c1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'naver_search_term_daily',
        sa.Column('conv_purchase_cnt', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'naver_search_term_daily',
        sa.Column('conv_direct_cnt', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'naver_search_term_daily',
        sa.Column('conv_purchase_amt', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'naver_search_term_daily',
        sa.Column('cart_cnt', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'naver_search_term_daily',
        sa.Column('cart_amt', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('naver_search_term_daily', 'cart_amt')
    op.drop_column('naver_search_term_daily', 'cart_cnt')
    op.drop_column('naver_search_term_daily', 'conv_purchase_amt')
    op.drop_column('naver_search_term_daily', 'conv_direct_cnt')
    op.drop_column('naver_search_term_daily', 'conv_purchase_cnt')
