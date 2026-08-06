# test_ad_report_roas_basis.py — 광고 리포트 ROAS의 **축**을 행마다 밝힌다 (2026-08-06 적대 리뷰)
#
# 왜: 같은 ROAS 열인데 행마다 뜻이 다르다.
#   3P·2P  = 우리가 소비자에게 직접 판다 → 광고전환매출 = **우리 매출**
#   Retail = 쿠팡이 사입해 자기 가격으로 판다 → 광고전환매출 = **쿠팡 매출**(우리 것이 아니다)
# 라이브 실측: 화면 3P 217.4% vs Retail 254.0% → "1P 광고가 낫다"로 읽히는데,
#   1P를 납품가로 환산하면 약 151%로 **오히려 3P보다 나쁘다**. 나란히 놓으면 결론이 뒤집힌다.
# ★값을 환산하지 않는다 — 우리 몫 비율은 기간마다 다르고 판매분석이 롤링 2개월만 덮어
#   과거는 환산 근거가 없다. 추정하느니 축을 이름으로 밝힌다.
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import CoupangAdReport
from app.routers.coupang_report import get_coupang_ad_report

D = date(2026, 8, 4)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _row(s, sell_type, spend, conv, *, d=D):
    s.add(CoupangAdReport(report_date=d, sell_type=sell_type, vendor_id="A01029796",
                          impressions=1000, clicks=100, ad_spend=Decimal(spend),
                          orders=10, sales_qty=10, conversion_revenue=Decimal(conv)))


def _by_type(out):
    return {i["sell_type"]: i for i in out["items"]}


def test_retail_row_is_labeled_consumer_price(db):
    """1P 행은 '소비자가 기준'임이 응답에 나와야 한다 — 숫자만으로는 구별할 수 없다."""
    _row(db, "Retail", "1358911", "4699000")
    db.commit()
    r = _by_type(get_coupang_ad_report(date_from=D, date_to=D, db=db))["로켓배송"]
    assert r["roas_basis"] == "consumer_price"
    assert "소비자가" in r["roas_basis_label"]


def test_3p_row_is_labeled_our_revenue(db):
    _row(db, "3P", "100000", "300000")
    db.commit()
    r = _by_type(get_coupang_ad_report(date_from=D, date_to=D, db=db))["윙"]
    assert r["roas_basis"] == "our_revenue"
    assert "우리 매출" in r["roas_basis_label"]


def test_total_across_mixed_axes_is_marked_uninterpretable(db):
    """★합계는 '우리 매출 + 쿠팡 매출'이라 어느 축으로도 해석되지 않는다 — 지우지 않고 밝힌다."""
    _row(db, "3P", "100000", "300000")
    _row(db, "Retail", "1358911", "4699000")
    db.commit()
    out = get_coupang_ad_report(date_from=D, date_to=D, db=db)
    assert out["total"]["roas_basis"] == "mixed"
    assert "해석 불가" in out["total"]["roas_basis_label"]


def test_total_is_not_mixed_when_single_axis(db):
    """3P만 있으면 합계도 우리 매출 축이다 — 무조건 'mixed'로 겁주지 않는다."""
    _row(db, "3P", "100000", "300000")
    _row(db, "2P", "50000", "120000")
    db.commit()
    out = get_coupang_ad_report(date_from=D, date_to=D, db=db)
    assert out["total"]["roas_basis"] == "our_revenue"


def test_roas_values_unchanged(db):
    """축 표기는 **표면**만 바꾼다 — 숫자는 건드리지 않았다(회귀 가드)."""
    _row(db, "Retail", "1358911", "4699000")
    db.commit()
    r = _by_type(get_coupang_ad_report(date_from=D, date_to=D, db=db))["로켓배송"]
    assert r["ad_spend"] == 1358911
    assert r["conversion_revenue"] == 4699000
    assert r["roas"] == pytest.approx(345.8, abs=0.1)
