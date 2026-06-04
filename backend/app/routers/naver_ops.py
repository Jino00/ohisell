# naver_ops.py — 네이버 스마트스토어 운영 패널 (매출/이익 집계)
# GET /api/naver/ops/sales-summary
# 이익 = 매출 − commission_amount(PG수수료) − 원가 − 광고비 − shipping_cost
# 광고비: ad_costs(source LIKE 'naver%') — product_id NULL이므로 요약 카드 총합만, 상품별 미배분
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_naver_config
from app.database import get_db
from app.models import (
    AdCost,
    Channel,
    NaverSettlementCase,
    NaverSettlementDaily,
    Order,
    ProductChannelMapping,
    ProductMaster,
)
from app.services.cafe24_status_mapper import REVENUE_EXCLUDED
from app.utils.kst import kst_today

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/naver/ops", tags=["naver-ops"])

_NAVER_CONFIG_KEY = "NAVER"

_NAVER_CHANNEL_ID = 6
_Q2 = Decimal("0.01")
_Z  = Decimal("0")


def _date_range(days: int) -> tuple[date, date]:
    today = kst_today()
    if days == 0:
        return today, today
    if days == 1:
        d = today - timedelta(days=1)
        return d, d
    return today - timedelta(days=days - 1), today


def _f(v) -> Decimal:
    if v is None:
        return _Z
    return v if isinstance(v, Decimal) else Decimal(str(v))


