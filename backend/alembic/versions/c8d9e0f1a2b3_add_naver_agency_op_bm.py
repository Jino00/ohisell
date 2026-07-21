"""add naver_agency_op (BM 벤치마크 레이어 SA-2 조작 감지기, D-NAO-78)

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-07-22 09:00:00.000000

트랙 docs/tracks/active/track_naver-ad-optimization.md — 스프린트 BM(벤치마크 학습 레이어,
docs/PLAN_naver-ad-bm-layer.md §2 테이블 2 · §6 Phase 2). D-NAO-78: 계정 전체 45캠페인(대행사
포함)의 매일 변화작업(조작)을 관찰·학습해 우리 캠페인에 반영.

naver_entity_snapshot의 D-1 vs D diff(DB-to-DB, 0 GET·리플레이 가능)로 대행사 일일 조작을
이벤트화한다(입찰변경·상태flip·구조 신설/소멸·키워드 증감 등). change_log와 분리해 대행사
노이즈가 OUR 학습 쿼리를 오염시키지 않게 한다. 관찰 전용(네이버 API 쓰기 0).

CREATE TABLE만(기존 행 무영향, 회귀 0).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8d9e0f1a2b3'
down_revision: Union[str, None] = 'b7c8d9e0f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'naver_agency_op',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('op_date', sa.Date(), nullable=False),
        sa.Column('detected_at', sa.DateTime(), nullable=False),
        sa.Column('entity_type', sa.String(10), nullable=False),
        sa.Column('entity_id', sa.String(50), nullable=False),
        sa.Column('campaign_id', sa.String(50), nullable=False, server_default=''),
        sa.Column('optimizer', sa.String(8), nullable=False, server_default='none'),
        sa.Column('op_type', sa.String(24), nullable=False),
        sa.Column('before_value', sa.Text(), nullable=True),
        sa.Column('after_value', sa.Text(), nullable=True),
        sa.Column('magnitude', sa.Float(), nullable=True),
        sa.Column('is_exception', sa.Boolean(), nullable=False, server_default=sa.text('0')),
    )
    op.create_index('ix_naver_agency_op_op_date', 'naver_agency_op', ['op_date'])
    op.create_index('ix_naver_agency_op_campaign_id', 'naver_agency_op', ['campaign_id'])
    op.create_index('ix_naver_agency_op_date_campaign', 'naver_agency_op', ['op_date', 'campaign_id'])


def downgrade() -> None:
    op.drop_index('ix_naver_agency_op_date_campaign', table_name='naver_agency_op')
    op.drop_index('ix_naver_agency_op_campaign_id', table_name='naver_agency_op')
    op.drop_index('ix_naver_agency_op_op_date', table_name='naver_agency_op')
    op.drop_table('naver_agency_op')
