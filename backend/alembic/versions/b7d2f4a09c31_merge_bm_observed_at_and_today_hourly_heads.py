"""merge — D-NAO-93 관측시각(d5f7b9c1e3a6)과 D-NAO-122 당일시간별(a1c4e7f20b91) 두 head 재결합 (DDL 없음)

PR #121(D-NAO-93)이 `c4e6a8b0d2f4`에서 갈라져 나온 뒤 2일간 열려 있는 동안, main은 그 위에서
여러 갈래를 거쳐 `4b66ae3706e6`(merge) → `a1c4e7f20b91`(D-NAO-122 신규 테이블)까지 전진했다.
결과적으로 같은 조상에서 갈라진 두 head가 남았다:
  · d5f7b9c1e3a6 — naver_entity_snapshot.entity_observed_at / p3_observed_at (nullable 컬럼 2개)
  · a1c4e7f20b91 — naver_adgroup_hourly_today (신규 테이블)

서로 다른 테이블만 건드리고 순서 의존도 없다(2026-07-29 실측 대조). 이 리비전은 **DDL을 하지
않고** 체인만 다시 하나로 모은다 — 단일 head 규칙 복원. 이 저장소가 형제 head를 합칠 때 써온
방식 그대로다(전례: `4b66ae3706e6_merge_promo_pnl_phase2_bp_vt3_heads.py`).

★배포 순서는 `scripts/safe_deploy.sh --migrate`가 강제한다(마이그 배포 → 원격 upgrade head →
코드 배포 → 재시작). 컬럼 추가는 additive·nullable이라 구코드와 하위호환된다.

Revision ID: b7d2f4a09c31
Revises: d5f7b9c1e3a6, a1c4e7f20b91
"""
from typing import Sequence, Union

revision: str = "b7d2f4a09c31"
down_revision: Union[str, Sequence[str], None] = ("d5f7b9c1e3a6", "a1c4e7f20b91")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
