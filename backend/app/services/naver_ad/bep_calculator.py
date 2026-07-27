# bep_calculator.py — bep_calculator_sa (단일 책임: 네이버 상품별 BEP ROAS 산출)
# D-NAO-8: product_master 원가 × orders 실거래 단가 × 실효 수수료율 → 상품별 손익분기 ROAS.
#   target_roas = bep_roas × 공격성 배수(안전1.3/표준1.15/공격1.05, D-NAO-2).
# 판매가 소스: 매핑엔 네이버 판매가가 없어(전부 0) orders 실거래가에서 단가 산출.
#   orders.selling_price는 라인총액 → 수량으로 나눠 단가 정규화, 상품별 median(프로모 완화).
# 참고 메모리: bep-roas-calculation-structure (BEP ROAS = 판매가 ÷ 공헌이익).
from __future__ import annotations

import json
import logging
import statistics
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import delete, func as sqlfunc
from sqlalchemy.orm import Session

from app.models import (
    Channel, NaverProductBep, NaverSettlementCase, NaverSettlementDaily, Order,
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

# ── D-NAO-57 (C) 배송비: 건당 단가(부가세포함, Jino 확정 2026-07-18) ──
# ★단가는 config 상수(단가 개정 대비), 적용은 주문 건별 배송방식 판별 기반(_order_shipping_cost).
SHIPPING_COST_NORMAL = Decimal("1900")     # 일반배송 건당
SHIPPING_COST_NBAESONG = Decimal("3020")   # N배송(품고 내일도착/도착보장) 건당 — D-NAO-84 실배선
SHIPPING_COST_BY_METHOD = {"normal": SHIPPING_COST_NORMAL, "nbaesong": SHIPPING_COST_NBAESONG}

# ── D-NAO-57 (B) 광고 의사결정용 수수료율 분해 게이트 ──
_AD_COMM_MIN_CASE_ROWS = 30       # 건별 정산 표본이 이보다 적으면 분해 신뢰 불가 → 블렌드 폴백
_AD_COMM_MIN_SHOPPING_SHARE = Decimal("0.05")  # 쇼핑주문 매출점유가 이 미만이면 언디루션 불안정
_AD_COMM_MAX_PLAUSIBLE = Decimal("0.20")  # 산출 광고 수수료율이 20%↑면 데이터 이상 → 폴백


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


def ad_commission_rate(db: Session) -> dict | None:
    """광고 의사결정용 실효 수수료율(0~1) — 정산 유형별 실측 분해(D-NAO-57 B).

    광고 전환은 100% 쇼핑 경유라 **매출연동(selling_interlock) 수수료가 항상** 붙는다.
    전체 회계 블렌드(effective_commission_rate)는 직접유입 주문까지 평균해 매출연동을 희석하므로
    광고 BEP엔 과소하다. 이 함수는 그 희석을 되돌려(un-dilute) 광고 경로 실효율을 낸다.

    실측 근거: naver_settlement_case가 유형별 수수료(주문관리=total_pay_commission,
    매출연동=selling_interlock_commission, 무이자할부=free_installment_commission)를 이미
    분해 저장한다(트랙 D-6, 05:30 크론). 커머스API 정산 상세가 유형별 분해를 제공함이 실증됨 —
    별도 수집 확장/요율 하드코딩 불필요(값은 전부 실측에서 역산).

    방법(분모 단위 모호성에 강건 — case 표본에선 '비율'만 쓴다):
      B = effective_commission_rate(db)                      # 일별 정산 블렌드(라이브 검증됨)
      interlock_frac = |Σ매출연동| / |Σ전체수수료|            # (건별, 분모무관 비율)
      shopping_share = Σpay_settle(매출연동≠0) / Σpay_settle(전체)   # 쇼핑주문 매출점유(비율)
      order_mgmt_rate = B × (1 − interlock_frac)             # 주문관리는 전 주문 보편 → 희석 없음
      full_interlock  = B × interlock_frac / shopping_share  # 쇼핑 주문 기준으로 언디루션
      ad_rate = order_mgmt_rate + full_interlock             # 항상 ≥ B

    반환 dict(basis="case_decomposition") 또는 None(표본 부족·shopping_share 바닥·매출연동 0·
    산출 rate 비현실 등 — 호출부가 effective_commission_rate 블렌드로 폴백).
    """
    blended = effective_commission_rate(db)
    if blended <= 0:
        return None

    # PROD_ORDER 건별 정산 유형별 합계 + 표본 수 + 매출점유 분모.
    total_pay, interlock, free_inst, pay_all, n_rows = db.query(
        sqlfunc.sum(NaverSettlementCase.total_pay_commission),
        sqlfunc.sum(NaverSettlementCase.selling_interlock_commission),
        sqlfunc.sum(NaverSettlementCase.free_installment_commission),
        sqlfunc.sum(NaverSettlementCase.pay_settle_amount),
        sqlfunc.count(NaverSettlementCase.id),
    ).filter(NaverSettlementCase.product_order_type == "PROD_ORDER").one()

    if not n_rows or n_rows < _AD_COMM_MIN_CASE_ROWS:
        return None

    comm_total = abs(Decimal(str(total_pay or 0)) + Decimal(str(interlock or 0)) + Decimal(str(free_inst or 0)))
    interlock_comm = abs(Decimal(str(interlock or 0)))
    pay_all_d = Decimal(str(pay_all or 0))
    if comm_total <= 0 or interlock_comm <= 0 or pay_all_d <= 0:
        return None  # 매출연동이 없거나 분모 0 → 언디루션 불가, 블렌드가 이미 최선

    # 매출연동이 붙은 주문의 매출점유(pay_settle 비례 — 분모 단위 무관 비율).
    pay_interlock = db.query(
        sqlfunc.sum(NaverSettlementCase.pay_settle_amount)
    ).filter(
        NaverSettlementCase.product_order_type == "PROD_ORDER",
        NaverSettlementCase.selling_interlock_commission != 0,
    ).scalar()
    pay_interlock_d = Decimal(str(pay_interlock or 0))
    shopping_share = (pay_interlock_d / pay_all_d) if pay_all_d > 0 else Decimal("0")
    if shopping_share < _AD_COMM_MIN_SHOPPING_SHARE:
        return None  # 쇼핑주문 표본이 너무 얇아 언디루션이 불안정 → 블렌드 폴백

    interlock_frac = interlock_comm / comm_total
    order_mgmt_rate = blended * (Decimal("1") - interlock_frac)
    full_interlock = blended * interlock_frac / shopping_share
    ad_rate = order_mgmt_rate + full_interlock

    if ad_rate <= 0 or ad_rate > _AD_COMM_MAX_PLAUSIBLE:
        return None  # 데이터 이상 방어 — 비현실 값이면 폴백

    return {
        "rate": ad_rate,
        "order_mgmt_rate": order_mgmt_rate,
        "full_interlock_rate": full_interlock,
        "blended_rate": blended,
        "interlock_share_of_commission": interlock_frac,
        "shopping_gross_share": shopping_share,
        "case_rows": int(n_rows),
        "basis": "case_decomposition",
    }


def _order_shipping_cost(order_row=None) -> Decimal:
    """주문 1건의 배송비(건당, 부가세포함) — 배송방식 판별 훅 (D-NAO-57 C · D-NAO-84 실배선).

    N배송(품고 내일도착/도착보장) 판별 필드가 실측되어(prod 주문 id 11929, 2026-07-22 21:52 KST
    첫 N배송 주문 raw_data) 배선 완료. 판별자: 원천 주문 응답 entry(raw_data JSON)의
    productOrder.deliveryAttributeType == "ARRIVAL_GUARANTEE" → N배송(3,020). 그 외(과거 전 건
    "TODAY")·필드 부재 → 일반배송(1,900).

    ★단일 판별자 원칙(원칙22, n=1 실측): 실측 주문엔 동반 키(logisticsCompanyId=="PG",
    logisticsCenterId, arrivalGuaranteeDate, deliveryTagType=="TOMORROW")도 함께 관측됐으나,
    프로그램 레벨 마커는 deliveryAttributeType 하나로 고정한다(동반 신호는 참고, 판별에 미사용 —
    표본이 얇을 때 판별자를 늘리면 오탐 위험). 파싱 실패·raw_data 부재·필드 부재는 모두
    일반배송으로 폴백(fail-safe — 종전 동작 보존, BEP를 낙관 쪽으로 흔들지 않음).

    order_row: _avg_qty_and_logistics의 경량 행 dict({"raw_data": ...}) 또는 raw_data 속성을
    가진 ORM 행. None이면 일반배송."""
    if not order_row:
        return SHIPPING_COST_NORMAL
    if isinstance(order_row, dict):
        rd = order_row.get("raw_data")
    else:
        rd = getattr(order_row, "raw_data", None)
    parsed = None
    if isinstance(rd, dict):
        parsed = rd
    elif isinstance(rd, str) and rd:
        try:
            obj = json.loads(rd)
            parsed = obj if isinstance(obj, dict) else None
        except (ValueError, TypeError):
            parsed = None  # 잘림/비JSON → 폴백
    if not isinstance(parsed, dict):
        return SHIPPING_COST_NORMAL
    po = parsed.get("productOrder")
    if not isinstance(po, dict):
        return SHIPPING_COST_NORMAL
    if po.get("deliveryAttributeType") == "ARRIVAL_GUARANTEE":
        return SHIPPING_COST_NBAESONG
    return SHIPPING_COST_NORMAL


def _avg_qty_and_logistics(db: Session) -> dict[str, dict]:
    """네이버 상품(channel_product_id)별 평균 주문수량 + 단가당 **순**물류비(D-NAO-57 C, 리뷰 P2-1).

    logistics(단가당) = 상품별 순배송원가(건당) ÷ 평균 주문수량.
      순배송원가 net_ship = max(0, 지불 배송비 − 평균 수취 배송비)
        - 지불 배송비: 주문 건별 배송방식 가중평균(_order_shipping_cost가 raw_data의
          productOrder.deliveryAttributeType로 일반배송 1,900 / N배송 3,020을 건별 판별 —
          D-NAO-84 실배선. 혼재 주문이면 가중평균으로 자동 반영)
        - 수취 배송비: Order.shipping_cost(고객이 낸 deliveryFeeAmount) 실측 평균 —
          라이브 실측(120일): 채널 25.4% 주문이 수취(상품별 무료/유료 혼합이 사실).
        - ★max(0,·) 클램프 = 보수 방향: 수취가 지불을 초과해도 배송 마진을 이익(음수 물류비)으로
          잡지 않는다 — BEP를 낙관 쪽으로 움직이는 오차를 구조적으로 차단.
    평균 주문수량·수취 배송비는 최근 _PRICE_WINDOW_DAYS 창(없으면 전기간). 주문 없는 상품은
    수량 1 + 수취 0 폴백(= 배송비 전액 차감, 보수적).

    반환: {cpid: {"avg_qty", "shipping"(지불), "collected"(수취 평균), "net_ship",
                  "logistics"(단가당), "orders"}}
    """
    cutoff = kst_today() - timedelta(days=_PRICE_WINDOW_DAYS)

    def _collect(since):
        # cpid → 주문 행(경량) 리스트. raw_data를 함께 로드해 _order_shipping_cost가 건별
        # 배송방식(일반/N배송)을 판별한다(D-NAO-84). 상품당 주문 수는 제한적이라 행당 JSON 파싱
        # 비용은 무시할 수준.
        qy = db.query(
            Order.platform_product_id, Order.quantity, Order.shipping_cost, Order.raw_data
        ).filter(
            Order.channel_id == NAVER_CHANNEL_ID,
            Order.quantity > 0,
        )
        if since is not None:
            qy = qy.filter(Order.order_date >= since)
        acc: dict[str, list] = {}
        for pid, qn, ship_in, raw in qy.all():
            if not pid:
                continue
            acc.setdefault(pid, []).append({
                "quantity": int(qn),
                # 수취 배송비: None=배송비 포함 상품(수취 0 취급, Order 모델 주석과 정합)
                "collected": Decimal(str(ship_in)) if ship_in else Decimal("0"),
                # 지불 배송비 판별용 원천 응답(productOrder.deliveryAttributeType)
                "raw_data": raw,
            })
        return acc

    recent = _collect(cutoff)
    alltime = _collect(None)
    out: dict[str, dict] = {}
    for pid, all_rows in alltime.items():
        rows = recent.get(pid) or all_rows
        n = len(rows)
        total_qty = sum(r["quantity"] for r in rows)
        avg_qty = Decimal(total_qty) / Decimal(n) if n and total_qty > 0 else Decimal("1")
        if avg_qty <= 0:
            avg_qty = Decimal("1")
        # 지불: 건별 배송방식 가중평균(_order_shipping_cost가 raw_data로 일반/N배송 판별).
        ship_sum = sum((_order_shipping_cost(r) for r in rows), Decimal("0"))
        shipping = (ship_sum / Decimal(n)) if n else SHIPPING_COST_NORMAL
        # 수취: 주문당 평균(COALESCE(shipping_cost,0) — 무료배송 주문은 0으로 평균에 포함).
        collected = (sum((r["collected"] for r in rows), Decimal("0")) / Decimal(n)) if n else Decimal("0")
        net_ship = max(Decimal("0"), shipping - collected)  # ★보수 클램프(배송마진 이익 미인정)
        logistics = (net_ship / avg_qty).quantize(Decimal("0.01"), ROUND_HALF_UP)
        out[pid] = {"avg_qty": avg_qty, "shipping": shipping, "collected": collected,
                    "net_ship": net_ship, "logistics": logistics, "orders": n}
    return out


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
    반환: {rows, with_bep, commission_rate, commission_basis, aggressiveness}.

    D-NAO-57 (B): 광고 의사결정 BEP라 광고 경로 실효율(ad_commission_rate, 매출연동 언디루션)을
    우선 쓰고, 정산 표본 부족 등으로 산출 불가면 기존 블렌드(effective_commission_rate)로 폴백.
    어느 기준을 썼는지 commission_basis(ad_case/blended)로 정직 표기(행·반환 둘 다).
    D-NAO-57 (C, 리뷰 P2-1): logistics=상품별 (순배송원가 ÷ 평균 주문수량) — 순배송원가 =
    max(0, 지불 1,900 − 고객 수취 배송비 평균)(수취 실측: 채널 25.4% 주문이 유료배송). VAT는
    기존 관례대로 공헌이익 분자 안에서 ÷1.1(원가·수수료와 동일 — 지불·수취 배송비 모두
    부가세포함이라 이중차감/미차감 없음).
    """
    ad_rate = ad_commission_rate(db)
    if ad_rate is not None:
        rate = ad_rate["rate"]
        commission_basis = "ad_case"
    else:
        rate = effective_commission_rate(db)
        commission_basis = "blended"
    mult = AGG_MULT.get(aggressiveness, AGG_MULT["standard"])
    prices = _unit_prices(db)
    logistics_by_pid = _avg_qty_and_logistics(db)
    masters = {pm.id: (Decimal(str(pm.cost_price or 0)), pm.product_name or "")
               for pm in db.query(ProductMaster).all()}
    mappings = db.query(ProductChannelMapping).filter(
        ProductChannelMapping.channel_id == NAVER_CHANNEL_ID,
        ProductChannelMapping.is_active.is_(True),
    ).order_by(ProductChannelMapping.product_id).all()

    # 같은 channel_product_id에 중복 매핑 존재(라이브 22건, 실측: 네이버 옵션 1개가 기기
    # variant별 SKU 여러 개에 매핑된 경우 — 원가는 항상 동일해 금액에는 영향 없음).
    # cpid당 1개로 dedupe: 원가 있는 매핑 우선(BEP 산출 가능), 동률이면 product_id 최솟값
    # (위 order_by로 고정) → 재실행해도 같은 SKU가 결정적으로 선택됨.
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
    n_mapped_price = 0  # 판매가를 매핑 폴백에서 가져온 행 수(신규 상품 관측용)
    for m in best.values():
        # 판매가 우선순위: ①orders 실거래 중앙값(기본 — 실제로 팔린 값이 가장 정직하다)
        # ②매핑에 손으로 넣은 판매가(product_channel_mapping.selling_price).
        # ②는 **주문 이력이 아직 0건인 신규 상품** 전용 폴백이다(D-NAO-95). 종전엔 신규 상품이
        # sp=0 → has_cost=0 → bep_roas=NULL로 남았고, 그 상태의 캠페인은 상한 산출이 계정 평균
        # BEP로 내려앉는다(guardrail_gate._check_bid 주석 참조) — 즉 "판매가를 모른다"가 아니라
        # "판매가를 넣을 자리가 없다"가 문제였다. 추정이 아니라 커머스API 실판매가를 매핑에
        # 적어 넣는 경로이며, 주문이 한 건이라도 쌓이면 ①이 자동으로 이긴다(폴백은 스스로 은퇴).
        sp = prices.get(m.channel_product_id, Decimal("0"))
        price_basis = "orders"
        if sp <= 0 and m.selling_price:
            mapped = Decimal(str(m.selling_price))
            if mapped > 0:
                sp = mapped
                price_basis = "mapping"
        cost, master_name = masters.get(m.product_id, (Decimal("0"), ""))
        name = (m.channel_product_name or master_name or "")[:300]
        # D-NAO-57 (C): 상품별 단가당 순물류비(순배송원가 ÷ 평균 주문수량, 수취 배송비 차감).
        # 주문 이력 없으면 수취 0 가정 = 배송비 전액 차감(수량 1, 보수적) — 헬퍼 폴백과 정합.
        logistics = logistics_by_pid.get(
            m.channel_product_id, {"logistics": SHIPPING_COST_NORMAL}
        )["logistics"]
        if price_basis == "mapping":
            n_mapped_price += 1
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
            commission_basis=commission_basis,
            calculated_at=now,
        ))
        n_total += 1
    db.commit()
    log.info("naver_product_bep 산출: %d행(bep %d, 매핑판매가 %d) rate=%.4f 기준=%s 공격성=%s",
             n_total, n_bep, n_mapped_price, float(rate), commission_basis, aggressiveness)
    return {"rows": n_total, "with_bep": n_bep, "mapped_price_rows": n_mapped_price,
            "commission_rate": float(rate), "commission_basis": commission_basis,
            "aggressiveness": aggressiveness}
