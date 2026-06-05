"""add coupang_rg_inbound + coupang_wing_cookie (RG 발송관제 S1, D-1/D-5)

Revision ID: e1f3a5c7b9d2
Revises: f0a1b2c3d4e5
Create Date: 2026-06-05

쿠팡 RG 발송관제 트랙 S1 — 입고(inbound) 리드타임 원천 테이블.
- coupang_rg_inbound: Wing 내부 API 입고 옵션 그레인. 발송→판매개시 리드타임(statusId 3→7).
  grain=(account_key, inbound_id, vendor_item_id). 신규 테이블 → 기존 동작 불변.
- coupang_wing_cookie: 세션쿠키 시크릿(계정별). cookie_blob·xsrf_token은 Fernet 암호문 저장(D-5).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e1f3a5c7b9d2'
down_revision: Union[str, None] = 'f0a1b2c3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'coupang_rg_inbound',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_key', sa.String(length=20), nullable=False),
        sa.Column('inbound_id', sa.String(length=40), nullable=False),
        sa.Column('vendor_id', sa.String(length=20), nullable=True),
        sa.Column('vendor_item_id', sa.String(length=20), nullable=False),
        sa.Column('sku_id', sa.String(length=50), nullable=True),
        sa.Column('cached_sku_name', sa.String(length=300), nullable=True),
        sa.Column('requested_qty', sa.Integer(), nullable=True),
        sa.Column('received_qty', sa.Integer(), nullable=True),
        sa.Column('stowed_qty', sa.Integer(), nullable=True),
        sa.Column('shipment_created_at', sa.DateTime(), nullable=True),
        sa.Column('stowing_at', sa.DateTime(), nullable=True),
        sa.Column('lead_time_days', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('inbound_created_at', sa.DateTime(), nullable=True),
        sa.Column('raw_json', sa.Text(), nullable=True),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'account_key', 'inbound_id', 'vendor_item_id', name='uq_coupang_rg_inbound'
        ),
    )
    op.create_index(
        op.f('ix_coupang_rg_inbound_inbound_id'), 'coupang_rg_inbound', ['inbound_id'], unique=False
    )
    op.create_index(
        op.f('ix_coupang_rg_inbound_vendor_item_id'),
        'coupang_rg_inbound', ['vendor_item_id'], unique=False,
    )
    op.create_index(
        op.f('ix_coupang_rg_inbound_stowing_at'), 'coupang_rg_inbound', ['stowing_at'], unique=False
    )

    op.create_table(
        'coupang_wing_cookie',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_key', sa.String(length=20), nullable=False),
        sa.Column('cookie_blob', sa.Text(), nullable=True),
        sa.Column('xsrf_token', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=12), server_default='unknown', nullable=False),
        sa.Column('last_error', sa.String(length=300), nullable=True),
        sa.Column('last_saved_at', sa.DateTime(), nullable=True),
        sa.Column('last_success_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    # account_key는 모델 unique=True,index=True → unique index 하나로 충분(codex S1: 중복 제약 제거)
    op.create_index(
        op.f('ix_coupang_wing_cookie_account_key'),
        'coupang_wing_cookie', ['account_key'], unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_coupang_wing_cookie_account_key'), table_name='coupang_wing_cookie')
    op.drop_table('coupang_wing_cookie')
    op.drop_index(op.f('ix_coupang_rg_inbound_stowing_at'), table_name='coupang_rg_inbound')
    op.drop_index(op.f('ix_coupang_rg_inbound_vendor_item_id'), table_name='coupang_rg_inbound')
    op.drop_index(op.f('ix_coupang_rg_inbound_inbound_id'), table_name='coupang_rg_inbound')
    op.drop_table('coupang_rg_inbound')
