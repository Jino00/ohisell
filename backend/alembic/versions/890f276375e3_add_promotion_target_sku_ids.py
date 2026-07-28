"""add coupang_rocket_promotion.target_sku_ids (Phase 2 손익 엔진 — 대상 SKU 수기 지정)

트랙: docs/tracks/active/track_coupang-promo-pnl.md (Phase 2)

가산 변경 1건(ADD COLUMN, nullable JSON) — 기존 행·기존 소비자 무영향, net_profit 불변.

왜 컬럼이 필요한가(2026-07-28 라이브 실측): 공급자허브 프로모션 API 응답에는 **적용 상품 목록이
  없다.** 있는 건 `detailCount`(적용상품 **수**)뿐이다 — 687878의 raw JSON 전 필드를 prod에서
  확인했다(detailCount=2, 상품 배열 없음). 그런데 손익을 계산하려면 "이 프로모션 창에서 어느
  SKU가 팔렸나"를 알아야 한다.
  ⇒ 추정 매핑(이름 유사도·기간 겹침 등)은 **금지**한다: 조용히 틀린 SKU를 물면 그 프로모션의
     손익·BEP ROAS가 전부 틀리는데 어디서도 대사되지 않는다(원칙22).
  ⇒ D-CPP-7의 unit_discount_amount와 **같은 성격의 수기 입력**으로 받는다. 페처는 이 칸을 절대
     쓰지 않으므로 재수집(snapshot upsert)이 수기값을 지우지 않는다.

JSON 리스트로 두는 이유: 한 프로모션에 SKU가 여러 개다(687878=2개). 별도 테이블을 만들 만큼의
  질의 요구가 없고(프로모션 단위로 통째로 읽는다), 스냅샷 upsert 경로가 손대지 않는 단일 칸이
  수기값 보존을 가장 단순하게 보장한다.

Revision ID: 890f276375e3
Revises: df7b34a2f46e
Create Date: 2026-07-28

★revision ID는 `uuid4().hex[:12]`로 뽑았다 — 손으로 짓지 않는다(LESSONS #50).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '890f276375e3'
down_revision: Union[str, None] = 'df7b34a2f46e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'coupang_rocket_promotion',
        sa.Column('target_sku_ids', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('coupang_rocket_promotion', 'target_sku_ids')
