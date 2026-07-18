# test_naver_cart_collection.py — D-NAO-58 CD1 장바구니(add_to_cart) 수집 데이터층 테스트
# 커버: fetch_conversion_daily가 purchase→conv_* / add_to_cart→cart_* 분리 라우팅(직/간접 유지),
#   기타 액션 무시, 구매 매출 불변. report_collector 병합 시 cart_* 보존.
#   ad_daily_ingest가 cart_* 영속화하되 total_conv(매출)에는 절대 안 섞음.
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily
from app.services.naver_ad import ad_daily_ingest, report_collector
from app.services import naver_sa_ad_fetcher as fetcher


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


# ── AD_CONVERSION 행: 13컬럼 (9직간접 10액션 11전환수 12전환매출) ──
def _conv_row(date_s, camp, grp, kw, dir_indir, action, cnt, val):
    return [date_s, "1313769", camp, grp, kw, "nad-x", "bsn-x", "27758", "M",
            dir_indir, action, str(cnt), str(val)]


def test_fetch_conversion_routes_cart_and_preserves_purchase(monkeypatch):
    """purchase→conv_*, add_to_cart→cart_*, 직접/간접 분리, 구매 매출 불변, 기타 액션 무시."""
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "x")
    monkeypatch.setattr(fetcher, "ensure_reports_built", lambda *a, **k: None)
    monkeypatch.setattr(fetcher, "_list_reports_by_type",
                        lambda tp, a, b: [{"date": "2026-07-05", "downloadUrl": "u"}])
    rows = [
        _conv_row("20260705", "cmp-01", "grp-1", "nkw-A", "1", "purchase", 1, 15900),      # 직접 구매
        _conv_row("20260705", "cmp-01", "grp-1", "nkw-A", "2", "purchase", 1, 10000),      # 간접 구매
        _conv_row("20260705", "cmp-01", "grp-1", "nkw-A", "1", "add_to_cart", 2, 5000),    # 직접 장바구니
        _conv_row("20260705", "cmp-01", "grp-1", "nkw-A", "2", "add_to_cart", 3, 7000),    # 간접 장바구니
        _conv_row("20260705", "cmp-01", "grp-1", "nkw-A", "1", "signup", 9, 99999),        # 기타 → 무시
    ]
    monkeypatch.setattr(fetcher, "_download_tsv", lambda url: rows)

    out = fetcher.fetch_conversion_daily(date(2026, 7, 5), date(2026, 7, 5))
    assert len(out) == 1
    r = out[0]
    # 구매 매출/수량은 byte-for-byte 불변(장바구니 섞이면 안 됨)
    assert r["conv_direct_cnt"] == 1 and r["conv_direct_amt"] == 15900
    assert r["conv_indirect_cnt"] == 1 and r["conv_indirect_amt"] == 10000
    # 장바구니는 별도 cart_* 로 라우팅(직/간접 유지)
    assert r["cart_direct_cnt"] == 2 and r["cart_direct_amt"] == 5000
    assert r["cart_indirect_cnt"] == 3 and r["cart_indirect_amt"] == 7000


def test_fetch_conversion_cart_only_row_kept(monkeypatch):
    """구매 0인데 장바구니만 있는 grain도 보존(선행지표 재료 — 클릭 병리 탐침 대상)."""
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "x")
    monkeypatch.setattr(fetcher, "ensure_reports_built", lambda *a, **k: None)
    monkeypatch.setattr(fetcher, "_list_reports_by_type",
                        lambda tp, a, b: [{"date": "2026-07-05", "downloadUrl": "u"}])
    rows = [
        _conv_row("20260705", "cmp-02", "grp-9", "-", "1", "add_to_cart", 4, 8000),
    ]
    monkeypatch.setattr(fetcher, "_download_tsv", lambda url: rows)
    out = fetcher.fetch_conversion_daily(date(2026, 7, 5), date(2026, 7, 5))
    assert len(out) == 1
    r = out[0]
    assert r["keyword_id"] == ""  # "-" 정규화
    assert r["conv_direct_cnt"] == 0 and r["conv_direct_amt"] == 0
    assert r["cart_direct_cnt"] == 4 and r["cart_direct_amt"] == 8000


