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
#   잡음이 신호를 압도). 기본 = 전기간, 상품 표본 < _MIN_PRODUCT_ROWS면 계정 실측으로 폴백.
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import and_
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

    @property
    def available(self) -> bool:
        return self.account.usable

    def rate_for(self, product_id: str | None, nbaesong_share: Decimal = Decimal("0")) -> tuple[Decimal, str]:
        """(요율, 근거) — 근거는 'delivery_case'(상품 실측) / 'delivery_acct'(계정 실측).

        nbaesong_share: harness가 산출한 **현재** N배송 혼합비(0~1, 원칙 18-6의 SA간 정보 유통).
        기본 0이면 배송방식 중립 요율(= 일반배송 기준)을 돌려준다.
        """
        comp = self.products.get(product_id or "")
        if comp is not None and comp.rows >= self.min_rows and comp.usable:
            basis = "delivery_case"
        else:
            comp, basis = self.account, "delivery_acct"
        share = nbaesong_share if nbaesong_share and nbaesong_share > 0 else Decimal("0")
        if share > 1:
            share = Decimal("1")
        rate = comp.mgmt_rate + comp.base_interlock_rate + NBAESONG_COMMISSION_PREMIUM * share
        return rate, basis


def measure(db: Session, *, window_days: int | None = None,
            min_rows: int = _MIN_PRODUCT_ROWS) -> ProductCommissionTable:
    """건별 정산(PROD_ORDER) × 주문 배송구분 → 상품별 수수료 구성 실측.

    조인 키는 (order_number, product_id) — NaverSettlementCase 모델 주석의 매칭 규약 그대로.
    배송방식은 orders.delivery_attribute_type(N0 영속 컬럼, 백필 완료) 하나만 본다.
    raw_data 폴백을 쓰지 않는 이유: 정산 행 전체에 대해 JSON을 재파싱하면 비용이 크고,
    미충전 행은 라이브 1건(99.99% 커버리지)이라 일반배송 폴백과 결과가 같다.

    window_days=None → 전기간(기본). 창을 주면 상품별로 그 창을 쓰되, 창 안 표본이 0이면
    해당 상품만 전기간으로 폴백한다(표본 소실 방지).
    """
    rows = db.query(
        Order.platform_product_id,
        Order.order_date,
        Order.delivery_attribute_type,
        NaverSettlementCase.pay_settle_amount,
        NaverSettlementCase.total_pay_commission,
        NaverSettlementCase.selling_interlock_commission,
        NaverSettlementCase.free_installment_commission,
    ).join(
        Order,
        and_(
            Order.channel_id == NAVER_CHANNEL_ID,
            Order.order_number == NaverSettlementCase.order_id,
            Order.platform_product_id == NaverSettlementCase.product_id,
        ),
    ).filter(NaverSettlementCase.product_order_type == "PROD_ORDER").all()

    cutoff = None if window_days is None else kst_today() - timedelta(days=window_days)
    by_pid: dict[str, list[tuple]] = {}
    for pid, order_date, attr, gross, mgmt, interlock, free_inst in rows:
        if not pid:
            continue
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

    table = ProductCommissionTable(window_days=window_days, min_rows=min_rows)
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

    if not table.available and table.account.rows:
        log.warning("product_commission: 계정 실측 요율이 비현실적(rows=%d gross=%s mgmt=%s interlock=%s) "
                    "→ 호출부가 계정 단일 요율로 폴백",
                    table.account.rows, table.account.gross,
                    table.account.mgmt_rate, table.account.base_interlock_rate)
    return table


def _as_date(value):
    """order_date(DateTime|date) → date."""
    return value.date() if hasattr(value, "date") else value
