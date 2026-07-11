# test_naver_ad_pipeline.py — 네이버 SA 광고 최적화 트랙 P0 파이프라인 단위 테스트
# 커버: fetcher 파싱(AD/AD_CONVERSION grain 집계·직간접 분리·"-"정규화), report_collector 병합,
#   bep_calculator 산식, ad_daily_ingest 멱등, hourly_snapshot 멱등.
# 컬럼 인덱스 근거: docs/references/21_naver_sa_stat_report_recon.md
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    Channel, NaverAdDaily, NaverHourlySnapshot, NaverProductBep,
    NaverSettlementDaily, Order, ProductChannelMapping, ProductMaster,
)
from app.services.naver_ad import ad_daily_ingest, bep_calculator, hourly_snapshot, report_collector
from app.services import naver_sa_ad_fetcher as fetcher
from app.utils.kst import kst_now, kst_today


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


# ── AD 행 헬퍼: 14컬럼 (0일자 2캠 3그룹 4키워드 8기기 9노출 10클릭 11비용 12순위합) ──
def _ad_row(date_s, camp, grp, kw, dev, imp, clk, cost, rank_sum):
    return [date_s, "1313769", camp, grp, kw, "nad-x", "bsn-x", "27758", dev,
            str(imp), str(clk), str(cost), str(rank_sum), "0"]


# ── AD_CONVERSION 행: 13컬럼 (9직간접 10액션 11전환수 12전환매출) ──
def _conv_row(date_s, camp, grp, kw, dir_indir, action, cnt, val):
    return [date_s, "1313769", camp, grp, kw, "nad-x", "bsn-x", "27758", "M",
            dir_indir, action, str(cnt), str(val)]


def test_fetch_ad_performance_aggregates_device_and_ad(monkeypatch):
    """소재·기기 롤업 + '-'→'' 정규화 + rank_sum 합산."""
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "x")
    monkeypatch.setattr(fetcher, "ensure_reports_built", lambda *a, **k: None)
    monkeypatch.setattr(fetcher, "list_ad_reports",
                        lambda a, b: [{"date": "2026-07-05", "downloadUrl": "u", "reportJobId": "j"}])
    rows = [
        _ad_row("20260705", "cmp-01", "grp-1", "nkw-A", "M", 10, 1, 100, 30),
        _ad_row("20260705", "cmp-01", "grp-1", "nkw-A", "P", 5, 1, 50, 10),   # 같은 키워드 다른 기기 → 합산
        _ad_row("20260705", "cmp-02", "grp-2", "-", "M", 20, 3, 200, 60),      # 쇼핑 그룹 단위
    ]
    monkeypatch.setattr(fetcher, "_download_tsv", lambda url: rows)

    out = fetcher.fetch_ad_performance_daily(date(2026, 7, 5), date(2026, 7, 5))
    by_kw = {(r["campaign_id"], r["keyword_id"]): r for r in out}
    assert len(out) == 2
    kwa = by_kw[("cmp-01", "nkw-A")]
    assert (kwa["imp"], kwa["clk"], kwa["cost"], kwa["rank_sum"]) == (15, 2, 150, 40)
    shop = by_kw[("cmp-02", "")]  # "-" → ""
    assert (shop["imp"], shop["clk"], shop["cost"]) == (20, 3, 200)


def test_fetch_conversion_splits_direct_indirect_and_excludes_cart(monkeypatch):
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "x")
    monkeypatch.setattr(fetcher, "ensure_reports_built", lambda *a, **k: None)
    monkeypatch.setattr(fetcher, "_list_reports_by_type",
                        lambda tp, a, b: [{"date": "2026-07-05", "downloadUrl": "u"}])
    rows = [
        _conv_row("20260705", "cmp-01", "grp-1", "nkw-A", "1", "purchase", 1, 15900),   # 직접
        _conv_row("20260705", "cmp-01", "grp-1", "nkw-A", "2", "purchase", 1, 10000),   # 간접
        _conv_row("20260705", "cmp-01", "grp-1", "nkw-A", "1", "add_to_cart", 1, 5000), # 제외
    ]
    monkeypatch.setattr(fetcher, "_download_tsv", lambda url: rows)

    out = fetcher.fetch_conversion_daily(date(2026, 7, 5), date(2026, 7, 5))
    assert len(out) == 1
    r = out[0]
    assert r["conv_direct_cnt"] == 1 and r["conv_direct_amt"] == 15900
    assert r["conv_indirect_cnt"] == 1 and r["conv_indirect_amt"] == 10000  # 장바구니 5000 제외


