# profit_calculator.py — 핵심 이익률 계산 엔진
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from datetime import time as dtime
from decimal import ROUND_DOWN, Decimal

from collections import defaultdict

from sqlalchemy import and_, func, text
from sqlalchemy.orm import Session

from app.models import Channel, CoupangRgSettlementFee, Order, ProductChannelMapping, ProductMaster
from app.services import order_delivery
from app.services.cafe24_status_mapper import REVENUE_EXCLUDED
from app.services.coupang.option_fee_rate import (
    commission_for,
    option_fee_rates,
    resolve_rate,
)
from app.services.coupang.revenue_fee_source import COUPANG_3P_CODES
from app.services.manual_revenue_service import get_daily_manual_revenue
from app.services.monthly_fixed_cost_service import get_daily_fixed_cost

log = logging.getLogger(__name__)

ZERO = Decimal("0")

# ── 순이익이 무엇을 담고 있는지 행 스스로 말하게 하는 라벨 (D-22, 2026-08-19) ──
# 손익을 못 재는 채널(로켓1P 계산서 축)도 **광고비는 확정 비용**이라 `-광고비` 하한을 낸다.
# 종전엔 그런 행의 net을 통째로 None으로 두었는데, 부모 소계는 그 자식의 광고비만 흡수하고
# 손익은 안 흡수해서 나간 돈이 화면에서 사라졌다.
NET_SCOPE_FULL = "full"        # 매출·원가·수수료·광고비까지 다 반영
NET_SCOPE_AD_ONLY = "ad_only"  # 광고비만 반영된 하한(원가·매출이 손익에 안 닿는 구간)
NET_SCOPE_PARTIAL = "partial"  # 위 둘이 섞인 소계

# Meta 광고비 키워드 (캠페인명 매칭용)
_META_KEYWORDS = [
    "지문방지필름", "골프필름", "버디필름", "강화유리", "셀카봉", "문캅스", "일미리케이스",
]
_KEYWORD_ALIAS = {"샐카봉": "셀카봉"}

# Naver SA 광고비 키워드 (sync_naver_sa_ad_costs.py의 PRODUCT_KEYWORDS와 동일 순서)
_NAVER_SA_KEYWORDS = [
    "지문방지", "강화유리", "종이질감", "사생활", "갤럭시탭", "아이패드", "아이폰",
    "갤럭시", "셀카봉", "뮤패드", "케이스",
]


def _extract_product_keyword(name: str) -> str | None:
    for kw in _META_KEYWORDS:
        if kw in (name or ""):
            return _KEYWORD_ALIAS.get(kw, kw)
    return None


def _extract_naver_product_keyword(name: str) -> str | None:
    for kw in _NAVER_SA_KEYWORDS:
        if kw in (name or ""):
            return kw
    return None


def _get_cafe24_channel_id(channel_map: dict) -> int | None:
    for cid, ch in channel_map.items():
        if ch.code == "CAFE24":
            return cid
    return None


def _get_naver_channel_id(channel_map: dict) -> int | None:
    for cid, ch in channel_map.items():
        if ch.code == "NAVER":
            return cid
    return None


def _get_meta_ad_spend_daily(
    db: Session, cafe24_channel_id: int, date_from: date, date_to: date
) -> dict[str, Decimal]:
    """ad_costs → Meta 일별 총 광고비 {date_str: spend}"""
    rows = db.execute(
        text("""
            SELECT ad_date, SUM(CAST(ad_spend AS REAL))
            FROM ad_costs
            WHERE channel_id = :cid AND source LIKE 'meta:%'
              AND ad_date >= :since AND ad_date <= :until
            GROUP BY ad_date
        """),
        {"cid": cafe24_channel_id, "since": date_from.isoformat(), "until": date_to.isoformat()},
    ).fetchall()
    return {str(r[0]): Decimal(str(r[1])) for r in rows if r[1]}


def _get_meta_ad_spend_by_keyword_day(
    db: Session, cafe24_channel_id: int, date_from: date, date_to: date
) -> dict[tuple[str, str], Decimal]:
    """ad_costs → Meta 키워드/일별 광고비 {(keyword, date_str): spend} (기타 제외)"""
    rows = db.execute(
        text("""
            SELECT source, ad_date, SUM(CAST(ad_spend AS REAL))
            FROM ad_costs
            WHERE channel_id = :cid AND source LIKE 'meta:%'
              AND source != 'meta:기타'
              AND ad_date >= :since AND ad_date <= :until
            GROUP BY source, ad_date
        """),
        {"cid": cafe24_channel_id, "since": date_from.isoformat(), "until": date_to.isoformat()},
    ).fetchall()
    return {(str(r[0])[5:], str(r[1])): Decimal(str(r[2])) for r in rows if r[2]}


def _get_naver_sa_ad_spend_daily(
    db: Session, naver_channel_id: int, date_from: date, date_to: date
) -> dict[str, Decimal]:
    """ad_costs → Naver SA 일별 총 광고비 {date_str: spend}"""
    rows = db.execute(
        text("""
            SELECT ad_date, SUM(CAST(ad_spend AS REAL))
            FROM ad_costs
            WHERE channel_id = :cid AND source LIKE 'naver_sa:%'
              AND ad_date >= :since AND ad_date <= :until
            GROUP BY ad_date
        """),
        {"cid": naver_channel_id, "since": date_from.isoformat(), "until": date_to.isoformat()},
    ).fetchall()
    return {str(r[0]): Decimal(str(r[1])) for r in rows if r[1]}


def _get_naver_sa_ad_spend_by_keyword_day(
    db: Session, naver_channel_id: int, date_from: date, date_to: date
) -> dict[tuple[str, str], Decimal]:
    """ad_costs → Naver SA 키워드/일별 광고비 {(keyword, date_str): spend} (기타 제외)"""
    rows = db.execute(
        text("""
            SELECT source, ad_date, SUM(CAST(ad_spend AS REAL))
            FROM ad_costs
            WHERE channel_id = :cid AND source LIKE 'naver_sa:%'
              AND source != 'naver_sa:기타'
              AND ad_date >= :since AND ad_date <= :until
            GROUP BY source, ad_date
        """),
        {"cid": naver_channel_id, "since": date_from.isoformat(), "until": date_to.isoformat()},
    ).fetchall()
    return {(str(r[0])[9:], str(r[1])): Decimal(str(r[2])) for r in rows if r[2]}


def _get_gfa_ad_spend_daily(
    db: Session, naver_channel_id: int, date_from: date, date_to: date
) -> dict[str, Decimal]:
    """ad_costs → GFA(ADVoost) 일별 총 광고비 {date_str: spend}

    ★`gfa:%` 계열 전체를 읽는다: 수동 CSV 시절의 `gfa:쇼핑`(~2026-06-04)과 비즈머니 실차감
    자동 적재분(`gfa:advoost`·`gfa:da`, 2026-06-05~)이 한 계열로 이어지기 때문이다.
    두 경로가 같은 날짜를 채우면 이중계상이 되므로, 쓰는 쪽(naver_display_ad_costs)이
    `gfa:쇼핑`이 있는 날짜를 건너뛰어 겹침을 막는다.
    """
    rows = db.execute(
        text("""
            SELECT ad_date, SUM(CAST(ad_spend AS REAL))
            FROM ad_costs
            WHERE channel_id = :cid AND source LIKE 'gfa:%'
              AND ad_date >= :since AND ad_date <= :until
            GROUP BY ad_date
        """),
        {"cid": naver_channel_id, "since": date_from.isoformat(), "until": date_to.isoformat()},
    ).fetchall()
    return {str(r[0]): Decimal(str(r[1])) for r in rows if r[1]}


def _line_commission(
    ch: Channel | None,
    o: Order,
    revenue: Decimal,
    fee_rates: dict[tuple[str, str], Decimal] | None = None,
) -> Decimal:
    """라인 수수료. 채널별 실측 우선:
    - cafe24/naver: 동기화 시 산출된 commission_amount.
    - 쿠팡 3P(WING1/2): 그 옵션의 정산 실측 «요율» × 매출 × 1.1, 요율 미상이면 채널 정률(D-CPP-32).
    - 그 외(쿠팡 RG/로켓·미지정): 채널 정률.
    fee_rates = (account_key, vendor_item_id)→요율(소수) 룩업(원칙18-6 Harness 주입). None이면 채널 정률."""
    if ch and ch.code == "CAFE24":
        return o.commission_amount if o.commission_amount is not None else ZERO
    if ch and ch.code == "NAVER":
        if o.commission_amount is not None:
            return o.commission_amount
        # commission_amount 없으면 채널 정률 폴백 (API 미지원 케이스 방어)
        rate = ch.commission_rate if ch else ZERO
        return revenue * rate / Decimal("100")
    # 쿠팡 3P(WING1/2): 그 옵션의 «요율» × 매출 × 1.1 (D-CPP-32).
    # ★왜 실측 «금액» 우선을 그만뒀나: 정산 행에서 service_fee = sale_amount×service_fee_ratio가
    #   661건 전수 성립하므로 실측 금액과 요율 계산은 같은 값이다(라이브 2026-08-10, 최대 차 0.8원
    #   반올림). 다른 점은 커버리지뿐 — 정산은 D+9~10 지연되므로 실측 금액만 쓰면 최근 주문이 폴백에
    #   떨어지고, 그 폴백이 계정 단일 정률이라 옵션별 요율 차이를 뭉갠다(WING2 6.4%·WING1 10.5/10.8%).
    #   요율은 옵션당 상수다(시기에 따라 바뀐 사례 전 기간 0건).
    if ch and ch.code in COUPANG_3P_CODES:
        rate, _basis = resolve_rate(
            fee_rates or {}, ch.code, o.platform_product_id,
            default=(ch.commission_rate / Decimal("100")) if ch.commission_rate else None,
        )
        return commission_for(revenue, rate)
    rate = ch.commission_rate if ch else ZERO
    return revenue * rate / Decimal("100")


def _coupang_3p_fee_rates(
    db: Session, orders: list[Order], channel_map: dict[int, Channel]
) -> dict[tuple[str, str], Decimal]:
    """3P(WING1/2) 옵션별 수수료 «요율» 룩업을 1회 생성(원칙18-6/8 허브 주입).

    각 집계 함수가 라인 루프 전에 한 번 호출해 _line_commission에 주입 → N×스캔 제거.
    ★주문번호가 아니라 계정 단위로 받는다 — 요율은 창 안 주문의 정산을 기다릴 필요가 없고
    (그게 D-CPP-32의 요점이다) 같은 옵션의 과거 정산이면 무엇이든 요율을 알려주기 때문이다."""
    account_keys = sorted({
        c.code
        for o in orders
        if (c := channel_map.get(o.channel_id)) and c.code in COUPANG_3P_CODES
    })
    if not account_keys:
        return {}
    return option_fee_rates(db, account_keys)


# 한진택배 주문(배송) 1건당 판매자 지급액 — 고객 결제 여부 무관.
# ★네이버 N배송(도착보장)은 단가가 다르다(3,020) → 정액 상수를 직접 쓰지 말고
#   _shipment_cost()를 통해 건별 단가를 받는다. 이 상수는 비-네이버 채널의 값이자
#   판별 불가 시 폴백이며, order_delivery.SHIPPING_COST_NORMAL과 같은 값이다.
HANJIN_PER_SHIPMENT = Decimal("1900")


def _shipment_cost(o: Order) -> Decimal:
    """물리 배송 1건에 우리가 지불하는 택배비(VAT 포함).

    ★2026-08-03 배선: 종전에는 전 채널·전 건 1,900 정액이었다. 07-2x부터 네이버 주문의
      절반 이상이 N배송(도착보장, 건당 3,020)으로 전환되면서 정액 가정이 배송비를
      과소계상하게 됐다(라이브 실측 2026-07-27~08-02: 653패키지 400,960원 과소).
      BEP 엔진(bep_calculator)은 이미 배송방식별 단가를 쓰고 있었으므로, 같은 계정에서
      두 엔진이 서로 다른 배송비를 쓰던 상태를 여기서 해소한다.

    단가 판별은 order_delivery(단일 진실 원천, D-NAO-84)에 위임한다 —
    orders.shipping_cost_paid(주문 시점 스냅샷) 우선, 없으면 raw_data 파싱 폴백.
    ★비-네이버 채널은 productOrder 키가 없어 항상 SHIPPING_COST_NORMAL(=1,900)이 나온다.
      즉 쿠팡·cafe24의 동작은 바뀌지 않는다(라이브 실측: 채널 1·2·7 전 행 shipping_cost_paid NULL).
    """
    return order_delivery.order_shipping_cost(o)


