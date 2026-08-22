"""merge: 수입건 원장(imp1ledger47a) + Wing 쿠키 last_error_kind(cs1kind0a1b2)

두 세션이 **같은 부모(`m2b2devw1eight`)에서 각자 갈라져** 나온 결과 head가 2개가 됐다.
둘 다 additive(테이블 신설 / nullable 컬럼 추가)라 순서 의존성이 없고, prod에는 이미
**둘 다 적용돼 있다**(2026-08-22 15:4x 실측: `alembic heads` → 두 줄).

이 리비전은 DDL을 하나도 만들지 않는다 — 단지 두 갈래를 다시 하나로 합쳐
`alembic upgrade head`가 다시 동작하게 한다. 이게 없으면 다음 배포가
`Multiple head revisions are present`로 막힌다(내가 실제로 그렇게 막혔다).

★왜 갈라졌나: `collection-stability-s1` 세션이 마이그레이션과 코드를 prod에 배포하고
PR을 올리기 전이었고, 그 사이 내가 같은 부모에서 갈라져 나왔다. 저장소가 경고하는
「배포 → 나중에 PR」 관례가 만드는 상태다(safe_deploy.sh 머리말). CAS 가드가
`models.py` clobber는 막아 줬고(그게 그 가드의 존재 이유다), 남은 alembic 분기를
여기서 닫는다.

Revision ID: mrg48s1heads
Revises: imp1ledger47a, cs1kind0a1b2
Create Date: 2026-08-22 KST
"""
from typing import Sequence, Union

revision: str = "mrg48s1heads"
down_revision: Union[str, Sequence[str], None] = ("imp1ledger47a", "cs1kind0a1b2")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """DDL 없음 — 분기 합류만 한다."""


def downgrade() -> None:
    """DDL 없음 — 되돌려도 두 갈래로 갈라질 뿐이다."""
