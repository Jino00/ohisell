# rocket_1p_channel_pnl.py — 로켓배송 1P 채널을 대시보드에 올린다 (트랙: 쿠팡 손익 정합)
#
# 왜 별도 엔진인가: `profit_calculator`는 `orders`만 읽는데, 1P는 쿠팡이 우리에게서 **매입**하는
#   구조라 주문 테이블에 행이 없다(channel_type='consignment' → profit_calculator.py의 skip).
#   그래서 대시보드에 **로켓1P 행 자체가 없었다** — 회사 매출의 상당 부분이 화면 밖이었다.
#   계약이 정한 구조는 "orders 축 단일 엔진 통합 리팩터"가 아니라 **채널별 평행 엔진 + 대시보드
#   합산**이므로, 여기서 채널 요약 행 하나를 만들어 기존 그룹핑에 얹는다.
#
# ★매출 축 = 세금계산서(`coupang_rocket_settlement`)의 **지급예정금액**, 작성일자 귀속.
#   - **발주액이 아니다**: 쿠팡은 발주한 걸 다 받아가지 않는다(월 수령률 77~93%). 현행
#     `rocket_intelligence` D-3 정의(발주액)는 5개월 실측에서 정산 대비 **+24.7% 과대**였다.
#     정본은 세금계산서다 — 부가가치세법 제15조(재화가 인도되는 때)를 1차 출처로 확인했고,
#     라이브 486건이 쿠팡 화면과 **전 필드 원 단위로 일치**한다(ref 44 §1·§8-2).
#   - **부가세 포함(payment_amount)이지 공급가(supply_amount)가 아니다.** 이 대시보드의 매출
#     축은 전 채널이 VAT 포함(네이버=결제금액)이고, VAT는 `payable_vat()`가 순이익 단계에서
#     납부세액으로 뺀다. 여기만 공급가를 쓰면 회사 합계가 **축이 섞인 숫자**가 된다.
#     (계약 합격기준 원문은 "공급가 ±1%"였다 — 같은 계산서의 다른 칸이고, 되돌리려면
#      `_REVENUE_COLUMN`만 바꾸면 된다. 어느 칸을 쓰든 원천은 세금계산서로 같다.)
#
# ★순이익은 내지 않는다(net_profit=None). 원가 근거가 아직 없기 때문이다 — 지어내지 않는다:
#   - 발주 라인(SKU×수량×매입가)은 **2026-06부터만** 수집된다(06월 60% · 07월 86% · 08월 100%,
#     05월 이전 0%). A축(계산서) 기간 대부분에 원가를 붙일 라인 자체가 없다.
#   - 있는 구간조차 원가 매핑이 **이름 유사도 자동 확정**이라 30일 GMV의 31.7%가 다른 물건의
#     원가를 쓰고 있다(ref 44 §4 — `OHI-TGLASS-IP17PRO` 하나에 12개 기종이 붙어 있었다).
#   `_finalize`의 기존 계약(measurable_rev 0 → net "—")이 이 상태를 그대로 표현한다.
#   원가 축이 정리되면 이 파일에 cost/net을 채우는 것으로 전환된다(호출부 변경 없음).
#
# ★광고비는 넣는다. 실측이고(계정×일 `coupang_ad_report`, sell_type='Retail'), 진짜 나간 돈을
#   순이익을 못 낸다는 이유로 화면에서 지우면 비용이 **없는 것처럼** 보인다.
from __future__ import annotations

import logging
import os
from datetime import date
from decimal import Decimal

from sqlalchemy import func, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from app.models import Channel, CoupangAdReport, CoupangRocketSettlement
from app.services.profit_calculator import payable_vat

log = logging.getLogger(__name__)

ZERO = Decimal("0")

# 1P 채널(위탁축)과 그 광고 계정. 계정은 오하이테크 Retail 하나다(coupang-account-ad-structure).
ROCKET_1P_CHANNEL_CODE = "COUPANG_ROCKET"
ROCKET_1P_VENDOR_ID = "A01029796"
ROCKET_1P_AD_SELL_TYPE = "Retail"

