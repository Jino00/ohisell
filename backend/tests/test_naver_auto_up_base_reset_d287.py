# test_naver_auto_up_base_reset_d287.py — D-NAO-287 「상한리셋 목표」
# 계약: docs/contracts/CONTRACT_auto_up_base_reset.md
#
# ★이 파일이 고정하는 것은 셋이다(계약 §4-D 변이 테스트):
#   ① 리셋 엔드포인트가 실재하고 감사 행을 남긴다 (지우면 빨개진다)
#   ② 그 행이 `auto_up_base_bid()`의 기준점을 **실제로 옮긴다** — 하네스의 인식 분기를
#      지우면 빨개진다. 이게 없으면 입구는 «API·화면·기록까지만 살고 기준점은 안 움직인다».
#   ③ 그 행이 **입찰 변경으로 세어지지 않는다** — 쿨다운·일일 하향 캡 카운터 불변.
#      `update_bid` 모양으로 되돌리면 빨개진다(계약 §2-1).
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import KNOWN_CHANGE_LOG_ACTIONS, NaverChangeLog, NaverProposal
from app.services.naver_ad import naver_execution_harness as harness
from app.services.naver_ad import naver_sa_writer
from app.utils.kst import kst_now

AD = "nad-a001-02-000000558104404"
CAMP = "cmp-a001-02-000000010164717"
RESET_URL = "/api/naver/ad/auto-up-base/reset"
CEILING_URL = "/api/naver/ad/auto-up-ceiling"


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
    seed = TestingSession()
    yield TestClient(app), seed
    seed.close()
    app.dependency_overrides.clear()


@pytest.fixture
def client(client_and_session):
    return client_and_session[0]


@pytest.fixture
def db(client_and_session):
    return client_and_session[1]


def _auto_up(db, before: int, after: int, at: datetime, *, ad: str = AD) -> None:
    """무인(시간당 레인) 자동 상향 1건 — 기준점 ②(자동화가 출발한 값)의 재료."""
    pr = NaverProposal(proposal_type="bid_up", target_type="ad", target_id=ad,
                       campaign_id=CAMP, status="approved", approval_source="auto_op_hr")
    db.add(pr)
    db.flush()
    db.add(NaverChangeLog(
        entity_type="ad", entity_id=ad, campaign_id=CAMP, action="update_bid",
        dry_run=False, changed_at=at, executed_at=at, proposal_id=pr.id,
        before_value=json.dumps({"adAttr": {"bidAmt": before, "useGroupBidAmt": False}}),
        after_value=json.dumps({"adAttr": {"bidAmt": after, "useGroupBidAmt": False}}),
    ))
    db.commit()


def _live_bid(monkeypatch, value):
    def _fake(ncc_ad_id: str):
        if callable(value):
            return value(ncc_ad_id)
        return value
    monkeypatch.setattr(naver_sa_writer, "get_ad_bid", _fake)


# ── ① 입구가 실재하고 사유를 요구한다 ────────────────────────────────
def test_reset_rejects_blank_reason(client, db, monkeypatch):
    """사유 없는 리셋은 400 — 이 입구의 존재 이유가 감사 기록이다."""
    _live_bid(monkeypatch, 1870)
    assert client.post(RESET_URL, json={"entity_id": AD, "reason": "   "}).status_code == 400
    assert client.post(RESET_URL, json={"entity_id": "  ", "reason": "왜"}).status_code == 400
    assert db.query(NaverChangeLog).count() == 0  # 거부는 행을 남기지 않는다


def test_reset_refuses_when_live_bid_unknown(client, monkeypatch):
    """라이브 조회가 실패하거나 비면 **추측하지 않고** 502 — 틀린 기준점은 상한을 조용히 푼다."""
    def _boom(_):
        raise RuntimeError("네이버 응답 없음")
    monkeypatch.setattr(naver_sa_writer, "get_ad_bid", _boom)
    assert client.post(RESET_URL, json={"entity_id": AD, "reason": "복귀"}).status_code == 502

    _live_bid(monkeypatch, None)
    assert client.post(RESET_URL, json={"entity_id": AD, "reason": "복귀"}).status_code == 502


