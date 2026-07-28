# rocket_promo_pnl.py — 쿠팡 프로모션 손익 엔진 (트랙 coupang-promo-pnl, Phase 2)
#
# 무엇을 계산하나: 셀러 부담 즉시할인 프로모션이 걸린 기간의 **진짜 손익**과 **진짜 BEP ROAS**.
#   쿠팡 광고 화면의 ROAS는 분자가 **소비자가**라, 우리 매출이 **납품가**인 1P에서는 항상 부풀어
#   보인다(D-CPP-2). 여기서 계산하는 BEP ROAS는 "광고 ROAS가 몇 배 이상이어야 본전인가"를
#   납품가·원가·분담금으로 되돌려 준다 — 그 왜곡 보정이 이 지표의 존재 이유다.
#
# 단일 책임 SA + 결합(원칙18):
#   ① _promo_window        : 프로모션 초 단위 기간 → 일 단위 창(경계일 포함)
#   ② _sales_by_sku        : 창 내 옵션×일 판매 → SKU별 수량·실현매출·옵션ID
#   ③ _supply_price_by_sku : 최신 발주 단가(납품가) — grain=product_number
#   ④ _cost_by_sku         : RocketProductCostMap → product_master.cost_price (D-CPP-8)
#   ⑤ _ad_spend_window     : 옵션 귀속 광고비(가능하면) + 계정단위 합(상한 프록시)
#   결합: compute_promotion_pnl(프로모션 1건) → compute_promo_pnl_overview(전체+신선도+RG쿠폰)
#
# ★★읽기 전용·회계축 불변: 이 모듈은 net_profit·종합조망을 **한 톨도 바꾸지 않는다.**
#   기존 회계 코드에서 이 모듈을 호출하지 않으며(신규 API 전용), 여기서 쓰는 판매 revenue는
#   소비자 실현가라 1P 회계 매출(발주 납품금액)과 **절대 합산하지 않는다**(D-CPP-2).
#
# ★모르는 것은 0으로 접지 않는다(원칙22). 납품가 미상·원가 미매핑·광고비 옵션귀속 불가는
#   전부 None + 사유 라벨로 올라온다. 0으로 접으면 "원가 0원짜리 대박 상품"이 조용히 태어난다.
from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    CoupangAdOptionDaily,
    CoupangAdReport,
    CoupangCoupon,
    CoupangCouponItem,
    CoupangRocketPromotion,
    CoupangRocketPurchaseOrderItem,
    CoupangRocketSalesDaily,
    ProductMaster,
    RocketProductCostMap,
)
from app.utils.kst import kst_today

log = logging.getLogger(__name__)

_Z = Decimal("0")
_Q2 = Decimal("0.01")
_Q4 = Decimal("0.0001")

# 로켓배송(1P) 광고의 판매방식 코드 — rocket_intelligence.ROCKET_AD_SELL_TYPE와 같은 축.
ROCKET_AD_SELL_TYPE = "Retail"

# 판매분석 롤링 창 길이(일). ★실측값이지 보장값이 아니다: 2026-07-28 정찰에서 서버가
#   `viewable period [2026-06-01 ~ 2026-07-27]`(57일)을 400 본문에 실어 줬다. 서버가 창을
#   바꾸면 이 숫자는 틀린다 — 그래서 응답에 `window_days_basis`로 근거를 함께 내보내고
#   env로 덮을 수 있게 둔다. 페처는 이 상수를 쓰지 않는다(서버가 준 값을 그대로 따른다).
_WINDOW_DAYS = int(os.getenv("ROCKET_SALES_WINDOW_DAYS") or 57)

# 판매분석 구독 무료체험 종료일(D-CPP-5). 2026-07-28 라이브 실측
#   `GET /rpd/v2/supplier/subscription/detail` → freeTrialEndDate=2026.08.20.
#   이 날이 지나면 수집이 **조용히** 멈출 수 있어 D-7부터 화면에 경고를 띄운다.
_TRIAL_END = os.getenv("ROCKET_SALES_TRIAL_END") or "2026-08-20"
_TRIAL_WARN_DAYS = 7

# 판매분석은 전일까지가 확정이다(오늘 날짜는 서버 유효구간 밖 — 위 실측에서 07-28 요청이
#   클램프됐다). 그래서 "최신이어야 할 날짜" = 어제.
_EXPECTED_LAG_DAYS = 1


def _f(v) -> Decimal:
    if v is None:
        return _Z
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _q2(v: Decimal | None) -> Decimal | None:
    return None if v is None else v.quantize(_Q2)


