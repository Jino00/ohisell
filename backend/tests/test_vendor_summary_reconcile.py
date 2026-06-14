# test_vendor_summary_reconcile.py — Wing 세션 자동화 트랙 S2 머니/비율 fixture (인메모리 SQLite)
# vendor_summary_sync(ingest/snapshot/조회) + revenue_reconcile Harness(닫힌일 드리프트%).
# D-2 사실·지표만 / D-3 닫힌 과거일만 / 읽기전용(net_profit 불변).
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Channel, CoupangProductItem, CoupangRgOrderItem, Order
from app.services.coupang import vendor_summary_sync as vss
from app.services.coupang.intelligence import compute_command_center
from app.services.coupang.revenue_reconcile import reconcile_revenue
from app.utils.kst import kst_today

_Z = Decimal(0)
# 닫힌 윈도우(어제까지). kst_today()는 실제 오늘이므로 충분히 과거인 6/8~6/13 사용(ref 18과 동일).
D_FROM = date(2026, 6, 8)
D_TO = date(2026, 6, 13)
OD = datetime(2026, 6, 8, 12, 0, 0)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _ch(db, cid, code, company):
    db.add(Channel(id=cid, name=f"c{cid}", code=code, platform="coupang", company=company))


def _product(db, vid, account_key, vendor_id):
    db.add(CoupangProductItem(
        vendor_item_id=vid, account_key=account_key, vendor_id=vendor_id,
        seller_product_id=f"SP_{vid}", item_name=f"opt{vid}", sale_price=_Z))


def _three_p(db, channel_id, oid, vid, price, qty=1, od=OD):
    db.add(Order(channel_id=channel_id, order_number=oid, platform_product_id=vid,
                 quantity=qty, selling_price=Decimal(str(price)), order_date=od, status="delivered"))


def _rg_order(db, account_key, vendor_id, vid, unit, qty, oid, od=OD):
    db.add(CoupangRgOrderItem(
        order_id=oid, vendor_item_id=vid, account_key=account_key, vendor_id=vendor_id,
        sales_quantity=qty, unit_sales_price=Decimal(str(unit)), paid_at=od))


def _ingest(db, account_key, rows):
    return vss.ingest_vendor_summary(db, account_key, rows)


# ════════════════════════════════ ingest / store SA ════════════════════════════════
def test_ingest_snapshot_upsert_replaces_not_duplicates(db):
    """같은 (date, account_key, type)를 다시 ingest하면 교체(중복 적재 아님)."""
    _ingest(db, "COUPANG_WING1", [
        {"date": D_FROM, "registration_type": "NORMAL", "gmv": 100, "units_sold": 1},
    ])
    _ingest(db, "COUPANG_WING1", [
        {"date": D_FROM, "registration_type": "NORMAL", "gmv": 250, "units_sold": 3},
    ])
    t = vss.get_vendor_summary_totals(db, "COUPANG_WING1", D_FROM, D_TO)
    assert t["gmv_3p"] == 250          # 100이 아니라 교체된 250
    assert t["days_with_data"] == 1


def test_get_totals_splits_3p_and_rg(db):
    """NORMAL=3P / RFM=RG 분리 합산."""
    _ingest(db, "COUPANG_WING1", [
        {"date": D_FROM, "registration_type": "NORMAL", "gmv": 1000, "units_sold": 2},
        {"date": D_FROM, "registration_type": "RFM", "gmv": 700, "units_sold": 1},
        {"date": date(2026, 6, 9), "registration_type": "NORMAL", "gmv": 500, "units_sold": 1},
    ])
    t = vss.get_vendor_summary_totals(db, "COUPANG_WING1", D_FROM, D_TO)
    assert t["gmv_3p"] == 1500
    assert t["gmv_rg"] == 700
    assert t["gmv_total"] == 2200
    assert t["days_with_data"] == 2


def test_get_totals_account_filter_and_none_sums_all(db):
    """account_key 필터 + None=전체 합산."""
    _ingest(db, "COUPANG_WING1", [{"date": D_FROM, "registration_type": "NORMAL", "gmv": 1000, "units_sold": 1}])
    _ingest(db, "COUPANG_WING2", [{"date": D_FROM, "registration_type": "NORMAL", "gmv": 300, "units_sold": 1}])
    assert vss.get_vendor_summary_totals(db, "COUPANG_WING1", D_FROM, D_TO)["gmv_3p"] == 1000
    assert vss.get_vendor_summary_totals(db, "COUPANG_WING2", D_FROM, D_TO)["gmv_3p"] == 300
    assert vss.get_vendor_summary_totals(db, None, D_FROM, D_TO)["gmv_3p"] == 1300


def test_unknown_registration_type_ignored(db):
    """스키마 방어: NORMAL/RFM 외 등록유형은 무시."""
    r = _ingest(db, "COUPANG_WING1", [
        {"date": D_FROM, "registration_type": "WEIRD", "gmv": 999, "units_sold": 1},
        {"date": D_FROM, "registration_type": "NORMAL", "gmv": 100, "units_sold": 1},
    ])
    assert r["ingested"] == 1
    assert vss.get_vendor_summary_totals(db, "COUPANG_WING1", D_FROM, D_TO)["gmv_3p"] == 100


