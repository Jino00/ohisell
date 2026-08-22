# intelligence.py — 쿠팡 종합 조망(Command Center) 결합 엔진 (트랙 P7, D-2/D-3)
# 목적: 옵션ID(vendor_item_id, 결합축 D-8)를 단일 키로 5개 소스를 각자 집계한 뒤 병합해
#       ① 회계(진짜 순이익) ② 광고(사실 정리) ③ 상품(판매현황) 3축을 파생한다.
# ★설계 핵심(스키마 검증으로 확정):
#   ① Fan-out 방지: 거대 단일 JOIN(N×M×K×J 곱) 금지 — 각 소스를 vendor_item_id로 따로
#      GROUP BY 집계한 뒤 dict merge (profit_calculator 패턴과 동일).
#   ② 날짜축이 소스마다 다름: 주문 order_date·광고 report_date·수수료 recognition_date·
#      반품 requested_at — 각자 자기 날짜로 기간 필터 후 옵션ID로 병합.
#   ③ orders는 전 채널 공용: platform='coupang' 채널만 필터(네이버·cafe24 제외).
# D-3: 시스템은 사실/지표 정리만 — 전략 추천 없음. 해석은 Jino 몫.
from __future__ import annotations

import logging
import os
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    Channel,
    CoupangAdOptionDaily,
    CoupangProductItem,
    CoupangReturnItem,
    CoupangRevenueFee,
    CoupangRgOrderItem,
    CoupangRgSettlementFee,
    Order,
    ProductChannelMapping,
    ProductMaster,
)
from app.services.cafe24_status_mapper import REVENUE_EXCLUDED
from app.utils.kst import kst_today
from app.services.coupang.ad_sell_type import (
    AD_SELL_TYPE_2P,
    ORDER_BASED_SELL_TYPES,
)
from app.services.coupang.rg_net_revenue import (
    accounts_in_window,
    net_revenue_by_option,
    option_axis_coverage,
)
from app.services.coupang.option_fee_rate import (
    BASIS_DEFAULT,
    BASIS_SETTLED,
    DEFAULT_FEE_RATE,
    FEE_VAT_MULT,
    fee_reconciliation,
    option_fee_rates,
)
from app.services.coupang.revenue_fee_source import COUPANG_3P_CODES
from app.services.coupang.settlement_revenue_adjust import settlement_revenue_adjustment

log = logging.getLogger(__name__)

_Z = Decimal("0")
_Q2 = Decimal("0.01")   # 금액 — 원 단위 소수 2자리(미양자화 시 25자리가 API로 새어나간다)
_Q4 = Decimal("0.0001")  # 비율(ROAS·CTR·반품률) 표시 자리수 — JSON 정리


def _ratio(num: Decimal, den) -> "Decimal | None":
    """비율 = num/den. den 0이면 None. 결과는 4자리로 quantize(긴 소수 방지)."""
    if not den:
        return None
    return (num / Decimal(den)).quantize(_Q4)


def _f(v) -> Decimal:
    """None/숫자 → Decimal. 집계 결과(func.sum)가 None(행 없음)이면 0."""
    if v is None:
        return _Z
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


# ──────────────────────────────────────────────
# 계정 분리(S1, 트랙 reconciliation D-4): 계정 키 → 소스별 필터 도출 SA
# ──────────────────────────────────────────────
def _resolve_account(db: Session, account_key: str | None) -> dict:
    """계정 키(COUPANG_WING1=오픽스/COUPANG_WING2=오하이테크) → 소스별 필터 값.

    ★소스마다 계정 식별 키가 다르다(라이브 확정 2026-06-14):
      매출=channel_id(Channel.code==account_key) · 광고·상품=vendor_id(A01564720 등) ·
      RG정산·반품·매출내역=account_key(세 테이블 모두 account_key 컬럼 보유 — 직접 필터, 상품마스터
      조인 불필요 → orphan vendor_item_id 누락 0).
    한 곳에서 모두 도출해 각 집계 SA에 optional 주입한다(원칙18-6 정보유통 허브).

    account_key가 None이면 전부 None 반환 → 각 SA가 WHERE를 안 붙임 → 전체 합산(기존 동작 불변,
    등가성 계약). 알 수 없는 account_key면 채널/벤더 빈집합 → 빈 결과(예외 대신 빈 뷰).
    """
    if account_key is None:
        return {"channel_ids": None, "vendor_id": None, "account_key": None}
    # 매출: 같은 법인(company)의 모든 쿠팡 채널로 매핑. fees/returns/RG정산이 account_key(법인 단위
    #   Wing키)로 필터되는 것과 orders 도메인을 맞춰 sum(계정)==전체 불변식을 견고하게 보장한다
    #   (Codex S1 P1#2). 현재 orders는 Wing 3P(채널 WING1/WING2)만 있고 RG/ROCKET 채널은 0행이라
    #   수치는 Channel.code 단독 매핑과 동일하지만, ROCKET/RG 주문이 생겨도 불변식이 안 깨진다.
    #   RG 매출은 S3에서 CoupangRgOrderItem로 별도 편입(orders 테이블엔 RG 없음 → 이중계상 없음).
    company = (db.query(Channel.company)
               .filter(Channel.code == account_key, Channel.platform == "coupang")
               .scalar())
    channel_ids = ([cid for (cid,) in
                    db.query(Channel.id).filter(Channel.platform == "coupang",
                                                Channel.company == company).all()]
                   if company else [])
    # 광고·상품: vendor_id. env(COUPANG_WING1_VENDOR_ID) 우선, 없으면 상품 스냅샷에서 도출.
    # ★account 지정 시 vendor_id는 항상 비-None 문자열로 둔다(None은 "전체"와 과부하). 미해결이면
    #   "" sentinel → 광고 SA가 vendor_id=="" 필터(실데이터 매칭 0)로 빈집합 반환(전체 누수 차단).
    vendor_id = os.getenv(f"{account_key}_VENDOR_ID")
    if not vendor_id:
        row = (db.query(CoupangProductItem.vendor_id)
               .filter(CoupangProductItem.account_key == account_key).first())
        vendor_id = row[0] if row else ""
    return {"channel_ids": channel_ids, "vendor_id": vendor_id, "account_key": account_key}


# ──────────────────────────────────────────────
# 소스별 기간 집계 (각 SA는 vendor_item_id 키 dict 반환 — 단독 GROUP BY, fan-out 없음)
# ──────────────────────────────────────────────
def _agg_orders(db: Session, dfrom: date, dto: date,
                channel_ids: list[int] | None = None) -> dict[str, dict]:
    """쿠팡 채널 주문을 옵션ID별 집계. 매출=Σ(selling_price), 단가=매출/수량.

    ★S2(2중계상 버그 수정, 라이브 확정 2026-06-14): 쿠팡 적재 시 selling_price=orderPrice인데
    orderPrice는 이미 라인총액(salesPrice×shippingCount)이다(raw 실증: salesPrice 16,900×수량 2
    =orderPrice 33,800). 따라서 매출은 Σ(selling_price)이며 ×quantity를 곱하면 안 된다(곱하면
    qty>1 주문에서 2~3배 부풀림). 단가(unit_price)=매출/수량으로 가중평균 단가가 나와 반품차감
    추정(unit_price×반품수량)도 정확해진다. (네이버 totalPaymentAmount도 라인총액 — profit_calculator의
    평행 2중계상은 별도 surface, channel.py 적재 의미 채널별 상이: cafe24만 단가.)

    codex[P2]: 취소/반품/입금전(REVENUE_EXCLUDED) 주문은 매출에서 제외 — 기존 profit_calculator와
    동일 기준. 안 거르면 ① 매출 부풀림 ② coupang_return_item에서 또 차감 = 이중차감.

    S1: channel_ids 주면 해당 채널만(계정 분리). None이면 전체 쿠팡 채널(기존 동작 불변).
    """
    start = datetime.combine(dfrom, time.min)
    end = datetime.combine(dto, time.max)
    q = (
        db.query(
            Order.platform_product_id,
            func.sum(Order.selling_price),  # selling_price=orderPrice=라인총액(이미 ×수량) — S2
            func.sum(Order.quantity),
            func.count(Order.id),
            func.max(Order.platform_product_name),
        )
        .join(Channel, Order.channel_id == Channel.id)
        .filter(
            Channel.platform == "coupang",
            Order.platform_product_id != "",
            Order.status.notin_(tuple(REVENUE_EXCLUDED)),
            Order.order_date >= start,
            Order.order_date <= end,
        )
    )
    if channel_ids is not None:
        q = q.filter(Order.channel_id.in_(channel_ids))
    rows = q.group_by(Order.platform_product_id).all()
    out: dict[str, dict] = {}
    for vid, revenue, qty, cnt, name in rows:
        rev = _f(revenue)
        q = int(qty or 0)
        out[str(vid)] = {
            "revenue": rev,
            "qty": q,
            "order_count": int(cnt or 0),
            "unit_price": (rev / q) if q else _Z,  # 반품 차감액 추정용 평균 단가
            "name": name,
        }
    return out


def _agg_rg_orders(db: Session, dfrom: date, dto: date,
                   account_key: str | None = None) -> dict[str, dict]:
    """로켓그로스(RG) **gross 주문 원장**을 옵션ID별 집계. 매출=Σ(unit_sales_price×sales_quantity), paid_at 기준.

    ★D-CPP-49(계약 ⓑ) 이후 **이 함수는 종합조망 매출의 원천이 아니다.** compute_command_center는
      `rg_net_revenue.net_revenue_by_option`(콘솔 net 옵션축)을 읽는다 — 대시보드가 이미 서 있는
      축이고, 둘이 다른 축에 서 있으면 같은 화면이 RG 매출을 두 값으로 말한다.
      이 함수가 남아 있는 이유는 둘이다:
        ① `revenue_canonical._agg_by_type` — 여기선 gross가 **옳다**. 닫힌일은 Wing net을 옵션에
           안분하는 «가중치»로 쓰이고, 당일분은 콘솔이 D+1이라 net이 원리적으로 없어 gross가
           유일한 프록시다. net으로 바꾸면 factor_rg≈1이 되어 gross→net 환산이 죽는다.
        ② `revenue_reconcile` — 「우리 수집이 콘솔과 얼마나 벌어졌나」를 재는 진짜 신호.
           ref 89 실측 +11.8%가 그것이고, 그 갭은 net끼리 비교하면 정의상 사라진다.

    ★S3(트랙 reconciliation D-2): RG 매출은 이미 CoupangRgOrderItem로 수집되나 종합조망 매출에
    미편입이었다(라이브 진단: 오픽스 6/1~6/11 쿠팡 판매분석 4,901,500 중 절반이 RG인데 우리는
    3P만). 이 SA가 RG 매출을 옵션ID(vendor_item_id, 동일 결합축 D-8)로 집계해 compute_command_center가
    orders(Wing 3P)에 병합한다. account_key 컬럼 직접 필터(계정 분리, S1과 동일 도메인).
    RG 단가 필드는 적재 시 unit_sales_price로 정규화됨(목록 unitSalesPrice/단건 salesPrice, D-8).
    RG 반품·수수료·광고는 CoupangRevenueFee/AdOptionDaily에 없고 RG 정산(rg_total)에만 있어
    net_profit summary 플립에서 차감된다(중복 없음). RG 원가는 cost_master(내부원가) 경유 반영.
    """
    start = datetime.combine(dfrom, time.min)
    end = datetime.combine(dto, time.max)
    q = (
        db.query(
            CoupangRgOrderItem.vendor_item_id,
            func.sum(CoupangRgOrderItem.unit_sales_price * CoupangRgOrderItem.sales_quantity),
            func.sum(CoupangRgOrderItem.sales_quantity),
            func.count(CoupangRgOrderItem.id),
            func.max(CoupangRgOrderItem.product_name),
        )
        .filter(CoupangRgOrderItem.paid_at >= start, CoupangRgOrderItem.paid_at <= end)
    )
    if account_key is not None:
        q = q.filter(CoupangRgOrderItem.account_key == account_key)
    rows = q.group_by(CoupangRgOrderItem.vendor_item_id).all()
    out: dict[str, dict] = {}
    for vid, rev, qty, cnt, name in rows:
        r = _f(rev)
        out[str(vid)] = {"revenue": r, "qty": int(qty or 0),
                         "order_count": int(cnt or 0), "name": name}
    return out