def _ratio4(num: Decimal, den: Decimal) -> Decimal | None:
    """num/den(4자리). den이 0 이하면 None — 0으로 나누지 않고, **음수 공헌이익도 None**이다.

    ★음수 분모를 그대로 나누면 BEP ROAS가 음수로 나와 "0배만 넘으면 본전"처럼 읽힌다.
      공헌이익이 0 이하 = 광고를 한 푼도 안 써도 팔수록 손해 → 본전 ROAS는 **존재하지 않는다**.
    """
    if den <= 0:
        return None
    return (num / den).quantize(_Q4)


def _as_date(v) -> date | None:
    """date | datetime | None → date. datetime이 먼저 걸려야 한다(datetime은 date의 서브클래스)."""
    if v is None:
        return None
    if hasattr(v, "date") and not isinstance(v, date):
        return v.date()
    if isinstance(v, date) and hasattr(v, "hour"):   # datetime
        return v.date()
    return v


def _parse_date(s: str) -> date | None:
    try:
        y, m, d = (int(x) for x in s.strip().replace(".", "-").split("-"))
        return date(y, m, d)
    except (ValueError, TypeError):
        return None


# ════════════════════════════════════════════════
# ① 프로모션 창 (초 단위 → 일 단위)
# ════════════════════════════════════════════════
def _promo_window(promo: CoupangRocketPromotion) -> tuple[date, date] | None:
    """행사기간(초 단위 KST) → 판매 조인용 일 단위 창 [시작일, 종료일] (양끝 포함).

    ★근사임을 숨기지 않는다: 판매분석 그레인이 **일**이라 초 단위 경계를 표현할 수 없다.
      687878은 07-24 **00:01:00**~07-26 23:59:59라 하루의 1분이 빠지지만, 그 1분에 팔린 건도
      07-24 행에 통째로 들어 있다. 즉 경계일은 **프로모션 밖 판매를 일부 포함할 수 있다**
      (과대 방향). 응답의 `window_basis`로 항상 표기한다(원칙22).
    """
    if promo.start_at is None or promo.end_at is None:
        return None
    d1, d2 = promo.start_at.date(), promo.end_at.date()
    if d2 < d1:
        return None
    return d1, d2


# ════════════════════════════════════════════════
# ② 창 내 판매 (SKU별)
# ════════════════════════════════════════════════
def _sales_by_sku(
    db: Session, vendor_id: str | None, sku_ids: list[str], dfrom: date, dto: date
) -> dict[str, dict]:
    """창 내 coupang_rocket_sales_daily를 SKU별로 집계.

    반환 {sku_id: {qty, revenue, option_ids[], days, product_name}}.
    revenue = **소비자 실현가** 합(회계 매출 아님, D-CPP-2) — BEP ROAS 분자로만 쓴다.
    조회된 SKU가 없으면 그 키는 아예 없다(0으로 만들지 않는다 — '안 팔림'과 '수집 안 됨'을
    호출부가 구분할 수 있어야 한다).
    """
    if not sku_ids:
        return {}
    S = CoupangRocketSalesDaily
    q = db.query(
        S.sku_id, S.option_id, S.qty, S.revenue, S.product_name
    ).filter(S.sku_id.in_(sku_ids), S.date >= dfrom, S.date <= dto)
    if vendor_id is not None:
        q = q.filter(S.vendor_id == vendor_id)

    out: dict[str, dict] = {}
    for sku, option_id, qty, revenue, name in q.all():
        e = out.setdefault(
            sku,
            {"qty": 0, "revenue": _Z, "option_ids": set(), "days": 0, "product_name": None},
        )
        e["qty"] += int(qty or 0)
        e["revenue"] += _f(revenue)
        e["days"] += 1
        if option_id:
            e["option_ids"].add(option_id)
        if e["product_name"] is None and name:
            e["product_name"] = name
    for e in out.values():
        e["option_ids"] = sorted(e["option_ids"])
    return out


