"""add_coupang_ad_option_daily

Revision ID: 9b2e4f6a7c1d
Revises: 8a1f2c3d4e5b
Create Date: 2026-06-02 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9b2e4f6a7c1d'
down_revision: Union[str, None] = '8a1f2c3d4e5b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'coupang_ad_option_daily',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('report_date', sa.Date(), nullable=False),
        sa.Column('vendor_id', sa.String(length=20), nullable=False),
        sa.Column('sell_type', sa.String(length=10), nullable=False),
        sa.Column('ad_option_id', sa.String(length=20), nullable=False),
        sa.Column('conv_option_id', sa.String(length=20), nullable=False),
        sa.Column('impressions', sa.Integer(), nullable=False),
        sa.Column('clicks', sa.Integer(), nullable=False),
        sa.Column('ad_spend', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('orders', sa.Integer(), nullable=False),
        sa.Column('sales_qty', sa.Integer(), nullable=False),
        sa.Column('conversion_revenue', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'report_date', 'vendor_id', 'sell_type', 'ad_option_id', 'conv_option_id',
            name='uq_coupang_ad_option_daily',
        ),
    )
    op.create_index('ix_coupang_ad_option_daily_report_date', 'coupang_ad_option_daily', ['report_date'])
    op.create_index('ix_coupang_ad_option_daily_ad_option_id', 'coupang_ad_option_daily', ['ad_option_id'])
    op.create_index('ix_coupang_ad_option_daily_conv_option_id', 'coupang_ad_option_daily', ['conv_option_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_coupang_ad_option_daily_conv_option_id', table_name='coupang_ad_option_daily')
    op.drop_index('ix_coupang_ad_option_daily_ad_option_id', table_name='coupang_ad_option_daily')
    op.drop_index('ix_coupang_ad_option_daily_report_date', table_name='coupang_ad_option_daily')
    op.drop_table('coupang_ad_option_daily')
