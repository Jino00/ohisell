# test_pao_scope_roster.py — PAO 스코프 대시보드 (D-NAO-244)
#
# Jino 원문 2026-08-24: *"ohisell에 PAO 메뉴를 만들어서 어떤 캠페인 - 광고그룹 을 돌릴지,
# 그 성과는 어떻게 나오는지 보여주는 대시보드를 같이 만들자"*
#
# 원칙22: SA 단위테스트는 라우터를 안 거치므로 라우터 레이어 500을 못 잡는다 — HTTP 왕복으로 쓴다.
#
# ★이 파일의 핵심은 **표면 절단 변이**다(계약 §4 마지막 항목): 스코프 상태가 «API 응답까지»
#   실제로 도달하는지를 묻는다. 하니스가 값을 잘 «계산»해도 응답에 안 실리면 화면은 아무것도
#   모른다 — 이 트랙이 네 번 밟은 병이 정확히 그것이다(LESSONS_LEARNED #346 계열).
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    NaverAdDaily,
    NaverAdgroupScope,
    NaverCampaignSettings,
    NaverEntity,
)
from app.services.naver_ad import pao_scope_roster
from app.utils.kst import kst_today

CAMPAIGN = "cmp-tpu"
G_IN = "grp-s25fe"
G_OUT = "grp-z8wide"
YESTERDAY = kst_today() - timedelta(days=1)


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


def _seed(db, *, scope=True, enabled=True, role="accel"):
    db.add_all([
        NaverEntity(entity_type="campaign", entity_id=CAMPAIGN,
                    campaign_id=CAMPAIGN, name="● 01. 갤럭시_지문방지_TPU", campaign_type="SHOPPING"),
        NaverEntity(entity_type="adgroup", entity_id=G_IN,
                    campaign_id=CAMPAIGN, parent_id=CAMPAIGN, name="S25FE"),
        NaverEntity(entity_type="adgroup", entity_id=G_OUT,
                    campaign_id=CAMPAIGN, parent_id=CAMPAIGN, name="Z폴드8와이드"),
        NaverCampaignSettings(campaign_id=CAMPAIGN, optimizer="ours", auto_operate=False),
    ])
    for gid, cost, conv in ((G_IN, 10_000, 30_000), (G_OUT, 90_000, 40_000)):
        db.add(NaverAdDaily(
            ad_date=YESTERDAY, campaign_id=CAMPAIGN, campaign_type="SHOPPING",
            adgroup_id=gid, keyword_id="", imp=100, clk=10, cost=cost,
            conv_direct_cnt=1, conv_indirect_cnt=0,
            conv_direct_amt=conv, conv_indirect_amt=0,
        ))
    if scope:
        db.add(NaverAdgroupScope(
            campaign_id=CAMPAIGN, adgroup_id=G_IN, role=role, enabled=enabled,
        ))
    db.commit()


def _campaign(payload):
    return next(c for c in payload["campaigns"] if c["campaign_id"] == CAMPAIGN)


def _group(payload, gid):
    return next(g for g in _campaign(payload)["adgroups"] if g["adgroup_id"] == gid)


# ──────────────────────────────────────────────────────────────────────────
# ★표면 절단 변이 — 스코프 상태가 «API 응답까지» 도달하는가
# ──────────────────────────────────────────────────────────────────────────

def test_surface_scope_fields_reach_the_api_response(client_and_session):
    """★★계약 §4의 표면 절단 변이 대상.

    하니스가 스코프를 잘 «판정»해도 응답에 안 실리면 화면은 아무것도 모른다. 이 테스트는
    `in_scope`·`scope_role`을 **HTTP 응답 본문에서** 확인한다 — 직렬화에서 그 키를 지우거나
    스코프 조인을 끊으면 여기서 죽는다."""
    client, db = client_and_session
    _seed(db, scope=True, role="accel")

    r = client.get(f"/api/naver/ad/scope/roster?campaign_id={CAMPAIGN}")
    assert r.status_code == 200
    payload = r.json()

    g_in = _group(payload, G_IN)
    assert g_in["in_scope"] is True
    assert g_in["scope_role"] == "accel"
    assert g_in["scope_enabled"] is True

    g_out = _group(payload, G_OUT)
    assert g_out["in_scope"] is False
    assert g_out["scope_role"] is None


def test_surface_performance_fields_reach_the_api_response(client_and_session):
    """성과 열(광고비·클릭·전환·ROAS·총이익)도 응답까지 간다 — 화면의 두 번째 질문."""
    client, db = client_and_session
    _seed(db)

    payload = client.get(f"/api/naver/ad/scope/roster?campaign_id={CAMPAIGN}").json()
    g = _group(payload, G_OUT)
    for key in ("cost", "imp", "clk", "conv_amt", "roas", "gross_profit", "profit_status", "bep_roas"):
        assert key in g, f"응답에 {key}가 없다 — 화면이 그 열을 그릴 수 없다"
    assert g["cost"] == 90_000


