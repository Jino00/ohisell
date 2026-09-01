"""naver_product_bep.price_basis + .logistics_basis (D-NAO-283 배송비 자 정합)

Revision ID: shipyard1s1a
Revises: cphist1s1a
Create Date: 2026-09-01 13:00:00.000000

계약 `docs/contracts/CONTRACT_shipping_yardstick.md` §4 합격기준 ⑤·§7.
트랙 `docs/tracks/active/track_naver-ad-optimization.md`.

**전부 additive · nullable**(기존 데이터 무영향, 구코드 안 깨짐 — `--migrate` 정순서로 배포).

BEP의 판매가·물류비는 각각 3단 폴백을 갖는데, 종전엔 **어느 단이 쓰였는지 행에 안 남았다.**
그래서 실측값과 폴백 상수가 화면의 같은 칸에 구분 없이 앉았고, 다음 세션이 「이 숫자가
실측인가 추정인가」를 되물을 수 없었다(`bep_calculator.py` 머리말이 이미 부채로 적어 둔 구멍).

  price_basis     — orders(실거래 median) / mapping(사람 입력, D-NAO-95) /
                    meta(커머스API 할인적용가, D-NAO-283 신설)
  logistics_basis — orders(자기 주문 실측) / sibling(같은 group_product_no 형제 실측, 신설) /
                    default(자기도 형제도 없음 = **측정이 아니라 «모름»**)

★백필하지 않는다. 다음 산출(크론 07:30 / 수동 recalc)이 전 행을 지우고 다시 쓰므로
  (bep_calculator.calculate_bep이 delete → insert 하는 스냅샷 테이블),
  과거 행에 값을 지어 넣는 것은 «없던 근거를 만드는 것»이다. 산출 전까지는 None이 정직하다.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'shipyard1s1a'
down_revision: Union[str, None] = 'cphist1s1a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('naver_product_bep', sa.Column('price_basis', sa.String(length=20), nullable=True))
    op.add_column('naver_product_bep', sa.Column('logistics_basis', sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column('naver_product_bep', 'logistics_basis')
    op.drop_column('naver_product_bep', 'price_basis')
