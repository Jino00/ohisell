# profit_calculator.py — 핵심 이익률 계산 엔진
from __future__ import annotations

import json
import logging
from datetime import date
from decimal import Decimal

from collections import defaultdict

from sqlalchemy import and_, func, text
from sqlalchemy.orm import Session

from app.models import Channel, CoupangRgSettlementFee, Order, ProductChannelMapping, ProductMaster
from app.services.cafe24_status_mapper import REVENUE_EXCLUDED
from app.services.manual_revenue_service import get_daily_manual_revenue

log = logging.getLogger(__name__)

ZERO = Decimal("0")

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
    """ad_costs → GFA(ADVoost) 일별 총 광고비 {date_str: spend}"""
    rows = db.execute(
        text("""
            SELECT ad_date, SUM(CAST(ad_spend AS REAL))
            FROM ad_costs
            WHERE channel_id = :cid AND source = 'gfa:쇼핑'
              AND ad_date >= :since AND ad_date <= :until
            GROUP BY ad_date
        """),
        {"cid": naver_channel_id, "since": date_from.isoformat(), "until": date_to.isoformat()},
    ).fetchall()
    return {str(r[0]): Decimal(str(r[1])) for r in rows if r[1]}


def _line_commission(ch: Channel | None, o: Order, revenue: Decimal) -> Decimal:
    """라인 수수료. cafe24/naver는 동기화 시 산출된 commission_amount 사용, 그 외는 채널 정률."""
    if ch and ch.code == "CAFE24":
        return o.commission_amount if o.commission_amount is not None else ZERO
    if ch and ch.code == "NAVER":
        if o.commission_amount is not None:
            return o.commission_amount
        # commission_amount 없으면 채널 정률 폴백 (API 미지원 케이스 방어)
        rate = ch.commission_rate if ch else ZERO
        return revenue * rate / Decimal("100")
    rate = ch.commission_rate if ch else ZERO
    return revenue * rate / Decimal("100")


# 한진택배 주문(배송) 1건당 판매자 지급액 — 전 채널 동일, 고객 결제 여부 무관
HANJIN_PER_SHIPMENT = Decimal("1900")


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
    """고객이 결제한 배송비 = 매출 가산분 (수수료 미부과). 라인 단위 원본값.

    NAVER deliveryFeeAmount: productOrder(패키지)별로 저장 → 라인 합 = 주문 총액 (정확).
    COUPANG shippingPrice: shipment 단위 값이 박스 내 모든 라인에 복사돼 있음
      → 호출부에서 _shipment_key로 배송 1회만 가산 (라인 합산 시 중복).
    CAFE24/기타: 고객 무료배송이라 수입 0.
    """
    if not ch:
        return ZERO
    code = ch.code or ""
    if code == "NAVER" or code.startswith("COUPANG"):
        return o.shipping_cost or ZERO
    return ZERO


def _is_coupang(ch: Channel | None) -> bool:
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


