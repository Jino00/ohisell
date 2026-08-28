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

from contextlib import contextmanager
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
from app.services.naver_ad import pao_scope_roster, probe_cell_aggregate
from app.utils.kst import kst_today

CAMPAIGN = "cmp-tpu"
G_IN = "grp-s25fe"
G_OUT = "grp-z8wide"


def _recent_weekday(*, before: date | None = None) -> date:
    """창 안의 «평시»(평일 ∧ 공휴일 아님) 날짜 하나 — 어제부터 거슬러 올라가며 찾는다.

    ★왜 「어제」를 그대로 안 쓰나 (D-NAO-267): 램프업 판정(교란축 X9)이 붙은 뒤로 이 픽스처는
      **요일에 의존**하게 됐다. 하루치만 심는데 그 하루가 토·일·공휴일이면 그룹의 평시 관측이
      0일이 되어 `profit_status='ramp_up'`이 되고, 총이익을 단언하는 기존 테스트가 **주말에만
      빨개진다.** 이런 건 CI가 평일에 도는 한 안 보이다가 어느 일요일에 터진다.

      그래서 픽스처가 요일을 «고른다». 이 함수가 램프업 판정 자체를 우회하는 게 아니다 —
      램프업은 아래 전용 테스트가 평시 0일 그룹을 따로 심어서 검증한다.
    """
    d = (before or kst_today()) - timedelta(days=1)
    while probe_cell_aggregate.env_cell_of_date(d) != "weekday":
        d -= timedelta(days=1)
    return d


YESTERDAY = _recent_weekday()


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
# ★총이익 «구간» — 있는 그대로 + [하한, 상한] (Jino 지시 2026-08-24)
# ──────────────────────────────────────────────────────────────────────────

def test_profit_band_primary_value_is_uncorrected():
    """★`gross_profit`은 **보정 없는 «있는 그대로»**여야 한다.

    Jino 원문: *"보정계수(1.3016)를 왜 쓰는거야? 있는 그대로를 보여줘야 하는거 아니야?"*
    초판은 여기에 `factor_high`(상한)를 실었고, 그 결과 TPU 21일이 무보정 −864,081원인데
    화면엔 +557,591원으로 떠 **부호가 뒤집혔다**. 기본값이 보정값으로 되돌아가면 이 테스트가
    죽는다."""
    band = pao_scope_roster._profit_band(
        conv_amt=1_711_000, cost=1_000_000, bep_roas=Decimal("1.711"),
        factor_low=Decimal("0.5"), factor_high=Decimal("2.0"),
    )
    # 보정 없이는 정확히 손익분기(1,711,000 / 1.711 = 1,000,000)
    assert band["gross_profit"] == 0
    # 구간 양끝은 그 양쪽으로 벌어진다
    assert band["gross_profit_low"] == -500_000
    assert band["gross_profit_high"] == 1_000_000
    assert band["profit_status"] == "ok"


def test_profit_band_does_not_always_contain_the_raw_value():
    """★적대 리뷰 P2-7 채택 — 「구간」이 「있는 그대로」를 **감싸지 않는 경우가 있다**.

    보정계수 구간은 `low=min(floor, point)` · `high=max(floor, point)`이고 floor=0.827이다.
    점추정도 1 미만일 수 있어(자체 문서화된 실측 스프레드 **0.8289~0.8862**) `high < 1`이 되면
    `gross_profit_high < gross_profit(raw)`가 된다 — 즉 큰 글씨가 자기 괄호 «밖»에 놓인다.

    ★이건 결함이 아니라 **성질**이다: 보정은 「convAmt를 실매출로 환산」하는 것이라 양끝이 둘 다
    1보다 작으면 raw보다 둘 다 작은 게 맞다. 다만 화면이 「구간이 감싼다」고 오해시키면 안 되므로
    `NaverAdScope.ProfitCell`이 그 경우 ⚠️로 표시한다. 이 테스트는 그 성질을 **문서로 고정**한다
    — 나중에 누가 「구간이 raw를 감싸야 한다」고 클램프를 넣으면 여기서 죽는다."""
    # 양끝이 둘 다 1 미만 → high < raw
    band = pao_scope_roster._profit_band(
        conv_amt=1_000_000, cost=300_000, bep_roas=Decimal("1.5"),
        factor_low=Decimal("0.827"), factor_high=Decimal("0.9"),
    )
    assert band["gross_profit"] == 366_667      # raw
    assert band["gross_profit_high"] == 300_000  # ★raw보다 «작다»
    assert band["gross_profit_high"] < band["gross_profit"]

    # 양끝이 둘 다 1 초과 → low > raw
    band2 = pao_scope_roster._profit_band(
        conv_amt=1_000_000, cost=300_000, bep_roas=Decimal("1.5"),
        factor_low=Decimal("1.1"), factor_high=Decimal("1.4"),
    )
    assert band2["gross_profit_low"] > band2["gross_profit"]


