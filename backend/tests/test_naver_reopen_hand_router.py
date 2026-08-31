# test_naver_reopen_hand_router.py — 계약 P2 넷째의 «손»: POST /search-term/exclusions/{id}/reopen
#
# ★존재 이유: 재개방의 **유형별 dispatch는 이미 있었다**(D-NAO-271 — 파워링크=id 기반 /
#   쇼핑=키워드 기반). 없던 것은 **손**이다. 그 dispatch는 자동 레인 안에서만 도는데, 레인은
#   `auto_operate=1`인 캠페인만 훑는다. 그래서 스위치가 꺼진 캠페인의 제외는 `next_review_at`이
#   지나도 아무도 못 열었다 — 2026-08-31 실측: due 1건이 `next_review_at=2026-08-21`로 **10일째**
#   밀려 있었고 화면엔 「재개방 대기」 **배지만** 있었다(누를 것이 없었다).
#
# ★★이 파일의 핵심 회귀는 **「손이 게이트를 우회하지 않는다」**이다. 재개방은 harness를 안 타는
#   예외 경로라, 손을 얇게 안 만들면 그 자체가 우회로가 된다(계약 §5 금지선). 사람이 눌렀다는
#   사실은 게이트 면제 사유가 아니다 — 그래서 「거부될 때 네이버 쓰기 호출이 0회」를 센다.
#   실패를 조용한 no-op으로 만들지 않는 것도 같은 무게다: 못 열었으면 **사유가 응답 본문에** 있고,
#   그 문장이 목록 응답(`reopen_block_reason`)에도 있어야 버튼이 «왜» 비활성인지 말할 수 있다.
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    NaverCampaignSettings,
    NaverChangeLog,
    NaverEntity,
    NaverSearchTermExclusion,
)
from app.services.naver_ad import naver_sa_writer
from app.services.naver_ad import search_term_ss_lane as lane
from app.utils.kst import kst_now, kst_today

CID = "cmp-reopen"
AGID = "grp-reopen"
URL = "/api/naver/ad/search-term/exclusions"


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


def _seed(db, *, auto_operate=True, status="excluded", source=None, restrict_kwd_id="rkw-1",
          due_offset_days=-1, adgroup_id=AGID, campaign_id=CID):
    """제외 1건 + 그 캠페인 설정 + 광고그룹 소속 인벤토리. 기본은 «지금 열 수 있는» 상태."""
    db.add(NaverCampaignSettings(campaign_id=campaign_id, optimizer="ours", auto_operate=auto_operate))
    db.add(NaverEntity(entity_type="adgroup", entity_id=adgroup_id, parent_id=campaign_id,
                       campaign_id=campaign_id, campaign_type="WEB_SITE", name="grp", status="on"))
    row = NaverSearchTermExclusion(
        campaign_id=campaign_id, adgroup_id=adgroup_id, search_term="아이패드종이필름",
        status=status, cycle=1, restrict_kwd_id=restrict_kwd_id, source=source,
        excluded_at=kst_now(), last_transition_at=kst_now(),
        next_review_at=kst_today() + timedelta(days=due_offset_days),
    )
    db.add(row)
    db.commit()
    return row


def _del_result():
    return naver_sa_writer.WriteResult(
        action="delete_restricted_keywords", before=[], response=None, after=[], created_ids=[],
    )


def _post(client, row_id):
    return client.post(f"{URL}/{row_id}/reopen")


# ── ① 손이 실제로 연다 — 원장 전이 + change_log + 관찰창 ────────────────────────────
def test_hand_opens_due_exclusion(client, db):
    row = _seed(db)
    with patch.object(lane.naver_sa_writer, "get_adgroup_type", return_value="WEB_SITE"), \
         patch.object(lane.naver_sa_writer, "delete_restricted_keywords",
                      return_value=_del_result()) as mock_del:
        r = _post(client, row.id)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["reason"] is None
    mock_del.assert_called_once_with(AGID, ["rkw-1"])
    db.expire_all()
    row = db.get(NaverSearchTermExclusion, row.id)
    assert row.status == "probation"
    # ★관찰창이 채워져야 한다 — 수동으로 연 건만 probation_until이 비면 재판정 쿼리에 영영 안
    #   잡힌다(그래서 전이 확정을 `open_exclusion_now` 한 자리로 모았다).
    assert row.probation_until == kst_today() + timedelta(days=lane._PROBATION_DAYS)
    cl = db.query(NaverChangeLog).filter(NaverChangeLog.action == lane._RESTORE_ACTION).one()
    assert cl.dry_run is False and cl.after_value is not None


