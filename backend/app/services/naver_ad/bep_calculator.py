# bep_calculator.py — bep_calculator_sa (단일 책임: 네이버 상품별 BEP ROAS 산출)
# D-NAO-8: product_master 원가 × orders 실거래 단가 × 실효 수수료율 → 상품별 손익분기 ROAS.
#   target_roas = bep_roas × 공격성 배수(안전1.3/표준1.15/공격1.05, D-NAO-2).
# 판매가 소스: 매핑엔 네이버 판매가가 없어(전부 0) orders 실거래가에서 단가 산출.
#   orders.selling_price는 라인총액 → 수량으로 나눠 단가 정규화, 상품별 median(프로모 완화).
# 참고 메모리: bep-roas-calculation-structure (BEP ROAS = 판매가 ÷ 공헌이익).
from __future__ import annotations

import logging
import statistics
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import delete, func as sqlfunc
from sqlalchemy.orm import Session

from app.models import (
    Channel, NaverProductBep, NaverSettlementDaily, Order,
    ProductChannelMapping, ProductMaster,
)
from app.utils.kst import kst_now, kst_today

log = logging.getLogger(__name__)

NAVER_CHANNEL_ID = 6
VAT_DIVISOR = Decimal("1.1")  # 매출 VAT 포함 → 실수취 (NaverOps 이익회계와 정합)
# 공격성 배수 (D-NAO-2): target_roas = bep_roas × 배수
AGG_MULT = {"safe": Decimal("1.30"), "standard": Decimal("1.15"), "aggressive": Decimal("1.05")}
_DEFAULT_COMMISSION_RATE = Decimal("0.055")  # 정산·채널 모두 없을 때 최종 폴백
_PRICE_WINDOW_DAYS = 120  # 대표 단가 산출 창(이 기간 주문 없으면 전기간 폴백)


def effective_commission_rate(db: Session) -> Decimal:
    """네이버 실효 수수료율(0~1) = |Σcommission| / (Σsettle + |Σcommission|).

    settle_amount=수수료 차감 후 정산금, commission_amount=수수료(음수). gross ≈ settle+|comm|.
    정산 데이터 없으면 channels.commission_rate(5.5%)·최종 상수 폴백.
    """
    settle, comm = db.query(
        sqlfunc.sum(NaverSettlementDaily.settle_amount),
        sqlfunc.sum(NaverSettlementDaily.commission_amount),
    ).one()
    if settle and comm:
        settle_d = Decimal(str(settle))
        comm_abs = abs(Decimal(str(comm)))
        gross = settle_d + comm_abs
        if gross > 0:
            return comm_abs / gross
    ch = db.get(Channel, NAVER_CHANNEL_ID)
    if ch and ch.commission_rate:
        return Decimal(str(ch.commission_rate)) / Decimal("100")
    return _DEFAULT_COMMISSION_RATE


def _unit_prices(db: Session) -> dict[str, Decimal]:
    """네이버 상품(channel_product_id)별 대표 단가 = median(selling_price/quantity).

    최근 _PRICE_WINDOW_DAYS일 주문 우선, 없으면 전기간. 원 단위 반올림.
    """
    cutoff = kst_today() - timedelta(days=_PRICE_WINDOW_DAYS)

    def _collect(since) -> dict[str, list]:
        qy = db.query(Order.platform_product_id, Order.selling_price, Order.quantity).filter(
            Order.channel_id == NAVER_CHANNEL_ID,
            Order.selling_price > 0,
            Order.quantity > 0,
        )
        if since is not None:
            qy = qy.filter(Order.order_date >= since)
        acc: dict[str, list] = {}
        for pid, sp, qn in qy.all():
            if not pid:
                continue
            acc.setdefault(pid, []).append(Decimal(str(sp)) / Decimal(int(qn)))
        return acc

    recent = _collect(cutoff)
    alltime = _collect(None)
    prices: dict[str, Decimal] = {}
    for pid, lst in alltime.items():
        src = recent.get(pid) or lst
        prices[pid] = Decimal(statistics.median(src)).quantize(Decimal("1"), ROUND_HALF_UP)
    return prices


def calculate_bep(db: Session, *, aggressiveness: str = "standard") -> dict:
    """네이버 전 활성 매핑에 대해 BEP ROAS 산출 → naver_product_bep snapshot 교체.

    한 상품당 1행(원가·단가 있으면 bep_roas 산출, 없으면 has_cost=False 행만).
    반환: {rows, with_bep, commission_rate, aggressiveness}.
    """
    rate = effective_commission_rate(db)
    mult = AGG_MULT.get(aggressiveness, AGG_MULT["standard"])
    prices = _unit_prices(db)
    masters = {pm.id: (Decimal(str(pm.cost_price or 0)), pm.product_name or "")
               for pm in db.query(ProductMaster).all()}
    mappings = db.query(ProductChannelMapping).filter(
        ProductChannelMapping.channel_id == NAVER_CHANNEL_ID,
        ProductChannelMapping.is_active.is_(True),
    ).all()

    # 같은 channel_product_id에 중복 매핑 존재(라이브 22건) → UNIQUE(channel,cpid) 제약.
    # cpid당 1개로 dedupe: 원가 있는 매핑 우선(BEP 산출 가능), 동률이면 먼저 것.
    best: dict[str, ProductChannelMapping] = {}
    for m in mappings:
        cur = best.get(m.channel_product_id)
        if cur is None:
            best[m.channel_product_id] = m
            continue
        cur_cost = masters.get(cur.product_id, (Decimal("0"), ""))[0]
        new_cost = masters.get(m.product_id, (Decimal("0"), ""))[0]
        if new_cost > 0 and cur_cost <= 0:
            best[m.channel_product_id] = m

    db.execute(delete(NaverProductBep).where(NaverProductBep.channel_id == NAVER_CHANNEL_ID))
    now = kst_now()
    n_total = 0
    n_bep = 0
    for m in best.values():
        sp = prices.get(m.channel_product_id, Decimal("0"))
        cost, master_name = masters.get(m.product_id, (Decimal("0"), ""))
        name = (m.channel_product_name or master_name or "")[:300]
        logistics = Decimal("0")  # 네이버 배송비 대개 구매자 부담 — P0 0, 정밀화는 P5
        has_cost = sp > 0 and cost > 0
        commission = sp * rate
        contribution = (sp - commission - cost - logistics) / VAT_DIVISOR if has_cost else Decimal("0")
        bep = None
        target = None
        if has_cost and contribution > 0:
            bep = (sp / contribution).quantize(Decimal("0.0001"), ROUND_HALF_UP)
            target = (bep * mult).quantize(Decimal("0.0001"), ROUND_HALF_UP)
            n_bep += 1
        db.add(NaverProductBep(
            channel_id=NAVER_CHANNEL_ID,
            channel_product_id=m.channel_product_id,
            product_master_id=m.product_id,
            product_name=name,
            selling_price=sp,
            cost_price=cost,
            commission_rate=rate.quantize(Decimal("0.0001"), ROUND_HALF_UP),
            logistics_cost=logistics,
            contribution_margin=contribution.quantize(Decimal("0.01"), ROUND_HALF_UP),
            bep_roas=bep,
            aggressiveness=aggressiveness,
            target_roas=target,
            has_cost=has_cost,
            calculated_at=now,
        ))
        n_total += 1
    db.commit()
    log.info("naver_product_bep 산출: %d행(bep %d) rate=%.4f 공격성=%s",
             n_total, n_bep, float(rate), aggressiveness)
    return {"rows": n_total, "with_bep": n_bep,
            "commission_rate": float(rate), "aggressiveness": aggressiveness}
