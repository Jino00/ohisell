# rocket_1p_revenue.py — 로켓배송(1P) 매출 축 대조 SA (트랙 coupang-promo-pnl / rocket-1p, S2)
#
# 왜 있나 (Jino, 2026-08-06): *"나는 SellC 화면에서 소비자 판매가도 포함된 매출 대시보드를 보고 싶은데."*
#   쿠팡 판매분석 화면엔 **우리 매출이 없고**, 우리 종합조망엔 **소비자 판매가가 없다.**
#   그래서 08-04 하루를 두 화면에서 보면 6,536,000원과 3,885,820원이 나오고 왜 다른지 알 수 없다.
#   두 축을 **나란히** 놓는 화면이 지금 어디에도 없다 — 이 SA가 그 화면의 데이터를 만든다.
#
# ★손익이 여기 붙었다 (Jino, 2026-08-07): *"여기에 비용을 넣어서 우리 손익까지 넣자.
#   광고비까지는 나오는데, 옆에 원가, 그래서 우리 손익이 얼마인지까지 나오게 하자"*
#   — 종전 주석은 "순이익을 여기 놓지 않는다"였다. 그 근거는 **원가 축이 없다**였는데
#   `_sell_through_window`가 이미 원가·분담금·VAT를 계산하고 있었고, 화면만 그걸 못 보고
#   있었다. 손익 축은 **우리 매출(납품가)** 하나뿐이다 — 소비자 매출은 손익에 들어가지 않는다.
#   ★대신 원가 커버리지가 100%가 아니면 그 손익은 **원가 확인분 부분집합**의 것이다
#   (라이브 2026-08-06 커버리지 48.9%). 미상분을 원가 0으로 넣어 전체인 척하지 않는다.
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
    COST_BY_PRODUCT_CTE,
    LATEST_UNIT_PRICE_CTE,
    ROCKET_1P_VENDOR_ID,
    ZERO,
    compute_rocket_1p_summary_row,
    promo_burden_by_option,
    window_freshness as _window_freshness,
)
from app.services.profit_calculator import payable_vat

log = logging.getLogger(__name__)

_DEFAULT_LIMIT = 100

# 원가 미상 SKU를 몇 개까지 이름으로 불러줄 것인가. 목록의 목적은 "이걸 등록하면 손익이
# 완성된다"를 알리는 것이라, 매출 큰 순으로 몇 개면 대개 커버리지의 대부분을 설명한다.
_UNCOSTED_TOP_N = 10

