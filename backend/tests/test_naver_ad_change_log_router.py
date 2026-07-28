# test_naver_ad_change_log_router.py — D-NAO-47 T3: GET /api/naver/ad/change-log HTTP 왕복
# 원칙22: SA 단위테스트는 라우터를 안 거치므로 라우터 레이어 500을 못 잡는다(P2-S2 사고 전례).
from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import NaverChangeLog
from app.services.naver_ad import naver_execution_harness
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
    # 핵심 계약(빈 결과도 200 + rows/total)만 고정. executed_total=None도 함께 확인하되
    # 추가 필드(대상 이름 등 per-row 필드는 빈 결과엔 없음)에는 관용.
    assert body["rows"] == [] and body["total"] == 0 and body["executed_total"] is None


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


def _seed_budget_pacing(db, *, outcome=None, executed=True, rationale="[예산페이싱] 테스트 근거"):
    """BP(D-NAO-102) 레인이 남기는 모양 그대로 — action은 세분화 라벨, 실행 경로는
    _execute_update_budget 하나라 성공행은 before/after가 채워지고 캠페인 단위다."""
    from app.services.naver_ad.budget_pacing import ACTION_BUDGET_UP_PACING

    row = NaverChangeLog(
        entity_type="campaign", entity_id="cmp-1", campaign_id="cmp-1",
        action=ACTION_BUDGET_UP_PACING, dry_run=False, changed_at=kst_now(),
        before_value=json.dumps({"dailyBudget": 30000}) if executed else None,
        after_value=json.dumps({"dailyBudget": 50000}) if executed else None,
        rationale=rationale, outcome=outcome,
    )
    db.add(row)
    db.commit()
    return row


def test_actor_ours_counts_budget_pacing_writes(client, db):
    """★D-NAO-102 ⑥ 회귀: BP 레인은 change_log에 'budget_up_pacing'이라는 **다른 라벨**을
    남기지만 실행 경로는 update_budget 하나 — 광고 일예산을 실제로 바꾼 우리 실집행이다.
    EXECUTION_ACTIONS가 라벨을 모르면 actor=ours가 이 행을 못 세고, 커맨드 센터 1층이
    "우리 조작 0회"라 말한다(D-47-h는 0을 0이라 말하라는 것이지, 한 일을 안 했다고
    말하라는 게 아니다)."""
    _seed_budget_pacing(db)
    body = client.get("/api/naver/ad/change-log?actor=ours").json()
    assert body["total"] == 1
    assert body["rows"][0]["action"] == "budget_up_pacing"
    # 두 번째 소비처(_execution_state)도 같은 집합을 쓴다 — 배지가 살아 있어야 한다.
    assert body["rows"][0]["execution_state"] == "executed"


def test_actor_ours_include_blocked_shows_budget_pacing_guard_block(client, db):
    """BP도 가드 거부 행에 **같은 라벨**을 단다(harness `_guard_failure(…, log_action, …)`).
    라벨이 집합 밖이면 '가드레일이 BP 증액을 막은 것'도 화면에서 통째로 사라진다."""
    from app.services.naver_ad.naver_execution_harness import GUARD_BLOCK_MARKER

    _seed_budget_pacing(
        db, executed=False, outcome="failed",
        rationale=f"[예산페이싱] 증액 {GUARD_BLOCK_MARKER} 가드레일 차단 — BEP 이익하한",
    )
    assert client.get("/api/naver/ad/change-log?actor=ours").json()["total"] == 0  # 미집행
    body = client.get("/api/naver/ad/change-log?actor=ours&include_blocked=true").json()
    assert body["total"] == 1
    assert body["rows"][0]["execution_state"] == "blocked"


def test_actor_all_is_default(client, db):
    _seed(db, action="external_status_change")
    _seed(db, action="update_bid")
    assert client.get("/api/naver/ad/change-log").json()["total"] == 2
    assert client.get("/api/naver/ad/change-log?actor=all").json()["total"] == 2


def test_actor_rejects_unknown_value(client):
    assert client.get("/api/naver/ad/change-log?actor=bogus").status_code == 422


