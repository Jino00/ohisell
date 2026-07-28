# test_naver_ad_performance_phase2.py — D-NAO-105 Phase 2
# (날짜 선택·날짜 비교·캠페인 선택기 · ③캠페인 상세 · ④예산 소진 곡선).
#
# 이 파일이 지키는 것(계획서 §0-5·원칙22):
#   · 날짜에 따라 숫자의 **출처가 다르다** — 오늘=실주문 프록시 / 과거=네이버 확정치.
#     경계(오늘·D-1·D-2·D-3)를 전부 건다. 여기가 어긋나면 화면이 프록시를 확정치로 말한다.
#   · **모름(null)과 측정된 0을 구분**한다. 합계에서 모름을 0으로 더하지 않는다.
#   · 배지 4상태는 **순수 판정**이다 — 하지 않은 일을 했다고 말하지 않는다("확장 중"은
#     실제로 올렸을 때만).
#   · 화면에 나가는 문자열에 **ID·내부 용어가 없다**(D-NAO-103①②).
# 원칙22: SA 단위테스트만으로는 라우터 레이어 500을 못 잡는다 → HTTP 왕복도 함께 건다.
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    NaverAdDaily,
    NaverAdgroupProduct,
    NaverCampaignSettings,
    NaverChangeLog,
    NaverEntity,
    NaverHourlySnapshot,
)
from app.services.naver_ad import (
    budget_pacing_view,
    group_state_badge,
    perf_campaign_harness,
    perf_today_harness,
)
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.utils.kst import kst_today

SHOPPING = "cmp-shopping-1"
POWERLINK = "cmp-powerlink-1"
GROUP_A = "grp-a"
GROUP_B = "grp-b"
PRODUCT_ID = "11730763642"


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


def _seed_entities(db):
    db.add_all([
        NaverEntity(entity_type="campaign", entity_id=SHOPPING, campaign_id=SHOPPING,
                    campaign_type="SHOPPING", name="● 02. 아이폰_강화유리", status="on",
                    status_reason="ELIGIBLE"),
        NaverEntity(entity_type="campaign", entity_id=POWERLINK, campaign_id=POWERLINK,
                    campaign_type="WEB_SITE", name="P. 아이패드 파워링크", status="on",
                    status_reason="ELIGIBLE"),
        NaverEntity(entity_type="adgroup", entity_id=GROUP_A, campaign_id=SHOPPING,
                    parent_id=SHOPPING, campaign_type="SHOPPING", name="17프로", status="on"),
        NaverEntity(entity_type="adgroup", entity_id=GROUP_B, campaign_id=SHOPPING,
                    parent_id=SHOPPING, campaign_type="SHOPPING", name="17프로맥스", status="on"),
        NaverCampaignSettings(campaign_id=SHOPPING, optimizer="ours", auto_operate=True),
    ])
    db.commit()


def _daily(db, day: date, *, campaign=SHOPPING, adgroup=GROUP_A, cost=10_000,
           imp=1_000, clk=50, conv_direct_amt=30_000, conv_indirect_amt=0):
    db.add(NaverAdDaily(
        ad_date=day, campaign_id=campaign, campaign_type="SHOPPING", adgroup_id=adgroup,
        keyword_id="", imp=imp, clk=clk, cost=cost, rank_sum=imp * 3,
        conv_direct_amt=conv_direct_amt, conv_indirect_amt=conv_indirect_amt,
        conv_direct_cnt=1, conv_indirect_cnt=0,
    ))
    db.commit()


def _snapshot(db, day: date, hour: int, *, campaign=SHOPPING, cost=0, budget=50_000,
              imp=0, clk=0):
    db.add(NaverHourlySnapshot(
        snapshot_at=datetime.combine(day, time(hour=hour)), ad_date=day, snapshot_hour=hour,
        campaign_id=campaign, campaign_type="SHOPPING", cost=cost, clk=clk, imp=imp,
        daily_budget=budget,
    ))
    db.commit()


def _change(db, *, at: datetime, action="update_bid", entity_type="adgroup", entity_id=GROUP_A,
            campaign_id=SHOPPING, before=None, after=None, rationale=None, outcome="executed"):
    db.add(NaverChangeLog(
        changed_at=at, entity_type=entity_type, entity_id=entity_id, campaign_id=campaign_id,
        action=action,
        before_value=json.dumps(before) if before is not None else None,
        after_value=json.dumps(after) if after is not None else None,
        rationale=rationale, dry_run=False, outcome=outcome,
    ))
    db.commit()


# ══════════════════════════════════════════════════════════════════
# ① 날짜 소스 분기 — 오늘=프록시 / D-1·D-2=확정 중 / D-3 이상=확정
# ══════════════════════════════════════════════════════════════════

