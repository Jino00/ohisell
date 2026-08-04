"""판정 근거 — naver_agency_op.feed_reason (D-NAO-139, 배포 직후 실증이 강제)

f2b8c40d9e17을 prod에 올린 직후, 08-03 사건 26건이 하룻밤 새 FEED→REAL로 뒤집히는 것을
확인했다. 원인은 `ad_edit_tm`이 **마지막 수정만** 남기는 것 — 그 소재들이 08-04 새벽 피드로
다시 움직이면서 08-03 시점의 근거가 덮였다. 즉 **소급 판정은 소재가 다시 움직이는 순간 썩는다.**

그래서 "판정할 수 없다"를 별도 상태로 남기게 됐는데, `feed_verdict`만으로는 그걸 구분할 수
없다: 같은 `unknown`이라도 「소재가 1개뿐」과 「근거가 덮였다」는 전혀 다른 상태이고, 같은
`real`이라도 「일부만 움직임」과 「추적 필드가 바뀜(가드①)」은 다른 이야기다. 화면이 둘을
같게 말하면 거짓말이 된다.

가산적·nullable. 소재 grain이 아닌 행은 영구히 NULL.

Revision ID: a91d3f60c7b2
Revises: f2b8c40d9e17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a91d3f60c7b2"
down_revision: Union[str, Sequence[str], None] = "f2b8c40d9e17"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("naver_agency_op", sa.Column("feed_reason", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("naver_agency_op", "feed_reason")