def test_profit_band_all_three_are_none_when_bep_unknown():
    """BEP를 모르면 세 값 전부 None — 구간이라고 숫자를 지어내지 않는다."""
    band = pao_scope_roster._profit_band(
        conv_amt=100, cost=100, bep_roas=None,
        factor_low=Decimal("0.8"), factor_high=Decimal("1.3"),
    )
    assert band["gross_profit"] is None
    assert band["gross_profit_low"] is None
    assert band["gross_profit_high"] is None
    assert band["profit_status"] == "bep_unknown"


def test_surface_profit_band_reaches_the_api_response(client_and_session):
    """★표면: 구간이 **API 응답까지** 간다 — 화면이 «얼마나 모르는지»를 그릴 수 있어야 한다.

    직렬화에서 `gross_profit_low`/`_high`를 빼면 화면은 단일값만 받게 되고, 그러면
    「채널 매출 100%가 광고 공」이라는 가정이 사실처럼 읽힌다."""
    client, db = client_and_session
    _seed(db)

    payload = client.get(f"/api/naver/ad/scope/roster?campaign_id={CAMPAIGN}").json()
    # 응답 헤더가 단일 value가 아니라 구간을 실어야 한다
    assert "low" in payload["correction_factor"]
    assert "high" in payload["correction_factor"]
    assert "value" not in payload["correction_factor"], "단일값이 부활하면 화면이 그걸 집는다"

    g = _group(payload, G_IN)
    for key in ("gross_profit", "gross_profit_low", "gross_profit_high"):
        assert key in g, f"응답에 {key}가 없다 — 화면이 구간을 그릴 수 없다"
    c = _campaign(payload)
    for key in ("gross_profit", "gross_profit_low", "gross_profit_high"):
        assert key in c, f"캠페인 합계에 {key}가 없다"


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
    """캐시가 실제로 재사용되는가 — 캠페인·계정 tier 조회가 그룹 수만큼 반복되지 않는다.

    ★적대 리뷰 P2-6 채택: 초판은 **계정 tier 호출 횟수만** 셌다. 캠페인 tier도 같이 세어
    비대칭을 없앤다 — 한쪽만 재면 나머지 tier의 캐시 로직 회귀를 못 잡는다."""
    from app.services.naver_ad import campaign_target_resolver, exploration

    client, db = client_and_session
    _seed(db)
    cache: dict = {}
    with patch.object(
        campaign_target_resolver, "weighted_product_value_for_campaign", return_value=None
    ) as mock_camp, patch.object(
        campaign_target_resolver, "account_default_bep_roas", return_value=None
    ) as mock_acct:
        for gid in (G_IN, G_OUT):
            exploration.resolve_exploration_bep_roas(db, CAMPAIGN, gid, cache=cache)
    # 그룹 2개를 돌았지만 캠페인·계정 tier 조회는 각 1회여야 한다
    assert mock_camp.call_count == 1
    assert mock_acct.call_count == 1


def test_shared_cache_does_not_leak_group_tier_across_groups(client_and_session):
    """★★적대 리뷰 P2-5 채택 — 이 파일에서 가장 중요한 테스트.

    실사용(`pao_scope_roster.build_roster`)은 **한 캠페인 안 여러 그룹이 «같은» cache dict를
    공유**한다. 그런데 초판 테스트는 그룹마다 `cache={}`를 새로 만들어 비교해서, 그 공유
    상황을 한 번도 재현하지 않았다.

    리뷰가 변이로 증명했다: 「①그룹 tier를 캠페인 단위 키로 캐싱」(설계가 명시적으로 금지한
    바로 그 실수)을 주입하니 **324건이 전부 생존**했고, 실제로는 그룹 B가 그룹 A의 BEP를
    돌려받는다. 캐시가 답을 바꾸는 회귀가 실재 가능한데 아무도 안 보고 있었던 것이다.

    ⇒ 두 그룹에 **서로 다른** 그룹 tier BEP를 주고 같은 cache로 연달아 부른 뒤,
    **각자 자기 값**이 나오는지 단언한다. 그룹 tier를 캐시하는 순간 이 테스트가 죽는다."""
    from decimal import Decimal as D

    from app.services.naver_ad import campaign_target_resolver, exploration

    client, db = client_and_session
    _seed(db)

    per_group = {G_IN: D("2.5"), G_OUT: D("3.3")}
    cache: dict = {}
    with patch.object(
        campaign_target_resolver, "weighted_product_value_for_adgroup",
        side_effect=lambda _db, adgroup_id, _col: per_group[adgroup_id],
    ):
        got_in = exploration.resolve_exploration_bep_roas(db, CAMPAIGN, G_IN, cache=cache)
        got_out = exploration.resolve_exploration_bep_roas(db, CAMPAIGN, G_OUT, cache=cache)

    assert got_in == D("2.5")
    assert got_out == D("3.3"), "그룹 tier가 캐시로 오염됐다 — 두 그룹이 같은 BEP를 받았다"


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