# 매출로 쓸 계산서 칸. 위 주석 참조 — VAT 포함 축이라 payment_amount다.
_REVENUE_COLUMN = CoupangRocketSettlement.payment_amount


def _dec(v) -> Decimal:
    if v is None:
        return ZERO
    return v if isinstance(v, Decimal) else Decimal(str(v))


def rocket_1p_channel(db: Session) -> Channel | None:
    """1P 채널 행. 없으면 None(이 기능 전체가 조용히 꺼진다 — 다른 채널엔 영향 없음)."""
    return db.query(Channel).filter(Channel.code == ROCKET_1P_CHANNEL_CODE).first()


def _settlement_sum(db: Session, date_from: date, date_to: date) -> Decimal:
    """창 내 계산서 지급예정금액 합. 음수(월말 역발행 차감)도 그대로 더한다.

    ★역발행 차감을 빼지 않는 이유: 그것도 쿠팡이 우리에게 지급하는 금액의 일부다(부호가 음수일
      뿐). 13개월 실측 7건 −4,321,823원. 내역은 쿠팡 화면에 없어 미해명이지만, 있는 그대로
      더하는 쪽이 임의로 제외하는 것보다 원장에 가깝다(ref 44 §8-3).
    """
    total = (
        db.query(func.sum(_REVENUE_COLUMN))
        .filter(
            CoupangRocketSettlement.vendor_id == ROCKET_1P_VENDOR_ID,
            CoupangRocketSettlement.issue_date >= date_from,
            CoupangRocketSettlement.issue_date <= date_to,
        )
        .scalar()
    )
    return _dec(total)


def _ad_spend_by_day(db: Session, date_from: date, date_to: date) -> dict[str, Decimal]:
    """창 내 1P 광고비를 **날짜별로** 낸다. 원천이 둘이라 날짜 단위 합집합으로 합친다.

    ★두 원천이 시대를 나눠 갖고 있다(라이브 실측 2026-08-05):
        `ad_costs`(channel_id=5, 사람이 올린 XLSX)          2026-03-17 ~ 05-18  43,700,024원
        `coupang_ad_report`(자동수집 report/SALES, Retail)  2026-05-18 ~ 현재    32,982,233원
      **겹치는 날은 2026-05-18 하루뿐이고 두 값이 552,537원으로 정확히 같다** — 같은 측정이
      수기에서 자동으로 넘어간 것이다. 그러므로 **더하면 안 되고**(그 하루가 2배가 된다)
      **버려도 안 된다**(자동 이전 두 달이 0원이 된다). 날짜별로 자동 우선·수기 폴백.
    ★옵션×일(`coupang_ad_option_daily`)을 쓰지 않는 이유: 그 축은 2026-07-04부터만 있어
      과거 창에서 광고비가 사라진다. 07-04 이후 계정 롤업과의 차이는 +0.019%뿐이다(ref 44 §5).
    """
    out: dict[str, Decimal] = {}
    # ① 수기 XLSX(과거) — 채널 그레인
    ch = rocket_1p_channel(db)
    if ch is not None:
        rows = db.execute(
            text(
                "SELECT ad_date, SUM(CAST(ad_spend AS REAL)) FROM ad_costs "
                "WHERE channel_id = :cid AND ad_date >= :since AND ad_date <= :until "
                "GROUP BY ad_date"
            ),
            {"cid": ch.id, "since": date_from.isoformat(), "until": date_to.isoformat()},
        ).fetchall()
        for d, amt in rows:
            if d and amt:
                out[str(d)[:10]] = _dec(amt)
    # ② 자동수집(현재) — 같은 날짜가 있으면 **덮어쓴다**(더하지 않는다)
    for d, amt in (
        db.query(CoupangAdReport.report_date, func.sum(CoupangAdReport.ad_spend))
        .filter(
            CoupangAdReport.vendor_id == ROCKET_1P_VENDOR_ID,
            CoupangAdReport.sell_type == ROCKET_1P_AD_SELL_TYPE,
            CoupangAdReport.report_date >= date_from,
            CoupangAdReport.report_date <= date_to,
        )
        .group_by(CoupangAdReport.report_date)
        .all()
    ):
        if d is None:
            continue
        out[d.isoformat() if hasattr(d, "isoformat") else str(d)[:10]] = _dec(amt)
    return out


