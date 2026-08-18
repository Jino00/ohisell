# test_naver_today_proxy_hourly.py — today_proxy_revenue의 «시간 해상도» 층 (ref 72 §2-②).
"""왜 이 파일이 있나: `Order.order_date`에 결제 시:분:초가 **이미** 저장돼 있는데 코드 전체에서
hour를 뽑는 곳이 0건이었다(2026-08-18 실측). 그 공백을 메우는 층이 `revenue_by_hour`다.

★이 테스트가 지켜야 하는 것 셋:
  ① **검산** — 시간 버킷 합 == 일 단위 프록시(`revenue_by_product`) 합. 두 값이 갈라지면
     같은 날 두 화면이 다른 매출을 말한다(회계 규약 단일화의 존재 이유).
  ② **«측정된 0» ≠ «아직 안 들어온 0»** — 미완 버킷을 0으로 합치면 D-NAO-193이 수리한
     «적재 창 밖을 0으로 읽는» 결함이 그대로 재발한다.
  ③ **건수 병기** — 시간대 금액은 단건 극단치에 지배된다(90일 창에서 hour=15 총액의 73%가
     주문 3건). 건수가 없으면 «이 시간대가 강하다»로 오독한다.
★세션 픽스처는 prod와 같은 `autoflush=False`다(app/database.py:16) — 픽스처가 prod보다
  관대하면 잡을 수 있는 결함을 원리적으로 못 잡는다(교훈 #292).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Order
from app.services.naver_ad import today_proxy_revenue
from app.services.naver_ad.load_window import LoadWindowError

PRODUCT_A = "11730763642"
PRODUCT_B = "11730763643"
DAY = date(2026, 8, 17)


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Testing = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Testing()
    try:
        yield db
    finally:
        db.close()


def _order(db, *, pid: str, at: datetime, amount: str, status: str = "delivered",
           channel_id: int = today_proxy_revenue.NAVER_CHANNEL_ID, n: int = 1):
    for i in range(n):
        db.add(Order(
            channel_id=channel_id,
            order_number=f"{pid}-{at.isoformat()}-{status}-{i}",
            platform_product_id=pid,
            quantity=1,
            selling_price=Decimal(amount),
            order_date=at,
            status=status,
        ))
    db.commit()


# ── last_complete_hour — 완결 경계는 «벽시계»가 아니라 «수집 크론»이 정한다 ─────────────


@pytest.mark.parametrize(
    "now,expected",
    [
        # 크론이 :45에 돈다. 시 버킷 h는 h:59:59에 끝나므로 (h+1):45 회차 뒤에야 완결이다.
        (datetime(2026, 8, 17, 14, 45), 13),   # 분 ≥ 45 → 14:45 회차 완료 → 13시까지 완결
        (datetime(2026, 8, 17, 14, 59), 13),
        (datetime(2026, 8, 17, 14, 44), 12),   # 분 < 45 → 마지막 회차 13:45 → 12시까지
        (datetime(2026, 8, 17, 14, 0), 12),
        (datetime(2026, 8, 17, 1, 45), 0),     # 01:45 회차 → 0시만 완결
        (datetime(2026, 8, 17, 1, 44), None),  # 아직 00:45 회차뿐 → 완결 버킷 없음
        (datetime(2026, 8, 17, 0, 10), None),
    ],
)
def test_last_complete_hour_follows_the_cron_not_the_wall_clock(now, expected):
    assert today_proxy_revenue.last_complete_hour(now, DAY) == expected


def test_past_day_is_fully_complete_and_future_day_has_nothing():
    now = datetime(2026, 8, 17, 14, 44)
    assert today_proxy_revenue.last_complete_hour(now, date(2026, 8, 16)) == 23
    assert today_proxy_revenue.last_complete_hour(now, date(2026, 8, 18)) is None


# ── revenue_by_hour — 버킷 분리·검산·제외 규약 ───────────────────────────────────────


def test_hours_are_separated_and_every_bucket_is_present(session):
    _order(session, pid=PRODUCT_A, at=datetime(2026, 8, 17, 9, 5), amount="10000")
    _order(session, pid=PRODUCT_A, at=datetime(2026, 8, 17, 9, 55), amount="5000")
    _order(session, pid=PRODUCT_A, at=datetime(2026, 8, 17, 21, 30), amount="7000")

    r = today_proxy_revenue.revenue_by_hour(
        session, [PRODUCT_A], DAY, now=datetime(2026, 8, 18, 10, 0)
    )
    assert set(r.hours) == set(range(24)), "0~23 전 버킷을 채워야 «없음»과 «0»을 구분할 수 있다"
    assert r.hours[9] == Decimal("15000")
    assert r.hours[21] == Decimal("7000")
    assert r.hours[10] == Decimal(0)
    assert r.order_counts[9] == 2 and r.order_counts[21] == 1 and r.order_counts[10] == 0


def test_bucket_sum_equals_daily_proxy(session):
    """★검산(ref 72 §2-② 합격기준) — 시간 버킷 합 == 일 단위 프록시 합."""
    for hour, amt in ((0, "1000"), (7, "23000"), (13, "500"), (23, "99000")):
        _order(session, pid=PRODUCT_A, at=datetime(2026, 8, 17, hour, 12), amount=amt)
    _order(session, pid=PRODUCT_B, at=datetime(2026, 8, 17, 13, 40), amount="4500")

    hourly = today_proxy_revenue.revenue_by_hour(
        session, [PRODUCT_A, PRODUCT_B], DAY, now=datetime(2026, 8, 18, 10, 0)
    )
    daily = today_proxy_revenue.revenue_by_product(session, [PRODUCT_A, PRODUCT_B], DAY)
    assert hourly.total() == sum(daily.values(), Decimal(0)) == Decimal("128000")


def test_revenue_excluded_status_and_other_channels_are_dropped_in_both_layers(session):
    """제외 규약이 시간층에서만 느슨해지면 두 화면이 다른 매출을 말한다."""
    _order(session, pid=PRODUCT_A, at=datetime(2026, 8, 17, 11, 0), amount="10000")
    _order(session, pid=PRODUCT_A, at=datetime(2026, 8, 17, 11, 10), amount="80000",
           status="cancelled")
    _order(session, pid=PRODUCT_A, at=datetime(2026, 8, 17, 11, 20), amount="70000",
           channel_id=today_proxy_revenue.NAVER_CHANNEL_ID + 1)

    hourly = today_proxy_revenue.revenue_by_hour(
        session, [PRODUCT_A], DAY, now=datetime(2026, 8, 18, 10, 0)
    )
    daily = today_proxy_revenue.revenue_by_product(session, [PRODUCT_A], DAY)
    assert hourly.hours[11] == Decimal("10000")
    assert hourly.total() == sum(daily.values(), Decimal(0))


def test_other_days_do_not_leak_into_the_buckets(session):
    _order(session, pid=PRODUCT_A, at=datetime(2026, 8, 16, 23, 59, 59), amount="60000")
    _order(session, pid=PRODUCT_A, at=datetime(2026, 8, 17, 0, 0, 1), amount="1000")
    _order(session, pid=PRODUCT_A, at=datetime(2026, 8, 18, 0, 0, 1), amount="60000")

    r = today_proxy_revenue.revenue_by_hour(
        session, [PRODUCT_A], DAY, now=datetime(2026, 8, 19, 10, 0)
    )
    assert r.total() == Decimal("1000")


def test_empty_product_list_returns_full_zero_grid_not_empty(session):
    r = today_proxy_revenue.revenue_by_hour(
        session, [], DAY, now=datetime(2026, 8, 18, 10, 0)
    )
    assert len(r.hours) == 24 and r.total() == Decimal(0)


# ── 미완 버킷 — «측정된 0»과 «아직 안 들어온 0»의 분리 ───────────────────────────────


def test_incomplete_hours_are_marked_and_excluded_from_complete_total(session):
    today = date(2026, 8, 18)
    _order(session, pid=PRODUCT_A, at=datetime(2026, 8, 18, 3, 0), amount="1000")
    _order(session, pid=PRODUCT_A, at=datetime(2026, 8, 18, 13, 0), amount="90000")

    # 14:20 → 마지막 회차 13:45 → 12시까지만 완결. 13시 매출은 «아직 안 들어왔을 수 있다».
    r = today_proxy_revenue.revenue_by_hour(
        session, [PRODUCT_A], today, now=datetime(2026, 8, 18, 14, 20)
    )
    assert r.complete_through_hour == 12
    assert 13 in r.incomplete_hours and 23 in r.incomplete_hours
    assert 12 not in r.incomplete_hours
    assert r.total() == Decimal("91000"), "전체 합은 미완분도 포함한다(원값 보존)"
    assert r.complete_total() == Decimal("1000"), "완결 합은 미완 버킷을 빼야 «하한»이 된다"


def test_no_complete_bucket_yet_marks_every_hour_incomplete(session):
    today = date(2026, 8, 18)
    _order(session, pid=PRODUCT_A, at=datetime(2026, 8, 18, 0, 10), amount="5000")
    r = today_proxy_revenue.revenue_by_hour(
        session, [PRODUCT_A], today, now=datetime(2026, 8, 18, 1, 20)
    )
    assert r.complete_through_hour is None
    assert r.incomplete_hours == tuple(range(24))
    assert r.complete_total() == Decimal(0) and r.total() == Decimal("5000")


def test_past_day_has_no_incomplete_bucket(session):
    _order(session, pid=PRODUCT_A, at=datetime(2026, 8, 17, 23, 30), amount="3000")
    r = today_proxy_revenue.revenue_by_hour(
        session, [PRODUCT_A], DAY, now=datetime(2026, 8, 18, 14, 20)
    )
    assert r.complete_through_hour == 23 and r.incomplete_hours == ()
    assert r.complete_total() == r.total() == Decimal("3000")


# ── 적재 창 가드 ────────────────────────────────────────────────────────────────────


def test_future_day_raises_instead_of_returning_a_silent_zero_grid(session):
    """창 밖을 0으로 돌려주면 «매출이 없었다»로 읽힌다 — D-NAO-193이 수리한 그 모양."""
    with pytest.raises(LoadWindowError):
        today_proxy_revenue.revenue_by_hour(
            session, [PRODUCT_A], date(2026, 8, 19), now=datetime(2026, 8, 18, 14, 20)
        )


# ── 극단치 가시성 ───────────────────────────────────────────────────────────────────


def test_single_order_concentration_is_visible_through_order_counts(session):
    """금액만 보면 «15시가 강한 시간대»로 읽히는 자리 — 건수가 그 오독을 막는다."""
    _order(session, pid=PRODUCT_A, at=datetime(2026, 8, 17, 15, 0), amount="15800000")
    _order(session, pid=PRODUCT_A, at=datetime(2026, 8, 17, 16, 0), amount="20000", n=8)

    r = today_proxy_revenue.revenue_by_hour(
        session, [PRODUCT_A], DAY, now=datetime(2026, 8, 18, 10, 0)
    )
    assert r.hours[15] > r.hours[16]
    assert r.order_counts[15] == 1 and r.order_counts[16] == 8
