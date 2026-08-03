"""수정 주체 정정 저장소 — naver_change_actor_override (「수정 사항」 화면)

★왜 신규 테이블인가(원천 무오염): 주체는 두 원천(naver_change_log·naver_agency_op)에서
**데이터로 자동 판정**한다. 사람이 "이건 내가 한 것"이라고 다는 주석은 성격이 다른 정보다 —
원천 컬럼을 덮어쓰면 탐지 로직의 산출물과 사람의 주석이 구분 불가가 되고, 그 순간부터
탐지가 옳았는지 검증할 수 없다(회귀 판단 근거 소실). 그래서 정정은 **별도 테이블에 쌓고**
읽을 때 겹쳐 보인다. 원천 테이블은 이 기능으로 단 한 행도 바뀌지 않는다.

★(source, source_id) 복합 유일키: 두 원천에 **동일한 모양**으로 붙어야 한다(원천마다 다른
테이블을 만들면 세 번째 원천이 생길 때 또 만들게 된다). source_id는 각 원천 테이블의 PK다 —
FK를 걸지 않는 이유는 원천이 둘이라 단일 FK로 표현할 수 없고, 원천 행이 리플레이로
삭제-재생성되는 경로(bm_diff 멱등 재생성)가 있어 CASCADE가 정정을 조용히 지우기 때문이다.
고아 정정은 조회 시 자연스럽게 매칭 실패로 무시된다(무해).

Revision ID: d4f6b8c0e2a5
Revises: c3e5a7b9d1f4
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4f6b8c0e2a5"
down_revision: Union[str, Sequence[str], None] = "c3e5a7b9d1f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "naver_change_actor_override",
        sa.Column("id", sa.Integer(), primary_key=True),
        # 어느 원천의 어느 행인가
        sa.Column("source", sa.String(length=12), nullable=False),  # change_log / agency_op
        sa.Column("source_id", sa.Integer(), nullable=False),
        # 정정된 주체 — ours / agency / jino
        sa.Column("actor", sa.String(length=12), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("source", "source_id", name="uq_naver_change_actor_override"),
    )
    op.create_index(
        "ix_naver_change_actor_override_source",
        "naver_change_actor_override",
        ["source", "source_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_naver_change_actor_override_source", table_name="naver_change_actor_override"
    )
    op.drop_table("naver_change_actor_override")
