# test_naver_ad_proposal_create_router.py — D-NAO-283(계약 P2-ⓒ · H2 사람 발의 입구)
# POST /proposals(발의) · GET /proposals/proposable-types(폼 목록) HTTP 라운드트립.
#
# ★이 파일이 지키려는 것은 «값»이 아니라 «층 사이의 계약»이다(교훈 #380). 세 배선을 명시로
#   고정한다: ①발의 유형 판정이 실행기 게이트에서 «파생»된다(하드코딩 목록이면 죽는 테스트)
#   ②구조 검증이 실행기 real_write_blocker를 «재사용»한다(복제하면 죽는 테스트)
#   ③엔진 전용 사유가 응답에 «실려 나간다»(백엔드 상수만 보면 통과하는 테스트가 아니다).
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import NaverCampaignSettings, NaverProposal
from app.services.naver_ad import naver_execution_harness as harness
from app.services.naver_ad.bid_step_types import (
    CHANGE_PCT_EXEMPT_TYPES,
    COLD_START_STEP_TYPES,
    EXPLORATION_STEP_TYPES,
    RANK_STEP_TYPES,
)

CREATE_URL = "/api/naver/ad/proposals"
TYPES_URL = "/api/naver/ad/proposals/proposable-types"


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


def _settings(db, campaign_id="cmp-1", optimizer="ours"):
    db.add(NaverCampaignSettings(campaign_id=campaign_id, optimizer=optimizer))
    db.commit()


def _body(**kw):
    base = {
        "proposal_type": "negative_keyword",
        "target_type": "search_term",
        "target_id": "무관검색어",
        "campaign_id": "cmp-1",
        "adgroup_id": "grp-1",
        "rationale": "전환 0 · 비용 12,000원 — 사람 판단으로 제외 발의",
    }
    base.update(kw)
    return base


# ══════════════════════════════════════════════════════════════════
# ① 유형 레지스트리는 «파생»이다 — 하드코딩하면 이 불변식이 깨진다
# ══════════════════════════════════════════════════════════════════

def test_human_proposable_never_includes_clamp_exempt_or_lane_only_types():
    """사람 발의 유형은 **봉투 면제·레인 폐루프 전용 유형과 서로소**여야 한다.

    ★근거: 면제 타입의 유일한 브레이크는 «레인이 산정한 상한»이고, 사람 발의엔 그 산정이
    없다. `_AD_UP_OPEN_TYPES`의 기존 불변식(300원→90,000원 적대 리뷰 재현)과 같은 이유이며,
    이 집합이 그 상수에서 파생되므로 같은 보호를 물려받아야 한다.
    """
    proposable = set(harness.HUMAN_PROPOSABLE_TYPES)
    for forbidden, name in [
        (CHANGE_PCT_EXEMPT_TYPES, "CHANGE_PCT_EXEMPT_TYPES"),
        (EXPLORATION_STEP_TYPES, "EXPLORATION_STEP_TYPES"),
        (COLD_START_STEP_TYPES, "COLD_START_STEP_TYPES"),
        (RANK_STEP_TYPES, "RANK_STEP_TYPES"),
    ]:
        assert not (proposable & forbidden), f"사람 발의 유형이 {name}와 겹친다: {proposable & forbidden}"


def test_human_proposable_up_is_exactly_the_approval_source_agnostic_up_set():
    """UP 쪽은 `_AD_UP_OPEN_TYPES`에서 파생된다 — 별도 목록을 손으로 적으면 갈라진다.

    ★이 테스트가 잡는 변이: `HUMAN_PROPOSABLE_TYPES`를 리터럴 집합으로 바꾸는 것. 그러면
    `_AD_UP_OPEN_TYPES`가 바뀌어도 여기가 안 따라오고, 승인원 쌍방향 잠금에 걸리는 UP이
    발의 목록에 남는다.
    """
    from app.services.naver_ad.bid_step_types import BID_UP_TYPES

    proposable_up = {t for t in harness.HUMAN_PROPOSABLE_TYPES if t in BID_UP_TYPES}
    assert proposable_up == set(harness._AD_UP_OPEN_TYPES)


def test_human_proposable_types_are_all_actually_executable():
    """발의 가능 유형은 전부 «실쓰기 액션이 개방된» 유형이어야 한다(이중 방벽의 발의판).
    아니면 발의하는 순간 죽은 카드가 된다."""
    open_actions = set(harness.open_executable_actions())
    for t in harness.human_proposable_types():
        assert harness._ACTION_BY_PROPOSAL_TYPE.get(t) in open_actions, f"{t}의 액션이 미개방"


