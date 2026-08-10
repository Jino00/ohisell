"""rocket_product_cost_map: status 'ignored' → 'excluded' (뜻을 하나로 통일)

왜 (2026-08-10, Jino 결정):
  `ignored` 하나가 **세 모듈에서 서로 다른 뜻**으로 읽히고 있었다 —
    · rocket_promo_pnl      : 원가 **0원**·해결됨
    · rocket_intelligence   : 원가 **0원**·`resolved_amount`에 가산(커버리지에도 포함)
    · rocket_1p_revenue     : 원가 **미상**·작업 목록에서만 제외
  그래서 2026-06-17 일괄 매핑이 «후보를 못 찾은» 22건을 `ignored`로 찍자, 두 엔진이 그것을
  **원가 0원 = 전액 이익**으로 셌다(90일 발주 실측: 발주 8,146,140원 / 진짜 원가 3,311,826원).

무엇을 바꾸나:
  값 이름을 `excluded`로 바꾸고 **뜻을 하나로 못 박는다** —
  «연결 안 함 · 재제안 방지 · 손익에서는 「모름」». 「원가 0원」 해석은 코드에서 제거된다.

★레거시 행을 `excluded`로 **올리는 것**이 왜 안전한가:
  이 마이그레이션은 이름만 바꾸고, 「원가 0원 → 모름」이라는 **의미 하향**은 코드가 한다.
  즉 옛 `ignored`가 무엇을 뜻했든 새 코드에서는 「모름」으로 다뤄져 이익이 부풀지 않는다.
  (반대로 「원가 0원」을 유지했다면 근거 없는 주장을 남기는 것이 된다.)

★배포 순서가 이 방향이라야 하는 이유(마이그 → 코드):
  구코드는 `status == 'ignored'` 비교가 전부 빗나가 `else`로 떨어지고, 거기서
  `internal_sku`가 NULL이라 원가가 None이 된다 = **미해결**. 즉 구코드도 안전한 쪽으로
  degrade 한다. 반대 순서면 신코드가 옛 값을 못 알아본다.

★prod 실측 2026-08-10: `ignored` **0건**(전날 16건을 링크로 해소). 그래서 이 마이그레이션은
  prod에서 사실상 no-op이고, 손익 숫자는 한 푼도 안 움직인다 — 바꾸기 가장 안전한 시점이다.
  (다른 환경에 남아 있을 수 있어 마이그레이션으로 둔다. 되돌릴 수 있게 downgrade도 쓴다.)

Revision ID: e4c7a1b8d206
Revises: b7d1e4f92a06
Create Date: 2026-08-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "e4c7a1b8d206"
down_revision = "b7d1e4f92a06"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    """테이블이 없는 환경(구 스냅샷)에서도 죽지 않게."""
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("rocket_product_cost_map"):
        return
    op.execute(
        "UPDATE rocket_product_cost_map SET status = 'excluded' WHERE status = 'ignored'"
    )


def downgrade() -> None:
    # ★되돌려도 «원가 0원» 해석은 코드에서 사라진 상태다 — 값 이름만 원복한다.
    #
    # ⚠️**코드까지 롤백하면 위험하다**(적대 리뷰 P2-10): 신코드가 만든 «진짜» excluded 행이
    #   `ignored`가 되고, 구코드는 그것을 다시 **원가 0원·해결됨**으로 센다 = 이익 과대가
    #   되살아난다. 이름 되돌리기의 본질적 위험이라 마이그레이션이 막을 수 없다 —
    #   코드를 되돌려야 한다면 그 행들을 먼저 확인할 것:
    #     SELECT product_number, note FROM rocket_product_cost_map WHERE status='excluded';
    if not _has_table("rocket_product_cost_map"):
        return
    op.execute(
        "UPDATE rocket_product_cost_map SET status = 'ignored' WHERE status = 'excluded'"
    )