def test_report_collector_merges_and_creates_conv_only_row():
    ad_rows = [{"date": "2026-07-05", "campaign_id": "cmp-01", "adgroup_id": "grp-1",
                "keyword_id": "nkw-A", "imp": 15, "clk": 2, "cost": 150, "rank_sum": 40}]
    conv_rows = [
        {"date": "2026-07-05", "campaign_id": "cmp-01", "adgroup_id": "grp-1", "keyword_id": "nkw-A",
         "conv_direct_cnt": 1, "conv_indirect_cnt": 0, "conv_direct_amt": 15900, "conv_indirect_amt": 0},
        # 성과행 없는 간접전환(다른 키워드) → 0성과 행 생성돼야 보존
        {"date": "2026-07-05", "campaign_id": "cmp-01", "adgroup_id": "grp-9", "keyword_id": "nkw-Z",
         "conv_direct_cnt": 0, "conv_indirect_cnt": 2, "conv_direct_amt": 0, "conv_indirect_amt": 5000},
    ]
    rows = report_collector.collect_daily_rows(
        date(2026, 7, 5), date(2026, 7, 5),
        campaign_types={"cmp-01": "WEB_SITE"}, ad_rows=ad_rows, conv_rows=conv_rows,
    )
    by = {(r["campaign_id"], r["adgroup_id"], r["keyword_id"]): r for r in rows}
    assert len(rows) == 2
    merged = by[("cmp-01", "grp-1", "nkw-A")]
    assert merged["cost"] == 150 and merged["conv_direct_amt"] == 15900
    assert merged["campaign_type"] == "WEB_SITE"
    conv_only = by[("cmp-01", "grp-9", "nkw-Z")]
    assert conv_only["imp"] == 0 and conv_only["conv_indirect_amt"] == 5000


def _seed_bep_fixtures(db):
    db.add(Channel(id=6, name="네이버 스마트스토어", code="NAVER", platform="naver", commission_rate=Decimal("5.5")))
    db.add(ProductMaster(id=1, internal_sku="s1", product_name="필름A", cost_price=Decimal("5000")))
    db.add(ProductMaster(id=2, internal_sku="s2", product_name="원가없음", cost_price=Decimal("0")))
    db.add(ProductChannelMapping(product_id=1, channel_id=6, channel_product_id="100",
                                 selling_price=Decimal("0"), is_active=True))
    db.add(ProductChannelMapping(product_id=2, channel_id=6, channel_product_id="200",
                                 selling_price=Decimal("0"), is_active=True))
    # 단가 소스: 라인총액 15900×1 + 31800×2 → 단가(=selling_price/qty) median 15900
    for i in range(5):
        db.add(Order(channel_id=6, order_number=f"o{i}", platform_product_id="100",
                     selling_price=Decimal("15900"), quantity=1, order_date=kst_now()))
    db.add(Order(channel_id=6, order_number="oq2", platform_product_id="100",
                 selling_price=Decimal("31800"), quantity=2, order_date=kst_now()))  # 단가 15900
    # 정산: 실효율 = 1304731/(29958779+1304731) = 0.04173
    db.add(NaverSettlementDaily(settle_expect_date="2026-07-01", settle_amount=Decimal("29958779"),
                                pay_settle_amount=Decimal("31320810"), commission_amount=Decimal("-1304731")))
    db.commit()


def test_effective_commission_rate_from_settlement(db):
    _seed_bep_fixtures(db)
    rate = bep_calculator.effective_commission_rate(db)
    assert abs(float(rate) - 0.04173) < 0.0005


def test_calculate_bep_formula_and_has_cost(db):
    _seed_bep_fixtures(db)
    res = bep_calculator.calculate_bep(db, aggressiveness="standard")
    assert res["rows"] == 2 and res["with_bep"] == 1  # 원가없음 상품은 bep 미산출
    rows = {r.channel_product_id: r for r in db.query(NaverProductBep).all()}
    p1 = rows["100"]
    assert p1.has_cost is True
    assert float(p1.selling_price) == 15900 and float(p1.cost_price) == 5000
    # contribution = (15900 - 15900*0.04173 - 5000)/1.1 ≈ 9306, bep = 15900/9306 ≈ 1.708
    assert 1.69 < float(p1.bep_roas) < 1.72
    assert abs(float(p1.target_roas) - float(p1.bep_roas) * 1.15) < 0.01
    p2 = rows["200"]
    assert p2.has_cost is False and p2.bep_roas is None


def test_calculate_bep_snapshot_replace_idempotent(db):
    _seed_bep_fixtures(db)
    bep_calculator.calculate_bep(db)
    bep_calculator.calculate_bep(db)  # 재실행해도 중복 없음(snapshot 교체)
    assert db.query(NaverProductBep).count() == 2


