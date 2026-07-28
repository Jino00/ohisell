# product_commission.py — SA(단일 책임: 네이버 상품별 실효 수수료율 산출, N1 · D-NAO-99)
#
# 존재 이유(ref 42 §2-②): BEP가 계정 단일 실효율(4.186%) 하나로 전 상품을 뭉개고 있었다.
# 라이브 건별 정산(naver_settlement_case) 실측은 그렇지 않다 —
#   주문관리 수수료 2.724% 고정 + 매출연동 {1.0%, 3.0%}(비 N배송) / {2.5%, 4.5%}(N배송)
#   → N배송 프리미엄 = 정확히 +1.5%p (도착보장 사용료. 120일 히스토그램에 이 4개 값 외 0건).
# 상품별 실효율은 3.7%~6.6%까지 벌어져, 단일 수수료율은 N배송 전환 상품의 BEP를 최대 3.6%
# 낙관 쪽으로 틀어 놓았다(입찰 상한 과대 → 클릭당 적자).
#
# ★이 SA는 "재는" 일만 한다(원칙 18-1). 창(window) 선택·N배송 혼합비 산출은 harness
#   (bep_calculator)의 몫이고, 이 모듈은 혼합비를 optional 입력으로 받아 요율을 합성한다
#   (원칙 18-8: SA는 다른 SA의 출력을 optional 파라미터로 받는다).
#
# 산식(ref 42 §3):
#   rate(상품) = 주문관리율(실측) + 기저 매출연동율(실측) + 1.5%p × N배송혼합비
#   기저 매출연동율 = (Σ매출연동수수료 − Σ(N배송 건의 gross × 1.5%p)) ÷ Σgross
#     → N배송 프리미엄을 걷어낸 "배송방식 중립" 요율. 여기에 harness가 준 **현재** 혼합비를
#       다시 얹기 때문에, 과거 창의 혼합비가 요율에 이중 반영되지 않는다.
#
# ★표본 민감 항목이라 창은 넓게 잡는다(ref 42 §0-6: 창을 일괄 단축하면 저볼륨 상품에서
#   잡음이 신호를 압도). 기본 창 = 120일(ref 42 §3), 상품 표본 < _MIN_PRODUCT_ROWS면 계정 실측 폴백.
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import NaverSettlementCase, Order
from app.utils.kst import kst_today

log = logging.getLogger(__name__)

NAVER_CHANNEL_ID = 6
NBAESONG_ATTR = "ARRIVAL_GUARANTEE"

# N배송(도착보장) 프리미엄 — 실측 상수(ref 42 §2-②: 비N배송 대비 정확히 +1.5%p).
NBAESONG_COMMISSION_PREMIUM = Decimal("0.015")

_MIN_PRODUCT_ROWS = 5        # 상품 표본이 이보다 적으면 계정 실측으로 폴백(ref 42 §3)
_MAX_PLAUSIBLE_RATE = Decimal("0.20")  # 산출 요율이 20%↑면 데이터 이상 → 사용 불가 처리
_COMMISSION_WINDOW_DAYS = 120  # 표본 민감 항목이라 넓은 창(ref 42 §3)


@dataclass
class _Component:
    """수수료 구성 누적기 — gross 가중 합계."""

    gross: Decimal = Decimal("0")
    mgmt: Decimal = Decimal("0")          # 주문관리 수수료 합(절대값)
    base_interlock: Decimal = Decimal("0")  # 매출연동 수수료 합에서 N배송 프리미엄을 제거한 값
    rows: int = 0
    nbaesong_rows: int = 0

    def add(self, gross: Decimal, mgmt: Decimal, interlock: Decimal, is_nbaesong: bool) -> None:
        self.gross += gross
        self.mgmt += mgmt
        self.base_interlock += interlock - (gross * NBAESONG_COMMISSION_PREMIUM if is_nbaesong else Decimal("0"))
        self.rows += 1
        if is_nbaesong:
            self.nbaesong_rows += 1

    @property
    def mgmt_rate(self) -> Decimal:
        return self.mgmt / self.gross if self.gross > 0 else Decimal("0")

    @property
    def base_interlock_rate(self) -> Decimal:
        # 음수 방어: 프리미엄 제거가 과해질 수 있는 경계(반품·부분취소 행)에서 0으로 클램프.
        return max(Decimal("0"), self.base_interlock) / self.gross if self.gross > 0 else Decimal("0")

    @property
    def usable(self) -> bool:
        if self.gross <= 0 or self.rows <= 0:
            return False
        return Decimal("0") < self.mgmt_rate + self.base_interlock_rate <= _MAX_PLAUSIBLE_RATE


