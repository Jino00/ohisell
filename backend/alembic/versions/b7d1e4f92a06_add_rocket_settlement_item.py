"""로켓1P 계산서 라인 (입고상세내역) — D-CPP-20

계산서 축 일별 매출의 **귀속일을 실측으로** 얻기 위한 테이블. 헤더만으로는 날짜를 못 정한다:
계산서 1건이 평균 5.8개 PO를 묶고 작성일은 실입고일보다 −4~+7일 흔들린다. PO 입고금액 비율로
배분하는 것도 근거가 없다 — 계산서 공급가 합 110,022,496 ↔ 묶인 PO 입고액 합 178,753,941로
**금액이 맞는 계산서가 0건**이다(1PO가 여러 계산서에 걸리고, 계산서가 입고액 전부를 덮지도 않는다).

원천 `/scm/receive/detail`은 **라인마다 입고일자와 금액**을 준다 → 배분도 추정도 필요 없다.
실증(계산서 30608513): 라인 48건 총단가 합 2,489,790원 = 계산서 지급예정금액과 **차이 0원**.

새 테이블만 추가 — 기존 테이블·컬럼 불변이라 구코드를 깨지 않는다.

Revision ID: b7d1e4f92a06
Revises: f4a9c2e70b58
Create Date: 2026-08-06
"""
from alembic import op
import sqlalchemy as sa

revision = "b7d1e4f92a06"
down_revision = "f4a9c2e70b58"
branch_labels = None
depends_on = None

_TABLE = "coupang_rocket_settlement_item"


def _has_table() -> bool:
    return _TABLE in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table():   # 병행 세션이 먼저 올렸을 때 죽지 않게
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("invoice_seq", sa.Integer(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("vendor_id", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=True),
        sa.Column("purchase_order_seq", sa.Integer(), nullable=True),
        sa.Column("sku_id", sa.String(length=30), nullable=True),
        sa.Column("sku_name", sa.String(length=300), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=True),
        sa.Column("center_code", sa.String(length=20), nullable=True),
        sa.Column("tax_type", sa.String(length=20), nullable=True),
        sa.Column("qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unit_price", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("supply_amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("vat", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("total_price", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("synced_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("invoice_seq", "line_no", name="uq_rocket_settle_item"),
    )
    op.create_index("ix_rocket_settle_item_invoice", _TABLE, ["invoice_seq"])
    op.create_index("ix_rocket_settle_item_vendor", _TABLE, ["vendor_id"])
    op.create_index("ix_rocket_settle_item_po", _TABLE, ["purchase_order_seq"])
    op.create_index("ix_rocket_settle_item_sku", _TABLE, ["sku_id"])
    # ★귀속일 인덱스 — 일별 매출 집계가 이 컬럼으로 range scan 한다.
    op.create_index("ix_rocket_settle_item_received", _TABLE, ["received_at"])


def downgrade() -> None:
    if not _has_table():
        return
    op.drop_table(_TABLE)
