# test_naver_ad_change_log_router.py — D-NAO-47 T3: GET /api/naver/ad/change-log HTTP 왕복
# 원칙22: SA 단위테스트는 라우터를 안 거치므로 라우터 레이어 500을 못 잡는다(P2-S2 사고 전례).
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import NaverChangeLog
from app.utils.kst import kst_now, kst_today


@pytest.fixture
def client_and_session():
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
    session_for_seed = TestingSession()
    yield TestClient(app), session_for_seed
    session_for_seed.close()
    app.dependency_overrides.clear()


@pytest.fixture
def client(client_and_session):
    return client_and_session[0]


@pytest.fixture
def db(client_and_session):
    return client_and_session[1]


def _seed(db, *, action="update_bid", campaign_id="cmp-1", dry_run=False, days_ago=0, outcome=None):
    row = NaverChangeLog(
        entity_type="keyword", entity_id="nkw-1", campaign_id=campaign_id,
        action=action, dry_run=dry_run, changed_at=kst_now() - timedelta(days=days_ago),
        before_value=json.dumps({"bidAmt": 700}), after_value=json.dumps({"bidAmt": 900}),
        rationale="테스트 근거", outcome=outcome,
    )
    db.add(row)
    db.commit()
    return row


def test_change_log_returns_rows_newest_first(client, db):
    _seed(db, days_ago=3)
    _seed(db, days_ago=1)
    r = client.get("/api/naver/ad/change-log")
    assert r.status_code == 200
    items = r.json()["rows"]
    assert len(items) == 2
    assert items[0]["changed_at"] > items[1]["changed_at"]


def test_change_log_parses_before_after_json(client, db):
    _seed(db)
    items = client.get("/api/naver/ad/change-log").json()["rows"]
    assert items[0]["before"] == {"bidAmt": 700}
    assert items[0]["after"] == {"bidAmt": 900}
    assert items[0]["action"] == "update_bid"
    assert items[0]["rationale"] == "테스트 근거"


def test_change_log_survives_malformed_json_without_500(client, db):
    """★before_value에 쓰레기가 들어있어도 500이 아니라 null로 흘려보낸다(fail-safe)."""
    row = _seed(db)
    row.before_value = "{not json"
    db.commit()
    r = client.get("/api/naver/ad/change-log")
    assert r.status_code == 200
    assert r.json()["rows"][0]["before"] is None


def test_change_log_filters_by_campaign(client, db):
    _seed(db, campaign_id="cmp-1")
    _seed(db, campaign_id="cmp-2")
    items = client.get("/api/naver/ad/change-log?campaign_id=cmp-1").json()["rows"]
    assert len(items) == 1
    assert items[0]["campaign_id"] == "cmp-1"


def test_change_log_filters_by_action(client, db):
    _seed(db, action="update_bid")
    _seed(db, action="external_bid_change")
    items = client.get("/api/naver/ad/change-log?action=external_bid_change").json()["rows"]
    assert len(items) == 1
    assert items[0]["action"] == "external_bid_change"


def test_change_log_excludes_dry_run_by_default(client, db):
    """★기본값이 중요하다: 1층 '우리 조작 N회'는 실제 집행만 세야 한다.
    dry_run을 섞으면 아무것도 안 했는데 일한 것처럼 보인다(D-47-h 정직성)."""
    _seed(db, dry_run=True)
    _seed(db, dry_run=False)
    items = client.get("/api/naver/ad/change-log").json()["rows"]
    assert len(items) == 1
    assert items[0]["dry_run"] is False

    all_items = client.get("/api/naver/ad/change-log?include_dry_run=true").json()["rows"]
    assert len(all_items) == 2


def test_change_log_respects_days_window(client, db):
    _seed(db, days_ago=40)
    _seed(db, days_ago=2)
    items = client.get("/api/naver/ad/change-log?days=7").json()["rows"]
    assert len(items) == 1


def test_change_log_limit_is_capped(client, db):
    r = client.get("/api/naver/ad/change-log?limit=99999")
    assert r.status_code == 422  # Query(le=500) 위반


def test_change_log_returns_total_for_pagination(client, db):
    for _ in range(3):
        _seed(db)
    body = client.get("/api/naver/ad/change-log?limit=2").json()
    assert body["total"] == 3
    assert len(body["rows"]) == 2


