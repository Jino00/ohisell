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
#
# ★★적대 리뷰 1R P1-1이 드러낸 것: 초판 테스트는 `persist`에 **손으로 만든 dict**를 넣어
#   «파이프라인 → persist» 이음매를 건너뛰었다. 그 이음매에 `finally`가 임시키를 지우는
#   단계가 있어서 실제 경로에선 컬럼이 **영구 NULL**이 되는데도 8종이 전부 초록이었다.
#   ⇒ 아래 `test_seam_*`가 그 이음매를 실제로 밟는다. **층을 건너뛴 테스트는 증거가 아니다.**
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
from app.services.naver_ad import proposal_pipeline, proposal_writer


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


# ── ① 컬럼 적재 (★이음매를 실제로 밟는다 — 1R P1-1의 자리) ──────────────────
def _apply_then_persist(db, candidates):
    """실제 prod 경로와 같은 순서: _apply_gave_priority → persist.

    ★이 순서가 핵심이다 — 전자의 `finally`가 `_gave_*` 임시키를 지운 «뒤»에 후자가 돈다.
    영속되는 값은 접두사 없는 실제 컬럼 `gave_score`여야만 살아남는다."""
    proposal_pipeline._apply_gave_priority(db, {"account_bep_roas": "1.711"}, candidates)
    saved = proposal_writer.persist(db, candidates)
    db.commit()
    return saved


def test_seam_pipeline_scoring_survives_into_column(client_and_session):
    _, db = client_and_session
    saved = _apply_then_persist(db, [
        _candidate("kw-scored", _gave_board="shopping_group_growth",
                   _gave_revenue=Decimal("100000"), _gave_cost=Decimal("10000")),
    ])
    assert len(saved) == 1
    # 점수가 rationale «에도» 실리고(기존 동작) 컬럼«에도» 남는다(이 슬라이스).
    assert "[GAVE사전: 기대점수" in saved[0].rationale
    assert saved[0].gave_score is not None, "이음매가 끊기면 여기가 None이 된다(1R P1-1)"
    assert saved[0].gave_score > 0


def test_seam_zero_score_persists_as_zero_not_null(client_and_session):
    """★변이 M5의 자리 — `0.0000`은 실제로 나오는 점수(무전환 방어 보드: revenue=0)다.

    truthy 검사로 거르면 0이 NULL로 떨어져 «미채점»과 구분이 사라진다."""
    _, db = client_and_session
    saved = _apply_then_persist(db, [
        _candidate("kw-zero", _gave_board="shopping_pause_candidates",
                   _gave_revenue=Decimal("0"), _gave_cost=Decimal("16079")),
    ])
    assert saved[0].gave_score is not None, "0점이 NULL로 떨어지면 미채점과 구분이 사라진다"
    assert saved[0].gave_score == Decimal("0.0000")


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


# ── ④ 정렬 «절»의 구조 (★SQLite에선 결과로 못 잡는 자리) ─────────────────────
def test_order_by_puts_nulls_last_structurally():
    """NULL을 뒤로 보내는 첫 키를 지워도 SQLite는 우연히 통과한다(DESC에서 NULL이 끝).
    PostgreSQL은 DESC 기본이 NULLS FIRST라 같은 코드가 거기선 미채점을 맨 앞에 세운다.
    ⇒ 순서 «결과»가 아니라 절의 «구조»를 단언한다."""
    from app.routers.naver_ad import _proposal_order_by

    order = _proposal_order_by("gave_score")
    assert len(order) == 3, "NULL 키·점수 키·동점 tiebreak 셋"
    first = str(order[0].compile(compile_kwargs={"literal_binds": True}))
    assert "IS NULL" in first.upper(), f"첫 정렬 키가 NULL 판별이 아니다: {first}"
    assert "DESC" in str(order[1]).upper()
    assert "created_at" in str(order[2])


def test_default_order_by_is_single_created_at_clause():
    from app.routers.naver_ad import _proposal_order_by

    order = _proposal_order_by("created_at")
    assert len(order) == 1
    assert "created_at" in str(order[0]) and "DESC" in str(order[0]).upper()
