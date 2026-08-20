"""add naver_product_meta_current + _change (D-NAO-212 · C10 상품 메타 · 북극성 M1 ④)

커머스 `POST /v1/products/search`를 일 1회 전건 폴링해 **현재 단면 upsert + 변경분 append**한다.

★이 축은 **소급이 원리적으로 불가능하다** — 상품 도메인 64 endpoint 전체에 변경-피드·변경
타임스탬프가 없다(75건 전건 개봉 실측 2026-08-19, 층화 가용 0건). 즉 **폴링 개통일 = 관측
창의 시작일**이고, 늦게 열수록 창이 영원히 짧다. 이게 이 마이그레이션이 지금 사는 이유다.

★전건 일일 스냅샷을 **쓰지 않는다**: prod 디스크 93%(여유 7.5G, 2026-08-21 00:00 실측)이고
D-NAO-198이 같은 모양을 실측으로 기각했다(98.2%가 퇴화 행).

★규모 실측(2026-08-21 00:0x, prod에서 실호출): `totalElements=1213`(원상품 단위 정황 —
size=8일 때 totalPages=152). 저장 grain은 **채널상품**이라 행수는 그 이상이 된다.

★신규 테이블만 추가한다 — 기존 테이블 스키마 변경 0건이라 구코드를 깨지 않는다(nullable 컬럼
추가 사고의 모양이 아니다). 그래도 safe_deploy.sh --migrate 순서는 지킨다.

Revision ID: c10meta1prod2
Revises: crit1age2gnd3int
Create Date: 2026-08-21 00:1x KST
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c10meta1prod2"
down_revision: Union[str, None] = "crit1age2gnd3int"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "naver_product_meta_current",
        sa.Column("id", sa.Integer(), nullable=False),
        # ★문자열이다 — 조인 상대편 naver_adgroup_product.mall_product_id ·
        #   naver_product_bep.channel_product_id가 둘 다 String(50)이다(PostgreSQL 이행 대비).
        sa.Column("channel_product_no", sa.String(length=50), nullable=False),
        sa.Column("origin_product_no", sa.String(length=50), nullable=True),
        sa.Column("group_product_no", sa.String(length=50), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("status_type", sa.String(length=30), nullable=True),
        sa.Column("display_status_type", sa.String(length=30), nullable=True),
        sa.Column("channel_service_type", sa.String(length=30), nullable=True),
        sa.Column("sale_price", sa.Integer(), nullable=True),
        sa.Column("discounted_price", sa.Integer(), nullable=True),
        sa.Column("mobile_discounted_price", sa.Integer(), nullable=True),
        sa.Column("stock_quantity", sa.Integer(), nullable=True),
        sa.Column("category_id", sa.String(length=30), nullable=True),
        sa.Column("whole_category_id", sa.String(length=200), nullable=True),
        sa.Column("whole_category_name", sa.String(length=300), nullable=True),
        sa.Column("brand_name", sa.String(length=150), nullable=True),
        sa.Column("manufacturer_name", sa.String(length=150), nullable=True),
        sa.Column("delivery_fee", sa.Integer(), nullable=True),
        sa.Column("return_fee", sa.Integer(), nullable=True),
        sa.Column("exchange_fee", sa.Integer(), nullable=True),
        sa.Column("delivery_attribute_type", sa.String(length=30), nullable=True),
        # ★리뷰 «수»가 아니라 판매자가 설정한 **적립 포인트 액수**다(29키 전수 실측 2026-08-21).
        #   accumulateSaleCount·reviewCount·averageReviewScore는 이 표면에 **없다** — 매치 0건.
        sa.Column("text_review_point", sa.Integer(), nullable=True),
        sa.Column("photo_video_review_point", sa.Integer(), nullable=True),
        sa.Column("regular_customer_point", sa.Integer(), nullable=True),
        sa.Column("manager_purchase_point", sa.Integer(), nullable=True),
        sa.Column("knowledge_shopping_registration", sa.Boolean(), nullable=True),
        sa.Column("seller_tags_json", sa.Text(), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        # 원문 문자열 그대로 — modifiedDate 갱신 의미론이 [미상]이라 파싱하면 가정이 섞인다.
        sa.Column("reg_date", sa.String(length=40), nullable=True),
        sa.Column("modified_date", sa.String(length=40), nullable=True),
        # ★응답 스키마가 항목마다 다르다(실측 8건 중 26키 3 · 29키 5) — 키 부재/null 구분의 정본.
        sa.Column("raw_json", sa.Text(), nullable=True),
        # ★모델은 `Mapped[datetime]`(nullable=False)이다 — 초판은 여기만 True라 스키마가 갈렸다
        #   (적대 리뷰 1R P2-7). ⚠️server_default는 SQLite에서 **UTC**인데 수집기가 넣는 값은
        #   KST다. 수집기가 항상 명시적으로 채우므로 이 기본값은 발화하지 않지만, 발화하면
        #   한 컬럼에 두 시간대가 섞인다(`scheduler_state`가 그렇게 됐다 — D-NAO-210 [미상] 해소).
        sa.Column("first_seen_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("last_changed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_product_no", name="uq_naver_product_meta_current"),
    )
    op.create_index("ix_naver_product_meta_current_channel_product_no",
                    "naver_product_meta_current", ["channel_product_no"])
    op.create_index("ix_naver_product_meta_current_origin_product_no",
                    "naver_product_meta_current", ["origin_product_no"])
    op.create_index("ix_naver_product_meta_current_status_type",
                    "naver_product_meta_current", ["status_type"])
    op.create_index("ix_naver_product_meta_current_last_seen_at",
                    "naver_product_meta_current", ["last_seen_at"])

    op.create_table(
        "naver_product_meta_change",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("channel_product_no", sa.String(length=50), nullable=False),
        # ★«폴링 시각»이지 «변경 시각»이 아니다 — 일 1회 grain이라 실제 변경은 ±1일 불확실.
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("changed_fields", sa.Text(), nullable=True),  # {필드: [old, new]}
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_naver_product_meta_change_channel_product_no",
                    "naver_product_meta_change", ["channel_product_no"])
    op.create_index("ix_naver_product_meta_change_observed_at",
                    "naver_product_meta_change", ["observed_at"])
    op.create_index("ix_naver_product_meta_change_cpn_at",
                    "naver_product_meta_change", ["channel_product_no", "observed_at"])


def downgrade() -> None:
    op.drop_index("ix_naver_product_meta_change_cpn_at", table_name="naver_product_meta_change")
    op.drop_index("ix_naver_product_meta_change_observed_at", table_name="naver_product_meta_change")
    op.drop_index("ix_naver_product_meta_change_channel_product_no", table_name="naver_product_meta_change")
    op.drop_table("naver_product_meta_change")
    op.drop_index("ix_naver_product_meta_current_last_seen_at", table_name="naver_product_meta_current")
    op.drop_index("ix_naver_product_meta_current_status_type", table_name="naver_product_meta_current")
    op.drop_index("ix_naver_product_meta_current_origin_product_no", table_name="naver_product_meta_current")
    op.drop_index("ix_naver_product_meta_current_channel_product_no", table_name="naver_product_meta_current")
    op.drop_table("naver_product_meta_current")