def test_execution_actions_derives_from_harness_mapping():
    """★드리프트 방지: 실행 액션 목록을 프론트나 라우터가 하드코딩하면 새 제안 유형이
    배선될 때 조용히 어긋난다. 단일 진실은 harness의 _ACTION_BY_PROPOSAL_TYPE(제안유형→액션)
    + 라벨 세분화분(BP의 PACING_ACTIONS)이다."""
    from app.services.naver_ad.budget_pacing import PACING_ACTIONS
    from app.services.naver_ad.naver_execution_harness import (
        EXECUTION_ACTIONS, _ACTION_BY_PROPOSAL_TYPE,
    )
    assert EXECUTION_ACTIONS == frozenset(_ACTION_BY_PROPOSAL_TYPE.values()) | frozenset(
        PACING_ACTIONS
    )
    assert "external_bid_change" not in EXECUTION_ACTIONS
    assert "external_status_change" not in EXECUTION_ACTIONS
    assert "optimizer_change" not in EXECUTION_ACTIONS


def test_execution_actions_covers_every_budget_log_label():
    """★구조 가드: `_budget_log_action`이 낼 수 있는 라벨은 **전부** EXECUTION_ACTIONS에
    있어야 한다. 이 관계가 깨지면 우리가 광고를 실제로 바꿔놓고 actor=ours가 0이라 말한다
    (D-NAO-102 ⑥ BP 라벨 분리 때 실제로 일어난 일). 목록을 다시 적지 않고 **함수를 직접
    호출해** 확인한다 — 사본을 만들면 그 사본이 다음 드리프트의 자리가 된다."""
    from app.models import NaverProposal
    from app.services.naver_ad import budget_pacing
    from app.services.naver_ad.naver_execution_harness import (
        EXECUTION_ACTIONS, _budget_log_action,
    )

    for approval_source in (None, "console", budget_pacing.APPROVAL_SOURCE_PACING):
        for proposal_type in ("budget_up", "budget_down"):
            label = _budget_log_action(
                NaverProposal(proposal_type=proposal_type, approval_source=approval_source)
            )
            assert label in EXECUTION_ACTIONS, (
                f"{approval_source=} {proposal_type=} → {label!r}가 EXECUTION_ACTIONS 밖 — "
                "actor=ours가 이 실집행을 못 센다"
            )


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


def _seed_blocked(db, *, action="update_bid", campaign_id="cmp-1", days_ago=0, entity_id="grp-blocked"):
    """가드레일 차단 시도 = harness._guard_failure가 남기는 모양.
    before/after 둘 다 None · outcome='failed' · dry_run=False(하드코딩 — harness:160).
    ★rationale 접두사는 harness 상수에서 가져온다 — 리터럴을 쓰면 harness가 문구를 바꿀 때
      테스트만 통과하고 prod에서 조용히 어긋난다(라우터가 이 접두사로 blocked/unknown을 가른다)."""
    row = NaverChangeLog(
        entity_type="adgroup", entity_id=entity_id, campaign_id=campaign_id,
        action=action, dry_run=False, outcome="failed",
        changed_at=kst_now() - timedelta(days=days_ago),
        before_value=None, after_value=None,
        rationale=f"근거 {naver_execution_harness.GUARD_BLOCK_MARKER} 변경폭 39.3% 초과(상한 15%, D-NAO-5)",
    )
    db.add(row)
    db.commit()
    return row


def _seed_write_failed(db, *, campaign_id="cmp-1", entity_id="grp-writefail"):
    """쓰기 실패 시도 = harness의 writer 예외 경로가 남기는 모양(:497 등).
    ★차단 행과 DB 모양이 **완전히 같다**(dry_run=False · outcome='failed' · after=None).
      다른 건 rationale 접두사뿐 — 그래서 이 테스트가 그 구분을 지킨다."""
    row = NaverChangeLog(
        entity_type="adgroup", entity_id=entity_id, campaign_id=campaign_id,
        action="update_bid", dry_run=False, outcome="failed", changed_at=kst_now(),
        before_value=None, after_value=None,
        rationale=(
            f"근거 {naver_execution_harness.WRITE_FAILURE_MARKER} "
            "WriteVerificationError: bidAmt는 반영됐으나 useGroupBidAmt가 false로 전환되지 않음"
        ),
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


def test_include_blocked_does_not_admit_external_detections(client, db):
    """차단분을 여는 것이 외부 감지까지 여는 뒷문이 되면 안 된다 — 남의 조작이 우리 성과로 섞인다."""
    _seed(db, action="external_bid_change")
    _seed_blocked(db)
    rows = client.get("/api/naver/ad/change-log?actor=ours&include_blocked=true").json()["rows"]
    assert all(r["action"] == "update_bid" for r in rows)




def test_include_blocked_still_excludes_dry_run(client, db):
    """dry-run은 '시도'조차 아니다 — include_dry_run과 직교해야 한다.

    ★이전 판(2026-07-17)은 `_seed_blocked(dry_run=True)`로 시드해 **존재할 수 없는 행 모양**을
    검증하며 공허하게 통과했다(harness._guard_failure는 dry_run=False를 하드코딩한다).
    실제 직교성은 이것이다: dry-run 행은 after_value도 outcome도 없어 or_ 어느 가지에도
    안 걸린다 — include_dry_run과 include_blocked를 **둘 다 켜도** 안 나온다."""
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-dry", campaign_id="cmp-1",
        action="update_bid", dry_run=True, outcome=None, changed_at=kst_now(),
        before_value=None, after_value=None, rationale="시뮬레이션",
    ))
    db.commit()
    body = client.get(
        "/api/naver/ad/change-log?actor=ours&include_blocked=true&include_dry_run=true"
    ).json()
    assert body["total"] == 0


