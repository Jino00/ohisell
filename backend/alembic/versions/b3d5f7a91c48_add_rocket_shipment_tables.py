"""add coupang_rocket_shipment / _item / _box (1P 발송 ASN 상시 수집, ref 45)

지금까지 저장된 수량은 발주(쿠팡이 시킨 양)와 입고(쿠팡이 인정한 양)뿐이었다. 그 사이의
"보냈는데 안 잡힌 것"=미수금은 2026-08-05 일회성 정찰로만 8,033,970원을 찾아냈고, 상시 수집이
없으면 다음 달에 같은 일이 생겨도 아무도 못 본다.

세 테이블 모두 **추가만** 한다(기존 테이블 무변경) — 구코드를 깨지 않으므로 코드보다 먼저
배포해도 안전하다(safe_deploy.sh --migrate 순서).

Revision ID: b3d5f7a91c48
Revises: c8e1a4b7d201
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa

revision = "b3d5f7a91c48"
down_revision = "c8e1a4b7d201"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "coupang_rocket_shipment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("shipment_seq", sa.Integer(), nullable=False),
        sa.Column("vendor_id", sa.String(length=20), nullable=False),
        sa.Column("shipment_type", sa.String(length=12), nullable=True),
        sa.Column("status_code", sa.String(length=20), nullable=True),
        sa.Column("status_label", sa.String(length=40), nullable=True),
        sa.Column("invoice_number", sa.String(length=40), nullable=True),
        sa.Column("carrier_name", sa.String(length=40), nullable=True),
        sa.Column("center_name", sa.String(length=40), nullable=True),
        sa.Column("origin_address", sa.String(length=200), nullable=True),
        sa.Column("shipped_at", sa.DateTime(), nullable=True),
        sa.Column("estimated_arrival_at", sa.DateTime(), nullable=True),
        sa.Column("box_count", sa.Integer(), nullable=False, server_default="0"),
        # ★nullable: 상세를 아직 안 받은 쉽먼트는 "모름"이다. 0으로 접으면 미수금을 지어낸다.
        sa.Column("po_count", sa.Integer(), nullable=True),
        sa.Column("sku_count", sa.Integer(), nullable=True),
        sa.Column("total_shipped_qty", sa.Integer(), nullable=True),
        sa.Column("total_received_qty", sa.Integer(), nullable=True),
        sa.Column("detail_synced_at", sa.DateTime(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shipment_seq", name="uq_rocket_shipment_seq"),
    )
    op.create_index("ix_coupang_rocket_shipment_shipment_seq", "coupang_rocket_shipment", ["shipment_seq"])
    op.create_index("ix_coupang_rocket_shipment_vendor_id", "coupang_rocket_shipment", ["vendor_id"])
    op.create_index("ix_coupang_rocket_shipment_status_code", "coupang_rocket_shipment", ["status_code"])
    op.create_index("ix_coupang_rocket_shipment_invoice_number", "coupang_rocket_shipment", ["invoice_number"])
    op.create_index("ix_coupang_rocket_shipment_shipped_at", "coupang_rocket_shipment", ["shipped_at"])

    op.create_table(
        "coupang_rocket_shipment_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("shipment_seq", sa.Integer(), nullable=False),
        sa.Column("vendor_id", sa.String(length=20), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("box_label", sa.String(length=80), nullable=True),
        sa.Column("purchase_order_seq", sa.Integer(), nullable=False),
        sa.Column("product_number", sa.String(length=30), nullable=False),
        sa.Column("product_name", sa.String(length=300), nullable=True),
        sa.Column("barcode", sa.String(length=40), nullable=True),
        sa.Column("shipped_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("received_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("synced_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shipment_seq", "line_no", name="uq_rocket_shipment_item"),
    )
    op.create_index("ix_coupang_rocket_shipment_item_shipment_seq", "coupang_rocket_shipment_item", ["shipment_seq"])
    op.create_index("ix_coupang_rocket_shipment_item_vendor_id", "coupang_rocket_shipment_item", ["vendor_id"])
    op.create_index("ix_coupang_rocket_shipment_item_po_seq", "coupang_rocket_shipment_item", ["purchase_order_seq"])
    op.create_index("ix_coupang_rocket_shipment_item_product_number", "coupang_rocket_shipment_item", ["product_number"])

    op.create_table(
        "coupang_rocket_shipment_box",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("shipment_seq", sa.Integer(), nullable=False),
        sa.Column("vendor_id", sa.String(length=20), nullable=False),
        sa.Column("box_label", sa.String(length=80), nullable=False),
        sa.Column("status_label", sa.String(length=40), nullable=True),
        sa.Column("invoice_number", sa.String(length=40), nullable=True),
        sa.Column("picked_up_at", sa.DateTime(), nullable=True),
        sa.Column("arrived_at", sa.DateTime(), nullable=True),
        sa.Column("unloaded_at", sa.DateTime(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shipment_seq", "box_label", name="uq_rocket_shipment_box"),
    )
    op.create_index("ix_coupang_rocket_shipment_box_shipment_seq", "coupang_rocket_shipment_box", ["shipment_seq"])
    op.create_index("ix_coupang_rocket_shipment_box_vendor_id", "coupang_rocket_shipment_box", ["vendor_id"])
    op.create_index("ix_coupang_rocket_shipment_box_invoice_number", "coupang_rocket_shipment_box", ["invoice_number"])


def downgrade() -> None:
    op.drop_index("ix_coupang_rocket_shipment_box_invoice_number", table_name="coupang_rocket_shipment_box")
    op.drop_index("ix_coupang_rocket_shipment_box_vendor_id", table_name="coupang_rocket_shipment_box")
    op.drop_index("ix_coupang_rocket_shipment_box_shipment_seq", table_name="coupang_rocket_shipment_box")
    op.drop_table("coupang_rocket_shipment_box")

    op.drop_index("ix_coupang_rocket_shipment_item_product_number", table_name="coupang_rocket_shipment_item")
    op.drop_index("ix_coupang_rocket_shipment_item_po_seq", table_name="coupang_rocket_shipment_item")
    op.drop_index("ix_coupang_rocket_shipment_item_vendor_id", table_name="coupang_rocket_shipment_item")
    op.drop_index("ix_coupang_rocket_shipment_item_shipment_seq", table_name="coupang_rocket_shipment_item")
    op.drop_table("coupang_rocket_shipment_item")

    op.drop_index("ix_coupang_rocket_shipment_shipped_at", table_name="coupang_rocket_shipment")
    op.drop_index("ix_coupang_rocket_shipment_invoice_number", table_name="coupang_rocket_shipment")
    op.drop_index("ix_coupang_rocket_shipment_status_code", table_name="coupang_rocket_shipment")
    op.drop_index("ix_coupang_rocket_shipment_vendor_id", table_name="coupang_rocket_shipment")
    op.drop_index("ix_coupang_rocket_shipment_shipment_seq", table_name="coupang_rocket_shipment")
    op.drop_table("coupang_rocket_shipment")