# ══════════════════════════════════════════════════════════════════
# ② GET /proposals/proposable-types — 사유가 «응답에 실려 나간다»
# ══════════════════════════════════════════════════════════════════

def test_proposable_types_endpoint_lists_open_types(client):
    body = client.get(TYPES_URL).json()
    assert [r["proposal_type"] for r in body["proposable"]] == harness.human_proposable_types()
    # 방향 파생이 실려 있어야 폼이 「올림/내림」을 스스로 추론하지 않는다.
    by_type = {r["proposal_type"]: r for r in body["proposable"]}
    assert by_type["bid_up"]["direction"] == "up"
    assert by_type["bid_down"]["direction"] == "down"
    assert by_type["negative_keyword"]["action"] == "add_negative_keyword"


def test_proposable_types_endpoint_carries_engine_only_reasons(client):
    """★배선 절단 변이 대상: 엔진 전용 목록과 «사유»가 응답에 실려야 한다.

    사유를 응답에서 빼면 화면은 「그 유형이 없다」고만 말한다 — 계약 §3 P2 ★v9가 명시로
    금지한 조용한 실패다. 상수(`human_proposal_blocker`)만 검사하는 테스트는 이 변이를
    원리적으로 못 잡는다(교훈 #380 — 두 층 다 초록인데 둘을 잇는 한 줄이 비어 있던 자리).
    """
    body = client.get(TYPES_URL).json()
    engine_only = {r["proposal_type"]: r["reason"] for r in body["engine_only"]}

    assert "bid_up_explore" in engine_only, "탐색 스텝이 엔진 전용 목록에 없다"
    assert "bid_up_cold" in engine_only
    for t, reason in engine_only.items():
        assert reason, f"{t}의 사유가 비어 있다 — 화면이 「왜 없는지」를 못 말한다"
        assert "엔진만 발의합니다" in reason
    # 열린 유형은 엔진 전용 목록에 있으면 안 된다(두 목록이 서로소).
    assert not (set(engine_only) & set(harness.human_proposable_types()))


# ══════════════════════════════════════════════════════════════════
# ③ POST /proposals — 발의
# ══════════════════════════════════════════════════════════════════

def test_create_negative_keyword_proposal_lands_as_pending(client, db):
    _settings(db)
    resp = client.post(CREATE_URL, json=_body())
    assert resp.status_code == 200, resp.text
    row = resp.json()

    assert row["status"] == "pending"
    assert row["proposal_type"] == "negative_keyword"
    assert row["executable"] is True and row["not_executable_reason"] is None
    # 발의 주체가 근거에 남는다(감사) — 그러나 «승인» 출처는 아직 없다.
    assert row["rationale"].startswith("[사람 발의: console]")

    saved = db.query(NaverProposal).one()
    assert saved.status == "pending"
    assert saved.approval_source is None, "발의는 승인이 아니다 — approval_source가 채워졌다"


def test_create_records_proposed_by(client, db):
    _settings(db)
    row = client.post(CREATE_URL, json=_body(proposed_by="jino")).json()
    assert row["rationale"].startswith("[사람 발의: jino]")


def test_create_bid_up_carries_target_bid(client, db):
    _settings(db)
    row = client.post(CREATE_URL, json=_body(
        proposal_type="bid_up", target_type="keyword", target_id="nkw-9",
        adgroup_id=None, target_bid=1400,
    )).json()
    assert row["target_bid"] == 1400
    assert row["action"] == "update_bid"


# ── 거부 경로 ────────────────────────────────────────────────────

def test_create_rejects_engine_only_type_with_reason(client, db):
    """탐색 UP은 사람이 발의할 수 없다 — 400 + 사유."""
    _settings(db)
    resp = client.post(CREATE_URL, json=_body(
        proposal_type="bid_up_explore", target_type="adgroup", target_id="grp-1", target_bid=9999,
    ))
    assert resp.status_code == 400
    assert "엔진만 발의합니다" in resp.json()["detail"]
    assert db.query(NaverProposal).count() == 0


@pytest.mark.parametrize("engine_only_type", sorted(
    EXPLORATION_STEP_TYPES | COLD_START_STEP_TYPES | RANK_STEP_TYPES | CHANGE_PCT_EXEMPT_TYPES
))
def test_create_rejects_every_lane_only_type(client, db, engine_only_type):
    """레인 전용 유형은 **전건** 거부된다 — 한 유형만 막고 나머지가 새는 부분등록 차단."""
    _settings(db)
    resp = client.post(CREATE_URL, json=_body(
        proposal_type=engine_only_type, target_type="ad", target_id="ad-1", target_bid=5000,
    ))
    assert resp.status_code == 400, f"{engine_only_type}이 발의를 통과했다"
    assert db.query(NaverProposal).count() == 0