def test_resolve_source_boundaries():
    """경계가 어긋나면 화면이 '아직 올라갈 숫자'를 확정이라고 말한다."""
    today = date(2026, 7, 28)
    assert perf_today_harness.resolve_source(today, today) == perf_today_harness.SOURCE_TODAY_PROXY
    assert perf_today_harness.resolve_source(today - timedelta(days=1), today) == perf_today_harness.SOURCE_SETTLING
    assert perf_today_harness.resolve_source(today - timedelta(days=2), today) == perf_today_harness.SOURCE_SETTLING
    assert perf_today_harness.resolve_source(today - timedelta(days=3), today) == perf_today_harness.SOURCE_CONFIRMED


def test_past_day_uses_confirmed_daily_not_proxy(client, db):
    """과거 날짜는 naver_ad_daily 확정치에서 온다 — 실주문 프록시를 쓰지 않는다."""
    _seed_entities(db)
    past = kst_today() - timedelta(days=10)
    _daily(db, past, cost=20_000, imp=2_000, clk=100, conv_direct_amt=60_000)

    body = client.get(f"/api/naver/ad/performance/day?date={past.isoformat()}").json()
    assert body["source"] == perf_today_harness.SOURCE_CONFIRMED
    assert body["source_label"] == "확정"
    card = next(c for c in body["campaigns"] if c["spend_today"] > 0)
    assert card["spend_today"] == 20_000
    assert card["imp_today"] == 2_000
    assert card["revenue_today_proxy"] == 60_000
    assert card["roas_today_proxy"] == pytest.approx(3.0)
    assert card["roas_label"] == "ROAS(확정)"
    # 과거 카드에 '지금 멈춰 있습니다' 같은 **현재 상태 문장**을 붙이면 시점이 어긋난다.
    assert "지금은" not in card["verdict_sentence"]


def test_past_day_excludes_backfill_sentinel(client, db):
    """sentinel 행을 같이 세면 광고비가 2배가 된다(2배 함정)."""
    _seed_entities(db)
    past = kst_today() - timedelta(days=10)
    _daily(db, past, cost=20_000, conv_direct_amt=60_000)
    _daily(db, past, adgroup=BACKFILL_SENTINEL_ADGROUP, cost=20_000, conv_direct_amt=60_000)

    body = client.get(f"/api/naver/ad/performance/day?date={past.isoformat()}").json()
    card = next(c for c in body["campaigns"] if c["campaign_id"] == SHOPPING)
    assert card["spend_today"] == 20_000


def test_settling_day_says_still_settling(client, db):
    """D-1은 간접전환이 아직 들어오는 중 — '확정'이라고 단언하지 않는다."""
    _seed_entities(db)
    yday = kst_today() - timedelta(days=1)
    _daily(db, yday, cost=10_000)
    body = client.get(f"/api/naver/ad/performance/day?date={yday.isoformat()}").json()
    assert body["source"] == perf_today_harness.SOURCE_SETTLING
    assert "올라갈 수 있습니다" in body["data_note"]


def test_past_day_with_no_records_says_so_instead_of_claiming_zero(client, db):
    """★"수집이 안 된 날"과 "아무것도 안 한 날"은 다르다 — 0원이라고 단언하지 않는다."""
    _seed_entities(db)
    past = kst_today() - timedelta(days=10)
    body = client.get(f"/api/naver/ad/performance/day?date={past.isoformat()}").json()
    assert body["data_gap_note"] is not None
    assert "둘 중 무엇인지는" in body["data_gap_note"]


def test_past_day_with_records_has_no_gap_note(client, db):
    _seed_entities(db)
    past = kst_today() - timedelta(days=10)
    _daily(db, past, cost=10_000)
    body = client.get(f"/api/naver/ad/performance/day?date={past.isoformat()}").json()
    assert body["data_gap_note"] is None


def test_today_never_claims_a_data_gap(client, db):
    """오늘은 확정 적재 전이 정상이다 — 매일 아침 '기록이 없다'고 뜨면 늑대소년이 된다."""
    _seed_entities(db)
    body = client.get("/api/naver/ad/performance/day").json()
    assert body["data_gap_note"] is None


def test_future_date_rejected(client, db):
    _seed_entities(db)
    future = (kst_today() + timedelta(days=1)).isoformat()
    assert client.get(f"/api/naver/ad/performance/day?date={future}").status_code == 400


def test_day_campaign_filter_narrows_cards_and_actions(client, db):
    """선택기가 캠페인을 고르면 카드·문장·합계가 전부 좁아진다."""
    _seed_entities(db)
    today = kst_today()
    _snapshot(db, today, 10, cost=5_000)
    _snapshot(db, today, 10, campaign=POWERLINK, cost=7_000)
    _change(db, at=datetime.combine(today, time(9)), before={"bidAmt": 200}, after={"bidAmt": 300})
    _change(db, at=datetime.combine(today, time(9, 30)), campaign_id=POWERLINK,
            entity_id="kw-1", entity_type="keyword", before={"bidAmt": 100}, after={"bidAmt": 150})

    body = client.get(f"/api/naver/ad/performance/day?campaign_id={SHOPPING}").json()
    assert body["campaign_filter"] == SHOPPING
    assert [c["campaign_id"] for c in body["campaigns"]] == [SHOPPING]
    assert body["totals"]["spend_today"] == 5_000
    assert body["today_actions"]["executed_count"] == 1


