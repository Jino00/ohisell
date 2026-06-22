# test_ohitech_ad_sync.py — 오하이테크(1P) 광고비 ingest 머니룰 (트랙 ohitech-ad S2, D-8/D-9/D-10)
# 머니코드 fixture(인메모리 SQLite). 라이브 API 없음.
#   ① ingest → coupang_ad_report(Retail) upsert
#   ② _agg_rocket_ad가 Retail 합산 → 1P 순이익 차감(전체값 D-10)
#   ③ 재ingest 멱등(중복 없음, 값 교체)
#   ④ 윈도우 격리(다른 날짜 보존, 전체삭제 금지)
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import CoupangAdReport
from app.services.coupang.ohitech_ad_sync import ingest_ohitech_ad_cost
from app.services.coupang.rocket_intelligence import _agg_rocket_ad

VID = "A01029796"  # 오하이테크


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


# 라이브 검증값(D-9, 2026.06.15~21): 전체(ALL_DELIVERED) 일별.
_DAYS = [
    {"date": "2026-06-15", "ad_spend": 646273, "conv_sales": 1600550},
    {"date": "2026-06-16", "ad_spend": 698979, "conv_sales": 1477800},
    {"date": "2026-06-17", "ad_spend": 716586, "conv_sales": 1674050},
    {"date": "2026-06-18", "ad_spend": 505192, "conv_sales": 1316100},
    {"date": "2026-06-19", "ad_spend": 476252, "conv_sales": 1322660},
    {"date": "2026-06-20", "ad_spend": 505100, "conv_sales": 1179650},
    {"date": "2026-06-21", "ad_spend": 491221, "conv_sales": 1382410},
]
_TOTAL = Decimal("4039603")  # Σ 전체 = 화면 전체집행 광고비(D-9)


def test_ingest_creates_retail_rows(db):
    r = ingest_ohitech_ad_cost(db, VID, _DAYS)
    assert r["upserted"] == 7
    assert r["skipped"] == 0
    assert r["sell_type"] == "Retail"
    assert r["date_from"] == "2026-06-15" and r["date_to"] == "2026-06-21"
    rows = db.query(CoupangAdReport).all()
    assert len(rows) == 7
    assert all(x.sell_type == "Retail" and x.vendor_id == VID for x in rows)


def test_agg_rocket_ad_deducts_total(db):
    """_agg_rocket_ad(Retail 합)가 적재 전체값과 정확히 일치(1P 순이익 차감값, D-10)."""
    ingest_ohitech_ad_cost(db, VID, _DAYS)
    got = _agg_rocket_ad(db, date(2026, 6, 15), date(2026, 6, 21))
    assert got == _TOTAL
    # vendor_id 필터도 동일
    assert _agg_rocket_ad(db, date(2026, 6, 15), date(2026, 6, 21), VID) == _TOTAL


def test_window_bound(db):
    """윈도우 밖 날짜는 합산에서 제외(report_date 경계)."""
    ingest_ohitech_ad_cost(db, VID, _DAYS)
    # 6/15~6/17만
    partial = sum(Decimal(str(d["ad_spend"])) for d in _DAYS[:3])
    assert _agg_rocket_ad(db, date(2026, 6, 15), date(2026, 6, 17)) == partial


def test_idempotent_upsert(db):
    """재ingest는 중복 행을 만들지 않고 값을 교체(멱등)."""
    ingest_ohitech_ad_cost(db, VID, _DAYS)
    # 같은 날짜 다른 값으로 재push → 교체
    changed = [{"date": "2026-06-15", "ad_spend": 999999, "conv_sales": 0}]
    ingest_ohitech_ad_cost(db, VID, changed)
    rows = db.query(CoupangAdReport).filter(CoupangAdReport.report_date == date(2026, 6, 15)).all()
    assert len(rows) == 1  # 중복 없음
    assert rows[0].ad_spend == Decimal("999999")
    # 나머지 날짜는 보존(전체삭제 금지)
    assert db.query(CoupangAdReport).count() == 7


def test_skips_bad_rows(db):
    bad = [{"date": "nope", "ad_spend": 100}, {"ad_spend": 200}, {"date": "2026-06-15", "ad_spend": 300}]
    r = ingest_ohitech_ad_cost(db, VID, bad)
    assert r["upserted"] == 1 and r["skipped"] == 2
    assert db.query(CoupangAdReport).count() == 1


def test_empty_days(db):
    r = ingest_ohitech_ad_cost(db, VID, [])
    assert r["upserted"] == 0 and r["date_from"] is None
