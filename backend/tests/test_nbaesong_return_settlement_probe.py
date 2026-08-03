# test_nbaesong_return_settlement_probe.py — N배송 반품 회수비 프로브 가드.
#
# 왜 이 가드들이 있나:
#   이 프로브는 N배송 반품 정산 행의 **상태 전이**를 잡으려고 만든 장치다. 라이브 실측상
#   N배송 반품은 468건 중 2건뿐이고(2026072553216341 결제 07-25 / 2026072852172181 결제 07-28),
#   지금은 둘 다 NORMAL_SETTLE_BEFORE_CANCEL(정산 전 취소)로만 잡힌다. 정산 성숙 D+12라
#   08-06·08-09경 상태가 바뀔 수 있고, 그 순간을 사람이 지키고 있을 수는 없다.
#   평소엔 **아무 알림도 안 오는 것이 정상**이라 조용히 고장 나 있어도 아무도 모른다.
#   이 테스트가 유일한 파수꾼이다.
#
#   ① API 실패를 빈 결과로 삼키면 → 표본이 익어도 "0건"으로 기록하고 알림 없이 지나간다.
#   ② 신규 조합 판정이 틀리면 → 매일 알리거나(무시하게 됨) 첫 출현을 놓친다.
#   ③ 멤버십·N배송 판별이 틀리면 → 가설 검증의 두 축이 통째로 무의미해진다.
#   ④ ★조회 파라미터가 틀리면 → 400으로 전손한다. 실제로 첫 배포본이 그랬다(2026-08-03):
#     orderId에 periodType·settleDecisionType을 함께 실어 보내 매 호출 400을 받았다.
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.clients.naver import NaverClient
from app.config import NaverAccountConfig
from app.models import Base, Channel, NaverClaimSettlementProbe, Order
from app.services import naver_claim_settlement_probe as probe
from app.services import scheduler_service as sched

TODAY = date(2026, 8, 6)
NBAESONG_ORDER = "2026072553216341"   # 라이브 N배송 반품 2건 중 하나
NORMAL_ORDER = "2026072099999999"


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    s.add(Channel(id=6, name="네이버", code="NAVER", platform="naver"))
    s.commit()
    try:
        yield s
    finally:
        s.close()


def _raw(*, attr: str | None, discount: int = 0) -> str:
    po: dict = {"deliveryDiscountAmount": discount}
    if attr is not None:
        po["deliveryAttributeType"] = attr
    return json.dumps({"productOrder": po})


def _order(db, order_number, *, attr, status="returned", discount=0, days_ago=5, line=""):
    db.add(Order(
        channel_id=6, order_number=order_number, platform_product_id="p1",
        platform_order_line_id=line, quantity=1, selling_price=Decimal("15900"),
        order_date=datetime(TODAY.year, TODAY.month, TODAY.day) - timedelta(days=days_ago),
        status=status, delivery_attribute_type=attr, raw_data=_raw(attr=attr, discount=discount),
    ))
    db.commit()


class _FakeClient:
    """정산 API 대역 — order_id → 응답 행 목록(유형 필터 없이 그 주문의 정산 행 전량)."""

    def __init__(self, table: dict[str, list[dict]]):
        self.table = table
        self.calls: list[str] = []

    def fetch_case_settlement_by_order(self, order_id):
        self.calls.append(order_id)
        return list(self.table.get(order_id, []))


def _elem(**kw) -> dict:
    base = {
        "product_order_id": "po-1", "order_id": NORMAL_ORDER, "product_id": None,
        "product_order_type": "DELIVERY", "settle_type": "NORMAL_SETTLE_ORIGINAL",
        "product_name": None, "pay_settle_amount": Decimal("5000"),
        "settle_expect_amount": Decimal("5000"), "pay_date": "2026-07-25",
        "settle_expect_date": None, "settle_complete_date": None,
    }
    base.update(kw)
    return base


# ── ① 조용한 실패 금지 ─────────────────────────────────────────────────────
def test_by_order_request_failure_raises():
    """_request가 None(인증·네트워크·400)이면 예외 — 빈 결과로 삼키면 '아직 안 뜸'과
    구분이 안 돼 08-06에 표본이 익어도 0건으로 기록하고 넘어간다."""
    c = NaverClient(NaverAccountConfig(client_id="x", client_secret="y"), access_token="t")
    c._request = lambda *a, **k: None
    with pytest.raises(RuntimeError, match="건별 정산 단건 조회 실패"):
        c.fetch_case_settlement_by_order(NBAESONG_ORDER)


def test_by_order_empty_result_is_not_an_error():
    # 진짜로 정산이 아직 없는 주문은 빈 리스트가 정상.
    c = NaverClient(NaverAccountConfig(client_id="x", client_secret="y"), access_token="t")
    c._request = lambda *a, **k: {"elements": []}
    assert c.fetch_case_settlement_by_order(NBAESONG_ORDER) == []


