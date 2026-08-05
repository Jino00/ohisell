# rocket_1p_revenue.py — 로켓배송(1P) 매출 축 대조 SA (트랙 coupang-promo-pnl / rocket-1p, S2)
#
# 왜 있나 (Jino, 2026-08-06): *"나는 SellC 화면에서 소비자 판매가도 포함된 매출 대시보드를 보고 싶은데."*
#   쿠팡 판매분석 화면엔 **우리 매출이 없고**, 우리 종합조망엔 **소비자 판매가가 없다.**
#   그래서 08-04 하루를 두 화면에서 보면 6,536,000원과 3,885,820원이 나오고 왜 다른지 알 수 없다.
#   두 축을 **나란히** 놓는 화면이 지금 어디에도 없다 — 이 SA가 그 화면의 데이터를 만든다.
#
# ★★금지선(D-CPP-2 불변): 여기서 내는 어떤 값도 net_profit·종합조망에 결합되지 않는다.
#   소비자 매출은 **쿠팡의 매출**이지 우리 것이 아니다(1P는 쿠팡이 사입해 자기 가격으로 판다).
#   더하면 같은 물건을 납품·판매 두 번 세는 이중계상이고, 쿠팡이 할인 행사를 할 때마다
#   우리 매출이 우리와 무관하게 출렁인다. 이 모듈은 **조회 전용**이다.
#
# ★재도출하지 않는다: 합계의 「우리 매출」·「계산서 매출」은 대시보드가 쓰는 함수
#   (`compute_rocket_1p_summary_row`)를 **그대로 호출해** 얻는다. 같은 숫자를 두 곳에서 따로
#   계산하면 언젠가 갈라지고, 그때 어느 쪽이 맞는지 아무도 모른다.
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.coupang.rocket_1p_channel_pnl import (
    BASIS_SALES,
    BASIS_SETTLEMENT,
    ROCKET_1P_VENDOR_ID,
    ZERO,
    compute_rocket_1p_summary_row,
)

log = logging.getLogger(__name__)

_DEFAULT_LIMIT = 100

# 옵션 그레인 매출 두 축.
# ★`_SELL_THROUGH_SQL`(손익용)과 조인이 **다르다**: 저쪽은 납품단가를 못 붙이는 SKU를 INNER JOIN으로
#   떨어뜨린다(손익을 못 내니 맞다). 여기는 **LEFT JOIN**이다 — 소비자 매출은 단가와 무관하게 관측된
#   사실이라, 단가를 모른다고 그 판매를 화면에서 지우면 "안 팔렸다"로 보인다. 대신 우리 매출만
#   NULL로 두고 화면이 "—"로 그린다(0으로 접지 않는다: 0은 "공짜로 줬다"는 뜻이다).
_OPTION_SQL = """
WITH price AS (
  SELECT product_number, unit_purchase_price,
         ROW_NUMBER() OVER (PARTITION BY product_number ORDER BY purchase_order_seq DESC) rn
  FROM coupang_rocket_purchase_order_item
),
p AS (SELECT product_number, unit_purchase_price FROM price WHERE rn = 1)
SELECT s.option_id                                       AS option_id,
       MAX(s.sku_id)                                     AS sku_id,
       MAX(s.product_name)                               AS product_name,
       SUM(s.qty)                                        AS qty,
       SUM(s.revenue)                                    AS consumer_revenue,
       SUM(CASE WHEN p.unit_purchase_price IS NOT NULL
                THEN s.qty * p.unit_purchase_price END)  AS our_revenue,
       MAX(p.unit_purchase_price)                        AS unit_price,
       SUM(s.visitors)                                   AS visitors
FROM coupang_rocket_sales_daily s
LEFT JOIN p ON p.product_number = s.sku_id
WHERE s.vendor_id = :vendor AND s.date >= :since AND s.date <= :until
GROUP BY s.option_id
"""

# 옵션별 광고비 — Billboard(PA 기준). ★계정 총액(report/SALES 전체 기준)과 **정의가 다르다**.
#   그래서 합계 타일의 광고비와 옵션 합계는 정확히 같지 않다. 이 화면은 그 차이를 숨기지 않고
#   `ad_reconciliation`으로 드러낸다(rocket_intelligence가 같은 이유로 쓰는 방식).
_OPTION_AD_SQL = """
SELECT ad_option_id AS option_id, SUM(ad_spend) AS ad_spend
FROM coupang_ad_option_daily
WHERE sell_type = 'Retail' AND vendor_id = :vendor
  AND report_date >= :since AND report_date <= :until
GROUP BY ad_option_id
"""


def _d(v) -> Decimal:
    """None → 0. SQLite는 SUM(...)을 float으로 줄 수 있어 Decimal로 되돌린다."""
    if v is None:
        return ZERO
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _dn(v) -> Decimal | None:
    """None은 **None으로 남긴다** — '모른다'와 '0'을 가르는 자리."""
    return None if v is None else _d(v)


def _ratio(num: Decimal | None, den: Decimal | None) -> Decimal | None:
    if num is None or den is None or den == ZERO:
        return None
    return (num / den).quantize(Decimal("0.0001"))