@dataclass
class ProductCommissionTable:
    """상품별 실측 수수료 구성 + 요율 합성기.

    available=False면 정산 표본이 없거나 비현실적이라 **호출부가 기존 계정 단일 요율로
    폴백해야 한다**(동작 회귀 0 — 정산 데이터가 없는 환경/테스트는 종전과 동일하게 흐른다).
    """

    account: _Component = field(default_factory=_Component)
    products: dict[str, _Component] = field(default_factory=dict)
    window_days: int | None = None
    min_rows: int = _MIN_PRODUCT_ROWS
    matched_by_line: int = 0     # 정확 키(product_order_id ↔ platform_order_line_id) 매칭 수
    matched_by_fallback: int = 0  # 유일 후보 폴백 매칭 수
    unmatched: int = 0            # 주문 귀속 실패(집계 제외) 수

    @property
    def available(self) -> bool:
        return self.account.usable

    def rate_for(self, product_id: str | None, nbaesong_share=Decimal("0")) -> tuple[Decimal, str]:
        """(요율, 근거) — 근거는 'delivery_case'(상품 실측) / 'delivery_acct'(계정 실측).

        nbaesong_share: harness가 산출한 **현재** N배송 혼합비(0~1, 원칙 18-6의 SA간 정보 유통).
        기본 0이면 배송방식 중립 요율(= 일반배송 기준)을 돌려준다.
        ★float/int/str도 받아 Decimal로 강제 변환한다 — 호출부가 float를 넘겼을 때
        Decimal×float TypeError로 BEP 산출 전체가 죽는 것을 막는다(입력 검증).
        """
        comp = self.products.get(product_id or "")
        if comp is not None and comp.rows >= self.min_rows and comp.usable:
            basis = "delivery_case"
        else:
            comp, basis = self.account, "delivery_acct"
        share = _as_share(nbaesong_share)
        rate = comp.mgmt_rate + comp.base_interlock_rate + NBAESONG_COMMISSION_PREMIUM * share
        return rate, basis


