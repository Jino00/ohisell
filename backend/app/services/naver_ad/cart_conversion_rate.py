# cart_conversion_rate.py — cart_conversion_rate SA (단일 책임: 장바구니→구매 전환율 산출)
"""상품별/캠페인별/계정 장바구니→구매 전환율을 naver_ad_daily 창 집계로 산출 (D-NAO-58 CD1).

D-58-1(선행지표 = 즉시구매 + 장바구니 × 상품별 장바구니→구매 전환율)의 재료를 만드는 순수 SA다.
장바구니는 100% 구매가 아니라 관심 신호이므로, 상품별 실측 전환율 P(구매|장바구니)로 가중해야
탐침 선행지표가 과대평가되지 않는다.

전환율 정의(grain별): rate = clamp( Σ(conv_direct_cnt+conv_indirect_cnt)
                                   / Σ(cart_direct_cnt+cart_indirect_cnt), 0.0, 1.0 )
창 [as_of - window_days, as_of] 안의 naver_ad_daily 행을 grain별로 합산한 뒤 계산한다.
구매 수가 장바구니 수를 넘을 수 있다(장바구니 없이 바로 사는 고객이 있으므로) — 이 비율은
[0,1] 가중치로 쓰이는 P(구매|장바구니)이므로 1.0으로 클램프한다.

grain 규칙(트랙 확정):
  by_product : 쇼핑 행(campaign_type='SHOPPING')을 naver_adgroup_product에 adgroup_id로 조인.
               한 adgroup이 **정확히 하나의** mall_product_id에 매핑될 때만 그 상품에 기여한다.
               여러 상품에 매핑된 adgroup은 모호 → by_product에서 제외(by_campaign엔 포함).
               상품 장바구니 합 ≥ min_carts 일 때만 rate 방출.
  by_campaign: 모든 행을 campaign_id로 합산(파워링크 P_Test 포함 — 상품 매핑이 없는 파워링크는
               campaign grain에서 해소된다. CD1 "파워링크 signal grain 확인" 결론).
               캠페인 장바구니 합 ≥ min_carts 일 때만 방출.
  global     : 전체 행 합산. 전체 장바구니 0이면 None.
  장바구니 0인 grain은 키를 만들지 않는다(호출부 폴백: 상품→캠페인→global). 0-장바구니 grain에
  대해 rate를 지어내지 않는다(정직 경계).
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverAdgroupProduct
from app.utils.kst import kst_today

SHOPPING_TYPE = "SHOPPING"


def _rate(conv: int, cart: int) -> float:
    """clamp(conv/cart, 0.0, 1.0). 호출부는 cart>0 을 이미 보장(0 나눗셈 없음)."""
    return max(0.0, min(1.0, conv / cart))


def _one_to_one_adgroup_product(db: Session) -> dict[str, str]:
    """adgroup_id → mall_product_id (정확히 1:1 매핑인 것만). 다상품 adgroup은 제외."""
    rows = (
        db.query(NaverAdgroupProduct.adgroup_id, NaverAdgroupProduct.mall_product_id)
        .all()
    )
    products_by_adgroup: dict[str, set[str]] = {}
    for adgroup_id, mall_product_id in rows:
        products_by_adgroup.setdefault(adgroup_id, set()).add(mall_product_id)
    return {
        ag: next(iter(pids))
        for ag, pids in products_by_adgroup.items()
        if len(pids) == 1
    }


def cart_conversion_rates(
    db: Session,
    *,
    window_days: int = 30,
    as_of: date | None = None,
    min_carts: int = 1,
) -> dict:
    """장바구니→구매 전환율을 상품/캠페인/계정 grain으로 산출.

    반환:
      {
        "by_product":  {mall_product_id: rate, ...},   # 1:1 adgroup 매핑 쇼핑 상품만
        "by_campaign": {campaign_id: rate, ...},         # 전 캠페인(파워링크 포함, 폴백 grain)
        "global":      rate 또는 None,                   # 계정 전체
        "window":      {"from": iso, "to": iso},
      }
    장바구니 합이 min_carts 미만인 grain은 키가 없다(호출부 폴백).
    """
    as_of = as_of or kst_today()
    date_from = as_of - timedelta(days=window_days)

    conv_expr = NaverAdDaily.conv_direct_cnt + NaverAdDaily.conv_indirect_cnt
    cart_expr = NaverAdDaily.cart_direct_cnt + NaverAdDaily.cart_indirect_cnt
    in_window = (NaverAdDaily.ad_date >= date_from) & (NaverAdDaily.ad_date <= as_of)

    # ── by_product: 쇼핑 행을 adgroup 단위로 합산 → 1:1 매핑 상품으로 롤업 ──
    ag_map = _one_to_one_adgroup_product(db)
    by_product: dict[str, float] = {}
    if ag_map:
        adgroup_rows = (
            db.query(
                NaverAdDaily.adgroup_id,
                sqlfunc.coalesce(sqlfunc.sum(conv_expr), 0),
                sqlfunc.coalesce(sqlfunc.sum(cart_expr), 0),
            )
            .filter(in_window, NaverAdDaily.campaign_type == SHOPPING_TYPE)
            .group_by(NaverAdDaily.adgroup_id)
            .all()
        )
        prod_acc: dict[str, list[int]] = {}  # mall_product_id → [conv, cart]
        for adgroup_id, conv, cart in adgroup_rows:
            pid = ag_map.get(adgroup_id)
            if pid is None:  # 매핑 없음 or 다상품 → by_product 제외
                continue
            acc = prod_acc.setdefault(pid, [0, 0])
            acc[0] += int(conv)
            acc[1] += int(cart)
        for pid, (conv, cart) in prod_acc.items():
            if cart >= min_carts and cart > 0:
                by_product[pid] = _rate(conv, cart)

    # ── by_campaign: 전 캠페인 합산(파워링크 포함) ──
    by_campaign: dict[str, float] = {}
    campaign_rows = (
        db.query(
            NaverAdDaily.campaign_id,
            sqlfunc.coalesce(sqlfunc.sum(conv_expr), 0),
            sqlfunc.coalesce(sqlfunc.sum(cart_expr), 0),
        )
        .filter(in_window)
        .group_by(NaverAdDaily.campaign_id)
        .all()
    )
    for campaign_id, conv, cart in campaign_rows:
        conv, cart = int(conv), int(cart)
        if cart >= min_carts and cart > 0:
            by_campaign[campaign_id] = _rate(conv, cart)

    # ── global: 전체 행 합산 ──
    g = (
        db.query(
            sqlfunc.coalesce(sqlfunc.sum(conv_expr), 0),
            sqlfunc.coalesce(sqlfunc.sum(cart_expr), 0),
        )
        .filter(in_window)
        .first()
    )
    g_conv, g_cart = int(g[0]), int(g[1])
    global_rate = _rate(g_conv, g_cart) if g_cart > 0 else None

    return {
        "by_product": by_product,
        "by_campaign": by_campaign,
        "global": global_rate,
        "window": {"from": date_from.isoformat(), "to": as_of.isoformat()},
    }
