"""월 고정비 — monthly_fixed_cost (3PL 입고·보관·항공도선·합포장, 주문 축에 못 붙는 비용)

Revision ID: e7a2c5b90d84
Revises: c8d1a4f97b26
Create Date: 2026-08-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7a2c5b90d84"
down_revision: Union[str, Sequence[str], None] = "c8d1a4f97b26"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "monthly_fixed_cost",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("year_month", sa.String(length=7), nullable=False),
        sa.Column("item", sa.String(length=30), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("memo", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel_id", "year_month", "item", name="uq_monthly_fixed_cost"
        ),
    )


def downgrade() -> None:
    op.drop_table("monthly_fixed_cost")
