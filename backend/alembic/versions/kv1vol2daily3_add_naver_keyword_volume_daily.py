"""add naver_keyword_volume_daily (D-NAO-186 ① 검색량 기준선 시계열)

기존 `naver_entity.monthly_volume`은 **덮어쓰기**라 과거가 안 남는다. D-NAO-186이 이 적재를
「소급 불가·마감 있음」으로 승인한 이유가 정확히 «기준선(시계열)»이므로 별도 테이블을 둔다.
아이폰은 매년 9월 출시 — 안 켠 날은 협상 불가로 사라진다.

Revision ID: kv1vol2daily3
Revises: cs1exat2when3
Create Date: 2026-08-18 15:4x KST
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "kv1vol2daily3"
down_revision: Union[str, None] = "cs1exat2when3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "naver_keyword_volume_daily",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("measured_date", sa.Date(), nullable=False),
        sa.Column("keyword", sa.String(length=200), nullable=False),
        sa.Column("pc_volume", sa.Integer(), nullable=True),
        sa.Column("mobile_volume", sa.Integer(), nullable=True),
        sa.Column("total_volume", sa.Integer(), nullable=True),
        sa.Column("competition", sa.String(length=10), nullable=True),
        sa.Column("is_below_threshold", sa.Boolean(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("synced_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("measured_date", "keyword",
                            name="uq_naver_keyword_volume_daily"),
    )
    op.create_index("ix_naver_keyword_volume_daily_kw", "naver_keyword_volume_daily",
                    ["keyword", "measured_date"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_naver_keyword_volume_daily_kw",
                  table_name="naver_keyword_volume_daily")
    op.drop_table("naver_keyword_volume_daily")