@router.get("/sales-summary")
def sales_summary(
    days: int = Query(default=7, ge=0, le=90),
    db: Session = Depends(get_db),
):
    """네이버 스마트스토어 매출 현황 — 기간별 집계.

    반환: summary(합계) + by_product(상품명별).
    광고비는 ad_costs(naver_sa:*) 기간 총합 — 상품별 배분 없음(product_id NULL).
    """
    dfrom, dto = _date_range(days)
    start = datetime.combine(dfrom, time.min)
    end   = datetime.combine(dto,   time.max)

    # ── 1. 주문 집계 (주문번호 + platform_product_id 라인 단위) ──────
    # 라인 단위로 가져와야 건별 정산(order_id+product_id)과 매칭 가능. 이후 상품별 재집계.
    order_rows = (
        db.query(
            Order.order_number,
            Order.platform_product_id,
            func.max(Order.platform_product_name),
            func.sum(Order.selling_price * Order.quantity),
            func.sum(Order.quantity),
            func.sum(Order.commission_amount),
            func.sum(Order.shipping_cost),
        )
        .filter(
            Order.channel_id == _NAVER_CHANNEL_ID,
            Order.platform_product_id != "",
            Order.platform_product_id.isnot(None),
            Order.status.notin_(tuple(REVENUE_EXCLUDED)),
            Order.order_date >= start,
            Order.order_date <= end,
        )
        .group_by(Order.order_number, Order.platform_product_id)
        .all()
    )

    # ── 2. 광고비 (기간 총합, 상품별 배분 없음) ──────────────────────
    ad_ref_date: str | None = None
    ad_dfrom, ad_dto = dfrom, dto
    if days == 0:
        latest_ad = (
            db.query(func.max(AdCost.ad_date))
            .filter(AdCost.channel_id == _NAVER_CHANNEL_ID)
            .scalar()
        )
        if latest_ad:
            ad_dfrom = ad_dto = latest_ad
            ad_ref_date = str(latest_ad)

    total_ad_spend = _f(
        db.query(func.sum(AdCost.ad_spend))
        .filter(
            AdCost.channel_id == _NAVER_CHANNEL_ID,
            AdCost.ad_date >= ad_dfrom,
            AdCost.ad_date <= ad_dto,
        )
        .scalar()
    )

    # 검색광고(SA) RoAS — 디스플레이 제외(전환추적 없음).
    # 네이버 전환 보고서는 최근 ~15일만 보관 → 전환데이터가 있는 날짜로만 분모를 맞춰
    # 분자(전환매출)/분모(광고비)를 같은 기간으로 정렬(저평가 방지, 원칙 22).
    conv_rows = (
        db.query(AdCost.ad_date, AdCost.ad_revenue)
        .filter(
            AdCost.channel_id == _NAVER_CHANNEL_ID,
            AdCost.source == "naver_sa:conv",
            AdCost.ad_date >= ad_dfrom,
            AdCost.ad_date <= ad_dto,
        )
        .all()
    )
    conv_dates = [r[0] for r in conv_rows]
    sa_conv_revenue = sum((_f(r[1]) for r in conv_rows), _Z)

    if conv_dates:
        sa_ad_spend = _f(
            db.query(func.sum(AdCost.ad_spend))
            .filter(
                AdCost.channel_id == _NAVER_CHANNEL_ID,
                AdCost.source.like("naver_sa:%"),
                AdCost.ad_date.in_(conv_dates),
            )
            .scalar()
        )
        sa_roas = (
            str((sa_conv_revenue / sa_ad_spend).quantize(_Q2, rounding=ROUND_HALF_UP))
            if sa_ad_spend else None
        )
        sa_conv_from = str(min(conv_dates))
        sa_conv_to = str(max(conv_dates))
    else:
        sa_ad_spend = _Z
        sa_roas = None
        sa_conv_from = sa_conv_to = None

    # ── 3. 원가 조회 (product_channel_mapping → product_master) ───────
    all_pids = {str(r[1]) for r in order_rows if r[1]}
    cost_rows = (
        db.query(ProductChannelMapping.channel_product_id, ProductMaster.cost_price, ProductMaster.id)
        .join(ProductMaster, ProductChannelMapping.product_id == ProductMaster.id)
        .filter(
            ProductChannelMapping.channel_id == _NAVER_CHANNEL_ID,
            ProductChannelMapping.is_active.is_(True),
            ProductChannelMapping.channel_product_id.in_(list(all_pids)),
        )
        .all()
    ) if all_pids else []
    cost_candidates: dict[str, list] = {}
    for cpid, cp, pid in cost_rows:
        cost_candidates.setdefault(str(cpid), []).append((cp, pid))
    cost_map: dict[str, Decimal] = {}
    for pid, cands in cost_candidates.items():
        costed = [(cp, p) for cp, p in cands if cp and cp > 0]
        chosen = min(costed or cands, key=lambda x: x[1])[0]
        if chosen:
            cost_map[pid] = _f(chosen)

    # ── 3b. 실측 수수료 맵 (건별 정산 settle/case, D-6) ────────────────
    # (order_id, product_id) → 실측 판매자부담 수수료(양수). PROD_ORDER만, 수수료 음수→부호반전.
    # 같은 (order_id, product_id)에 productOrderId가 여럿이면 group_by sum으로 합산(라이브 표본
    # 1.4%, 전부 동시정산이라 정확). 정산후취소 행은 음수 보정되어 sum 순효과로 반영된다.
    # SQLite 변수 한계(구버전 999) 회피 — order_id IN을 청크로 분할 조회.
    all_order_ids = list({str(r[0]) for r in order_rows if r[0]})
    actual_fee_map: dict[tuple[str, str], Decimal] = {}
    _CHUNK = 800
    for i in range(0, len(all_order_ids), _CHUNK):
        chunk = all_order_ids[i:i + _CHUNK]
        case_rows = (
            db.query(
                NaverSettlementCase.order_id,
                NaverSettlementCase.product_id,
                func.sum(
                    NaverSettlementCase.total_pay_commission
                    + NaverSettlementCase.selling_interlock_commission
                    + NaverSettlementCase.free_installment_commission
                ),
            )
            .filter(
                NaverSettlementCase.product_order_type == "PROD_ORDER",
                NaverSettlementCase.order_id.in_(chunk),
                NaverSettlementCase.product_id.isnot(None),
            )
            .group_by(NaverSettlementCase.order_id, NaverSettlementCase.product_id)
            .all()
        )
        for oid, pid, comm_sum in case_rows:
            if pid is None:
                continue
            # order_id 기준 청크라 같은 키는 한 청크에만 등장 — 누적도 안전.
            key = (str(oid), str(pid))
            actual_fee_map[key] = actual_fee_map.get(key, _Z) + (-_f(comm_sum))  # 음수 합 → 양수 fee

    # ── 4. 라인 단위 매칭(실측/예상) → 상품별 집계 ────────────────────
    prod_acc: dict[str, dict] = {}
    total_rev = total_fee = total_cost = total_ship = _Z
    settled_lines = 0   # 실측 수수료 적용 라인 수
    est_lines = 0       # 주문API 예상 수수료 폴백 라인 수

    for order_no, pid, pname, rev, qty, commission, shipping in order_rows:
        pid_s     = str(pid)
        rev       = _f(rev)
        qty_      = int(qty or 0)
        ship      = _f(shipping)
        # 하이브리드 폴백(D-6): 실측 있으면 실측, 없으면 주문API 예상 수수료.
        # 주의: 같은 (order,product)에 분할 productOrderId가 있고 일부만 정산되면 실측이
        # 미정산분을 가릴 수 있음(라이브 표본상 부분정산 0건). 발생 시 과소 추정.
        actual    = actual_fee_map.get((str(order_no), pid_s))
        if actual is not None:
            fee = actual
            settled_lines += 1
        else:
            fee = _f(commission)
            est_lines += 1
        unit_cost = cost_map.get(pid_s, _Z)
        cost      = unit_cost * qty_

        total_rev  += rev
        total_fee  += fee
        total_cost += cost
        total_ship += ship

        acc = prod_acc.get(pid_s)
        if acc is None:
            acc = prod_acc[pid_s] = {
                "name": pname or pid_s, "rev": _Z, "fee": _Z,
                "cost": _Z, "ship": _Z, "settled": 0, "est": 0,
            }
        acc["rev"]  += rev
        acc["fee"]  += fee
        acc["cost"] += cost
        acc["ship"] += ship
        if actual is not None:
            acc["settled"] += 1
        else:
            acc["est"] += 1
        if pname and acc["name"] == pid_s:
            acc["name"] = pname

    by_product = []
    for pid_s, acc in prod_acc.items():
        rev = acc["rev"]
        profit = rev - acc["fee"] - acc["cost"] - acc["ship"]
        by_product.append({
            "product_name":  acc["name"],
            "platform_id":   pid_s,
            "revenue":       str(rev.quantize(_Q2)),
            "fee":           str(acc["fee"].quantize(_Q2)),
            "fee_actual":    acc["est"] == 0 and acc["settled"] > 0,  # 전부 실측이면 True
            "cost":          str(acc["cost"].quantize(_Q2)),
            "shipping":      str(acc["ship"].quantize(_Q2)),
            "profit":        str(profit.quantize(_Q2)),
            "profit_rate":   str((profit / rev * 100).quantize(_Q2)) if rev else None,
        })

    by_product.sort(key=lambda x: -Decimal(x["revenue"]))

    # ── 5. 요약 (광고비 차감해서 전체 이익 계산) ─────────────────────
    total_profit = total_rev - total_fee - total_cost - total_ad_spend - total_ship
    profit_rate  = (total_profit / total_rev * 100).quantize(_Q2) if total_rev else None

    return {
        "period":       {"from": str(dfrom), "to": str(dto)},
        "ad_ref_date":  ad_ref_date,
        "summary": {
            "revenue":      str(total_rev.quantize(_Q2)),
            "fee":          str(total_fee.quantize(_Q2)),
            "cost":         str(total_cost.quantize(_Q2)),
            "ad_spend":     str(total_ad_spend.quantize(_Q2)),
            "shipping":     str(total_ship.quantize(_Q2)),
            "profit":       str(total_profit.quantize(_Q2)),
            "profit_rate":  str(profit_rate) if profit_rate is not None else None,
            "sa_conv_revenue": str(sa_conv_revenue.quantize(_Q2)),
            "sa_ad_spend":     str(sa_ad_spend.quantize(_Q2)),
            "sa_roas":         sa_roas,
            "sa_conv_from":    sa_conv_from,
            "sa_conv_to":      sa_conv_to,
            "fee_settled_lines": settled_lines,   # 실측 수수료 적용 주문라인 수 (D-6)
            "fee_est_lines":     est_lines,       # 주문API 예상 수수료 폴백 라인 수
        },
        "by_product": by_product,
    }


