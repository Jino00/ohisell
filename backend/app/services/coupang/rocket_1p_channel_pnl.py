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
    return _settlement_window(db, date_from, date_to)["revenue"]


# ★귀속일 = 라인의 실입고일(D-CPP-20).
#
# ⚠️2026-08-07 정정 — 이 자리에 있던 근거 서술은 **틀렸다**(ref 50 §10에 실측).
#   종전 주석: "작성일−실입고일 = −4~+7일 / 2026-07에 6,356,855원이 다른 달 입고분 /
#   18,388,235원은 여러 날이 섞인 계산서". 그 수치들은 계산서 라인이 없던 시점에
#   **PO의 납품예정일**을 대리 변수로 삼아 잰 것이고, 실제 라인이 들어오자 뒤집혔다:
#     · 정산라인이 있는 **계산서 480건 전건**에서 issue_date = 라인 입고일(최대=최소)
#     · 라인 입고일이 **2일 이상에 걸친 계산서 0건** ("여러 날이 섞인 계산서"는 없다)
#     · 두 축(작성일 vs 실입고일)의 **일별 합계가 305일 전건 동일**, 총액도 동일
#   즉 **이 전환은 숫자를 하나도 바꾸지 않는다.** 계산서는 하루치 입고 단위로 끊기고
#   작성일이 곧 그 입고일이기 때문이다.
#
#   그런데도 라인 축을 정본으로 유지하는 이유는 둘이다(되돌리지 말 것):
#     ① 작성일=입고일은 **쿠팡 발행 관행에 대한 관측**이지 계약된 보장이 아니다. 관행이
#        바뀌어 계산서가 여러 날을 묶기 시작하면 라인 축은 자동으로 옳고 작성일 축은 틀린다.
#     ② 라인은 **SKU 그레인**이라 원가 결합·미정산 재판정(ref 50)이 여기 얹힌다.
#        작성일 축으로는 그 경로가 아예 열리지 않는다.
#   ★이 전환의 실제 산출물은 「날짜 교정」이 아니라 **SKU 그레인 정산 라인 그 자체**였다.
#
# ★라인이 아직 없는 계산서는 **작성일로 폴백**한다(0으로 접지 않는다, 원칙22).
#   라인 수집은 계산서별 화면을 훑어야 해서 점진적으로 찬다. 폴백이 없으면 그 사이 매출이
#   통째로 사라진다. 라인이 찬 계산서부터 날짜가 정확해지고, 커버리지는 아래가 함께 낸다.
_LINE_SUM_SQL = """
SELECT COALESCE(SUM(i.total_price), 0) AS amt,
       COUNT(DISTINCT i.invoice_seq)   AS invoices
FROM coupang_rocket_settlement_item i
WHERE i.vendor_id = :vendor
  AND date(i.received_at) >= :since AND date(i.received_at) <= :until
"""

# 라인이 **하나도 없는** 계산서만 작성일 기준으로 더한다(라인 있는 것과 이중계상 금지).
_HEADER_FALLBACK_SQL = """
SELECT COALESCE(SUM(s.payment_amount), 0) AS amt, COUNT(*) AS invoices
FROM coupang_rocket_settlement s
WHERE s.vendor_id = :vendor
  AND s.issue_date >= :since AND s.issue_date <= :until
  AND NOT EXISTS (SELECT 1 FROM coupang_rocket_settlement_item i
                  WHERE i.invoice_seq = s.invoice_seq)
"""


_LINE_BY_DAY_SQL = """
SELECT date(i.received_at) AS d, COALESCE(SUM(i.total_price), 0) AS amt
FROM coupang_rocket_settlement_item i
WHERE i.vendor_id = :vendor
  AND date(i.received_at) >= :since AND date(i.received_at) <= :until
GROUP BY date(i.received_at)
"""

