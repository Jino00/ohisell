"""M3-b: naver_change_log에 목적함수 정합 채점 4컬럼 추가

계약 `docs/PLAN_naver-m3-wisdom-scorecard.md` §4-B ④(D-NAO-223) · §8-Q1·Q3 확정 ·
앵커 `.claude/anchors/c7105dae-d7c9-4200-870b-872693c92aa5.md`.

기존 `outcome`은 전/후 **RPC(수익/클릭) 배율**로 성패를 찍는데, 분모가 클릭이라
「클릭·매출이 함께 줄어도 매출이 덜 줄었으면 개선」이 된다(ref 90 §2 — improved 전건
4/4가 매출 감소). 트랙 목표(D-NAO-59)는 **총이익 절대액**이므로 새 자를 붙인다 — 판정은 «총이익 델타»
((cf보정매출/BEP) − 비용의 전/후 비교, D-NAO-225)이고, GAVE 점수는 «크기» 축으로
gave_before/gave_after에 함께 저장한다.

★Q1 확정에 따라 기존 `outcome`은 **불변**이다 — 새 식의 산출은 이 별도 컬럼들에만
기록해, 「교정 전 채점기가 무엇을 찍었나」를 영구 증거로 남긴다(교훈 #274 존중).
★Q3 확정에 따라 **신설 원장이 아니라 확장**이다 — `outcome` 소비처 전수(backend 14 ·
frontend 2)가 전부 기존 값에만 반응하고 새 컬럼을 읽는 소비처는 0이라, additive
스키마 변경으로 깨지는 곳이 없다(§8-Q3 확정 각주).

★additive만이다 — 기존 컬럼·행 무변경, 삭제·타입 변경 0. 전부 nullable이고
server_default를 두지 않는다: NULL(아직 새 식으로 안 잰 행)과 실제 판정을 구분해야
하고, Boolean에 정수 리터럴을 넣어 PostgreSQL에서 롤백된 M2-a 적대 리뷰 1R P1의
함정이 원리적으로 없다(String/Float만 쓴다).

Revision ID: m3bprofitscore
Revises: mrg48s1heads
Create Date: 2026-08-22 KST
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "m3bprofitscore"
down_revision: Union[str, None] = "mrg48s1heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("naver_change_log", sa.Column("outcome_profit", sa.String(length=12), nullable=True))
    op.add_column("naver_change_log", sa.Column("gave_before", sa.Float(), nullable=True))
    op.add_column("naver_change_log", sa.Column("gave_after", sa.Float(), nullable=True))
    op.add_column("naver_change_log", sa.Column("bep_source", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("naver_change_log", "bep_source")
    op.drop_column("naver_change_log", "gave_after")
    op.drop_column("naver_change_log", "gave_before")
    op.drop_column("naver_change_log", "outcome_profit")
