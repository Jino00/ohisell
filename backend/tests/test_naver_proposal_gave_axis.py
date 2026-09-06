# test_naver_proposal_gave_axis.py — M2 T3(D-NAO-297) · 계약 §4-C S2-③ / ref 65 §6 S2-ⓒ
#
# 지키는 것 셋:
#   ① 값이 «컬럼»에 남는다 — proposal_writer.persist가 `_gave_expected_score`를 gave_score로
#      옮겨 담는다(나머지 `_gave_*` 임시키는 종전대로 버린다).
#   ② 값이 «API 응답»에 뜬다 — 서비스층이 아니라 HTTP body에서 본다(교훈 #321: response_model이
#      키를 지우는 전례가 있어 서비스층 확인은 증거가 아니다).
#   ③ 값이 «정렬 축»이 된다 — sort=gave_score가 실제로 순서를 바꾸고, 미채점(NULL)은 뒤로 간다.
#
# ★표면 절단 변이 대비(계약 §4-C 공통): 직렬화 한 줄이나 order_by 분기를 지우면
#   ②·③이 죽어야 한다. 그래서 «키 존재»와 «순서»를 각각 따로 단언한다.
from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import NaverProposal
from app.services.naver_ad import proposal_writer


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


def _candidate(target_id: str, **extra) -> dict:
    base = {
        "proposal_type": "bid_up",
        "target_type": "keyword",
        "target_id": target_id,
        "campaign_id": "cmp-1",
        "rationale": "테스트",
        "status": "pending",
    }
    base.update(extra)
    return base


# ── ① 컬럼 적재 ────────────────────────────────────────────────────────────
def test_persist_moves_gave_expected_score_into_column(client_and_session):
    _, db = client_and_session
    saved = proposal_writer.persist(db, [
        _candidate(
            "kw-scored",
            _gave_expected_score=Decimal("143327.5200"),
            _gave_board="shopping_group_growth",   # 다른 임시키는 버려져야 한다
            _gave_revenue=Decimal("1000"),
        ),
    ])
    db.commit()
    assert len(saved) == 1
    assert saved[0].gave_score == Decimal("143327.5200")


def test_persist_leaves_null_when_board_is_not_gave_scored(client_and_session):
    """GAVE 채점 보드가 아닌 후보는 NULL로 남는다 — 0으로 채우지 않는다.

    0.0000은 «실제로 나오는 점수»(무전환 스톱로스 후보)라, 미채점을 0으로 적으면
    두 상태가 같은 값이 되어 정렬에서 구분이 사라진다."""
    _, db = client_and_session
    saved = proposal_writer.persist(db, [_candidate("kw-unscored")])
    db.commit()
    assert saved[0].gave_score is None


def test_persist_still_strips_gave_temp_keys(client_and_session):
    """임시키가 ORM으로 새어 들어가면 TypeError로 죽는다 — 종전 방어가 살아 있는지."""
    _, db = client_and_session
    saved = proposal_writer.persist(db, [
        _candidate("kw-x", _gave_board="b", _gave_revenue=Decimal("1"), _gave_cost=Decimal("1")),
    ])
    db.commit()
    assert saved[0].target_id == "kw-x"


# ── ② API 노출 ─────────────────────────────────────────────────────────────
def test_proposals_response_exposes_gave_score_field(client_and_session):
    client, db = client_and_session
    db.add(NaverProposal(
        proposal_type="bid_up", target_type="keyword", target_id="kw-1",
        campaign_id="cmp-1", status="pending", gave_score=Decimal("21409.3500"),
    ))
    db.commit()

    r = client.get("/api/naver/ad/proposals")
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 1
    # ★HTTP body에 «키»가 있어야 한다 — 값만 보면 키가 사라진 것을 못 잡는다.
    assert "gave_score" in rows[0]
    assert rows[0]["gave_score"] == pytest.approx(21409.35)


def test_null_gave_score_serializes_as_none_not_zero(client_and_session):
    client, db = client_and_session
    db.add(NaverProposal(
        proposal_type="bid_up", target_type="keyword", target_id="kw-1",
        campaign_id="cmp-1", status="pending",
    ))
    db.commit()

    rows = client.get("/api/naver/ad/proposals").json()["rows"]
    assert "gave_score" in rows[0]
    assert rows[0]["gave_score"] is None


# ── ③ 정렬 축 ──────────────────────────────────────────────────────────────
def _seed_three(db):
    # created_at은 server_default(UTC now)라 셋이 사실상 동시 — 최신순 정렬로는
    # gave_score 순서를 흉내낼 수 없다. 그래서 이 표본이 «정렬 축이 실제로 쓰였나»를 가른다.
    db.add_all([
        NaverProposal(proposal_type="bid_up", target_type="keyword", target_id="low",
                      campaign_id="c", status="pending", gave_score=Decimal("10.0000")),
        NaverProposal(proposal_type="bid_up", target_type="keyword", target_id="none",
                      campaign_id="c", status="pending", gave_score=None),
        NaverProposal(proposal_type="bid_up", target_type="keyword", target_id="high",
                      campaign_id="c", status="pending", gave_score=Decimal("999.0000")),
    ])
    db.commit()


def test_sort_by_gave_score_orders_desc_with_nulls_last(client_and_session):
    client, db = client_and_session
    _seed_three(db)

    rows = client.get("/api/naver/ad/proposals?sort=gave_score").json()["rows"]
    assert [r["target_id"] for r in rows] == ["high", "low", "none"]


def test_default_sort_is_unchanged(client_and_session):
    """기본값은 종전 그대로 — 기존 소비자가 이 슬라이스로 순서가 바뀌면 안 된다."""
    client, db = client_and_session
    _seed_three(db)

    rows = client.get("/api/naver/ad/proposals").json()["rows"]
    # created_at DESC(동시각이면 삽입 역순이 보장되진 않으므로) — GAVE 순서가 «아님»만 단언한다.
    assert [r["target_id"] for r in rows] != ["high", "low", "none"]


def test_unknown_sort_is_rejected(client_and_session):
    client, _ = client_and_session
    r = client.get("/api/naver/ad/proposals?sort=rationale")
    assert r.status_code == 400
    assert "sort" in r.json()["detail"]