_HEADER_BY_DAY_SQL = """
SELECT s.issue_date AS d, COALESCE(SUM(s.payment_amount), 0) AS amt
FROM coupang_rocket_settlement s
WHERE s.vendor_id = :vendor
  AND s.issue_date >= :since AND s.issue_date <= :until
  AND NOT EXISTS (SELECT 1 FROM coupang_rocket_settlement_item i
                  WHERE i.invoice_seq = s.invoice_seq)
GROUP BY s.issue_date
"""


def _has_item_table(db: Session) -> bool:
    """라인 테이블이 아직 없는 환경(마이그 전 구코드)에서도 죽지 않게."""
    try:
        return sa_inspect(db.get_bind()).has_table("coupang_rocket_settlement_item")
    except Exception:  # noqa: BLE001
        return False


def _settlement_window(db: Session, date_from: date, date_to: date) -> dict:
    """계산서 축 창 합계 + 귀속 근거.

    반환 {revenue, line_amount, fallback_amount, line_invoices, fallback_invoices, line_ratio}.
    line_ratio = 실입고일로 귀속된 금액 비율 — 화면이 "이 숫자가 얼마나 정확한 날짜인지" 말할 수 있게.
    """
    params = {"vendor": ROCKET_1P_VENDOR_ID,
              "since": date_from.isoformat(), "until": date_to.isoformat()}
    if not _has_item_table(db):
        total = (
            db.query(func.sum(_REVENUE_COLUMN))
            .filter(
                CoupangRocketSettlement.vendor_id == ROCKET_1P_VENDOR_ID,
                CoupangRocketSettlement.issue_date >= date_from,
                CoupangRocketSettlement.issue_date <= date_to,
            )
            .scalar()
        )
        amt = _dec(total)
        return {"revenue": amt, "line_amount": ZERO, "fallback_amount": amt,
                "line_invoices": 0, "fallback_invoices": 0, "line_ratio": ZERO}

    lamt, linv = db.execute(text(_LINE_SUM_SQL), params).fetchone()
    famt, finv = db.execute(text(_HEADER_FALLBACK_SQL), params).fetchone()
    line_amount, fb_amount = _dec(lamt), _dec(famt)
    revenue = line_amount + fb_amount
    ratio = (line_amount / revenue) if revenue else ZERO
    return {"revenue": revenue, "line_amount": line_amount, "fallback_amount": fb_amount,
            "line_invoices": int(linv or 0), "fallback_invoices": int(finv or 0),
            "line_ratio": ratio}


def _settlement_by_day(db: Session, date_from: date, date_to: date) -> dict[str, Decimal]:
    """계산서 축 일별 매출 — 요약(`_settlement_window`)과 **같은 규칙**으로 쪼갠다.

    라인 있으면 실입고일, 없으면 작성일 폴백. 두 축이 다르면 합계와 추이가 어긋난다.
    """
    params = {"vendor": ROCKET_1P_VENDOR_ID,
              "since": date_from.isoformat(), "until": date_to.isoformat()}
    out: dict[str, Decimal] = {}
    if not _has_item_table(db):
        rows = (
            db.query(CoupangRocketSettlement.issue_date, func.sum(_REVENUE_COLUMN))
            .filter(
                CoupangRocketSettlement.vendor_id == ROCKET_1P_VENDOR_ID,
                CoupangRocketSettlement.issue_date >= date_from,
                CoupangRocketSettlement.issue_date <= date_to,
            )
            .group_by(CoupangRocketSettlement.issue_date)
            .all()
        )
        for d, amt in rows:
            if d is None:
                continue
            key = d.isoformat() if hasattr(d, "isoformat") else str(d)
            out[key] = out.get(key, ZERO) + _dec(amt)
        return out
    for sql in (_LINE_BY_DAY_SQL, _HEADER_BY_DAY_SQL):
        for d, amt in db.execute(text(sql), params).fetchall():
            if not d:
                continue
            key = d.isoformat() if hasattr(d, "isoformat") else str(d)[:10]
            out[key] = out.get(key, ZERO) + _dec(amt)
    return out


