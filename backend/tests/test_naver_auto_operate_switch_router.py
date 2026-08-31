# test_naver_auto_operate_switch_router.py — H1(계약 P2): PUT /campaign-settings/auto-operate
#
# ★존재 이유: 이 저장소엔 `auto_operate`를 «켜는» API가 **없었다**(2026-08-27 전수 확인이
#   ignition_preflight 머리주석에 기록돼 있다). 점화는 prod DB 직접 UPDATE였고, 그래서
#   ①감사 행이 앱 코드 밖에서 생기고 ②끄고 켜는 손이 사람에게 없어 제외 재개방이 10일째
#   밀려 있었다(2026-08-31 실측: due 1건, 그 캠페인 auto_operate=0).
#
# ★★이 파일의 핵심 회귀는 **W3(재개방 경고)**다. 다른 자동 조치는 실행 harness의
#   `optimizer=='ours'` 하드체크에 걸리는데, 제외 재개방(`_open_exclusion`)만 harness를 안 탄다.
#   그래서 **optimizer='none'인 캠페인이라도 이 플래그를 켜면 네이버 실쓰기가 나간다.**
#   그 사실을 켜기 «전에» 말하지 않으면 아무도 모른다 — 경고가 사라지는 변이를 여기서 잡는다.
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    NaverAdgroupScope,
    NaverCampaignSettings,
    NaverChangeLog,
    NaverSearchTermExclusion,
)
from app.services.naver_ad import ignition_preflight
from app.utils.kst import kst_now, kst_today

CID = "cmp-test-h1"
AGID = "grp-test-h1"


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


def _put(client, cid=CID, on=True):
    return client.put(
        "/api/naver/ad/campaign-settings/auto-operate",
        json={"campaign_id": cid, "auto_operate": on},
    )


def _seed_due_exclusion(db, *, campaign_id=CID, adgroup_id=AGID, days_overdue=10, term="아이패드종이필름"):
    """재심사 개방이 «오늘 도래한» 제외 1건. 레인의 후보 조건과 같은 모양으로 심는다."""
    db.add(NaverSearchTermExclusion(
        campaign_id=campaign_id,
        adgroup_id=adgroup_id,
        search_term=term,
        status="excluded",
        cycle=1,
        # ★NOT NULL — 픽스처는 prod 스키마와 같아야 한다(교훈: test-fixture-must-match-prod).
        excluded_at=kst_now(),
        last_transition_at=kst_now(),
        next_review_at=kst_today() - timedelta(days=days_overdue),
        live_state="alive",
        source=None,  # NULL = 우리 실행분
    ))
    db.commit()


# ── 기본 동작 ────────────────────────────────────────────────────────────────

def test_switch_preserves_every_other_field(client, db):
    """★핵심: auto_operate만 바꾸고 나머지는 손대지 않는다(optimizer 스위치와 같은 계약)."""
    db.add(NaverCampaignSettings(
        campaign_id=CID, optimizer="ours", mode="growth",
        target_roas_override=Decimal("2.5"), gamma=Decimal("1.2"), memo="03 태깅",
        auto_operate=False, loss_policy="leash",
    ))
    db.commit()

    r = _put(client, on=True)
    assert r.status_code == 200, r.text

    row = db.query(NaverCampaignSettings).filter_by(campaign_id=CID).one()
    db.refresh(row)
    assert row.auto_operate is True
    # ★나머지 전부 불변 — 여기가 깨지면 「필드 하나 바꾸는 동작」이 전체 치환으로 퇴화한 것이다.
    assert row.optimizer == "ours"
    assert row.mode == "growth"
    assert row.target_roas_override == Decimal("2.5")
    assert row.gamma == Decimal("1.2")
    assert row.memo == "03 태깅"
    assert row.loss_policy == "leash"