def _shipment_cost_map(orders: list, channel_map: dict) -> dict[tuple, Decimal]:
    """집계 대상 주문 전체를 **선행 1회 훑어** 배송(skey)별 택배비를 확정한다.

    ★패키지 내 단가가 갈리면 **최댓값**. 라이브 실측으로는 한 패키지 안에서 배송방식이
      섞인 경우가 0/10,159건이지만, N배송 전환이 진행 중이라 앞으로 생길 수 있다.
      그때 낮은 쪽을 집으면 배송비가 과소계상돼 이익이 부풀려진다 — 최댓값이면 그
      방향으로는 틀리지 않는다.

    ★선행 패스인 이유(codex 리뷰 P2): 처음엔 본 루프에서 만나는 대로 더하고 더 비싼
      단가를 만나면 차액을 보정했는데, 일별 트렌드는 **날짜별 버킷**이라 같은 배송이
      두 날짜에 걸치면 앞 날짜에 1,900 · 뒤 날짜에 1,120이 쪼개져 들어갔다. 기간 합계는
      맞지만 일별 손익이 DB 반환 순서에 따라 달라진다 — "배송 1건은 한 번에 한 곳"이라는
      계약이 버킷 수준에서 깨진다. 금액을 먼저 확정하면 순서와 무관해진다.
      (라이브 실측 0/10,159건이지만, 순서 의존은 조용히 틀리는 종류라 구조로 없앤다.)

    집계에서 빠지는 주문(위탁 채널·취소/반품/입금전)은 본 루프와 **같은 규칙**으로
    제외한다 — 여기서 포함해 버리면 매출 0인 배송에 택배비만 잡힌다.
    """
    costs: dict[tuple, Decimal] = {}
    for o in orders:
        ch = channel_map.get(o.channel_id)
        if ch and ch.channel_type == "consignment":
            continue
        if o.status in REVENUE_EXCLUDED:
            continue
        skey = _shipment_key(ch, o)
        cost = _shipment_cost(o)
        if cost > costs.get(skey, ZERO):
            costs[skey] = cost
    return costs


def _delivery_income_map(orders: list, channel_map: dict) -> dict[tuple, Decimal]:
    """배송(skey)별 **배송비 수입**을 선행 1회 훑어 확정한다.

    ★선행 패스인 이유(codex 2R P2): 종전엔 본 루프에서 first-wins로 잡았는데, 쿼리에 결정적
      정렬이 없고 동기화가 라인을 각각 갱신하므로 한 패키지 안에서 값이 갈리면 **매출·VAT·
      수수료가 임의로 정해진다**. _shipment_cost_map과 같은 형태로 맞춰 순서 의존을 없앤다.

    ★갈릴 때 규칙 = **최솟값**(비용 쪽 _shipment_cost_map의 최댓값과 반대). 수입은 적게, 비용은
      많게가 보수 방향이라 이익이 부풀려지지 않는다. 갈린 사실 자체는 조용히 넘기지 않고
      로그로 표면화한다 — 라이브 실측은 9,614패키지 전부 단일 값(혼재 0건)이라, 로그가 찍히면
      그건 원천 데이터 성질이 바뀐 신호다.
    """
    income: dict[tuple, Decimal] = {}
    conflicts = 0
    for o in orders:
        ch = channel_map.get(o.channel_id)
        if ch and ch.channel_type == "consignment":
            continue
        if o.status in REVENUE_EXCLUDED:
            continue
        deliv = _delivery_income(ch, o)
        if deliv <= 0:
            continue
        skey = _shipment_key(ch, o)
        prev = income.get(skey)
        if prev is None:
            income[skey] = deliv
        elif prev != deliv:
            conflicts += 1
            income[skey] = min(prev, deliv)
    if conflicts:
        log.warning("배송비 수입이 한 배송 안에서 갈림 %d건 — 최솟값 채택(원천 데이터 확인 필요)",
                    conflicts)
    return income


# 배송비 수취분에 붙는 네이버페이 주문관리 수수료율 = 상품 주문관리 수수료율(2.724%).
# ★원 단위 규칙은 **절사(ROUND_DOWN)** — 라이브 전수 대조(DELIVERY 행 1,652건):
#     금액   건수   실측    절사   반올림
#     2,500  1,441   68      68 ✓   68 ✓
#     3,000    127   81      81 ✓   82 ✗
#     5,000     74  136     136 ✓  136 ✓
#     7,500      2  204     204 ✓  204 ✓
#     5,500      5  150     149 ✗  150 ✓
#       500      3   13      13 ✓   14 ✗
#   절사 1,644/1,652(99.5%) vs 반올림 1,522(92.1%). 특히 **N배송의 유일한 금액인 3,000원에서
#   절사만 맞는다** — N배송이 주력이 되는 지금 반올림을 쓰면 매 건 +1원 체계적 과대다.
#   5,500·500의 예외 8건은 결제수단별 요율 차이로 보이나(commission-details의 payMeansType별
#   분해에서 요율이 갈린다) orders에 결제수단을 저장하지 않아 재현 불가 — 과적합하지 않는다.
NAVER_DELIVERY_COMMISSION_RATE = Decimal("0.02724")


def _cost_of_line(product_map: dict, product_id, quantity) -> Decimal | None:
    """이 라인의 원가. **원가를 알 수 없으면 None**(0이 아니다).

    ★0을 돌려주면 안 되는 이유: 엔진은 여태 원가를 못 찾으면 조용히 더하지 않았고, 그러면
      "원가 0원짜리 상품"과 "원가를 모르는 상품"이 손익에서 **완전히 같은 모양**이 된다.
      후자는 판 돈이 전부 이익으로 잡혀 이익을 부풀리는데 아무 경보도 안 울린다.
    ⚠️`product_master.cost_price`는 NOT NULL default 0이라 **미입력과 실제 0을 구분할 수 없다**
      (prod 실측: 원가 0인 상품 90개). 그래서 여기서는 0도 '미상'으로 본다 — 최근 30일
      매출 기여가 8,900원뿐이라 실익이 거의 없고, 과대계상을 놓치는 쪽이 더 위험하다.
      원가 축이 확정되면(원가 트랙) 이 판정 한 줄만 고치면 된다.
    """
    if product_id is None or product_id not in product_map:
        return None
    cost_price = product_map[product_id].cost_price
    if not cost_price:
        return None
    return cost_price * quantity


def _new_bucket(*, with_order_count: bool = True) -> dict:
    """집계 버킷 템플릿. 주문 루프와 반품 손익 반영이 **같은 모양**을 쓰도록 한 곳에 모은다
    (한쪽에만 키가 있으면 반품만 있는 날짜에서 KeyError로 죽는다)."""
    b = {
        "revenue": ZERO,
        "product_revenue": ZERO,
        "shipping_revenue": ZERO,
        "cost": ZERO,
        "commission": ZERO,
        "shipping": ZERO,
        "ad_spend": ZERO,
        "fixed_cost": ZERO,
        "unmapped_revenue": ZERO,
        "vat": ZERO,
    }
    if with_order_count:
        b["order_count"] = 0
    return b


def payable_vat(revenue: Decimal, *deductible_costs: Decimal) -> Decimal:
    """납부세액 = 매출VAT − 매입세액공제. 순이익에서 뺄 부가세는 이 값이다.

    ★2026-08-04 Jino 결정으로 종전 방침을 뒤집는다. 종전 주석은 이랬다 —
      "VAT 미차감 — 판매자 VAT는 매입세액공제로 상당부분 상쇄되는 통과분"(2026-06-15).
      맞는 말이지만 **'상당부분'이 전부는 아니다**. 실측(7월 네이버): 매출VAT 4,196,650 −
      매입세액 3,592,185 = **납부세액 604,465원**이 실제로 국세청에 나간다. 이걸 빼지 않으면
      순이익이 그만큼 부풀고, 특히 이익률이 낮을수록 왜곡 비중이 커진다.
      반대로 **매출VAT 전액**을 빼면(4,196,650) 매입세액공제를 통째로 무시해 과다차감된다 —
      Jino가 두 안 중 '납부세액'을 골랐다.

    ★매입세액에 넣는 것: 원가·수수료·배송비·광고비. 넷 다 **VAT 포함 축**임이 확인됐다
      (배송비 상수는 ×1.1, 네이버 수수료·광고비는 실차감이 카드 결제액과 같은 축).
    ★원가 축 확정(Jino 2026-08-04): "sellc에 들어있는 원가는 **부가세 포함**된 가격이야."
      → 종전 ⚠️미확인 표시 해제. 이 함수는 원가를 매입세액에 넣어 ÷1.1 하고 있었으므로
      **가정이 확정과 일치**했다(코드 변경 0줄, 이익 숫자 정정 없음). 원가 축이 언젠가
      공급가 기준으로 바뀌면 여기 `deductible_costs`에서 원가만 빼면 된다.

    수식이 결국 `(매출−비용)/1.1`과 같아지는 것은 우연이 아니다 — 모든 축이 VAT 포함이면
    공급가 기준 이익과 같아진다. 그래도 두 항을 명시적으로 쓴다: 원가의 VAT 축이 바뀌면
    한 줄만 빠지면 되고, 축약형은 그 변경 지점을 숨긴다.
    """
    sales_vat = revenue * Decimal("10") / Decimal("110")
    input_vat = sum(deductible_costs, ZERO) * Decimal("10") / Decimal("110")
    return sales_vat - input_vat


def _return_shipping_pnl(
    db: Session, channel_map: dict, date_from: date, date_to: date, channel_id: int | None = None
) -> dict[int, dict[str, dict]]:
    """반품 **완료일**별·채널별 배송 손익 → {channel_id: {date_str: {income, commission, cost}}}.

    ★왜 필요한가: 반품 건은 REVENUE_EXCLUDED라 매출·원가·배송비가 **전부 0으로 빠진다**.
      그런데 실제로는 출고비와 회수비가 나가고 반품비가 들어온다. 그래서 종전 대시보드는
      **반품이 늘어도 이익이 반응하지 않았다** — 반품이 공짜인 것처럼 보였다.

    ★귀속일 = return_completed_at(반품 완료일). 결제일이 아니다(Jino 지시 2026-08-03:
      "과거 이익이 흔들리면 안 돼"). 결제일에 실으면 이미 마감해서 본 지난달 이익이 반품이
      생길 때마다 뒤로 바뀐다. 그래서 이 함수만 order_date가 아닌 별도 축으로 조회한다.

    ★수입은 정액이 아니라 건별 실측(return_fee_demand_amount). 라이브 86건 중 **22건이 미청구**
      (일반 20 + N배송 2, 원천이 `미청구(N배송)`이라고 명시)라, 정액 5,000을 쓰면 그 22건이
      통째로 틀린 수입이 되어 반품이 이익 나는 일처럼 보인다.

    ★수입을 배송수입과 **똑같이** 다루는 이유(수수료·VAT 포함): 정산 원장이 둘을 구분하지
      않는다 — 원 배송비도 반품비도 같은 productOrderType='DELIVERY' 행으로 같은 주문관리
      수수료를 맞는다(라이브: 5,000→136 · 3,000→81, 2.724% 절사). 여기서만 다르게 처리하면
      원천에 없는 세무 판단을 코드가 새로 만드는 셈이다.

    비용 = 출고비(배송방식별 실단가) + 회수비(회수 택배사 단가). 배송(패키지)당 1회.
    """
    start = datetime.combine(date_from, dtime.min)
    end = datetime.combine(date_to, dtime.max)
    q = db.query(Order).filter(
        Order.status == "returned",
        Order.return_completed_at >= start,
        Order.return_completed_at <= end,
    )
    if channel_id:
        q = q.filter(Order.channel_id == channel_id)

    out: dict[int, dict[str, dict]] = {}
    seen: set[tuple] = set()   # 배송(패키지)당 1회 — 라인마다 복사된 값을 중복 계상하지 않는다
    for o in q.all():
        ch = channel_map.get(o.channel_id)
        if ch and ch.channel_type == "consignment":
            continue
        skey = _shipment_key(ch, o)
        if skey in seen:
            continue
        seen.add(skey)

        d = str(o.return_completed_at)[:10]
        income = Decimal(str(o.return_fee_demand_amount or 0))
        # ★네이버 지원액만큼 회수비가 상쇄된다(2026-08-03 정정). N배송 반품은 고객 청구가 0인데
        #   그건 우리 손실이 아니라 네이버가 5,500원을 부담하기 때문이다 — 지원 필드를 안 보면
        #   정확히 반대로 읽힌다(처음에 그렇게 읽었다).
        #   ★상한 = 회수비. 초과분(N배송 5,500 − 2,500 = 3,000)은 **수입으로 잡지 않는다** —
        #     그 돈이 실제로 우리에게 지급되는지는 정산에서 아직 확인되지 않았다(D+12 미성숙).
        #     프로브(naver_claim_settlement_probe)가 08-06·08-09에 잡으면 그때 재심한다.
        pickup = order_delivery.return_pickup_cost(o.delivery_attribute_type)
        support = min(Decimal(str(o.return_fee_support_amount or 0)), pickup)
        cost = _shipment_cost(o) + pickup - support
        bucket = out.setdefault(o.channel_id, {}).setdefault(
            d, {"income": ZERO, "commission": ZERO, "cost": ZERO}
        )
        bucket["income"] += income
        bucket["commission"] += _delivery_commission(ch, income)
        bucket["cost"] += cost
    return out