def _ad_spend_by_day(db: Session, date_from: date, date_to: date,
                     vendor_id: str | None = None) -> dict[str, Decimal]:
    """창 내 1P 광고비를 **날짜별로** 낸다. 원천이 둘이라 날짜 단위 합집합으로 합친다.

    ★★이 함수가 1P 광고 축의 **단일 진실 원천**이다(2026-08-06 적대 리뷰 P1).
      그 전엔 `rocket_intelligence._agg_rocket_ad`가 같은 축을 따로 구현해 `coupang_ad_report`만
      읽었고, 그래서 `/api/overview/rocket-overview`에서 **2026-03-17~05-18 63일 43,147,487원이
      통째로 빠졌다**(수기 XLSX 시대). 두 엔진이 같은 축을 각자 구현한 것이 사고의 구조적 원인이라,
      데이터를 옮기는 대신 **호출을 여기로 모았다**. 광고 원천이 늘면 이 함수만 고친다.

    vendor_id: 자동수집(`coupang_ad_report`) 필터. None이면 1P 계정(기본)으로 본다.
      ★수기 XLSX(`ad_costs` ch5)는 **채널 그레인**이라 vendor 축이 없다 — 1P 채널 그 자체다.
        그래서 1P 계정을 볼 때만 합치고, 다른 vendor를 명시하면 자동수집만 본다(섞으면 남의
        계정 창에 1P 수기분이 얹힌다).

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
    # ① 수기 XLSX(과거) — 채널 그레인. 1P 계정 창에서만 합친다(위 vendor_id 주석).
    ch = rocket_1p_channel(db) if vendor_id in (None, ROCKET_1P_VENDOR_ID) else None
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
            CoupangAdReport.vendor_id == (vendor_id or ROCKET_1P_VENDOR_ID),
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


def _ad_spend_sum(db: Session, date_from: date, date_to: date,
                  vendor_id: str | None = None) -> Decimal:
    return sum(_ad_spend_by_day(db, date_from, date_to, vendor_id).values(), ZERO)


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

# ── 「납품단가」와 「원가」의 단일 정의 (재도출 금지) ──────────────────────
# ★두 CTE는 **여기가 유일한 정의**다. 매출 두 축 대조 화면(S2)이 같은 개념을 옵션
#   그레인으로 쓰는데, 거기서 SQL을 따로 쓰면 언젠가 갈라진다 — 그리고 갈라지면 화면 둘이
#   다른 이익을 말하는데 어느 쪽이 맞는지 아무도 모른다. 광고 축이 실제로 그렇게 갈라져
#   63일 43,147,487원이 통째로 빠졌었다(위 `_ad_spend_by_day` 주석). 같은 실수를 안 한다.
LATEST_UNIT_PRICE_CTE = """
price AS (           -- SKU별 최근 납품단가(발주 라인에서, PO 최신순)
  SELECT product_number, unit_purchase_price,
         ROW_NUMBER() OVER (PARTITION BY product_number ORDER BY purchase_order_seq DESC) rn
  FROM coupang_rocket_purchase_order_item
),
p AS (SELECT product_number, unit_purchase_price FROM price WHERE rn = 1)
"""

COST_BY_PRODUCT_CTE = """
cost AS (            -- 상품번호 → internal_sku → 등록원가(부가세 포함 축, D 2026-08-04)
  SELECT m.product_number, pm.cost_price
  FROM rocket_product_cost_map m
  JOIN product_master pm ON pm.internal_sku = m.internal_sku
  WHERE m.status = 'confirmed' AND pm.cost_price IS NOT NULL
)
"""

_SELL_THROUGH_SQL = f"""
WITH {LATEST_UNIT_PRICE_CTE}, {COST_BY_PRODUCT_CTE}
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


