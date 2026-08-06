"""로켓1P 발주 실입고 시각 (receivingStartedAt / receivingFinishedAt)

원천 `/po-web/app/purchase-order/list` 응답에 **계속 오고 있었는데 파서가 버리던** 값이다.
추가 API 호출 0 — 페처는 raw 페이지를 그대로 push하므로 백엔드만 고치면 과거 90일치도
다음 수집에서 자동으로 채워진다.

왜 필요한가 (실측 2026-08-06):
  · `expected_delivery_date`는 **예정**이라 실제와 어긋난다. 납품예정 08-04인 PO들의 실입고
    완료가 08-04 05:17 / 08-05 07:05 / 08-05 17:24로 갈렸다.
  · 계산서는 **"같은 날 입고된 것"을 묶어** 며칠 뒤 발행된다(480건 실측: 계산서 1건이 평균
    5.8개 PO를 묶고, 79%는 납품예정일이 하나, 작성일−납품예정일 = −4~+7일).
    그래서 계산서 축 일별 매출의 정본은 작성일도 예정일도 아닌 **실입고일**이다.
  · 미수금의 연령(aging)도 이 값 없이는 못 낸다 — "며칠 된 미수인가"를 셀 기준이 없었다.

⚠️입고 전 PO는 원천이 null을 준다 → 그대로 NULL로 둔다. 0이나 예정일로 접지 않는다
  (원칙22: 미수집 ≠ 없음). 실측상 「거래명세서확인」 상태 81건은 전건 값이 있었다.

전부 nullable + 기존 컬럼 불변 → 구코드를 깨지 않는다(safe_deploy `--migrate` 순서 그대로).

Revision ID: f4a9c2e70b58
Revises: e2c7a4f19b30
Create Date: 2026-08-06
"""
from alembic import op
import sqlalchemy as sa

revision = "f4a9c2e70b58"
down_revision = "e2c7a4f19b30"
branch_labels = None
depends_on = None

_TABLE = "coupang_rocket_purchase_order"
# (컬럼, 타입, 인덱스명 or None) — upgrade/downgrade가 같은 목록을 보게 해 한쪽만 빠지는 걸 막는다.
_COLUMNS = [
    ("receiving_started_at", sa.DateTime(), None),
    ("receiving_finished_at", sa.DateTime(), "ix_coupang_rocket_po_receiving_finished_at"),
]


def _existing() -> set[str]:
    """이미 있는 컬럼 — 병행 세션이 먼저 올렸을 때 중복 추가로 죽지 않게."""
    insp = sa.inspect(op.get_bind())
    if _TABLE not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(_TABLE)}


def _existing_indexes() -> set[str]:
    insp = sa.inspect(op.get_bind())
    if _TABLE not in insp.get_table_names():
        return set()
    return {i["name"] for i in insp.get_indexes(_TABLE)}


def upgrade() -> None:
    have = _existing()
    for name, type_, _idx in _COLUMNS:
        if name in have:
            continue
        op.add_column(_TABLE, sa.Column(name, type_, nullable=True))
    have_idx = _existing_indexes()
    for name, _type, idx in _COLUMNS:
        if idx and idx not in have_idx:
            op.create_index(idx, _TABLE, [name])


def downgrade() -> None:
    have_idx = _existing_indexes()
    for _name, _type, idx in _COLUMNS:
        if idx and idx in have_idx:
            op.drop_index(idx, table_name=_TABLE)
    have = _existing()
    for name, _type, _idx in reversed(_COLUMNS):
        if name not in have:
            continue
        op.drop_column(_TABLE, name)