def _ad_spend_sum(db: Session, date_from: date, date_to: date) -> Decimal:
    return sum(_ad_spend_by_day(db, date_from, date_to).values(), ZERO)


# ════════════════════════════════════════════════════════════════════
# 판매 축 (sell-through) — 계산서 축과 **택일**이다 (D: 둘 다 쓰되 합치지 않는다)
# ════════════════════════════════════════════════════════════════════
# 계산서 축이 "쿠팡에 얼마어치 **납품**했나"라면 이 축은 "고객에게 얼마어치 **팔렸나"다.
#   실측 차이(2026-08-04 하루): 계산서 1,578,000 vs 판매 3,885,820 — **두 배 넘게 다르다.**
#   같은 물건을 납품 시점과 판매 시점에 각각 세는 것이므로 **두 축을 더하면 이중계상**이다.
# ★매출 = 판매수량 × **우리 납품단가**다. 판매분석의 `revenue`(소비자가 GMV)가 아니다 —
#   그건 쿠팡이 소비자에게 받은 돈이지 우리 매출이 아니다(1P는 납품가 축, D-CPP-2).
#   실측(2026-08-04): 소비자가 6,536,000 vs 납품가 3,885,820.
# ★공식은 프로모션 손익 레이어가 이미 쓰는 것과 **같다**(새로 만들지 않는다):
#       납품매출 − 원가 − 프로모션 분담금 − 광고비 − VAT
#   (그 모듈명을 여기 적지 않는 이유: 회계 코드가 그 엔진을 참조하는지 **문자열로** 검사하는
#    가드 테스트가 있다. 우리는 호출하지 않고 공식만 맞춘 것이라 이름을 적으면 거짓 양성이 된다.)
_COST_COVERAGE_MIN = Decimal(os.getenv("ROCKET_1P_COST_COVERAGE_MIN") or "0.95")

_SELL_THROUGH_SQL = """
WITH price AS (           -- SKU별 최근 납품단가(발주 라인에서, PO 최신순)
  SELECT product_number, unit_purchase_price,
         ROW_NUMBER() OVER (PARTITION BY product_number ORDER BY purchase_order_seq DESC) rn
  FROM coupang_rocket_purchase_order_item
),
p AS (SELECT product_number, unit_purchase_price FROM price WHERE rn = 1),
cost AS (                 -- 상품번호 → internal_sku → 등록원가(부가세 포함 축, D 2026-08-04)
  SELECT m.product_number, pm.cost_price
  FROM rocket_product_cost_map m
  JOIN product_master pm ON pm.internal_sku = m.internal_sku
  WHERE m.status = 'confirmed' AND pm.cost_price IS NOT NULL
)
SELECT s.date                                              AS d,
       SUM(s.qty)                                          AS qty,
       SUM(s.qty * p.unit_purchase_price)                  AS revenue,
       SUM(CASE WHEN c.cost_price IS NOT NULL
                THEN s.qty * p.unit_purchase_price END)    AS revenue_costed,
       SUM(CASE WHEN c.cost_price IS NOT NULL
                THEN s.qty * c.cost_price END)             AS cost
FROM coupang_rocket_sales_daily s
JOIN p ON p.product_number = s.sku_id
LEFT JOIN cost c ON c.product_number = s.sku_id
WHERE s.vendor_id = :vendor AND s.date >= :since AND s.date <= :until
GROUP BY s.date
"""

