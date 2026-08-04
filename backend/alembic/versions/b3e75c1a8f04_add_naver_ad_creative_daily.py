"""소재(광고) 단위 일별 성과 — naver_ad_creative_daily (D-NAO-140)

우리 자동화는 **소재 단위로 입찰을 바꾸는데 소재 단위 성과가 없었다**(`naver_ad_daily`의 grain은
광고그룹까지). 그래서 상향 브레이크가 실제 성과가 아니라 대리 지표(기준가 2배)로 걸려 있었고,
자동화가 자기 손이 뭘 했는지 모른 채 움직였다 — 2026-07-30 전면 정지의 구조적 원인 중 하나다.

★수집 비용 0: 소스는 이미 매일 받는 `AD`·`AD_CONVERSION` 보고서이고, **두 보고서 모두 컬럼 5가
소재 ID**다(2026-08-04 라이브 실측 — 08-03분 6,874행·152행 다운로드해 확인). 지금까지 집계하며
버리고 있었을 뿐이다.

★왜 `naver_ad_daily`에 컬럼을 더하지 않고 새 테이블인가: 그 테이블에 grain을 하나 더 얹으면
기존 소비자가 전부 이중계상한다. 2026-08-04에 실제로 그 함정에 걸려 03 캠페인의 08-03 소진을
69,912원이 아니라 139,824원(정확히 2배)으로 읽었다. 같은 테이블에 grain이 섞이면 그런 오독은
합계가 안 맞을 때까지 보이지 않는다.

가산적이다: 기존 테이블·집계 로직은 한 줄도 안 바뀐다(계약 금지선).

Revision ID: b3e75c1a8f04
Revises: a91d3f60c7b2
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3e75c1a8f04"
down_revision: Union[str, Sequence[str], None] = "a91d3f60c7b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COUNTERS = (
    "imp", "clk", "cost", "rank_sum",
    "conv_direct_cnt", "conv_indirect_cnt", "conv_direct_amt", "conv_indirect_amt",
    "cart_direct_cnt", "cart_indirect_cnt", "cart_direct_amt", "cart_indirect_amt",
)


def upgrade() -> None:
    op.create_table(
        "naver_ad_creative_daily",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ad_date", sa.Date(), nullable=False),
        sa.Column("ad_id", sa.String(50), nullable=False),
        sa.Column("campaign_id", sa.String(50), nullable=False, server_default=""),
        sa.Column("campaign_type", sa.String(20), nullable=False, server_default=""),
        sa.Column("adgroup_id", sa.String(50), nullable=False, server_default=""),
        *[sa.Column(c, sa.Integer(), nullable=False, server_default="0") for c in _COUNTERS],
        sa.Column("synced_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ad_date", "ad_id", name="uq_naver_ad_creative_daily"),
    )
    op.create_index("ix_naver_ad_creative_daily_ad_date", "naver_ad_creative_daily", ["ad_date"])
    op.create_index("ix_naver_ad_creative_daily_ad_id", "naver_ad_creative_daily", ["ad_id"])
    op.create_index(
        "ix_naver_ad_creative_daily_campaign_id", "naver_ad_creative_daily", ["campaign_id"]
    )
    op.create_index(
        "ix_naver_ad_creative_daily_adgroup_id", "naver_ad_creative_daily", ["adgroup_id"]
    )


def downgrade() -> None:
    op.drop_table("naver_ad_creative_daily")