# ════════════════════════════════════════════════
# ③ 납품가 (최신 발주 단가)
# ════════════════════════════════════════════════
def _supply_price_by_sku(db: Session, sku_ids: list[str]) -> dict[str, Decimal]:
    """SKU(=product_number)별 **최신 발주의 단가**(unit_purchase_price).

    "최신" = purchase_order_seq 최대. 발주번호는 쿠팡이 증가시키는 값이라 시간 순서를 대신한다
    (po_created_at은 발주상세 라인에 없다 — 라인은 PO에 종속).
    ★발주 이력이 없는 SKU는 **키가 없다**(0이 아니다). 신상품(예: 69411570)이 여기 해당한다 —
      0으로 접으면 "납품매출 0원짜리 프로모션"이 조용히 손익에 앉는다.
    """
    if not sku_ids:
        return {}
    I = CoupangRocketPurchaseOrderItem
    latest = (
        db.query(I.product_number, func.max(I.purchase_order_seq))
        .filter(I.product_number.in_(sku_ids))
        .group_by(I.product_number)
        .all()
    )
    out: dict[str, Decimal] = {}
    for pn, seq in latest:
        price = (
            db.query(I.unit_purchase_price)
            .filter(I.product_number == pn, I.purchase_order_seq == seq)
            .order_by(I.line_no)
            .first()
        )
        if price is not None and price[0] is not None:
            out[pn] = _f(price[0])
    return out


# ════════════════════════════════════════════════
# ④ 원가 (D-CPP-8: product_master.cost_price가 단일 진실)
# ════════════════════════════════════════════════
def _cost_by_sku(db: Session, sku_ids: list[str]) -> dict[str, dict]:
    """SKU별 원가 = RocketProductCostMap(상품번호→internal_sku) → product_master.cost_price.

    반환 {sku: {"cost_price": Decimal|None, "status": confirmed|ignored, "internal_sku": str|None}}.
    - status='ignored' → 원가 **0으로 결정된 것**(샘플·증정). cost_price=0으로 해결 처리.
    - 매핑 없음 / confirmed인데 master 없음 → 키가 없거나 cost_price=None(**미해결**).
    ★D-CPP-8: cost_price는 환율에 따라 상시 변하는 **단일 현재값**이고, 과거 창에 소급 적용된다.
      Jino 승인된 근사이지 시점 정확값이 아니다 — 응답 note에 명시한다.
    """
    if not sku_ids:
        return {}
    rows = (
        db.query(
            RocketProductCostMap.product_number,
            RocketProductCostMap.status,
            RocketProductCostMap.internal_sku,
            ProductMaster.cost_price,
        )
        .outerjoin(ProductMaster, ProductMaster.internal_sku == RocketProductCostMap.internal_sku)
        .filter(RocketProductCostMap.product_number.in_(sku_ids))
        .all()
    )
    out: dict[str, dict] = {}
    for pn, status, isku, cost_price in rows:
        if status == "ignored":
            out[pn] = {"cost_price": _Z, "status": "ignored", "internal_sku": None}
        else:
            out[pn] = {
                "cost_price": _f(cost_price) if cost_price is not None else None,
                "status": status,
                "internal_sku": isku,
            }
    return out


