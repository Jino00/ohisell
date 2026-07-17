# test_ad_cost_cookie_status_cron.py — cookie_status의 refresh_cron_enabled 3분기.
#   ★크론이 꺼져 있으면 쿠키가 멀쩡해도 push가 끊긴다(13일 정지의 실제 원인) — 배너가
#   쿠키 red로 오진단하지 않도록 이 플래그를 노출·우선한다.
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import SchedulerState
from app.services.coupang import ad_cost_sync


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    yield s
    s.close()


def _seed_cron(db, is_enabled):
    db.add(SchedulerState(job_name="request_ad_cost_refresh",
                          cron_expression="*/5 * * * *", is_enabled=is_enabled))
    db.commit()


def test_cron_enabled_true(db):
    _seed_cron(db, True)
    assert ad_cost_sync.cookie_status(db)["refresh_cron_enabled"] is True


def test_cron_enabled_false(db):
    _seed_cron(db, False)
    assert ad_cost_sync.cookie_status(db)["refresh_cron_enabled"] is False


def test_cron_row_missing_returns_none(db):
    # request_ad_cost_refresh 행이 없으면 판정 불가 → None.
    assert ad_cost_sync.cookie_status(db)["refresh_cron_enabled"] is None
