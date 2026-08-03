# today_proxy_revenue.py — 캠페인별 **당일** 매출 프록시 SA (D-NAO-104 Phase 1, 계획서 §4-ⓐ).
"""역할(SA·단일 책임·읽기 전용): "오늘 이 캠페인은 얼마를 벌었나"에 답할 **유일한 근사치**를
캠페인 단위로 산출한다. 판정도 문장 조립도 하지 않는다 — 숫자와 "왜 못 구했는지"만 준다.

★왜 별도 SA인가: `actual_revenue.naver_order_revenue()`는 **계정 총계 전용**이다(주문에는
캠페인 귀속 정보가 없다). 캠페인 배분은 쇼핑 광고그룹↔판매상품 매핑(naver_adgroup_product,
mall_product_id == orders.platform_product_id)으로만 성립하고, 그 매핑이 없는 지면
(파워링크·브랜드검색)은 **원리적으로 배분이 불가능**하다. 그때 0을 채우면 거짓이므로
`revenue=None`(= 알 수 없음)과 사유를 함께 돌려준다(원칙22, 계획서 §0-5).

★정직 경계(소비 전 숙지 — 화면에 반드시 병기):
  이 값은 "광고로 인한 매출"이 아니라 **그 상품의 그날 전체 판매액**이다(광고 외 유입 포함).
  구조적으로 과대추정 방향의 **상한 프록시**다([[naver-ad-today-conversion-via-smartstore]]).
  budget_pacing이 증액 판정에서 이 신호를 "필요조건"으로만 쓰는 것과 같은 이유로, 성과 뷰도
  이걸 확정 성과로 표시하면 안 된다.

★회계 규약은 actual_revenue와 동일하게 고정한다(채널6 · 매출제외 상태 제외 ·
  selling_price=라인총액이라 ×수량 2중계상 없음). 여기서만 다르게 굴면 같은 날 두 화면이
  다른 매출을 말한다.

★한 상품이 여러 캠페인에 매핑돼 있으면 매출을 캠페인 수로 **균등 분할**한다. 실제 기여
  비율은 알 수 없고(추정 금지), 양쪽에 100%씩 계상하면 계정 합계가 실제 매출을 넘어버린다.
  분모는 `product_campaign_share.campaigns_per_product` **한 곳에서만** 온다(P2-1) — 여기서
  요청 범위 안의 캠페인만 세면 조회 범위에 따라 같은 캠페인의 매출이 달라진다.
"""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdgroupProduct, Order
from app.services.cafe24_status_mapper import REVENUE_EXCLUDED
from app.services.naver_ad import adgroup_product_freshness, product_campaign_share

NAVER_CHANNEL_ID = 6

# 매핑이 없어 배분 자체가 불가능한 경우의 사유(화면이 그대로 쓸 수 있는 한국어).
NO_MAPPING_REASON = "이 광고에 연결된 판매 상품 정보가 없어 오늘 매출을 계산할 수 없습니다."


def product_ids_by_campaign(db: Session, campaign_ids: list[str]) -> dict[str, list[str]]:
    """{campaign_id: [상품ID…]} — 1쿼리(N+1 금지). 매핑이 없는 캠페인은 키 자체가 없다.

    소스는 shopping_ad_product_sync 스냅샷이라 **쇼핑 캠페인만** 행이 있다(파워링크·브랜드검색은
    소재에 mallProductId가 없다). 갓 'ours'로 전환돼 다음 sync 전인 캠페인도 여기서 비어 있다 —
    둘 다 "아직 모른다"이지 "매출 0"이 아니다."""
    if not campaign_ids:
        return {}
    rows = (
        db.query(NaverAdgroupProduct.campaign_id, NaverAdgroupProduct.mall_product_id)
        .filter(NaverAdgroupProduct.campaign_id.in_(campaign_ids),
                # codex 3R P1: 누적 upsert 테이블이라 옛 상품이 남는다. 필터 없이 읽으면
                # 이미 빠진 상품의 당일 판매액이 이 캠페인 매출로 잡혀 ROAS가 부풀고,
                # budget_pacing이 그 값으로 증액을 통과시킨다.
                adgroup_product_freshness.fresh_condition())
        .all()
    )
    by_campaign: dict[str, list[str]] = {}
    for cid, pid in rows:
        if not cid or not pid:
            continue
        bucket = by_campaign.setdefault(cid, [])
        if pid not in bucket:
            bucket.append(pid)
    return {cid: sorted(pids) for cid, pids in by_campaign.items()}


def _revenue_by_product(db: Session, product_ids: list[str], day: date) -> dict[str, Decimal]:
    """당일(KST) 상품별 실주문 매출 합 — 1쿼리. 주문이 없는 상품은 키가 없다(=0원)."""
    if not product_ids:
        return {}
    rows = (
        db.query(
            Order.platform_product_id,
            sqlfunc.coalesce(sqlfunc.sum(Order.selling_price), 0),
        )
        .filter(
            Order.channel_id == NAVER_CHANNEL_ID,
            Order.status.notin_(tuple(REVENUE_EXCLUDED)),
            Order.platform_product_id.in_(product_ids),
            Order.order_date >= datetime.combine(day, time.min),
            Order.order_date <= datetime.combine(day, time.max),
        )
        .group_by(Order.platform_product_id)
        .all()
    )
    return {pid: Decimal(str(amount or 0)) for pid, amount in rows if pid}


def build(db: Session, campaign_ids: list[str], day: date) -> dict[str, dict]:
    """{campaign_id: {revenue, product_count, shared_product_count, reason}} — 요청한 캠페인 전부.

    revenue: int(원) 또는 **None**(=알 수 없음, 상품 매핑 부재). 매핑이 있는데 그날 주문이
      없으면 revenue=0이다 — 이건 측정된 0이지 '모름'이 아니라서 구분해 돌려준다.
    reason: revenue가 None일 때만 채워지는 한국어 사유(화면 그대로 노출용).
    shared_product_count: 여러 캠페인이 공유해 매출을 나눠 계상한 상품 수(정직 표기용).
    """
    result: dict[str, dict] = {
        cid: {"revenue": None, "product_count": 0, "shared_product_count": 0,
              "reason": NO_MAPPING_REASON}
        for cid in campaign_ids
    }
    pids_by_campaign = product_ids_by_campaign(db, campaign_ids)
    if not pids_by_campaign:
        return result

    # 분모 = "그 상품을 매핑한 **모든** 캠페인 수"(요청 범위·자동운영 여부 무관, P2-1).
    # ★요청 범위 안에서만 세면 안 된다: 같은 캠페인을 단독 조회할 때와 전체 조회할 때 매출이
    #   달라진다(범위가 좁으면 분모가 작아져 과대 계상).
    all_pids = sorted({pid for pids in pids_by_campaign.values() for pid in pids})
    shares = product_campaign_share.campaigns_per_product(db, all_pids)
    revenue_by_product = _revenue_by_product(db, all_pids, day)

    for cid, pids in pids_by_campaign.items():
        total = Decimal(0)
        shared = 0
        for pid in pids:
            share = shares.get(pid, 1) or 1
            if share > 1:
                shared += 1
            total += revenue_by_product.get(pid, Decimal(0)) / Decimal(share)
        result[cid] = {
            "revenue": int(total),
            "product_count": len(pids),
            "shared_product_count": shared,
            "reason": None,
        }
    return result
