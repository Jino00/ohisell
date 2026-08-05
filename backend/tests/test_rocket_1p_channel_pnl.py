# test_rocket_1p_channel_pnl.py — 로켓배송 1P 채널을 대시보드에 올리는 평행 엔진
#
# 이 파일이 지키는 것(전부 2026-08-05 라이브 실측에서 나온 계약, ref 44):
#   ① 매출 원천 = 세금계산서(payment_amount) — 발주액이 아니다(발주액은 +24.7% 과대)
#   ② 광고비 원천이 둘인데 **겹치는 하루를 두 번 세면 안 된다**(수기 XLSX ↔ 자동수집)
#   ③ 원가 근거가 없으므로 **순이익을 지어내지 않는다**(net None)
#   ④ 라우터는 기존 1P 행을 **갈아끼운다** — append하면 수기 레거시가 이중계상된다
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Channel, CoupangAdReport, CoupangRocketSettlement
from app.services.coupang import rocket_1p_channel_pnl as svc


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(Channel(id=5, code="COUPANG_ROCKET", name="쿠팡 로켓배송", platform="coupang",
                  channel_type="consignment", company="주식회사 오하이테크"))
    s.commit()
    yield s
    s.close()


def _settle(s, seq, issue, pay):
    s.add(CoupangRocketSettlement(
        invoice_seq=seq, vendor_id=svc.ROCKET_1P_VENDOR_ID,
        supply_amount=Decimal(pay) / Decimal("1.1"), vat=Decimal("0"),
        payment_amount=Decimal(pay), issue_date=date.fromisoformat(issue),
        first_payment_amount=Decimal("0"), second_payment_amount=Decimal("0")))


def _report(s, d, spend):
    s.add(CoupangAdReport(report_date=date.fromisoformat(d), sell_type="Retail",
                          vendor_id=svc.ROCKET_1P_VENDOR_ID, impressions=0, clicks=0,
                          ad_spend=Decimal(spend), orders=0, sales_qty=0,
                          conversion_revenue=Decimal("0")))


def _adcost(s, d, spend, channel_id=5):
    s.execute(text("INSERT INTO ad_costs (channel_id, ad_date, ad_spend, source) "
                   "VALUES (:c, :d, :s, 'excel')"),
              {"c": channel_id, "d": d, "s": spend})


# ═══ ① 매출 = 세금계산서 ═══
def test_revenue_comes_from_tax_invoice(db):
    _settle(db, 1, "2026-07-24", 7581450)
    _settle(db, 2, "2026-07-25", 2048128)
    db.commit()
    row = svc.compute_rocket_1p_summary_row(db, date(2026, 7, 1), date(2026, 7, 31))
    assert Decimal(row["revenue"]) == Decimal("9629578")
    assert Decimal(row["product_revenue"]) == Decimal("9629578")  # 1P는 수수료·배송비 없음
    assert Decimal(row["shipping_revenue"]) == Decimal("0")


def test_negative_reverse_issue_invoice_is_kept(db):
    """월말 역발행 차감(음수)도 그대로 더한다 — 임의 제외가 원장에서 더 멀다."""
    _settle(db, 1, "2026-07-24", 7581450)
    _settle(db, 2, "2026-07-31", -830500)        # 라이브 실측 30573973
    db.commit()
    row = svc.compute_rocket_1p_summary_row(db, date(2026, 7, 1), date(2026, 7, 31))
    assert Decimal(row["revenue"]) == Decimal("6750950")


def test_window_is_by_issue_date(db):
    _settle(db, 1, "2026-06-30", 1000)
    _settle(db, 2, "2026-07-01", 2000)
    _settle(db, 3, "2026-08-01", 4000)
    db.commit()
    row = svc.compute_rocket_1p_summary_row(db, date(2026, 7, 1), date(2026, 7, 31))
    assert Decimal(row["revenue"]) == Decimal("2000")


