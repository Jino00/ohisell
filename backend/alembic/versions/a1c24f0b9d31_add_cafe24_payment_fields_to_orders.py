"""add cafe24 payment fields to orders (payment_type, commission_amount)

Revision ID: a1c24f0b9d31
Revises: e8f0d3882c59
Create Date: 2026-05-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c24f0b9d31'
down_revision: Union[str, None] = 'e8f0d3882c59'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('orders', sa.Column('payment_type', sa.String(length=30), nullable=True))
    op.add_column('orders', sa.Column('commission_amount', sa.Numeric(12, 2), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('orders', 'commission_amount')
    op.drop_column('orders', 'payment_type')
