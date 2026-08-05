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


# 라이브 실측(2026-08-04 메츨 싱): 일예산 1,500,000 → 70,000
_BUDGET_CHANGE = {"changeType": "BUDGET", "before": 1500000, "after": 70000}


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


class TestRaceAbsorbedIsCountedApart:
    """★P2-4: `duplicate` 한 칸에 "정상 재푸시"와 "UNIQUE 충돌 흡수"를 같이 담으면 실제 경합이
    묻힌다. 페처는 90일 이력을 매 회차 통째로 재push하므로 duplicate은 **평시에 수백 건**이
    정상이다 — 그 잡음 속에 경합 1건을 더하면 영영 안 보인다. race_absorbed는 평시 0이어야 한다.
    """

    def test_평시_재푸시는_duplicate이지_경합이_아니다(self, db):
        hist.ingest_events(db, ACC, [_ev(_BUDGET_CHANGE)])
        out = hist.ingest_events(db, ACC, [_ev(_BUDGET_CHANGE)])
        assert out["duplicate"] == 1
        assert out["race_absorbed"] == 0        # ★평시 재푸시를 경합이라 부르지 않는다

    def test_조회와_삽입_사이의_커밋만_race_absorbed로_뜬다(self, tmp_path):
        """진짜 경합 재현 — 사전 조회가 원리적으로 못 잡는 창을 연다.

        ★파일 DB + 세션 2개 + `after_cursor_execute` 훅: A의 `_find_existing` SELECT가 **끝난
          직후**(결과가 이미 "없음"으로 확정된 뒤) B(다른 커넥션·다른 트랜잭션)가 같은 키를
          커밋한다. 사전 조회가 원리적으로 못 잡는 창이 정확히 이것이다 — 조회를 아무리 정확히
          해도 그 다음 순간의 커밋은 못 본다. 그래서 SAVEPOINT 흡수가 유일한 방어선이다.
          (훅을 `before_`에 걸면 B의 커밋이 A의 SELECT에 그냥 보여 경합이 안 만들어진다.)
          인메모리 StaticPool로는 커넥션이 하나라 이 상황이 원리적으로 안 만들어진다.
        """
        from sqlalchemy import event

        eng = create_engine(f"sqlite:///{tmp_path}/race.db")
        Base.metadata.create_all(eng)
        make = sessionmaker(bind=eng, autoflush=False)   # ★prod SessionLocal과 같은 설정
        a, b = make(), make()
        armed = {"v": True}

        @event.listens_for(eng, "after_cursor_execute")
        def _inject(conn, cursor, statement, params, context, executemany):
            if not armed["v"] or "coupang_ad_change_log" not in statement:
                return
            if not statement.lstrip().upper().startswith("SELECT"):
                return
            armed["v"] = False
            # 스냅샷 유래 행 — 쿠팡이 이겨야 하므로 결과는 upgraded가 되어야 한다.
            b.add(CoupangAdChangeLog(
                account=ACC, entity_type=diff.ENTITY_CAMPAIGN, entity_id=CID,
                campaign_id=CID, entity_name=NAME, op=diff.OP_FIELD, field="budget",
                before_value=None, after_value=None, occurred_at=OCCURRED,
                time_basis="src", detected_at=OCCURRED, source=diff.SOURCE_SNAPSHOT))
            b.commit()

        try:
            out = hist.ingest_events(a, ACC, [_ev(_BUDGET_CHANGE)], name_of={CID: NAME})
            a.commit()
            assert armed["v"] is False, "훅이 안 걸렸다 — 경합 창을 못 열었다"
            assert out["race_absorbed"] == 1     # ★흡수한 경합이 카운트로 뜬다
            assert out["written"] == 0           # 삽입은 졌다
            assert out["upgraded_from_snapshot"] == 1   # 그래도 쿠팡 값이 이긴다

            fresh = make()
            rows = fresh.query(CoupangAdChangeLog).all()
            assert len(rows) == 1                # ★두 줄로 새지 않는다
            assert (rows[0].source, rows[0].before_value, rows[0].after_value) == (
                diff.SOURCE_COUPANG, "1500000", "70000")
            fresh.close()
        finally:
            event.remove(eng, "after_cursor_execute", _inject)
            a.close()
            b.close()
            eng.dispose()
