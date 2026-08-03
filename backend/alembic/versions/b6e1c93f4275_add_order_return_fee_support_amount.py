"""반품 배송비 네이버 지원액 — orders.return_fee_support_amount

a2d5f80c34b7에서 반품 배송 손익을 배선하며 **고객 청구액만** 봤는데, 그것만 보면 N배송 반품이
정확히 반대로 읽힌다: 고객 청구 0 = "우리가 회수비를 다 떠안았다"로 보이지만, 실제로는 네이버가
`claimDeliveryFeeSupportType=MEMBERSHIP_ARRIVAL_GUARANTEE`로 **5,500원을 대신 부담**한다
(라이브 N배송 반품 2건 실측, 일반배송은 지원 0).

Revision ID: b6e1c93f4275
Revises: a2d5f80c34b7
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b6e1c93f4275"
down_revision: Union[str, Sequence[str], None] = "a2d5f80c34b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("return_fee_support_amount", sa.Numeric(12, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "return_fee_support_amount")
