"""반품 배송 손익 컬럼 — orders.return_completed_at / return_fee_demand_amount / return_collect_company

반품 건은 매출에서 제외되지만(REVENUE_EXCLUDED) 배송 손익은 실제로 발생한다 — 우리는 출고비와
회수비를 쓰고 고객에게 반품비를 받는다. 종전엔 셋 다 0으로 빠져 있어 **반품이 늘어도 대시보드
이익이 반응하지 않았다.**

세 컬럼 전부 nullable 추가(기존 행 영향 없음). 값은 raw_data에 이미 있는 것을 뽑아 영속화하는
것이라 새 원천이 필요 없다:
  return_completed_at      = return.returnCompletedDate (라이브 86/86건 존재)
  return_fee_demand_amount = return.claimDeliveryFeeDemandAmount (건별 실측, NULL 아님=0 포함)
  return_collect_company   = return.collectDeliveryCompany (라이브 전건 HANJIN)

★NULL과 0을 구분한다: NULL=반품 정보 없음(반품 아님), 0=미청구(실측된 0). 라이브 86건 중
  22건이 실제 미청구다 — 정액으로 채웠으면 그 22건이 통째로 틀린 수입이 됐다.

Revision ID: a2d5f80c34b7
Revises: f3c1d7e9a482
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a2d5f80c34b7"
down_revision: Union[str, Sequence[str], None] = "f3c1d7e9a482"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("return_completed_at", sa.DateTime(), nullable=True))
    op.add_column("orders", sa.Column("return_fee_demand_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column("orders", sa.Column("return_collect_company", sa.String(length=20), nullable=True))
    # 집계가 이 컬럼으로 기간을 자른다(반품 완료일 귀속) → 인덱스 필수.
    op.create_index("ix_orders_return_completed_at", "orders", ["return_completed_at"])


def downgrade() -> None:
    op.drop_index("ix_orders_return_completed_at", table_name="orders")
    op.drop_column("orders", "return_collect_company")
    op.drop_column("orders", "return_fee_demand_amount")
    op.drop_column("orders", "return_completed_at")
