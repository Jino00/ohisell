"""S5: 파워링크(WEB_SITE) 텍스트 소재 원장 신설 — `get_ads`가 버리던 절반

계약 `docs/contracts/CONTRACT_ignition_readiness.md` §4-A S5 · 트랙
`docs/tracks/active/track_naver-ad-optimization.md` · 체인 `pao-논의` n=61 · D-NAO-263.

★**왜 지금까지 DB 0행이었나**(ref 103 §5): `naver_sa_ad_fetcher.get_ads()`가
`referenceData.mallProductId`가 없는 소재를 `continue`로 버린다 — 쇼핑 상품 매핑 전용으로
태어난 함수라 그 필터가 그 함수의 계약이다. 파워링크 소재는 `referenceData`가 **아예 None**이고
문안이 `ad{headline,description,pc,mobile}`에 실려 오므로 **한 건도 남지 않았다**.

★**소급이 원리적으로 불가능하다** — `/ncc/ads`는 현재값만 주고 변경 피드가 없다. 즉 수집
개통일이 곧 관측 창의 시작일이다(C10 상품메타·검색량 기준선 D-NAO-186과 같은 성질).
계약 §5: *"제목·태그는 콘솔에서 누가 만지는 순간 원복 좌표가 사라지므로 S5는 늦을수록 잃는다."*

★**순수 추가다** — 기존 테이블·컬럼을 하나도 건드리지 않는다. 신설 2표뿐이라 구코드가 새
스키마 위에서 그대로 돈다(계약 §3 「신설 컬럼은 전부 additive」의 표 판).

★**적재 전용 원장이다.** 문안 «쓰기»는 계약 §1 「안 하는 것」 6이 점화 후 별도 계약으로
미뤘다 — 이 표의 존재가 그 승인을 대신하지 않는다.

Revision ID: pltext1s5a
Revises: otaostk1s4a
Create Date: 2026-08-27 21:0x KST
"""
from alembic import op
import sqlalchemy as sa


revision = "pltext1s5a"
down_revision = "otaostk1s4a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "naver_ad_creative_text",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ad_id", sa.String(length=60), nullable=False),
        sa.Column("adgroup_id", sa.String(length=60), nullable=False),
        sa.Column("campaign_id", sa.String(length=60), nullable=False, server_default=""),
        sa.Column("campaign_type", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("ad_type", sa.String(length=40), nullable=False, server_default=""),
        # 문안·링크는 Text — 대체키워드 구문(`{keyword:...}`) 때문에 표시 자수보다 길 수 있다.
        sa.Column("headline", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("pc_final", sa.Text(), nullable=True),
        sa.Column("pc_display", sa.Text(), nullable=True),
        sa.Column("mobile_final", sa.Text(), nullable=True),
        sa.Column("mobile_display", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=True),
        sa.Column("inspect_status", sa.String(length=30), nullable=True),
        sa.Column("user_lock", sa.Boolean(), nullable=True),
        sa.Column("edit_tm", sa.String(length=40), nullable=True),
        sa.Column("raw_json", sa.Text(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_changed_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ad_id", name="uq_naver_ad_creative_text_ad"),
    )
    op.create_index("ix_naver_ad_creative_text_ad_id", "naver_ad_creative_text", ["ad_id"])
    op.create_index("ix_naver_ad_creative_text_adgroup_id", "naver_ad_creative_text", ["adgroup_id"])
    op.create_index("ix_naver_ad_creative_text_campaign_id", "naver_ad_creative_text", ["campaign_id"])
    op.create_index("ix_naver_ad_creative_text_last_seen_at", "naver_ad_creative_text", ["last_seen_at"])
    op.create_index(
        "ix_naver_ad_creative_text_ag", "naver_ad_creative_text", ["adgroup_id", "last_seen_at"]
    )

    op.create_table(
        "naver_ad_creative_text_change",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ad_id", sa.String(length=60), nullable=False),
        # 폴링 시각이지 «변경 시각»이 아니다(일 1회 grain이라 ±1일 불확실).
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("changed_fields", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_naver_ad_creative_text_change_ad_id", "naver_ad_creative_text_change", ["ad_id"]
    )
    op.create_index(
        "ix_naver_ad_creative_text_change_observed_at",
        "naver_ad_creative_text_change", ["observed_at"],
    )
    op.create_index(
        "ix_naver_ad_creative_text_change_ad_at",
        "naver_ad_creative_text_change", ["ad_id", "observed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_naver_ad_creative_text_change_ad_at", table_name="naver_ad_creative_text_change")
    op.drop_index("ix_naver_ad_creative_text_change_observed_at", table_name="naver_ad_creative_text_change")
    op.drop_index("ix_naver_ad_creative_text_change_ad_id", table_name="naver_ad_creative_text_change")
    op.drop_table("naver_ad_creative_text_change")
    op.drop_index("ix_naver_ad_creative_text_ag", table_name="naver_ad_creative_text")
    op.drop_index("ix_naver_ad_creative_text_last_seen_at", table_name="naver_ad_creative_text")
    op.drop_index("ix_naver_ad_creative_text_campaign_id", table_name="naver_ad_creative_text")
    op.drop_index("ix_naver_ad_creative_text_adgroup_id", table_name="naver_ad_creative_text")
    op.drop_index("ix_naver_ad_creative_text_ad_id", table_name="naver_ad_creative_text")
    op.drop_table("naver_ad_creative_text")
