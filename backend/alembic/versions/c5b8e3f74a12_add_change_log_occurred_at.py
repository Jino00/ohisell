"""변경 이력에도 발생 시각 — naver_change_log.occurred_at (D-NAO-147)

D-NAO-146은 **스냅샷 diff 경로**(naver_agency_op)에만 시각을 붙였다. 그런데 외부 변경은
경로가 둘이다 — entity_sync가 매일 07:35 관측에서 남기는 `naver_change_log.external_*`가
나머지 절반이고, 그쪽은 여전히 `changed_at = 우리가 알아챈 시각`이었다.

실증(2026-08-04): 10:49:25/10:49:28에 꺼진 광고그룹 2개가 change_log에 **18:33:51**로 남아,
화면이 "감지 시각 — 실제로 언제 손댔는지는 기록이 없습니다"라고 말했다. 같은 순간
`naver_entity.edit_tm`은 10:49를 갖고 있었다.

★`changed_at`을 고치지 않고 새 컬럼을 두는 이유: `changed_at`은 쿨다운·echo 대조창·D+7/14
학습 루프가 **"우리가 언제 썼나"**로 소비하는 축이다. 거기에 외부 발생 시각을 섞으면 그
소비자들이 조용히 틀린다(2026-07-10 `changed_at` UTC 혼입으로 쿨다운이 무력화된 전례).
두 시각은 의미가 다르므로 칸도 다르다.

키워드도 커버된다: `/ncc/keywords` 응답에도 `editTm`이 온다(라이브 실측 41/41 = 100%) —
30일 기준 외부 입찰 변경 342건이 여기 속한다. 추가 GET 0.

가산적이다: nullable이고 기존 행은 NULL로 남으며, 화면은 NULL이면 종전대로 `changed_at`을
쓰고 `time_basis='detected'`로 표시한다. **backfill 하지 않는다**(editTm은 마지막 수정만
남아 소급하면 판정이 썩는다, LESSONS #119).

Revision ID: c5b8e3f74a12
Revises: f1a4c7e20b93
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c5b8e3f74a12"
down_revision: Union[str, Sequence[str], None] = "f1a4c7e20b93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("naver_change_log") as b:
        b.add_column(sa.Column("occurred_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("naver_change_log") as b:
        b.drop_column("occurred_at")
