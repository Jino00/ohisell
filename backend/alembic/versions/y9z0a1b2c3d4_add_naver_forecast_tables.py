"""add naver forecast tables (예측·전문가 스프린트 F1 — forecast_engine, D-NAO-24)

Revision ID: y9z0a1b2c3d4
Revises: x8y9z0a1b2c3
Create Date: 2026-07-08 00:00:00.000000

F1 forecast_engine 코어(캠페인 grain 예측 모델)를 위한 신규 테이블 2개(additive):

  naver_forecast_model: grain(F1은 campaign만)별 게이트 상태(active/fallback/demoted)와
    최근 모델 계수 스냅샷 원장. forecast_gate가 매일 데이터 충분성을 재평가하고,
    forecast_scorer가 최근 성적 불량 시 demoted로 강등(demoted_until까지 쿨다운).

  naver_forecast_daily: target_date별 예측치(clk/cost/cpc/conv_amt) + 익일 백필되는
    실측·MAPE. forecast_model_builder가 생성(actual_*는 NULL), forecast_scorer가
    다음날 실측을 백필해 오차를 채운다.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'y9z0a1b2c3d4'
down_revision: Union[str, None] = 'x8y9z0a1b2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'naver_forecast_model',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('grain', sa.String(16), nullable=False),
        sa.Column('scope_key', sa.String(60), nullable=False),
        sa.Column('gate_status', sa.String(16), nullable=False, server_default='fallback'),
        sa.Column('sample_days', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('recent_mape', sa.Numeric(8, 4), nullable=True),
        sa.Column('demoted_until', sa.Date(), nullable=True),
        sa.Column('params_json', sa.Text(), nullable=True),
        sa.Column('trained_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('grain', 'scope_key', name='uq_naver_forecast_model'),
    )
    op.create_index('ix_naver_forecast_model_scope_key', 'naver_forecast_model', ['scope_key'])

    op.create_table(
        'naver_forecast_daily',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('target_date', sa.Date(), nullable=False),
        sa.Column('grain', sa.String(16), nullable=False),
        sa.Column('scope_key', sa.String(60), nullable=False),
        sa.Column('pred_clk', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('pred_cost', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('pred_cpc', sa.Numeric(10, 2), nullable=True),
        sa.Column('pred_conv_amt', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('actual_clk', sa.Integer(), nullable=True),
        sa.Column('actual_cost', sa.Integer(), nullable=True),
        sa.Column('actual_conv_amt', sa.Integer(), nullable=True),
        sa.Column('mape_clk', sa.Numeric(8, 4), nullable=True),
        sa.Column('mape_cost', sa.Numeric(8, 4), nullable=True),
        sa.Column('mape_conv_amt', sa.Numeric(8, 4), nullable=True),
        sa.Column('scored_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('target_date', 'grain', 'scope_key', name='uq_naver_forecast_daily'),
    )
    op.create_index('ix_naver_forecast_daily_target_date', 'naver_forecast_daily', ['target_date'])
    op.create_index('ix_naver_forecast_daily_scope_key', 'naver_forecast_daily', ['scope_key'])


def downgrade() -> None:
    op.drop_index('ix_naver_forecast_daily_scope_key', table_name='naver_forecast_daily')
    op.drop_index('ix_naver_forecast_daily_target_date', table_name='naver_forecast_daily')
    op.drop_table('naver_forecast_daily')
    op.drop_index('ix_naver_forecast_model_scope_key', table_name='naver_forecast_model')
    op.drop_table('naver_forecast_model')
