"""add coupang_rocket_invoice_confirm — 「거래명세서확인」(RI→CI) 실행 명령 겸 감사 레코드

계약: docs/contracts/CONTRACT_1p_invoice_confirm_write.md §4 S1 (Jino 승인 2026-08-28 07:28 KST)
정찰: docs/references/106_ri_confirm_recon_20260827.md

왜 새 표인가: supplier 쓰기는 이 저장소 전체에 **0건**이라 재사용할 원장이 없다. 그리고 이
  쓰기는 **되돌릴 수 없는 회계 확정**이라(CI→RI 복귀 경로가 supplier 화면에 없다) 정정 경로가
  없다 — 전역 §1이 요구하는 셋 중 「사후 가시성」과 「근거 보존」을 이 표가 통째로 진다.
  기존 `coupang_wing_cookie`의 refresh 플래그 방식을 쓰지 않은 이유: 그건 **읽기 갱신용**이라
  자동 재claim(재시도 3회)이 계약에 박혀 있고, 쓰기에 얹으면 같은 확인을 두 번 누른다
  (`refresh_contract.py` 헤더 계약 — 계약 §3 금지선이 이 재사용을 명시적으로 금지한다).

★한 행 = 사실 1개. 한 명령 = PO 1건 = POST 최대 1회. 배치 컬럼이 없다(그게 설계다).
★`response_body`가 Text인 이유: supplier가 실패를 구조화해 주지 않는다(`alert(data)`).
  body 원문이 유일한 진단 재료라 success 불리언만 남기는 것은 계약 §3 금지선이다.
★`purchase_order_seq`에 unique를 걸지 않는다 — 이력 누적이다. 「열린 명령 중복 금지」는
  DB 제약이 아니라 서비스가 state로 판정한다(pending/claimed가 있으면 새 명령을 안 만든다).
  ★★unique를 걸면 오히려 위험하다: 재시도 금지의 근거인 **과거 unknown 행이 덮여 사라진다.**
★시각은 전부 KST naive(`kst_now()`) — 이 저장소 로켓 계열 `synced_at`과 같은 규약이라
  server_default(SQLite `now()` = UTC)를 쓰지 않는다.

배포 순서: `safe_deploy.sh ... --migrate --restart`가 ①마이그→②upgrade→③코드→④재시작을
  강제한다. 신규 표 추가라 구코드를 깨지 않는다(구코드는 이 표를 모른다).

Revision ID: ricfm1w7b
Revises: exslot1s6a
Create Date: 2026-08-28 (KST)
"""
from alembic import op
import sqlalchemy as sa

revision = "ricfm1w7b"
down_revision = "exslot1s6a"
branch_labels = None
depends_on = None

_TABLE = "coupang_rocket_invoice_confirm"


def _has_table(bind) -> bool:
    return _TABLE in set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("purchase_order_seq", sa.Integer(), nullable=False),
        sa.Column("vendor_id", sa.String(length=20), nullable=False),
        # pending | claimed | succeeded | already_confirmed | failed | unknown
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("requested_note", sa.String(length=200), nullable=True),
        sa.Column("received_amount_at_request", sa.Integer(), nullable=True),
        sa.Column("lease", sa.String(length=40), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        # 사전 GET 게이트: button_present | button_absent | fetch_failed
        sa.Column("precheck", sa.String(length=20), nullable=True),
        sa.Column("precheck_http_status", sa.Integer(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.String(length=500), nullable=True),
    )
    op.create_index(
        "ix_coupang_rocket_invoice_confirm_purchase_order_seq", _TABLE, ["purchase_order_seq"]
    )
    op.create_index("ix_coupang_rocket_invoice_confirm_vendor_id", _TABLE, ["vendor_id"])
    op.create_index("ix_coupang_rocket_invoice_confirm_state", _TABLE, ["state"])
    op.create_index("ix_coupang_rocket_invoice_confirm_requested_at", _TABLE, ["requested_at"])
    op.create_index("ix_coupang_rocket_invoice_confirm_lease", _TABLE, ["lease"])


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        return
    # ⚠️ 이 표는 되돌릴 수 없는 쓰기의 **유일한 근거 보존처**다. downgrade는 스키마 되돌리기용
    #    경로일 뿐이고, 운영 중 실행하면 감사 이력이 통째로 사라진다 — 실행 전 덤프를 뜰 것.
    op.drop_table(_TABLE)