def test_create_rejects_budget_types(client, db):
    """예산 변경 개방은 계약 §1 「안 하는 것」 — 발의 입구로 우회되지 않는다."""
    _settings(db)
    for t in ("budget_up", "budget_down"):
        resp = client.post(CREATE_URL, json=_body(
            proposal_type=t, target_type="campaign", target_id="cmp-1", adgroup_id=None,
        ))
        assert resp.status_code == 400, f"{t}가 발의를 통과했다"
    assert db.query(NaverProposal).count() == 0


def test_create_rejects_non_ours_campaign(client, db):
    """D-NAO-13 — optimizer!='ours' 캠페인엔 발의 자체를 막는다(입구·실행 같은 답)."""
    _settings(db, optimizer="none")
    resp = client.post(CREATE_URL, json=_body())
    assert resp.status_code == 409
    assert "D-NAO-13" in resp.json()["detail"]
    assert db.query(NaverProposal).count() == 0


def test_create_rejects_unknown_campaign(client, db):
    """settings 행이 아예 없으면 optimizer는 'none'으로 해석된다(fail-closed)."""
    resp = client.post(CREATE_URL, json=_body(campaign_id="cmp-없음"))
    assert resp.status_code == 409
    assert db.query(NaverProposal).count() == 0


def test_create_rejects_structurally_unexecutable_with_executor_reason(client, db):
    """★구조 검증은 실행기 판정을 «재사용»한다 — 사유 문구가 실행기에서 나와야 한다.

    negative_keyword인데 adgroup_id가 없으면 `real_write_blocker`가 거부한다. 라우터가
    필수 필드 목록을 «자기 손으로» 다시 적으면 이 사유는 라우터 문구로 바뀌고, 실행기가
    조건을 바꿔도 입구가 안 따라온다(죽은 카드 133건의 구조).
    """
    _settings(db)
    resp = client.post(CREATE_URL, json=_body(adgroup_id=None))
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "adgroup_id 없음" in detail, f"실행기 사유가 그대로 안 실렸다: {detail}"
    assert db.query(NaverProposal).count() == 0


def test_create_rejects_wrong_target_type_for_negative_keyword(client, db):
    _settings(db)
    resp = client.post(CREATE_URL, json=_body(target_type="keyword"))
    assert resp.status_code == 400
    assert "search_term" in resp.json()["detail"]


def test_create_rejects_bid_proposal_without_target_bid(client, db):
    _settings(db)
    resp = client.post(CREATE_URL, json=_body(
        proposal_type="bid_up", target_type="keyword", target_id="nkw-9", adgroup_id=None,
    ))
    assert resp.status_code == 400
    assert "target_bid 없음" in resp.json()["detail"]


def test_create_requires_rationale(client, db):
    """근거 없는 발의는 스키마에서 막힌다(422) — 학습 사슬의 유령 방지."""
    _settings(db)
    body = _body()
    del body["rationale"]
    assert client.post(CREATE_URL, json=body).status_code == 422


# ══════════════════════════════════════════════════════════════════
# ④ 발의 → 승인 → (실행 직전까지) 기존 경로를 그대로 탄다
# ══════════════════════════════════════════════════════════════════

def test_created_proposal_flows_into_existing_approval_path(client, db):
    """발의한 카드가 기존 승인 라우터로 approved가 되고 approval_source='console'이 박힌다.
    ★새 승인 경로를 만들지 않았음의 증명(계약 §3 P2 — 「이후 기존 승인→실행 경로」)."""
    _settings(db)
    created = client.post(CREATE_URL, json=_body()).json()

    resp = client.post(f"{CREATE_URL}/{created['id']}/status", json={"status": "approved"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved"

    db.expire_all()
    saved = db.query(NaverProposal).one()
    assert saved.status == "approved"
    assert saved.approval_source == "console"


def test_created_proposal_appears_in_proposal_list(client, db):
    """★배선 절단 변이 대상: 발의한 카드가 «목록 화면»에 뜬다. 생성만 되고 목록에 안 뜨면
    사람은 자기가 만든 것을 승인할 수 없다."""
    _settings(db)
    created = client.post(CREATE_URL, json=_body()).json()

    rows = client.get("/api/naver/ad/proposals", params={"informational": False}).json()["rows"]
    assert [r["id"] for r in rows] == [created["id"]]
    assert rows[0]["executable"] is True
