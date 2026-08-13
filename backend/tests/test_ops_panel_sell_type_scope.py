# test_ops_panel_sell_type_scope.py — 쿠팡 운영 패널(sales-summary)의 판매방식 축
#
# ★2026-08-13 라이브 회귀(오하이테크, 2026-08-12 「어제」):
#     상단 KPI  : 매출 53,700 · 광고비 576,573 · 이익 −543,622 (이익률 −1012.3%)
#     하단 상품표: 매출 53,700 · 광고비  40,361 · 이익   −7,410
#   같은 화면 안에서 광고 축이 14배 갈렸다. KPI가 vendor_id만으로 광고를 걸러 Retail(1P,
#   로켓배송) 536,212원을 3P 매출 옆에 얹었기 때문이다. 오하이테크는 Wing2·RG2·로켓배송이
#   vendor_id(A01029796)를 공유하므로 vendor만으로는 축이 안 갈린다.
#
#   같은 결함을 2026-08-03에 커맨드센터에서 이미 고쳤다(교훈 #103,
#   test_command_center_sell_type_scope.py). 그런데 이 화면은 쿼리를 따로 써서 그 수정이
#   닿지 않았다 — 그래서 이번엔 축의 정의를 ad_sell_type.py 한 곳에 두고 둘이 같이 읽는다.
from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Channel, CoupangAdOptionDaily, Order
from app.routers.coupang_ops import sales_summary
from app.utils.kst import kst_today

OHITECH_VENDOR = "A01029796"   # 1P·2P·3P가 vendor_id를 공유하는 계정
OFIX_VENDOR = "A01564720"      # Retail 행이 없는 계정(회귀 방어용)
YESTERDAY = kst_today() - timedelta(days=1)

WING_VID = "8811"      # 주문이 있는 3P 옵션
RETAIL_VID = "9911"    # 로켓배송 옵션 — 주문 테이블에 대응 매출이 원리적으로 없다


def _ad(vendor: str, sell_type: str, vid: str, spend: str, conv: str):
    return CoupangAdOptionDaily(
        report_date=YESTERDAY, vendor_id=vendor, sell_type=sell_type,
        ad_option_id=vid, conv_option_id=vid,
        impressions=10, clicks=1, ad_spend=Decimal(spend),
        orders=0, sales_qty=0, conversion_revenue=Decimal(conv),
    )


@pytest.fixture(autouse=True)
def _vendor_cfg(monkeypatch):
    """계정별 vendor_id 주입 — 테스트 env엔 쿠팡 설정이 없어 `_safe_cfg`가 None을 준다.

    ★그러면 `ad_filter`가 통째로 비어 **전 vendor가 들어온다**(계정 분리가 조용히 풀린다).
      prod엔 env가 있어 실제로는 안 걸리지만, 그 폴백 자체가 이 화면의 약한 고리다.
    """
    class _Cfg:
        def __init__(self, vid): self.vendor_id = vid

    vendors = {
        "COUPANG_WING1": OFIX_VENDOR, "COUPANG_RG1": OFIX_VENDOR,
        "COUPANG_WING2": OHITECH_VENDOR, "COUPANG_RG2": OHITECH_VENDOR,
        "COUPANG_ROCKET": OHITECH_VENDOR,
    }
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
    wing1 = Channel(name="오픽스 Wing", code="COUPANG_WING1",
                    platform="coupang", channel_type="marketplace")
    s.add_all([wing2, wing1])
    s.flush()
    s.add_all([
        # 오하이테크 3P 주문 — 라이브와 같은 53,700원
        Order(channel_id=wing2.id, order_number="O-1", platform_product_id=WING_VID,
              platform_product_name="오하이 지문방지 필름", quantity=1,
              selling_price=Decimal("53700"),
              order_date=datetime.combine(YESTERDAY, time(12, 0)), status="delivered"),
        # 광고 — 라이브와 같은 두 축
        _ad(OHITECH_VENDOR, "Retail", RETAIL_VID, "536212", "1383850"),
        _ad(OHITECH_VENDOR, "3P", WING_VID, "40361", "71600"),
    ])
    s.commit()
    try:
        yield s
    finally:
        s.close()


def _summary(db, company="오하이테크"):
    return sales_summary(company=company, days=1, db=db)


def test_KPI_광고비는_1P를_안_담는다(db):
    s = _summary(db)["summary"]
    assert Decimal(s["ad_spend"]) == Decimal("40361"), (
        f"1P 광고비가 3P KPI에 섞였다: {s['ad_spend']} (기대 40361)")
    assert Decimal(s["conv_revenue"]) == Decimal("71600")


def test_뺀_1P_광고비는_숨기지_않는다(db):
    """빼되 사라지면 안 된다 — 은폐는 정합이 아니다."""
    s = _summary(db)["summary"]
    assert Decimal(s["excluded_ad_spend"]) == Decimal("536212")
    assert Decimal(s["excluded_ad_conv"]) == Decimal("1383850")


def test_KPI_이익이_상품표_합계와_일치한다(db):
    """이 회귀의 본질 — 같은 화면의 두 숫자가 갈리면 안 된다."""
    out = _summary(db)
    kpi = Decimal(out["summary"]["profit"])
    table = sum((Decimal(r["profit"]) for r in out["by_product"]), Decimal("0"))
    assert kpi == table, f"KPI 이익 {kpi} ≠ 상품표 합계 {table}"


def test_이익이_라이브_기대값이다(db):
    """수수료 7.8%×VAT 폴백 기준: 53,700 − 4,607 − 0 − 40,361 − 1,900(한진) 근처."""
    s = _summary(db)["summary"]
    assert Decimal(s["profit"]) > Decimal("-100000"), (
        f"1P 광고비가 여전히 차감되고 있다: {s['profit']}")


def test_판매유형_분해의_합이_총계와_같다(db):
    """분해가 총계와 어긋나면 둘 중 하나는 거짓말이다."""
    out = _summary(db)
    s = out["summary"]
    for field in ("revenue", "fee", "cost", "shipping"):
        part = sum((Decimal(r[field]) for r in out["by_sell_type"]), Decimal("0"))
        assert part == Decimal(s[field]), f"{field}: 분해합 {part} ≠ 총계 {s[field]}"
    ad_part = sum((Decimal(r["ad_spend"]) for r in out["by_sell_type"]), Decimal("0"))
    assert ad_part + Decimal(s["ad_spend_unassigned"]) == Decimal(s["ad_spend"]), (
        "광고비 분해합 + 미분류가 총계와 다르다")


def test_분해가_3P와_2P_둘_다_낸다(db):
    """2P가 0원인 날에도 칸이 사라지면 «없다»와 «0»을 구분 못 한다."""
    rows = {r["sell_type"]: r for r in _summary(db)["by_sell_type"]}
    assert set(rows) == {"3P", "2P"}
    assert Decimal(rows["3P"]["ad_spend"]) == Decimal("40361")
    assert Decimal(rows["2P"]["ad_spend"]) == Decimal("0")


def test_오픽스는_값이_안_바뀐다(db):
    """Retail 행이 없는 계정은 이 필터로 달라지는 게 없어야 한다(회귀 방어)."""
    db.add(_ad(OFIX_VENDOR, "3P", "7001", "116115", "144540"))
    db.commit()
    s = _summary(db, company="오픽스")["summary"]
    assert Decimal(s["ad_spend"]) == Decimal("116115")
    assert Decimal(s["excluded_ad_spend"]) == Decimal("0")