# ──────────────────────────────────────────────────────────────────────────
# ★교란축 X9 — 「신규 그룹 램프업」 (D-NAO-267 · M2 계약 §4-C S2-④ / ref 63 §10)
#
# 계약 원문: *"신규 그룹(X9) 라벨이 붙은 그룹은 밴드 확정값 «대신» 「램프업」 표기"*.
# 판정은 발명하지 않았다 — ref 63 §10의 「baseline 부재」(평시 표본 0일)를 그대로 옮겼다.
# ──────────────────────────────────────────────────────────────────────────

G_RAMP = "grp-newly-created"
_TEST_BEP = Decimal("1.711")  # 계정 BEP 실측치 — 새 상수가 아니라 기존 값


def _recent_weekend(*, before: date | None = None) -> date:
    """창 안의 «비평시»(주말 또는 공휴일) 날짜 — 램프업 그룹을 심을 자리."""
    d = (before or kst_today()) - timedelta(days=1)
    while probe_cell_aggregate.env_cell_of_date(d) == "weekday":
        d -= timedelta(days=1)
    return d


@contextmanager
def _bep_resolved(value: Decimal = _TEST_BEP):
    """BEP 사다리의 계정 tier를 채워 `profit_status='ok'`가 나오게 한다.

    ★램프업 테스트엔 이게 **필수**다 — BEP가 없으면 bep_unknown이 이겨서(더 깊은 막힘이
      먼저다) 램프업 라벨 자체가 안 나온다. 그 우선순위는 아래 전용 테스트가 따로 지킨다.
    """
    from app.services.naver_ad import campaign_target_resolver
    with patch.object(campaign_target_resolver, "account_default_bep_roas", return_value=value):
        yield


def _seed_ramp_up_group(db) -> date:
    """평시 관측이 **0일**인 그룹 — 비평시 날짜에만 집행이 있다(신규 그룹의 모양).

    ref 63 §10이 실제로 발견한 사례(TPU3)와 같은 구조다: *"TPU3는 평시(baseline) 관측이
    0건이다. 즉 이 그룹들의 적자는 「평시 대비 악화」가 아니라 「평시가 존재한 적 없는 신규
    그룹의 초기 구간」"*.
    """
    weekend = _recent_weekend()
    db.add(NaverEntity(entity_type="adgroup", entity_id=G_RAMP,
                       campaign_id=CAMPAIGN, parent_id=CAMPAIGN, name="신규 그룹"))
    db.add(NaverAdDaily(
        ad_date=weekend, campaign_id=CAMPAIGN, campaign_type="SHOPPING",
        adgroup_id=G_RAMP, keyword_id="", imp=500, clk=50, cost=70_000,
        conv_direct_cnt=1, conv_indirect_cnt=0,
        conv_direct_amt=200_000, conv_indirect_amt=0,
    ))
    db.commit()
    return weekend


def _roster(client):
    return client.get(f"/api/naver/ad/scope/roster?campaign_id={CAMPAIGN}").json()


def test_ramp_up_replaces_the_band_value_not_annotates_it(client_and_session):
    """★핵심 — 램프업이면 확정 밴드값이 «지워진다».

    라벨만 붙이고 숫자를 남겨 두면 화면이 숫자를 집어 들고 라벨은 장식이 된다. 계약이
    「밴드 확정값 **대신**」이라고 쓴 이유가 그것이다. gross_profit 3종이 전부 null이어야 한다.
    """
    client, db = client_and_session
    _seed(db)
    _seed_ramp_up_group(db)

    with _bep_resolved():
        g = _group(_roster(client), G_RAMP)

    assert g["profit_status"] == "ramp_up"
    assert g["baseline_days"] == 0
    assert g["gross_profit"] is None, "확정값이 남아 있으면 라벨이 장식이 된다"
    assert g["gross_profit_low"] is None
    assert g["gross_profit_high"] is None
    # 비용·전환 같은 «관측값»은 그대로 있어야 한다 — 못 재는 건 밴드 판정이지 집행 사실이 아니다
    assert g["cost"] == 70_000
    assert g["conv_amt"] == 200_000


