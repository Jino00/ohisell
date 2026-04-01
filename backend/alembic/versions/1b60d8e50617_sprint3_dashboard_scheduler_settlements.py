"""sprint3_dashboard_scheduler_settlements

Revision ID: 1b60d8e50617
Revises: 5e9fc0ecf187
Create Date: 2026-04-02 07:12:58.009287

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1b60d8e50617'
down_revision: Union[str, None] = '5e9fc0ecf187'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # scheduler_state 테이블 생성
    op.create_table('scheduler_state',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('job_name', sa.String(length=100), nullable=False),
    sa.Column('cron_expression', sa.String(length=50), nullable=False),
    sa.Column('is_enabled', sa.Boolean(), nullable=False),
    sa.Column('last_run_at', sa.DateTime(), nullable=True),
    sa.Column('next_run_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('job_name')
    )

    # profit_report — batch mode로 컬럼 추가 + 유니크 제약조건
    with op.batch_alter_table('profit_report', schema=None) as batch_op:
        batch_op.add_column(sa.Column('period_type', sa.String(length=10), nullable=False, server_default='daily'))
        batch_op.create_unique_constraint('uq_profit_report_period', ['product_id', 'channel_id', 'period_start', 'period_end', 'period_type'])

    # settlements — 새 컬럼 추가 (batch mode for SQLite)
    with op.batch_alter_table('settlements', schema=None) as batch_op:
        batch_op.add_column(sa.Column('settlement_period_start', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('settlement_period_end', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('order_count', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('shipping_fee', sa.Numeric(precision=12, scale=2), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('source', sa.String(length=20), nullable=False, server_default='excel'))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('settlements', schema=None) as batch_op:
        batch_op.drop_column('source')
        batch_op.drop_column('shipping_fee')
        batch_op.drop_column('order_count')
        batch_op.drop_column('settlement_period_end')
        batch_op.drop_column('settlement_period_start')

    with op.batch_alter_table('profit_report', schema=None) as batch_op:
        batch_op.drop_constraint('uq_profit_report_period', type_='unique')
        batch_op.drop_column('period_type')

    op.drop_table('scheduler_state')
