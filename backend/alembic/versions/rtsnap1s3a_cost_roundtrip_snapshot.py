"""왕복 스냅샷 테이블 — S3 「DB-생성 다운로드」의 대조 기준점

계약 `docs/contracts/CONTRACT_cost_excel_roundtrip.md` D-CPP-62 「원가 왕복 목표」 S3,
체인 `sellc-원가-메뉴` n=17. Jino 착수 확인 2026-08-28 13:59 KST (원문 *"그래"*).

무엇을 담나 — **내려보낸 그 순간의 표 한 장**
  다운로드가 만드는 파일과 «짝»이 되는 불변 증거물이다. S4(diff-확인 업로드)의 3-방향 대조가
  이 위에서 돈다: **스냅샷(내가 받았을 때 값) ↔ 파일에 적혀 온 값 ↔ 지금 현재값**.
  스냅샷이 없으면 「값 하나 고친 파일」과 「그 사이 화면에서 바뀐 값」을 구별할 수 없고,
  그 구별이 곧 계약 §4 S4의 「충돌」 묶음이다.

왜 행을 정규화하지 않고 JSON 한 칸에 담나 (둘 다 실제 위험이다)
  ① 열 스펙이 진화하면 정규화 스키마가 따라 바뀌어야 하고, 그 순간 **구 스냅샷이 마이그레이션
     부채**가 된다. blob은 구판을 그대로 품는다 — `column_spec`을 같이 담아 스냅샷을
     **자기서술적**으로 만들었으므로, 구 파일을 업로드해도 라벨→키 매핑이 그 파일의 스냅샷
     안에 있다.
  ② 정규화된 스냅샷 행 테이블은 생김새가 **단가 이력의 두 번째 사본**이다. 누군가 반드시 그걸
     조회하기 시작하고, 그 순간 정본 `cost_material_price`와 갈라진다 — 이 저장소가 반복해서
     밟은 병이다(D-CPP-60 · 직렬화기 두 벌 `standard_cost.py`↔`recipes.py`).
  대가: material별 스냅샷 횡단 조회가 안 된다. 그 질문의 정당한 답은 어차피 단가 원장이다.

`content_hash`가 하는 일
  직전 스냅샷과 내용이 같으면 새 행을 만들지 않고 **같은 스냅샷을 재발급**한다. 다운로드가
  사실상 멱등이 되고, 행 증가가 「상태가 실제로 바뀐 횟수」에 묶이며, 같은 상태를 두 번 받아
  둘 다 올려도 S4에서 가짜 충돌이 안 난다.

`created_at`에 `server_default=func.now()`를 **쓰지 않는** 이유
  이 저장소에서 `func.now()`는 **UTC**다([[sqlite-server-default-now-is-utc]]). 이 값은 파일
  `_meta` 시트에 「생성 시각 (KST)」로 그대로 찍히므로, 9시간 어긋나면 사람이 「어느 게
  최신인가」를 바로 그 자리에서 다시 묻게 된다 — 그 질문을 없애는 것이 이 계약이다.
  그래서 앱(`round_trip.build_snapshot`)이 `kst_now()`로 명시 세팅한다.

순수 추가 마이그레이션이다 — 기존 테이블·컬럼을 건드리지 않으므로 `--migrate` 정방향
(마이그 → 코드 → 재시작)으로 배포한다.
"""

from alembic import op
import sqlalchemy as sa


revision = "rtsnap1s3a"
down_revision = "cnote1trim8a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cost_roundtrip_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("column_spec", sa.Text(), nullable=False),
        sa.Column("rows", sa.Text(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
    )
    op.create_index(
        "ix_cost_roundtrip_snapshot_content_hash",
        "cost_roundtrip_snapshot",
        ["content_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_cost_roundtrip_snapshot_content_hash",
        table_name="cost_roundtrip_snapshot",
    )
    op.drop_table("cost_roundtrip_snapshot")
