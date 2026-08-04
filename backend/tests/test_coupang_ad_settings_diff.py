# test_coupang_ad_settings_diff.py — 쿠팡 광고 설정 변경 탐지 (트랙 coupang-ad-change-log)
#
# 지키는 불변식 넷:
#   ① **성과는 변경이 아니다.** spentBudget이 20분에 6,501→6,592로 움직이는 걸 실측했다.
#      이게 변경 행을 만들면 매일 아침 "전 캠페인 수정됨"이 뜬다(네이버 editTm 피드 재적용 오염과 동류).
#   ② **On/Off와 내용은 다른 축이다**(D-CAC-3). On/Off는 전량에서, 내용은 활성만.
#      한 사건이 두 줄로 새지 않아야 한다.
#   ③ **모르는 걸 아는 척하지 않는다.** 발생 시각은 쿠팡 updatedAt이 있을 때만 'src'다.
#      설정을 처음 보는 엔티티는 field_change를 한 줄도 만들지 않는다(기준선일 뿐이다).
#   ④ **조회 실패를 삭제로 오독하지 않는다.** 전량이 비면 회차를 통째로 건너뛴다.
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import CoupangAdChangeLog, CoupangAdEntitySnapshot
from app.services.coupang import ad_settings_diff as mod

ACC = mod.ACCOUNT_OHITECH
T1 = datetime(2026, 8, 4, 1, 0, 0)   # 회차 1 감지 시각(UTC naive)
T2 = datetime(2026, 8, 4, 2, 0, 0)   # 회차 2
UPD = "2026-08-04T01:51:21.372Z"     # 라이브 실측값(메츨 싱) — KST 10:51:21
UPD_DT = datetime(2026, 8, 4, 1, 51, 21, 372000)


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


def _campaign(cid="104882010", name="[매.최] 메츨 싱", *, active=True, budget=70000,
              roas=340, updated=UPD, groups=None, **extra):
    """라이브 응답에서 관측된 모양 그대로. 성과 필드도 실제처럼 섞어 넣는다."""
    c = {
        "id": int(cid), "name": name, "isActive": active, "isDeleted": False,
        "budget": budget, "calculatedBudget": budget, "capType": "dailySoft",
        "roasTarget": roas, "targetType": "roas", "isSuspended": False,
        "campaignType": "PA", "goalType": "SALES", "isAgencyManaged": False,
        "startDate": "2026-04-28T08:12:22.555Z", "endDate": None,
        "createdAt": "2026-04-28T08:12:22.555Z", "updatedAt": updated,
        # ★성과·파생 필드 — 변경 행을 만들면 안 된다
        "spentBudget": 6501.49, "averageTimeBudgetUtilRate": 100, "totalAdCount": 3,
        "servingStatus": {"status": "DELIVERABLE", "hints": []},
        "learningStatus": {"status": "LEARNING_COMPLETED", "calculatedAt": "2026-07-23T04:07:21Z"},
        "version": 1,
        "groupList": groups if groups is not None else [],
    }
    c.update(extra)
    return c


def _group(gid="205607158", cid="104882010", *, name="새 광고 그룹", pricing=100,
           bid="AUTO_BID", roas=340, updated="2026-06-26T13:12:01.000Z", active=True):
    return {
        "id": gid, "paCampaignId": int(cid), "name": name, "isActive": active,
        "bidType": bid, "pricingType": "CPC", "pricing": pricing, "roasTarget": roas,
        "andonRoasTarget": None, "keywordTargeting": "AUTOMATIC", "isDeleted": False,
        "budget": None, "budgetType": None, "startDate": None, "endDate": None,
        "adSelectionType": None, "smartTargetingPricingType": None,
        "smartTargetingPricing": None, "adDiscountOptIn": False,
        "createdAt": "2026-06-26T13:12:01.000Z", "updatedAt": updated, "version": 2,
    }


def _thin(c: dict) -> dict:
    """전량 조회가 실제로 쓰는 세 필드만 — A축은 이것만 있으면 된다."""
    return {"id": c["id"], "name": c["name"], "isActive": c["isActive"],
            "updatedAt": c.get("updatedAt"), "createdAt": c.get("createdAt")}


