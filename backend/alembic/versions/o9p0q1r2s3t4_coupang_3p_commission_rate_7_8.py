"""coupang_3p_commission_rate_7_8

쿠팡 3P(WING1/WING2) 채널 commission_rate 플레이스홀더 10.8 → 실측 7.8(폴백 정률).
실측 판매수수료는 coupang_revenue_fee로 라인별 적용되고, 이 값은 미정산 라인의 폴백.
PLAN_coupang-3p-fee-actualization D-E. RG1/RG2는 별도 정산경로(불변, D-C).

Revision ID: o9p0q1r2s3t4
Revises: n8o9p0q1r2s3
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa

revision = "o9p0q1r2s3t4"
down_revision = "n8o9p0q1r2s3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE channels SET commission_rate = 7.8 "
            "WHERE code IN ('COUPANG_WING1', 'COUPANG_WING2')"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE channels SET commission_rate = 10.8 "
            "WHERE code IN ('COUPANG_WING1', 'COUPANG_WING2')"
        )
    )
