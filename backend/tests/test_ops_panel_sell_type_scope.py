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
from app.models import Channel, CoupangAdOptionDaily, CoupangRgOrderItem, Order
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
    rocket = Channel(name="오하이테크 로켓배송", code="COUPANG_ROCKET",
                     platform="coupang", channel_type="consignment")
    s.add_all([wing2, wing1, rocket])
    s.flush()
    s._chs = {"COUPANG_WING1": wing1, "COUPANG_WING2": wing2, "COUPANG_ROCKET": rocket}
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


# ══════════════════════════════════════════════════════════════════
# 적대 리뷰 1R가 요구한 회귀 — 생존 변이 6종(M-A·M-B·M-C·M-D·M-E·M-J)을 죽인다.
#   원래 픽스처가 3P 주문 1건뿐이라 **RG(2P) 경로와 일별 폴백 경로가 통째로 미검증**이었다.
#   오픽스 매출의 대부분이 RG인데도 그랬다(최근 30일 RG 13,971,550 vs Wing 1,221,210).
# ══════════════════════════════════════════════════════════════════

def _split_sum(out, field):
    return sum((Decimal(r[field]) for r in out["by_sell_type"]), Decimal("0"))


def test_M_A_M_E_RG매출과_비용이_2P_칸에_잡힌다(db):
    """M-A(매출 전부 3P로)·M-E(수수료·원가·물류 전부 3P로)를 죽인다."""
    db.add_all([
        Order(channel_id=db._chs["COUPANG_WING1"].id, order_number="W-1",
              platform_product_id="7001", platform_product_name="윙상품", quantity=1,
              selling_price=Decimal("100000"),
              order_date=datetime.combine(YESTERDAY, time(12, 0)), status="delivered"),
        CoupangRgOrderItem(order_id="RG-1", account_key="COUPANG_WING1",
                           vendor_item_id="7002", vendor_id=OFIX_VENDOR,
                           product_name="RG상품", unit_sales_price=Decimal("50000"),
                           sales_quantity=2,
                           paid_at=datetime.combine(YESTERDAY, time(12, 0))),
    ])
    db.commit()
    out = _summary(db, company="오픽스")
    rows = {r["channel_type"]: r for r in out["by_sell_type"]}
    assert Decimal(rows["로켓그로스"]["revenue"]) == Decimal("100000"), \
        "RG(2P) 매출이 2P 칸에 없다 — 전부 3P로 몰렸다"
    assert Decimal(rows["Wing"]["revenue"]) == Decimal("100000")
    # 비용도 유형별로 갈려야 한다(Wing만 한진 물류비 1,900원)
    assert Decimal(rows["Wing"]["shipping"]) > 0
    assert Decimal(rows["로켓그로스"]["shipping"]) == 0


def test_M_D_M_J_이익_분해합이_KPI와_같다(db):
    """M-D(이익식에서 물류비 누락)·M-J(이익 2배 위조)를 죽인다.

    ★1R에서 이 열을 보는 테스트가 **0건**이었다 — 표의 헤드라인 열인데도."""
    db.add(CoupangRgOrderItem(
        order_id="RG-2", account_key="COUPANG_WING2", vendor_item_id="8899",
        vendor_id=OHITECH_VENDOR, product_name="RG상품", unit_sales_price=Decimal("30000"),
        sales_quantity=1, paid_at=datetime.combine(YESTERDAY, time(12, 0))))
    db.commit()
    out = _summary(db)
    assert _split_sum(out, "profit") == Decimal(out["summary"]["profit"]), \
        "분해 이익 합이 KPI 이익과 다르다 — 같은 화면의 두 숫자가 또 갈렸다"


def test_M_B_M_C_일별폴백은_미분류로_드러나고_이익이_어긋나지_않는다(db, monkeypatch):
    """M-B(폴백 누락)·M-C(xlsx_dates scope 필터 제거)를 죽인다.

    ★1R 라이브: 오픽스 7일 탭에서 분해 이익이 KPI보다 1,363원 높았다 — 폴백분이
      KPI에만 들어가고 분해엔 어디에도 없었기 때문이다."""
    db.add(Order(channel_id=db._chs["COUPANG_WING1"].id, order_number="W-4",
                 platform_product_id="7001", platform_product_name="윙상품", quantity=1,
                 selling_price=Decimal("100000"),
                 order_date=datetime.combine(YESTERDAY, time(12, 0)), status="delivered"))
    db.commit()
    monkeypatch.setattr(
        "app.services.coupang.ad_cost_sync.get_ad_cost_range",
        lambda _db, a, b: [{"date": str(YESTERDAY), "day_cost": 123456,
                            "all_day_cost": 123456, "conv_sales": 7000}])
    out = _summary(db, company="오픽스")
    assert Decimal(out["summary"]["ad_spend_unassigned"]) == Decimal("123456")
    rows = {r["channel_type"]: r for r in out["by_sell_type"]}
    assert "미분류" in rows, "가를 수 없는 광고비가 표에서 사라졌다"
    assert Decimal(rows["미분류"]["ad_spend"]) == Decimal("123456")
    assert Decimal(rows["미분류"]["conv_revenue"]) == Decimal("7000")
    for f in ("revenue", "fee", "cost", "shipping", "ad_spend", "conv_revenue", "profit"):
        assert _split_sum(out, f) == Decimal(out["summary"][f]), f"{f}: 분해합 ≠ 총계"