def get_rg_total_by_account(db: Session, date_from: date, date_to: date) -> dict[str, Decimal]:
    """RG 정산 총액을 account_key별로 반환 (KPI·channel-breakdown 차감용).

    intelligence._agg_rg_settlement_fees와 같은 overlap 필터·vendor_item_id="" 가드 적용.
    반환: {"COUPANG_WING1": Decimal, "COUPANG_WING2": Decimal, ...}
    데이터 없으면 빈 dict → 차감 no-op.
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

    # 수동 매출이 있으면 주문이 없어도 계속 진행
    manual_lookup = get_daily_manual_revenue(db, date_from, date_to, channel_id)
    if not orders and not manual_lookup:
        return []

    option_map = _get_option_id_map(db)

    # 모든 option_id 수집하여 광고비 일괄 조회
    all_option_ids = list(set(o.platform_product_id for o in orders if o.platform_product_id))
    ad_lookup = _get_ad_spend_lookup(ad_db, date_from, date_to, all_option_ids)

    # 날짜별 집계
    daily: dict[str, dict] = {}
    manual_revenue_by_date: dict[str, Decimal] = {}  # 순이익 제외용 추적
    seen_shipments: set[tuple] = set()  # 배송(packageNumber/박스)당 1,900 1회
    seen_deliv: set[tuple] = set()      # 쿠팡 배송수입 박스당 1회 (라인 복사 dedup)
    for o in orders:
        ch = channel_map.get(o.channel_id)
        if ch and ch.channel_type == "consignment":
            continue
        if o.status in REVENUE_EXCLUDED:  # 취소/반품/입금전 매출 제외
            continue

        d = str(o.order_date.date()) if hasattr(o.order_date, "date") else str(o.order_date)[:10]
        if d not in daily:
            daily[d] = {
                "revenue": ZERO,
                "product_revenue": ZERO,
                "shipping_revenue": ZERO,
                "cost": ZERO,
                "commission": ZERO,
                "shipping": ZERO,
                "ad_spend": ZERO,
                "vat": ZERO,
                "order_count": 0,
            }

        bucket = daily[d]
        skey = _shipment_key(ch, o)
        product_rev = _line_revenue(ch, o)
        # 고객이 낸 배송비 → 매출 가산. 쿠팡은 박스값이 라인마다 복사돼 배송당 1회만.
        deliv = _delivery_income(ch, o)
        if deliv and _is_coupang(ch):
            if skey in seen_deliv:
                deliv = ZERO
            else:
                seen_deliv.add(skey)
        revenue = product_rev + deliv
        bucket["product_revenue"] += product_rev
        bucket["shipping_revenue"] += deliv
        bucket["revenue"] += revenue
        bucket["order_count"] += 1

        # 원가
        if o.product_id and o.product_id in product_map:
            bucket["cost"] += product_map[o.product_id].cost_price * o.quantity

        # 수수료 — 상품매출 기준만 (배송비엔 수수료 미부과)
        bucket["commission"] += _line_commission(ch, o, product_rev)

        # 판매자 배송비 — 한진 1,900/물리배송(packageNumber·박스). 배송당 1회만.
        if skey not in seen_shipments:
            seen_shipments.add(skey)
            bucket["shipping"] += HANJIN_PER_SHIPMENT

        # VAT — 표시 매출(상품+배송) 기준
        bucket["vat"] += revenue * Decimal("10") / Decimal("110")

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
            daily[d] = {
                "revenue": ZERO, "product_revenue": ZERO, "shipping_revenue": ZERO,
                "cost": ZERO, "commission": ZERO,
                "shipping": ZERO, "ad_spend": ZERO, "vat": ZERO, "order_count": 0,
            }
        daily[d]["revenue"] += revenue
        daily[d]["product_revenue"] += revenue
        manual_revenue_by_date[d] = manual_revenue_by_date.get(d, ZERO) + revenue

    # 정렬 후 반환
    result = []
    for d in sorted(daily.keys()):
        b = daily[d]
        # 수동 매출은 순이익 계산에서 제외 (매출만 표시)
        mr = manual_revenue_by_date.get(d, ZERO)
        net = (b["revenue"] - mr) - b["cost"] - b["commission"] - b["shipping"] - b["ad_spend"] - b["vat"]
        result.append({
            "date": d,
            "revenue": str(b["revenue"]),
            "product_revenue": str(b["product_revenue"]),
            "shipping_revenue": str(b["shipping_revenue"]),
            "cost": str(b["cost"]),
            "commission": str(b["commission"]),
            "ad_spend": str(b["ad_spend"]),
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
    manual_lookup_ch = get_daily_manual_revenue(db, date_from, date_to)
    if not orders and not manual_lookup_ch:
        return []

    option_map = _get_option_id_map(db)
    all_option_ids = list(set(o.platform_product_id for o in orders if o.platform_product_id))
    ad_lookup = _get_ad_spend_lookup(ad_db, date_from, date_to, all_option_ids)

    # 채널별 집계
    by_channel: dict[int, dict] = {}
    seen_shipments: set[tuple] = set()  # 배송(packageNumber/박스)당 1,900 1회
    seen_deliv: set[tuple] = set()      # 쿠팡 배송수입 박스당 1회 (라인 복사 dedup)
    for o in orders:
        ch = channel_map.get(o.channel_id)
        if ch and ch.channel_type == "consignment":
            continue
        if o.status in REVENUE_EXCLUDED:  # 취소/반품/입금전 매출 제외
            continue

        cid = o.channel_id
        if cid not in by_channel:
            by_channel[cid] = {
                "revenue": ZERO, "product_revenue": ZERO, "shipping_revenue": ZERO,
                "cost": ZERO, "commission": ZERO,
                "ad_spend": ZERO, "shipping": ZERO, "vat": ZERO, "order_count": 0,
            }

        b = by_channel[cid]
        skey = _shipment_key(ch, o)
        product_rev = _line_revenue(ch, o)
        # 고객이 낸 배송비 → 매출 가산. 쿠팡은 박스값이 라인마다 복사돼 배송당 1회만.
        deliv = _delivery_income(ch, o)
        if deliv and _is_coupang(ch):
            if skey in seen_deliv:
                deliv = ZERO
            else:
                seen_deliv.add(skey)
        revenue = product_rev + deliv
        b["product_revenue"] += product_rev
        b["shipping_revenue"] += deliv
        b["revenue"] += revenue
        b["order_count"] += 1

        if o.product_id and o.product_id in product_map:
            b["cost"] += product_map[o.product_id].cost_price * o.quantity

        # 수수료 — 상품매출 기준만 (배송비엔 수수료 미부과)
        b["commission"] += _line_commission(ch, o, product_rev)
        # 판매자 배송비 — 한진 1,900/물리배송(packageNumber·박스). 배송당 1회만.
        if skey not in seen_shipments:
            seen_shipments.add(skey)
            b["shipping"] += HANJIN_PER_SHIPMENT
        # VAT — 표시 매출(상품+배송) 기준
        b["vat"] += revenue * Decimal("10") / Decimal("110")

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

    # 수동 매출 채널 행 병합 (매출-only, 순이익 None)
    manual_by_channel: dict[int, Decimal] = {}
    for (mr_ch_id, _), revenue in manual_lookup_ch.items():
        manual_by_channel[mr_ch_id] = manual_by_channel.get(mr_ch_id, ZERO) + revenue

    result = []
    for cid, b in by_channel.items():
        ch = channel_map.get(cid)
        net = b["revenue"] - b["cost"] - b["commission"] - b["ad_spend"] - b["shipping"] - b["vat"]
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
            "net_profit": None,  # 순이익 계산 불가 (매출-only)
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
    """상품별 이익률 Top N"""
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
    if not orders:
        return []

    channel_map, product_map = _build_channel_maps(db)
    all_option_ids = list(set(o.platform_product_id for o in orders if o.platform_product_id))
    ad_lookup = _get_ad_spend_lookup(ad_db, date_from, date_to, all_option_ids)

    # option_id별 광고비 합산
    ad_by_option: dict[str, Decimal] = {}
    for (oid, _), spend in ad_lookup.items():
        ad_by_option[oid] = ad_by_option.get(oid, ZERO) + spend

    # 상품별 집계 (배송비·고객배송수입은 배송 단위라 아래에서 라인 비례 배분)
    by_product: dict[int, dict] = {}
    ship_groups: dict[tuple, dict] = {}
    for o in orders:
        ch = channel_map.get(o.channel_id)
        if ch and ch.channel_type == "consignment":
            continue
        if o.status in REVENUE_EXCLUDED:  # 취소/반품/입금전 매출 제외
            continue

        pid = o.product_id
        if pid not in by_product:
            by_product[pid] = {
                "revenue": ZERO, "product_revenue": ZERO, "shipping_revenue": ZERO,
                "cost": ZERO, "commission": ZERO,
                "ad_spend": ZERO, "shipping": ZERO, "vat": ZERO, "quantity": 0,
            }

        b = by_product[pid]
        product_rev = _line_revenue(ch, o)
        b["product_revenue"] += product_rev
        b["revenue"] += product_rev  # 고객배송수입은 배송단위 배분으로 아래에서 추가
        b["quantity"] += o.quantity
        # VAT — 상품매출 기준 누적 (배송수입 VAT는 아래 alloc 단계에서 추가)
        b["vat"] += product_rev * Decimal("10") / Decimal("110")

        if pid in product_map:
            b["cost"] += product_map[pid].cost_price * o.quantity

        # 수수료 — 상품매출 기준만 (배송비엔 수수료 미부과)
        b["commission"] += _line_commission(ch, o, product_rev)

        # 배송 그룹 — 한진 1,900 & 고객배송수입을 라인 비례 배분
        skey = _shipment_key(ch, o)
        g = ship_groups.setdefault(skey, {"lines": [], "deliv": ZERO})
        g["lines"].append((pid, product_rev))
        dval = _delivery_income(ch, o)
        if dval:
            if _is_coupang(ch):
                if g["deliv"] == ZERO:  # 박스값 라인 복사 → 최초 1회만 (first-wins)
                    g["deliv"] = dval
            else:
                g["deliv"] += dval  # NAVER: 패키지별 라인 합 = 배송 총액

        # 광고비는 아래에서 option_id → product_id 매핑으로 일괄 할당

    # 배송 1건당 한진 1,900 + 고객배송수입을 그룹 내 라인 매출 비례 배분 (합 보존)
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
        _alloc_to_lines(_g["lines"], HANJIN_PER_SHIPMENT, "shipping")
        if _g["deliv"]:
            # 고객배송수입을 revenue와 shipping_revenue 양쪽에 동시 누적 (불변식: revenue = product + shipping)
            _alloc_to_lines(_g["lines"], _g["deliv"], "revenue")
            _alloc_to_lines(_g["lines"], _g["deliv"], "shipping_revenue")
            # 배송수입에 대한 VAT도 라인 비례 배분
            _alloc_to_lines(_g["lines"], _g["deliv"] * Decimal("10") / Decimal("110"), "vat")

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

    result = []
    for pid, b in by_product.items():
        p = product_map.get(pid)
        if not p:
            continue
        net = b["revenue"] - b["cost"] - b["commission"] - b["ad_spend"] - b["shipping"] - b["vat"]
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


def _classify_channel(ch: Channel) -> tuple[str, str, bool]:
    """채널 → (회사, leaf 라벨, 순이익 산정가능 여부)"""
    company = ch.company or "미지정"
    code = ch.code or ""
    if code.startswith("COUPANG"):
        if ch.channel_type == "consignment":  # 로켓배송 (위탁/수동)
            seg, has_profit = "쿠팡 로켓배송", False
        else:  # 윙 / 로켓그로스
            seg, has_profit = "쿠팡 로켓그로스·윙", True
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
    }


def _add_net(block: dict, net: str | None, revenue: Decimal) -> None:
    """집계 net은 '측정가능分 합' (기존 대시보드 의미론).
    위탁(로켓배송 등 net None) 자식은 net/측정매출에 미반영 — 매출/광고비는
    호출부에서 별도 누적됨. 측정가능 자식이 하나도 없을 때만 net/rate "—".
    """
    if net is None:
        return
    block["net_profit"] += Decimal(net)
    block["measurable_rev"] += revenue


def _finalize(kind: str, company: str | None, label: str, b: dict) -> dict:
    if b["measurable_rev"] > 0:
        net = b["net_profit"]
        rate = (net / b["measurable_rev"] * 100).quantize(Decimal("0.01"))
        net_s, rate_s = str(net), str(rate)
    else:
        net_s, rate_s = None, None  # 측정가능 매출 0 (예: 순수 로켓배송 leaf)
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
        for blk in (lb, cb, total):
            blk["revenue"] += rev
            blk["product_revenue"] += prod_rev
            blk["shipping_revenue"] += ship_rev
            blk["ad_spend"] += Decimal(r["ad_spend"])
            blk["order_count"] += r["order_count"]
            _add_net(blk, r.get("net_profit"), rev)

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
