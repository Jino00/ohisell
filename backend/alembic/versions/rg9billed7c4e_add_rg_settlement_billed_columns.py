"""add rg settlement billed columns (S9 — 청구 근거를 추론에서 정산서 실측으로)

Revision ID: rg9billed7c4e
Revises: b6e1c93f4275
Create Date: 2026-08-03 00:00:00.000000

coupang_rg_settlement_fee에 정산 엑셀이 **직접 알려주는** 청구 근거 3컬럼을 추가한다(additive,
nullable). 감사(S8)가 지금까지 추론하던 두 값이 정산서 상세에 원래부터 있었다(ref 17 §8-1):

  billed_size_type    개별포장사이즈 — 쿠팡이 **청구에 쓴** 사이즈 등급.
                      종전엔 배송비 금액이 어느 등급 최소금액대에 맞는지로 역추정했다.
  billed_order_count  그 (옵션·주기·비용종류)의 distinct 주문ID 수 — 배송비 분모(주문당 부과).
  billed_quantity     Σ 판매수량 — 입출고비 분모(수량당 부과).

왜 필요한가(2026-08-03 실사고): 감사는 분모를 `coupang_rg_order_item`(**결제일** basis)에서
세어 **매출인식일** 기준 정산주기에 맞췄다. 두 basis가 어긋나고 주문 수집 창(2026-06-03 시작·
백필 2026-05-05)이 정산 수집 창(2026-03-16~)보다 짧아, 주기 4개 청구액을 주문 2건으로 나눠
단가를 2배로 부풀렸다 → '실측 vs 청구 불일치' 오탐 4건(LESSONS #92·#93).
정산서에서 읽으면 분자와 분모가 **같은 파일·같은 basis**라 그 오차 자체가 사라진다.

nullable인 이유: 계정 단위 row(status/api 수집, vendor_item_id='')엔 이 값이 없고, 이미
적재된 옵션 row도 재수집 전까지 NULL이다. 감사는 NULL이면 종전 경로로 폴백한다(무중단).
회계 영향 없음 — amount를 건드리지 않으므로 net_profit 불변(D-17 감사는 읽기 전용).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'rg9billed7c4e'
down_revision: Union[str, None] = 'b6e1c93f4275'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # nullable 추가라 SQLite에서도 batch_alter 없이 안전(테이블 재작성 불필요).
    op.add_column(
        "coupang_rg_settlement_fee",
        sa.Column("billed_size_type", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "coupang_rg_settlement_fee",
        sa.Column("billed_order_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "coupang_rg_settlement_fee",
        sa.Column("billed_quantity", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("coupang_rg_settlement_fee", "billed_quantity")
    op.drop_column("coupang_rg_settlement_fee", "billed_order_count")
    op.drop_column("coupang_rg_settlement_fee", "billed_size_type")