# 창에 걸치는 프로모션 중 **할인액 원천이 없는** 것의 수. >0이면 분담금은 "모름"이다.
#   ★분담금 0과 모름을 가르는 판정자: 그 기간에 프로모션이 **있었는가**.
#     - 프로모션이 아예 없었다 → 분담금 0은 참이다.
#     - 프로모션은 있었는데 그 제안서를 아직 못 받았다 → **모름**. 0으로 접으면 그 프로모션의
#       할인액만큼 이익이 부풀어 보인다.
_PROMO_UNPRICED_SQL = """
SELECT COUNT(*) FROM coupang_rocket_promotion pr
WHERE pr.vendor_id = :vendor
  AND date(pr.start_at) <= :until AND date(pr.end_at) >= :since
  AND NOT EXISTS (SELECT 1 FROM coupang_promo_discount_item d WHERE d.request_id = pr.request_id)
"""


def _promo_burden_by_day(db: Session, date_from: date, date_to: date) -> dict[str, Decimal] | None:
    """일자별 프로모션 분담금. **모르면 None** — 0으로 접지 않는다.

    분담률은 실측 13건 전부 100%(전액 우리 부담)라 할인액이 곧 분담금이다(D-CPP-10).

    모름의 경로가 둘이다:
      ① 원천 테이블 자체가 없다(마이그레이션 전 환경).
      ② 테이블은 있는데 **창에 걸친 프로모션의 할인액이 비어 있다** — 제안서를 아직 못 받은
         기간이 그렇다. 테이블이 main에 병합되면서(2026-08-05, PR #200) ①만으로는 못 막게 됐다:
         빈 테이블이 곧 "분담금 0"으로 읽혀, 수집 전 기간의 순이익이 분담금 없음을 전제로
         계산된다. 같은 실패가 형태만 바꿔 돌아온 것이라 판정자를 프로모션 존재로 옮긴다.
    프로모션이 창에 하나도 없으면 빈 dict(=0)를 낸다 — 그건 추정이 아니라 사실이다.
    """
    bind = db.get_bind()
    insp = sa_inspect(bind)
    if not insp.has_table("coupang_promo_discount_item"):
        return None
    if insp.has_table("coupang_rocket_promotion"):
        unpriced = db.execute(
            text(_PROMO_UNPRICED_SQL),
            {"vendor": ROCKET_1P_VENDOR_ID,
             "since": date_from.isoformat(), "until": date_to.isoformat()},
        ).scalar()
        if int(unpriced or 0) > 0:
            log.info("분담금 미상 — 창에 걸친 프로모션 %s건의 할인액 원천이 없다(순이익 미산정)",
                     unpriced)
            return None
    rows = db.execute(
        text(_PROMO_BURDEN_SQL),
        {"vendor": ROCKET_1P_VENDOR_ID,
         "since": date_from.isoformat(), "until": date_to.isoformat()},
    ).fetchall()
    return {str(d)[:10]: _dec(b) for d, b in rows}


_PROMO_BURDEN_BY_OPTION_SQL = _PROMO_BURDEN_SQL.replace(
    "SELECT s.date AS d,", "SELECT s.option_id AS d,"
).replace("GROUP BY s.date", "GROUP BY s.option_id")


def promo_burden_by_option(
    db: Session, date_from: date, date_to: date, vendor_id: str | None = None
) -> dict[str, Decimal] | None:
    """옵션별 프로모션 분담금. **모르면 None** — `_promo_burden_by_day`와 같은 판정자다.

    왜 여기 있나: 매출 화면이 옵션별 손익을 내려면 옵션 그레인 분담금이 필요한데, "분담금을
    모를 때가 언제인가"는 회계 규칙이라 이 파일이 정본이다. 판정 규칙이 두 곳에 생기면
    한쪽만 고쳐져 화면이 "분담금 0"을 사실처럼 낸다(그게 정확히 2026-08-05에 났던 사고다).
    """
    guard = _promo_burden_by_day(db, date_from, date_to)
    if guard is None:
        return None
    rows = db.execute(
        text(_PROMO_BURDEN_BY_OPTION_SQL),
        {"vendor": vendor_id or ROCKET_1P_VENDOR_ID,
         "since": date_from.isoformat(), "until": date_to.isoformat()},
    ).fetchall()
    return {str(o): _dec(b) for o, b in rows}


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
        "revenue": rev, "revenue_costed": rev_costed,
        "cost": cost, "promo_burden": burden, "ad_spend": ad,
        "coverage": coverage, "net_profit": net, "by_day": by_day,
        "qty": sum(v["qty"] for v in by_day.values()),
    }


