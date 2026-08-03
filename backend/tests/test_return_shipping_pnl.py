# 이 파일은 반품 배송 손익(2026-08-03) 머니코드 테스트입니다.
#
# 가드 대상: 반품 건이 REVENUE_EXCLUDED라 매출·원가·배송비가 전부 0으로 빠져 있었고, 그래서
#   **반품이 늘어도 대시보드 이익이 반응하지 않았다**(반품이 공짜인 것처럼 보였다).
#   실제로는 출고비·회수비가 나가고 반품비가 들어온다.
#
# 라이브 실측(반품 86건, 2026-08-03)이 이 테스트의 근거다:
#   claimDeliveryFeeDemandAmount = 5,000원 58건 / 미청구 20건 / 2,500원 4건 / 7,500원 2건
#   N배송 반품 2건은 원천이 `미청구(N배송)` → 청구액 0
#   collectDeliveryCompany는 N배송 건 포함 **전부 HANJIN**(출고는 품고여도 회수는 한진)
#
# 특히 지키는 것:
#   ① 정액이 아니라 **건별 실측**을 쓴다 — 정액을 쓰면 미청구 22건이 통째로 틀린 수입이 된다.
#   ② 귀속일은 **반품 완료일** — 결제일이면 마감해서 본 과거 이익이 뒤로 바뀐다(Jino 금지선).
#   ③ 반품은 order_count를 올리지 않는다 — 주문수가 부풀면 객단가가 틀어진다.
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
    _apply_return_pnl,
    _new_bucket,
    _return_shipping_pnl,
    _return_shipping_pnl_by_product,
)

D = Decimal
NAVER_CH = 6
CH_MAP = {NAVER_CH: Channel(id=NAVER_CH, code="NAVER", channel_type="own")}
PICKUP = order_delivery.RETURN_PICKUP_COST_NORMAL       # 일반배송 회수비(⚠️한진 추정치)
PICKUP_NB = order_delivery.RETURN_PICKUP_COST_NBAESONG  # N배송 회수비(품고 요율표 실단가)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    s.add(Channel(id=NAVER_CH, name="네이버", code="NAVER", platform="naver", channel_type="own"))
    s.commit()
    try:
        yield s
    finally:
        s.close()


def _returned(db, *, pkg, attr="TODAY", demand, completed, company="HANJIN", line="",
              status="returned", support=0, product_id=None):
    o = Order(
        channel_id=NAVER_CH, order_number=pkg, platform_product_id="p1",
        platform_order_line_id=line, quantity=1, selling_price=D("15900"),
        order_date=datetime(2026, 7, 1, 10, 0), status=status,
        delivery_attribute_type=attr, product_id=product_id,
        shipping_cost_paid=order_delivery.shipping_cost_paid_of(
            order_delivery.shipping_method_of(attr)
        ),
        raw_data=json.dumps({"productOrder": {"packageNumber": pkg, "deliveryAttributeType": attr}}),
        return_completed_at=completed,
        return_fee_demand_amount=None if demand is None else D(str(demand)),
        return_collect_company=company,
        return_fee_support_amount=D(str(support)),
    )
    db.add(o)
    db.commit()
    return o


# ── ① 건별 실측 (정액 금지) ────────────────────────────────────────────────
def test_charged_return_uses_actual_demand_amount(db):
    """일반배송 반품 5,000원 청구 — 수입은 실측 그대로, 비용은 출고 1,900 + 회수."""
    _returned(db, pkg="pk1", demand=5000, completed=datetime(2026, 8, 2, 14, 0))
    pnl = _return_shipping_pnl(db, CH_MAP, date(2026, 8, 1), date(2026, 8, 3))
    row = pnl[NAVER_CH]["2026-08-02"]
    assert row["income"] == D("5000")
    assert row["commission"] == D("136")           # 2.724% 절사 — 라이브 정산과 원 단위 일치
    assert row["cost"] == D("1900") + PICKUP


def test_uncharged_return_is_pure_loss(db):
    """★미청구 반품(라이브 22건) — 수입 0인데 회수비는 나간다.

    정액 5,000을 폴백으로 썼다면 이 건이 +5,000 수입으로 잡혀 반품이 이익 나는 일이 된다."""
    _returned(db, pkg="pk2", demand=0, completed=datetime(2026, 8, 2, 9, 0))
    row = _return_shipping_pnl(db, CH_MAP, date(2026, 8, 1), date(2026, 8, 3))[NAVER_CH]["2026-08-02"]
    assert row["income"] == D("0")
    assert row["commission"] == D("0")
    assert row["cost"] == D("1900") + PICKUP


