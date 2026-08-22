"""D-CPP-50: 관세를 «배부»에서 «귀속»으로 — duty_rate · is_duty 2컬럼 추가

계약 `docs/PLAN_import-cost-ledger.md` · 트랙 `docs/tracks/active/track_cost-truth-ledger.md`

★왜: **품목별 관세율이 다르다.** 실측 2건에서 cleaning kits(부자재)는 관세 **0%**,
유리·필름은 **5.6%**였다(예측 15,204 vs 실제 15,200 / 5.1345% vs 5.1344%).
금액 기준으로 관세까지 일괄 배부하면 무관세 품목이 관세를 떠안고 나머지 품목 원가가
과소 계상된다 — 7/22 건 실측으로 유리 개당 **−135원(원가의 5.0%)**이었다.

★additive만이다. 둘 다 nullable이고 기존 행은 NULL로 남는다.
`duty_rate`가 하나도 없는 건은 종전과 **완전히 같은 결과**를 낸다(`duty_mode="blended"`) —
prod에 이미 확정된 수입건 1건이 있으므로 이게 전제조건이다.

★`is_duty`에 server_default를 두지 않는다(교훈 #341: Boolean 기본값은 PostgreSQL에서
터지는 자리다). nullable이라 기존 행 백필도 필요 없고, 코드는 `bool(None) == False`로 읽는다.

Revision ID: duty50attrib
Revises: mrg48s1heads
Create Date: 2026-08-22 KST
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "duty50attrib"
down_revision: Union[str, None] = "mrg48s1heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 품목별 관세율(0.0560 = 5.6%). NULL은 «모름»이지 0%가 아니다.
    op.add_column(
        "import_invoice_line",
        sa.Column("duty_rate", sa.Numeric(precision=7, scale=4), nullable=True),
    )
    # 이 비용 라인이 «관세»인가 — 관세는 배부가 아니라 귀속 대상이다.
    op.add_column("import_cost_line", sa.Column("is_duty", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("import_cost_line", "is_duty")
    op.drop_column("import_invoice_line", "duty_rate")
