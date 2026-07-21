# campaign_target_resolver.py — campaign_target_resolver_sa (캠페인별 목표 ROAS 해석, P2-S1)
# 역할: 진단·시뮬레이션(S2/S3)이 쓸 target_roas를 우선순위로 해석하는 순수 함수.
#   우선순위(계획서 §P2-S1⑤): ① naver_campaign_settings.target_roas_override
#   ② (쇼핑) 상품BEP 연결  ③ 계정 기본값(BEP 매출가중).
# ✅ ②는 D-NAO-57 A에서 구현됨 — /ncc/ads referenceData.mallProductId(라이브 실증)를
#   shopping_ad_product_sync가 naver_adgroup_product에 적재하고, 여기서 그 매핑의 상품 BEP
#   target_roas를 쓴다(여러 소재면 최근 주문매출 가중, 매출 없으면 단순평균). 매핑/BEP가 없으면
#   기존 ③(계정 기본값)으로 폴백. 이름 유사도 등 추정 매칭은 여전히 금지(원칙: 추정 금지) —
#   ②는 확정 소스(mall_product_id == channel_product_id)로만 성립한다.
# ★우선순위 ①(override)은 절대 불변 — 항상 최우선(D-NAO-57 A 명시).
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdgroupProduct, NaverCampaignSettings, NaverProductBep, Order

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


