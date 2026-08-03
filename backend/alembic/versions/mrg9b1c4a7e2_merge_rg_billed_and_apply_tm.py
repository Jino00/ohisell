"""merge heads — rg9billed7c4e(S9 청구근거 컬럼) + c4a7e2b91d63(소재 APPLY_TM)

Revision ID: mrg9b1c4a7e2
Revises: rg9billed7c4e, c4a7e2b91d63
Create Date: 2026-08-03 20:30:00.000000

두 세션이 같은 부모(b6e1c93f4275)에서 동시에 갈라져 head가 2개가 됐다:
  · rg9billed7c4e  — RG 정산 청구근거 3컬럼(S9, 이 세션). **prod에 이미 적용됨.**
  · c4a7e2b91d63  — 소재 피드 적용시각 APPLY_TM(다른 세션, D-NAO-137 S1). prod 미적용.

**어느 쪽도 재부모하지 않는다.** 내 리비전은 prod의 `alembic_version`에 이미
rg9billed7c4e(부모=b6e1c93f4275)로 기록돼 있어서, 부모를 c4a7e2b91d63으로 바꾸면 alembic이
"c4a7e2b91d63은 조상이므로 이미 적용됐다"고 오판해 **남의 마이그레이션을 영구히 건너뛴다**
(컬럼 없이 그쪽 코드가 도는 순간 `no such column`으로 그 ingest 경로가 통째로 침묵 —
프로젝트 CLAUDE.md가 경고하는 바로 그 사고 모양이다). 반대로 남의 것을 재부모하는 것도
그쪽 세션의 배포 이력을 내가 고치는 일이라 하지 않는다.

그래서 alembic이 지원하는 **병합 리비전**으로 합류만 시킨다(스키마 변경 0).
`test_single_head_linear_chain`의 docstring이 지정한 방식이다.

배포 순서(다음 배포자): prod는 현재 rg9billed7c4e에 있으므로 `alembic upgrade head`가
c4a7e2b91d63 → mrg9b1c4a7e2 순으로 적용한다. c4a7e2b91d63은 컬럼 추가(additive)라
그쪽 코드보다 먼저 적용되는 것이 안전한 순서다(마이그레이션 선행 원칙).
이 세션은 이 파일을 **배포하지 않는다** — 내 코드는 이미 prod에서 검증됐고, 남의
마이그레이션을 내가 대신 적용하지 않기 위함이다.
"""
from typing import Sequence, Union

from alembic import op  # noqa: F401  (병합 리비전은 DDL 없음 — 임포트는 관례상 유지)
import sqlalchemy as sa  # noqa: F401


# revision identifiers, used by Alembic.
revision: str = 'mrg9b1c4a7e2'
down_revision: Union[str, Sequence[str], None] = ('rg9billed7c4e', 'c4a7e2b91d63')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """합류점 — 스키마 변경 없음."""
    pass


def downgrade() -> None:
    """합류점 — 스키마 변경 없음."""
    pass
