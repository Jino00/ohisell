"""소재(ad) grain 외부 변경 탐지 컬럼 2종 (D-NAO-127)

- naver_adgroup_product.ad_edit_tm: 네이버 소재 editTm 원문(마지막 수정 시각 앵커).
  값 비교만으로는 "우리가 바꾼 걸 외부가 되돌림"이 무변동으로 보이지만 editTm은 단조 전진한다.
- naver_agency_op.occurred_at: 그 조작이 **실제로 일어난** 시각(KST). ad grain만 채워지고
  스냅샷 diff 유래 grain은 NULL(시각 불명 — 지어내지 않는다).

둘 다 additive nullable — 구코드와 하위호환(기존 행은 NULL, 다음 sync가 채운다).

Revision ID: c3e5a7b9d1f4
Revises: b7d2f4a09c31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3e5a7b9d1f4"
down_revision: Union[str, Sequence[str], None] = "b7d2f4a09c31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "naver_adgroup_product",
        sa.Column("ad_edit_tm", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "naver_agency_op",
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("naver_agency_op", "occurred_at")
    op.drop_column("naver_adgroup_product", "ad_edit_tm")
