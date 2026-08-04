"""캠페인·광고그룹·키워드에 네이버 regTm(생성 시각 앵커) 추가 (D-NAO-148)

문제: D-NAO-146·147이 **수정** 시각(`editTm`)을 붙였지만 **신설**은 여전히 시각이 없다.
prod 실측(2026-08-04, 30일): `campaign_add` 1건 · `adgroup_add` 6건 · `external_keyword_added`
95건 = **102건 전부 occurred_at NULL**. 화면은 "언제 만들어졌는지 기록이 없습니다"라고 말한다.

발견: `/ncc/campaigns`·`/ncc/adgroups`·`/ncc/keywords` 응답에 **`regTm`이 이미 실려 온다**
(라이브 실측 2026-08-04: 캠페인 46/46 · 그룹 96/96 · 키워드 41/41 = 100%). 세 엔드포인트는
이미 매일 호출 중이므로 **추가 네이버 API 콜 0**이다. "응답에 있는 걸 버리고 있었다"가 네 번째다
(D-NAO-127 소재 editTm · D-NAO-137 APPLY_TM · D-NAO-146 캠페인/그룹 editTm · 이번).

★`editTm`과 결정적으로 다른 점: **regTm은 불변이다.** 실측 대비 — 어제 외부가 정지한 그룹
`grp-a001-01-000000060792990`은 `regTm=2026-01-20T07:28:07Z`(생성) · `editTm=2026-08-04T01:49:25Z`
(어제 10:49:25 KST 정지)로 완전히 갈린다. 그래서 editTm에 금지된 **소급 백필이 이 필드엔
성립한다** — LESSONS #119("마지막 수정만 남아 소급하면 판정이 썩는다")의 전제가 생성 시각엔
없기 때문이다. 백필은 별도 스크립트로 수행하며 값은 오늘 조회해도 그때와 같다.

교차 검증(실측 5건, UTC→KST):
  · cmp-a001-02-000000010907625 regTm 07-27 21:56:58 → 07-28 아침 스냅샷이 campaign_add로 발견
  · grp-a001-02-000000070992116 regTm 07-30 14:04:04 → 07-31 발견
  · grp-a001-02-000000070833127 regTm 07-27 21:58:06 → 07-28 발견
  · nkw-a001-01-000008420756866 regTm 07-22 13:41:30 → 07-23 07:37 sync가 발견
  · nkw-a001-01-000008420176074 regTm 07-22 11:49:36 → 07-23 07:37 sync가 발견
다섯 건 모두 `(직전 관측, 이번 관측]` 창 안이라 D-NAO-146의 창 규약을 그대로 재사용한다.

가산적이다: 둘 다 nullable, 기존 행은 NULL, 소비 지점(bm_diff·entity_sync)은 NULL이면
종전대로 occurred_at을 비운다.

Revision ID: a7c19e04d6b2
Revises: c5b8e3f74a12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7c19e04d6b2"
down_revision: Union[str, Sequence[str], None] = "c5b8e3f74a12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # naver_entity: entity_sync가 매일 관측하는 현재값(campaign/adgroup/**keyword** 전부).
    # 키워드까지 채우는 것이 editTm과 다르다 — external_keyword_added가 이 필드를 소비한다.
    with op.batch_alter_table("naver_entity") as b:
        b.add_column(sa.Column("reg_tm", sa.String(length=40), nullable=True))
    # naver_entity_snapshot: 날짜별 사본 — bm_diff가 신설 op의 occurred_at으로 승격.
    with op.batch_alter_table("naver_entity_snapshot") as b:
        b.add_column(sa.Column("reg_tm", sa.String(length=40), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("naver_entity_snapshot") as b:
        b.drop_column("reg_tm")
    with op.batch_alter_table("naver_entity") as b:
        b.drop_column("reg_tm")
