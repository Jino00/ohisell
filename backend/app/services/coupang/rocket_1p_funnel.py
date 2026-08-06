# rocket_1p_funnel.py — 로켓배송(1P) 옵션별 유입·전환 퍼널 SA (S3, 2026-08-06)
#
# 무엇에 답하나: **"왜 안 팔리나"**. 매출 화면(S2)이 "얼마 벌었나"에 답한다면 이쪽은 그 앞단이다.
#   방문자 → 조회 → 주문 → 판매수량 으로 내려가며 어느 단계에서 새는지 옵션별로 가른다.
#   재료는 S1이 담기 시작한 `page_views`·`orders`와 옵션 속성이다(추가 API 호출 0).
#
# ★★전환율은 **Σ주문 ÷ Σ조회**로만 낸다. 일별 전환율의 평균이 아니다 —
#   조회 4회에 주문 1회인 날(25%)과 조회 400회에 주문 40회인 날(10%)을 평균 내면 17.5%가 되는데
#   실제 기간 전환율은 10.1%다. 작은 날이 큰 날과 같은 표를 갖는 셈이라 **틀린다**.
#   (S1이 orders·page_views를 담기 전에는 이 계산 자체가 불가능했다 — 일별 비율만 있었다.)
#
# ★★전략 추천을 하지 않는다(금지선). 이 모듈은 **위치**만 말한다: 기간 중앙값 대비 어디인가.
#   분류에 쓰는 임계값은 전부 응답에 실어 보낸다 — 숨은 기준으로 판정하면 그건 사실이 아니라 의견이다.
#
# ★조회 전용. net_profit·종합조망에 결합되지 않는다(D-CPP-2 불변).
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.coupang.rocket_1p_channel_pnl import ROCKET_1P_VENDOR_ID, ZERO

log = logging.getLogger(__name__)

_DEFAULT_LIMIT = 200
# 분류에 필요한 최소 조회수. 낮은 표본에서 나온 전환율은 위치를 말할 근거가 못 된다
#   (조회 3회에 주문 1회 = 33%가 상위로 올라가면 표가 통째로 거짓말을 한다).
#   ★임계값은 응답에 실어 보낸다 — 숨기면 판정이 사실처럼 보인다.
_DEFAULT_MIN_PAGE_VIEWS = 30

_FUNNEL_SQL = """
SELECT s.option_id                                            AS option_id,
       MAX(s.sku_id)                                          AS sku_id,
       MAX(s.product_name)                                    AS product_name,
       SUM(s.visitors)                                        AS visitors,
       SUM(s.page_views)                                      AS page_views,
       SUM(s.orders)                                          AS orders,
       SUM(s.qty)                                             AS qty,
       SUM(s.revenue)                                         AS consumer_revenue,
       COUNT(*)                                               AS days,
       -- ★부분 결손을 센다: SUM은 NULL을 조용히 건너뛴다. 며칠치가 빠진 합계를
       --   온전한 합계처럼 보여주면 전환율이 이유 없이 튄다.
       SUM(CASE WHEN s.page_views IS NULL THEN 1 ELSE 0 END)  AS days_missing_pv,
       SUM(CASE WHEN s.orders IS NULL THEN 1 ELSE 0 END)      AS days_missing_orders
FROM coupang_rocket_sales_daily s
WHERE s.vendor_id = :vendor AND s.date >= :since AND s.date <= :until
GROUP BY s.option_id
"""

_ATTRS_SQL = """
SELECT option_id, is_oos, rating_count, rating_score, brand_name, category_path, attrs_observed_at
FROM coupang_rocket_option_sku
"""


def _i(v) -> int | None:
    return None if v is None else int(v)


def _rate(num, den) -> Decimal | None:
    """비율. 분모가 0이거나 어느 쪽이든 모르면 **None**(0이 아니다)."""
    if num is None or den in (None, 0):
        return None
    return (Decimal(num) / Decimal(den)).quantize(Decimal("0.0001"))


def _median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    if len(s) % 2:
        return s[mid]
    return ((s[mid - 1] + s[mid]) / 2).quantize(Decimal("0.0001"))


def _position(cvr: Decimal | None, pv: int | None, med_cvr: Decimal | None,
              med_pv: Decimal | None, min_pv: int) -> str:
    """옵션의 **위치**를 기간 중앙값 대비로 말한다. 추천이 아니라 서술이다.

    ★표본이 얕으면 판정하지 않는다 — 조회 3회에 주문 1회(33%)를 '전환 우수'로 올리면
      표 전체가 거짓말을 한다. 그런 옵션은 'low_sample'로 남기고 끝낸다.
    """
    if pv is None or pv < min_pv or cvr is None or med_cvr is None or med_pv is None:
        return "low_sample"
    hi_pv = Decimal(pv) >= med_pv
    hi_cvr = cvr >= med_cvr
    if hi_pv and hi_cvr:
        return "views_high_cvr_high"
    if hi_pv and not hi_cvr:
        return "views_high_cvr_low"      # 보이긴 하는데 안 산다
    if not hi_pv and hi_cvr:
        return "views_low_cvr_high"      # 사긴 사는데 안 보인다
    return "views_low_cvr_low"


