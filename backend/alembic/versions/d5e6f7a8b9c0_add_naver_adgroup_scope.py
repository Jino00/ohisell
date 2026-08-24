"""add naver_adgroup_scope (자동운영 스코프의 광고그룹 축 — D-NAO-244)

Revision ID: d5e6f7a8b9c0
Revises: cst3exref53c
Create Date: 2026-08-24 20:00:00.000000

Jino 원문 2026-08-24: *"우리 엔진의 스코프는 캠페인, 광고그룹 모두 포함해야해"*.

캠페인 축(naver_campaign_settings.auto_operate) 아래에 광고그룹 축을 한 단계 더 둔다.
결합 규칙은 «캠페인 마스터 ∧ 그룹 제한» — 진리표 단일 소스는
`app.services.naver_ad.adgroup_scope.in_scope_now` 하나다.

★additive이고 **행이 0개면 행위 변화 0**이다: 스코프 행이 없는 캠페인은 리졸버가
「제한 없음」으로 판정하므로 기존 동작(캠페인 켜지면 전 그룹)이 그대로 유지된다.
B3 카나리 게이트(AD_BID_CANARY_CAMPAIGNS)의 「기본 빈 집합 = 전면 hold, 배포 즉시
행위 변화 0」 원칙을 상수에서 테이블로 옮긴 것 — 상수와 달리 그룹 하나를 빼고 넣는 데
배포가 필요 없다(UPDATE 1문).

★이 마이그레이션은 스코프 행을 **넣지 않는다.** 어느 그룹을 열지(개시)는 별도 계약이다.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, None] = 'cst3exref53c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'naver_adgroup_scope',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('campaign_id', sa.String(50), nullable=False),
        sa.Column('adgroup_id', sa.String(50), nullable=False),
        # accel / boundary / brake — 역할 라벨(판정·가드·화면용, 입찰 로직 분기 아님)
        sa.Column('role', sa.String(12), nullable=True),
        # False = 행은 남기되 잠시 끔(되돌리기 사다리 첫 칸)
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('memo', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('campaign_id', 'adgroup_id', name='uq_naver_adgroup_scope'),
    )
    op.create_index(
        'ix_naver_adgroup_scope_campaign_id', 'naver_adgroup_scope', ['campaign_id']
    )
    op.create_index(
        'ix_naver_adgroup_scope_adgroup_id', 'naver_adgroup_scope', ['adgroup_id']
    )


def downgrade() -> None:
    op.drop_index('ix_naver_adgroup_scope_adgroup_id', table_name='naver_adgroup_scope')
    op.drop_index('ix_naver_adgroup_scope_campaign_id', table_name='naver_adgroup_scope')
    op.drop_table('naver_adgroup_scope')
