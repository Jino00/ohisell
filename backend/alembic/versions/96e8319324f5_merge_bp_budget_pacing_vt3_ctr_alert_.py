"""merge BP budget pacing + VT3 ctr alert log + promo pnl unit discount heads

Revision ID: 96e8319324f5
Revises: a1b3c5d7e9f2, bp1c2d3e4f5a, df7b34a2f46e
Create Date: 2026-07-28 14:04:58.102949

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '96e8319324f5'
down_revision: Union[str, None] = ('a1b3c5d7e9f2', 'bp1c2d3e4f5a', 'df7b34a2f46e')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