def window_freshness(db: Session, date_from: date, date_to: date,
                     vendor_id: str | None = None) -> dict:
    """요청한 창에 판매분석 데이터가 **실제로 며칠** 들어 있는지(2026-08-06 적대 리뷰 P1).

    왜 필요한가: 쿠팡 판매분석은 **당일·전일치를 주지 않는다**. 그래서 "최근 7일"을 열면
    늘 5일치만 들어오고, 화면이 그걸 7일이라 말하면 주간 비교에서 이번 주가 **항상** 20%
    낮게 나온다. 더 나쁜 건 수집이 진짜 멈췄을 때도 화면상 구별할 수단이 없다는 것이다
    (green-while-stale — 이 프로젝트가 반복해 당한 형태).

    ★`days_no_data`는 **날짜 축**으로 센다. 옵션별 NULL 컬럼 카운트로는 "그 날 행이 통째로
      없음"을 원리적으로 감지할 수 없다 — GROUP BY 집계에서 아예 나타나지 않기 때문이다.
    ★`stale`은 원천의 정상 지연(D-1~D-2)과 진짜 정지를 가른다. 늘 경고가 떠 있으면
      아무도 안 본다 — 경보가 거짓말하면 경보가 죽는다.
    """
    row = db.execute(
        text(
            "SELECT COUNT(DISTINCT date), MAX(date) FROM coupang_rocket_sales_daily "
            "WHERE vendor_id = :vendor AND date >= :since AND date <= :until"
        ),
        {"vendor": vendor_id or ROCKET_1P_VENDOR_ID,
         "since": date_from.isoformat(), "until": date_to.isoformat()},
    ).fetchone()
    days_with_data = int(row[0] or 0) if row else 0
    data_as_of = str(row[1])[:10] if row and row[1] else None
    expected = (date_to - date_from).days + 1
    lag = None if data_as_of is None else (date_to - date.fromisoformat(data_as_of)).days
    return {
        "days_expected": expected,
        "days_with_data": days_with_data,
        "days_no_data": max(0, expected - days_with_data),
        "data_as_of": data_as_of,
        "lag_days": lag,
        # 원천은 보통 D-1~D-2까지만 준다 → 그 이상 벌어져야 진짜 정지 신호로 본다.
        "stale": bool(lag is not None and lag > _FRESHNESS_LAG_OK),
        "note": "판매분석은 당일·전일치를 주지 않는다. days_no_data가 0이 아닌 것 자체는 "
                "정상일 수 있고, stale=true여야 수집 정지를 의심한다.",
    }


# 원천의 정상 지연 상한(일). 이 이상 벌어지면 수집 정지를 의심한다.
_FRESHNESS_LAG_OK = int(os.getenv("ROCKET_1P_FRESHNESS_LAG_OK") or "3")

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
        # ★요약(_settlement_window)과 **같은 축**으로 쪼갠다 — 두 곳이 다르면 화면의 합계와
        #   추이가 어긋나고 RoAS가 두 값이 된다. 라인 있으면 실입고일, 없으면 작성일 폴백.
        for d, amt in _settlement_by_day(db, date_from, date_to).items():
            by_day.setdefault(d, {"revenue": ZERO, "ad_spend": ZERO})["revenue"] += amt

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