def _merge_rg_orders(orders: dict[str, dict], rg_orders: dict[str, dict]) -> Decimal:
    """RG 주문 매출을 orders(Wing 3P) dict에 옵션ID로 병합(가산). 반환=병합된 RG 매출 총액.

    같은 vendor_item_id가 3P·RG 양쪽에 있으면 매출·수량 가산(이중계상 아님 — 서로 다른 판매경로).
    by_option·summary 모두 RG 포함하게 되어 summary==Σby_option 일관성 유지(S3). D-14 개정:
    RG 매출 미집계 전제가 이 트랙 D-2로 변경됨(by_option도 RG 반영).

    ★unit_price는 절대 재계산하지 않는다(Codex S3 P1#1): unit_price는 반품차감(return_deduction)
    추정에만 쓰이는데 반품(CoupangReturnItem)은 3P 전용이다. RG 매출을 섞어 평균단가를 바꾸면
    같은 vid를 3P·RG 둘 다 파는 경우 3P 반품차감이 RG 판매에 오염된다. 3P 단가(_agg_orders가 설정)를
    그대로 둔다. RG 단독 vid는 unit_price 키가 없지만(merge 루프가 sale_price로 폴백) RG 반품은
    CoupangReturnItem에 없어 return_qty=0 → return_deduction=0이라 무관하다."""
    rg_total_rev = _Z
    for vid, rg in rg_orders.items():
        rg_total_rev += rg["revenue"]
        o = orders.get(vid)
        if o is None:
            o = {"revenue": _Z, "qty": 0, "order_count": 0, "name": rg.get("name")}
            orders[vid] = o
        o["revenue"] = o.get("revenue", _Z) + rg["revenue"]
        o["qty"] = o.get("qty", 0) + rg["qty"]
        o["order_count"] = o.get("order_count", 0) + rg["order_count"]
        o["name"] = o.get("name") or rg.get("name")
        # unit_price는 의도적으로 건드리지 않음 — 3P 단가 보존(위 docstring, Codex P1#1).
    return rg_total_rev


def _rg_axis_coverage(db: Session, dfrom: date, dto: date,
                      account_key: str | None) -> dict:
    """창 안에서 RG 옵션축이 며칠을 덮었나 — **절대 None을 반환하지 않는다.**

    ★None 금지가 규칙인 이유(적대 리뷰 1R P1): 프론트는 「complete === false」일 때만 경고를
      그린다. None이 나가면 경고도 「N/M일」 힌트도 통째로 사라져 화면이 「RG 0원」이라고
      **단정**한다 — 이 변경이 막겠다고 선언한 바로 그 실패 모양이다(교훈 #123).
      초판은 요약축에 RFM 행이 하나도 없는 창(예: 「어제」 버튼 = 오늘~오늘)에서 None을 냈고,
      같은 사실인데 계정 지정 뷰는 실토하고 전체합산 뷰는 침묵했다.
      ⇒ 「모른다」를 표현하는 방법은 필드를 지우는 것이 아니라 **complete=False**다.

    ★«닫힌 날»만 분모로 센다(적대 리뷰 1R P2-5): 콘솔은 D+1이라 오늘은 원리적으로 못 덮는다.
      오늘을 분모에 넣으면 **오늘이 포함된 모든 창**(기본 7일 창 포함)이 영원히 미완이 되어
      경고가 상시 켜지고 무뎌진다 — 「백필 구멍」과 「오늘이라 당연히 빈 것」이 구분이 안 된다.
      아직 안 닫힌 날은 경고가 아니라 **사실**(`open_days`)로 따로 낸다.
      ⚠️`option_axis_coverage` 자체의 뜻은 안 바꾼다 — 대시보드(`rg_channel_pnl`)가 그걸로
      원가·순이익을 게이트하는 배포된 경로라, 여기 사정으로 그 의미를 흔들면 안 된다.

    ★account=None이면 **가장 나쁜 계정 기준**으로 답한다(한 계정이라도 못 덮으면 합계는 부분치).
      분모 계정 목록은 `accounts_in_window`(두 축 합집합·등록유형 무필터)에서 온다.
    """
    yesterday = kst_today() - timedelta(days=1)
    closed_end = min(dto, yesterday)
    # ★`dto - closed_end`로 세면 안 된다(적대 리뷰 2R P2-2): `dfrom`이 미래면 창 밖의 날까지
    #   세어 「5일 창에 미확정 6일」 같은 값이 나온다. 창 «안»의 안 닫힌 날만 센다.
    open_start = max(dfrom, yesterday + timedelta(days=1))
    open_days = (dto - open_start).days + 1 if dto >= open_start else 0

    if closed_end < dfrom:
        # 창 전체가 아직 안 닫혔다(오늘만 보는 창 등). 「0일 덮었다」가 아니라 「닫힌 날이 없다」다.
        return {"days_total": 0, "days_covered": 0, "first_date": None, "last_date": None,
                "complete": False, "open_days": open_days, "accounts": []}

    days_total = (closed_end - dfrom).days + 1
    keys = [account_key] if account_key is not None else accounts_in_window(db, dfrom, closed_end)
    if not keys:
        # 어느 계정의 콘솔 축도 이 창에 없다 → 「RG 0원」이 아니라 「미상」이다.
        return {"days_total": days_total, "days_covered": 0, "first_date": None,
                "last_date": None, "complete": False, "open_days": open_days, "accounts": []}

    covs = [option_axis_coverage(db, dfrom, closed_end, k) for k in keys]
    worst = min(covs, key=lambda c: c["days_covered"])
    return {
        "days_total": worst["days_total"],
        "days_covered": worst["days_covered"],
        "first_date": worst["first_date"],
        "last_date": worst["last_date"],
        "complete": all(c["complete"] for c in covs),
        "open_days": open_days,
        "accounts": list(keys),
    }


def _orphan_return_stats(db: Session, dfrom: date, dto: date,
                         account_key: str | None = None,
                         channel_ids: list[int] | None = None) -> dict:
    """차감에서 «빠진» 반품이 몇 건인지 센다 — 억제는 조용히 하면 안 된다(D-CPP-33).

    orphan  = 원주문이 orders에 아예 없다(수집 전 취소분). 매출에 없으니 차감 대상도 아니다.
    excluded= 원주문이 있으나 status가 매출제외다. 이미 매출에서 빠져 이중차감이 될 뻔한 것.
    화면이 「반품 N건 중 M건은 매출에 없어 차감하지 않았다」고 실토할 수 있게 한다.
    """
    start = datetime.combine(dfrom, time.min)
    end = datetime.combine(dto, time.max)
    base = db.query(CoupangReturnItem).filter(
        CoupangReturnItem.withdrawn.is_(False),
        CoupangReturnItem.requested_at >= start,
        CoupangReturnItem.requested_at <= end,
    )
    if account_key is not None:
        base = base.filter(CoupangReturnItem.account_key == account_key)

    def _order_q(counted: bool):
        qq = (
            db.query(Order.id)
            .join(Channel, Order.channel_id == Channel.id)
            .filter(
                Channel.platform == "coupang",
                Order.order_number == CoupangReturnItem.order_id,
                Order.platform_product_id == CoupangReturnItem.vendor_item_id,
            )
        )
        qq = qq.filter(Order.status.notin_(tuple(REVENUE_EXCLUDED)) if counted
                       else Order.status.in_(tuple(REVENUE_EXCLUDED)))
        if channel_ids is not None:
            qq = qq.filter(Order.channel_id.in_(channel_ids))
        return qq

    total = base.count()
    deducted = base.filter(_order_q(True).exists()).count()
    excluded = base.filter(_order_q(False).exists()).count()
    return {
        "return_rows": total,
        "deducted_rows": deducted,
        "suppressed_excluded_rows": excluded,
        "suppressed_orphan_rows": total - deducted - excluded,
    }


def _agg_seller_shipping_3p(db: Session, dfrom: date, dto: date,
                            channel_ids: list[int] | None = None) -> dict:
    """쿠팡 3P 배송의 «비용»과 «수입»을 배송(박스) 단위로 함께 집계한다.

    ★D-CPP-33: 종전엔 비용(한진 1,900)만 세고 고객이 낸 배송비 «수입»은 안 셌다 — 구 대시보드는
      둘 다 센다(shipping_revenue). 비용만 빼고 수입은 안 세면 배송이 무조건 손해로 잡힌다.
      수입은 `_delivery_income`(구 대시보드와 같은 SoT)을 배송당 1회만 가산한다 — 쿠팡은 배송
      단위 값을 박스 내 모든 라인에 복사해 두므로 라인마다 더하면 과대계상된다(2026-08-03 실증).
    ★수입을 «매출»이 아니라 «순이익»에만 더하는 이유: 종합조망의 매출 축은 쿠팡 판매분석과
      1:1 대조하는 축이다(S2 정본매출). 배송 수입을 매출에 섞으면 그 대조가 깨진다.

    구 대시보드는 차감하나 종합조망은 누락했던 3P 실비용(Jino 2026-06-15 통일 결정).
    3P(판매자배송)에서만 발생 — RG/로켓은 쿠팡 풀필먼트라 Order 테이블에 행이 없어 자동 0.
    ★구 대시보드 profit_calculator._shipment_key(배송 dedup=shipmentBoxId)·HANJIN_PER_SHIPMENT(단가)
      를 그대로 재사용(SoT 단일 정의). 매출 기준과 동일하게 REVENUE_EXCLUDED·위탁은 제외(_agg_orders 정합).
    channel_ids 주면 해당 계정만(없으면 전체 쿠팡). 데이터 없으면 0(회귀 가드)."""
    from app.services.profit_calculator import (
        HANJIN_PER_SHIPMENT, _delivery_income, _shipment_key,
    )
    from app.services.coupang.revenue_fee_source import COUPANG_3P_CODES
    start = datetime.combine(dfrom, time.min)
    end = datetime.combine(dto, time.max)
    chmap = {c.id: c for c in db.query(Channel).filter(Channel.platform == "coupang").all()}
    q = (
        db.query(Order)
        .join(Channel, Order.channel_id == Channel.id)
        .filter(
            Channel.platform == "coupang",
            Order.status.notin_(tuple(REVENUE_EXCLUDED)),
            Order.order_date >= start,
            Order.order_date <= end,
        )
    )
    if channel_ids is not None:
        q = q.filter(Order.channel_id.in_(channel_ids))
    seen: set = set()
    income = _Z
    for o in q.all():
        ch = chmap.get(o.channel_id)
        # ★3P(판매자배송) 채널만 — RG/로켓은 쿠팡 풀필먼트라 판매자 한진비 없음(codex P1).
        #   RG 채널은 marketplace 타입이라 consignment 필터로는 안 걸러짐 → 3P 코드로 명시 제한.
        if not (ch and ch.code in COUPANG_3P_CODES):
            continue
        skey = _shipment_key(ch, o)
        if skey in seen:
            continue          # 배송 1건당 1회 — 비용도 수입도(라인 복사값 과대계상 방지)
        seen.add(skey)
        income += _delivery_income(ch, o)
    return {
        "cost": HANJIN_PER_SHIPMENT * len(seen),
        "income": income,
        "shipments": len(seen),
    }