def compute_rocket_1p_funnel(
    db: Session,
    date_from: date,
    date_to: date,
    vendor_id: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    min_page_views: int = _DEFAULT_MIN_PAGE_VIEWS,
) -> dict:
    """옵션별 유입·전환 퍼블. 방문자 → 조회 → 주문 → 판매수량.

    반환의 모든 비율은 **합계의 몫**이지 일별 비율의 평균이 아니다.
    """
    vendor = vendor_id or ROCKET_1P_VENDOR_ID
    params = {"vendor": vendor, "since": date_from.isoformat(), "until": date_to.isoformat()}
    rows = db.execute(text(_FUNNEL_SQL), params).fetchall()
    attrs = {
        str(r[0]): {
            "is_oos": None if r[1] is None else bool(r[1]),
            "rating_count": _i(r[2]),
            # 리뷰가 0건이면 평점은 **모르는 값**이다(0점이 아니다) — 0.0으로 그리면 최악의
            #   상품처럼 보인다. 신상품이 그 자리에 앉는다.
            "rating_score": None if (r[3] is None or not r[2]) else str(r[3]),
            "brand_name": r[4],
            "category_path": r[5],
            "attrs_observed_at": None if r[6] is None else str(r[6]),
        }
        for r in db.execute(text(_ATTRS_SQL)).fetchall()
    }

    options: list[dict] = []
    t_visitors = t_pv = t_orders = t_qty = 0
    t_revenue = ZERO
    missing_days = 0
    for (option_id, sku_id, name, visitors, pv, orders, qty, revenue,
         days, miss_pv, miss_orders) in rows:
        v_i, pv_i, o_i = _i(visitors), _i(pv), _i(orders)
        q_i = int(qty or 0)
        t_visitors += v_i or 0
        t_pv += pv_i or 0
        t_orders += o_i or 0
        t_qty += q_i
        t_revenue += Decimal(str(revenue or 0))
        missing_days += int(miss_pv or 0)
        options.append({
            "option_id": str(option_id),
            "sku_id": sku_id,
            "product_name": name,
            "visitors": v_i,
            "page_views": pv_i,
            "orders": o_i,
            "qty": q_i,
            "consumer_revenue": str(Decimal(str(revenue or 0))),
            # 탐색 깊이 — 한 방문자가 상세를 몇 번 봤나.
            "views_per_visitor": _s(_rate(pv_i, v_i)),
            # 구매전환율 — 쿠팡 화면의 「구매전환율」과 같은 정의(주문/조회).
            "cvr": _s(_rate(o_i, pv_i)),
            # 개당 구매수 — 한 주문에 몇 개를 담았나.
            "units_per_order": _s(_rate(q_i, o_i)),
            "days": int(days or 0),
            # 며칠치가 빠졌는지 — 합계가 온전한지 판단할 근거.
            "days_missing": int(miss_pv or 0) + int(miss_orders or 0),
            **attrs.get(str(option_id), {
                "is_oos": None, "rating_count": None, "rating_score": None,
                "brand_name": None, "category_path": None, "attrs_observed_at": None,
            }),
        })

    # ── 위치 판정용 중앙값 (표본 임계를 넘은 옵션들만으로 낸다) ──────────
    eligible = [o for o in options if (o["page_views"] or 0) >= min_page_views and o["cvr"]]
    med_cvr = _median([Decimal(o["cvr"]) for o in eligible])
    med_pv = _median([Decimal(o["page_views"]) for o in eligible])
    for o in options:
        o["position"] = _position(
            Decimal(o["cvr"]) if o["cvr"] else None, o["page_views"], med_cvr, med_pv, min_page_views
        )

    options.sort(key=lambda o: (o["page_views"] or 0), reverse=True)
    shown = options[: max(1, limit)]

    return {
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat(), "vendor_id": vendor},
        "totals": {
            "visitors": t_visitors,
            "page_views": t_pv,
            "orders": t_orders,
            "qty": t_qty,
            "consumer_revenue": str(t_revenue),
            "views_per_visitor": _s(_rate(t_pv, t_visitors)),
            "cvr": _s(_rate(t_orders, t_pv)),          # ★Σ주문/Σ조회 — 일별 평균이 아니다
            "units_per_order": _s(_rate(t_qty, t_orders)),
        },
        # 판정 기준을 숨기지 않는다 — 숨은 임계로 나눈 분류는 사실이 아니라 의견이다.
        "thresholds": {
            "min_page_views": min_page_views,
            "median_cvr": _s(med_cvr),
            "median_page_views": _s(med_pv),
            "eligible_options": len(eligible),
            "note": "위치(position)는 기간 중앙값 대비 상/하일 뿐 권고가 아니다. 조회가 "
                    f"{min_page_views}회 미만인 옵션은 표본이 얕아 판정하지 않는다(low_sample).",
        },
        "coverage": {
            "option_days_missing_metrics": missing_days,
            "note": "S1(2026-08-06) 이전에 수집된 행은 조회·주문이 없다. 그런 날이 섞이면 "
                    "합계가 부분값이 되어 전환율이 이유 없이 튄다 — 그래서 세어 둔다.",
        },
        "option_count": len(options),
        "shown": len(shown),
        "options": shown,
        # 실측(2026-08-06): 바이박스 승자 필드는 1P 244옵션 **전부 False**라 열로 내지 않는다.
        #   3P와 의미가 다를 수 있다(쿠팡이 직접 파는 구조). 값이 생기면 그때 넣는다.
        "omitted_fields": ["is_item_winner"],
    }


def _s(v: Decimal | None) -> str | None:
    return None if v is None else str(v)