def test_switch_creates_row_when_absent(client, db):
    """행이 없던 캠페인 — 이 플래그만 세팅하고 optimizer 기본값을 지어내지 않는다."""
    r = _put(client, on=True)
    assert r.status_code == 200, r.text
    row = db.query(NaverCampaignSettings).filter_by(campaign_id=CID).one()
    assert row.auto_operate is True
    assert row.optimizer in (None, "none")  # ★임의 기본값 발명 금지


def test_response_carries_auto_operate(client, db):
    """★응답에 현재값이 실린다 — 누른 뒤 결과를 같은 표면에서 읽을 수 있어야 한다.

    종전 `_serialize_settings`는 optimizer만 실어서 캠페인 설정 API로는 킬스위치의 현재값을
    **아예 볼 수 없었다.** 이 키가 사라지는 변이를 잡는다.
    """
    assert _put(client, on=True).json()["auto_operate"] is True
    assert _put(client, on=False).json()["auto_operate"] is False


# ── 감사 로그 ────────────────────────────────────────────────────────────────

def test_switch_writes_audit_record(client, db):
    _put(client, on=True)
    logs = db.query(NaverChangeLog).filter_by(action="auto_operate_change").all()
    assert len(logs) == 1
    assert logs[0].campaign_id == CID
    assert logs[0].before_value == "off"
    assert logs[0].after_value == "on"


def test_switch_no_audit_when_unchanged(client, db):
    """값이 그대로면 감사 행을 만들지 않는다 — 원장이 「눌렀다」로 부풀지 않게."""
    db.add(NaverCampaignSettings(campaign_id=CID, auto_operate=True))
    db.commit()
    _put(client, on=True)
    assert db.query(NaverChangeLog).filter_by(action="auto_operate_change").count() == 0


def test_audit_changed_at_is_kst_not_utc(client, db):
    """★`changed_at`을 명시 전달하지 않으면 server_default=func.now()가 먹어 **UTC**로 박힌다.

    판별 규칙(n=74 실측으로 확정): `kst_now()` 명시 스탬프는 **소수점(microsecond)이 있고**,
    SQLite `CURRENT_TIMESTAMP`는 없다. 이 라우터에서 같은 함정이 네 번째라 회귀로 못 박는다.
    """
    _put(client, on=True)
    log = db.query(NaverChangeLog).filter_by(action="auto_operate_change").one()
    assert log.changed_at is not None
    assert log.changed_at.microsecond != 0, "changed_at이 UTC server_default로 박혔다(소수점 없음)"


# ── 입력 경계 ────────────────────────────────────────────────────────────────

def test_switch_rejects_optimizer_field(client):
    """★두 스위치는 층이 다르다 — 한 요청으로 둘을 바꾸면 감사에서 의도를 못 가른다."""
    r = client.put(
        "/api/naver/ad/campaign-settings/auto-operate",
        json={"campaign_id": CID, "auto_operate": True, "optimizer": "ours"},
    )
    assert r.status_code == 422


def test_switch_does_not_touch_optimizer_of_existing_row(client, db):
    """켜기가 optimizer를 'ours'로 승격시키지 않는다(권한 상승 금지)."""
    db.add(NaverCampaignSettings(campaign_id=CID, optimizer="none", auto_operate=False))
    db.commit()
    _put(client, on=True)
    db.refresh(db.query(NaverCampaignSettings).filter_by(campaign_id=CID).one())
    assert db.query(NaverCampaignSettings).filter_by(campaign_id=CID).one().optimizer == "none"


# ── preflight 부착 ───────────────────────────────────────────────────────────

def test_preflight_attached_on_turn_on_only(client, db):
    """켜는 요청엔 항상 붙이고(경고 0건이어도 — 교훈 #123), 끄는 요청엔 안 붙인다."""
    assert "ignition_preflight" in _put(client, on=True).json()
    assert "ignition_preflight" not in _put(client, on=False).json()


# ── ★★W3 — 켜면 나가는 «네이버 실쓰기»를 켜기 전에 말한다 ────────────────────