def test_change_log_empty_is_200_not_404(client):
    """★빈 상태는 에러가 아니다 — 1층이 '우리 조작 0회'를 정직하게 그려야 한다(D-47-h)."""
    body = client.get("/api/naver/ad/change-log").json()
    assert body == {"rows": [], "total": 0}


# ── codex[P2] 2026-07-17: actor 필터 — "우리 조작"에 외부 감지를 섞으면 안 된다 ──
def test_actor_ours_excludes_external_detections(client, db):
    """★D-47-h의 핵심. prod 실측상 change_log의 dry_run=False 행 15건이 **전부**
    external_status_change(외부가 바꾼 걸 우리가 감지한 것)다. 필터 없이 total을 쓰면
    1층이 "우리 조작 15회"라고 표시한다 — 우리는 아무것도 실행하지 않았는데.
    0을 0이라고 말하는 게 이 화면의 존재 이유인데 정반대로 거짓말을 하게 된다."""
    _seed(db, action="external_status_change")
    _seed(db, action="external_bid_change")
    _seed(db, action="update_bid")  # 우리 실집행

    ours = client.get("/api/naver/ad/change-log?actor=ours").json()
    assert ours["total"] == 1
    assert ours["rows"][0]["action"] == "update_bid"


def test_actor_ours_is_zero_when_only_external_exists(client, db):
    """★prod 현재 상태 재현: 외부 감지만 있고 우리 실집행은 0."""
    for _ in range(15):
        _seed(db, action="external_status_change")
    body = client.get("/api/naver/ad/change-log?actor=ours").json()
    assert body["total"] == 0
    assert body["rows"] == []


def test_actor_external_returns_only_detections(client, db):
    _seed(db, action="external_bid_change")
    _seed(db, action="update_bid")
    body = client.get("/api/naver/ad/change-log?actor=external").json()
    assert body["total"] == 1
    assert body["rows"][0]["action"] == "external_bid_change"


def test_actor_excludes_settings_changes_from_ours(client, db):
    """optimizer_change·update_expert_delegation은 우리 시스템 내부 설정이지
    광고 실집행이 아니다(광고 API 쓰기 아님)."""
    _seed(db, action="optimizer_change")
    _seed(db, action="update_expert_delegation")
    assert client.get("/api/naver/ad/change-log?actor=ours").json()["total"] == 0


def test_actor_all_is_default(client, db):
    _seed(db, action="external_status_change")
    _seed(db, action="update_bid")
    assert client.get("/api/naver/ad/change-log").json()["total"] == 2
    assert client.get("/api/naver/ad/change-log?actor=all").json()["total"] == 2


def test_actor_rejects_unknown_value(client):
    assert client.get("/api/naver/ad/change-log?actor=bogus").status_code == 422


def test_execution_actions_derives_from_harness_mapping():
    """★드리프트 방지: 실행 액션 목록을 프론트나 라우터가 하드코딩하면 새 제안 유형이
    배선될 때 조용히 어긋난다. harness의 _ACTION_BY_PROPOSAL_TYPE이 단일 진실이다."""
    from app.services.naver_ad.naver_execution_harness import (
        EXECUTION_ACTIONS, _ACTION_BY_PROPOSAL_TYPE,
    )
    assert EXECUTION_ACTIONS == frozenset(_ACTION_BY_PROPOSAL_TYPE.values())
    assert "external_bid_change" not in EXECUTION_ACTIONS
    assert "external_status_change" not in EXECUTION_ACTIONS
    assert "optimizer_change" not in EXECUTION_ACTIONS


def test_actor_ours_excludes_failed_writes(client, db):
    """★codex[P2] R2: 실패한 쓰기는 '우리 조작'이 아니다. harness는 가드 거부·쓰기 실패에도
    같은 action(update_bid)을 dry_run=False로 남긴다(_guard_failure는 writer를 부르지도 않음).
    그걸 세면 광고에 아무 변화가 없었는데 "우리 조작 1회"가 된다.
    판별은 outcome이 아니라 **after_value 존재**다 — 이 코드베이스가 이미 정한 규약
    (naver_execution_harness.py:372 주석·_load_our_bid_writes와 동일)."""
    _seed(db, action="update_bid")  # 성공(before/after 채워짐)
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-fail", campaign_id="cmp-1",
        action="update_bid", dry_run=False, outcome="failed",
        changed_at=kst_now(), before_value=None, after_value=None,  # ← 실패라 안 채워짐
    ))
    db.commit()

    body = client.get("/api/naver/ad/change-log?actor=ours").json()
    assert body["total"] == 1
    assert body["rows"][0]["entity_id"] == "nkw-1"


