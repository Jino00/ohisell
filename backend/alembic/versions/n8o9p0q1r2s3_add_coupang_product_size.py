"""add_coupang_product_size

Revision ID: n8o9p0q1r2s3
Revises: m7n8o9p0q1r2
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa

revision = "n8o9p0q1r2s3"
down_revision = "m7n8o9p0q1r2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "coupang_product_size",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("vendor_item_id", sa.String(30), nullable=False, unique=True),
        sa.Column("seller_product_id", sa.String(30), nullable=True),
        sa.Column("sku_id", sa.String(30), nullable=True),
        sa.Column("product_name", sa.String(200), nullable=True),
        sa.Column("option_name", sa.String(200), nullable=True),
        sa.Column("size_type", sa.String(20), nullable=False),
        sa.Column("source_group_key", sa.String(60), nullable=True),
        sa.Column("synced_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_coupang_product_size_vendor_item_id", "coupang_product_size", ["vendor_item_id"])


def downgrade() -> None:
    op.drop_index("ix_coupang_product_size_vendor_item_id", "coupang_product_size")
    op.drop_table("coupang_product_size")
