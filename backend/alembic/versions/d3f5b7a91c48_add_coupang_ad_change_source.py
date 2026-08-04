"""쿠팡 변경 이력 API를 두 번째 원천으로 (트랙 coupang-ad-change-log 슬라이스2)

정찰 2차에서 **쿠팡이 변경 이력을 직접 준다**는 걸 발견했다
(`POST /marketing/tetris-api/change-history/events-simple`, 90일 소급, executionId=UUID).
겹치는 축(BUDGET·TROAS·CAMPAIGN_ONOFF)에선 쿠팡이 전/후 값과 실행 시각을 주므로 더 정확하다 —
우리 스냅샷 diff는 updatedAt으로 **시각만** 안다. 그래서 원천을 구분하고 우선순위를 둔다.

★우리 스냅샷을 끄지 않는 이유: 쿠팡 API가 안 주는 축이 있다(캠페인 이름 등 13개 설정,
  광고그룹 15개 필드, 신규 생성·삭제). 네이버 「수정 사항」이 두 원천을 합치는 것과 같은 구조다.

`detail_json`이 필요한 이유: 소재 변경은 "옵션 20개 추가 + 그 옵션ID 목록"이라
before/after 두 칸에 안 들어간다.

가산적이다: 기존 행은 `source='snapshot'`으로 남고 로직도 그대로 동작한다.

Revision ID: d3f5b7a91c48
Revises: c8d1a4f97b26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d3f5b7a91c48"
down_revision: Union[str, Sequence[str], None] = "c8d1a4f97b26"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("coupang_ad_change_log") as b:
        b.add_column(sa.Column("source", sa.String(length=10), nullable=False,
                               server_default="snapshot"))
        b.add_column(sa.Column("external_id", sa.String(length=64), nullable=True))
        b.add_column(sa.Column("detail_json", sa.Text(), nullable=True))
    # 쿠팡 이벤트 재적재 시 같은 executionId를 빠르게 찾기 위한 인덱스(유니크는 아니다 —
    # 한 executionId에 changes가 2개 들어오는 경우가 실측 5건 있었다).
    op.create_index("ix_coupang_ad_change_log_external_id", "coupang_ad_change_log",
                    ["account", "external_id"])


def downgrade() -> None:
    op.drop_index("ix_coupang_ad_change_log_external_id", table_name="coupang_ad_change_log")
    with op.batch_alter_table("coupang_ad_change_log") as b:
        b.drop_column("detail_json")
        b.drop_column("external_id")
        b.drop_column("source")
