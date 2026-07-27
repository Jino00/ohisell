# test_naver_ad_proposals_router.py — P2-S3 T7 HTTP 라운드트립(TestClient)
# GET /proposals, GET/PUT /campaign-settings. P2-S2 500 사고 재발방지 원칙(원칙22) —
# SA/harness 단위테스트는 라우터 코드를 안 거치므로 라우터 레이어 버그(미정의 상수 참조 등
# 500 유발)를 못 잡는다. 반드시 실제 HTTP 왕복으로 검증한다.
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import NaverChangeLog, NaverProposal


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


# ── GET /proposals ──
def test_proposals_endpoint_returns_200_empty(client):
    resp = client.get("/api/naver/ad/proposals")
    assert resp.status_code == 200
    # D-NAO-47: total 추가(additive — 기존 rows 소비자 불변). limit으로 자른 페이지 길이를
    # 건수로 쓰면 틀린 숫자가 되므로 서버가 전체 건수를 준다.
    # open_actions 추가(additive) — 배너의 "현재 개방" 표시가 하드코딩 대신 이 값을 쓴다.
    from app.services.naver_ad import naver_execution_harness
    assert resp.json() == {
        "total": 0,
        "open_actions": naver_execution_harness.open_executable_actions(),
        "rows": [],
    }


def test_proposals_endpoint_returns_seeded_rows(client, db):
    db.add(NaverProposal(proposal_type="bid_down", target_type="keyword", target_id="nkw-1",
                          campaign_id="cmp-1", status="pending", rationale="테스트"))
    db.commit()

    resp = client.get("/api/naver/ad/proposals")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["proposal_type"] == "bid_down"
    assert rows[0]["target_id"] == "nkw-1"


def test_proposals_endpoint_resolves_target_and_campaign_names(client, db):
    """대상 키워드ID → naver_entity.name(Jino 2026-07-18: "여기에도 대상이 알수없는게").
    키워드 텍스트 + 소속 캠페인명을 함께 준다. 미해석이면 None(프론트 폴백)."""
    from app.models import NaverEntity
    db.add_all([
        NaverEntity(entity_type="keyword", entity_id="nkw-1", campaign_id="cmp-1",
                    name="아이폰17 케이스"),
        NaverEntity(entity_type="campaign", entity_id="cmp-1", campaign_id="cmp-1",
                    name="04. 아이폰_지문방지"),
    ])
    db.add(NaverProposal(proposal_type="bid_up", target_type="keyword", target_id="nkw-1",
                          campaign_id="cmp-1", status="pending"))
    db.add(NaverProposal(proposal_type="bid_up", target_type="keyword", target_id="nkw-미동기",
                          campaign_id="cmp-1", status="pending"))
    db.commit()
    by_id = {r["target_id"]: r for r in client.get("/api/naver/ad/proposals").json()["rows"]}
    assert by_id["nkw-1"]["target_name"] == "아이폰17 케이스"
    assert by_id["nkw-1"]["campaign_name"] == "04. 아이폰_지문방지"
    assert by_id["nkw-미동기"]["target_name"] is None  # 미해석 → 지어내지 않음


def test_proposals_endpoint_filters_by_status(client, db):
    db.add(NaverProposal(proposal_type="bid_down", target_type="keyword", target_id="nkw-1",
                          campaign_id="cmp-1", status="pending"))
    db.add(NaverProposal(proposal_type="bid_up", target_type="keyword", target_id="nkw-2",
                          campaign_id="cmp-1", status="approved"))
    db.commit()

    resp = client.get("/api/naver/ad/proposals", params={"status": "approved"})
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["target_id"] == "nkw-2"


def test_proposals_endpoint_rejects_invalid_status(client):
    resp = client.get("/api/naver/ad/proposals", params={"status": "bogus"})
    assert resp.status_code == 400


