"""merge heads — cs1kind0a1b2(수집 안정화 S1) + imp1ledger47a(수입원가 원장)

★왜 필요한가: 두 브랜치가 **같은 부모**(`m2b2devw1eight`)에서 각자 마이그레이션을 만들어
  머지 후 head가 둘이 됐다. 그대로 두면 다음 사람의 `alembic upgrade head`가
  `Multiple head revisions are present` 로 죽고, 이 저장소는 배포 시 `safe_deploy.sh --migrate`가
  그 명령을 쓰므로 **모든 DB 배포가 막힌다**.

★prod는 이미 둘 다 적용돼 있다(2026-08-22 실측: `alembic_version`에 `cs1kind0a1b2`와
  `imp1ledger47a` **두 행**, `coupang_wing_cookie.last_error_kind` 컬럼과 `import_*` 테이블
  5종이 모두 존재). 즉 이 파일은 **스키마를 바꾸지 않는다** — 갈라진 계보를 하나로 접어
  버전 행을 하나로 되돌리는 것이 전부다.

★내용이 비어 있는 것이 정상이다(alembic merge revision의 정의). 여기에 DDL을 추가하지 말 것 —
  merge revision은 두 계보 어느 쪽에서 올라와도 통과해야 하므로, 한쪽에만 유효한 변경을
  넣으면 다른 경로에서 깨진다.

Revision ID: mrg2heads0822
Revises: cs1kind0a1b2, imp1ledger47a
Create Date: 2026-08-22 (KST)
"""

revision = "mrg2heads0822"
down_revision = ("cs1kind0a1b2", "imp1ledger47a")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """no-op — 계보 병합 전용."""


def downgrade() -> None:
    """no-op — 계보 병합 전용."""
