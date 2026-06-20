# settlement_revenue_adjust.py — net_profit 매출기준 정산화 Harness (트랙 revenue-wing-truth S4)
# 성숙 정산일(D-11)의 net_profit '매출 기준'을 쿠팡 정산(실지급, settlement_revenue_source)으로
# 교체하는 계정 단위 가산 보정. RG 플립(apply_rg_net_profit_flip)·비-PA·S2 canonical과 동일한
# "summary만 조정·by_option 불변·읽기전용" 패턴(D-12, D-14).
#
# 보정식(계정 단위, Jino 결정 B):
#   adjustment = Σ_성숙일 ( settlement_net_day − our_net_rev_day )
#   our_net_rev_day = active_revenue_day − return_deduction_day
#     · active_revenue_day = Σ active 주문(status∉REVENUE_EXCLUDED)의 selling_price, order_date=day
#     · return_deduction_day = Σ_vid unit_price_vid × return_qty(order_date=day) — 그 날 주문의 반품차감
#       (settlement_net은 이미 환불 반영 → 성숙일 한정 되돌림으로 이중차감 방지)
#   net_profit_S4 = net_profit_current + adjustment   (성숙일만; 미성숙·폴백일은 0 기여 → 불변)
#
# 성숙 판정(D-11): 그 날 우리 active 주문번호 ⊆ 그 날 정산 등장 주문번호(전부 정산 인식됨). 부분/미성숙
#   (최근 ~8일, 쿠팡 정산 지연)은 폴백 → 보정 0 → 현행 주문기반 그대로(과소계상 0, S2 게이트 동일).
# 범위(D-13): 3P(WING1·WING2)만. RG는 정산구조 상이 → 매출=Wing GMV(S2) 유지.
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.models import Channel, CoupangReturnItem, Order
from app.services.cafe24_status_mapper import REVENUE_EXCLUDED
from app.services.coupang.revenue_fee_source import COUPANG_3P_CODES
from app.services.coupang.settlement_revenue_source import (
    settled_order_ids_by_day,
    settlement_net_revenue_by_day,
)

_Z = Decimal("0")
# REVENUE_EXCLUDED(취소/반품/입금전)는 cafe24_status_mapper 정본을 그대로 사용 —
# intelligence._agg_orders/_agg_returns와 동일 매출제외 도메인 보장(별도 정의 시 드리프트 위험).


def _f(v) -> Decimal:
    if v is None:
        return _Z
    return v if isinstance(v, Decimal) else Decimal(str(v))


# ── 순수 로직 (DB 없음, 테스트 용이) ──
def day_matured(active_order_ids: set[str], settled_order_ids: set[str]) -> bool:
    """D-11: 그 날 active 주문이 비어있지 않고 전부 정산에 등장하면 성숙."""
    return bool(active_order_ids) and active_order_ids <= settled_order_ids


def compute_settlement_adjustment(
    settlement_net_by_day: dict[date, Decimal],
    our_net_rev_by_day: dict[date, Decimal],
    matured_days: set[date],
) -> Decimal:
    """성숙일만 보정 합산 = Σ(settlement_net − our_net_rev). 미성숙일은 기여 0(불변)."""
    adj = _Z
    for d in matured_days:
        adj += settlement_net_by_day.get(d, _Z) - our_net_rev_by_day.get(d, _Z)
    return adj