# ══════════════════════════════════════════════════════════════════
# D-NAO-54 ③ execution_state — "모름"을 "차단됨"이라고 말하지 않는다(원칙22)
# ══════════════════════════════════════════════════════════════════


def test_execution_state_executed_for_real_write(client, db):
    _seed(db, action="update_bid")
    rows = client.get("/api/naver/ad/change-log?actor=ours").json()["rows"]
    assert rows[0]["execution_state"] == "executed"


def test_execution_state_blocked_only_for_guard_rejection(client, db):
    _seed_blocked(db)
    rows = client.get("/api/naver/ad/change-log?actor=ours&include_blocked=true").json()["rows"]
    assert rows[0]["execution_state"] == "blocked"


def test_execution_state_write_failure_is_unknown_not_blocked(client, db):
    """★핵심 회귀 고정. naver_sa_writer의 WriteVerificationError는 **bidAmt가 이미 반영된 뒤**
    useGroupBidAmt 미전환에서도 뜬다(writer:341) — 그 행을 '차단됨'이라 부르면 네이버엔
    우리 입찰가가 들어가 있는데 안 바꿨다고 단언하는 것이다(원칙22 위반).
    PUT 성공 후 응답만 유실된 네트워크 타임아웃도 같은 행을 만든다."""
    _seed_write_failed(db)
    rows = client.get("/api/naver/ad/change-log?actor=ours&include_blocked=true").json()["rows"]
    assert rows[0]["execution_state"] == "unknown"
    assert rows[0]["execution_state"] != "blocked"


def test_execution_state_unknown_when_marker_absent(client, db):
    """접두사가 없으면 보수 판정 — 가드 거부라고 단정하려면 적극적 증거가 있어야 한다."""
    db.add(NaverChangeLog(
        entity_type="adgroup", entity_id="grp-nomarker", campaign_id="cmp-1",
        action="update_bid", dry_run=False, outcome="failed", changed_at=kst_now(),
        before_value=None, after_value=None, rationale="접두사 없는 옛 행",
    ))
    db.commit()
    rows = client.get("/api/naver/ad/change-log?actor=ours&include_blocked=true").json()["rows"]
    assert rows[0]["execution_state"] == "unknown"


def test_execution_state_is_none_for_external_and_settings(client, db):
    """★execution_state는 actor=ours 관점 전용인데 응답 전역에 나간다. bool 하나였다면
    external_keyword_removed(after 없음)는 '차단됨', optimizer_change(after 있음)는 '집행됨'이
    된다 — 우리 가드레일은 남의 조작에 걸리지 않고, optimizer_change는 광고 API를 건드린 적이 없다."""
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-gone", campaign_id="cmp-1",
        action="external_keyword_removed", dry_run=False, changed_at=kst_now(),
        before_value=json.dumps({"keyword": "사라진키워드"}), after_value=None,
    ))
    _seed(db, action="optimizer_change")
    db.commit()
    rows = client.get("/api/naver/ad/change-log?actor=all").json()["rows"]
    assert {r["execution_state"] for r in rows} == {None}


def test_executed_total_splits_real_from_attempted(client, db):
    """★`총 N건` 하나만 주면 "우리가 한 일" 카드가 차단분을 집행으로 세게 한다 —
    actor 필터가 막으려던 거짓말이 화면에서 되살아난다."""
    _seed(db, action="update_bid")
    _seed_blocked(db, entity_id="grp-b1")
    _seed_write_failed(db)
    body = client.get("/api/naver/ad/change-log?actor=ours&include_blocked=true").json()
    assert body["total"] == 3
    assert body["executed_total"] == 1, "실제로 광고가 바뀐 건 1건뿐"