def measure(db: Session, *, window_days: int | None = _COMMISSION_WINDOW_DAYS,
            min_rows: int = _MIN_PRODUCT_ROWS) -> ProductCommissionTable:
    """건별 정산(PROD_ORDER) × 주문 배송구분 → 상품별 수수료 구성 실측.

    ★매칭 키 = `naver_settlement_case.product_order_id ↔ orders.platform_order_line_id`
      (둘 다 네이버 productOrderId 그레인 = **원자 주문라인**, 라이브 실측 유일성 10,075/10,075).
      종전 후보였던 (order_number, product_id)는 **양쪽 어디서도 유일하지 않다** — 같은 주문+상품이
      부분취소/부분배송으로 2개 productOrderId로 쪼개지면(라이브 1.4%, Order 모델 주석) 정산 1행이
      주문 2행과 붙어 데카르트 중복이 난다. 라이브 실측: 6,910행 → 7,074행(+2.4%, 최악 상품 +20%).
      중복 자체보다 나쁜 것은 **혼합 배송구분 분할 주문**이다 — N배송 라인이 중복 계수되면 기저
      매출연동에서 프리미엄이 과도하게 빠져 요율이 낙관(=상한 과대) 쪽으로 틀어진다.

    미매칭(라이브 13/6,910 = 0.19% — 정산 productOrderId가 주문 라인 id와 끝자리가 다른 케이스)은
    **(order_number, product_id) 후보가 정확히 1건일 때만** 폴백 매칭한다. 실측 13건 전부 후보 1건이라
    전량 회수되고, 후보가 2건 이상이면(=분할 주문) 애매하므로 집계에서 제외하고 건수를 로그로 남긴다.
    조용히 버리지도, 애매한 것을 억지로 붙이지도 않는다.

    배송방식은 orders.delivery_attribute_type(N0 영속 컬럼, 백필 99.99%) 하나만 본다.
    raw_data 폴백을 쓰지 않는 이유: 정산 행 전체에 대해 JSON을 재파싱하면 비용이 크고,
    미충전 행은 라이브 1건이라 일반배송 폴백과 결과가 같다.

    window_days: 기본 120일(ref 42 §3 — 표본 민감 항목이라 넓은 창). 상품별로 그 창을 쓰되,
    창 안 표본이 0이면 해당 상품만 전기간으로 폴백한다(표본 소실 방지). None이면 전기간.
    """
    by_line: dict[str, tuple] = {}
    by_order_product: dict[tuple, list[tuple]] = defaultdict(list)
    for line_id, order_number, pid, attr, order_date in db.query(
        Order.platform_order_line_id, Order.order_number, Order.platform_product_id,
        Order.delivery_attribute_type, Order.order_date,
    ).filter(Order.channel_id == NAVER_CHANNEL_ID).all():
        if not pid:
            continue
        rec = (pid, attr, order_date)
        if line_id:
            by_line[line_id] = rec
        by_order_product[(order_number, pid)].append(rec)

    table = ProductCommissionTable(window_days=window_days, min_rows=min_rows)
    cutoff = None if window_days is None else kst_today() - timedelta(days=window_days)
    by_pid: dict[str, list[tuple]] = {}
    for po_id, order_id, case_pid, gross, mgmt, interlock, free_inst in db.query(
        NaverSettlementCase.product_order_id,
        NaverSettlementCase.order_id,
        NaverSettlementCase.product_id,
        NaverSettlementCase.pay_settle_amount,
        NaverSettlementCase.total_pay_commission,
        NaverSettlementCase.selling_interlock_commission,
        NaverSettlementCase.free_installment_commission,
    ).filter(NaverSettlementCase.product_order_type == "PROD_ORDER").all():
        rec = by_line.get(po_id) if po_id else None
        if rec is not None:
            table.matched_by_line += 1
        else:
            cands = by_order_product.get((order_id, case_pid)) or []
            if len(cands) != 1:  # 0건=주문 미수집 / 2건↑=분할 주문이라 귀속 애매 → 제외
                table.unmatched += 1
                continue
            rec = cands[0]
            table.matched_by_fallback += 1
        pid, attr, order_date = rec
        g = Decimal(str(gross or 0))
        if g <= 0:
            continue
        by_pid.setdefault(pid, []).append((
            order_date,
            g,
            abs(Decimal(str(mgmt or 0))),
            abs(Decimal(str(interlock or 0))) + abs(Decimal(str(free_inst or 0))),
            attr == NBAESONG_ATTR,
        ))

    for pid, lst in by_pid.items():
        picked = lst
        if cutoff is not None:
            recent = [r for r in lst if r[0] is not None and _as_date(r[0]) >= cutoff]
            picked = recent or lst
        comp = _Component()
        for _od, g, mgmt, interlock, is_nb in picked:
            comp.add(g, mgmt, interlock, is_nb)
            table.account.add(g, mgmt, interlock, is_nb)
        table.products[pid] = comp

    if table.unmatched:
        log.info("product_commission: 정산 %d행 주문 귀속 실패(집계 제외) — 정확키 %d · 폴백 %d",
                 table.unmatched, table.matched_by_line, table.matched_by_fallback)
    if not table.available and table.account.rows:
        log.warning("product_commission: 계정 실측 요율이 비현실적(rows=%d gross=%s mgmt=%s interlock=%s) "
                    "→ 호출부가 계정 단일 요율로 폴백",
                    table.account.rows, table.account.gross,
                    table.account.mgmt_rate, table.account.base_interlock_rate)
    return table


def _as_date(value):
    """order_date(DateTime|date) → date."""
    return value.date() if hasattr(value, "date") else value


def _as_share(value) -> Decimal:
    """N배송 혼합비 입력 정규화 — Decimal 강제 + [0,1] 클램프. 변환 불가면 0(보수)."""
    if value is None:
        return Decimal("0")
    if not isinstance(value, Decimal):
        try:
            value = Decimal(str(value))
        except (ValueError, TypeError, ArithmeticError):
            return Decimal("0")
    if value <= 0:
        return Decimal("0")
    return Decimal("1") if value > 1 else value
