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
from datetime import date
from decimal import Decimal

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models import Channel, CoupangAdReport, CoupangRocketSettlement

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


def compute_rocket_1p_summary_row(
    db: Session, date_from: date, date_to: date
) -> dict | None:
    """1P 채널 요약 행 — `calculate_channel_summary` 출력과 **같은 모양**.

    매출도 광고비도 0이면 None(빈 행을 화면에 만들지 않는다).
    반환 형태는 매출-only 채널 선례(profit_calculator의 수동매출 행)를 그대로 따른다.
    """
    ch = rocket_1p_channel(db)
    if ch is None:
        log.warning("로켓1P 채널(code=%s)이 없다 — 1P 행을 만들지 않는다", ROCKET_1P_CHANNEL_CODE)
        return None

    revenue = _settlement_sum(db, date_from, date_to)
    ad_spend = _ad_spend_sum(db, date_from, date_to)
    if revenue == ZERO and ad_spend == ZERO:
        return None

    return {
        "channel_id": ch.id,
        "channel_name": ch.name or "",
        "revenue": str(revenue),
        # 1P는 판매수수료·배송비가 없다(쿠팡 부담) → 전액 상품매출, 배송매출 0.
        "product_revenue": str(revenue),
        "shipping_revenue": "0",
        "cost": "0",          # 미산정 — 파일 헤더 주석 참조
        "commission": "0",    # 1P는 판매수수료 없음(납품가 축)
        "ad_spend": str(ad_spend),
        "shipping": "0",
        "net_profit": None,   # ★원가 근거 없음 → 지어내지 않는다
        "profit_rate": None,
        "order_count": 0,     # 계산서 그레인엔 주문 개념이 없다
    }


def compute_rocket_1p_daily_points(
    db: Session, date_from: date, date_to: date
) -> list[dict]:
    """1P 일자별 추이 — `calculate_channel_daily_trend` 출력과 같은 모양.

    ★매출이 **계산서 작성일자**에 몰려 뜬다(주 3~4장). 판매가 그날 몰린 게 아니라 계산서가
      그날 발행된 것이다 — 일자 추이는 뾰족하게 보이는 게 정상이다. 판매 시점 축이 필요하면
      sell-through(판매분석)를 써야 하는데 그건 매출 정의가 다르다(납품가 × 판매수량).
    """
    ch = rocket_1p_channel(db)
    if ch is None:
        return []

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
    by_day: dict[str, dict[str, Decimal]] = {}
    for d, amt in rev_rows:
        if d is None:
            continue
        key = d.isoformat() if hasattr(d, "isoformat") else str(d)
        by_day.setdefault(key, {"revenue": ZERO, "ad_spend": ZERO})["revenue"] += _dec(amt)
    # 광고비는 요약과 **같은 합집합 함수**를 쓴다 — 요약 합계와 추이 합계가 어긋나면
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
            "net_profit": None,   # ★요약과 같은 이유
        }
        for day, v in sorted(by_day.items())
    ]