# ═══ ② 광고비 두 원천 — 겹치는 날을 두 번 세지 않는다 ═══
def test_ad_sources_union_without_double_counting_overlap_day(db):
    """★라이브 실측: 2026-05-18 하루만 겹치고 두 값이 552,537로 **같다**.

    더하면 그날이 2배가 되고, 한쪽을 버리면 나머지 시대가 0원이 된다.
    """
    _adcost(db, "2026-05-17", 625020)            # 수기만
    _adcost(db, "2026-05-18", 552537)            # 겹침
    _report(db, "2026-05-18", 552537)            # 겹침(같은 값)
    _report(db, "2026-05-24", 1040375)           # 자동만
    db.commit()
    by_day = svc._ad_spend_by_day(db, date(2026, 5, 1), date(2026, 5, 31))
    assert by_day["2026-05-18"] == Decimal("552537")     # 1,105,074가 아니다
    assert svc._ad_spend_sum(db, date(2026, 5, 1), date(2026, 5, 31)) == Decimal(
        625020 + 552537 + 1040375)


def test_automated_source_wins_when_values_disagree(db):
    """두 원천이 어긋나면 자동수집(report/SALES)이 이긴다 — 수기는 폴백일 뿐."""
    _adcost(db, "2026-05-18", 999999)
    _report(db, "2026-05-18", 552537)
    db.commit()
    assert svc._ad_spend_by_day(db, date(2026, 5, 18), date(2026, 5, 18)) == {
        "2026-05-18": Decimal("552537")}


def test_other_channels_ad_costs_are_not_picked_up(db):
    """ad_costs는 채널 그레인 — 다른 채널(네이버 등) 광고비를 1P로 끌어오면 안 된다."""
    _adcost(db, "2026-05-17", 111111, channel_id=6)
    db.commit()
    assert svc._ad_spend_sum(db, date(2026, 5, 1), date(2026, 5, 31)) == Decimal("0")


# ═══ ③ 순이익을 지어내지 않는다 ═══
def test_net_profit_is_none_because_cost_basis_is_absent(db):
    """원가는 발주 라인이 2026-06부터만 있고, 있는 것도 31.7%가 오매핑이다(ref 44 §4).

    숫자를 내면 그건 지어낸 것이다 — 기존 '측정불가 → —' 계약을 그대로 쓴다.
    """
    _settle(db, 1, "2026-07-24", 7581450)
    db.commit()
    row = svc.compute_rocket_1p_summary_row(db, date(2026, 7, 1), date(2026, 7, 31))
    assert row["net_profit"] is None
    assert row["profit_rate"] is None
    assert Decimal(row["cost"]) == 0 and Decimal(row["commission"]) == 0


def test_no_row_when_window_is_empty(db):
    """매출도 광고비도 없으면 빈 행을 만들지 않는다."""
    assert svc.compute_rocket_1p_summary_row(db, date(2026, 1, 1), date(2026, 1, 31)) is None


def test_missing_channel_disables_feature_quietly(db):
    db.query(Channel).delete()
    db.commit()
    assert svc.compute_rocket_1p_summary_row(db, date(2026, 7, 1), date(2026, 7, 31)) is None
    assert svc.compute_rocket_1p_daily_points(db, date(2026, 7, 1), date(2026, 7, 31)) == []


# ═══ 일자 추이 — 요약과 합계가 어긋나면 안 된다 ═══
def test_daily_points_sum_matches_summary_row(db):
    _settle(db, 1, "2026-07-24", 7581450)
    _settle(db, 2, "2026-07-31", -830500)
    _adcost(db, "2026-07-02", 400000)
    _report(db, "2026-07-24", 728232)
    db.commit()
    row = svc.compute_rocket_1p_summary_row(db, date(2026, 7, 1), date(2026, 7, 31))
    pts = svc.compute_rocket_1p_daily_points(db, date(2026, 7, 1), date(2026, 7, 31))
    assert sum(Decimal(p["revenue"]) for p in pts) == Decimal(row["revenue"])
    assert sum(Decimal(p["ad_spend"]) for p in pts) == Decimal(row["ad_spend"])
    assert all(p["net_profit"] is None for p in pts)
    assert all(p["channel_id"] == 5 for p in pts)


def test_ad_only_day_still_produces_a_point(db):
    """매출 없는 날의 광고비가 사라지면 요약 RoAS와 추이 RoAS가 어긋난다."""
    _report(db, "2026-07-02", 478847)
    db.commit()
    pts = svc.compute_rocket_1p_daily_points(db, date(2026, 7, 1), date(2026, 7, 31))
    assert len(pts) == 1
    assert pts[0]["date"] == "2026-07-02"
    assert Decimal(pts[0]["revenue"]) == 0
    assert Decimal(pts[0]["ad_spend"]) == Decimal("478847")
