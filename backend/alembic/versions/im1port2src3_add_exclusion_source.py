"""naver_search_term_exclusion.source — 이 행이 어디서 왔는가 (D-NAO-176)

★왜 필요한가: 콘솔에 이미 걸려 있는 제외 약 43건을 장부에 편입하는데, 그건 **시점을 모르는
과거 조치**다. 감시 대상은 되어야 하지만 성적표가 판정하면 안 되고(전후 창이 실행 시점을
기준으로 잡히는데 그 시점을 모른다), 학습 입력이 되어서도 안 된다.
그 구분을 «행이 어디서 왔는가»로 남긴다 — 값이 없으면(NULL) 종전대로 우리가 실행/보고한 행이다.

  NULL            — 기존 경로(record_execution: 화면 보고 · 라이브 자동 발견)
  console_import  — 콘솔에 이미 걸려 있던 것을 일괄 편입(일기 없음·성적표 판정 제외)

nullable 컬럼 추가라 구코드를 깨지 않는다(구코드는 이 컬럼을 SELECT 목록에 넣지 않는다).
그래도 배포 순서는 safe_deploy.sh --migrate가 강제한다 —
①마이그 파일 ②원격 alembic upgrade ③코드 ④재시작.

Revision ID: im1port2src3
Revises: st1surv2live3
Create Date: 2026-08-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "im1port2src3"
down_revision = "st1surv2live3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "naver_search_term_exclusion",
        sa.Column("source", sa.String(length=20), nullable=True),
    )
    op.create_index(
        "ix_naver_search_term_exclusion_source",
        "naver_search_term_exclusion",
        ["source"],
    )


def downgrade() -> None:
    op.drop_index("ix_naver_search_term_exclusion_source", table_name="naver_search_term_exclusion")
    op.drop_column("naver_search_term_exclusion", "source")
