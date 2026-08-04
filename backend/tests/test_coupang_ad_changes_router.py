# test_coupang_ad_changes_router.py — 「수정 사항」 쿠팡판 HTTP 경계.
#   POST /api/coupang/ops/ad-settings/ingest (페처 push) · GET  /api/coupang/ops/ad-changes (화면)
#
# 경계에서 지키는 것:
#   ① 토큰 없는 push는 401 — 광고 이력은 쓰기 표면이다.
#   ② 조회는 **KST 날짜창**이다. occurred_at은 UTC naive로 저장되므로 -9h로 훑어야
#      "오늘 아침 것"이 오늘로 보인다(KST 09:12 = UTC 00:12 — 날짜가 갈리는 구간).
#   ③ time_basis를 그대로 내보낸다 — 화면이 '진짜 발생 시각'과 '알아챈 시각'을 구분해야 한다.
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app

_INGEST = "/api/coupang/ops/ad-settings/ingest"
_LIST = "/api/coupang/ops/ad-changes"
_TOKEN = "test-token-123"

# KST 2026-08-04 10:51:21 = UTC 01:51:21 (라이브 실측: 메츨 싱)
_UPD = "2026-08-04T01:51:21.372Z"
# KST 2026-08-04 09:12:56 = UTC 00:12:56 — UTC로는 같은 날이지만 KST 창 계산이 틀리면 샌다
_UPD_EARLY = "2026-08-04T00:12:56.000Z"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AD_INGEST_TOKEN", _TOKEN)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _camp(cid=104882010, name="[매.최] 메츨 싱", *, budget=70000, updated=_UPD, active=True):
    return {
        "id": cid, "name": name, "isActive": active, "budget": budget,
        "capType": "dailySoft", "roasTarget": 340, "targetType": "roas",
        "isSuspended": False, "campaignType": "PA", "goalType": "SALES",
        "spentBudget": 6501.49, "updatedAt": updated,
        "createdAt": "2026-04-28T08:12:22.555Z", "groupList": [],
    }


def _push(client, campaigns, *, token=_TOKEN, account="ohitech"):
    return client.post(
        _INGEST,
        json={"account": account,
              "all": [{"id": c["id"], "name": c["name"], "isActive": c["isActive"],
                       "updatedAt": c.get("updatedAt"), "createdAt": c.get("createdAt")}
                      for c in campaigns],
              "active": [c for c in campaigns if c["isActive"]]},
        headers={"X-Ingest-Token": token},
    )


class TestIngestAuth:
    def test_토큰_없으면_401(self, env):
        r = env.post(_INGEST, json={"account": "ohitech", "all": [], "active": []})
        assert r.status_code == 401

    def test_토큰_틀리면_401(self, env):
        assert _push(env, [_camp()], token="wrong").status_code == 401

    def test_모르는_계정은_400(self, env):
        assert _push(env, [_camp()], account="naver").status_code == 400

    def test_all이_리스트가_아니면_400(self, env):
        r = env.post(_INGEST, json={"account": "ohitech", "all": None, "active": []},
                     headers={"X-Ingest-Token": _TOKEN})
        assert r.status_code == 400


class TestIngestAndList:
    def test_예산_변경이_화면_경로까지_전후값과_함께_나온다(self, env):
        """계약 ④ 합격기준의 HTTP판."""
        assert _push(env, [_camp(budget=70000)]).status_code == 200
        r = _push(env, [_camp(budget=110000, updated="2026-08-04T02:10:00.000Z")])
        assert r.status_code == 200 and r.json()["changes"] >= 1

        got = env.get(_LIST, params={"from": "2026-08-04", "to": "2026-08-04"}).json()
        fields = [i for i in got["items"] if i["op"] == "field_change"]
        assert len(fields) == 1
        it = fields[0]
        assert (it["field"], it["before_value"], it["after_value"]) == ("budget", "70000", "110000")
        assert it["occurred_at"].startswith("2026-08-04T11:10:00")   # KST 환산(+9h)
        assert it["time_basis"] == "src"
        assert it["entity_name"] == "[매.최] 메츨 싱"

    def test_성과_필드는_한_줄도_안_나온다(self, env):
        _push(env, [_camp()])
        c = _camp()
        c["spentBudget"] = 6592.87          # 20분 만에 실제로 이렇게 움직인다
        _push(env, [c])
        got = env.get(_LIST, params={"from": "2026-08-04", "to": "2026-08-04"}).json()
        assert [i for i in got["items"] if i["op"] == "field_change"] == []

    def test_KST_이른_아침_변경이_그날로_잡힌다(self, env):
        """KST 09:12 = UTC 00:12. 창을 UTC로 잡으면 하루가 밀린다."""
        _push(env, [_camp(budget=70000, updated="2026-08-03T00:00:00.000Z")])
        _push(env, [_camp(budget=80000, updated=_UPD_EARLY)])
        got = env.get(_LIST, params={"from": "2026-08-04", "to": "2026-08-04"}).json()
        fields = [i for i in got["items"] if i["op"] == "field_change"]
        assert len(fields) == 1
        assert fields[0]["occurred_at"].startswith("2026-08-04T09:12:56")

    def test_On_Off가_행으로_나온다(self, env):
        _push(env, [_camp()])
        _push(env, [_camp(active=False)])
        got = env.get(_LIST, params={"from": "2026-08-04", "to": "2026-08-04"}).json()
        ops = [i["op"] for i in got["items"]]
        assert "turned_off" in ops

    def test_계정_필터(self, env):
        _push(env, [_camp()], account="ohitech")
        _push(env, [_camp()], account="ofix")
        a = env.get(_LIST, params={"from": "2026-01-01", "to": "2026-12-31",
                                   "account": "ofix"}).json()
        assert a["count"] >= 1 and {i["account"] for i in a["items"]} == {"ofix"}

    def test_첫_회차의_created는_오늘을_어지럽히지_않는다(self, env):
        """★첫 적재는 기존 캠페인 전부가 '우리에겐 처음'이다. 그 created를 감지일에 귀속시키면
        오하이테크 525건이 통째로 오늘 목록에 쏟아진다. 쿠팡 createdAt(2026-04-28)에 붙는 게 맞다."""
        _push(env, [_camp()])
        today = env.get(_LIST, params={"from": "2026-08-04", "to": "2026-08-04"}).json()
        assert [i for i in today["items"] if i["op"] == "created"] == []
        hist = env.get(_LIST, params={"from": "2026-04-28", "to": "2026-04-28"}).json()
        created = [i for i in hist["items"] if i["op"] == "created"]
        assert len(created) == 1 and created[0]["time_basis"] == "src"

    def test_last_observed_at이_신선도를_말해준다(self, env):
        _push(env, [_camp()])
        got = env.get(_LIST, params={"from": "2026-08-04", "to": "2026-08-04"}).json()
        assert got["last_observed_at"] is not None


class TestListValidation:
    def test_from이_to보다_늦으면_422(self, env):
        r = env.get(_LIST, params={"from": "2026-08-05", "to": "2026-08-04"})
        assert r.status_code == 422

    def test_날짜_형식_오류는_422(self, env):
        assert env.get(_LIST, params={"from": "08/04/2026"}).status_code == 422

    def test_모르는_계정은_400(self, env):
        assert env.get(_LIST, params={"account": "naver"}).status_code == 400

    def test_빈_DB는_빈_목록(self, env):
        got = env.get(_LIST).json()
        assert got["count"] == 0 and got["items"] == [] and got["last_observed_at"] is None
