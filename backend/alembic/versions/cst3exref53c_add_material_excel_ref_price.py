"""D-CPP-53 (S2): 부자재 종에 «엑셀 참고값» 1칸 — 단가가 아니다

계약 `docs/PLAN_cost-menu-standard-cost.md` · 선행 리비전 `cst2snap53b`(같은 계약 S1).

## 왜 필요한가

S2는 원가 정본 엑셀에서 **구성(부자재 목록·수량)**을 읽는다. 그런데 그 엑셀에는 숫자도 함께
적혀 있고, 화면은 그것을 **참고로 보여줘야** 한다(계약 §3 금지선: *"화면이 엑셀 값을 «참고»로
보여주는 것까지는 허용"*). Jino가 그걸 보고 「채택」을 눌러야 비로소 단가가 된다.

문제는 «어디에 두느냐»다. 자유 텍스트(`note`)에 `엑셀 참고단가: 600`처럼 적어 두면 나중에
그것을 다시 **파싱**해야 하고, 파싱은 조용히 깨진다. 그래서 이름이 스스로 정체를 말하는
컬럼 하나를 둔다 — `excel_ref_price`. 「ref」가 이것이 참고값이지 단가가 아님을 말한다.

## ★이 컬럼이 «단가가 아닌» 이유는 구조에 있다

표준원가 계산(`services/cost_menu/standard_cost.py`)이 읽는 것은 **`cost_material_price` 행**
뿐이다. 이 컬럼은 그 테이블에 있지 않으므로 **계산 경로에 원리적으로 닿지 않는다.**
「안 쓰기로 했다」가 아니라 「쓸 수 없는 자리에 뒀다」다 — 규율을 주석이 아니라 배치가 지킨다.

Jino가 화면에서 「엑셀 참고값 채택」을 누르면 그때 이 값이 `cost_material_price`에
`source='manual'` 행으로 **복사**되고, 그 행의 `note`에 출처(파일명·열)가 남는다. 그것이
계약 §3이 허용한 「Jino가 입력·승인한 값」의 경로다.

★**순수 추가다** — nullable 컬럼 1개라 구코드가 이 마이그 뒤에도 그대로 돈다
(`safe_deploy.sh --migrate`의 정상 순서로 나간다. 컬럼 삭제형이 아니다).
★**백필 없다** — 기존 종은 엑셀에서 온 것이 아니라 원장에서 왔다(cleaning kit). 없는 근거를
지어내지 않는다(계약 §2-7).

Revision ID: cst3exref53c
Revises: cst2snap53b
Create Date: 2026-08-23 KST
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "cst3exref53c"
down_revision: Union[str, None] = "cst2snap53b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cost_material",
        sa.Column("excel_ref_price", sa.Numeric(precision=14, scale=2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cost_material", "excel_ref_price")
