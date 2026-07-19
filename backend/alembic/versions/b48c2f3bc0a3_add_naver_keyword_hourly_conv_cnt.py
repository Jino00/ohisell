"""add naver_keyword_hourly conv_cnt column (D-NAO-60 RL1)

Revision ID: b48c2f3bc0a3
Revises: u3v4w5x6y7z8
Create Date: 2026-07-19 00:00:00.000000

트랙 docs/tracks/active/track_naver-ad-optimization.md D-NAO-60 (순위 고삐 통합
설계, 스프린트 RL). RL1 시간당 전환 데이터층: fetch_entity_hh24가 hh24 breakdown의
ccnt(시간당 전환건수)를 이제 수집하므로 naver_keyword_hourly에 저장 컬럼을 additive로
추가한다(기존 데이터 무영향):

  conv_cnt — 시간당 전환건수(구간값, 당일 누적 아님)

★회계 불변(CD1 계승): conv_cnt는 건수만. convAmt(전환금액)는 hh24 breakdown에 존재하지
않아(일별 필드로만 제공) 여기 들어오지 않는다. 매출/BEP/ROAS 등 회계 집계 코드는 이
컬럼을 절대 읽지 않는다 — 순수 관측 신호(시간당 순위 고삐 설계의 입력 원료).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b48c2f3bc0a3'
down_revision: Union[str, None] = 'u3v4w5x6y7z8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'naver_keyword_hourly',
        sa.Column('conv_cnt', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('naver_keyword_hourly', 'conv_cnt')