def test_surface_campaign_has_scope_flag(client_and_session):
    """캠페인 행의 `has_scope` — 「이 캠페인은 일부만 맡긴 상태」를 화면이 말할 수 있어야 한다.

    이 플래그가 참이면 캠페인 레벨 액션(예산)이 hold된다는 뜻이라, 운영자가 알아야 할 상태다."""
    client, db = client_and_session
    _seed(db, scope=True)

    payload = client.get(f"/api/naver/ad/scope/roster?campaign_id={CAMPAIGN}").json()
    c = _campaign(payload)
    assert c["has_scope"] is True
    assert c["scoped_count"] == 1


def test_no_scope_rows_reports_unrestricted(client_and_session):
    """스코프 행이 없으면 has_scope=False — 전 그룹이 대상이라는 뜻(진리표 2행)."""
    client, db = client_and_session
    _seed(db, scope=False)

    payload = client.get(f"/api/naver/ad/scope/roster?campaign_id={CAMPAIGN}").json()
    c = _campaign(payload)
    assert c["has_scope"] is False
    assert c["scoped_count"] == 0
    assert all(g["in_scope"] is False for g in c["adgroups"])


def test_disabled_scope_row_is_not_in_scope_but_still_shown(client_and_session):
    """enabled=False는 «맡기지 않음»이되 화면에서 사라지면 안 된다 — 왜 꺼졌는지 보여야 한다."""
    client, db = client_and_session
    _seed(db, scope=True, enabled=False, role="brake")

    g = _group(client.get(f"/api/naver/ad/scope/roster?campaign_id={CAMPAIGN}").json(), G_IN)
    assert g["in_scope"] is False
    assert g["scope_enabled"] is False
    assert g["scope_role"] == "brake"  # 역할은 남아 있다


# ──────────────────────────────────────────────────────────────────────────
# 총이익 — 0원과 «모름»을 구분한다
# ──────────────────────────────────────────────────────────────────────────

def test_profit_is_none_when_bep_unknown(client_and_session):
    """BEP 해석 불가면 숫자를 지어내지 않는다(profit_scorecard §4-1 '숫자 조작 금지').

    ★0원과 «모름»은 다른 값이다 — 화면이 이걸 0으로 그리면 적자 그룹이 손익분기로 보인다."""
    client, db = client_and_session
    _seed(db)  # 상품 매핑·BEP 없음

    g = _group(client.get(f"/api/naver/ad/scope/roster?campaign_id={CAMPAIGN}").json(), G_IN)
    assert g["gross_profit"] is None
    assert g["profit_status"] == "bep_unknown"


def test_profit_formula_matches_profit_scorecard():
    """총이익 = (Σconv_amt × factor) ÷ bep_roas − Σcost — profit_scorecard와 같은 산식.

    산식이 갈라지면 같은 캠페인의 총이익이 화면과 Slack에서 다르게 나온다."""
    profit, status = pao_scope_roster._profit(
        conv_amt=1_711_000, cost=1_000_000, factor=Decimal(1), bep_roas=Decimal("1.711"),
    )
    assert status == "ok"
    assert profit == 0  # 1,711,000 / 1.711 = 1,000,000 → 총이익 0(정확히 손익분기)

    profit2, _ = pao_scope_roster._profit(
        conv_amt=3_422_000, cost=1_000_000, factor=Decimal(1), bep_roas=Decimal("1.711"),
    )
    assert profit2 == 1_000_000


def test_profit_none_when_bep_zero_or_negative():
    """0·음수 BEP로 나누지 않는다(ZeroDivision·부호 뒤집힘 방지)."""
    assert pao_scope_roster._profit(1, 1, factor=Decimal(1), bep_roas=Decimal(0))[0] is None
    assert pao_scope_roster._profit(1, 1, factor=Decimal(1), bep_roas=None)[0] is None


# ──────────────────────────────────────────────────────────────────────────
# ★BEP 사다리 캐시 (적대 리뷰 P2-2 상환) — «답을 바꾸지 않는다»가 안전 속성이다
# ──────────────────────────────────────────────────────────────────────────

