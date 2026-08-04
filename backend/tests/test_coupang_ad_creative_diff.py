# test_coupang_ad_creative_diff.py — 소재(옵션ID) 증감 추적.
#
# 지키는 불변식 넷:
#   ① **첫 회차는 기준선이다.** 전부 '추가'로 보이지만 사실이 아니다 — 한 줄도 만들지 않는다.
#   ② **쿠팡 이벤트에 목록을 얹는다.** 쿠팡은 시각과 개수를, 우리는 옵션ID를 안다.
#      개수가 정확히 맞을 때만 붙인다 — 아니면 엉뚱한 사건에 목록을 다는 것이다.
#   ③ **안 물어본 캠페인의 소재를 '빠졌다'로 만들지 않는다.** 조회 범위와 사실을 구분한다.
#   ④ **빈 응답을 '소재 전멸'로 읽지 않는다.**
from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import CoupangAdChangeLog, CoupangAdEntitySnapshot
from app.services.coupang import ad_change_history as hist
from app.services.coupang import ad_creative_diff as cre
from app.services.coupang import ad_settings_diff as diff

ACC = diff.ACCOUNT_OHITECH
CID = "105170637"
GID = "205605856"
T1 = datetime(2026, 8, 4, 1, 0, 0)
T2 = datetime(2026, 8, 4, 5, 0, 0)


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


def _ad(node: str, viid: int, *, cid=CID, gid=GID, name="오픽스 필름", active=True,
        suspended=False, override=None):
    return {"campaign_id": cid, "adgroup_id": gid, "ad_node_id": node,
            "vendor_item_id": viid, "item_name": name, "is_active": active,
            "is_suspended": suspended, "pricing_override": override}


def _rows(db, **f):
    q = db.query(CoupangAdChangeLog)
    for k, v in f.items():
        q = q.filter(getattr(CoupangAdChangeLog, k) == v)
    return q.order_by(CoupangAdChangeLog.id).all()


class TestBaseline:
    """① 첫 회차는 사실이 아니라 기준선이다."""

    def test_첫_회차는_증감_행을_안_만든다(self, db):
        r = cre.ingest_ads(db, ACC, [_ad("1", 111), _ad("2", 222)], detected_at=T1)
        assert r["baseline"] is True and r["ads"] == 2
        assert _rows(db) == []
        assert db.query(CoupangAdEntitySnapshot).filter_by(entity_type=cre.ENTITY_AD).count() == 2

    def test_빈_입력은_소재_전멸이_아니다(self, db):
        """④ 조회 실패를 사실로 착각하면 되돌리기 어렵다."""
        cre.ingest_ads(db, ACC, [_ad("1", 111)], detected_at=T1)
        r = cre.ingest_ads(db, ACC, [], detected_at=T2)
        assert r["skipped"] == "empty_ads"
        assert _rows(db, op=cre.OP_ADS_REMOVED) == []


class TestAddRemove:
    def test_추가된_옵션ID가_목록으로_남는다(self, db):
        cre.ingest_ads(db, ACC, [_ad("1", 111)], detected_at=T1)
        cre.ingest_ads(db, ACC, [_ad("1", 111), _ad("2", 222, name="폴드8 필름"),
                                 _ad("3", 333)], detected_at=T2)
        rows = _rows(db, op=cre.OP_ADS_ADDED)
        assert len(rows) == 1
        d = json.loads(rows[0].detail_json)
        assert d["count"] == 2
        assert {o["vendor_item_id"] for o in d["options"]} == {"222", "333"}
        assert any(o["item_name"] == "폴드8 필름" for o in d["options"])
        assert rows[0].time_basis == "detected"     # 쿠팡이 시각을 안 준 경로다

    def test_제거된_옵션ID도_남는다(self, db):
        cre.ingest_ads(db, ACC, [_ad("1", 111), _ad("2", 222)], detected_at=T1)
        cre.ingest_ads(db, ACC, [_ad("1", 111)], detected_at=T2)
        rows = _rows(db, op=cre.OP_ADS_REMOVED)
        assert len(rows) == 1
        assert json.loads(rows[0].detail_json)["options"][0]["vendor_item_id"] == "222"

    def test_안_물어본_캠페인의_소재는_빠진_게_아니다(self, db):
        """③ 이번 회차가 캠페인 A만 조회했다면, B의 소재가 목록에 없는 건 당연하다."""
        cre.ingest_ads(db, ACC, [_ad("1", 111, cid="A"), _ad("9", 999, cid="B")],
                       detected_at=T1)
        cre.ingest_ads(db, ACC, [_ad("1", 111, cid="A")], detected_at=T2)   # A만 조회
        assert _rows(db, op=cre.OP_ADS_REMOVED) == []

    def test_변화_없으면_0건(self, db):
        cre.ingest_ads(db, ACC, [_ad("1", 111)], detected_at=T1)
        r = cre.ingest_ads(db, ACC, [_ad("1", 111)], detected_at=T2)
        assert r["added"] == 0 and r["removed"] == 0 and _rows(db) == []