def test_group_with_weekday_baseline_is_not_ramp_up(client_and_session):
    """대조군 — 평시 관측이 있으면 램프업이 아니다(라벨이 전건에 붙는 버그 방어)."""
    client, db = client_and_session
    _seed(db)

    with _bep_resolved():
        g = _group(_roster(client), G_OUT)

    assert g["baseline_days"] >= 1
    assert g["profit_status"] == "ok"
    assert g["gross_profit"] is not None


def test_bep_unknown_beats_ramp_up(client_and_session):
    """★사유 우선순위 — 둘 다 해당하면 **bep_unknown**이 이긴다.

    bep_unknown은 「애초에 못 잰다」(원가 미연결 — 사람이 고쳐야 풀림)이고 ramp_up은
    「재도 의미 없다」(시간이 풀어 줌)다. 램프업을 위에 씌우면 평일이 지나 라벨이 풀린
    **뒤에야** 원가 미연결을 알게 된다 — 같은 그룹에서 두 번 놀란다.
    """
    client, db = client_and_session
    _seed(db)
    _seed_ramp_up_group(db)  # BEP 패치 없음 = 사다리 전 tier 미해석

    g = _group(_roster(client), G_RAMP)
    assert g["baseline_days"] == 0          # 램프업 조건은 성립하는데
    assert g["profit_status"] == "bep_unknown"  # 더 깊은 막힘을 먼저 말한다


def test_surface_ramp_up_reaches_the_api_response(client_and_session):
    """★★표면 절단 변이 대상 (계약 §4-C 공통).

    하니스가 램프업을 잘 «판정»해도 응답 키가 없으면 화면은 그 그룹을 평범한 「모름」으로
    그린다 — 그러면 신규 그룹의 초기 잡음이 「상품 원가 미연결」과 한 칸에 뭉개진다.
    `profit_status`·`baseline_days`를 **HTTP 응답 본문에서** 확인한다(교훈 #321: 서비스층
    확인은 `response_model`이 키를 지우는 경우를 못 잡는다).
    """
    client, db = client_and_session
    _seed(db)
    _seed_ramp_up_group(db)

    with _bep_resolved():
        r = client.get(f"/api/naver/ad/scope/roster?campaign_id={CAMPAIGN}")
    assert r.status_code == 200
    g = _group(r.json(), G_RAMP)
    for key in ("profit_status", "baseline_days"):
        assert key in g, f"응답에 {key}가 없다 — 화면이 램프업을 말할 수 없다"
    assert g["profit_status"] == "ramp_up"


def test_campaign_says_how_many_groups_ramp_up_removed(client_and_session):
    """★캠페인 총이익에서 «빠진» 그룹 수가 응답에 있다.

    램프업 그룹은 총이익 합산에서 빠지는데, 그 사실을 말하지 않으면 캠페인 총이익이 그냥
    「그만큼인 값」으로 읽힌다. 「모름」이 「0원」으로 읽히는 자리를 카운터로 가른다.
    """
    client, db = client_and_session
    _seed(db)
    _seed_ramp_up_group(db)

    with _bep_resolved():
        c = _campaign(_roster(client))

    assert c["ramp_up_count"] == 1
    known = [g for g in c["adgroups"] if g["gross_profit"] is not None]
    assert known, "대조군이 없으면 이 테스트가 아무것도 안 지킨다"
    assert c["gross_profit"] == sum(g["gross_profit"] for g in known)
    # 램프업 그룹의 광고비 7만원은 «집행 사실»이라 캠페인 합에 그대로 남아야 한다 —
    # 빠지는 건 이익 판정뿐이다
    assert c["cost"] == 10_000 + 90_000 + 70_000


def test_campaign_ramp_up_count_is_zero_when_none(client_and_session):
    """램프업이 없으면 0 — 키가 조건부로 사라지면 화면이 분기를 못 그린다."""
    client, db = client_and_session
    _seed(db)

    with _bep_resolved():
        c = _campaign(_roster(client))
    assert c["ramp_up_count"] == 0


