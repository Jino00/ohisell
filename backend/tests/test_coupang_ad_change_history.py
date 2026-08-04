# test_coupang_ad_change_history.py — 쿠팡이 직접 주는 변경 이력 + 두 원천 병합.
#
# 지키는 불변식 셋:
#   ① **같은 사건이 두 줄로 뜨지 않는다.** 쿠팡 executionTime은 초까지, 우리 updatedAt은
#      밀리초까지(01:51:21 vs 01:51:21.372) — 초 절삭이 없으면 영영 안 겹쳐 두 줄이 된다.
#   ② **겹치면 쿠팡이 이긴다.** 쿠팡은 전/후 값을 주고 우리는 시각만 안다.
#      어느 쪽이 먼저 들어와도 결과가 같아야 한다(순서 무관).
#   ③ **모르는 changeType을 삼키지 않는다.** 버리면 영영 안 보인다.
from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import CoupangAdChangeLog
from app.services.coupang import ad_change_history as hist
from app.services.coupang import ad_settings_diff as diff

ACC = diff.ACCOUNT_OHITECH
CID = "104882010"
NAME = "[매.최] 메츨 싱"
# 라이브 실측(2026-08-04): 쿠팡 executionTime(초) vs 캠페인 updatedAt(밀리초)
EXEC_TIME = "2026-08-04T01:51:21Z"
UPDATED_AT = "2026-08-04T01:51:21.372Z"
OCCURRED = datetime(2026, 8, 4, 1, 51, 21)      # 초 절삭 후 두 원천이 만나는 지점
T_DETECT = datetime(2026, 8, 4, 3, 0, 0)


@pytest.fixture
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    try:
        yield s
    finally:
        s.close()


def _ev(change: dict, *, cid=CID, when=EXEC_TIME, exec_id="ee1626b4-87b1-d576-0e52-149e2d489dbb"):
    return {"campaignId": int(cid), "executionTime": when, "executionId": exec_id,
            "changes": [change]}


def _camp(*, cid=CID, name=NAME, budget=70000, updated=UPDATED_AT, active=True, roas=340):
    return {"id": int(cid), "name": name, "isActive": active, "budget": budget,
            "capType": "dailySoft", "roasTarget": roas, "targetType": "roas",
            "isSuspended": False, "campaignType": "PA", "goalType": "SALES",
            "spentBudget": 6501.49, "updatedAt": updated,
            "createdAt": "2026-04-28T08:12:22.555Z", "groupList": []}


def _thin(c):
    return {k: c.get(k) for k in ("id", "name", "isActive", "updatedAt", "createdAt")}


def _rows(db, **f):
    q = db.query(CoupangAdChangeLog)
    for k, v in f.items():
        q = q.filter(getattr(CoupangAdChangeLog, k) == v)
    return q.order_by(CoupangAdChangeLog.id).all()


class TestChangeTypeMapping:
    """실측 4종이 각각 어떤 줄이 되는가."""

    def test_BUDGET(self, db):
        r = hist.ingest_events(db, ACC, [_ev({"changeType": "BUDGET",
                                              "before": 1500000, "after": 70000})],
                               name_of={CID: NAME})
        db.flush()
        assert r["written"] == 1
        row = _rows(db)[0]
        assert (row.op, row.field) == (diff.OP_FIELD, "budget")
        assert (row.before_value, row.after_value) == ("1500000", "70000")
        assert row.occurred_at == OCCURRED and row.time_basis == "src"
        assert row.source == diff.SOURCE_COUPANG and row.entity_name == NAME
        assert row.external_id == "ee1626b4-87b1-d576-0e52-149e2d489dbb"

    def test_TROAS(self, db):
        hist.ingest_events(db, ACC, [_ev({"changeType": "TROAS", "before": 270, "after": 230})])
        db.flush()
        row = _rows(db)[0]
        assert (row.op, row.field) == (diff.OP_FIELD, "roasTarget")
        assert (row.before_value, row.after_value) == ("270", "230")

    def test_CAMPAIGN_ONOFF는_op으로_간다(self, db):
        """On/Off는 field_change가 아니라 별도 op다 — 스냅샷 쪽과 같은 어휘를 써야 겹친다."""
        hist.ingest_events(db, ACC, [_ev({"changeType": "CAMPAIGN_ONOFF",
                                          "before": True, "after": False})])
        db.flush()
        row = _rows(db)[0]
        assert row.op == diff.OP_TURNED_OFF and row.field == ""
        assert (row.before_value, row.after_value) == ("on", "off")

    def test_VIID는_개수와_증감을_담는다(self, db):
        hist.ingest_events(db, ACC, [_ev({"changeType": "VIID", "before": 6, "after": 26,
                                          "added": 20, "removed": 0})])
        db.flush()
        row = _rows(db)[0]
        assert row.op == hist.OP_ADS_CHANGED
        assert (row.before_value, row.after_value) == ("6", "26")
        assert json.loads(row.detail_json) == {"added": 20, "removed": 0}

    def test_모르는_유형은_원문으로_남긴다(self, db):
        """③ 삼키면 영영 안 보인다."""
        r = hist.ingest_events(db, ACC, [_ev({"changeType": "KEYWORD_BID",
                                              "before": 100, "after": 200})])
        db.flush()
        assert r["unknown_change_types"] == ["KEYWORD_BID"]
        row = _rows(db)[0]
        assert row.field == "KEYWORD_BID" and row.after_value == "200"