def test_proposals_endpoint_rejects_inverted_date_range(client):
    resp = client.get("/api/naver/ad/proposals", params={
        "date_from": "2026-07-07", "date_to": "2026-06-23",
    })
    assert resp.status_code == 400


def test_proposals_endpoint_filters_by_campaign_id(client, db):
    db.add(NaverProposal(proposal_type="bid_down", target_type="keyword", target_id="nkw-1",
                          campaign_id="cmp-1", status="pending"))
    db.add(NaverProposal(proposal_type="bid_down", target_type="keyword", target_id="nkw-2",
                          campaign_id="cmp-2", status="pending"))
    db.commit()

    resp = client.get("/api/naver/ad/proposals", params={"campaign_id": "cmp-2"})
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["campaign_id"] == "cmp-2"


# ── GET/PUT /campaign-settings ──
def test_campaign_settings_get_returns_200_empty(client):
    resp = client.get("/api/naver/ad/campaign-settings")
    assert resp.status_code == 200
    assert resp.json() == {"rows": []}


def test_campaign_settings_put_creates_new_row(client, db):
    """★D-NAO-48(codex[P2] R2): 이 PUT은 이제 **모드·공격성 전용**이고 optimizer를 받지 않는다.
    새 행의 optimizer는 항상 'none'에서 시작한다 — 모드만 저장했는데 라이브 쓰기가 켜지면 안 된다."""
    resp = client.put("/api/naver/ad/campaign-settings", json={
        "campaign_id": "cmp-new", "mode": "growth",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["campaign_id"] == "cmp-new"
    assert body["mode"] == "growth"
    assert body["optimizer"] == "none"

    get_resp = client.get("/api/naver/ad/campaign-settings", params={"campaign_id": "cmp-new"})
    assert get_resp.json()["rows"][0]["optimizer"] == "none"


def test_campaign_settings_put_rejects_optimizer_field(client, db):
    """★쓰기 경로를 **구조적으로** 하나로 만든다(codex[P2] R2). 이전엔 optional로 두고
    "콘솔이 안 보내면 된다"고 문서로 막았는데, 그건 이 세션 내내 배격한 방식이다
    (reason은 타입으로, EXECUTION_ACTIONS는 파생으로 막았듯이). 문을 열어두면 언젠가 들어온다:
    stale 값 하나가 스위치로 끈 캠페인을 'ours'로 되돌려 확인창 없이 쓰기를 재무장시킨다.
    → 이 PUT은 optimizer를 **거부**한다. 유일 경로 = PUT /campaign-settings/optimizer."""
    r = client.put("/api/naver/ad/campaign-settings",
                   json={"campaign_id": "cmp-1", "optimizer": "ours", "mode": "growth"})
    assert r.status_code == 422


def test_campaign_settings_put_never_touches_optimizer(client, db):
    """모드를 저장해도 관리주체는 그대로 — 이 PUT은 optimizer를 건드리지 않는다."""
    from app.models import NaverCampaignSettings
    db.add(NaverCampaignSettings(campaign_id="cmp-1", optimizer="ours"))
    db.commit()

    r = client.put("/api/naver/ad/campaign-settings", json={"campaign_id": "cmp-1", "mode": "defense"})
    assert r.status_code == 200
    assert r.json()["optimizer"] == "ours"
    assert r.json()["mode"] == "defense"
    # 전이가 없으니 감사 기록도 없다
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "optimizer_change").count() == 0


def test_campaign_settings_put_memo_only_writes_no_audit(client, db):
    """memo만 바꾸면 감사 기록이 없다. (optimizer 전이 로깅은 이제 이 PUT의 책임이 아니다 —
    D-NAO-48로 전용 스위치 엔드포인트로 이관, test_naver_optimizer_switch_router.py가 커버.)"""
    client.put("/api/naver/ad/campaign-settings", json={"campaign_id": "cmp-1", "memo": "메모"})
    client.put("/api/naver/ad/campaign-settings", json={"campaign_id": "cmp-1", "memo": "메모만 변경"})
    assert db.query(NaverChangeLog).filter(NaverChangeLog.campaign_id == "cmp-1").count() == 0


def test_campaign_settings_put_rejects_invalid_mode(client):
    resp = client.put("/api/naver/ad/campaign-settings", json={
        "campaign_id": "cmp-1", "mode": "bogus",
    })
    assert resp.status_code == 400


def test_campaign_settings_put_target_roas_override(client):
    resp = client.put("/api/naver/ad/campaign-settings", json={
        "campaign_id": "cmp-1", "target_roas_override": 350.5,
    })
    assert resp.status_code == 200
    assert resp.json()["target_roas_override"] == 350.5


# ── D-NAO-47 T4: _serialize_proposal 보강 ──
def test_proposal_serializes_target_bid(client, db):
    """★"입찰 인상" 카드가 '얼마로' 올리는지 화면에 없던 결함(스펙 §1-6).
    현재 pending 실행대상 5건이 전부 bid_up이라 바로 체감되는 누락이다."""
    db.add(NaverProposal(
        proposal_type="bid_up", target_type="keyword", target_id="nkw-1",
        campaign_id="cmp-1", status="pending", target_bid=1450,
    ))
    db.commit()
    items = client.get("/api/naver/ad/proposals?status=pending").json()["rows"]
    assert items[0]["target_bid"] == 1450


def test_proposal_serializes_target_lock_and_budget(client, db):
    db.add(NaverProposal(
        proposal_type="pause", target_type="keyword", target_id="nkw-2",
        campaign_id="cmp-1", status="pending", target_lock=True, target_budget=50000,
    ))
    db.commit()
    items = client.get("/api/naver/ad/proposals?status=pending").json()["rows"]
    assert items[0]["target_lock"] is True
    assert items[0]["target_budget"] == 50000


def test_proposal_serializes_informational_flag(client, db):
    """프론트가 '나를 기다리는 것'(실행형)과 '롤업 대상'(정보성)을 가르는 기준.
    ★프론트에서 유형 문자열을 하드코딩해 재분류하면 드리프트한다 — 백엔드가 진실을 준다."""
    db.add(NaverProposal(
        proposal_type="trigger_pacing", target_type="campaign", target_id="cmp-1",
        campaign_id="cmp-1", status="pending",
    ))
    db.add(NaverProposal(
        proposal_type="bid_up", target_type="keyword", target_id="nkw-3",
        campaign_id="cmp-1", status="pending", target_bid=900,
    ))
    db.commit()
    items = client.get("/api/naver/ad/proposals?status=pending").json()["rows"]
    by_type = {i["proposal_type"]: i for i in items}
    assert by_type["trigger_pacing"]["informational"] is True
    assert by_type["bid_up"]["informational"] is False


# ── 실행 액션 파생값(action) + 개방 목록(open_actions) 드리프트 가드 ──
def test_open_actions_matches_harness_derived_value(client, db):
    """★배너의 "현재 개방" 표시는 하드코딩(구: "제외키워드")이 아니라 목록 API의 open_actions를
    쓴다. 그 값이 harness의 실제 개방 집합(이중 방벽 교집합)과 어긋나면 안 된다 — 개방 순서가
    코드로 진행돼도 배너가 자동으로 따라온다."""
    from app.services.naver_ad import naver_execution_harness
    body = client.get("/api/naver/ad/proposals").json()
    assert body["open_actions"] == naver_execution_harness.open_executable_actions()
    # 교집합의 실제 의미: OPEN_ACTIONS이면서 구현(_WRITE_EXECUTORS)이 있는 것만.
    assert set(body["open_actions"]) <= naver_execution_harness.OPEN_ACTIONS
    for a in body["open_actions"]:
        assert a in naver_execution_harness._WRITE_EXECUTORS


def test_proposal_serializes_action_for_bid_up(client, db):
    """bid_up → update_bid. 프론트가 유형 문자열로 액션을 재추론하지 않도록 백엔드가 파생값을
    준다(harness._ACTION_BY_PROPOSAL_TYPE 단일 진실)."""
    db.add(NaverProposal(
        proposal_type="bid_up", target_type="keyword", target_id="nkw-1",
        campaign_id="cmp-1", status="pending", target_bid=1450,
    ))
    db.commit()
    items = client.get("/api/naver/ad/proposals?status=pending").json()["rows"]
    assert items[0]["action"] == "update_bid"


def test_proposal_serializes_action_null_for_informational(client, db):
    """정보성 유형은 실행 대상이 없어 action이 None(매핑에 없음)."""
    from app.services.naver_ad.proposal_writer import INFORMATIONAL_PROPOSAL_TYPES
    info_type = sorted(INFORMATIONAL_PROPOSAL_TYPES)[0]
    db.add(NaverProposal(
        proposal_type=info_type, target_type="campaign", target_id="cmp-1",
        campaign_id="cmp-1", status="pending",
    ))
    db.commit()
    items = client.get("/api/naver/ad/proposals?status=pending").json()["rows"]
    assert items[0]["informational"] is True
    assert items[0]["action"] is None


def test_all_proposal_types_constant_covers_every_emitted_type():
    """★드리프트 방지: 백엔드가 새 유형을 만들면 이 상수에 반드시 추가해야 한다.
    프론트 라벨 13종은 이 상수를 진실로 삼는다(유령 라벨 재발 방지).

    ⚠️ 계획서(2026-07-17-mop-command-center-backend.md T4)의 "13종" 표는 budget_down을
    누락했다(실측: naver_execution_harness._ACTION_BY_PROPOSAL_TYPE에 budget_up과 대칭으로
    이미 존재 — D-NAO-42-f, 커밋 68d7ef5). ALL_PROPOSAL_TYPES는 이 드리프트 가드 자체가
    커버해야 할 실제 코드 상태를 진실로 삼으므로 14종으로 정정한다(Phase 2 프론트 라벨은
    별도 계획서 소관 — 여기서는 백엔드 집합의 정합성만 보장)."""
    from app.services.naver_ad.proposal_writer import (
        ALL_PROPOSAL_TYPES, INFORMATIONAL_PROPOSAL_TYPES, PARAM_CHANGE,
    )
    from app.services.naver_ad.naver_execution_harness import _ACTION_BY_PROPOSAL_TYPE

    assert INFORMATIONAL_PROPOSAL_TYPES <= ALL_PROPOSAL_TYPES
    assert set(_ACTION_BY_PROPOSAL_TYPE) <= ALL_PROPOSAL_TYPES
    # 14 + wisdom_promoted(D-NAO-54 P3 정보성) + param_change(D-NAO-54 P4 결정 전용) = 16.
    # ★IU-R R1: bid_up_servo(쇼검 폐루프 순위 서보) → 17. ★IU-R R2: bid_up_rank(파워링크
    # estimate 직행, auto_operator inline 생성) → 18. ★B-X BX2: bid_up_explore(저볼륨 그룹 탐색
    # UP, exploration 레인 inline·BX3 배선) → 19. 전부 레인 inline 전용(위임 제외).
    # param_change/wisdom_promoted 모두 실행 매핑(_ACTION_BY_PROPOSAL_TYPE)에는 절대 넣지
    # 않는다(지혜→실행 직접 쓰기 금지 금지선). wisdom_promoted는 정보성 집합에, param_change는
    # 어느 집합에도 넣지 않는다(결정 전용 — 라우터 DECISION_ONLY_PROPOSAL_TYPES가 분기).
    # ★SS3: search_term_exclude(검색어 제외, Confirm 전용·delegation 제외) → 20.
    # ★SS4: search_term_promote(검색어 승격, 정보성 — 등록 실행 매핑 없음, L3 스코프) → 21.
    # ★CS: bid_up_cold(콜드 소재 첫 입찰, cold_start_bid_lane inline·위임 영구 제외) → 22.
    assert len(ALL_PROPOSAL_TYPES) == 22
    assert PARAM_CHANGE not in INFORMATIONAL_PROPOSAL_TYPES  # 결정 전용 ≠ 정보성
    assert PARAM_CHANGE not in _ACTION_BY_PROPOSAL_TYPE  # 금지선: 실행 매핑 부재


# ── D-NAO-47 라이브 배포 검증에서 발견: 정보성이 실행형을 페이지 밖으로 밀어낸다 ──
def test_proposals_informational_filter_surfaces_actionable(client, db):
    """★prod 실측(2026-07-17 배포 중 발견): pending 107건 중 trigger_pacing 102건(07-16)이
    bid_up 5건(07-15)보다 최신이라, created_at DESC + limit=100이면 **bid_up이 한 건도
    안 나온다**. 프론트가 받은 페이지를 !informational로 걸러 "지금 결정할 제안이 없습니다"를
    렌더했다 — 5건이 기다리는데 없다고 말하는 것.
    limit만 올리는 건 임시방편이다(내일 trigger_pacing이 500건이면 또 밀려난다).
    **실행형은 실행형으로 질의한다.**"""
    db.add(NaverProposal(
        proposal_type="bid_up", target_type="adgroup", target_id="grp-old",
        campaign_id="cmp-1", status="pending", target_bid=2090,
        created_at=datetime(2026, 7, 15, 9, 0),
    ))
    for i in range(30):  # 더 최신인 정보성이 잔뜩
        db.add(NaverProposal(
            proposal_type="trigger_pacing", target_type="campaign", target_id=f"cmp-{i}",
            campaign_id="cmp-1", status="pending",
            created_at=datetime(2026, 7, 16, 9, 0),
        ))
    db.commit()

    # 순진한 방식: 최신 5건만 받으면 bid_up이 안 나온다(회귀 재현)
    naive = client.get("/api/naver/ad/proposals?status=pending&limit=5").json()["rows"]
    assert all(r["proposal_type"] == "trigger_pacing" for r in naive), "전제 재현 실패"

    # 필터: 실행형만 질의하면 페이지 위치와 무관하게 나온다
    actionable = client.get("/api/naver/ad/proposals?status=pending&informational=false&limit=5").json()["rows"]
    assert len(actionable) == 1
    assert actionable[0]["proposal_type"] == "bid_up"
    assert actionable[0]["target_bid"] == 2090

    only_info = client.get("/api/naver/ad/proposals?status=pending&informational=true&limit=100").json()["rows"]
    assert len(only_info) == 30
    assert all(r["informational"] for r in only_info)


def test_proposals_informational_filter_omitted_returns_both(client, db):
    db.add(NaverProposal(
        proposal_type="bid_up", target_type="adgroup", target_id="g1",
        campaign_id="c1", status="pending", target_bid=100,
    ))
    db.add(NaverProposal(
        proposal_type="trigger_pacing", target_type="campaign", target_id="c1",
        campaign_id="c1", status="pending",
    ))
    db.commit()
    assert len(client.get("/api/naver/ad/proposals?status=pending").json()["rows"]) == 2


def test_proposals_returns_total_independent_of_limit(client, db):
    """★limit으로 자른 페이지 길이를 건수로 쓰면 안 된다 — 서버가 total을 준다.
    (정보성 "N건 집계됨"이 limit에 따라 달라지면 그냥 틀린 숫자다.)"""
    for i in range(7):
        db.add(NaverProposal(
            proposal_type="trigger_pacing", target_type="campaign", target_id=f"c{i}",
            campaign_id="c1", status="pending",
        ))
    db.commit()
    body = client.get("/api/naver/ad/proposals?status=pending&informational=true&limit=2").json()
    assert body["total"] == 7          # 전체
    assert len(body["rows"]) == 2      # 페이지
