"""프로브 테이블에서 settle_decision_type 삭제 — 단건 조회로는 얻을 수 없는 축이었다

d4f6a8c0b2e5가 만든 naver_claim_settlement_probe는 settleDecisionType(SETTLED/UNSETTLED/
BEFORE_CANCEL)을 축으로 잡았는데, 라이브 실측 결과 **주문번호 단건 조회에서는 그 축을 얻을 수
없다**:
  - 요청: orderId + periodType → 400 "periodType 값은 orderId, productOrderId 값과 같이
    입력될 수 없습니다"(settleDecisionType은 periodType=PAY_DATE일 때만 의미를 가진다).
  - 응답: 공식 스펙(get-v1-pay-settle-settle-case.md)의 elements에 settleDecisionType 필드가
    아예 없다.
그래서 유형 축을 응답의 settleType(NORMAL_SETTLE_ORIGINAL / NORMAL_SETTLE_BEFORE_CANCEL / …)
으로 옮기고 이 컬럼을 삭제한다. UNIQUE도 (product_order_id, product_order_type, settle_type,
observed_date)로 좁힌다.

★drop column이 아니라 drop+create인 이유: SQLite에서 컬럼 삭제 + UNIQUE 재정의를 한 번에
  하려면 어차피 테이블 재작성이고, 이 테이블은 **prod에서 0행**이다(잡이 400으로 전손해
  한 번도 적재된 적이 없다 — 그 사실을 배포 전 라이브로 확인했다). 보존할 데이터가 없으므로
  재작성이 가장 단순하고 확실하다. 데이터가 있었다면 batch_alter_table로 옮겨야 한다.

Revision ID: e7b2c9d4a610
Revises: d4f6a8c0b2e5
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7b2c9d4a610"
down_revision: Union[str, Sequence[str], None] = "d4f6a8c0b2e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEXES = (
    ("ix_naver_claim_settlement_probe_order_number", ["order_number"]),
    ("ix_naver_claim_settlement_probe_delivery_attribute_type", ["delivery_attribute_type"]),
    ("ix_naver_claim_settlement_probe_observed_date", ["observed_date"]),
)


def _create(*, with_decision_type: bool) -> None:
    columns = [
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_number", sa.String(length=40), nullable=False),
        sa.Column("product_order_id", sa.String(length=40), nullable=False),
        sa.Column("delivery_attribute_type", sa.String(length=40), nullable=True),
        sa.Column("is_membership", sa.Boolean(), nullable=False),
        sa.Column("order_status", sa.String(length=20), nullable=False),
        sa.Column("product_order_type", sa.String(length=40), nullable=False),
        sa.Column("settle_type", sa.String(length=40), nullable=False),
        sa.Column("pay_settle_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("settle_expect_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("pay_date", sa.String(length=10), nullable=True),
        sa.Column("settle_expect_date", sa.String(length=10), nullable=True),
        sa.Column("observed_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
    ]
    unique = ["product_order_id", "product_order_type", "settle_type", "observed_date"]
    if with_decision_type:
        columns.insert(6, sa.Column("settle_decision_type", sa.String(length=20), nullable=False))
        unique.insert(3, "settle_decision_type")

    op.create_table(
        "naver_claim_settlement_probe",
        *columns,
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(*unique, name="uq_naver_claim_settlement_probe"),
    )
    for name, cols in _INDEXES:
        op.create_index(name, "naver_claim_settlement_probe", cols)


def _drop() -> None:
    for name, _cols in _INDEXES:
        op.drop_index(name, table_name="naver_claim_settlement_probe")
    op.drop_table("naver_claim_settlement_probe")


def upgrade() -> None:
    _drop()
    _create(with_decision_type=False)


def downgrade() -> None:
    _drop()
    _create(with_decision_type=True)