# ── DB 조립 ──
def _active_orders_by_day(
    db: Session, dfrom: date, dto: date, channel_ids: list[int] | None
) -> tuple[dict[date, Decimal], dict[date, set[str]]]:
    """active 주문을 order_date 일자별로: (매출합 dict, 주문번호집합 dict).

    매출=Σ(selling_price)(=라인총액, _agg_orders S2 계약과 동일). 주문집합=성숙도 판정용.
    """
    start = datetime.combine(dfrom, time.min)
    end = datetime.combine(dto, time.max)
    day = func.date(Order.order_date)
    q = (
        db.query(day, Order.order_number, Order.selling_price)
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
    rev_by_day: dict[date, Decimal] = {}
    ids_by_day: dict[date, set[str]] = {}
    for d, onum, sp in q.all():
        dd = d if isinstance(d, date) else date.fromisoformat(str(d))
        rev_by_day[dd] = rev_by_day.get(dd, _Z) + _f(sp)
        ids_by_day.setdefault(dd, set()).add(str(onum))
    return rev_by_day, ids_by_day


def _return_deduction_by_day(
    db: Session, dfrom: date, dto: date, channel_ids: list[int] | None,
    unit_price_by_vid: dict[str, Decimal],
) -> dict[date, Decimal]:
    """그 날(order_date) active 주문의 반품차감 = Σ_vid unit_price_vid × return_qty.

    net_profit이 실제 차감한 것과 일치시켜 되돌림(이중차감 방지): _agg_returns와 동일하게
    requested_at∈[dfrom,dto] · withdrawn=False · 매출제외(REVENUE_EXCLUDED) 주문라인 제외(상호배타).
    단 일자 그레인은 반품의 원주문 order_date(매출 축과 정렬). unit_price는 윈도우 평균(주입).
    """
    start = datetime.combine(dfrom, time.min)
    end = datetime.combine(dto, time.max)
    day = func.date(Order.order_date)
    q = (
        db.query(day, CoupangReturnItem.vendor_item_id, func.sum(CoupangReturnItem.cancel_count))
        .join(
            Order,
            and_(
                Order.order_number == CoupangReturnItem.order_id,
                Order.platform_product_id == CoupangReturnItem.vendor_item_id,
            ),
        )
        .join(Channel, Order.channel_id == Channel.id)
        .filter(
            CoupangReturnItem.withdrawn.is_(False),
            CoupangReturnItem.requested_at >= start,
            CoupangReturnItem.requested_at <= end,
            Channel.platform == "coupang",
            Order.status.notin_(tuple(REVENUE_EXCLUDED)),
        )
    )
    if channel_ids is not None:
        q = q.filter(Order.channel_id.in_(channel_ids))
    rows = q.group_by(day, CoupangReturnItem.vendor_item_id).all()
    out: dict[date, Decimal] = {}
    for d, vid, qty in rows:
        dd = d if isinstance(d, date) else date.fromisoformat(str(d))
        unit = _f(unit_price_by_vid.get(str(vid), _Z))
        out[dd] = out.get(dd, _Z) + unit * Decimal(int(qty or 0))
    return out


def _scope_account_keys(acc: dict) -> list[str]:
    """정산화 대상 3P account_key 목록. account=None → [WING1, WING2]. 특정 3P키 → [키].
    RG 등 비-3P 키 → [](보정 0, D-13)."""
    ak = acc.get("account_key")
    if ak is None:
        return list(COUPANG_3P_CODES)
    return [ak] if ak in COUPANG_3P_CODES else []


def _channel_ids_for_account(db: Session, account_key: str) -> list[int]:
    """3P account_key(=채널 code)의 채널 id. 3P 주문 도메인 — 같은 code 채널만(RG 채널 제외)."""
    return [cid for (cid,) in
            db.query(Channel.id)
            .filter(Channel.code == account_key, Channel.platform == "coupang")
            .all()]


def _adjust_one_account(
    db: Session, dfrom: date, dto: date, account_key: str,
    channel_ids: list[int], unit_price_by_vid: dict[str, Decimal],
) -> dict:
    """단일 3P 계정의 정산화 보정. 계정별로 독립 산출 → 상위에서 합산(등가성 계약 보장:
    account=None 보정 == Σ 계정 보정, 성숙 판정을 계정×일자 단위로 분리하므로)."""
    settle_net = settlement_net_revenue_by_day(db, dfrom, dto, [account_key])
    settled_ids = settled_order_ids_by_day(db, dfrom, dto, [account_key])
    active_rev, active_ids = _active_orders_by_day(db, dfrom, dto, channel_ids)
    ret_ded = _return_deduction_by_day(db, dfrom, dto, channel_ids, unit_price_by_vid)

    settled_by_day = {d: s for (_ak, d), s in settled_ids.items()}
    settle_net_by_day = {d: v for (_ak, d), v in settle_net.items()}
    matured = {d for d, ids in active_ids.items()
               if day_matured(ids, settled_by_day.get(d, set()))}
    our_net_rev = {d: active_rev.get(d, _Z) - ret_ded.get(d, _Z) for d in active_rev}
    adjustment = compute_settlement_adjustment(settle_net_by_day, our_net_rev, matured)
    return {
        "adjustment": adjustment,
        "matured_days": len(matured),
        "settlement_net_matured": sum((settle_net_by_day.get(d, _Z) for d in matured), _Z),
        "our_net_rev_matured": sum((our_net_rev.get(d, _Z) for d in matured), _Z),
    }


def settlement_revenue_adjustment(
    db: Session,
    dfrom: date,
    dto: date,
    acc: dict,
    unit_price_by_vid: dict[str, Decimal],
) -> dict:
    """계정 단위 정산화 보정액 산출(원칙18-6: intelligence가 unit_price 주입).

    ★등가성(account=None == ΣWING): 각 3P 계정을 독립 산출해 합산. 성숙 판정을 계정×일자로
    분리하므로 '한 계정만 성숙한 날'이 집계뷰에서 누락·이중계산되지 않는다.
    반환: {adjustment, matured_days, settlement_net_matured, our_net_rev_matured, basis}.
    데이터 없음/비-3P → adjustment 0(회귀 가드).
    """
    keys = _scope_account_keys(acc)
    if not keys:  # 비-3P 계정 — 정산화 대상 아님(D-13)
        return {"adjustment": _Z, "matured_days": 0,
                "settlement_net_matured": _Z, "our_net_rev_matured": _Z,
                "basis": "skipped: non-3p account (D-13)"}

    adjustment = _Z
    matured_days = 0
    settle_net_matured = _Z
    our_net_matured = _Z
    for ak in keys:
        ch_ids = _channel_ids_for_account(db, ak)
        if not ch_ids:
            continue  # 채널 미존재 → 그 계정 0(회귀 가드)
        one = _adjust_one_account(db, dfrom, dto, ak, ch_ids, unit_price_by_vid)
        adjustment += one["adjustment"]
        matured_days += one["matured_days"]
        settle_net_matured += one["settlement_net_matured"]
        our_net_matured += one["our_net_rev_matured"]
    return {
        "adjustment": adjustment,
        "matured_days": matured_days,
        "settlement_net_matured": settle_net_matured,
        "our_net_rev_matured": our_net_matured,
        "basis": "settlement(SALE-REFUND) replaces order-based net revenue on matured days (D-11~D-13)",
    }
