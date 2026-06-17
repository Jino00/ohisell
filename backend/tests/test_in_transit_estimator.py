# test_in_transit_estimator.py — in_transit_estimator SA fixture 테스트 (Phase 2 D-13·X5·X6, R4)
# 발송중 수량 정확성(필름100·버디0), X6 만료/취소/부분입고, X5 freshness-gate 검증.
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from app.services.coupang.in_transit_estimator import (
    EXPIRY_BUFFER_DAYS,
    _in_transit_for_row,
    _expected_stowing_at,
    estimate_in_transit,
)


# ────────────────────────────────────
# 헬퍼: mock CoupangRgInbound 행 생성
# ────────────────────────────────────
def _row(
    *,
    vendor_item_id: str = "111",
    account_key: str = "COUPANG_WING1",
    requested_qty: int | None = 100,
    received_qty: int | None = None,
    stowed_qty: int | None = 0,
    stowing_at=None,
    shipment_created_at=None,
    inbound_id: str = "IB001",
):
    r = MagicMock()
    r.vendor_item_id = vendor_item_id
    r.account_key = account_key
    r.requested_qty = requested_qty
    r.received_qty = received_qty
    r.stowed_qty = stowed_qty
    r.stowing_at = stowing_at
    r.shipment_created_at = shipment_created_at
    r.inbound_id = inbound_id
    return r


NOW = datetime(2026, 6, 18, 10, 0, 0)
LEAD_P90 = 3.0


# ────────────────────────────────────
# _in_transit_for_row 단위 테스트
# ────────────────────────────────────

def test_fully_in_transit_no_stowing():
    """stowing_at=None, stowed_qty=0 → in_transit = requested_qty."""
    r = _row(requested_qty=100, stowed_qty=0, stowing_at=None, shipment_created_at=NOW - timedelta(days=1))
    assert _in_transit_for_row(r, NOW, LEAD_P90) == 100


def test_fully_stowed_returns_zero():
    """stowing_at이 설정됨 → 판매개시 완료, in_transit = 0."""
    r = _row(requested_qty=60, stowed_qty=60, stowing_at=NOW - timedelta(days=1), shipment_created_at=NOW - timedelta(days=2))
    assert _in_transit_for_row(r, NOW, LEAD_P90) == 0


def test_partial_stow():
    """stowing_at=None + stowed_qty < requested_qty → 차액만 in_transit."""
    r = _row(requested_qty=60, stowed_qty=40, stowing_at=None, shipment_created_at=NOW - timedelta(days=1))
    assert _in_transit_for_row(r, NOW, LEAD_P90) == 20


def test_stowed_qty_none_treated_as_zero():
    """stowed_qty=None → 0으로 취급, requested_qty 전체가 in_transit."""
    r = _row(requested_qty=50, stowed_qty=None, stowing_at=None, shipment_created_at=NOW - timedelta(days=1))
    assert _in_transit_for_row(r, NOW, LEAD_P90) == 50


def test_x6_expiry_phantom_removal():
    """X6: 발송 시점이 lead_p90 + EXPIRY_BUFFER_DAYS 이상 경과 → 만료(phantom 제거)."""
    old_ship = NOW - timedelta(days=LEAD_P90 + EXPIRY_BUFFER_DAYS + 1)
    r = _row(requested_qty=100, stowed_qty=0, stowing_at=None, shipment_created_at=old_ship)
    assert _in_transit_for_row(r, NOW, LEAD_P90) == 0


def test_x6_not_expired_within_window():
    """X6: 아직 만료 기한 내 → 정상 in_transit."""
    fresh_ship = NOW - timedelta(days=LEAD_P90 + EXPIRY_BUFFER_DAYS - 1)
    r = _row(requested_qty=100, stowed_qty=0, stowing_at=None, shipment_created_at=fresh_ship)
    assert _in_transit_for_row(r, NOW, LEAD_P90) == 100


def test_no_shipment_created_at_not_expired():
    """shipment_created_at=None이면 만료 판정 불가 → 만료 스킵, requested−stowed 반환."""
    r = _row(requested_qty=80, stowed_qty=10, stowing_at=None, shipment_created_at=None)
    assert _in_transit_for_row(r, NOW, LEAD_P90) == 70


