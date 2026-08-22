"""add coupang_wing_cookie.last_error_kind — 「로그인 필요」를 이벤트가 아니라 상태로

계약: docs/contracts/CONTRACT_collection_stability_s1.md (W1, Jino 승인 2026-08-22)

왜 컬럼인가: 지금 「로그인 필요」의 유일한 흔적은 `last_error` 문자열 안의 "[로그인 필요 …]"이고,
프론트는 그걸 **문자열 매칭**으로 읽는다(streamRefresh.ts `isLoginRequired`). 그래서
① 문구를 바꾸면 화면이 조용히 깨지고 ② 상태가 아니라 이벤트라 레인마다 다시 발견하고
③ 배너·집계가 「로그인 필요」와 「그 외 실패」를 구분하지 못한다.

`refresh_contract.status_fields()`를 6레인 전부가 공유하므로 이 컬럼 하나로 전 레인에 전파된다.

★nullable + server_default 없음: 기존 행은 NULL(=「분류되지 않은 상태」)로 남고, 다음 성공·실패
  보고에서 채워진다. 구코드가 이 컬럼을 몰라도 SELECT는 깨지지 않는다(추가만 하므로).
★String 컬럼이라 SQLite·PostgreSQL 모두에서 같은 DDL로 실행된다(교훈: M2-a의 Boolean 정수
  리터럴 사고 — SQLite는 타입을 강제하지 않아 로컬·CI가 전부 초록인데 PG에서만 죽었다).

Revision ID: cs1kind0a1b2
Revises: m2b2devw1eight
Create Date: 2026-08-22 (KST)
"""
from alembic import op
import sqlalchemy as sa

revision = "cs1kind0a1b2"
down_revision = "m2b2devw1eight"
branch_labels = None
depends_on = None

_TABLE = "coupang_wing_cookie"
_COL = "last_error_kind"


def _has_column(bind) -> bool:
    return _COL in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        return
    op.add_column(_TABLE, sa.Column(_COL, sa.String(length=32), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        return
    op.drop_column(_TABLE, _COL)
