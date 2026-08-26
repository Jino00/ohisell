"""D-CPP-60: 원가 설정 이력 + 단가 자동 갱신 로그 3테이블 신설

계약 `docs/contracts/CONTRACT_cost_valuation_autorefresh.md` (Jino 승인 2026-08-26 10:12)
트랙 `docs/tracks/active/track_cost-truth-ledger.md` · 체인 `sellc-원가-메뉴` n=12
앵커 `.claude/anchors/2d1a0985-9f2b-47d7-8f7a-0bfe33a0fd50.md`

왜 세 테이블인가 — 계약 §7-3의 자동 3요소(사후 가시성·정정 경로·근거 보존)가 각각 «쌓이는
자리»를 요구한다. 셋 중 하나라도 자리가 없으면 자동이 아니라 방치다.

- `cost_setting_history` — 평가방법 확인·변경 이력(append 전용). `cost_setting`은 in-place로
  갱신되는 단일 행이라 «지금 값»만 안다. 그런데 법인세법 시행령 §74의 「신고한 방법」이
  나중에 확인되면 **그 시점 전후를 갈라야** 하므로, 지금 값만 남기면 그 질문에 영영 답할 수
  없다. `old_*`를 함께 담는 이유는 정정 경로(§7-3 ②)가 옛 값을 알아야 성립하기 때문이다.
- `cost_auto_refresh_run` — 자동 갱신 1회전. ★**`updated=0`이어도 행이 남는다**(§2-6 침묵
  금지). 그 행이 매일 쌓이는 것이 「자동이 살아 있다」의 유일한 증거다 — 안 남기면
  «돌았는데 바뀔 게 없었다»와 «죽었다»가 화면에서 똑같이 보인다.
- `cost_auto_refresh_entry` — 회전 안의 개별 사건. `hbl_no`·`item_name`을 FK가 아니라
  **스냅샷 문자열**로 담는다: 원장 재적재 시 SQLite rowid가 재사용되므로(계약 A′ S1 적대
  리뷰 1R P1-2 실증) id만으로는 추적이 끊긴다.

★**순수 추가다** — 기존 테이블을 하나도 건드리지 않는다. `cost_setting`·`cost_material`·
`cost_material_price`·`import_*`·`product_master` 전부 **무접촉**이다. 그래서 이 마이그레이션
자체는 어떤 값도 바꾸지 않는다(행위 변화 0).

★**Boolean에 정수 리터럴을 쓰지 않는다** (교훈 #341): `server_default=0`은 SQLite에선 통과하고
**PostgreSQL에선 타입 에러로 트랜잭션이 통째로 롤백된다** — 로컬·CI가 전부 초록인 채 prod에서만
죽는 모양이다. `sa.false()`를 쓴다(방언별로 FALSE/0으로 옳게 컴파일된다).

Revision ID: cst60auto
Revises: otaoev1merge
Create Date: 2026-08-26 KST
"""

from alembic import op
import sqlalchemy as sa

revision = "cst60auto"
down_revision = "otaoev1merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cost_setting_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("old_value", sa.String(length=200), nullable=True),
        sa.Column("new_value", sa.String(length=200), nullable=False),
        sa.Column("old_confirmed", sa.Boolean(), nullable=True),
        sa.Column(
            "new_confirmed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("actor", sa.String(length=50), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_cost_setting_history_key", "cost_setting_history", ["key"]
    )
    op.create_index(
        "ix_cost_setting_history_created_at", "cost_setting_history", ["created_at"]
    )

    op.create_table(
        "cost_auto_refresh_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(length=10), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("checked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queued", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_cost_auto_refresh_run_started_at", "cost_auto_refresh_run", ["started_at"]
    )

    op.create_table(
        "cost_auto_refresh_entry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=12), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=True),
        sa.Column("material_name", sa.String(length=200), nullable=True),
        sa.Column("price_id", sa.Integer(), nullable=True),
        sa.Column("import_invoice_line_id", sa.Integer(), nullable=True),
        sa.Column("hbl_no", sa.String(length=50), nullable=True),
        sa.Column("item_name", sa.String(length=200), nullable=True),
        sa.Column("old_price_ex_vat", sa.Numeric(14, 2), nullable=True),
        sa.Column("new_price_ex_vat", sa.Numeric(14, 2), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["cost_auto_refresh_run.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_cost_auto_refresh_entry_run_id", "cost_auto_refresh_entry", ["run_id"]
    )
    op.create_index(
        "ix_cost_auto_refresh_entry_outcome", "cost_auto_refresh_entry", ["outcome"]
    )
    op.create_index(
        "ix_cost_auto_refresh_entry_material_id",
        "cost_auto_refresh_entry",
        ["material_id"],
    )
    op.create_index(
        "ix_cost_auto_refresh_entry_line_id",
        "cost_auto_refresh_entry",
        ["import_invoice_line_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_cost_auto_refresh_entry_line_id", "cost_auto_refresh_entry")
    op.drop_index("ix_cost_auto_refresh_entry_material_id", "cost_auto_refresh_entry")
    op.drop_index("ix_cost_auto_refresh_entry_outcome", "cost_auto_refresh_entry")
    op.drop_index("ix_cost_auto_refresh_entry_run_id", "cost_auto_refresh_entry")
    op.drop_table("cost_auto_refresh_entry")
    op.drop_index("ix_cost_auto_refresh_run_started_at", "cost_auto_refresh_run")
    op.drop_table("cost_auto_refresh_run")
    op.drop_index("ix_cost_setting_history_created_at", "cost_setting_history")
    op.drop_index("ix_cost_setting_history_key", "cost_setting_history")
    op.drop_table("cost_setting_history")