def _exchange_rows(
    db: Session, date_from: date, date_to: date, channel_id: int | None = None
):
    """교환 손익 대상 주문 — 재배송 처리일이 창 안에 있는 건. 배송(패키지)당 1회로 dedup 전."""
    start = datetime.combine(date_from, dtime.min)
    end = datetime.combine(date_to, dtime.max)
    q = db.query(Order).filter(
        Order.exchange_completed_at >= start,
        Order.exchange_completed_at <= end,
    )
    if channel_id:
        q = q.filter(Order.channel_id == channel_id)
    return q.all()


def _exchange_pnl_one(o) -> dict:
    """교환 1건의 손익 3항목 → {income, cost}.

    ★반품과 결정적으로 다른 점: 교환 주문은 `exchanged` 상태이고 **REVENUE_EXCLUDED에 없다** —
      매출·원가·수수료·최초 출고비가 이미 정상 계상돼 있다. 그래서 여기서 다시 더하면 안 되고,
      **추가로 발생한 것만** 잡는다:
        수입 = 고객에게 받은 교환 배송비(건별 실측)
        비용 = 회수비 + **재발송 출고비**
      ★재발송 출고비가 이 함수의 존재 이유다 — 한 주문에 출고가 두 번 일어났는데 엔진은
        `_shipment_cost`로 한 번만 센다. 회수비만 더하면 교환이 실제보다 싸 보인다.

    ★지원액은 회수비를 상한으로 상쇄(반품과 동일 규칙). 라이브는 전건 0이지만 N배송 교환이
      생기면 값이 붙는다 — 그때 이 줄이 없으면 "고객 미청구=우리 손실"로 정확히 반대로 읽는다.
    """
    income = Decimal(str(o.exchange_fee_demand_amount or 0))
    pickup = order_delivery.return_pickup_cost(o.delivery_attribute_type)
    support = min(Decimal(str(o.exchange_fee_support_amount or 0)), pickup)
    redelivery = _shipment_cost(o)          # 재발송 = 출고 1회 추가
    return {"income": income, "cost": redelivery + pickup - support}


def _exchange_shipping_pnl(
    db: Session, channel_map: dict, date_from: date, date_to: date, channel_id: int | None = None
) -> dict[int, dict[str, dict]]:
    """교환 **재배송 처리일**별·채널별 배송 손익 → {channel_id: {date_str: {income, commission, cost}}}.

    ★왜 필요한가: 교환은 여태 손익 엔진에 **0회 등장**했다. 매출은 잡히니 티가 안 났지만
      회수비·재발송비가 통째로 빠져 있어 교환이 늘어도 이익이 반응하지 않았다.
    ★귀속일 = 재배송 처리일. 요청일에 실으면 마감해서 본 지난달 이익이 뒤로 바뀐다(반품과 동일).
    ★수입에 수수료를 매기는 것도 반품과 동일 — 정산 원장이 원 배송비와 클레임 배송비를
      같은 `DELIVERY` 행으로 끊고 같은 주문관리 수수료를 매긴다.
    """
    out: dict[int, dict[str, dict]] = {}
    seen: set[tuple] = set()
    for o in _exchange_rows(db, date_from, date_to, channel_id):
        ch = channel_map.get(o.channel_id)
        if ch and ch.channel_type == "consignment":
            continue
        skey = _shipment_key(ch, o)
        if skey in seen:
            continue
        seen.add(skey)

        d = str(o.exchange_completed_at)[:10]
        pnl = _exchange_pnl_one(o)
        bucket = out.setdefault(o.channel_id, {}).setdefault(
            d, {"income": ZERO, "commission": ZERO, "cost": ZERO}
        )
        bucket["income"] += pnl["income"]
        bucket["commission"] += _delivery_commission(ch, pnl["income"])
        bucket["cost"] += pnl["cost"]
    return out


def _exchange_shipping_pnl_by_product(
    db: Session, channel_map: dict, date_from: date, date_to: date, channel_id: int | None = None
) -> dict[int, dict]:
    """교환 배송 손익을 **상품별**로 → {product_id: {income, commission, cost}}.

    반품과 달리 고정비(②)처럼 배분 추정이 필요 없다 — 교환은 특정 주문의 특정 상품이다."""
    out: dict[int, dict] = {}
    seen: set[tuple] = set()
    for o in _exchange_rows(db, date_from, date_to, channel_id):
        ch = channel_map.get(o.channel_id)
        if ch and ch.channel_type == "consignment":
            continue
        if o.product_id is None:
            continue
        skey = _shipment_key(ch, o)
        if skey in seen:
            continue
        seen.add(skey)

        pnl = _exchange_pnl_one(o)
        bucket = out.setdefault(
            o.product_id, {"income": ZERO, "commission": ZERO, "cost": ZERO}
        )
        bucket["income"] += pnl["income"]
        bucket["commission"] += _delivery_commission(ch, pnl["income"])
        bucket["cost"] += pnl["cost"]
    return out


def _return_shipping_pnl_by_product(
    db: Session, channel_map: dict, date_from: date, date_to: date, channel_id: int | None = None
) -> dict[int, dict]:
    """반품 배송 손익을 **상품별**로 → {product_id: {income, commission, cost}}.

    ★배분 규칙이 필요 없다(Jino 지적 2026-08-03): 라이브 반품 86패키지가 **전부 상품 1종**이다
      (라인 87개). 반품 회수비는 그 반품이 발생한 건에 그대로 붙이면 된다.
      2종 이상인 패키지가 생기면 상품매출 비례로 나눈다 — 광고비 배분과 같은 규칙이라
      새 규칙을 만드는 게 아니고, 실측 0건이라 지금은 발동하지 않는다.
    product_id가 없는 라인(미연결 상품)은 버린다 — 이 집계 자체가 상품 그레인이다.
    """
    start = datetime.combine(date_from, dtime.min)
    end = datetime.combine(date_to, dtime.max)
    q = db.query(Order).filter(
        Order.status == "returned",
        Order.return_completed_at >= start,
        Order.return_completed_at <= end,
        Order.product_id.isnot(None),
    )
    if channel_id:
        q = q.filter(Order.channel_id == channel_id)

    by_pkg: dict[tuple, list] = {}
    for o in q.all():
        ch = channel_map.get(o.channel_id)
        if ch and ch.channel_type == "consignment":
            continue
        by_pkg.setdefault(_shipment_key(ch, o), []).append(o)

    out: dict[int, dict] = {}
    for lines in by_pkg.values():
        head = lines[0]
        ch = channel_map.get(head.channel_id)
        income = Decimal(str(head.return_fee_demand_amount or 0))
        pickup = order_delivery.return_pickup_cost(head.delivery_attribute_type)
        support = min(Decimal(str(head.return_fee_support_amount or 0)), pickup)
        cost = _shipment_cost(head) + pickup - support
        commission = _delivery_commission(ch, income)

        weights = {}
        for o in lines:
            weights[o.product_id] = weights.get(o.product_id, ZERO) + _line_revenue(ch, o)
        total = sum(weights.values())
        if total <= 0:   # 반품이라 매출이 0인 라인만 있으면 균등 배분
            weights = {pid: Decimal(1) for pid in weights}
            total = Decimal(len(weights))

        for pid, w in weights.items():
            share = w / total
            b = out.setdefault(pid, {"income": ZERO, "commission": ZERO, "cost": ZERO})
            b["income"] += income * share
            b["commission"] += commission * share
            b["cost"] += cost * share
    return out


def _apply_claim_shipping_pnl(bucket: dict, pnl: dict) -> None:
    """클레임(반품·교환) 배송 손익 1건분을 집계 버킷에 반영.

    order_count는 **올리지 않는다** — 클레임은 주문이 아니다(주문수가 부풀면 객단가가 틀어진다).
    교환도 같은 함수를 쓴다: 반품이든 교환이든 "수입은 배송수입으로 매출에 들어가고 수수료를
    맞으며, 비용은 배송비로 나간다"는 모양이 같기 때문이다(2026-08-04 교환 배선 시 개명 —
    종전 이름 `_apply_return_pnl`은 공용인데 반품 전용처럼 보였다).
    """
    bucket["shipping_revenue"] += pnl["income"]
    bucket["revenue"] += pnl["income"]
    bucket["commission"] += pnl["commission"]
    bucket["shipping"] += pnl["cost"]
    bucket["vat"] += pnl["income"] * Decimal("10") / Decimal("110")


def _delivery_commission(ch: Channel | None, deliv: Decimal) -> Decimal:
    """고객이 낸(=우리가 정산받는) 배송비에 붙는 주문관리 수수료.

    ★종전 코드는 "배송비엔 수수료 미부과"를 가정했는데 **실측은 반대**다 — 정산이 배송비를
      product_order_type='DELIVERY' 원장 행으로 따로 끊고 거기에도 주문관리 수수료를 매긴다.
    ★N배송(도착보장) 배송비 3,000원 정책이 2026-07-22부터 돌면서 이 누락이 급격히 커진다.
      구조(라이브 실측 438건): deliveryFeeAmount는 항상 3,000이고
        - 멤버십 회원(deliveryDiscountAmount=3,000, 61.6%) → 네이버가 고객에게 면제해주고
          그 3,000원을 우리에게 지급
        - 비멤버십(deliveryDiscountAmount=0, 37.7%) → 고객이 우리에게 3,000원 지급
      **어느 쪽이든 우리가 3,000원을 정산받고 거기에 수수료가 붙는다**(DELIVERY 행 3,000원
      109건으로 실증). 그래서 할인 여부를 볼 필요 없이 수취 배송비에 요율을 곱하면 된다.

    쿠팡·cafe24는 배송비 수수료 구조가 확인되지 않아 적용하지 않는다(추정 금지 — 확인되면 확장).
    """
    if deliv <= 0 or not ch or ch.code != "NAVER":
        return ZERO
    return (deliv * NAVER_DELIVERY_COMMISSION_RATE).quantize(Decimal("1"), ROUND_DOWN)


def _raw(o: Order) -> dict:
    """Order.raw_data(Text JSON) → dict (실패 시 빈 dict)."""
    rd = o.raw_data
    if isinstance(rd, dict):
        return rd
    if isinstance(rd, str) and rd:
        try:
            parsed = json.loads(rd)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _shipment_key(ch: Channel | None, o: Order) -> tuple:
    """물리 배송 1건 식별 — 한진 1,900 부과 단위 & 쿠팡 배송수입 dedup 단위.

    NAVER: productOrder.packageNumber (1주문이 여러 패키지 가능 → 패키지=실배송)
    COUPANG*: shipmentBoxId (배송박스 = 물리 배송)
    그 외(CAFE24 등): order_number (주문=배송)
    키가 비면 행 고유 sentinel — 서로 다른 주문이 한 배송으로 합쳐지는 것 방지.
    """
    code = (ch.code if ch else "") or ""
    raw = _raw(o)
    if code == "NAVER":
        po = raw.get("productOrder")
        sid = po.get("packageNumber") if isinstance(po, dict) else None
    elif code.startswith("COUPANG"):
        sid = raw.get("shipmentBoxId")
    else:
        sid = o.order_number
    if sid in (None, "", 0, "0"):
        sid = f"__row_{o.id}"  # 고유 fallback (합쳐짐 방지)
    return (o.channel_id, str(sid))


