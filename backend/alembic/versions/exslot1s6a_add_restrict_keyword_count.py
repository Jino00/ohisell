"""add naver_adgroup_target_current.restrict_keyword_count — 제외 슬롯 사용량을 원장으로

계약: docs/contracts/CONTRACT_ignition_readiness.md §4-A S6 (Jino 승인 2026-08-26) · ref 66 §5-1

왜 컬럼인가: `/ncc/targets` 응답엔 `RESTRICT_KEYWORD_TARGET`이 이미 실려 오는데
`get_adgroup_targets`가 **「있었다」는 이름(target_types)만 남기고 버렸다** — 제외 슬롯이
그룹당 70칸(ref 24·30, 네이버 공식)인데 **몇 칸 찼는지 아무도 안 세고 있었다**.
`get_ads`가 파워링크 소재를 버리던 것과 같은 자리다(D-NAO-263).
배너 렌더 경로에서 네이버 API를 부르지 않는 것이 이 계열의 규율이라(ref 66 §5-1·
exclusion_survival 모듈 주석) 라이브 count를 **저장**해야 화면이 읽는다.

★nullable + server_default 없음이 이 컬럼의 전부다: `NULL` = 「셀 수 없었다」(프로브 비-200
  또는 스키마 이상)이고 `0` = 「제외가 하나도 없다」다. 둘을 0으로 뭉개면 조회가 죽은 그룹이
  **잔여 70칸의 여유로운 초록**으로 보인다 — 모름을 0건으로 세는 것이 이 계열의 고질
  결함이다(교훈 #123). 기존 1,013행은 NULL로 남고 다음 스윕(09:35)이 채운다.
★Integer 컬럼이라 SQLite·PostgreSQL 모두 같은 DDL로 실행된다.
★구코드가 이 컬럼을 몰라도 SELECT는 깨지지 않는다(추가만 하므로) — 그래도 배포 순서는
  `safe_deploy.sh --migrate`가 강제하는 ①마이그→②upgrade→③코드→④재시작을 따른다.

Revision ID: exslot1s6a
Revises: pltext1s5a
Create Date: 2026-08-27 (KST)
"""
from alembic import op
import sqlalchemy as sa

revision = "exslot1s6a"
down_revision = "pltext1s5a"
branch_labels = None
depends_on = None

_TABLE = "naver_adgroup_target_current"
_COL = "restrict_keyword_count"


def _has_column(bind) -> bool:
    return _COL in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        return
    op.add_column(_TABLE, sa.Column(_COL, sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        return
    op.drop_column(_TABLE, _COL)
