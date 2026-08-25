"""add ops_wisdom_candidates rejudge/evidence-preservation cols (D-NAO-251)

Revision ID: ev1preserve51
Revises: cst4pick59a
Create Date: 2026-08-26 07:00:00.000000

계약 D-NAO-251 §4-① — 「판정이 증거 수집을 «영구히» 끝낸다」의 수리.

구판 `harvest_candidates`는 promoted/rejected를 똑같이 terminal로 보고 **tally 갱신까지**
막았다(`wisdom_candidates.py` `_TERMINAL_STATUSES`). 그래서 판사가 *"45회 관찰이 단 이틀
안에 집중되어… 승격을 보류합니다"* 로 기각하면 그 시그니처는 **영원히 표본이 부족한 채로**
남았다 — 실측: 그 기각 뒤 일주일에 같은 조건으로 818건이 더 쌓였는데 판사는 다시 못 봤다.
「표본 부족」 기각이 표본을 영원히 부족하게 만드는 **자기충족 함정**이다.

ops_wisdom_candidates에 4컬럼 추가(전부 nullable 또는 서버 기본값 — additive):

  judged_at             DateTime — 마지막 판정 시각(KST 명시)
  judged_occurrences    Integer  — 판정 시점의 occurrences. **재개방 기준선**
  rejudge_count         Integer  — 재심 횟수(기본 0, 상한은 코드 상수 _MAX_REJUDGE)
  prior_judgments_json  Text     — 이전 판정문 이력(append-only JSON 배열)

★judge_verdict_json을 덮어쓰지 않고 이력 컬럼을 따로 두는 이유: 그 컬럼의 «형태»에
  `wisdom_writer.py:51`(principle/rationale)과 `wisdom_apply.py:72`(param_suggestion)가
  의존한다. 모양을 바꾸면 소비층이 조용히 깨진다(계약 §3 「판정문 삭제·덮어쓰기 금지」).

## 데이터 백필 2건 (관측값 기록이지 발명이 아니다)

1. **judged_occurrences 기준선** — 이미 판정된 행(promoted/rejected)에 `judged_occurrences :=
   현재 occurrences`를 넣는다. 이 값이 없으면 재개방 조건(2배)이 «어디서부터 2배인지» 모른다.
   지금 값을 기준선으로 삼는 것이 유일하게 정직한 선택이다 — 과거의 판정 시점 occurrences는
   기록이 없어 복원 불가이고, 지어내면 그게 발명이다. ⇒ **기존 rejected는 «지금부터» 2배가
   쌓여야 재개방된다**(소급 재개방 아님). 이 한계는 계약 §0 「효과 상한」과 같은 결이다.

2. **action 미상 후보 hidden 처분**(계약 §4-② ⓒ) — `action IS NULL OR action = ''` 인 후보를
   hidden으로 내린다. 이 후보들은 `_sibling_buckets`가 대조군을 원리적으로 못 만들고
   (액션이 패턴의 의미 축 그 자체다), 같은 계약의 수확층 수정이 앞으로 이런 행을 아예 안
   만든다. **삭제하지 않는다** — 행과 그 관찰 이력은 남는다.

★downgrade는 컬럼만 드롭한다. hidden 처분은 «원래 status가 무엇이었는지» 기록이 없어
  되돌릴 수 없다 — 그래서 upgrade가 사유를 observation 끝에 한 줄로 남긴다(감사 흔적).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'ev1preserve51'
down_revision: Union[str, None] = 'cst4pick59a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_HIDDEN_NOTE = " [D-NAO-251: action 미상이라 대조군 형성 불가 — hidden 처분]"


def upgrade() -> None:
    op.add_column('ops_wisdom_candidates', sa.Column('judged_at', sa.DateTime(), nullable=True))
    op.add_column('ops_wisdom_candidates', sa.Column('judged_occurrences', sa.Integer(), nullable=True))
    op.add_column(
        'ops_wisdom_candidates',
        sa.Column('rejudge_count', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column('ops_wisdom_candidates', sa.Column('prior_judgments_json', sa.Text(), nullable=True))

    conn = op.get_bind()
    # 백필 1 — 이미 판정된 행의 재개방 기준선(현재 관측값 기록).
    conn.execute(sa.text(
        "UPDATE ops_wisdom_candidates SET judged_occurrences = occurrences "
        "WHERE status IN ('promoted', 'rejected') AND judged_occurrences IS NULL"
    ))
    # 백필 2 — action 미상 후보 hidden 처분 + 사유 흔적(삭제 아님).
    # ★promoted는 제외한다(적대 리뷰 P2). 승격된 지혜의 근원 후보를 hidden으로 내리면
    #   `wisdom_writer`가 1:1로 묶어 둔 지혜의 출처가 조용히 «망각분»으로 재분류된다 —
    #   이 계약은 promoted를 어디서도 건드리지 않기로 했다(§4-① 「promoted는 완전 terminal」).
    #   2026-08-26 prod 실측으로는 action 미상 6건 중 promoted 0건이라 지금은 무해하지만,
    #   미래 행 하나가 조용히 강등되는 경로를 열어 둘 이유가 없다.
    # ★멱등: status <> 'hidden' 조건이 재실행 시 재진입을 막으므로 사유 문구가 중복 append
    #   되지 않는다(이 마이그가 두 번 도는 경우는 downgrade→upgrade 왕복뿐이고, 그때는
    #   status가 이미 hidden이라 건너뛴다).
    conn.execute(sa.text(
        "UPDATE ops_wisdom_candidates "
        "SET status = 'hidden', observation = COALESCE(observation, '') || :note "
        "WHERE (action IS NULL OR action = '') AND status NOT IN ('hidden', 'promoted')"
    ), {"note": _HIDDEN_NOTE})


def downgrade() -> None:
    op.drop_column('ops_wisdom_candidates', 'prior_judgments_json')
    op.drop_column('ops_wisdom_candidates', 'rejudge_count')
    op.drop_column('ops_wisdom_candidates', 'judged_occurrences')
    op.drop_column('ops_wisdom_candidates', 'judged_at')