def _logs(db, **f):
    q = db.query(CoupangAdChangeLog)
    for k, v in f.items():
        q = q.filter(getattr(CoupangAdChangeLog, k) == v)
    return q.order_by(CoupangAdChangeLog.id).all()


def _seed(db, campaigns):
    """회차 1 — 기준선을 만든다(여기서 나오는 created는 테스트 관심사가 아니다)."""
    return mod.ingest(db, ACC, [_thin(c) for c in campaigns],
                      [c for c in campaigns if c["isActive"]], detected_at=T1)


class TestPerformanceFieldsAreNotChanges:
    """① 성과는 변경이 아니다 — 이걸 놓치면 화면이 매일 거짓말을 한다."""

    def test_spentBudget만_움직이면_변경_0건(self, db):
        _seed(db, [_campaign()])
        after = _campaign(**{"spentBudget": 6592.87, "averageTimeBudgetUtilRate": 97,
                             "totalAdCount": 5})
        r = mod.ingest(db, ACC, [_thin(after)], [after], detected_at=T2)
        assert r["changes"] == 0
        assert _logs(db, op=mod.OP_FIELD) == []

    def test_learningStatus_servingStatus_변동도_0건(self, db):
        _seed(db, [_campaign()])
        after = _campaign(**{
            "servingStatus": {"status": "DELIVERABLE_PARTIAL_ADS_ERROR", "hints": [{"a": 1}]},
            "learningStatus": {"status": "LEARNING_IN_PROGRESS", "calculatedAt": "2026-08-04T01:00:00Z"},
        })
        r = mod.ingest(db, ACC, [_thin(after)], [after], detected_at=T2)
        assert r["changes"] == 0


class TestOnOffAxis:
    """② On/Off는 전량에서 잡고, 내용 축과 섞이지 않는다."""

    def test_Off되면_turned_off_한_줄(self, db):
        _seed(db, [_campaign()])
        off = _campaign(active=False)
        # Off면 활성 조회에서 빠진다 — 실제 호출과 같은 모양.
        mod.ingest(db, ACC, [_thin(off)], [], detected_at=T2)
        rows = _logs(db, op=mod.OP_TURNED_OFF)
        assert len(rows) == 1
        assert (rows[0].before_value, rows[0].after_value) == ("on", "off")
        assert _logs(db, op=mod.OP_FIELD) == []   # 같은 사건이 내용 축으로 새지 않는다

    def test_On되면_turned_on_한_줄(self, db):
        off = _campaign(active=False)
        mod.ingest(db, ACC, [_thin(off)], [], detected_at=T1)
        on = _campaign(active=True)
        mod.ingest(db, ACC, [_thin(on)], [on], detected_at=T2)
        rows = _logs(db, op=mod.OP_TURNED_ON)
        assert len(rows) == 1
        assert rows[0].after_value == "on"

    def test_isActive는_field_change로_안_뜬다(self, db):
        """허용목록에 isActive가 없다 — 있으면 On/Off가 두 줄이 된다."""
        assert "isActive" not in mod.CAMPAIGN_FIELDS

    def test_비활성_캠페인의_내용은_보지_않는다(self, db):
        """D-CAC-3: 내용 diff는 활성만. 525개를 매번 필드 비교하지 않기 위한 규칙."""
        _seed(db, [_campaign()])
        off = _campaign(active=False, budget=999999)     # 예산이 바뀌었지만 Off다
        mod.ingest(db, ACC, [_thin(off)], [], detected_at=T2)
        assert _logs(db, op=mod.OP_FIELD) == []