def compute_rocket_1p_revenue(
    db: Session,
    date_from: date,
    date_to: date,
    vendor_id: str | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> dict:
    """1P 매출 두 축(소비자가 ∥ 납품가) + 옵션별 내역. **조회 전용, 회계 결합 없음.**

    합계의 our_revenue·settlement_revenue·ad_spend는 대시보드와 **같은 함수**에서 온다
    (재도출 금지 — 갈라지면 어느 쪽이 맞는지 알 수 없게 된다).
    consumer_revenue만 이 모듈이 새로 낸다(판매분석 totalGmv 합).
    """
    vendor = vendor_id or ROCKET_1P_VENDOR_ID
    params = {"vendor": vendor, "since": date_from.isoformat(), "until": date_to.isoformat()}

    # ── 합계: 기존 축 둘은 대시보드 함수를 그대로 호출한다 ──────────────
    sales_row = compute_rocket_1p_summary_row(db, date_from, date_to, BASIS_SALES)
    settle_row = compute_rocket_1p_summary_row(db, date_from, date_to, BASIS_SETTLEMENT)
    our_revenue = _dn(sales_row["revenue"]) if sales_row else None
    settlement_revenue = _dn(settle_row["revenue"]) if settle_row else None
    ad_spend = _d(sales_row["ad_spend"]) if sales_row else ZERO
    qty_axis = int(sales_row["order_count"]) if sales_row else 0

    # ── 옵션 그레인 ────────────────────────────────────────────────
    rows = db.execute(text(_OPTION_SQL), params).fetchall()
    ad_by_option = {
        str(r[0]): _d(r[1]) for r in db.execute(text(_OPTION_AD_SQL), params).fetchall()
    }

    options: list[dict] = []
    consumer_total = ZERO
    qty_total = 0
    priced_qty = 0
    for option_id, sku_id, product_name, qty, consumer, ours, unit_price, visitors in rows:
        q = int(qty or 0)
        consumer_d = _d(consumer)
        ours_d = _dn(ours)
        consumer_total += consumer_d
        qty_total += q
        if ours_d is not None:
            priced_qty += q
        ad = ad_by_option.get(str(option_id))
        options.append({
            "option_id": str(option_id),
            "sku_id": sku_id,
            "product_name": product_name,
            "qty": q,
            "consumer_revenue": str(consumer_d),
            # ★납품단가를 못 붙인 옵션은 None — 화면이 "—"로 그린다(0으로 접지 않는다).
            "our_revenue": None if ours_d is None else str(ours_d),
            "unit_price": None if unit_price is None else str(_d(unit_price)),
            "visitors": None if visitors is None else int(visitors),
            # 우리 몫 비율 = 납품가 ÷ 소비자가. 쿠팡이 얹은 마진의 뒷면이다.
            "our_share": _s(_ratio(ours_d, consumer_d)),
            "ad_spend": None if ad is None else str(ad),
            # ★RoAS는 **우리 매출 기준**이다. 소비자 매출로 내면 우리가 못 번 돈으로 광고를
            #   정당화하게 된다(1P에서 소비자가는 쿠팡의 매출이다).
            "roas": _s(_ratio(ours_d, ad)),
        })

    options.sort(key=lambda o: Decimal(o["consumer_revenue"]), reverse=True)
    shown = options[: max(1, limit)]

    ad_option_sum = sum(ad_by_option.values(), ZERO)
    return {
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat(), "vendor_id": vendor},
        "totals": {
            "qty": qty_total,
            # 소비자 판매가 축 — **쿠팡의 매출**이다. 우리 회계 매출이 아니다(D-CPP-2).
            "consumer_revenue": str(consumer_total),
            # 우리 매출(납품가 × 판매수량) — 대시보드 판매 축과 **같은 함수** 산출값.
            "our_revenue": _s(our_revenue),
            # 회계 정본(계산서 지급액). 판매 축과 **택일**이지 더하는 값이 아니다.
            "settlement_revenue": _s(settlement_revenue),
            "ad_spend": str(ad_spend),
            "our_share": _s(_ratio(our_revenue, consumer_total)),
            "roas": _s(_ratio(our_revenue, ad_spend)),
        },
        # 두 경로가 어긋나면 그건 **납품단가 커버리지**다 — 숨기지 않고 드러낸다.
        "coverage": {
            "qty_axis": qty_axis,          # 판매 축(손익용 INNER JOIN)이 센 수량
            "qty_all": qty_total,          # 소비자 축(LEFT JOIN)이 센 수량 = 전량
            "qty_priced": priced_qty,      # 그중 납품단가를 붙일 수 있었던 수량
            "priced_pct": _s(_ratio(Decimal(priced_qty), Decimal(qty_total))) if qty_total else None,
            "options_unpriced": sum(1 for o in options if o["our_revenue"] is None),
        },
        "ad_reconciliation": {
            "option_sum": str(ad_option_sum),
            "account_total": str(ad_spend),
            "diff": str(ad_option_sum - ad_spend),
            "basis": "옵션 합계는 Billboard(PA 기준), 계정 총액은 report/SALES(전체 기준, D-10). "
                     "정의가 달라 완전히 같지 않다 — 차이가 커지면 수집이 어긋난 신호다.",
        },
        "option_count": len(options),
        "shown": len(shown),
        "options": shown,
        "axes_note": "소비자 매출은 쿠팡이 고객에게 판 금액(쿠팡의 매출)이고, 우리 매출은 "
                     "판매수량×납품단가다. 두 축은 더하지 않는다 — 같은 물건을 두 번 세게 된다.",
    }


def _s(v: Decimal | None) -> str | None:
    return None if v is None else str(v)
