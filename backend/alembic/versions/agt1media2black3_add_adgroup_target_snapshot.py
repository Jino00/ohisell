"""add naver_adgroup_target_current + _media_black + _target_change (D-NAO-201 ③ A5·A6)

`/ncc/targets` 응답은 쇼핑 제외 관리(D-NAO-180/181)의 정규 경로가 이미 받고 있으나, 코드가
`targetTp != RESTRICT_KEYWORD_TARGET`을 그 자리에서 버려 왔다 — A5(매체 블랙리스트)·A6(PC/모바일)
원료가 매번 도착했다가 사라진 것이다.

★단 「편승하면 추가 API 콜 0」은 **커버리지가 24.6%**다(2026-08-19 실측: 생존감시 08:25가 도는
그룹은 제외 원장 보유 131그룹뿐, 성과축 307그룹 중 116만 겹친다). Jino 결정 = **전수 일일 스윕**
(naver_entity의 non-deleted adgroup 1,013건) — 「추가 콜 0」을 포기하고 커버리지를 산다.

★왜 일일 스냅샷이 아닌가: prod 디스크 92%. 타겟팅은 거의 안 바뀐다(533그룹 중 2026년 이후 수정
83건) → 현재상태 upsert + 변경 이벤트만 적재.

Revision ID: agt1media2black3
Revises: std1dim2axes3
Create Date: 2026-08-19 08:4x KST
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "agt1media2black3"
down_revision: Union[str, None] = "std1dim2axes3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "naver_adgroup_target_current",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("adgroup_id", sa.String(length=50), nullable=False),
        sa.Column("campaign_id", sa.String(length=50), nullable=False),
        sa.Column("probe_status", sa.Integer(), nullable=False),
        sa.Column("media_target_id", sa.String(length=50), nullable=False),
        sa.Column("media_type", sa.Integer(), nullable=True),
        sa.Column("media_search", sa.Text(), nullable=True),
        sa.Column("media_contents", sa.Text(), nullable=True),
        sa.Column("media_white", sa.Text(), nullable=True),
        sa.Column("black_media_json", sa.Text(), nullable=True),
        sa.Column("black_media_count", sa.Integer(), nullable=False),
        sa.Column("black_mediagroup_json", sa.Text(), nullable=True),
        sa.Column("media_reg_tm", sa.String(length=40), nullable=True),
        sa.Column("media_edit_tm", sa.String(length=40), nullable=True),
        sa.Column("pcm_target_id", sa.String(length=50), nullable=False),
        sa.Column("pc", sa.Boolean(), nullable=True),
        sa.Column("mobile", sa.Boolean(), nullable=True),
        sa.Column("pcm_edit_tm", sa.String(length=40), nullable=True),
        sa.Column("target_types_json", sa.Text(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("observed_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("adgroup_id", name="uq_naver_adgroup_target_current"),
    )
    op.create_index("ix_naver_adgroup_target_current_adgroup_id", "naver_adgroup_target_current", ["adgroup_id"])
    op.create_index("ix_naver_adgroup_target_current_campaign_id", "naver_adgroup_target_current", ["campaign_id"])
    op.create_index("ix_naver_adgroup_target_current_observed_at", "naver_adgroup_target_current", ["observed_at"])

    op.create_table(
        "naver_adgroup_media_black",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("adgroup_id", sa.String(length=50), nullable=False),
        # ★문자열이다 — 조인 상대 naver_search_term_dim_daily.dim_value와 타입을 맞춘다.
        #   (SQLite에선 TEXT affinity 덕에 int로 넣어도 조인이 성립한다 — 실측 확인.
        #    문자열로 두는 진짜 이유는 PostgreSQL 이행 시 VARCHAR에 int 바인딩이 에러라는 것.)
        sa.Column("media_code", sa.String(length=20), nullable=False),
        sa.Column("source_edit_tm", sa.String(length=40), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("observed_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("adgroup_id", "media_code", name="uq_naver_adgroup_media_black"),
    )
    op.create_index("ix_naver_adgroup_media_black_adgroup_id", "naver_adgroup_media_black", ["adgroup_id"])
    op.create_index("ix_naver_adgroup_media_black_media_code", "naver_adgroup_media_black", ["media_code"])

    op.create_table(
        "naver_adgroup_target_change",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("adgroup_id", sa.String(length=50), nullable=False),
        sa.Column("observed_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("field", sa.String(length=40), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_naver_adgroup_target_change_adgroup_id", "naver_adgroup_target_change", ["adgroup_id"])
    op.create_index("ix_naver_adgroup_target_change_observed_at", "naver_adgroup_target_change", ["observed_at"])


def downgrade() -> None:
    op.drop_table("naver_adgroup_target_change")
    op.drop_table("naver_adgroup_media_black")
    op.drop_table("naver_adgroup_target_current")
