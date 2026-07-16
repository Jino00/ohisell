# test_naver_optimizer_switch_router.py — D-NAO-48: PUT /campaign-settings/optimizer
# ★존재 이유: 기존 PUT /campaign-settings는 **전체 치환**이다(settings.mode = body.mode를
#   무조건 대입). 스위치가 optimizer만 보내면 mode·target_roas_override·gamma·memo가 전부
#   null로 날아간다 — 03에 남긴 태깅 메모까지. 콘솔은 memo를 되돌려보내 겨우 막고 있는데
#   (NaverAdOptimizationConsole.tsx:344), gamma는 api.ts에 파라미터조차 없어 **저장할 때마다
#   조용히 지워진다**(스펙 §1-6이 지적한 그 괴리의 실제 결과).
#   "필드 하나 바꾸는 동작"에 전체 치환 PUT을 쓰는 게 애초에 틀렸다 → 좁은 엔드포인트로
#   그 실수를 구조적으로 불가능하게 만든다.
from __future__ import annotations

import pytest
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import NaverCampaignSettings, NaverChangeLog


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


def _put(client, cid, optimizer):
    return client.put("/api/naver/ad/campaign-settings/optimizer",
                      json={"campaign_id": cid, "optimizer": optimizer})


def test_switch_preserves_every_other_field(client, db):
    """★핵심: optimizer만 바꾸고 나머지는 손대지 않는다. 전체 치환 PUT의 함정 차단."""
    db.add(NaverCampaignSettings(
        campaign_id="cmp-1", optimizer="none", mode="growth",
        target_roas_override=Decimal("3.5"), gamma=Decimal("0.7"), memo="소중한 메모",
    ))
    db.commit()

    r = _put(client, "cmp-1", "ours")
    assert r.status_code == 200

    s = db.query(NaverCampaignSettings).filter_by(campaign_id="cmp-1").one()
    db.refresh(s)
    assert s.optimizer == "ours"
    assert s.mode == "growth", "mode가 날아감"
    assert s.target_roas_override == Decimal("3.5"), "override가 날아감"
    assert s.gamma == Decimal("0.7"), "gamma가 날아감"
    assert s.memo == "소중한 메모", "memo가 날아감"


def test_switch_creates_row_when_absent(client, db):
    """설정 행이 없던 캠페인도 스위치로 바로 넘길 수 있어야 한다(카나리 확대)."""
    r = _put(client, "cmp-new", "ours")
    assert r.status_code == 200
    s = db.query(NaverCampaignSettings).filter_by(campaign_id="cmp-new").one()
    assert s.optimizer == "ours"
    assert s.mode is None  # 없던 건 없는 채로 — 임의 기본값을 지어내지 않는다


def test_switch_writes_audit_record(client, db):
    """D-NAO-13: 관리주체 전환은 감사 기록이 남아야 한다(기존 PUT과 동일 계약)."""
    _put(client, "cmp-1", "ours")
    logs = db.query(NaverChangeLog).filter(NaverChangeLog.action == "optimizer_change").all()
    assert len(logs) == 1
    assert logs[0].entity_id == "cmp-1"
    assert "none" in (logs[0].before_value or "")
    assert "ours" in (logs[0].after_value or "")


def test_switch_no_audit_when_unchanged(client, db):
    """같은 값으로 다시 눌러도 감사 로그를 늘리지 않는다(기존 PUT 관례와 동일)."""
    db.add(NaverCampaignSettings(campaign_id="cmp-1", optimizer="ours"))
    db.commit()
    _put(client, "cmp-1", "ours")
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "optimizer_change").count() == 0


def test_switch_rejects_invalid_optimizer(client):
    assert _put(client, "cmp-1", "bogus").status_code == 422


def test_switch_accepts_all_three_states(client, db):
    """★3단 스위치(D-48-a): 우리/원본MOP/수동. 2단이면 'mop'을 표현 못 해
    3열 대조의 MOP 열이 죽는다."""
    for opt in ("ours", "mop", "none"):
        assert _put(client, "cmp-1", opt).status_code == 200
        db.expire_all()
        assert db.query(NaverCampaignSettings).filter_by(campaign_id="cmp-1").one().optimizer == opt


# ── codex[P1] 2026-07-17: optimizer 쓰기 경로는 하나여야 한다 ──
def test_full_put_without_optimizer_preserves_it(client, db):
    """★codex[P1]: 콘솔의 전체 치환 PUT이 optimizer를 같이 보내면 **확인창을 우회**해
    캠페인이 라이브 쓰기 대상이 된다(MOP 경고 없이). 그리고 stale 편집 버퍼가 나중에
    커밋되면 스위치로 끈 걸 다시 'ours'로 되돌려 **아무도 의도하지 않은 채 쓰기가 재무장**된다.

    → optimizer를 optional로 만들고, 생략하면 **건드리지 않는다**. 콘솔은 이제 안 보낸다
    (모드·공격성 전용). optimizer의 유일한 쓰기 경로 = PUT /campaign-settings/optimizer."""
    db.add(NaverCampaignSettings(campaign_id="cmp-1", optimizer="ours", mode="growth"))
    db.commit()

    r = client.put("/api/naver/ad/campaign-settings",
                   json={"campaign_id": "cmp-1", "mode": "defense"})  # optimizer 없음
    assert r.status_code == 200
    assert r.json()["optimizer"] == "ours", "optimizer가 생략됐는데 바뀌었다"
    assert r.json()["mode"] == "defense"


def test_full_put_without_optimizer_defaults_none_for_new_row(client, db):
    """설정 행이 없던 캠페인에 모드만 저장하면 optimizer는 'none'(자동화 안 함)에서 시작한다
    — 기본값이 'ours'면 모드 저장만으로 라이브 쓰기가 켜진다."""
    r = client.put("/api/naver/ad/campaign-settings",
                   json={"campaign_id": "cmp-new", "mode": "growth"})
    assert r.status_code == 200
    assert r.json()["optimizer"] == "none"


def test_full_put_without_optimizer_writes_no_audit(client, db):
    db.add(NaverCampaignSettings(campaign_id="cmp-1", optimizer="ours"))
    db.commit()
    client.put("/api/naver/ad/campaign-settings", json={"campaign_id": "cmp-1", "mode": "growth"})
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "optimizer_change").count() == 0