def test_scope_only_group_is_bep_unknown_not_ramp_up(client_and_session):
    """창 안 집행이 아예 없는 스코프 그룹은 «관측 없음»이지 램프업이 아니다.

    둘 다 baseline_days=0이지만 사유가 다르다 — 섞으면 「아직 안 돌린 그룹」과 「막 만든
    그룹」이 같은 라벨을 받는다.
    """
    client, db = client_and_session
    _seed(db, scope=True)
    db.add(NaverAdgroupScope(campaign_id=CAMPAIGN, adgroup_id="grp-never-run",
                             role="brake", enabled=True))
    db.commit()

    with _bep_resolved():
        g = _group(_roster(client), "grp-never-run")
    assert g["profit_status"] == "bep_unknown"
    assert g["baseline_days"] == 0


# ──────────────────────────────────────────────────────────────────────────
# ★평시/주말/공휴일 «날짜 grain» 분리 (D-NAO-267 · 적대 리뷰 1R P1-1의 처방)
#
# retro 쪽 분리(day_class_rollup)는 asof_date로 가르는데 성과는 사후창에서 난다 —
# d7은 어느 발신일이든 주말 2일을 포함해 분리가 원리적으로 0이다. ref 63의 축은
# `ad_profit_{g,d}`(당일)이라, 그 질문에 답하는 자리는 날짜 grain이 실재하는 여기다.
# ──────────────────────────────────────────────────────────────────────────

def _seed_across_day_classes(db) -> dict[str, int]:
    """평시·주말 각각에 집행을 심는다 → 칸이 실제로 갈리는지 볼 수 있다."""
    weekday, weekend = YESTERDAY, _recent_weekend()
    for d, cost in ((weekday, 10_000), (weekend, 3_000)):
        db.add(NaverAdDaily(
            ad_date=d, campaign_id=CAMPAIGN, campaign_type="SHOPPING",
            adgroup_id="grp-split", keyword_id="", imp=100, clk=10, cost=cost,
            conv_direct_cnt=1, conv_indirect_cnt=0,
            conv_direct_amt=cost * 2, conv_indirect_amt=0,
        ))
    db.add(NaverEntity(entity_type="adgroup", entity_id="grp-split",
                       campaign_id=CAMPAIGN, parent_id=CAMPAIGN, name="분리 대상"))
    db.commit()
    return {"weekday_cost": 10_000, "weekend_cost": 3_000}


def test_surface_day_class_split_reaches_the_api_response(client_and_session):
    """★★표면 절단 변이 대상 — 분리 열이 HTTP 응답 본문까지 간다."""
    client, db = client_and_session
    _seed(db)
    _seed_across_day_classes(db)

    body = _roster(client)
    assert "weekend_holiday" in body, "분리 열이 없다 — 화면은 여전히 섞인 값만 본다"
    split = body["weekend_holiday"]
    for bucket in ("weekday", "weekend", "holiday"):
        assert bucket in split
        for key in ("days", "cost", "imp", "clk", "conv_amt", "roas"):
            assert key in split[bucket], f"{bucket}.{key}가 없다"


def test_day_class_split_actually_separates(client_and_session):
    """평시와 주말이 «다른 칸»에 들어간다 — 한 칸에 뭉치면 분리가 아니다."""
    client, db = client_and_session
    _seed(db)
    seeded = _seed_across_day_classes(db)

    split = _roster(client)["weekend_holiday"]
    # _seed()가 심은 평시 2건(10,000+90,000) + 분리용 평시 1건(10,000)
    assert split["weekday"]["cost"] == 100_000 + seeded["weekday_cost"]
    assert split["weekend"]["cost"] == seeded["weekend_cost"]
    assert split["weekday"]["days"] >= 1
    assert split["weekend"]["days"] == 1


def test_day_class_split_identity_holds_against_the_same_aggregator(client_and_session):
    """★항등식 — 세 칸의 합이 «같은 집계기»의 totals와 일치한다.

    이쪽 분리는 retro와 달리 grain이 ref 63과 같아서 이 항등식이 «의미»를 갖는다:
    한 날짜는 정확히 한 칸이고, 그 날의 성과는 그 날의 환경에 귀속된다.
    """
    client, db = client_and_session
    _seed(db)
    _seed_across_day_classes(db)

    body = _roster(client)
    ident = body["weekend_holiday"]["identity"]
    assert ident["ok"] is True
    for key in ("cost", "imp", "clk", "conv_amt"):
        assert ident["sum_of_parts"][key] == ident["total"][key], key
        assert ident["total"][key] == body["totals"][key], f"{key}: 집계기 totals와 갈라졌다"