# ══════════════════════════════════════════════════════════════════
# ② 날짜 비교
# ══════════════════════════════════════════════════════════════════

def test_compare_deltas_absolute_and_percent(client, db):
    _seed_entities(db)
    base = kst_today() - timedelta(days=5)
    against = kst_today() - timedelta(days=10)
    _daily(db, base, cost=20_000, imp=2_000, clk=100, conv_direct_amt=60_000)
    _daily(db, against, cost=10_000, imp=1_000, clk=50, conv_direct_amt=40_000)

    body = client.get(
        f"/api/naver/ad/performance/compare?base={base.isoformat()}&against={against.isoformat()}"
    ).json()
    assert body["base"]["totals"]["spend"] == 20_000
    assert body["against"]["totals"]["spend"] == 10_000
    assert body["deltas"]["spend"] == {"abs": 10_000, "pct": 1.0}   # +100%
    # ROAS 3.0 vs 4.0 → 절대 -1.0배, -25%
    assert body["deltas"]["roas"]["abs"] == pytest.approx(-1.0)
    assert body["deltas"]["roas"]["pct"] == pytest.approx(-0.25)
    assert body["mixed_source_note"] is None  # 둘 다 확정치
    assert body["settling_note"] is None      # 둘 다 정착 끝난 창
    row = next(r for r in body["rows"] if r["name"].startswith("02."))
    assert row["deltas"]["clk"]["abs"] == 50


def test_compare_settling_vs_confirmed_is_not_a_definition_mismatch(client, db):
    """★07-27 라이브 스모크에서 잡힌 오탐: 둘 다 naver_ad_daily 확정치인데(성숙도만 다름)
    '매출 기준이 다르다'고 경고하면 매일 뜨는 늑대소년이 된다. 대신 '아직 올라갈 수 있다'만
    말한다."""
    _seed_entities(db)
    base = kst_today() - timedelta(days=1)      # settling
    against = kst_today() - timedelta(days=10)  # confirmed
    _daily(db, base, cost=20_000)
    _daily(db, against, cost=10_000)
    body = client.get(
        f"/api/naver/ad/performance/compare?base={base.isoformat()}&against={against.isoformat()}"
    ).json()
    assert body["mixed_source_note"] is None
    assert body["settling_note"] is not None
    assert base.isoformat() in body["settling_note"]


def test_compare_today_vs_past_flags_mixed_source(client, db):
    """오늘(프록시) vs 과거(확정)는 정의가 다른 값끼리의 비교 — 반드시 말한다."""
    _seed_entities(db)
    today = kst_today()
    past = today - timedelta(days=7)
    _snapshot(db, today, 12, cost=5_000)
    _daily(db, past, cost=10_000)

    body = client.get(
        f"/api/naver/ad/performance/compare?base={today.isoformat()}&against={past.isoformat()}"
    ).json()
    assert body["mixed_source_note"] is not None
    assert "참고로만" in body["mixed_source_note"]


def test_compare_same_date_rejected(client, db):
    _seed_entities(db)
    d = kst_today().isoformat()
    assert client.get(f"/api/naver/ad/performance/compare?base={d}&against={d}").status_code == 400


def test_compare_skips_rows_with_no_spend_on_either_day(client, db):
    _seed_entities(db)
    base = kst_today() - timedelta(days=5)
    against = kst_today() - timedelta(days=10)
    _daily(db, base, cost=20_000)
    body = client.get(
        f"/api/naver/ad/performance/compare?base={base.isoformat()}&against={against.isoformat()}"
    ).json()
    assert [r["campaign_id"] for r in body["rows"]] == [SHOPPING]  # 파워링크는 양쪽 0 → 제외


def test_compare_totals_do_not_count_unknown_revenue_as_zero(client, db):
    """매핑 없는 캠페인의 매출은 **모름**이다 — 0으로 더하면 합계가 조용히 과소가 된다."""
    _seed_entities(db)
    db.add(NaverAdgroupProduct(adgroup_id=GROUP_A, campaign_id=SHOPPING,
                               mall_product_id=PRODUCT_ID, product_name="강화유리"))
    db.commit()
    today = kst_today()
    _snapshot(db, today, 12, cost=5_000)
    _snapshot(db, today, 12, campaign=POWERLINK, cost=7_000)

    body = client.get(
        f"/api/naver/ad/performance/compare?base={today.isoformat()}"
        f"&against={(today - timedelta(days=3)).isoformat()}"
    ).json()
    totals = body["base"]["totals"]
    assert totals["spend"] == 12_000
    assert totals["revenue"] == 0            # 쇼핑은 매핑 있음 · 주문 없음 = 측정된 0
    assert totals["revenue_unknown_campaigns"] == 1   # 파워링크 = 모름(0이 아님)


