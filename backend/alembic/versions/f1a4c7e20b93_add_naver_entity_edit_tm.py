"""캠페인·광고그룹에 네이버 editTm(발생 시각 앵커) 추가 (D-NAO-146)

문제: `naver_agency_op`의 campaign/adgroup 행은 `occurred_at`이 **100% NULL**이었다
(prod 실측 2026-08-04, 30일 90/90). 일별 스냅샷 diff라 "그날 어딘가"까지만 알 수 있었고,
하필 예산 4배 증액·캠페인 On/Off처럼 가장 무거운 변경이 이 grain에 있다.

발견: `/ncc/campaigns`·`/ncc/adgroups` 응답에 **`editTm`이 이미 실려 온다**
(라이브 실측: 캠페인 46/46 · 그룹 1,010/1,010). 두 엔드포인트는 이미 매일 호출 중이므로
**추가 네이버 API 콜 0**이다. 소재 grain의 `editTm`(D-NAO-127)·`APPLY_TM`(D-NAO-137)에 이어
"응답에 이미 있는 걸 버리고 있었다"가 세 번째다.

★소재와 결정적으로 다른 점: **피드 재적용 잡음이 없다.** 소재는 상품 피드 재적용으로 하루
229건이 전진해 신호 4 : 잡음 229였는데(D-NAO-136), 캠페인은 46건 중 최근 3일 전진이 1건,
그룹은 1,010건 중 2건이다(31개 캠페인은 아직 2026-03-30에 멈춰 있다). 그래서 이 grain엔
피드 판별자가 필요 없다. 교차 검증: 03 캠페인 editTm = 2026-08-03 14:32:37 KST로 트랙에
기록된 대행사 일예산 50,000→200,000 변경 시각과 초 단위 일치.

가산적이다: 둘 다 nullable이고 기존 행은 NULL로 남으며, bm_diff는 NULL이면 종전대로
occurred_at을 비운다. **backfill 하지 않는다** — editTm은 마지막 수정만 남기므로 과거 행에
현재 값을 소급하면 판정이 썩는다(LESSONS #119, D-NAO-139에서 실제로 하룻밤 새 26건이
뒤집힌 전례).

Revision ID: f1a4c7e20b93
Revises: b2f9d61ae403

★부모 재지정(2026-08-04 18:2x, Jino 승인): 원래 부모는 `d3f5b7a91c48`였는데, 병행 세션의
  `b2f9d61ae403`(orders 교환 컬럼)이 **같은 부모에서 갈라져 prod에 먼저 적용**되는 바람에
  헤드가 2개가 됐다(`alembic upgrade head`가 "Multiple head revisions"로 거부 —
  safe_deploy.sh가 코드 배포 **전에** 멈춰 세웠다). 이 리비전은 그 시점에 **어디에도 적용되지
  않은 상태**였으므로, 적용된 이력은 건드리지 않고 이쪽만 뒤로 물러선다. 두 리비전은 서로 다른
  테이블(orders / naver_entity·naver_entity_snapshot)을 만지므로 순서 의존이 없다.
  ★이번이 같은 사고의 세 번째다(d3f5b7a91c48·3130ee5 전례).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1a4c7e20b93"
down_revision: Union[str, Sequence[str], None] = "b2f9d61ae403"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # naver_entity: entity_sync가 매일 관측하는 현재값(campaign/adgroup 행만 채워진다).
    with op.batch_alter_table("naver_entity") as b:
        b.add_column(sa.Column("edit_tm", sa.String(length=40), nullable=True))
    # naver_entity_snapshot: 그 값의 날짜별 사본 — bm_diff가 여기서 읽어 occurred_at으로 승격.
    with op.batch_alter_table("naver_entity_snapshot") as b:
        b.add_column(sa.Column("edit_tm", sa.String(length=40), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("naver_entity_snapshot") as b:
        b.drop_column("edit_tm")
    with op.batch_alter_table("naver_entity") as b:
        b.drop_column("edit_tm")
