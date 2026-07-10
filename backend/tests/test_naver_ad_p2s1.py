# test_naver_ad_p2s1.py — 네이버 SA 광고 최적화 트랙 P2-S1(데이터 기반) 단위 테스트
# 커버: entity_sync(수집·upsert·stale 표시), 검색어 리포트 컬럼 파싱(fetcher),
#   search_term_ingest(snapshot 교체), campaign_backfill(sentinel grain·멱등),
#   campaign_target_resolver(override>account_default 우선순위), keywordstool 파싱.
# 컬럼 인덱스 근거: docs/references/22_naver_sa_p2s1_recon.md (실측 확정)
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdDaily, NaverCampaignSettings, NaverChangeLog, NaverEntity, NaverProductBep, NaverSearchTermDaily, Order,
)
from app.services.naver_ad import (
    campaign_backfill, campaign_target_resolver, entity_sync, keyword_volume_sync, search_term_ingest,
)
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


# ── entity_sync ──
def test_collect_entities_skips_keywords_for_non_web_site():
    campaigns = [
        {"campaign_id": "cmp-web", "campaign_type": "WEB_SITE", "name": "파워링크", "status": "ELIGIBLE"},
        {"campaign_id": "cmp-shop", "campaign_type": "SHOPPING", "name": "쇼핑", "status": "ELIGIBLE"},
    ]
    adgroups = {
        "cmp-web": [{"adgroup_id": "grp-web", "name": "그룹W", "status": "ELIGIBLE", "user_lock": False, "bid_amt": 500}],
        "cmp-shop": [{"adgroup_id": "grp-shop", "name": "그룹S", "status": "ELIGIBLE", "user_lock": False, "bid_amt": 500}],
    }
    keywords = {
        "grp-web": [{"keyword_id": "nkw-1", "keyword": "필름", "status": "ELIGIBLE", "user_lock": False, "bid_amt": 700}],
        "grp-shop": [{"keyword_id": "nkw-shop-1", "keyword": "쇼핑키워드", "status": "ELIGIBLE", "user_lock": False, "bid_amt": 70}],
    }
    rows = entity_sync.collect_entities(campaigns=campaigns, adgroups_by_campaign=adgroups, keywords_by_adgroup=keywords)

    types = {r["entity_type"] for r in rows}
    assert types == {"campaign", "adgroup", "keyword"}
    kw_rows = [r for r in rows if r["entity_type"] == "keyword"]
    assert len(kw_rows) == 1  # SHOPPING 그룹의 키워드는 제외
    assert kw_rows[0]["entity_id"] == "nkw-1"


def test_sync_entities_preserves_volume_and_marks_stale_deleted(db):
    rows_v1 = [
        {"entity_type": "campaign", "entity_id": "cmp-1", "parent_id": "", "campaign_id": "cmp-1",
         "campaign_type": "WEB_SITE", "name": "캠페인1", "status": "on", "bid_amt": None},
        {"entity_type": "keyword", "entity_id": "nkw-1", "parent_id": "grp-1", "campaign_id": "cmp-1",
         "campaign_type": "WEB_SITE", "name": "필름", "status": "on", "bid_amt": 700},
    ]
    entity_sync.sync_entities(db, rows=rows_v1)

    kw = db.query(NaverEntity).filter(NaverEntity.entity_id == "nkw-1").one()
    kw.monthly_volume = 1234
    db.commit()

    # 2차 동기화: campaign만 남고 keyword는 사라짐(계정에서 삭제됨을 시뮬레이션)
    rows_v2 = [rows_v1[0]]
    result = entity_sync.sync_entities(db, rows=rows_v2)

    assert result["stale_marked_deleted"] == 1
    kw2 = db.query(NaverEntity).filter(NaverEntity.entity_id == "nkw-1").one()
    assert kw2.status == "deleted"
    assert kw2.monthly_volume == 1234  # 볼륨 데이터는 보존


