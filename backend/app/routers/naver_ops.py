# naver_ops.py — 네이버 스마트스토어 운영 패널 (매출/이익 집계)
# GET /api/naver/ops/sales-summary
# 순이익 = (상품매출 + 고객배송비 − 수수료 − 원가 − 한진물류비) ÷ 1.1 − 광고비
#   · 부가세는 통과항목 → VAT 포함 항목 전부 공급가(÷1.1)로 통일. 광고비만 이미 공급가(네이버 광고=세금계산서 별도).
#   · 고객배송비(deliveryFeeAmount)=매출 가산, 한진물류비=packageNumber 배송건×1900(비용).
#   · 수수료=건별정산 실측(미정산은 주문API 예상 폴백, D-6). 광고비·배송·물류는 요약만(상품 미배분).
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
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
_VAT_DIVISOR = Decimal("1.1")       # VAT 포함 → 공급가(부가세 제외) 환산
_HANJIN_PER_SHIPMENT = Decimal("1900")  # 한진택배 물리배송 1건당(packageNumber 기준), VAT 포함


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
    # 광고비는 네이버 광고 = 공급가(VAT 제외, 세금계산서 별도) → 공급가 통일 시 그대로.

    # ── 2b. 한진 물류비 — 물리배송 1건당 1,900 (packageNumber 기준, VAT 포함) ──
    # 메인 엔진(profit_calculator)과 동일 기준. 매출 제외 주문은 빼고, 배송 단위(패키지)당 1회.
    shipment_count = int(
        db.query(
            func.count(
                func.distinct(
                    func.coalesce(
                        func.nullif(
                            func.json_extract(Order.raw_data, "$.productOrder.packageNumber"),
                            "",
                        ),
                        Order.order_number,
                    )
                )
            )
        )
        .filter(
            Order.channel_id == _NAVER_CHANNEL_ID,
            Order.status.notin_(tuple(REVENUE_EXCLUDED)),
            Order.order_date >= start,
            Order.order_date <= end,
        )
        .scalar()
        or 0
    )
    total_logistics = _HANJIN_PER_SHIPMENT * shipment_count  # VAT 포함

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
    # 금액은 VAT 포함(소비자가·정산수수료·매입원가) 그대로 누적 → 5단계서 공급가(÷1.1) 통일.
    prod_acc: dict[str, dict] = {}
    total_rev = total_fee = total_cost = total_delivery_rev = _Z  # 전부 VAT 포함
    settled_lines = 0   # 실측 수수료 적용 라인 수
    est_lines = 0       # 주문API 예상 수수료 폴백 라인 수

    for order_no, pid, pname, rev, qty, commission, shipping in order_rows:
        pid_s     = str(pid)
        rev       = _f(rev)
        qty_      = int(qty or 0)
        ship      = _f(shipping)   # 고객이 낸 배송비(deliveryFeeAmount) = 매출 가산분
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

        total_rev           += rev
        total_fee           += fee
        total_cost          += cost
        total_delivery_rev  += ship   # 고객배송비 → 매출 가산(비용 아님)

        acc = prod_acc.get(pid_s)
        if acc is None:
            acc = prod_acc[pid_s] = {
                "name": pname or pid_s, "rev": _Z, "fee": _Z,
                "cost": _Z, "settled": 0, "est": 0,
            }
        acc["rev"]  += rev
        acc["fee"]  += fee
        acc["cost"] += cost
        if actual is not None:
            acc["settled"] += 1
        else:
            acc["est"] += 1
        if pname and acc["name"] == pid_s:
            acc["name"] = pname

    # 상품별 = 순수 상품 손익(공급가): (상품매출 − 수수료 − 원가) ÷ 1.1.
    # 고객배송비·한진물류비·광고비는 배송/계정 단위라 상품 미배분 → 요약에만 반영.
    by_product = []
    for pid_s, acc in prod_acc.items():
        rev_s  = acc["rev"] / _VAT_DIVISOR        # 공급가
        fee_s  = acc["fee"] / _VAT_DIVISOR
        cost_s = acc["cost"] / _VAT_DIVISOR
        profit = rev_s - fee_s - cost_s
        by_product.append({
            "product_name":  acc["name"],
            "platform_id":   pid_s,
            "revenue":       str(rev_s.quantize(_Q2)),
            "fee":           str(fee_s.quantize(_Q2)),
            "fee_actual":    acc["est"] == 0 and acc["settled"] > 0,  # 전부 실측이면 True
            "cost":          str(cost_s.quantize(_Q2)),
            "profit":        str(profit.quantize(_Q2)),
            "profit_rate":   str((profit / rev_s * 100).quantize(_Q2)) if rev_s else None,
        })

    by_product.sort(key=lambda x: -Decimal(x["revenue"]))

    # ── 5. 요약 — 공급가(VAT 제외) 통일 ──────────────────────────────
    # 정확한 손익: 부가세는 통과항목(매출VAT−매입VAT 상쇄) → 모든 VAT 포함 항목을 ÷1.1.
    #   순이익 = (상품매출 + 고객배송비 − 수수료 − 원가 − 한진물류비) ÷ 1.1 − 광고비(공급가)
    gross_vat_incl   = total_rev + total_delivery_rev            # 매출(상품+배송), VAT 포함
    supply_revenue   = gross_vat_incl / _VAT_DIVISOR             # 공급가 매출
    supply_fee       = total_fee / _VAT_DIVISOR
    supply_cost      = total_cost / _VAT_DIVISOR
    supply_logistics = total_logistics / _VAT_DIVISOR
    supply_delivery  = total_delivery_rev / _VAT_DIVISOR
    supply_product   = total_rev / _VAT_DIVISOR
    total_profit = supply_revenue - supply_fee - supply_cost - supply_logistics - total_ad_spend
    profit_rate  = (total_profit / supply_revenue * 100).quantize(_Q2) if supply_revenue else None

    return {
        "period":       {"from": str(dfrom), "to": str(dto)},
        "ad_ref_date":  ad_ref_date,
        "summary": {
            "revenue":          str(supply_revenue.quantize(_Q2)),   # 공급가 매출(상품+배송)
            "revenue_vat_incl": str(gross_vat_incl.quantize(_Q2)),   # VAT 포함(소비자 결제) 병기
            "product_revenue":  str(supply_product.quantize(_Q2)),   # 공급가 상품매출
            "delivery_revenue": str(supply_delivery.quantize(_Q2)),  # 공급가 고객배송비 매출
            "fee":          str(supply_fee.quantize(_Q2)),
            "cost":         str(supply_cost.quantize(_Q2)),
            "ad_spend":     str(total_ad_spend.quantize(_Q2)),       # 광고비(이미 공급가)
            "logistics":    str(supply_logistics.quantize(_Q2)),     # 한진 물류비(공급가)
            "shipment_count": shipment_count,                        # 물리배송 건수(packageNumber)
            "profit":       str(total_profit.quantize(_Q2)),
            "profit_rate":  str(profit_rate) if profit_rate is not None else None,
            "supply_basis": True,                                    # 전 금액 공급가(VAT 제외) 기준
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


# ──────────────────────────────────────────────────────────────────────────────
# N3. 고객 문의 (트랙 N3)
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/inquiries")
def get_inquiries(
    days: int = Query(default=30, ge=1, le=365),
    answered: bool | None = Query(default=None),
):
    """고객 문의 목록 라이브 조회 (최근 days일). 트랙 N3.

    answered=true이면 답변 완료, false이면 미답변, 생략 시 전체.
    DB 저장 없이 라이브 API 직접 반환.
    """
    cfg = get_naver_config(_NAVER_CONFIG_KEY)
    if not cfg:
        raise HTTPException(status_code=503, detail="네이버 설정 없음")

    from app.clients.naver import NaverClient
    dto = kst_today()
    dfrom = dto - timedelta(days=days - 1)
    try:
        rows = NaverClient(cfg).fetch_inquiries(dfrom, dto, answered)
    except Exception as e:
        log.error("네이버 고객 문의 조회 실패: %s", e)
        raise HTTPException(status_code=502, detail=f"네이버 고객 문의 API 오류: {e}")

    unanswered = sum(1 for r in rows if not r["answered"])
    return {
        "period": {"from": str(dfrom), "to": str(dto)},
        "total": len(rows),
        "unanswered": unanswered,
        "rows": rows,
    }


# ──────────────────────────────────────────────────────────────────────────────
# N5. 판매자 정보 (트랙 N5)
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/seller")
def get_seller_info():
    """판매자 계정·채널 정보 조회. 트랙 N5. DB 저장 없이 라이브 반환."""
    cfg = get_naver_config(_NAVER_CONFIG_KEY)
    if not cfg:
        raise HTTPException(status_code=503, detail="네이버 설정 없음")
    from app.clients.naver import NaverClient
    try:
        return NaverClient(cfg).fetch_seller_info()
    except Exception as e:
        log.error("네이버 판매자 정보 조회 실패: %s", e)
        raise HTTPException(status_code=502, detail=f"네이버 판매자 정보 API 오류: {e}")


@router.get("/products")
def get_products(
    status: str | None = Query(default=None, description="SALE,SUSPENSION,CLOSE,UNADMISSION,PROHIBITION 콤마 구분"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=500, ge=1, le=500),
):
    """상품 목록 조회. 트랙 N4. DB 저장 없이 라이브 반환."""
    cfg = get_naver_config(_NAVER_CONFIG_KEY)
    if not cfg:
        raise HTTPException(status_code=503, detail="네이버 설정 없음")
    from app.clients.naver import NaverClient
    status_types = [s.strip() for s in status.split(",")] if status else None
    try:
        return NaverClient(cfg).search_products(status_types=status_types, page=page, size=size)
    except Exception as e:
        log.error("네이버 상품 목록 조회 실패: %s", e)
        raise HTTPException(status_code=502, detail=f"네이버 상품 API 오류: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# N6. 발주/발송 처리 (쓰기 — dry_run+confirm 이중확인). 트랙 N6.
#   스펙 출처: docs/references/14_naver_order_write_apis.md (전부 POST)
#   안전: dry_run=true(기본)면 네이버에 전송하지 않고 보낼 body만 구성·반환.
# ──────────────────────────────────────────────────────────────────────────────

# 택배사 코드 + 송장번호가 필수인 배송방법 (택배 계열)
_DISPATCH_METHODS_NEED_TRACKING = {"DELIVERY"}
_VALID_DELIVERY_METHODS = {
    "DELIVERY", "GDFW_ISSUE_SVC", "VISIT_RECEIPT", "DIRECT_DELIVERY",
    "QUICK_SVC", "NOTHING", "RETURN_DESIGNATED",
}
_VALID_DELAY_REASONS = {
    "PRODUCT_PREPARE", "CUSTOMER_REQUEST", "CUSTOM_BUILD",
    "RESERVED_DISPATCH", "OVERSEA_DELIVERY", "ETC",
}
_MAX_BATCH = 30  # confirm/dispatch 한 번 최대 30건 (API 제약)


def _now_kst_iso() -> str:
    """현재 시각을 네이버가 받는 ISO8601(+09:00) 형식으로."""
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%dT%H:%M:%S+09:00")


_ISO_OFFSET_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}([.]\d+)?[+-]\d{2}:\d{2}$")
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _normalize_kst_datetime(value: str, field: str) -> str:
    """네이버가 받는 ISO8601(오프셋 포함)로 정규화·검증 (codex P2-2).

    'YYYY-MM-DD'(date-only)는 당일 마감(23:59:59+09:00)으로 승격. 오프셋 포함
    완전한 ISO8601이면 그대로. 그 외(UTC·오프셋 없음·malformed)는 400으로 거부
    → 라이브 400 대신 사전 차단.
    """
    v = (value or "").strip()
    if not v:
        raise HTTPException(status_code=400, detail=f"{field} 누락")
    if _DATE_ONLY_RE.match(v):
        return f"{v}T23:59:59+09:00"
    if _ISO_OFFSET_RE.match(v):
        return v
    raise HTTPException(status_code=400, detail=f"{field}는 ISO8601(+09:00) 형식이어야 합니다: '{v}'")


def _raise_naver_write_error(action: str, result: dict):
    """쓰기 실패를 구조화된 detail로 surface (codex P2-3). naver_data에 건별 실패 포함."""
    raise HTTPException(status_code=502, detail={
        "message": f"{action} 실패",
        "naver_status": result.get("status"),
        "naver_error": result.get("error"),
        "naver_data": result.get("data"),
    })


class ConfirmRequest(BaseModel):
    product_order_ids: list[str] = Field(..., min_length=1)


class DispatchItem(BaseModel):
    product_order_id: str
    delivery_method: str
    delivery_company_code: str | None = None
    tracking_number: str | None = None
    dispatch_date: str | None = None  # 미지정 시 현재 KST


class DispatchRequest(BaseModel):
    items: list[DispatchItem] = Field(..., min_length=1)


class DelayRequest(BaseModel):
    product_order_id: str
    dispatch_due_date: str           # ISO8601 (+09:00)
    delayed_dispatch_reason: str     # 코드
    dispatch_delayed_detailed_reason: str = ""


def _get_naver_client():
    cfg = get_naver_config(_NAVER_CONFIG_KEY)
    if not cfg:
        raise HTTPException(status_code=503, detail="네이버 설정 없음")
    from app.clients.naver import NaverClient
    return NaverClient(cfg)


@router.get("/orders/pending")
def get_pending_orders(days: int = Query(default=14, ge=1, le=90)):
    """발주확인 대기·발송 대기 주문 라이브 조회 (트랙 N6). DB 저장 없이 라이브 반환.

    변경상태 기반이라 days 창 밖 미발송 건은 누락 가능(기본 14일).
    """
    try:
        return _get_naver_client().fetch_pending_orders(days=days)
    except HTTPException:
        raise
    except Exception as e:
        log.error("네이버 미발송 주문 조회 실패: %s", e)
        raise HTTPException(status_code=502, detail=f"네이버 주문 조회 오류: {e}")


@router.post("/orders/confirm")
def confirm_orders(req: ConfirmRequest, dry_run: bool = Query(default=True)):
    """발주 확인 처리 (트랙 N6). dry_run=true(기본)면 전송 안 함."""
    ids = [str(x).strip() for x in req.product_order_ids if str(x).strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="상품주문번호가 비어 있습니다.")
    if len(ids) > _MAX_BATCH:
        raise HTTPException(status_code=400, detail=f"한 번에 최대 {_MAX_BATCH}건까지 가능 (요청 {len(ids)}건)")

    would_send = {"productOrderIds": ids}
    if dry_run:
        return {"dry_run": True, "action": "confirm", "count": len(ids), "would_send": would_send}

    result = _get_naver_client().confirm_orders(ids)
    if not result.get("ok"):
        _raise_naver_write_error("발주확인", result)
    return {"dry_run": False, "action": "confirm", "count": len(ids), "naver": result.get("data")}


@router.post("/orders/dispatch")
def dispatch_orders(req: DispatchRequest, dry_run: bool = Query(default=True)):
    """발송 처리 (트랙 N6). dry_run=true(기본)면 전송 안 함.

    택배(DELIVERY) 방법이면 택배사코드+송장번호 필수. dispatch_date 미지정 시 현재 KST.
    """
    if len(req.items) > _MAX_BATCH:
        raise HTTPException(status_code=400, detail=f"한 번에 최대 {_MAX_BATCH}건까지 가능 (요청 {len(req.items)}건)")

    naver_items: list[dict] = []
    errors: list[str] = []
    for idx, it in enumerate(req.items):
        poid = (it.product_order_id or "").strip()
        method = (it.delivery_method or "").strip()
        if not poid:
            errors.append(f"{idx+1}번: 상품주문번호 누락")
            continue
        if method not in _VALID_DELIVERY_METHODS:
            errors.append(f"{poid}: 알 수 없는 배송방법 '{method}'")
            continue
        company = (it.delivery_company_code or "").strip()
        tracking = (it.tracking_number or "").strip()
        if method in _DISPATCH_METHODS_NEED_TRACKING and (not company or not tracking):
            errors.append(f"{poid}: 택배 발송은 택배사코드+송장번호 필수")
            continue
        item: dict = {"productOrderId": poid, "deliveryMethod": method}
        if company:
            item["deliveryCompanyCode"] = company
        if tracking:
            item["trackingNumber"] = tracking
        raw_date = (it.dispatch_date or "").strip()
        item["dispatchDate"] = _normalize_kst_datetime(raw_date, f"{poid} 발송일") if raw_date else _now_kst_iso()
        naver_items.append(item)

    if errors:
        raise HTTPException(status_code=400, detail="검증 실패: " + " / ".join(errors))

    would_send = {"dispatchProductOrders": naver_items}
    if dry_run:
        return {"dry_run": True, "action": "dispatch", "count": len(naver_items), "would_send": would_send}

    result = _get_naver_client().dispatch_orders(naver_items)
    if not result.get("ok"):
        _raise_naver_write_error("발송처리", result)
    return {"dry_run": False, "action": "dispatch", "count": len(naver_items), "naver": result.get("data")}


@router.post("/orders/delay")
def delay_order(req: DelayRequest, dry_run: bool = Query(default=True)):
    """발송 지연 처리 (단건, 트랙 N6). dry_run=true(기본)면 전송 안 함."""
    poid = (req.product_order_id or "").strip()
    if not poid:
        raise HTTPException(status_code=400, detail="상품주문번호 누락")
    if req.delayed_dispatch_reason not in _VALID_DELAY_REASONS:
        raise HTTPException(status_code=400, detail=f"알 수 없는 지연사유 코드 '{req.delayed_dispatch_reason}'")
    due = _normalize_kst_datetime(req.dispatch_due_date, "발송기한")

    # delay는 productOrderId가 path 파라미터 → would_send는 실제 전송 형태(path+body)로 분리 (codex P2-1)
    body = {
        "dispatchDueDate": due,
        "delayedDispatchReason": req.delayed_dispatch_reason,
        "dispatchDelayedDetailedReason": req.dispatch_delayed_detailed_reason or "",
    }
    would_send = {"path": f"/v1/pay-order/seller/product-orders/{poid}/delay", "body": body}
    if dry_run:
        return {"dry_run": True, "action": "delay", "would_send": would_send}

    result = _get_naver_client().delay_order(
        poid, due, req.delayed_dispatch_reason,
        req.dispatch_delayed_detailed_reason or "",
    )
    if not result.get("ok"):
        _raise_naver_write_error("발송지연", result)
    return {"dry_run": False, "action": "delay", "naver": result.get("data")}


# ──────────────────────────────────────────────────────────────────────────────
# N7. 클레임 (취소/반품/교환) — wave 1 취소. dry_run+confirm. 트랙 D-10.
#   스펙: docs/references/14_naver_order_write_apis.md (전부 POST, 공통응답 구조체)
# ──────────────────────────────────────────────────────────────────────────────

_VALID_CANCEL_REASONS = {
    "INTENT_CHANGED", "COLOR_AND_SIZE", "WRONG_ORDER", "PRODUCT_UNSATISFIED",
    "DELAYED_DELIVERY", "SOLD_OUT", "INCORRECT_INFO",
}


@router.get("/claims")
def get_claims(days: int = Query(default=14, ge=1, le=90)):
    """클레임(취소/반품/교환) 요청 건 라이브 조회 (트랙 N7). DB 저장 없이 라이브 반환.

    claim_status별로 처리 대상 판별: CANCEL_REQUEST=취소승인 / RETURN_REQUEST=반품승인 등.
    """
    try:
        return _get_naver_client().fetch_claims(days=days)
    except HTTPException:
        raise
    except Exception as e:
        log.error("네이버 클레임 조회 실패: %s", e)
        raise HTTPException(status_code=502, detail=f"네이버 클레임 조회 오류: {e}")


class CancelApproveRequest(BaseModel):
    product_order_id: str


class CancelRequestRequest(BaseModel):
    product_order_id: str
    cancel_reason: str
    cancel_detailed_reason: str = ""
    cancel_quantity: int | None = None


@router.post("/claims/cancel/approve")
def approve_cancel(req: CancelApproveRequest, dry_run: bool = Query(default=True)):
    """취소 요청 승인 (구매자 취소요청 승인, 단건). dry_run=true(기본)면 전송 안 함. 트랙 N7."""
    poid = (req.product_order_id or "").strip()
    if not poid:
        raise HTTPException(status_code=400, detail="상품주문번호 누락")
    # 스펙상 본문 없음 (codex P1) — would_send도 body None으로 표시
    would_send = {"path": f"/v1/pay-order/seller/product-orders/{poid}/claim/cancel/approve", "body": None}
    if dry_run:
        return {"dry_run": True, "action": "cancel_approve", "would_send": would_send}
    result = _get_naver_client().approve_cancel(poid)
    if not result.get("ok"):
        _raise_naver_write_error("취소 승인", result)
    return {"dry_run": False, "action": "cancel_approve", "naver": result.get("data")}


@router.post("/claims/cancel/request")
def request_cancel(req: CancelRequestRequest, dry_run: bool = Query(default=True)):
    """취소 요청 (판매자 직접 취소, 단건). dry_run=true(기본)면 전송 안 함. 트랙 N7."""
    poid = (req.product_order_id or "").strip()
    if not poid:
        raise HTTPException(status_code=400, detail="상품주문번호 누락")
    if req.cancel_reason not in _VALID_CANCEL_REASONS:
        raise HTTPException(status_code=400, detail=f"알 수 없는 취소사유 코드 '{req.cancel_reason}'")
    if req.cancel_quantity is not None and req.cancel_quantity < 1:
        raise HTTPException(status_code=400, detail="취소수량은 1 이상이어야 합니다")
    detail = req.cancel_detailed_reason or ""
    if len(detail) > 500:  # 조용히 자르지 않고 거부 (codex P2)
        raise HTTPException(status_code=400, detail="취소 상세 사유는 500자를 넘을 수 없습니다")
    body: dict = {"cancelReason": req.cancel_reason}
    if detail:
        body["cancelDetailedReason"] = detail
    if req.cancel_quantity is not None:
        body["cancelQuantity"] = req.cancel_quantity
    would_send = {"path": f"/v1/pay-order/seller/product-orders/{poid}/claim/cancel/request", "body": body}
    if dry_run:
        return {"dry_run": True, "action": "cancel_request", "would_send": would_send}
    result = _get_naver_client().request_cancel(
        poid, req.cancel_reason, detail, req.cancel_quantity,
    )
    if not result.get("ok"):
        _raise_naver_write_error("취소 요청", result)
    return {"dry_run": False, "action": "cancel_request", "naver": result.get("data")}


# ──────────────────────────────────────────────────────────────────────────────
# N7. 클레임 — wave 2 반품(Return). dry_run+confirm. 트랙 D-10.
#   스펙: docs/references/14_naver_order_write_apis.md (전부 POST, 단건 path)
# ──────────────────────────────────────────────────────────────────────────────

# 반품 요청 사유 (requestReturnClaimReason enum, 실측)
_VALID_RETURN_REASONS = {
    "INTENT_CHANGED", "COLOR_AND_SIZE", "WRONG_ORDER", "PRODUCT_UNSATISFIED",
    "DELAYED_DELIVERY", "SOLD_OUT", "DROPPED_DELIVERY", "BROKEN",
    "INCORRECT_INFO", "WRONG_DELIVERY", "WRONG_OPTION",
}
# 반품 보류 유형 (holdbackClassType enum, 실측 — ★EXTRAFEEE/EXTRAFEEE 철자 원문대로)
_VALID_HOLDBACK_CLASS_TYPES = {
    "RETURN_DELIVERYFEE", "EXTRAFEEE", "RETURN_DELIVERYFEE_AND_EXTRAFEEE",
    "RETURN_PRODUCT_NOT_DELIVERED", "ETC",
    "EXCHANGE_DELIVERYFEE", "EXCHANGE_EXTRAFEE", "EXCHANGE_PRODUCT_READY",
    "EXCHANGE_PRODUCT_NOT_DELIVERED", "EXCHANGE_HOLDBACK",
    "SELLER_CONFIRM_NEED", "PURCHASER_CONFIRM_NEED", "SELLER_REMIT", "ETC2",
}
# 반품 수거 배송 방법 (deliveryMethod enum, 실측 — 발송용과 달리 RETURN_* 포함)
_VALID_COLLECT_DELIVERY_METHODS = {
    "DELIVERY", "GDFW_ISSUE_SVC", "VISIT_RECEIPT", "DIRECT_DELIVERY",
    "QUICK_SVC", "NOTHING", "RETURN_DESIGNATED", "RETURN_DELIVERY",
    "RETURN_INDIVIDUAL", "RETURN_MERCHANT", "UNKNOWN",
}


class ReturnApproveRequest(BaseModel):
    product_order_id: str


class ReturnRejectRequest(BaseModel):
    product_order_id: str
    reject_return_reason: str  # 자유 텍스트


class ReturnHoldbackRequest(BaseModel):
    product_order_id: str
    holdback_class_type: str
    holdback_return_detail_reason: str
    extra_return_fee_amount: int | None = None


class ReturnHoldbackReleaseRequest(BaseModel):
    product_order_id: str


class ReturnRequestRequest(BaseModel):
    product_order_id: str
    return_reason: str
    collect_delivery_method: str
    collect_delivery_company: str = ""
    collect_tracking_number: str = ""
    return_quantity: int | None = None


@router.post("/claims/return/approve")
def approve_return(req: ReturnApproveRequest, dry_run: bool = Query(default=True)):
    """반품 승인 (구매자 반품요청 승인, 단건). body 없음. dry_run=true(기본). 트랙 N7 wave2."""
    poid = (req.product_order_id or "").strip()
    if not poid:
        raise HTTPException(status_code=400, detail="상품주문번호 누락")
    would_send = {"path": f"/v1/pay-order/seller/product-orders/{poid}/claim/return/approve", "body": None}
    if dry_run:
        return {"dry_run": True, "action": "return_approve", "would_send": would_send}
    result = _get_naver_client().approve_return(poid)
    if not result.get("ok"):
        _raise_naver_write_error("반품 승인", result)
    return {"dry_run": False, "action": "return_approve", "naver": result.get("data")}


@router.post("/claims/return/reject")
def reject_return(req: ReturnRejectRequest, dry_run: bool = Query(default=True)):
    """반품 거부(철회) (단건). dry_run=true(기본). 트랙 N7 wave2."""
    poid = (req.product_order_id or "").strip()
    if not poid:
        raise HTTPException(status_code=400, detail="상품주문번호 누락")
    reason = (req.reject_return_reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="반품 거부 사유는 필수입니다")
    if len(reason) > 250:  # 조용히 자르지 않고 거부
        raise HTTPException(status_code=400, detail="반품 거부 사유는 250자를 넘을 수 없습니다")
    body = {"rejectReturnReason": reason}
    would_send = {"path": f"/v1/pay-order/seller/product-orders/{poid}/claim/return/reject", "body": body}
    if dry_run:
        return {"dry_run": True, "action": "return_reject", "would_send": would_send}
    result = _get_naver_client().reject_return(poid, reason)
    if not result.get("ok"):
        _raise_naver_write_error("반품 거부", result)
    return {"dry_run": False, "action": "return_reject", "naver": result.get("data")}


@router.post("/claims/return/holdback")
def holdback_return(req: ReturnHoldbackRequest, dry_run: bool = Query(default=True)):
    """반품 보류 (단건). dry_run=true(기본). 트랙 N7 wave2."""
    poid = (req.product_order_id or "").strip()
    if not poid:
        raise HTTPException(status_code=400, detail="상품주문번호 누락")
    if req.holdback_class_type not in _VALID_HOLDBACK_CLASS_TYPES:
        raise HTTPException(status_code=400, detail=f"알 수 없는 보류유형 코드 '{req.holdback_class_type}'")
    detail = (req.holdback_return_detail_reason or "").strip()
    if not detail:
        raise HTTPException(status_code=400, detail="보류 상세 사유는 필수입니다")
    if len(detail) > 250:
        raise HTTPException(status_code=400, detail="보류 상세 사유는 250자를 넘을 수 없습니다")
    if req.extra_return_fee_amount is not None and req.extra_return_fee_amount < 0:
        raise HTTPException(status_code=400, detail="기타 반품 비용은 0 이상이어야 합니다")
    body: dict = {
        "holdbackClassType": req.holdback_class_type,
        "holdbackReturnDetailReason": detail,
    }
    if req.extra_return_fee_amount is not None:
        body["extraReturnFeeAmount"] = req.extra_return_fee_amount
    would_send = {"path": f"/v1/pay-order/seller/product-orders/{poid}/claim/return/holdback", "body": body}
    if dry_run:
        return {"dry_run": True, "action": "return_holdback", "would_send": would_send}
    result = _get_naver_client().holdback_return(
        poid, req.holdback_class_type, detail, req.extra_return_fee_amount,
    )
    if not result.get("ok"):
        _raise_naver_write_error("반품 보류", result)
    return {"dry_run": False, "action": "return_holdback", "naver": result.get("data")}


@router.post("/claims/return/holdback/release")
def release_return_holdback(req: ReturnHoldbackReleaseRequest, dry_run: bool = Query(default=True)):
    """반품 보류 해제 (단건). body 없음. dry_run=true(기본). 트랙 N7 wave2."""
    poid = (req.product_order_id or "").strip()
    if not poid:
        raise HTTPException(status_code=400, detail="상품주문번호 누락")
    would_send = {"path": f"/v1/pay-order/seller/product-orders/{poid}/claim/return/holdback/release", "body": None}
    if dry_run:
        return {"dry_run": True, "action": "return_holdback_release", "would_send": would_send}
    result = _get_naver_client().release_return_holdback(poid)
    if not result.get("ok"):
        _raise_naver_write_error("반품 보류 해제", result)
    return {"dry_run": False, "action": "return_holdback_release", "naver": result.get("data")}


@router.post("/claims/return/request")
def request_return(req: ReturnRequestRequest, dry_run: bool = Query(default=True)):
    """반품 요청 (판매자 직접 반품 접수, 단건). dry_run=true(기본). 트랙 N7 wave2."""
    poid = (req.product_order_id or "").strip()
    if not poid:
        raise HTTPException(status_code=400, detail="상품주문번호 누락")
    if req.return_reason not in _VALID_RETURN_REASONS:
        raise HTTPException(status_code=400, detail=f"알 수 없는 반품사유 코드 '{req.return_reason}'")
    method = (req.collect_delivery_method or "").strip()
    if method not in _VALID_COLLECT_DELIVERY_METHODS:
        raise HTTPException(status_code=400, detail=f"알 수 없는 수거 배송방법 '{method}'")
    if req.return_quantity is not None and req.return_quantity < 1:
        raise HTTPException(status_code=400, detail="반품수량은 1 이상이어야 합니다")
    company = (req.collect_delivery_company or "").strip()
    tracking = (req.collect_tracking_number or "").strip()
    body: dict = {"returnReason": req.return_reason, "collectDeliveryMethod": method}
    if company:
        body["collectDeliveryCompany"] = company
    if tracking:
        body["collectTrackingNumber"] = tracking
    if req.return_quantity is not None:
        body["returnQuantity"] = req.return_quantity
    would_send = {"path": f"/v1/pay-order/seller/product-orders/{poid}/claim/return/request", "body": body}
    if dry_run:
        return {"dry_run": True, "action": "return_request", "would_send": would_send}
    result = _get_naver_client().request_return(
        poid, req.return_reason, method, company, tracking, req.return_quantity,
    )
    if not result.get("ok"):
        _raise_naver_write_error("반품 요청", result)
    return {"dry_run": False, "action": "return_request", "naver": result.get("data")}


# ──────────────────────────────────────────────────────────────────────────────
# N7. 클레임 — wave 3 교환(Exchange). dry_run+confirm. 트랙 D-10.
#   스펙: docs/references/14 (전부 POST, 단건 path, 경로 claim/exchange/*)
#   보류 유형 enum은 반품과 동일(_VALID_HOLDBACK_CLASS_TYPES 재사용),
#   재배송 배송방법 enum은 수거와 동일(_VALID_COLLECT_DELIVERY_METHODS 재사용).
# ──────────────────────────────────────────────────────────────────────────────


class ExchangeCollectApproveRequest(BaseModel):
    product_order_id: str


class ExchangeDispatchRequest(BaseModel):
    product_order_id: str
    re_delivery_method: str = ""
    re_delivery_company: str = ""
    re_delivery_tracking_number: str = ""


class ExchangeHoldbackRequest(BaseModel):
    product_order_id: str
    holdback_class_type: str
    holdback_exchange_detail_reason: str
    extra_exchange_fee_amount: int | None = None


class ExchangeHoldbackReleaseRequest(BaseModel):
    product_order_id: str


class ExchangeRejectRequest(BaseModel):
    product_order_id: str
    reject_exchange_reason: str  # 자유 텍스트


@router.post("/claims/exchange/collect/approve")
def approve_exchange_collect(req: ExchangeCollectApproveRequest, dry_run: bool = Query(default=True)):
    """교환 수거완료 (단건). body 없음. dry_run=true(기본). 트랙 N7 wave3."""
    poid = (req.product_order_id or "").strip()
    if not poid:
        raise HTTPException(status_code=400, detail="상품주문번호 누락")
    would_send = {"path": f"/v1/pay-order/seller/product-orders/{poid}/claim/exchange/collect/approve", "body": None}
    if dry_run:
        return {"dry_run": True, "action": "exchange_collect_approve", "would_send": would_send}
    result = _get_naver_client().approve_exchange_collect(poid)
    if not result.get("ok"):
        _raise_naver_write_error("교환 수거완료", result)
    return {"dry_run": False, "action": "exchange_collect_approve", "naver": result.get("data")}


@router.post("/claims/exchange/dispatch")
def dispatch_exchange(req: ExchangeDispatchRequest, dry_run: bool = Query(default=True)):
    """교환 재배송 (단건). body 필드 전부 선택. dry_run=true(기본). 트랙 N7 wave3."""
    poid = (req.product_order_id or "").strip()
    if not poid:
        raise HTTPException(status_code=400, detail="상품주문번호 누락")
    method = (req.re_delivery_method or "").strip()
    if method and method not in _VALID_COLLECT_DELIVERY_METHODS:
        raise HTTPException(status_code=400, detail=f"알 수 없는 배송방법 '{method}'")
    company = (req.re_delivery_company or "").strip()
    tracking = (req.re_delivery_tracking_number or "").strip()
    # 스펙상 dispatch body 필드는 전부 선택(API센터 실측, codex 합의) — 앱 제약 안 둠.
    # 부분 입력도 그대로 전송하고, 네이버가 거부하면 _request_write가 4xx 본문 surface.
    body: dict = {}
    if method:
        body["reDeliveryMethod"] = method
    if company:
        body["reDeliveryCompany"] = company
    if tracking:
        body["reDeliveryTrackingNumber"] = tracking
    would_send = {"path": f"/v1/pay-order/seller/product-orders/{poid}/claim/exchange/dispatch", "body": body}
    if dry_run:
        return {"dry_run": True, "action": "exchange_dispatch", "would_send": would_send}
    result = _get_naver_client().dispatch_exchange(poid, method, company, tracking)
    if not result.get("ok"):
        _raise_naver_write_error("교환 재배송", result)
    return {"dry_run": False, "action": "exchange_dispatch", "naver": result.get("data")}


@router.post("/claims/exchange/holdback")
def holdback_exchange(req: ExchangeHoldbackRequest, dry_run: bool = Query(default=True)):
    """교환 보류 (단건). dry_run=true(기본). 트랙 N7 wave3."""
    poid = (req.product_order_id or "").strip()
    if not poid:
        raise HTTPException(status_code=400, detail="상품주문번호 누락")
    if req.holdback_class_type not in _VALID_HOLDBACK_CLASS_TYPES:
        raise HTTPException(status_code=400, detail=f"알 수 없는 보류유형 코드 '{req.holdback_class_type}'")
    detail = (req.holdback_exchange_detail_reason or "").strip()
    if not detail:
        raise HTTPException(status_code=400, detail="보류 상세 사유는 필수입니다")
    if len(detail) > 250:
        raise HTTPException(status_code=400, detail="보류 상세 사유는 250자를 넘을 수 없습니다")
    if req.extra_exchange_fee_amount is not None and req.extra_exchange_fee_amount < 0:
        raise HTTPException(status_code=400, detail="기타 교환 비용은 0 이상이어야 합니다")
    body: dict = {
        "holdbackClassType": req.holdback_class_type,
        "holdbackExchangeDetailReason": detail,
    }
    if req.extra_exchange_fee_amount is not None:
        body["extraExchangeFeeAmount"] = req.extra_exchange_fee_amount
    would_send = {"path": f"/v1/pay-order/seller/product-orders/{poid}/claim/exchange/holdback", "body": body}
    if dry_run:
        return {"dry_run": True, "action": "exchange_holdback", "would_send": would_send}
    result = _get_naver_client().holdback_exchange(
        poid, req.holdback_class_type, detail, req.extra_exchange_fee_amount,
    )
    if not result.get("ok"):
        _raise_naver_write_error("교환 보류", result)
    return {"dry_run": False, "action": "exchange_holdback", "naver": result.get("data")}


@router.post("/claims/exchange/holdback/release")
def release_exchange_holdback(req: ExchangeHoldbackReleaseRequest, dry_run: bool = Query(default=True)):
    """교환 보류 해제 (단건). body 없음. dry_run=true(기본). 트랙 N7 wave3."""
    poid = (req.product_order_id or "").strip()
    if not poid:
        raise HTTPException(status_code=400, detail="상품주문번호 누락")
    would_send = {"path": f"/v1/pay-order/seller/product-orders/{poid}/claim/exchange/holdback/release", "body": None}
    if dry_run:
        return {"dry_run": True, "action": "exchange_holdback_release", "would_send": would_send}
    result = _get_naver_client().release_exchange_holdback(poid)
    if not result.get("ok"):
        _raise_naver_write_error("교환 보류 해제", result)
    return {"dry_run": False, "action": "exchange_holdback_release", "naver": result.get("data")}


@router.post("/claims/exchange/reject")
def reject_exchange(req: ExchangeRejectRequest, dry_run: bool = Query(default=True)):
    """교환 거부(철회) (단건). dry_run=true(기본). 트랙 N7 wave3."""
    poid = (req.product_order_id or "").strip()
    if not poid:
        raise HTTPException(status_code=400, detail="상품주문번호 누락")
    reason = (req.reject_exchange_reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="교환 거부 사유는 필수입니다")
    if len(reason) > 250:
        raise HTTPException(status_code=400, detail="교환 거부 사유는 250자를 넘을 수 없습니다")
    body = {"rejectExchangeReason": reason}
    would_send = {"path": f"/v1/pay-order/seller/product-orders/{poid}/claim/exchange/reject", "body": body}
    if dry_run:
        return {"dry_run": True, "action": "exchange_reject", "would_send": would_send}
    result = _get_naver_client().reject_exchange(poid, reason)
    if not result.get("ok"):
        _raise_naver_write_error("교환 거부", result)
    return {"dry_run": False, "action": "exchange_reject", "naver": result.get("data")}


# ──────────────────────────────────────────────────────────────────────────────
# N8. 상품 판매 상태 변경 (쓰기 — dry_run+confirm). 트랙 D-11.
#   스펙: docs/references/15_naver_product_write_apis.md N8-2 (PUT change-status)
#   범위: 판매중/품절/판매중지 3상태만. 가격 안 건드림(위험0). 오하이=원상품 단위 재고.
# ──────────────────────────────────────────────────────────────────────────────

# 패널에 노출하는 안전한 3상태만 허용 (DELETE 등 시스템/위험 상태는 차단)
_VALID_PRODUCT_STATUS_TYPES = {"SALE", "OUTOFSTOCK", "SUSPENSION"}
_MAX_STOCK_QUANTITY = 99999999


class ChangeProductStatusRequest(BaseModel):
    origin_product_no: int
    status_type: str
    stock_quantity: int | None = None
    sale_start_date: str = ""   # ISO8601(+09:00), 선택
    sale_end_date: str = ""     # ISO8601(+09:00), 선택


@router.put("/products/change-status")
def change_product_status(req: ChangeProductStatusRequest, dry_run: bool = Query(default=True)):
    """원상품 판매 상태 변경 (단건, 트랙 N8). dry_run=true(기본)면 전송 안 함.

    전이 규칙(API센터 실측): SALE→OUTOFSTOCK은 재고 0됨, 품절·중지→SALE 전환 시 재고 필수,
    재고 0이면 OUTOFSTOCK 유지. 가격(salePrice) 미전송 → 가격 손실 위험 0.
    """
    if req.origin_product_no <= 0:
        raise HTTPException(status_code=400, detail="원상품번호 누락")
    if req.status_type not in _VALID_PRODUCT_STATUS_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"허용되지 않은 판매상태 '{req.status_type}' (SALE/OUTOFSTOCK/SUSPENSION만 가능)",
        )

    # 재고 수량 결정 — 전이 규칙(API센터 실측) 반영 (codex P1-1/P2-1)
    #  · SALE(재입고): 재고 필수, 0이면 네이버가 OUTOFSTOCK 유지 → 의도와 불일치라 1 이상 요구
    #  · OUTOFSTOCK(품절): 재고 0 강제 (스펙: SALE→OUTOFSTOCK 시 재고 0됨)
    #  · SUSPENSION(판매중지): 재고 불필요 → 입력 무시
    stock: int | None
    if req.status_type == "SALE":
        if req.stock_quantity is None:
            raise HTTPException(status_code=400, detail="판매중(SALE) 전환 시 재고 수량은 필수입니다")
        if req.stock_quantity < 1:
            raise HTTPException(status_code=400, detail="판매중(SALE) 전환 시 재고 수량은 1 이상이어야 합니다 (0이면 품절로 유지됨)")
        if req.stock_quantity > _MAX_STOCK_QUANTITY:
            raise HTTPException(status_code=400, detail=f"재고 수량은 {_MAX_STOCK_QUANTITY}를 넘을 수 없습니다")
        stock = req.stock_quantity
    elif req.status_type == "OUTOFSTOCK":
        stock = 0
    else:  # SUSPENSION
        stock = None

    body: dict = {"statusType": req.status_type}
    if stock is not None:
        body["stockQuantity"] = stock
    if req.sale_start_date:
        body["saleStartDate"] = _normalize_kst_datetime(req.sale_start_date, "판매 시작 일시")
    if req.sale_end_date:
        body["saleEndDate"] = _normalize_kst_datetime(req.sale_end_date, "판매 종료 일시")

    would_send = {
        "path": f"/v1/products/origin-products/{req.origin_product_no}/change-status",
        "body": body,
    }
    if dry_run:
        return {"dry_run": True, "action": "product_change_status", "would_send": would_send}
    result = _get_naver_client().change_product_status(
        req.origin_product_no,
        req.status_type,
        stock,
        body.get("saleStartDate", ""),
        body.get("saleEndDate", ""),
    )
    if not result.get("ok"):
        _raise_naver_write_error("판매 상태 변경", result)
    return {"dry_run": False, "action": "product_change_status", "naver": result.get("data")}
