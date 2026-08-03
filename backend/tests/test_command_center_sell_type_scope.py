# test_command_center_sell_type_scope.py — 커맨드센터 광고 축의 판매방식 스코프 (트랙 D-13)
#
# ★2026-08-03 실측 회귀: 오하이테크 1P 옵션 광고비를 처음 적재한 직후, 커맨드센터의
#   COUPANG_WING2(오하이테크 Wing=3P/RG) 뷰가 **매출 160,500(3P)에 광고비 5,450,601(1P)**을
#   얹어 net_profit −5,382,780으로 뒤집혔다. 오하이테크는 **같은 vendor_id로 1P와 3P를 함께**
#   갖기 때문에 vendor_id만으로는 계정 분리가 끝나지 않는다 — 판매방식으로도 잘라야 한다.
#   1P는 별도 화면(rocket-overview)이 PO 그레인으로 다룬다.
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import CoupangAdOptionDaily
from app.services.coupang.intelligence import _agg_ads

VENDOR = "A01029796"      # 오하이테크 — 1P(Retail)와 3P를 같은 vendor_id로 갖는다
D = date(2026, 7, 27)


@pytest.fixture()
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    s.add_all([
        CoupangAdOptionDaily(report_date=D, vendor_id=VENDOR, sell_type="Retail",
                             ad_option_id="1001", conv_option_id="1001",
                             impressions=10, clicks=1, ad_spend=Decimal("5450601"),
                             orders=0, sales_qty=0, conversion_revenue=Decimal("0")),
        CoupangAdOptionDaily(report_date=D, vendor_id=VENDOR, sell_type="3P",
                             ad_option_id="2002", conv_option_id="2002",
                             impressions=5, clicks=2, ad_spend=Decimal("3000"),
                             orders=1, sales_qty=1, conversion_revenue=Decimal("20000")),
    ])
    s.commit()
    try:
        yield s
    finally:
        s.close()


def test_Wing_뷰는_1P_광고비를_안_섞는다(db):
    ads = _agg_ads(db, D, D, VENDOR)
    assert "1001" not in ads, "1P(Retail) 광고비가 Wing 뷰에 섞였다 — net_profit이 뒤집힌다"
    assert ads["2002"]["spend"] == Decimal("3000")


def test_같은_vendor여도_판매방식으로_갈린다(db):
    """vendor_id 필터만으로는 부족하다는 것을 명시한다(이 회귀의 본질)."""
    total = sum(r["spend"] for r in _agg_ads(db, D, D, VENDOR).values())
    assert total == Decimal("3000"), f"Wing 축 합계에 1P가 포함됐다: {total}"


def test_의도적으로_열면_전부_보인다(db):
    """sell_types=None은 호출자가 명시할 때만 — 기본값이 조용히 전부를 담지 않는다."""
    ads = _agg_ads(db, D, D, VENDOR, sell_types=None)
    assert sum(r["spend"] for r in ads.values()) == Decimal("5453601")


def test_오픽스는_동작이_안_바뀐다(db):
    """오픽스(3P/2P만)는 Retail 행이 없으므로 이 필터로 달라지는 게 없어야 한다."""
    db.add(CoupangAdOptionDaily(report_date=D, vendor_id="A01564720", sell_type="3P",
                                ad_option_id="3003", conv_option_id="3003",
                                impressions=1, clicks=1, ad_spend=Decimal("777"),
                                orders=0, sales_qty=0, conversion_revenue=Decimal("0")))
    db.commit()
    assert _agg_ads(db, D, D, "A01564720")["3003"]["spend"] == Decimal("777")