def test_report_collector_carries_cart_fields():
    """collect_daily_rows가 ad-row·conv-only 양쪽 분기에서 cart_* 초기화·병합."""
    ad_rows = [{"date": "2026-07-05", "campaign_id": "cmp-01", "adgroup_id": "grp-1",
                "keyword_id": "nkw-A", "imp": 15, "clk": 2, "cost": 150, "rank_sum": 40}]
    conv_rows = [
        {"date": "2026-07-05", "campaign_id": "cmp-01", "adgroup_id": "grp-1", "keyword_id": "nkw-A",
         "conv_direct_cnt": 1, "conv_indirect_cnt": 0, "conv_direct_amt": 15900, "conv_indirect_amt": 0,
         "cart_direct_cnt": 2, "cart_indirect_cnt": 1, "cart_direct_amt": 5000, "cart_indirect_amt": 3000},
        # 성과행 없는 장바구니-only(다른 그룹) → 0성과 행 생성돼야 보존
        {"date": "2026-07-05", "campaign_id": "cmp-01", "adgroup_id": "grp-9", "keyword_id": "",
         "conv_direct_cnt": 0, "conv_indirect_cnt": 0, "conv_direct_amt": 0, "conv_indirect_amt": 0,
         "cart_direct_cnt": 4, "cart_indirect_cnt": 0, "cart_direct_amt": 8000, "cart_indirect_amt": 0},
    ]
    rows = report_collector.collect_daily_rows(
        date(2026, 7, 5), date(2026, 7, 5),
        campaign_types={"cmp-01": "SHOPPING"}, ad_rows=ad_rows, conv_rows=conv_rows,
    )
    by = {(r["campaign_id"], r["adgroup_id"], r["keyword_id"]): r for r in rows}
    m = by[("cmp-01", "grp-1", "nkw-A")]
    assert m["cart_direct_cnt"] == 2 and m["cart_indirect_cnt"] == 1
    assert m["cart_direct_amt"] == 5000 and m["cart_indirect_amt"] == 3000
    assert m["conv_direct_amt"] == 15900  # 구매 매출 불변
    cart_only = by[("cmp-01", "grp-9", "")]
    assert cart_only["imp"] == 0 and cart_only["cart_direct_cnt"] == 4 and cart_only["cart_direct_amt"] == 8000


def test_report_collector_defaults_cart_zero_when_conv_lacks_cart():
    """구버전 conv_rows(cart_* 키 없음)도 크래시 없이 cart_*=0으로."""
    ad_rows = [{"date": "2026-07-05", "campaign_id": "cmp-01", "adgroup_id": "grp-1",
                "keyword_id": "nkw-A", "imp": 15, "clk": 2, "cost": 150, "rank_sum": 40}]
    conv_rows = [
        {"date": "2026-07-05", "campaign_id": "cmp-01", "adgroup_id": "grp-1", "keyword_id": "nkw-A",
         "conv_direct_cnt": 1, "conv_indirect_cnt": 0, "conv_direct_amt": 15900, "conv_indirect_amt": 0},
    ]
    rows = report_collector.collect_daily_rows(
        date(2026, 7, 5), date(2026, 7, 5),
        campaign_types={"cmp-01": "WEB_SITE"}, ad_rows=ad_rows, conv_rows=conv_rows,
    )
    m = rows[0]
    assert m["cart_direct_cnt"] == 0 and m["cart_indirect_cnt"] == 0
    assert m["cart_direct_amt"] == 0 and m["cart_indirect_amt"] == 0


def test_ingest_persists_cart_without_inflating_total_conv(db):
    rows = [
        {"ad_date": "2026-07-05", "campaign_id": "cmp-01", "campaign_type": "SHOPPING",
         "adgroup_id": "grp-1", "keyword_id": "", "imp": 15, "clk": 2, "cost": 150, "rank_sum": 40,
         "conv_direct_cnt": 1, "conv_indirect_cnt": 0, "conv_direct_amt": 15900, "conv_indirect_amt": 0,
         "cart_direct_cnt": 2, "cart_indirect_cnt": 1, "cart_direct_amt": 5000, "cart_indirect_amt": 3000},
    ]
    res = ad_daily_ingest.ingest_ad_daily(db, date(2026, 7, 5), date(2026, 7, 5), rows=rows)
    # total_conv(매출)은 구매만 — 장바구니 매출(5000+3000)이 섞이면 안 됨
    assert res["total_conv_amt"] == 15900
    row = db.query(NaverAdDaily).one()
    assert row.cart_direct_cnt == 2 and row.cart_indirect_cnt == 1
    assert row.cart_direct_amt == 5000 and row.cart_indirect_amt == 3000
    assert row.conv_direct_amt == 15900  # 구매 매출 불변


def test_ingest_backward_safe_when_rows_lack_cart(db):
    """cart_* 키 없는 구버전 rows도 이전 동작 그대로(cart_*=0 적재, total_conv 불변)."""
    rows = [
        {"ad_date": "2026-07-05", "campaign_id": "cmp-01", "campaign_type": "WEB_SITE",
         "adgroup_id": "grp-1", "keyword_id": "nkw-A", "imp": 15, "clk": 2, "cost": 150, "rank_sum": 40,
         "conv_direct_cnt": 1, "conv_indirect_cnt": 0, "conv_direct_amt": 15900, "conv_indirect_amt": 0},
    ]
    res = ad_daily_ingest.ingest_ad_daily(db, date(2026, 7, 5), date(2026, 7, 5), rows=rows)
    assert res["total_conv_amt"] == 15900
    row = db.query(NaverAdDaily).one()
    assert row.cart_direct_cnt == 0 and row.cart_indirect_amt == 0
