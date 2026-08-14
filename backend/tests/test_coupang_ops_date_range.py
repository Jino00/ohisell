# test_coupang_ops_date_range.py — 쿠팡 운영 패널의 임의 기간 선택
#
# 이 변경의 실제 위험은 «기간을 날짜로 받는 것»이 아니라, 종전에 `days == 0`으로 갈리던
# **세 갈래를 `is_today_only`(확정 기간 기준)로 바꾼 것**이다:
#   ①`latest_ad`(오늘 탭이 쓰는 최신 XLSX 일자) ②일별 폴백 발동 조건 ③`ad_today` 카드.
# 셋 중 하나라도 어긋나면 «같은 하루»가 어떻게 골랐느냐에 따라 다른 광고비를 낸다 —
# 2026-08-13에 이 화면이 정확히 그 모양(축이 갈려 이익이 뒤집힘)으로 틀렸다.
from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Channel, CoupangAdOptionDaily, Order
from app.routers.coupang_ops import sales_summary
from app.utils.kst import kst_today

OHITECH_VENDOR = "A01029796"
TODAY = kst_today()
YESTERDAY = TODAY - timedelta(days=1)


@pytest.fixture(autouse=True)
def _vendor_cfg(monkeypatch):
    class _Cfg:
        def __init__(self, vid): self.vendor_id = vid
    vendors = {"COUPANG_WING2": OHITECH_VENDOR, "COUPANG_RG2": OHITECH_VENDOR,
               "COUPANG_ROCKET": OHITECH_VENDOR}
    monkeypatch.setattr("app.routers.coupang_ops._safe_cfg",
                        lambda code: _Cfg(vendors[code]) if code in vendors else None)


@pytest.fixture()
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    wing2 = Channel(name="오하이테크 Wing", code="COUPANG_WING2",
                    platform="coupang", channel_type="marketplace")
    s.add(wing2)
    s.flush()
    for d, price, num in ((YESTERDAY, "53700", "O-1"), (TODAY, "88000", "O-2")):
        s.add(Order(channel_id=wing2.id, order_number=num, platform_product_id="8811",
                    platform_product_name="지문방지 필름", quantity=1,
                    selling_price=Decimal(price),
                    order_date=datetime.combine(d, time(12, 0)), status="delivered"))
    # 광고 원장은 **어제까지만** 존재한다(옵션 XLSX는 익일 적재) — 오늘 탭이 최신일을 찾는 근거.
    s.add(CoupangAdOptionDaily(
        report_date=YESTERDAY, vendor_id=OHITECH_VENDOR, sell_type="3P",
        ad_option_id="8811", conv_option_id="8811", impressions=10, clicks=1,
        ad_spend=Decimal("40361"), orders=0, sales_qty=0,
        conversion_revenue=Decimal("71600")))
    s.commit()
    try:
        yield s
    finally:
        s.close()


def _iso(d) -> str:
    return d.isoformat()


def test_오늘을_날짜로_골라도_오늘_버튼과_같다(db):
    """★핵심 — days=0의 세 갈래(latest_ad·폴백 조건·ad_today)가 날짜 경로에서도 같아야 한다."""
    by_button = sales_summary(company="오하이테크", days=0, db=db)
    by_date = sales_summary(company="오하이테크", days=7,
                            date_from=_iso(TODAY), date_to=_iso(TODAY), db=db)
    assert by_date["period"] == by_button["period"]
    assert by_date["ad_ref_date"] == by_button["ad_ref_date"], \
        "오늘 탭의 광고 기준일이 날짜 경로에서 달라졌다"
    assert by_date["summary"] == by_button["summary"]


def test_오늘_탭은_어제_광고원장을_기준일로_잡는다(db):
    """위 동치가 «둘 다 None»으로 성립하면 의미가 없다 — 값 자체를 못박는다."""
    out = sales_summary(company="오하이테크", days=7,
                        date_from=_iso(TODAY), date_to=_iso(TODAY), db=db)
    assert out["ad_ref_date"] == _iso(YESTERDAY)
    assert Decimal(out["summary"]["ad_spend"]) == Decimal("40361")


def test_어제를_날짜로_골라도_어제_버튼과_같다(db):
    by_button = sales_summary(company="오하이테크", days=1, db=db)
    by_date = sales_summary(company="오하이테크", days=7,
                            date_from=_iso(YESTERDAY), date_to=_iso(YESTERDAY), db=db)
    assert by_date["summary"] == by_button["summary"]
    assert by_date["ad_ref_date"] is None, "어제 하루는 «오늘 탭»이 아니다"


def test_다일_구간은_오늘_탭_레이아웃으로_안_빠진다(db):
    """오늘을 «포함»하는 것과 «오늘 하루»는 다르다 — ad_ref_date가 뜨면 프론트가 분기한다."""
    out = sales_summary(company="오하이테크", days=7,
                        date_from=_iso(YESTERDAY), date_to=_iso(TODAY), db=db)
    assert out["ad_ref_date"] is None


def test_days_프리셋은_안_바뀐다(db):
    """프리셋이 **어느 창**을 가리키는지 못박는다 — `from <= to`만 보면 창이 밀려도 통과한다."""
    expected = {
        0:  (TODAY, TODAY),
        1:  (YESTERDAY, YESTERDAY),
        7:  (TODAY - timedelta(days=6), TODAY),
        15: (TODAY - timedelta(days=14), TODAY),
        30: (TODAY - timedelta(days=29), TODAY),
    }
    for d, (f, t_) in expected.items():
        out = sales_summary(company="오하이테크", days=d, db=db)
        assert out["period"] == {"from": _iso(f), "to": _iso(t_)}, f"days={d} 창이 밀렸다"


@pytest.mark.parametrize("kwargs", [
    {"date_from": None, "date_to": None},        # 정상(프리셋)
])
def test_날짜를_안_주면_종전대로_동작한다(db, kwargs):
    out = sales_summary(company="오하이테크", days=1, db=db, **kwargs)
    assert out["period"]["from"] == _iso(YESTERDAY)


def test_잘못된_기간은_거절한다(db):
    """검증은 공용 `app.utils.date_range`가 한다 — 이 화면에서도 실제로 걸리는지 본다."""
    cases = [
        ({"date_from": _iso(TODAY)}, "한쪽만"),
        ({"date_from": _iso(TODAY), "date_to": _iso(YESTERDAY)}, "뒤집힘"),
        ({"date_from": _iso(TODAY - timedelta(days=90)), "date_to": _iso(TODAY)}, "90일 초과"),
        ({"date_from": "2026/08/14", "date_to": _iso(TODAY)}, "형식"),
    ]
    for kwargs, why in cases:
        with pytest.raises(HTTPException) as e:
            sales_summary(company="오하이테크", days=7, db=db, **kwargs)
        assert e.value.status_code == 400, why
