"""네이버 클레임 정산 구조 관측 로그 (N배송 반품 회수비 프로브)

N배송(ARRIVAL_GUARANTEE) 반품의 정산 구조가 미지 — 라이브 실측상 N배송 반품은 468건 중
2건뿐(2026072553216341·2026072852172181)이고 둘 다 settleDecisionType 3유형 전부에서 정산 행
0건이다(정산 성숙 D+12 → 08-06·08-09경 예상). 표본이 익는 순간을 사람이 지키고 있을 수 없으니
관측을 영속화하고 **처음 보는 조합**만 Slack으로 올린다.

신규 테이블 하나뿐 — 기존 테이블 변경 없음(구코드와 완전 하위호환).

Revision ID: d4f6a8c0b2e5
Revises: c3e5a7b9d1f4
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4f6a8c0b2e5"
down_revision: Union[str, Sequence[str], None] = "c3e5a7b9d1f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "naver_claim_settlement_probe",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_number", sa.String(length=40), nullable=False),
        sa.Column("product_order_id", sa.String(length=40), nullable=False),
        sa.Column("delivery_attribute_type", sa.String(length=40), nullable=True),
        sa.Column("is_membership", sa.Boolean(), nullable=False),
        sa.Column("order_status", sa.String(length=20), nullable=False),
        sa.Column("settle_decision_type", sa.String(length=20), nullable=False),
        sa.Column("product_order_type", sa.String(length=40), nullable=False),
        sa.Column("settle_type", sa.String(length=40), nullable=False),
        sa.Column("pay_settle_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("settle_expect_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("pay_date", sa.String(length=10), nullable=True),
        sa.Column("settle_expect_date", sa.String(length=10), nullable=True),
        sa.Column("observed_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        # observed_date를 키에 포함 = "언제 무엇으로 보였나"의 시계열 보존(UNSETTLED→SETTLED
        # 전이가 이 프로브의 관측 대상이다). settle_type/product_order_id는 NULL 대신 ''
        # sentinel — SQLite/Postgres 모두 UNIQUE에서 NULL을 distinct로 취급해 중복을 못 막는다.
        sa.UniqueConstraint(
            "product_order_id", "product_order_type", "settle_type",
            "settle_decision_type", "observed_date",
            name="uq_naver_claim_settlement_probe",
        ),
    )
    op.create_index(
        "ix_naver_claim_settlement_probe_order_number",
        "naver_claim_settlement_probe", ["order_number"],
    )
    op.create_index(
        "ix_naver_claim_settlement_probe_delivery_attribute_type",
        "naver_claim_settlement_probe", ["delivery_attribute_type"],
    )
    op.create_index(
        "ix_naver_claim_settlement_probe_observed_date",
        "naver_claim_settlement_probe", ["observed_date"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_naver_claim_settlement_probe_observed_date",
        table_name="naver_claim_settlement_probe",
    )
    op.drop_index(
        "ix_naver_claim_settlement_probe_delivery_attribute_type",
        table_name="naver_claim_settlement_probe",
    )
    op.drop_index(
        "ix_naver_claim_settlement_probe_order_number",
        table_name="naver_claim_settlement_probe",
    )
    op.drop_table("naver_claim_settlement_probe")
