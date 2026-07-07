# test_naver_ad_report.py — 네이버 SA 광고 리포트(P1) 파이프라인 단위 테스트
# 커버: metrics_aggregator(grain집계·파생지표), actual_revenue(매출제외 필터),
#   hourly_pacing(누적→증분), ad_report Harness(3열 ROAS·비교기간 델타).
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverHourlySnapshot, Order
from app.services.naver_ad import actual_revenue, ad_report, hourly_pacing, metrics_aggregator
from app.utils.kst import kst_now

D = date(2026, 7, 5)
D2 = date(2026, 7, 6)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _ad(ad_date, camp, grp, kw, imp, clk, cost, rank_sum, cd_cnt, ci_cnt, cd_amt, ci_amt, ctype="WEB_SITE"):
    return NaverAdDaily(
        ad_date=ad_date, campaign_id=camp, campaign_type=ctype, adgroup_id=grp, keyword_id=kw,
        imp=imp, clk=clk, cost=cost, rank_sum=rank_sum,
        conv_direct_cnt=cd_cnt, conv_indirect_cnt=ci_cnt,
        conv_direct_amt=cd_amt, conv_indirect_amt=ci_amt,
    )


def _seed_ad_daily(db, ad_date=D):
    # A/kw1, A/kw2 (승자), B/'' (출혈: 전환 0)
    db.add(_ad(ad_date, "A", "g1", "nkw-1", 1000, 100, 50000, 3000, 5, 5, 100000, 50000))
    db.add(_ad(ad_date, "A", "g1", "nkw-2", 1000, 100, 50000, 3000, 5, 5, 100000, 50000))
    db.add(_ad(ad_date, "B", "g9", "", 1000, 50, 100000, 5000, 0, 0, 0, 0, ctype="SHOPPING"))
    db.commit()


def test_metrics_aggregator_totals_and_derived(db):
    _seed_ad_daily(db)
    res = metrics_aggregator.aggregate(db, D, D, grain="campaign")
    t = res["totals"]
    assert t["imp"] == 3000 and t["clk"] == 250 and t["cost"] == 200000
    assert t["conv_cnt"] == 20 and t["conv_amt"] == 300000
    assert t["ctr"] == pytest.approx(250 / 3000, abs=1e-4)
    assert t["cpc"] == pytest.approx(800.0)
    assert t["avg_rank"] == pytest.approx(11000 / 3000, abs=1e-2)
    assert t["roas_naver"] == pytest.approx(1.5)   # 300000/200000
    assert t["roas_direct"] == pytest.approx(1.0)  # 200000/200000


def test_metrics_aggregator_campaign_grain_rows(db):
    _seed_ad_daily(db)
    res = metrics_aggregator.aggregate(db, D, D, grain="campaign")
    rows = {r["campaign_id"]: r for r in res["rows"]}
    assert set(rows) == {"A", "B"}
    assert rows["A"]["cost"] == 100000 and rows["A"]["roas_naver"] == pytest.approx(3.0)
    assert rows["B"]["cost"] == 100000 and rows["B"]["roas_naver"] == pytest.approx(0.0)
    # 비용 큰 순 정렬(동률이면 순서 무관) — 둘 다 100000
    assert res["rows"][0]["cost"] >= res["rows"][1]["cost"]


def test_metrics_aggregator_keyword_grain_and_zero_denominator(db):
    _seed_ad_daily(db)
    res = metrics_aggregator.aggregate(db, D, D, grain="keyword")
    b = [r for r in res["rows"] if r["campaign_id"] == "B"][0]
    assert b["keyword_id"] == "" and b["roas_naver"] == pytest.approx(0.0)
    # imp>0이므로 avg_rank 정의됨. cost>0, clk>0 → cpc 정의됨
    assert b["cpc"] is not None


def test_metrics_aggregator_date_grain_sorted(db):
    _seed_ad_daily(db, D2)
    _seed_ad_daily(db, D)
    res = metrics_aggregator.aggregate(db, D, D2, grain="date")
    dates = [r["ad_date"] for r in res["rows"]]
    assert dates == [D.isoformat(), D2.isoformat()]  # 오름차순


def test_metrics_aggregator_campaign_filter(db):
    _seed_ad_daily(db)
    res = metrics_aggregator.aggregate(db, D, D, grain="campaign", campaign_filter="A")
    assert res["totals"]["cost"] == 100000
    assert {r["campaign_id"] for r in res["rows"]} == {"A"}


def test_metrics_aggregator_invalid_grain(db):
    with pytest.raises(ValueError):
        metrics_aggregator.aggregate(db, D, D, grain="hour")


def test_actual_revenue_excludes_non_revenue_status(db):
    from datetime import datetime, time
    dt = datetime.combine(D, time(12, 0))
    db.add(Order(channel_id=6, order_number="o1", platform_product_id="p1",
                 selling_price=Decimal("150000"), quantity=1, order_date=dt, status="delivered"))
    db.add(Order(channel_id=6, order_number="o2", platform_product_id="p2",
                 selling_price=Decimal("99999"), quantity=1, order_date=dt, status="cancelled"))
    db.add(Order(channel_id=6, order_number="o3", platform_product_id="p3",
                 selling_price=Decimal("50000"), quantity=1, order_date=dt, status="delivered"))
    # 다른 채널 주문은 제외
    db.add(Order(channel_id=1, order_number="x1", platform_product_id="p9",
                 selling_price=Decimal("777"), quantity=1, order_date=dt, status="delivered"))
    db.commit()
    res = actual_revenue.naver_order_revenue(db, D, D)
    assert res["revenue"] == 200000  # 150000 + 50000 (cancelled·타채널 제외)
    assert res["order_count"] == 2


