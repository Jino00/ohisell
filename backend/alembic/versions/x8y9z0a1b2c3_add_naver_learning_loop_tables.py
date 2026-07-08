"""add naver learning loop tables (듀얼모드 스프린트 Phase 6 — learning_loops)

Revision ID: x8y9z0a1b2c3
Revises: w7x8y9z0a1b2
Create Date: 2026-07-08 00:00:00.000000

Phase 6 학습루프 3(conversion_maturity)·5(hourly_pattern)는 기존 테이블만으로는 계산이
불가능해 신규 테이블 2개가 필요하다(신규, additive):

  naver_conversion_maturity_snapshot: naver_ad_daily는 "같은 날짜 재수집 시 확정치로 교체"
    (snapshot upsert, 모델 docstring 참조) — 즉 conv_amt가 시간에 따라 어떻게 누적됐는지
    이력이 남지 않는다. 이 테이블이 매일 "ad_date 기준 며칠째(days_since)에 얼마가 찍혀
    있었는지"를 별도로 적립해 성숙곡선 m(d)을 역산할 수 있게 한다. Phase 6 착수 시점부터
    적립을 시작하므로, 실제 곡선은 며칠~몇 주 데이터가 쌓여야 산출 가능(정직 경계).

  naver_hourly_pattern_history: naver_hourly_snapshot은 7일 롤링 삭제(hourly_snapshot.py
    _RETAIN_DAYS=7)라 요일×시간(168칸) 패턴을 여러 주에 걸쳐 누적할 수 없다. 이 테이블이
    매일 전날의 시간대별 순증분(hourly_pacing 계산 결과)을 요일×시간 버킷에 무기한 합산해
    "주차 누적으로 정교화"(D-NAO-14)되는 168칸 패턴의 저장소 역할을 한다.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'x8y9z0a1b2c3'
down_revision: Union[str, None] = 'w7x8y9z0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'naver_conversion_maturity_snapshot',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ad_date', sa.Date(), nullable=False),
        sa.Column('days_since', sa.Integer(), nullable=False),
        sa.Column('direct_amt', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('indirect_amt', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_amt', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('snapshot_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('ad_date', 'days_since', name='uq_naver_conv_maturity_snapshot'),
    )
    op.create_index('ix_naver_conv_maturity_ad_date', 'naver_conversion_maturity_snapshot', ['ad_date'])

    op.create_table(
        'naver_hourly_pattern_history',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('weekday', sa.Integer(), nullable=False),  # 0=월 ~ 6=일 (Python date.weekday())
        sa.Column('hour', sa.Integer(), nullable=False),  # 0~23 KST
        sa.Column('clk_sum', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cost_sum', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('sample_days', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_folded_date', sa.Date(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('weekday', 'hour', name='uq_naver_hourly_pattern_history'),
    )


def downgrade() -> None:
    op.drop_table('naver_hourly_pattern_history')
    op.drop_index('ix_naver_conv_maturity_ad_date', table_name='naver_conversion_maturity_snapshot')
    op.drop_table('naver_conversion_maturity_snapshot')
