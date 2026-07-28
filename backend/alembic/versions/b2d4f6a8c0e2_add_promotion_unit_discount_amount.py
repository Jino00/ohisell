"""add coupang_rocket_promotion.unit_discount_amount (D-CPP-7 수기 단위 할인액)

트랙: docs/tracks/active/track_coupang-promo-pnl.md (D-CPP-7)

가산 변경 1건(ADD COLUMN, nullable) — 기존 행·기존 소비자 무영향, net_profit 불변.

왜 컬럼이 필요한가: 공급자허브 프로모션 목록/상세 API에는 **상품별·단위 할인액이 없다**
  (2026-07-28 라이브 실측 — discountBudget=총예산, supplierFundRate=분담%, discountType=할인방식).
  D-CPP-7(Jino 확정): 한 프로모션의 할인액은 상품이 여러 개여도 **하나의 값**이다 →
  프로모션당 1칸을 수기(ops PATCH)로 받는다. 페처는 이 칸을 절대 쓰지 않는다.

Revision ID: b2d4f6a8c0e2
Revises: a1c3e5f7b9d1
Create Date: 2026-07-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b2d4f6a8c0e2'
down_revision: Union[str, None] = 'a1c3e5f7b9d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'coupang_rocket_promotion',
        sa.Column('unit_discount_amount', sa.Numeric(12, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('coupang_rocket_promotion', 'unit_discount_amount')