def test_by_order_sends_only_orderid_and_paging():
    """★첫 배포본 전손의 재발 가드(2026-08-03 라이브).

    네이버는 orderId와 periodType·searchDate를 **상호 배타**로 본다:
      orderId + periodType → 400 "periodType 값은 orderId, productOrderId 값과 같이
                             입력될 수 없습니다"
    settleDecisionType은 periodType=PAY_DATE일 때만 의미가 있으므로 함께 못 보낸다.
    orderId가 빠지면 반대로 계정 전체가 돌아와 엉뚱한 주문의 정산이 이 주문의 관측이 된다.
    """
    seen: dict = {}
    c = NaverClient(NaverAccountConfig(client_id="x", client_secret="y"), access_token="t")

    def _capture(method, path, params=None):
        seen.update(params or {})
        return {"elements": []}

    c._request = _capture
    c.fetch_case_settlement_by_order(NBAESONG_ORDER)
    assert seen["orderId"] == NBAESONG_ORDER
    assert seen["pageNumber"] == 1 and seen["pageSize"] == 1000   # 스펙상 필수
    assert set(seen) == {"orderId", "pageNumber", "pageSize"}     # 나머지는 전부 400 유발


def test_probe_propagates_api_failure(db, monkeypatch):
    """서비스 레이어도 삼키지 않는다 — 잡이 raise해야 EVENT_JOB_ERROR로 표면화된다."""
    _order(db, NBAESONG_ORDER, attr="ARRIVAL_GUARANTEE")

    class _Boom:
        def fetch_case_settlement_by_order(self, *a, **k):
            raise RuntimeError("건별 정산 단건 조회 실패")

    with pytest.raises(RuntimeError):
        probe.run_probe(db, _Boom(), today=TODAY)


# ── ② 판별 정확도 ──────────────────────────────────────────────────────────
def test_membership_detected_by_delivery_discount_amount():
    # 멤버십: 네이버가 고객 배송비를 면제하고 그 금액을 deliveryDiscountAmount로 표시.
    assert probe.is_membership_order(_raw(attr="ARRIVAL_GUARANTEE", discount=3000)) is True


def test_non_membership_is_zero_discount():
    # 라이브 N배송 반품 2건은 둘 다 이 쪽(=멤버십 표본 0건).
    assert probe.is_membership_order(_raw(attr="ARRIVAL_GUARANTEE", discount=0)) is False


def test_membership_unknown_falls_back_to_false():
    """파싱 불가·필드 부재는 비멤버십으로 — 멤버십 표본이 0건이라 오탐이 곧 첫 알림의
    거짓 발화가 된다(그러면 진짜 첫 건이 왔을 때 아무도 안 믿는다)."""
    assert probe.is_membership_order(None) is False
    assert probe.is_membership_order("{잘린 JSON") is False
    assert probe.is_membership_order(json.dumps({"productOrder": {}})) is False
    assert probe.is_membership_order(json.dumps({"shipmentBoxId": 1})) is False  # 쿠팡 raw


def test_nbaesong_detected_by_single_discriminator(db):
    """N배송 판별자는 deliveryAttributeType == 'ARRIVAL_GUARANTEE' 하나뿐(order_delivery 계약)."""
    _order(db, NBAESONG_ORDER, attr="ARRIVAL_GUARANTEE")
    _order(db, NORMAL_ORDER, attr="TODAY")
    got = {e["order_number"]: e["delivery_attribute_type"] for e in probe.collect_claim_orders(db, today=TODAY)}
    assert got == {NBAESONG_ORDER: "ARRIVAL_GUARANTEE", NORMAL_ORDER: "TODAY"}
    nb = ("ARRIVAL_GUARANTEE", False, "DELIVERY", "NORMAL_SETTLE_BEFORE_CANCEL")
    assert probe._highlight(nb).startswith("★★★")
    assert probe._highlight(("TODAY", False, "DELIVERY", "NORMAL_SETTLE_ORIGINAL")) is None


# ── 스캔 대상 선별 ──────────────────────────────────────────────────────────
def test_only_claim_orders_in_window_are_scanned(db):
    _order(db, NBAESONG_ORDER, attr="ARRIVAL_GUARANTEE", status="returned")
    _order(db, "exch-1", attr="TODAY", status="exchanged")
    _order(db, "deliv-1", attr="TODAY", status="delivered")     # 클레임 아님
    _order(db, "old-1", attr="TODAY", status="returned", days_ago=45)  # 창 밖(44일)
    names = {e["order_number"] for e in probe.collect_claim_orders(db, today=TODAY)}
    assert names == {NBAESONG_ORDER, "exch-1"}