# ════════════════════════════════════════════════
# ⑤ 광고비 (옵션 귀속 시도 → 실패 시 계정단위 상한 프록시)
# ════════════════════════════════════════════════
def _ad_spend_window(
    db: Session, vendor_id: str | None, dfrom: date, dto: date, option_ids: list[str]
) -> dict:
    """창 내 광고비. **옵션 귀속이 되면 그것을, 안 되면 계정단위 합을 상한으로** 돌려준다.

    ★2026-07-28 prod 실측: `coupang_ad_option_daily`에는 A01564720(3P) 행만 있고 오하이테크
      (A01029796·Retail)는 **0행**이다. 즉 1P는 지금 옵션×일 광고비가 없다 — 계정단위
      `coupang_ad_report`(sell_type='Retail')만 있다(rocket_intelligence D-4와 같은 사실).
    ⇒ 이 프로모션 SKU들에 얼마가 쓰였는지는 **모른다**. 0으로 접지 않고 available=False로
      올린다(원칙22 — 조용한 0은 순이익을 과대하게 만든다).
    ⇒ 대신 계정 전체 합을 준다. 프로모션 SKU는 계정의 부분집합이므로 이 값은 **상한**이다.
      상한과 BEP 광고비를 나란히 놓으면 "최악의 경우에도 남는가"를 판정할 수 있다.
    옵션 데이터가 나중에 들어오면(빌보드 옵션 ingest가 Retail을 채우면) 이 함수가 자동으로
      available=True로 바뀐다 — 코드 변경 없이 전환된다.
    """
    account = db.query(func.coalesce(func.sum(CoupangAdReport.ad_spend), 0)).filter(
        CoupangAdReport.report_date >= dfrom,
        CoupangAdReport.report_date <= dto,
        CoupangAdReport.sell_type == ROCKET_AD_SELL_TYPE,
    )
    if vendor_id is not None:
        account = account.filter(CoupangAdReport.vendor_id == vendor_id)
    account_spend = _f(account.scalar())

    by_option: dict[str, Decimal] = {}
    if option_ids:
        O = CoupangAdOptionDaily
        oq = db.query(O.ad_option_id, func.coalesce(func.sum(O.ad_spend), 0)).filter(
            O.report_date >= dfrom,
            O.report_date <= dto,
            O.sell_type == ROCKET_AD_SELL_TYPE,
            O.ad_option_id.in_(option_ids),
        )
        if vendor_id is not None:
            oq = oq.filter(O.vendor_id == vendor_id)
        for opt, spend in oq.group_by(O.ad_option_id).all():
            by_option[opt] = _f(spend)

    available = bool(by_option)
    return {
        "available": available,
        "attributed": sum(by_option.values(), _Z) if available else None,
        "by_option": {k: str(v) for k, v in by_option.items()},
        "account_window_spend": account_spend,
        "basis": (
            "옵션 귀속 광고비(coupang_ad_option_daily, sell_type=Retail)"
            if available
            else (
                "★옵션 귀속 불가 — coupang_ad_option_daily에 이 계정(Retail) 행이 없다"
                "(2026-07-28 prod 실측: 옵션×일 광고비는 3P 계정만 수집 중). 프로모션 SKU에 "
                "얼마가 쓰였는지는 **모른다** → 0으로 접지 않고 미상으로 둔다. "
                "account_window_spend는 계정 전체 Retail 광고비이며, 프로모션 SKU는 그 부분집합이라 "
                "**상한(upper bound)**으로만 읽을 것."
            )
        ),
    }


