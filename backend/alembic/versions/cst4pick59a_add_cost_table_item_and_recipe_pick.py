"""계약 A′ 개정 4 (D-CPP-59) — 원가표 항목 저장 + 레시피 픽

구성 이식의 열쇠를 `product_master.cost_price`(불신 컬럼)에서 **사람의 픽**으로 옮기기 위한
순수 추가 마이그레이션이다. 기존 테이블의 «변경»은 `cost_recipe`에 nullable 컬럼 5개를
더하는 것뿐이고, 그 테이블은 이 계약이 만든 것이라 §3 「기존 테이블 변경 0」과 무충돌이다
(cst2snap53b·cst3exref53c 선례).

★`cost_table_item`의 자연 키(section·item_name·form_factor)에 **유니크를 걸지 않는다** —
계약 §9-9①의 폴드 중복 정의(행 42 vs 행 105, 같은 이름 다른 값)가 원가표에 실재하므로,
유니크를 걸면 파서가 진짜 사실을 못 싣고 한쪽을 조용히 버린다.

Revision ID: cst4pick59a
Revises: a7b8c9d0e1f2
Create Date: 2026-08-25 (KST)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "cst4pick59a"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cost_table_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(length=100), nullable=False),
        sa.Column("item_name", sa.String(length=300), nullable=False),
        sa.Column("form_factor", sa.String(length=24), nullable=True),
        sa.Column(
            "recipe_kind",
            sa.String(length=20),
            nullable=False,
            server_default="assembly",
        ),
        sa.Column("total_inc_vat", sa.Numeric(14, 2), nullable=True),
        sa.Column("row_number", sa.Integer(), nullable=True),
        sa.Column("anomalies", sa.String(length=200), nullable=True),
        sa.Column(
            "uploaded_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cost_table_item_section", "cost_table_item", ["section"])
    op.create_index("ix_cost_table_item_item_name", "cost_table_item", ["item_name"])
    op.create_index("ix_cost_table_item_form_factor", "cost_table_item", ["form_factor"])

    op.create_table(
        "cost_table_item_line",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("material_name", sa.String(length=300), nullable=False),
        sa.Column("quantity", sa.Numeric(14, 3), nullable=False),
        sa.Column("ref_price", sa.Numeric(14, 4), nullable=True),
        sa.Column("source_column", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["item_id"], ["cost_table_item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_cost_table_item_line_item_id", "cost_table_item_line", ["item_id"]
    )

    # ── cost_recipe — 픽·부재확인 칸 (전부 nullable · 기본값 없음) ──────────────
    #   ★기본값을 안 두는 이유: 「아직 아무도 안 봤다」가 NULL로 표현돼야 하기 때문이다.
    #   0이나 빈 문자열로 채우면 「사람이 보고 없다고 판정했다」와 구별이 사라진다(§2-7).
    with op.batch_alter_table("cost_recipe") as batch:
        batch.add_column(sa.Column("picked_item_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("picked_item_key", sa.String(length=600), nullable=True))
        batch.add_column(sa.Column("picked_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("absent_confirmed_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("absent_note", sa.Text(), nullable=True))
        batch.create_foreign_key(
            "fk_cost_recipe_picked_item",
            "cost_table_item",
            ["picked_item_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_cost_recipe_picked_item_id", "cost_recipe", ["picked_item_id"])


def downgrade() -> None:
    op.drop_index("ix_cost_recipe_picked_item_id", table_name="cost_recipe")
    with op.batch_alter_table("cost_recipe") as batch:
        batch.drop_constraint("fk_cost_recipe_picked_item", type_="foreignkey")
        batch.drop_column("absent_note")
        batch.drop_column("absent_confirmed_at")
        batch.drop_column("picked_at")
        batch.drop_column("picked_item_key")
        batch.drop_column("picked_item_id")

    op.drop_index("ix_cost_table_item_line_item_id", table_name="cost_table_item_line")
    op.drop_table("cost_table_item_line")
    op.drop_index("ix_cost_table_item_form_factor", table_name="cost_table_item")
    op.drop_index("ix_cost_table_item_item_name", table_name="cost_table_item")
    op.drop_index("ix_cost_table_item_section", table_name="cost_table_item")
    op.drop_table("cost_table_item")
