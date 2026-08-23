"""D-CPP-53: 원가 메뉴·표준원가 7테이블 신설 (계약 A′ S1)

계약 `docs/PLAN_cost-menu-standard-cost.md` §5-1 · 트랙 `docs/tracks/active/track_cost-truth-ledger.md`

「구성(부자재×수량) × 단가(원장 파생 또는 Jino 확인분)」로 표준원가를 계산·표시하는 층의
스키마다. S1이 실제로 쓰는 것은 `cost_material`·`cost_material_price`·`cost_setting` 셋이고,
나머지 넷(레시피·링크·표준원가)은 **스키마만 먼저 선다** — DB 스키마 변경이 배포 순서를
강제하는 유일한 변경이라(`safe_deploy.sh --migrate`), 슬라이스마다 마이그를 내보내면 그
순서 사고의 표면이 세 배가 되기 때문이다(계약 §6).

★**순수 추가다** — 기존 테이블을 하나도 건드리지 않는다. 특히 `product_master.cost_price`와
그 소비처는 무접촉이다(계약 §3 금지선). 원가 반영·컷오버는 계약 C 몫이다.

★**Boolean에 정수 리터럴을 쓰지 않는다** (교훈 #341): `server_default=1`은 SQLite에선
통과하고 PostgreSQL에선 타입 에러로 트랜잭션이 통째로 롤백된다. `cost_setting.confirmed`의
기본값은 `sa.false()`로 준다 — 이 테이블만은 마이그가 초기 2행을 INSERT하므로 값을 명시하지만,
나중에 손으로 INSERT하는 경로가 기본값 없이 죽지 않게 둔다.

★**미입력은 NULL이다 — 0으로 채우지 않는다**(계약 §2-7). 단가 컬럼이 전부 nullable인 이유가
그것이다: `cost_price`가 NOT NULL default 0이라 「0인가 미입력인가」를 못 가리는 것이 기존
스키마의 결함이고, 그걸 새 층에서 재생산하면 이 층을 만들 이유가 없다.

Revision ID: cst1menu53a
Revises: duty50attrib
Create Date: 2026-08-22 KST
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "cst1menu53a"
down_revision: Union[str, None] = "duty50attrib"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. 부자재·구성요소 종 ─────────────────────────────────────────
    op.create_table(
        "cost_material",
        sa.Column("id", sa.Integer(), nullable=False),
        # 이름이 곧 매칭이 닿는 자리다(계약 §5-1 ★원단 결정 ③) — 그래서 유일하다.
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=True),
        sa.Column("category", sa.String(length=30), nullable=True),
        sa.Column(
            "status", sa.String(length=12), nullable=False, server_default="unconfirmed"
        ),
        sa.Column("excel_label", sa.String(length=200), nullable=True),
        sa.Column("match_rule", sa.String(length=200), nullable=True),
        # 분류·필터용이지 단가 축이 아니다(계약 §5-1 ★원단 결정).
        sa.Column("form_factor", sa.String(length=24), nullable=True),
        sa.Column("part", sa.String(length=24), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # ── 2. 단가 관측 1건 ──────────────────────────────────────────────
    op.create_table(
        "cost_material_price",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),  # ledger / manual
        sa.Column("import_invoice_line_id", sa.Integer(), nullable=True),
        sa.Column("supplier", sa.String(length=200), nullable=True),
        sa.Column("unit_price_ex_vat", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("unit_price_inc_vat", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["material_id"], ["cost_material.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["import_invoice_line_id"], ["import_invoice_line.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        # 한 원장 라인이 한 종에 두 번 붙지 않는다. NULL은 서로 같지 않으므로
        # `manual` 행(라인 id NULL)은 이 제약에 걸리지 않는다 — 수동 입력은 여러 번 가능하다.
        sa.UniqueConstraint(
            "material_id", "import_invoice_line_id", name="uq_cost_material_price_ledger_line"
        ),
    )
    op.create_index(
        "ix_cost_material_price_material_id", "cost_material_price", ["material_id"]
    )
    op.create_index(
        "ix_cost_material_price_import_invoice_line_id",
        "cost_material_price",
        ["import_invoice_line_id"],
    )

    # ── 3. 레시피 헤더 (상품명 × 폼팩터) — S2에서 채운다 ────────────────
    op.create_table(
        "cost_recipe",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_name", sa.String(length=300), nullable=False),
        sa.Column("form_factor", sa.String(length=24), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="draft"),
        sa.Column("source", sa.String(length=10), nullable=False, server_default="manual"),
        sa.Column(
            "recipe_kind", sa.String(length=20), nullable=False, server_default="assembly"
        ),
        sa.Column("anomaly_flag", sa.String(length=40), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # ⚠️ NULL끼리는 같지 않다 — form_factor가 NULL인 행(수입 완제품·매입품)의 상품명 중복은
        #    이 제약이 막지 못한다. 그 판정은 승인 화면 몫이다(S2).
        sa.UniqueConstraint("product_name", "form_factor", name="uq_cost_recipe_name_form"),
    )
    op.create_index("ix_cost_recipe_product_name", "cost_recipe", ["product_name"])

    # ── 4. 레시피 라인 — S2에서 채운다 ─────────────────────────────────
    op.create_table(
        "cost_recipe_line",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recipe_id", sa.Integer(), nullable=False),
        # material_id / ledger_item_name 둘 중 정확히 하나만 채워진다(판정은 서비스층).
        sa.Column("material_id", sa.Integer(), nullable=True),
        sa.Column("ledger_item_name", sa.String(length=200), nullable=True),
        # 매수다 — 3매 제품 = 3(원가표의 「매입」 열). 계약 §5-1.
        sa.Column("quantity", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["recipe_id"], ["cost_recipe.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["material_id"], ["cost_material.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cost_recipe_line_recipe_id", "cost_recipe_line", ["recipe_id"])
    op.create_index("ix_cost_recipe_line_material_id", "cost_recipe_line", ["material_id"])

    # ── 5. SKU → 레시피 링크 — S2에서 채운다 ───────────────────────────
    op.create_table(
        "cost_recipe_link",
        sa.Column("id", sa.Integer(), nullable=False),
        # 문자열 참조다 — FK를 걸지 않는다(계약 B와 같은 이유: 원가 층 미접촉).
        sa.Column("internal_sku", sa.String(length=50), nullable=False),
        sa.Column("recipe_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="draft"),
        sa.Column("source", sa.String(length=10), nullable=False, server_default="manual"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["recipe_id"], ["cost_recipe.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("internal_sku", "recipe_id", name="uq_cost_recipe_link_sku_recipe"),
    )
    op.create_index("ix_cost_recipe_link_internal_sku", "cost_recipe_link", ["internal_sku"])
    op.create_index("ix_cost_recipe_link_recipe_id", "cost_recipe_link", ["recipe_id"])

    # ── 6. 표준원가 계산 결과 (레시피 grain) — S2에서 채운다 ────────────
    op.create_table(
        "cost_standard",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recipe_id", sa.Integer(), nullable=False),
        sa.Column("price_rule", sa.String(length=20), nullable=False, server_default="latest"),
        # nullable — 「단가 미확정이라 계산 못 함」과 「0원」은 다른 사실이다(계약 §2-7).
        sa.Column("std_cost_ex_vat", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("std_cost_inc_vat", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("breakdown", sa.Text(), nullable=True),
        sa.Column("computed_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["recipe_id"], ["cost_recipe.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("recipe_id", "price_rule", name="uq_cost_standard_recipe_rule"),
    )
    op.create_index("ix_cost_standard_recipe_id", "cost_standard", ["recipe_id"])

    # ── 7. 설정 ───────────────────────────────────────────────────────
    op.create_table(
        "cost_setting",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("value", sa.String(length=200), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )

    # ── 초기 행 (계약 §5-1) ────────────────────────────────────────────
    # ★`valuation_method`의 confirmed=False가 핵심이다 — 「무신고 시 법정 기본값이라 이 값」과
    #   「우리가 신고한 값이 이것」은 다른 사실이고, 화면이 그 차이를 자백해야 한다(계약 §9-1).
    #   「선입선출이 우리 신고 방법」이라고 확정 기재하는 것은 금지선이다(계약 §3).
    op.bulk_insert(
        sa.table(
            "cost_setting",
            sa.column("key", sa.String),
            sa.column("value", sa.String),
            sa.column("confirmed", sa.Boolean),
            sa.column("note", sa.Text),
        ),
        [
            {
                "key": "valuation_method",
                "value": "fifo",
                "confirmed": False,
                "note": (
                    "법인세법 시행령 §74④ 무신고 시 법정 기본값. **우리 신고 내역 미확인** — "
                    "재고자산 평가조정명세서(시행규칙 별지 제39호서식) 「③신고방법」 칸이 답이다."
                ),
            },
            {
                "key": "standard_price_rule",
                "value": "latest",
                "confirmed": True,
                "note": (
                    "표준원가 단가로 어느 로트를 쓰나 — 기본 `latest`(최신 확정 로트). "
                    "대안 `moving_avg_n`. 층1은 7.14 표준원가법이라 «실제와 유사»하면 되고, "
                    "실측상 최신 로트가 실제에 가장 가깝다(계약 §4)."
                ),
            },
        ],
    )

    # ── cleaning kit 종 1개 (계약 §9-3: 「미확정」으로 표면화) ────────────
    # ★단가는 넣지 않는다 — 단가는 원장에서 «사람이 연결»할 때 생긴다(계약 §5-2).
    #   `excel_label`을 NULL로 두는 것이 이 행의 요점이다: 168원/개가 엑셀의 어느 항목인지
    #   불명이고(「부자재(밀대외) 22」·「알콜솜 2EA 60」·「패키지 98」 어느 것과도 안 맞음),
    #   원가 정본 「제품 원가표」에도 대응 항목이 없음을 2026-08-22 실측으로 재확인했다.
    #   억지로 라벨을 붙이면 추론이 확인분으로 굳는다(교훈 #204) — 비워 두고 화면이 자백한다.
    op.bulk_insert(
        sa.table(
            "cost_material",
            sa.column("name", sa.String),
            sa.column("unit", sa.String),
            sa.column("category", sa.String),
            sa.column("status", sa.String),
            sa.column("excel_label", sa.String),
            sa.column("match_rule", sa.String),
            sa.column("note", sa.Text),
        ),
        [
            {
                "name": "cleaning kit",
                "unit": "ea",
                "category": "부자재",
                "status": "unconfirmed",
                "excel_label": None,
                "match_rule": "cleaning kit",
                "note": (
                    "수입 원장에서 `line_type='material'`로 배부받고 있는 품목(계약 B). "
                    "엑셀 부자재 항목과의 대응은 **미확정**이다(계약 §9-3) — Jino가 부자재 탭에서 "
                    "처분한다. 단가는 엑셀이 아니라 원장 로트에서 온다."
                ),
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("cost_setting")
    op.drop_index("ix_cost_standard_recipe_id", table_name="cost_standard")
    op.drop_table("cost_standard")
    op.drop_index("ix_cost_recipe_link_recipe_id", table_name="cost_recipe_link")
    op.drop_index("ix_cost_recipe_link_internal_sku", table_name="cost_recipe_link")
    op.drop_table("cost_recipe_link")
    op.drop_index("ix_cost_recipe_line_material_id", table_name="cost_recipe_line")
    op.drop_index("ix_cost_recipe_line_recipe_id", table_name="cost_recipe_line")
    op.drop_table("cost_recipe_line")
    op.drop_index("ix_cost_recipe_product_name", table_name="cost_recipe")
    op.drop_table("cost_recipe")
    op.drop_index(
        "ix_cost_material_price_import_invoice_line_id", table_name="cost_material_price"
    )
    op.drop_index("ix_cost_material_price_material_id", table_name="cost_material_price")
    op.drop_table("cost_material_price")
    op.drop_table("cost_material")
