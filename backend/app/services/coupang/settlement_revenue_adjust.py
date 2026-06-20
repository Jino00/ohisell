# settlement_revenue_adjust.py — net_profit 매출기준 정산화 Harness (트랙 revenue-wing-truth S4)
# 정산 인식된 옵션라인(D-11, codex P1#2)의 net_profit '매출 기준'을 쿠팡 정산(실지급,
# settlement_revenue_source)으로 교체하는 계정 단위 가산 보정. RG 플립·비-PA·S2 canonical과 동일한
# "summary만 조정·by_option 불변·읽기전용" 패턴(D-12, D-14).
#
# 보정식(라인 그레인, Jino 결정 B):
#   adjustment = Σ_라인L∈(정산∩active) ( settlement_net(L) − our_net_rev(L) )
#   our_net_rev(L) = active_revenue(L) − unit_price[vid] × return_qty(L)
#     · L=(order_id, vendor_item_id). 정산에 존재(=성숙)하고 우리 active 주문라인인 라인만 스왑.
#     · return_qty(L)=그 라인 반품수량(withdrawn=False·requested_at∈윈도우). 정산net이 이미 환불
#       반영 → active 라인의 반품차감을 라인 단위로 되돌려 이중차감 0(codex P1#1).
#   net_profit_S4 = net_profit_current + adjustment
# 성숙(D-11, 라인 그레인): 정산에 라인이 존재 = 쿠팡이 그 라인을 정산 인식. 미정산 라인(최근 ~8일
#   정산지연)은 보정 대상 아님 → 현행 주문기반 폴백(과소계상 0). 정산만 있고 active 아닌 라인(취소·
#   미동기)은 스왑 안 함(매출에 안 잡힘). 부분 옵션 정산도 정산된 라인만 정확히 스왑(codex P1#2 해소).
# 범위(D-13): 3P(WING1·WING2)만. RG는 정산구조 상이 → 매출=Wing GMV(S2) 유지.
# 등가성(account=None == ΣWING): 계정별 독립 산출 후 합산.
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Channel, CoupangReturnItem, Order
from app.services.cafe24_status_mapper import REVENUE_EXCLUDED
from app.services.coupang.revenue_fee_source import COUPANG_3P_CODES
from app.services.coupang.settlement_revenue_source import settlement_net_by_line

_Z = Decimal("0")
# REVENUE_EXCLUDED(취소/반품/입금전)는 cafe24_status_mapper 정본 사용 — intelligence._agg_orders와
# 동일 매출제외 도메인 보장(별도 정의 시 드리프트 위험).


def _f(v) -> Decimal:
    if v is None:
        return _Z
    return v if isinstance(v, Decimal) else Decimal(str(v))


# ── 순수 산식 (DB 없음, 테스트 용이) ──
def compute_line_adjustment(
    settlement_net_by_line: dict[tuple[str, str], Decimal],
    active_revenue_by_line: dict[tuple[str, str], Decimal],
    return_qty_by_line: dict[tuple[str, str], int],
    unit_price_by_vid: dict[str, Decimal],
) -> dict:
    """정산∩active 라인만 스왑. 보정 = Σ(settlement_net(L) − our_net_rev(L)).

    정산에만 있고 active 아닌 라인(취소·미동기)은 스킵(매출 미반영). active만 있고 미정산 라인은
    스킵(폴백). 반환 {adjustment, matured_lines, settlement_net_matured, our_net_rev_matured}.
    """
    adj = _Z
    matured = 0
    settle_sum = _Z
    our_sum = _Z
    for line, srev in settlement_net_by_line.items():
        if line not in active_revenue_by_line:
            continue  # 정산됐으나 우리 매출엔 없음(취소/미동기) → 스왑 안 함
        arev = active_revenue_by_line[line]
        rq = return_qty_by_line.get(line, 0)
        unit = _f(unit_price_by_vid.get(line[1], _Z))
        our_net = arev - unit * Decimal(int(rq or 0))
        adj += srev - our_net
        matured += 1
        settle_sum += srev
        our_sum += our_net
    return {
        "adjustment": adj,
        "matured_lines": matured,
        "settlement_net_matured": settle_sum,
        "our_net_rev_matured": our_sum,
    }