# ── ② 게이트 우회 금지 — 거부될 땐 네이버 쓰기가 «0회»여야 한다 ──────────────────────
@pytest.mark.parametrize(
    "kwargs, expect_code",
    [
        ({"auto_operate": False}, "auto_operate_off"),
        ({"due_offset_days": +3}, "not_due"),
        ({"status": "probation"}, "not_excluded"),
        ({"restrict_kwd_id": None}, "powerlink_no_key"),
    ],
)
def test_hand_refuses_and_writes_nothing(client, db, kwargs, expect_code):
    """사람이 눌렀다는 사실은 게이트 면제 사유가 아니다(계약 §5 금지선).

    ★쓰기 호출 0회를 «세는» 것이 이 테스트의 전부다 — 응답만 보면 「거부했다고 말하면서 실제로는
      썼다」를 못 잡는다.
    """
    row = _seed(db, **kwargs)
    with patch.object(lane.naver_sa_writer, "get_adgroup_type", return_value="WEB_SITE"), \
         patch.object(lane.naver_sa_writer, "delete_restricted_keywords") as mock_del, \
         patch.object(lane.naver_sa_writer, "remove_shopping_exclusions") as mock_shop:
        r = _post(client, row.id)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["reason_code"] == expect_code
    # 사유는 «사람이 읽는 문장»으로도 실려야 한다 — 코드만 주면 화면이 자기 말로 다시 쓴다.
    assert body["reason"] == lane.REOPEN_BLOCK_MESSAGES[expect_code]
    mock_del.assert_not_called()
    mock_shop.assert_not_called()
    db.expire_all()
    assert db.get(NaverSearchTermExclusion, row.id).status == ("probation" if kwargs.get("status") == "probation" else "excluded")


def test_hand_refuses_console_import(client, db):
    """콘솔 편입분은 대상이 아니다 — 우리가 걸지 않은 제외는 우리가 풀지 않는다(계약 §5 금지선).

    실측(2026-08-31 prod): 제외 3,990행 중 `console_import`가 **3,987행**이다. 이 검사가 없으면
    손 하나로 대행사가 건 제외 전체가 열 수 있는 대상이 된다.
    """
    row = _seed(db, source="console_import")
    with patch.object(lane.naver_sa_writer, "get_adgroup_type", return_value="WEB_SITE"), \
         patch.object(lane.naver_sa_writer, "delete_restricted_keywords") as mock_del:
        r = _post(client, row.id)
    assert r.status_code == 200 and r.json()["ok"] is False
    # 코드는 기계가, 문장은 사람이 읽는다 — 둘 다 실려야 화면이 문구를 지어내지 않는다.
    assert r.json()["reason_code"] == "console_import"
    assert "콘솔 편입분" in r.json()["reason"]
    mock_del.assert_not_called()


def test_list_marks_console_import_as_not_a_target(client, db):
    """목록에서도 콘솔 편입분은 «왜 버튼이 없는지»를 말한다 — 조용히 빼면 「누락」으로 읽힌다."""
    _seed(db, source="console_import")
    rows = client.get(URL).json()["rows"]
    assert rows[0]["reopen_block_reason"] and "콘솔 편입분" in rows[0]["reopen_block_reason"]