def test_bep_cache_does_not_change_the_answer(client_and_session):
    """캐시를 넘기든 안 넘기든 **같은 값**이 나와야 한다.

    성능 최적화가 값을 바꾸면 그건 최적화가 아니라 버그다. prod 실측 10.1초를 줄이려고
    요청 단위 메모를 넣었는데, 그 메모가 답을 흔들면 총이익이 화면마다 달라진다."""
    from app.services.naver_ad import exploration

    client, db = client_and_session
    _seed(db)

    for gid in (G_IN, G_OUT):
        no_cache = exploration.resolve_exploration_bep_roas(db, CAMPAIGN, gid)
        with_cache = exploration.resolve_exploration_bep_roas(db, CAMPAIGN, gid, cache={})
        assert no_cache == with_cache


def test_bep_cache_default_is_no_cache(client_and_session):
    """★기본값이 None이라 기존 호출부(레인)는 동작이 «완전히» 그대로다 — 소급 0.

    레인처럼 오래 도는 경로에 장수 캐시가 붙으면 그 사이 바뀐 BEP를 못 본다. 그래서 캐시는
    옵트인이고, 넘길지는 호출부가 정한다."""
    from app.services.naver_ad import exploration
    import inspect

    sig = inspect.signature(exploration.resolve_exploration_bep_roas)
    assert sig.parameters["cache"].default is None


def test_bep_cache_is_reused_across_groups(client_and_session):
    """캐시가 실제로 재사용되는가 — 캠페인·계정 tier 조회가 그룹 수만큼 반복되지 않는다."""
    from app.services.naver_ad import campaign_target_resolver, exploration

    client, db = client_and_session
    _seed(db)
    cache: dict = {}
    with patch.object(
        campaign_target_resolver, "account_default_bep_roas", return_value=None
    ) as mock_acct:
        for gid in (G_IN, G_OUT):
            exploration.resolve_exploration_bep_roas(db, CAMPAIGN, gid, cache=cache)
    # 그룹 2개를 돌았지만 계정 tier 조회는 1회여야 한다
    assert mock_acct.call_count == 1


# ──────────────────────────────────────────────────────────────────────────
# 쓰기 경로 — 스코프 행 upsert / 삭제
# ──────────────────────────────────────────────────────────────────────────

def test_put_scope_adgroup_upserts(client_and_session):
    client, db = client_and_session
    _seed(db, scope=False)

    r = client.put("/api/naver/ad/scope/adgroup", json={
        "campaign_id": CAMPAIGN, "adgroup_id": G_IN, "role": "brake", "enabled": True,
    })
    assert r.status_code == 200
    assert r.json()["role"] == "brake"

    # 같은 행 재호출 = 갱신(중복 생성 아님)
    r2 = client.put("/api/naver/ad/scope/adgroup", json={
        "campaign_id": CAMPAIGN, "adgroup_id": G_IN, "role": "accel", "enabled": False,
    })
    assert r2.json()["role"] == "accel"
    assert r2.json()["enabled"] is False
    assert db.query(NaverAdgroupScope).count() == 1


def test_put_scope_rejects_bogus_role(client_and_session):
    client, db = client_and_session
    _seed(db, scope=False)
    r = client.put("/api/naver/ad/scope/adgroup", json={
        "campaign_id": CAMPAIGN, "adgroup_id": G_IN, "role": "액셀밟아", "enabled": True,
    })
    assert r.status_code == 422


def test_delete_last_row_reports_campaign_now_unrestricted(client_and_session):
    """★마지막 행을 지우면 캠페인이 «전 그룹 대상»으로 돌아간다 — 조용히 넓어지면 안 된다.

    일부만 끄고 싶으면 삭제가 아니라 enabled=false다. 둘의 결과가 정반대라 응답이 이 사실을
    명시적으로 말한다(화면이 확인창을 띄울 근거)."""
    client, db = client_and_session
    _seed(db, scope=True)

    r = client.delete(f"/api/naver/ad/scope/adgroup?campaign_id={CAMPAIGN}&adgroup_id={G_IN}")
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    assert r.json()["remaining_rows"] == 0
    assert r.json()["campaign_now_unrestricted"] is True


def test_scope_write_does_not_turn_the_engine_on(client_and_session):
    """★스코프 행을 넣어도 auto_operate는 그대로다 — 켜는 것은 별도 결정이다.

    n=45가 8개 목록 중 2개만 하고 스위치를 눌렀던 사고의 구조적 방지: 이 엔드포인트에는
    엔진을 켜는 경로 자체가 없다."""
    client, db = client_and_session
    _seed(db, scope=False)

    client.put("/api/naver/ad/scope/adgroup", json={
        "campaign_id": CAMPAIGN, "adgroup_id": G_IN, "role": "accel", "enabled": True,
    })
    s = db.query(NaverCampaignSettings).filter(
        NaverCampaignSettings.campaign_id == CAMPAIGN
    ).one()
    db.refresh(s)
    assert s.auto_operate is False
