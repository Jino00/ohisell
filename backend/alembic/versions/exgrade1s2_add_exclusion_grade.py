"""제외 «임대» 등급 — naver_search_term_exclusion에 grade·grade_reason 추가 (S2)

계약: docs/contracts/CONTRACT_ignition_readiness.md §4-A S2 · §4-B⑥

★additive nullable만 추가한다(계약 §3 금지선) — 구코드가 새 스키마 위에서 그대로 돈다.
  NOT NULL도, server_default도 걸지 않는다: 백필 «전»의 NULL이 곧 「아직 분류 안 된 행」의
  표면이고, 기본값을 박으면 그 표면이 사라진다(전부 분류된 것처럼 보인다).

★배포 순서는 `scripts/safe_deploy.sh --migrate`가 강제한다:
  ①마이그 파일 배포 → ②원격 `alembic upgrade head` → ③코드 배포 → ④재시작.
  이 앱은 부팅 시 인프로세스 마이그레이션을 하지 않으므로 models.py가 먼저 가면 ORM이
  엔티티를 통째로 SELECT 하다 `no such column`으로 그 테이블 ingest 경로가 통째로 침묵한다.

Revision ID: exgrade1s2
Revises: cst60auto
Create Date: 2026-08-27 (KST)
"""

from alembic import op
import sqlalchemy as sa

revision = "exgrade1s2"
down_revision = "cst60auto"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("naver_search_term_exclusion") as batch:
        batch.add_column(sa.Column("grade", sa.String(length=12), nullable=True))
        batch.add_column(sa.Column("grade_reason", sa.String(length=200), nullable=True))
    op.create_index(
        "ix_naver_search_term_exclusion_grade",
        "naver_search_term_exclusion",
        ["grade"],
    )


def downgrade() -> None:
    op.drop_index("ix_naver_search_term_exclusion_grade", table_name="naver_search_term_exclusion")
    with op.batch_alter_table("naver_search_term_exclusion") as batch:
        batch.drop_column("grade_reason")
        batch.drop_column("grade")