# ══════════════════════════════════════════════════════════════════
# ③ 그룹 상태 배지 — 순수 판정
# ══════════════════════════════════════════════════════════════════

def _judge(**kw):
    base = dict(
        name="17프로", status="on", status_reason=None, cost=10_000, roas=2.0,
        bep_roas=1.4758, target_roas=1.72, blocked_reasons=[], raised_recently=False,
        lowered_recently=False, unknown_recently=False, window_days=30,
        managed_by_us=True,
    )
    base.update(kw)
    return group_state_badge.judge(**base)


def test_badge_blocked_when_group_is_off():
    out = _judge(status="off", status_reason="ADGROUP_PAUSED")
    assert out["state"] == group_state_badge.STATE_BLOCKED
    assert out["state_label"] == "차단됨"
    assert "멈춰 있습니다" in out["reason_sentence"]


def test_badge_blocked_when_guardrail_refused():
    out = _judge(blocked_reasons=["지금 성과가 손익분기 아래라 증액을 막았습니다"])
    assert out["state"] == group_state_badge.STATE_BLOCKED
    assert "안전장치가 막았습니다" in out["reason_sentence"]


def test_badge_hold_below_bep():
    out = _judge(roas=1.0)
    assert out["state"] == group_state_badge.STATE_HOLD
    assert out["state_label"] == "증액 보류"
    assert "1.48배" in out["reason_sentence"]


def test_badge_hold_when_spent_but_roas_unknown():
    """돈은 썼는데 ROAS를 못 구하면 '모름'을 '좋음'으로 넘기지 않는다."""
    out = _judge(roas=None)
    assert out["state"] == group_state_badge.STATE_HOLD
    assert "성과를 확인할 수 없어" in out["reason_sentence"]


def test_badge_expanding_only_when_we_actually_raised():
    """성과가 좋아도 우리가 아무것도 안 했으면 '확장 중'이 아니다(하지 않은 일 금지)."""
    assert _judge(roas=3.0)["state"] == group_state_badge.STATE_WATCHING
    out = _judge(roas=3.0, raised_recently=True)
    assert out["state"] == group_state_badge.STATE_EXPANDING
    assert "노출을 넓히는 중" in out["reason_sentence"]


def test_badge_zero_spend_is_never_expanding_even_after_a_raise():
    """★리뷰 실측 회귀 고정: 노출·클릭·지출이 전부 0인데 "반응이 나오는지 보는 중"은 거짓이다.
    올린 이력이 있어도 집행이 0이면 확장 중이 아니다(원칙22 — '확장 중' 규칙의 거울상)."""
    out = _judge(cost=0, roas=None, raised_recently=True)
    assert out["state"] == group_state_badge.STATE_WATCHING
    assert out["reason_sentence"] == "입찰을 올려두었지만 아직 집행이 없습니다."
    assert "반응이 나오는지" not in out["reason_sentence"]


def test_badge_zero_spend_beats_hold_and_blocked_reasons_stay_first():
    """판정 순서 계약: 차단 > 집행0 > 증액보류 > 확장 > 관망."""
    # 차단 이력은 집행 0원보다 먼저다(광고가 안 나간 이유를 말해주는 쪽이 더 유용).
    assert _judge(cost=0, roas=None, blocked_reasons=["쿨다운"])["state"] \
        == group_state_badge.STATE_BLOCKED
    # 집행 0원은 증액 보류(BEP 미달)보다 먼저다 — 성과 자체가 없으니 BEP 비교가 성립 안 한다.
    assert _judge(cost=0, roas=None)["state"] == group_state_badge.STATE_WATCHING


def test_badge_observed_when_campaign_is_not_ours():
    """★리뷰 실측 회귀 고정: 46캠페인 중 43개가 optimizer='none'인데 그 그룹에도
    "더 키우지 않고 있습니다"가 붙었다 — 하지도 않는 관리를 한다고 말하는 것."""
    out = _judge(managed_by_us=False, roas=3.0, raised_recently=True)
    assert out["state"] == group_state_badge.STATE_OBSERVED
    assert out["state_label"] == "관찰만"
    assert out["reason_sentence"].startswith(group_state_badge.NOT_OURS_PREFIX)
    # 능동 관리 동사가 한 개도 없어야 한다.
    for phrase in ("키우지 않고 있습니다", "유지하고 있습니다", "넓히는 중",
                   "지켜보는 중", "막았습니다", "올려두었지만"):
        assert phrase not in out["reason_sentence"]


def test_badge_observed_states_the_performance_fact():
    below = _judge(managed_by_us=False, roas=1.0)
    assert "남는 기준(1.48배) 아래입니다" in below["reason_sentence"]
    idle = _judge(managed_by_us=False, cost=0, roas=None)
    assert "집행이 없었습니다" in idle["reason_sentence"]
    off = _judge(managed_by_us=False, status="off", status_reason="ADGROUP_PAUSED")
    assert off["state"] == group_state_badge.STATE_OBSERVED
    assert "멈춰 있습니다" in off["reason_sentence"]