def test_hourly_pacing_cumulative_to_incremental(db):
    # 캠페인A: 8시 1000, 9시 3000, 10시 3500 (누적)
    for h, c in [(8, 1000), (9, 3000), (10, 3500)]:
        db.add(NaverHourlySnapshot(snapshot_at=kst_now(), ad_date=D, snapshot_hour=h,
                                   campaign_id="A", cost=c, clk=h, imp=h * 10))
    # 캠페인B: 9시 500 (누적) → 9시 슬롯에 +500
    db.add(NaverHourlySnapshot(snapshot_at=kst_now(), ad_date=D, snapshot_hour=9,
                               campaign_id="B", cost=500, clk=1, imp=10))
    db.commit()
    res = hourly_pacing.hourly_rows(db, ad_date=D)
    by_hour = {r["hour"]: r for r in res["rows"]}
    assert by_hour[8]["cost"] == 1000   # 첫 기록 = 그 값
    assert by_hour[9]["cost"] == 2500   # A증분 2000 + B증분 500
    assert by_hour[10]["cost"] == 500   # 3500-3000
    assert res["total_cost"] == 4000
    assert res["ad_date"] == D.isoformat()
    assert res["clamped"] == 0  # 정상 누적 증가만 있었음


def test_hourly_pacing_clamps_and_flags_cumulative_reset(db):
    # 캠페인A: 8시 3000 → 9시 1000(리셋/재적재 이상치, 누적 감소) → 클램프 0 + clamped 카운트 반영
    for h, c in [(8, 3000), (9, 1000)]:
        db.add(NaverHourlySnapshot(snapshot_at=kst_now(), ad_date=D, snapshot_hour=h,
                                   campaign_id="A", cost=c, clk=1, imp=10))
    db.commit()
    res = hourly_pacing.hourly_rows(db, ad_date=D)
    by_hour = {r["hour"]: r for r in res["rows"]}
    assert by_hour[9]["cost"] == 0  # 음수 증분은 0으로 클램프
    assert res["clamped"] == 1  # 이상치 발생 1건이 가시화됨(원칙22)


def test_hourly_pacing_empty(db):
    res = hourly_pacing.hourly_rows(db, ad_date=D)
    assert res["rows"] == [] and res["total_cost"] == 0 and res["clamped"] == 0


def test_build_report_3col_roas(db):
    from datetime import datetime, time
    _seed_ad_daily(db)
    dt = datetime.combine(D, time(12, 0))
    db.add(Order(channel_id=6, order_number="o1", platform_product_id="p1",
                 selling_price=Decimal("200000"), quantity=1, order_date=dt, status="delivered"))
    db.commit()
    rep = ad_report.build_report(db, D, D, grain="campaign")
    r3 = rep["roas_3col"]
    assert r3["cost"] == 200000
    assert r3["naver"]["roas"] == pytest.approx(1.5)         # 네이버 convAmt 과대
    assert r3["direct"]["roas"] == pytest.approx(1.0)
    assert r3["actual_order"]["roas"] == pytest.approx(1.0)  # 실주문 200000/200000
    assert r3["actual_order"]["order_count"] == 1
    assert rep["kpis"]["cost"] == 200000
    assert len(rep["trend"]) == 1                            # 하루치
    assert {row["campaign_id"] for row in rep["rows"]} == {"A", "B"}


def test_build_report_compare_deltas(db):
    _seed_ad_daily(db, D)     # cost 200000
    # 비교기간(D2 하루 전날로 절반 규모)
    db.add(_ad(D2 - timedelta(days=10), "A", "g1", "nkw-1", 500, 50, 100000, 1500, 2, 0, 50000, 0))
    db.commit()
    cmp_from = D2 - timedelta(days=10)
    rep = ad_report.build_report(db, D, D, grain="date",
                                 compare_from=cmp_from, compare_to=cmp_from)
    assert "compare" in rep
    d = rep["compare"]["deltas_pct"]
    # 현재 cost 200000 vs 비교 100000 → +100%
    assert d["cost"] == pytest.approx(100.0)


def test_build_report_hour_grain(db):
    _seed_ad_daily(db)
    for h, c in [(8, 1000), (9, 3000)]:
        db.add(NaverHourlySnapshot(snapshot_at=kst_now(), ad_date=D, snapshot_hour=h,
                                   campaign_id="A", cost=c, clk=h, imp=h * 10))
    db.commit()
    rep = ad_report.build_report(db, D, D, grain="hour")
    assert rep["grain"] == "hour"
    assert "hourly_meta" in rep and rep["hourly_meta"]["ad_date"] == D.isoformat()
    assert [r["hour"] for r in rep["rows"]] == [8, 9]
    # KPI·3열은 여전히 naver_ad_daily 기준(hour grain이어도 총계 동일)
    assert rep["kpis"]["cost"] == 200000
