"""merge heads — pochg1obs9a(발주 관측 원장) + rtsnap1s3a(원가 왕복 스냅샷)

★왜 필요했나 (2026-08-28 15:0x KST 실측): 두 리비전이 **같은 부모 `cnote1trim8a`에서
  갈라져** alembic 헤드가 2개가 됐고, prod 배포에서 `alembic upgrade head`가
  «Multiple head revisions are present» 로 거부했다. 코드 배포는 그 앞에서 멈춰
  prod는 구버전 그대로였다(safe_deploy의 순서 가드가 제대로 동작한 것이다).

  · `rtsnap1s3a` — 원가 메뉴 트랙(다른 세션). **prod에 이미 적용돼 있다**(`alembic_version`
    실측 = `['rtsnap1s3a']`).
  · `pochg1obs9a` — 이 트랙(계약 CONTRACT_1p_po_status_history). 아직 미적용.

★이 파일은 **두 갈래를 잇기만 한다** — 스키마를 바꾸지 않는다(`upgrade`/`downgrade`가 비어 있다).
  남의 마이그레이션 파일을 고쳐 사슬을 다시 엮는 방법도 있으나, 그건 이미 prod에 적용된
  리비전의 부모를 바꾸는 것이라 **그쪽 트랙의 이력을 내가 다시 쓰는** 셈이 된다.
  병합 리비전은 양쪽을 그대로 둔 채 위에서 합치므로 남의 트랙을 건드리지 않는다.

★병행 세션이 활발한 저장소의 구조적 부채다 — 적대 리뷰 1R이 P2-9로 「선행 부채, 이 PR
  소관 아님」이라 짚었던 바로 그 자리이고, 실제로 배포에서 터졌다. 앞으로도 두 세션이
  같은 부모에서 마이그레이션을 만들면 재발한다(마이그 생성 전 `alembic heads` 확인이
  `next_ids.sh`와 같은 결의 습관이다).

Revision ID: mrg1po9rt3a
Revises: pochg1obs9a, rtsnap1s3a
Create Date: 2026-08-28 (KST)
"""
from alembic import op  # noqa: F401  (병합 리비전은 스키마를 안 바꾼다)
import sqlalchemy as sa  # noqa: F401

revision = "mrg1po9rt3a"
down_revision = ("pochg1obs9a", "rtsnap1s3a")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """스키마 변경 없음 — 두 갈래를 잇는 것이 이 리비전의 전부다."""


def downgrade() -> None:
    """스키마 변경 없음 — 되돌리면 다시 두 갈래가 된다."""