def test_actor_ours_scoped_by_campaign(client, db):
    """★codex[P2] R2: D-47-c(N=1→N=여럿)를 위해 캠페인별 집계가 되어야 한다.
    캠페인이 늘면 A의 조작이 B 행에도 표시되면 안 된다."""
    _seed(db, action="update_bid", campaign_id="cmp-1")
    _seed(db, action="update_bid", campaign_id="cmp-2")
    _seed(db, action="update_bid", campaign_id="cmp-2")

    assert client.get("/api/naver/ad/change-log?actor=ours&campaign_id=cmp-1").json()["total"] == 1
    assert client.get("/api/naver/ad/change-log?actor=ours&campaign_id=cmp-2").json()["total"] == 2


# ══════════════════════════════════════════════════════════════════
# D-NAO-54 ① 날짜 범위(date_from/date_to) — 프리셋 5종의 원천
#   당일 / 어제 / 어제부터 7일 / 어제부터 30일 / 캘린더 커스텀.
#   ★`days`는 "지금부터 N일 전"이라 **당일만**·**어제만** 같은 닫힌 구간을 표현할 수 없다.
#   ★changed_at은 KST 저장이므로(kst_now() 명시 전달) 경계도 KST 날짜로 판단한다.
#     서버 시계가 UTC인데 `date('now')`를 쓰면 9시간 어긋난다(memory: sqlite-server-default-now-is-utc).
# ══════════════════════════════════════════════════════════════════


def _seed_blocked(db, *, action="update_bid", campaign_id="cmp-1", days_ago=0, dry_run=False):
    """가드레일 차단 시도 = harness._guard_failure가 남기는 모양.
    before/after 둘 다 None · outcome='failed' · dry_run=False(실제 시도였으므로)."""
    row = NaverChangeLog(
        entity_type="adgroup", entity_id="grp-blocked", campaign_id=campaign_id,
        action=action, dry_run=dry_run, outcome="failed",
        changed_at=kst_now() - timedelta(days=days_ago),
        before_value=None, after_value=None,
        rationale="근거 [실행 불가] 가드레일 차단 — 변경폭 39.3% 초과(상한 15%, D-NAO-5)",
    )
    db.add(row)
    db.commit()
    return row


def test_date_range_is_inclusive_on_both_ends(client, db):
    """당일·어제 프리셋의 근간 — date_to 당일 23:59:59가 잘리면 '당일' 탭이 빈다."""
    today = kst_today().isoformat()
    _seed(db, days_ago=0)
    _seed(db, days_ago=1)
    _seed(db, days_ago=2)

    body = client.get(f"/api/naver/ad/change-log?date_from={today}&date_to={today}").json()
    assert body["total"] == 1, "당일 = 오늘 하루만"

    yesterday = (kst_today() - timedelta(days=1)).isoformat()
    body = client.get(f"/api/naver/ad/change-log?date_from={yesterday}&date_to={yesterday}").json()
    assert body["total"] == 1, "어제 = 어제 하루만(오늘 제외)"


def test_date_range_seven_days_ending_yesterday_excludes_today(client, db):
    """Jino 확정(2026-07-17): 7일·30일은 **어제 기준**이다 — 당일은 진행 중이라 별도 탭.
    당일이 섞이면 '완결된 과거'라는 탭의 의미가 깨진다."""
    _seed(db, days_ago=0)   # 오늘 — 제외되어야 함
    _seed(db, days_ago=1)   # 어제 — 포함
    _seed(db, days_ago=7)   # 7일 창의 끝 — 포함
    _seed(db, days_ago=8)   # 창 밖 — 제외

    date_to = (kst_today() - timedelta(days=1)).isoformat()
    date_from = (kst_today() - timedelta(days=7)).isoformat()
    body = client.get(f"/api/naver/ad/change-log?date_from={date_from}&date_to={date_to}").json()
    assert body["total"] == 2, "어제 + 7일전만(오늘·8일전 제외)"