class TestContentDiff:
    """내용 변경 — 전/후 값과 발생 시각이 함께 나와야 합격이다(계약 ④)."""

    def test_예산_변경이_전후값과_함께_잡힌다(self, db):
        _seed(db, [_campaign(budget=70000)])
        after = _campaign(budget=110000, updated="2026-08-04T02:10:00.000Z")
        mod.ingest(db, ACC, [_thin(after)], [after], detected_at=T2)
        rows = _logs(db, op=mod.OP_FIELD, field="budget")
        assert len(rows) == 1
        assert (rows[0].before_value, rows[0].after_value) == ("70000", "110000")
        assert rows[0].occurred_at == datetime(2026, 8, 4, 2, 10, 0)
        assert rows[0].time_basis == "src"

    def test_roasTarget_변경(self, db):
        _seed(db, [_campaign(roas=340)])
        after = _campaign(roas=310, updated="2026-08-04T02:10:00.000Z")
        mod.ingest(db, ACC, [_thin(after)], [after], detected_at=T2)
        rows = _logs(db, op=mod.OP_FIELD, field="roasTarget")
        assert (rows[0].before_value, rows[0].after_value) == ("340", "310")

    def test_광고그룹_입찰가_변경이_잡힌다(self, db):
        _seed(db, [_campaign(groups=[_group(pricing=100)])])
        after = _campaign(groups=[_group(pricing=250, updated="2026-08-04T02:20:00.000Z")])
        mod.ingest(db, ACC, [_thin(after)], [after], detected_at=T2)
        rows = _logs(db, entity_type=mod.ENTITY_ADGROUP, field="pricing")
        assert len(rows) == 1
        assert (rows[0].before_value, rows[0].after_value) == ("100", "250")
        assert rows[0].campaign_id == "104882010"       # 어느 캠페인 밑인지 붙어 있다
        assert rows[0].occurred_at == datetime(2026, 8, 4, 2, 20, 0)

    def test_변경_없으면_0건(self, db):
        _seed(db, [_campaign(groups=[_group()])])
        c = _campaign(groups=[_group()])
        r = mod.ingest(db, ACC, [_thin(c)], [c], detected_at=T2)
        assert r["changes"] == 0


class TestTimeBasis:
    """③ 발생 시각을 아는 척하지 않는다."""

    def test_updatedAt이_있으면_src(self, db):
        _seed(db, [_campaign(budget=70000)])
        after = _campaign(budget=80000, updated=UPD)
        mod.ingest(db, ACC, [_thin(after)], [after], detected_at=T2)
        row = _logs(db, op=mod.OP_FIELD)[0]
        assert row.time_basis == "src" and row.occurred_at == UPD_DT

    def test_updatedAt이_없으면_detected(self, db):
        _seed(db, [_campaign(budget=70000, updated=None)])
        after = _campaign(budget=80000, updated=None)
        mod.ingest(db, ACC, [_thin(after)], [after], detected_at=T2)
        row = _logs(db, op=mod.OP_FIELD)[0]
        assert row.time_basis == "detected" and row.occurred_at == T2

    def test_설정을_처음_본_엔티티는_field_change를_안_만든다(self, db):
        """Off였다가 On된 캠페인 — 그 사이 설정을 추적한 적이 없다. 없던 걸 쏟아내면 거짓이다."""
        off = _campaign(active=False)
        mod.ingest(db, ACC, [_thin(off)], [], detected_at=T1)
        on = _campaign(active=True, budget=123456)
        mod.ingest(db, ACC, [_thin(on)], [on], detected_at=T2)
        assert _logs(db, op=mod.OP_FIELD) == []          # 기준선일 뿐
        assert len(_logs(db, op=mod.OP_TURNED_ON)) == 1  # On 자체는 잡힌다