# 분담금은 **별도 조회**다 — 원천 테이블이 main에 아직 없기 때문이다.
#   `coupang_promo_discount_item`(D-CPP-10 제안서 엑셀 수집)은 브랜치
#   `claude/d-cpp-10-promo-file-ingest`에만 있고 prod엔 배포돼 있다(마이그 c8e1a4b7d201).
#   즉 **prod에는 있고 main에는 없다.** 하드 의존하면 main 기반 환경이 통째로 깨진다.
# ★없을 때 0으로 접지 않는다 — 0은 "분담금이 없었다"는 뜻이고 실제로는 "모른다"다.
#   모르면 순이익을 내지 않는다(원가 커버리지와 같은 원칙).
_PROMO_BURDEN_SQL = """
SELECT s.date AS d,
       SUM(CASE WHEN d.discount_type = 'RATE'
                THEN s.qty * p.unit_purchase_price * d.discount_value
                ELSE s.qty * d.discount_value END) AS burden
FROM coupang_rocket_sales_daily s
JOIN (SELECT product_number, unit_purchase_price,
             ROW_NUMBER() OVER (PARTITION BY product_number ORDER BY purchase_order_seq DESC) rn
      FROM coupang_rocket_purchase_order_item) p
  ON p.product_number = s.sku_id AND p.rn = 1
JOIN coupang_promo_discount_item d ON d.product_number = s.sku_id
JOIN coupang_rocket_promotion pr
  ON pr.request_id = d.request_id AND pr.vendor_id = :vendor
 AND s.date BETWEEN date(pr.start_at) AND date(pr.end_at)
WHERE s.vendor_id = :vendor AND s.date >= :since AND s.date <= :until
GROUP BY s.date
"""


def _promo_burden_by_day(db: Session, date_from: date, date_to: date) -> dict[str, Decimal] | None:
    """일자별 프로모션 분담금. **원천이 없으면 None(=모름)** — 0으로 접지 않는다.

    분담률은 실측 13건 전부 100%(전액 우리 부담)라 할인액이 곧 분담금이다(D-CPP-10).
    """
    if not sa_inspect(db.get_bind()).has_table("coupang_promo_discount_item"):
        return None
    rows = db.execute(
        text(_PROMO_BURDEN_SQL),
        {"vendor": ROCKET_1P_VENDOR_ID,
         "since": date_from.isoformat(), "until": date_to.isoformat()},
    ).fetchall()
    return {str(d)[:10]: _dec(b) for d, b in rows}


def _sell_through_by_day(db: Session, date_from: date, date_to: date) -> dict[str, dict]:
    """판매 축 일자별 원자료. 반환 {날짜: {qty, revenue, revenue_costed, cost}}.

    ★납품단가를 못 찾는 SKU(`JOIN p`)는 **행에서 빠진다**. 매출을 0으로 접으면 그 판매가
      없었던 것처럼 보이므로, 빠진 수량은 호출부가 커버리지로 표면화한다.
      (2026-08-04 실측에선 단가 미상 수량 0 — 발주상세 전 이력 백필 이후.)
    """
    rows = db.execute(
        text(_SELL_THROUGH_SQL),
        {"vendor": ROCKET_1P_VENDOR_ID,
         "since": date_from.isoformat(), "until": date_to.isoformat()},
    ).fetchall()
    out: dict[str, dict] = {}
    for d, qty, rev, rev_costed, cost in rows:
        out[str(d)[:10]] = {
            "qty": int(qty or 0),
            "revenue": _dec(rev),
            "revenue_costed": _dec(rev_costed),
            "cost": _dec(cost),
        }
    return out


