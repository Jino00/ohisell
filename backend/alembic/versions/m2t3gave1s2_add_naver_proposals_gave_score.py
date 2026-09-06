"""naver_proposals.gave_score 추가 — GAVE 사전 기대점수를 «컬럼»으로 (M2 T3 · D-NAO-297)

왜: `ref 65` §6 S2-ⓒ가 요구하는 것은 *"gave_score를 검색어·제안 성적표의 정렬 축으로 승격"*
이고, 계약 `CONTRACT_m2_l2_wiring.md` §4-C S2-③의 표면은 *"성적표 API 응답에 노출"*이다.

그런데 2026-09-07 07:29 KST prod 실측에서 `GET /api/naver/ad/proposals`의 응답 키 24개·정렬
파라미터 6개 어디에도 `gave_score`가 없었다. 값 자체는 살아 있다 — 같은 시각 `naver_proposals`
118건의 `rationale`에 `[GAVE사전: 기대점수 143327.5200·유형실적 51661.8(n=3656)]` 형태로
박혀 있다. 즉 **계산은 되는데 기계가 읽을 자리가 없다.**

★rationale을 파싱해서 정렬하지 않는 이유: 이 테이블의 다른 실행 목표값들(`target_bid`·
`target_budget`)이 주석에 *"실행자는 이 컬럼만 읽는다(rationale 텍스트 파싱 금지)"*를 명시적으로
달고 있다. 같은 규율을 정렬 축에도 적용한다 — 문자열 서식이 바뀌면 조용히 죽는 정렬이 된다.

⚠️**백필하지 않는다.** 기존 행의 이 컬럼은 전부 NULL로 남는다. 백필하려면 rationale 문자열을
역파싱해야 하는데 그게 위 금지선이고, 원 점수는 그 시점 보드·γ·유형실적에 의존해 지금 다시
계산하면 «그때 그 값»이 아니다(추정 금지). 값은 다음 제안 생성 사이클(`proposal_pipeline`)부터
채워진다 — 이 사실은 계약 판정문에 그대로 적는다.

값의 출처: `proposal_pipeline._apply_gave_priority`가 `gave_score.score_batch()`로 계산해
후보 dict의 `_gave_expected_score`에 실어 두는 값(Decimal, 소수 4자리 quantize).
`S = min{(ROAS/BEP)^γ, 1} × 매출` — `ref 65` §5-b ⑤ 카드 그대로이고 새 상수는 없다.

Numeric(18, 4)인 이유: 기존 quantize가 소수 4자리(`_Q4`)이고, 관측된 최대 기대점수가
143,327.5200(2026-09-05 23:00 제안 id 9401)이라 정수부 14자리면 충분히 여유가 있다.

Revision ID: m2t3gave1s2
Revises: anomw1s2a
Create Date: 2026-09-07 (KST)
"""

from alembic import op
import sqlalchemy as sa

revision = "m2t3gave1s2"
down_revision = "anomw1s2a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # additive nullable — 기존 행·기존 코드 무영향(구코드는 이 컬럼을 안 읽는다).
    op.add_column(
        "naver_proposals",
        sa.Column("gave_score", sa.Numeric(18, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("naver_proposals", "gave_score")
