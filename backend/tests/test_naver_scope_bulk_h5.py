# test_naver_scope_bulk_h5.py — 스코프 캠페인 단위 일괄 지정 (H5 · 계약 P2)
#
# 이 파일이 지키는 것 다섯:
#   ①일괄이 실제로 **행을 만든다**(created/updated/unchanged 카운트가 사실과 맞는다)
#   ②★**감사 원장까지 간다** — 반환값만 보는 테스트는 「값은 맞는데 원장은 비어 있는」 상태를
#     통과시킨다. 이 저장소가 반복해 데인 자리라, 원장 행 수·before/after를 직접 센다.
#   ③★**no-op은 원장을 더럽히지 않는다** — 같은 값으로 두 번 누르면 두 번째는 감사 줄 0이다.
#     이게 없으면 버튼 한 번에 no-op 수십 줄이 쌓여 「무엇이 실제로 바뀌었나」가 사라진다.
#   ④거부: 중복 id·잘못된 role·빈 목록·상한 초과·오필드
#   ⑤★**단건과 일괄이 같은 규칙을 쓴다** — 한쪽만 고치면 깨진다(갈라짐 방지가 뽑아낸 이유다)
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import NaverAdgroupScope, NaverChangeLog

CAMPAIGN = "cmp-tpu"
G1, G2, G3 = "grp-1", "grp-2", "grp-3"
URL_BULK = "/api/naver/ad/scope/campaign"
URL_ONE = "/api/naver/ad/scope/adgroup"


@pytest.fixture
def client_and_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app), Session
    app.dependency_overrides.clear()


def _scope_rows(Session, campaign_id=CAMPAIGN):
    s = Session()
    try:
        return {
            r.adgroup_id: (r.role, bool(r.enabled), r.memo)
            for r in s.query(NaverAdgroupScope)
            .filter(NaverAdgroupScope.campaign_id == campaign_id)
            .all()
        }
    finally:
        s.close()


def _audit_rows(Session):
    s = Session()
    try:
        return (
            s.query(NaverChangeLog)
            .filter(NaverChangeLog.action == "adgroup_scope_change")
            .order_by(NaverChangeLog.id)
            .all()
        )
    finally:
        s.close()


