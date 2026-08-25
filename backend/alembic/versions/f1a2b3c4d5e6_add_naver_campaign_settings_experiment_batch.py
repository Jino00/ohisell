"""add naver_campaign_settings.experiment_batch (D-NAO-248 부록 Q3 — 풀링 경계 «실험 배치»)

Revision ID: f1a2b3c4d5e6
Revises: d5e6f7a8b9c0
Create Date: 2026-08-25 09:00:00.000000

계약 D-NAO-248 부록 Q3 원문: *"실험 배치(A/B·MOP 열·대조군·홀드아웃) — 경계 — 확정 — 합치면
A/B 오염"*. 지혜 수확기(wisdom_candidates)가 전역 시그니처로 표본을 합칠 때, 실험 배치 라벨이
붙은 캠페인의 관찰은 그 배치 밖 전역 풀과 **절대 섞이지 않아야** 한다 — 섞으면 대조군·MOP 열의
관찰이 「우리 정책」 학습으로 오염된다.

additive nullable String(60) — 기존 행 무영향. 대부분 캠페인은 실험 배치가 아니므로 NULL이
정상(=전역 풀 참여 자격).

★초기값 1건만 시드한다: campaign_id='cmp-a001-02-000000008492582'
(memo 원문 "D-47-g: 03=원본 MOP가 돌리는 캠페인. 03(MOP) vs 04(우리) 철학 A/B의 MOP 열
(D-NAO-42-e)")에 experiment_batch='iphone-philosophy-ab:mop'를 세팅한다 — 이 캠페인이 MOP
열임이 memo로 이미 확정돼 있다. 짝인 04(cmp-a001-02-000000008514959, 우리 열)에는 라벨을
넣지 않는다 — 그건 우리 정책 학습 재료라 전역 풀에 남아야 한다(D-NAO-248 §1: 91회 중 45회와
승격 지혜 71회가 둘 다 04 것 — 04를 빼면 계약이 풀려는 문제 자체가 사라진다).

이 UPDATE는 campaign_id로 조건을 건 1행뿐이고, 다른 캠페인은 손대지 않는다. downgrade는
컬럼 자체를 지운다(값도 함께 사라짐 — 이 결정 자체를 되돌리는 것이 이 downgrade의 의미).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = 'd5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MOP_LANE_CAMPAIGN_ID = 'cmp-a001-02-000000008492582'
_MOP_LANE_BATCH_LABEL = 'iphone-philosophy-ab:mop'


def upgrade() -> None:
    op.add_column(
        'naver_campaign_settings',
        sa.Column('experiment_batch', sa.String(60), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE naver_campaign_settings SET experiment_batch = :batch "
            "WHERE campaign_id = :cid"
        ).bindparams(batch=_MOP_LANE_BATCH_LABEL, cid=_MOP_LANE_CAMPAIGN_ID)
    )


def downgrade() -> None:
    op.drop_column('naver_campaign_settings', 'experiment_batch')