def test_calculate_bep_dedupes_duplicate_channel_product_id(db):
    """같은 channel_product_id에 중복 매핑이 있어도 cpid당 1행(원가 있는 쪽 우선). UNIQUE 위반 방지."""
    db.add(Channel(id=6, name="네이버", code="NAVER", platform="naver", commission_rate=Decimal("5.5")))
    db.add(ProductMaster(id=1, internal_sku="s1", product_name="원가없음", cost_price=Decimal("0")))
    db.add(ProductMaster(id=2, internal_sku="s2", product_name="원가있음", cost_price=Decimal("5000")))
    # 같은 cpid "500"에 두 매핑 (원가없는 것 먼저, 원가있는 것 나중)
    db.add(ProductChannelMapping(product_id=1, channel_id=6, channel_product_id="500",
                                 selling_price=Decimal("0"), is_active=True))
    db.add(ProductChannelMapping(product_id=2, channel_id=6, channel_product_id="500",
                                 selling_price=Decimal("0"), is_active=True))
    db.add(Order(channel_id=6, order_number="o1", platform_product_id="500",
                 selling_price=Decimal("15900"), quantity=1, order_date=kst_now()))
    db.add(NaverSettlementDaily(settle_expect_date="2026-07-01", settle_amount=Decimal("100"),
                                pay_settle_amount=Decimal("105"), commission_amount=Decimal("-5")))
    db.commit()
    res = bep_calculator.calculate_bep(db)
    assert res["rows"] == 1  # cpid 1개로 dedupe
    row = db.query(NaverProductBep).one()
    assert row.channel_product_id == "500"
    assert row.has_cost is True and float(row.cost_price) == 5000  # 원가 있는 매핑 선택


def test_calculate_bep_dedup_tiebreak_deterministic_when_both_have_cost(db):
    """양쪽 매핑 모두 원가가 있어도(라이브 실측: 네이버 옵션 1개=기기 variant SKU 여러개,
    원가 항상 동일) product_id 최솟값으로 결정적 선택 — 재실행해도 같은 SKU 유지."""
    db.add(Channel(id=6, name="네이버", code="NAVER", platform="naver", commission_rate=Decimal("5.5")))
    db.add(ProductMaster(id=10, internal_sku="s10", product_name="기기A", cost_price=Decimal("4353")))
    db.add(ProductMaster(id=11, internal_sku="s11", product_name="기기B", cost_price=Decimal("4353")))
    db.add(ProductChannelMapping(product_id=11, channel_id=6, channel_product_id="600",
                                 selling_price=Decimal("0"), is_active=True))
    db.add(ProductChannelMapping(product_id=10, channel_id=6, channel_product_id="600",
                                 selling_price=Decimal("0"), is_active=True))
    db.add(Order(channel_id=6, order_number="o2", platform_product_id="600",
                 selling_price=Decimal("15900"), quantity=1, order_date=kst_now()))
    db.add(NaverSettlementDaily(settle_expect_date="2026-07-01", settle_amount=Decimal("100"),
                                pay_settle_amount=Decimal("105"), commission_amount=Decimal("-5")))
    db.commit()
    res1 = bep_calculator.calculate_bep(db)
    row1 = db.query(NaverProductBep).one()
    res2 = bep_calculator.calculate_bep(db)  # 재실행
    row2 = db.query(NaverProductBep).one()
    assert res1["rows"] == 1 and res2["rows"] == 1
    assert row1.product_master_id == row2.product_master_id == 10  # 최솟값 product_id로 고정


def test_ingest_ad_daily_snapshot_replace(db):
    rows = [
        {"ad_date": "2026-07-05", "campaign_id": "cmp-01", "campaign_type": "WEB_SITE",
         "adgroup_id": "grp-1", "keyword_id": "nkw-A", "imp": 15, "clk": 2, "cost": 150, "rank_sum": 40,
         "conv_direct_cnt": 1, "conv_indirect_cnt": 0, "conv_direct_amt": 15900, "conv_indirect_amt": 0},
        {"ad_date": "2026-07-05", "campaign_id": "cmp-02", "campaign_type": "SHOPPING",
         "adgroup_id": "grp-2", "keyword_id": "", "imp": 20, "clk": 3, "cost": 200, "rank_sum": 60,
         "conv_direct_cnt": 0, "conv_indirect_cnt": 0, "conv_direct_amt": 0, "conv_indirect_amt": 0},
    ]
    r1 = ad_daily_ingest.ingest_ad_daily(db, date(2026, 7, 5), date(2026, 7, 5), rows=rows)
    assert r1["rows"] == 2 and r1["total_cost"] == 350
    assert db.query(NaverAdDaily).count() == 2
    # 같은 날짜 재적재(1행) → 이전 2행 교체
    ad_daily_ingest.ingest_ad_daily(db, date(2026, 7, 5), date(2026, 7, 5), rows=rows[:1])
    assert db.query(NaverAdDaily).count() == 1


def test_hourly_snapshot_idempotent_same_hour(db):
    campaigns = [{"campaign_id": "c1", "daily_budget": 1000, "campaign_type": "SHOPPING"}]
    stats = [{"campaign_id": "c1", "cost": 500, "clk": 2, "imp": 100, "conv_cnt": 0, "conv_amt": 0}]
    hourly_snapshot.snapshot_hourly(db, campaigns=campaigns, stats=stats)
    hourly_snapshot.snapshot_hourly(db, campaigns=campaigns, stats=stats)  # 같은 시각 재실행 → 교체
    rows = db.query(NaverHourlySnapshot).all()
    assert len(rows) == 1
    assert rows[0].cost == 500 and rows[0].daily_budget == 1000