# ════════════════════════════════════════════════════════════════════
# N1 정산 — 일별 정산 내역 (실측 수수료·정산금액)
# ════════════════════════════════════════════════════════════════════

def _upsert_settlement(db: Session, rows: list[dict]) -> int:
    """일별 정산 행 upsert (settle_expect_date 그레인). 반환=반영 건수."""
    n = 0
    for r in rows:
        ed = r.get("settle_expect_date")
        if not ed:
            continue
        existing = (
            db.query(NaverSettlementDaily)
            .filter(NaverSettlementDaily.settle_expect_date == ed)
            .first()
        )
        target = existing or NaverSettlementDaily(settle_expect_date=ed)
        target.settle_basis_start = r.get("settle_basis_start")
        target.settle_basis_end = r.get("settle_basis_end")
        target.settle_complete_date = r.get("settle_complete_date")
        target.settle_amount = r.get("settle_amount") or _Z
        target.pay_settle_amount = r.get("pay_settle_amount") or _Z
        target.commission_amount = r.get("commission_amount") or _Z
        target.benefit_amount = r.get("benefit_amount") or _Z
        target.payholdback_amount = r.get("payholdback_amount") or _Z
        target.settle_method = r.get("settle_method")
        if not existing:
            db.add(target)
        n += 1
    db.commit()
    return n