# 옵션 그레인 매출 두 축.
# ★`_SELL_THROUGH_SQL`(손익용)과 조인이 **다르다**: 저쪽은 납품단가를 못 붙이는 SKU를 INNER JOIN으로
#   떨어뜨린다(손익을 못 내니 맞다). 여기는 **LEFT JOIN**이다 — 소비자 매출은 단가와 무관하게 관측된
#   사실이라, 단가를 모른다고 그 판매를 화면에서 지우면 "안 팔렸다"로 보인다. 대신 우리 매출만
#   NULL로 두고 화면이 "—"로 그린다(0으로 접지 않는다: 0은 "공짜로 줬다"는 뜻이다).
#
# ★원가도 **LEFT JOIN**이다(같은 이유). 원가를 못 붙인 SKU를 떨어뜨리면 그 판매가 화면에서
#   사라지고, 남은 것만으로 낸 이익률이 전체인 척한다. 원가만 NULL로 두고 "—"로 그린다.
# ★`price`·`cost` CTE는 손익 엔진에서 **import**한다 — 여기서 따로 쓰면 두 정의가 갈라진다.
_OPTION_SQL = f"""
WITH {LATEST_UNIT_PRICE_CTE}, {COST_BY_PRODUCT_CTE}
SELECT s.option_id                                       AS option_id,
       MAX(s.sku_id)                                     AS sku_id,
       MAX(s.product_name)                               AS product_name,
       SUM(s.qty)                                        AS qty,
       SUM(s.revenue)                                    AS consumer_revenue,
       SUM(CASE WHEN p.unit_purchase_price IS NOT NULL
                THEN s.qty * p.unit_purchase_price END)  AS our_revenue,
       MAX(p.unit_purchase_price)                        AS unit_price,
       SUM(s.visitors)                                   AS visitors,
       SUM(CASE WHEN c.cost_price IS NOT NULL
                THEN s.qty * c.cost_price END)           AS cost,
       MAX(c.cost_price)                                 AS unit_cost
FROM coupang_rocket_sales_daily s
LEFT JOIN p ON p.product_number = s.sku_id
LEFT JOIN cost c ON c.product_number = s.sku_id
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


_CENT = Decimal("0.01")


def _money(v: Decimal) -> Decimal:
    """옵션 손익을 전 단위로 못 박는다.

    ★왜 반올림하나: VAT가 ÷11이라 순이익엔 무한소수가 붙는다. 그대로 두면 옵션 행들의 합과
      합계 타일이 **누적 순서 차이만으로** 끝자리에서 어긋난다(라이브 실측 2e-25원). 금액이
      1원이라도 다르면 사용자는 둘 다 안 믿는다 — 그래서 합계는 반올림된 행들의 **합**으로만
      만든다(타일을 따로 계산하지 않는다).
    """
    return v.quantize(_CENT)


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

    # ★★판매분석 미수집 구간을 **0으로 접지 않는다**(2026-08-06 적대 리뷰 P1).
    #   `coupang_rocket_sales_daily`는 쿠팡 롤링창(약 2개월) 때문에 과거가 없다. 그 창에서
    #   `compute_rocket_1p_summary_row(BASIS_SALES)`는 매출 "0"을 돌려주고, 예전 판은 그것을
    #   그대로 실었다 — 화면에 「우리 매출 0원 · RoAS 0.00」이 떠서 "광고비 2,941만원 쓰고
    #   매출이 0이었다"로 읽혔다. 실제로는 0이 아니라 **관측 불가**다.
    #   ★판별자는 qty가 아니라 **행 존재 여부**다: 행이 있고 qty=0이면 그건 진짜 0판매일이다.
    covered = len(rows) > 0
    sales_span = db.execute(
        text("SELECT MIN(date), MAX(date) FROM coupang_rocket_sales_daily WHERE vendor_id = :vendor"),
        {"vendor": vendor},
    ).fetchone()
    ad_by_option = {
        str(r[0]): _d(r[1]) for r in db.execute(text(_OPTION_AD_SQL), params).fetchall()
    }
    # ★분담금은 **모를 수 있다**(제안서 미수집 기간). None이면 손익 자체를 내지 않는다 —
    #   0으로 접으면 그 프로모션의 할인액만큼 이익이 부풀어 보인다. 판정은 엔진이 한다.
    burden_by_option = promo_burden_by_option(db, date_from, date_to, vendor)

    options: list[dict] = []
    consumer_total = ZERO
    qty_total = 0
    priced_qty = 0
    # ── 손익 누계는 **원가를 붙인 옵션만** 더한다(부분집합) ──────────────
    pnl_qty = 0
    pnl_revenue = pnl_cost = pnl_burden = pnl_ad = pnl_net = ZERO
    uncosted: dict[str, dict] = {}
    for (option_id, sku_id, product_name, qty, consumer, ours,
         unit_price, visitors, cost, unit_cost) in rows:
        q = int(qty or 0)
        consumer_d = _d(consumer)
        ours_d = _dn(ours)
        cost_d = _dn(cost)
        consumer_total += consumer_d
        qty_total += q
        if ours_d is not None:
            priced_qty += q
        ad = ad_by_option.get(str(option_id))
        # 광고 행이 없는 옵션 = 그 옵션에 광고를 안 돌린 것 → 손익에선 0원이 맞다.
        # (표시는 계속 None="—"로 둔다 — "0원 썼다"와 "행이 없다"를 화면에서 구분하려고.)
        ad_for_pnl = ad if ad is not None else ZERO
        burden = None if burden_by_option is None else burden_by_option.get(str(option_id), ZERO)

        # ★순이익 = 우리 매출 − 원가 − 분담금 − 광고비 − 납부세액.
        #   손익 엔진(`_sell_through_window`)이 창 단위로 쓰는 **같은 공식**이다.
        #   VAT가 선형이라 옵션별로 쪼개 더해도 창 합계와 정확히 같다(반올림 안 함).
        net = None
        if ours_d is not None and cost_d is not None and burden is not None:
            vat = payable_vat(ours_d, cost_d, burden, ad_for_pnl)
            net = _money(ours_d - cost_d - burden - ad_for_pnl - vat)
            pnl_qty += q
            pnl_revenue += ours_d
            pnl_cost += cost_d
            pnl_burden += burden
            pnl_ad += ad_for_pnl
            pnl_net += net
        elif cost_d is None:
            # 원가를 못 붙인 SKU — "이것만 등록하면 손익이 완성된다"를 화면이 말할 수 있게
            # SKU(=원가 등록 단위)로 묶어 모은다.
            key = str(sku_id) if sku_id is not None else f"option:{option_id}"
            u = uncosted.setdefault(key, {
                "sku_id": sku_id, "product_name": product_name,
                "qty": 0, "our_revenue": ZERO, "consumer_revenue": ZERO,
                "our_revenue_known": True,
            })
            u["qty"] += q
            u["consumer_revenue"] += consumer_d
            if ours_d is None:
                u["our_revenue_known"] = False
            else:
                u["our_revenue"] += ours_d

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
            # ── 손익 ── 원가 미상이면 cost·net 모두 None이다(0이 아니다).
            "cost": None if cost_d is None else str(cost_d),
            "unit_cost": None if unit_cost is None else str(_d(unit_cost)),
            "promo_burden": None if burden is None else str(burden),
            "net_profit": None if net is None else str(net),
            "profit_rate": _s(_ratio(net, ours_d)),
        })

    options.sort(key=lambda o: Decimal(o["consumer_revenue"]), reverse=True)
    shown = options[: max(1, limit)]

    # ── 손익 블록 ─────────────────────────────────────────────────
    # ★★이 순이익은 **원가를 붙인 부분집합**의 것이다(pnl.basis 참조). 왜 전체로 안 내는가:
    #   원가 미상 SKU를 0원 원가로 넣으면 그 매출이 통째로 이익이 되어 **이익이 부풀어 보인다**
    #   (라이브 2026-08-06: 매출의 51%가 원가 미상이었다). 그래서 매출·원가·분담금·광고비를
    #   **전부 같은 부분집합으로 제한**한다 — 축이 섞이지 않아 이익률이 그 부분집합에선 참이다.
    #   커버리지가 100%면 부분집합 = 전체이고, 그때만 이 값이 창 전체의 손익이다.
    # ★합계는 옵션 행들의 합과 **원 단위까지 같다**(반올림 없음) — 화면에서 표와 타일이
    #   어긋나면 사용자는 둘 다 안 믿는다.
    priced_revenue = sum(
        (Decimal(o["our_revenue"]) for o in options if o["our_revenue"] is not None), ZERO
    )
    pnl_vat = pnl_revenue - pnl_cost - pnl_burden - pnl_ad - pnl_net
    cost_coverage = _ratio(pnl_revenue, priced_revenue) if priced_revenue > ZERO else None
    uncosted_rows = sorted(
        uncosted.values(),
        key=lambda u: (u["our_revenue"] if u["our_revenue_known"] else u["consumer_revenue"]),
        reverse=True,
    )
    has_pnl = covered and pnl_revenue > ZERO
    pnl_block = {
        # costed_subset = 원가 확인분만 / full = 전량(커버리지 100%)
        "basis": None if not has_pnl else ("full" if cost_coverage == Decimal("1.0000")
                                           else "costed_subset"),
        "qty": pnl_qty if has_pnl else None,
        "revenue": _s(pnl_revenue) if has_pnl else None,
        "cost": _s(pnl_cost) if has_pnl else None,
        "promo_burden": _s(pnl_burden) if has_pnl else None,
        "ad_spend": _s(pnl_ad) if has_pnl else None,
        "vat": _s(pnl_vat) if has_pnl else None,
        "net_profit": _s(pnl_net) if has_pnl else None,
        "profit_rate": _s(_ratio(pnl_net, pnl_revenue)) if has_pnl else None,
        # 원가를 붙인 매출 ÷ 납품단가를 붙인 매출. 1.0이면 창 전체의 손익이다.
        "cost_coverage": _s(cost_coverage) if covered else None,
        "revenue_priced": _s(priced_revenue) if covered else None,
        # ★분담금을 모르면 손익 자체가 없다 — 화면이 "왜 안 나오는지" 말할 수 있게 이유를 싣는다.
        "promo_burden_known": burden_by_option is not None,
        "uncosted": {
            "skus": len(uncosted_rows),
            "qty": sum(u["qty"] for u in uncosted_rows),
            "our_revenue": str(sum((u["our_revenue"] for u in uncosted_rows), ZERO)),
            "top": [
                {
                    "sku_id": u["sku_id"],
                    "product_name": u["product_name"],
                    "qty": u["qty"],
                    # 납품단가까지 모르면 우리 매출도 모른다 — 0으로 접지 않는다.
                    "our_revenue": str(u["our_revenue"]) if u["our_revenue_known"] else None,
                    "consumer_revenue": str(u["consumer_revenue"]),
                }
                for u in uncosted_rows[:_UNCOSTED_TOP_N]
            ],
        },
        "note": "순이익 = 우리 매출(납품가) − 원가 − 프로모션 분담금 − 광고비 − 납부세액. "
                "basis='costed_subset'이면 **원가를 붙인 옵션만** 더한 값이라 창 전체의 "
                "손익이 아니다 — 원가 미상 SKU를 등록하면 커버리지가 오르고 basis가 'full'이 "
                "된다. 광고비는 옵션 그레인(Billboard)이라 계정 총액과 정의가 다르다.",
    }

    ad_option_sum = sum(ad_by_option.values(), ZERO)
    # 판매 축에서 온 값들은 **판매분석이 그 창을 덮을 때만** 사실이다. 안 덮으면 전부 모름.
    #   광고비·계산서 매출은 다른 원천이라 그대로 둔다(그건 실측이다).
    consumer_out = _s(consumer_total) if covered else None
    our_out = _s(our_revenue) if covered else None
    return {
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat(), "vendor_id": vendor},
        "totals": {
            "qty": qty_total if covered else None,
            # 소비자 판매가 축 — **쿠팡의 매출**이다. 우리 회계 매출이 아니다(D-CPP-2).
            "consumer_revenue": consumer_out,
            # 우리 매출(납품가 × 판매수량) — 대시보드 판매 축과 **같은 함수** 산출값.
            "our_revenue": our_out,
            # 회계 정본(계산서 지급액). 판매 축과 **택일**이지 더하는 값이 아니다.
            "settlement_revenue": _s(settlement_revenue),
            "ad_spend": str(ad_spend),
            "our_share": _s(_ratio(our_revenue, consumer_total)) if covered else None,
            "roas": _s(_ratio(our_revenue, ad_spend)) if covered else None,
        },
        "pnl": pnl_block,
        # 두 경로가 어긋나면 그건 **납품단가 커버리지**다 — 숨기지 않고 드러낸다.
        "coverage": {
            "sales_data_covered": covered,     # ★False면 위 판매 축 값들이 전부 null이다
            "sales_data_from": None if not sales_span or sales_span[0] is None else str(sales_span[0])[:10],
            "sales_data_to": None if not sales_span or sales_span[1] is None else str(sales_span[1])[:10],
            "qty_axis": qty_axis,          # 판매 축(손익용 INNER JOIN)이 센 수량
            "qty_all": qty_total if covered else None,   # 소비자 축(LEFT JOIN)이 센 수량 = 전량
            "qty_priced": priced_qty if covered else None,
            "priced_pct": _s(_ratio(Decimal(priced_qty), Decimal(qty_total))) if qty_total else None,
            "options_unpriced": sum(1 for o in options if o["our_revenue"] is None),
            "note": "sales_data_covered=false면 그 창에 판매분석 행이 하나도 없다는 뜻이다 — "
                    "판매가 0이었던 게 아니라 **관측 불가**다(쿠팡 판매분석은 롤링 약 2개월).",
        },
        "ad_reconciliation": {
            "option_sum": str(ad_option_sum),
            "account_total": str(ad_spend),
            "diff": str(ad_option_sum - ad_spend),
            "basis": "옵션 합계는 Billboard(PA 기준), 계정 총액은 report/SALES(전체 기준, D-10). "
                     "정의가 달라 완전히 같지 않다 — 차이가 커지면 수집이 어긋난 신호다.",
        },
        # ★★"며칠을 보고 있는가"를 화면이 말할 수 있게 한다(2026-08-06 적대 리뷰 P1).
        #   판매분석은 당일·전일치를 주지 않아 기본 창(오늘까지 7일)엔 늘 5일치만 들어온다.
        #   그걸 모르고 주간 비교를 하면 이번 주가 **항상** 20% 낮게 나온다. 더 나쁜 건
        #   수집이 진짜 멈춰도 화면상 구별할 수단이 없다는 것이다 — 그래서 창의 실제 상태를 싣는다.
        "freshness": _window_freshness(db, date_from, date_to, vendor),
        "option_count": len(options),
        "shown": len(shown),
        "options": shown,
        "axes_note": "소비자 매출은 쿠팡이 고객에게 판 금액(쿠팡의 매출)이고, 우리 매출은 "
                     "판매수량×납품단가다. 두 축은 더하지 않는다 — 같은 물건을 두 번 세게 된다.",
    }


def _s(v: Decimal | None) -> str | None:
    return None if v is None else str(v)
