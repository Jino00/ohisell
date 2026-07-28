"""merge — BP·VT3·promo-pnl 세 갈래를 단일 head로 합류 (DDL 없음)

세 마이그레이션이 같은 부모(f6a8c0b2d4e6)에서 동시에 갈라졌다:
  · bp1c2d3e4f5a — naver_campaign_settings.base_daily_budget (BP 예산 페이싱, D-NAO-102)
  · a1b3c5d7e9f2 — naver_ctr_alert_log (VT3 소재 CTR 경보 개편, D-NAO-103)
  · df7b34a2f46e — coupang_rocket_promotion.unit_discount_amount (D-CPP-7, 이미 main에 병합됨)
서로 다른 테이블이라 충돌은 없고, 순서 의존도 없다. 이 리비전은 **DDL을 하지 않고**
체인만 다시 하나로 모은다(단일 head 규칙 복원).

Revision ID: 96e8319324f5
Revises: a1b3c5d7e9f2, bp1c2d3e4f5a, df7b34a2f46e
"""
from typing import Sequence, Union

revision: str = '96e8319324f5'
down_revision: Union[str, Sequence[str], None] = ('a1b3c5d7e9f2', 'bp1c2d3e4f5a', 'df7b34a2f46e')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