def test_badge_watching_when_write_result_unknown():
    """쓰기 실패(모름)는 차단이 아니다 — 반영 여부를 모르는 것이다."""
    out = _judge(roas=1.6, unknown_recently=True)
    assert out["state"] == group_state_badge.STATE_WATCHING
    assert "확인되지 않아" in out["reason_sentence"]


def test_badge_watching_when_no_spend():
    out = _judge(cost=0, roas=None)
    assert out["state"] == group_state_badge.STATE_WATCHING
    assert "판단할 근거가 없습니다" in out["reason_sentence"]


def test_badge_labels_have_no_internal_codes():
    for state in group_state_badge.STATE_LABEL.values():
        assert state in ("확장 중", "관망", "증액 보류", "차단됨", "관찰만")


# ══════════════════════════════════════════════════════════════════
# ③ 캠페인 상세 API
# ══════════════════════════════════════════════════════════════════

def test_campaign_detail_not_ours_downgrades_badges_to_observation(client, db):
    """★리뷰 실측 회귀 고정: 우리가 운영하지 않는 캠페인에 능동 관리 문장을 붙이지 않는다."""
    _seed_entities(db)
    # 03을 우리 소유에서 빼고(=대다수 캠페인의 실제 모습) 성과만 남긴다.
    db.query(NaverCampaignSettings).filter(
        NaverCampaignSettings.campaign_id == SHOPPING
    ).delete()
    db.commit()
    _daily(db, kst_today() - timedelta(days=1), cost=10_000, conv_direct_amt=30_000)

    body = client.get(f"/api/naver/ad/performance/campaign/{SHOPPING}").json()
    assert body["managed_by_us"] is False
    assert body["managed_by_label"] == "직접 관리(자동 운영 안 함)"
    assert body["managed_note"] is not None
    for g in body["groups"]:
        assert g["state"] == group_state_badge.STATE_OBSERVED
        assert g["state_label"] == "관찰만"
        assert "키우지 않고 있습니다" not in g["reason_sentence"]
        assert "유지하고 있습니다" not in g["reason_sentence"]


def test_campaign_detail_ours_keeps_active_badges(client, db):
    _seed_entities(db)
    _daily(db, kst_today() - timedelta(days=1), cost=10_000, conv_direct_amt=30_000)
    body = client.get(f"/api/naver/ad/performance/campaign/{SHOPPING}").json()
    assert body["managed_by_us"] is True
    assert body["managed_by_label"] == "우리가 자동으로 운영"
    assert body["managed_note"] is None
    assert all(g["state"] != group_state_badge.STATE_OBSERVED for g in body["groups"])


def test_campaign_detail_series_lines_and_group_badges(client, db):
    _seed_entities(db)
    today = kst_today()
    _daily(db, today - timedelta(days=2), cost=10_000, conv_direct_amt=30_000)
    _daily(db, today - timedelta(days=1), cost=12_000, conv_direct_amt=12_000)
    _daily(db, today - timedelta(days=1), adgroup=GROUP_B, cost=8_000, conv_direct_amt=1_000)
    # 오늘(D-0)은 시계열에서 빠져야 한다(창 관례).
    _daily(db, today, cost=99_999, conv_direct_amt=0)

    body = client.get(f"/api/naver/ad/performance/campaign/{SHOPPING}?days=30").json()
    assert body["name"] == "02. 아이폰_강화유리"      # 장식 기호 제거 · 코드 접두 보존
    assert body["type_label"] == "쇼핑검색"
    dates = [p["date"] for p in body["series"]]
    assert today.isoformat() not in dates
    assert len(dates) == 2
    assert body["lines"]["bep_roas"] is None or isinstance(body["lines"]["bep_roas"], float)
    names = {g["name"] for g in body["groups"]}
    assert names == {"17프로", "17프로맥스"}
    for g in body["groups"]:
        assert g["state_label"] in ("확장 중", "관망", "증액 보류", "차단됨")
        assert g["reason_sentence"]


def test_campaign_detail_folds_ad_level_change_into_group(client, db):
    """쇼핑의 실제 레버는 **소재 입찰**이다 — 그룹 행만 세면 아무 일도 안 한 것처럼 보인다."""
    _seed_entities(db)
    db.add(NaverAdgroupProduct(adgroup_id=GROUP_A, campaign_id=SHOPPING,
                               mall_product_id=PRODUCT_ID, product_name="강화유리",
                               ad_id="nad-1"))
    db.commit()
    today = kst_today()
    _daily(db, today - timedelta(days=1), cost=10_000, conv_direct_amt=30_000)
    _change(db, at=datetime.combine(today, time(9)), entity_type="ad", entity_id="nad-1",
            before={"adAttr": {"bidAmt": 200}}, after={"adAttr": {"bidAmt": 300}})

    body = client.get(f"/api/naver/ad/performance/campaign/{SHOPPING}").json()
    group_a = next(g for g in body["groups"] if g["name"] == "17프로")
    assert group_a["state"] == group_state_badge.STATE_EXPANDING


