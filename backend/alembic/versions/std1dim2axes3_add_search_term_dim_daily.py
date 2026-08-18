"""add naver_search_term_dim_daily + _cell_daily (D-NAO-198 ① col7/8/9 시간대·지역·매체)

SHOPPINGKEYWORD_DETAIL 리포트는 매일 07:40에 이미 받고 있으나(추가 API 콜 0), 파싱이
(일자×캠페인×그룹×검색어)로 뭉개면서 col7(시간대)·col8(지역)·col9(매체)를 매일 버려 왔다.
★재생성 한도가 **정확히 180일**이라 매일 창이 굴러가 앞이 사라진다 — 이 마이그레이션이
그 소실을 멈춘다.

★두 표로 가른 이유(2026-08-18 실측): 결합 grain 전건은 180일 3.43M행·586MB인데 그중 98.2%가
clk=0·cost=0인 «노출만 있는 칸»이다. prod 디스크 92%(디스크 포화로 배치를 유실한 전력 있음)라
전건은 못 싣는다. → 축별 마진은 전건(약 197MB), 결합은 clk>0 or cost>0인 칸만(약 10MB).

Revision ID: std1dim2axes3
Revises: kv1vol2daily3
Create Date: 2026-08-18 23:0x KST
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "std1dim2axes3"
down_revision: Union[str, None] = "kv1vol2daily3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "naver_search_term_dim_daily",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ad_date", sa.Date(), nullable=False),
        sa.Column("campaign_id", sa.String(length=50), nullable=False),
        sa.Column("adgroup_id", sa.String(length=50), nullable=False),
        sa.Column("dim_type", sa.String(length=1), nullable=False),
        sa.Column("dim_value", sa.String(length=20), nullable=False),
        sa.Column("imp", sa.Integer(), nullable=False),
        sa.Column("clk", sa.Integer(), nullable=False),
        sa.Column("cost", sa.Integer(), nullable=False),
        sa.Column("rank_sum", sa.Integer(), nullable=False),
        sa.Column("synced_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ad_date", "adgroup_id", "dim_type", "dim_value",
            name="uq_naver_search_term_dim_daily",
        ),
    )
    op.create_index(op.f("ix_naver_search_term_dim_daily_ad_date"),
                    "naver_search_term_dim_daily", ["ad_date"], unique=False)
    op.create_index(op.f("ix_naver_search_term_dim_daily_campaign_id"),
                    "naver_search_term_dim_daily", ["campaign_id"], unique=False)

    op.create_table(
        "naver_search_term_dim_cell_daily",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ad_date", sa.Date(), nullable=False),
        sa.Column("campaign_id", sa.String(length=50), nullable=False),
        sa.Column("adgroup_id", sa.String(length=50), nullable=False),
        sa.Column("hour_code", sa.String(length=2), nullable=False),
        sa.Column("region_code", sa.String(length=20), nullable=False),
        sa.Column("media_code", sa.String(length=20), nullable=False),
        sa.Column("imp", sa.Integer(), nullable=False),
        sa.Column("clk", sa.Integer(), nullable=False),
        sa.Column("cost", sa.Integer(), nullable=False),
        sa.Column("rank_sum", sa.Integer(), nullable=False),
        sa.Column("synced_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ad_date", "adgroup_id", "hour_code", "region_code", "media_code",
            name="uq_naver_search_term_dim_cell_daily",
        ),
    )
    op.create_index(op.f("ix_naver_search_term_dim_cell_daily_ad_date"),
                    "naver_search_term_dim_cell_daily", ["ad_date"], unique=False)
    op.create_index(op.f("ix_naver_search_term_dim_cell_daily_campaign_id"),
                    "naver_search_term_dim_cell_daily", ["campaign_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_naver_search_term_dim_cell_daily_campaign_id"),
                  table_name="naver_search_term_dim_cell_daily")
    op.drop_index(op.f("ix_naver_search_term_dim_cell_daily_ad_date"),
                  table_name="naver_search_term_dim_cell_daily")
    op.drop_table("naver_search_term_dim_cell_daily")
    op.drop_index(op.f("ix_naver_search_term_dim_daily_campaign_id"),
                  table_name="naver_search_term_dim_daily")
    op.drop_index(op.f("ix_naver_search_term_dim_daily_ad_date"),
                  table_name="naver_search_term_dim_daily")
    op.drop_table("naver_search_term_dim_daily")
