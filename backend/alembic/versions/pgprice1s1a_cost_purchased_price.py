"""cost_purchased_price — 매입 완제품 SKU 단위 매입가 (계약 D-CPP-63 S1)

순수 «추가»다: 새 테이블 1개 + 인덱스뿐이고 기존 테이블·컬럼을 건드리지 않는다.
⇒ 구코드가 이 테이블을 몰라도 깨지지 않으므로 `--migrate`의 정상 순서
   (마이그 → upgrade → 코드 → 재시작)를 그대로 쓸 수 있다.

★왜 이 테이블이 필요한가는 `models.CostPurchasedPrice` docstring에 있다(요지: 매입가는
기종마다 달라 SKU 그레인이고, 조립품의 `cost_standard`는 레시피당 값이 하나라 담을 수 없다).

Revision ID: pgprice1s1a
Revises: mrg1po9rt3a
Create Date: 2026-08-28 18:45 KST
"""

from alembic import op
import sqlalchemy as sa

revision = "pgprice1s1a"
down_revision = "mrg1po9rt3a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cost_purchased_price",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("internal_sku", sa.String(length=64), nullable=False),
        # nullable — 「모른다」와 「0원」은 다른 사실이다. 파일의 1원 자리표시자는
        # 여기 오지 않는다(계약 §3).
        sa.Column("unit_price_inc_vat", sa.Numeric(14, 2), nullable=True),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("source_file", sa.String(length=200), nullable=True),
        sa.Column("source_product_name", sa.String(length=300), nullable=True),
        # NULL이면 제안이지 확정이 아니다 — 계산은 확정만 읽는다.
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True
        ),
    )
    op.create_index(
        "ix_cost_purchased_price_internal_sku",
        "cost_purchased_price",
        ["internal_sku"],
    )
    # 「이 SKU의 최신 행」이 조회의 기본 질문이라 복합 인덱스를 함께 둔다.
    op.create_index(
        "ix_cost_purchased_price_sku_created",
        "cost_purchased_price",
        ["internal_sku", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_cost_purchased_price_sku_created", table_name="cost_purchased_price")
    op.drop_index("ix_cost_purchased_price_internal_sku", table_name="cost_purchased_price")
    op.drop_table("cost_purchased_price")
