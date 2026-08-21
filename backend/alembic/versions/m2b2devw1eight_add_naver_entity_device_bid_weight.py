"""M2-b2: naver_entity에 기기별 입찰가중치 2컬럼 추가

계약 `docs/PLAN_naver-m2-l2-wiring.md` §6 M2-b2 · D-NAO-218 · 앵커
`.claude/anchors/c6abb15b-b207-4e81-8b3a-22f3c7567f78.md`.

`/ncc/adgroups` 응답의 `pcNetworkBidWeight`·`mobileNetworkBidWeight`(공식 Swagger 확정:
range 10~500·기본 100 — 100 초과값도 유효, 이상치 아님)를 적재한다. entity sync가
**이미 매일 부르는** `/ncc/adgroups`의 응답에서 지금까지 버리던 2키를 살릴 뿐이라
추가 API 콜 0(계약 §5 「M2-b2는 콜을 늘리지 않는다」).

★additive만이다 — 기존 컬럼·행 무변경. nullable Integer라 PostgreSQL Boolean
server_default 함정(M2-a 적대 리뷰 1R P1)이 원리적으로 없다. server_default를 두지
않는다 — ingest가 매 upsert마다 명시적으로 채우고, NULL(미관측)과 100(진짜 가중치
없음)을 구분해야 하므로 DB 기본값으로 100을 지어내면 안 된다(계약 스펙 원문:
"NULL과 100을 구분해 로깅해라").

Revision ID: m2b2devw1eight
Revises: m2b1criterion2
Create Date: 2026-08-21 KST
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "m2b2devw1eight"
down_revision: Union[str, None] = "m2b1criterion2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("naver_entity", sa.Column("pc_bid_weight", sa.Integer(), nullable=True))
    op.add_column("naver_entity", sa.Column("mobile_bid_weight", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("naver_entity", "mobile_bid_weight")
    op.drop_column("naver_entity", "pc_bid_weight")
