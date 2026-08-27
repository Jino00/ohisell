"""S4: OTAO 자사 재고 스냅샷 원장 신설 (계약 §4 S4 「초기 실사」 항)

계약 `docs/contracts/CONTRACT_inventory_unified.md` §4 S4 · 트랙 `docs/tracks/active/track_inventory-management.md`
앵커 `.claude/anchors/c3b74f44-7866-4de5-afc6-b0b5b28c9984.md` · 체인 `발주예측` n=8.

S4는 「파생 현재고(초기 실사 + 픽업 입고 − 판매)」를 화면에 세운다. 그 첫 항인 **자사 재고**가
지금 저장소 어디에도 없다 — n=8 실측(2026-08-27): prod `inventory` 테이블 **0행**, 저장소 전역
grep에서 실사 데이터 **0건**.

★**그리고 한 번 잃은 적이 있다.** n=2(2026-08-25)가 ECOUNT에서 창고별 재고 1,391행을 받아
되감기(`재고(t) = 현재고 − Σ입고(>t) + Σ판매(>t)`)를 188칸에 돌려 실증했는데, 그 스냅샷과
매핑 스크립트 3종이 **세션 스크래치패드에만 있었고 세션과 함께 사라졌다**(ref 99 §9가 스스로
"비커밋"이라 적어 뒀다). 이 트랙의 핵심 실증이 지금은 재현 불가다. 관측을 원장에 담지 않으면
관측은 주장으로만 남는다 — 그래서 테이블로 둔다.

★**순수 추가다** — 기존 테이블을 하나도 건드리지 않는다. `import_shipment`·`import_invoice_line`
(계약 A′/B 소관)은 여전히 **무접촉·읽기 전용**이고(§3-8), `inventory`(0행 레거시)도 손대지 않는다.

★**§3-1 「재고 정본 이원화 금지」에 걸리지 않는다.** 금지의 본뜻은 「양쪽에 동시에 **쓰기**」인데
이 테이블은 ①ECOUNT → ohisell 한 방향만이고 ②행을 정정하지 않으며(값이 달라지면 새
`snapshot_at`으로 새 행) ③수량의 권위를 주장하지 않는다(화면은 「ECOUNT 스냅샷(찍은 시각)」으로
부른다). Jino 원문이 그 한계를 이미 말했다: *"현재 본사 재고로 잡혀있는 수량들은 비슷한
수준이지 100%는 아니야"*(2026-08-25 18:08).

★**창고를 행 키에 넣는다** — §1 창고 5개 표가 「본사 / 본사-포장 / 반품창고 / 쿠팡 제트배송 /
아마존」의 의미가 서로 다르다고 원문으로 못 박았고, 초판 실측이 **전 창고 합계**를 내서 틀린
자리다. 합치는 것은 서비스 층이 역할별로 갈라서 할 일이고, 원장은 합치지 않는다.

★**유일 제약은 `(snapshot_at, warehouse_code, product_code)`이지 `(warehouse, product)`가 아니다.**
후자로 걸면 재적재가 «덮어쓰기»가 되어 시계열이 사라지는데, **오차는 두 시점 사이에서만 생기므로**
시계열이 없으면 S4의 「실사 대조 오차」를 원리적으로 못 잰다.

★`quantity`는 Integer가 아니라 **Numeric(16,3)**이다 — ECOUNT가 소수를 돌려줄 수 있고, 임의
반올림은 「원장이 말한 값」을 우리가 바꾸는 것이다.

Revision ID: otaostk1s4a
Revises: cst60auto
Create Date: 2026-08-27 KST
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "otaostk1s4a"
down_revision: Union[str, None] = "cst60auto"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ★nullable은 `app/models.py`의 `OtaoStockSnapshot`과 **글자 그대로 같아야 한다.**
    # n=4 적대 리뷰 P1-2가 정확히 이 자리였다(모델 False ↔ 마이그 True 드리프트로
    # `create_all`이 만든 스키마와 prod 스키마가 갈렸다).
    op.create_table(
        "otao_stock_snapshot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(), nullable=False),
        sa.Column("base_date", sa.Date(), nullable=True),
        sa.Column("warehouse_code", sa.String(length=30), nullable=False),
        sa.Column("warehouse_name", sa.String(length=80), nullable=True),
        sa.Column("product_code", sa.String(length=50), nullable=False),
        sa.Column("product_name", sa.String(length=300), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=16, scale=3), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("raw_json", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_at",
            "warehouse_code",
            "product_code",
            name="uq_otao_stock_snapshot_grain",
        ),
    )
    op.create_index(
        "ix_otao_stock_snapshot_snapshot_at",
        "otao_stock_snapshot",
        ["snapshot_at"],
    )
    op.create_index(
        "ix_otao_stock_snapshot_warehouse_code",
        "otao_stock_snapshot",
        ["warehouse_code"],
    )
    op.create_index(
        "ix_otao_stock_snapshot_product_code",
        "otao_stock_snapshot",
        ["product_code"],
    )


def downgrade() -> None:
    op.drop_index("ix_otao_stock_snapshot_product_code", table_name="otao_stock_snapshot")
    op.drop_index(
        "ix_otao_stock_snapshot_warehouse_code", table_name="otao_stock_snapshot"
    )
    op.drop_index("ix_otao_stock_snapshot_snapshot_at", table_name="otao_stock_snapshot")
    op.drop_table("otao_stock_snapshot")