def test_sync_entities_logs_external_status_change_to_change_log(db):
    """[codex P2, D-NAO-40] entity_sync가 라이브에서 status 변경을 감지하면 change_log에
    action='external_status_change', proposal_id=None 행을 남긴다.
    이렇게 하면 resume_candidates의 기존 'proposal_id is None → skip' 로직이
    외부 재정지를 자동 차단한다."""
    rows_v1 = [
        {"entity_type": "keyword", "entity_id": "nkw-1", "parent_id": "grp-1",
         "campaign_id": "cmp-1", "campaign_type": "WEB_SITE", "name": "필름",
         "status": "on", "bid_amt": 700},
    ]
    entity_sync.sync_entities(db, rows=rows_v1)

    # 2차 동기화: 외부(MOP/사람)가 keyword를 off로 변경한 상태
    rows_v2 = [
        {"entity_type": "keyword", "entity_id": "nkw-1", "parent_id": "grp-1",
         "campaign_id": "cmp-1", "campaign_type": "WEB_SITE", "name": "필름",
         "status": "off", "bid_amt": 700},
    ]
    entity_sync.sync_entities(db, rows=rows_v2)

    logs = db.query(NaverChangeLog).filter(
        NaverChangeLog.entity_id == "nkw-1",
        NaverChangeLog.action == "external_status_change",
    ).all()
    assert len(logs) == 1
    assert logs[0].proposal_id is None
    assert logs[0].entity_type == "keyword"
    assert logs[0].campaign_id == "cmp-1"
    assert logs[0].dry_run is False
    import json
    after = json.loads(logs[0].after_value)
    assert after == {"userLock": True}  # off = userLock:True
    before = json.loads(logs[0].before_value)
    assert before == {"userLock": False}  # on = userLock:False


def test_sync_entities_logs_external_resume_to_change_log(db):
    """외부 재개(off→on)도 change_log에 남긴다 — 완전한 이력 추적."""
    rows_v1 = [
        {"entity_type": "keyword", "entity_id": "nkw-1", "parent_id": "grp-1",
         "campaign_id": "cmp-1", "campaign_type": "WEB_SITE", "name": "필름",
         "status": "off", "bid_amt": 700},
    ]
    entity_sync.sync_entities(db, rows=rows_v1)

    rows_v2 = [
        {"entity_type": "keyword", "entity_id": "nkw-1", "parent_id": "grp-1",
         "campaign_id": "cmp-1", "campaign_type": "WEB_SITE", "name": "필름",
         "status": "on", "bid_amt": 700},
    ]
    entity_sync.sync_entities(db, rows=rows_v2)

    logs = db.query(NaverChangeLog).filter(
        NaverChangeLog.entity_id == "nkw-1",
        NaverChangeLog.action == "external_status_change",
    ).all()
    assert len(logs) == 1
    import json
    after = json.loads(logs[0].after_value)
    assert after == {"userLock": False}


def test_sync_entities_no_log_when_status_unchanged(db):
    """status 동일하면 change_log에 기록하지 않는다."""
    rows = [
        {"entity_type": "keyword", "entity_id": "nkw-1", "parent_id": "grp-1",
         "campaign_id": "cmp-1", "campaign_type": "WEB_SITE", "name": "필름",
         "status": "on", "bid_amt": 700},
    ]
    entity_sync.sync_entities(db, rows=rows)
    entity_sync.sync_entities(db, rows=rows)  # 같은 status

    logs = db.query(NaverChangeLog).filter(
        NaverChangeLog.entity_id == "nkw-1",
        NaverChangeLog.action == "external_status_change",
    ).all()
    assert logs == []


def test_sync_entities_skips_log_for_our_own_change(db):
    """우리가 실행해서 바뀐 것(change_log에 이미 최근 set_user_lock 성공 기록이 있는 경우)은
    external_status_change를 남기지 않는다 — 이중 기록 방지."""
    import json
    from datetime import datetime as dt
    rows_v1 = [
        {"entity_type": "keyword", "entity_id": "nkw-1", "parent_id": "grp-1",
         "campaign_id": "cmp-1", "campaign_type": "WEB_SITE", "name": "필름",
         "status": "on", "bid_amt": 700},
    ]
    entity_sync.sync_entities(db, rows=rows_v1)

    # 우리가 정지 실행한 기록(proposal_id 있음, after_value={"userLock": true})
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-1", campaign_id="cmp-1",
        action="set_user_lock", proposal_id=42, dry_run=False,
        after_value=json.dumps({"userLock": True}),
        changed_at=dt(2026, 7, 15, 8, 0, 0),
    ))
    db.commit()

    # 동기화에서 off 확인 — 우리가 직접 정지한 결과이므로 external log 안 남김
    rows_v2 = [
        {"entity_type": "keyword", "entity_id": "nkw-1", "parent_id": "grp-1",
         "campaign_id": "cmp-1", "campaign_type": "WEB_SITE", "name": "필름",
         "status": "off", "bid_amt": 700},
    ]
    entity_sync.sync_entities(db, rows=rows_v2)

    ext_logs = db.query(NaverChangeLog).filter(
        NaverChangeLog.entity_id == "nkw-1",
        NaverChangeLog.action == "external_status_change",
    ).all()
    assert ext_logs == []


