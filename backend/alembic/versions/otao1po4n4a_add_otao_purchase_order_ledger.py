"""D-INV-1·3: OTAO 발주 원장 3테이블 신설 (계약 §4 S1)

계약 `docs/contracts/CONTRACT_inventory_unified.md` §4 S1 · 트랙 `docs/tracks/active/track_inventory-management.md`
앵커 `.claude/anchors/79e11935-ff73-4d48-91c9-0f15c9834a13.md` · 체인 `발주예측` n=4.

S1은 콘솔 발주 메뉴에서 SKU별 **발주 누계 · 픽업 누계 · OTAO 예약 잔량**을 3칸으로 보여준다.
그중 「발주 누계」의 SKU 라인이 **어디에도 없었다** — ECOUNT 발주서조회 API는 헤더 그레인이라
`PROD_CD`를 안 주고(S0-a 판정, 1,622건 전건), SKU 단위 원천은 발주서 PDF뿐인데 그 PDF는
Jino의 Google Drive 로컬 동기화 폴더에 있어 **prod가 못 읽는다.** 그래서 파싱 결과를 심는다.

★**순수 추가다** — 기존 테이블을 하나도 건드리지 않는다. 특히 `import_shipment`·
`import_invoice_line`(계약 A′/B 소관)은 **무접촉**이고 우리는 읽기 전용 소비자로 남는다
(계약 §3-8 금지선). `internal_sku`를 우리가 채우지 않는 대신 사전을
`otao_item_name_map`에 따로 둔다 — prod 실측 `internal_sku` **0/158(0%)**이라 그 컬럼만
바라보면 픽업 누계 칸이 원리적으로 못 서기 때문이다(D-INV-1, Jino 2026-08-25 22:52 승인).

★**Boolean에 정수 리터럴을 쓰지 않는다** (교훈 #341): `server_default=1`은 SQLite에선 통과하고
**PostgreSQL에선 타입 에러로 트랜잭션이 통째로 롤백된다** — 로컬·CI가 전부 초록인 채 prod에서만
죽는 모양이다. 여기서는 `sa.false()`를 쓴다(방언별로 FALSE/0으로 옳게 컴파일된다).

★`serial`에 unique를 걸지 않는다 — **중복이 정상이다.** 실측: PDF 121개 중 발주서 95건이
고유 발주번호 **66**개에 대응하고(28개 번호가 파일 둘 이상), 그중 수량까지 다른 개정본이 4건이다.
정본은 버리는 방식이 아니라 `is_authoritative` 플래그로 가른다(D-INV-3) — 버리면 「왜 이
숫자인가」를 되짚을 수 없고, 개정 이력 자체가 인사이트이기 때문이다. 멱등성은 `content_sha256`
unique가 준다.

Revision ID: otao1po4n4a
Revises: cst4pick59a
Create Date: 2026-08-25 KST
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "otao1po4n4a"
down_revision: Union[str, None] = "cst4pick59a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "otao_purchase_order",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("serial", sa.String(length=30), nullable=False),
        sa.Column("order_date", sa.Date(), nullable=True),
        sa.Column("source_kind", sa.String(length=12), nullable=False),
        sa.Column("source_file", sa.String(length=300), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "is_authoritative", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("supersede_reason", sa.String(length=200), nullable=True),
        sa.Column("header_qty", sa.Integer(), nullable=True),
        sa.Column("total_amount", sa.Numeric(16, 2), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("remarks", sa.String(length=200), nullable=True),
        sa.Column("parsed_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_sha256", name="uq_otao_po_sha"),
    )
    op.create_index("ix_otao_purchase_order_serial", "otao_purchase_order", ["serial"])
    op.create_index("ix_otao_purchase_order_order_date", "otao_purchase_order", ["order_date"])
    op.create_index(
        "ix_otao_purchase_order_is_authoritative", "otao_purchase_order", ["is_authoritative"]
    )

    op.create_table(
        "otao_purchase_order_line",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("product_code", sa.String(length=50), nullable=False),
        sa.Column("name_ko", sa.String(length=300), nullable=True),
        # B형 발주서엔 영문상품명 칸이 없다 — NULL이 정상이고, 한글명에서 지어내지 않는다.
        sa.Column("name_en", sa.String(length=300), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("unit_price", sa.Numeric(14, 4), nullable=True),
        sa.Column("amount", sa.Numeric(16, 2), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["otao_purchase_order.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "seq", name="uq_otao_po_line_seq"),
    )
    op.create_index("ix_otao_po_line_order_id", "otao_purchase_order_line", ["order_id"])
    op.create_index("ix_otao_po_line_product_code", "otao_purchase_order_line", ["product_code"])
    op.create_index("ix_otao_po_line_name_en", "otao_purchase_order_line", ["name_en"])

    op.create_table(
        "otao_item_name_map",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("raw_name", sa.String(length=300), nullable=False),
        # NULL = 미확정. 화면에 「매핑 필요」로 드러낸다(계약 §2-9·§3-6) — 조용히 빼면 발주 누락,
        # 조용히 넣으면 발주 오염이다. 둘 다 침묵이 병이다.
        sa.Column("product_code", sa.String(length=50), nullable=True),
        sa.Column("match_kind", sa.String(length=16), nullable=False),
        sa.Column("evidence", sa.String(length=300), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("raw_name", name="uq_otao_item_name_map_raw"),
    )
    op.create_index("ix_otao_item_name_map_product_code", "otao_item_name_map", ["product_code"])


def downgrade() -> None:
    op.drop_index("ix_otao_item_name_map_product_code", table_name="otao_item_name_map")
    op.drop_table("otao_item_name_map")
    op.drop_index("ix_otao_po_line_name_en", table_name="otao_purchase_order_line")
    op.drop_index("ix_otao_po_line_product_code", table_name="otao_purchase_order_line")
    op.drop_index("ix_otao_po_line_order_id", table_name="otao_purchase_order_line")
    op.drop_table("otao_purchase_order_line")
    op.drop_index("ix_otao_purchase_order_is_authoritative", table_name="otao_purchase_order")
    op.drop_index("ix_otao_purchase_order_order_date", table_name="otao_purchase_order")
    op.drop_index("ix_otao_purchase_order_serial", table_name="otao_purchase_order")
    op.drop_table("otao_purchase_order")
