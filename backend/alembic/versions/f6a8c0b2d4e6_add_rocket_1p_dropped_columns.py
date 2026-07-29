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
   전체**가 실패한다(실측 확인). app/main.py 는 인프로세스 마이그레이션을 하지 않는다.
   → 이 실측이 근거가 되어 safe_deploy.sh 에 alembic 순서 가드가 추가됐다(main `a516951`,
     2026-07-28). 이제 `--migrate` 를 주면 스크립트가 위 순서를 강제한다(마이그 대기 상태에서
     코드 배포/재시작은 거부, upgrade 실패 시 코드 미전송).

★백필 경계: 기존 적재분은 NULL이며 자동 백필되지 않는다. 페처 수집 창 안에서 재수집될 때만
   채워진다 — 발주상세 `po_detail_days=45`(+ `po_detail_max=80`건 캡), 정산 `settle_days=90`
   (tools/rocket_supplier_fetcher.py). 그 이전 데이터는 별도 일회성 백필이 필요하다.

★재연결 이력(2026-07-28): 최초 작성 시 부모는 `e5f7a9c1b3d5` 였다. 그런데 병행 세션의
   promo-pnl 마이그레이션 `a1c3e5f7b9d1` 이 **같은 부모**를 물고 먼저 main에 병합돼 head가
   되었다. 그대로 두면 이 브랜치 병합 시 head가 2개가 되므로(각 브랜치의 `alembic heads` 는
   브랜치-로컬 검사라 이 형제 관계를 못 잡는다), 나중에 병합하는 쪽인 이 파일의 부모를
   `a1c3e5f7b9d1` 로 재연결했다(merge revision 대신 직렬 재연결). LESSONS #49 참조.
   ★그 뒤 그 promo 마이그레이션이 **ID 충돌로 개명**되어(prod에 같은 ID의 다른 마이그가 이미 적용)
   이 파일의 부모도 `c2998cfe1f7c` 로 다시 옮겼다. LESSONS #50 참조.

Revision ID: f6a8c0b2d4e6
Revises: c2998cfe1f7c
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f6a8c0b2d4e6'
down_revision: Union[str, None] = 'c2998cfe1f7c'
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
