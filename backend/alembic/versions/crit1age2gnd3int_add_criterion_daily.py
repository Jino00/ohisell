"""add naver_criterion_daily + _conv_daily + _dict (D-NAO-203 ② 연령·성별·관심사)

StatReport `CRITERION`·`CRITERION_CONVERSION` **벌크 리포트** 경로로 적재한다. ref 75 ADS §4-4가
「존재 확인만, 응답 스키마·성공 여부 미확인」으로 남긴 것을 2026-08-19 실호출로 닫았다 —
두 리포트 모두 POST /stat-reports로 자체생성되고 수 초 만에 BUILT 된다. 엔티티별
`/ncc/criterion/{ownerId}` GET 스윕(광고그룹 1,013콜/일 감각)은 **불필요**하다.

★소급 한도 = 정확히 365일(D-365 BUILT ↔ D-366 400 {"code":10004}). 매일 하루씩 사라진다.

★저장 범위 = **전건**(Jino 결정 2026-08-19). 약 7,300행/일 × 365 ≈ 2.7M행. 실측 5,602행 중
4,881행(87%)이 clk=0·cost=0이지만 노출은 있고, 그 행이 곧 「연령대별 노출 점유」라는 독립
축이다 — 365일 한도라 나중에 되돌릴 수 없어 지금 다 싣는다. (D-NAO-198은 결합 전건이 586MB·
98.2%가 노출 전용이라 「유료 칸만」을 샀는데, 여긴 분모가 5배 작고 소급 복구가 불가능해 반대 결론.)

★신규 테이블만 추가한다 — 기존 테이블 스키마 변경 0건이라 순서 위험이 낮다(그래도
safe_deploy.sh --migrate 순서는 지킨다).

Revision ID: crit1age2gnd3int
Revises: agt1media2black3
Create Date: 2026-08-19 11:1x KST
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "crit1age2gnd3int"
down_revision: Union[str, None] = "agt1media2black3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "naver_criterion_daily",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ad_date", sa.Date(), nullable=False),
        sa.Column("adgroup_id", sa.String(length=50), nullable=False),
        # AG=연령 GN=성별 AD=관심사 SD=요일시간. 사전 밖 접두는 'XX'(미상) — 추정 분류 금지.
        sa.Column("criterion_type", sa.String(length=2), nullable=False),
        # ★문자열이다 — 코드가 'AG3034'·'GNF'처럼 영숫자 혼합이고, 사전
        #   (naver_criterion_dict.dictionary_code)과 타입을 맞춘다.
        sa.Column("criterion_code", sa.String(length=32), nullable=False),
        sa.Column("device", sa.String(length=1), nullable=False),  # P=PC M=모바일
        sa.Column("imp", sa.Integer(), nullable=False),
        sa.Column("clk", sa.Integer(), nullable=False),
        sa.Column("cost", sa.Integer(), nullable=False),
        sa.Column("synced_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ad_date", "adgroup_id", "criterion_code", "device",
                            name="uq_naver_criterion_daily"),
    )
    op.create_index("ix_naver_criterion_daily_ad_date", "naver_criterion_daily", ["ad_date"])
    op.create_index("ix_naver_criterion_daily_adgroup_id", "naver_criterion_daily", ["adgroup_id"])

    op.create_table(
        "naver_criterion_conv_daily",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ad_date", sa.Date(), nullable=False),
        sa.Column("adgroup_id", sa.String(length=50), nullable=False),
        sa.Column("criterion_type", sa.String(length=2), nullable=False),
        sa.Column("criterion_code", sa.String(length=32), nullable=False),
        sa.Column("device", sa.String(length=1), nullable=False),
        sa.Column("conv_kind", sa.String(length=20), nullable=False),  # purchase / add_to_cart
        sa.Column("conv_type", sa.String(length=1), nullable=False),   # 1=직접 2=간접
        sa.Column("conv_cnt", sa.Integer(), nullable=False),
        sa.Column("conv_amt", sa.Integer(), nullable=False),
        sa.Column("synced_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ad_date", "adgroup_id", "criterion_code", "device",
                            "conv_kind", "conv_type", name="uq_naver_criterion_conv_daily"),
    )
    op.create_index("ix_naver_criterion_conv_daily_ad_date", "naver_criterion_conv_daily", ["ad_date"])
    op.create_index("ix_naver_criterion_conv_daily_adgroup_id", "naver_criterion_conv_daily", ["adgroup_id"])

    op.create_table(
        "naver_criterion_dict",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dictionary_code", sa.String(length=32), nullable=False),
        sa.Column("criterion_type", sa.String(length=2), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("synced_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dictionary_code", name="uq_naver_criterion_dict"),
    )
    op.create_index("ix_naver_criterion_dict_criterion_type", "naver_criterion_dict", ["criterion_type"])


def downgrade() -> None:
    op.drop_table("naver_criterion_dict")
    op.drop_table("naver_criterion_conv_daily")
    op.drop_table("naver_criterion_daily")
