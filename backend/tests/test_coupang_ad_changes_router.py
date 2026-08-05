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


class TestCrossSourceDuplicateDoesNotCrash:
    """2026-08-05 20:55 라이브 사고 재현: 페처가 change-history events와 캠페인 스냅샷을
    한 회차에 같이 push하면, 이벤트 경로(ad_change_history)와 스냅샷 diff 경로
    (ad_settings_diff)가 **같은 예산 변경을 서로 다른 경로로 잡아** coupang_ad_change_log의
    UNIQUE(account, entity_type, entity_id, op, field, occurred_at)에 같은 키로 부딪혔다.

    이 라우터의 세션은 `autoflush=False`(prod `SessionLocal`과 동일 설정, env 픽스처 참고) —
    이벤트가 먼저 add한 행이 뒤이은 스냅샷 diff의 사전 SELECT엔 안 보여(미flush) "없다"고
    오판, 같은 키로 다시 add했고 flush 시점에 sqlite3.IntegrityError → 500이 났다. 페처는
    90일 이력을 매번 통째로 재push하므로 재실행마다 필연적으로 재현됐다(정상 동작에서 발생하는
    중복이지 데이터 오류가 아니다). 이 테스트는 500이 아니라 200 + skip 카운트로 수렴하는지,
    그리고 같은 사건이 화면에 두 줄로 새지 않는지(쿠팡 값이 이겨야 한다)를 확인한다."""

    def test_이벤트와_스냅샷_diff가_같은_예산_변경을_동시에_보내도_500이_아니다(self, env):
        cid = 105061655
        name = "[매.최] 아이폰16프로_강화유리"
        # 기준선: 이전 예산 165000
        assert _push(env, [_camp(cid=cid, name=name, budget=165000,
                                 updated="2026-08-04T00:00:00.000Z")]).status_code == 200

        body = {
            "account": "ohitech",
            "all": [{"id": cid, "name": name, "isActive": True,
                     "updatedAt": "2026-08-05T07:02:10.000Z",
                     "createdAt": "2026-04-28T08:12:22.555Z"}],
            "active": [_camp(cid=cid, name=name, budget=130000,
                             updated="2026-08-05T07:02:10.000Z")],
            "events": [{
                "campaignId": str(cid),
                "executionTime": "2026-08-05T07:02:10Z",   # 스냅샷 updatedAt과 절삭 후 같은 초
                "executionId": "evt-budget-105061655",
                "changes": [{"changeType": "BUDGET", "before": 165000, "after": 130000}],
            }],
        }
        r = env.post(_INGEST, json=body, headers={"X-Ingest-Token": _TOKEN})
        assert r.status_code == 200          # ★핵심 합격기준: 500이 아니다
        out = r.json()
        # 이벤트가 새 행을 쓰고, 스냅샷 diff는 같은 키를 "중복"으로 건너뛴다(조용히 사라지지 않는다).
        assert out["history"]["written"] == 1
        assert out["changes"] == 0
        assert out["changes_duplicate"] == 1

        got = env.get(_LIST, params={"from": "2026-08-05", "to": "2026-08-05"}).json()
        budget_rows = [i for i in got["items"]
                      if i["op"] == "field_change" and i["field"] == "budget"]
        assert len(budget_rows) == 1          # ★같은 사건이 두 줄로 새지 않는다
        row = budget_rows[0]
        assert (row["before_value"], row["after_value"]) == ("165000", "130000")
        assert row["time_basis"] == "src"     # 쿠팡 값이 이겼다(스냅샷이 아니라)

    def test_같은_회차를_재실행해도_행이_늘지_않는다(self, env):
        """페처는 90일 이력을 매번 통째로 재push한다 — 재실행이 곧 정상 동작이다."""
        cid = 105061655
        body = {
            "account": "ohitech",
            "all": [{"id": cid, "name": "캠페인", "isActive": True,
                     "updatedAt": "2026-08-05T07:02:10.000Z",
                     "createdAt": "2026-04-28T08:12:22.555Z"}],
            "active": [_camp(cid=cid, name="캠페인", budget=130000,
                             updated="2026-08-05T07:02:10.000Z")],
            "events": [{
                "campaignId": str(cid), "executionTime": "2026-08-05T07:02:10Z",
                "executionId": "evt-budget-105061655",
                "changes": [{"changeType": "BUDGET", "before": 165000, "after": 130000}],
            }],
        }
        first = env.post(_INGEST, json=body, headers={"X-Ingest-Token": _TOKEN})
        second = env.post(_INGEST, json=body, headers={"X-Ingest-Token": _TOKEN})
        assert first.status_code == 200 and second.status_code == 200
        assert second.json()["history"]["duplicate"] == 1
        got = env.get(_LIST, params={"from": "2026-08-05", "to": "2026-08-05"}).json()
        budget_rows = [i for i in got["items"]
                      if i["op"] == "field_change" and i["field"] == "budget"]
        assert len(budget_rows) == 1


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