def test_campaign_detail_includes_groups_without_spend(client, db):
    """창 안에 집행이 없는 그룹도 배지를 단다 — 0은 '없는 것'이 아니다(D-47-h)."""
    _seed_entities(db)
    body = client.get(f"/api/naver/ad/performance/campaign/{SHOPPING}").json()
    assert {g["name"] for g in body["groups"]} == {"17프로", "17프로맥스"}
    assert all(g["cost"] == 0 for g in body["groups"])


def test_campaign_detail_404_for_unknown_campaign(client, db):
    _seed_entities(db)
    r = client.get("/api/naver/ad/performance/campaign/cmp-does-not-exist")
    assert r.status_code == 404
    assert "찾을 수 없습니다" in r.json()["detail"]


# ══════════════════════════════════════════════════════════════════
# ④ 예산 소진 곡선 · 암전
# ══════════════════════════════════════════════════════════════════

def test_budget_curve_blackout_detected_after_budget_exhausted(client, db):
    """1차 신호 = 증분 0 연속 2시간, 2차 신호 = 멈추기 직전 소진율."""
    _seed_entities(db)
    today = kst_today()
    for hour, cost in ((9, 10_000), (12, 30_000), (13, 50_000), (14, 50_000), (15, 50_000)):
        _snapshot(db, today, hour, cost=cost, budget=50_000)

    body = client.get("/api/naver/ad/performance/budget").json()
    curve = next(c for c in body["curves"] if c["campaign_id"] == SHOPPING)
    assert curve["daily_budget"] == 50_000
    assert curve["blackout_hours"] == [14, 15]
    assert "오후 2시" in curve["blackout_sentence"]
    # 누적이 아니라 시간당 지출도 함께 준다(누적만 그리면 곡선이 계단으로만 보인다).
    assert [p["hour_cost"] for p in curve["points"]] == [10_000, 20_000, 20_000, 0, 0]


def test_budget_curve_catches_stall_below_98_percent(client, db):
    """★리뷰 실측 회귀 고정(03 아이폰_강화유리 2026-07-27): 19시에 48,699원(예산 50,000의
    97.4%)을 쓰고 20~23시 4시간을 멈췄다. 옛 규칙(소진율 ≥0.98)은 0.6%p 차이로 **한 건도
    못 잡았다** — 네이버는 100%를 채우고 멈추지 않는다."""
    _seed_entities(db)
    today = kst_today()
    for hour, cost in ((18, 43_812), (19, 48_699), (20, 48_699), (21, 48_699),
                       (22, 48_699), (23, 48_699)):
        _snapshot(db, today, hour, cost=cost, budget=50_000)

    body = client.get("/api/naver/ad/performance/budget").json()
    curve = next(c for c in body["curves"] if c["campaign_id"] == SHOPPING)
    assert curve["blackout_hours"] == [20, 21, 22, 23]
    assert "오후 8시" in curve["blackout_sentence"]
    assert "4시간" in curve["blackout_sentence"]
    assert "적어도" not in curve["blackout_sentence"]  # 결번 없음 → 단정해도 된다


def test_budget_curve_no_blackout_when_early_hours_are_quiet(client, db):
    """새벽 무노출(증분 0)을 '예산 소진'으로 오판하면 안 된다."""
    _seed_entities(db)
    today = kst_today()
    for hour, cost in ((3, 0), (4, 0), (9, 5_000)):
        _snapshot(db, today, hour, cost=cost, budget=50_000)
    body = client.get("/api/naver/ad/performance/budget").json()
    curve = next(c for c in body["curves"] if c["campaign_id"] == SHOPPING)
    assert curve["blackout_hours"] == []
    assert curve["blackout_sentence"] is None


def test_budget_curve_low_ratio_stall_is_not_attributed_to_budget(client, db):
    """★07-27 실측: 증분 0 구간 37건 중 대부분이 소진율 1~19%다. 트리거만 보고 예산 탓으로
    돌리면 매일 전 캠페인이 '예산 소진'이 된다 — 귀속 근거 없으면 말하지 않는다."""
    _seed_entities(db)
    today = kst_today()
    for hour, cost in ((9, 1_000), (10, 1_000), (11, 1_000), (12, 1_000)):
        _snapshot(db, today, hour, cost=cost, budget=50_000)  # 소진율 2%
    body = client.get("/api/naver/ad/performance/budget").json()
    curve = next(c for c in body["curves"] if c["campaign_id"] == SHOPPING)
    assert curve["blackout_hours"] == []
    assert curve["blackout_sentence"] is None
    assert curve["stall_sentence"] is None  # 2%는 애매 구간도 아니다 — 아예 말하지 않는다


