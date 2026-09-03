"""PAO 캘린더 통일이 새로 만든 **창 파라미터·창 응답**을 HTTP 경계에서 잰다.

왜 이 파일이 있나 (적대 리뷰 1R P1-3·P1-4, 2026-09-03):

`GET /performance/ownership-bands`에 이번에 `date_from`/`date_to`를 가산하며 422 가드 셋을
같이 넣었는데(둘 다 지정 · 역전 금지 · 상한), **그 셋을 통째로 지워도 백엔드 7,706건이
전부 초록이었다.** 유일하게 `ownership-bands`를 언급하는 테스트 파일조차 SA 함수를 직접
부를 뿐 라우터를 통과하지 않았다 — 즉 라우터 층을 아무도 안 지키고 있었다.

같은 이유로 `perf_timeline_harness.build_timeline`의 신설 `window:{from,to}`도 무검증이었다.
프론트의 **잠긴 캘린더**가 그 값을 그대로 화면에 박아 「서버가 실제로 쓴 창」이라고 사용자에게
보여주므로, 서버 쪽 값이 틀리면 화면이 조용히 거짓말을 한다. `window.from`을 미래로
뒤집는 변이가 24건을 그대로 통과했다.

★여기서 재는 것은 «라우터가 무엇을 받고 무엇을 돌려주나»다 — 밴드 집계 로직 자체는
  `test_naver_ownership_timeline.py`가 SA 층에서 잰다(두 층을 겹쳐 재지 않는다).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import NaverAdDaily
from app.services.naver_ad import perf_timeline_harness

_BANDS = "/api/naver/ad/performance/ownership-bands"


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    seed = TestingSession()
    # 확정 적재가 있어야 창이 잘리지 않는다 — 밴드 «값»이 아니라 «창 처리»를 재는 것이라
    # 한 줄이면 충분하다.
    seed.add(
        NaverAdDaily(
            ad_date=date(2026, 8, 20), campaign_id="cmp-1", adgroup_id="grp-1",
            cost=1000, imp=10, clk=1,
        )
    )
    seed.commit()
    yield TestClient(app)
    seed.close()
    app.dependency_overrides.clear()


class TestOwnershipBandsWindowParams:
    """신설 `date_from`/`date_to` — 셋 다 라우터 층에서만 걸린다."""

    def test_date_range_is_used_verbatim(self, client):
        """고른 날짜가 그대로 창이 된다. 이것이 «고른 날짜 ≠ 실제 창»을 막는 자리다."""
        r = client.get(_BANDS, params={"date_from": "2026-08-10", "date_to": "2026-08-20"})
        assert r.status_code == 200, r.text
        w = r.json()["window"]
        assert w["date_from"] == "2026-08-10"
        assert w["date_to"] == "2026-08-20"

    def test_only_one_side_is_rejected(self, client):
        """한쪽만 주면 422 — 나머지 한쪽을 서버가 «지어내면» 창이 조용히 달라진다."""
        assert client.get(_BANDS, params={"date_from": "2026-08-10"}).status_code == 422
        assert client.get(_BANDS, params={"date_to": "2026-08-20"}).status_code == 422

    def test_reversed_range_is_rejected(self, client):
        """뒤집힌 구간은 422 — 빈 결과를 「그 기간엔 광고가 없었다」로 읽으면 거짓이다."""
        r = client.get(_BANDS, params={"date_from": "2026-08-20", "date_to": "2026-08-10"})
        assert r.status_code == 422

    def test_span_cap(self, client):
        """상한 경계 — 365일은 통과, 366일은 422(프론트 `customRangeError`와 같은 값이다)."""
        end = date(2026, 8, 20)
        ok = client.get(_BANDS, params={
            "date_from": (end - timedelta(days=364)).isoformat(), "date_to": end.isoformat(),
        })
        assert ok.status_code == 200, ok.text
        too_long = client.get(_BANDS, params={
            "date_from": (end - timedelta(days=365)).isoformat(), "date_to": end.isoformat(),
        })
        assert too_long.status_code == 422

    def test_days_path_is_untouched(self, client):
        """종전 `days` 경로는 한 글자도 안 바뀐다 — 그 경로의 「확정 N일」 기준점 규칙은
        적대 리뷰 P1-2 수리라 이번 가산이 덮으면 안 된다."""
        r = client.get(_BANDS, params={"days": 30})
        assert r.status_code == 200, r.text
        assert "window" in r.json()

    def test_explicit_range_wins_over_days(self, client):
        """둘 다 주면 «사람이 고른 날짜»가 이긴다 — 그러지 않으면 화면이 보낸 창이 무시된다."""
        r = client.get(_BANDS, params={
            "days": 30, "date_from": "2026-08-18", "date_to": "2026-08-20",
        })
        assert r.status_code == 200, r.text
        assert r.json()["window"]["date_from"] == "2026-08-18"


class TestTimelineWindowIsServerTruth:
    """신설 `window:{from,to}` — 잠긴 캘린더가 이 값을 «서버가 쓴 창»이라고 화면에 박는다."""

    def test_window_matches_the_collection_boundary(self, client):
        """★값이 `improvement_events.collect`의 경계(`since = day - days`)와 같아야 한다.
        여기가 어긋나면 화면은 «서버가 쓴 창»이라며 서버가 안 쓴 날짜를 보여준다."""
        day = date(2026, 9, 3)
        out = perf_timeline_harness.build_timeline(
            _session_of(client), days=90, today=day,
        )
        assert out["window"]["from"] == (day - timedelta(days=90)).isoformat()
        assert out["window"]["to"] == day.isoformat()

    def test_window_is_not_reversed(self, client):
        """시작이 끝보다 앞이다 — 뒤집히면 화면의 두 칸이 말이 안 되는 구간을 보여준다."""
        out = perf_timeline_harness.build_timeline(
            _session_of(client), days=30, today=date(2026, 9, 3),
        )
        assert out["window"]["from"] < out["window"]["to"]

    def test_window_tracks_days(self, client):
        """창의 «길이»가 days를 따라 움직인다 — 프리셋을 눌러도 창이 안 바뀌면 죽은 버튼이다."""
        day = date(2026, 9, 3)
        db = _session_of(client)
        w30 = perf_timeline_harness.build_timeline(db, days=30, today=day)["window"]
        w180 = perf_timeline_harness.build_timeline(db, days=180, today=day)["window"]
        assert w30["from"] > w180["from"]
        assert w30["to"] == w180["to"] == day.isoformat()


def _session_of(client: TestClient):
    """라우터가 쓰는 것과 같은 오버라이드 세션 하나를 꺼낸다."""
    gen = app.dependency_overrides[get_db]()
    db = next(gen)
    return db
