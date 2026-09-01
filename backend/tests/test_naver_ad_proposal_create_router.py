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
from app.models import NaverAdgroupScope, NaverCampaignSettings, NaverProposal
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


def test_human_proposable_types_is_literally_derived_not_a_hardcoded_set():
    """★`HUMAN_PROPOSABLE_TYPES`가 **파생식으로 쓰였는지**를 AST로 검사한다.

    ## 왜 값 검사로는 안 되나 (적대 리뷰 1R 변이 11이 생존한 자리)
    리뷰어가 이 상수를 **같은 값의 리터럴 집합**으로 바꿨더니 값 동등성 검사(`==`)를 하는
    테스트가 전건 초록이었다. 당연하다 — `frozenset`은 모듈 로드 시 한 번 굳고, 「어떻게
    계산됐는가」는 값에 남지 않는다. 그래서 그 위 테스트의 주석이 약속한 「리터럴로 바꾸면
    잡는다」는 실제로는 **「다른 값의 리터럴로 바꾸면 잡는다」**였다.

    ## 처방 — 이 저장소가 이미 값을 치른 그 처방
    n=62가 같은 함정(`SPECS[k].default is judge._X`가 CPython 작은정수 캐싱 탓 리터럴에도
    True)에서 **AST 검사로 전환**해 해결했다. 여기도 같다: 소스를 파싱해 이 할당의 우변이
    게이트 상수 «이름»을 참조하는지 본다. 값이 아니라 **표현식의 모양**을 재는 것이 요점이다.

    이걸 어기는 유일한 정당한 길은 이 테스트를 같이 고치는 것이고, 그때는 리뷰 diff에 남는다.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(harness))
    assign = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AnnAssign | ast.Assign)
            and any(
                getattr(t, "id", None) == "HUMAN_PROPOSABLE_TYPES"
                for t in ([node.target] if isinstance(node, ast.AnnAssign) else node.targets)
            )
        ),
        None,
    )
    assert assign is not None, "HUMAN_PROPOSABLE_TYPES 할당을 소스에서 못 찾았다"

    referenced = {n.id for n in ast.walk(assign.value) if isinstance(n, ast.Name)}
    for required in ("_AD_UP_OPEN_TYPES", "BID_DOWN_TYPES"):
        assert required in referenced, (
            f"HUMAN_PROPOSABLE_TYPES가 {required}를 참조하지 않는다 — 하드코딩되면 그 게이트가 "
            f"바뀌어도 발의 목록이 안 따라온다(참조된 이름: {sorted(referenced)})"
        )


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


@pytest.mark.parametrize("t", ["budget_up", "budget_down", "pause", "resume"])
def test_create_rejects_types_outside_the_opened_set_by_type_gate(client, db, t):
    """예산·정지/재개는 이 계약이 여는 집합 밖이다 — 발의 입구로 우회되지 않는다.

    ★★사유까지 검사하는 이유(변이 BM12·BM13이 잡은 공허성): 이 유형들은 `target_budget`·
    `target_lock`을 필요로 하는데 발의 스키마가 그 칸을 **애초에 안 받는다.** 그래서 유형
    게이트를 통째로 열어도 「필드 없음」으로 400이 나 **테스트가 초록인 채 게이트만 사라진다.**
    거부가 «유형 때문»임을 문구로 못 박아야 그 변이가 죽는다 —
    n=77의 「픽스처가 재는 대상을 안 만들었다」와 같은 종류의 공허성이다.
    """
    _settings(db)
    resp = client.post(CREATE_URL, json=_body(
        proposal_type=t, target_type="campaign", target_id="cmp-1", adgroup_id=None,
    ))
    assert resp.status_code == 400, f"{t}가 발의를 통과했다"
    assert "엔진만 발의합니다" in resp.json()["detail"], (
        f"{t}가 «유형 게이트»가 아니라 다른 이유로 거부됐다 — 게이트가 사라져도 안 잡힌다: "
        f"{resp.json()['detail']}"
    )
    assert db.query(NaverProposal).count() == 0


def test_create_rejects_out_of_scope_adgroup(client, db):
    """★스코프 밖 광고그룹(D-NAO-244)이면 **발의 단계에서** 막힌다.

    ★★왜 이 테스트가 필요한가(변이 BM6이 잡은 구멍): `real_write_blocker`의 스코프 판정은
    `approval_source is not None`일 때만 발동한다. 그래서 라우터가 판정용 `'console'`을
    안 실으면 스코프 밖 그룹이 **입구를 통과**하고, 사람이 승인한 뒤 실행에서
    ScopeGuardError로 죽는다 — 이 입구가 만들지 않으려던 죽은 카드가 정확히 그 모양이다.
    """
    _settings(db)
    # 스코프 행이 «있고» 대상 그룹이 그 안에 없다 → blocked_by_scope=True
    db.add(NaverAdgroupScope(campaign_id="cmp-1", adgroup_id="grp-허용", enabled=True))
    db.commit()

    resp = client.post(CREATE_URL, json=_body(adgroup_id="grp-스코프밖"))
    assert resp.status_code == 400
    assert "스코프 밖 광고그룹" in resp.json()["detail"], resp.json()["detail"]
    assert db.query(NaverProposal).count() == 0


def test_create_allows_in_scope_adgroup(client, db):
    """반대 방향 — 스코프 «안»이면 통과한다(위 테스트가 전부를 막는 것으로 통과하지 않게)."""
    _settings(db)
    db.add(NaverAdgroupScope(campaign_id="cmp-1", adgroup_id="grp-1", enabled=True))
    db.commit()

    resp = client.post(CREATE_URL, json=_body(adgroup_id="grp-1"))
    assert resp.status_code == 200, resp.text
    assert db.query(NaverProposal).count() == 1


def test_proposable_set_shrinks_when_an_action_closes(client, db, monkeypatch):
    """★액션이 닫히면 그 유형은 발의 목록에서 «빠진다»(이중 방벽의 발의판).

    ★★왜 필요한가(변이 BM4가 잡은 구멍): 지금은 네 유형의 액션이 전부 개방돼 있어
    `human_proposable_types()`의 개방 교집합을 **지워도 결과가 같다** — 오늘의 값만 보는
    테스트로는 그 필터가 사라진 걸 알 수 없다. 액션 하나를 닫아 봐야 필터가 실재함이 증명된다.
    """
    monkeypatch.setattr(
        harness, "OPEN_ACTIONS", harness.OPEN_ACTIONS - {"update_bid"}, raising=True,
    )
    assert "bid_up" not in harness.human_proposable_types()
    assert "bid_down" not in harness.human_proposable_types()
    assert "negative_keyword" in harness.human_proposable_types()

    # 화면 목록도 같이 줄어야 한다(백엔드 상수만 줄고 응답이 그대로면 화면이 거짓말한다).
    body = client.get(TYPES_URL).json()
    assert "bid_up" not in [r["proposal_type"] for r in body["proposable"]]

    # 그리고 그 유형의 발의는 거부된다 — 사유는 「발의 대상이나 실쓰기 미개방」.
    _settings(db)
    resp = client.post(CREATE_URL, json=_body(
        proposal_type="bid_up", target_type="keyword", target_id="nkw-9",
        adgroup_id=None, target_bid=1400,
    ))
    assert resp.status_code == 400
    assert "미개방" in resp.json()["detail"], resp.json()["detail"]


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