def test_reset_appends_audit_row_with_actor_time_and_reason(client, db, monkeypatch):
    """감사 행에 **주체·시각·사유**가 셋 다 들어간다(계약 §4-B)."""
    _live_bid(monkeypatch, 1870)
    before = kst_now()
    r = client.post(RESET_URL, json={"entity_id": AD, "reason": "09-01 편입분 복귀", "actor": "Jino"})
    assert r.status_code == 200, r.text

    rows = db.query(NaverChangeLog).filter(NaverChangeLog.action == "reset_auto_up_base").all()
    assert len(rows) == 1
    row = rows[0]
    assert row.entity_type == "ad" and row.entity_id == AD
    assert row.dry_run is False
    assert row.proposal_id is None            # 사람 쓰기다 — 제안에 매달지 않는다
    assert "Jino" in (row.rationale or "")    # 주체
    assert "09-01 편입분 복귀" in (row.rationale or "")  # 사유
    # 시각 — kst_now()를 명시 전달했으므로 UTC(server_default)로 박히지 않는다
    assert row.changed_at is not None
    assert abs((row.changed_at - before).total_seconds()) < 120
    assert json.loads(row.after_value)["bidAmt"] == 1870


def test_reset_action_is_registered_in_known_actions():
    """새 action은 KNOWN_CHANGE_LOG_ACTIONS에 등록돼 있어야 한다 — 그게 「지어낸 이름」과
    「새로 만든 이름」을 가르는 이 저장소의 유일한 신호다(models.py B-1 가드)."""
    assert "reset_auto_up_base" in KNOWN_CHANGE_LOG_ACTIONS