def test_executed_total_is_none_without_include_blocked(client, db):
    """차단분을 안 켰으면 total 자체가 이미 실집행 수라 분리 집계가 의미 없다."""
    _seed(db, action="update_bid")
    assert client.get("/api/naver/ad/change-log?actor=ours").json()["executed_total"] is None


def test_include_blocked_rejected_outside_actor_ours(client, db):
    """조용히 무시하면 호출자가 켰다고 믿는다."""
    assert client.get("/api/naver/ad/change-log?actor=all&include_blocked=true").status_code == 422
    assert client.get("/api/naver/ad/change-log?actor=external&include_blocked=true").status_code == 422


def test_marker_constants_match_harness_rationale_writes():
    """★드리프트 방지: 라우터가 이 접두사 문자열로 blocked/unknown을 가른다. harness가 문구를
    바꾸면 라우터는 **조용히** 전부 unknown으로 떨어진다 — 상수를 공유해 그걸 막는다."""
    from app.services.naver_ad.naver_execution_harness import (
        GUARD_BLOCK_MARKER, WRITE_FAILURE_MARKER,
    )
    src = pathlib.Path(naver_execution_harness.__file__).read_text()
    assert GUARD_BLOCK_MARKER != WRITE_FAILURE_MARKER
    # 리터럴이 코드에 남아 있으면 상수화가 뚫린 것이다(주석 블록은 설명이라 허용).
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    assert f'{GUARD_BLOCK_MARKER} {{reason}}' not in code
    assert f'{WRITE_FAILURE_MARKER} {{type(exc)' not in code


# ══════════════════════════════════════════════════════════════════
# D-NAO-54 경계 — 검토자 지적: 기존 테스트가 "경계"라 이름 붙이고 경계를 안 짚었다
# ══════════════════════════════════════════════════════════════════


def test_date_range_includes_last_microsecond_of_date_to(client, db):
    """★`_seed(days_ago=0)`은 '지금'이라 23:59:59를 안 짚는다. until을 `<=`로 잘못 쓰거나
    +1일을 빠뜨리면 여기서만 잡힌다."""
    today = kst_today()
    for label, t in (("sod", datetime.min.time()), ("eod", datetime.max.time())):
        db.add(NaverChangeLog(
            entity_type="keyword", entity_id=f"nkw-{label}", campaign_id="cmp-1",
            action="update_bid", dry_run=False,
            changed_at=datetime.combine(today, t),
            before_value=json.dumps({"bidAmt": 700}), after_value=json.dumps({"bidAmt": 900}),
        ))
    db.commit()
    iso = today.isoformat()
    body = client.get(f"/api/naver/ad/change-log?date_from={iso}&date_to={iso}").json()
    assert body["total"] == 2, "당일 00:00:00.000000 과 23:59:59.999999 둘 다 포함"


def test_date_range_span_boundary_365_ok_366_rejected(client, db):
    """off-by-one 고정 — 양끝 포함이므로 365일째까지가 합법이다."""
    date_to = kst_today()
    ok = (date_to - timedelta(days=364)).isoformat()
    over = (date_to - timedelta(days=365)).isoformat()
    assert client.get(f"/api/naver/ad/change-log?date_from={ok}&date_to={date_to.isoformat()}").status_code == 200
    assert client.get(f"/api/naver/ad/change-log?date_from={over}&date_to={date_to.isoformat()}").status_code == 422


def test_date_range_max_date_is_422_not_500(client, db):
    """★`date(9999,12,31) + 1일`이 OverflowError를 던져 500이 났다. 뒤집힘·span 검사를 전부
    통과한 뒤 터진다(from==to면 span 1일이라 합법). input에 max가 없어 도달 가능하다."""
    r = client.get("/api/naver/ad/change-log?date_from=9999-12-31&date_to=9999-12-31")
    assert r.status_code == 422