def test_multi_line_order_is_merged_once(db):
    """주문라인 그레인을 order_number로 뭉치지 않으면 같은 orderId를 라인 수만큼 중복 호출한다."""
    _order(db, NBAESONG_ORDER, attr="ARRIVAL_GUARANTEE", discount=0, line="l1")
    _order(db, NBAESONG_ORDER, attr="ARRIVAL_GUARANTEE", discount=3000, line="l2")
    entries = probe.collect_claim_orders(db, today=TODAY)
    assert len(entries) == 1
    assert entries[0]["is_membership"] is True  # 한 라인이라도 멤버십이면 멤버십(OR)


# ── ③ 신규 조합 알림 ────────────────────────────────────────────────────────
def test_new_combo_notifies_and_persists(db, monkeypatch):
    """08-06 시나리오: N배송 반품에 DELIVERY 행이 처음 뜨면 알리고 DB에 남긴다."""
    sent: list[str] = []
    monkeypatch.setattr(probe, "notify_text", lambda text, **k: sent.append(text) or {"sent": True})
    _order(db, NBAESONG_ORDER, attr="ARRIVAL_GUARANTEE")
    client = _FakeClient({
        NBAESONG_ORDER: [_elem(order_id=NBAESONG_ORDER, pay_settle_amount=Decimal("3000"))],
    })

    result = probe.run_probe(db, client, today=TODAY)

    assert client.calls == [NBAESONG_ORDER]   # 주문당 1회 — 유형별 반복 조회는 400을 부른다
    assert result["inserted"] == 1
    assert result["notified"] is True
    assert ("ARRIVAL_GUARANTEE", False, "DELIVERY", "NORMAL_SETTLE_ORIGINAL") in result["new_combos"]
    assert "★★★" in sent[0] and "N배송" in sent[0]

    # DB round-trip — 관측이 실제로 남아야 다음 실행이 "이미 본 조합"을 안다.
    row = db.query(NaverClaimSettlementProbe).one()
    assert row.order_number == NBAESONG_ORDER
    assert row.delivery_attribute_type == "ARRIVAL_GUARANTEE"
    assert row.is_membership is False
    assert row.order_status == "returned"
    assert row.product_order_type == "DELIVERY"
    assert row.settle_type == "NORMAL_SETTLE_ORIGINAL"
    assert Decimal(str(row.pay_settle_amount)) == Decimal("3000")
    assert row.observed_date == TODAY


def test_settle_type_transition_is_a_new_combo(db, monkeypatch):
    """★이 프로브의 존재 이유 — 같은 주문의 정산 상태가 옮겨가면(BEFORE_CANCEL → ORIGINAL)
    그것이 신규 조합으로 잡혀야 한다. 지금 N배송 반품 2건은 전부 BEFORE_CANCEL이고,
    08-06·08-09 성숙에서 바뀌는지가 회수비 구조 규명의 관문이다."""
    sent: list[str] = []
    monkeypatch.setattr(probe, "notify_text", lambda text, **k: sent.append(text) or {"sent": True})
    _order(db, NBAESONG_ORDER, attr="ARRIVAL_GUARANTEE")

    before = _FakeClient({NBAESONG_ORDER: [
        _elem(order_id=NBAESONG_ORDER, settle_type="NORMAL_SETTLE_BEFORE_CANCEL",
              pay_settle_amount=Decimal("3000")),
    ]})
    probe.run_probe(db, before, today=TODAY)
    assert len(sent) == 1

    after = _FakeClient({NBAESONG_ORDER: [
        _elem(order_id=NBAESONG_ORDER, settle_type="NORMAL_SETTLE_ORIGINAL",
              pay_settle_amount=Decimal("3000")),
    ]})
    result = probe.run_probe(db, after, today=TODAY + timedelta(days=3))

    assert result["new_combos"] == [
        ("ARRIVAL_GUARANTEE", False, "DELIVERY", "NORMAL_SETTLE_ORIGINAL")
    ]
    assert len(sent) == 2
    # 전이 전후가 둘 다 남는다(덮어쓰지 않는다) — 시계열이 이 프로브의 산출물이다.
    assert {r.settle_type for r in db.query(NaverClaimSettlementProbe).all()} == {
        "NORMAL_SETTLE_BEFORE_CANCEL", "NORMAL_SETTLE_ORIGINAL",
    }


