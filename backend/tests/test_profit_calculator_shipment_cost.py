# 이 파일은 profit_calculator의 배송비 단가 헬퍼(_shipment_cost/_add_shipment_cost) 머니코드 테스트입니다.
# 가드 대상: 07-2x 네이버 N배송(도착보장) 전환 후에도 배송비를 전 건 1,900 정액으로 잡아
#   배송비를 과소계상(=순이익 과대)하던 버그. 라이브 실측 2026-07-27~08-02: 653패키지 400,960원.
# 단가 판별은 order_delivery(D-NAO-84 단일 진실 원천)에 위임하므로, 여기서는 위임 결과가
#   배송(패키지) 단위로 1회만, 그리고 갈릴 때 최댓값으로 적재되는지를 고정한다.
import json
from decimal import Decimal

from app.models import Order
from app.services.profit_calculator import (
    HANJIN_PER_SHIPMENT,
    _add_shipment_cost,
    _shipment_cost,
)

D = Decimal
NBAESONG = D("3020")


def _naver_order(attr: str | None, *, paid=None) -> Order:
    """네이버 주문 1라인. paid=None이면 shipping_cost_paid 결측(백필 이전 행) 재현."""
    raw = {"productOrder": {"deliveryAttributeType": attr} if attr else {}}
    return Order(raw_data=json.dumps(raw), shipping_cost_paid=paid)


def _foreign_order() -> Order:
    """쿠팡/cafe24 주문 — raw_data에 productOrder 키가 없다."""
    return Order(raw_data=json.dumps({"shipmentBoxId": "box-1"}), shipping_cost_paid=None)


# ── _shipment_cost: 배송방식별 단가 ─────────────────────────────────────────
def test_nbaesong_uses_snapshot_price():
    assert _shipment_cost(_naver_order("ARRIVAL_GUARANTEE", paid=NBAESONG)) == NBAESONG


def test_today_delivery_is_normal_price():
    # 오늘출발(TODAY)은 N배송이 아니다 — 단일 판별자는 ARRIVAL_GUARANTEE뿐.
    assert _shipment_cost(_naver_order("TODAY", paid=D("1900"))) == D("1900")


def test_nbaesong_without_snapshot_falls_back_to_raw_parse():
    # shipping_cost_paid 결측(백필 이전 행)이어도 raw_data 파싱으로 3,020을 얻는다.
    assert _shipment_cost(_naver_order("ARRIVAL_GUARANTEE", paid=None)) == NBAESONG


def test_non_naver_channel_unchanged():
    # ★회귀 가드: 쿠팡·cafe24는 종전과 동일한 1,900이어야 한다.
    assert _shipment_cost(_foreign_order()) == HANJIN_PER_SHIPMENT


# ── _add_shipment_cost: 배송당 1회 + 최댓값 ────────────────────────────────
def test_same_shipment_charged_once():
    bucket, seen = {"shipping": D("0")}, {}
    skey = (6, "pkg-1")
    for _ in range(3):  # 한 패키지에 라인 3개
        _add_shipment_cost(bucket, seen, skey, _naver_order("TODAY", paid=D("1900")))
    assert bucket["shipping"] == D("1900")


def test_distinct_shipments_charged_separately():
    bucket, seen = {"shipping": D("0")}, {}
    _add_shipment_cost(bucket, seen, (6, "pkg-1"), _naver_order("TODAY", paid=D("1900")))
    _add_shipment_cost(bucket, seen, (6, "pkg-2"),
                       _naver_order("ARRIVAL_GUARANTEE", paid=NBAESONG))
    assert bucket["shipping"] == D("1900") + NBAESONG


def test_mixed_shipment_takes_max_regardless_of_order():
    # 한 패키지에 단가가 갈리면 최댓값. 낮은 쪽을 먼저 봐도(=차액 보정) 결과가 같아야 한다.
    for first, second in (
        (D("1900"), NBAESONG),
        (NBAESONG, D("1900")),
    ):
        bucket, seen = {"shipping": D("0")}, {}
        skey = (6, "pkg-mixed")
        _add_shipment_cost(bucket, seen, skey, _naver_order("TODAY", paid=first))
        _add_shipment_cost(bucket, seen, skey, _naver_order("TODAY", paid=second))
        assert bucket["shipping"] == NBAESONG


def test_nbaesong_costs_more_than_flat_rate():
    # 이 테스트가 깨지면 정액 회귀 — 버그가 되돌아왔다는 뜻.
    assert _shipment_cost(_naver_order("ARRIVAL_GUARANTEE", paid=NBAESONG)) > HANJIN_PER_SHIPMENT