# ★커맨드센터(옵션 그레인 조망)는 **Wing 축(3P/2P)**이다 — 1P 로켓배송은 별도 화면
#   (rocket-overview)이 PO 그레인으로 다룬다. 그런데 오하이테크는 **같은 vendor_id로 1P와
#   3P를 함께** 가지므로, vendor_id만으로 거르면 1P 광고비가 3P 매출 옆에 붙는다.
#   2026-08-03 실측: 옵션 광고비 적재 직후 COUPANG_WING2 뷰가 매출 160,500(3P)에 광고비
#   5,450,601(1P)을 얹어 net_profit −5,382,780으로 뒤집혔다. 사과와 오렌지를 더한 값이다.
#   → 판매방식으로도 잘라야 계정 분리가 완결된다.
#   ★2026-08-13: 이 축의 정의를 `ad_sell_type.py`로 옮겼다 — 같은 결함이 운영 패널
#   (`coupang_ops.sales_summary`)에 그대로 남아 있었기 때문이다(리터럴이 여기에만 있어서
#   그쪽 쿼리가 참조할 곳이 없었다). 이름은 호출부 호환을 위해 유지한다.
_WING_SELL_TYPES = ORDER_BASED_SELL_TYPES


def _agg_ads(db: Session, dfrom: date, dto: date,
             vendor_id: str | None = None,
             sell_types: tuple[str, ...] | None = _WING_SELL_TYPES) -> dict[str, dict]:
    """광고 옵션 일별을 옵션ID별 집계. D-9: 비용·노출·클릭은 ad_option_id 귀속,
    매출·주문은 conv_option_id 귀속(간접전환 대비 분리). 같은 옵션이면 한 행에 합쳐짐.

    S1: vendor_id 주면 해당 계정 광고만(계정 분리). None이면 전체(기존 동작 불변).
    ★sell_types: 기본 (3P,2P) = Wing 축. None이면 전 판매방식(호출자가 의도적으로 열 때만).
      Retail(1P)을 기본에서 빼는 이유는 위 주석 참조 — 같은 vendor에 축이 두 개다."""
    out: dict[str, dict] = {}

    def _row(vid: str) -> dict:
        return out.setdefault(
            str(vid),
            {"spend": _Z, "impressions": 0, "clicks": 0,
             "conv_revenue": _Z, "ad_orders": 0, "ad_qty": 0},
        )

    cost_q = (
        db.query(
            CoupangAdOptionDaily.ad_option_id,
            func.sum(CoupangAdOptionDaily.ad_spend),
            func.sum(CoupangAdOptionDaily.impressions),
            func.sum(CoupangAdOptionDaily.clicks),
        )
        .filter(CoupangAdOptionDaily.report_date >= dfrom,
                CoupangAdOptionDaily.report_date <= dto)
    )
    if vendor_id is not None:
        cost_q = cost_q.filter(CoupangAdOptionDaily.vendor_id == vendor_id)
    if sell_types is not None:
        cost_q = cost_q.filter(CoupangAdOptionDaily.sell_type.in_(sell_types))
    cost_rows = cost_q.group_by(CoupangAdOptionDaily.ad_option_id).all()
    for vid, spend, imp, clk in cost_rows:
        b = _row(vid)
        b["spend"] += _f(spend)
        b["impressions"] += int(imp or 0)
        b["clicks"] += int(clk or 0)

    conv_q = (
        db.query(
            CoupangAdOptionDaily.conv_option_id,
            func.sum(CoupangAdOptionDaily.conversion_revenue),
            func.sum(CoupangAdOptionDaily.orders),
            func.sum(CoupangAdOptionDaily.sales_qty),
        )
        .filter(CoupangAdOptionDaily.report_date >= dfrom,
                CoupangAdOptionDaily.report_date <= dto)
    )
    if vendor_id is not None:
        conv_q = conv_q.filter(CoupangAdOptionDaily.vendor_id == vendor_id)
    if sell_types is not None:
        conv_q = conv_q.filter(CoupangAdOptionDaily.sell_type.in_(sell_types))
    conv_rows = conv_q.group_by(CoupangAdOptionDaily.conv_option_id).all()
    for vid, conv_rev, ords, qty in conv_rows:
        b = _row(vid)
        b["conv_revenue"] += _f(conv_rev)
        b["ad_orders"] += int(ords or 0)
        b["ad_qty"] += int(qty or 0)
    return out


def _agg_returns(db: Session, dfrom: date, dto: date,
                account_key: str | None = None,
                channel_ids: list[int] | None = None) -> dict[str, dict]:
    """반품/취소를 옵션ID별 집계. withdrawn=False(철회 제외)만. 사실=반품 수량·건수.

    S1: account_key 주면 해당 계정만(계정 분리, account_key 컬럼 직접 필터). None이면 전체(불변).
    S6(D-12): channel_ids 주면 그 채널 도메인에서 status로 매출제외된 주문라인의 반품행은 제외
    (reconcile-by-absence와 return_deduction 이중차감 방지). _agg_orders와 동일 도메인으로 대칭."""
    start = datetime.combine(dfrom, time.min)
    end = datetime.combine(dto, time.max)
    q = (
        db.query(
            CoupangReturnItem.vendor_item_id,
            func.sum(CoupangReturnItem.cancel_count),
            func.count(CoupangReturnItem.id),
            func.max(CoupangReturnItem.vendor_item_name),
        )
        .filter(
            CoupangReturnItem.withdrawn.is_(False),
            CoupangReturnItem.requested_at >= start,
            CoupangReturnItem.requested_at <= end,
        )
    )
    if account_key is not None:
        q = q.filter(CoupangReturnItem.account_key == account_key)
    # S6 상호배타(D-12, 머니룰): status가 매출제외(REVENUE_EXCLUDED)된 주문라인의 반품행은 뺀다.
    # reconcile-by-absence가 '전체취소' 주문을 status=cancelled로 매출에서 제외(권위)하므로,
    # return_deduction(=unit_price×return_qty)이 또 차감하면 같은 취소를 2번 빼는 이중계상이 된다
    # (라이브 확정 2026-06-14: 사라진 주문 전부 반품테이블 존재). '부분반품'은 주문이 쿠팡
    # ordersheets에서 안 사라져 status 활성으로 남으므로 제외 대상이 아니라 여기서 계속 담당한다.
    # 불변식: return_deduction은 '매출에 잡힌(status 활성) 주문'의 반품에만 적용된다.
    # 상관(codex P1/P2): _agg_orders의 도메인 규칙을 그대로 미러해, '같은 도메인'에서 같은 주문·라인이
    # 매출제외(REVENUE_EXCLUDED)된 경우에만 억제 → 매출제외↔반품차감이 대칭(이중차감 0, fail-open 0).
    #   - Channel.platform=='coupang' (+ channel_ids is not None이면 channel_id IN channel_ids):
    #     _agg_orders와 동일 채널 범위. None=전체뷰는 전체 쿠팡(등가성: 계정합==전체).
    #     (Channel.code==account_key는 company 다채널 도메인보다 좁아 RG가 orders 편입 시 비대칭 →
    #      이중차감 재발 위험. codex R2 권고대로 _agg_orders 도메인으로 대칭화.)
    #   - platform_product_id == vendor_item_id: 옵션ID 라인 단위(반품 grain과 정렬). vid 전역 UNIQUE.
    # ★D-CPP-33(2026-08-10): 위 불변식을 «양쪽 다» 강제한다.
    #   종전 구현은 「주문이 있고 + 매출제외됨」만 걸러냈다(부정 조건). 그래서 «주문이 아예 없는»
    #   반품행은 그대로 통과해 매출로 센 적 없는 주문의 원가·매출을 빼고 있었다 — 유령 차감이다.
    #   라이브 실측(90일, 3P 2계정): 반품 53건 중 **34건(64%)이 고아**(원주문이 orders에 없음.
    #   WING1 61% · WING2 68%). 7월 WING2는 6건 중 5건이 고아라 return_deduction 38,800원이
    #   **전액 유령**이었다. 나머지 1건은 status=cancelled라 어차피 억제된다.
    #   고아가 생기는 이유: 쿠팡 주문 API가 취소분을 안 주므로 «수집 전에 취소된 주문»은 orders에
    #   영영 안 들어온다. 그런 주문은 매출에 없으니 뺄 것도 없다.
    #   → 부정 조건(~excluded)을 **긍정 불변식(매출에 잡힌 주문이 존재)**으로 바꾼다.
    #     이 한 줄이 두 경우를 모두 덮는다: 고아(매칭 0건) · 매출제외(status 제외라 미매칭).
    counted_q = (
        db.query(Order.id)
        .join(Channel, Order.channel_id == Channel.id)
        .filter(
            Channel.platform == "coupang",
            Order.order_number == CoupangReturnItem.order_id,
            Order.platform_product_id == CoupangReturnItem.vendor_item_id,
            Order.status.notin_(tuple(REVENUE_EXCLUDED)),
        )
    )
    if channel_ids is not None:
        counted_q = counted_q.filter(Order.channel_id.in_(channel_ids))

    # ★★적대 리뷰 P1-1 수용(2026-08-10): 억제를 여기서 «통째로» 걸면 사실 축까지 죽는다.
    #   이 SA는 돈(return_deduction)만이 아니라 **사실**(return_qty·receipt_count·return_rate)의
    #   유일한 원천이다. 첫 구현은 억제된 행을 집계에서 아예 빼서, 라이브 90일 반품 53건·56개가
    #   있는데도 화면이 「반품 0건 · 반품률 0%」라고 말했다 — 화면이 사실을 두고 거짓말한 것이다.
    #   → 사실은 «전부» 세고(억제 없음), 차감에 쓸 수량만 따로 센다(deductible_qty).
    #   호출부는 return_deduction·net_qty엔 deductible_qty를, 표시엔 return_qty를 쓴다.
    fact_rows = q.group_by(CoupangReturnItem.vendor_item_id).all()
    ded_rows = (
        q.filter(counted_q.exists())
        .group_by(CoupangReturnItem.vendor_item_id)
        .all()
    )
    deductible = {str(vid): int(cancel or 0) for vid, cancel, _cnt, _name in ded_rows}
    return {
        str(vid): {
            "return_qty": int(cancel or 0),          # 사실 — 억제 없음
            "receipt_count": int(cnt or 0),          # 사실 — 억제 없음
            "deductible_qty": deductible.get(str(vid), 0),  # 돈 — 매출에 잡힌 주문의 반품만
            "name": name,
        }
        for vid, cancel, cnt, name in fact_rows
    }


