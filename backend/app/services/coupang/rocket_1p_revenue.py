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
import os
from datetime import date, timedelta
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
    _money,
    compute_rocket_1p_summary_row,
    promo_burden_by_day_option,
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
#
# ★그레인이 **「날짜×옵션」**이다(2026-08-07, Jino: "일일 손익을 보고 싶은거야").
#   화면이 일별 손익과 옵션별 손익을 둘 다 내는데, 각자 자기 그레인에서 반올림하면
#   「일별 합계 ≠ 옵션별 합계 ≠ 기간 타일」이 된다. 가장 잘게 쪼갠 여기서 한 번만
#   반올림하고 양쪽 다 그 합으로 만들면 셋이 원 단위까지 정확히 같아진다.
_DAY_OPTION_SQL = f"""
WITH {LATEST_UNIT_PRICE_CTE}, {COST_BY_PRODUCT_CTE}
SELECT s.date                                            AS d,
       s.option_id                                       AS option_id,
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
GROUP BY s.date, s.option_id
"""

# 옵션별 광고비 — Billboard(PA 기준). ★계정 총액(report/SALES 전체 기준)과 **정의가 다르다**.
#   그래서 합계 타일의 광고비와 옵션 합계는 정확히 같지 않다. 이 화면은 그 차이를 숨기지 않고
#   `ad_reconciliation`으로 드러낸다(rocket_intelligence가 같은 이유로 쓰는 방식).
_DAY_OPTION_AD_SQL = """
SELECT report_date AS d, ad_option_id AS option_id, SUM(ad_spend) AS ad_spend
FROM coupang_ad_option_daily
WHERE sell_type = 'Retail' AND vendor_id = :vendor
  AND report_date >= :since AND report_date <= :until
GROUP BY report_date, ad_option_id
"""

# ★원가가 안 붙는 이유는 **셋인데 할 일이 다 다르다**(2026-08-07, Jino 실사고).
#   화면이 "원가를 SellC에 등록하세요"라고만 말했는데, 실제로 등록해도 아무것도 안 움직였다 —
#   그 SKU들은 원가가 없는 게 아니라 **쿠팡 상품번호 ↔ 내부 SKU 연결이 없었다**(178건).
#   원가는 `product_master`(내부 SKU 그레인)에 붙고 판매는 쿠팡 상품번호로 들어오므로,
#   둘을 잇는 다리(`rocket_product_cost_map`)가 없으면 원가를 아무리 넣어도 안 붙는다.
#   ⓐ no_link  : 다리가 없다 → 「쿠팡 운영」 원가 매핑에서 **연결**해야 한다
#   ⓑ no_cost  : 다리는 있는데 그 내부 SKU에 원가가 없다 → SellC에 **원가 등록**
#   ⓒ excluded : 원가 제외로 **이미 결정**됨(샘플·증정) → 아무것도 하면 안 된다(재제안 방지)
# ★"언제부터 팔린 물건인가" — 신제품을 가려내는 두 축(2026-08-07, Jino).
#   왜 필요한가: 원가 미연결 목록이 매출 순이라 **옛날부터 안 이어진 꼬리**와 **이번에 새로
#   나온 신제품**이 섞여 보인다. 그런데 둘은 급한 정도가 다르다 — 실측(2026-08-07): 06-18
#   매핑 작업 이후 새로 들어온 상품번호는 **단 3개**인데 그 3개(Z폴드8 3종)가 미연결 매출의
#   **56%**였다. 새 폰이 나올 때마다 이 일이 반복되므로, 신규는 나오자마자 보여야 한다.
#
# ★`first_sold_at`은 **판매분석 전 범위**에서 센다(조회 창이 아니라). 창 안의 MIN을 쓰면
#   어느 창을 열어도 전부 "이번에 처음"이 되어 신호가 죽는다.
# ★단 판매분석은 롤링(약 2개월)이라 그 시작일과 같은 값이면 **그 전은 모른다** —
#   화면이 "신규"라고 단정하지 못하게 그 사실을 함께 싣는다(0으로 접지 않기와 같은 원칙).
_FIRST_SOLD_SQL = """
SELECT sku_id, MIN(date) AS first_sold
FROM coupang_rocket_sales_daily
WHERE vendor_id = :vendor AND sku_id IS NOT NULL
GROUP BY sku_id
"""

# 발주에 처음 등장한 날 = "상품이 생긴 날". 판매분석과 달리 **전 이력**이 있어(2025-07~)
# 롤링창의 한계를 받지 않는다 — 신제품 판별의 주 축이다.
_FIRST_PO_SQL = """
SELECT i.product_number, MIN(date(o.po_created_at)) AS first_po
FROM coupang_rocket_purchase_order_item i
JOIN coupang_rocket_purchase_order o ON o.purchase_order_seq = i.purchase_order_seq
WHERE o.vendor_id = :vendor AND o.po_created_at IS NOT NULL
GROUP BY i.product_number
"""

_COST_MAP_STATE_SQL = """
SELECT m.product_number,
       m.status,
       CASE WHEN pm.cost_price IS NOT NULL THEN 1 ELSE 0 END AS has_cost,
       m.note
FROM rocket_product_cost_map m
LEFT JOIN product_master pm ON pm.internal_sku = m.internal_sku
"""


def _d(v) -> Decimal:
    """None → 0. SQLite는 SUM(...)을 float으로 줄 수 있어 Decimal로 되돌린다."""
    if v is None:
        return ZERO
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _dn(v) -> Decimal | None:
    """None은 **None으로 남긴다** — '모른다'와 '0'을 가르는 자리."""
    return None if v is None else _d(v)


# ★`_money`(전 단위 반올림)는 **`rocket_1p_channel_pnl`이 정본**이다 — 위에서 import한다.
#   여기 있던 것을 2026-08-07에 옮겼다: 손익 근거 SA도 같은 규약이 필요한데 그 모듈은
#   D-CPP-2 가드 때문에 이 파일을 참조할 수 없어서다. 규약이 두 곳이면 언젠가 갈라진다.


def _bep_roas(revenue: Decimal | None, cost: Decimal | None,
              burden: Decimal | None) -> Decimal | None:
    """손익분기 RoAS = 매출 ÷ 공헌이익(매출 − 원가 − 분담금). 모르면 None.

    ★이 값보다 실제 RoAS가 **낮으면 그 옵션은 적자**다. 원가나 분담금을 모르면 낼 수 없고,
      공헌이익이 0 이하면 광고를 아무리 잘 돌려도 흑자가 안 되므로 수치 대신 None을 낸다
      (0이나 무한대로 적으면 화면이 그걸 «달성 가능한 목표»로 그린다).
    """
    if revenue is None or cost is None or burden is None or revenue <= ZERO:
        return None
    contribution = revenue - cost - burden
    if contribution <= ZERO:
        return None
    return (revenue / contribution).quantize(Decimal("0.0001"))


def _ratio(num: Decimal | None, den: Decimal | None) -> Decimal | None:
    if num is None or den is None or den == ZERO:
        return None
    return (num / den).quantize(Decimal("0.0001"))


