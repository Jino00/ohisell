# test_naver_loss_policy_router.py — UI1(D-NAO-65): PUT /campaign-settings/loss-policy
# ★존재 이유: loss 대응 정책(고삐 vs 종전 하드 정지)은 캠페인별 예외 스위치다(§0). 쓰기 경로는
#   이 전용 PUT 하나뿐 — optimizer 스위치와 같은 원칙(전체 치환 PUT의 필드 유실 함정 차단,
#   D-NAO-53). NULL=leash 정규화로 무변경 감사 로그를 남기지 않는다.
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


def _put(client, cid, loss_policy):
    return client.put("/api/naver/ad/campaign-settings/loss-policy",
                      json={"campaign_id": cid, "loss_policy": loss_policy})


def test_loss_policy_switch_creates_row_none_optimizer(client, db):
    """없던 캠페인도 정책 스위치로 바로 설정 가능 — 단 optimizer는 'none'에서 시작한다(정책만
    저장했다고 라이브 쓰기가 켜지면 안 됨)."""
    r = _put(client, "cmp-new", "stoploss_pause")
    assert r.status_code == 200
    assert r.json()["loss_policy"] == "stoploss_pause"
    s = db.query(NaverCampaignSettings).filter_by(campaign_id="cmp-new").one()
    assert s.loss_policy == "stoploss_pause"
    assert s.optimizer == "none"


def test_loss_policy_switch_preserves_other_fields(client, db):
    """★핵심: loss_policy만 바꾸고 나머지(optimizer/mode/override/gamma/memo)는 손대지 않는다."""
    db.add(NaverCampaignSettings(
        campaign_id="cmp-1", optimizer="ours", mode="growth",
        target_roas_override=Decimal("3.5"), gamma=Decimal("0.7"), memo="소중한 메모",
    ))
    db.commit()

    r = _put(client, "cmp-1", "stoploss_pause")
    assert r.status_code == 200
    s = db.query(NaverCampaignSettings).filter_by(campaign_id="cmp-1").one()
    db.refresh(s)
    assert s.loss_policy == "stoploss_pause"
    assert s.optimizer == "ours", "optimizer가 날아감"
    assert s.mode == "growth"
    assert s.target_roas_override == Decimal("3.5")
    assert s.gamma == Decimal("0.7")
    assert s.memo == "소중한 메모"


def test_loss_policy_switch_writes_audit_record(client, db):
    """실제 변경(NULL→stoploss_pause)은 경량 감사 기록(action='set_loss_policy')이 남는다.
    NULL=leash 정규화라 before_value는 'leash'로 기록된다(정직 표기)."""
    _put(client, "cmp-1", "stoploss_pause")
    logs = db.query(NaverChangeLog).filter(NaverChangeLog.action == "set_loss_policy").all()
    assert len(logs) == 1
    assert logs[0].entity_id == "cmp-1"
    assert logs[0].before_value == "leash"
    assert logs[0].after_value == "stoploss_pause"


def test_loss_policy_switch_null_to_leash_writes_no_audit(client, db):
    """NULL→'leash'는 실질 무변경(둘 다 기본 고삐) — 감사 로그를 늘리지 않는다(정규화 비교)."""
    db.add(NaverCampaignSettings(campaign_id="cmp-1", optimizer="ours"))  # loss_policy NULL
    db.commit()
    r = _put(client, "cmp-1", "leash")
    assert r.status_code == 200
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "set_loss_policy").count() == 0


def test_loss_policy_switch_no_audit_when_unchanged(client, db):
    """같은 값(stoploss_pause)으로 다시 눌러도 감사 로그를 늘리지 않는다."""
    db.add(NaverCampaignSettings(campaign_id="cmp-1", optimizer="ours", loss_policy="stoploss_pause"))
    db.commit()
    _put(client, "cmp-1", "stoploss_pause")
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "set_loss_policy").count() == 0


def test_loss_policy_switch_back_to_leash_writes_audit(client, db):
    """stoploss_pause→leash는 실질 변경이라 감사 기록이 남는다(정책 회귀도 추적)."""
    db.add(NaverCampaignSettings(campaign_id="cmp-1", optimizer="ours", loss_policy="stoploss_pause"))
    db.commit()
    _put(client, "cmp-1", "leash")
    logs = db.query(NaverChangeLog).filter(NaverChangeLog.action == "set_loss_policy").all()
    assert len(logs) == 1
    assert logs[0].before_value == "stoploss_pause"
    assert logs[0].after_value == "leash"


def test_loss_policy_switch_rejects_invalid_value(client):
    assert _put(client, "cmp-1", "bogus").status_code == 422


def test_loss_policy_switch_rejects_extra_field(client):
    """extra='forbid': 이 엔드포인트로 optimizer 등 다른 필드를 밀어넣을 수 없다(쓰기 경로 분리)."""
    r = client.put("/api/naver/ad/campaign-settings/loss-policy",
                   json={"campaign_id": "cmp-1", "loss_policy": "leash", "optimizer": "ours"})
    assert r.status_code == 422


def test_full_put_rejects_loss_policy(client, db):
    """전체 치환 PUT /campaign-settings는 loss_policy를 받지 않는다(extra='forbid') — 정책
    쓰기의 유일 경로가 전용 엔드포인트임을 구조로 강제."""
    r = client.put("/api/naver/ad/campaign-settings",
                   json={"campaign_id": "cmp-1", "loss_policy": "stoploss_pause"})
    assert r.status_code == 422


def test_get_campaign_settings_includes_loss_policy(client, db):
    """GET /campaign-settings 응답에 loss_policy가 포함된다(콘솔 스위치 렌더용)."""
    db.add(NaverCampaignSettings(campaign_id="cmp-1", optimizer="ours", loss_policy="stoploss_pause"))
    db.add(NaverCampaignSettings(campaign_id="cmp-2", optimizer="ours"))  # NULL
    db.commit()
    r = client.get("/api/naver/ad/campaign-settings")
    assert r.status_code == 200
    rows = {row["campaign_id"]: row for row in r.json()["rows"]}
    assert rows["cmp-1"]["loss_policy"] == "stoploss_pause"
    assert rows["cmp-2"]["loss_policy"] is None  # NULL은 콘솔에서 기본값 leash로 해석


def test_loss_policy_round_trip_all_states(client, db):
    """정책 3상태 round-trip: leash / stoploss_pause / 다시 leash."""
    for lp in ("leash", "stoploss_pause", "leash"):
        assert _put(client, "cmp-1", lp).status_code == 200
        db.expire_all()
        assert db.query(NaverCampaignSettings).filter_by(campaign_id="cmp-1").one().loss_policy == lp
