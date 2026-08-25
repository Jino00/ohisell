"""add ops_wisdom_candidates global-grain cols + naver_proposals decision cols (D-NAO-248)

Revision ID: a7b8c9d0e1f2
Revises: f1a2b3c4d5e6
Create Date: 2026-08-25 09:05:00.000000

계약 D-NAO-248 §1·부록 Q2 처분 (b′) — 「전역 시그니처 «단일» grain + 캠페인별 분해를 후보
«안에» 병기」. 옛 시그니처(campaign_id|action|day_class|season|iphone_window)는 표본이
캠페인 수만큼 쪼개져, 4캠페인 합 91회 관찰된 패턴이 45/38/5/3으로 갈려 전부 rejected됐다
(승격된 유일한 지혜는 같은 액션의 71회짜리 하나뿐 — 근거가 더 두꺼운 쪽을 못 배웠다).

ops_wisdom_candidates에 4컬럼 추가(전부 nullable — 기존 27행은 grain=NULL로 남아
«레거시 캠페인 grain»을 뜻한다. 소급 재계산이 아니라 소급 **재수확**이다: 기존 90일 일기
위에 새 grain의 새 행만 생긴다):

  grain              String(12) — 'global'(신규 수확기가 붙이는 값). 기존 27행은 NULL.
  campaign_type      String(20) — 경계 축 ⓐ(부록 Q3: SHOPPING/WEB_SITE/BRAND_SEARCH는
                      «같은 액션 이름이 다른 레버»라 합치면 안 되는 경계).
  experiment_batch   String(60) — 경계 축 ⓑ(A/B·MOP 열·대조군·홀드아웃). 전역 풀 참여
                      후보는 NULL, 분리 버킷 후보만 실제 라벨 값.
  by_campaign_json   Text — 후보 1행 안의 캠페인별 good/bad 분해(이질성을 판사에게 병기).

naver_proposals에 3컬럼 추가(전부 nullable, 이번 스프린트는 쓰기 로직 없음 — 다음 단계
「승인=적용 사슬」이 쓸 자리만 미리 둔다):

  decided_at      DateTime
  decided_by      String(40)
  decision_note   Text

★둘 다 additive nullable — 기존 행 무영향, 기존 데이터 소급 변경 0.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('ops_wisdom_candidates', sa.Column('grain', sa.String(12), nullable=True))
    op.add_column('ops_wisdom_candidates', sa.Column('campaign_type', sa.String(20), nullable=True))
    op.add_column('ops_wisdom_candidates', sa.Column('experiment_batch', sa.String(60), nullable=True))
    op.add_column('ops_wisdom_candidates', sa.Column('by_campaign_json', sa.Text(), nullable=True))

    op.add_column('naver_proposals', sa.Column('decided_at', sa.DateTime(), nullable=True))
    op.add_column('naver_proposals', sa.Column('decided_by', sa.String(40), nullable=True))
    op.add_column('naver_proposals', sa.Column('decision_note', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('naver_proposals', 'decision_note')
    op.drop_column('naver_proposals', 'decided_by')
    op.drop_column('naver_proposals', 'decided_at')

    op.drop_column('ops_wisdom_candidates', 'by_campaign_json')
    op.drop_column('ops_wisdom_candidates', 'experiment_batch')
    op.drop_column('ops_wisdom_candidates', 'campaign_type')
    op.drop_column('ops_wisdom_candidates', 'grain')