@router.post("/settlement/sync")
def sync_settlement(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """네이버 일별 정산 내역을 라이브 조회 → DB 적재 (트랙 N1).

    정산 예정일 기준 최근 days일. 서버 IP 화이트리스트 필요(로컬은 권한오류 가능).
    """
    cfg = get_naver_config(_NAVER_CONFIG_KEY)
    if not cfg:
        raise HTTPException(status_code=503, detail="네이버 설정 없음")

    from app.clients.naver import NaverClient
    dto = kst_today()
    dfrom = dto - timedelta(days=days - 1)
    try:
        rows = NaverClient(cfg).fetch_daily_settlement(dfrom, dto)
    except Exception as e:
        log.error("네이버 정산 조회 실패: %s", e)
        raise HTTPException(status_code=502, detail=f"네이버 정산 API 오류: {e}")

    n = _upsert_settlement(db, rows)
    return {"synced": n, "date_from": str(dfrom), "date_to": str(dto)}


@router.get("/settlement")
def get_settlement(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """적재된 일별 정산 내역 + 합계 (정산 예정일 기준 최근 days일)."""
    dto = kst_today()
    dfrom = dto - timedelta(days=days - 1)
    rows = (
        db.query(NaverSettlementDaily)
        .filter(
            NaverSettlementDaily.settle_expect_date >= dfrom.isoformat(),
            NaverSettlementDaily.settle_expect_date <= dto.isoformat(),
        )
        .order_by(NaverSettlementDaily.settle_expect_date.desc())
        .all()
    )

    def _f(v) -> Decimal:
        return v if isinstance(v, Decimal) else Decimal(str(v or 0))

    t_settle = sum((_f(r.settle_amount) for r in rows), _Z)
    t_pay = sum((_f(r.pay_settle_amount) for r in rows), _Z)
    t_comm = sum((_f(r.commission_amount) for r in rows), _Z)
    t_benefit = sum((_f(r.benefit_amount) for r in rows), _Z)
    t_hold = sum((_f(r.payholdback_amount) for r in rows), _Z)

    return {
        "period": {"from": str(dfrom), "to": str(dto)},
        "summary": {
            "settle_amount": str(t_settle.quantize(_Q2)),
            "pay_settle_amount": str(t_pay.quantize(_Q2)),
            "commission_amount": str(t_comm.quantize(_Q2)),
            "benefit_amount": str(t_benefit.quantize(_Q2)),
            "payholdback_amount": str(t_hold.quantize(_Q2)),
        },
        "rows": [
            {
                "settle_expect_date": r.settle_expect_date,
                "settle_basis_start": r.settle_basis_start,
                "settle_basis_end": r.settle_basis_end,
                "settle_complete_date": r.settle_complete_date,
                "settle_amount": str(_f(r.settle_amount).quantize(_Q2)),
                "pay_settle_amount": str(_f(r.pay_settle_amount).quantize(_Q2)),
                "commission_amount": str(_f(r.commission_amount).quantize(_Q2)),
                "benefit_amount": str(_f(r.benefit_amount).quantize(_Q2)),
                "payholdback_amount": str(_f(r.payholdback_amount).quantize(_Q2)),
                "settle_method": r.settle_method,
            }
            for r in rows
        ],
    }


# ════════════════════════════════════════════════════════════════════
# N1·D-6 이익 정밀화 — 건별 정산 (productOrderId별 실측 수수료)
# ════════════════════════════════════════════════════════════════════

def _upsert_case_settlement(db: Session, rows: list[dict]) -> int:
    """건별 정산 행 upsert (product_order_id 그레인). 반환=반영 건수."""
    n = 0
    for r in rows:
        poid = r.get("product_order_id")
        if not poid:
            continue
        existing = (
            db.query(NaverSettlementCase)
            .filter(NaverSettlementCase.product_order_id == poid)
            .first()
        )
        target = existing or NaverSettlementCase(product_order_id=poid)
        target.order_id = r.get("order_id") or ""
        target.product_id = r.get("product_id")
        target.product_order_type = r.get("product_order_type") or ""
        target.settle_type = r.get("settle_type")
        target.product_name = r.get("product_name")
        target.pay_settle_amount = r.get("pay_settle_amount") or _Z
        target.total_pay_commission = r.get("total_pay_commission") or _Z
        target.selling_interlock_commission = r.get("selling_interlock_commission") or _Z
        target.free_installment_commission = r.get("free_installment_commission") or _Z
        target.benefit_amount = r.get("benefit_amount") or _Z
        target.settle_expect_amount = r.get("settle_expect_amount") or _Z
        target.pay_date = r.get("pay_date")
        target.settle_expect_date = r.get("settle_expect_date")
        target.settle_complete_date = r.get("settle_complete_date")
        if not existing:
            db.add(target)
        n += 1
    db.commit()
    return n


@router.post("/settlement/case/sync")
def sync_case_settlement(
    days: int = Query(default=45, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """건별 정산(실측 수수료)을 결제일 기준 최근 days일 라이브 조회 → DB 적재 (트랙 N1·D-6).

    결제일(PAY_DATE)·정산확정(SETTLED) 건만. 서버 IP 화이트리스트 필요.
    재실행 시 upsert(정산 확정되며 갱신). 기본 45일(정산 지연 흡수).
    """
    cfg = get_naver_config(_NAVER_CONFIG_KEY)
    if not cfg:
        raise HTTPException(status_code=503, detail="네이버 설정 없음")

    from app.clients.naver import NaverClient
    dto = kst_today()
    dfrom = dto - timedelta(days=days - 1)
    try:
        rows = NaverClient(cfg).fetch_case_settlement(dfrom, dto)
    except Exception as e:
        log.error("네이버 건별 정산 조회 실패: %s", e)
        raise HTTPException(status_code=502, detail=f"네이버 건별 정산 API 오류: {e}")

    n = _upsert_case_settlement(db, rows)
    return {"synced": n, "date_from": str(dfrom), "date_to": str(dto)}
