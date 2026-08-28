"""add coupang_rocket_po_change_log — 스냅샷 upsert가 버리던 «직전 값»의 관측 원장

계약: docs/contracts/CONTRACT_1p_po_status_history.md (Jino 승인 2026-08-28 13:33 KST)

왜 새 표인가: `_upsert_po`가 snapshot upsert라 `coupang_rocket_purchase_order`는 «현재 단면»만
  갖는다. 그래서 「①확인 대기가 왜 줄고 ②발송 대기가 왜 늘었나」에 아무도 답하지 못했고,
  그 자리에서 **근거 없는 인과 주장**이 나왔다(2026-08-28: 「Jino가 확정했기 때문」).
  실측이 반증했다 — 오늘 발주 9건 중 8건이 12:34 수집에서 **처음 관측**됐고(10:14엔 없었다),
  「처음부터 PA」인지 「그 사이 확정」인지 우리 데이터는 원리적으로 구분 못 한다.

★이 표의 규율: **«우리가 본 것»만 적고 «실제로 일어난 것»을 주장하지 않는다.**
  모든 변화가 `prev_observed_at ~ observed_at` **구간**에 귀속되도록 두 컬럼을 둘 다 둔다 —
  한쪽만 두면 그게 곧 시점 단정이 된다(07-30 변경을 08-03으로 잡은 실사고, CoupangAdChangeLog 주석).
  `first_seen`은 전이가 아니라 **출현**이라 `prev_observed_at`이 NULL이다.

★유니크 키에 `field`가 들어가므로 **NULL 대신 `''`**를 쓴다 — SQLite에서 NULL은 서로 달라
  중복이 안 막힌다(`uq_coupang_ad_change_log`와 같은 이유). `first_seen` 행은 `field=''`.

★멱등: diff가 있을 때만 행을 만들므로 재수신이 저절로 멱등이고, 이 유니크 제약은 그 위의
  안전망이다(같은 회차 재실행 방어).

★시각은 KST naive(`kst_now()`). server_default를 두지 않는다 — SQLite `now()`는 UTC라 규약이
  갈린다. ⚠️같은 도메인의 `po_created_at`은 UTC 저장이다(규약 혼재는 실측된 사실이다).

배포 순서: `safe_deploy.sh ... --migrate --restart`가 ①마이그→②upgrade→③코드→④재시작을 강제한다.
  신규 표 추가라 구코드를 깨지 않는다(구코드는 이 표를 모른다).

Revision ID: pochg1obs9a
Revises: cnote1trim8a
Create Date: 2026-08-28 (KST)
"""
from alembic import op
import sqlalchemy as sa

revision = "pochg1obs9a"
down_revision = "cnote1trim8a"
branch_labels = None
depends_on = None

_TABLE = "coupang_rocket_po_change_log"


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
        # first_seen | field_change
        sa.Column("event", sa.String(length=16), nullable=False),
        # field_change일 때만 채운다. NULL 대신 ''(유니크 키에 들어간다).
        sa.Column("field", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("before_value", sa.Text(), nullable=True),
        sa.Column("after_value", sa.Text(), nullable=True),
        # ★구간의 양 끝 — 변화는 이 «사이»에 일어났다.
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("prev_observed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("purchase_order_seq", "event", "field", "observed_at",
                            name="uq_coupang_rocket_po_change_log"),
    )
    op.create_index(
        "ix_coupang_rocket_po_change_log_purchase_order_seq", _TABLE, ["purchase_order_seq"])
    op.create_index("ix_coupang_rocket_po_change_log_vendor_id", _TABLE, ["vendor_id"])
    op.create_index("ix_coupang_rocket_po_change_log_observed_at", _TABLE, ["observed_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        return
    # ⚠️ 이 표는 **소급 복원이 불가능하다**(원장에 직전 값이 없다). 지우면 그 구간의 관측은
    #    영구히 사라진다 — 실행 전 덤프를 뜰 것.
    op.drop_table(_TABLE)
