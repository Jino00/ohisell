"""add naver_retro_signal gave_score_d3/d7 (D-NAO-72)

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-07-21 16:00:00.000000

트랙 docs/tracks/active/track_naver-ad-optimization.md D-NAO-72. GAVE 페널티 점수
(ref 26 §2 TOP5 ⑤)를 소급 채점 목적함수에 배선. retro_scorer가 방향 채점(verdict)과
병렬로 D+3/D+7 사후창 GAVE 점수 = min{(roas_c/bep_asof)^γ,1}×(cf 보정 매출)을 남긴다
(D-NAO-59 총이익 극대화·D-NAO-1 이익하한의 정식화). 점수 컬럼 2개만 추가:

  gave_score_d3 — D+3 사후창 GAVE 점수 (Float, nullable)
  gave_score_d7 — D+7 사후창 GAVE 점수 (Float, nullable)

★신규 테이블 0·additive nullable(기존 행 무영향, backfill 불필요 — 다음 08:30 크론이
사후창 도래분부터 채운다, LESSONS #14 additive). penalty·roas_ratio는 roas_c_post/bep_asof로
재구성 가능하니 점수만 저장(최소 스키마).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e7f8a9b0c1d2'
down_revision: Union[str, None] = 'd6e7f8a9b0c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('naver_retro_signal', sa.Column('gave_score_d3', sa.Float(), nullable=True))
    op.add_column('naver_retro_signal', sa.Column('gave_score_d7', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('naver_retro_signal', 'gave_score_d7')
    op.drop_column('naver_retro_signal', 'gave_score_d3')