# ════════════════════════════════════════════════
# 결합 — 프로모션 1건 손익
# ════════════════════════════════════════════════
def compute_promotion_pnl(
    db: Session, promo: CoupangRocketPromotion, vendor_id: str | None = None
) -> dict:
    """프로모션 1건의 손익 + SKU별 분해.

    공식(설계 확정):
      납품매출 = 최신 납품단가 × 판매량
      원가     = cost_price × 판매량
      분담금   = 판매량 × unit_discount_amount   (미입력이면 **N/A** — 0 아님)
      순이익   = 납품매출 − 원가 − 분담금 − 광고비
      BEP 광고비 = 납품매출 − 원가 − 분담금       (= 광고비가 이 값을 넘으면 적자)
      진짜 BEP ROAS = 실현 소비자가 ÷ 개당 공헌이익(납품단가 − 원가 − 개당 분담금)

    ★한 조각이라도 미상이면 그 SKU는 **미해결**로 빠지고, 합계는 해결된 SKU만 더한다.
      빠진 수량·SKU는 unresolved_*로 함께 올라온다(조용한 누락 금지, 원칙22).
    """
    window = _promo_window(promo)
    target_skus = [str(s).strip() for s in (promo.target_sku_ids or []) if str(s).strip()]
    unit_disc = promo.unit_discount_amount
    unit_disc_known = unit_disc is not None

    base: dict = {
        "request_id": promo.request_id,
        "vendor_id": promo.vendor_id,
        "promotion_name": promo.promotion_name,
        "promotion_type": promo.promotion_type,
        "status": promo.status,
        "start_at": promo.start_at.isoformat() if promo.start_at else None,
        "end_at": promo.end_at.isoformat() if promo.end_at else None,
        "share_ratio": promo.share_ratio,
        "budget_amount": promo.budget_amount,
        "applied_product_count": promo.applied_product_count,
        "unit_discount_amount": unit_disc,
        "unit_discount_missing": not unit_disc_known,
        "target_sku_ids": target_skus,
        "target_sku_missing": not target_skus,
        "window": None,
        "window_basis": (
            "행사기간은 초 단위지만 판매분석 그레인은 **일**이라 시작·종료일을 그 날 전체로 본다. "
            "경계일은 프로모션 밖 판매를 일부 포함할 수 있는 **근사**다(과대 방향)."
        ),
        "skus": [],
        "totals": None,
        "ad": None,
        "blockers": [],
    }

    blockers: list[str] = []
    if window is None:
        blockers.append("행사기간(start_at/end_at) 없음 — 창을 만들 수 없다")
    if not target_skus:
        blockers.append(
            "대상 SKU 미지정 — 프로모션 API에 적용 상품 목록이 없다(detailCount만 존재). "
            "PATCH /rocket/promotion/{request_id}/unit-discount 의 target_sku_ids로 지정할 것 "
            "(추정 매핑 금지)"
        )
    if not unit_disc_known:
        blockers.append("개당 할인액 미입력 — 분담금을 계산할 수 없다(0으로 접지 않음, D-CPP-7)")
    base["blockers"] = blockers
    if window is None or not target_skus:
        return base

    dfrom, dto = window
    base["window"] = {"from": dfrom.isoformat(), "to": dto.isoformat(),
                      "days": (dto - dfrom).days + 1}

    sales = _sales_by_sku(db, vendor_id, target_skus, dfrom, dto)
    supply = _supply_price_by_sku(db, target_skus)
    costs = _cost_by_sku(db, target_skus)

    all_option_ids: list[str] = []
    for e in sales.values():
        all_option_ids.extend(e["option_ids"])
    ad = _ad_spend_window(db, vendor_id, dfrom, dto, sorted(set(all_option_ids)))
    base["ad"] = {
        "available": ad["available"],
        "attributed": ad["attributed"],
        "by_option": ad["by_option"],
        "account_window_spend": ad["account_window_spend"],
        "basis": ad["basis"],
    }

    t_qty = 0
    t_realized = _Z
    t_realized_all = _Z
    t_supply = _Z
    t_cost = _Z
    t_funding = _Z
    unresolved_skus: list[str] = []
    unresolved_qty = 0

    sku_rows: list[dict] = []
    for sku in target_skus:
        s = sales.get(sku)
        qty = int(s["qty"]) if s else 0
        realized = s["revenue"] if s else _Z
        sp = supply.get(sku)
        cm = costs.get(sku)
        cost_price = cm["cost_price"] if cm else None

        reasons: list[str] = []
        if s is None:
            reasons.append("창 내 판매분석 행 없음(미수집이거나 판매 0)")
        if sp is None:
            reasons.append("납품가 미상 — 발주(발주상세) 이력 없음")
        if cost_price is None:
            reasons.append(
                "원가 미상 — rocket_product_cost_map 매핑 없음 또는 product_master 없음"
                if cm is None or cm.get("internal_sku") is None
                else "원가 미상 — product_master.cost_price 없음"
            )
        if not unit_disc_known:
            reasons.append("개당 할인액 미입력")

        resolved = sp is not None and cost_price is not None and unit_disc_known
        supply_rev = _q2(sp * qty) if sp is not None else None
        cost_amt = _q2(cost_price * qty) if cost_price is not None else None
        funding = _q2(_f(unit_disc) * qty) if unit_disc_known else None

        unit_contrib = None
        bep_roas = None
        realized_unit_price = _q2(realized / qty) if qty else None
        if resolved:
            unit_contrib = _q2(_f(sp) - _f(cost_price) - _f(unit_disc))
            # 진짜 BEP ROAS = 실현 소비자가 ÷ 개당 공헌이익.
            #   분자를 소비자가로 두는 이유: 쿠팡 광고 ROAS의 분자가 소비자가라, 같은 축으로
            #   비교되어야 "광고 화면의 ROAS가 이 값을 넘는가"를 바로 읽을 수 있다(D-CPP-2).
            if realized_unit_price is not None:
                bep_roas = _ratio4(realized_unit_price, unit_contrib)

        t_realized_all += realized

        bep_ad = None
        if resolved:
            bep_ad = _q2(_f(supply_rev) - _f(cost_amt) - _f(funding))
            t_qty += qty
            t_realized += realized
            t_supply += _f(supply_rev)
            t_cost += _f(cost_amt)
            t_funding += _f(funding)
        else:
            unresolved_skus.append(sku)
            unresolved_qty += qty

        sku_rows.append({
            "sku_id": sku,
            "product_name": s["product_name"] if s else None,
            "option_ids": s["option_ids"] if s else [],
            "sales_days": s["days"] if s else 0,
            "qty": qty,
            "realized_revenue": _q2(realized),
            "realized_unit_price": realized_unit_price,
            "supply_unit_price": sp,
            "supply_revenue": supply_rev,
            "cost_price": cost_price,
            "cost": cost_amt,
            "funding": funding,
            "unit_contribution": unit_contrib,
            "bep_ad_spend": bep_ad,
            "bep_roas": bep_roas,
            "resolved": resolved,
            "unresolved_reasons": reasons if not resolved else [],
        })
    base["skus"] = sku_rows

    resolved_count = sum(1 for r in sku_rows if r["resolved"])
    bep_ad_total = _q2(t_supply - t_cost - t_funding) if resolved_count else None
    # 프로모션 단위 BEP ROAS = Σ실현매출 ÷ Σ공헌이익. SKU별 값의 평균이 아니다(가중이 틀어진다).
    bep_roas_total = (
        _ratio4(t_realized, t_supply - t_cost - t_funding) if resolved_count else None
    )

    ad_cost = ad["attributed"] if ad["available"] else None
    net_profit = (
        _q2(_f(bep_ad_total) - _f(ad_cost))
        if (bep_ad_total is not None and ad_cost is not None)
        else None
    )
    net_lower = (
        _q2(_f(bep_ad_total) - ad["account_window_spend"])
        if bep_ad_total is not None and not ad["available"]
        else None
    )

    base["totals"] = {
        # ★두 축을 나눠 낸다. qty/realized_revenue는 **손익에 실제로 들어간 분**(해결된 SKU)이고,
        #   qty_all/realized_revenue_all은 대상 SKU 전체의 판매다. 하나만 내보내면 둘 중 하나가
        #   거짓말이 된다 — 손익 소계를 판매량으로 읽거나(과소), 판매량으로 손익을 나누거나(과대).
        "qty": t_qty,
        "qty_all": t_qty + unresolved_qty,
        "realized_revenue_all": _q2(t_realized_all),
        "realized_revenue": _q2(t_realized),          # 소비자 실현가(회계 매출 아님, D-CPP-2)
        "supply_revenue": _q2(t_supply) if resolved_count else None,
        "cost": _q2(t_cost) if resolved_count else None,
        "funding": _q2(t_funding) if resolved_count else None,
        "ad_cost": ad_cost,
        "net_profit": net_profit,
        "bep_ad_spend": bep_ad_total,                 # 이 값을 넘는 광고비 = 적자
        "bep_roas": bep_roas_total,                   # ★진짜 BEP ROAS(광고 ROAS가 이 값 이상이어야 본전)
        "net_profit_lower_bound": net_lower,          # 계정 전체 광고비를 이 프로모션에 전부 물린 최악값
        "resolved_sku_count": resolved_count,
        "unresolved_sku_ids": unresolved_skus,
        "unresolved_qty": unresolved_qty,
        "basis": (
            "납품매출=최신 발주단가×판매량 / 원가=product_master.cost_price×판매량(D-CPP-8: 단일 "
            "현재값의 소급 적용 — 승인된 근사) / 분담금=판매량×개당 할인액(D-CPP-7 수기) / "
            "순이익=납품매출−원가−분담금−광고비 / BEP 광고비=납품매출−원가−분담금 / "
            "BEP ROAS=Σ실현 소비자가÷Σ개당 공헌이익. ★미해결 SKU는 합계에서 빠진다"
            "(unresolved_* 참조) — 0으로 접지 않는다(원칙22)."
        ),
    }
    if not ad["available"]:
        blockers.append(
            "광고비 옵션 귀속 불가 — 순이익은 미상(N/A). 계정 전체 광고비를 상한으로 본 "
            "net_profit_lower_bound만 제공한다"
        )
    if unresolved_skus:
        blockers.append(
            f"미해결 SKU {len(unresolved_skus)}건(수량 {unresolved_qty}) — 합계에서 제외됨: "
            + ", ".join(unresolved_skus[:10])
        )
    base["blockers"] = blockers
    return base