def _agg_fees(db: Session, dfrom: date, dto: date,
             account_key: str | None = None) -> dict[str, dict]:
    """매출내역(실측수수료)을 옵션ID별 집계. recognition_date 기준. SALE/REFUND 모두 포함
    (service_fee·sale_amount는 REFUND가 음수로 저장 — 사실 그대로 합산, D-3).

    S1: account_key 주면 해당 계정만(계정 분리, account_key 컬럼 직접 필터). None이면 전체(불변)."""
    q = (
        db.query(
            CoupangRevenueFee.vendor_item_id,
            func.sum(CoupangRevenueFee.service_fee),
            func.sum(CoupangRevenueFee.service_fee_vat),
            func.sum(CoupangRevenueFee.sale_amount),
            func.count(CoupangRevenueFee.id),
            func.max(CoupangRevenueFee.vendor_item_name),
        )
        .filter(CoupangRevenueFee.recognition_date >= dfrom,
                CoupangRevenueFee.recognition_date <= dto)
    )
    if account_key is not None:
        q = q.filter(CoupangRevenueFee.account_key == account_key)
    rows = q.group_by(CoupangRevenueFee.vendor_item_id).all()
    # codex[P2]: 쿠팡 정산은 service_fee + service_fee_vat 둘 다 차감 → total_fee로 합산.
    return {
        str(vid): {
            "service_fee": _f(fee),
            "service_fee_vat": _f(vat),
            "total_fee": _f(fee) + _f(vat),
            "settled_sale_amount": _f(sale),
            "fee_rows": int(cnt or 0),
            "name": name,
        }
        for vid, fee, vat, sale, cnt, name in rows
    }


def _agg_rg_settlement_fees(db: Session, dfrom: date, dto: date,
                            account_key: str | None = None,
                            grain: str = "account") -> dict[str, dict]:
    """RG 정산 수수료를 집계. D-6/D-7: 대조(reconciliation) 뷰용. D3(S3 트랙): grain 파라미터화.

    S1: account_key 주면 해당 계정만(계정 분리). None이면 전체(기존 동작 불변).

    날짜 필터: recognition_date_from과 recognition_date_to가 [dfrom, dto]와 겹치는 행.
    겹침 조건 = recognition_date_from <= dto AND recognition_date_to >= dfrom.

    grain="account"(기본값, 기존 동작 불변 — IRON RULE 회귀테스트 대상):
      계정 단위 대조뷰. status/api 계정 row(sentinel vendor_item_id='', VAT後)만 집계.
      반환: {account_key: {fee_type: amount, ..., "total": Decimal}}
      ★S6 옵션 row(vendor_item_id=옵션ID, VAT前 A-B)는 같은 (account,기간,fee_type)에 공존하므로
        필터하지 않으면 VAT後+VAT前이 합산돼 비용이 과대계상된다(codex S1 P1).

    grain="option"(신규, S3 트랙 D3/D-9): 옵션 단위. S6 옵션 row(vendor_item_id≠'', VAT前 A-B)만 집계.
      반환: {account_key: {vendor_item_id: {fee_type: amount, ..., "total": Decimal}}}
      ★VAT前 값이므로 계정 row(VAT後)와 직접 비교 불가 — 호출부가 gross-up 후 대조(모델 §8-1,
        Σ옵션(VAT前)+요약세액 == 계정 row(VAT後)). 이 함수는 원값(VAT前)만 반환, gross-up은 호출부 책임.
    """
    if grain not in ("account", "option"):
        raise ValueError(f"_agg_rg_settlement_fees: unknown grain {grain!r}")
    is_option = grain == "option"
    vendor_item_filter = (
        CoupangRgSettlementFee.vendor_item_id != ""
        if is_option
        else CoupangRgSettlementFee.vendor_item_id == ""
    )
    group_cols = [CoupangRgSettlementFee.account_key, CoupangRgSettlementFee.fee_type]
    if is_option:
        group_cols.insert(1, CoupangRgSettlementFee.vendor_item_id)
    rows = (
        db.query(
            CoupangRgSettlementFee.account_key,
            *([CoupangRgSettlementFee.vendor_item_id] if is_option else []),
            CoupangRgSettlementFee.fee_type,
            func.sum(CoupangRgSettlementFee.amount),
        )
        .filter(
            CoupangRgSettlementFee.recognition_date_from <= dto,
            CoupangRgSettlementFee.recognition_date_to >= dfrom,
            vendor_item_filter,
            *([CoupangRgSettlementFee.account_key == account_key]
              if account_key is not None else []),
        )
        .group_by(*group_cols)
        .all()
    )
    result: dict[str, dict] = {}
    if is_option:
        for acct, vid, fee_type, amount in rows:
            acct_entry = result.setdefault(acct, {})
            opt_entry = acct_entry.setdefault(vid, {"total": _Z})
            opt_entry[fee_type] = _f(amount)
            opt_entry["total"] = opt_entry["total"] + _f(amount)
        return result
    for acct, fee_type, amount in rows:
        entry = result.setdefault(acct, {"total": _Z})
        entry[fee_type] = _f(amount)
        entry["total"] = entry["total"] + _f(amount)
    return result


# ──────────────────────────────────────────────
# D-16 광고비 처리 규칙 (S7 /browse 라이브 확정 2026-06-09, D-11/D-15 개정)
# ──────────────────────────────────────────────
# ★D-16(S7): RG 광고비는 RG 정산(totalAdSalesDeductionAmount)에만 있고, 업로드되는 광고 XLSX
# (CoupangAdOptionDaily, 광고센터 PA 보고서 pa_daily)에는 안 잡힌다 — 광고센터는 마켓플레이스(3P/윙)
# 검색·디스플레이 광고 플랫폼이고 RG 광고는 별개 청구(prod 광고 XLSX 2P 0행 라이브 실증). 따라서
# net_profit 플립은 **광고 제외** 정산액을 차감한다(D-CPP-43). settlement ad_sales는 표시·검산용
# — 광고센터 PA의 «공제»라 PA(ad_spend)에서 이미 빠졌다.
# 아래 RG(2P) 합산 헬퍼는 광고센터에서 RG상품 검색광고를 돌릴 경우의 미래 겹침 감시용일 뿐(현재 0).
# sell_type 코드: 3P=윙·2P=RG·Retail=로켓배송(ad_costs 확정).
RG_AD_SELL_TYPE = AD_SELL_TYPE_2P  # 광고 XLSX 판매방식 코드: 2P=로켓그로스(RG)


def rg_ad_spend_to_exclude(ad_rows) -> Decimal:
    """순수규칙: (sell_type, ad_spend) 행들에서 RG(2P) 광고비 합을 반환.

    ★D-16(S7): 광고 XLSX에는 RG 광고가 없으므로(광고센터 PA 보고서 미포함, 라이브 확정) 이 값은
    현재 0이며 net_profit 차감엔 안 쓴다 — 미래에 광고센터에서 RG상품 검색광고를 돌려 PA 2P가
    생길 경우의 겹침 감시·자릿수 대조용 보조다.
    순수 함수라 DB 없이 fixture 테스트 가능(D-12). DB 래퍼는 _agg_rg_ad_overlap.
    sell_type은 ad_costs 적재 시 이미 strip되지만(ad_costs.py), 순수함수 계약 견고성 위해 한 번 더 정규화.
    """
    return sum(
        (_f(spend) for sell_type, spend in ad_rows
         if (sell_type or "").strip() == RG_AD_SELL_TYPE),
        _Z,
    )


def _rg_account_breakdown(account_key: str, v: dict) -> dict:
    """RG 정산 계정별 대조 카드 브레이크다운(D-6/D-7). 라인합+other == total 보장.

    풀필먼트(J)=배송(delivery)+입출고(warehousing)+보관(storage) (D-10 라이브 확정).
    reconcile guard(Codex S5 지적1): 표시 컴포넌트 합과 DB total 차이를 'other'로 노출 —
    legacy 'fulfillment'·미지/미래 fee_type이 있어도 silent drop·중복 없이 가시화. 정상=0.
    """
    sale_fee = v.get("sale_fee", _Z)
    fulfillment = v.get("delivery", _Z) + v.get("warehousing", _Z) + v.get("storage", _Z)
    return_fee = v.get("return_shipping", _Z) + v.get("return_handling", _Z)
    ad_sales = v.get("ad_sales", _Z)
    total = v.get("total", _Z)
    other = total - (sale_fee + fulfillment + return_fee + ad_sales)
    if other != _Z:
        log.warning("RG 정산 %s: 미매핑 fee_type 잔액 %s (legacy/스키마 변동 의심)", account_key, other)
    return {
        "account_key": account_key,
        "total": total,
        "sale_fee": sale_fee,
        "fulfillment": fulfillment,
        "delivery": v.get("delivery", _Z),
        "warehousing": v.get("warehousing", _Z),
        "storage": v.get("storage", _Z),
        "return_fee": return_fee,
        "ad_sales": ad_sales,          # D-11: 광고비(d), 표시만(중복주의)
        "other": other,                # reconcile 잔액(정상=0)
    }


