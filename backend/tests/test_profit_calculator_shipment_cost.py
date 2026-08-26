# 이 파일은 profit_calculator의 배송비 단가 헬퍼(_shipment_cost/_add_shipment_cost) 머니코드 테스트입니다.
# 가드 대상: 07-2x 네이버 N배송(도착보장) 전환 후에도 배송비를 전 건 1,900 정액으로 잡아
#   배송비를 과소계상(=순이익 과대)하던 버그. 라이브 실측 2026-07-27~08-02: 653패키지 400,960원.
# 단가 판별은 order_delivery(D-NAO-84 단일 진실 원천)에 위임하므로, 여기서는 위임 결과가
#   배송(패키지) 단위로 1회만, 그리고 갈릴 때 최댓값으로 적재되는지를 고정한다.
import json
from decimal import Decimal

from app.models import Channel, Order
from app.services.profit_calculator import (
    HANJIN_PER_SHIPMENT,
    _shipment_cost,
    _shipment_cost_map,
)

D = Decimal
NBAESONG = D("3377")
NAVER_CH = 6


def _naver_order(attr: str | None, *, paid=None, pkg="pkg-1", status="delivered") -> Order:
    """네이버 주문 1라인. paid=None이면 shipping_cost_paid 결측(백필 이전 행) 재현."""
    po = {"packageNumber": pkg}
    if attr:
        po["deliveryAttributeType"] = attr
    return Order(
        channel_id=NAVER_CH,
        order_number=pkg,
        status=status,
        raw_data=json.dumps({"productOrder": po}),
        shipping_cost_paid=paid,
    )


def _foreign_order() -> Order:
    """쿠팡/cafe24 주문 — raw_data에 productOrder 키가 없다."""
    return Order(raw_data=json.dumps({"shipmentBoxId": "box-1"}), shipping_cost_paid=None)


_CH_MAP = {NAVER_CH: Channel(code="NAVER", channel_type="own")}


# ── _shipment_cost: 배송방식별 단가 ─────────────────────────────────────────
def test_nbaesong_uses_snapshot_price():
    assert _shipment_cost(_naver_order("ARRIVAL_GUARANTEE", paid=NBAESONG)) == NBAESONG


def test_today_delivery_is_normal_price():
    # 오늘출발(TODAY)은 N배송이 아니다 — 단일 판별자는 ARRIVAL_GUARANTEE뿐.
    assert _shipment_cost(_naver_order("TODAY", paid=D("1900"))) == D("1900")


def test_nbaesong_without_snapshot_falls_back_to_raw_parse():
    # shipping_cost_paid 결측(백필 이전 행)이어도 raw_data 파싱으로 3,377을 얻는다.
    assert _shipment_cost(_naver_order("ARRIVAL_GUARANTEE", paid=None)) == NBAESONG


def test_non_naver_channel_unchanged():
    # ★회귀 가드: 쿠팡·cafe24는 종전과 동일한 1,900이어야 한다.
    assert _shipment_cost(_foreign_order()) == HANJIN_PER_SHIPMENT


# ── _shipment_cost_map: 배송별 단가 선행 확정 ──────────────────────────────
def test_same_shipment_resolves_to_one_entry():
    orders = [_naver_order("TODAY", paid=D("1900")) for _ in range(3)]  # 한 패키지 3라인
    costs = _shipment_cost_map(orders, _CH_MAP)
    assert list(costs.values()) == [D("1900")]


def test_distinct_shipments_priced_separately():
    costs = _shipment_cost_map(
        [
            _naver_order("TODAY", paid=D("1900"), pkg="pkg-1"),
            _naver_order("ARRIVAL_GUARANTEE", paid=NBAESONG, pkg="pkg-2"),
        ],
        _CH_MAP,
    )
    assert sum(costs.values()) == D("1900") + NBAESONG


def test_mixed_shipment_takes_max_regardless_of_order():
    # 한 패키지에 단가가 갈리면 최댓값. 입력 순서가 반대여도 같은 답이어야 한다.
    for first, second in ((D("1900"), NBAESONG), (NBAESONG, D("1900"))):
        costs = _shipment_cost_map(
            [
                _naver_order("TODAY", paid=first, pkg="pkg-mixed"),
                _naver_order("TODAY", paid=second, pkg="pkg-mixed"),
            ],
            _CH_MAP,
        )
        assert sum(costs.values()) == NBAESONG


def test_excluded_orders_do_not_create_shipments():
    # ★취소/반품은 매출에서 빠지므로 택배비도 잡히면 안 된다 — 본 루프와 같은 규칙.
    costs = _shipment_cost_map(
        [
            _naver_order("TODAY", paid=D("1900"), pkg="live"),
            _naver_order("ARRIVAL_GUARANTEE", paid=NBAESONG, pkg="dead", status="cancelled"),
            _naver_order("ARRIVAL_GUARANTEE", paid=NBAESONG, pkg="dead2", status="returned"),
        ],
        _CH_MAP,
    )
    assert sum(costs.values()) == D("1900")


def test_consignment_channel_excluded():
    ch_map = {NAVER_CH: Channel(code="X", channel_type="consignment")}
    assert _shipment_cost_map([_naver_order("TODAY", paid=D("1900"))], ch_map) == {}


def test_price_is_order_independent_across_buckets():
    # ★codex P2 회귀 가드: 같은 배송이 여러 날짜 버킷에 걸쳐도 금액은 선행 확정이라
    #   입력 순서와 무관하다. 종전 차액 보정 방식은 앞 버킷 1,900 / 뒤 버킷 1,120으로
    #   쪼개져 일별 손익이 DB 반환 순서에 따라 달라졌다.
    a = _naver_order("TODAY", paid=D("1900"), pkg="span")
    b = _naver_order("ARRIVAL_GUARANTEE", paid=NBAESONG, pkg="span")
    assert _shipment_cost_map([a, b], _CH_MAP) == _shipment_cost_map([b, a], _CH_MAP)
    assert sum(_shipment_cost_map([a, b], _CH_MAP).values()) == NBAESONG


def test_nbaesong_costs_more_than_flat_rate():
    # 이 테스트가 깨지면 정액 회귀 — 버그가 되돌아왔다는 뜻.
    assert _shipment_cost(_naver_order("ARRIVAL_GUARANTEE", paid=NBAESONG)) > HANJIN_PER_SHIPMENT