class TestEnrichCoupangEvent:
    """② 쿠팡 행에 목록을 얹는다 — 시각은 쿠팡이, 옵션ID는 우리가 안다."""

    def _viid_event(self, db, *, before=1, after=3, added=2, removed=0,
                    when="2026-08-04T03:00:00Z"):
        hist.ingest_events(db, ACC, [{
            "campaignId": int(CID), "executionTime": when, "executionId": "exec-1",
            "changes": [{"changeType": "VIID", "before": before, "after": after,
                         "added": added, "removed": removed}]}])
        db.flush()

    def test_개수가_맞으면_쿠팡_행에_붙고_우리_행은_안_생긴다(self, db):
        cre.ingest_ads(db, ACC, [_ad("1", 111)], detected_at=T1)
        self._viid_event(db)
        r = cre.ingest_ads(db, ACC, [_ad("1", 111), _ad("2", 222), _ad("3", 333)],
                           detected_at=T2)
        assert r["enriched"] == 1 and r.get("own_rows", 0) == 0
        assert _rows(db, op=cre.OP_ADS_ADDED) == []          # ★두 줄이 되지 않는다
        row = _rows(db, op=hist.OP_ADS_CHANGED)[0]
        d = json.loads(row.detail_json)
        assert {o["vendor_item_id"] for o in d["options"]} == {"222", "333"}
        assert d["options_of"] == "added"
        assert row.time_basis == "src"                        # 시각은 쿠팡 것이 남는다

    def test_개수가_어긋나면_붙이지_않고_우리_행을_낸다(self, db):
        """엉뚱한 사건에 목록을 다느니 따로 내는 게 낫다."""
        cre.ingest_ads(db, ACC, [_ad("1", 111)], detected_at=T1)
        self._viid_event(db, added=5)                          # 쿠팡은 5개 추가라고 한다
        r = cre.ingest_ads(db, ACC, [_ad("1", 111), _ad("2", 222)], detected_at=T2)
        assert r["enriched"] == 0 and r["own_rows"] == 1
        assert len(_rows(db, op=cre.OP_ADS_ADDED)) == 1
        assert json.loads(_rows(db, op=hist.OP_ADS_CHANGED)[0].detail_json).get("options") is None

    def test_이미_목록이_붙은_행에는_다시_안_붙인다(self, db):
        cre.ingest_ads(db, ACC, [_ad("1", 111)], detected_at=T1)
        self._viid_event(db)
        cre.ingest_ads(db, ACC, [_ad("1", 111), _ad("2", 222), _ad("3", 333)], detected_at=T2)
        # 다음 회차에 또 2개가 늘면, 이미 채워진 행이 아니라 새 행이 나와야 한다.
        r = cre.ingest_ads(db, ACC, [_ad("1", 111), _ad("2", 222), _ad("3", 333),
                                     _ad("4", 444), _ad("5", 555)],
                           detected_at=datetime(2026, 8, 4, 9, 0, 0))
        assert r["enriched"] == 0 and r["own_rows"] == 1

    def test_다른_캠페인_이벤트에는_안_붙는다(self, db):
        cre.ingest_ads(db, ACC, [_ad("1", 111, cid="OTHER")], detected_at=T1)
        self._viid_event(db)                                    # CID 이벤트
        r = cre.ingest_ads(db, ACC, [_ad("1", 111, cid="OTHER"), _ad("2", 222, cid="OTHER"),
                                     _ad("3", 333, cid="OTHER")], detected_at=T2)
        assert r["enriched"] == 0 and r["own_rows"] == 1


class TestAdFieldChanges:
    def test_소재별_입찰가_오버라이드_변경이_잡힌다(self, db):
        cre.ingest_ads(db, ACC, [_ad("1", 111, override=None)], detected_at=T1)
        r = cre.ingest_ads(db, ACC, [_ad("1", 111, override=250)], detected_at=T2)
        assert r["field_changes"] == 1
        row = _rows(db, entity_type=cre.ENTITY_AD, field="pricingOverride")[0]
        assert (row.before_value, row.after_value) == (None, "250")

    def test_소재_정지도_잡힌다(self, db):
        cre.ingest_ads(db, ACC, [_ad("1", 111)], detected_at=T1)
        cre.ingest_ads(db, ACC, [_ad("1", 111, suspended=True)], detected_at=T2)
        row = _rows(db, entity_type=cre.ENTITY_AD, field="isSuspended")[0]
        assert (row.before_value, row.after_value) == ("false", "true")

    def test_성과성_잡음은_필드에_없다(self, db):
        """servingStatus 같은 걸 넣으면 매 회차가 '전 소재 수정됨'이 된다."""
        assert set(cre.AD_FIELDS) == {"isActive", "isSuspended", "pricingOverride"}


class TestSafety:
    def test_같은_회차를_두_번_넣어도_안_는다(self, db):
        cre.ingest_ads(db, ACC, [_ad("1", 111)], detected_at=T1)
        cre.ingest_ads(db, ACC, [_ad("1", 111), _ad("2", 222)], detected_at=T2)
        n = len(_rows(db))
        cre.ingest_ads(db, ACC, [_ad("1", 111), _ad("2", 222)], detected_at=T2)
        assert len(_rows(db)) == n

    def test_모르는_계정은_거부(self, db):
        with pytest.raises(ValueError):
            cre.ingest_ads(db, "naver", [_ad("1", 111)])