def _sell_through_window(db: Session, date_from: date, date_to: date) -> dict:
    """판매 축 창 합계 + 원가 커버리지 + (충분할 때만) 순이익."""
    by_day = _sell_through_by_day(db, date_from, date_to)
    rev = sum((v["revenue"] for v in by_day.values()), ZERO)
    rev_costed = sum((v["revenue_costed"] for v in by_day.values()), ZERO)
    cost = sum((v["cost"] for v in by_day.values()), ZERO)
    burden_by_day = _promo_burden_by_day(db, date_from, date_to)
    burden = None if burden_by_day is None else sum(burden_by_day.values(), ZERO)
    ad = _ad_spend_sum(db, date_from, date_to)
    coverage = (rev_costed / rev) if rev > 0 else ZERO

    net = None
    if rev > 0 and coverage >= _COST_COVERAGE_MIN and burden is not None:
        # 스마트스토어와 같은 방식: 매출 − 원가 − 분담금 − 광고비 − 납부세액
        vat = payable_vat(rev, cost, burden, ad)
        net = rev - cost - burden - ad - vat
    return {
        "revenue": rev, "cost": cost, "promo_burden": burden, "ad_spend": ad,
        "coverage": coverage, "net_profit": net, "by_day": by_day,
        "qty": sum(v["qty"] for v in by_day.values()),
    }


BASIS_SETTLEMENT = "settlement"   # 계산서 축(sell-in) — 회계 정본, **기본값**
BASIS_SALES = "sales"             # 판매 축(sell-through) — 운영 지표
VALID_BASES = (BASIS_SETTLEMENT, BASIS_SALES)


def normalize_basis(value: str | None) -> str:
    """알 수 없는 값이 오면 조용히 **계산서 축**으로 떨어진다(기본값이 회계 정본이라서).

    ★오타 하나로 화면 숫자가 두 배 넘게 바뀌는 것을 막는다 — 모르는 축을 추측하지 않는다.
    """
    v = (value or "").strip().lower()
    if v in VALID_BASES:
        return v
    if v:
        log.warning("알 수 없는 매출 축 %r — 기본값(%s)으로 처리한다", value, BASIS_SETTLEMENT)
    return BASIS_SETTLEMENT


def compute_rocket_1p_summary_row(
    db: Session, date_from: date, date_to: date, basis: str = BASIS_SETTLEMENT
) -> dict | None:
    """1P 채널 요약 행 — `calculate_channel_summary` 출력과 **같은 모양**.

    basis=settlement(기본): 매출 = 세금계산서 지급예정금액, 순이익 없음(원가 축이 안 닿는다).
    basis=sales:            매출 = 판매수량 × 납품단가, 순이익 = 매출−원가−분담금−광고비−VAT
                            (단 **원가 커버리지가 임계 이상일 때만** — 아니면 None).
    ★두 축은 **택일**이다. 더하면 같은 물건을 납품·판매 두 번 세는 이중계상이 된다.
    매출도 광고비도 0이면 None(빈 행을 화면에 만들지 않는다).
    """
    ch = rocket_1p_channel(db)
    if ch is None:
        log.warning("로켓1P 채널(code=%s)이 없다 — 1P 행을 만들지 않는다", ROCKET_1P_CHANNEL_CODE)
        return None

    basis = normalize_basis(basis)
    if basis == BASIS_SALES:
        w = _sell_through_window(db, date_from, date_to)
        revenue, ad_spend = w["revenue"], w["ad_spend"]
        cost, burden, net = w["cost"], w["promo_burden"], w["net_profit"]
        coverage, qty = w["coverage"], w["qty"]
    else:
        revenue = _settlement_sum(db, date_from, date_to)
        ad_spend = _ad_spend_sum(db, date_from, date_to)
        cost = burden = ZERO
        net, coverage, qty = None, None, 0

    if revenue == ZERO and ad_spend == ZERO:
        return None

    rate = None
    if net is not None and revenue > 0:
        rate = str((net / revenue * Decimal("100")).quantize(Decimal("0.01")))

    return {
        "channel_id": ch.id,
        "channel_name": ch.name or "",
        "revenue": str(revenue),
        # 1P는 판매수수료·배송비가 없다(쿠팡 부담) → 전액 상품매출, 배송매출 0.
        "product_revenue": str(revenue),
        "shipping_revenue": "0",
        "cost": str(cost),
        "commission": "0",    # 1P는 판매수수료 없음(납품가 축)
        "ad_spend": str(ad_spend),
        "shipping": "0",
        "net_profit": None if net is None else str(net),
        "profit_rate": rate,
        "order_count": qty,   # 계산서 축엔 주문 개념이 없어 0, 판매 축은 판매수량
        # ── 화면이 축과 신뢰도를 말할 수 있게 하는 부가 정보(다른 채널 행엔 없다) ──
        "revenue_basis": basis,
        "cost_coverage": None if coverage is None else str(coverage.quantize(Decimal("0.0001"))),
        "promo_burden": None if burden is None else str(burden),
    }


