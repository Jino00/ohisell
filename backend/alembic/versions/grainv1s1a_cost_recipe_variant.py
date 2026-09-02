"""cost_recipe.variant — grain의 셋째 축 (계약 D-CPP-67 S1)

왜: 「상품명 × 폼팩터」로는 못 가르는 묶음이 92 SKU 실재했다(ref 124). 레시피 3개 안에
구성 여럿(매트 flip 5 · fold 4)·크기 여럿(태블릿 3)이 섞여 있어 `cost_standard`가
레시피당 1행이라는 성질과 충돌했고, 그 92건은 「그레인 불일치 — 보류」에 갇혀 있었다.

이 마이그레이션은 **컬럼 1개 추가 + 유니크 제약 교체**만 한다. 기존 100개 레시피는 전건
`variant = ''`(단일 그레인)이 되고 동작이 바뀌지 않는다 — 값을 채우는 것은 코드가 아니라
D-CPP-67 §4 S1·S2의 분할 실행이다.

⚠️SQLite는 `ALTER TABLE ... DROP CONSTRAINT`가 없어 batch 모드(테이블 재생성)를 쓴다.

Revision ID: grainv1s1a
Revises: shipyard1s1a
Create Date: 2026-09-02 (KST)
"""

from alembic import op
import sqlalchemy as sa

revision = "grainv1s1a"
down_revision = "shipyard1s1a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("cost_recipe", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "variant",
                sa.String(length=60),
                nullable=False,
                server_default="",
            )
        )
        # 옛 제약을 먼저 떨어뜨린다 — 새 제약이 그것의 «확장»이라 공존할 이유가 없다.
        batch_op.drop_constraint("uq_cost_recipe_name_form", type_="unique")
        batch_op.create_unique_constraint(
            "uq_cost_recipe_name_form_variant",
            ["product_name", "form_factor", "variant"],
        )


def downgrade() -> None:
    # ⚠️되돌리면 분할된 변형 레시피들이 (product_name, form_factor) 중복이 되어 옛 제약을
    #   위반한다. 분할을 실행한 뒤에는 이 downgrade가 실패하는 것이 **옳다** — 데이터를
    #   말없이 지우는 downgrade를 쓰지 않는다.
    with op.batch_alter_table("cost_recipe", schema=None) as batch_op:
        batch_op.drop_constraint("uq_cost_recipe_name_form_variant", type_="unique")
        batch_op.create_unique_constraint(
            "uq_cost_recipe_name_form", ["product_name", "form_factor"]
        )
        batch_op.drop_column("variant")