def test_known_combo_does_not_notify(db, monkeypatch):
    """이미 본 조합은 조용히 적재만 — 매일 오는 알림은 곧 무시되는 알림이다."""
    sent: list[str] = []
    monkeypatch.setattr(probe, "notify_text", lambda text, **k: sent.append(text) or {"sent": True})
    db.add(NaverClaimSettlementProbe(
        order_number="prev-1", product_order_id="po-prev",
        delivery_attribute_type="ARRIVAL_GUARANTEE", is_membership=False,
        order_status="returned",
        product_order_type="DELIVERY", settle_type="NORMAL_SETTLE_ORIGINAL",
        pay_settle_amount=Decimal("3000"), settle_expect_amount=Decimal("3000"),
        observed_date=TODAY - timedelta(days=1),
    ))
    db.commit()
    _order(db, NBAESONG_ORDER, attr="ARRIVAL_GUARANTEE")
    client = _FakeClient({NBAESONG_ORDER: [_elem(order_id=NBAESONG_ORDER)]})

    result = probe.run_probe(db, client, today=TODAY)

    assert result["new_combos"] == []
    assert result["notified"] is False
    assert sent == []
    assert result["inserted"] == 1   # 관측 자체는 계속 쌓인다(시계열 보존)


def test_same_day_rerun_does_not_double_notify(db, monkeypatch):
    """같은 날 두 번 돌려도 두 번 알리면 안 된다 — 첫 실행이 이미 적재했으므로
    두 번째엔 '이번 실행 이전에 본 조합'이어야 한다(멱등)."""
    sent: list[str] = []
    monkeypatch.setattr(probe, "notify_text", lambda text, **k: sent.append(text) or {"sent": True})
    _order(db, NBAESONG_ORDER, attr="ARRIVAL_GUARANTEE")
    client = _FakeClient({NBAESONG_ORDER: [_elem(order_id=NBAESONG_ORDER)]})

    first = probe.run_probe(db, client, today=TODAY)
    second = probe.run_probe(db, client, today=TODAY)

    assert first["notified"] is True and second["notified"] is False
    assert len(sent) == 1
    assert second["inserted"] == 0                       # UNIQUE 중복 미적재
    assert db.query(NaverClaimSettlementProbe).count() == 1


def test_membership_nbaesong_is_highlighted(db, monkeypatch):
    """멤버십 N배송 클레임은 아직 표본 0건 — 처음 뜨면 강조돼야 한다."""
    sent: list[str] = []
    monkeypatch.setattr(probe, "notify_text", lambda text, **k: sent.append(text) or {"sent": True})
    _order(db, NBAESONG_ORDER, attr="ARRIVAL_GUARANTEE", discount=3000)
    client = _FakeClient({
        NBAESONG_ORDER: [_elem(order_id=NBAESONG_ORDER, product_order_type="PROD_ORDER")],
    })

    probe.run_probe(db, client, today=TODAY)

    assert "멤버십 N배송" in sent[0]
    assert db.query(NaverClaimSettlementProbe).one().is_membership is True


def test_compensation_type_is_highlighted(db, monkeypatch):
    """CONCESSION/DEDUCTION_RESTORE/PREFERENTIAL_COMMISSION = '네이버가 보상' 가설의 직접 증거."""
    sent: list[str] = []
    monkeypatch.setattr(probe, "notify_text", lambda text, **k: sent.append(text) or {"sent": True})
    _order(db, NBAESONG_ORDER, attr="ARRIVAL_GUARANTEE")
    client = _FakeClient({
        NBAESONG_ORDER: [
            _elem(order_id=NBAESONG_ORDER, product_order_type="CONCESSION", settle_type=None),
        ],
    })

    probe.run_probe(db, client, today=TODAY)

    assert "CONCESSION" in sent[0]
    assert db.query(NaverClaimSettlementProbe).one().settle_type == ""  # NULL 대신 '' sentinel


def test_no_observations_means_no_notification(db, monkeypatch):
    """정산 행이 아직 없는 주문만 있으면 조용해야 한다(평상시의 정상 상태)."""
    sent: list[str] = []
    monkeypatch.setattr(probe, "notify_text", lambda text, **k: sent.append(text) or {"sent": True})
    _order(db, NBAESONG_ORDER, attr="ARRIVAL_GUARANTEE")
    result = probe.run_probe(db, _FakeClient({}), today=TODAY)
    assert result == {
        "orders": 1, "observations": 0, "inserted": 0, "new_combos": [], "notified": False,
    }
    assert sent == []


# ── 스케줄러 배선 ───────────────────────────────────────────────────────────
def test_probe_job_is_registered():
    import inspect
    src = inspect.getsource(sched)
    assert '("run_naver_nbaesong_return_probe", "2 6 * * *")' in src
    assert sched.job_func_for("run_naver_nbaesong_return_probe") is not None


def test_probe_job_is_excluded_from_catchup():
    """관측 전용이라 하루 늦어도 무해 — 돈 잡 복구를 지연시키지 않게 catch-up 밖에 둔다."""
    assert "run_naver_nbaesong_return_probe" not in sched._CATCHUP_ORDER


def test_probe_job_scan_window_matches_case_settlement_convention():
    # 기존 건별정산 수집과 같은 44일 — 성숙(D+12)에 32일 여유.
    assert probe.SCAN_LOOKBACK_DAYS == 44