def compute_rocket_1p_daily_points(
    db: Session, date_from: date, date_to: date, basis: str = BASIS_SETTLEMENT
) -> list[dict]:
    """1P 일자별 추이 — `calculate_channel_daily_trend` 출력과 같은 모양.

    ★계산서 축은 매출이 **계산서 작성일자**에 몰려 뜬다(주 3~4장). 판매가 그날 몰린 게 아니라
      계산서가 그날 발행된 것이다 — 뾰족하게 보이는 게 정상이다. 날짜별 실제 판매 흐름을 보려면
      basis='sales'로 바꾼다(그게 이 축이 존재하는 이유다).
    ★일자 순이익은 **내지 않는다**(창 단위로만 낸다): 원가 커버리지·VAT는 창 합계로 판정하는데
      하루씩 쪼개면 커버리지가 들쭉날쭉해 어떤 날은 나오고 어떤 날은 안 나온다. 합계와 추이의
      순이익이 어긋나느니 추이는 매출·광고비만 보인다.
    """
    ch = rocket_1p_channel(db)
    if ch is None:
        return []

    basis = normalize_basis(basis)
    by_day: dict[str, dict[str, Decimal]] = {}

    if basis == BASIS_SALES:
        for key, v in _sell_through_by_day(db, date_from, date_to).items():
            by_day.setdefault(key, {"revenue": ZERO, "ad_spend": ZERO})["revenue"] += v["revenue"]
    else:
        rev_rows = (
            db.query(CoupangRocketSettlement.issue_date, func.sum(_REVENUE_COLUMN))
            .filter(
                CoupangRocketSettlement.vendor_id == ROCKET_1P_VENDOR_ID,
                CoupangRocketSettlement.issue_date >= date_from,
                CoupangRocketSettlement.issue_date <= date_to,
            )
            .group_by(CoupangRocketSettlement.issue_date)
            .all()
        )
        for d, amt in rev_rows:
            if d is None:
                continue
            key = d.isoformat() if hasattr(d, "isoformat") else str(d)
            by_day.setdefault(key, {"revenue": ZERO, "ad_spend": ZERO})["revenue"] += _dec(amt)

    # 광고비는 축과 무관하게 **같은 합집합 함수**를 쓴다 — 요약 합계와 추이 합계가 어긋나면
    # 화면 두 곳의 RoAS가 달라진다(기존 calculate_channel_daily_trend 주석의 같은 이유).
    for key, amt in _ad_spend_by_day(db, date_from, date_to).items():
        by_day.setdefault(key, {"revenue": ZERO, "ad_spend": ZERO})["ad_spend"] += amt

    return [
        {
            "channel_id": ch.id,
            "channel_name": ch.name or "",
            "date": day,
            "revenue": str(v["revenue"]),
            "product_revenue": str(v["revenue"]),
            "shipping_revenue": "0",
            "ad_spend": str(v["ad_spend"]),
            "net_profit": None,   # ★위 docstring 참조 — 일자 단위로는 내지 않는다
        }
        for day, v in sorted(by_day.items())
    ]