def _weighted_target_for_cpids(
    db: Session, cpids: list[str], column
) -> Decimal | None:
    """주어진 상품(channel_product_id) 집합의 column(target/bep_roas)을 최근 주문매출로 가중평균.

    D-NAO-57 A: 우선순위 ②(상품 파생 target)의 코어. has_cost=True(원가 있어 산출됨) 상품만
    대상 — 원가 미확인 상품은 임의 추정 없이 이 계산에서 자연히 빠진다(빠진 결과로 대상 상품이
    0개면 None 반환 → 호출부가 ③ 폴백). 여러 상품이면 실거래 매출 가중, 매출이 전혀 없으면
    단순평균(_revenue_weighted_avg와 동일 관례).
    """
    if not cpids:
        return None
    uniq = list(dict.fromkeys(cpids))  # 중복 제거(순서 보존)
    rows = db.query(NaverProductBep.channel_product_id, column).filter(
        NaverProductBep.channel_id == NAVER_CHANNEL_ID,
        NaverProductBep.has_cost.is_(True),
        NaverProductBep.channel_product_id.in_(uniq),
        column.isnot(None),
    ).all()
    if not rows:
        return None

    revenue_by_pid = dict(
        db.query(Order.platform_product_id, sqlfunc.sum(Order.selling_price))
        .filter(
            Order.channel_id == NAVER_CHANNEL_ID,
            Order.selling_price > 0,
            Order.platform_product_id.in_([pid for pid, _ in rows]),
        )
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


def _cpids_for_adgroup(db: Session, adgroup_id: str) -> list[str]:
    """광고그룹에 매핑된 상품(mall_product_id=channel_product_id) 목록(naver_adgroup_product)."""
    return [
        r[0] for r in db.query(NaverAdgroupProduct.mall_product_id)
        .filter(NaverAdgroupProduct.adgroup_id == adgroup_id).all()
    ]


def _cpids_for_campaign(db: Session, campaign_id: str) -> list[str]:
    """캠페인의 그룹들에 매핑된 상품 전체 목록(campaign grain 해석용, 그룹들 가중)."""
    return [
        r[0] for r in db.query(NaverAdgroupProduct.mall_product_id)
        .filter(NaverAdgroupProduct.campaign_id == campaign_id).all()
    ]


def weighted_product_value_for_adgroup(db: Session, adgroup_id: str, column) -> Decimal | None:
    """광고그룹 매핑 상품의 column(selling_price/contribution_margin/bep_roas 등)을
    최근 주문매출로 가중평균(D-NAO-60 RL2, 원칙18-6 정직 경계 단일화).

    has_cost=True(원가 확인된) 상품만 대상 — intraday_roas.adgroup_unit_price가 판매가·
    공헌이익·bep_roas를 같은 모집단(원가 기반 BEP와 정합하는 상품)에서 뽑아 쓰기 위한
    공개 헬퍼. 내부 `_weighted_target_for_cpids`(사적 함수)를 다른 모듈이 직접 크로스임포트
    하지 않도록 이 함수가 공식 진입점 역할을 한다. 매핑/대상 상품이 0개면 None.
    """
    return _weighted_target_for_cpids(db, _cpids_for_adgroup(db, adgroup_id), column)


def weighted_product_value_for_campaign(db: Session, campaign_id: str, column) -> Decimal | None:
    """캠페인의 그룹들에 매핑된 상품의 column(bep_roas 등)을 최근 주문매출로 가중평균
    (B-X BX2 — exploration_ceiling의 캠페인-grain BEP 폴백용, weighted_product_value_for_adgroup의
    캠페인 대칭). has_cost=True(원가 확인) 상품만 대상, 매핑/대상 0개면 None. 사적 함수
    `_weighted_target_for_cpids`를 다른 모듈이 직접 크로스임포트하지 않도록 이 공개 진입점을 둔다."""
    return _weighted_target_for_cpids(db, _cpids_for_campaign(db, campaign_id), column)


def resolve_adgroup_target_roas(db: Session, adgroup_id: str) -> dict:
    """광고그룹 grain의 목표 ROAS를 상품 파생(②)으로 해석. 반환: {target_roas, source}.

    D-NAO-57 A: 그 그룹의 매핑 상품(들)의 상품 BEP target_roas 가중평균. 매핑/BEP가 없으면
    계정 기본값(③)으로 폴백. 우선순위 ①(캠페인 override)은 grain이 캠페인이라 여기서는 다루지
    않는다 — override는 캠페인 수준 resolve_target_roas에서만 적용된다.
    source: product_bep(②) / account_default(③) / unavailable.

    ⚠️ 현재 프로덕션 호출부 0(의도적 선구축, 리뷰 P3-2 승인 유지): 기존 소비처(제안·레인·진단)는
    전부 캠페인 grain으로만 호출한다. 1차 활용 후보 = auto_operator **시간당 밴드 레인** —
    핫셋이 keyword/adgroup 엔티티 단위라 캠페인 target 대신 이 함수로 그룹별 상품 target을 쓰면
    한 캠페인 안 상품별 差를 반영할 수 있다(호출부 변경은 별도 결정·리뷰 대상, 임의 배선 금지).
    """
    val = _weighted_target_for_cpids(db, _cpids_for_adgroup(db, adgroup_id), NaverProductBep.target_roas)
    if val is not None:
        return {"target_roas": val, "source": "product_bep"}
    default = account_default_target_roas(db)
    return {"target_roas": default, "source": "account_default" if default is not None else "unavailable"}


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

    우선순위(D-NAO-57 A로 ② 활성화):
      ① naver_campaign_settings.target_roas_override — **절대 불변, 항상 최우선**
      ② 상품 파생 — 이 캠페인의 그룹들에 매핑된 상품 BEP target_roas 가중평균(그룹들 가중)
      ③ 계정 기본값(BEP 매출가중)
    source: override(①) / product_bep(②) / account_default(③) / unavailable.
    """
    settings = db.query(NaverCampaignSettings).filter(
        NaverCampaignSettings.campaign_id == campaign_id
    ).first()
    if settings and settings.target_roas_override is not None:
        return {"target_roas": settings.target_roas_override, "source": "override"}

    # ② 상품 파생: 캠페인의 그룹들에 매핑된 상품 BEP target 가중평균(그룹들 가중).
    product_val = _weighted_target_for_cpids(
        db, _cpids_for_campaign(db, campaign_id), NaverProductBep.target_roas
    )
    if product_val is not None:
        return {"target_roas": product_val, "source": "product_bep"}

    default = account_default_target_roas(db)
    return {"target_roas": default, "source": "account_default" if default is not None else "unavailable"}
