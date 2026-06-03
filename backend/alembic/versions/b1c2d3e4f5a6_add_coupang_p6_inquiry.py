"""add_coupang_p6_inquiry

Revision ID: b1c2d3e4f5a6
Revises: f2a4c6e8b0d1
Create Date: 2026-06-04 09:00:00.000000

P6 CS 도메인 — 고객문의 경량 테이블(운영 보조 지표).
물류/카테고리/브랜드 SA는 온디맨드 조회(DB 미적재).
명세: docs/references/10_coupang_cs_api_specs.md #1(상품Q&A) + #3(CS이관).
"""
from alembic import op
import sqlalchemy as sa

revision = "b1c2d3e4f5a6"
down_revision = "f2a4c6e8b0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "coupang_inquiry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_key", sa.String(20), nullable=False),
        sa.Column("vendor_id", sa.String(20), nullable=False),
        sa.Column("inquiry_type", sa.String(20), nullable=False),  # online / call_center
        sa.Column("inquiry_id", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=True),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("answered", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("inquired_at", sa.DateTime(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_key", "inquiry_type", "inquiry_id", name="uq_coupang_inquiry"),
    )
    op.create_index("ix_coupang_inquiry_vendor_id", "coupang_inquiry", ["vendor_id"])
    op.create_index("ix_coupang_inquiry_answered", "coupang_inquiry", ["answered"])


def downgrade() -> None:
    op.drop_index("ix_coupang_inquiry_answered", "coupang_inquiry")
    op.drop_index("ix_coupang_inquiry_vendor_id", "coupang_inquiry")
    op.drop_table("coupang_inquiry")
