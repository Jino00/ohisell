"""M2-a: 계층 EB 풀링 산출 테이블 신설 + BRAND_SEARCH settings 2행 시드

ref 65 S1-ⓑ(풀링 배선)·S1-ⓓ(BRAND_SEARCH 등록) · 계약 docs/PLAN_naver-m2-l2-wiring.md · D-NAO-214.

★두 변경 다 **additive**다 — 기존 컬럼·행을 건드리지 않는다.
  ①`naver_pooled_estimate_daily` 신설(기존 `naver_forecast_daily` 확장이 불가한 근거는 models.py
    docstring 참조 — forecast_scorer가 grain 무관하게 백필해 진짜 예측 모델을 강등시킨다).
  ②`naver_campaign_settings`에 BRAND_SEARCH 캠페인 2행을 `optimizer='none'`으로 시드.
    ★`optimizer='none'`은 「수동, 기본」이라 **어떤 자동 경로도 열리지 않는다**(models.py:
    *"제안·실행은 optimizer='ours'만"*). 등록의 목적은 이 2캠페인이 진단·리포트 대상으로
    **보이게** 하는 것이지 운영을 넘기는 것이 아니다(계약 §3: PAO 자동 쓰기 0건).
    멱등이다 — campaign_id UNIQUE라 이미 있으면 INSERT를 건너뛴다(재실행 안전).

Revision ID: m2a1pool2eb3
Revises: c10meta1prod2
"""
from alembic import op
import sqlalchemy as sa


revision = "m2a1pool2eb3"
down_revision = "c10meta1prod2"
branch_labels = None
depends_on = None


# ref 65 S1-ⓓ 대상 — prod `naver_ad_daily`에서 campaign_type='BRAND_SEARCH'로 실측한 2건
# (2026-08-21). 하드코딩인 이유: 시드는 «그 시점에 실재한 2개»를 박는 일회성 데이터이고,
# 조회로 만들면 마이그레이션이 실행 환경의 원장 상태에 따라 달라져 재현이 안 된다.
_BRAND_SEARCH_CAMPAIGNS = (
    "cmp-a001-04-000000005294498",
    "cmp-a001-04-000000009198275",
)


def upgrade() -> None:
    op.create_table(
        "naver_pooled_estimate_daily",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("grain", sa.String(length=16), nullable=False),
        sa.Column("scope_key", sa.String(length=60), nullable=False),
        sa.Column("campaign_id", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("adgroup_id", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("window_from", sa.Date(), nullable=False),
        sa.Column("window_to", sa.Date(), nullable=False),
        sa.Column("n_imp", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_clk", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_conv_cnt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_conv_amt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("raw_ctr", sa.Numeric(precision=12, scale=6), nullable=False, server_default="0"),
        sa.Column("raw_cvr", sa.Numeric(precision=12, scale=6), nullable=False, server_default="0"),
        sa.Column("raw_rpc", sa.Numeric(precision=14, scale=4), nullable=False, server_default="0"),
        sa.Column("prior_ctr", sa.Numeric(precision=12, scale=6), nullable=False, server_default="0"),
        sa.Column("prior_cvr", sa.Numeric(precision=12, scale=6), nullable=False, server_default="0"),
        sa.Column("prior_rpc", sa.Numeric(precision=14, scale=4), nullable=False, server_default="0"),
        sa.Column("pooled_ctr", sa.Numeric(precision=12, scale=6), nullable=False, server_default="0"),
        sa.Column("pooled_cvr", sa.Numeric(precision=12, scale=6), nullable=False, server_default="0"),
        sa.Column("pooled_rpc", sa.Numeric(precision=14, scale=4), nullable=False, server_default="0"),
        sa.Column("shrink_k", sa.Numeric(precision=8, scale=2), nullable=False, server_default="10"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("target_date", "grain", "scope_key", name="uq_naver_pooled_estimate_daily"),
    )
    op.create_index(
        op.f("ix_naver_pooled_estimate_daily_target_date"),
        "naver_pooled_estimate_daily", ["target_date"], unique=False,
    )
    op.create_index(
        op.f("ix_naver_pooled_estimate_daily_scope_key"),
        "naver_pooled_estimate_daily", ["scope_key"], unique=False,
    )

    # S1-ⓓ — 멱등 시드. optimizer='none'(수동 기본) · auto_operate=0(자동 운영 대상 아님).
    conn = op.get_bind()
    for campaign_id in _BRAND_SEARCH_CAMPAIGNS:
        conn.execute(
            sa.text(
                "INSERT INTO naver_campaign_settings (campaign_id, optimizer, auto_operate, memo) "
                "SELECT :cid, 'none', :auto_operate, :memo "
                "WHERE NOT EXISTS (SELECT 1 FROM naver_campaign_settings WHERE campaign_id = :cid)"
            ),
            # ★`0`이 아니라 **바인딩된 파이썬 bool**이다(적대 리뷰 1R P1). auto_operate는 Boolean
            # 컬럼이라 PostgreSQL은 정수 리터럴을 거부한다("열은 boolean인데 표현식은 integer") —
            # 그리고 PG에선 마이그레이션이 트랜잭션으로 감싸이므로 이 INSERT 하나가 실패하면
            # 같은 파일의 create_table까지 통째로 롤백돼 **테이블 신설 자체가 무산**된다.
            # SQLite는 타입을 강제하지 않아 로컬·CI에서 전혀 안 보이고 PG 컷오버에서 처음 터진다.
            # 저장소 관례도 이미 bool이다(n4o5p6q7r8s9 마이그의 `sa.false()`) — 이 파일만의 이탈이었다.
            {"cid": campaign_id, "auto_operate": False,
             "memo": "BRAND_SEARCH 등록 (ref 65 S1-ⓓ · D-NAO-214 M2-a)"},
        )


def downgrade() -> None:
    conn = op.get_bind()
    # 시드한 2행만 되돌린다 — 사람이 나중에 optimizer를 바꿨으면 그건 남긴다(남의 결정을 지우지 않는다).
    for campaign_id in _BRAND_SEARCH_CAMPAIGNS:
        conn.execute(
            sa.text(
                "DELETE FROM naver_campaign_settings "
                "WHERE campaign_id = :cid AND optimizer = 'none' AND auto_operate = :auto_operate"
            ),
            # upgrade와 같은 이유로 정수 리터럴 금지 — PG에선 `boolean = integer` 연산자가 없다.
            {"cid": campaign_id, "auto_operate": False},
        )
    op.drop_index(op.f("ix_naver_pooled_estimate_daily_scope_key"), table_name="naver_pooled_estimate_daily")
    op.drop_index(op.f("ix_naver_pooled_estimate_daily_target_date"), table_name="naver_pooled_estimate_daily")
    op.drop_table("naver_pooled_estimate_daily")