class TestLifecycle:
    """신규·삭제 — Off와 삭제는 다른 사건이다."""

    def test_신규_캠페인은_created_한_줄이고_필드_스팸이_없다(self, db):
        r = mod.ingest(db, ACC, [_thin(_campaign())], [_campaign()], detected_at=T1)
        assert len(_logs(db, op=mod.OP_CREATED, entity_type=mod.ENTITY_CAMPAIGN)) == 1
        assert _logs(db, op=mod.OP_FIELD) == []
        assert r["campaigns_total"] == 1 and r["campaigns_active"] == 1

    def test_전량에서_사라지면_deleted(self, db):
        a, bcamp = _campaign(), _campaign(cid="999", name="다른 캠페인")
        _seed(db, [a, bcamp])
        mod.ingest(db, ACC, [_thin(a)], [a], detected_at=T2)
        rows = _logs(db, op=mod.OP_DELETED)
        assert len(rows) == 1 and rows[0].entity_id == "999"

    def test_삭제는_한_번만_기록된다(self, db):
        a, bcamp = _campaign(), _campaign(cid="999")
        _seed(db, [a, bcamp])
        mod.ingest(db, ACC, [_thin(a)], [a], detected_at=T2)
        mod.ingest(db, ACC, [_thin(a)], [a], detected_at=datetime(2026, 8, 4, 3, 0, 0))
        assert len(_logs(db, op=mod.OP_DELETED)) == 1

    def test_Off는_삭제가_아니다(self, db):
        _seed(db, [_campaign()])
        mod.ingest(db, ACC, [_thin(_campaign(active=False))], [], detected_at=T2)
        assert _logs(db, op=mod.OP_DELETED) == []


class TestSafety:
    """④ 조회 실패를 사실로 착각하지 않는다 + 회차 재실행이 이력을 부풀리지 않는다."""

    def test_전량이_비면_회차를_통째로_건너뛴다(self, db):
        _seed(db, [_campaign()])
        r = mod.ingest(db, ACC, [], [], detected_at=T2)
        assert r["skipped"] == "empty_all_rows"
        assert _logs(db, op=mod.OP_DELETED) == []        # 삭제로 오독하지 않는다

    def test_같은_입력을_두_번_넣어도_행이_안_는다(self, db):
        _seed(db, [_campaign(budget=70000)])
        after = _campaign(budget=90000, updated="2026-08-04T02:10:00.000Z")
        first = mod.ingest(db, ACC, [_thin(after)], [after], detected_at=T2)
        n = len(_logs(db))
        again = mod.ingest(db, ACC, [_thin(after)], [after],
                           detected_at=datetime(2026, 8, 4, 5, 0, 0))
        assert first["changes"] >= 1 and again["changes"] == 0
        assert len(_logs(db)) == n

    def test_계정이_섞이지_않는다(self, db):
        _seed(db, [_campaign()])
        # 같은 캠페인 id가 다른 계정에 있어도 서로의 이력을 건드리지 않는다.
        mod.ingest(db, mod.ACCOUNT_OFIX, [_thin(_campaign())], [_campaign()], detected_at=T2)
        assert len(_logs(db, account=mod.ACCOUNT_OFIX, op=mod.OP_CREATED)) == 1
        assert len(_logs(db, account=ACC, op=mod.OP_CREATED)) == 1
        snaps = db.query(CoupangAdEntitySnapshot).filter(
            CoupangAdEntitySnapshot.entity_id == "104882010").all()
        assert {s.account for s in snaps} == {mod.ACCOUNT_OFIX, ACC}

    def test_모르는_계정은_거부한다(self, db):
        with pytest.raises(ValueError):
            mod.ingest(db, "naver", [_thin(_campaign())], [], detected_at=T1)


class TestSnapshotState:
    """스냅샷이 '전' 값으로 실제 기능하는지."""

    def test_활성_조회에서만_settings가_갱신된다(self, db):
        """전량 조회는 id·name·isActive만 준다 — 그걸로 덮으면 설정이 통째로 날아간다."""
        _seed(db, [_campaign(budget=70000)])
        snap = db.query(CoupangAdEntitySnapshot).filter_by(
            account=ACC, entity_type=mod.ENTITY_CAMPAIGN, entity_id="104882010").one()
        assert snap.settings_json and '"budget": "70000"' in snap.settings_json
        # Off → 활성 조회에서 빠짐. 설정은 마지막으로 추적한 값이 남아야 한다.
        mod.ingest(db, ACC, [_thin(_campaign(active=False))], [], detected_at=T2)
        db.refresh(snap)
        assert '"budget": "70000"' in snap.settings_json
        assert snap.is_active is False and snap.present is True