def test_nbaesong_return_uses_pumgo_rate_not_hanjin(db):
    """★회수비 축은 회수 택배사가 아니라 **배송방식**이다(2026-08-03 정정).

    처음엔 라이브 86건의 collectDeliveryCompany가 전부 HANJIN이라 회수사 축으로 잡았는데,
    **일반배송도 N배송도 실제 택배사가 한진**이라 그 축으론 구분이 안 된다. 갈리는 것은
    계약 주체다 — 일반배송은 우리 한진 직계약, N배송은 품고 요율표(센터반품 2,050 +
    반품작업비 900 = 3,245 VAT포함). 회수사 축이면 N배송에 한진 추정치가 잘못 붙는다."""
    _returned(db, pkg="pk3", attr="ARRIVAL_GUARANTEE", demand=0,
              completed=datetime(2026, 8, 2, 9, 0))
    row = _return_shipping_pnl(db, CH_MAP, date(2026, 8, 1), date(2026, 8, 3))[NAVER_CH]["2026-08-02"]
    assert row["income"] == D("0")                              # 원천이 `미청구(N배송)`
    assert row["cost"] == order_delivery.SHIPPING_COST_NBAESONG + PICKUP_NB


def test_naver_support_offsets_pickup_cost(db):
    """★★고객 청구 0을 '우리 손실'로 읽으면 정확히 반대다 — 네이버가 5,500원을 부담한다.

    라이브 N배송 반품 2건: claimDeliveryFeeSupportType=MEMBERSHIP_ARRIVAL_GUARANTEE,
    claimDeliveryFeeSupportAmount=5,500. 처음 배선에서 이 필드를 안 봐서 회수비를 통째로
    우리 부담으로 계상했다(2026-08-03 정정)."""
    _returned(db, pkg="pk3s", attr="ARRIVAL_GUARANTEE", demand=0, support=5500,
              completed=datetime(2026, 8, 2, 9, 0))
    row = _return_shipping_pnl(db, CH_MAP, date(2026, 8, 1), date(2026, 8, 3))[NAVER_CH]["2026-08-02"]
    # 회수비는 전액 상쇄, 출고비(품고)만 남는다.
    assert row["cost"] == order_delivery.SHIPPING_COST_NBAESONG


def test_support_above_pickup_is_not_booked_as_income(db):
    """지원액 초과분(5,500 − 2,500 = 3,000)은 수입으로 잡지 않는다.

    그 돈이 실제로 우리에게 지급되는지는 정산에서 아직 확인되지 않았다(D+12 미성숙).
    상계 상한을 안 두면 회수비가 **음수**가 되어 반품이 돈을 버는 일이 된다."""
    _returned(db, pkg="pk3x", attr="TODAY", demand=0, support=99999,
              completed=datetime(2026, 8, 2, 9, 0))
    row = _return_shipping_pnl(db, CH_MAP, date(2026, 8, 1), date(2026, 8, 3))[NAVER_CH]["2026-08-02"]
    assert row["cost"] == D("1900")        # 출고비만 — 회수비 0이 하한
    assert row["income"] == D("0")


# ── ② 귀속일 = 반품 완료일 ─────────────────────────────────────────────────
def test_attributed_to_return_date_not_order_date(db):
    """결제 7/1 · 반품완료 8/2 → 8월에 실린다. 7월(과거) 이익은 흔들리지 않는다."""
    _returned(db, pkg="pk4", demand=5000, completed=datetime(2026, 8, 2, 14, 0))
    assert _return_shipping_pnl(db, CH_MAP, date(2026, 7, 1), date(2026, 7, 31)) == {}
    assert "2026-08-02" in _return_shipping_pnl(db, CH_MAP, date(2026, 8, 1), date(2026, 8, 31))[NAVER_CH]


def test_window_boundaries_are_inclusive(db):
    """창의 첫날 00:00·마지막날 23:59도 포함 — 경계에서 반품이 사라지면 안 된다."""
    _returned(db, pkg="pk5", demand=2500, completed=datetime(2026, 8, 1, 0, 0))
    _returned(db, pkg="pk6", demand=2500, completed=datetime(2026, 8, 3, 23, 59, 59))
    got = _return_shipping_pnl(db, CH_MAP, date(2026, 8, 1), date(2026, 8, 3))[NAVER_CH]
    assert set(got) == {"2026-08-01", "2026-08-03"}


# ── 배송(패키지)당 1회 ─────────────────────────────────────────────────────
def test_multi_line_package_counted_once(db):
    """청구액·회수비는 배송 단위 값이 라인마다 복사된다 — 라인 수만큼 곱해지면 안 된다."""
    _returned(db, pkg="pk7", demand=5000, completed=datetime(2026, 8, 2, 14, 0), line="l1")
    _returned(db, pkg="pk7", demand=5000, completed=datetime(2026, 8, 2, 14, 0), line="l2")
    row = _return_shipping_pnl(db, CH_MAP, date(2026, 8, 1), date(2026, 8, 3))[NAVER_CH]["2026-08-02"]
    assert row["income"] == D("5000")
    assert row["cost"] == D("1900") + PICKUP