def _delivery_income(ch: Channel | None, o: Order) -> Decimal:
    """우리가 정산받는 배송비 = 매출 가산분. 라인 단위 원본값 — **배송당 1회만** 쓸 것.

    ★2026-08-03 정정: 종전 주석은 "NAVER deliveryFeeAmount는 패키지별로 저장 → 라인 합 =
      주문 총액(정확)"이라고 적었는데 **틀렸다**. 실측하면 네이버도 쿠팡과 똑같이
      **배송 단위 값이 패키지 내 모든 라인에 복사**돼 있다:
        2라인 패키지 13개 → 라인 합 78,000 vs 실제 39,000
        3라인 패키지  1개 → 라인 합  9,000 vs 실제  3,000
        채널6 전 기간 과대계상 62,500원(21패키지)
      정산이 이를 확정한다 — DELIVERY 원장 행은 **배송 1건당 1행**이다(N배송 423패키지 ↔
      DELIVERY 행 110건, 미성숙분 제외). 분할배송이면 packageNumber가 갈리며 행도 2개가 된다.
      → 그래서 호출부는 채널을 가리지 않고 _shipment_key로 배송 1회만 가산한다.

    NAVER deliveryFeeAmount: N배송은 3,000 고정. 멤버십이면 네이버가 고객에게 면제하고 그
      금액을 우리에게 지급, 아니면 고객이 지급 — **어느 쪽이든 우리 수입**이라 할인 여부를
      보지 않는다(_delivery_commission 주석의 실측 참조).
    COUPANG shippingPrice: shipment 단위 값이 박스 내 모든 라인에 복사.
    CAFE24/기타: 고객 무료배송이라 수입 0.
    """
    if not ch:
        return ZERO
    code = ch.code or ""
    if code == "NAVER" or code.startswith("COUPANG"):
        return o.shipping_cost or ZERO
    return ZERO


def _is_coupang(ch: Channel | None) -> bool:
    """쿠팡 계열 채널 판별. 배송수입 dedup이 전 채널로 통일되면서(2026-08-03) 호출부가
    없어졌지만, _line_revenue/_delivery_income의 채널 판별과 짝이 되는 공개 헬퍼라 남긴다."""
    return bool(ch and (ch.code or "").startswith("COUPANG"))


def _line_revenue(ch: Channel | None, o: Order) -> Decimal:
    """라인 상품매출(총액). selling_price 의미가 채널마다 달라 여기서 통일한다.

    - 쿠팡(channel.py: orderPrice=salesPrice×shippingCount)·네이버(totalPaymentAmount):
      적재값이 이미 라인총액 → selling_price 그대로. ×수량하면 2~3배 2중계상.
    - cafe24(product_price)·기타/미지정: 단가 → ×수량.
    트랙 track_coupang-revenue-ad-reconciliation S2. _delivery_income과 동일 채널 판별.
    """
    code = (ch.code if ch else "") or ""
    if code == "NAVER" or code.startswith("COUPANG"):
        return o.selling_price
    return o.selling_price * o.quantity


# ──────────────────────────────────────────────
# 내부 헬퍼
# ──────────────────────────────────────────────


def _get_option_id_map(db: Session) -> dict[str, int]:
    """product_channel_mapping에서 {channel_product_id: product_id} 딕셔너리"""
    rows = db.query(
        ProductChannelMapping.channel_product_id,
        ProductChannelMapping.product_id,
    ).filter(ProductChannelMapping.is_active.is_(True)).all()
    return {r[0]: r[1] for r in rows}


def _get_ad_spend_lookup(
    ad_db, date_from: date, date_to: date, option_ids: list[str]
) -> dict[tuple[str, str], Decimal]:
    """ad_data.db에서 option_id별/일별 광고비 조회 → {(option_id, date_str): spend}"""
    if ad_db is None or not option_ids:
        return {}

    try:
        # SQLite는 IN 절에 파라미터 바인딩이 까다로우므로 청크 처리
        lookup: dict[tuple[str, str], Decimal] = {}
        chunk_size = 500
        for i in range(0, len(option_ids), chunk_size):
            chunk = option_ids[i : i + chunk_size]
            placeholders = ", ".join(f":oid_{j}" for j in range(len(chunk)))
            params: dict = {
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
            }
            for j, oid in enumerate(chunk):
                params[f"oid_{j}"] = oid

            sql = f"""
                SELECT option_id, date, SUM(ad_spend) as spend
                FROM daily_ad_data
                WHERE date >= :date_from AND date <= :date_to
                  AND option_id IN ({placeholders})
                GROUP BY option_id, date
            """
            result = ad_db.execute(text(sql), params)
            for row in result:
                m = row._mapping
                lookup[(str(m["option_id"]), str(m["date"]))] = Decimal(str(m["spend"]))

        return lookup
    except Exception as e:
        log.error("ad_data.db 광고비 조회 에러: %s", e)
        return {}


def _build_channel_maps(db: Session) -> tuple[dict[int, Channel], dict[int, ProductMaster]]:
    """채널/상품 맵 생성"""
    channels = {c.id: c for c in db.query(Channel).all()}
    products = {p.id: p for p in db.query(ProductMaster).all()}
    return channels, products


def _calc_line(
    revenue: Decimal,
    cost: Decimal,
    commission_rate: Decimal,
    shipping: Decimal,
    ad_spend: Decimal,
) -> dict:
    """단일 라인의 수수료/VAT/순이익 계산"""
    commission = revenue * commission_rate / Decimal("100")
    vat = revenue * Decimal("10") / Decimal("110")
    net_profit = revenue - cost - commission - shipping - ad_spend - vat
    return {
        "commission": commission,
        "vat": vat,
        "net_profit": net_profit,
    }


# ──────────────────────────────────────────────
# RG 정산 수수료 조회 (dashboard B안 — KPI/채널요약 반영용)
# ──────────────────────────────────────────────


#: 순이익에서 **차감하지 않는** 정산 fee_type (D-CPP-43, 2026-08-12 · PR #287).
#:
#: 정산서의 `ad_sales`는 별개 비용이 아니라 **광고센터 PA 광고비를 정산에서 «공제»**하는 것이다.
#: 1차 출처(윙 > 정산 > 로켓그로스 정산현황 > 「광고비 내역」): *"로켓그로스 상품의 광고비는 해당
#: 월 말에 계산되어 **매입세금계산서 1건이 발행**되며"* — 즉 같은 돈이고, 우리는 그 돈을 이미
#: PA 광고 원장(`ad_spend`)으로 한 번 세고 있다. 여기서 또 빼면 **이중계상**이다.
#:
#: ★D-16(「전액 차감」)은 `sell_type=2P` 0행을 근거로 「겹침 없음」이라 판정했는데, **그 라벨이
#:   판매경로를 뜻하지 않는다**(오픽스 PA 광고비의 97.28%가 RG로 팔리는 옵션에 쓰인다 — ref 56 §3).
#:   그래서 D-16이 걸어 둔 감시 장치(`ad_xlsx_rg_overlap>0`)는 이 형태를 **원리적으로 못 잡았다**
#:   (교훈 #261·#268). D-CPP-43이 D-16을 폐기하고 「광고 제외 차감」으로 되돌렸다.
_RG_FEE_TYPES_NOT_DEDUCTED: frozenset[str] = frozenset({"ad_sales"})


def get_rg_total_by_account(
    db: Session, date_from: date, date_to: date
) -> dict[str, Decimal]:
    """RG 정산 «차감 대상» 총액을 account_key별로 반환 (KPI·channel-breakdown 차감용).

    = 판매수수료(`sale_fee`) + 풀필먼트(`delivery`+`warehousing`+`storage`) + 반품
      (`return_shipping`+`return_handling`) — 즉 `rg_total − ad_sales`(D-CPP-43).
    풀필먼트 3컴포넌트는 **서로 독립**이라 합산해도 이중계상이 아니다(ref 17 §8 라이브 확정 —
      `totalFulfillmentFeeDeductionAmount`는 「배송비」뿐이지 풀필먼트 합계가 아니다).

    intelligence._agg_rg_settlement_fees와 같은 overlap 필터·vendor_item_id="" 가드 적용.
    반환: {"COUPANG_WING1": Decimal, "COUPANG_WING2": Decimal, ...}
    데이터 없으면 빈 dict → 차감 no-op.

    ★2026-08-22(D-CPP-47)에 `ad_sales` 제외를 여기 넣었다. 종전엔 이 함수만 **폐기된 D-16
      규칙**(전액 차감)으로 남아 있었다 — 종합조망(`intelligence.py`)과 상품손익(`product_pnl.py`)은
      이미 D-CPP-43으로 옮겨 갔는데 대시보드만 안 옮겨져서, **같은 계정의 RG 수수료를 두 화면이
      서로 다른 금액으로 빼고 있었다.** 이 저장소가 반복해 앓은 「같은 숫자를 두 엔진이 따로 낸다」의
      또 한 사례다.
    """
    rows = (
        db.query(
            CoupangRgSettlementFee.account_key,
            func.sum(CoupangRgSettlementFee.amount),
        )
        .filter(
            CoupangRgSettlementFee.recognition_date_from <= date_to,
            CoupangRgSettlementFee.recognition_date_to >= date_from,
            CoupangRgSettlementFee.vendor_item_id == "",
            CoupangRgSettlementFee.fee_type.notin_(_RG_FEE_TYPES_NOT_DEDUCTED),
        )
        .group_by(CoupangRgSettlementFee.account_key)
        .all()
    )
    return {ak: Decimal(str(amt or 0)) for ak, amt in rows}


def get_rg_ad_settlement_by_account(
    db: Session, date_from: date, date_to: date
) -> dict[str, Decimal]:
    """정산서에 실린 RG 광고 «공제»액 — **차감하지 않는다. 표시·검산용이다**(D-CPP-43).

    화면이 「정산 총액」과 「순이익 차감액」을 분리해 보여줄 수 있게 하고(취소선 + "차감 안 함"),
    PA 원장과의 규모 대조에도 쓴다. 여기 값이 PA 광고비와 크게 어긋나면 그건 우리가 알아야 할
    사실이다 — 그 감시는 `scheduler_health`가 맡는다(D-CPP-43이 차감을 뺀 자리에 남긴 안전망).
    """
    rows = (
        db.query(
            CoupangRgSettlementFee.account_key,
            func.sum(CoupangRgSettlementFee.amount),
        )
        .filter(
            CoupangRgSettlementFee.recognition_date_from <= date_to,
            CoupangRgSettlementFee.recognition_date_to >= date_from,
            CoupangRgSettlementFee.vendor_item_id == "",
            CoupangRgSettlementFee.fee_type.in_(_RG_FEE_TYPES_NOT_DEDUCTED),
        )
        .group_by(CoupangRgSettlementFee.account_key)
        .all()
    )
    return {ak: Decimal(str(amt or 0)) for ak, amt in rows}


# ──────────────────────────────────────────────
# 공개 API
# ──────────────────────────────────────────────