# ── 검색어 리포트 컬럼 파싱 (SHOPPINGKEYWORD_DETAIL/EXPKEYWORD 공통 16열) ──
def _st_row(date_s, camp, grp, term, imp, clk, cost, rank_sum):
    return [date_s, "1313769", camp, grp, term, "nad-x", "bsn-x", "03", "02",
            "8753", "M", str(imp), str(clk), str(cost), str(rank_sum), "0"]


def test_fetch_search_term_daily_aggregates(monkeypatch):
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "x")
    monkeypatch.setattr(fetcher, "_list_reports_by_type",
                        lambda tp, a, b: [{"date": "2026-07-05", "downloadUrl": "u"}])
    rows = [
        _st_row("20260705", "cmp-01", "grp-1", "판매갤럭시S25", 663, 19, 33243, 2378),
        _st_row("20260705", "cmp-01", "grp-1", "판매갤럭시S25", 10, 1, 500, 30),  # 같은 검색어 다른 소재 → 합산
    ]
    monkeypatch.setattr(fetcher, "_download_tsv", lambda url: rows)

    out = fetcher.fetch_search_term_daily("SHOPPINGKEYWORD_DETAIL", date(2026, 7, 5), date(2026, 7, 5))
    assert len(out) == 1
    r = out[0]
    assert (r["imp"], r["clk"], r["cost"], r["rank_sum"]) == (673, 20, 33743, 2408)


def test_ingest_search_term_daily_snapshot_replace(db, monkeypatch):
    def fake_fetch(report_tp, date_from, date_to):
        if report_tp == "SHOPPINGKEYWORD_DETAIL":
            return [{"date": "2026-07-05", "campaign_id": "cmp-01", "adgroup_id": "grp-1",
                      "search_term": "검색어A", "imp": 10, "clk": 1, "cost": 100, "rank_sum": 3}]
        return []

    monkeypatch.setattr(search_term_ingest, "fetch_search_term_daily", fake_fetch)
    monkeypatch.setattr(search_term_ingest, "request_missing_expkeyword_reports", lambda a, b: [])

    result = search_term_ingest.ingest_search_term_daily(db, date(2026, 7, 5), date(2026, 7, 5))
    assert result["shopping_rows"] == 1
    rows = db.query(NaverSearchTermDaily).filter(NaverSearchTermDaily.source == "shopping").all()
    assert len(rows) == 1 and rows[0].cost == 100

    # 재실행(같은 날짜) — 교체되어 중복 안 남음
    search_term_ingest.ingest_search_term_daily(db, date(2026, 7, 5), date(2026, 7, 5))
    rows2 = db.query(NaverSearchTermDaily).filter(NaverSearchTermDaily.source == "shopping").all()
    assert len(rows2) == 1


# ── keywordstool 파싱 ──
def test_fetch_keyword_volumes_parses_low_volume_sentinel(monkeypatch):
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "x")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"keywordList": [
                {"relKeyword": "필름A", "monthlyPcQcCnt": "120", "monthlyMobileQcCnt": "<10", "compIdx": "high"},
                {"relKeyword": "무관한연관어", "monthlyPcQcCnt": "9999", "monthlyMobileQcCnt": "9999", "compIdx": "low"},
            ]}
    monkeypatch.setattr(fetcher, "_get", lambda path, params=None: FakeResp())

    out = fetcher.fetch_keyword_volumes(["필름A"])
    assert out == {"필름A": {"monthly_volume": 125, "competition": "high"}}  # 120+5(<10 sentinel)
    assert "무관한연관어" not in out  # 요청하지 않은 연관 키워드는 제외


