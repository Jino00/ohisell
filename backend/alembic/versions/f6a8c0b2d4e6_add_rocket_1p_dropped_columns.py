"""로켓배송(1P) 파서가 버리던 원본 컬럼 2개 복원 (트랙 rocket-1p)

원본 DOM에 존재하는데 매핑 누락으로 폐기되던 컬럼을 배선한다. ADD COLUMN(nullable)만 —
기존 행·기존 소비자 무영향(회귀 0), SQLite 호환.

① coupang_rocket_settlement.tax_invoice_transmitted (Boolean, nullable)
   정산 테이블 **마지막 링크 컬럼**(헤더명이 빈 문자열, ref 20 §4 표 #16)에서 전자세금계산서
   전송상태 파싱. True='전송성공' 표기 / False=표기 없음(미전송) / NULL=셀 부재·미관측 토큰.

② coupang_rocket_purchase_order_item.vendor_confirmed_qty (Integer, nullable)
   발주상세 Table[7] 인덱스 5 '업체납품가능수량'(ref 20b §2). PO그레인
   sumOfVendorConfirmedQty의 per-SKU 판.

★배포 순서 (지키지 않으면 로켓 1P 수집 전체가 죽는다):
   ① 이 파일 배포 → ② prod에서 `alembic upgrade head` → ③ models.py 등 코드 배포·restart.
   이유: rocket_supplier_sync 의 _upsert_settlement / ingest_po_items 는 컬럼을 명시하지 않고
   엔티티를 통째로 SELECT 한다 → 신규 컬럼이 DB에 없는 상태로 models.py 만 올라가면
   `OperationalError: no such column` 으로 **신규 필드만이 아니라 정산·발주상세 ingest 경로
   전체**가 실패한다(실측 확인). safe_deploy.sh 에는 alembic 단계가 없고 app/main.py 도
   인프로세스 마이그레이션을 하지 않으므로, 이 순서는 자동으로 보장되지 않는다.

★백필 경계: 기존 적재분은 NULL이며 자동 백필되지 않는다. 페처 수집 창 안에서 재수집될 때만
   채워진다 — 발주상세 `po_detail_days=45`(+ `po_detail_max=80`건 캡), 정산 `settle_days=90`
   (tools/rocket_supplier_fetcher.py). 그 이전 데이터는 별도 일회성 백필이 필요하다.

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