def test_executed_total_never_counts_dry_run_as_executed(client, db):
    """★R2 자체 검증에서 발견. executed_total은 `execution_state=="executed"` 행 수와
    **정확히 같아야** 한다 — 푸터가 그 배지들의 집계라고 주장하기 때문이다.
    dry_run을 안 걸렀더니 include_dry_run=true 조합에서 푸터가 "집행 1건"이라 말하면서
    정작 그 행엔 배지가 없었다(실측). 시뮬을 집행으로 세는 건 D-47-h가 금지하는 거짓말이다."""
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-dry", campaign_id="cmp-1", action="update_bid",
        dry_run=True, changed_at=kst_now(),
        before_value=json.dumps({"bidAmt": 700}), after_value=json.dumps({"bidAmt": 900}),
    ))
    db.commit()
    body = client.get(
        "/api/naver/ad/change-log?actor=ours&include_blocked=true&include_dry_run=true"
    ).json()
    assert body["executed_total"] == 0, "dry-run은 집행이 아니다"
    # 불변식: 푸터 숫자 == 배지 개수
    badged = sum(1 for r in body["rows"] if r["execution_state"] == "executed")
    assert body["executed_total"] == badged


def test_executed_total_equals_executed_badges_invariant(client, db):
    """위 불변식을 섞인 데이터로 한 번 더 — 푸터와 배지가 갈라지면 화면이 자기모순이다."""
    _seed(db, action="update_bid")
    _seed_blocked(db, entity_id="grp-b1")
    _seed_write_failed(db)
    body = client.get("/api/naver/ad/change-log?actor=ours&include_blocked=true").json()
    badged = sum(1 for r in body["rows"] if r["execution_state"] == "executed")
    assert body["executed_total"] == badged == 1


def test_write_failure_marker_wins_over_guard_marker(client, db):
    """★검사 순서 고정. harness는 제안 rationale **뒤에** 접두사를 이어붙인다
    (f"{proposal.rationale} {MARKER} {reason}"). 제안 원문에 '[실행 불가]'가 섞여 있으면
    쓰기 실패 행이 blocked로 오판된다 — 반영됐을 수도 있는데 "확실히 안 바뀜"이라 단언하는
    P1-2의 거짓말이다. prod 실측(2026-07-17) 제안 1,023건 중 0건이라 지금은 도달 불가하지만,
    문자열 검사에 기대는 이상 순서로 막는다."""
    db.add(NaverChangeLog(
        entity_type="adgroup", entity_id="grp-both", campaign_id="cmp-1", action="update_bid",
        dry_run=False, outcome="failed", changed_at=kst_now(), before_value=None, after_value=None,
        rationale=(
            f"제안 원문에 {naver_execution_harness.GUARD_BLOCK_MARKER} 문자열이 섞여 있음 "
            f"{naver_execution_harness.WRITE_FAILURE_MARKER} WriteVerificationError: ..."
        ),
    ))
    db.commit()
    rows = client.get("/api/naver/ad/change-log?actor=ours&include_blocked=true").json()["rows"]
    assert rows[0]["execution_state"] == "unknown", "쓰기 실패는 마커가 섞여도 unknown이어야 한다"


# ── D-NAO-54 2026-07-18: 대상 사람 이름 해석(Jino "적혀있는 대상은 알아볼 수가 없어") ──
def test_change_log_resolves_entity_and_campaign_names(client, db):
    """대상 ID → naver_entity.name. adgroup='17E' + 소속 캠페인명을 함께 준다."""
    from app.models import NaverEntity
    db.add_all([
        NaverEntity(entity_type="campaign", entity_id="cmp-1", campaign_id="cmp-1",
                    name="04. 아이폰_지문방지"),
        NaverEntity(entity_type="adgroup", entity_id="grp-9", campaign_id="cmp-1",
                    name="17E"),
    ])
    row = _seed(db)
    row.entity_type = "adgroup"; row.entity_id = "grp-9"
    db.commit()
    item = client.get("/api/naver/ad/change-log").json()["rows"][0]
    assert item["entity_name"] == "17E"
    assert item["campaign_name"] == "04. 아이폰_지문방지"


def test_change_log_entity_name_is_none_when_unresolved(client, db):
    """이름을 못 찾으면 키를 지어내지 않고 None → 프론트가 'type id'로 폴백한다(원칙22)."""
    _seed(db)  # naver_entity에 nkw-1 없음
    item = client.get("/api/naver/ad/change-log").json()["rows"][0]
    assert item["entity_name"] is None
    assert item["campaign_name"] is None


def test_change_log_skips_bulk_entity_in_name_lookup(client, db):
    """__bulk__ 요약행은 실엔티티가 아니라 이름 해석 대상이 아니다(빈 IN 방지)."""
    row = _seed(db, action="external_keyword_removed")
    row.entity_id = "__bulk__"
    db.commit()
    item = client.get("/api/naver/ad/change-log").json()["rows"][0]
    assert item["entity_name"] is None