# ════════════════════════════════════════════════
# 신선도·감시 ① 판매분석 유효구간 내 빈 날짜
# ════════════════════════════════════════════════
def compute_sales_freshness(db: Session, vendor_id: str | None = None,
                            today: date | None = None) -> dict:
    """롤링 유효구간 안에서 **행이 하나도 없는 날짜**를 찾아 만료 임박순으로 돌려준다.

    왜 필요한가(리뷰어 (c)3 상설 결손 감지): 판매분석은 약 57일 롤링 창이라, 안 채운 날은
      조용히 **창 밖으로 밀려 영원히 못 채운다**. 수집이 멈춘 사실보다 "언제까지 메울 수 있나"가
      운영 판단에 필요하다.
    ★창 길이는 실측(2026-07-28)이지 보장이 아니다 — window_days_basis로 근거를 함께 낸다.
    """
    today = today or kst_today()
    window_end = today - timedelta(days=_EXPECTED_LAG_DAYS)   # 판매분석 확정 최신일=어제
    # ★창은 **끝에서부터** 센다(today가 아니라 window_end 기준). 라이브 실측으로 검산되는 정의다:
    #   2026-07-28에 서버가 준 구간이 [2026-06-01 ~ 2026-07-27]이고, 07-27 − 56 = 06-01로 맞는다.
    #   today에서 빼면 하루가 밀려(06-02) 실제로는 아직 메울 수 있는 날을 "이미 만료"로 읽는다.
    window_start = window_end - timedelta(days=_WINDOW_DAYS - 1)

    S = CoupangRocketSalesDaily
    q = db.query(S.date).filter(S.date >= window_start, S.date <= window_end)
    if vendor_id is not None:
        q = q.filter(S.vendor_id == vendor_id)
    # SQLite는 Date를 date로, 드라이버에 따라 datetime으로 돌려준다 — 둘 다 date로 정규화한다.
    #   정규화를 빼먹으면 `d not in have`가 항상 참이 되어 **모든 날짜가 결손으로 보인다**.
    have = {_as_date(row[0]) for row in q.distinct().all() if row[0] is not None}

    missing: list[dict] = []
    d = window_start
    while d <= window_end:
        if d not in have:
            missing.append({
                "date": d.isoformat(),
                # 이 날짜가 창 밖으로 밀려나기까지 남은 일수(0이면 오늘이 마지막 기회).
                "days_until_expiry": (d - window_start).days,
            })
        d += timedelta(days=1)
    missing.sort(key=lambda m: m["days_until_expiry"])

    lq = db.query(func.max(S.date))
    if vendor_id is not None:
        lq = lq.filter(S.vendor_id == vendor_id)
    latest = _as_date(lq.scalar())

    stale_days = (window_end - latest).days if latest is not None else None

    trial_end = _parse_date(_TRIAL_END)
    days_to_trial_end = (trial_end - today).days if trial_end else None

    return {
        "today": today.isoformat(),
        "window": {"from": window_start.isoformat(), "to": window_end.isoformat(),
                   "days": _WINDOW_DAYS},
        "window_days_basis": (
            f"롤링 창 {_WINDOW_DAYS}일 = 2026-07-28 라이브 실측(서버가 400 본문에 "
            "`viewable period [2026-06-01 ~ 2026-07-27]`을 실어 줬다). **보장값이 아니다** — "
            "서버가 창을 바꾸면 이 계산은 틀린다(env ROCKET_SALES_WINDOW_DAYS로 조정)."
        ),
        "latest_date": latest.isoformat() if latest else None,
        "stale_days": stale_days,           # 어제 대비 며칠 뒤처졌나(0=최신)
        "missing_count": len(missing),
        "missing_dates": missing[:_WINDOW_DAYS],
        "urgent_count": sum(1 for m in missing if m["days_until_expiry"] <= 7),
        # ② 구독 체험 종료 경고 (D-CPP-5)
        "subscription": {
            "free_trial_end": trial_end.isoformat() if trial_end else None,
            "days_left": days_to_trial_end,
            "warn": bool(days_to_trial_end is not None and days_to_trial_end <= _TRIAL_WARN_DAYS),
            "expired": bool(days_to_trial_end is not None and days_to_trial_end < 0),
            "basis": (
                "2026-07-28 라이브 실측 GET /rpd/v2/supplier/subscription/detail → "
                "permittedLevel=BASIC, freeTrialEndDate=2026.08.20 (D-CPP-5). 체험이 끝나면 "
                "판매분석이 **조용히** 멈출 수 있어 D-7부터 경고한다."
            ),
        },
    }