def test_폴백이_없으면_미분류_행도_없다(db):
    """늘 켜진 0원 행은 읽히지 않는다 — 값이 있을 때만 낸다."""
    rows = {r["channel_type"] for r in _summary(db)["by_sell_type"]}
    assert rows == {"Wing", "로켓그로스"}


def test_P2_1_로켓배송_주문도_사라지지_않는다(db):
    """1R P2-1: 이름 없는 채널 축의 돈이 조용히 증발하던 경로.

    로켓배송 주문은 지금 prod에 0건이지만(쿠팡 매입 구조), 생기는 날 분해합이
    총계보다 작아지면 그건 «없는 돈»이 된다."""
    db.add(Order(channel_id=db._chs["COUPANG_ROCKET"].id, order_number="R-1",
                 platform_product_id="9999", platform_product_name="로켓상품", quantity=1,
                 selling_price=Decimal("1000000"),
                 order_date=datetime.combine(YESTERDAY, time(12, 0)), status="delivered"))
    db.commit()
    out = _summary(db)
    assert _split_sum(out, "revenue") == Decimal(out["summary"]["revenue"]), \
        "로켓배송 매출이 분해에서 증발했다"


def test_P2_7_이익이_라이브_기대값과_정확히_같다(db):
    """1R P2-7: `> -100000`은 −99,999도 통과시킨다 — 계약 합격기준 ①을 못 박는다.

    53,700 − 4,607.46(수수료 7.8%×1.1) − 0(원가) − 40,361(3P 광고) − 1,900(한진) = 6,831.54
    ※ 라이브 −7,410원과 다른 건 원가 10,441원이 이 픽스처에 없기 때문이다."""
    s = _summary(db)["summary"]
    assert Decimal(s["profit"]) == Decimal("6831.54"), f"기대 6831.54, 실제 {s['profit']}"


def test_P1_1_오늘탭_최신일이_Retail만_있는_날로_안_잡힌다(db):
    """1R P1-1: `latest_ad`에 scope가 없으면 Retail만 있는 날이 최신으로 잡혀
    「어제 광고비 0원」이 되고 전액이 숨는다. prod에 그런 날이 30일 연속 있었다."""
    later = YESTERDAY + timedelta(days=1)
    db.add(_ad(OHITECH_VENDOR, "Retail", "9912", "999999", "0"))
    db.query(CoupangAdOptionDaily).filter_by(ad_option_id="9912").update(
        {"report_date": later})
    db.commit()
    out = sales_summary(company="오하이테크", days=0, db=db)
    assert out["ad_ref_date"] == str(YESTERDAY), (
        f"최신일이 Retail 전용 날짜({later})로 잡혔다 — 3P/2P 광고비가 0으로 보인다")
    assert Decimal(out["summary"]["ad_spend"]) == Decimal("40361")


def test_M_C_Retail만_있는_날은_일별폴백이_열려야_한다(db, monkeypatch):
    """M-C(`xlsx_dates`의 scope 필터 제거)를 죽인다.

    ★이 필터가 실제로 갈리는 조건은 「그날 광고 원장에 Retail 행만 있는 경우」다.
      필터가 없으면 그날이 «XLSX 있음»으로 세어져 일별 폴백까지 막히고, 3P/2P 광고비가
      0인 채로 **그날 광고비가 통째로 사라진다.** prod에 그런 날이 2026-04-15~05-14
      **30일 연속** 실재했다(적대 리뷰 1R).
    """
    # 그날의 3P 행을 지워 Retail만 남긴다 — 리뷰어가 prod에서 찾은 그 형태.
    db.query(CoupangAdOptionDaily).filter_by(sell_type="3P").delete()
    db.commit()
    monkeypatch.setattr(
        "app.services.coupang.ad_cost_sync.get_ad_cost_range",
        lambda _db, a, b: [{"date": str(YESTERDAY), "day_cost": 88888,
                            "all_day_cost": 88888, "conv_sales": 0}])
    s = sales_summary(company="ALL", days=1, db=db)["summary"]
    assert Decimal(s["ad_spend"]) == Decimal("88888"), (
        "Retail만 있는 날이 «XLSX 있음»으로 세어져 폴백이 막혔다 — 그날 광고비가 사라진다")
    assert Decimal(s["ad_spend_unassigned"]) == Decimal("88888")
