# test_health_partial_sync.py — 부분수집이 워치독 판정과 응답에 실리는가 (D-NAO-204)
#
# ★존재 이유: 2026-08-18에 주문 23건·상품매출 356,100원이 사라졌는데 그날 sync_log 네 회차가
#   전부 `success`였다. 다른 어떤 감시로도 안 잡힌다 — 잡은 돌았고(stale 아님) 상태는
#   success(failed 아님) 데이터도 어제 것이 있다(data_stale 아님). 이 감시가 그 사각지대다.
# ★픽스처는 prod 세션과 같게(autoflush=False) — 교훈 #292.
from __future__ import annotations

import pathlib
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Channel, SyncLog
from app.services.scheduler_health import (
    PARTIAL_SYNC_MAX_ROWS,
    PARTIAL_SYNC_WINDOW_HOURS,
    build_health,
    compute_scheduler_health,
)
from app.services.sync_service import PARTIAL_SYNC_MARKER

NOW = datetime(2026, 8, 19, 10, 30)


@pytest.fixture
def db():
    # ★StaticPool + check_same_thread=False: TestClient는 다른 스레드에서 도므로 기본 풀이면
    #   «테이블이 없다»로 깨진다. HTTP 경계 테스트를 쓸 수 있는 모양이어야 한다 —
    #   이 파일이 서비스층까지만 보던 것이 P1을 놓친 원인이다(적대 리뷰 2026-08-19).
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    s.add(Channel(id=6, name="네이버 스마트스토어", code="NAVER",
                  platform="naver", api_type="oauth2_bcrypt", api_config_key="naver"))
    s.commit()
    yield s
    s.close()


class _Sched:
    running = True

    def get_jobs(self):
        return []


def _log(db, *, msg: str | None, age_h: float = 1, status: str = "success",
         base: datetime = NOW):
    # ★`base`를 여는 이유: 순수 코어 테스트는 `build_health(..., NOW)`로 시계를 **주입**하지만,
    #   HTTP 경계 테스트가 타는 라우트는 `kst_now()`(실제 시각)를 쓴다(routers/scheduler.py).
    #   그래서 고정 상수 NOW로 심으면 벽시계가 창(PARTIAL_SYNC_WINDOW_HOURS=24)을 지나는 순간
    #   행이 조용히 창 밖으로 밀려 «부분수집 0건»이 되어 테스트가 썩는다 — 2026-08-24 실측:
    #   CI가 결제 차단으로 2주 멈춘 사이 이 테스트가 그 방식으로 죽어 있었다.
    #   HTTP 경계 테스트는 **라우트와 같은 시계**로 심어야 언제 돌려도 같은 결과가 난다.
    at = base - timedelta(hours=age_h)
    db.add(SyncLog(channel_id=6, sync_type="orders", status=status, records_synced=336,
                   error_message=msg, started_at=at, completed_at=at))
    db.commit()


# ── 코어: 부분수집이 있으면 healthy를 깬다 ────────────────────────────────────
def test_partial_sync_breaks_healthy():
    h = build_health([], [], set(), True, NOW,
                     partial_sync=[{"channel_name": "네이버", "detail": f"{PARTIAL_SYNC_MARKER} …"}])
    assert h["healthy"] is False
    assert h["partial_sync"]


def test_no_partial_sync_keeps_healthy():
    h = build_health([], [], set(), True, NOW, partial_sync=[])
    assert h["healthy"] is True
    # ★키는 항상 있어야 한다 — 없는 키와 0건이 같아 보이면 «판정 안 함»이 «이상 없음»으로 읽힌다.
    assert "partial_sync" in h
    assert h["partial_sync"] == []


def test_partial_sync_key_present_when_not_evaluated():
    h = build_health([], [], set(), True, NOW)
    assert "partial_sync" in h
    assert h["partial_sync"] is None
    # 조회를 못 한 것은 이상이 아니다 — 그걸로 healthy를 깨면 워치독이 자기 실패로 배너를 켠다.
    assert h["healthy"] is True


# ── Harness: sync_log에서 실제로 집어 온다 ────────────────────────────────────
def test_collects_partial_sync_rows_from_sync_log(db):
    _log(db, msg=f"{PARTIAL_SYNC_MARKER} 변경상태 스윕 미완주 1일: 2026-08-18")
    h = compute_scheduler_health(db, _Sched(), NOW)
    rows = h["partial_sync"]
    assert rows and len(rows) == 1
    assert rows[0]["channel_name"] == "네이버 스마트스토어"
    # 원문 그대로 실려야 «어느 날»이 남는다 — 재수집 대상을 고르는 유일한 좌표다.
    assert "2026-08-18" in rows[0]["detail"]
    assert h["healthy"] is False


def test_ignores_other_error_messages(db):
    _log(db, msg="이미 동기화가 진행 중입니다", status="error")
    h = compute_scheduler_health(db, _Sched(), NOW)
    assert h["partial_sync"] == []


def test_ignores_clean_success(db):
    _log(db, msg=None)
    assert compute_scheduler_health(db, _Sched(), NOW)["partial_sync"] == []


def test_window_size_is_pinned():
    """★창 «크기» 자체를 못 박는다 — 아래 두 창 테스트는 `age_h`를 상수 자신으로부터
    계산하므로 상수를 8760(1년)으로 늘려도 **둘 다 통과한다**(2026-08-24 적대 리뷰 P2-3
    변이 (f) 생존 실측). 그러면 «옛 부분수집이 배너를 영원히 켜 둔다»는 바로 그 사고를
    아무 테스트도 막지 못한다. `CONSERVATION_WINDOW_DAYS`는 이미 같은 방식으로 고정돼 있고
    (`test_vendor_item_axis.py`), 그 덕에 대응 변이가 잡혔다 — 비대칭을 없앤다.
    ★값을 바꾸려면 이 줄과 함께 «왜 24가 아니어야 하는가»를 근거로 남길 것.
    """
    assert PARTIAL_SYNC_WINDOW_HOURS == 24


