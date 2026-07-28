"""로켓배송(1P) 파서가 버리던 원본 컬럼 2개 복원 (트랙 rocket-1p)

원본 DOM에 존재하는데 매핑 누락으로 폐기되던 컬럼을 배선한다. ADD COLUMN(nullable)만 —
기존 행·기존 소비자 무영향(회귀 0), SQLite 호환.

① coupang_rocket_settlement.tax_invoice_transmitted (Boolean, nullable)
   정산 테이블 **마지막 링크 컬럼**(헤더명이 빈 문자열, ref 20 §4 표 #16)에서 전자세금계산서
   전송상태 파싱. True='전송성공' 표기 / False=표기 없음(미전송) / NULL=셀 부재·미관측 토큰.

② coupang_rocket_purchase_order_item.vendor_confirmed_qty (Integer, nullable)
   발주상세 Table[7] 인덱스 5 '업체납품가능수량'(ref 20b §2). PO그레인
   sumOfVendorConfirmedQty의 per-SKU 판. 기존 적재분은 NULL(재수집 시 채워짐).

Revision ID: f6a8c0b2d4e6
Revises: e5f7a9c1b3d5
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f6a8c0b2d4e6'
down_revision: Union[str, None] = 'e5f7a9c1b3d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'coupang_rocket_settlement',
        sa.Column('tax_invoice_transmitted', sa.Boolean(), nullable=True),
    )
    op.add_column(
        'coupang_rocket_purchase_order_item',
        sa.Column('vendor_confirmed_qty', sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('coupang_rocket_purchase_order_item', 'vendor_confirmed_qty')
    op.drop_column('coupang_rocket_settlement', 'tax_invoice_transmitted')
