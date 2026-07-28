# test_order_delivery.py — 배송 구분 판별 SA + 영속 컬럼 + BEP 동작 불변 (Jino 지시 2026-07-28)
#
# 핵심 검증축:
#   1) 판별 계약이 종전(_order_shipping_cost)과 **정확히 같다** — 새 규칙 추가 없음.
#   2) 영속 컬럼(shipping_cost_paid)을 읽는 경로와 raw_data 파싱 폴백 경로의 산출이 동일.
#   3) 판별 불가는 NULL로 남고 추정하지 않는다.
from __future__ import annotations

import json
from decimal import Decimal

from app.services import order_delivery as od
from app.services.naver_ad import bep_calculator


def _entry(attr, **extra):
    po = {"productOrderId": "1", "quantity": 1}
    if attr is not None:
        po["deliveryAttributeType"] = attr
    po.update(extra)
    return json.dumps({"order": {"orderId": "o1"}, "productOrder": po}, ensure_ascii=False)


# ── 1. 판별 계약 ──────────────────────────────────────────────
def test_nbaesong_only_by_single_discriminator():
    assert od.shipping_method_of("ARRIVAL_GUARANTEE") == od.METHOD_NBAESONG
    for other in ("TODAY", "NORMAL", "", None, "ARRIVAL", "arrival_guarantee"):
        assert od.shipping_method_of(other) == od.METHOD_NORMAL, other


def test_companion_signals_do_not_decide():
    """동반 신호(PG 물류사·TOMORROW 태그)만으로 N배송 판정하지 않는다(단일 판별자 원칙)."""
    raw = _entry("TODAY", logisticsCompanyId="PG", deliveryTagType="TOMORROW")
    assert od.order_shipping_cost({"raw_data": raw}) == Decimal("1900")


def test_unit_prices_unchanged():
    assert od.SHIPPING_COST_NORMAL == Decimal("1900")
    assert od.SHIPPING_COST_NBAESONG == Decimal("3020")
    # bep_calculator 재수출이 같은 객체를 가리키는가(단가 진실 원천 1개)
    assert bep_calculator.SHIPPING_COST_NORMAL == od.SHIPPING_COST_NORMAL
    assert bep_calculator.SHIPPING_COST_NBAESONG == od.SHIPPING_COST_NBAESONG


# ── 2. 필드 추출 ──────────────────────────────────────────────
def test_delivery_fields_nbaesong_live_shape():
    """prod 실측 N배송 주문(orders.id 12361) 모양."""
    raw = _entry(
        "ARRIVAL_GUARANTEE", deliveryPolicyType="유료", shippingFeeType="선결제",
        logisticsCompanyId="PG", logisticsCenterId=468, deliveryFeeAmount=3000,
    )
    f = od.delivery_fields(raw)
    assert f == {
        "delivery_attribute_type": "ARRIVAL_GUARANTEE",
        "delivery_policy_type": "유료",
        "shipping_fee_type": "선결제",
        "logistics_company_id": "PG",
        "shipping_cost_paid": Decimal("3020"),
    }


def test_delivery_fields_normal_live_shape():
    """prod 실측 일반 주문(orders.id 12362) 모양 — logistics* 필드 없음."""
    raw = _entry("TODAY", deliveryPolicyType="무료", shippingFeeType="무료", deliveryFeeAmount=0)
    f = od.delivery_fields(raw)
    assert f["delivery_attribute_type"] == "TODAY"
    assert f["logistics_company_id"] is None
    assert f["shipping_cost_paid"] == Decimal("1900")


def test_delivery_fields_none_when_unresolvable():
    """판별 불가는 None → 컬럼 NULL 유지(추정 금지)."""
    assert od.delivery_fields(None) is None
    assert od.delivery_fields("") is None
    assert od.delivery_fields('{"order":{}}...(truncated)') is None   # JSON 잘림
    assert od.delivery_fields("[]") is None                            # dict 아님
    assert od.delivery_fields(json.dumps({"order": {}})) is None        # productOrder 없음
    assert od.delivery_fields(json.dumps({"shipmentType": "X"})) is None  # 쿠팡 raw_data


def test_delivery_fields_missing_attr_still_costs_normal():
    """productOrder는 있는데 판별 필드가 없으면 → 타입 NULL + 일반배송 단가(fail-safe)."""
    f = od.delivery_fields(_entry(None))
    assert f["delivery_attribute_type"] is None
    assert f["shipping_cost_paid"] == Decimal("1900")


