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

# 옵션×일 광고비가 그 날 "완결됐다"고 볼 최소 정합 비율 = 옵션 합 ÷ 계정 롤업 합.
#   1.0을 요구하지 않는 이유: 두 소스(빌보드 옵션 XLSX / report SALES)는 반올림 그레인이 달라
#   완결된 날에도 소수 오차가 난다. 0.95는 **휴리스틱**이지 실측 임계값이 아니다 — 그래서
#   일별 실비율을 option_vs_account_ratio로 함께 내보내 판정을 사후 검증할 수 있게 둔다.
_AD_COVERAGE_MIN = Decimal(os.getenv("ROCKET_AD_OPTION_COVERAGE_MIN") or "0.95")

# missing_dates 응답 캡. ★_WINDOW_DAYS를 쓰면 자기가 캡하려는 리스트와 같은 변수로 커져서
#   실제 바운드가 아니다(env로 창을 365일로 늘리면 캡도 365) — 고정 상수로 둔다(3R-F15).
_MAX_MISSING_DATES = 200


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
        S.sku_id, S.option_id, S.qty, S.revenue, S.product_name, S.date
    ).filter(S.sku_id.in_(sku_ids), S.date >= dfrom, S.date <= dto)
    if vendor_id is not None:
        q = q.filter(S.vendor_id == vendor_id)

    out: dict[str, dict] = {}
    for sku, option_id, qty, revenue, name, d in q.all():
        e = out.setdefault(
            sku,
            {"qty": 0, "revenue": _Z, "option_ids": set(), "dates": set(),
             "days": 0, "product_name": None},
        )
        e["qty"] += int(qty or 0)
        e["revenue"] += _f(revenue)
        # ★행 수가 아니라 **날짜 수**를 센다. 이 테이블 그레인은 (vendor, option, date)라
        #   한 SKU에 옵션이 N개면 하루에 N행이 생긴다 — 행을 세면 sales_days가 창 길이를
        #   넘는 값이 나온다(옵션 2개·3일 창 → 6). 일평균 계산에 쓰이면 그대로 틀린다.
        e["dates"].add(_as_date(d))
        if option_id:
            e["option_ids"].add(option_id)
        if e["product_name"] is None and name:
            e["product_name"] = name
    for e in out.values():
        e["option_ids"] = sorted(e["option_ids"])
        e["days"] = len(e["dates"])
        del e["dates"]
    return out


# ════════════════════════════════════════════════
# ②-b 창 커버리지 — '수집 안 됨' / '아직 안 옴' / '진짜 0판매'를 가른다
# ════════════════════════════════════════════════
def _window_coverage(
    db: Session, vendor_id: str | None, dfrom: date, dto: date, today: date | None = None
) -> dict:
    """창의 각 날짜를 covered / missing(수집 결손) / pending(아직 확정 전)으로 분류.

    ★왜 필요한가(적대적 리뷰 F1·F5): 판매행이 없다는 사실 하나로는 **"안 팔렸다"와 "수집이
      안 됐다"를 구분할 수 없다.** 구분 없이 qty=0으로 접으면 51일 결손이 알려진 이 시스템에서
      "판매 0·BEP 광고비 0원"짜리 유령 손익이 조용히 태어난다(원칙22 정면 위반).
    ⇒ 창 안에 **어떤 SKU든** 판매행이 있는 날 = 그 날 수집이 돈 날. 그 날 특정 SKU에 행이
      없으면 그건 진짜 0판매다. 반대로 아무 행도 없는 날은 판정 불가(missing)다.
    ⇒ 판매분석 확정 최신일은 어제(_EXPECTED_LAG_DAYS)라, 그 뒤 날짜는 결손이 아니라 **pending**
      이다(진행 중 프로모션). 둘을 섞으면 진행 중 행사가 '수집 사고'로 보인다.

    null_sku_rows: sku_id가 NULL인 창 내 판매행 수. SKU 조인에서 조용히 빠지는 분(과소 방향).
    """
    today = today or kst_today()
    data_through = today - timedelta(days=_EXPECTED_LAG_DAYS)

    S = CoupangRocketSalesDaily
    q = db.query(S.date).filter(S.date >= dfrom, S.date <= dto)
    nq = db.query(func.count(S.id)).filter(
        S.date >= dfrom, S.date <= dto, S.sku_id.is_(None)
    )
    if vendor_id is not None:
        q = q.filter(S.vendor_id == vendor_id)
        nq = nq.filter(S.vendor_id == vendor_id)
    covered = {_as_date(r[0]) for r in q.distinct().all() if r[0] is not None}

    missing: list[str] = []
    pending: list[str] = []
    d = dfrom
    while d <= dto:
        if d not in covered:
            (pending if d > data_through else missing).append(d.isoformat())
        d += timedelta(days=1)

    total = (dto - dfrom).days + 1
    return {
        "window_days": total,
        "covered_days": total - len(missing) - len(pending),
        "missing_days": missing,          # 수집 결손 — 판매량이 실제보다 적다
        "pending_days": pending,          # 아직 확정 전(진행 중 행사) — 결손이 아니다
        "data_through": min(dto, data_through).isoformat(),
        # 창 안에서 판매 데이터가 실제로 닿은 마지막 날 — 광고 지평선(ad_days_covered)과
        #   어긋나면 순이익이 서로 다른 기간을 섞는다(3R-N1). 두 값을 나란히 내보내 대조하게 둔다.
        "sales_through": max(covered).isoformat() if covered else None,
        "in_flight": bool(pending),
        "complete": not missing and not pending,
        "null_sku_rows": int(nq.scalar() or 0),
    }


