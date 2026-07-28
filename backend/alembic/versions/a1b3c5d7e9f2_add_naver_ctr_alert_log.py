"""add naver_ctr_alert_log (D-NAO-103 — 소재 CTR 경보 발화 억제 상태)

Revision ID: a1b3c5d7e9f2
Revises: f6a8c0b2d4e6
Create Date: 2026-07-28 00:00:00.000000

VT3 개편(D-NAO-103): "신규 진입만 개별 발화, 만성은 주 1회 요약"을 위해 전일 **판정 집합**이
필요하다. ops_diary_entries의 브리핑 본문은 억제된 건이 애초에 없고 사람이 읽는 이름 표기라
ID 역파싱이 불가능해 별도 이력 테이블을 신설한다(additive — 기존 경로 무영향).

grain: (as_of_date, campaign_id, adgroup_id). as_of_date = detect_ctr_alerts의 D0(=today−1).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b3c5d7e9f2'
down_revision: Union[str, None] = 'f6a8c0b2d4e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'naver_ctr_alert_log',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('as_of_date', sa.Date(), nullable=False),
        sa.Column('campaign_id', sa.String(50), nullable=False),
        sa.Column('adgroup_id', sa.String(50), nullable=False, server_default=''),
        sa.Column('window', sa.String(8), nullable=False, server_default=''),
        sa.Column('imp', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('clk', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('avg_rank', sa.Numeric(6, 2), nullable=True),
        sa.Column('expected_clk', sa.Numeric(10, 2), nullable=True),
        sa.Column('streak_days', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('notified', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('as_of_date', 'campaign_id', 'adgroup_id', name='uq_naver_ctr_alert_log'),
    )
    op.create_index('ix_naver_ctr_alert_log_as_of_date', 'naver_ctr_alert_log', ['as_of_date'])
    op.create_index('ix_naver_ctr_alert_log_campaign_id', 'naver_ctr_alert_log', ['campaign_id'])


def downgrade() -> None:
    op.drop_index('ix_naver_ctr_alert_log_campaign_id', table_name='naver_ctr_alert_log')
    op.drop_index('ix_naver_ctr_alert_log_as_of_date', table_name='naver_ctr_alert_log')
    op.drop_table('naver_ctr_alert_log')
