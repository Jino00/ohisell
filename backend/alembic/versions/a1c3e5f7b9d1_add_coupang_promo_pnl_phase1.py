"""add coupang promo pnl phase1 (프로모션 손익 레이어 — 수집 신설)

트랙: docs/tracks/active/track_coupang-promo-pnl.md (D-CPP-1~6)

전부 **가산 변경**(CREATE TABLE 2 + ADD COLUMN 3) — 기존 행·기존 소비자 무영향, net_profit 불변.

1) coupang_rocket_sales_daily — 1P(로켓배송) 옵션×일 소비자 판매(supplier 애널리틱스>판매 분석).
   ★revenue는 회계 매출이 아니다(D-CPP-2). 1P 회계 매출 = 발주(납품)금액 축 유지.
2) coupang_rocket_promotion  — 1P 프로모션 신청(공급자허브). 행사기간은 초 단위 → DateTime.
   ★D-CPP-4: 분담금 청구 방식 미확정 → 사실 기록일 뿐 비용 라인 아님.
3) coupang_coupon.used_amount / used_amount_source / used_amount_synced_at
   — 2P RG 셀러 부담 실사용 할인액(D-CPP-3 권위값). 쿠팡 Open API엔 없어 ingest로만 채운다.

Revision ID: a1c3e5f7b9d1
Revises: e5f7a9c1b3d5
Create Date: 2026-07-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a1c3e5f7b9d1'
down_revision: Union[str, None] = 'e5f7a9c1b3d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'coupang_rocket_sales_daily',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('vendor_id', sa.String(length=20), nullable=False),
        sa.Column('option_id', sa.String(length=30), nullable=False),
        sa.Column('sku_id', sa.String(length=30), nullable=True),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('qty', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('revenue', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('visitors', sa.Integer(), nullable=True),
        sa.Column('conversion_rate', sa.Numeric(7, 4), nullable=True),
        sa.Column('product_name', sa.String(length=300), nullable=True),
        sa.Column('source', sa.String(length=20), nullable=False, server_default='sales_analysis'),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('vendor_id', 'option_id', 'date',
                            name='uq_coupang_rocket_sales_daily'),
    )
    op.create_index('ix_coupang_rocket_sales_daily_vendor_id',
                    'coupang_rocket_sales_daily', ['vendor_id'], unique=False)
    op.create_index('ix_coupang_rocket_sales_daily_option_id',
                    'coupang_rocket_sales_daily', ['option_id'], unique=False)
    op.create_index('ix_coupang_rocket_sales_daily_sku_id',
                    'coupang_rocket_sales_daily', ['sku_id'], unique=False)
    op.create_index('ix_coupang_rocket_sales_daily_date',
                    'coupang_rocket_sales_daily', ['date'], unique=False)

    op.create_table(
        'coupang_rocket_promotion',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('request_id', sa.String(length=30), nullable=False),
        sa.Column('vendor_id', sa.String(length=20), nullable=False),
        sa.Column('contract_id', sa.String(length=30), nullable=True),
        sa.Column('promotion_name', sa.String(length=300), nullable=True),
        sa.Column('promotion_type', sa.String(length=40), nullable=True),
        sa.Column('status', sa.String(length=30), nullable=True),
        sa.Column('start_at', sa.DateTime(), nullable=True),
        sa.Column('end_at', sa.DateTime(), nullable=True),
        sa.Column('share_ratio', sa.Numeric(7, 2), nullable=True),
        sa.Column('discount_method', sa.String(length=40), nullable=True),
        sa.Column('discount_value', sa.Numeric(12, 2), nullable=True),
        sa.Column('budget_amount', sa.Numeric(14, 2), nullable=True),
        sa.Column('settlement_date', sa.Date(), nullable=True),
        sa.Column('applied_product_count', sa.Integer(), nullable=True),
        sa.Column('requested_at', sa.DateTime(), nullable=True),
        sa.Column('raw', sa.JSON(), nullable=True),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_coupang_rocket_promotion_request_id',
                    'coupang_rocket_promotion', ['request_id'], unique=True)
    op.create_index('ix_coupang_rocket_promotion_vendor_id',
                    'coupang_rocket_promotion', ['vendor_id'], unique=False)
    op.create_index('ix_coupang_rocket_promotion_status',
                    'coupang_rocket_promotion', ['status'], unique=False)
    op.create_index('ix_coupang_rocket_promotion_start_at',
                    'coupang_rocket_promotion', ['start_at'], unique=False)

    # 기존 쿠폰 테이블 가산 컬럼(전부 nullable — 기존 행 무영향)
    op.add_column('coupang_coupon', sa.Column('used_amount', sa.Numeric(14, 2), nullable=True))
    op.add_column('coupang_coupon', sa.Column('used_amount_source', sa.String(length=20), nullable=True))
    op.add_column('coupang_coupon', sa.Column('used_amount_synced_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('coupang_coupon', 'used_amount_synced_at')
    op.drop_column('coupang_coupon', 'used_amount_source')
    op.drop_column('coupang_coupon', 'used_amount')

    op.drop_index('ix_coupang_rocket_promotion_start_at', table_name='coupang_rocket_promotion')
    op.drop_index('ix_coupang_rocket_promotion_status', table_name='coupang_rocket_promotion')
    op.drop_index('ix_coupang_rocket_promotion_vendor_id', table_name='coupang_rocket_promotion')
    op.drop_index('ix_coupang_rocket_promotion_request_id', table_name='coupang_rocket_promotion')
    op.drop_table('coupang_rocket_promotion')

    op.drop_index('ix_coupang_rocket_sales_daily_date', table_name='coupang_rocket_sales_daily')
    op.drop_index('ix_coupang_rocket_sales_daily_sku_id', table_name='coupang_rocket_sales_daily')
    op.drop_index('ix_coupang_rocket_sales_daily_option_id', table_name='coupang_rocket_sales_daily')
    op.drop_index('ix_coupang_rocket_sales_daily_vendor_id', table_name='coupang_rocket_sales_daily')
    op.drop_table('coupang_rocket_sales_daily')