# ── ① 일괄이 행을 만든다 ────────────────────────────────────────────────
def test_bulk_creates_rows_for_every_requested_adgroup(client_and_session):
    client, Session = client_and_session
    r = client.put(URL_BULK, json={
        "campaign_id": CAMPAIGN, "adgroup_ids": [G1, G2, G3], "role": "accel", "enabled": True,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["requested"] == 3
    assert body["changed"] == 3
    assert body["counts"] == {"created": 3, "updated": 0, "unchanged": 0}

    rows = _scope_rows(Session)
    assert set(rows) == {G1, G2, G3}
    assert all(v == ("accel", True, None) for v in rows.values())


# ── ② 감사 원장까지 간다 ────────────────────────────────────────────────
def test_bulk_writes_one_audit_row_per_changed_adgroup(client_and_session):
    """★반환값이 아니라 **원장**을 센다.

    변이 저항: 라우터에서 `db.add(NaverChangeLog(...))` 한 줄을 지워도 위 ①은 그대로 초록이다
    (행은 만들어지고 카운트도 맞다). 이 테스트만이 그 변이를 잡는다 — 「값이 도는 층」과
    「사람이 나중에 읽는 층」은 다른 층이고, 둘을 잇는 것을 테스트가 지켜야 한다.
    """
    client, Session = client_and_session
    client.put(URL_BULK, json={
        "campaign_id": CAMPAIGN, "adgroup_ids": [G1, G2], "role": "brake", "enabled": True,
    })
    audit = _audit_rows(Session)
    assert len(audit) == 2
    assert {a.entity_id for a in audit} == {G1, G2}
    for a in audit:
        assert a.entity_type == "adgroup"
        assert a.campaign_id == CAMPAIGN
        # 새로 생긴 행이므로 before는 「없었다」 = None (빈 문자열이 아니다)
        assert a.before_value is None
        assert a.after_value == "role=brake enabled=True"
        assert "일괄" in (a.rationale or "")


def test_bulk_audit_records_actual_before_state_on_update(client_and_session):
    """★`before_value`가 **실제 이전 상태**다.

    변이 저항: `before_value=None`으로 되돌리면(종전 단건 동작) 이 테스트가 죽는다.
    원장에서 「무엇에서 무엇으로」를 읽을 수 없으면 되돌릴 근거가 사라진다.
    """
    client, Session = client_and_session
    client.put(URL_BULK, json={"campaign_id": CAMPAIGN, "adgroup_ids": [G1], "role": "accel"})
    client.put(URL_BULK, json={"campaign_id": CAMPAIGN, "adgroup_ids": [G1], "role": "brake"})

    audit = _audit_rows(Session)
    assert len(audit) == 2
    assert audit[1].before_value == "role=accel enabled=True"
    assert audit[1].after_value == "role=brake enabled=True"


# ── ③ no-op은 원장을 더럽히지 않는다 ────────────────────────────────────
def test_bulk_twice_with_same_values_writes_no_second_audit_row(client_and_session):
    client, Session = client_and_session
    payload = {"campaign_id": CAMPAIGN, "adgroup_ids": [G1, G2], "role": "accel", "enabled": True}
    client.put(URL_BULK, json=payload)
    second = client.put(URL_BULK, json=payload).json()

    assert second["counts"] == {"created": 0, "updated": 0, "unchanged": 2}
    assert second["changed"] == 0, "값이 그대로면 «했다»고 세지 않는다"
    assert len(_audit_rows(Session)) == 2, "두 번째 호출은 감사 줄을 만들지 않는다"


def test_memo_only_edit_counts_as_changed(client_and_session):
    """memo만 바꾼 것도 «바뀜»이다 — unchanged로 적으면 원장이 그 편집을 잃는다."""
    client, Session = client_and_session
    client.put(URL_BULK, json={"campaign_id": CAMPAIGN, "adgroup_ids": [G1], "role": "accel"})
    res = client.put(URL_BULK, json={
        "campaign_id": CAMPAIGN, "adgroup_ids": [G1], "role": "accel", "memo": "왜 맡겼는지",
    }).json()
    assert res["counts"]["updated"] == 1
    assert len(_audit_rows(Session)) == 2
    assert _scope_rows(Session)[G1][2] == "왜 맡겼는지"


# ── ④ 거부 ──────────────────────────────────────────────────────────────
def test_duplicate_adgroup_ids_are_rejected_not_silently_deduped(client_and_session):
    """조용히 dedupe 하면 「보낸 수」와 「손댄 수」가 어긋난다 — 거부가 정직하다."""
    client, Session = client_and_session
    r = client.put(URL_BULK, json={"campaign_id": CAMPAIGN, "adgroup_ids": [G1, G1]})
    assert r.status_code == 422
    assert "중복" in r.text
    assert _scope_rows(Session) == {}, "거부된 요청은 아무것도 쓰지 않는다"


def test_invalid_role_is_rejected(client_and_session):
    client, Session = client_and_session
    r = client.put(URL_BULK, json={"campaign_id": CAMPAIGN, "adgroup_ids": [G1], "role": "쎈놈"})
    assert r.status_code == 422
    assert _scope_rows(Session) == {}


def test_empty_adgroup_ids_is_rejected(client_and_session):
    client, _ = client_and_session
    assert client.put(URL_BULK, json={"campaign_id": CAMPAIGN, "adgroup_ids": []}).status_code == 422


def test_over_cap_is_rejected(client_and_session):
    client, Session = client_and_session
    r = client.put(URL_BULK, json={
        "campaign_id": CAMPAIGN, "adgroup_ids": [f"g{i}" for i in range(501)],
    })
    assert r.status_code == 422
    assert _scope_rows(Session) == {}, "상한 초과는 한 건도 쓰지 않는다"


def test_unknown_field_is_rejected(client_and_session):
    """`extra='forbid'` — 단건과 같은 규격. 오필드가 조용히 무시되면 「설정했다」고 착각한다."""
    client, _ = client_and_session
    r = client.put(URL_BULK, json={
        "campaign_id": CAMPAIGN, "adgroup_ids": [G1], "enabledd": True,
    })
    assert r.status_code == 422


# ── ⑤ 단건과 일괄이 같은 규칙을 쓴다 ────────────────────────────────────
def test_single_and_bulk_produce_identical_row_and_audit(client_and_session):
    """★같은 뜻의 두 경로가 **같은 결과**를 낸다.

    변이 저항: 단건 라우터가 `apply_scope_row`를 안 쓰고 자기 upsert를 다시 적으면
    (종전 모양) `before_value`가 None으로 굳어 이 테스트가 죽는다. 감사 규칙이 두 벌로
    갈라지는 순간을 잡는 것이 이 테스트의 전부다.
    """
    client, Session = client_and_session
    # 단건으로 만들고 → 단건으로 고친다
    client.put(URL_ONE, json={"campaign_id": CAMPAIGN, "adgroup_id": G1, "role": "accel"})
    client.put(URL_ONE, json={"campaign_id": CAMPAIGN, "adgroup_id": G1, "role": "brake"})
    # 일괄로 만들고 → 일괄로 고친다
    client.put(URL_BULK, json={"campaign_id": CAMPAIGN, "adgroup_ids": [G2], "role": "accel"})
    client.put(URL_BULK, json={"campaign_id": CAMPAIGN, "adgroup_ids": [G2], "role": "brake"})

    audit = _audit_rows(Session)
    single = [a for a in audit if a.entity_id == G1]
    bulk = [a for a in audit if a.entity_id == G2]
    assert len(single) == 2 and len(bulk) == 2
    assert [a.before_value for a in single] == [a.before_value for a in bulk]
    assert [a.after_value for a in single] == [a.after_value for a in bulk]

    rows = _scope_rows(Session)
    assert rows[G1] == rows[G2] == ("brake", True, None)


def test_single_put_with_same_values_is_unchanged_too(client_and_session):
    """단건도 no-op이면 감사 줄을 안 쓴다(일괄과 같은 규칙)."""
    client, Session = client_and_session
    payload = {"campaign_id": CAMPAIGN, "adgroup_id": G1, "role": "accel", "enabled": True}
    first = client.put(URL_ONE, json=payload).json()
    second = client.put(URL_ONE, json=payload).json()
    assert first["outcome"] == "created"
    assert second["outcome"] == "unchanged"
    assert len(_audit_rows(Session)) == 1


def test_bulk_disable_keeps_rows_but_flips_enabled(client_and_session):
    """`enabled=false`와 «삭제»는 결과가 정반대다(삭제하면 전 그룹이 다시 대상이 된다).
    일괄 끄기가 행을 지우지 않는다는 것을 못 박는다."""
    client, Session = client_and_session
    client.put(URL_BULK, json={"campaign_id": CAMPAIGN, "adgroup_ids": [G1, G2], "role": "accel"})
    res = client.put(URL_BULK, json={
        "campaign_id": CAMPAIGN, "adgroup_ids": [G1, G2], "role": "accel", "enabled": False,
    }).json()
    assert res["counts"]["updated"] == 2
    rows = _scope_rows(Session)
    assert set(rows) == {G1, G2}, "행은 남는다 — 삭제가 아니다"
    assert all(v[1] is False for v in rows.values())
