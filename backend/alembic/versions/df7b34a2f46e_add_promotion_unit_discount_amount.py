"""add coupang_rocket_promotion.unit_discount_amount (D-CPP-7 수기 단위 할인액)

트랙: docs/tracks/active/track_coupang-promo-pnl.md (D-CPP-7)

가산 변경 1건(ADD COLUMN, nullable) — 기존 행·기존 소비자 무영향, net_profit 불변.

왜 컬럼이 필요한가: 공급자허브 프로모션 목록/상세 API에는 **상품별·단위 할인액이 없다**
  (2026-07-28 라이브 실측 — discountBudget=총예산, supplierFundRate=분담%, discountType=할인방식).
  D-CPP-7(Jino 확정): 한 프로모션의 할인액은 상품이 여러 개여도 **하나의 값**이다 →
  프로모션당 1칸을 수기(ops PATCH)로 받는다. 페처는 이 칸을 절대 쓰지 않는다.

Revision ID: df7b34a2f46e
Revises: f6a8c0b2d4e6
Create Date: 2026-07-28

★revision ID는 `uuid4().hex[:12]`로 뽑았다 — 손으로 짓지 않는다(LESSONS #50).
  이 마이그레이션의 첫 판은 손으로 지은 `b2d4f6a8c0e2`(= 이미 충돌한 `f6a8c0b2d4e6`/
  `f6a8c0e2b4d6`와 같은 hex 문자 재배열)였고, `a1c3e5f7b9d1`을 부모로 물었다. 그런데
  `a1c3e5f7b9d1`은 main·prod에서 **다른 마이그레이션**(status_reason+delivery merge rev)이며,
  이 트랙의 Phase 1은 그 충돌 때문에 `c2998cfe1f7c`로 개명된 뒤였다. 그대로 두면 ①중복 정의로
  alembic 전체 사망 ②형제 head 2개 ③`coupang_rocket_promotion`을 만드는 `c2998cfe1f7c`보다
  먼저 실행돼 ADD COLUMN이 없는 테이블을 친다. → main head(`f6a8c0b2d4e6`) 뒤에 다시 물렸다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'df7b34a2f46e'
down_revision: Union[str, None] = 'f6a8c0b2d4e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'coupang_rocket_promotion',
        sa.Column('unit_discount_amount', sa.Numeric(12, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('coupang_rocket_promotion', 'unit_discount_amount')
