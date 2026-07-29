"""naver_adgroup_hourly_today — 당일(진행 중) 애드그룹 시간별 성과 전용 테이블 (D-NAO-122)

naver_keyword_hourly에 당일 진행분을 섞으면 그 테이블의 완결 불변식
("행이 있다 = 그 날이 통째로 스윕됐다")이 깨져 auto_operator의 롤링 24h 흐름이
과소계상된다(codex 리뷰 P1, 2026-07-29). 신규 grain=신규 테이블 규약대로 분리한다.

additive only — 기존 테이블·컬럼을 건드리지 않으므로 구코드와 하위호환된다.

Revision ID: a1c4e7f20b91
Revises: 4b66ae3706e6
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1c4e7f20b91"
down_revision: Union[str, Sequence[str], None] = "4b66ae3706e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "naver_adgroup_hourly_today",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ad_date", sa.Date(), nullable=False),
        sa.Column("hour", sa.Integer(), nullable=False),
        sa.Column("adgroup_id", sa.String(length=50), nullable=False),
        sa.Column("campaign_id", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("campaign_type", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("imp", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clk", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("conv_cnt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_rank", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("synced_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ad_date", "adgroup_id", "hour", name="uq_naver_adgroup_hourly_today"),
    )
    op.create_index(
        op.f("ix_naver_adgroup_hourly_today_ad_date"),
        "naver_adgroup_hourly_today", ["ad_date"], unique=False,
    )
    op.create_index(
        op.f("ix_naver_adgroup_hourly_today_adgroup_id"),
        "naver_adgroup_hourly_today", ["adgroup_id"], unique=False,
    )
    op.create_index(
        op.f("ix_naver_adgroup_hourly_today_campaign_id"),
        "naver_adgroup_hourly_today", ["campaign_id"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_naver_adgroup_hourly_today_campaign_id"), table_name="naver_adgroup_hourly_today")
    op.drop_index(op.f("ix_naver_adgroup_hourly_today_adgroup_id"), table_name="naver_adgroup_hourly_today")
    op.drop_index(op.f("ix_naver_adgroup_hourly_today_ad_date"), table_name="naver_adgroup_hourly_today")
    op.drop_table("naver_adgroup_hourly_today")