# ════════════════════════════════════════════════
# RG(2P) 쿠폰 — 메타 + used_amount 나열 (엔진 확장은 used_amount 수집 후)
# ════════════════════════════════════════════════
def list_rg_coupons(db: Session, dfrom: date | None = None, dto: date | None = None,
                    limit: int = 50) -> dict:
    """즉시할인(INSTANT) 쿠폰 메타 + used_amount 나열.

    ★D-CPP-3: 우리 실부담의 권위값은 쿠폰 "사용 금액"이고, 쿠팡 Open API에는 그 값이 없다
      (ref 06 §E 전수 대조). 지금은 `/coupon/used-amount/ingest`로만 들어오며 **대부분 NULL**이다.
      NULL을 0으로 읽으면 "부담 0원짜리 쿠폰"이 되므로 그대로 미수집으로 표기한다.
    손익 엔진 확장은 used_amount 원천이 확정된 뒤(계획서 §3 Wing 정찰) — 지금은 나열 수준.
    """
    C = CoupangCoupon
    q = db.query(C).filter(C.coupon_kind == "INSTANT")
    if dfrom is not None:
        q = q.filter((C.end_at.is_(None)) | (C.end_at >= dfrom))
    if dto is not None:
        q = q.filter((C.start_at.is_(None)) | (C.start_at <= dto))
    # SQLite/PostgreSQL 모두 DESC에서 NULL 위치가 다르지만(SQLite=마지막, PG=처음), 이 목록은
    #   표시 순서일 뿐 계산에 쓰이지 않으므로 nullslast를 강요하지 않는다(방언 의존 제거).
    rows = q.order_by(C.start_at.desc(), C.id.desc()).limit(limit).all()

    ids = [r.coupon_id for r in rows]
    item_counts: dict[str, int] = {}
    if ids:
        for cid, cnt in (
            db.query(CoupangCouponItem.coupon_id, func.count(CoupangCouponItem.id))
            .filter(CoupangCouponItem.coupon_id.in_(ids))
            .group_by(CoupangCouponItem.coupon_id)
            .all()
        ):
            item_counts[cid] = int(cnt or 0)

    out = [{
        "coupon_id": r.coupon_id,
        "account_key": r.account_key,
        "promotion_name": r.promotion_name,
        "status": r.status,
        "discount_type": r.discount_type,
        "discount": r.discount,
        "start_at": r.start_at.isoformat() if r.start_at else None,
        "end_at": r.end_at.isoformat() if r.end_at else None,
        "option_count": item_counts.get(r.coupon_id, 0),
        "used_amount": r.used_amount,
        "used_amount_source": r.used_amount_source,
        "used_amount_pending": r.used_amount is None,   # ★0이 아니라 '아직 안 들어옴'
    } for r in rows]

    return {
        "coupons": out,
        "count": len(out),
        "pending_count": sum(1 for c in out if c["used_amount_pending"]),
        "note": (
            "RG(2P) 쿠폰은 **나열 수준**이다. 우리 실부담 권위값 = 쿠폰 '사용 금액'(D-CPP-3)인데 "
            "쿠팡 Open API에 그 필드가 없어(ref 06 §E) 아직 수집 경로가 없다 → used_amount는 "
            "대부분 NULL이며 **0이 아니라 미수집**이다. 손익 엔진 편입은 원천 확정 후."
        ),
    }


