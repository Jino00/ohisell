"""naver_search_term_exclusion.console_excluded_at — 콘솔이 알려준 «진짜 제외 시각» (D-NAO-177)

★왜 `excluded_at`을 덮지 않고 칸을 새로 두나 (이 마이그레이션의 존재 이유 전부)
D-NAO-176을 설계할 때의 전제는 「콘솔은 제외 시점을 안 알려준다」였다. 그 전제가 **틀렸다** —
2026-08-12 Jino가 콘솔 화면을 확인해 준 결과 「제외 검색어」 탭에는 `등록시각` 칸이 실제로
있다(「골프」 2026.08.11 22:26, 오래된 것은 2024.12.26). 그래서 편입분도 실제 시각을 알 수 있다.

그렇다고 그 값을 `excluded_at`에 넣으면 안 된다. 그 칸은 이미 **「장부가 이 행을 제외로
세운 시각」**이라는 뜻으로 여러 곳이 읽고 있고, 그중 둘은 과거 날짜가 들어오면 오작동한다:
  ① `search_term_execution.void_execution` — 짝인 일기를 찾는 시간 하한이 `excluded_at`이다.
     2024-12-26이 들어오면 매칭 창이 1년 반으로 벌어져 **무관한 옛 일기**를 붙잡는다.
  ② `exclusion_survival.summary` — 「제외된 지 대조 주기를 넘겼는데 여태 안 본 행」을 방치로
     센다. 편입 직후 43건이 전부 과거 날짜면 즉시 방치로 잡혀 **전역 배너가 거짓 빨강**이 된다
     (그 파일이 직접 「상시 빨강은 이 감시가 죽는 방식」이라고 적어 둔 상태다).

그래서 뜻이 다른 값은 칸을 나눈다:
  excluded_at          NOT NULL — 장부가 이 행을 제외로 세운 시각(편입분은 편입 시각). 뜻 불변.
  console_excluded_at  NULL 허용 — **콘솔이 보여준 실제 등록시각.** NULL = 콘솔이 안 알려줬거나
                       사람이 안 적었다 = **모른다.** 추정해서 채우지 않는다.

NULL이 「모른다」를 그대로 표현하는 것이 이 칸의 요점이다 — 이 리포가 반복해서 당한 결함이
「모르는 것을 아는 것으로 센다」이고(D-NAO-174·175·176 전건 같은 모양), 여기서 `excluded_at`에
편입 시각을 넣고 그걸 실제 시각처럼 쓰면 같은 실패를 네 번째로 반복하는 것이다.

nullable 컬럼 추가라 구코드를 깨지 않는다. 배포 순서는 safe_deploy.sh --migrate가 강제한다 —
①마이그 파일 ②원격 alembic upgrade ③코드 ④재시작.

Revision ID: cs1exat2when3
Revises: im1port2src3
Create Date: 2026-08-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "cs1exat2when3"
down_revision = "im1port2src3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "naver_search_term_exclusion",
        sa.Column("console_excluded_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("naver_search_term_exclusion", "console_excluded_at")