# ── ② 리셋이 기준점을 실제로 옮긴다 (하네스 인식 분기 변이) ──────────
def test_reset_row_moves_the_anchor(client, db, monkeypatch):
    """★변이 표적: `_HUMAN_ANCHOR_ACTIONS`에서 reset을 빼면 이 테스트가 죽는다.

    그 분기가 없으면 입구는 API·화면·기록까지만 살고 **기준점은 그대로**다 —
    이 저장소가 반복해 데인 「라벨은 붙고 집행은 안 됨」의 정확한 재현이 된다.
    """
    now = kst_now()
    _auto_up(db, 1240, 1420, now - timedelta(hours=5))
    _auto_up(db, 1420, 1870, now - timedelta(hours=3))
    assert harness.auto_up_base_bid(db, "ad", AD, now) == 1240  # 자동화가 출발한 값

    _live_bid(monkeypatch, 1870)
    r = client.post(RESET_URL, json={"entity_id": AD, "reason": "사람이 1870을 받아들인다"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["base_before"] == 1240
    assert body["base_after"] == 1870
    assert body["ceiling_before"] == 2480 and body["ceiling_after"] == 3740

    db.expire_all()
    assert harness.auto_up_base_bid(db, "ad", AD, kst_now()) == 1870


# ── ③ 리셋의 부작용은 «숨기지 않고 응답에 싣는다» ─────────────────────
def test_reset_arms_the_cooldown_and_says_so(client, db, monkeypatch):
    """★2026-09-05 실측이 계약 초판 근거를 정정한 자리 — 이 테스트가 그 사실을 고정한다.

    초판은 「별도 action이면 쿨다운에 안 잡힌다」고 적었으나 `compute_change_cadence`는
    `dry_run=False ∧ after_value IS NOT NULL`만 보고 **action을 보지 않는다**(그 docstring
    원문: "액션 유형 무관"). 기준점은 after_value에 담아야 `auto_up_base_bid`가 읽으므로
    **행을 남기는 한 쿨다운은 반드시 걸린다.** 회피가 불가능하면 남는 규율은 하나다 —
    **응답이 그 사실을 말한다.** 화면이 이걸 안 말하면 사람은 「리셋했는데 왜 안 올라가지」를
    다시 상한 탓으로 오독한다.
    """
    now = kst_now()
    _auto_up(db, 1240, 1420, now - timedelta(hours=6))
    before_last_change, before_count = harness.compute_change_cadence(db, "ad", AD, now)

    _live_bid(monkeypatch, 1420)
    r = client.post(RESET_URL, json={"entity_id": AD, "reason": "부작용 표면화 확인"})
    assert r.status_code == 200, r.text

    db.expire_all()
    after_last_change, after_count = harness.compute_change_cadence(db, "ad", AD, kst_now())
    assert after_last_change > before_last_change   # 쿨다운 시계가 새로 걸렸다
    assert after_count == before_count + 1          # 일일 변경 건수도 하나 먹었다

    # ★그리고 응답이 그 둘을 말한다 — 이 필드를 지우면 화면이 조용히 거짓말한다.
    side = r.json()["side_effect"]
    assert side["cooldown_hours"] == 2
    assert side["changes_today"] == after_count


def test_reset_does_not_count_as_an_auto_bid_down(client, db, monkeypatch):
    """자동 하향 일일 상한은 제안 조인으로 세므로 `proposal_id=NULL`인 리셋 행을 안 센다.

    ★적대 리뷰 1R P2 정정: 이 불변을 지키는 것은 **action 이름이 아니라 `proposal_id`
    부재**다(리뷰어가 재현: action을 `update_bid`로 위장해도 이 테스트는 살아남는다).
    초판 독스트링은 「action을 위장하면 죽는다」고 과장했다 — 실제 변이 표적은 리셋 행에
    제안을 매다는 것이고, 그러면 사람의 기준점 리셋이 그날의 **손실 하향 여력**을 먹는다.
    """
    now = kst_now()
    _auto_up(db, 1240, 1420, now - timedelta(hours=6))
    before_downs = harness.count_auto_bid_down_today(db, "ad", AD, now)

    _live_bid(monkeypatch, 1420)
    assert client.post(RESET_URL, json={"entity_id": AD, "reason": "하향 상한 불변 확인"}).status_code == 200

    db.expire_all()
    assert harness.count_auto_bid_down_today(db, "ad", AD, kst_now()) == before_downs


# ── 현황판: 0건을 0건이라고 말한다 ───────────────────────────────────
def test_ceiling_board_says_zero_when_nothing_is_capped(client, db):
    """빈 표는 「닿은 게 없다」와 「안 재고 있다」를 같은 그림으로 만든다(교훈 #123).
    그래서 판은 «센 대상 수»와 «닿은 수»를 따로 말한다."""
    now = kst_now()
    _auto_up(db, 1240, 1420, now - timedelta(hours=2))

    body = client.get(CEILING_URL).json()
    assert body["counted"] == 1
    assert body["cap_applies_count"] == 1
    assert body["capped_count"] == 0          # ★0건임을 «숫자로» 말한다
    assert body["multiple"] == pytest.approx(2.0)
    row = body["rows"][0]
    assert row["entity_id"] == AD
    assert row["base_bid"] == 1240 and row["ceiling"] == 2480
    assert row["current_bid"] == 1420 and row["current_bid_source"] == "last_known"
    assert row["capped"] is False
    assert row["headroom_pct"] == pytest.approx(74.6, abs=0.2)


def test_ceiling_board_flags_a_capped_row(client, db):
    """천장에 닿은 소재는 `capped`로 뜬다 — 이 행에만 화면이 리셋 버튼을 낸다."""
    now = kst_now()
    _auto_up(db, 1000, 1500, now - timedelta(hours=4))
    _auto_up(db, 1500, 2100, now - timedelta(hours=2))  # 1000×2.0=2000 천장을 넘긴 상태

    body = client.get(CEILING_URL).json()
    assert body["capped_count"] == 1
    row = body["rows"][0]
    assert row["base_bid"] == 1000 and row["ceiling"] == 2000 and row["current_bid"] == 2100
    assert row["capped"] is True


def test_ceiling_board_capped_is_inclusive_at_the_ceiling(client, db):
    """★적대 리뷰 1R P2 채택: 천장에 **정확히 닿은** 값도 상한 도달이다.

    초판 테스트는 current 2,100 > ceiling 2,000만 써서 `>=`를 `>`로 바꿔도 10/10 초록이었다
    (리뷰어 변이 #11 생존). 경계값 한 칸이 판정을 뒤집는 자리는 테스트가 눌러 둔다.
    """
    now = kst_now()
    _auto_up(db, 1000, 1500, now - timedelta(hours=4))
    _auto_up(db, 1500, 2000, now - timedelta(hours=2))  # 1000×2.0 = 2000 — 정확히 천장

    body = client.get(CEILING_URL).json()
    assert body["rows"][0]["current_bid"] == 2000 and body["rows"][0]["ceiling"] == 2000
    assert body["rows"][0]["capped"] is True
    assert body["capped_count"] == 1


def test_ceiling_board_marks_cap_not_applicable_when_no_anchor(client, db):
    """기준점이 없으면 상한이 «적용되지 않는다» — 「여력 무한」이 아니라 «게이트 밖»이다."""
    now = kst_now()
    pr = NaverProposal(proposal_type="bid_up_cold", target_type="ad", target_id=AD,
                       campaign_id=CAMP, status="approved", approval_source="cold_op")
    db.add(pr)
    db.flush()
    db.add(NaverChangeLog(
        entity_type="ad", entity_id=AD, campaign_id=CAMP, action="update_bid",
        dry_run=False, changed_at=now, executed_at=now, proposal_id=pr.id,
        before_value=json.dumps({"adAttr": {"bidAmt": 300, "useGroupBidAmt": False}}),
        after_value=json.dumps({"adAttr": {"bidAmt": 900, "useGroupBidAmt": False}}),
    ))
    db.commit()

    body = client.get(CEILING_URL).json()
    assert body["counted"] == 1
    assert body["cap_applies_count"] == 0
    assert body["capped_count"] == 0
    row = body["rows"][0]
    assert row["cap_applies"] is False and row["base_bid"] is None and row["ceiling"] is None
