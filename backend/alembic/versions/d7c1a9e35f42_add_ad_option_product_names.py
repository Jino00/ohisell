"""add product name columns to coupang_ad_option_daily

옵션 표가 옵션ID 숫자만 보여줘 사람이 상품을 못 알아본다(트랙 ohitech-ad §6 1순위).
광고 XLSX가 같은 행에 실어 오는 상품명([7] 광고집행 상품명 / [9] 광고전환매출발생 상품명)을
적재 시점에 보존한다. 1P Retail 옵션은 coupang_product_item(3P product_sync 산물)에 없어
조인으로는 라벨을 붙일 수 없다.

nullable — 이 마이그레이션 이전 적재분은 NULL로 남고, 페처의 30일 롤링 재적재
(option_days=30, delete-then-insert)로 최근 30일이 자연 채워진다. 표시는 옵션ID로 폴백.

Revision ID: d7c1a9e35f42
Revises: c4a7e2b91d63
Create Date: 2026-08-03 20:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7c1a9e35f42'
down_revision: Union[str, None] = 'c4a7e2b91d63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('coupang_ad_option_daily') as batch_op:
        batch_op.add_column(sa.Column('ad_product_name', sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column('conv_product_name', sa.String(length=300), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('coupang_ad_option_daily') as batch_op:
        batch_op.drop_column('conv_product_name')
        batch_op.drop_column('ad_product_name')
