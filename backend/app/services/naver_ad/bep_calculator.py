# bep_calculator.py — bep_calculator_sa (단일 책임: 네이버 상품별 BEP ROAS 산출)
# D-NAO-8: product_master 원가 × orders 실거래 단가 × 실효 수수료율 → 상품별 손익분기 ROAS.
#   target_roas = bep_roas × 공격성 배수(안전1.3/표준1.15/공격1.05, D-NAO-2).
# 판매가 소스: ①orders 실거래가(기본) — selling_price는 라인총액이라 수량으로 나눠 단가
#   정규화 후 상품별 median(프로모 완화). ②주문 0건이면 product_channel_mapping.selling_price
#   폴백(D-NAO-95, 신규 상품 온보딩용 — 사람이 커머스API 실판매가를 적어 넣는 자리).
#   ★②로 산출된 행은 "실거래로 검증된 판매가"가 아니다(운영자 입력값) — 주문이 한 건이라도
#   생기면 ①이 이겨 자동 은퇴한다. 행 단위 출처 컬럼은 아직 없다(스키마 변경 별건).
# 참고 메모리: bep-roas-calculation-structure (BEP ROAS = 판매가 ÷ 공헌이익).
# ★N1 (D-NAO-99, ref 42): 이 모듈은 harness다 — 입력 3개를 각각의 SA에서 받아 조합한다.
#   판매가·N배송 혼합비 = 최근 10건 표본(레짐 변수, D-NAO-100) / 평균수량·수취배송비 = 넓은 창 /
#   수수료율 = product_commission SA(상품별 실측 + N배송 프리미엄). 산식 자체는 불변.
from __future__ import annotations

import logging
import statistics
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import delete, func as sqlfunc
from sqlalchemy.orm import Session

from app.models import (
    Channel, NaverProductBep, NaverSettlementCase, NaverSettlementDaily, Order,
    ProductChannelMapping, ProductMaster,
)
from app.services import order_delivery
from app.services.naver_ad import product_commission
from app.utils.kst import kst_now, kst_today

log = logging.getLogger(__name__)

NAVER_CHANNEL_ID = 6
VAT_DIVISOR = Decimal("1.1")  # 매출 VAT 포함 → 실수취 (NaverOps 이익회계와 정합)
# 공격성 배수 (D-NAO-2): target_roas = bep_roas × 배수
AGG_MULT = {"safe": Decimal("1.30"), "standard": Decimal("1.15"), "aggressive": Decimal("1.05")}
_DEFAULT_COMMISSION_RATE = Decimal("0.055")  # 정산·채널 모두 없을 때 최종 폴백
_PRICE_WINDOW_DAYS = 120  # 넓은 창 — 표본 민감 항목(평균 수량·수취 배송비) 전용

# ── D-NAO-100 (ref 43): 레짐 변수 표본 = 최근 N건(달력 상한 120일) — 백테스트 승자 E5c ──
# 판매가·N배송 혼합비를 **같은 최근 10건**에서 뽑는다. 종전 N1의 적응형 사다리(7→14→30→60→120,
# nmin=10)는 폐기 — 창이 계단으로 점프하며 표본을 통째로 갈아치워 일간 ±10~17% 왕복을 만들었다
# (레짐 변동 25종 공정구간 왕복 12회). 최근 N건은 주문이 하나 들어오고 하나 나가는 연속 이동이라
# 같은 실효 창 길이에서도 왕복이 2회뿐이고, 정확도(MAPE 3.50 vs 4.03)·인하 감지(20일 7/7 vs
# 25일 6/7)까지 사다리를 3축 전부에서 지배한다(ref 43 §0·§2·§4).
# N=10은 내부 최적점(N=5는 편향 +1.21%, N=20↑는 정확도 붕괴). 사다리와 달리 N에 둔감하다(§5).
_RECENT_SAMPLE_SIZE = 10          # E5c의 N
_RECENT_SAMPLE_MAX_AGE_DAYS = 120  # 달력 상한 — 신선도 하한을 잃지 않기 위한 안전장치(§6)
_EPOCH_DATE = date(1970, 1, 1)  # order_date 결측 행 정렬용 sentinel

