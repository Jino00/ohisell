"""병합 리비전 — `otao1po4n4a`(발주 원장) ∧ `ev1preserve51`(지혜 증거보전) 합류

## 왜 생겼나 (스키마 변경 없음 — 순수 합류)

병행 세션 둘이 **같은 부모 `cst4pick59a`에서 갈라졌다**:

    cst4pick59a ─┬─ otao1po4n4a   OTAO 발주 원장 3테이블   (체인 `발주예측` n=4·n=5 · PR #460)
                 └─ ev1preserve51 지혜 후보 재판정 컬럼    (체인 `pao-논의` n=54 · PR #461)

`ev1preserve51`이 2026-08-26 08:0x에 main에 먼저 들어왔고, 이 브랜치가 그것을 병합하면서
head가 둘이 됐다(`test_single_head_linear_chain` 실패로 관측). 저장소 관례는 **어느 한쪽을
재부모화하는 것이 아니라 병합 리비전으로 합류**시키는 것이다 — 그 테스트의 실패 메시지가
직접 그렇게 지시하고, `down_revision` 파서도 튜플 형태를 이미 지원한다.

★**재부모화(내 것의 `down_revision`을 `ev1preserve51`로 바꾸기)를 하지 않은 이유**: 그러면
두 마이그레이션 사이에 **없는 의존 관계**가 생긴다. 둘은 서로 다른 테이블을 만지고 순서
제약이 없다 — 있지도 않은 순서를 스키마 역사에 박아 두면 나중에 어느 한쪽만 되돌릴 수 없다.

★`upgrade`/`downgrade`가 비어 있는 것이 정상이다. 이 리비전은 **DDL을 하나도 실행하지 않고**
갈래만 잇는다.

Revision ID: otaoev1merge
Revises: otao1po4n4a, ev1preserve51
Create Date: 2026-08-26 KST
"""
from typing import Sequence, Union

revision: str = "otaoev1merge"
down_revision: Union[str, Sequence[str], None] = ("otao1po4n4a", "ev1preserve51")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """합류만 한다 — 스키마 변경 없음."""


def downgrade() -> None:
    """합류만 한다 — 스키마 변경 없음."""
