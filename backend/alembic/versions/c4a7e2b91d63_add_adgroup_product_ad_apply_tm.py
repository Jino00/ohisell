"""소재 피드 적용 시각 관측 — naver_adgroup_product.ad_apply_tm (D-NAO-137 S1)

`ad_edit_tm`은 대행사가 소재를 만져도 전진하지만 **네이버가 상품 피드를 재적용해도 전진한다**
(2026-08-03 라이브 실측: editTm 전진 233건 중 229건은 referenceData의 상품 데이터만 바뀌고
광고 설정(bidAmt·useGroupBidAmt·userLock)은 무변동 = 피드 재적용, 실제 조작은 4건).
즉 editTm 단독으로는 신호 4 : 잡음 229를 가르지 못한다.

후보 판별자가 `editTm − referenceData.APPLY_TM`인데(그날 표본에서 ≤120초 229건 : >1시간 4건),
**APPLY_TM은 API가 현재값만 주므로 과거분 소급 수집이 원리적으로 불가능하다** — 지금부터
적재하지 않으면 표본이 영영 하루치다. 그래서 판정 배선(S2)보다 적재(S1)를 먼저 한다.

이 마이그레이션은 **가산적이고 판정에 쓰이지 않는다**: 기존 행은 NULL로 남고(backfill 불가),
다음 07:45 sync가 관측한 행부터 채운다. 자료형은 에폭 밀리초라 32비트를 넘어 BigInteger.

Revision ID: c4a7e2b91d63
Revises: b6e1c93f4275
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4a7e2b91d63"
down_revision: Union[str, Sequence[str], None] = "b6e1c93f4275"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("naver_adgroup_product", sa.Column("ad_apply_tm", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("naver_adgroup_product", "ad_apply_tm")
