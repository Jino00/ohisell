# test_naver_campaign_backfill.py — campaign_backfill(SA) 단위테스트 (기존 커버리지 0건)
# codex review(F1 예측엔진 스프린트) 지적 반영 — 삭제 범위가 응답에 없던 날짜(무활동일 등
# API가 생략한 날)까지 커버하는지가 forecast_gate 정확성에 직결되어 회귀테스트로 고정한다.
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP, backfill_campaign_daily

CAMPAIGN = {"campaign_id": "cmp-1", "campaign_type": "WEB_SITE"}
D1, D2, D3 = date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)


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


def _row(d, clk=10, cost=100, conv_cnt=0, conv_amt=0):
    return {"date": d.isoformat(), "campaign_id": CAMPAIGN["campaign_id"],
            "imp": clk * 10, "clk": clk, "cost": cost, "conv_cnt": conv_cnt, "conv_amt": conv_amt}


def test_clears_stale_row_for_date_absent_from_new_response(db, monkeypatch):
    """API가 특정일(무활동 등)을 응답에서 생략해도, 이전 실행의 sentinel 행은 재백필 범위
    안이면 반드시 지워져야 한다 — 안 지우면 forecast_gate가 실제로는 없는 활동을 본다."""
    import app.services.naver_ad.campaign_backfill as cb

    monkeypatch.setattr(cb, "fetch_campaign_daily_backfill",
                         lambda cid, dfrom, dto: [_row(D1, clk=10), _row(D2, clk=20), _row(D3, clk=30)])
    backfill_campaign_daily(db, D1, D3, campaigns=[CAMPAIGN])
    assert db.query(NaverAdDaily).filter(
        NaverAdDaily.adgroup_id == BACKFILL_SENTINEL_ADGROUP, NaverAdDaily.ad_date == D2,
    ).count() == 1

    # 재실행 — 이번엔 API가 D2를 생략(예: 그날 무활동으로 리포트에서 빠짐)
    monkeypatch.setattr(cb, "fetch_campaign_daily_backfill",
                         lambda cid, dfrom, dto: [_row(D1, clk=10), _row(D3, clk=30)])
    result = backfill_campaign_daily(db, D1, D3, campaigns=[CAMPAIGN])

    stale = db.query(NaverAdDaily).filter(
        NaverAdDaily.adgroup_id == BACKFILL_SENTINEL_ADGROUP, NaverAdDaily.ad_date == D2,
    ).count()
    assert stale == 0  # D2의 예전 sentinel 행이 남아있으면 안 됨
    assert result["rows"] == 2
    assert result["dates_covered"] == 2


def test_rerun_same_range_replaces_idempotently(db, monkeypatch):
    import app.services.naver_ad.campaign_backfill as cb

    monkeypatch.setattr(cb, "fetch_campaign_daily_backfill",
                         lambda cid, dfrom, dto: [_row(D1, clk=10)])
    backfill_campaign_daily(db, D1, D1, campaigns=[CAMPAIGN])
    backfill_campaign_daily(db, D1, D1, campaigns=[CAMPAIGN])

    rows = db.query(NaverAdDaily).filter(NaverAdDaily.adgroup_id == BACKFILL_SENTINEL_ADGROUP).all()
    assert len(rows) == 1
    assert rows[0].clk == 10


def test_does_not_touch_other_campaigns_or_real_p0_rows(db, monkeypatch):
    """다른 캠페인의 sentinel 행이나 P0 실단위 행(다른 adgroup_id)은 건드리지 않는다."""
    import app.services.naver_ad.campaign_backfill as cb

    db.add(NaverAdDaily(
        ad_date=D1, campaign_id="cmp-other", campaign_type="WEB_SITE",
        adgroup_id=BACKFILL_SENTINEL_ADGROUP, keyword_id="", imp=1, clk=1, cost=1, rank_sum=0,
    ))
    db.add(NaverAdDaily(
        ad_date=D1, campaign_id=CAMPAIGN["campaign_id"], campaign_type="WEB_SITE",
        adgroup_id="grp-real", keyword_id="nkw-1", imp=1, clk=1, cost=1, rank_sum=0,
    ))
    db.commit()

    monkeypatch.setattr(cb, "fetch_campaign_daily_backfill",
                         lambda cid, dfrom, dto: [_row(D1, clk=10)])
    backfill_campaign_daily(db, D1, D1, campaigns=[CAMPAIGN])

    assert db.query(NaverAdDaily).filter(NaverAdDaily.campaign_id == "cmp-other").count() == 1
    assert db.query(NaverAdDaily).filter(NaverAdDaily.adgroup_id == "grp-real").count() == 1
