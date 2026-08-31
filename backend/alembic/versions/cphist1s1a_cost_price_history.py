"""cost_price_history — `product_master.cost_price` 변경 이력 (계약 D-CPP-64 §4 S1-①)

순수 «추가»다: 새 테이블 1개 + 인덱스뿐이고 기존 테이블·컬럼을 건드리지 않는다.
⇒ 구코드가 이 테이블을 몰라도 깨지지 않으므로 `--migrate`의 정상 순서
   (마이그 → upgrade → 코드 → 재시작)를 그대로 쓸 수 있다.

★왜 이 테이블이 필요한가는 `models.CostPriceHistory` docstring에 있다(요지: `cost_price`를
바꾸는 4경로 중 **어느 것도 이력을 안 남겨** 「수정이 실수 없이 됐나」에 답할 방법이 없었다.
`product_master.updated_at`은 20일째 정지해 있어 대체재가 못 된다 — ref 118 §2-1·ref 119 §3).

★소급하지 않는다. 이 테이블은 배포 시점부터의 사실만 담고, 그 이전의 변경은 **영영 모른다.**
그건 결함이 아니라 이 계약이 늦게 시작했다는 사실이고, 화면이 그걸 말한다(「이력 시작 시각」).

Revision ID: cphist1s1a
Revises: pgprice1s1a
Create Date: 2026-08-31 21:0x KST
"""

from alembic import op
import sqlalchemy as sa

revision = "cphist1s1a"
down_revision = "pgprice1s1a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cost_price_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("internal_sku", sa.String(length=64), nullable=False),
        # FK를 걸지 않는다 — 상품 행이 지워져도 「그때 그 SKU의 원가가 이렇게 움직였다」는
        # 사실은 남아야 한다(models 참조).
        sa.Column("product_id", sa.Integer(), nullable=True),
        # nullable — 신규 생성이면 옛 값이 **없다**. 0이 아니다(「없음 ≠ 0」, 계약 §3-B).
        sa.Column("old_value", sa.Numeric(12, 2), nullable=True),
        sa.Column("new_value", sa.Numeric(12, 2), nullable=False),
        # 「어느 문으로 들어왔나」 — 이 계약의 질문이라 nullable이 아니다.
        sa.Column("path", sa.String(length=40), nullable=False),
        sa.Column("actor", sa.String(length=50), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        # ★`nullable=False` — 모델(`Mapped[datetime]`)과 **같아야** 한다. 어긋나면 테스트
        #   DDL(`create_all`)과 prod DDL이 달라지고, 그러면 「테스트에선 되는데 prod에선
        #   안 되는」 자리가 생긴다(픽스처가 prod와 다르면 결함을 못 잡는다).
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_cost_price_history_internal_sku", "cost_price_history", ["internal_sku"]
    )
    op.create_index("ix_cost_price_history_path", "cost_price_history", ["path"])
    op.create_index(
        "ix_cost_price_history_created_at", "cost_price_history", ["created_at"]
    )
    # 「이 SKU가 언제 어떻게 움직였나」가 조회의 기본 질문이라 복합 인덱스를 함께 둔다.
    op.create_index(
        "ix_cost_price_history_sku_created",
        "cost_price_history",
        ["internal_sku", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_cost_price_history_sku_created", table_name="cost_price_history")
    op.drop_index("ix_cost_price_history_created_at", table_name="cost_price_history")
    op.drop_index("ix_cost_price_history_path", table_name="cost_price_history")
    op.drop_index("ix_cost_price_history_internal_sku", table_name="cost_price_history")
    op.drop_table("cost_price_history")