# ── 스코프 가드 ────────────────────────────────────────────────────────────
def test_non_returned_status_is_ignored(db):
    """반품 컬럼이 채워져 있어도 상태가 반품이 아니면 집계하지 않는다(이중 안전장치)."""
    _returned(db, pkg="pk8", demand=5000, completed=datetime(2026, 8, 2, 14, 0), status="delivered")
    assert _return_shipping_pnl(db, CH_MAP, date(2026, 8, 1), date(2026, 8, 3)) == {}


def test_null_return_date_is_ignored(db):
    """반품 완료일이 없으면 어느 날에 실을지 정할 수 없다 — 추정하지 않고 건너뛴다."""
    _returned(db, pkg="pk9", demand=5000, completed=None)
    assert _return_shipping_pnl(db, CH_MAP, date(2026, 8, 1), date(2026, 8, 3)) == {}


def test_channel_filter_applies(db):
    _returned(db, pkg="pk10", demand=5000, completed=datetime(2026, 8, 2, 14, 0))
    assert _return_shipping_pnl(db, CH_MAP, date(2026, 8, 1), date(2026, 8, 3), channel_id=99) == {}


# ── ③ 버킷 반영 ────────────────────────────────────────────────────────────
def test_apply_does_not_inflate_order_count():
    """★반품은 주문이 아니다 — order_count가 오르면 객단가가 틀어진다."""
    b = _new_bucket()
    _apply_return_pnl(b, {"income": D("5000"), "commission": D("136"), "cost": D("4400")})
    assert b["order_count"] == 0
    assert b["shipping_revenue"] == D("5000") and b["revenue"] == D("5000")
    assert b["commission"] == D("136") and b["shipping"] == D("4400")
    assert b["vat"] == D("5000") * D("10") / D("110")


def test_uncharged_return_reduces_profit():
    """미청구 반품은 순수 손실 — 순이익 공식(revenue−cost−commission−shipping−ad−vat) 기준."""
    b = _new_bucket()
    _apply_return_pnl(b, {"income": D("0"), "commission": D("0"), "cost": D("4400")})
    net = b["revenue"] - b["cost"] - b["commission"] - b["shipping"] - b["ad_spend"] - b["vat"]
    assert net == D("-4400")


# ── ④ 상품별 귀속 (배분 규칙 불필요) ────────────────────────────────────────
def test_product_grain_attaches_whole_cost_to_the_single_product(db):
    """★라이브 반품 86패키지가 전부 상품 1종 — 배분할 게 없다(Jino 지적 2026-08-03).

    처음엔 "배분 규칙이 없다"는 이유로 상품별 이익에서 반품을 통째로 뺐는데, 그래서
    일별·채널별 합계와 상품별 합계가 반품분만큼 어긋나 있었다."""
    _returned(db, pkg="pp1", demand=5000, completed=datetime(2026, 8, 2, 14, 0), product_id=7)
    got = _return_shipping_pnl_by_product(db, CH_MAP, date(2026, 8, 1), date(2026, 8, 3))
    assert set(got) == {7}
    assert got[7]["income"] == D("5000")
    assert got[7]["commission"] == D("136")
    assert got[7]["cost"] == D("1900") + PICKUP


def test_product_grain_splits_multi_product_package_by_revenue(db):
    """2종 이상 패키지는 상품매출 비례(광고비 배분과 같은 규칙). 실측 0건이라 지금은 미발동."""
    _returned(db, pkg="pp2", demand=5000, completed=datetime(2026, 8, 2, 14, 0),
              product_id=1, line="l1")
    _returned(db, pkg="pp2", demand=5000, completed=datetime(2026, 8, 2, 14, 0),
              product_id=2, line="l2")
    got = _return_shipping_pnl_by_product(db, CH_MAP, date(2026, 8, 1), date(2026, 8, 3))
    assert set(got) == {1, 2}
    # 두 라인 매출이 같으므로 정확히 반씩 — 합치면 패키지 1건분과 같아야 한다(이중계상 금지).
    assert got[1]["cost"] + got[2]["cost"] == D("1900") + PICKUP
    assert got[1]["income"] + got[2]["income"] == D("5000")


def test_product_grain_skips_unlinked_lines(db):
    """상품 미연결(product_id NULL) 라인은 상품 그레인 집계 대상이 아니다."""
    _returned(db, pkg="pp3", demand=5000, completed=datetime(2026, 8, 2, 14, 0), product_id=None)
    assert _return_shipping_pnl_by_product(db, CH_MAP, date(2026, 8, 1), date(2026, 8, 3)) == {}
