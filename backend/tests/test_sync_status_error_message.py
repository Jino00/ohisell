# test_sync_status_error_message.py — /api/sync/status가 부분수집을 실어 보내는가 (D-NAO-204 합격기준 ③)
# ★적대 리뷰 변이 M7이 살아남은 자리다: `error_message=` 한 줄을 지워도 아무 테스트도 안 깨졌다.
#   합격기준에 있는데 테스트가 0건이면 그 기준은 장식이다.
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Channel, SyncLog
from app.services.sync_service import PARTIAL_SYNC_MARKER

NOW = datetime(2026, 8, 19, 10, 30)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    s.add(Channel(id=6, name="네이버 스마트스토어", code="NAVER",
                  platform="naver", api_type="oauth2_bcrypt", api_config_key="naver"))
    s.commit()
    yield s
    s.close()


def _log(db, msg, status="success"):
    db.add(SyncLog(channel_id=6, sync_type="orders", status=status, records_synced=336,
                   error_message=msg, started_at=NOW, completed_at=NOW))
    db.commit()


def _get(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        r = TestClient(app).get("/api/sync/status")
        assert r.status_code == 200
        return r.json()
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_partial_sync_message_reaches_api(db):
    _log(db, f"{PARTIAL_SYNC_MARKER} 변경상태 스윕 미완주 1일: 2026-08-18")
    row = next(r for r in _get(db) if r["channel_id"] == 6)
    assert row["status"] == "success"          # 초록인데
    assert row["error_message"], "…덜 들어왔다는 사실이 API에 실려야 화면이 읽는다"
    assert "2026-08-18" in row["error_message"], "어느 날인지가 재수집 좌표다"


def test_clean_success_has_no_message(db):
    _log(db, None)
    assert next(r for r in _get(db) if r["channel_id"] == 6)["error_message"] is None


def test_raw_exception_is_not_leaked(db):
    """★원시 예외는 API로 내보내지 않는다 — 헬스 라우터의 «sanitized 한 줄» 규약과 같은 결."""
    _log(db, 'Traceback: /home/ubuntu/ohisell/backend/app/x.py line 42: KeyError("secret")',
         status="error")
    msg = next(r for r in _get(db) if r["channel_id"] == 6)["error_message"]
    assert msg and "Traceback" not in msg and "/home/ubuntu" not in msg
    assert "서버 로그" in msg
