"""rename_rg_fee_type_fulfillment_to_delivery

Revision ID: h2i3j4k5l6m7
Revises: g1h2i3j4k5l6
Create Date: 2026-06-09 12:00:00.000000

RG 수수료 회계 자동화 트랙 S5 — D-10 라이브 확정(2026-06-09, 원칙22).
totalFulfillmentFeeDeductionAmount는 "풀필먼트 합계"가 아니라 **배송비(delivery)뿐**임을 라이브로 확정
(레퍼런스 17 §7: 배송 130,599 + 입출고 75,489 + 보관 168 = J 206,256). fee_type 'fulfillment'→'delivery' 리네임.

★데이터 마이그레이션: 기존 fee_type='fulfillment' 행을 'delivery'로 UPDATE해 stale 이중계상을 코드레벨에서
차단(Codex S5 needs-changes 지적1 수용). clear+재싱크 대신 데이터 보존 + 카드 reconcile 유지.
'delivery'는 신규 fee_type이라 unique(account_key, from, to, fee_type) 충돌 없음.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'h2i3j4k5l6m7'
down_revision: Union[str, None] = 'g1h2i3j4k5l6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE coupang_rg_settlement_fee SET fee_type='delivery' WHERE fee_type='fulfillment'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE coupang_rg_settlement_fee SET fee_type='fulfillment' WHERE fee_type='delivery'"
    )