def apply_rg_net_profit_flip(
    net_profit_pre_rg: Decimal, rg_deducted: Decimal
) -> Decimal:
    """종합조망 순이익에 RG 정산 비용을 반영(플립). **광고비를 뺀** 정산액을 차감한다.

    공식: net_profit_new = net_profit_pre_rg − rg_deducted (호출부가 rg_total − ad_sales를 넘긴다).

    ★D-CPP-43(2026-08-12) — **D-16 폐기**, 구 D-15의 「광고 제외 차감」으로 복귀.
      D-16은 *"RG 광고비는 광고센터 PA 보고서에 안 잡히고 RG 정산에만 존재"*를 근거로 광고 포함
      전액을 차감했다. 그 근거는 `sell_type=2P` 0행 실증이었는데 **라벨이 판매경로를 뜻하지
      않는다**(오픽스 PA 광고비의 97.28%가 RG로 팔리는 옵션에 쓰이고, 광고비 상위 5개 옵션의
      3P 주문은 0건 — ref 56 §3).

      **1차 출처로 확정**(2026-08-12, 윙 > 정산 > 로켓그로스 정산현황 > 「광고비 내역」
      `/tenants/rfm-portal-2/cmg/settlement`):
        · 화면 안내문 — *"로켓그로스 상품의 광고비는 해당 월 말에 계산되어 매입세금계산서 1건이
          발행되며 … 정산시 지급액보다 공제할 금액(광고비)이 큰 경우 공제하지 못한 남은 광고비는
          다음 정산으로 이월됩니다."*
        · 상세내역의 **광고유형이 전부 `PA`**이고 **캠페인 이름이 광고센터 캠페인과 그대로 일치**
          (AI스마트광고 · [매.최] 골프필름 · [매.최] 카드케이스 · [매.최] 사생활 지문방지필름 ·
           [매.최] 플립, 폴드 지문방지 내부+사생활외부 · 〃_8 시리즈).
      → 정산의 `ad_sales`는 별개 비용이 아니라 **광고센터 PA 광고비를 정산에서 «공제»**하는 것이다.
        PA는 이미 `ad_spend`로 차감되므로 여기서 또 빼면 **이중계상**이다.
      → 「이월」 규칙이 주간비 0.44~1.98 요동과 「PA 있는데 정산 0인 주」를 설명한다(ref 56 §6-B).

      ★이 결론은 새 발견이 아니라 **복원**이다 — ref 04 §2가 이미 *"RG 정산: 최종 판매가 −
        판매수수료 − 추가비용(광고비 등) − RG 서비스이용비 후 지급"*이라 적었고, ref 17 §7도
        *"우리 ad_costs와 이중계상 주의"*라고 경고했다. D-16이 라벨 증거로 그 둘을 덮었다.

    ★부호: 음수 환급주기(rg_deducted<0)도 그대로 가산되어 환급이 정확히 반영된다.
    ★순수 함수(D-12). **무엇을 넘길지는 호출부가 정한다** — 이 함수는 「넘어온 것을 뺀다」만 한다.
    """
    return net_profit_pre_rg - rg_deducted


def _agg_rg_ad_overlap(db: Session, dfrom: date, dto: date,
                       vendor_id: str | None = None) -> Decimal:
    """기간 내 광고비 XLSX의 RG(2P) 광고비 합 — 미래 이중계상 겹침 감시용.

    ★D-16(S7): RG 광고는 광고센터 PA 보고서(이 XLSX)에 안 잡히고 RG 정산에만 있어(라이브 실증)
    ★D-CPP-43 이후 이 감시는 무력하다(2P 라벨을 보는데 실제 RG 광고는 3P로 실린다). 이 값이 0이라는
    것을 «이중계상 없음»의 근거로 쓰지 말 것. 필드는 하위호환으로만 남긴다. 이 값은 정상=0 — PA 2P가
    생기면 정산 ad_sales와 겹쳐 이중계상 위험이 되므로 그 신호를 감시한다(0이 아니면 호출부에서 경고).

    S1: vendor_id 주면 해당 계정만(계정 분리). None이면 전체(기존 동작 불변).
    """
    # func.trim: 적재 시 이미 strip되지만(ad_costs.py), DB 레벨에서도 공백 방어(Codex S5 지적4a).
    q = (
        db.query(CoupangAdOptionDaily.sell_type, CoupangAdOptionDaily.ad_spend)
        .filter(
            CoupangAdOptionDaily.report_date >= dfrom,
            CoupangAdOptionDaily.report_date <= dto,
            func.trim(CoupangAdOptionDaily.sell_type) == RG_AD_SELL_TYPE,
        )
    )
    if vendor_id is not None:
        q = q.filter(CoupangAdOptionDaily.vendor_id == vendor_id)
    return rg_ad_spend_to_exclude(q.all())


def _product_master(db: Session, account_key: str | None = None) -> dict[str, dict]:
    """상품 옵션 마스터 — 이름·가격·등록수수료율·재고·원가성 공급가. 조망 베이스.

    S1: account_key 주면 해당 계정 옵션만(계정 분리 — all_vids 합집합이 계정으로 한정됨).
    None이면 전체(기존 동작 불변)."""
    out: dict[str, dict] = {}
    q = db.query(CoupangProductItem)
    if account_key is not None:
        q = q.filter(CoupangProductItem.account_key == account_key)
    for p in q.all():
        out[str(p.vendor_item_id)] = {
            "name": p.item_name or p.seller_product_name,
            "sale_price": _f(p.sale_price),
            "supply_price": p.supply_price,  # None 가능 — 원가 미설정
            "sale_agent_commission": p.sale_agent_commission,
            "stock": p.amount_in_stock if p.amount_in_stock is not None else p.max_buy_count,
            "on_sale": p.on_sale,
            "status_name": p.status_name,
            "brand": p.brand,
            "account_key": p.account_key,
            "vendor_id": p.vendor_id,
        }
    return out


def _cost_master(db: Session) -> dict[str, dict]:
    """옵션ID(vendor_item_id) → 내부 product_master(원가·정식 상품명) 다리. (트랙 D-12)

    원가는 coupang_product_item.supply_price(실거래 0.6% 커버)가 아니라 내부
    product_master.cost_price(89% 보유)에 product_channel_mapping(coupang, is_active)으로
    닿는다 — 실거래 66% 커버. profit_calculator._get_option_id_map과 **동일 경로**(is_active
    매핑)라 기존 회계엔진과 원가 원천이 일치한다. (라이브 진단: 옵션ID당 원가충돌 0건 확인)
    """
    products = {p.id: p for p in db.query(ProductMaster).all()}
    rows = (
        db.query(
            ProductChannelMapping.channel_product_id,
            ProductChannelMapping.product_id,
        )
        .join(Channel, ProductChannelMapping.channel_id == Channel.id)
        .filter(
            Channel.platform == "coupang",
            ProductChannelMapping.is_active.is_(True),
        )
        .all()
    )
    # 옵션ID별 후보 수집 — 같은 옵션ID가 WING+RG 두 채널에 매핑되거나(같은 product) 재지정
    # 이력으로 중복될 수 있다. 라이브 진단상 현재 원가충돌 0건이나, codex[P2] 견고성 지적
    # 수용: 첫 행 임의채택 금지하고 결정적으로 고른다.
    candidates: dict[str, list] = {}
    for cpid, pid in rows:
        pm = products.get(pid)
        if pm is not None:
            candidates.setdefault(str(cpid), []).append(pm)

    out: dict[str, dict] = {}
    for cpid, pms in candidates.items():
        # 원가>0 보유 행 우선(0/None이 유효원가 가리지 않게). 동률이면 product_id 최소(결정적).
        costed = [pm for pm in pms if pm.cost_price and pm.cost_price > 0]
        chosen = min(costed or pms, key=lambda pm: pm.id)
        distinct = {pm.cost_price for pm in costed}
        if len(distinct) > 1:  # 서로 다른 실원가 충돌 — 임의판단 금지, 사실 경고(D-3)
            log.warning(
                "옵션ID %s 활성 쿠팡매핑이 서로 다른 원가 %s 보유 — product_id 최소(%s) 선택",
                cpid, sorted(map(str, distinct)), chosen.id,
            )
        out[cpid] = {"cost_price": chosen.cost_price, "name": chosen.product_name}
    return out