class TestMergePrecedence:
    """①② 같은 사건은 한 줄이고, 그 한 줄은 쿠팡 값이다."""

    def _snapshot_first(self, db):
        """스냅샷이 먼저 '10:51:21에 예산이 바뀌었다'를 기록한다(전 값은 스냅샷이 안다)."""
        diff.ingest(db, ACC, [_thin(_camp(budget=1500000, updated="2026-08-03T00:00:00Z"))],
                    [_camp(budget=1500000, updated="2026-08-03T00:00:00Z")],
                    detected_at=datetime(2026, 8, 3, 1, 0, 0))
        diff.ingest(db, ACC, [_thin(_camp(budget=70000))], [_camp(budget=70000)],
                    detected_at=T_DETECT)
        db.flush()

    def test_스냅샷_먼저_쿠팡_나중이면_한_줄이고_쿠팡_값이다(self, db):
        self._snapshot_first(db)
        before = _rows(db, op=diff.OP_FIELD, field="budget")
        assert len(before) == 1 and before[0].source == diff.SOURCE_SNAPSHOT

        r = hist.ingest_events(db, ACC, [_ev({"changeType": "BUDGET",
                                              "before": 1500000, "after": 70000})],
                               name_of={CID: NAME})
        db.flush()
        after = _rows(db, op=diff.OP_FIELD, field="budget")
        assert len(after) == 1                      # ★두 줄이 되지 않는다
        assert r["upgraded_from_snapshot"] == 1
        assert after[0].source == diff.SOURCE_COUPANG
        assert (after[0].before_value, after[0].after_value) == ("1500000", "70000")

    def test_쿠팡_먼저_스냅샷_나중이어도_결과가_같다(self, db):
        hist.ingest_events(db, ACC, [_ev({"changeType": "BUDGET",
                                          "before": 1500000, "after": 70000})],
                           name_of={CID: NAME})
        db.flush()
        self._snapshot_first(db)
        rows = _rows(db, op=diff.OP_FIELD, field="budget")
        assert len(rows) == 1                       # ★순서 무관
        assert rows[0].source == diff.SOURCE_COUPANG
        assert rows[0].after_value == "70000"

    def test_초_절삭이_없으면_겹치지_않는다(self, db):
        """왜 절삭이 필요한지 — 원본 밀리초는 서로 다르다."""
        assert diff.trunc_second(datetime(2026, 8, 4, 1, 51, 21, 372000)) == OCCURRED

    def test_On_Off도_한_줄로_합쳐진다(self, db):
        diff.ingest(db, ACC, [_thin(_camp())], [_camp()], detected_at=datetime(2026, 8, 3, 1, 0, 0))
        diff.ingest(db, ACC, [_thin(_camp(active=False))], [], detected_at=T_DETECT)
        db.flush()
        hist.ingest_events(db, ACC, [_ev({"changeType": "CAMPAIGN_ONOFF",
                                          "before": True, "after": False})])
        db.flush()
        rows = _rows(db, op=diff.OP_TURNED_OFF)
        assert len(rows) == 1 and rows[0].source == diff.SOURCE_COUPANG

    def test_다른_시각이면_당연히_두_줄이다(self, db):
        """합치기가 과하게 먹어 서로 다른 사건을 삼키면 안 된다."""
        hist.ingest_events(db, ACC, [_ev({"changeType": "BUDGET", "before": 1, "after": 2})])
        hist.ingest_events(db, ACC, [_ev({"changeType": "BUDGET", "before": 2, "after": 3},
                                         when="2026-08-04T02:00:00Z", exec_id="other")])
        db.flush()
        assert len(_rows(db, field="budget")) == 2


class TestIdempotency:
    def test_같은_이벤트를_두_번_넣어도_안_는다(self, db):
        ev = [_ev({"changeType": "BUDGET", "before": 1500000, "after": 70000})]
        a = hist.ingest_events(db, ACC, ev); db.flush()
        b = hist.ingest_events(db, ACC, ev); db.flush()
        assert a["written"] == 1 and b["written"] == 0
        assert len(_rows(db)) == 1

    def test_한_이벤트에_change가_둘이면_두_줄(self, db):
        """실측 5건 존재 — executionId는 같고 changeType이 다르다."""
        ev = {"campaignId": int(CID), "executionTime": EXEC_TIME, "executionId": "x",
              "changes": [{"changeType": "BUDGET", "before": 1, "after": 2},
                          {"changeType": "TROAS", "before": 300, "after": 400}]}
        r = hist.ingest_events(db, ACC, [ev]); db.flush()
        assert r["written"] == 2
        assert {x.field for x in _rows(db)} == {"budget", "roasTarget"}


class TestSafety:
    def test_모르는_계정은_거부(self, db):
        with pytest.raises(ValueError):
            hist.ingest_events(db, "naver", [])

    def test_시각이_없는_이벤트는_건너뛴다(self, db):
        r = hist.ingest_events(db, ACC, [{"campaignId": 1, "changes": [
            {"changeType": "BUDGET", "before": 1, "after": 2}]}])
        assert r["skipped"] == 1 and r["written"] == 0

    def test_빈_입력은_무해하다(self, db):
        assert hist.ingest_events(db, ACC, [])["written"] == 0

    def test_계정이_섞이지_않는다(self, db):
        ev = [_ev({"changeType": "BUDGET", "before": 1, "after": 2})]
        hist.ingest_events(db, ACC, ev)
        hist.ingest_events(db, diff.ACCOUNT_OFIX, ev)
        db.flush()
        assert len(_rows(db, account=ACC)) == 1
        assert len(_rows(db, account=diff.ACCOUNT_OFIX)) == 1
