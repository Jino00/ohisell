"""D-CPP-36: 쿠팡 Wing 판매분석 «일자×옵션» 정본 매출 축 신설

왜 새 테이블인가(요약축 `coupang_vendor_summary_daily` 확장이 아니라):
  grain이 다르다 — 요약축은 (일자, 계정, 등록유형), 이쪽은 (계정, 일자, 옵션ID).
  한 테이블에 섞으면 합산이 이중계상되고, 무엇보다 **요약축이 독립된 대조 상대로 남아야**
  보존식(Σ옵션 == 요약)이 자기 자신을 대조하는 무의미한 검사가 되지 않는다.

grain: (account_key, sale_date, vendor_item_id) — UNIQUE. 같은 날짜를 다시 받으면 교체(upsert).
소스: m-wing.coupang.com /tenants/rfm-ss/api/business-insight/vi-detail-search
  (2026-08-10 라이브 정찰. 요약축과 같은 세션·같은 same-origin fetch 헬퍼를 쓴다.)

raw_metrics를 왜 남기나: 응답의 UV/PV/검색량/전환 지표는 이번 범위가 아니지만 재조회가
  비싸다(봇 감지 하에서 «조회만, 천천히»). 원본을 JSON으로 동봉해 두면 나중 슬라이스가
  과거를 다시 긁지 않아도 된다.

Revision ID: vi1s2a3x4b5c
Revises: e4c7a1b8d206
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "vi1s2a3x4b5c"
down_revision: Union[str, None] = "e4c7a1b8d206"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "coupang_vendor_item_sales_daily",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sale_date", sa.Date(), nullable=False),
        sa.Column("account_key", sa.String(length=20), nullable=False),
        # 조인축 — coupang_revenue_fee.vendor_item_id · orders.platform_product_id와 같은 문자열 옵션ID.
        # String(20)은 이 리포의 vendor_item_id 관례다(정수로 두면 조인마다 캐스팅이 필요해진다).
        sa.Column("vendor_item_id", sa.String(length=20), nullable=False),
        sa.Column("registration_type", sa.String(length=10), nullable=False),
        sa.Column("item_name", sa.String(length=300), nullable=True),
        sa.Column("product_id", sa.String(length=20), nullable=True),
        # gmv/units_sold는 음수가 정상이다(쿠팡 GMV = 판매액 − 환불액. 요약축이 같은 정책).
        sa.Column("gmv", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("units_sold", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_orders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("raw_metrics", sa.Text(), nullable=True),
        sa.Column("last_refresh", sa.String(length=30), nullable=True),
        sa.Column("synced_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_key", "sale_date", "vendor_item_id",
            name="uq_coupang_vendor_item_sales_daily",
        ),
    )
    op.create_index(
        "ix_coupang_vendor_item_sales_daily_sale_date",
        "coupang_vendor_item_sales_daily", ["sale_date"],
    )
    op.create_index(
        "ix_coupang_vendor_item_sales_daily_account_key",
        "coupang_vendor_item_sales_daily", ["account_key"],
    )
    # 보존식(계정·일자별 Σ옵션)과 옵션 조인이 둘 다 이 축을 탄다.
    op.create_index(
        "ix_coupang_vendor_item_sales_daily_vendor_item_id",
        "coupang_vendor_item_sales_daily", ["vendor_item_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_coupang_vendor_item_sales_daily_vendor_item_id",
        table_name="coupang_vendor_item_sales_daily",
    )
    op.drop_index(
        "ix_coupang_vendor_item_sales_daily_account_key",
        table_name="coupang_vendor_item_sales_daily",
    )
    op.drop_index(
        "ix_coupang_vendor_item_sales_daily_sale_date",
        table_name="coupang_vendor_item_sales_daily",
    )
    op.drop_table("coupang_vendor_item_sales_daily")