def calculate_daily_trend(
    db: Session,
    ad_db,
    channel_id: int | None,
    date_from: date,
    date_to: date,
) -> list[dict]:
    """일별 매출/비용/순이익 추이"""
    query = db.query(Order).filter(
        and_(
            Order.order_date >= date_from.isoformat(),
            Order.order_date < (date_to.isoformat() + "T23:59:59"),
        )
    )
    if channel_id:
        query = query.filter(Order.channel_id == channel_id)

    orders = query.all()
    channel_map, product_map = _build_channel_maps(db)
    fee_rates = _coupang_3p_fee_rates(db, orders, channel_map)  # 쿠팡3P 옵션별 실측 요율(D-CPP-32)

    # 수동 매출이 있으면 주문이 없어도 계속 진행
    manual_lookup = get_daily_manual_revenue(db, date_from, date_to, channel_id)
    # 반품 배송 손익 — 반품 완료일 귀속이라 order_date 창과 다른 축이다(별도 조회).
    return_pnl = _return_shipping_pnl(db, channel_map, date_from, date_to, channel_id)
    # 교환 배송 손익 — 재배송 처리일 귀속이라 order_date 창과 다른 축이다(반품과 같은 이유).
    exchange_pnl = _exchange_shipping_pnl(db, channel_map, date_from, date_to, channel_id)
    # 월 고정비(3PL 입고·보관·항공도선·합포장) — 주문 축에 없는 비용이라 별도 조회·일할 배분.
    fixed_lookup = get_daily_fixed_cost(db, date_from, date_to, channel_id)
    if not orders and not manual_lookup and not return_pnl and not exchange_pnl and not fixed_lookup:
        return []

    option_map = _get_option_id_map(db)

    # 모든 option_id 수집하여 광고비 일괄 조회
    all_option_ids = list(set(o.platform_product_id for o in orders if o.platform_product_id))
    ad_lookup = _get_ad_spend_lookup(ad_db, date_from, date_to, all_option_ids)

    # 날짜별 집계
    daily: dict[str, dict] = {}
    manual_revenue_by_date: dict[str, Decimal] = {}  # 순이익 제외용 추적
    shipment_costs = _shipment_cost_map(orders, channel_map)  # 배송별 택배비 선행 확정
    seen_shipments: set[tuple] = set()  # 배송(packageNumber/박스)당 1회 부과
    seen_deliv: set[tuple] = set()      # 배송수입 배송당 1회 (전 채널 라인 복사 dedup)
    delivery_income = _delivery_income_map(orders, channel_map)  # 배송별 수입 선행 확정
    for o in orders:
        ch = channel_map.get(o.channel_id)
        if ch and ch.channel_type == "consignment":
            continue
        if o.status in REVENUE_EXCLUDED:  # 취소/반품/입금전 매출 제외
            continue

        d = str(o.order_date.date()) if hasattr(o.order_date, "date") else str(o.order_date)[:10]
        if d not in daily:
            daily[d] = _new_bucket()

        bucket = daily[d]
        skey = _shipment_key(ch, o)
        product_rev = _line_revenue(ch, o)
        # 고객이 낸 배송비 → 매출 가산. 쿠팡은 박스값이 라인마다 복사돼 배송당 1회만.
        # ★배송수입은 배송(패키지·박스)당 1회 — 네이버도 쿠팡과 같이 라인마다 복사돼 있다
        #   (_delivery_income 주석의 실측). 종전엔 쿠팡만 dedup해 네이버가 이중계상됐다.
        #   금액은 선행 확정(_delivery_income_map)이라 라인 순서와 무관하다.
        deliv = ZERO
        if skey not in seen_deliv:
            seen_deliv.add(skey)
            deliv = delivery_income.get(skey, ZERO)
        revenue = product_rev + deliv
        bucket["product_revenue"] += product_rev
        bucket["shipping_revenue"] += deliv
        bucket["revenue"] += revenue
        bucket["order_count"] += 1

        # 원가 — 알 수 없으면 그 라인의 제품매출을 '원가 미상'으로 따로 센다(표시 전용).
        _line_cost = _cost_of_line(product_map, o.product_id, o.quantity)
        if _line_cost is None:
            bucket["unmapped_revenue"] += product_rev
        else:
            bucket["cost"] += _line_cost

        # 수수료 — 상품매출 + 배송비 수취분(정산 DELIVERY 원장 행에 실제로 부과됨)
        bucket["commission"] += _line_commission(ch, o, product_rev, fee_rates)
        bucket["commission"] += _delivery_commission(ch, deliv)

        # 판매자 배송비 — 물리배송(packageNumber·박스)당 1회, 배송방식별 실단가(선행 확정).
        if skey not in seen_shipments:
            seen_shipments.add(skey)
            bucket["shipping"] += shipment_costs[skey]

        # VAT — 표시 매출(상품+배송) 기준
        bucket["vat"] += revenue * Decimal("10") / Decimal("110")

    # 반품 배송 손익 — 반품 완료일 버킷에 싣는다. 그날 주문이 하나도 없어도 버킷을 만든다
    # (반품만 있는 날에 손실이 사라지면 안 된다).
    for _cid, by_date in return_pnl.items():
        for d, pnl in by_date.items():
            if d not in daily:
                daily[d] = _new_bucket()
            _apply_claim_shipping_pnl(daily[d], pnl)

    # 교환 배송 손익 — 재배송 처리일 버킷에. 반품과 같은 규칙(그날 주문이 없어도 버킷을 만든다).
    for _cid, by_date in exchange_pnl.items():
        for d, pnl in by_date.items():
            if d not in daily:
                daily[d] = _new_bucket()
            _apply_claim_shipping_pnl(daily[d], pnl)

    # 광고비 합산 (option_id 기반 — ad_data.db 연결 시 사용, 현재 비활성)
    for (oid, d_str), spend in ad_lookup.items():
        if d_str in daily:
            daily[d_str]["ad_spend"] += spend

    # Meta 광고비 (ad_costs 테이블 — cafe24 키워드별 일별)
    cafe24_id = _get_cafe24_channel_id(channel_map)
    if cafe24_id and (channel_id is None or channel_id == cafe24_id):
        meta_daily = _get_meta_ad_spend_daily(db, cafe24_id, date_from, date_to)
        for d, spend in meta_daily.items():
            if d in daily:
                daily[d]["ad_spend"] += spend

    # Naver SA + GFA 광고비 (ad_costs 테이블 — naver 채널 일별)
    naver_id = _get_naver_channel_id(channel_map)
    if naver_id and (channel_id is None or channel_id == naver_id):
        naver_daily = _get_naver_sa_ad_spend_daily(db, naver_id, date_from, date_to)
        for d, spend in naver_daily.items():
            if d in daily:
                daily[d]["ad_spend"] += spend
        gfa_daily = _get_gfa_ad_spend_daily(db, naver_id, date_from, date_to)
        for d, spend in gfa_daily.items():
            if d in daily:
                daily[d]["ad_spend"] += spend

    # 수동 매출 병합 (로켓배송 등 — 매출-only, 순이익은 미반영)
    # 수동매출은 제품/배송 분리 불가 → product_revenue로 일괄 처리
    for (mr_ch_id, d), revenue in manual_lookup.items():
        if channel_id is not None and mr_ch_id != channel_id:
            continue
        if d not in daily:
            daily[d] = _new_bucket()  # ★손으로 쓴 dict였다 — 키가 하나 늘 때마다 여기서 KeyError가 난다
        daily[d]["revenue"] += revenue
        daily[d]["product_revenue"] += revenue
        manual_revenue_by_date[d] = manual_revenue_by_date.get(d, ZERO) + revenue

    # 월 고정비 일할 배분분 병합.
    # ★주문이 없는 날에도 행을 만든다 — 보관료는 주문이 없어도 나가는 돈이라, 그날을 건너뛰면
    #   합계가 월 총액에 못 미친다(비용이 조용히 사라진다).
    for (_fc_ch_id, d), amount in fixed_lookup.items():
        if d not in daily:
            daily[d] = _new_bucket()
        daily[d]["fixed_cost"] += amount

    # 정렬 후 반환
    result = []
    for d in sorted(daily.keys()):
        b = daily[d]
        # 수동 매출은 순이익 계산에서 제외 (매출만 표시)
        mr = manual_revenue_by_date.get(d, ZERO)
        # 부가세 차감(Jino 2026-08-04, 종전 '미차감' 방침 뒤집음) — 상세는 payable_vat 참조.
        _rev = b["revenue"] - mr
        net = (
            _rev - b["cost"] - b["commission"] - b["shipping"] - b["ad_spend"] - b["fixed_cost"]
            - payable_vat(
                _rev, b["cost"], b["commission"], b["shipping"], b["ad_spend"], b["fixed_cost"]
            )
        )
        result.append({
            "date": d,
            "revenue": str(b["revenue"]),
            "product_revenue": str(b["product_revenue"]),
            "shipping_revenue": str(b["shipping_revenue"]),
            "cost": str(b["cost"]),
            "commission": str(b["commission"]),
            "ad_spend": str(b["ad_spend"]),
            "fixed_cost": str(b["fixed_cost"]),
            "unmapped_revenue": str(b["unmapped_revenue"]),
            "shipping": str(b["shipping"]),
            "vat": str(b["vat"]),
            "net_profit": str(net),
            "order_count": b["order_count"],
        })

    return result