def day_option_atoms(
    db: Session, date_from: date, date_to: date, vendor_id: str | None = None
) -> dict:
    """「날짜×옵션」 원자와 그 파생 — **이 함수가 원자 파생의 단일 출처다.**

    `compute_rocket_1p_revenue`가 이것을 소비해 폴드(일별·옵션별·사다리)를 만들고,
    손익 근거 화면(pnl-audit)이 같은 것을 소비해 원자 목록·원자 상세를 만든다.
    두 화면이 **같은 코드**로 파생하므로 «근거가 화면과 다른 공식을 쓰는 일»이 없다.

    ★★그러나 **코드의 단일 출처는 결과의 단일 출처가 아니다** — 결과가 인자에 의존할 때는.
      아래 창 종속성을 어기면 같은 함수를 쓰고도 두 화면의 숫자가 갈린다.

    ── 창(date_from·date_to) 종속성 계약 ──────────────────────────────
    · **창-불변**: qty·consumer_revenue·our_revenue·unit_price·unit_cost·cost·visitors
      ·ad_spend — 어느 창으로 불러도 같은 원자는 같은 값이다(창은 필터일 뿐이다).
    · **창-종속**: `promo_burden` · `net_profit` · `net_profit_upper` · `burden_known`.
      분담금 가드가 **창에 걸친 프로모션**을 보기 때문이다 — 창 안 어디든 제안서(할인액
      원천) 없는 프로모션이 하나라도 있으면 `burden_known=False`가 되어 **그 창의 모든**
      원자가 net=None이 된다(그 프로모션과 무관한 날의 원자까지).
      실측: 같은 (2026-08-04, "A") 원자, 8/1~8/2에 제안서 없는 프로모션이 있을 때 —
        창 8/1~8/4 → burden_known=False · net=None
        창 8/4~8/4 → burden_known=True  · net=363636.36
    · ★★**따라서 호출자는 화면과 같은 창으로 불러야 한다.** 하루치만 보려고 `(d, d)`로
      좁혀 부르면 화면이 «—»(모름)로 그린 행에 숫자가 찍힌다 — 그건 근거가 아니라 **다른
      주장**이다. 넓은 창으로 부른 뒤 결과를 그 날짜로 **거르는** 것이 옳다.

    반환: {"atoms": [원자…], "burden_known": bool,
           "ad_by_day_option": {(날짜,옵션): 광고비}, "ad_by_option": {옵션: 광고비}}

    ★광고 맵 둘은 `atoms`의 **상위집합**이다 — 그 창에 판매행이 없는 (날짜,옵션)도 들어
      있지만 원자는 판매행에서만 나온다. 즉 `Σ ad_by_option ≠ Σ atom["ad_spend"]`가
      정상이고, 그 차이가 「판매 없는 옵션의 광고비」다(귀속할 원자가 없는 돈).
    ★`ad_spend` 불변식: 광고 행이 **있으면** 금액, 없으면 **None** — None은 "행이 없다"는
      뜻이지 "0원 썼다"가 아니다. 손익에 쓰는 값은 `ad_for_pnl`이고 `ad_spend or 0`과 같다.
    원자 필드의 None 의미는 화면과 같다 — 모름이지 0이 아니다.
    """
    vendor = vendor_id or ROCKET_1P_VENDOR_ID
    params = {"vendor": vendor, "since": date_from.isoformat(), "until": date_to.isoformat()}
    rows = db.execute(text(_DAY_OPTION_SQL), params).fetchall()

    ad_by_day_option: dict[tuple[str, str], Decimal] = {}
    ad_by_option: dict[str, Decimal] = {}
    for d_, oid_, amt in db.execute(text(_DAY_OPTION_AD_SQL), params).fetchall():
        key, val = (str(d_)[:10], str(oid_)), _d(amt)
        ad_by_day_option[key] = val
        ad_by_option[str(oid_)] = ad_by_option.get(str(oid_), ZERO) + val

    # ★분담금은 **모를 수 있다**(제안서 미수집 기간). None이면 손익 자체를 내지 않는다 —
    #   0으로 접으면 그 프로모션의 할인액만큼 이익이 부풀어 보인다. 판정은 엔진이 한다.
    burden_by_day_option = promo_burden_by_day_option(db, date_from, date_to, vendor)

    atoms: list[dict] = []
    for (day, option_id, sku_id, product_name, qty, consumer, ours,
         unit_price, visitors, cost, unit_cost) in rows:
        day_key = str(day)[:10]
        oid = str(option_id)
        q = int(qty or 0)
        consumer_d = _d(consumer)
        ours_d = _dn(ours)
        cost_d = _dn(cost)
        ad = ad_by_day_option.get((day_key, oid))
        # 광고 행이 없는 옵션·날 = 그날 그 옵션에 광고를 안 돌린 것 → 손익에선 0원이 맞다.
        # (표시는 계속 None="—"로 둔다 — "0원 썼다"와 "행이 없다"를 화면에서 구분하려고.)
        ad_for_pnl = ad if ad is not None else ZERO
        burden = (None if burden_by_day_option is None
                  else burden_by_day_option.get((day_key, oid), ZERO))

        # ★순이익 = 우리 매출 − 원가 − 분담금 − 광고비 − 납부세액.
        #   손익 엔진(`_sell_through_window`)이 창 단위로 쓰는 **같은 공식**이다.
        #   VAT는 선형이라 어느 그레인으로 쪼개도 공식은 같다. 반올림은 **여기서 한 번만**.
        # ★공식·반올림 지점은 종전 인라인 파생과 **문자 그대로 같다** — 옮긴 것이지 고친 게
        #   아니다. 바꾸면 test_rocket_1p_revenue.py 30여 건이 잡는다.
        net = None
        net_upper = None
        if ours_d is not None and cost_d is not None and burden is not None:
            vat = payable_vat(ours_d, cost_d, burden, ad_for_pnl)
            net = _money(ours_d - cost_d - burden - ad_for_pnl - vat)
        elif cost_d is None and ours_d is not None and burden is not None:
            # ★원가를 몰라도 **부호는 알 수 있는 경우가 있다**(적대 리뷰 P1): 원가는 0 이상이므로
            #   원가를 0으로 놓은 값이 순이익의 **상한**이다. 그 상한이 음수면 이 옵션은
            #   원가가 얼마든 **확정 적자**다. 라이브에 그런 행이 있었는데(광고비 73,593원 >
            #   우리 매출 48,000원) 순이익 열이 "—"(모름)으로 떠서 «문제 없음»으로 읽혔다.
            bound = _money(ours_d - burden - ad_for_pnl
                           - payable_vat(ours_d, ZERO, burden, ad_for_pnl))
            if bound < ZERO:
                net_upper = bound

        atoms.append({
            "date": day_key, "option_id": oid,
            "sku_id": sku_id, "product_name": product_name,
            "qty": q, "consumer_revenue": consumer_d, "our_revenue": ours_d,
            "unit_price": _dn(unit_price), "cost": cost_d, "unit_cost": _dn(unit_cost),
            "visitors": None if visitors is None else int(visitors),
            "ad_spend": ad, "ad_for_pnl": ad_for_pnl, "promo_burden": burden,
            "net_profit": net, "net_profit_upper": net_upper,
        })
    return {"atoms": atoms, "burden_known": burden_by_day_option is not None,
            "ad_by_day_option": ad_by_day_option, "ad_by_option": ad_by_option}


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

    # ── 합계: 기존 축 둘은 대시보드 함수를 그대로 호출한다 ──────────────
    sales_row = compute_rocket_1p_summary_row(db, date_from, date_to, BASIS_SALES)
    settle_row = compute_rocket_1p_summary_row(db, date_from, date_to, BASIS_SETTLEMENT)
    our_revenue = _dn(sales_row["revenue"]) if sales_row else None
    settlement_revenue = _dn(settle_row["revenue"]) if settle_row else None
    ad_spend = _d(sales_row["ad_spend"]) if sales_row else ZERO
    qty_axis = int(sales_row["order_count"]) if sales_row else 0

    # ── 「날짜×옵션」 그레인 ─────────────────────────────────────────
    # ★파생(순이익·상한·광고 귀속)은 **여기서 하지 않는다** — `day_option_atoms`가 유일한
    #   출처다. 손익 근거 화면이 같은 원자를 소비하므로 두 화면이 다른 공식을 쓸 수 없다.
    #   (창 종속성 계약은 그 함수의 docstring에 있다 — 같은 창으로 불러야 같은 숫자다.)
    # ★단 이 함수는 원자를 **접기만 하지 않는다**: basis='full'일 때 `ad_no_sales`
    #   (그 창에 판매행이 없는 옵션의 광고비 — 귀속할 원자가 아예 없는 돈)를 세후로 타일에서
    #   **추가 차감**한다. 그래서 「Σ원자 순이익 = 타일」의 성립 여부를 정하는 술어는
    #   `ad_no_sales`의 크기가 아니라 **`ad_no_sales_included`(= is_full)**다:
    #     · included=True  → `Σ원자 − 타일 = _money(ad_no_sales × 100/110)`
    #     · included=False → `ad_no_sales`가 0이 아니어도 **`Σ원자 = 타일`**(차감 자체가 없다)
    #   ★기본 상태는 included=False다 — 라이브 실측(2026-08-07 22:47, 창 08-01~08-07):
    #     basis='costed_subset' · cost_coverage=0.9731 · ad_no_sales=282,794 · included=False.
    #     즉 **0이 아닌 돈이 있는데도 차감이 없다.** 조건을 안 적으면 이 문장이 평상시에 거짓이 된다.
    #   ★잔차가 생길 때 그것은 결함이 아니라 **귀속 불가능한 돈의 크기**다 — 숨기지 않는다.
    #     (크기 감각은 아래 `ad_no_sales` 주석에 창·시점과 함께 있다.)
    ctx = day_option_atoms(db, date_from, date_to, vendor)

    # ★★판매분석 미수집 구간을 **0으로 접지 않는다**(2026-08-06 적대 리뷰 P1).
    #   `coupang_rocket_sales_daily`는 쿠팡 롤링창(약 2개월) 때문에 과거가 없다. 그 창에서
    #   `compute_rocket_1p_summary_row(BASIS_SALES)`는 매출 "0"을 돌려주고, 예전 판은 그것을
    #   그대로 실었다 — 화면에 「우리 매출 0원 · RoAS 0.00」이 떠서 "광고비 2,941만원 쓰고
    #   매출이 0이었다"로 읽혔다. 실제로는 0이 아니라 **관측 불가**다.
    #   ★판별자는 qty가 아니라 **행 존재 여부**다: 행이 있고 qty=0이면 그건 진짜 0판매일이다.
    covered = len(ctx["atoms"]) > 0
    sales_span = db.execute(
        text("SELECT MIN(date), MAX(date) FROM coupang_rocket_sales_daily WHERE vendor_id = :vendor"),
        {"vendor": vendor},
    ).fetchone()
    ad_by_day_option = ctx["ad_by_day_option"]
    ad_by_option = ctx["ad_by_option"]
    # ★분담금을 **모르면** 손익 자체가 없다(원자가 그렇게 판정한다) — 화면·배지가 그 이유를
    #   댈 수 있게 여기까지 들고 온다. 0으로 접으면 그만큼 이익이 부풀어 보인다.
    burden_known = ctx["burden_known"]
    # 원가가 안 붙는 **이유**를 SKU별로 가른다(위 `_COST_MAP_STATE_SQL` 주석 참조).
    cost_map_state = {
        str(r[0]): (r[1], bool(r[2]), r[3])
        for r in db.execute(text(_COST_MAP_STATE_SQL)).fetchall()
    }

    first_sold = {
        str(r[0]): str(r[1])[:10]
        for r in db.execute(text(_FIRST_SOLD_SQL), {"vendor": vendor}).fetchall() if r[1]
    }
    first_po = {
        str(r[0]): str(r[1])[:10]
        for r in db.execute(text(_FIRST_PO_SQL), {"vendor": vendor}).fetchall() if r[1]
    }
    obs_from = None if not sales_span or sales_span[0] is None else str(sales_span[0])[:10]

    def _uncosted_reason(sku: str | None) -> str:
        st = cost_map_state.get(str(sku)) if sku is not None else None
        if st is None:
            return "no_link"          # 다리 자체가 없다 — 원가를 넣어도 안 붙는다
        if st[0] == "ignored":
            return "excluded"         # 이미 "제외"로 결정 — 시키면 안 된다
        return "no_cost"              # 다리는 있는데 그 내부 SKU에 원가가 없다

    def _excluded_note(sku: str | None) -> str | None:
        """`ignored`로 찍힐 때 남은 사유 문자열. **없으면 None**(모름이지 «사유 없음»이 아니다).

        ★왜 싣나(2026-08-09, Jino 실사고): 화면이 `ignored`를 통째로 «샘플·증정으로 이미
          결정한 것»이라 말하고 "그냥 두세요"로 안내했는데, prod 22건이 **전부**
          `note='no suggestion or low score'`였다 — 즉 «결정»이 아니라 **이름 유사도 매칭이
          실패한 것**을 같은 칸에 넣어 둔 것이다(ref 47 §2가 같은 결론을 이미 적었다).
          두 의미가 한 status에 섞여 있는 한, 화면이 할 수 있는 정직한 일은 **기록된 사유를
          그대로 보여 주고 판단을 사람에게 돌려주는 것**이다. 사유 문자열을 파싱해 «이건
          매칭 실패다»라고 화면이 단정하면, 그건 또 하나의 추측을 코드에 굳히는 것이다.
        """
        st = cost_map_state.get(str(sku)) if sku is not None else None
        return None if st is None else st[2]

    # ── 「날짜×옵션」 원자를 한 번 돌면서 **두 방향으로 접는다** ────────────
    #   opt_acc = 옵션별(표) · day_acc = 날짜별(일별 손익). 반올림은 원자에서 한 번뿐이라
    #   두 합계와 기간 타일이 원 단위까지 정확히 같다.
    opt_acc: dict[str, dict] = {}
    day_acc: dict[str, dict] = {}
    consumer_total = ZERO
    qty_total = 0
    priced_qty = 0
    pnl_qty = 0
    pnl_revenue = pnl_cost = pnl_burden = pnl_ad = pnl_net = ZERO
    # ★`costed_*`는 **분담금과 무관하게** 원가가 붙은 매출이다. `pnl_*`와 갈라둔 이유:
    #   분담금을 모르면 pnl_*가 0이 되는데, 예전 판은 그걸로 원가 커버리지를 내서 원가가
    #   100% 등록돼 있어도 화면에 「원가 확인 0.0%분만」이 떴다(적대 리뷰 P1). 커버리지는
    #   원가에 대한 사실이지 분담금 수집 상태에 대한 사실이 아니다.
    costed_revenue = ZERO
    uncosted: dict[str, dict] = {}
    # ★광고 원장을 **네 통으로 남김없이** 가르기 위한 열쇠 모음(2026-08-09).
    #   원자 하나 = (날짜, 옵션) 하나이고 `_DAY_OPTION_SQL`이 그 그레인으로 GROUP BY 하므로
    #   `atom_keys`는 광고 맵 `ad_by_day_option`의 **부분집합 키**가 된다. 남은 키가 곧
    #   「귀속할 원자가 없는 광고비」이고, 그것을 다시 «옵션은 팔렸나»로 두 통으로 가른다.
    atom_keys: set[tuple[str, str]] = set()
    # 구멍1 — 판매행은 있는데 손익에 못 들어간 원자(원가·납품단가·분담금 미상)의 광고비.
    ad_uncosted = ZERO

    def _blank(**extra) -> dict:
        base = {"qty": 0, "consumer_revenue": ZERO, "our_revenue": ZERO, "cost": ZERO,
                "promo_burden": ZERO, "ad_spend": ZERO, "net_profit": ZERO,
                # ★★손익 축은 **부분집합 전용 칸**에 따로 쌓는다(2026-08-07 라이브 결함).
                #   한 칸에 섞으면 날짜 행에서 「원가·분담금·광고비는 그날 전량, 매출·순이익은
                #   원가 확인분」이 되어 행 산술이 안 맞고, 잔차로 낸 부가세가 그 차이를
                #   흡수해 **음수로 떴다**. 옵션 행은 SKU가 하나라 안 갈라지지만 날짜 행은
                #   여러 옵션이 섞이므로 갈라진다.
                "pnl_qty": 0, "pnl_cost": ZERO, "pnl_burden": ZERO, "pnl_ad": ZERO,
                # 손익에 못 들어간 원자의 광고비(구멍1) — pnl_ad와 **합집합이 아니라 여집합**이다.
                "ad_uncosted": ZERO,
                # ★«모름»을 0으로 접지 않으려고 «관측된 것이 있는가»를 따로 센다.
                "priced_revenue": ZERO, "costed_revenue": ZERO,
                "has_priced": False, "has_costed": False, "has_uncosted": False,
                "has_unpriced": False, "has_ad": False, "pnl_rows": 0}
        base.update(extra)
        return base

    for a in ctx["atoms"]:
        day_key, oid = a["date"], a["option_id"]
        sku_id, product_name = a["sku_id"], a["product_name"]
        q = a["qty"]
        consumer_d, ours_d, cost_d = a["consumer_revenue"], a["our_revenue"], a["cost"]
        unit_price, unit_cost, visitors = a["unit_price"], a["unit_cost"], a["visitors"]
        ad, ad_for_pnl, burden = a["ad_spend"], a["ad_for_pnl"], a["promo_burden"]
        net, net_upper = a["net_profit"], a["net_profit_upper"]
        consumer_total += consumer_d
        qty_total += q
        atom_keys.add((day_key, oid))
        if ours_d is not None:
            priced_qty += q

        # net is not None ⟺ 매출·원가·분담금 셋 다 앎 — 종전 인라인 조건과 동치다.
        if net is not None:
            pnl_qty += q
            pnl_revenue += ours_d
            pnl_cost += cost_d
            pnl_burden += burden
            pnl_ad += ad_for_pnl
            pnl_net += net
        else:
            # ★★구멍1 — 이 돈은 예전 판에서 **어느 줄에도 나타나지 않았다.** 손익에 못 들어간
            #   원자의 광고비라 `pnl_ad`에서 빠지는데, 옵션은 팔렸으므로 `ad_no_sales`에도
            #   안 잡혀 화면에서 통째로 증발했다(라이브 2026-08-08 하루 33,750원 = 계정
            #   총액의 4.2%). 각주가 "차이의 대부분은 부분집합 제한"이라고 **말로만** 설명하던
            #   그 돈이다 — 말이 아니라 금액으로 적는다.
            ad_uncosted += ad_for_pnl
        if ours_d is not None and cost_d is not None:
            costed_revenue += ours_d
        elif cost_d is None:
            # 원가를 못 붙인 SKU — 무엇을 해야 하는지 화면이 말할 수 있게 SKU로 묶어 모은다.
            key = str(sku_id) if sku_id is not None else f"option:{oid}"
            u = uncosted.setdefault(key, {
                "sku_id": sku_id, "product_name": product_name,
                "qty": 0, "our_revenue": ZERO, "consumer_revenue": ZERO,
                "our_revenue_known": True, "loss_confirmed": False,
                # ★무엇을 해야 하는지가 셋으로 갈린다 — 틀리면 사용자가 헛일을 한다.
                "reason": _uncosted_reason(sku_id),
                # ★"이번에 새로 팔리기 시작했는가" — 신규는 나오자마자 매출 1위가 된다.
                "first_sold_at": first_sold.get(str(sku_id)),
                "first_po_at": first_po.get(str(sku_id)),
            })
            u["qty"] += q
            u["consumer_revenue"] += consumer_d
            if net_upper is not None:
                u["loss_confirmed"] = True
            if ours_d is None:
                u["our_revenue_known"] = False
            else:
                u["our_revenue"] += ours_d

        # ── 두 방향으로 접는다 ──────────────────────────────────────
        o = opt_acc.setdefault(oid, _blank(
            option_id=oid, sku_id=sku_id, product_name=product_name,
            unit_price=None, unit_cost=None, visitors=None, net_upper=ZERO,
            has_net_upper=False))
        dd = day_acc.setdefault(day_key, _blank(date=day_key))
        for acc in (o, dd):
            acc["qty"] += q
            acc["consumer_revenue"] += consumer_d
            if ours_d is not None:
                acc["our_revenue"] += ours_d
                acc["priced_revenue"] += ours_d
                acc["has_priced"] = True
            else:
                acc["has_unpriced"] = True
            if cost_d is not None:
                acc["cost"] += cost_d
                acc["has_costed"] = True
                if ours_d is not None:
                    acc["costed_revenue"] += ours_d
            else:
                acc["has_uncosted"] = True
            if ad is not None:
                acc["ad_spend"] += ad
                acc["has_ad"] = True
            if burden is not None:
                acc["promo_burden"] += burden
            if net is not None:
                acc["net_profit"] += net
                acc["pnl_rows"] += 1
                acc["pnl_qty"] += q
                acc["pnl_cost"] += cost_d
                acc["pnl_burden"] += burden
                acc["pnl_ad"] += ad_for_pnl
            else:
                acc["ad_uncosted"] += ad_for_pnl
        if unit_price is not None:
            o["unit_price"] = unit_price
        if unit_cost is not None:
            o["unit_cost"] = unit_cost
        if visitors is not None:
            o["visitors"] = int(visitors) + (o["visitors"] or 0)
        if net_upper is not None:
            o["net_upper"] += net_upper
            o["has_net_upper"] = True

    options: list[dict] = []
    for o in opt_acc.values():
        ours_d = o["our_revenue"] if o["has_priced"] else None
        cost_d = o["cost"] if o["has_costed"] else None
        ad = o["ad_spend"] if o["has_ad"] else None
        burden = None if not burden_known else o["promo_burden"]
        net = o["net_profit"] if o["pnl_rows"] else None
        net_upper = o["net_upper"] if o["has_net_upper"] else None
        consumer_d = o["consumer_revenue"]
        q = o["qty"]
        option_id, sku_id, product_name = o["option_id"], o["sku_id"], o["product_name"]
        unit_price, unit_cost, visitors = o["unit_price"], o["unit_cost"], o["visitors"]
        options.append({
            "option_id": str(option_id),
            "sku_id": sku_id,
            "product_name": product_name,
            "qty": q,
            "consumer_revenue": str(consumer_d),
            # ★납품단가를 못 붙인 옵션은 None — 화면이 "—"로 그린다(0으로 접지 않는다).
            "our_revenue": None if ours_d is None else str(ours_d),
            "unit_price": None if unit_price is None else str(unit_price),
            "visitors": None if visitors is None else int(visitors),
            # 우리 몫 비율 = 납품가 ÷ 소비자가. 쿠팡이 얹은 마진의 뒷면이다.
            "our_share": _s(_ratio(ours_d, consumer_d)),
            "ad_spend": None if ad is None else str(ad),
            # ★RoAS는 **우리 매출 기준**이다. 소비자 매출로 내면 우리가 못 번 돈으로 광고를
            #   정당화하게 된다(1P에서 소비자가는 쿠팡의 매출이다).
            "roas": _s(_ratio(ours_d, ad)),
            # ★손익분기 RoAS = 매출 ÷ (매출 − 원가 − 분담금). **판정 기준이지 권고가 아니다.**
            #   유도: net = (매출−원가−분담금−광고비) × 100/110 이므로 net=0 ⟺ 광고비 =
            #   매출−원가−분담금. VAT는 선형이라 분기점에서 상쇄된다(양변에 같은 비율).
            #   ★왜 필요한가: RoAS 열만 있으면 "1보다 크니 괜찮다"로 읽힌다. 라이브 실측에
            #   RoAS 1.08인데 이익률 −22.1%인 행이 있었다 — 기준선이 없으면 알 수 없다.
            #   공헌이익이 0 이하면 어떤 RoAS로도 흑자가 안 되므로 None(수치가 아니라 상태다).
            "bep_roas": _s(_bep_roas(ours_d, cost_d, burden)),
            # ── 손익 ── 원가 미상이면 cost·net 모두 None이다(0이 아니다).
            "cost": None if cost_d is None else str(cost_d),
            "unit_cost": None if unit_cost is None else str(unit_cost),
            "promo_burden": None if burden is None else str(burden),
            "net_profit": None if net is None else str(net),
            "profit_rate": _s(_ratio(net, ours_d)),
            # 원가 미상이지만 **원가가 얼마든 적자**인 경우의 상한(음수일 때만 싣는다).
            "net_profit_upper": None if net_upper is None else str(net_upper),
        })

    options.sort(key=lambda o: Decimal(o["consumer_revenue"]), reverse=True)
    shown = options[: max(1, limit)]

    # ── 손익 블록 ─────────────────────────────────────────────────
    # ★★이 순이익은 **원가를 붙인 부분집합**의 것이다(pnl.basis 참조). 왜 전체로 안 내는가:
    #   원가 미상 SKU를 0원 원가로 넣으면 그 매출이 통째로 이익이 되어 **이익이 부풀어 보인다**
    #   (라이브 2026-08-06: 매출의 51%가 원가 미상이었다). 그래서 매출·원가·분담금·광고비를
    #   **전부 같은 부분집합으로 제한**한다 — 축이 섞이지 않아 이익률이 그 부분집합에선 참이다.
    #   커버리지가 100%면 부분집합 = 전체이고, 그때만 이 값이 창 전체의 손익이다.
    # ★합계는 **반올림된 옵션 행들의 합**이다 — 화면에서 표와 타일이 어긋나면 사용자는
    #   둘 다 안 믿는다(`_money` 참조. 예전 주석의 "반올림 없음"은 틀렸다).
    priced_revenue = sum(
        (Decimal(o["our_revenue"]) for o in options if o["our_revenue"] is not None), ZERO
    )
    # 납품단가를 못 붙인 옵션 수. 원가와 **다른 결손**이라 basis 판정에 따로 들어간다 —
    # 이 옵션들은 손익에서도 빠지고 원가 작업 목록에도 안 잡히기 때문이다(유일한 사각).
    coverage_unpriced = sum(1 for o in options if o["our_revenue"] is None)
    costed_options = sum(1 for o in options if o["cost"] is not None)
    # ★★그 창에 **판매행이 없는 옵션의 광고비**(적대 리뷰 P1). 예전 판은 판매행을 돌면서만
    #   광고비를 소비해서 이 돈이 손익에 **한 번도 들어가지 않았다** — 라이브 실측 8/1~7에
    #   광고 옵션 418개 중 295개가 그랬고 282,794원이었다(창 2026-08-01~08-07. 같은 날 22:47
    #   라이브에서 재확인했고, 창을 07-31~08-06으로 하루 밀면 253,091원이다 — **창이 다르면
    #   값이 다르므로 이 지표는 창을 밝히지 않고 인용하면 안 된다**).
    #   커버리지가 100%가 돼서 basis가
    #   'full'("기간 전체")이 돼도 영영 안 들어오므로, **의도된 종착 상태에 거짓말이 남는다**.
    #   재현에선 실제 −181,818원이 +272,727원으로 **부호가 뒤집혔다**.
    #   ★귀속 불가능한 돈이라 부분집합 손익에는 섞지 않고, 'full'일 때만 차감한다.
    #     어느 쪽이든 **항상 금액을 싣는다** — 안 보이면 없는 돈이 된다.
    sold_option_ids = set(opt_acc)
    ad_no_sales = sum(
        (v for k, v in ad_by_option.items() if k not in sold_option_ids), ZERO
    )
    # ★★구멍2 — **팔린 옵션이 «안 팔린 날»에 쓴 광고비**(2026-08-09에 배선. 발견 자체는
    #   2026-08-07 근거 화면 A7이었고, 그때는 "관측된 결손"으로 fail만 띄우고 엔진은 안 고쳤다).
    #   왜 어느 통에도 안 들어갔나: 원자는 판매행에서만 나오므로 그 날엔 원자가 없고,
    #   `ad_no_sales`는 «옵션 단위»로 판정해서 그 옵션은 (다른 날 팔렸으므로) 제외된다.
    #   즉 판정 그레인이 하나는 (날짜×옵션), 하나는 (옵션)이라 그 사이로 돈이 샜다.
    #   ★크기: 창 2026-07-31~08-06에 435,916원. **하루짜리 창에서는 구조적으로 0**이므로
    #     (판매행 없는 날 = 그 옵션이 sold_option_ids에 없는 날) 하루로 재보고 «없다»고
    #     결론내면 안 된다 — 창을 밝히지 않고 인용하지 말 것.
    ad_no_sales_days = sum(
        (v for (dk, oid), v in ad_by_day_option.items()
         if oid in sold_option_ids and (dk, oid) not in atom_keys), ZERO
    )
    # ★네 통의 합 = 옵션 광고 원장 전액. 이 항등식이 이 블록의 존재 이유다 — 어느 통도
    #   «남는 것»이 아니라 이름과 금액을 가진다. (테스트가 전건 대조로 지킨다.)
    ad_unattributed = ad_uncosted + ad_no_sales_days + ad_no_sales

    cost_coverage = _ratio(costed_revenue, priced_revenue) if priced_revenue > ZERO else None
    uncosted_rows = sorted(
        uncosted.values(),
        # ★known과 unknown을 **한 키로 섞지 않는다** — 소비자가가 통상 1.6배 커서 섞으면
        #   단가 미상 SKU가 부당하게 위로 올라온다. known을 먼저, 그 안에서 금액 내림차순.
        key=lambda u: (u["our_revenue_known"],
                       u["our_revenue"] if u["our_revenue_known"] else u["consumer_revenue"]),
        reverse=True,
    )
    # 「최근에 나온 상품」의 지평. ★조회 창 길이와 **분리한다**(2026-08-07 라이브 교정):
    #   창 안에서만 보면 기본 7일 화면에서 3주 전 출시한 신제품이 안 잡히고, 창을 넓히면
    #   "그 창에서 처음 관측"이 무더기로 신규가 된다. 지평은 창이 아니라 **출시 시점**이다.
    #   숨은 기준을 두지 않으려고 응답에 그대로 싣는다.
    new_sku_days = int(os.getenv("ROCKET_1P_NEW_SKU_DAYS") or "90")

    def _newness(u: dict) -> dict:
        """이 SKU가 **최근에 새로 나온 것**인가.

        ★판별자는 **발주에 처음 등장한 날**이다. 판매분석의 첫 관측일이 아니다 —
          "안 팔리던 게 이제 팔린다"와 "새로 나왔다"는 다른 일이고, 전자는 재노출·시즌이라
          매핑이 급하지 않다. 라이브 교정(2026-08-07): 발주 2025-07-25짜리가 판매 첫 관측만
          창 안이라는 이유로 「신규」로 잡혔다. 발주 이력은 전 범위(2025-07~)라 이 판정에
          쓸 수 있는 유일한 축이다.
        ★발주 이력이 아예 없을 때만 판매 축으로 떨어진다. 그때도 판매분석 롤링창(약 2개월)
          시작일과 첫 관측일이 같으면 그 전은 **모르므로** 단정하지 않는다.
        """
        fs, fp = u["first_sold_at"], u["first_po_at"]
        horizon = (date_to - timedelta(days=new_sku_days)).isoformat()
        bounded = fs is not None and obs_from is not None and fs <= obs_from
        if fp is not None:
            is_new = fp >= horizon
        else:
            is_new = fs is not None and fs >= horizon and not bounded
        return {
            "first_sold_at": fs,
            "first_po_at": fp,
            "is_new": bool(is_new),
            "first_sold_at_bounded": bool(bounded),
        }

    for u in uncosted_rows:
        u.update(_newness(u))
    actionable = [u for u in uncosted_rows if u["reason"] != "excluded"]
    # ★신규를 **먼저** 보인다 — 매출 순으로만 두면 옛 꼬리에 묻힌다(그게 지금 상태였다).
    actionable.sort(key=lambda u: (
        u["is_new"],
        u["our_revenue"] if u["our_revenue_known"] else u["consumer_revenue"],
    ), reverse=True)

    # ★basis 판정을 **반올림된 비율이 아니라 구조**로 한다(적대 리뷰 P2). `_ratio`는 소수
    #   4자리로 quantize하므로 0.99996도 1.0000이 되어, 원가 미상 SKU가 남아 있는데 배지가
    #   「원가 확인 100% · 기간 전체」로 뜨고 그 아래 「원가 미등록 SKU N개」가 동시에 떴다.
    is_full = not uncosted_rows and coverage_unpriced == 0
    include_no_sales = is_full
    # ★is_full이면 **귀속 불가능한 두 통을 다 넣는다**(2026-08-09 교정). 예전 판은 구멍3만
    #   넣어서, 커버리지가 100%가 되어 basis='full'("기간 전체")이 돼도 구멍2가 여전히 빠졌다 —
    #   «의도된 종착 상태에 거짓말이 남는다»는 같은 결함이 한 겹 아래에 또 있었던 것이다.
    #   ★구멍1(ad_uncosted)은 여기 안 들어간다. is_full이면 정의상 0이고(원가 미상 원자가
    #     없어야 is_full이다), 0이 아닌 상태에서 넣으면 **부분집합 매출에 전량 광고비를**
    #     빼는 것이 되어 손익이 위조된다.
    ad_folded = ad_no_sales_days + ad_no_sales
    pnl_ad_total = pnl_ad + (ad_folded if include_no_sales else ZERO)
    # 귀속 불가 광고비도 매입세액공제 대상이라 세후로 차감한다(payable_vat와 같은 축).
    pnl_net_total = pnl_net - (
        _money(ad_folded * Decimal("100") / Decimal("110")) if include_no_sales else ZERO
    )
    pnl_vat = pnl_revenue - pnl_cost - pnl_burden - pnl_ad_total - pnl_net_total

    # ── 일별 손익 (Jino 2026-08-07: "일일 손익을 보고 싶은거야") ─────────
    # ★날짜별로도 **원가 확인분만** 더한다 — 창 합계와 같은 규칙이다. 날마다 팔린 SKU가
    #   달라 커버리지가 들쭉날쭉하므로 **일별 커버리지를 함께 낸다**: 그게 없으면 어떤 날
    #   이익률이 왜 튀는지 알 수 없고, 낮은 커버리지의 날을 전체인 줄 알고 읽게 된다.
    # ★일별 순이익의 합 = 옵션별 순이익의 합 = 기간 타일. 셋 다 같은 「날짜×옵션」 원자를
    #   접은 것이라 반올림까지 일치한다(따로 계산하지 않는다).
    daily = []
    for day_key in sorted(day_acc):
        v = day_acc[day_key]
        d_priced = v["priced_revenue"] if v["has_priced"] else None
        d_net = v["net_profit"] if v["pnl_rows"] else None
        d_cov = _ratio(v["costed_revenue"], v["priced_revenue"]) if v["priced_revenue"] > ZERO else None
        d_ad_no_sales = sum(
            (amt for (dk, oid), amt in ad_by_day_option.items()
             if dk == day_key and oid not in sold_option_ids), ZERO
        )
        # 그날 몫의 구멍1·구멍2 — 창 합계와 **같은 판정자**로 가른다(다르면 일별 합 ≠ 기간 합).
        d_ad_no_sales_days = sum(
            (amt for (dk, oid), amt in ad_by_day_option.items()
             if dk == day_key and oid in sold_option_ids and (dk, oid) not in atom_keys), ZERO
        )
        d_ad_uncosted = v["ad_uncosted"]
        # ★부가세는 **부분집합 축끼리** 잔차로 낸다 — 전량 값과 섞으면 음수가 나온다.
        d_vat = (v["costed_revenue"] - v["pnl_cost"] - v["pnl_burden"]
                 - v["pnl_ad"] - d_net) if d_net is not None else None
        daily.append({
            "date": day_key,
            "qty": v["qty"],
            "consumer_revenue": str(v["consumer_revenue"]),
            "our_revenue": None if d_priced is None else str(d_priced),
            # 그날 **전량** 광고비(Billboard) — 아래 손익 축의 광고비와 다르다.
            "ad_spend_all": str(v["ad_spend"]) if v["has_ad"] else "0",
            # ★여기부터는 전부 **원가 확인분** 축이다 — 위 our_revenue(전량)와 분모가 다르다.
            "pnl_revenue": str(v["costed_revenue"]) if d_net is not None else None,
            "pnl_qty": v["pnl_qty"] if d_net is not None else None,
            "cost": str(v["pnl_cost"]) if d_net is not None else None,
            "promo_burden": str(v["pnl_burden"]) if d_net is not None else None,
            "ad_spend": str(v["pnl_ad"]) if d_net is not None else None,
            # 그날 광고는 돌았는데 판매행이 없는 옵션의 광고비(위 순이익에 미포함).
            "ad_no_sales": str(d_ad_no_sales),
            # 그날 팔린 옵션이되 **그날은 판매행이 없던** 옵션의 광고비(구멍2). 하루 창에선 0.
            "ad_no_sales_days": str(d_ad_no_sales_days),
            # 그날 팔렸지만 손익에 못 들어간 옵션의 광고비(구멍1).
            "ad_uncosted": str(d_ad_uncosted),
            "vat": None if d_vat is None else str(d_vat),
            "net_profit": None if d_net is None else str(d_net),
            "profit_rate": _s(_ratio(d_net, v["costed_revenue"])) if d_net is not None else None,
            "cost_coverage": _s(d_cov),
        })

    # ★손익이 안 나오는 이유를 **backend가 정한다**. 예전엔 화면이 추측했는데, 우선순위가
    #   조금만 틀려도 사용자를 엉뚱한 작업으로 보냈다(원가는 다 있는데 "원가를 등록하세요").
    blocked = None
    if not covered:
        blocked = ("sales_not_covered",
                   "이 기간엔 쿠팡 판매분석 수집분이 없습니다 — 판매가 0이었던 게 아니라 관측 불가입니다.")
    elif not burden_known:
        blocked = ("promo_burden_unknown",
                   "이 기간에 걸친 프로모션의 할인액 원천(제안서)이 아직 없습니다. 분담금을 0으로 "
                   "놓으면 그만큼 이익이 부풀어 보이므로 손익을 내지 않습니다. 원가를 등록해도 "
                   "이 상태는 풀리지 않습니다.")
    elif priced_revenue <= ZERO:
        blocked = ("no_priced_sku",
                   "이 기간에 팔린 옵션 중 납품단가를 붙일 수 있는 것이 없습니다 — 원가가 아니라 "
                   "발주 라인(납품단가)이 없는 상태입니다.")
    elif costed_options == 0:
        blocked = ("no_costed_sku",
                   "이 기간에 팔린 SKU 중 원가가 붙은 것이 하나도 없습니다. 아래 목록이 "
                   "SKU마다 무엇이 빠졌는지(연결 / 원가 등록) 말해 줍니다 — 원가가 이미 "
                   "있어도 쿠팡 상품번호와 내부 SKU가 연결돼 있지 않으면 붙지 않습니다.")
    elif pnl_revenue <= ZERO:
        blocked = ("costed_revenue_zero",
                   "원가가 붙은 옵션은 있지만 그 매출 합이 0입니다(판매수량 0).")
    has_pnl = blocked is None

    pnl_block = {
        # costed_subset = 원가 확인분만 / full = 전량(원가·납품단가 결손 0)
        "basis": None if not has_pnl else ("full" if is_full else "costed_subset"),
        "qty": pnl_qty if has_pnl else None,
        "revenue": _s(pnl_revenue) if has_pnl else None,
        "cost": _s(pnl_cost) if has_pnl else None,
        "promo_burden": _s(pnl_burden) if has_pnl else None,
        "ad_spend": _s(pnl_ad_total) if has_pnl else None,
        "vat": _s(pnl_vat) if has_pnl else None,
        "net_profit": _s(pnl_net_total) if has_pnl else None,
        "profit_rate": _s(_ratio(pnl_net_total, pnl_revenue)) if has_pnl else None,
        # ── 광고 원장을 네 통으로 남김없이 가른 것 ────────────────────────
        #   ad_spend(=pnl_ad_total) 안의 «귀속분» + 아래 셋 = ad_option_total.
        #   ★셋을 다 싣는 이유: 안 보이는 통이 하나라도 있으면 그 돈은 화면에서 없는 돈이
        #     된다. 실제로 구멍1은 예전 판에서 어느 줄에도 없었고, 각주가 «차이의 대부분»
        #     이라는 말로 대신하고 있었다.
        # ★그 창에 안 팔린 옵션의 광고비(구멍3). included=False면 위 순이익에 **안 들어있다**.
        "ad_no_sales": str(ad_no_sales),
        # ★팔린 옵션이 «안 팔린 날»에 쓴 광고비(구멍2). included 플래그를 ad_no_sales와 공유한다.
        "ad_no_sales_days": str(ad_no_sales_days),
        # ★판매행은 있으나 손익 부분집합에 못 들어간 옵션의 광고비(구멍1).
        #   **is_full이면 정의상 0**이고, 0이 아닐 땐 순이익에 절대 넣지 않는다(위 주석).
        "ad_uncosted": str(ad_uncosted),
        # 위 셋의 합 — 「계정 총액과 사다리가 왜 다른가」의 답 전체.
        "ad_unattributed": str(ad_unattributed),
        # ad_no_sales·ad_no_sales_days 둘의 포함 여부. ad_uncosted는 포함 대상이 아니다.
        # ★`has_pnl`을 **곱한다**(2026-08-09 실측 교정): `is_full`은 «결손 0»이라 판매분석
        #   미수집 창에서도 참이 된다(원자가 없으면 미상 SKU도 0건이다). 그 창은 사다리 자체가
        #   없는데(blocked='sales_not_covered') 플래그만 true라 «광고비가 순이익에 이미
        #   반영됐다»고 말하고 있었다 — 라이브 창 2026-05-01~07에서 3,421,987원이 그 상태였다.
        #   없는 사다리에 무언가가 «포함됐다»고 말할 수는 없다.
        "ad_no_sales_included": include_no_sales and has_pnl,
        # 화면이 "왜 사다리 광고비가 계정 총액과 다른가"에 **맞는 이유**를 댈 수 있게 함께 싣는다.
        "ad_account_total": str(ad_spend),
        "ad_option_total": str(sum(ad_by_option.values(), ZERO)),
        # ★원가를 붙인 매출 ÷ 납품단가를 붙인 매출. **분담금 수집 상태와 무관**하다.
        "cost_coverage": _s(cost_coverage) if covered else None,
        "revenue_priced": _s(priced_revenue) if covered else None,
        # ★분담금을 모르면 손익 자체가 없다 — 화면이 "왜 안 나오는지" 말할 수 있게 이유를 싣는다.
        "promo_burden_known": burden_known,
        "blocked": None if blocked is None else {"code": blocked[0], "reason": blocked[1]},
        "uncosted": {
            "skus": len(uncosted_rows),
            "qty": sum(u["qty"] for u in uncosted_rows),
            # ★known분만 더한다. 단가까지 모르는 SKU를 0원으로 더하면 합계가 **작게** 보인다
            #   — 이 파일이 반복해 선언한 "0으로 접지 않는다"의 예외가 되어 있었다.
            "our_revenue": str(sum((u["our_revenue"] for u in uncosted_rows
                                    if u["our_revenue_known"]), ZERO)),
            "our_revenue_partial": any(not u["our_revenue_known"] for u in uncosted_rows),
            # 원가 등록으로 해소되는 것만 센다. `ignored`는 이미 "제외"로 결정된 SKU라 빼면
            # 안 되는 게 아니라 **넣으면 안 된다**(재제안 방지가 그 상태의 존재 이유다).
            "actionable_skus": len(actionable),
            # ★할 일별로 센다. 「원가 등록」 하나로 뭉뚱그리면 사용자가 헛일을 한다 —
            #   실제로 그렇게 됐다(2026-08-07: 원가를 등록했는데 화면이 안 움직였다).
            "link_missing_skus": sum(1 for u in uncosted_rows if u["reason"] == "no_link"),
            "cost_missing_skus": sum(1 for u in uncosted_rows if u["reason"] == "no_cost"),
            "excluded_skus": sum(1 for u in uncosted_rows if u["reason"] == "excluded"),
            "loss_confirmed_skus": sum(1 for u in uncosted_rows if u["loss_confirmed"]),
            # ★이번 창에서 **처음 팔리기 시작한** 미연결 SKU. 새 폰이 나올 때마다 생긴다.
            "new_skus": sum(1 for u in actionable if u["is_new"]),
            "new_our_revenue": str(sum((u["our_revenue"] for u in actionable
                                        if u["is_new"] and u["our_revenue_known"]), ZERO)),
            # ★판정에 쓴 지평을 숨기지 않는다 — 기준이 안 보이면 화면을 믿을 근거가 없다.
            "new_sku_window_days": new_sku_days,
            # ★「원가 제외」로 이미 결정된 SKU도 **이름으로 보인다**(2026-08-07, Jino).
            #   개수만 세면 "그중 실제로는 원가가 있어야 할 게 섞였나"를 확인할 방법이 없다.
            #   작업 목록(top)과는 **분리**한다 — 여기 있는 건 시키는 게 아니라 재검토 대상이다.
            "excluded_top": [
                {
                    "sku_id": u["sku_id"],
                    "product_name": u["product_name"],
                    "qty": u["qty"],
                    "our_revenue": str(u["our_revenue"]) if u["our_revenue_known"] else None,
                    "consumer_revenue": str(u["consumer_revenue"]),
                    # ★제외로 찍을 때 남은 사유 원문. None이면 «사유가 기록되지 않았다»다.
                    #   화면이 이걸 그대로 보여야 «샘플·증정 결정»과 «매칭 실패»를 사람이 가른다.
                    "excluded_note": _excluded_note(u["sku_id"]),
                }
                for u in sorted(
                    (u for u in uncosted_rows if u["reason"] == "excluded"),
                    key=lambda u: (u["our_revenue_known"],
                                   u["our_revenue"] if u["our_revenue_known"]
                                   else u["consumer_revenue"]),
                    reverse=True,
                )[:_UNCOSTED_TOP_N]
            ],
            "excluded_our_revenue": str(sum(
                (u["our_revenue"] for u in uncosted_rows
                 if u["reason"] == "excluded" and u["our_revenue_known"]), ZERO)),
            "top": [
                {
                    "sku_id": u["sku_id"],
                    "product_name": u["product_name"],
                    "qty": u["qty"],
                    # 납품단가까지 모르면 우리 매출도 모른다 — 0으로 접지 않는다.
                    "our_revenue": str(u["our_revenue"]) if u["our_revenue_known"] else None,
                    "consumer_revenue": str(u["consumer_revenue"]),
                    # 원가가 얼마든 적자인 SKU(광고비가 매출을 넘었다).
                    "loss_confirmed": u["loss_confirmed"],
                    # no_link | no_cost | excluded — 무엇을 해야 하는지.
                    "reason": u["reason"],
                    # ★이번 창에서 처음 팔리기 시작했나 + 그 근거 날짜.
                    "is_new": u["is_new"],
                    "first_sold_at": u["first_sold_at"],
                    "first_po_at": u["first_po_at"],
                    "first_sold_at_bounded": u["first_sold_at_bounded"],
                }
                for u in actionable[:_UNCOSTED_TOP_N]
            ],
        },
        "note": "순이익 = 우리 매출(납품가) − 원가 − 프로모션 분담금 − 광고비 − 납부세액. "
                "basis='costed_subset'이면 **원가를 붙인 옵션만** 더한 값이라 창 전체의 "
                "손익이 아니고, 그 부분집합은 무작위가 아니다(매출·광고비 큰 SKU가 통째로 빠질 "
                "수 있다) — **이익률을 곱해 전체를 추정하면 안 된다**. 광고비는 옵션 "
                "그레인(Billboard)이고 원장 전액은 ad_option_total이며, 그중 위 순이익에 "
                "들어간 몫이 ad_spend다. 나머지는 ad_uncosted(판매O·손익밖) + "
                "ad_no_sales_days(팔린 옵션의 판매 없는 날) + ad_no_sales(판매행 없는 옵션) "
                "셋으로 남김없이 갈라져 있고 그 합이 ad_unattributed다. "
                "ad_no_sales_included=true면 뒤의 둘은 이미 순이익에서 빠졌다.",
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
            "options_unpriced": coverage_unpriced,
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
        # ★일별 손익 — 「날짜×옵션」 원자를 날짜로 접은 것. 옵션별 표와 **같은 원자**라
        #   일별 합 = 옵션별 합 = pnl 타일이 원 단위까지 같다.
        "daily": daily,
        "freshness": _window_freshness(db, date_from, date_to, vendor),
        "option_count": len(options),
        "shown": len(shown),
        "options": shown,
        "axes_note": "소비자 매출은 쿠팡이 고객에게 판 금액(쿠팡의 매출)이고, 우리 매출은 "
                     "판매수량×납품단가다. 두 축은 더하지 않는다 — 같은 물건을 두 번 세게 된다.",
    }


def _s(v: Decimal | None) -> str | None:
    return None if v is None else str(v)