def test_day_class_split_identity_is_computed_not_hardcoded(client_and_session):
    """★M5형 결함 방어 — ok가 «계산된 값»이어야 한다.

    같은 병(자기 검산이 상수로 굳음)을 retro 쪽에서 이미 한 번 밟았다. 여기선 집계기의
    totals를 어긋나게 만들어 ok가 거짓이 되는지 본다.
    """
    _client, db = client_and_session
    _seed(db)
    fake = {"rows": [], "totals": {"cost": 999, "imp": 0, "clk": 0, "conv_amt": 0}}
    out = pao_scope_roster.day_class_split(db, YESTERDAY, YESTERDAY, None, date_agg=fake)
    ident = out["identity"]
    assert ident["ok"] is False, "합이 어긋났는데 ok가 참이면 검산이 아니다"
    # ★자기 변이 N7 상환 — 「보이는 두 숫자」도 진짜여야 한다.
    #   `total`을 `sum_of_parts`로 바꿔치기하면 ok는 여전히 거짓이지만 화면엔 둘이 같아
    #   보인다. 판정은 맞는데 근거가 거짓인 상태 — M5(ok 하드코딩)의 다른 얼굴이다.
    assert ident["total"]["cost"] == 999, "total은 집계기의 값이어야 한다(칸 합이 아니라)"
    assert ident["sum_of_parts"]["cost"] == 0, "sum_of_parts는 칸의 합이어야 한다"


def test_day_class_split_does_not_report_profit(client_and_session):
    """칸별 총이익은 «안» 낸다 — BEP가 그룹마다 다르고 날짜 grain엔 그 조인이 없다.

    지어내면 그 숫자가 그대로 판정에 쓰인다(§2-3 「재사용 불가면 멈추고 기록」).
    """
    client, db = client_and_session
    _seed(db)
    _seed_across_day_classes(db)

    split = _roster(client)["weekend_holiday"]
    for bucket in ("weekday", "weekend", "holiday"):
        assert "gross_profit" not in split[bucket]


def test_day_class_split_ratio_is_not_in_the_identity(client_and_session):
    """ROAS는 비율이라 칸끼리 더하지 않는다 — 항등식에 섞이면 그 자체가 오류다."""
    client, db = client_and_session
    _seed(db)
    _seed_across_day_classes(db)

    ident = _roster(client)["weekend_holiday"]["identity"]
    assert "roas" not in ident["total"]
    assert "roas" not in ident["sum_of_parts"]


def test_day_class_split_states_its_basis(client_and_session):
    """★기준(날짜 grain)을 응답이 «스스로» 밝힌다 — retro 쪽 분리와 혼동되면 안 된다."""
    client, db = client_and_session
    _seed(db)

    split = _roster(client)["weekend_holiday"]
    assert "ad_date" in split["basis"]
    assert "ref 63" in split["reference"]


def test_day_class_split_reports_real_roas_per_bucket(client_and_session):
    """★자기 변이 N9 상환 — 칸별 ROAS가 «실제 값»이어야 한다.

    전건 None으로 만들어도 아무 테스트가 안 죽었다 — 키 존재만 검사했지 값을 아무도 안 봤다.
    이 값의 존재 이유는 BEP(1.711)와 비교해 「이 칸이 흑자 구간인가」를 사람이 읽는 것이라,
    None이면 열은 있는데 아무 말도 안 하는 상태가 된다.
    """
    client, db = client_and_session
    _seed(db)
    _seed_across_day_classes(db)

    split = _roster(client)["weekend_holiday"]
    # 심은 값: 평시 conv 20,000+180,000+20,000 / cost 10,000+90,000+10,000
    assert split["weekday"]["roas"] is not None
    assert split["weekday"]["roas"] == round(
        split["weekday"]["conv_amt"] / split["weekday"]["cost"], 4
    )
    # 주말도 마찬가지 — 한 칸만 지키면 나머지 칸의 회귀를 못 잡는다
    assert split["weekend"]["roas"] == round(
        split["weekend"]["conv_amt"] / split["weekend"]["cost"], 4
    )
    # 집행이 없는 칸은 0이 아니라 None — 「ROAS 0」은 「적자」라는 거짓 사실이다
    assert split["holiday"]["cost"] == 0
    assert split["holiday"]["roas"] is None
