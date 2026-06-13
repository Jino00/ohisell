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
from decimal import Decimal

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

log = logging.getLogger(__name__)

_Z = Decimal("0")
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
    """로켓그로스(RG) 주문을 옵션ID별 집계. 매출=Σ(unit_sales_price×sales_quantity), paid_at 기준.

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


def _agg_ads(db: Session, dfrom: date, dto: date,
             vendor_id: str | None = None) -> dict[str, dict]:
    """광고 옵션 일별을 옵션ID별 집계. D-9: 비용·노출·클릭은 ad_option_id 귀속,
    매출·주문은 conv_option_id 귀속(간접전환 대비 분리). 같은 옵션이면 한 행에 합쳐짐.

    S1: vendor_id 주면 해당 계정 광고만(계정 분리). None이면 전체(기존 동작 불변)."""
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
    conv_rows = conv_q.group_by(CoupangAdOptionDaily.conv_option_id).all()
    for vid, conv_rev, ords, qty in conv_rows:
        b = _row(vid)
        b["conv_revenue"] += _f(conv_rev)
        b["ad_orders"] += int(ords or 0)
        b["ad_qty"] += int(qty or 0)
    return out


def _agg_returns(db: Session, dfrom: date, dto: date,
                account_key: str | None = None) -> dict[str, dict]:
    """반품/취소를 옵션ID별 집계. withdrawn=False(철회 제외)만. 사실=반품 수량·건수.

    S1: account_key 주면 해당 계정만(계정 분리, account_key 컬럼 직접 필터). None이면 전체(불변)."""
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
    rows = q.group_by(CoupangReturnItem.vendor_item_id).all()
    return {
        str(vid): {
            "return_qty": int(cancel or 0),
            "receipt_count": int(cnt or 0),
            "name": name,
        }
        for vid, cancel, cnt, name in rows
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
                            account_key: str | None = None) -> dict[str, dict]:
    """RG 정산 수수료를 account_key별·fee_type별로 집계. D-6/D-7: 대조(reconciliation) 뷰용.

    S1: account_key 주면 해당 계정만(계정 분리). None이면 전체(기존 동작 불변).

    날짜 필터: recognition_date_from과 recognition_date_to가 [dfrom, dto]와 겹치는 행.
    겹침 조건 = recognition_date_from <= dto AND recognition_date_to >= dfrom.
    반환: {account_key: {fee_type: amount, ..., "total": Decimal}}

    ★vendor_item_id=="" 가드(S6, codex P1): 계정 단위 대조뷰는 status/api 계정 row(sentinel '',
      VAT후)만 집계한다. S6 옵션 row(vendor_item_id=옵션ID, VAT前 A-B)는 같은 (account,기간,fee_type)에
      공존하므로 필터하지 않으면 VAT후+VAT前이 합산돼 비용이 과대계상된다. 옵션 row는 S7 net_profit
      플립에서 별도 reader로 사용. (검산: Σ옵션(VAT前)+요약세액 == 계정 row(VAT후).)
    """
    rows = (
        db.query(
            CoupangRgSettlementFee.account_key,
            CoupangRgSettlementFee.fee_type,
            func.sum(CoupangRgSettlementFee.amount),
        )
        .filter(
            CoupangRgSettlementFee.recognition_date_from <= dto,
            CoupangRgSettlementFee.recognition_date_to >= dfrom,
            CoupangRgSettlementFee.vendor_item_id == "",  # 계정 row만(옵션 row 이중계상 차단)
            *([CoupangRgSettlementFee.account_key == account_key]
              if account_key is not None else []),
        )
        .group_by(
            CoupangRgSettlementFee.account_key,
            CoupangRgSettlementFee.fee_type,
        )
        .all()
    )
    result: dict[str, dict] = {}
    for account_key, fee_type, amount in rows:
        entry = result.setdefault(account_key, {"total": _Z})
        entry[fee_type] = _f(amount)
        entry["total"] = entry["total"] + _f(amount)
    return result


# ──────────────────────────────────────────────
# D-16 광고비 처리 규칙 (S7 /browse 라이브 확정 2026-06-09, D-11/D-15 개정)
# ──────────────────────────────────────────────
# ★D-16(S7): RG 광고비는 RG 정산(totalAdSalesDeductionAmount)에만 있고, 업로드되는 광고 XLSX
# (CoupangAdOptionDaily, 광고센터 PA 보고서 pa_daily)에는 안 잡힌다 — 광고센터는 마켓플레이스(3P/윙)
# 검색·디스플레이 광고 플랫폼이고 RG 광고는 별개 청구(prod 광고 XLSX 2P 0행 라이브 실증). 따라서
# net_profit 플립(S7)은 광고 포함 rg_total을 전액 차감한다(D-16). settlement ad_sales는 표시·검산용.
# 아래 RG(2P) 합산 헬퍼는 광고센터에서 RG상품 검색광고를 돌릴 경우의 미래 겹침 감시용일 뿐(현재 0).
# sell_type 코드: 3P=윙·2P=RG·Retail=로켓배송(ad_costs 확정).
RG_AD_SELL_TYPE = "2P"  # 광고 XLSX 판매방식 코드: 2P=로켓그로스(RG)


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
    """D-16(S7): 종합조망 순이익에 RG 정산 비용을 반영(플립). 정산 총액을 전액 차감한다.

    공식: net_profit_new = net_profit_pre_rg − rg_deducted (= rg_total, 광고 포함).

    D-16(/browse 라이브 조사): RG 광고비는 광고센터 PA 보고서에 안 잡히고 RG 정산에만 존재하므로
    (prod 2P 0행 실증), 광고 포함 rg_total을 전액 차감해야 net_profit에 반영된다. rg_total은
    전부 정산 basis라 basis 불일치 없음. 음수 환급주기(rg_deducted<0)도 부호 그대로 가산되어
    환급이 정확히 반영된다. 순수 함수라 DB 없이 fixture 테스트 가능(D-12).
    """
    return net_profit_pre_rg - rg_deducted


def _agg_rg_ad_overlap(db: Session, dfrom: date, dto: date,
                       vendor_id: str | None = None) -> Decimal:
    """기간 내 광고비 XLSX의 RG(2P) 광고비 합 — 미래 이중계상 겹침 감시용.

    ★D-16(S7): RG 광고는 광고센터 PA 보고서(이 XLSX)에 안 잡히고 RG 정산에만 있어(라이브 실증)
    플립은 rg_total을 전액 차감한다. 이 값은 정상=0 — 광고센터에서 RG상품 검색광고를 돌려 PA 2P가
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
    # S3(D-2): RG 매출 편입 — CoupangRgOrderItem을 옵션ID로 orders에 병합. summary·by_option 모두 RG 포함.
    rg_orders = _agg_rg_orders(db, dfrom, dto, acc["account_key"])
    rg_revenue = _merge_rg_orders(orders, rg_orders)
    ads = _agg_ads(db, dfrom, dto, acc["vendor_id"])
    returns = _agg_returns(db, dfrom, dto, acc["account_key"])
    fees = _agg_fees(db, dfrom, dto, acc["account_key"])
    rg_fees = _agg_rg_settlement_fees(db, dfrom, dto, acc["account_key"])  # D-6/D-7: 대조 뷰용

    all_vids = set(master) | set(orders) | set(ads) | set(returns) | set(fees)

    account_rows: list[dict] = []
    ad_rows: list[dict] = []
    product_rows: list[dict] = []

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
        return_qty = r.get("return_qty", 0)
        return_deduction = unit_price * return_qty  # 추정(평균단가×반품수량)
        service_fee = f.get("service_fee", _Z)
        service_fee_vat = f.get("service_fee_vat", _Z)
        total_fee = f.get("total_fee", _Z)  # 수수료+수수료VAT (쿠팡 실차감, codex[P2])
        ad_spend = a.get("spend", _Z)

        # 원가 — D-12: 내부 product_master.cost_price 우선, 없으면 coupang supply_price 폴백.
        # 단가는 순판매수량(주문−반품)에 적용. 0/None은 원가정보 없음으로 간주(미반영).
        internal_cost = cm.get("cost_price")
        supply = m.get("supply_price")
        net_qty = order_qty - return_qty
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
            "return_qty": return_qty,
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
        "cost_covered_options": sum(1 for x in account_rows if x["has_cost"]),
        "cost_internal_options": sum(1 for x in account_rows if x["cost_source"] == "internal"),
        "cost_supply_options": sum(1 for x in account_rows if x["cost_source"] == "coupang_supply"),
        "option_count": len(account_rows),
    }
    # S3(D-2): 매출 중 로켓그로스(RG) 편입분 표시 — 쿠팡 판매분석(3P+RG)과 1:1 대조용.
    account_sum["revenue_rg"] = rg_revenue
    account_sum["revenue_3p"] = account_sum["revenue"] - rg_revenue
    # S4(D-9, Codex S3 P1#2): net_profit 날짜축 명시 — 오인 방지(투명화). 매출은 주문일 기준이라
    # 쿠팡 판매분석과 일치하나, RG 정산차감(rg_total)은 정산인식일 기준(판매보다 지연)이라 단기
    # 윈도우 RG 순이익은 낙관적(매출 전액 인식·정산 일부만 차감), 장기·정산완료 구간에서 수렴.
    account_sum["net_profit_basis"] = (
        "mixed: revenue=order-date(paid_at), rg_settlement_deduction=recognition-date(lags). "
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
    product_sum = {
        "option_count": len(product_rows),
        "order_count": sum(x["order_count"] for x in product_rows),
        "order_qty": sum(x["order_qty"] for x in product_rows),
        "return_qty": sum(x["return_qty"] for x in product_rows),
    }

    # RG 정산 비용 집계 + 계정별 대조(reconciliation) 브레이크다운.
    # ★D-10 라이브 확정(2026-06-09): 풀필먼트(J) = 배송비(delivery)+입출고비(warehousing)+보관비(storage).
    #   세 컴포넌트를 fulfillment 한 그룹으로 묶어 표시(레퍼런스 17 §7 검산 일치). 라인 합 = total로 reconcile.
    #   ad_sales(광고비 d)는 D-16에서 net_profit 전액 차감에 포함됨(RG 광고는 정산 전용, 광고센터 미포함).
    rg_total = sum((v["total"] for v in rg_fees.values()), _Z)
    rg_by_account = [_rg_account_breakdown(ak, v) for ak, v in sorted(rg_fees.items())]
    rg_ad_settlement = sum((v.get("ad_sales", _Z) for v in rg_fees.values()), _Z)
    # D-16 미래 겹침 감시(Codex S7): 정상=0. 0이 아니면 광고센터에서 RG상품 검색광고를 돌려 PA 2P가
    #   생긴 것 → rg_total 전액 차감(정산 광고 포함)과 XLSX 2P(net_profit에 이미 반영)가 이중계상될 수 있음.
    rg_ad_xlsx_overlap = _agg_rg_ad_overlap(db, dfrom, dto, acc["vendor_id"])
    if rg_ad_xlsx_overlap != _Z:
        log.warning(
            "RG 광고 이중계상 위험(D-16): 광고 XLSX 2P=%s 발생 — 정산 ad_sales=%s와 겹칠 수 있음. "
            "D-16(전액 차감)은 2P=0 전제 → 재검토 필요(트랙 D-16 잔존 리스크).",
            rg_ad_xlsx_overlap, rg_ad_settlement,
        )

    # ─── S7(D-14/D-16): net_profit 플립 — 계정 단위 RG 정산 총액 전액 차감 ───
    # ★D-16(D-15 개정, /browse 라이브 조사 2026-06-09): RG 광고비는 광고센터 PA 보고서(pa_daily XLSX,
    #   advertising.coupang.com /reports/pa = 마켓플레이스 광고)에 안 잡히고 RG 정산에만 존재
    #   (prod 광고 XLSX 2P 0행 실증) → 광고 포함 rg_total 전액 차감해야 net_profit에 반영된다.
    #   rg_total은 전부 정산 basis라 basis 불일치 없음(D-15 non-ad 차감보다 단순·정확). 2P=0이라
    #   이중계상 없음. (구 D-15 "광고 제외 차감"은 RG 광고가 XLSX 2P에 있다는 틀린 전제 → 폐기.)
    # D-14: 차감은 summary(account_sum) 레벨만. by_option net_profit은 운영지표로 불변.
    # 회귀 가드: RG 데이터 0이면 rg_total=0 → 플립 no-op(불변).
    account_sum["net_profit_pre_rg"] = account_sum["net_profit"]  # 대조 기준선(플립 전)
    account_sum["rg_settlement_total"] = rg_total                 # ★전액 차감액(VAT後, 광고 포함)
    account_sum["rg_ad_settlement"] = rg_ad_settlement            # 표시: 전액 중 광고분(D-16 라이브 조사)
    account_sum["rg_non_ad_deducted"] = rg_total - rg_ad_settlement  # 표시: 전액 중 광고 제외분(브레이크다운)
    account_sum["net_profit"] = apply_rg_net_profit_flip(
        account_sum["net_profit"], rg_total
    )
    # status enum(Codex #6): money basis 명시. 불리언 안 씀.
    account_sum["rg_flip_status"] = "applied_full" if len(rg_fees) > 0 else "not_applied_no_data"

    rg_settlement = {
        "summary": {
            "total": rg_total,
            "has_data": len(rg_fees) > 0,
            "note": (
                "RG 정산 비용 반영됨(Phase2/S7, 계정 단위 전액 차감, D-14/D-16). "
                "RG 광고비는 광고센터 PA 보고서에 없고 정산에만 있어 전액(광고 포함) 차감(라이브 조사). "
                "정산주기 기준(부분 윈도우도 주기 전액)."
            ),
            "flip_status": "applied_full" if len(rg_fees) > 0 else "not_applied_no_data",
            "deducted": rg_total,                    # ★net_profit에서 실제 차감된 RG 총액(광고 포함)
            "non_ad_deducted": rg_total - rg_ad_settlement,  # 표시: 전액 중 광고 제외분(브레이크다운)
            # D-16: RG 광고는 정산 전용(광고센터 미포함) → 전액 차감에 포함.
            "ad_settlement": rg_ad_settlement,      # RG정산 광고비(전액 중 광고분)
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