# ──────────────────────────────────────────────
# 결합 엔진 — 5소스 병합 + 3축 파생
# ──────────────────────────────────────────────
def compute_command_center(db: Session, dfrom: date, dto: date,
                           account: str | None = None) -> dict:
    """옵션ID 합집합을 키로 5소스를 병합해 3축(회계·광고·상품)을 파생.

    Decimal은 그대로 반환(라우터에서 str 직렬화). D-3: 사실/지표만, 추천 없음.
    순이익 원가는 내부 product_master.cost_price 우선(D-12), 없으면 coupang supply_price 폴백.
    둘 다 없으면 미반영(has_cost=False)으로 표기.

    S1(트랙 reconciliation D-4): account(COUPANG_WING1=오픽스/COUPANG_WING2=오하이테크) 주면
    해당 계정만 집계(계정 분리 뷰). None이면 전체 합산(기존 동작 불변, 등가성 계약).
    account를 _resolve_account로 소스별 필터(channel_ids·vendor_id·account_key·vendor_item_ids)로
    풀어 각 집계 SA에 optional 주입한다(원칙18-6 허브). 매출은 현재 Wing 3P만 — RG 매출은 S3 편입.
    """
    acc = _resolve_account(db, account)
    master = _product_master(db, acc["account_key"])
    cost_master = _cost_master(db)  # D-12: 내부 원가·정식상품명 다리(vid 키 룩업 — all_vids에 안 들어가 계정필터 불필요)
    orders = _agg_orders(db, dfrom, dto, acc["channel_ids"])
    # S3(D-2): RG 매출 편입 — 옵션ID로 orders에 병합. summary·by_option 모두 RG 포함.
    # ★D-CPP-49(계약 ⓑ): 원천을 gross 주문 원장 → **콘솔 net 옵션축**으로 바꿨다.
    #   왜: 대시보드 RG 행(`rg_channel_pnl`)은 이미 콘솔 net 위에 서 있는데 여기만 gross라
    #   **같은 화면이 RG 매출을 두 값으로 말했다**(오픽스 30일 기준 gross가 +11.8%·+694,070원
    #   과대 — ref 89). 쿠팡 RG 주문 API엔 취소·상태 축이 없어 gross에서 net을 뺄 길이 원리적으로
    #   없으므로, net을 알려면 net을 주는 표면을 읽어야 한다.
    #   ★대시보드는 요약축·여기는 옵션축(옵션 grain이 필요하다)이라 축이 갈리는데, 두 축의 등가는
    #     prod 전 구간 실측으로 확인됐다 — WING1 76일·WING2 71일 **147 계정-일 전건**에서
    #     금액·수량 불일치 0, 한쪽에만 있는 날 0(2026-08-22 15:47 KST).
    rg_orders = net_revenue_by_option(db, dfrom, dto, acc["account_key"])
    rg_revenue = _merge_rg_orders(orders, rg_orders)
    # ★커버리지를 화면이 실토하게 한다 — 옵션축은 페처 `vi_days` 롤링이라 창 앞쪽이 빌 수 있고,
    #   빈 날의 RG 매출은 «0원»이 아니라 «미상»이다. 이걸 안 실으면 「그날 RG가 0이었다」로 읽힌다
    #   (교훈 #123 — 「발견 0건」과 「실행 안 됨」이 같은 숫자로 보이면 안 된다).
    #   그리고 당일(오늘)은 콘솔이 D+1이라 «항상» 비어 있다 — 대시보드도 같다(축이 같아 갈리지 않는다).
    rg_axis = _rg_axis_coverage(db, dfrom, dto, acc["account_key"])
    # 참고 지표: 같은 창의 gross 주문 원장 합. **매출이 아니다** — 우리 수집과 콘솔의 간극을
    # 재는 진단값이고, 이 값이 없으면 net 전환 뒤 드리프트 대조가 통째로 «자기 자신과의 비교»가 된다.
    rg_gross_revenue = sum(
        (o["revenue"] for o in _agg_rg_orders(db, dfrom, dto, acc["account_key"]).values()), _Z
    )
    ads = _agg_ads(db, dfrom, dto, acc["vendor_id"])
    returns = _agg_returns(db, dfrom, dto, acc["account_key"], acc["channel_ids"])
    fees = _agg_fees(db, dfrom, dto, acc["account_key"])
    rg_fees = _agg_rg_settlement_fees(db, dfrom, dto, acc["account_key"])  # D-6/D-7: 대조 뷰용
    # D-CPP-32: 수수료를 «정산 인식일 축의 실측 금액»이 아니라 «주문 축의 요율 계산»으로 바꾼다.
    #   왜: _agg_fees는 recognition_date 창이라 매출(주문일 창)과 **다른 주문**을 가리켰고, 정산이
    #   D+9~10 지연되므로 최근 주문은 수수료가 통째로 0원으로 잡혔다(라이브 2026-08-10: WING2 30일
    #   49라인 중 25라인·450,700원이 수수료 0원 → 순이익 약 29,000원 과대).
    #   요율만 알면 금액은 결정된다(service_fee = sale_amount×ratio, vat = fee×0.1 — 661건 전수 성립).
    fee_rates = option_fee_rates(db, [acc["account_key"]] if acc["account_key"] else None)
    # account=None(전체 합산)이면 계정을 특정할 수 없다. vendor_item_id는 전역유일·단일계정 소유(D-8)라
    # vid만으로 인덱싱해도 교차 오염이 없다.
    fee_rate_by_vid: dict[str, Decimal] = {}
    for (_ak, _vid), _rate in fee_rates.items():
        _prev = fee_rate_by_vid.get(_vid)
        if _prev is not None and _prev != _rate:
            # D-8(vendor_item_id 전역유일)이 깨진 신호. prod 실측 0건이지만 조용히 마지막 것을
            # 취하면 계정에 따라 수수료가 달라지고 아무도 모른다.
            log.warning(
                "요율 룩업 충돌: vendor_item_id %s가 계정별로 다른 요율(%s vs %s) — D-8 전역유일 위반 가능",
                _vid, _prev, _rate,
            )
        fee_rate_by_vid[_vid] = _rate
    # ★수수료 과세표준은 «3P(WING1/2) 주문 매출»뿐이다 — orders(=_agg_orders)는 쿠팡 전 채널이라
    #   그대로 곱하면 두 곳에서 이중계상된다:
    #     ① 1P(COUPANG_ROCKET): 쿠팡이 사입해 파는 것이라 우리에게 판매수수료가 없다.
    #     ② RG(로켓그로스): 수수료가 RG 정산에 통째로 들어오고 rg_total로 전액차감된다(D-16).
    #   그래서 매출에서 빼는 방식이 아니라 3P 채널만 다시 집계해 «따로» 만든다(뺄셈은 축이 어긋난다).
    _3p_channel_ids = [
        cid for (cid,) in db.query(Channel.id).filter(Channel.code.in_(COUPANG_3P_CODES)).all()
    ]
    if acc["channel_ids"] is not None:
        _allowed = set(acc["channel_ids"])
        _3p_channel_ids = [c for c in _3p_channel_ids if c in _allowed]
    orders_3p = _agg_orders(db, dfrom, dto, _3p_channel_ids)

    all_vids = set(master) | set(orders) | set(ads) | set(returns) | set(fees)

    account_rows: list[dict] = []
    ad_rows: list[dict] = []
    product_rows: list[dict] = []
    # S4(D-11~D-13): 정산화 보정의 반품차감 되돌림이 파이프라인과 일치하도록 vid별 단가 수집
    # (line 단가 = o.unit_price 우선, 없으면 master sale_price 폴백 — return_deduction과 동일 소스).
    unit_price_by_vid: dict[str, Decimal] = {}

    for vid in all_vids:
        m = master.get(vid, {})
        cm = cost_master.get(vid, {})  # D-12: 내부 원가·정식상품명
        o = orders.get(vid, {})
        a = ads.get(vid, {})
        r = returns.get(vid, {})
        f = fees.get(vid, {})
        # 이름 폴백: 쿠팡옵션명 → 내부 정식상품명 → 주문 → 매출내역 → 반품 (화면 유지)
        name = (
            m.get("name") or cm.get("name") or o.get("name") or f.get("name")
            or r.get("name") or "(이름 미상)"
        )

        revenue = o.get("revenue", _Z)
        order_qty = o.get("qty", 0)
        unit_price = o.get("unit_price", _Z) or m.get("sale_price", _Z)
        return_qty = r.get("return_qty", 0)                 # 사실(표시용) — 고아·매출제외 포함
        deductible_qty = r.get("deductible_qty", 0)         # 돈(차감용) — 매출에 잡힌 것만
        return_deduction = unit_price * deductible_qty  # 추정(평균단가×차감대상수량)
        unit_price_by_vid[vid] = unit_price  # S4: 정산화 보정의 반품 되돌림용(동일 단가)
        # ── 수수료: 순매출 × 그 옵션의 요율 × 1.1 (D-CPP-32) ──
        # 순매출인 이유: 반품분은 쿠팡이 수수료도 환급한다(정산에 REFUND 음수 행). 총매출에 곱하면
        # 반품된 건의 수수료를 우리만 계속 무는 셈이 된다.
        known_rate = fee_rate_by_vid.get(vid)
        fee_rate = known_rate if known_rate is not None else DEFAULT_FEE_RATE
        fee_basis = BASIS_SETTLED if known_rate is not None else BASIS_DEFAULT
        # 과세표준 = 3P 매출 − 반품차감(반품은 CoupangReturnItem = 3P 전용이라 축이 맞다).
        # 반품차감이 3P 매출보다 클 수 있다(단가가 1P·RG 섞인 평균이라 — return_deduction 자체의
        # 한계, 엔진 단일화 작업의 몫). 음수 수수료를 만들지 않도록 0에서 끊는다.
        fee_base = orders_3p.get(vid, {}).get("revenue", _Z) - return_deduction
        fee_base_clamped = False
        if fee_base > _Z:
            # VAT 배수는 공유 상수를 쓴다 — 두 엔진 통일의 구조적 근거가 이 상수 하나다.
            service_fee = (fee_base * fee_rate).quantize(_Q2, ROUND_HALF_UP)
            service_fee_vat = (service_fee * (FEE_VAT_MULT - Decimal("1"))).quantize(_Q2, ROUND_HALF_UP)
        else:
            # 반품차감이 3P매출을 넘어 과세표준이 음수가 된 라인. 원인은 축 불일치다 —
            # _agg_returns는 requested_at 창, orders_3p는 order_date 창이라 창 밖 주문의 반품이
            # 창 안 매출을 깎는다. 0에서 끊되 «몇 건이 끊겼는지»를 summary로 올린다(침묵 금지).
            fee_base_clamped = fee_base < _Z
            fee_base = _Z
            service_fee = _Z
            service_fee_vat = _Z
        total_fee = service_fee + service_fee_vat
        # 실측(정산 인식일 축) — net_profit에는 쓰지 않고 대조·신선도 표면용으로만 남긴다.
        settled_fee = f.get("total_fee", _Z)
        settled_rows = f.get("fee_rows", 0)
        ad_spend = a.get("spend", _Z)

        # 원가 — D-12: 내부 product_master.cost_price 우선, 없으면 coupang supply_price 폴백.
        # 단가는 순판매수량(주문−반품)에 적용. 0/None은 원가정보 없음으로 간주(미반영).
        internal_cost = cm.get("cost_price")
        supply = m.get("supply_price")
        # 원가도 «돈 축»을 따른다 — 고아 반품은 그 주문 수량이 order_qty에 애초에 없으므로
        # 빼면 없는 수량을 두 번 빼는 셈이 된다.
        net_qty = order_qty - deductible_qty
        if internal_cost is not None and internal_cost > 0:
            unit_cost, cost_source = _f(internal_cost), "internal"
        elif supply is not None and supply > 0:
            unit_cost, cost_source = _f(supply), "coupang_supply"
        else:
            unit_cost, cost_source = None, None
        if unit_cost is not None:
            cost = unit_cost * net_qty
            has_cost = True
        else:
            cost = _Z
            has_cost = False

        net_profit = revenue - return_deduction - total_fee - ad_spend - cost

        account_rows.append({
            "vendor_item_id": vid, "name": name,
            "revenue": revenue, "return_deduction": return_deduction,
            "service_fee": service_fee, "service_fee_vat": service_fee_vat,
            "total_fee": total_fee, "ad_spend": ad_spend,
            # D-CPP-32: 값과 «근거 등급»을 같이 싣는다. 등급을 안 실으면 화면이 실토할 수가 없다.
            "fee_rate": fee_rate, "fee_basis": fee_basis, "fee_base": fee_base,
            "fee_base_clamped": fee_base_clamped,
            "settled_fee": settled_fee, "settled_fee_rows": settled_rows,
            "cost": cost, "has_cost": has_cost, "cost_source": cost_source,
            "net_profit": net_profit,
        })

        clicks = a.get("clicks", 0)
        impressions = a.get("impressions", 0)
        conv_revenue = a.get("conv_revenue", _Z)
        ad_rows.append({
            "vendor_item_id": vid, "name": name,
            "ad_spend": ad_spend, "impressions": impressions, "clicks": clicks,
            "conv_revenue": conv_revenue,
            "roas": _ratio(conv_revenue, ad_spend),  # 사실(배수). 추천 아님
            "ctr": _ratio(Decimal(clicks), impressions),
        })

        product_rows.append({
            "vendor_item_id": vid, "name": name,
            "order_count": o.get("order_count", 0), "order_qty": order_qty,
            "return_qty": return_qty,                       # 사실
            "deductible_qty": deductible_qty,                # 그중 손익에 반영된 수량
            "return_rate": _ratio(Decimal(return_qty), order_qty),
            "stock": m.get("stock"), "on_sale": m.get("on_sale"),
            "status_name": m.get("status_name"),
            "sale_price": m.get("sale_price", _Z),
            "in_master": vid in master,
        })

    # 정렬: 회계=순이익 desc, 광고=비용 desc, 상품=주문수 desc (결정론적, D-3)
    account_rows.sort(key=lambda x: x["net_profit"], reverse=True)
    ad_rows.sort(key=lambda x: x["ad_spend"], reverse=True)
    product_rows.sort(key=lambda x: x["order_count"], reverse=True)

    account_sum = {
        "revenue": sum((x["revenue"] for x in account_rows), _Z),
        "return_deduction": sum((x["return_deduction"] for x in account_rows), _Z),
        "service_fee": sum((x["service_fee"] for x in account_rows), _Z),
        "service_fee_vat": sum((x["service_fee_vat"] for x in account_rows), _Z),
        "total_fee": sum((x["total_fee"] for x in account_rows), _Z),
        "ad_spend": sum((x["ad_spend"] for x in account_rows), _Z),
        "cost": sum((x["cost"] for x in account_rows), _Z),
        "net_profit": sum((x["net_profit"] for x in account_rows), _Z),
        # D-CPP-32 ④ 화면이 실토하게 — 수수료 요율의 근거 등급을 금액으로 드러낸다.
        "fee_rate_known_options": sum(
            1 for x in account_rows if x["fee_basis"] == BASIS_SETTLED and x["fee_base"] > _Z
        ),
        # 수수료 과세표준 합계 = 3P 매출 − 반품차감. summary.revenue와 다른 게 정상이다
        # (revenue는 1P·RG를 포함하고 그쪽엔 판매수수료가 없다).
        "fee_base_total": sum((x["fee_base"] for x in account_rows), _Z),
        # 반품차감이 3P매출을 넘어 과세표준이 0으로 끊긴 옵션 수(축 불일치 신호 — 수수료 과소).
        "fee_base_clamped_options": sum(1 for x in account_rows if x["fee_base_clamped"]),
        "fee_rate_default_options": sum(
            1 for x in account_rows if x["fee_basis"] == BASIS_DEFAULT and x["fee_base"] > _Z
        ),
        # 요율을 모른 채 기본 7.8%로 계산한 매출 — 이 금액만큼 수수료가 «추정»이다.
        "fee_default_revenue": sum(
            (x["fee_base"] for x in account_rows if x["fee_basis"] == BASIS_DEFAULT), _Z
        ),
        # 참고: 창 안에 정산 «인식»된 실측 총액. 축이 달라(주문일 vs 인식일) 위 total_fee와 직접
        # 비교하면 안 된다 — 신선도 표면용이다.
        "settled_fee_recognized": sum((x["settled_fee"] for x in account_rows), _Z),
        # 전제 검증: 이미 정산된 라인에서 «계산한 수수료 == 실측»인가. 어긋나면 화면이 경고한다.
        "fee_check": fee_reconciliation(
            db, datetime.combine(dfrom, time.min), datetime.combine(dto, time.max),
            [acc["account_key"]] if acc["account_key"] else None,
        ),
        "cost_covered_options": sum(1 for x in account_rows if x["has_cost"]),
        "cost_internal_options": sum(1 for x in account_rows if x["cost_source"] == "internal"),
        "cost_supply_options": sum(1 for x in account_rows if x["cost_source"] == "coupang_supply"),
        "option_count": len(account_rows),
    }
    # S3(D-2): 매출 중 로켓그로스(RG) 편입분 표시 — 쿠팡 판매분석(3P+RG)과 1:1 대조용.
    account_sum["revenue_rg"] = rg_revenue
    account_sum["revenue_3p"] = account_sum["revenue"] - rg_revenue
    # ── D-CPP-49: 이 숫자가 «무엇인지»와 «얼마나 믿을 수 있는지»를 같이 낸다 ──
    account_sum["revenue_rg_basis"] = "console_net"   # 대시보드 RG 행과 같은 축(계약 ⓑ)
    account_sum["revenue_rg_gross"] = rg_gross_revenue  # 우리 주문 원장(진단용 — 매출 아님)
    # 옵션축이 창을 덮은 날짜 수. complete=False면 revenue_rg는 **부분치**다.
    #   ★셋 다 **항상 값이 있다** — 「모른다」는 필드를 지워서가 아니라 complete=False로 말한다.
    account_sum["rg_option_axis_days"] = f"{rg_axis['days_covered']}/{rg_axis['days_total']}"
    account_sum["rg_option_axis_complete"] = bool(rg_axis["complete"])
    # 아직 콘솔이 닫지 않은 날 수(오늘 등). 경고가 아니라 **사실**이다 — RG 매출이 그날 없는 것은
    # 결함이 아니라 D+1의 결과고, 이걸 분모에서 빼야 「백필 구멍」 경고가 무뎌지지 않는다.
    account_sum["rg_open_days"] = rg_axis["open_days"]
    # S4(D-9, Codex S3 P1#2): net_profit 날짜축 명시 — 오인 방지(투명화). 매출은 주문일 기준이라
    # 쿠팡 판매분석과 일치하나, RG 정산차감(rg_total)은 정산인식일 기준(판매보다 지연)이라 단기
    # 윈도우 RG 순이익은 낙관적(매출 전액 인식·정산 일부만 차감), 장기·정산완료 구간에서 수렴.
    account_sum["net_profit_basis"] = (
        "mixed: revenue=order-date(paid_at), commission=order-date(net_revenue x option rate, D-CPP-32), "
        "rg_settlement_deduction=recognition-date(lags, **광고 제외** — D-CPP-43), "
        "ad=report-date(PA; RG 광고비도 여기 한 곳에서만 차감된다). "
        "RG net_profit optimistic on short windows; converges over closed periods (D-9)."
    )
    ad_spend_total = sum((x["ad_spend"] for x in ad_rows), _Z)
    ad_conv_total = sum((x["conv_revenue"] for x in ad_rows), _Z)
    ad_sum = {
        "ad_spend": ad_spend_total,
        "impressions": sum(x["impressions"] for x in ad_rows),
        "clicks": sum(x["clicks"] for x in ad_rows),
        "conv_revenue": ad_conv_total,
        "roas": _ratio(ad_conv_total, ad_spend_total),
    }

    # ─── S5a(D-15): 비-PA 광고비 — 전체(ALL) 전환, 계정 단위 net_profit 차감 ───
    # report/SALES(권위값, 쿠팡 [광고센터] 0.02% 일치)의 ALL_DELIVERED − DELIVERED = 비-PA
    # (브랜드/디스플레이 등). 비-PA는 옵션 귀속 불가(계정 단위) → RG 플립과 동일하게 account_sum만
    # 조정하고 by_option(ad_rows)은 운영 ROAS 지표로 불변(D-14 패턴).
    # ★게이트(codex P1): ad_cost_daily(ADV_SALES)는 '광고주(advertiser) 단위'(현재 오픽스 A01564720
    #   전용)이고 계정 미태깅(get_ad_cost_totals에 계정 필터 없음). 따라서 '활동 프록시'가 아니라
    #   '계정 식별'로 게이트한다 — ① account=None(전체): 오픽스가 유일 광고주 → 전액 적용.
    #   ② 특정 계정: 그 계정 vendor가 광고주 vendor(오픽스)일 때만. WING2 등 타 계정엔 절대 미적용
    #   (구 'ad_spend_total>0' 프록시는 비-PA만 있는 윈도우 누락 + WING2 옵션PA 생기면 오적용 — 둘 다 결함).
    #   광고주 vendor는 COUPANG_AD_VENDOR_ID → COUPANG_WING1_VENDOR_ID → "A01564720"(라이브 확정) 순.
    #   회귀가드: 데이터0 → nonpa=0 불변.
    from app.services.coupang import ad_cost_sync as _adcost
    _ad_vendor = (os.getenv("COUPANG_AD_VENDOR_ID")
                  or os.getenv("COUPANG_WING1_VENDOR_ID") or "A01564720")
    _apply_nonpa = account is None or acc["vendor_id"] == _ad_vendor
    _conf = _adcost.get_ad_cost_totals(db, dfrom, dto) if _apply_nonpa else None
    # 돈 경로는 불변: 미적용이면 비-PA 추가차감 0원(회귀가드 — 아래 net_profit 계산이 이 값을 쓴다).
    _nonpa = _f(_conf["nonpa"]) if _conf is not None else _Z
    # ★★«미적용»을 0으로 표현하지 않는다 (D-CPP-38, 2026-08-11 시각 QA 발견).
    #   종전엔 게이트에 걸린 계정(WING2 등)에도 0을 실어 보냈고, 프론트의 `?? s.ad_spend` 폴백은
    #   null/undefined만 잡으므로 화면이 **「전체 광고비(ALL) 0원 · net_profit 차감 기준」이라고
    #   단정**했다. 그런데 같은 화면 아래 요약·옵션표는 11,247원이다 — 한 화면에서 광고비가 두 값.
    #   돈은 안 틀렸다(순이익은 옵션축 값을 제대로 뺀다). 틀린 건 실토다: 읽는 사람은
    #   «오하이테크는 광고를 안 썼다»로 읽는다. 「위쪽 단정이 아래쪽 사실을 덮는다」(교훈 #235).
    #   → 광고센터 소스가 이 계정에 **적용되지 않으면 None**을 준다. 그래야 화면이 «값 없음»과
    #     «측정된 0원»을 구분하고, 폴백(옵션축)이 살아난다. 판정 플래그도 같이 실어
    #     프론트가 «무엇을 보고 있는지»를 말할 수 있게 한다.
    ad_sum["ad_confirmed_applies"] = bool(_apply_nonpa)
    ad_sum["ad_confirmed_pa"] = _f(_conf["pa"]) if _conf is not None else None
    ad_sum["ad_confirmed_total"] = _f(_conf["total"]) if _conf is not None else None
    ad_sum["ad_confirmed_nonpa"] = _nonpa if _conf is not None else None
    ad_sum["ad_basis"] = (
        "ad_spend=option-level PA rollup(per-product breakdown). "
        "ad_confirmed_*=report/SALES vendor-level(쿠팡 광고센터 0.02% 일치, 정합 대조용). "
        "net_profit은 전체(집행+비-PA) 차감 — 비-PA는 광고주 계정에만 추가 차감(by_option 불변, D-15)."
    )
    # 감사 체인: net_profit_pre_nonpa(옵션합) → +정산화보정(S4) → −비-PA → net_profit_pre_rg → −RG → net_profit.
    account_sum["net_profit_pre_nonpa"] = account_sum["net_profit"]  # 계정 조정 전 = 옵션합

    # ─── S4(D-11~D-13): net_profit 매출기준 정산화 — 계정 단위 가산 보정 ───
    # 성숙 정산일(그 날 active 주문 전부 정산 인식)은 매출기준을 쿠팡 정산(실지급, SALE−REFUND)으로
    # 교체한다. 보정 = Σ_성숙일(settlement_net − our_net_rev). 미성숙/비-3P/데이터0 → 0(불변, 회귀가드).
    # D-14 패턴: summary(account_sum)만 조정, by_option(account_rows) 운영지표 불변. 화면 '정본매출'(S2
    # Wing GMV)과 분리 — net_profit 매출기준만 정산화(D-12). RG플립/비-PA/배송비 차감 전에 적용(3P 매출축).
    _settle_adj = settlement_revenue_adjustment(db, dfrom, dto, acc, unit_price_by_vid)
    account_sum["settlement_revenue_adjustment"] = _settle_adj["adjustment"]
    account_sum["settlement_matured_lines"] = _settle_adj["matured_lines"]
    account_sum["settlement_net_matured"] = _settle_adj["settlement_net_matured"]
    account_sum["net_profit"] += _settle_adj["adjustment"]

    account_sum["ad_nonpa_deducted"] = _nonpa
    account_sum["net_profit"] -= _nonpa

    product_sum = {
        "option_count": len(product_rows),
        "order_count": sum(x["order_count"] for x in product_rows),
        "order_qty": sum(x["order_qty"] for x in product_rows),
        "return_qty": sum(x["return_qty"] for x in product_rows),
    }

    # RG 정산 비용 집계 + 계정별 대조(reconciliation) 브레이크다운.
    # ★D-10 라이브 확정(2026-06-09): 풀필먼트(J) = 배송비(delivery)+입출고비(warehousing)+보관비(storage).
    #   세 컴포넌트를 fulfillment 한 그룹으로 묶어 표시(레퍼런스 17 §7 검산 일치). 라인 합 = total로 reconcile.
    #   ★ad_sales(광고비 d)는 **차감에서 제외**된다(D-CPP-43) — 광고센터 PA 광고비의 «공제»이고
    #     PA는 이미 ad_spend로 차감됐다. 1차 출처: 윙 「광고비 내역」(광고유형 전부 PA·캠페인 동일).
    rg_total = sum((v["total"] for v in rg_fees.values()), _Z)
    rg_by_account = [_rg_account_breakdown(ak, v) for ak, v in sorted(rg_fees.items())]
    rg_ad_settlement = sum((v.get("ad_sales", _Z) for v in rg_fees.values()), _Z)
    # ★이 감시는 사실상 무력화됐다(D-CPP-43): 2P 라벨을 보는데 실제 RG 광고는 3P 라벨로 실린다.
    #   더구나 이제 정산 광고비를 차감하지 않으므로 이 축의 이중계상 위험 자체가 사라졌다.
    #   필드는 하위호환으로 남기되 **이 값이 0이라는 것을 안전의 근거로 쓰지 말 것**(교훈 #261).
    rg_ad_xlsx_overlap = _agg_rg_ad_overlap(db, dfrom, dto, acc["vendor_id"])
    if rg_ad_xlsx_overlap != _Z:
        log.warning(
            "RG 광고 이중계상 위험(D-16): 광고 XLSX 2P=%s 발생 — 정산 ad_sales=%s와 겹칠 수 있음. "
            "(D-CPP-43 이후 정산 광고비는 차감하지 않으므로 이 축의 이중계상 위험은 사라졌다 — 참고 로그.)",
            rg_ad_xlsx_overlap, rg_ad_settlement,
        )

    # ─── S7(D-14/D-CPP-43): net_profit 플립 — 계정 단위 RG 정산액 차감(**광고 제외**) ───
    # ★D-CPP-43(2026-08-12): **D-16 폐기**, 구 D-15의 「광고 제외 차감」으로 복귀.
    #   D-16은 `sell_type=2P` 0행을 근거로 광고 포함 전액을 뺐으나 **라벨이 판매경로가 아니다**
    #   (오픽스 PA의 97.28%가 RG 옵션에 쓰이고 상위 5옵션의 3P 주문은 0건).
    #   1차 출처(윙 「광고비 내역」)가 정산 광고비 = 광고센터 PA의 «공제»임을 확정했다 →
    #   PA는 ad_spend로 이미 차감되므로 여기서 또 빼면 이중계상. ref 04 §2·ref 17 §7이 이미
    #   같은 말을 했었다(D-16이 하루 뒤 라벨 증거로 덮었다).
    # D-14: 차감은 summary(account_sum) 레벨만. by_option net_profit은 운영지표로 불변.
    # 회귀 가드: RG 데이터 0이면 rg_total=0 → 플립 no-op(불변).
    # 대조 기준선(RG 플립 전). ★주의(codex P2-1): 이 값은 '비-PA 차감 후·RG 차감 전'이다
    #   (옵션합=net_profit_pre_nonpa, 비-PA 차감 후=net_profit_pre_rg). 감사 시 체인 구분.
    account_sum["net_profit_pre_rg"] = account_sum["net_profit"]
    # ★D-CPP-43: 차감액은 **광고 제외분**이다. 정산 ad_sales는 광고센터 PA 광고비의 «공제»이고
    #   PA는 이미 ad_spend로 차감됐으므로 여기서 또 빼면 이중계상이다(1차 출처: 윙 「광고비 내역」).
    rg_deducted = rg_total - rg_ad_settlement
    account_sum["rg_settlement_total"] = rg_total                 # 정산 총액(광고 포함) — 표시·검산용
    account_sum["rg_ad_settlement"] = rg_ad_settlement            # 표시 전용: PA 공제분(**차감 안 함**)
    account_sum["rg_non_ad_deducted"] = rg_deducted               # 광고 제외분(= 실제 차감액)
    account_sum["rg_settlement_deducted"] = rg_deducted           # ★순이익에서 실제로 뺀 값(명시 필드)
    account_sum["net_profit"] = apply_rg_net_profit_flip(
        account_sum["net_profit"], rg_deducted
    )
    # status enum(Codex #6): money basis 명시. 불리언 안 씀.
    account_sum["rg_flip_status"] = "applied_ex_ad" if len(rg_fees) > 0 else "not_applied_no_data"

    # ─── 3P 배송 손익(한진 1,900 비용 − 고객이 낸 배송비 수입) — 계정 단위 최종 반영 ───
    # 구 대시보드는 차감하나 종합조망은 누락했던 3P 실비용(Jino 2026-06-15).
    # ★D-CPP-33(2026-08-10): 위 줄에 있던 "VAT는 양쪽 미차감으로 통일"은 **폐기된 옛말**이다 —
    #   Jino가 2026-08-04에 「납부세액을 뺀다」로 방침을 뒤집었는데(payable_vat 참조) 그 결정이
    #   profit_calculator에만 내려앉고 여기엔 옛 주석만 남았다. 아래에서 같이 차감한다.
    # ★D-CPP-33: 배송 «수입»도 함께 센다. 종전엔 비용만 빼서 배송이 무조건 손해로 잡혔다
    #   (라이브 7월 WING2: 수입 10,000원이 통째로 빠져 있었다).
    # 3P(판매자배송)만 발생, RG/로켓=쿠팡 풀필먼트라 Order rows 0 → 자동 0(회귀 가드).
    # D-14 패턴: 계정 단위(summary)만 반영, by_option(account_rows) 운영지표 불변.
    account_sum["net_profit_pre_shipping"] = account_sum["net_profit"]
    _ship = _agg_seller_shipping_3p(db, dfrom, dto, acc["channel_ids"])
    account_sum["seller_shipping_3p"] = _ship["cost"]
    account_sum["shipping_income_3p"] = _ship["income"]
    account_sum["shipment_count_3p"] = _ship["shipments"]
    account_sum["net_profit"] -= _ship["cost"]
    account_sum["net_profit"] += _ship["income"]

    # ─── 납부세액(부가세) — Jino 2026-08-04 결정을 이 엔진에도 내린다(D-CPP-33) ───
    # 매출VAT − 매입세액공제. 구 대시보드(profit_calculator)는 2026-08-04부터 이걸 빼 왔고
    # 종합조망만 안 빼서 두 화면이 갈렸다(라이브 7월 WING2 실측 차 32,262.96원 = 이 항목).
    # 매입세액에 넣는 것: 원가·수수료·배송비·광고비 — 넷 다 VAT 포함 축(payable_vat docstring).
    # ★★적대 리뷰 P1-2 수용(2026-08-10): 「구 대시보드도 RG를 VAT 밖에 둔다」던 첫 주석은 틀렸다.
    #   구 대시보드는 RG를 **양쪽 다** 밖에 둔다 — profit_calculator는 orders만 읽고 prod orders에
    #   RG 채널 행이 0건이라 그 VAT 과세표준엔 RG 매출이 애초에 없다. 반면 종합조망의 revenue는
    #   RG를 편입하고(라이브 90일 매출의 83%가 RG) 있으므로, RG 정산액의 매입세액만 빼면
    #   «매출은 넣고 매입은 부인»하는 편측 항이 된다 — 라이브 90일 2,631,770.27원 과다차감.
    #   RG 정산 구성(광고·배송·입출고·판매수수료·보관…)은 전부 쿠팡에서 매입한 용역이라
    #   total_fee와 성격이 같다. → rg_total도 매입세액에 넣는다.
    from app.services.profit_calculator import payable_vat
    _vat_revenue = account_sum["revenue"] + _ship["income"]
    # 원 단위 소수 2자리로 양자화 — /110이 무한소수라 그대로 두면 25자리가 API로 새고,
    # 계정합≠전체의 1e-24 차이가 등가성 검사를 흔든다(PR #273 리뷰 P2-8과 같은 부류).
    _vat = payable_vat(
        _vat_revenue,
        account_sum["cost"], account_sum["total_fee"],
        _ship["cost"], account_sum["ad_spend"],
        account_sum["ad_nonpa_deducted"],   # 비-PA 광고비도 VAT 포함 실비용이다(매입세액 대상)
        # ★D-CPP-43: **광고 제외분**을 넘긴다. 종전엔 rg_total(광고 포함)을 넘겨서, PA 광고비의
        #   매입세액이 ad_spend로 한 번 + rg_total 안의 ad_sales로 또 한 번 **이중 공제**됐다.
        #   차감의 이중계상과 같은 구조의 오류이고, 윙 「광고비 내역」이 *"광고비는 … 매입세금
        #   계산서 **1건**이 발행"*이라 못 박으므로 매입세액도 한 번만 인정해야 한다.
        rg_deducted,                        # RG 정산액 중 광고 제외분(VAT 포함 매입)
    ).quantize(_Q2, ROUND_HALF_UP)
    account_sum["net_profit_pre_vat"] = account_sum["net_profit"]
    account_sum["payable_vat"] = _vat
    account_sum["net_profit"] -= _vat

    # 반품 억제 실토 — 몇 건이 왜 차감에서 빠졌는지(D-CPP-33, 침묵 금지)
    account_sum["return_suppression"] = _orphan_return_stats(
        db, dfrom, dto, acc["account_key"], acc["channel_ids"]
    )

    rg_settlement = {
        "summary": {
            "total": rg_total,
            "has_data": len(rg_fees) > 0,
            "note": (
                "RG 정산 비용 반영됨(계정 단위, D-14/D-CPP-43). "
                "★정산 광고비는 광고센터 PA 광고비의 «공제»이므로 여기서 차감하지 않는다 — "
                "PA는 이미 ad_spend로 차감됐다(이중계상 방지). 1차 출처: 윙 「광고비 내역」 "
                "(광고유형 전부 PA · 캠페인 이름이 광고센터와 동일 · 미공제분은 다음 정산으로 이월). "
                "정산주기 기준(부분 윈도우도 주기 전액)."
            ),
            "flip_status": "applied_ex_ad" if len(rg_fees) > 0 else "not_applied_no_data",
            # ★D-CPP-43: 실제 차감액은 **광고 제외분**이다(광고는 PA에서 이미 차감 — 이중계상 방지).
            "deducted": rg_total - rg_ad_settlement,        # ★net_profit에서 실제 차감된 값
            "non_ad_deducted": rg_total - rg_ad_settlement,  # 동일값(하위호환 유지)
            "ad_settlement": rg_ad_settlement,      # 정산 광고비 = PA 공제분. **차감 안 함**(표시 전용)
            "ad_xlsx_rg_overlap": rg_ad_xlsx_overlap,  # 광고비 XLSX의 RG(2P)분(현재 0, 미래 겹침 감시용)
        },
        "by_account": rg_by_account,
    }

    period = {"from": dfrom.isoformat(), "to": dto.isoformat()}
    if account is not None:
        period["account"] = account  # 계정 분리 뷰일 때만 명시. account=None은 기존 응답 형태 보존(등가성, Codex S1 P1#1).
    return {
        "period": period,
        "account": {"summary": account_sum, "by_option": account_rows},
        "ad": {"summary": ad_sum, "by_option": ad_rows},
        "product": {"summary": product_sum, "by_option": product_rows},
        "rg_settlement": rg_settlement,
    }
