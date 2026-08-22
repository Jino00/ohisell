"""D-CPP-48: 수입건 원장(landed cost) 5테이블 신설

계약 `docs/PLAN_import-cost-ledger.md` · 트랙 `docs/tracks/active/track_cost-truth-ledger.md`
앵커 `.claude/anchors/a9a2121b-0e5c-4888-97a9-b7bcb478c26a.md`.

수입 1건의 서류 3종(CI·PL·통관경비서)을 받아 통관비를 SKU별로 배부하고, 로트별 실제 단가를
VAT 포함/제외 두 값으로 확정 저장한다.

★**순수 추가다** — 기존 테이블을 하나도 건드리지 않는다. 특히 `product_master.cost_price`와
그 소비처 14곳은 무접촉이다(계약 §3 금지선). 원가 반영은 계약 C 몫이다.

★**Boolean에 정수 리터럴을 쓰지 않는다** (교훈 #341 / M2-a 적대 리뷰 1R P1):
`server_default=1`은 SQLite에선 통과하고 **PostgreSQL에선 타입 에러로 트랜잭션이 통째로
롤백된다** — 로컬·CI가 전부 초록인 채 prod에서만 죽는 모양이다. 여기서는 아예
**Boolean에 server_default를 두지 않는다**. 신규 테이블이라 기존 행 백필이 없고,
INSERT는 전부 값을 명시하므로 기본값이 필요 없다.

Revision ID: imp1ledger47a
Revises: m2b2devw1eight
Create Date: 2026-08-22 KST
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "imp1ledger47a"
down_revision: Union[str, None] = "m2b2devw1eight"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "import_shipment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("hbl_no", sa.String(length=50), nullable=False),
        sa.Column("declaration_no", sa.String(length=50), nullable=True),
        sa.Column("declaration_date", sa.Date(), nullable=True),
        sa.Column("eta", sa.Date(), nullable=True),
        sa.Column("shipper_name", sa.String(length=200), nullable=True),
        sa.Column("invoice_no", sa.String(length=100), nullable=True),
        sa.Column("vessel", sa.String(length=200), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="CNY"),
        # 신고환율. 실송금 환율은 이번 범위 밖이라 nullable로 자리만 둔다.
        sa.Column("fx_rate", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("remittance_fx_rate", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("declared_inv_value", sa.Numeric(precision=16, scale=2), nullable=True),
        sa.Column("customs_value_krw", sa.Numeric(precision=16, scale=2), nullable=True),
        sa.Column("carton_count", sa.Integer(), nullable=True),
        sa.Column("gross_weight_kg", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("cbm", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column(
            "allocation_basis", sa.String(length=10), nullable=False, server_default="amount"
        ),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="draft"),
        sa.Column("memo", sa.Text(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hbl_no", name="uq_import_shipment_hbl"),
    )

    op.create_table(
        "import_cost_line",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("shipment_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("item_name", sa.String(length=200), nullable=False),
        sa.Column(
            "supply_amount", sa.Numeric(precision=16, scale=2), nullable=False, server_default="0"
        ),
        sa.Column(
            "tax_amount", sa.Numeric(precision=16, scale=2), nullable=False, server_default="0"
        ),
        # ★server_default 없음 — Boolean 기본값은 PostgreSQL에서 터지는 자리다(교훈 #341).
        sa.Column("is_costing", sa.Boolean(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["shipment_id"], ["import_shipment.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shipment_id", "seq", name="uq_import_cost_line_seq"),
    )
    op.create_index(
        "ix_import_cost_line_shipment_id", "import_cost_line", ["shipment_id"], unique=False
    )

    op.create_table(
        "import_invoice_line",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("shipment_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("order_no", sa.String(length=100), nullable=True),
        sa.Column("item_name", sa.String(length=200), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("unit_price_foreign", sa.Numeric(precision=14, scale=4), nullable=False),
        # 미분류(unknown)를 판매 SKU로 접지 않는다 — 0=미입력 혼동의 재생산 금지.
        sa.Column(
            "line_type", sa.String(length=12), nullable=False, server_default="unknown"
        ),
        sa.Column("internal_sku", sa.String(length=50), nullable=True),
        sa.Column("gross_weight_kg", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("cbm", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("goods_amount_krw", sa.Numeric(precision=16, scale=2), nullable=True),
        sa.Column("allocated_cost_krw", sa.Numeric(precision=16, scale=2), nullable=True),
        sa.Column("unit_cost_ex_vat", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("unit_cost_inc_vat", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.ForeignKeyConstraint(["shipment_id"], ["import_shipment.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shipment_id", "seq", name="uq_import_invoice_line_seq"),
    )
    op.create_index(
        "ix_import_invoice_line_shipment_id", "import_invoice_line", ["shipment_id"], unique=False
    )
    op.create_index(
        "ix_import_invoice_line_internal_sku", "import_invoice_line", ["internal_sku"], unique=False
    )

    op.create_table(
        "import_packing_line",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("shipment_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("carton_range", sa.String(length=50), nullable=True),
        sa.Column("item_name", sa.String(length=200), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("qty_per_carton", sa.Numeric(precision=14, scale=3), nullable=True),
        sa.Column("carton_count", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("gross_weight_kg", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("measure", sa.String(length=50), nullable=True),
        sa.Column("cbm", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("remark", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["shipment_id"], ["import_shipment.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shipment_id", "seq", name="uq_import_packing_line_seq"),
    )
    op.create_index(
        "ix_import_packing_line_shipment_id", "import_packing_line", ["shipment_id"], unique=False
    )

    op.create_table(
        "import_document",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("shipment_id", sa.Integer(), nullable=False),
        sa.Column("doc_type", sa.String(length=20), nullable=False),
        sa.Column("filename", sa.String(length=300), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["shipment_id"], ["import_shipment.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_import_document_shipment_id", "import_document", ["shipment_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_import_document_shipment_id", table_name="import_document")
    op.drop_table("import_document")
    op.drop_index("ix_import_packing_line_shipment_id", table_name="import_packing_line")
    op.drop_table("import_packing_line")
    op.drop_index("ix_import_invoice_line_internal_sku", table_name="import_invoice_line")
    op.drop_index("ix_import_invoice_line_shipment_id", table_name="import_invoice_line")
    op.drop_table("import_invoice_line")
    op.drop_index("ix_import_cost_line_shipment_id", table_name="import_cost_line")
    op.drop_table("import_cost_line")
    op.drop_table("import_shipment")