def test_budget_curve_uncertain_stall_states_fact_without_cause(client, db):
    """소진율 60~90%에서 멈추면 사실만 말하고 **원인은 말하지 않는다**(원칙22)."""
    _seed_entities(db)
    today = kst_today()
    for hour, cost in ((9, 35_000), (10, 35_000), (11, 35_000)):
        _snapshot(db, today, hour, cost=cost, budget=50_000)  # 70%
    body = client.get("/api/naver/ad/performance/budget").json()
    curve = next(c for c in body["curves"] if c["campaign_id"] == SHOPPING)
    assert curve["blackout_hours"] == []
    assert "예산의 70%" in curve["stall_sentence"]
    assert "확실하지 않습니다" in curve["stall_sentence"]
    assert "다 써서" not in curve["stall_sentence"]


def test_budget_curve_single_zero_hour_is_not_a_blackout(client, db):
    """1시간짜리 증분 0은 그냥 한산한 시간이다 — MIN_BLACKOUT_HOURS 계약."""
    _seed_entities(db)
    today = kst_today()
    for hour, cost in ((9, 49_000), (10, 49_000), (11, 50_000)):
        _snapshot(db, today, hour, cost=cost, budget=50_000)
    body = client.get("/api/naver/ad/performance/budget").json()
    curve = next(c for c in body["curves"] if c["campaign_id"] == SHOPPING)
    assert curve["blackout_hours"] == []


def test_budget_curve_gap_in_collection_does_not_invent_hours(client, db):
    """수집 결번: 시각이 안 이어지면 구간을 잇지 않는다. 잘렸으면 '적어도 N시간'으로 물러선다."""
    _seed_entities(db)
    today = kst_today()
    # 19시 97.4% → 20·21시 정지 → 22시 결번 → 23시 관측(여전히 같은 누적)
    for hour, cost in ((19, 48_699), (20, 48_699), (21, 48_699), (23, 48_699)):
        _snapshot(db, today, hour, cost=cost, budget=50_000)
    body = client.get("/api/naver/ad/performance/budget").json()
    curve = next(c for c in body["curves"] if c["campaign_id"] == SHOPPING)
    # 23시는 21시와 이어지지 않으므로 같은 구간으로 세지 않는다(없는 시간을 만들지 않는다).
    assert curve["blackout_hours"] == [20, 21]
    assert "적어도 2시간" in curve["blackout_sentence"]
    assert "기록이 빠진 시간대" in curve["blackout_sentence"]


def test_find_stall_runs_uses_hour_adjacency_not_list_order():
    """순수 함수 계약 — 리스트 인접이 아니라 **시각 인접**으로 구간을 만든다."""
    points = [
        {"hour": 9, "cost": 1_000, "spend_ratio": 0.02},
        {"hour": 14, "cost": 1_000, "spend_ratio": 0.02},   # 5시간 건너뜀 → 구간 아님
        {"hour": 15, "cost": 1_000, "spend_ratio": 0.02},
        {"hour": 16, "cost": 1_000, "spend_ratio": 0.02},
    ]
    runs = budget_pacing_view.find_stall_runs(points)
    assert [r["hours"] for r in runs] == [[15, 16]]
    assert runs[0]["ratio_before"] == 0.02


def test_find_stall_runs_ignores_cost_correction_downward():
    """누적이 **줄어든** 시각은 '멈춤'이 아니라 재집계 보정이다."""
    points = [
        {"hour": 9, "cost": 10_000, "spend_ratio": 0.2},
        {"hour": 10, "cost": 9_000, "spend_ratio": 0.18},   # 보정(감소)
        {"hour": 11, "cost": 9_000, "spend_ratio": 0.18},   # 여기부터 진짜 정지
        {"hour": 12, "cost": 9_000, "spend_ratio": 0.18},
    ]
    runs = budget_pacing_view.find_stall_runs(points)
    assert [r["hours"] for r in runs] == [[11, 12]]


def test_budget_curve_without_daily_budget_refuses_to_judge(client, db):
    """일예산이 없으면 소진율도 암전도 판정하지 않는다(0%로 그리면 거짓)."""
    _seed_entities(db)
    today = kst_today()
    db.add(NaverHourlySnapshot(
        snapshot_at=datetime.combine(today, time(9)), ad_date=today, snapshot_hour=9,
        campaign_id=SHOPPING, campaign_type="SHOPPING", cost=5_000, clk=1, imp=10,
        daily_budget=None,
    ))
    db.commit()
    body = client.get("/api/naver/ad/performance/budget").json()
    curve = next(c for c in body["curves"] if c["campaign_id"] == SHOPPING)
    assert curve["daily_budget"] is None
    assert curve["spend_ratio"] is None
    assert "판단할 수 없습니다" in curve["blackout_sentence"]


def test_budget_changes_empty_is_a_sentence_not_an_error(client, db):
    """BP 레인 배포 전에는 빈 배열이 정상 — 500도 아니고 침묵도 아니다."""
    _seed_entities(db)
    body = client.get("/api/naver/ad/performance/budget").json()
    assert body["budget_changes"] == []
    assert body["budget_changes_empty_reason"] == "이날은 예산을 자동으로 바꾼 기록이 없습니다."


