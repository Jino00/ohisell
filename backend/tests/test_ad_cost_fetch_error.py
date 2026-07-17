# test_ad_cost_fetch_error.py — Mac 페처 실패의 prod 보고 경로.
#   ★배경(2026-07-17 13:02 실사고): 페처가 브라우저 에러로 죽었는데 그 실패를 prod에 알리는
#   경로가 없어 last_error=null·last_success_at 불변 → UI는 "실패"와 "아직 진행 중"을
#   구분할 방법이 없었다(화면은 이미 끝난 작업을 계속 갱신 중으로 표시).
#   last_success_at의 짝인 last_error_at을 두어 "언제 실패했나"를 만들고, UI 폴링이
#   그 변화로 실패를 즉시 감지한다. 성공 시 자동 클리어(오래된 실패가 남지 않음).
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import CoupangWingCookie
from app.services.coupang import ad_cost_sync


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    yield s
    s.close()


def _seed_cookie(db) -> CoupangWingCookie:
    # 계정 키는 SA 상수를 그대로 씀 — 하드코딩하면 SA와 조용히 어긋난다(_cookie_row가 못 찾음).
    row = CoupangWingCookie(account_key=ad_cost_sync._ADS_ACCOUNT, cookie_blob="x", status="unknown")
    db.add(row)
    db.commit()
    return row


def test_mark_fetch_error_records_message_time_and_red(db):
    """페처 실패 보고 → last_error·last_error_at·status=red. (실사고 메시지 그대로)"""
    _seed_cookie(db)
    ad_cost_sync.mark_fetch_error(db, "browser: Target page, context or browser has been closed")

    st = ad_cost_sync.cookie_status(db)
    assert "browser" in (st["last_error"] or "")
    assert st["last_error_at"] is not None  # ← 짝이 없어 실패/진행중을 못 가르던 뿌리
    assert st["status"] == "red"


def test_mark_fetch_error_truncates_to_column_limit(db):
    """긴 스택트레이스가 와도 컬럼(300자)을 넘겨 쓰기 실패로 번지지 않는다."""
    _seed_cookie(db)
    ad_cost_sync.mark_fetch_error(db, "E" * 5000)
    assert len(db.query(CoupangWingCookie).first().last_error) <= 300


def test_success_clears_stale_error(db):
    """성공하면 과거 실패 흔적(메시지·시각)이 남지 않는다 — 배너 오진단 방지."""
    _seed_cookie(db)
    ad_cost_sync.mark_fetch_error(db, "browser closed")
    ad_cost_sync.ingest_ad_cost_days(db, [
        {"date": date(2026, 7, 17), "ad_spend": 1000, "conv_sales": 5000}])

    st = ad_cost_sync.cookie_status(db)
    assert st["last_error"] is None
    assert st["last_error_at"] is None
    assert st["status"] == "green"


def test_refresh_status_exposes_error_time_for_ui_polling(db):
    """UI 폴링(refresh-status)이 실패를 감지할 수 있어야 한다 — 이 필드가 없으면 215초를 헛기다린다."""
    _seed_cookie(db)
    before = ad_cost_sync.refresh_status(db)
    assert before["last_error_at"] is None

    ad_cost_sync.mark_fetch_error(db, "browser closed")
    after = ad_cost_sync.refresh_status(db)
    assert after["last_error_at"] is not None
    assert after["last_error_at"] != before["last_error_at"]


def test_repeated_identical_error_still_moves_the_clock(db):
    """같은 문구로 두 번 실패해도 시각이 갱신된다(문자열 비교만으론 2회차를 놓친다)."""
    _seed_cookie(db)
    ad_cost_sync.mark_fetch_error(db, "browser closed")
    first = ad_cost_sync.refresh_status(db)["last_error_at"]

    row = db.query(CoupangWingCookie).first()
    row.last_error_at = row.last_error_at.replace(year=2020)  # 과거로 밀어 갱신 여부 관찰
    db.commit()

    ad_cost_sync.mark_fetch_error(db, "browser closed")
    assert ad_cost_sync.refresh_status(db)["last_error_at"] != "2020"
    assert db.query(CoupangWingCookie).first().last_error_at.year != 2020


def test_mark_fetch_error_without_cookie_row_is_noop(db):
    """쿠키 행이 없어도(초기 상태) 페처 보고가 예외로 번지지 않는다."""
    ad_cost_sync.mark_fetch_error(db, "browser closed")  # 예외 없이 통과해야 함
    assert ad_cost_sync.cookie_status(db)["configured"] is False
