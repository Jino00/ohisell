"""add naver_bm_benchmark (BM 벤치마크 레이어 SA-3 성과 대조 프라이어, D-NAO-78)

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-07-22 10:30:00.000000

트랙 docs/tracks/active/track_naver-ad-optimization.md — 스프린트 BM(벤치마크 학습 레이어,
docs/PLAN_naver-ad-bm-layer.md §2 테이블 3 · §6 Phase 4). D-NAO-78: 계정 전체 45캠페인(대행사
포함)의 구조↔성과 상관을 벤치마크化해 B-X(탐색 초기입찰)·SS4(승격 교차)가 optional 프라이어로
소비한다(전부 fail-open, 부재 시 기존 동일).

bench_kind: keyword_verified(대행사 등록 키워드셋)/bid_band([min,p50,max])/group_structure.
값이 구조(집합·밴드·다차원)라 naver_learning_state 단일 Numeric에 안 맞아 value_json에 담는다
(§9-2 혼용 결정 — slope는 향후 learning_state, 나머지는 이 테이블). 매일 07:37 재산출(교체 upsert).

CREATE TABLE만(기존 행 무영향, 회귀 0).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9e0f1a2b3c4'
down_revision: Union[str, None] = 'c8d9e0f1a2b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'naver_bm_benchmark',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('bench_kind', sa.String(24), nullable=False),
        sa.Column('bench_key', sa.String(120), nullable=False, server_default=''),
        sa.Column('value_json', sa.Text(), nullable=True),
        sa.Column('sample_n', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('computed_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('bench_kind', 'bench_key', name='uq_naver_bm_benchmark'),
    )
    op.create_index('ix_naver_bm_benchmark_bench_kind', 'naver_bm_benchmark', ['bench_kind'])


def downgrade() -> None:
    op.drop_index('ix_naver_bm_benchmark_bench_kind', table_name='naver_bm_benchmark')
    op.drop_table('naver_bm_benchmark')