def test_window_excludes_old_rows(db):
    """옛 부분수집이 영원히 배너를 켜 두면 배너가 배경음이 되고 다음 사고가 묻힌다."""
    _log(db, msg=f"{PARTIAL_SYNC_MARKER} 옛것", age_h=PARTIAL_SYNC_WINDOW_HOURS + 1)
    h = compute_scheduler_health(db, _Sched(), NOW)
    assert h["partial_sync"] == []


def test_window_includes_recent_rows(db):
    _log(db, msg=f"{PARTIAL_SYNC_MARKER} 최근것", age_h=PARTIAL_SYNC_WINDOW_HOURS - 1)
    assert len(compute_scheduler_health(db, _Sched(), NOW)["partial_sync"]) == 1


# ── ★HTTP 경계 — response_model이 키를 지우지 않는가 (적대 리뷰 P1, 2026-08-19) ──
def test_health_route_actually_returns_partial_sync(db):
    """★★서비스층 dict만 보는 테스트는 이 사고를 **원리적으로** 못 잡는다.

    FastAPI는 `response_model`에 없는 키를 직렬화에서 뺀다 — 서비스층엔 있고 HTTP body엔
    없는 상태가 만들어지고, 프론트는 `?? []`로 받아 조용해진다. 부분수집이 유일한 이상이면
    배너가 **통째로 숨는다**. 실제로 이 변경의 초판이 정확히 그 상태였다(schemas.py의 같은
    경고 주석 4개를 다 읽고도 새 필드에만 그 줄이 없었다).
    """
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from app.database import get_db  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415
    from app.utils.kst import kst_now  # noqa: PLC0415

    # ★라우트가 쓰는 시계(`kst_now()`)를 기준으로 심는다 — NOW 상수로 심으면 벽시계가 24시간
    #   창을 지난 뒤부터 영구 실패한다(위 `_log` 주석 참조).
    _log(db, msg=f"{PARTIAL_SYNC_MARKER} 변경상태 스윕 미완주 1일: 2026-08-18",
         base=kst_now())

    app.dependency_overrides[get_db] = lambda: db
    try:
        r = TestClient(app).get("/api/scheduler/health")
        assert r.status_code == 200
        body = r.json()
        assert "partial_sync" in body, "★response_model이 partial_sync를 지웠다 — 배너가 숨는다"
        assert body["partial_sync"], "부분수집 행이 HTTP body까지 와야 화면이 읽는다"
        assert "2026-08-18" in body["partial_sync"][0]["detail"]
        assert body["healthy"] is False
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_health_route_keeps_key_when_empty(db):
    """이상이 없어도 키는 와야 한다 — 없는 키와 «이상 없음»이 같아 보이면 판정을 못 한다."""
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from app.database import get_db  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    app.dependency_overrides[get_db] = lambda: db
    try:
        body = TestClient(app).get("/api/scheduler/health").json()
        assert "partial_sync" in body
        assert body["partial_sync"] == []
    finally:
        app.dependency_overrides.pop(get_db, None)


# ── 계약: 표식은 생산자 한 곳에서만 정의된다 ──────────────────────────────────
def test_marker_matches_frontend_copy():
    """★프론트는 언어가 달라 상수를 공유하지 못하고 **사본**을 갖는다(`frontend/src/pages/partialSync.ts`).

    갈라지면 백엔드·프론트 테스트가 양쪽 다 초록인데 주문 화면만 조용해진다(적대 리뷰 변이 M3).
    사본을 없앨 수는 없으니, 백엔드를 바꾸는 순간 여기서 걸리게 한다.
    """
    ts = pathlib.Path(__file__).resolve().parents[2] / "frontend/src/pages/partialSync.ts"
    assert ts.exists(), f"프론트 사본 파일을 못 찾음: {ts}"
    assert f'PARTIAL_SYNC_MARKER = "{PARTIAL_SYNC_MARKER}"' in ts.read_text(), (
        f"프론트 사본이 백엔드 표식({PARTIAL_SYNC_MARKER!r})과 다르다 — 화면이 조용해진다"
    )


def test_like_prefix_is_anchored(db):
    """접두사 매칭이어야 한다 — 부분일치(`%…%`)면 본문에 표식이 섞인 다른 메시지까지 잡는다."""
    _log(db, msg=f"동기화 실패(참고: {PARTIAL_SYNC_MARKER} 아님)")
    assert compute_scheduler_health(db, _Sched(), NOW)["partial_sync"] == []


def test_row_limit_is_applied(db):
    """폭주 방어 — 상한을 넘겨도 응답이 무한정 커지지 않는다."""
    for i in range(PARTIAL_SYNC_MAX_ROWS + 5):
        _log(db, msg=f"{PARTIAL_SYNC_MARKER} 건 {i}", age_h=1 + i * 0.01)
    rows = compute_scheduler_health(db, _Sched(), NOW)["partial_sync"]
    assert len(rows) == PARTIAL_SYNC_MAX_ROWS


def test_rows_are_newest_first(db):
    """프론트가 `partial_sync[0]`을 «최신»으로 쓴다 — 그 가정이 여기서 고정된다."""
    _log(db, msg=f"{PARTIAL_SYNC_MARKER} 오래된", age_h=10)
    _log(db, msg=f"{PARTIAL_SYNC_MARKER} 최신", age_h=1)
    rows = compute_scheduler_health(db, _Sched(), NOW)["partial_sync"]
    assert "최신" in rows[0]["detail"]