def calculate_channel_summary(
    db: Session, ad_db, date_from: date, date_to: date
) -> list[dict]:
    """채널별 매출/이익 요약"""
    query = db.query(Order).filter(
        and_(
            Order.order_date >= date_from.isoformat(),
            Order.order_date < (date_to.isoformat() + "T23:59:59"),
        )
    )
    orders = query.all()
    channel_map, product_map = _build_channel_maps(db)
    fee_rates = _coupang_3p_fee_rates(db, orders, channel_map)  # 쿠팡3P 옵션별 실측 요율(D-CPP-32)
    manual_lookup_ch = get_daily_manual_revenue(db, date_from, date_to)
    return_pnl = _return_shipping_pnl(db, channel_map, date_from, date_to)
    exchange_pnl = _exchange_shipping_pnl(db, channel_map, date_from, date_to)
    if not orders and not manual_lookup_ch and not return_pnl:
        return []

    option_map = _get_option_id_map(db)
    all_option_ids = list(set(o.platform_product_id for o in orders if o.platform_product_id))
    ad_lookup = _get_ad_spend_lookup(ad_db, date_from, date_to, all_option_ids)

    # 채널별 집계
    by_channel: dict[int, dict] = {}
    shipment_costs = _shipment_cost_map(orders, channel_map)  # 배송별 택배비 선행 확정
    seen_shipments: set[tuple] = set()  # 배송(packageNumber/박스)당 1회 부과
    seen_deliv: set[tuple] = set()      # 배송수입 배송당 1회 (전 채널 라인 복사 dedup)
    delivery_income = _delivery_income_map(orders, channel_map)  # 배송별 수입 선행 확정
    for o in orders:
        ch = channel_map.get(o.channel_id)
        if ch and ch.channel_type == "consignment":
            continue
        if o.status in REVENUE_EXCLUDED:  # 취소/반품/입금전 매출 제외
            continue

        cid = o.channel_id
        if cid not in by_channel:
            by_channel[cid] = _new_bucket()

        b = by_channel[cid]
        skey = _shipment_key(ch, o)
        product_rev = _line_revenue(ch, o)
        # 고객이 낸 배송비 → 매출 가산. 쿠팡은 박스값이 라인마다 복사돼 배송당 1회만.
        # ★배송수입은 배송(패키지·박스)당 1회 — 네이버도 쿠팡과 같이 라인마다 복사돼 있다
        #   (_delivery_income 주석의 실측). 종전엔 쿠팡만 dedup해 네이버가 이중계상됐다.
        #   금액은 선행 확정(_delivery_income_map)이라 라인 순서와 무관하다.
        deliv = ZERO
        if skey not in seen_deliv:
            seen_deliv.add(skey)
            deliv = delivery_income.get(skey, ZERO)
        revenue = product_rev + deliv
        b["product_revenue"] += product_rev
        b["shipping_revenue"] += deliv
        b["revenue"] += revenue
        b["order_count"] += 1

        _line_cost = _cost_of_line(product_map, o.product_id, o.quantity)
        if _line_cost is None:
            b["unmapped_revenue"] += product_rev
        else:
            b["cost"] += _line_cost

        # 수수료 — 상품매출 + 배송비 수취분(정산 DELIVERY 원장 행에 실제로 부과됨)
        b["commission"] += _line_commission(ch, o, product_rev, fee_rates)
        b["commission"] += _delivery_commission(ch, deliv)
        # 판매자 배송비 — 물리배송(packageNumber·박스)당 1회, 배송방식별 실단가(선행 확정).
        if skey not in seen_shipments:
            seen_shipments.add(skey)
            b["shipping"] += shipment_costs[skey]
        # VAT — 표시 매출(상품+배송) 기준
        b["vat"] += revenue * Decimal("10") / Decimal("110")

    # 반품 배송 손익 — 반품 완료일이 이 기간에 든 건을 해당 채널에 싣는다.
    # 그 채널에 이 기간 주문이 하나도 없어도 버킷을 만든다(반품 손실이 사라지면 안 된다).
    for cid, by_date in return_pnl.items():
        for pnl in by_date.values():
            if cid not in by_channel:
                by_channel[cid] = _new_bucket()
            _apply_claim_shipping_pnl(by_channel[cid], pnl)

    # 교환 배송 손익 — 반품과 같은 규칙으로 채널에 싣는다.
    for cid, by_date in exchange_pnl.items():
        for pnl in by_date.values():
            if cid not in by_channel:
                by_channel[cid] = _new_bucket()
            _apply_claim_shipping_pnl(by_channel[cid], pnl)

    # 광고비를 option_id → 주문의 채널로 직접 매핑 (bleeding 방지)
    # 1) option_id별 광고비 합산
    ad_by_option: dict[str, Decimal] = {}
    for (oid, _), spend in ad_lookup.items():
        ad_by_option[oid] = ad_by_option.get(oid, ZERO) + spend

    # 2) option_id → channel_id 매핑 (주문 데이터 기반)
    option_to_channel: dict[str, int] = {}
    for o in orders:
        if o.platform_product_id and o.platform_product_id not in option_to_channel:
            option_to_channel[o.platform_product_id] = o.channel_id

    # 3) 광고비를 해당 채널에 직접 할당 (ad_data.db 기반 — 현재 비활성)
    for oid, spend in ad_by_option.items():
        cid = option_to_channel.get(oid)
        if cid and cid in by_channel:
            by_channel[cid]["ad_spend"] += spend

    # Meta 광고비 직접 합산 (cafe24 채널 전체)
    cafe24_id = _get_cafe24_channel_id(channel_map)
    if cafe24_id and cafe24_id in by_channel:
        meta_daily = _get_meta_ad_spend_daily(db, cafe24_id, date_from, date_to)
        for spend in meta_daily.values():
            by_channel[cafe24_id]["ad_spend"] += spend

    # Naver SA + GFA 광고비 직접 합산 (naver 채널 전체)
    naver_id = _get_naver_channel_id(channel_map)
    if naver_id and naver_id in by_channel:
        naver_daily = _get_naver_sa_ad_spend_daily(db, naver_id, date_from, date_to)
        for spend in naver_daily.values():
            by_channel[naver_id]["ad_spend"] += spend
        gfa_daily = _get_gfa_ad_spend_daily(db, naver_id, date_from, date_to)
        for spend in gfa_daily.values():
            by_channel[naver_id]["ad_spend"] += spend

    # 월 고정비 채널 합산 (3PL 입고·보관·항공도선·합포장 — 일할 배분분의 창 내 합계)
    for (fc_ch_id, _), amount in get_daily_fixed_cost(db, date_from, date_to).items():
        if fc_ch_id in by_channel:
            by_channel[fc_ch_id]["fixed_cost"] += amount

    # 수동 매출 채널 행 병합 (매출-only, 순이익 None)
    manual_by_channel: dict[int, Decimal] = {}
    for (mr_ch_id, _), revenue in manual_lookup_ch.items():
        manual_by_channel[mr_ch_id] = manual_by_channel.get(mr_ch_id, ZERO) + revenue

    result = []
    for cid, b in by_channel.items():
        ch = channel_map.get(cid)
        # 부가세 차감(Jino 2026-08-04, 종전 '미차감' 방침 뒤집음) — 상세는 payable_vat 참조.
        net = (
            b["revenue"] - b["cost"] - b["commission"] - b["ad_spend"] - b["shipping"]
            - b["fixed_cost"]
            - payable_vat(
                b["revenue"], b["cost"], b["commission"], b["shipping"], b["ad_spend"],
                b["fixed_cost"],
            )
        )
        rate_pct = (net / b["revenue"] * 100) if b["revenue"] > 0 else ZERO

        result.append({
            "channel_id": cid,
            "channel_name": ch.name if ch else "",
            "revenue": str(b["revenue"]),
            "product_revenue": str(b["product_revenue"]),
            "shipping_revenue": str(b["shipping_revenue"]),
            "cost": str(b["cost"]),
            "commission": str(b["commission"]),
            "ad_spend": str(b["ad_spend"]),
            "fixed_cost": str(b["fixed_cost"]),
            "unmapped_revenue": str(b["unmapped_revenue"]),
            "shipping": str(b["shipping"]),
            "net_profit": str(net),
            "profit_rate": str(rate_pct.quantize(Decimal("0.01")) if isinstance(rate_pct, Decimal) else "0.00"),
            "order_count": b["order_count"],
        })

    # 수동 매출 채널의 기존 ad_costs 합산 (XLSX 업로드분 포함)
    # by_channel에 없는 채널이라도 광고비는 ad_costs 테이블에 있을 수 있음 (P2 fix)
    _manual_ch_ad: dict[int, Decimal] = {}
    if manual_by_channel:
        _manual_ch_ids = [c for c in manual_by_channel if c not in by_channel]
        if _manual_ch_ids:
            _ad_rows = db.execute(
                text("""
                    SELECT channel_id, SUM(CAST(ad_spend AS REAL))
                    FROM ad_costs
                    WHERE channel_id IN ({})
                      AND ad_date >= :since AND ad_date <= :until
                    GROUP BY channel_id
                """.format(",".join(str(c) for c in _manual_ch_ids))),
                {"since": date_from.isoformat(), "until": date_to.isoformat()},
            ).fetchall()
            for r in _ad_rows:
                if r[1]:
                    _manual_ch_ad[r[0]] = Decimal(str(r[1]))

    # 수동 매출 채널은 순이익 null로 별도 행 추가 (by_channel에 없는 채널만)
    for cid, total_rev in manual_by_channel.items():
        if cid in by_channel:
            continue  # 이미 Order 기반 집계에 포함됐으면 skip (중복 방지)
        ch = channel_map.get(cid)
        ch_ad_spend = _manual_ch_ad.get(cid, ZERO)
        result.append({
            "channel_id": cid,
            "channel_name": ch.name if ch else "",
            "revenue": str(total_rev),
            # 수동매출은 제품/배송 분리 불가 → product_revenue로 일괄
            "product_revenue": str(total_rev),
            "shipping_revenue": "0",
            "cost": "0",
            "commission": "0",
            "ad_spend": str(ch_ad_spend),
            "shipping": "0",
            # ★순이익 계산 불가(매출-only)지만 **광고비는 나갔다** — `net_contribution`이
            #   집계 시 `-광고비` 하한을 만든다(D-22). 여기서 None을 유지하는 이유는
            #   "원가·수수료를 모른다"가 이 행의 진실이기 때문이고, 하한 판정은 한 곳에만 둔다.
            "net_profit": None,
            "profit_rate": None,
            "order_count": 0,
        })

    result.sort(key=lambda x: Decimal(x["revenue"]), reverse=True)
    return result


