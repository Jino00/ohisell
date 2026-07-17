# test_scheduler_toggle_router.py — PUT /toggle/{job_id} 라이브 등록(쿠팡 광고비 13일 정지 재발 방지)
#   ★존재 이유: enable해도 APScheduler 미등록 잡이면 resume이 조용히 실패해 DB만 바뀌고
#   실제로는 안 돌았다(재시작 필요). 이제 미등록·매핑된 잡은 라이브 add_job으로 살리고,
#   매핑 밖 잡은 live_registered=false로 알린다.
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.routers.scheduler as sched_router
from app.database import Base, get_db
from app.main import app
from app.models import SchedulerState


class _FakeScheduler:
    """APScheduler 대역 — add/resume/pause 호출을 기록한다."""

    def __init__(self, registered=()):
        self.running = True
        self._jobs = {jid: object() for jid in registered}
        self.added: list[str] = []
        self.resumed: list[str] = []
        self.paused: list[str] = []

    def get_job(self, job_id):
        return self._jobs.get(job_id)

    def add_job(self, func, trigger=None, id=None, replace_existing=False):
        self._jobs[id] = object()
        self.added.append(id)

    def resume_job(self, job_id):
        self.resumed.append(job_id)

    def pause_job(self, job_id):
        self.paused.append(job_id)


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    fake = _FakeScheduler()
    monkeypatch.setattr(sched_router, "scheduler", fake)
    seed = TestingSession()
    yield TestClient(app), seed, fake
    seed.close()
    app.dependency_overrides.clear()


def _seed(seed, job_name, *, is_enabled, cron="*/5 * * * *"):
    seed.add(SchedulerState(job_name=job_name, cron_expression=cron, is_enabled=is_enabled))
    seed.commit()


def test_enable_unregistered_mapped_job_live_registers(env):
    """미등록 + 매핑된 잡을 enable → add_job 호출·live_registered=True."""
    client, seed, fake = env
    _seed(seed, "request_ad_cost_refresh", is_enabled=False)

    r = client.put("/api/scheduler/toggle/request_ad_cost_refresh")
    assert r.status_code == 200
    body = r.json()
    assert body["is_enabled"] is True
    assert body["live_registered"] is True
    assert "request_ad_cost_refresh" in fake.added
    assert fake.resumed == []


def test_enable_unmapped_job_reports_not_registered(env):
    """매핑 밖 잡을 enable → add_job 미호출·live_registered=False·warning 포함(500 금지)."""
    client, seed, fake = env
    _seed(seed, "totally_unknown_job", is_enabled=False)

    r = client.put("/api/scheduler/toggle/totally_unknown_job")
    assert r.status_code == 200
    body = r.json()
    assert body["is_enabled"] is True
    assert body["live_registered"] is False
    assert "warning" in body
    assert fake.added == []


def test_enable_already_registered_job_resumes(env):
    """이미 등록된 잡을 enable → resume_job·add_job 미호출·live_registered=True."""
    client, seed, fake = env
    _seed(seed, "auto_sync_orders", is_enabled=False)
    fake._jobs["auto_sync_orders"] = object()  # 이미 등록됨

    r = client.put("/api/scheduler/toggle/auto_sync_orders")
    assert r.status_code == 200
    body = r.json()
    assert body["live_registered"] is True
    assert fake.resumed == ["auto_sync_orders"]
    assert fake.added == []


def test_disable_pauses_and_live_registered_none(env):
    """enabled 잡을 disable → pause_job·live_registered=None."""
    client, seed, fake = env
    _seed(seed, "auto_sync_orders", is_enabled=True)
    fake._jobs["auto_sync_orders"] = object()

    r = client.put("/api/scheduler/toggle/auto_sync_orders")
    assert r.status_code == 200
    body = r.json()
    assert body["is_enabled"] is False
    assert body["live_registered"] is None
    assert fake.paused == ["auto_sync_orders"]