def test_preflight_warns_about_due_reopen(client, db):
    """★핵심 회귀: 재개방 due 건이 있으면 켜기 응답이 그 건수를 말한다."""
    _seed_due_exclusion(db)
    body = _put(client, on=True).json()
    codes = [w["code"] for w in body["ignition_preflight"]["warnings"]]
    assert ignition_preflight.WARN_REOPEN_DUE in codes
    warn = next(w for w in body["ignition_preflight"]["warnings"]
                if w["code"] == ignition_preflight.WARN_REOPEN_DUE)
    assert warn["detail"]["terms"][0]["search_term"] == "아이패드종이필름"
    assert body["ignition_preflight"]["safe_to_ignite"] is False


def test_preflight_warns_even_when_optimizer_is_none(client, db):
    """★★이 파일의 존재 이유 — 재개방은 harness를 안 타므로 optimizer='none'이어도 나간다.

    「optimizer가 none이니 안전하다」는 optimizer 스위치의 문장이고, 이 경로엔 **적용되지
    않는다.** 경고를 optimizer로 가리는 변이를 여기서 잡는다.
    """
    db.add(NaverCampaignSettings(campaign_id=CID, optimizer="none", auto_operate=False))
    db.commit()
    _seed_due_exclusion(db)
    pf = _put(client, on=True).json()["ignition_preflight"]
    assert pf["optimizer"] == "none"
    assert ignition_preflight.WARN_REOPEN_DUE in [w["code"] for w in pf["warnings"]]


def test_preflight_no_reopen_warning_when_not_due(client, db):
    """미래 재심일은 세지 않는다 — 「열린다」고 해 놓고 안 열리면 경고를 아무도 안 믿는다."""
    _seed_due_exclusion(db, days_overdue=-10)  # 10일 «뒤»
    pf = _put(client, on=True).json()["ignition_preflight"]
    assert ignition_preflight.WARN_REOPEN_DUE not in [w["code"] for w in pf["warnings"]]


def test_preflight_excludes_scope_blocked_rows(client, db):
    """스코프가 막는 행은 실제로 안 열리므로 세지 않는다."""
    _seed_due_exclusion(db)
    # 다른 그룹만 enabled → 우리 due 행(AGID)은 스코프 밖 = 안 열림
    db.add(NaverAdgroupScope(campaign_id=CID, adgroup_id="grp-other", enabled=True))
    db.commit()
    pf = _put(client, on=True).json()["ignition_preflight"]
    assert ignition_preflight.WARN_REOPEN_DUE not in [w["code"] for w in pf["warnings"]]


def test_preflight_reopen_warning_is_campaign_scoped(client, db):
    """남의 캠페인 due를 내 경고로 옮기지 않는다(W2와 같은 규율)."""
    _seed_due_exclusion(db, campaign_id="cmp-someone-else", adgroup_id="grp-else")
    pf = _put(client, on=True).json()["ignition_preflight"]
    assert ignition_preflight.WARN_REOPEN_DUE not in [w["code"] for w in pf["warnings"]]


def test_preflight_surfaces_source_for_each_due_row(client, db):
    """★계약 §5 금지선: `console_import`는 재개방 대상이 아닌데 레인 후보 쿼리엔 source 필터가
    «없다». 숨기지 말고 행마다 실어 사람이 가릴 수 있게 한다."""
    _seed_due_exclusion(db)
    db.add(NaverSearchTermExclusion(
        campaign_id=CID, adgroup_id=AGID, search_term="대행사가건것",
        status="excluded", cycle=1, excluded_at=kst_now(), last_transition_at=kst_now(),
        next_review_at=kst_today() - timedelta(days=1),
        live_state="alive", source="console_import",
    ))
    db.commit()
    pf = _put(client, on=True).json()["ignition_preflight"]
    warn = next(w for w in pf["warnings"] if w["code"] == ignition_preflight.WARN_REOPEN_DUE)
    sources = {t["search_term"]: t["source"] for t in warn["detail"]["terms"]}
    assert sources["아이패드종이필름"] is None
    assert sources["대행사가건것"] == "console_import"