# ── campaign_backfill ──
def test_backfill_campaign_daily_uses_sentinel_adgroup(db, monkeypatch):
    def fake_backfill(campaign_id, date_from, date_to):
        return [{"date": "2026-01-05", "campaign_id": campaign_id, "imp": 100, "clk": 5,
                  "cost": 5000, "conv_cnt": 1, "conv_amt": 20000}]

    monkeypatch.setattr(campaign_backfill, "fetch_campaign_daily_backfill", fake_backfill)
    campaigns = [{"campaign_id": "cmp-01", "campaign_type": "WEB_SITE"}]

    result = campaign_backfill.backfill_campaign_daily(db, date(2026, 1, 5), date(2026, 1, 5), campaigns=campaigns)
    assert result["rows"] == 1
    row = db.query(NaverAdDaily).filter(NaverAdDaily.campaign_id == "cmp-01").one()
    assert row.adgroup_id == campaign_backfill.BACKFILL_SENTINEL_ADGROUP
    assert row.cost == 5000 and row.conv_indirect_amt == 20000 and row.conv_direct_amt == 0

    # 재실행 — 멱등(같은 sentinel 행만 교체, 중복 안 남음)
    campaign_backfill.backfill_campaign_daily(db, date(2026, 1, 5), date(2026, 1, 5), campaigns=campaigns)
    rows = db.query(NaverAdDaily).filter(NaverAdDaily.campaign_id == "cmp-01").all()
    assert len(rows) == 1


# ── campaign_target_resolver ──
def test_resolve_target_roas_prefers_override(db):
    db.add(NaverCampaignSettings(campaign_id="cmp-01", optimizer="ours", target_roas_override=Decimal("250")))
    db.commit()

    result = campaign_target_resolver.resolve_target_roas(db, "cmp-01")
    assert result == {"target_roas": Decimal("250"), "source": "override"}


def test_resolve_target_roas_falls_back_to_revenue_weighted_account_default(db):
    db.add(NaverProductBep(channel_id=6, channel_product_id="p1", has_cost=True, target_roas=Decimal("200")))
    db.add(NaverProductBep(channel_id=6, channel_product_id="p2", has_cost=True, target_roas=Decimal("400")))
    db.add(Order(channel_id=6, platform_product_id="p1", selling_price=Decimal("9000"), quantity=1,
                  order_date=date(2026, 7, 1), order_number="o1"))
    db.add(Order(channel_id=6, platform_product_id="p2", selling_price=Decimal("1000"), quantity=1,
                  order_date=date(2026, 7, 1), order_number="o2"))
    db.commit()

    result = campaign_target_resolver.resolve_target_roas(db, "cmp-no-override")
    assert result["source"] == "account_default"
    # 매출가중: (200*9000 + 400*1000) / 10000 = 220
    assert result["target_roas"] == Decimal("220")


# ── keyword_volume_sync ──
def test_sync_keyword_volumes_targets_low_click_only(db, monkeypatch):
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-low", campaign_id="cmp-1",
                        campaign_type="WEB_SITE", name="저클릭키워드", status="on"))
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-high", campaign_id="cmp-1",
                        campaign_type="WEB_SITE", name="고클릭키워드", status="on"))
    db.add(NaverAdDaily(ad_date=date(2026, 7, 5), campaign_id="cmp-1", keyword_id="nkw-high", clk=50))
    db.commit()

    monkeypatch.setattr(keyword_volume_sync, "fetch_keyword_volumes",
                        lambda names: {n: {"monthly_volume": 1000, "competition": "mid"} for n in names})

    result = keyword_volume_sync.sync_keyword_volumes(db)
    assert result["targeted"] == 1  # 고클릭 키워드는 대상 제외
    low = db.query(NaverEntity).filter(NaverEntity.entity_id == "nkw-low").one()
    assert low.monthly_volume == 1000
    high = db.query(NaverEntity).filter(NaverEntity.entity_id == "nkw-high").one()
    assert high.monthly_volume is None