# ── D-NAO-57 (C) 배송비: 건당 단가(부가세포함, Jino 확정 2026-07-18) ──
# ★단가·판별의 단일 진실 원천 = services/order_delivery.py (Jino 지시 2026-07-28로 이관).
#   여기서는 이름만 재수출한다 — 기존 소비자·테스트가 그대로 동작하도록.
SHIPPING_COST_NORMAL = order_delivery.SHIPPING_COST_NORMAL      # 일반배송 건당 1,900
SHIPPING_COST_NBAESONG = order_delivery.SHIPPING_COST_NBAESONG  # N배송 건당 3,020 (D-NAO-84)
SHIPPING_COST_BY_METHOD = order_delivery.SHIPPING_COST_BY_METHOD

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
    """주문 1건의 배송비(건당, 부가세포함) — 배송방식 판별 (D-NAO-57 C · D-NAO-84 실배선).

    ★판별 로직은 services/order_delivery.py로 이관됐다(Jino 지시 2026-07-28 — 동기화·백필·BEP가
    같은 함수를 쓰게). 이 함수는 그 SA로 위임하는 얇은 훅이고, **계약은 그대로**다:
      orders.shipping_cost_paid(영속 컬럼, 주문 시점 단가 스냅샷) 우선 →
      없으면 raw_data의 productOrder.deliveryAttributeType == "ARRIVAL_GUARANTEE" → N배송(3,020) →
      그 외·필드 부재·파싱 실패·raw_data 부재 → 일반배송(1,900) fail-safe.
    백필이 쓰는 값 = 폴백 파싱이 내는 값이라 두 경로의 산출은 항상 같다(회귀 0).

    order_row: _avg_qty_and_logistics의 경량 행 dict 또는 같은 속성을 가진 ORM 행. None이면 일반배송."""
    return order_delivery.order_shipping_cost(order_row)


def _is_nbaesong(row) -> bool:
    """주문 1건이 N배송(도착보장)인가 — 영속 컬럼 우선, raw_data 폴백.

    판별 계약은 order_delivery SA 그대로(단일 판별자 deliveryAttributeType). 컬럼이 NULL인
    행(백필 이전·판별 불가)만 raw_data를 파싱한다."""
    attr = row.get("delivery_attribute_type") if isinstance(row, dict) else getattr(
        row, "delivery_attribute_type", None)
    if attr is None:
        raw = row.get("raw_data") if isinstance(row, dict) else getattr(row, "raw_data", None)
        po = order_delivery.product_order_of(raw)
        attr = po.get("deliveryAttributeType") if po else None
    return order_delivery.shipping_method_of(attr) == order_delivery.METHOD_NBAESONG


def _naver_order_rows(db: Session) -> dict[str, list[dict]]:
    """네이버 주문(수량>0)을 상품별로 1회만 적재 — 날짜 내림차순.

    ★종전엔 창별로 같은 스캔을 여러 번 돌렸다. 레짐 표본은 상품마다 "최근 N건"이라 SQL에서
    창을 미리 자를 수 없으므로, 한 번 읽고 파이썬에서 잘라 쓴다(주문 1만 건 규모).

    행 dict: quantity / unit_price(None=판매가 0) / collected(고객 수취) / nbaesong(배송방식) /
             order_date. 지불 배송비는 값이 아니라 **혼합비**로 쓰므로 방식만 남긴다.
    """
    rows = db.query(
        Order.platform_product_id, Order.quantity, Order.selling_price, Order.shipping_cost,
        Order.order_date, Order.delivery_attribute_type, Order.raw_data,
    ).filter(
        Order.channel_id == NAVER_CHANNEL_ID,
        Order.quantity > 0,
    ).all()
    out: dict[str, list[dict]] = {}
    for pid, qn, sp, ship_in, od, attr, raw in rows:
        if not pid:
            continue
        qty = int(qn)
        price = Decimal(str(sp)) if sp else Decimal("0")
        out.setdefault(pid, []).append({
            "quantity": qty,
            "unit_price": (price / Decimal(qty)) if price > 0 else None,
            # 수취 배송비: None=배송비 포함 상품(수취 0 취급, Order 모델 주석과 정합)
            "collected": Decimal(str(ship_in)) if ship_in else Decimal("0"),
            "nbaesong": _is_nbaesong({"delivery_attribute_type": attr, "raw_data": raw}),
            "order_date": od.date() if hasattr(od, "date") else od,
        })
    for lst in out.values():
        lst.sort(key=lambda r: r["order_date"] or _EPOCH_DATE, reverse=True)
    return out


