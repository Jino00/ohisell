# campaign_target_resolver.py — campaign_target_resolver_sa (캠페인별 목표 ROAS 해석, P2-S1)
# 역할: 진단·시뮬레이션(S2/S3)이 쓸 target_roas를 우선순위로 해석하는 순수 함수.
#   우선순위(계획서 §P2-S1⑤): ① naver_campaign_settings.target_roas_override
#   ② (쇼핑) 상품BEP 연결  ③ 계정 기본값(BEP 매출가중).
# ⚠️ ②는 미구현 — 캠페인/그룹→상품(channel_product_id) 매핑 데이터가 아직 없음(네이버 쇼핑
#   광고는 그룹 단위로만 성과가 잡히고, 어느 그룹이 어느 상품을 노출하는지 연결하는 소스를
#   아직 확보하지 못함). 이름 유사도 등 추정 매칭은 금전 판단에 쓰기엔 근거가 약해 시도하지
#   않음(원칙: 추정 금지) — 확정 소스(예: /ncc/ads 소재-상품 연결 또는 ShoppingProduct
#   master-report) 확인 전까지 ①→③ 순으로 폴백. S2 착수 시 재검토.
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverCampaignSettings, NaverProductBep, Order

NAVER_CHANNEL_ID = 6


def _revenue_weighted_avg(db: Session, column) -> Decimal | None:
    """상품별 column(target_roas/bep_roas)을 최근 주문매출로 가중평균(매출가중).

    has_cost=True(원가 있어 산출됨) 상품만 대상. 매출 가중치는 naver_product_bep와
    같은 창(주문 테이블 직접 조회, 순환 임포트 회피)의 실거래 매출 합계.
    주문이 전혀 없으면 단순평균으로 폴백.
    """
    rows = db.query(NaverProductBep.channel_product_id, column).filter(
        NaverProductBep.channel_id == NAVER_CHANNEL_ID,
        NaverProductBep.has_cost.is_(True),
        column.isnot(None),
    ).all()
    if not rows:
        return None

    revenue_by_pid = dict(
        db.query(Order.platform_product_id, sqlfunc.sum(Order.selling_price))
        .filter(Order.channel_id == NAVER_CHANNEL_ID, Order.selling_price > 0)
        .group_by(Order.platform_product_id).all()
    )

    weighted_sum = Decimal("0")
    weight_total = Decimal("0")
    for pid, value in rows:
        rev = Decimal(str(revenue_by_pid.get(pid) or 0))
        weighted_sum += value * rev
        weight_total += rev

    if weight_total > 0:
        return weighted_sum / weight_total
    return sum((v for _, v in rows), Decimal("0")) / len(rows)  # 주문 없으면 단순평균


def account_default_target_roas(db: Session) -> Decimal | None:
    """계정 기본 목표 ROAS = 상품별 target_roas를 최근 주문매출로 가중평균(매출가중)."""
    return _revenue_weighted_avg(db, NaverProductBep.target_roas)


def account_default_bep_roas(db: Session) -> Decimal | None:
    """계정 기본 손익분기 ROAS = 상품별 bep_roas를 최근 주문매출로 가중평균(매출가중, P2-S2).

    진단 엔진(account_diagnosis)의 '출혈'(BEP 미달) 판정 기준 — target_roas(공격성 배수
    포함)와 달리 순수 손익분기선. 캠페인별 override 개념 없음(D-NAO-2 다이얼은 target에만
    적용) — 계정 전체 단일 기준.
    """
    return _revenue_weighted_avg(db, NaverProductBep.bep_roas)


def resolve_target_roas(db: Session, campaign_id: str) -> dict:
    """캠페인의 목표 ROAS를 우선순위대로 해석. 반환: {target_roas, source}.

    source: override(①) / account_default(③). ②(상품BEP 연결)는 위 모듈 docstring 참조 —
    데이터 소스 확보 전까지 미구현.
    """
    settings = db.query(NaverCampaignSettings).filter(
        NaverCampaignSettings.campaign_id == campaign_id
    ).first()
    if settings and settings.target_roas_override is not None:
        return {"target_roas": settings.target_roas_override, "source": "override"}

    default = account_default_target_roas(db)
    return {"target_roas": default, "source": "account_default" if default is not None else "unavailable"}