def test_date_range_takes_precedence_over_days(client, db):
    _seed(db, days_ago=0)
    _seed(db, days_ago=10)
    today = kst_today().isoformat()
    body = client.get(f"/api/naver/ad/change-log?days=30&date_from={today}&date_to={today}").json()
    assert body["total"] == 1, "date_from/date_to가 오면 days는 무시한다"


def test_date_range_rejects_reversed_range(client, db):
    today = kst_today().isoformat()
    yesterday = (kst_today() - timedelta(days=1)).isoformat()
    r = client.get(f"/api/naver/ad/change-log?date_from={today}&date_to={yesterday}")
    assert r.status_code == 422, "캘린더에서 뒤집어 고를 수 있다 — 빈 결과로 조용히 넘기지 않는다"


def test_date_range_rejects_span_over_limit(client, db):
    """days가 le=365인 것과 같은 상한 — 캘린더로 우회되면 안 된다."""
    date_to = kst_today().isoformat()
    date_from = (kst_today() - timedelta(days=400)).isoformat()
    r = client.get(f"/api/naver/ad/change-log?date_from={date_from}&date_to={date_to}")
    assert r.status_code == 422


def test_date_range_requires_both_ends(client, db):
    """한쪽만 주면 나머지 창이 days로 결정돼 사용자가 고른 적 없는 구간이 나온다 — 명시적으로 막는다."""
    today = kst_today().isoformat()
    assert client.get(f"/api/naver/ad/change-log?date_from={today}").status_code == 422
    assert client.get(f"/api/naver/ad/change-log?date_to={today}").status_code == 422


def test_date_range_rejects_malformed_date(client, db):
    assert client.get("/api/naver/ad/change-log?date_from=2026-13-99&date_to=2026-07-17").status_code == 422


# ══════════════════════════════════════════════════════════════════
# D-NAO-54 ② include_blocked — 가드레일이 막은 시도를 보이게
#   ★actor=ours의 기본 계약("실집행만")은 불변이다: 1층 "우리 조작 N회"의 원천이라
#   차단분이 기본으로 섞이면 광고에 아무 변화가 없었는데 "조작 1회"가 된다(codex[P2] R2).
#   그래서 **옵트인**이고, 프론트는 executed 플래그로 구분해 그린다.
# ══════════════════════════════════════════════════════════════════


def test_include_blocked_defaults_off_preserving_ours_contract(client, db):
    _seed(db, action="update_bid")
    _seed_blocked(db)
    body = client.get("/api/naver/ad/change-log?actor=ours").json()
    assert body["total"] == 1, "기본값은 실집행만 — 1층 카운트 정직성(D-47-h)"


def test_include_blocked_surfaces_guard_rejections(client, db):
    _seed(db, action="update_bid")
    _seed_blocked(db)
    body = client.get("/api/naver/ad/change-log?actor=ours&include_blocked=true").json()
    assert body["total"] == 2
    ids = {r["entity_id"] for r in body["rows"]}
    assert "grp-blocked" in ids


def test_executed_flag_distinguishes_blocked_from_real(client, db):
    """프론트가 🚫 배지를 달 근거. ★outcome이 아니라 after 존재로 판별한다 —
    outcome은 D+14 채점 전 NULL이고 채점 후 improved/declined로 바뀌어 '실행됨'의 영구 상태가 아니다."""
    _seed(db, action="update_bid")
    _seed_blocked(db)
    rows = client.get("/api/naver/ad/change-log?actor=ours&include_blocked=true").json()["rows"]
    by_id = {r["entity_id"]: r for r in rows}
    assert by_id["nkw-1"]["executed"] is True
    assert by_id["grp-blocked"]["executed"] is False


def test_include_blocked_does_not_admit_external_detections(client, db):
    """차단분을 여는 것이 외부 감지까지 여는 뒷문이 되면 안 된다 — 남의 조작이 우리 성과로 섞인다."""
    _seed(db, action="external_bid_change")
    _seed_blocked(db)
    rows = client.get("/api/naver/ad/change-log?actor=ours&include_blocked=true").json()["rows"]
    assert all(r["action"] == "update_bid" for r in rows)


def test_include_blocked_still_excludes_dry_run(client, db):
    """dry-run 차단은 '시도'조차 아니다 — include_dry_run과 직교해야 한다."""
    _seed_blocked(db, dry_run=True)
    body = client.get("/api/naver/ad/change-log?actor=ours&include_blocked=true").json()
    assert body["total"] == 0