def _recent_sample(rows: list[dict], n: int = _RECENT_SAMPLE_SIZE) -> tuple[list[dict], bool]:
    """레짐 변수 표본 = **최근 n건**(달력 상한 _RECENT_SAMPLE_MAX_AGE_DAYS 내). D-NAO-100 · ref 43.

    rows는 날짜 내림차순이라 슬라이스가 곧 "최근 n건"이다(무상태 — `ORDER BY order_date DESC
    LIMIT n`과 같은 물건). n건 미만이면 상한 내 있는 만큼 쓴다.
    상한 안에 **0건이면 전기간 최근 n건**으로 폴백한다(ref 43 §7) — 상한은 신선도 하한이지
    표본을 없애는 장치가 아니다. 라이브 45개 상품이 120일간 주문이 없어, 여기서 표본을 비우면
    판매가가 매핑 폴백(대개 미입력)으로 떨어져 BEP 행이 통째로 사라진다.

    반환: (표본, 달력상한_내부인가). 두 번째 값은 진단·로그용."""
    if not rows:
        return [], True
    cut = kst_today() - timedelta(days=_RECENT_SAMPLE_MAX_AGE_DAYS)
    fresh = [r for r in rows if r["order_date"] and r["order_date"] >= cut]
    if fresh:
        return fresh[:n], True
    return rows[:n], False


def _avg_qty_and_logistics(db: Session, *, orders_by_pid: dict[str, list[dict]] | None = None,
                           sample_size: int = _RECENT_SAMPLE_SIZE) -> dict[str, dict]:
    """네이버 상품(channel_product_id)별 평균 주문수량 + 단가당 **순**물류비(D-NAO-57 C, 리뷰 P2-1).

    logistics(단가당) = 상품별 순배송원가(건당) ÷ 평균 주문수량.
      순배송원가 net_ship = max(0, 지불 배송비 − 평균 수취 배송비)
        - 지불 배송비 = 1,900 + 1,120 × **N배송 혼합비** (N1 · D-NAO-99).
          ★혼합비는 **레짐 변수라 최근 10건 표본**에서 뽑는다(D-NAO-100 — 판매가와 같은 행
          집합). N배송은 2026-07-22 시작이라 120일 창이 이 레짐 전환을 1~29%로 희석해,
          전환 상품의 물류비를 최대 −766원 낙관 쪽으로 틀어 놓았다(ref 42 §2-①).
          값 자체는 종전과 같은 가중평균 형태다
          (mean(paid) = 1,900·(1−p) + 3,020·p = 1,900 + 1,120p).
          ★비율은 창이 바뀔 때 판매가보다 더 크게 튄다 — 사다리를 판매가에서만 걷어내면
          물류비가 계속 계단 진동을 탄다(ref 43 §7 말미). 그래서 함께 교체했다.
          단 N배송은 07-22 시작이라 백테스트할 과거가 없다 — 근거는 "같은 메커니즘이니 같은
          병에 걸린다"는 구조 논증이지 실측이 아니다(ref 43 §8-4).
        - 수취 배송비: Order.shipping_cost(고객이 낸 deliveryFeeAmount) 실측 평균 —
          라이브 실측(120일): 채널 25.4% 주문이 수취(상품별 무료/유료 혼합이 사실).
        - ★max(0,·) 클램프 = 보수 방향: 수취가 지불을 초과해도 배송 마진을 이익(음수 물류비)으로
          잡지 않는다 — BEP를 낙관 쪽으로 움직이는 오차를 구조적으로 차단.
    ★평균 주문수량·수취 배송비는 **표본 민감 항목이라 넓은 창(_PRICE_WINDOW_DAYS)을 유지**한다
    (창을 같이 줄이면 잡음이 폭증 — ref 42 §0-6). 주문 없는 상품은 수량 1 + 수취 0 폴백
    (= 배송비 전액 차감, 보수적).

    반환: {cpid: {"avg_qty", "shipping"(지불), "collected"(수취 평균), "net_ship",
                  "logistics"(단가당), "orders", "nb_share", "nb_sample", "nb_fresh"}}
    """
    by_pid = orders_by_pid if orders_by_pid is not None else _naver_order_rows(db)
    cutoff = kst_today() - timedelta(days=_PRICE_WINDOW_DAYS)
    out: dict[str, dict] = {}
    for pid, all_rows in by_pid.items():
        # 넓은 창(없으면 전기간 폴백) — 평균 수량·수취 배송비 전용.
        wide = [r for r in all_rows if r["order_date"] and r["order_date"] >= cutoff] or all_rows
        n = len(wide)
        total_qty = sum(r["quantity"] for r in wide)
        avg_qty = Decimal(total_qty) / Decimal(n) if n and total_qty > 0 else Decimal("1")
        if avg_qty <= 0:
            avg_qty = Decimal("1")
        # 지불: 최근 10건 표본의 N배송 혼합비 × 단가차(레짐 변수 — 판매가와 같은 행 집합).
        recent, fresh = _recent_sample(all_rows, sample_size)
        nb_share = (Decimal(sum(1 for r in recent if r["nbaesong"])) / Decimal(len(recent))
                    if recent else Decimal("0"))
        shipping = SHIPPING_COST_NORMAL + (SHIPPING_COST_NBAESONG - SHIPPING_COST_NORMAL) * nb_share
        # 수취: 주문당 평균(COALESCE(shipping_cost,0) — 무료배송 주문은 0으로 평균에 포함).
        collected = (sum((r["collected"] for r in wide), Decimal("0")) / Decimal(n)) if n else Decimal("0")
        net_ship = max(Decimal("0"), shipping - collected)  # ★보수 클램프(배송마진 이익 미인정)
        logistics = (net_ship / avg_qty).quantize(Decimal("0.01"), ROUND_HALF_UP)
        out[pid] = {"avg_qty": avg_qty, "shipping": shipping, "collected": collected,
                    "net_ship": net_ship, "logistics": logistics, "orders": n,
                    "nb_share": nb_share, "nb_sample": len(recent), "nb_fresh": fresh}
    return out


