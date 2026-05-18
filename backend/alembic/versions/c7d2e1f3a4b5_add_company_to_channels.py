"""add_company_to_channels

채널 회사 그룹핑 컬럼 추가 + 시드.
SQLite 호환 위해 batch_alter_table 사용 (downgrade로 롤백 가능).

Revision ID: c7d2e1f3a4b5
Revises: 3b94b7c55a1f
Create Date: 2026-05-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7d2e1f3a4b5"
down_revision: Union[str, None] = "3b94b7c55a1f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 회사 매핑 (channel code → company). code 기준이라 id 변동에 안전.
_COMPANY_BY_CODE = {
    "COUPANG_WING1": "개인회사 오픽스",
    "COUPANG_RG1": "개인회사 오픽스",
    "COUPANG_WING2": "주식회사 오하이테크",
    "COUPANG_RG2": "주식회사 오하이테크",
    "COUPANG_ROCKET": "주식회사 오하이테크",
    "CAFE24": "주식회사 오하이테크",
    "NAVER": "주식회사 오하이",
}


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("channels") as batch_op:
        batch_op.add_column(sa.Column("company", sa.String(length=50), nullable=True))

    conn = op.get_bind()
    for code, company in _COMPANY_BY_CODE.items():
        conn.execute(
            sa.text("UPDATE channels SET company = :c WHERE code = :code"),
            {"c": company, "code": code},
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("channels") as batch_op:
        batch_op.drop_column("company")
