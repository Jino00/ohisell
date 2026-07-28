"""merge — Phase 2 promo-pnl(890f276375e3)과 main 형제 head(96e8319324f5)를 단일 head로 재결합 (DDL 없음)

Phase 2 promo-pnl 브랜치는 `890f276375e3`(target_sku_ids, D-CPP-7 이후 갈라짐)에서 독립적으로
작업하는 동안, main은 병행 세션의 BP(예산 페이싱)·VT3(CTR 경보) 두 갈래를 `df7b34a2f46e` 위에서
합쳐 `96e8319324f5`로 만들었다. 결과적으로 같은 부모(df7b34a2f46e)에서 갈라진 두 head가 남았다:
  · 890f276375e3 — coupang_rocket_promotion.target_sku_ids (Phase 2 손익 엔진, 이 트랙)
  · 96e8319324f5 — naver_campaign_settings.base_daily_budget + naver_ctr_alert_log (BP/VT3, 이미 main)
서로 다른 테이블·다른 트랙이라 충돌은 없고 순서 의존도 없다. 이 리비전은 **DDL을 하지 않고**
체인만 다시 하나로 모은다(단일 head 규칙 복원, 원칙12 "main 병합했나"만으로는 안 잡히는 형제
head 분기 — 이번엔 병합 전 fetch 단계에서 발견).

Revision ID: 4b66ae3706e6
Revises: 890f276375e3, 96e8319324f5
"""
from typing import Sequence, Union

revision: str = '4b66ae3706e6'
down_revision: Union[str, Sequence[str], None] = ('890f276375e3', '96e8319324f5')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
