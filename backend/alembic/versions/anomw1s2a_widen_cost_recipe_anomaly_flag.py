"""cost_recipe.anomaly_flag 40 → 200 (원천 `cost_table_item.anomalies`와 폭을 맞춘다)

왜: 이 컬럼은 `String(40)`인데 값을 채우는 세 자리가 전부 원천을 `[:40]`으로 **잘라서**
넣고 있었다. 원천 `CostTableItem.anomalies`는 `String(200)`이라, 좁은 쪽이 넓은 쪽을
받아 적는 구조 자체가 **손실을 보장**했다.

★손실은 표시 흐림이 아니라 **판정 변화**다. 인박스 묶음은 프론트 `costHome.ts`의
`anomalyKinds()`가 문자열을 `,`로 갈라 `:` 앞을 토큰으로 읽어 정한다. 40자에서 끊기면
꼬리 토큰이 반토막 나고, 반토막은 어느 묶음 규칙과도 안 맞는다.

prod 실측 (2026-09-04 12:0x KST, 읽기 전용):
  · `length(anomaly_flag) = 40`인 행 **3개** — r45 · r81 · r97
  · r45·r97 → `['price_conflict', 'price_co']` — 첫 토큰이 살아 「모순」 묶음엔 **선다**(표시만 깨졌다)
  · r81   → `['landed_only_vat_unknown', 'needs_manual_lin']`
           원본은 `landed_only_vat_unknown,needs_manual_lines`(42자)였다.
           `needs_manual_lin`은 「구성 없음」 규칙(`no_recipe_match` | `needs_manual_lines`)에
           **안 맞고** `price_conflict`도 아니다 ⇒ **어느 묶음에도 안 서서 화면에서 사라졌다.**

이 마이그레이션이 하는 것 둘:
  ①폭을 200으로 넓힌다 — 앞으로 잘리지 않는다(코드의 `[:40]` 제거와 짝이다).
  ②**복원 가능한 행만** 되살린다 — 판별 근거는 「저장값이 픽된 항목 `anomalies`의 접두사」다.
    접두사가 아니면 손대지 않는다(같은 40자라도 다른 경로로 들어온 값일 수 있다).

⚠️r81은 `picked_item_id`가 **NULL**이라 ②로 복원되지 않는다 — 그 값은 업로드 경로에서
왔고 DB 안에 원본이 없다. **원가표 재업로드가 유일한 회복 경로**다. 마이그레이션이
지어내지 않는다.

⚠️SQLite는 컬럼 타입 변경에 테이블 재생성이 필요해 batch 모드를 쓴다
(같은 테이블에 대한 선례: `grainv1s1a`, prod 적용 완료).

Revision ID: anomw1s2a
Revises: grainv1s1a
Create Date: 2026-09-04 (KST)
"""

from alembic import op
import sqlalchemy as sa

revision = "anomw1s2a"
down_revision = "grainv1s1a"
branch_labels = None
depends_on = None


# 저장값이 원천의 «접두사»인 행만 되살린다. 접두사 검사가 곧 「이건 잘린 것이다」의 증명이다 —
# 길이가 40이라는 사실만으로는 잘렸다고 말할 수 없다(우연히 40자인 온전한 값이 있을 수 있다).
_REPAIR_SQL = """
UPDATE cost_recipe
   SET anomaly_flag = (
         SELECT cti.anomalies FROM cost_table_item cti
          WHERE cti.id = cost_recipe.picked_item_id
       )
 WHERE picked_item_id IS NOT NULL
   AND anomaly_flag IS NOT NULL
   -- ★빈 문자열을 빼는 이유(적대 리뷰 1R P2-3): `substr(x, 1, 0) = ''`는 **항상 참**이라
   --   빈 깃발이 「접두사」로 판정돼 픽된 항목의 문자열로 통째로 덮인다.
   --   prod 실측 0건이라 오늘은 안 물리지만, 접두사 검사가 곧 「잘렸다」의 증명인데
   --   빈 문자열은 아무것도 증명하지 않는다.
   AND anomaly_flag <> ''
   AND EXISTS (
         SELECT 1 FROM cost_table_item cti
          WHERE cti.id = cost_recipe.picked_item_id
            AND cti.anomalies IS NOT NULL
            AND cti.anomalies <> cost_recipe.anomaly_flag
            AND substr(cti.anomalies, 1, length(cost_recipe.anomaly_flag))
                = cost_recipe.anomaly_flag
       )
"""


def upgrade() -> None:
    with op.batch_alter_table("cost_recipe", schema=None) as batch_op:
        batch_op.alter_column(
            "anomaly_flag",
            existing_type=sa.String(length=40),
            type_=sa.String(length=200),
            existing_nullable=True,
        )
    op.get_bind().execute(sa.text(_REPAIR_SQL))


def downgrade() -> None:
    # ⚠️폭만 되돌린다. **복원된 문자열은 다시 자르지 않는다** —
    #   되돌리기가 데이터를 말없이 지우게 두지 않는다(선례 `grainv1s1a`의 downgrade 주석과 같은 원칙).
    #   SQLite는 VARCHAR 길이를 강제하지 않으므로 긴 값이 남아도 동작하고,
    #   PostgreSQL이라면 이 downgrade가 **실패하는 것이 옳다**(잘라야 통과하는 되돌리기는 손실이다).
    with op.batch_alter_table("cost_recipe", schema=None) as batch_op:
        batch_op.alter_column(
            "anomaly_flag",
            existing_type=sa.String(length=200),
            type_=sa.String(length=40),
            existing_nullable=True,
        )