# ── 3. 영속 컬럼 vs 파싱 폴백 동치 ────────────────────────────
def test_column_and_parse_paths_agree():
    for attr, expect in (("ARRIVAL_GUARANTEE", "3020"), ("TODAY", "1900"), ("NORMAL", "1900")):
        raw = _entry(attr)
        parsed = od.order_shipping_cost({"raw_data": raw})           # 폴백 경로
        backfilled = od.delivery_fields(raw)["shipping_cost_paid"]   # 백필이 쓰는 값
        column = od.order_shipping_cost({"shipping_cost_paid": backfilled, "raw_data": None})
        assert parsed == column == Decimal(expect), attr


def test_column_wins_over_raw_data_snapshot_semantics():
    """단가 개정 대비: 컬럼(주문 시점 스냅샷)이 현재 상수보다 우선한다."""
    row = {"shipping_cost_paid": Decimal("1500"), "raw_data": _entry("ARRIVAL_GUARANTEE")}
    assert od.order_shipping_cost(row) == Decimal("1500")


def test_order_shipping_cost_legacy_contract_preserved():
    """종전 _order_shipping_cost 계약(폴백 1900)이 그대로인가."""
    assert bep_calculator._order_shipping_cost(None) == Decimal("1900")
    assert bep_calculator._order_shipping_cost({"quantity": 1}) == Decimal("1900")
    assert bep_calculator._order_shipping_cost({"raw_data": None}) == Decimal("1900")
    assert bep_calculator._order_shipping_cost({"raw_data": "not json{"}) == Decimal("1900")
    assert bep_calculator._order_shipping_cost({"raw_data": "[]"}) == Decimal("1900")
    assert bep_calculator._order_shipping_cost(
        {"raw_data": _entry("ARRIVAL_GUARANTEE")}) == Decimal("3020")


# ── 4. 실부담(clamp 없음) ─────────────────────────────────────
def test_net_burden_can_be_negative():
    """수취가 지불을 넘으면 음수 — 리포트는 사실 그대로(BEP의 보수 클램프와 별개)."""
    assert od.net_shipping_burden(Decimal("3020"), Decimal("3000")) == Decimal("20")
    assert od.net_shipping_burden(Decimal("1900"), Decimal("2500")) == Decimal("-600")
    assert od.net_shipping_burden(Decimal("1900"), None) == Decimal("1900")


# ── 5. ORM 반영 ───────────────────────────────────────────────
class _Row:
    delivery_attribute_type = None
    delivery_policy_type = None
    shipping_fee_type = None
    logistics_company_id = None
    shipping_cost_paid = None


def test_apply_delivery_fields_sets_and_preserves():
    r = _Row()
    assert od.apply_delivery_fields(r, _entry("ARRIVAL_GUARANTEE", deliveryPolicyType="유료")) is True
    assert r.delivery_attribute_type == "ARRIVAL_GUARANTEE"
    assert r.shipping_cost_paid == Decimal("3020")
    # 판별 불가 재동기화가 이미 확보한 실측을 지우지 않는다
    assert od.apply_delivery_fields(r, None) is False
    assert r.delivery_attribute_type == "ARRIVAL_GUARANTEE"


# ── 6. 신규 주문 동기화 배선 ──────────────────────────────────
def _mem_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_sync_fills_delivery_columns_on_insert_and_update(monkeypatch):
    """동기화가 저장할 때 배송 구분 컬럼을 함께 채우는가(기존 저장 로직·중복 판정 불변)."""
    from datetime import date

    from app.clients.base import RawOrder
    from app.models import Channel, Order
    from app.services import sync_service

    db = _mem_db()
    ch = Channel(name="네이버", code="NAVER", platform="naver",
                 api_type="oauth2_bcrypt", api_config_key="NAVER")
    db.add(ch)
    db.commit()

    def _raw_dict(attr, policy, fee_type, fee, logi=None):
        po = {"productOrderId": "PO", "deliveryAttributeType": attr,
              "deliveryPolicyType": policy, "shippingFeeType": fee_type,
              "deliveryFeeAmount": fee}
        if logi:
            po["logisticsCompanyId"] = logi
        return {"order": {"orderId": "ORD1"}, "productOrder": po}

    def _payload(raw_dict, collected):
        return [RawOrder(
            order_number="ORD1", platform_product_id="9810", platform_order_line_id="PO",
            platform_product_name="상품X", quantity=1, selling_price=Decimal("10000"),
            shipping_cost=collected, order_date="2026-06-01T10:00:00",
            status="confirmed", raw_data=raw_dict,
        )]

    class _FakeClient:
        payload = _payload(_raw_dict("TODAY", "무료", "무료", 0), None)

        def test_connection(self):
            return {"status": "ok"}

        def fetch_orders(self, date_from, date_to):
            return self.payload

    fake = _FakeClient()
    monkeypatch.setattr(sync_service, "_get_client_for_channel", lambda channel, db=None: fake)

    # insert 경로
    assert sync_service.sync_channel_orders(
        db, ch.id, date(2026, 6, 1), date(2026, 6, 1))["status"] == "success"
    o = db.query(Order).one()
    assert o.delivery_attribute_type == "TODAY"
    assert o.shipping_cost_paid == Decimal("1900")
    assert o.is_nbaesong is False
    assert o.shipping_cost_net == Decimal("1900")

    # update 경로 — 같은 라인이 N배송으로 바뀌어 재동기화
    fake.payload = _payload(_raw_dict("ARRIVAL_GUARANTEE", "유료", "선결제", 3000, "PG"),
                            Decimal("3000"))
    assert sync_service.sync_channel_orders(
        db, ch.id, date(2026, 6, 1), date(2026, 6, 1))["status"] == "success"
    rows = db.query(Order).all()
    assert len(rows) == 1  # 중복 판정 키 불변(새 행 안 생김)
    o = rows[0]
    assert o.delivery_attribute_type == "ARRIVAL_GUARANTEE"
    assert o.logistics_company_id == "PG"
    assert o.shipping_cost_paid == Decimal("3020")
    assert o.shipping_cost_net == Decimal("20")   # 실부담
    db.close()