# ════════════════════════════════════════════════
# ③ 납품가 (최신 발주 단가)
# ════════════════════════════════════════════════
def _supply_price_by_sku(
    db: Session, sku_ids: list[str], vendor_id: str | None = None
) -> dict[str, dict]:
    """SKU(=product_number)별 **최신 발주의 단가**(unit_purchase_price) + 그 발주번호.

    반환 {sku: {"price": Decimal, "po_seq": int}}.
    "최신" = purchase_order_seq 최대. 발주번호는 쿠팡이 증가시키는 값이라 시간 순서를 대신한다
    (po_created_at은 발주상세 라인에 없다 — 라인은 PO에 종속). `purchase_order_seq`는 Integer라
    수치 비교이고, UniqueConstraint(purchase_order_seq, product_number)가 동점 라인을 배제하므로
    선택은 **결정적**이다.
    ★날짜 필터가 없다 = **창 이후 발주의 단가가 과거 창에 소급 적용될 수 있다.** 원가 쪽 같은
      성격의 근사(D-CPP-8)와 짝이며, 어느 발주에서 왔는지 대사할 수 있도록 po_seq를 함께 낸다.
    ★발주 이력이 없는 SKU는 **키가 없다**(0이 아니다). 신상품(예: 69411570)이 여기 해당한다 —
      0으로 접으면 "납품매출 0원짜리 프로모션"이 조용히 손익에 앉는다.
    ★쿼리 1회(서브쿼리 조인). SKU마다 1회씩 돌면 프로모션 20건 × SKU 200개 = 4천 쿼리가 된다.
    """
    if not sku_ids:
        return {}
    I = CoupangRocketPurchaseOrderItem
    latest = (
        db.query(I.product_number.label("pn"),
                 func.max(I.purchase_order_seq).label("seq"))
        .filter(I.product_number.in_(sku_ids))
    )
    if vendor_id is not None:
        latest = latest.filter(I.vendor_id == vendor_id)
    latest = latest.group_by(I.product_number).subquery()

    rows = (
        db.query(I.product_number, I.purchase_order_seq, I.unit_purchase_price)
        .join(
            latest,
            (I.product_number == latest.c.pn) & (I.purchase_order_seq == latest.c.seq),
        )
        .all()
    )
    out: dict[str, dict] = {}
    for pn, seq, price in rows:
        if price is not None:
            out[pn] = {"price": _f(price), "po_seq": int(seq)}
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
    db: Session, vendor_id: str | None, dfrom: date, dto: date, option_ids: list[str],
    pending_days: list[str] | None = None,
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

    ★귀속 완전성 판정자 = **창 내 모든 날짜에 이 계정(Retail) 옵션행이 있는가**(적대적 리뷰 F2).
      "행이 있는 옵션 전부"를 판정자로 쓰면 안 된다: `coupang_ad_option_daily`는 집행이 있어야
      행이 생기므로 **광고비가 진짜 0원인 옵션은 행이 없다** — 옵션 집합으로 판정하면 빌보드가
      완전히 붙어도 available이 영원히 False가 된다.
      반대로 '한 행이라도 있으면 완전 귀속'도 안 된다: 두 옵션 중 하나만 들어온 상태에서
      계정 광고비 2.27M 중 1천 원만 물린 채 "순이익 확정 흑자"가 나온다(리뷰 재현).
      날짜 커버리지는 둘 다 피한다 — 그 날 ingest가 돌았으면 그 날 행이 없는 옵션은 정당한 0원,
      ingest가 안 돈 날이 하나라도 있으면 **부분 귀속**이라 확정값을 내지 않는다.

    ★단, "그 날 행이 있다"는 완결의 **프록시지 증거가 아니다**(적대적 리뷰 3R-a): 하루치가 부분
      적재되면(페이지네이션 중단·일부 캠페인만 export) 존재 판정은 통과하는데 우리 옵션만 빠질 수
      있다 — F2 원본 사고가 하루 안으로 숨는다. 그래서 존재가 아니라 **금액 정합**으로 판정한다:
        그 날 옵션 합(계정 전체 옵션) ≥ 그 날 계정 롤업(coupang_ad_report) × _AD_COVERAGE_MIN
      계정 롤업이 그 날 0이면(집행 자체가 없던 날) **자명하게 커버된 날**이다(3R-c).
      아직 확정 전인 날(pending)은 결손이 아니므로 분모에서 뺀다(3R-d) — 안 그러면 진행 중
      프로모션이 구조적으로 전부 net_profit을 잃는다.
    """
    A = CoupangAdReport
    aq = db.query(A.report_date, func.coalesce(func.sum(A.ad_spend), 0)).filter(
        A.report_date >= dfrom,
        A.report_date <= dto,
        A.sell_type == ROCKET_AD_SELL_TYPE,
    )
    if vendor_id is not None:
        aq = aq.filter(A.vendor_id == vendor_id)
    account_by_day: dict[date, Decimal] = {
        _as_date(d): _f(v) for d, v in aq.group_by(A.report_date).all() if d is not None
    }
    account_spend = sum(account_by_day.values(), _Z)

    O = CoupangAdOptionDaily
    # 그 날 **계정 전체** 옵션 광고비 합(우리 옵션만이 아니다 — 완결성 판정용 분자).
    dq = db.query(O.report_date, func.coalesce(func.sum(O.ad_spend), 0)).filter(
        O.report_date >= dfrom, O.report_date <= dto, O.sell_type == ROCKET_AD_SELL_TYPE
    )
    if vendor_id is not None:
        dq = dq.filter(O.vendor_id == vendor_id)
    option_by_day: dict[date, Decimal] = {
        _as_date(d): _f(v) for d, v in dq.group_by(O.report_date).all() if d is not None
    }

    pending = set(pending_days or [])
    window_days = (dto - dfrom).days + 1
    judged_days = 0
    days_covered = 0
    day_ratios: dict[str, str] = {}
    for i in range(window_days):
        d = dfrom + timedelta(days=i)
        if d.isoformat() in pending:      # 아직 올 시간이 안 된 날 — 결손 아님(3R-d)
            continue
        judged_days += 1
        acct = account_by_day.get(d, _Z)
        opt = option_by_day.get(d, _Z)
        if acct <= 0:                     # 그 날 계정 집행 0 — 자명하게 커버(3R-c)
            days_covered += 1
            continue
        ratio = opt / acct
        day_ratios[d.isoformat()] = str(ratio.quantize(_Q4))
        if ratio >= _AD_COVERAGE_MIN:     # 금액 정합으로 판정(3R-b)
            days_covered += 1

    by_option: dict[str, Decimal] = {}
    if option_ids:
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

    attributed_sum = sum(by_option.values(), _Z)
    available = judged_days > 0 and days_covered == judged_days
    partial = bool(by_option) and not available
    return {
        "available": available,
        "attributed": attributed_sum if available else None,
        # 부분 귀속분은 **버리지 않고** 별도 칸으로 보인다(있는 사실을 숨기지 않되 확정값으로
        #   쓰지 않는다). 확정 순이익은 available일 때만 산출된다.
        "attributed_partial": attributed_sum if partial else None,
        "by_option": {k: str(v) for k, v in by_option.items()},
        "ad_days_covered": days_covered,
        "ad_days_judged": judged_days,          # pending 제외한 판정 대상 일수
        "ad_days_total": window_days,
        "option_vs_account_ratio": day_ratios,  # 일별 옵션합÷계정합 — 부분 적재를 숫자로 노출
        "options_with_spend": len(by_option),
        "options_total": len(option_ids),
        "account_window_spend": account_spend,
        "basis": (
            f"옵션 귀속 광고비(coupang_ad_option_daily, sell_type=Retail) — 판정 대상 "
            f"{judged_days}일 전부에서 옵션 합이 계정 롤업의 {_AD_COVERAGE_MIN} 이상이라 완전 "
            "귀속으로 본다. 커버된 날에 행이 없는 옵션은 그 날 집행이 없었다는 뜻이라 0원이다."
            if available
            else (
                f"★옵션 귀속 불완전 — 판정 대상 {judged_days}일 중 {days_covered}일만 옵션×일 "
                "광고비가 계정 롤업과 정합한다"
                + (
                    "(2026-07-28 prod 실측: 옵션×일 광고비는 3P 계정만 수집 중). "
                    if days_covered == 0
                    else "(빌보드 옵션 ingest가 창을 다 못 채웠거나 하루치가 부분 적재됐다 — "
                         "option_vs_account_ratio 참조). "
                )
                + "빠진 날에 이 SKU들이 얼마를 썼는지는 **모른다** → 0으로 접지 않고 미상으로 둔다"
                  "(부분 합계는 attributed_partial로만 참고). account_window_spend는 계정 전체 "
                  "Retail 광고비이며, 프로모션 SKU는 그 부분집합이라 **상한(upper bound)**으로만 읽을 것."
            )
        ),
    }


# ════════════════════════════════════════════════
# 결합 — 프로모션 1건 손익
# ════════════════════════════════════════════════
def compute_promotion_pnl(
    db: Session, promo: CoupangRocketPromotion, vendor_id: str | None = None,
    today: date | None = None,
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
    cov = _window_coverage(db, vendor_id, dfrom, dto, today=today)
    base["window"] = {"from": dfrom.isoformat(), "to": dto.isoformat(),
                      "days": (dto - dfrom).days + 1, **cov}

    # ★3상태로 가른다(적대적 리뷰 F1 + 3R-N2 과잉교정 경보). 2상태로 만들면 안 된다:
    #   "결손이 하나라도 있으면 전부 미해결"로 가면 알려진 51일 결손 동안 기능이 통째로 빈
    #   화면이 된다 — 모르는 걸 0으로 접는 것(F1)의 거울상이고 똑같이 원칙22 위반이다.
    #     ① 창 완전 커버 + 판매행 0  → **진짜 0판매**. resolved, 손익 확정.
    #     ② 창 부분 커버 + 판매행 有  → resolved + qty_is_lower_bound(수량·손익은 "≥" 의미).
    #     ③ 창 부분 커버 + 판매행 0   → **판정 불가**. unresolved. ← F1이 뚫린 지점은 여기뿐.
    #     ④ 창 전부 pending          → '시작 전/집계 전'이지 결손이 아니다(별도 상태).
    sales_trustworthy = not cov["missing_days"] and not cov["pending_days"]
    qty_is_lower_bound = not sales_trustworthy
    not_started = bool(cov["pending_days"]) and cov["covered_days"] == 0 and not cov["missing_days"]
    if cov["missing_days"]:
        blockers.append(
            f"판매분석 결손 {len(cov['missing_days'])}일 / 창 {cov['window_days']}일 — "
            f"판매량이 실제보다 적다(누락일: {', '.join(cov['missing_days'][:5])}"
            f"{' 외' if len(cov['missing_days']) > 5 else ''}). "
            "행 없는 SKU는 '안 팔림'이 아니라 **판정 불가**로 둔다"
        )
    if cov["pending_days"]:
        blockers.append(
            (
                "아직 시작 전이거나 첫 집계 전 — 창 전체가 미확정이다"
                f"(확정 최신일 {cov['data_through']}). 결손이 아니다"
            )
            if not_started
            else (
                f"진행 중 — 창 {cov['window_days']}일 중 {len(cov['pending_days'])}일이 아직 확정 전"
                f"(확정 최신일 {cov['data_through']}). 손익은 **부분 창**의 값이다"
            )
        )
    if cov["null_sku_rows"]:
        blockers.append(
            f"sku_id 없는 판매행 {cov['null_sku_rows']}건 — SKU 조인에서 빠진다(판매량 과소)"
        )

    sales = _sales_by_sku(db, vendor_id, target_skus, dfrom, dto)
    supply = _supply_price_by_sku(db, target_skus, vendor_id)
    costs = _cost_by_sku(db, target_skus)

    all_option_ids: list[str] = []
    for e in sales.values():
        all_option_ids.extend(e["option_ids"])
    ad = _ad_spend_window(db, vendor_id, dfrom, dto, sorted(set(all_option_ids)),
                          pending_days=cov["pending_days"])
    base["ad"] = {
        "available": ad["available"],
        "attributed": ad["attributed"],
        "attributed_partial": ad["attributed_partial"],
        "by_option": ad["by_option"],
        "ad_days_covered": ad["ad_days_covered"],
        "ad_days_judged": ad["ad_days_judged"],
        "ad_days_total": ad["ad_days_total"],
        "option_vs_account_ratio": ad["option_vs_account_ratio"],
        "options_with_spend": ad["options_with_spend"],
        "options_total": ad["options_total"],
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
        sup = supply.get(sku)
        sp = sup["price"] if sup else None
        cm = costs.get(sku)
        cost_price = cm["cost_price"] if cm else None

        reasons: list[str] = []
        # ★행이 없다 ≠ 안 팔렸다. 창이 완전할 때만 '진짜 0판매'로 읽고 해결 처리한다(F1).
        sales_known = s is not None or sales_trustworthy
        if s is None and not sales_trustworthy:
            reasons.append(
                "창 내 판매분석 행 없음 + 창에 결손/미확정일 존재 — '안 팔림'인지 "
                "'수집 안 됨'인지 **판정 불가**(0으로 접지 않는다)"
            )
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

        resolved = (
            sales_known and sp is not None and cost_price is not None and unit_disc_known
        )
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
            "supply_price_po_seq": sup["po_seq"] if sup else None,   # 어느 발주에서 왔나(대사용)
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
        # ★이름에 scope를 박는다(적대적 리뷰 F4): 이건 **해결된 SKU만의** 하한이지 프로모션
        #   전체의 하한이 아니다. 미해결 SKU가 실제로 적자였다면 참값은 이 밑으로 뚫린다.
        #   경계일 과대 포함(window_basis)도 이 값을 낙관 방향으로 민다. "최악값"이라 부르면 거짓말.
        "net_profit_lower_bound_resolved_only": net_lower,
        "lower_bound_excludes_unresolved": bool(unresolved_skus),
        "resolved_sku_count": resolved_count,
        "unresolved_sku_ids": unresolved_skus,
        "unresolved_qty": unresolved_qty,
        # ★창이 부분 커버면 qty·손익은 확정값이 아니라 **하한**이다(3R-N2 ② 상태).
        #   화면은 이 플래그를 보고 "≥" 의미로 렌더해야 한다.
        "qty_is_lower_bound": qty_is_lower_bound,
        "not_started": not_started,
        "basis": (
            "납품매출=최신 발주단가×판매량 / 원가=product_master.cost_price×판매량(D-CPP-8: 단일 "
            "현재값의 소급 적용 — 승인된 근사) / 분담금=판매량×개당 할인액(D-CPP-7 수기) / "
            "순이익=납품매출−원가−분담금−광고비 / BEP 광고비=납품매출−원가−분담금 / "
            "BEP ROAS=Σ실현 소비자가÷Σ개당 공헌이익. ★미해결 SKU는 합계에서 빠진다"
            "(unresolved_* 참조) — 0으로 접지 않는다(원칙22). "
            "★납품단가는 **최신 발주**(supply_price_po_seq)에서 오며 창 이후 발주일 수 있다 — "
            "원가(D-CPP-8)와 같은 성격의 소급 근사다. "
            "★net_profit_lower_bound_resolved_only는 **해결된 SKU만의** 하한이다: 미해결 SKU가 "
            "적자였다면 참값은 그 밑이고, 경계일 과대 포함도 이 값을 낙관 방향으로 민다."
        ),
    }
    # ★지평선 정렬(3R-N1): 광고가 완전 귀속(available)일 때만 순이익이 나오는데, 판매 쪽이
    #   부분 커버면 **N일치 마진 − 창 전체 광고비**가 된다. 방향은 항상 **과소**(마진만 빠진다)라
    #   운영자를 흑자로 오도하지 않지만, 그래도 숨기지 않는다.
    if net_profit is not None and qty_is_lower_bound:
        blockers.append(
            f"지평선 불일치 — 판매는 {base['window'].get('sales_through') or '미도달'}까지인데 "
            f"광고비는 창 전체({ad['ad_days_judged']}일)를 물린다. 순이익은 **과소 방향**으로 "
            "치우친 하한이다"
        )
    if not ad["available"]:
        blockers.append(
            f"광고비 옵션 귀속 불완전(판정 대상 {ad['ad_days_judged']}일 중 "
            f"{ad['ad_days_covered']}일만 계정 롤업과 정합) — 순이익은 미상(N/A). "
            "계정 전체 광고비를 상한으로 본 "
            "net_profit_lower_bound_resolved_only만 제공한다"
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
        "missing_dates": missing[:_MAX_MISSING_DATES],
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
                    limit: int = 50, account_key: str | None = None) -> dict:
    """즉시할인(INSTANT) 쿠폰 메타 + used_amount 나열.

    ★D-CPP-3: 우리 실부담의 권위값은 쿠폰 "사용 금액"이고, 쿠팡 Open API에는 그 값이 없다
      (ref 06 §E 전수 대조). 지금은 `/coupon/used-amount/ingest`로만 들어오며 **대부분 NULL**이다.
      NULL을 0으로 읽으면 "부담 0원짜리 쿠폰"이 되므로 그대로 미수집으로 표기한다.
    손익 엔진 확장은 used_amount 원천이 확정된 뒤(계획서 §3 Wing 정찰) — 지금은 나열 수준.
    """
    C = CoupangCoupon
    q = db.query(C).filter(C.coupon_kind == "INSTANT")
    if account_key is not None:
        q = q.filter(C.account_key == account_key)
    # ★start_at/end_at은 **DateTime**인데 창은 date다. `start_at <= dto`로 비교하면 dto가
    #   자정으로 바인딩돼 **종료일 당일에 시작한 쿠폰이 통째로 빠진다**(07-26 09:00 시작 쿠폰이
    #   창[07-24,07-26]에서 0건). 엔진의 다른 모든 경계는 양끝 포함이므로 여기만 배타면 안 된다.
    #   → 다음 날 자정 미만으로 바꿔 '종료일 하루 전체'를 포함시킨다.
    if dfrom is not None:
        q = q.filter((C.end_at.is_(None)) | (C.end_at >= dfrom))
    if dto is not None:
        q = q.filter((C.start_at.is_(None)) | (C.start_at < dto + timedelta(days=1)))
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
        # ★어느 창으로 걸렀는지 명시(3R-F8 라이더): 1P 프로모션 카드가 0건이거나 전부 기간
        #   미상이면 창이 None이 되어 **무제한 목록**이 뜬다 — 화면의 쿠폰이 어느 기간 것인지
        #   정의되지 않은 상태가 조용히 생긴다. account_key도 마찬가지로 명시한다.
        "window": (
            {"from": dfrom.isoformat() if dfrom else None,
             "to": dto.isoformat() if dto else None}
            if (dfrom or dto) else None
        ),
        "window_note": (
            None if (dfrom or dto)
            else "★창 없음 — 1P 프로모션 카드에서 기간을 못 얻어 기간 필터 없이 최근 N건을 낸다"
        ),
        "account_key": account_key,
        "limit": limit,
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

    cards = [compute_promotion_pnl(db, p, vendor_id, today=today) for p in promos]
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