# ────────────────────────────────────
# _expected_stowing_at 단위 테스트
# ────────────────────────────────────

def test_expected_stowing_at():
    """판매개시 예정 = shipment_created_at + mean_lead_days."""
    ship = datetime(2026, 6, 15, 12, 0, 0)
    result = _expected_stowing_at(_row(shipment_created_at=ship), mean_lead_days=2.5)
    # 2026-06-15 12:00 + 2.5일(60시간) = 2026-06-18 00:00 → date "2026-06-18"
    assert result == "2026-06-18"


def test_expected_stowing_at_none_when_no_ship():
    """shipment_created_at=None → None."""
    result = _expected_stowing_at(_row(shipment_created_at=None), mean_lead_days=2.5)
    assert result is None


# ────────────────────────────────────
# estimate_in_transit 통합 테스트 (X5 freshness-gate)
# ────────────────────────────────────

def _make_db_mock(rows, last_success_at=None):
    """간단한 DB mock: CoupangRgInbound.all()=rows, CoupangWingCookie.last_success_at=last_success_at."""
    db = MagicMock()
    # cookie query chain
    cookie_scalar = MagicMock()
    cookie_scalar.scalar.return_value = last_success_at
    db.query.return_value.filter.return_value = cookie_scalar
    # inbound query chain
    inbound_all = MagicMock()
    inbound_all.all.return_value = rows
    db.query.return_value.all = MagicMock(return_value=rows)
    return db


def test_x5_stale_returns_empty():
    """X5: last_success_at=3일 전(stale) → fresh=False, options={}."""
    old = NOW - timedelta(days=3)

    with patch("app.services.coupang.in_transit_estimator.kst_now", return_value=NOW):
        with patch("app.services.coupang.in_transit_estimator._fetch_freshness", return_value=(False, old.isoformat())):
            db = MagicMock()
            result = estimate_in_transit(db)

    assert result["fresh"] is False
    assert result["options"] == {}
    assert result["total_in_transit_qty"] == 0


def test_x5_fresh_returns_data():
    """X5: last_success_at=1일 전(fresh) → 발송중 데이터 반환."""
    ship = NOW - timedelta(days=1)
    row1 = _row(vendor_item_id="111", requested_qty=100, stowed_qty=0, stowing_at=None, shipment_created_at=ship)
    row2 = _row(vendor_item_id="222", requested_qty=60, stowed_qty=60,
                stowing_at=NOW - timedelta(hours=2), shipment_created_at=ship)

    with patch("app.services.coupang.in_transit_estimator.kst_now", return_value=NOW):
        with patch("app.services.coupang.in_transit_estimator._fetch_freshness",
                   return_value=(True, (NOW - timedelta(days=1)).isoformat())):
            db = MagicMock()
            db.query.return_value.all.return_value = [row1, row2]
            db.query.return_value.filter.return_value.all.return_value = [row1, row2]
            result = estimate_in_transit(db)

    assert result["fresh"] is True
    assert result["options"]["111"]["in_transit_qty"] == 100
    assert "222" not in result["options"]  # 완전히 stowed → in_transit=0 → 제외
    assert result["total_in_transit_qty"] == 100


def test_realworld_film100_buddy0():
    """실측 대조: 필름 in_transit=100, 버디필름 in_transit=0 (D-13 실증 케이스)."""
    ship = NOW - timedelta(days=1)
    film_row = _row(vendor_item_id="FILM", requested_qty=100, stowed_qty=0, stowing_at=None, shipment_created_at=ship)
    buddy_row = _row(vendor_item_id="BUDDY", requested_qty=60, stowed_qty=60,
                     stowing_at=NOW - timedelta(hours=5), shipment_created_at=ship)

    with patch("app.services.coupang.in_transit_estimator.kst_now", return_value=NOW):
        with patch("app.services.coupang.in_transit_estimator._fetch_freshness",
                   return_value=(True, (NOW - timedelta(days=1)).isoformat())):
            db = MagicMock()
            # account_key=None이면 .filter() 없이 .all() 호출
            db.query.return_value.all.return_value = [film_row, buddy_row]
            result = estimate_in_transit(db)

    assert result["options"].get("FILM", {}).get("in_transit_qty") == 100
    assert "BUDDY" not in result["options"]