def test_backfill_is_idempotent_and_reports_unresolved(monkeypatch):
    """백필: 재실행 안전 + 판별 불가 행은 NULL 유지하고 건수로 보고(추정 금지)."""
    import importlib.util
    from datetime import datetime
    from pathlib import Path

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.models import Channel, Order

    path = Path(__file__).resolve().parent.parent / "scripts" / "backfill_order_delivery_fields.py"
    spec = importlib.util.spec_from_file_location("bf_delivery", path)
    bf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bf)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(bf, "engine", engine)
    db = sessionmaker(bind=engine)()
    db.add(Channel(id=6, name="네이버", code="NAVER", platform="naver"))
    db.commit()

    def _mk(n, raw):
        return Order(channel_id=6, order_number="o%d" % n, platform_product_id="cp",
                     platform_order_line_id="l%d" % n, quantity=1,
                     selling_price=Decimal("1000"), order_date=datetime(2026, 7, 1),
                     status="delivered", raw_data=raw)

    db.add(_mk(1, _entry("ARRIVAL_GUARANTEE")))
    db.add(_mk(2, _entry("TODAY")))
    db.add(_mk(3, '{"order":{}...(truncated)'))   # JSON 잘림 → 판별 불가
    db.add(_mk(4, None))                            # raw_data 없음
    db.commit()

    s1 = bf.run(channel_id=6, dry_run=False, batch=2)
    assert s1["scanned"] == 4
    assert s1["filled"] == 2
    assert s1["unresolved"] == 1
    assert s1["no_raw_data"] == 1

    db.expire_all()
    rows = {o.order_number: o for o in db.query(Order).all()}
    assert rows["o1"].shipping_cost_paid == Decimal("3020")
    assert rows["o2"].shipping_cost_paid == Decimal("1900")
    assert rows["o3"].delivery_attribute_type is None   # 추정으로 채우지 않음
    assert rows["o4"].shipping_cost_paid is None

    # 재실행 — 이미 같은 값이라 쓰기 0건
    s2 = bf.run(channel_id=6, dry_run=False, batch=2)
    assert s2["filled"] == 0
    assert s2["unchanged"] == 2
    db.close()


def test_sync_leaves_columns_null_for_non_naver_raw_data(monkeypatch):
    """쿠팡 등 productOrder 없는 raw_data → 컬럼 NULL(추정으로 채우지 않는다)."""
    from datetime import date

    from app.clients.base import RawOrder
    from app.models import Channel, Order
    from app.services import sync_service

    db = _mem_db()
    ch = Channel(name="쿠팡", code="COUPANG_WING", platform="coupang",
                 api_type="hmac", api_config_key="COUPANG")
    db.add(ch)
    db.commit()

    class _FakeClient:
        last_fetch_complete = False

        def test_connection(self):
            return {"status": "ok"}

        def fetch_orders(self, date_from, date_to):
            return [RawOrder(
                order_number="C1", platform_product_id="1", platform_order_line_id="",
                platform_product_name="상품", quantity=1, selling_price=Decimal("5000"),
                shipping_cost=None, order_date="2026-06-01T10:00:00", status="confirmed",
                raw_data={"shipmentType": "THIRD_PARTY"},
            )]

    monkeypatch.setattr(sync_service, "_get_client_for_channel",
                        lambda channel, db=None: _FakeClient())
    sync_service.sync_channel_orders(db, ch.id, date(2026, 6, 1), date(2026, 6, 1))
    o = db.query(Order).one()
    assert o.delivery_attribute_type is None
    assert o.shipping_cost_paid is None
    assert o.shipping_cost_net is None
    db.close()