# ════════════════════════════════ reconcile Harness ════════════════════════════════
def test_drift_reproduces_ref18(db):
    """ref 18 수동 대조 자동 재현: 3P +1.8%, RG +7.4% (단일 닫힌일 = 완전 적재)."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _product(db, "V3P", "COUPANG_WING1", "A01564720")
    _product(db, "VRG", "COUPANG_WING1", "A01564720")
    _three_p(db, 1, "O1", "V3P", 1724230)                              # 우리 3P 1,724,230
    _rg_order(db, "COUPANG_WING1", "A01564720", "VRG", 1918700, 1, "RG1")  # 우리 RG 1,918,700
    _ingest(db, "COUPANG_WING1", [
        {"date": D_FROM, "registration_type": "NORMAL", "gmv": 1693230, "units_sold": 1},
        {"date": D_FROM, "registration_type": "RFM", "gmv": 1786500, "units_sold": 1},
    ])
    db.commit()
    r = reconcile_revenue(db, D_FROM, D_FROM, "COUPANG_WING1")  # 단일 닫힌일 → complete
    assert r["has_closed_days"] is True
    assert r["has_official"] is True
    assert r["coverage"]["complete"] is True
    assert r["coverage"]["expected_days"] == 1
    assert r["ours"]["revenue_3p"] == Decimal("1724230")
    assert r["ours"]["revenue_rg"] == Decimal("1918700")
    assert r["official"]["gmv_3p"] == 1693230
    assert r["official"]["gmv_rg"] == 1786500
    assert round(float(r["drift"]["pct_3p"]), 1) == 1.8
    assert round(float(r["drift"]["pct_rg"]), 1) == 7.4
    assert r["drift"]["abs_3p"] == Decimal("31000")
    assert r["drift"]["abs_rg"] == Decimal("132200")


def test_partial_coverage_flagged_incomplete(db):
    """codex P1: 닫힌 윈도우 6일 중 일부 날짜만 적재 → complete=False(드리프트는 참고치)."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _product(db, "V3P", "COUPANG_WING1", "A01564720")
    _three_p(db, 1, "O1", "V3P", 100000)
    _ingest(db, "COUPANG_WING1", [   # 6/8, 6/9만 적재(윈도우 6/8~6/13 = 6일)
        {"date": D_FROM, "registration_type": "NORMAL", "gmv": 30000, "units_sold": 1},
        {"date": date(2026, 6, 9), "registration_type": "NORMAL", "gmv": 40000, "units_sold": 1},
    ])
    db.commit()
    r = reconcile_revenue(db, D_FROM, D_TO, "COUPANG_WING1")   # 6일 윈도우
    assert r["coverage"]["expected_days"] == 6
    assert r["coverage"]["days_with_data"] == 2
    assert r["coverage"]["complete"] is False
    assert r["has_official"] is True       # 데이터는 있지만 불완전
    assert "부분 적재" in r["note"]
    # 드리프트는 여전히 계산됨(참고치) — 권위 판정은 complete=True일 때만.
    assert r["drift"]["pct_3p"] is not None


def test_aggregate_view_never_authoritative(db):
    """codex P1 round2: account=None 집계 뷰는 full 적재여도 complete=False(계정별 커버리지 미검증)."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _product(db, "V3P", "COUPANG_WING1", "A01564720")
    _three_p(db, 1, "O1", "V3P", 50000)
    _ingest(db, "COUPANG_WING1", [
        {"date": D_FROM, "registration_type": "NORMAL", "gmv": 50000, "units_sold": 1},
    ])
    db.commit()
    r = reconcile_revenue(db, D_FROM, D_FROM, None)   # 단일일·full 적재지만 집계 뷰
    assert r["coverage"]["complete"] is False
    assert "집계" in r["note"]
    assert r["drift"]["pct_3p"] is not None   # 드리프트는 참고치로 계산됨


def test_drift_div0_guard_when_official_zero(db):
    """쿠팡 GMV 0이면 드리프트% 미정(None) — 0 나눗셈 방지."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _product(db, "V3P", "COUPANG_WING1", "A01564720")
    _three_p(db, 1, "O1", "V3P", 50000)
    # official 미적재(0)
    db.commit()
    r = reconcile_revenue(db, D_FROM, D_TO, "COUPANG_WING1")
    assert r["has_official"] is False
    assert r["drift"]["pct_3p"] is None
    assert r["drift"]["abs_3p"] == Decimal("50000")   # 절대차는 계산됨


def test_closed_day_clamp_excludes_today(db):
    """오늘을 포함한 윈도우 요청 → closed_through는 어제(D-3)."""
    today = kst_today()
    r = reconcile_revenue(db, today - timedelta(days=3), today, "COUPANG_WING1")
    assert r["period"]["closed_through"] == (today - timedelta(days=1)).isoformat()
    assert r["has_closed_days"] is True


def test_no_closed_days_when_today_only(db):
    """요청 윈도우가 오늘뿐이면 대조 생략(닫힌 과거일 없음)."""
    today = kst_today()
    r = reconcile_revenue(db, today, today, "COUPANG_WING1")
    assert r["has_closed_days"] is False
    assert r["ours"] is None
    assert r["drift"] is None


def test_reconcile_is_read_only_net_profit_unchanged(db):
    """reconcile 호출 전후 compute_command_center net_profit 불변(읽기전용)."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _product(db, "V3P", "COUPANG_WING1", "A01564720")
    _three_p(db, 1, "O1", "V3P", 80000)
    _ingest(db, "COUPANG_WING1", [
        {"date": D_FROM, "registration_type": "NORMAL", "gmv": 70000, "units_sold": 1},
    ])
    db.commit()
    before = compute_command_center(db, D_FROM, D_TO, "COUPANG_WING1")["account"]["summary"]["net_profit"]
    reconcile_revenue(db, D_FROM, D_TO, "COUPANG_WING1")
    after = compute_command_center(db, D_FROM, D_TO, "COUPANG_WING1")["account"]["summary"]["net_profit"]
    assert before == after
