# 교환 배송 손익(2026-08-04, D-NAO-145) 머니코드 테스트.
#
# 가드 대상: 교환은 손익 엔진에 **0회 등장**했다. 반품과 달리 `exchanged`는 REVENUE_EXCLUDED에
#   없어서 매출은 잡히고 있었고, 그래서 티가 안 났다. 빠져 있던 것은 셋이다 —
#   ①고객에게 받은 교환 배송비 ②회수비 ③**재발송 출고비**(한 주문에 출고가 두 번 일어났는데
#   엔진은 `_shipment_cost`로 한 번만 센다).
#
# 라이브 실측(prod 2026-08-04)이 이 테스트의 근거다:
#   exchange 보유 55건(+raw_data 잘림 1) 전부 채널 6(네이버)
#   claimStatus: EXCHANGE_DONE 49 / EXCHANGE_REJECT 6
#   배송방식: TODAY 45 + NORMAL 4 = **전부 일반배송**(N배송 교환 0건)
#   청구액 5,000원 40건 / 미청구 9건 · 네이버 지원액 전건 0
#   reDeliveryOperationDate·collectCompletedDate는 DONE 49건 **전건에** 존재
#
# 특히 지키는 것:
#   ① 정액이 아니라 **건별 실측** — 미청구 9건에 5,000을 넣으면 교환이 이익 나는 일로 보인다.
#   ② **EXCHANGE_REJECT는 손익 0** — 거부된 교환은 회수도 재발송도 없다.
#   ③ 귀속일은 **재배송 처리일** — 요청일이면 마감해서 본 과거 이익이 뒤로 바뀐다.
#   ④ 비용에 **재발송 출고비가 들어간다** — 회수비만 더하면 교환이 실제보다 싸 보인다.
import json
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base, Channel, Order
from app.services import order_delivery
from app.services.profit_calculator import (
    _exchange_shipping_pnl,
    _exchange_shipping_pnl_by_product,
)

D = Decimal
NAVER_CH = 6
CH_MAP = {NAVER_CH: Channel(id=NAVER_CH, code="NAVER", channel_type="own")}
PICKUP = order_delivery.RETURN_PICKUP_COST_NORMAL       # 일반배송 회수비(⚠️한진 추정치)
SHIP = order_delivery.SHIPPING_COST_NORMAL              # 일반배송 재발송 출고비
WIN = (date(2026, 8, 1), date(2026, 8, 3))


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    s.add(Channel(id=NAVER_CH, name="네이버", code="NAVER", platform="naver", channel_type="own"))
    s.commit()
    try:
        yield s
    finally:
        s.close()


def _exchanged(db, *, pkg, attr="TODAY", demand, completed, line="", support=0, product_id=None):
    o = Order(
        channel_id=NAVER_CH, order_number=pkg, platform_product_id="p1",
        platform_order_line_id=line, quantity=1, selling_price=D("15900"),
        order_date=datetime(2026, 7, 1, 10, 0), status="exchanged",
        delivery_attribute_type=attr, product_id=product_id,
        shipping_cost_paid=order_delivery.shipping_cost_paid_of(
            order_delivery.shipping_method_of(attr)
        ),
        raw_data=json.dumps({"productOrder": {"packageNumber": pkg, "deliveryAttributeType": attr}}),
        exchange_completed_at=completed,
        exchange_fee_demand_amount=None if demand is None else D(str(demand)),
        exchange_collect_company="HANJIN",
        exchange_fee_support_amount=D(str(support)),
    )
    db.add(o)
    db.commit()
    return o


# ── ① 건별 실측 + 재발송 출고비 ────────────────────────────────────────────
def test_charged_exchange_counts_redelivery_and_pickup(db):
    """5,000원 청구 교환 — 수입은 실측, 비용은 **재발송 출고 + 회수** 둘 다."""
    _exchanged(db, pkg="ex1", demand=5000, completed=datetime(2026, 8, 2, 14, 0))
    row = _exchange_shipping_pnl(db, CH_MAP, *WIN)[NAVER_CH]["2026-08-02"]
    assert row["income"] == D("5000")
    assert row["cost"] == SHIP + PICKUP
    # 회수비만 더한 값보다 반드시 크다 — 재발송을 빠뜨리면 이 단언이 깨진다
    assert row["cost"] > PICKUP


def test_uncharged_exchange_is_pure_loss(db):
    """미청구 교환(우리 귀책) — 수입 0, 비용은 그대로. 정액을 쓰면 여기가 5,000이 된다."""
    _exchanged(db, pkg="ex2", demand=None, completed=datetime(2026, 8, 2, 14, 0))
    row = _exchange_shipping_pnl(db, CH_MAP, *WIN)[NAVER_CH]["2026-08-02"]
    assert row["income"] == D("0")
    assert row["cost"] == SHIP + PICKUP


def test_income_gets_delivery_commission(db):
    """수입에도 주문관리 수수료가 붙는다 — 정산이 클레임 배송비를 DELIVERY 행으로 끊기 때문."""
    _exchanged(db, pkg="ex3", demand=5000, completed=datetime(2026, 8, 2, 14, 0))
    row = _exchange_shipping_pnl(db, CH_MAP, *WIN)[NAVER_CH]["2026-08-02"]
    assert row["commission"] > 0
    assert row["commission"] < row["income"]


# ── ② 귀속일 ───────────────────────────────────────────────────────────────
def test_attributed_to_redelivery_date_not_order_date(db):
    """주문일은 7월인데 재배송은 8월 — 8월에 실린다(과거 이익을 흔들지 않는다)."""
    _exchanged(db, pkg="ex4", demand=5000, completed=datetime(2026, 8, 2, 14, 0))
    assert _exchange_shipping_pnl(db, CH_MAP, date(2026, 7, 1), date(2026, 7, 31)) == {}
    assert "2026-08-02" in _exchange_shipping_pnl(db, CH_MAP, date(2026, 8, 1), date(2026, 8, 31))[NAVER_CH]


