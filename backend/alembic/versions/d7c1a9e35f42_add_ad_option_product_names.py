"""add product name columns to coupang_ad_option_daily

옵션 표가 옵션ID 숫자만 보여줘 사람이 상품을 못 알아본다(트랙 ohitech-ad §6 1순위).
광고 XLSX가 같은 행에 실어 오는 상품명([7] 광고집행 상품명 / [9] 광고전환매출발생 상품명)을
적재 시점에 보존한다. 1P Retail 옵션은 coupang_product_item(3P product_sync 산물)에 없어
조인으로는 라벨을 붙일 수 없다.

nullable — 이 마이그레이션 이전 적재분은 NULL로 남고, 페처의 30일 롤링 재적재
(option_days=30, delete-then-insert)로 최근 30일이 자연 채워진다. 표시는 옵션ID로 폴백.

Revision ID: d7c1a9e35f42
Revises: mrg9b1c4a7e2
Create Date: 2026-08-03 20:30:00.000000

★부모가 머지 리비전인 이유(2026-08-03): prod의 alembic head가 `rg9billed7c4e`(RG 정산 S9,
  당시 미병합 브랜치에서 배포됨)였고 main의 head는 `c4a7e2b91d63`(네이버 APPLY_TM)이라 같은
  부모 `b6e1c93f4275`에서 두 갈래가 나 있었다. 그 위에 그대로 얹으면 prod에 head가 둘이 되어
  `alembic upgrade head`가 실패하고 **모든 세션의 배포 경로가 함께 막힌다**(safe_deploy가 이
  명령을 쓴다). → RG 세션이 만들어 둔 머지 리비전 `mrg9b1c4a7e2`를 main으로 가져와 갈래를
  합치고 그 위에 얹었다(Jino 승인).

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7c1a9e35f42'
down_revision: Union[str, None] = 'mrg9b1c4a7e2'
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