def calculate_product_profit(
    db: Session,
    ad_db,
    date_from: date,
    date_to: date,
    channel_id: int | None = None,
    sort_by: str = "revenue",
    limit: int = 20,
) -> list[dict]:
    """상품별 이익률 Top N.

    ★반품 배송 손익도 포함한다(2026-08-03). 처음엔 "패키지 안 여러 상품에 어떻게 배분하나"를
      이유로 뺐는데, 실측하니 **반품 86패키지가 전부 상품 1종**이라 배분할 것 자체가 없었다
      (Jino 지적). 일별·채널별 집계와 합계가 어긋나지 않는다.
    """
    query = db.query(Order).filter(
        and_(
            Order.order_date >= date_from.isoformat(),
            Order.order_date < (date_to.isoformat() + "T23:59:59"),
            Order.product_id.isnot(None),
        )
    )
    if channel_id:
        query = query.filter(Order.channel_id == channel_id)

    orders = query.all()
    channel_map, product_map = _build_channel_maps(db)
    # 반품 배송 손익(반품 완료일 귀속) — order_date 창과 다른 축이라 별도 조회.
    return_pnl_prod = _return_shipping_pnl_by_product(db, channel_map, date_from, date_to, channel_id)
    exchange_pnl_prod = _exchange_shipping_pnl_by_product(db, channel_map, date_from, date_to, channel_id)
    if not orders and not return_pnl_prod:
        return []

    fee_rates = _coupang_3p_fee_rates(db, orders, channel_map)  # 쿠팡3P 옵션별 실측 요율(D-CPP-32)
    all_option_ids = list(set(o.platform_product_id for o in orders if o.platform_product_id))
    ad_lookup = _get_ad_spend_lookup(ad_db, date_from, date_to, all_option_ids)

    # option_id별 광고비 합산
    ad_by_option: dict[str, Decimal] = {}
    for (oid, _), spend in ad_lookup.items():
        ad_by_option[oid] = ad_by_option.get(oid, ZERO) + spend

    # 상품별 집계 (배송비·고객배송수입은 배송 단위라 아래에서 라인 비례 배분)
    by_product: dict[int, dict] = {}
    ship_groups: dict[tuple, dict] = {}
    delivery_income = _delivery_income_map(orders, channel_map)  # 배송별 수입 선행 확정
    for o in orders:
        ch = channel_map.get(o.channel_id)
        if ch and ch.channel_type == "consignment":
            continue
        if o.status in REVENUE_EXCLUDED:  # 취소/반품/입금전 매출 제외
            continue

        pid = o.product_id
        if pid not in by_product:
            # 같은 함수의 반품 경로(아래)와 **같은 생성자**를 쓴다 — 손으로 쓴 dict를 두면
            # 버킷 키가 늘 때 두 경로가 갈라진다(_new_bucket docstring 참조).
            by_product[pid] = _new_bucket(with_order_count=False)
            by_product[pid]["quantity"] = 0

        b = by_product[pid]
        product_rev = _line_revenue(ch, o)
        b["product_revenue"] += product_rev
        b["revenue"] += product_rev  # 고객배송수입은 배송단위 배분으로 아래에서 추가
        b["quantity"] += o.quantity
        # VAT — 상품매출 기준 누적 (배송수입 VAT는 아래 alloc 단계에서 추가)
        b["vat"] += product_rev * Decimal("10") / Decimal("110")

        _line_cost = _cost_of_line(product_map, pid, o.quantity)
        if _line_cost is None:
            b["unmapped_revenue"] += product_rev
        else:
            b["cost"] += _line_cost

        # 수수료 — 상품매출분. 배송비 수취분 수수료는 배송 그룹에서 라인 비례 배분한다.
        b["commission"] += _line_commission(ch, o, product_rev, fee_rates)

        # 배송 그룹 — 택배비 & 고객배송수입을 라인 비례 배분
        skey = _shipment_key(ch, o)
        g = ship_groups.setdefault(skey, {"lines": [], "deliv": ZERO, "cost": ZERO, "ch": ch})
        g["lines"].append((pid, product_rev))
        # 택배비는 배송당 1회 — 단가가 갈리면 최댓값(_shipment_cost_map과 같은 이유).
        _c = _shipment_cost(o)
        if _c > g["cost"]:
            g["cost"] = _c
        # ★배송수입도 배송당 1회 — 네이버도 쿠팡과 같이 라인마다 복사돼 있다.
        #   종전엔 네이버만 라인 합산해 다라인 패키지에서 이중계상됐다(라이브 62,500원).
        #   금액은 선행 확정 맵에서 가져와 라인 순서와 무관하게 만든다(codex 2R P2).
        g["deliv"] = delivery_income.get(skey, ZERO)

        # 광고비는 아래에서 option_id → product_id 매핑으로 일괄 할당

    # 배송 1건당 택배비(배송방식별 실단가) + 고객배송수입을 그룹 내 라인 매출 비례 배분 (합 보존)
    def _alloc_to_lines(_lines: list, _amount: Decimal, _field: str) -> None:
        _total = sum((rev for _, rev in _lines), ZERO)
        _n = len(_lines)
        _acc = ZERO
        for _i, (_pid, _rev) in enumerate(_lines):
            if _i == _n - 1:
                _share = _amount - _acc  # 잔여 — 합 = _amount 보장
            elif _total > 0:
                _share = (_amount * _rev / _total).quantize(Decimal("1"))
                _acc += _share
            else:
                _share = (_amount / _n).quantize(Decimal("1"))
                _acc += _share
            if _pid in by_product:
                by_product[_pid][_field] += _share

    for _g in ship_groups.values():
        _alloc_to_lines(_g["lines"], _g["cost"], "shipping")
        if _g["deliv"]:
            # 고객배송수입을 revenue와 shipping_revenue 양쪽에 동시 누적 (불변식: revenue = product + shipping)
            _alloc_to_lines(_g["lines"], _g["deliv"], "revenue")
            _alloc_to_lines(_g["lines"], _g["deliv"], "shipping_revenue")
            # 배송수입에 대한 VAT도 라인 비례 배분
            _alloc_to_lines(_g["lines"], _g["deliv"] * Decimal("10") / Decimal("110"), "vat")
            # 배송비 수취분에 붙는 주문관리 수수료(정산 DELIVERY 원장 행)도 같은 비례로.
            _dc = _delivery_commission(_g["ch"], _g["deliv"])
            if _dc:
                _alloc_to_lines(_g["lines"], _dc, "commission")

    # option_id → product_id 매핑으로 광고비 직접 할당 (ad_data.db 기반 — 현재 비활성)
    option_to_product: dict[str, int] = {}
    for o in orders:
        if o.platform_product_id and o.product_id and o.platform_product_id not in option_to_product:
            option_to_product[o.platform_product_id] = o.product_id

    for oid, spend in ad_by_option.items():
        pid = option_to_product.get(oid)
        if pid and pid in by_product:
            by_product[pid]["ad_spend"] += spend

    # Meta 광고비 키워드 비례 배분 (cafe24 상품명 기반)
    cafe24_id = _get_cafe24_channel_id(channel_map)
    if cafe24_id:
        kw_day_spend = _get_meta_ad_spend_by_keyword_day(db, cafe24_id, date_from, date_to)
        if kw_day_spend:
            # 키워드/일/상품별 매출 집계 (비례 배분 기준)
            kw_day_products: dict[tuple[str, str], dict[int, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
            kw_day_total: dict[tuple[str, str], Decimal] = defaultdict(Decimal)

            for o in orders:
                ch = channel_map.get(o.channel_id)
                if not (ch and ch.code == "CAFE24"):
                    continue
                if o.status in REVENUE_EXCLUDED:
                    continue
                if not o.product_id:
                    continue
                pm = product_map.get(o.product_id)
                pname = (pm.product_name if pm else "") or (o.platform_product_name or "")
                kw = _extract_product_keyword(pname)
                if not kw:
                    continue
                d = str(o.order_date.date()) if hasattr(o.order_date, "date") else str(o.order_date)[:10]
                rev = _line_revenue(ch, o)
                kw_day_products[(kw, d)][o.product_id] += rev
                kw_day_total[(kw, d)] += rev

            for (kw, d), spend in kw_day_spend.items():
                total = kw_day_total.get((kw, d), ZERO)
                if total == ZERO:
                    continue
                for pid, prod_rev in kw_day_products.get((kw, d), {}).items():
                    if pid in by_product:
                        by_product[pid]["ad_spend"] += spend * prod_rev / total

    # Naver SA 광고비 키워드 비례 배분 (네이버 스마트스토어 상품명 기반)
    naver_id = _get_naver_channel_id(channel_map)
    if naver_id:
        naver_kw_day_spend = _get_naver_sa_ad_spend_by_keyword_day(db, naver_id, date_from, date_to)
        if naver_kw_day_spend:
            naver_kw_day_products: dict[tuple[str, str], dict[int, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
            naver_kw_day_total: dict[tuple[str, str], Decimal] = defaultdict(Decimal)

            for o in orders:
                ch = channel_map.get(o.channel_id)
                if not (ch and ch.code == "NAVER"):
                    continue
                if o.status in REVENUE_EXCLUDED:
                    continue
                if not o.product_id:
                    continue
                pm = product_map.get(o.product_id)
                pname = (pm.product_name if pm else "") or (o.platform_product_name or "")
                kw = _extract_naver_product_keyword(pname)
                if not kw:
                    continue
                d = str(o.order_date.date()) if hasattr(o.order_date, "date") else str(o.order_date)[:10]
                rev = _line_revenue(ch, o)
                naver_kw_day_products[(kw, d)][o.product_id] += rev
                naver_kw_day_total[(kw, d)] += rev

            for (kw, d), spend in naver_kw_day_spend.items():
                total = naver_kw_day_total.get((kw, d), ZERO)
                if total == ZERO:
                    continue
                for pid, prod_rev in naver_kw_day_products.get((kw, d), {}).items():
                    if pid in by_product:
                        by_product[pid]["ad_spend"] += spend * prod_rev / total

    # 반품 배송 손익 — 그 상품에 그대로 붙인다(반품 패키지는 실측상 전부 상품 1종).
    # 기간 내 판매가 0인 상품도 반품은 있을 수 있으므로 버킷을 새로 만든다.
    for pid, pnl in return_pnl_prod.items():
        if pid not in by_product:
            b = _new_bucket(with_order_count=False)
            b["quantity"] = 0
            by_product[pid] = b
        _apply_claim_shipping_pnl(by_product[pid], pnl)

    # 교환 배송 손익 — 특정 주문의 특정 상품이라 배분 추정이 필요 없다.
    for pid, pnl in exchange_pnl_prod.items():
        if pid not in by_product:
            b = _new_bucket(with_order_count=False)
            b["quantity"] = 0
            by_product[pid] = b
        _apply_claim_shipping_pnl(by_product[pid], pnl)

    result = []
    for pid, b in by_product.items():
        p = product_map.get(pid)
        if not p:
            continue
        # 부가세 차감(Jino 2026-08-04, 종전 '미차감' 방침 뒤집음) — 상세는 payable_vat 참조.
        net = (
            b["revenue"] - b["cost"] - b["commission"] - b["ad_spend"] - b["shipping"]
            - payable_vat(b["revenue"], b["cost"], b["commission"], b["shipping"], b["ad_spend"])
        )
        rate_pct = (net / b["revenue"] * 100) if b["revenue"] > 0 else ZERO

        result.append({
            "product_id": pid,
            "product_name": p.product_name,
            "internal_sku": p.internal_sku,
            "revenue": str(b["revenue"]),
            "product_revenue": str(b["product_revenue"]),
            "shipping_revenue": str(b["shipping_revenue"]),
            "cost": str(b["cost"]),
            "commission": str(b["commission"]),
            "ad_spend": str(b["ad_spend"]),
            "shipping": str(b["shipping"]),
            "net_profit": str(net),
            "profit_rate": str(rate_pct.quantize(Decimal("0.01")) if isinstance(rate_pct, Decimal) else "0.00"),
            "quantity": b["quantity"],
        })

    # 정렬
    sort_key = sort_by if sort_by in ("revenue", "net_profit", "profit_rate") else "revenue"
    result.sort(key=lambda x: Decimal(x[sort_key]), reverse=True)
    return result[:limit]


def _get_channel_ad_spend_daily(
    db: Session, channel_id: int, date_from: date, date_to: date
) -> dict[str, Decimal]:
    """ad_costs → 특정 채널의 일별 총 광고비 {date_str: spend} (XLSX 업로드분 포함)"""
    rows = db.execute(
        text("""
            SELECT ad_date, SUM(CAST(ad_spend AS REAL))
            FROM ad_costs
            WHERE channel_id = :cid AND ad_date >= :since AND ad_date <= :until
            GROUP BY ad_date
        """),
        {"cid": channel_id, "since": date_from.isoformat(), "until": date_to.isoformat()},
    ).fetchall()
    return {str(r[0]): Decimal(str(r[1])) for r in rows if r[1]}


def calculate_channel_daily_trend(
    db: Session, ad_db, date_from: date, date_to: date
) -> list[dict]:
    """채널별 일자별 매출/광고비/순이익 추이.

    기존 calculate_daily_trend 엔진을 채널별로 호출해 조합한다 (엔진 회귀 위험 0).
    위탁/수동매출 채널은 순이익 산정 불가 → net_profit=None.
    위탁 채널은 주문이 없어 엔진이 ad_spend=0을 주므로, ad_costs(XLSX 업로드분)를
    별도로 병합한다. 요약표(channel_summary)와 RoAS 일관성 유지.
    """
    channels = db.query(Channel).all()
    result: list[dict] = []
    for ch in channels:
        daily = calculate_daily_trend(db, ad_db, ch.id, date_from, date_to)
        is_consignment = ch.channel_type == "consignment"

        # 위탁 채널: 엔진은 주문이 없어 ad_spend=0 → ad_costs 테이블에서 직접 병합
        ch_ad_daily = (
            _get_channel_ad_spend_daily(db, ch.id, date_from, date_to)
            if is_consignment
            else {}
        )

        seen_dates: set[str] = set()
        for point in daily:
            d = point["date"]
            seen_dates.add(d)
            ad_spend = Decimal(point["ad_spend"])
            if is_consignment and d in ch_ad_daily:
                ad_spend += ch_ad_daily[d]
            result.append({
                "channel_id": ch.id,
                "channel_name": ch.name,
                "date": d,
                "revenue": point["revenue"],
                "product_revenue": point.get("product_revenue", "0"),
                "shipping_revenue": point.get("shipping_revenue", "0"),
                "ad_spend": str(ad_spend),
                "net_profit": None if is_consignment else point["net_profit"],
            })

        # 위탁 채널: 매출 없는 날에도 광고비가 있으면 ad-only 포인트 추가
        # (요약표 기간 합산 RoAS와 추이 차트 합산 RoAS 일치 보장)
        if is_consignment:
            for d, spend in ch_ad_daily.items():
                if d in seen_dates:
                    continue
                result.append({
                    "channel_id": ch.id,
                    "channel_name": ch.name,
                    "date": d,
                    "revenue": "0",
                    "product_revenue": "0",
                    "shipping_revenue": "0",
                    "ad_spend": str(spend),
                    "net_profit": None,
                })
    return result


# ──────────────────────────────────────────────
# 회사별 그룹핑 (Sprint 4B-company-grouping)
# 엔진 미변경 — 채널 단위 산출 결과를 회사 > leaf 로 묶는 순수 함수
# ──────────────────────────────────────────────


#: 쿠팡 채널의 판매경로별 leaf 라벨 (D-CPP-47). 축은 `Channel.sell_type`이다 —
#: seed.py가 3P/RG/1P를 채널마다 못 박아 두었고, 이게 「윙이냐 로켓그로스냐」를 가르는 유일한
#: 결정적 축이다(`channel_type`의 consignment는 1P만 가르고 3P·RG를 못 가른다).
#: ★종전엔 3P와 RG가 «쿠팡 로켓그로스·윙» 한 줄로 뭉쳐 있었다. 두 판매경로는 **수수료 구조가
#:   다르고**(3P=판매수수료만 / RG=판매수수료+풀필먼트+반품, ref 17 §1의 9종 체계) 매출 원장도
#:   다르다(3P=`orders` / RG=콘솔 net 축). 한 줄로 뭉치면 어느 쪽이 적자인지 화면이 말할 수 없다.
_COUPANG_SEGMENT_BY_SELL_TYPE: dict[str, tuple[str, bool]] = {
    "3P": ("쿠팡 3P(판매자배송)", True),
    "RG": ("쿠팡 로켓그로스", True),
    "1P": ("쿠팡 로켓배송(1P)", False),
}


def _classify_channel(ch: Channel) -> tuple[str, str, bool]:
    """채널 → (회사, leaf 라벨, 순이익 산정가능 여부)"""
    company = ch.company or "미지정"
    code = ch.code or ""
    if code.startswith("COUPANG"):
        seg_has = _COUPANG_SEGMENT_BY_SELL_TYPE.get(ch.sell_type or "")
        if seg_has is not None:
            seg, has_profit = seg_has
        elif ch.channel_type == "consignment":  # sell_type 미지정 폴백 — 위탁이면 1P다
            seg, has_profit = "쿠팡 로켓배송(1P)", False
        else:
            # sell_type이 비었고 위탁도 아니다 — 추측하지 않는다(D-3: 사실만, 판단은 Jino).
            # 채널 이름을 그대로 써서 «분류 안 됨»이 화면에 드러나게 한다.
            seg, has_profit = ch.name or "쿠팡 (분류 안 됨)", True
    elif code == "CAFE24":
        seg, has_profit = "자사몰(cafe24)", True
    elif code == "NAVER":
        seg, has_profit = "네이버 스마트스토어", True
    else:
        seg, has_profit = ch.name, True
    return company, f"{company} · {seg}", has_profit


def get_channel_company_map(db: Session) -> dict[int, tuple[str, str, bool]]:
    """{channel_id: (회사, leaf 라벨, has_profit)}"""
    return {ch.id: _classify_channel(ch) for ch in db.query(Channel).all()}


def _agg_block() -> dict:
    return {
        "revenue": ZERO, "product_revenue": ZERO, "shipping_revenue": ZERO,
        "ad_spend": ZERO, "order_count": 0,
        "net_profit": ZERO, "measurable_rev": ZERO,
        # 원가를 못 붙인 제품매출 — 이익률을 위로 부풀리므로 행마다 자백한다(D-22).
        "unmapped_revenue": ZERO,
        # 손익이 「광고비만 반영된 하한」으로 들어온 자식의 광고비 합(D-22).
        "net_floor_ad": ZERO,
    }


def net_contribution(row: dict, revenue: Decimal) -> tuple[Decimal | None, Decimal, Decimal]:
    """이 채널 행이 부모 손익에 얹는 것 → (순이익, 이익률 분모, 하한으로 들어간 광고비).

    ★`net_profit`이 None이어도 **광고비가 나갔으면 `-광고비` 하한을 만든다.**
      이 판정을 producer(각 채널 엔진)가 아니라 **집계층 한 곳**에 두는 이유:
      손익을 못 내는 행을 만드는 곳은 앞으로도 늘어나는데(로켓1P 계산서 축,
      `calculate_channel_summary`의 수동매출-only 채널, 다음에 붙을 채널…),
      각자 하한을 기억하게 하면 **한 곳만 빠져도 그 채널의 광고비가 조용히 샌다.**
      실제로 그렇게 샜다 — 2026-08-18 하루 597,888원(D-22 적대 리뷰 1R P1-1).
      여기서 막으면 「net=None인데 ad_spend가 있는 행」이라는 모양 자체가 불가능해진다.

    ★광고비도 0이면 (None, 0, 0) — 아는 게 없으면 "—"가 정직하다.
    """
    net = row.get("net_profit")
    ad = Decimal(row.get("ad_spend") or "0")
    if net is None:
        return (None, ZERO, ZERO) if ad <= ZERO else (-ad, ZERO, ad)
    basis = row.get("net_basis_revenue")
    return (
        Decimal(net),
        revenue if basis is None else Decimal(basis),
        ad if row.get("net_scope") == NET_SCOPE_AD_ONLY else ZERO,
    )


def _add_net(block: dict, row: dict, revenue: Decimal) -> None:
    """집계 net은 '측정가능分 합' (기존 대시보드 의미론).

    ★2026-08-19 D-22 — **손익 분모를 행이 직접 정할 수 있게 했다.** 종전엔 net이 있으면
      그 행의 매출 전액을 이익률 분모(`measurable_rev`)에 넣었는데, 로켓1P 계산서 축은
      **매출은 계산서대로 있지만 그 매출의 원가를 모른다.** 그 매출을 분모에 넣으면
      「광고비만 반영된 하한 손익 ÷ 원가를 모르는 매출」이라는 뜻 없는 비율이 나온다.
      그래서 행이 `net_basis_revenue`를 주면 그 값을, 안 주면 종전대로 매출 전액을 쓴다.

    ★같은 개정에서 **net이 None인 자식은 이제 원칙적으로 없다** — 손익을 못 재는 채널도
      광고비만큼은 확정 비용이라 `-광고비` 하한을 낸다(rocket_1p_channel_pnl 참조).
      그 전엔 광고비가 부모 `ad_spend`에는 더해지는데 부모 `net_profit`에서는 한 번도
      안 빠져서, 2026-08-18 하루에만 597,888원이 손익에서 증발했다(Jino 발견).
    """
    net, basis_rev, floor_ad = net_contribution(row, revenue)
    if net is None:
        return
    block["net_profit"] += net
    block["measurable_rev"] += basis_rev
    block["net_floor_ad"] += floor_ad


# 로켓1P leaf에만 있는 부가 필드(축·원가 커버리지·분담금). 회사/전체로는 **올리지 않는다** —
# 축이 다른 채널을 섞어 놓고 "이 회사의 매출 축"이라고 말할 수 없기 때문이다.
#
# ★여기에 없으면 그 필드는 **집계층에서 조용히 사라진다.** 서비스층에서 아무리 정직하게 계산해도
#   화면엔 안 뜬다 — 교훈 #321(서비스층 테스트는 초록인데 HTTP body엔 키가 없어 배너가 통째로
#   숨었다)과 같은 결함 모양이다. 실제로 D-CPP-47 초판이 `ad_unallocated`·`option_axis_days`를
#   여기 안 넣어 적대 리뷰 P1으로 잡혔다 — **자백하라고 만든 칸을 자백 못 하게 만드는 자리가 여기다.**
#   ⇒ 새 자백 칸을 만들면 여기와 `schemas.GroupedSummaryRow` **둘 다** 손대야 하고,
#     HTTP 경계를 넘는지 테스트로 확인해야 한다(서비스층 dict 테스트는 원리적으로 못 잡는다).
_LEAF_PASSTHROUGH = (
    "revenue_basis", "cost_coverage", "promo_burden",
    # RG leaf(D-CPP-47) — 매출·원가의 축이 갈려 있어 행이 자기 신뢰도를 스스로 말해야 한다.
    "option_axis_days",         # 옵션축이 창을 얼마나 덮었나 ("16/16")
    "ad_unallocated",           # 카탈로그에 없는 옵션에 쓰인 광고비(= 이 행에 안 실린 돈)
    "ad_unallocated_options",
    "units_sold",               # 판매수량 — `order_count`(주문 건수)와 뜻이 다르다
)


def _finalize(kind: str, company: str | None, label: str, b: dict) -> dict:
    """집계 블록 → 화면 행.

    ★net_scope(D-22) — 이 행의 순이익이 무엇을 담고 있는지 **행 스스로 말한다**:
      full     = 매출·원가·수수료·광고비까지 다 반영된 손익
      ad_only  = 손익을 못 재는 구간이라 **확정 비용인 광고비만** 반영된 하한
      partial  = 위 둘이 섞임(회사·전체 소계) — `net_floor_ad`가 그 하한 몫
      비율(`profit_rate`)의 분모는 **손익을 잰 매출**이지 표의 총매출이 아니다.
    """
    floor_ad = b.get("net_floor_ad", ZERO)
    if b["measurable_rev"] > 0:
        net = b["net_profit"]
        rate = (net / b["measurable_rev"] * 100).quantize(Decimal("0.01"))
        net_s, rate_s = str(net), str(rate)
        scope = NET_SCOPE_PARTIAL if floor_ad > 0 else NET_SCOPE_FULL
    elif floor_ad > 0:
        # 손익을 잰 매출이 0인데 광고비는 나갔다 — 하한만 낸다(비율은 분모가 없어 "—").
        net_s, rate_s = str(b["net_profit"]), None
        scope = NET_SCOPE_AD_ONLY
    else:
        net_s, rate_s, scope = None, None, None  # 잴 것이 아무것도 없다
    return {
        "kind": kind,
        "company": company,
        "label": label,
        "revenue": str(b["revenue"]),
        "product_revenue": str(b["product_revenue"]),
        "shipping_revenue": str(b["shipping_revenue"]),
        "ad_spend": str(b["ad_spend"]),
        "net_profit": net_s,
        "profit_rate": rate_s,
        "order_count": b["order_count"],
        "unmapped_revenue": str(b.get("unmapped_revenue", ZERO)),
        "net_scope": scope,
        "net_floor_ad": str(floor_ad),
        "net_basis_revenue": str(b["measurable_rev"]),
        **{k: b[k] for k in _LEAF_PASSTHROUGH if b.get(k) is not None},
    }


def group_summary_by_company(
    rows: list[dict], cmap: dict[int, tuple[str, str, bool]]
) -> list[dict]:
    """채널 요약 행 → [전체, 회사소계, leaf...] 계층 평탄 리스트.

    rows: calculate_channel_summary 출력 (채널 단위, 엔진 미변경).
    """
    leaves: dict[str, dict] = {}
    leaf_company: dict[str, str] = {}
    companies: dict[str, dict] = {}
    total = _agg_block()

    for r in rows:
        cid = r["channel_id"]
        company, leaf_label, _ = cmap.get(cid, ("미지정", f"미지정 · {r['channel_name']}", True))
        rev = Decimal(r["revenue"])

        lb = leaves.setdefault(leaf_label, _agg_block())
        leaf_company[leaf_label] = company
        cb = companies.setdefault(company, _agg_block())

        prod_rev = Decimal(r.get("product_revenue", "0"))
        ship_rev = Decimal(r.get("shipping_revenue", "0"))
        for k in _LEAF_PASSTHROUGH:      # leaf 블록에만 실어 보낸다(회사/전체 제외)
            if r.get(k) is not None:
                lb[k] = r[k]
        unmapped = Decimal(r.get("unmapped_revenue") or "0")
        for blk in (lb, cb, total):
            blk["revenue"] += rev
            blk["product_revenue"] += prod_rev
            blk["shipping_revenue"] += ship_rev
            blk["ad_spend"] += Decimal(r["ad_spend"])
            blk["order_count"] += r["order_count"]
            blk["unmapped_revenue"] += unmapped
            _add_net(blk, r, rev)

    out: list[dict] = [_finalize("total", None, "전체", total)]
    # 회사 매출 desc, 회사 내 leaf 매출 desc
    for company in sorted(companies, key=lambda c: companies[c]["revenue"], reverse=True):
        out.append(_finalize("company", company, company, companies[company]))
        c_leaves = [ll for ll, comp in leaf_company.items() if comp == company]
        for ll in sorted(c_leaves, key=lambda x: leaves[x]["revenue"], reverse=True):
            out.append(_finalize("leaf", company, ll, leaves[ll]))
    return out


def group_trend_by_company(
    points: list[dict], cmap: dict[int, tuple[str, str, bool]]
) -> list[dict]:
    """채널별 일자 추이 → leaf 그룹 단위 일자 추이.

    points: calculate_channel_daily_trend 출력 (채널 단위, 엔진 미변경).
    """
    agg: dict[tuple[str, str], dict] = {}
    meta: dict[str, str] = {}  # leaf_label → company
    for p in points:
        cid = p["channel_id"]
        company, leaf_label, _ = cmap.get(cid, ("미지정", f"미지정 · {p['channel_name']}", True))
        meta[leaf_label] = company
        key = (leaf_label, p["date"])
        b = agg.setdefault(key, {"revenue": ZERO, "product_revenue": ZERO,
                                 "shipping_revenue": ZERO, "ad_spend": ZERO,
                                 "net_profit": ZERO, "has_measurable": False})
        b["revenue"] += Decimal(p["revenue"])
        b["product_revenue"] += Decimal(p.get("product_revenue", "0"))
        b["shipping_revenue"] += Decimal(p.get("shipping_revenue", "0"))
        b["ad_spend"] += Decimal(p["ad_spend"])
        if p.get("net_profit") is not None:
            b["net_profit"] += Decimal(p["net_profit"])
            b["has_measurable"] = True

    out: list[dict] = []
    for (leaf_label, d), b in agg.items():
        out.append({
            "group": leaf_label,
            "company": meta[leaf_label],
            "date": d,
            "revenue": str(b["revenue"]),
            "product_revenue": str(b["product_revenue"]),
            "shipping_revenue": str(b["shipping_revenue"]),
            "ad_spend": str(b["ad_spend"]),
            "net_profit": str(b["net_profit"]) if b["has_measurable"] else None,
        })
    return out
