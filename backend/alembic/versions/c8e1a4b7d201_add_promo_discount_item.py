"""add coupang_promo_discount_item (1P 프로모션 상품별 개당 할인액, D-CPP-10)

제안서 엑셀(구글드라이브)이 개당 할인액의 유일한 출처다 — 프로모션 API·상세 화면·계약서
본문 셋 다 개당 할인액을 주지 않는 것이 2026-08-05 라이브로 확정됐다.

Revision ID: c8e1a4b7d201
Revises: a7c19e04d6b2
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa

revision = "c8e1a4b7d201"
down_revision = "a7c19e04d6b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "coupang_promo_discount_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=30), nullable=False),
        sa.Column("product_number", sa.String(length=30), nullable=False),
        sa.Column("discount_type", sa.String(length=10), nullable=False),
        sa.Column("discount_value", sa.Numeric(12, 4), nullable=False),
        sa.Column("source_file", sa.String(length=300), nullable=True),
        sa.Column("file_mtime", sa.DateTime(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id", "product_number", name="uq_promo_discount_item"),
    )
    op.create_index(
        "ix_coupang_promo_discount_item_request_id",
        "coupang_promo_discount_item", ["request_id"],
    )
    op.create_index(
        "ix_coupang_promo_discount_item_product_number",
        "coupang_promo_discount_item", ["product_number"],
    )


def downgrade() -> None:
    op.drop_index("ix_coupang_promo_discount_item_product_number", table_name="coupang_promo_discount_item")
    op.drop_index("ix_coupang_promo_discount_item_request_id", table_name="coupang_promo_discount_item")
    op.drop_table("coupang_promo_discount_item")
