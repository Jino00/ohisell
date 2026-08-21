"""M2-b: /ncc/criterion 적재 3표 신설 + 제외 원장 match_type 컬럼

ref 65 S1-ⓐ(bidWeight 판독·적재, D-NAO-216 경로 정정) · 매칭 타입(exact/phrase) 동승 적재
(계약 §8-Q2-b) · 계약 `docs/PLAN_naver-m2-l2-wiring.md` §6 M2-b.

★두 변경 다 **additive**다 — 기존 컬럼·행을 건드리지 않는다.
  ①`naver_adgroup_criterion_current`·`_change`·`_probe` 3표 신설(D-NAO-201의
    `naver_adgroup_target_current` 계열과 같은 관례 — 현재상태 upsert + 변경 이벤트 원장 +
    조회 결과 자체를 남기는 프로브 표). `naver_adgroup_target_current`를 확장하지 않는 이유는
    models.py `NaverAdgroupCriterionCurrent` docstring 참조(grain이 다르고 — 그룹당 평균
    12.5행 vs 그룹당 1행 — bidWeight가 애초에 `/ncc/targets` 응답에 없다).
  ②`naver_search_term_exclusion`에 `match_type` 컬럼 추가(nullable, 기존 행은 전부 NULL —
    다음 생존 감시 회전이 SHOPPING 행을 채운다).

★**Boolean server_default에 정수 리터럴을 쓰지 않는다** — 이 계약의 배경이 된 M2-a 적대
  리뷰 1R P1이 정확히 이것이었다(`server_default="0"`을 PostgreSQL이 boolean에 캐스팅
  못 해 `create_table`까지 롤백됐는데 SQLite는 타입을 강제하지 않아 로컬·CI 전부 초록으로
  통과했다). 이 마이그레이션은 그 함정을 두 가지로 피한다: ①Boolean 컬럼(`negative`·
  `enable`·`del_flag`)엔 **server_default를 아예 안 둔다** — ingest 코드가 매 upsert마다
  값을 명시적으로 채우므로 DB 기본값이 필요 없다(신설 표라 기존 행도 없다) ②정말 필요한
  자리에서 SQL 리터럴을 쓸 일이 있다면 `sa.false()`/`sa.true()`만 쓴다(이 파일엔 그런
  자리가 없다 — 참고용으로 규율만 적어 둔다).

Revision ID: m2b1criterion2
Revises: m2a1pool2eb3
Create Date: 2026-08-21 KST
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "m2b1criterion2"
down_revision: Union[str, None] = "m2a1pool2eb3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "naver_adgroup_criterion_current",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("adgroup_id", sa.String(length=50), nullable=False),
        sa.Column("campaign_id", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("criterion_type", sa.String(length=4), nullable=False),
        sa.Column("dictionary_code", sa.String(length=32), nullable=False),
        sa.Column("code_name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("bid_weight", sa.Integer(), nullable=True),
        # ★server_default 없음 — ingest가 매 행마다 명시적으로 채운다(위 파일 머리말).
        sa.Column("negative", sa.Boolean(), nullable=False),
        sa.Column("enable", sa.Boolean(), nullable=False),
        sa.Column("del_flag", sa.Boolean(), nullable=False),
        sa.Column("reg_tm", sa.String(length=40), nullable=True),
        sa.Column("edit_tm", sa.String(length=40), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("observed_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "adgroup_id", "criterion_type", "dictionary_code",
            name="uq_naver_adgroup_criterion_current",
        ),
    )
    op.create_index(
        "ix_naver_adgroup_criterion_current_adgroup_id",
        "naver_adgroup_criterion_current", ["adgroup_id"],
    )
    op.create_index(
        "ix_naver_adgroup_criterion_current_observed_at",
        "naver_adgroup_criterion_current", ["observed_at"],
    )

    op.create_table(
        "naver_adgroup_criterion_change",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("adgroup_id", sa.String(length=50), nullable=False),
        sa.Column("criterion_type", sa.String(length=4), nullable=False),
        sa.Column("dictionary_code", sa.String(length=32), nullable=False),
        sa.Column("field", sa.String(length=40), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("changed_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_naver_adgroup_criterion_change_adgroup_id",
        "naver_adgroup_criterion_change", ["adgroup_id"],
    )
    op.create_index(
        "ix_naver_adgroup_criterion_change_changed_at",
        "naver_adgroup_criterion_change", ["changed_at"],
    )

    op.create_table(
        "naver_adgroup_criterion_probe",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("adgroup_id", sa.String(length=50), nullable=False),
        sa.Column("probe_status", sa.Integer(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("adgroup_id", name="uq_naver_adgroup_criterion_probe"),
    )
    op.create_index(
        "ix_naver_adgroup_criterion_probe_adgroup_id",
        "naver_adgroup_criterion_probe", ["adgroup_id"],
    )
    op.create_index(
        "ix_naver_adgroup_criterion_probe_observed_at",
        "naver_adgroup_criterion_probe", ["observed_at"],
    )

    op.add_column(
        "naver_search_term_exclusion",
        sa.Column("match_type", sa.String(length=8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("naver_search_term_exclusion", "match_type")

    op.drop_index("ix_naver_adgroup_criterion_probe_observed_at", table_name="naver_adgroup_criterion_probe")
    op.drop_index("ix_naver_adgroup_criterion_probe_adgroup_id", table_name="naver_adgroup_criterion_probe")
    op.drop_table("naver_adgroup_criterion_probe")

    op.drop_index("ix_naver_adgroup_criterion_change_changed_at", table_name="naver_adgroup_criterion_change")
    op.drop_index("ix_naver_adgroup_criterion_change_adgroup_id", table_name="naver_adgroup_criterion_change")
    op.drop_table("naver_adgroup_criterion_change")

    op.drop_index("ix_naver_adgroup_criterion_current_observed_at", table_name="naver_adgroup_criterion_current")
    op.drop_index("ix_naver_adgroup_criterion_current_adgroup_id", table_name="naver_adgroup_criterion_current")
    op.drop_table("naver_adgroup_criterion_current")
