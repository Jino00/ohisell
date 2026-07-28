# test_scheduler_naver_orders_hourly.py — sync_naver_orders_hourly_job 채널 범위·창·RG 배제 계약
# (2026-07-28 실사고: 근시간 동기화 유일 트리거가 프런트 마운트뿐이라 7시간28분 시도 0건.
# 이 잡은 그 공백을 네이버 스마트스토어 한정으로 메운다 — 쿠팡/cafe24 확대·RG 포함은 스코프 밖.)
from __future__ import annotations

import inspect

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Channel
from app.services import scheduler_service, sync_service


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _mixed_channels(db):
    """쿠팡·cafe24·네이버가 섞인 상태 — 네이버만 골라내는지가 이 스프린트의 핵심 계약."""
    db.add_all([
        Channel(id=1, name="wing1", code="COUPANG_W1", platform="coupang",
                api_type="hmac", api_config_key=None),
        Channel(id=2, name="cafe24_1", code="CAFE24_1", platform="cafe24",
                api_type="oauth2_code", api_config_key=None),
        Channel(id=6, name="naver", code="NAVER", platform="naver",
                api_type="oauth2_bcrypt", api_config_key=None),
    ])
    db.commit()


# ── ① 채널 범위 계약: 네이버 채널 id로만 1회 호출, 다른 채널은 0회 ──────────────────


def test_only_naver_channel_is_synced(db, monkeypatch):
    _mixed_channels(db)
    calls = []

    def _fake_sync_channel_orders(db_, channel_id, date_from=None, date_to=None):
        calls.append((channel_id, date_from, date_to))
        return {"status": "success", "new_orders": 0, "reconciled_cancelled": 0}

    monkeypatch.setattr(sync_service, "sync_channel_orders", _fake_sync_channel_orders)
    monkeypatch.setattr(scheduler_service, "_get_own_db_session", lambda: db)
    scheduler_service.sync_naver_orders_hourly_job()

    assert len(calls) == 1, f"네이버 채널만 1회 호출돼야 하는데 {calls}"
    assert calls[0][0] == 6, "네이버 채널(id=6)이 호출돼야 한다"


def test_coupang_and_cafe24_are_never_synced_by_this_job(db, monkeypatch):
    """역검증용 계약: 이 잡이 platform 필터를 빼먹으면(=전체 채널 순회) 실패해야 한다."""
    _mixed_channels(db)
    called_ids = []

    def _fake_sync_channel_orders(db_, channel_id, date_from=None, date_to=None):
        called_ids.append(channel_id)
        return {"status": "success", "new_orders": 0, "reconciled_cancelled": 0}

    monkeypatch.setattr(sync_service, "sync_channel_orders", _fake_sync_channel_orders)
    monkeypatch.setattr(scheduler_service, "_get_own_db_session", lambda: db)
    scheduler_service.sync_naver_orders_hourly_job()

    assert 1 not in called_ids, "쿠팡 채널(id=1)은 호출되면 안 된다"
    assert 2 not in called_ids, "cafe24 채널(id=2)은 호출되면 안 된다"


def test_no_naver_channel_returns_quietly(db, monkeypatch):
    """네이버 채널이 0건이면 조용히 return — 예외 없이 끝난다."""
    db.add(Channel(id=1, name="wing1", code="COUPANG_W1", platform="coupang",
                   api_type="hmac", api_config_key=None))
    db.commit()
    calls = []
    monkeypatch.setattr(
        sync_service, "sync_channel_orders",
        lambda *a, **kw: calls.append(a) or {"status": "success"},
    )
    monkeypatch.setattr(scheduler_service, "_get_own_db_session", lambda: db)
    scheduler_service.sync_naver_orders_hourly_job()  # raise 없이 완주해야 함
    assert calls == []


# ── ② 7일 창 계약: date_from=None, date_to=None으로 호출(30일 창을 매시간 돌리면 안 됨) ──


def test_calls_with_none_dates_so_sync_service_default_7day_window_applies(db, monkeypatch):
    _mixed_channels(db)
    captured = {}

    def _fake_sync_channel_orders(db_, channel_id, date_from=None, date_to=None):
        captured["date_from"] = date_from
        captured["date_to"] = date_to
        return {"status": "success"}

    monkeypatch.setattr(sync_service, "sync_channel_orders", _fake_sync_channel_orders)
    monkeypatch.setattr(scheduler_service, "_get_own_db_session", lambda: db)
    scheduler_service.sync_naver_orders_hourly_job()

    assert captured["date_from"] is None, "date_from을 None으로 넘겨야 sync_service 기본 7일 창이 적용된다"
    assert captured["date_to"] is None, "date_to를 None으로 넘겨야 sync_service 기본 7일 창이 적용된다"


# ── ③ RG 미호출 계약 ──────────────────────────────────────────────────────────


def test_rg_order_sync_is_never_called():
    """sync_all_rg_orders를 호출/임포트하지 않는다(정적 계약). 설계 근거를 남기는 docstring
    관례상 함수명이 설명문에 언급될 수 있으므로, 실제 호출("(...)")과 import 문만 검사한다."""
    src = inspect.getsource(scheduler_service.sync_naver_orders_hourly_job)
    assert "sync_all_rg_orders(" not in src
    assert "import sync_all_rg_orders" not in src
    assert "rg_order_sync import" not in src


# ── ④ 등록 계약: 크론·잡 매핑에 등록, _CATCHUP_ORDER에는 없음 ──────────────────────


def test_default_cron_is_minute45_every_hour():
    src = inspect.getsource(scheduler_service._ensure_default_states)
    assert '("sync_naver_orders_hourly", "45 * * * *")' in src


def test_wired_in_job_func_for_mapping():
    assert scheduler_service.job_func_for("sync_naver_orders_hourly") \
        is scheduler_service.sync_naver_orders_hourly_job


def test_not_registered_in_catchup_order():
    # 시간성이 소멸하는 근시간 보정 레인 — catch-up 제외(놓친 회차는 폐기, 다음 정시가 재기회)
    assert "sync_naver_orders_hourly" not in scheduler_service._CATCHUP_ORDER


def test_has_own_5min_misfire_grace_not_global_1hour():
    assert scheduler_service._NAVER_ORDERS_HOURLY_MISFIRE_GRACE == 300
    assert scheduler_service.job_kwargs_for("sync_naver_orders_hourly") == {
        "misfire_grace_time": 300
    }


def test_job_function_has_self_contained_session_pattern():
    src = inspect.getsource(scheduler_service.sync_naver_orders_hourly_job)
    assert "_get_own_db_session" in src
    assert "db.close()" in src