def test_budget_changes_narrated_with_hour(client, db):
    _seed_entities(db)
    today = kst_today()
    _snapshot(db, today, 15, cost=40_000)
    _change(db, at=datetime.combine(today, time(15, 10)), action="budget_up_pacing",
            entity_type="campaign", entity_id=SHOPPING,
            before={"dailyBudget": 50_000}, after={"dailyBudget": 65_000},
            rationale="[예산페이싱] 소진율 92%")
    body = client.get("/api/naver/ad/performance/budget").json()
    [chg] = body["budget_changes"]
    assert chg["hour"] == 15
    assert "50,000원에서 65,000원으로 늘렸습니다" in chg["sentence"]
    assert "예산페이싱" not in chg["sentence"]   # 내부 태그는 화면에 안 나간다


def test_budget_hour_words_are_human():
    assert budget_pacing_view._hour_words(0) == "밤 12시"
    assert budget_pacing_view._hour_words(9) == "오전 9시"
    assert budget_pacing_view._hour_words(12) == "낮 12시"
    assert budget_pacing_view._hour_words(20) == "오후 8시"


# ══════════════════════════════════════════════════════════════════
# 캠페인 선택기 · 표기 규약(D-NAO-103)
# ══════════════════════════════════════════════════════════════════

def test_campaign_options_list_names_not_ids(client, db):
    _seed_entities(db)
    body = client.get("/api/naver/ad/performance/campaigns").json()
    names = [c["name"] for c in body["campaigns"]]
    assert "02. 아이폰_강화유리" in names
    assert all(not n.startswith("cmp-") for n in names)
    assert any(c["managed_by_label"] == "우리가 자동으로 운영" for c in body["campaigns"])


def test_phase2_responses_leak_no_ids_or_internal_terms(client, db):
    """화면에 렌더되는 문자열 전수 검사 — ID·내부 용어가 한 글자도 없어야 한다."""
    _seed_entities(db)
    today = kst_today()
    _daily(db, today - timedelta(days=1), cost=10_000, conv_direct_amt=5_000)
    _snapshot(db, today, 14, cost=49_500)
    _snapshot(db, today, 15, cost=50_000)
    _snapshot(db, today, 16, cost=50_000)
    _change(db, at=datetime.combine(today, time(9)), before={"bidAmt": 200}, after={"bidAmt": 300},
            rationale="[탐색UP] target_bid×10 D-NAO-20")
    _change(db, at=datetime.combine(today, time(10)), after=None, outcome="failed",
            rationale="[실행 불가] BEP 미달 — shopping_group_bep(D-NAO-64)")

    texts: list[str] = []
    detail = client.get(f"/api/naver/ad/performance/campaign/{SHOPPING}").json()
    texts += [detail["name"], detail["type_label"], detail["series_note"]]
    texts += [g["name"] for g in detail["groups"]] + [g["reason_sentence"] for g in detail["groups"]]
    texts += [g["state_label"] for g in detail["groups"]]

    budget = client.get("/api/naver/ad/performance/budget").json()
    texts.append(budget["data_note"])
    for c in budget["curves"]:
        texts.append(c["campaign_name"])
        if c["blackout_sentence"]:
            texts.append(c["blackout_sentence"])
    texts += [c["sentence"] for c in budget["budget_changes"]]

    day = client.get("/api/naver/ad/performance/day").json()
    texts.append(day["data_note"])
    texts += [i["sentence"] for i in day["today_actions"]["items"]]

    forbidden = ("cmp-", "grp-", "nad-", "nkw-", "D-NAO", "target_bid", "shopping_group_bep",
                 "탐색UP", "BEP 미달", "W1", "W3", "bid_up", "not_viable", "__backfill__")
    for text in texts:
        for token in forbidden:
            assert token not in text, f"{token!r} 누출: {text!r}"


def test_read_only_contract_no_write_endpoints_added():
    """이 페이지에는 쓰기 경로가 없다(계획서 §0-1). 라우트가 늘어도 GET만이어야 한다."""
    perf_routes = [
        r for r in app.routes
        if getattr(r, "path", "").startswith("/api/naver/ad/performance")
    ]
    assert perf_routes
    for r in perf_routes:
        assert set(r.methods) <= {"GET", "HEAD"}, f"{r.path}: {r.methods}"


def test_action_sets_match_between_harnesses():
    """②가 세는 액션 집합이 갈라지면 같은 변경이 화면마다 다르게 잡힌다."""
    assert perf_campaign_harness._TRACKED_ACTIONS == perf_today_harness.TODAY_ACTIONS
    assert perf_campaign_harness._BUDGET_ACTIONS == perf_today_harness.BUDGET_ACTIONS
    assert perf_campaign_harness._BUDGET_ACTIONS <= perf_today_harness.TODAY_ACTIONS
