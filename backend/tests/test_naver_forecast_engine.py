# test_naver_forecast_engine.py — 예측·전문가 스프린트 F1 forecast_engine(Harness) 단위테스트
# 주의: backfill_campaign_daily는 campaigns 미지정 시 실제 네이버 API(get_campaigns_full)를
#   호출한다(네트워크) — 단위테스트에서는 항상 monkeypatch로 대체해 네트워크 의존을 없앤다.
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverEntity
from app.services.naver_ad import forecast_engine

TODAY = date(2026, 7, 8)


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


def _noop_backfill(db, date_from, date_to, *, campaigns=None):
    return {"campaigns": 0, "rows": 0, "date_range": [date_from.isoformat(), date_to.isoformat()], "dates_covered": 0, "total_cost": 0}


def test_run_daily_all_stages_ok_with_no_data(db, monkeypatch):
    monkeypatch.setattr(forecast_engine, "backfill_campaign_daily", _noop_backfill)

    result = forecast_engine.run_daily(db, today=TODAY)

    assert result["stage_status"] == {"backfill_refresh": "ok", "model_builder": "ok", "scorer": "ok"}
    assert result["errors"] == []


def test_run_daily_isolates_backfill_failure(db, monkeypatch):
    """재백필이 실패해도(예: 네트워크 오류) 기존 시계열로 나머지 단계는 계속 진행한다."""
    def _boom(db, date_from, date_to, *, campaigns=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(forecast_engine, "backfill_campaign_daily", _boom)

    result = forecast_engine.run_daily(db, today=TODAY)

    assert result["stage_status"]["backfill_refresh"] == "failed"
    assert result["stage_status"]["model_builder"] == "ok"
    assert result["stage_status"]["scorer"] == "ok"
    assert any("backfill_refresh" in e for e in result["errors"])


def test_run_daily_only_forecasts_active_status_entities(db, monkeypatch):
    monkeypatch.setattr(forecast_engine, "backfill_campaign_daily", _noop_backfill)
    db.add(NaverEntity(entity_type="campaign", entity_id="cmp-on", campaign_id="cmp-on",
                        campaign_type="WEB_SITE", name="온", status="on"))
    db.add(NaverEntity(entity_type="campaign", entity_id="cmp-off", campaign_id="cmp-off",
                        campaign_type="WEB_SITE", name="오프", status="off"))
    db.commit()

    result = forecast_engine.run_daily(db, today=TODAY)

    assert result["model_builder"]["campaigns"] == 1  # off 캠페인 제외


def test_run_daily_includes_adgroup_and_keyword_scopes(db, monkeypatch):
    """F2a: campaign뿐 아니라 status='on'인 adgroup·keyword 엔티티도 순회 대상에 포함해야 한다."""
    monkeypatch.setattr(forecast_engine, "backfill_campaign_daily", _noop_backfill)
    db.add(NaverEntity(entity_type="campaign", entity_id="cmp-1", campaign_id="cmp-1",
                        campaign_type="WEB_SITE", name="캠페인", status="on"))
    db.add(NaverEntity(entity_type="adgroup", entity_id="adg-1", campaign_id="cmp-1",
                        campaign_type="WEB_SITE", name="그룹", status="on"))
    db.add(NaverEntity(entity_type="adgroup", entity_id="adg-off", campaign_id="cmp-1",
                        campaign_type="WEB_SITE", name="꺼진그룹", status="off"))
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-1", campaign_id="cmp-1",
                        campaign_type="WEB_SITE", name="키워드", status="on"))
    db.commit()

    result = forecast_engine.run_daily(db, today=TODAY)

    assert result["model_builder"]["campaigns"] == 3  # cmp-1 + adg-1 + nkw-1(꺼진 adg-off 제외)