def _unit_prices(db: Session, *, orders_by_pid: dict[str, list[dict]] | None = None,
                 sample_size: int = _RECENT_SAMPLE_SIZE) -> dict[str, Decimal]:
    """네이버 상품(channel_product_id)별 대표 단가 = **최근 10건 median**(D-NAO-100 · ref 43 E5c).

    판매가는 레짐 변수다. 120일 median은 인하 전 구가격을 오래 붙들어(예: 16프로 18,900 vs
    실제 15,900) BEP를 낙관 쪽으로 틀고, 인하 7건 중 5건을 끝내 못 따라갔다(중앙 84일).
    반대로 N1의 적응형 사다리는 창 계단을 점프하며 하루 만에 왕복했다(왕복 12회).
    최근 N건은 표본이 한 건씩 밀려 나가는 연속 이동이라 **정확도·안정성·전환 감지 3축 전부에서**
    사다리를 이긴다(MAPE 3.50 vs 4.03 / 왕복 2 vs 12 / 인하 20일 7/7 vs 25일 6/7).

    표본 = _recent_sample(달력 상한 120일, 없으면 전기간 최근 N건). 그중 판매가가 있는 주문의
    단가 median, 원 단위 반올림. 표본에 유효 판매가가 하나도 없으면(데이터 이상) 전기간
    최근 N건의 판매가 있는 주문으로 폴백한다. 주문 자체가 0건이면 여기서 값을 내지 않고
    호출부의 매핑 판매가 폴백(D-NAO-95)이 받는다.
    """
    by_pid = orders_by_pid if orders_by_pid is not None else _naver_order_rows(db)
    prices: dict[str, Decimal] = {}
    for pid, all_rows in by_pid.items():
        priced = [r for r in all_rows if r["unit_price"] is not None]
        if not priced:
            continue
        recent, _fresh = _recent_sample(all_rows, sample_size)
        src = [r["unit_price"] for r in recent if r["unit_price"] is not None]
        if not src:  # 표본 전부가 판매가 0(데이터 이상) → 판매가 있는 최근 N건으로 폴백
            src = [r["unit_price"] for r in _recent_sample(priced, sample_size)[0]]
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
    max(0, 지불 − 고객 수취 배송비 평균)(수취 실측: 채널 25.4% 주문이 유료배송). VAT는
    기존 관례대로 공헌이익 분자 안에서 ÷1.1(원가·수수료와 동일 — 지불·수취 배송비 모두
    부가세포함이라 이중차감/미차감 없음).

    ★N1 (D-NAO-99, ref 42) — 배송방식 인지형 정합. **산식(공헌이익·BEP 정의)은 불변**이고,
    3개 입력의 산출 창·입자도만 바뀐다:
      ① 판매가        → 최근 10건 표본의 median (레짐 변수)
      ② N배송 혼합비  → 같은 표본의 비율 → 지불 배송비 = 1,900 + 1,120 × 혼합비
      ③ 수수료율      → 계정 단일값 폐기, product_commission SA의 상품별 실측
                        (주문관리 + 기저 매출연동 + 1.5%p × N배송 혼합비)
    셋은 곱셈적으로 결합해 N배송 전환 + 가격 인하가 겹친 라인에서 BEP를 크게 움직인다
    (레짐 변동 25종 안팎, 나머지 500종은 |Δ|<1%).
    ★①②의 표본 규칙은 D-NAO-100(ref 43 백테스트)에서 적응형 사다리 → 최근 N건으로 교체됐다.
    ③의 실측 표본이 없으면(정산 미수집 환경·테스트) 종전 계정 단일 요율로 폴백해 회귀 0.
    ★언디루션 무효(ad_commission_rate가 shopping_share=1.0이라 항등)는 N1 범위 밖 — N3 이월
    (ref 42 §6). 배송비 수취분에 붙는 주문관리 수수료(건당 81원)도 미모델링(§7-7, 별건).
    """
    ad_rate = ad_commission_rate(db)
    if ad_rate is not None:
        fallback_rate = ad_rate["rate"]
        fallback_basis = "ad_case"
    else:
        fallback_rate = effective_commission_rate(db)
        fallback_basis = "blended"
    # 상품별 실측 수수료(N1 ③). 표본이 없으면 available=False → 계정 단일 요율로 폴백.
    commissions = product_commission.measure(db)
    rate = fallback_rate            # 반환·로그용 대표값(계정 수준)
    commission_basis = fallback_basis
    if commissions.available:
        rate, commission_basis = commissions.rate_for(None)  # 계정 실측(배송 중립) 대표값
    mult = AGG_MULT.get(aggressiveness, AGG_MULT["standard"])
    order_rows = _naver_order_rows(db)  # 주문 1회 적재 → 두 헬퍼가 공유(창만 달리 자름)
    prices = _unit_prices(db, orders_by_pid=order_rows)
    logistics_by_pid = _avg_qty_and_logistics(db, orders_by_pid=order_rows)
    masters = {pm.id: (Decimal(str(pm.cost_price or 0)), pm.product_name or "")
               for pm in db.query(ProductMaster).all()}
    mappings = db.query(ProductChannelMapping).filter(
        ProductChannelMapping.channel_id == NAVER_CHANNEL_ID,
        ProductChannelMapping.is_active.is_(True),
    ).order_by(ProductChannelMapping.product_id).all()

    # 같은 channel_product_id에 중복 매핑 존재(라이브 22건, 실측: 네이버 옵션 1개가 기기
    # variant별 SKU 여러 개에 매핑된 경우 — 원가는 항상 동일).
    # cpid당 1개로 dedupe: ①원가 있는 매핑 우선(BEP 산출 가능) ②원가 동률이면 판매가 있는
    # 매핑 우선 ③그래도 동률이면 product_id 최솟값(위 order_by로 고정) → 재실행 결정적.
    # ★②는 D-NAO-95에서 추가. 종전 주석은 "원가는 항상 동일해 금액에는 영향 없음"을 근거로
    # 타이브레이크가 무해하다고 적었는데, 판매가 폴백이 생긴 뒤로 그 근거가 깨졌다 —
    # 중복 매핑 중 값이 적힌 행이 지면 (ⓐ)판매가를 적어도 조용히 무시되고, 반대로 낡은 값이
    # 이기면 (ⓑ)상한이 부풀어 과지출 방향으로 틀어진다. ②로 ⓐ를 막고, 값이 서로 다른
    # 중복은 경고로 표면화해 ⓑ가 조용히 지나가지 않게 한다.
    best: dict[str, ProductChannelMapping] = {}
    for m in mappings:
        cur = best.get(m.channel_product_id)
        if cur is None:
            best[m.channel_product_id] = m
            continue
        cur_cost = masters.get(cur.product_id, (Decimal("0"), ""))[0]
        new_cost = masters.get(m.product_id, (Decimal("0"), ""))[0]
        cur_price = Decimal(str(cur.selling_price or 0))
        new_price = Decimal(str(m.selling_price or 0))
        if cur_price > 0 and new_price > 0 and cur_price != new_price:
            log.warning(
                "naver_product_bep: cpid=%s 중복 매핑의 판매가 불일치(%s vs %s) — 낡은 값이 "
                "BEP 상한을 왜곡할 수 있음. 매핑 정리 필요.",
                m.channel_product_id, cur_price, new_price,
            )
        if new_cost > 0 and cur_cost <= 0:
            best[m.channel_product_id] = m
        elif (new_cost > 0) == (cur_cost > 0) and new_price > 0 and cur_price <= 0:
            best[m.channel_product_id] = m

    db.execute(delete(NaverProductBep).where(NaverProductBep.channel_id == NAVER_CHANNEL_ID))
    now = kst_now()
    n_total = 0
    n_bep = 0
    n_mapped_price = 0      # 판매가를 매핑 폴백에서 가져온 행 수
    n_mapped_with_bep = 0   # 그중 실제로 BEP까지 산출된 행 수(원가까지 있어야 함)
    n_case_rate = 0         # 상품별 실측 수수료율이 적용된 행 수(N1 — 나머지는 계정 실측)
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
        logi_row = logistics_by_pid.get(
            m.channel_product_id, {"logistics": SHIPPING_COST_NORMAL, "nb_share": Decimal("0")}
        )
        logistics = logi_row["logistics"]
        # N1 ③: 상품별 실측 수수료율 + 그 상품의 현재 N배송 혼합비(원칙 18-6 — harness가
        # 물류 SA의 출력을 수수료 SA의 입력으로 유통시킨다). 실측 표본이 없으면 계정 폴백.
        nb_share = logi_row.get("nb_share") or Decimal("0")
        if commissions.available:
            row_rate, row_basis = commissions.rate_for(m.channel_product_id, nb_share)
            if row_basis == "delivery_case":
                n_case_rate += 1
        else:
            row_rate, row_basis = fallback_rate, fallback_basis
        has_cost = sp > 0 and cost > 0
        commission = sp * row_rate
        contribution = (sp - commission - cost - logistics) / VAT_DIVISOR if has_cost else Decimal("0")
        bep = None
        target = None
        if has_cost and contribution > 0:
            bep = (sp / contribution).quantize(Decimal("0.0001"), ROUND_HALF_UP)
            target = (bep * mult).quantize(Decimal("0.0001"), ROUND_HALF_UP)
            n_bep += 1
            if price_basis == "mapping":
                n_mapped_with_bep += 1
        if price_basis == "mapping":
            n_mapped_price += 1
        db.add(NaverProductBep(
            channel_id=NAVER_CHANNEL_ID,
            channel_product_id=m.channel_product_id,
            product_master_id=m.product_id,
            product_name=name,
            selling_price=sp,
            cost_price=cost,
            commission_rate=row_rate.quantize(Decimal("0.0001"), ROUND_HALF_UP),
            logistics_cost=logistics,
            contribution_margin=contribution.quantize(Decimal("0.01"), ROUND_HALF_UP),
            bep_roas=bep,
            aggressiveness=aggressiveness,
            target_roas=target,
            has_cost=has_cost,
            commission_basis=row_basis,
            calculated_at=now,
        ))
        n_total += 1
    db.commit()
    log.info("naver_product_bep 산출: %d행(bep %d, 매핑판매가 %d→bep %d, 상품실측요율 %d) "
             "rate=%.4f 기준=%s 공격성=%s 표본=최근%d건",
             n_total, n_bep, n_mapped_price, n_mapped_with_bep, n_case_rate, float(rate),
             commission_basis, aggressiveness, _RECENT_SAMPLE_SIZE)
    return {"rows": n_total, "with_bep": n_bep, "mapped_price_rows": n_mapped_price,
            "mapped_price_with_bep": n_mapped_with_bep,
            "product_rate_rows": n_case_rate,
            "commission_rate": float(rate), "commission_basis": commission_basis,
            "aggressiveness": aggressiveness}
