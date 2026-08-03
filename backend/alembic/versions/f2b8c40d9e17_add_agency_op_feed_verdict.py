"""피드 재적용 판별 컬럼 — naver_agency_op.feed_* (D-NAO-139)

「수정 사항」 화면이 신호를 잡음에 묻는 문제를 고친다. `ad_edit_tm`은 대행사가 소재를 만져도
전진하지만 **네이버가 상품 피드를 재적용해도 전진**해서, 08-03 하루만 봐도 실조작 4건이
피드 재적용 229건에 섞였다.

판별은 구조로 한다 — **같은 상품의 소재가 전량 같은 초로 움직였으면 피드**(사람은 하나씩
만진다). 규칙·검증·실패 모드는 `services/naver_ad/feed_reapply.py` docstring.

★왜 컬럼으로 저장하나(조회 시 계산이 아니라): `naver_adgroup_product`는 삭제하지 않는
**누적 테이블**이라 stale 행이 쌓이며 `total`이 계속 커진다. 조회 때마다 다시 세면 **과거
이벤트의 판정이 볼 때마다 달라진다.** 그래서 근거 숫자까지 판정 시점 값으로 굳힌다.

가산적이고 기존 경로를 건드리지 않는다: 전부 nullable이고, 소재 grain이 아닌 행
(campaign/adgroup grain)은 영구히 NULL로 남는다.

Revision ID: f2b8c40d9e17
Revises: e5b3f28a91c7
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2b8c40d9e17"
down_revision: Union[str, Sequence[str], None] = "e5b3f28a91c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLS = (
    ("feed_verdict", sa.String(8)),
    ("feed_product_id", sa.String(50)),
    ("feed_moved", sa.Integer()),
    ("feed_total", sa.Integer()),
)


def upgrade() -> None:
    for name, type_ in _COLS:
        op.add_column("naver_agency_op", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLS):
        op.drop_column("naver_agency_op", name)
