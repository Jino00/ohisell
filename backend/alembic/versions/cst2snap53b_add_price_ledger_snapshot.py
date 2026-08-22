"""D-CPP-53: 단가 행에 «저장 시점 원장 신원» 스냅샷 2칸 (적대 리뷰 1R P1-2)

계약 `docs/PLAN_cost-menu-standard-cost.md` · 선행 리비전 `cst1menu53a`(같은 슬라이스).

## 왜 필요한가

`cost_material_price`가 `import_invoice_line_id`**만** 들고 있으면 「이 단가가 어느 품목에서
왔나」를 지금 다시 확인할 수 없다. 계약 B `_replace_lines`가 라인을 지우고 다시 넣을 때
**SQLite rowid가 재사용**되므로, 같은 id가 나중에 **다른 품목**을 가리킨다(적대 리뷰 실증:
`import_invoice_line_id=15`가 cleaning kit → Glass_iP12promax로 바뀜). 저장 시점의 품목명과
수입건 id를 함께 남겨 두면 조회 시점 재검사(`services/cost_menu/ledger_check.py`)가 그
바뀜을 **화면에 자백**시킬 수 있다.

★**순수 추가다** — 컬럼 2개가 전부이고 둘 다 nullable이라 구코드가 이 마이그 뒤에도 그대로
돈다(컬럼 삭제형이 아니므로 `safe_deploy.sh --migrate`의 정상 순서로 나간다).

★**기존 행은 원장에서 되채운다**(backfill). 못 채우는 행(원장 라인이 이미 사라진 고아)은
NULL로 남고, 재검사가 그 행을 「원장 라인 없음」으로 표면화한다 — 억지로 값을 지어내지
않는다(계약 §2-7).

Revision ID: cst2snap53b
Revises: cst1menu53a
Create Date: 2026-08-23 KST
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "cst2snap53b"
down_revision: Union[str, None] = "cst1menu53a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cost_material_price",
        sa.Column("linked_item_name", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "cost_material_price",
        sa.Column("linked_shipment_id", sa.Integer(), nullable=True),
    )
    # 이미 연결된 행이 있으면 지금 원장이 말하는 품목명·수입건으로 채운다. 상관 서브쿼리라
    # SQLite·PostgreSQL 양쪽에서 같은 문장이 돈다.
    op.execute(
        """
        UPDATE cost_material_price
           SET linked_item_name = (
                   SELECT l.item_name FROM import_invoice_line l
                    WHERE l.id = cost_material_price.import_invoice_line_id
               ),
               linked_shipment_id = (
                   SELECT l.shipment_id FROM import_invoice_line l
                    WHERE l.id = cost_material_price.import_invoice_line_id
               )
         WHERE import_invoice_line_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("cost_material_price", "linked_shipment_id")
    op.drop_column("cost_material_price", "linked_item_name")
