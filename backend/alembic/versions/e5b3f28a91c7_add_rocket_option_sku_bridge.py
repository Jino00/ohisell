"""add coupang_rocket_option_sku — 1P 옵션ID↔상품번호 브리지를 일급 자산으로

옵션별/상품별 손익의 결합축이다. 이 대응은 시간 불변인데(실측: sku_id가 바뀐 옵션 0건)
지금까지 `coupang_rocket_sales_daily` 일별 수집의 부산물로만 존재했다 — 판매분석은
BETA + "Basic 무료체험"(2026-08-20 종료 예정)이라 그 수집이 끊기면 브리지가 통째로
사라진다. 여기 누적 보존해 수집이 끊겨도 이미 아는 상품은 계속 산출되게 한다.

★이 마이그레이션은 **데이터 시드까지 한다**: 기존 sales_daily에 이미 관측된 대응을
  option_id별로 한 행씩 옮긴다(sku_id NOT NULL인 것만). 코드로 백필하면 "언제 돌렸나"가
  기록에 안 남고, 안 돌린 환경에서 표가 조용히 빈다.

Revision ID: e5b3f28a91c7
Revises: d7c1a9e35f42
Create Date: 2026-08-03 22:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5b3f28a91c7'
down_revision: Union[str, None] = 'd7c1a9e35f42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'coupang_rocket_option_sku',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('option_id', sa.String(length=30), nullable=False),
        sa.Column('sku_id', sa.String(length=30), nullable=False),
        sa.Column('vendor_id', sa.String(length=20), nullable=True),
        sa.Column('product_name', sa.String(length=300), nullable=True),
        sa.Column('source', sa.String(length=20), server_default='sales_analysis', nullable=False),
        sa.Column('first_observed_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('last_observed_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('option_id', name='uq_coupang_rocket_option_sku'),
    )
    op.create_index('ix_coupang_rocket_option_sku_option_id', 'coupang_rocket_option_sku', ['option_id'])
    op.create_index('ix_coupang_rocket_option_sku_sku_id', 'coupang_rocket_option_sku', ['sku_id'])
    op.create_index('ix_coupang_rocket_option_sku_vendor_id', 'coupang_rocket_option_sku', ['vendor_id'])

    # ── 시드: 이미 관측된 대응을 옮긴다 ──
    # option_id별 1행. sku_id는 시간 불변이라(실측) 어느 행을 골라도 같지만, 만약 원천이
    # 어긋난 적이 있다면 **가장 최근 관측**을 채택한다(MAX(date) 기준). 이름도 같은 행에서.
    op.execute(
        """
        INSERT INTO coupang_rocket_option_sku
            (option_id, sku_id, vendor_id, product_name, source, first_observed_at, last_observed_at)
        SELECT s.option_id,
               s.sku_id,
               s.vendor_id,
               s.product_name,
               'backfill',
               COALESCE(agg.first_date, CURRENT_TIMESTAMP),
               COALESCE(agg.last_date, CURRENT_TIMESTAMP)
          FROM coupang_rocket_sales_daily s
          JOIN (
                SELECT option_id,
                       MIN(date) AS first_date,
                       MAX(date) AS last_date,
                       MAX(date) AS pick_date
                  FROM coupang_rocket_sales_daily
                 WHERE sku_id IS NOT NULL AND sku_id <> ''
                 GROUP BY option_id
               ) agg
            ON agg.option_id = s.option_id AND agg.pick_date = s.date
         WHERE s.sku_id IS NOT NULL AND s.sku_id <> ''
         GROUP BY s.option_id
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_coupang_rocket_option_sku_vendor_id', table_name='coupang_rocket_option_sku')
    op.drop_index('ix_coupang_rocket_option_sku_sku_id', table_name='coupang_rocket_option_sku')
    op.drop_index('ix_coupang_rocket_option_sku_option_id', table_name='coupang_rocket_option_sku')
    op.drop_table('coupang_rocket_option_sku')
