"""교환 배송 손익 컬럼 — orders.exchange_* (교환이 손익 엔진에 0회 등장하던 것을 닫는다)

Revision ID: b2f9d61ae403
Revises: d3f5b7a91c48
Create Date: 2026-08-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2f9d61ae403"
down_revision: Union[str, Sequence[str], None] = "d3f5b7a91c48"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("exchange_completed_at", sa.DateTime(), nullable=True))
    op.add_column(
        "orders", sa.Column("exchange_fee_demand_amount", sa.Numeric(12, 2), nullable=True)
    )
    op.add_column("orders", sa.Column("exchange_collect_company", sa.String(20), nullable=True))
    op.add_column(
        "orders", sa.Column("exchange_fee_support_amount", sa.Numeric(12, 2), nullable=True)
    )
    op.create_index(
        "ix_orders_exchange_completed_at", "orders", ["exchange_completed_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_orders_exchange_completed_at", table_name="orders")
    op.drop_column("orders", "exchange_fee_support_amount")
    op.drop_column("orders", "exchange_collect_company")
    op.drop_column("orders", "exchange_fee_demand_amount")
    op.drop_column("orders", "exchange_completed_at")