# ════════════════════════════════════════════════
# 결합 — 화면 1회 호출용 조망
# ════════════════════════════════════════════════
def compute_promo_pnl_overview(
    db: Session,
    vendor_id: str | None = None,
    limit: int = 20,
    request_id: str | None = None,
    today: date | None = None,
) -> dict:
    """프로모션 손익 조망 — 프로모션 카드 N건 + 신선도/감시 + RG 쿠폰 블록.

    읽기 전용. 기존 net_profit·종합조망 회계는 **전혀 건드리지 않는다**(신규 API 전용).
    """
    q = db.query(CoupangRocketPromotion)
    if vendor_id is not None:
        q = q.filter(CoupangRocketPromotion.vendor_id == vendor_id)
    if request_id:
        q = q.filter(CoupangRocketPromotion.request_id == request_id)
    promos = q.order_by(
        CoupangRocketPromotion.start_at.desc(),
        CoupangRocketPromotion.id.desc(),
    ).limit(limit).all()

    cards = [compute_promotion_pnl(db, p, vendor_id) for p in promos]
    fresh = compute_sales_freshness(db, vendor_id, today=today)

    dfrom = dto = None
    windows = [c["window"] for c in cards if c.get("window")]
    if windows:
        dfrom = min(_parse_date(w["from"]) for w in windows)
        dto = max(_parse_date(w["to"]) for w in windows)

    return {
        "vendor_id": vendor_id,
        "promotions": cards,
        "promotion_count": len(cards),
        "freshness": fresh,
        "rg_coupons": list_rg_coupons(db, dfrom, dto),
        "accounting_note": (
            "★읽기 전용 레이어다. 1P 회계 매출은 여전히 발주(납품)금액 축이며(D-CPP-2), 여기 "
            "realized_revenue(소비자 실현가)는 회계에 합산되지 않는다. 분담금 청구 방식도 "
            "미확정(D-CPP-4)이라 어떤 비용 라인에도 자동 반영되지 않는다 — 9월 정산서 대사 후 확정."
        ),
    }
