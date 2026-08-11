"""D-NAO-173 P1-①: 검색어 제외의 «조치 생존» 대조 결과 칸 신설

왜 필요한가:
  우리가 네이버에 건 제외키워드가 **아직 걸려 있는지** 시스템이 말해주지 않았다. 대행사가
  우리 조치를 되돌린 사례가 2회 있고(2026-07-30 캠페인 재개 · 2026-08-10 그룹 userLock 해제)
  그중 한 번은 우리 change_log에 아무 흔적도 남지 않았다 — «되돌림 이벤트 탐지»는 이미
  한 번 실패한 방식이므로, **현재 라이브 상태를 매일 대조**하는 쪽으로 간다.

왜 컬럼인가(새 테이블이 아니라):
  grain이 같다 — (adgroup_id, search_term) 제외 1건의 «지금 상태»다. 별도 테이블로 빼면
  조인 없이는 «걸려 있나»를 못 보고, 이 표를 읽는 모든 화면이 조인을 하나 더 지게 된다.

nullable인 이유: 기존 행은 아직 대조된 적이 없다. NULL = «한 번도 확인 안 함»이고, 이는
  alive와 명확히 다르다(교훈 #123 — 발견 0건과 실행 안 됨은 같은 숫자로 보인다).

Revision ID: st1surv2live3
Revises: vi1s2a3x4b5c
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "st1surv2live3"
down_revision: Union[str, None] = "vi1s2a3x4b5c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # alive/missing/deleted/unknown — 8자가 최대라 String(12)로 여유를 둔다.
    op.add_column(
        "naver_search_term_exclusion",
        sa.Column("live_state", sa.String(length=12), nullable=True),
    )
    op.add_column(
        "naver_search_term_exclusion",
        sa.Column("live_checked_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "naver_search_term_exclusion",
        sa.Column("live_note", sa.String(length=200), nullable=True),
    )
    op.create_index(
        "ix_naver_search_term_exclusion_live_state",
        "naver_search_term_exclusion",
        ["live_state"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_naver_search_term_exclusion_live_state",
        table_name="naver_search_term_exclusion",
    )
    op.drop_column("naver_search_term_exclusion", "live_note")
    op.drop_column("naver_search_term_exclusion", "live_checked_at")
    op.drop_column("naver_search_term_exclusion", "live_state")