def test_hand_refuses_when_daily_cap_exhausted(client, db):
    """일일 복귀 캡은 손에도 걸린다 — 봉투는 사람이 눌러도 봉투다."""
    row = _seed(db)
    for i in range(lane._SS_DAILY_RETURN_CAP):
        db.add(NaverChangeLog(
            entity_type="search_term", entity_id=f"t{i}", campaign_id=CID,
            action=lane._RESTORE_ACTION, dry_run=False, after_value="{}",
            changed_at=kst_now(), executed_at=kst_now(),
        ))
    db.commit()
    with patch.object(lane.naver_sa_writer, "get_adgroup_type", return_value="WEB_SITE"), \
         patch.object(lane.naver_sa_writer, "delete_restricted_keywords") as mock_del:
        r = _post(client, row.id)
    assert r.json()["reason_code"] == "daily_cap"
    mock_del.assert_not_called()


def test_missing_row_is_404(client, db):
    assert _post(client, 999999).status_code == 404


# ── ③ 표면 — 목록이 «왜 못 여는가»를 말한다(버튼 비활성 사유) ────────────────────────
def test_list_carries_block_reason_for_the_button(client, db):
    """★표면 절단 회귀: 목록 응답에서 `reopen_block_reason`이 사라지면 버튼은 «왜» 비활성인지
    말할 수 없고, 화면은 조용히 「그냥 안 되는 것」이 된다. 값의 출처가 실행 경로와 같은 함수라는
    것도 여기서 함께 지킨다 — 문구를 손으로 적으면 두 벌이 갈라진다.
    """
    _seed(db, auto_operate=False)
    r = client.get(URL)
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 1
    assert "reopen_block_reason" in rows[0]
    assert rows[0]["reopen_block_reason"] == lane.REOPEN_BLOCK_MESSAGES["auto_operate_off"]


def test_list_block_reason_is_none_when_openable(client, db):
    """열 수 있는 건은 사유가 None이어야 한다 — 항상 사유를 채우면 버튼이 영원히 비활성이 된다."""
    _seed(db)
    rows = client.get(URL).json()["rows"]
    assert rows[0]["reopen_block_reason"] is None


def test_list_does_not_call_live_api(client, db):
    """목록은 라이브 광고그룹 유형 GET을 «안» 때린다(100행이면 100번이 된다).

    ★그 대가로 목록의 판정은 «DB로 알 수 있는 범위»이고 권위는 실행 시점에 있다. 방향이
      fail-closed라 안전하다 — 화면이 「열림」이라 해도 실행이 다시 막지만, 그 반대는 없다.
    """
    _seed(db)
    with patch.object(lane.naver_sa_writer, "get_adgroup_type") as mock_type:
        client.get(URL)
    mock_type.assert_not_called()


# ── ④ 게이트 단일 출처 — 손과 레인이 같은 함수를 본다 ────────────────────────────────
def test_gate_is_shared_with_the_auto_lane(db):
    """`reopen_gate`가 «단일 판정»이라는 구조 회귀.

    손 쪽에서만 막고 레인은 안 막히는(또는 그 반대) 상태가 되면, 화면이 말하는 사유와 실제로
    막는 것이 갈라진다 — 이 저장소가 이미 값을 치른 병이다(판정창 ≠ 실행 재검증창).
    """
    row = _seed(db, auto_operate=False)
    assert lane.reopen_gate(db, row, kst_now()).reason == "auto_operate_off"
    # 레인도 같은 판정으로 막힌다(그리고 쓰기 0회).
    with patch.object(lane.naver_sa_writer, "delete_restricted_keywords") as mock_del:
        assert lane._open_exclusion(db, row, kst_now()) is False
    mock_del.assert_not_called()


def test_every_reason_code_has_a_human_sentence(db):
    """사유 코드와 문구가 한 자리에서 짝지어져 있는지 — 코드만 늘고 문구가 안 늘면 라우터가
    KeyError로 죽는다(조용한 실패가 아니라 즉시 실패라 낫지만, 여기서 먼저 잡는다)."""
    row = _seed(db)
    for code in lane.REOPEN_BLOCK_MESSAGES:
        assert lane.REOPEN_BLOCK_MESSAGES[code].strip()
    # 게이트가 실제로 내는 코드가 전부 사전에 있는지(대표 경로 1개로 확인).
    gate = lane.reopen_gate(db, row, kst_now(), check_live=False)
    assert gate.reason is None or gate.reason in lane.REOPEN_BLOCK_MESSAGES
