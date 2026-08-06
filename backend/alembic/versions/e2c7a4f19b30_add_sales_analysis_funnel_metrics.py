"""판매분석 퍼널 지표 + 옵션 속성 (D-CPP-11)

판매분석 응답(`vi-detail-search`)에서 **이미 받고 있으면서 버리던** 값을 담는다. 추가 API 호출 0.

축을 두 테이블로 가른 근거(실측 2026-08-06):
  · `coupang_rocket_sales_daily` ← **날짜별 값**. 같은 옵션의 08-04 metrics와 06-05 metrics가
    서로 다르다(totalUnitsSold 0 vs 1). 조회·주문이 여기 온다.
  · `coupang_rocket_option_sku`  ← **현재값 스냅샷**. 08-04와 06-05를 공통 옵션 22개로 대조했더니
    isItemWinner·isOOS·ratingCount·ratingReview·brandName·categoryPath가 **전부 동일**했다.
    날짜별 값이 아니므로 일별 테이블에 넣으면 6월 행에 오늘의 품절 상태가 찍혀 가짜 시계열이 된다.

⚠️담지 않은 것: totalAddToCart · searchVolume · srpClick · srpClickShare — 3개 날짜(08-04·07-15·
  06-05) 전 옵션에서 값이 0이었다(프리미엄 데이터 2.0 등급 지표로 보인다). 0을 담으면 "관측 없음"과
  "정말 0"이 구분되지 않는다. 등급 전환 후 재확인하고 추가할 것.

전부 nullable + 기존 컬럼 불변 → 구코드를 깨지 않는다(safe_deploy `--migrate` 순서 그대로).

Revision ID: e2c7a4f19b30
Revises: b3d5f7a91c48
Create Date: 2026-08-06
"""
from alembic import op
import sqlalchemy as sa

revision = "e2c7a4f19b30"
down_revision = "b3d5f7a91c48"
branch_labels = None
depends_on = None


# (테이블, 컬럼, 타입) — upgrade/downgrade가 같은 목록을 보게 해 한쪽만 빠지는 일을 막는다.
_DAILY = "coupang_rocket_sales_daily"
_OPTION = "coupang_rocket_option_sku"
_COLUMNS = [
    (_DAILY, "page_views", sa.Integer()),
    (_DAILY, "orders", sa.Integer()),
    (_OPTION, "brand_name", sa.String(length=100)),
    (_OPTION, "category_path", sa.String(length=300)),
    (_OPTION, "is_item_winner", sa.Boolean()),
    (_OPTION, "is_oos", sa.Boolean()),
    (_OPTION, "rating_count", sa.Integer()),
    (_OPTION, "rating_score", sa.Numeric(3, 2)),
    (_OPTION, "attrs_observed_at", sa.DateTime()),
]


def _existing(table: str) -> set[str]:
    """이미 있는 컬럼 이름 — 병행 세션이 먼저 올렸을 때 중복 추가로 죽지 않게."""
    insp = sa.inspect(op.get_bind())
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    have = {t: _existing(t) for t in (_DAILY, _OPTION)}
    for table, name, type_ in _COLUMNS:
        if name in have[table]:
            continue
        op.add_column(table, sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    have = {t: _existing(t) for t in (_DAILY, _OPTION)}
    for table, name, _type in reversed(_COLUMNS):
        if name not in have[table]:
            continue
        op.drop_column(table, name)
