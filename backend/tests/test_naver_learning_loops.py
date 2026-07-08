# test_naver_learning_loops.py — 듀얼모드 스프린트 Phase 6 learning_loops harness 단위테스트
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.services.naver_ad import learning_loops

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


def test_run_all_succeeds_with_no_data(db):
    """데이터가 전혀 없어도(관찰모드 초기 상태) 4개 루프 전부 예외 없이 ok로 끝나야 한다."""
    result = learning_loops.run_all(db, today=TODAY)
    assert result["stage_status"] == {
        "proposal_scoreboard": "ok", "estimate_calibrator": "ok",
        "conversion_maturity": "ok", "hourly_pattern": "ok",
    }
    assert result["errors"] == []


def test_run_all_isolates_one_failing_loop(db, monkeypatch):
    def _boom(db, **kwargs):
        raise RuntimeError("의도적 실패")

    monkeypatch.setattr(learning_loops.estimate_calibrator, "run_daily", _boom)
    result = learning_loops.run_all(db, today=TODAY)
    assert result["stage_status"]["estimate_calibrator"] == "failed"
    assert result["stage_status"]["proposal_scoreboard"] == "ok"
    assert result["stage_status"]["conversion_maturity"] == "ok"
    assert result["stage_status"]["hourly_pattern"] == "ok"
    assert any("estimate_calibrator" in e for e in result["errors"])