def test_null_completed_at_is_skipped(db):
    """귀속일이 없으면(=REJECT거나 정보 없음) 아예 안 잡힌다."""
    _exchanged(db, pkg="ex5", demand=5000, completed=None)
    assert _exchange_shipping_pnl(db, CH_MAP, *WIN) == {}


# ── ③ 중복 계상 방지 ───────────────────────────────────────────────────────
def test_multi_line_package_counted_once(db):
    """같은 패키지의 라인 2개 — 회수도 재발송도 배송당 1회다."""
    _exchanged(db, pkg="ex6", demand=5000, completed=datetime(2026, 8, 2, 14, 0), line="L1")
    _exchanged(db, pkg="ex6", demand=5000, completed=datetime(2026, 8, 2, 14, 0), line="L2")
    row = _exchange_shipping_pnl(db, CH_MAP, *WIN)[NAVER_CH]["2026-08-02"]
    assert row["income"] == D("5000")
    assert row["cost"] == SHIP + PICKUP


def test_channel_filter(db):
    _exchanged(db, pkg="ex7", demand=5000, completed=datetime(2026, 8, 2, 14, 0))
    assert _exchange_shipping_pnl(db, CH_MAP, *WIN, channel_id=99) == {}


# ── ④ 지원액 상쇄 ──────────────────────────────────────────────────────────
def test_support_offsets_pickup_capped(db):
    """네이버 지원액은 회수비를 상한으로 상쇄한다(초과분은 수입으로 잡지 않는다)."""
    _exchanged(db, pkg="ex8", demand=0, completed=datetime(2026, 8, 2, 14, 0), support=99999)
    row = _exchange_shipping_pnl(db, CH_MAP, *WIN)[NAVER_CH]["2026-08-02"]
    assert row["cost"] == SHIP   # 회수비가 전액 상쇄되고 그 이상은 깎이지 않는다


# ── ⑤ 상품별 ───────────────────────────────────────────────────────────────
def test_by_product_attributes_to_the_product(db):
    _exchanged(db, pkg="ex9", demand=5000, completed=datetime(2026, 8, 2, 14, 0), product_id=77)
    got = _exchange_shipping_pnl_by_product(db, CH_MAP, *WIN)
    assert got[77]["income"] == D("5000")
    assert got[77]["cost"] == SHIP + PICKUP


def test_by_product_skips_unmapped(db):
    """상품 연결이 없으면 상품 축엔 안 실린다(채널 축엔 실린다)."""
    _exchanged(db, pkg="ex10", demand=5000, completed=datetime(2026, 8, 2, 14, 0), product_id=None)
    assert _exchange_shipping_pnl_by_product(db, CH_MAP, *WIN) == {}
    assert _exchange_shipping_pnl(db, CH_MAP, *WIN) != {}


# ── ⑥ 추출 경계: EXCHANGE_DONE만 ───────────────────────────────────────────
class TestExchangeFieldsExtraction:
    def _raw(self, status, **kw):
        ex = {"claimStatus": status, "reDeliveryOperationDate": "2026-08-02T14:00:00+09:00"}
        ex.update(kw)
        return json.dumps({"exchange": ex})

    def test_done_is_extracted(self):
        f = order_delivery.exchange_fields(self._raw("EXCHANGE_DONE", claimDeliveryFeeDemandAmount=5000))
        assert f["exchange_fee_demand_amount"] == D("5000")
        assert f["exchange_completed_at"] == datetime(2026, 8, 2, 14, 0)

    def test_reject_yields_none(self):
        """거부된 교환은 회수도 재발송도 없다 — 컬럼을 NULL로 남긴다."""
        assert order_delivery.exchange_fields(self._raw("EXCHANGE_REJECT")) is None

    def test_missing_demand_is_zero_not_none(self):
        """청구 필드 부재 = 미청구(실측된 0). NULL이 아니라 0이어야 손익이 맞다."""
        f = order_delivery.exchange_fields(self._raw("EXCHANGE_DONE"))
        assert f["exchange_fee_demand_amount"] == D("0")

    def test_falls_back_to_collect_then_request_date(self):
        ex = {"claimStatus": "EXCHANGE_DONE", "collectCompletedDate": "2026-08-01T09:00:00+09:00"}
        f = order_delivery.exchange_fields(json.dumps({"exchange": ex}))
        assert f["exchange_completed_at"] == datetime(2026, 8, 1, 9, 0)

    def test_no_date_yields_none(self):
        assert order_delivery.exchange_fields(json.dumps({"exchange": {"claimStatus": "EXCHANGE_DONE"}})) is None

    def test_return_payload_is_not_exchange(self):
        """반품 페이로드로 교환 컬럼이 차면 안 된다(키가 다르다)."""
        assert order_delivery.exchange_fields(json.dumps({"return": {"claimStatus": "RETURN_DONE"}})) is None

    def test_truncated_json_yields_none(self):
        """라이브에 raw_data 잘린 행이 1건 있다 — 추정하지 않고 NULL로 남긴다."""
        assert order_delivery.exchange_fields('{"exchange": {"claimStatus": "EXCH') is None

    def test_support_field_name_differs_from_return(self):
        """지원액 필드명이 반품과 다르다 — 반품 키를 읽으면 0으로 떨어진다."""
        f = order_delivery.exchange_fields(
            self._raw("EXCHANGE_DONE", membershipsArrivalGuaranteeClaimSupportingAmount=5500)
        )
        assert f["exchange_fee_support_amount"] == D("5500")