# ── DB 조립 ──
def _active_revenue_by_line(
    db: Session, dfrom: date, dto: date, channel_ids: list[int] | None
) -> dict[tuple[str, str], Decimal]:
    """active 주문라인 (order_number, vendor_item_id) → 매출(Σ selling_price=라인총액, _agg_orders 계약).

    status∉REVENUE_EXCLUDED, order_date∈윈도우. 쿠팡 line_id=""라 (order, vid) 1:1.
    """
    start = datetime.combine(dfrom, time.min)
    end = datetime.combine(dto, time.max)
    q = (
        db.query(Order.order_number, Order.platform_product_id, func.sum(Order.selling_price))
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
    rows = q.group_by(Order.order_number, Order.platform_product_id).all()
    return {(str(onum), str(vid)): _f(rev) for onum, vid, rev in rows}


def _return_qty_by_line(
    db: Session, dfrom: date, dto: date, account_keys: list[str]
) -> dict[tuple[str, str], int]:
    """반품수량 cancel_count per (order_id, vendor_item_id). withdrawn=False·requested_at∈윈도우.

    매출제외(REVENUE_EXCLUDED) 상호배타는 호출부가 active 라인에만 되돌림을 적용해 자동 보장
    (active 라인은 정의상 비-제외). account_keys로 계정 스코프(반품행은 account_key 컬럼 보유).
    """
    if not account_keys:
        return {}
    start = datetime.combine(dfrom, time.min)
    end = datetime.combine(dto, time.max)
    rows = (
        db.query(
            CoupangReturnItem.order_id,
            CoupangReturnItem.vendor_item_id,
            func.sum(CoupangReturnItem.cancel_count),
        )
        .filter(
            CoupangReturnItem.withdrawn.is_(False),
            CoupangReturnItem.account_key.in_(account_keys),
            CoupangReturnItem.requested_at >= start,
            CoupangReturnItem.requested_at <= end,
        )
        .group_by(CoupangReturnItem.order_id, CoupangReturnItem.vendor_item_id)
        .all()
    )
    return {(str(oid), str(vid)): int(qty or 0) for oid, vid, qty in rows}


def _scope_account_keys(acc: dict) -> list[str]:
    """정산화 대상 3P account_key. account=None → [WING1, WING2]. 특정 3P키 → [키]. 비-3P → []."""
    ak = acc.get("account_key")
    if ak is None:
        return list(COUPANG_3P_CODES)
    return [ak] if ak in COUPANG_3P_CODES else []


def _channel_ids_for_account(db: Session, account_key: str) -> list[int]:
    """3P account_key(=채널 code)의 채널 id(3P 주문 도메인, RG 채널 제외)."""
    return [cid for (cid,) in
            db.query(Channel.id)
            .filter(Channel.code == account_key, Channel.platform == "coupang")
            .all()]


def settlement_revenue_adjustment(
    db: Session,
    dfrom: date,
    dto: date,
    acc: dict,
    unit_price_by_vid: dict[str, Decimal],
) -> dict:
    """계정 단위 정산화 보정액 산출(원칙18-6: intelligence가 unit_price 주입).

    ★등가성(account=None == ΣWING): 각 3P 계정을 독립 산출해 합산.
    데이터 없음/비-3P/미정산 → adjustment 0(회귀 가드).
    """
    keys = _scope_account_keys(acc)
    if not keys:
        return {"adjustment": _Z, "matured_lines": 0,
                "settlement_net_matured": _Z, "our_net_rev_matured": _Z,
                "basis": "skipped: non-3p account (D-13)"}

    adjustment = _Z
    matured_lines = 0
    settle_matured = _Z
    our_matured = _Z
    for ak in keys:
        ch_ids = _channel_ids_for_account(db, ak)
        if not ch_ids:
            continue
        settle = settlement_net_by_line(db, dfrom, dto, [ak])
        active = _active_revenue_by_line(db, dfrom, dto, ch_ids)
        retq = _return_qty_by_line(db, dfrom, dto, [ak])
        one = compute_line_adjustment(settle, active, retq, unit_price_by_vid)
        adjustment += one["adjustment"]
        matured_lines += one["matured_lines"]
        settle_matured += one["settlement_net_matured"]
        our_matured += one["our_net_rev_matured"]
    return {
        "adjustment": adjustment,
        "matured_lines": matured_lines,
        "settlement_net_matured": settle_matured,
        "our_net_rev_matured": our_matured,
        "basis": "settlement(SALE-REFUND) replaces order-based net revenue per settled line (D-11~D-13)",
    }
