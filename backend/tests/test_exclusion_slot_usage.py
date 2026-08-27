# test_exclusion_slot_usage.py — 제외 슬롯 사용률이 **화면까지 흐르는지** 지킨다 (S6, D-NAO-264).
#
# ## 왜 이 파일이 있나
# 제외 슬롯은 그룹당 70칸인데 **몇 칸 찼는지 아무도 안 세고 있었다** — `/ncc/targets` 응답에
# 이미 실려 오는데 `get_adgroup_targets`가 「있었다」는 이름만 남기고 버렸다. 70/70이 되면
# 그 그룹의 음의 레버가 소멸하는데 파이프라인도 값도 정상이라 **다른 어떤 감시에도 안 잡힌다**.
#
# ★그리고 이 파일의 절반은 «닿는 층»을 지킨다: 이 세션 계열에서 「만드는 층은 20종이 지키고
#   닿는 층(로그·응답 키·화면)은 0종이 지킨다」가 **네 번** 재발했다(n=57·58·59·61, 교훈 #362).
#   그래서 판정 테스트와 배선 테스트를 같은 파일에 둔다 — 판정만 촘촘히 물리면 그 침묵을 못 본다.
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdgroupScope,
    NaverAdgroupTargetCurrent,
    NaverCampaignSettings,
    NaverSearchTermExclusion,
)
from app.services.naver_ad import exclusion_slot_usage as esu
from app.services.naver_ad import ignition_preflight as pre
from app.services.naver_sa_ad_fetcher import _count_restrict_keywords

NOW = datetime(2026, 8, 27, 23, 0, 0)
CAMPAIGN = "cmp-1"
GROUP = "grp-a001-01-000000060841583"


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _target(**kw) -> NaverAdgroupTargetCurrent:
    base = dict(
        adgroup_id=GROUP, campaign_id=CAMPAIGN, probe_status=200,
        restrict_keyword_count=10, observed_at=NOW,
    )
    base.update(kw)
    return NaverAdgroupTargetCurrent(**base)


def _excl(**kw) -> NaverSearchTermExclusion:
    base = dict(
        campaign_id=CAMPAIGN, adgroup_id=GROUP, search_term="검색어",
        status="excluded", excluded_at=NOW, last_transition_at=NOW,
    )
    base.update(kw)
    return NaverSearchTermExclusion(**base)


def _client(db):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


# ══════════════ A. 카운터 — 쓰기 경로와 «같은» 규칙인가 ══════════════

def test_counts_keywords_in_restrict_target():
    items = [{"targetTp": "RESTRICT_KEYWORD_TARGET",
              "target": [{"keyword": "a"}, {"keyword": "b"}, {"keyword": "c"}]}]
    assert _count_restrict_keywords(items, GROUP) == 3


def test_absent_restrict_target_is_zero_not_unknown():
    """제외를 한 번도 안 건 그룹은 «0건»이다 — 모름이 아니다."""
    assert _count_restrict_keywords([{"targetTp": "MEDIA_TARGET", "target": {}}], GROUP) == 0


def test_none_target_is_zero_not_error():
    """★`target: None` = 「한 번도 설정한 적 없다」(2026-08-17 대조군 확정).

    초판 쓰기 경로는 이걸 raise로 받아 3그룹을 **매 스윕 영구 에러**로 만들었다.
    0건을 에러로 세는 것은 모름을 0건으로 세는 것만큼 나쁘다 — 방향만 반대다.
    """
    assert _count_restrict_keywords([{"targetTp": "RESTRICT_KEYWORD_TARGET", "target": None}], GROUP) == 0


def test_deleted_target_row_is_not_counted():
    items = [{"targetTp": "RESTRICT_KEYWORD_TARGET", "delFlag": True,
              "target": [{"keyword": "a"}, {"keyword": "b"}]}]
    assert _count_restrict_keywords(items, GROUP) == 0


def test_unexpected_shape_is_unknown_not_zero():
    """★스키마가 바뀌면 «모름»이다. 0이라 말하면 그 그룹이 영원히 «잔여 70칸»으로 보인다."""
    items = [{"targetTp": "RESTRICT_KEYWORD_TARGET", "target": "이건 리스트가 아니다"}]
    assert _count_restrict_keywords(items, GROUP) is None


def test_items_without_keyword_are_not_counted():
    items = [{"targetTp": "RESTRICT_KEYWORD_TARGET",
              "target": [{"keyword": "a"}, {"nope": 1}, "문자열"]}]
    assert _count_restrict_keywords(items, GROUP) == 1


# ══════════════ B. 적재 — 실패가 «0건»으로 새지 않는가 ══════════════

def test_non_200_does_not_touch_the_count():
    """★비-200이면 설정 필드를 손대지 않는다(이 파일 P1-1의 규율) — count도 그 필드다."""
    from app.services.naver_ad.adgroup_target_ingest import _values_from_parsed

    vals = _values_from_parsed(GROUP, CAMPAIGN, {"status": 404, "restrict_keyword_count": None})
    assert "restrict_keyword_count" not in vals, "실패가 기존 사용량을 덮으면 안 된다"


def test_zero_and_unknown_are_carried_apart_into_the_row():
    """★`or 0` 한 글자면 소멸하는 구분 — 적재 지점이 그걸 지키는가."""
    from app.services.naver_ad.adgroup_target_ingest import _values_from_parsed

    parsed = {"status": 200, "target_types": [], "media": None, "pc_mobile": None,
              "black_media": [], "black_mediagroup": []}
    assert _values_from_parsed(GROUP, CAMPAIGN, {**parsed, "restrict_keyword_count": 0})[
        "restrict_keyword_count"] == 0
    assert _values_from_parsed(GROUP, CAMPAIGN, {**parsed, "restrict_keyword_count": None})[
        "restrict_keyword_count"] is None


def test_count_is_in_the_change_ledger():
    """★원장에서 빼면 소진 «속도»를 영영 못 센다(소급 불가 축이다)."""
    from app.services.naver_ad.adgroup_target_ingest import _TRACKED_FIELDS

    assert "restrict_keyword_count" in _TRACKED_FIELDS


# ══════════════ C. 판정 — «모름»이 «여유»로 보이지 않는가 ══════════════

def test_unknown_usage_is_not_ok(db):
    db.add(_target(restrict_keyword_count=None, probe_status=404))
    db.commit()
    out = esu.slot_usage(db, now=NOW)
    assert out["rows"][0]["state"] == esu.STATE_UNKNOWN
    assert out["unknown"] == 1
    assert out["healthy"] is False, "못 본 것을 초록으로 두면 감시가 죽은 날부터 조용해진다"


def test_full_slots_are_red_without_any_threshold(db):
    db.add(_target(restrict_keyword_count=esu.EXCLUSION_SLOT_CAP))
    db.commit()
    out = esu.slot_usage(db, now=NOW)
    assert out["rows"][0]["state"] == esu.STATE_EXHAUSTED
    assert out["rows"][0]["remaining"] == 0
    assert out["healthy"] is False


def test_stale_observation_is_not_ok(db):
    db.add(_target(observed_at=NOW - timedelta(hours=esu.STALE_HOURS + 1)))
    db.commit()
    assert esu.slot_usage(db, now=NOW)["rows"][0]["state"] == esu.STATE_STALE


def test_stale_but_full_is_still_red(db):
    """★칸은 저절로 비지 않는다 — 관측이 묵었어도 70칸은 여전히 70칸이다."""
    db.add(_target(restrict_keyword_count=esu.EXCLUSION_SLOT_CAP,
                   observed_at=NOW - timedelta(days=30)))
    db.commit()
    assert esu.slot_usage(db, now=NOW)["rows"][0]["state"] == esu.STATE_EXHAUSTED


def test_worst_rows_come_first(db):
    """★표본 상한에 잘려도 빨강은 남아야 한다 — 정렬이 그 보장이다."""
    db.add(_target(adgroup_id="g-ok", restrict_keyword_count=1))
    db.add(_target(adgroup_id="g-full", restrict_keyword_count=70))
    db.add(_target(adgroup_id="g-unknown", restrict_keyword_count=None))
    db.commit()
    states = [r["state"] for r in esu.slot_usage(db, now=NOW)["rows"]]
    assert states[0] == esu.STATE_EXHAUSTED
    assert states.index(esu.STATE_UNKNOWN) < states.index(esu.STATE_OK)


# ══════════════ D. 소진 예상일 — 못 낼 땐 «사유»를 낸다 ══════════════

def test_eta_without_inflow_gives_reason_not_blank(db):
    db.add(_target(restrict_keyword_count=10))
    db.commit()
    row = esu.slot_usage(db, now=NOW)["rows"][0]
    assert row["exhaust_eta_days"] is None
    assert "관측되지 않는다" in row["exhaust_eta_reason"], "빈칸은 왜 비었는지 말하지 않는다"


def test_eta_is_labelled_as_an_upper_bound(db):
    """★우리 실집행이 0이라 지금 값은 «대행사 유입만 반영된 상한»이다. 그 한계를 값과 같이 낸다."""
    db.add(_target(restrict_keyword_count=40))
    for i in range(10):
        db.add(_excl(search_term=f"t{i}", source="console_import",
                     console_excluded_at=NOW - timedelta(days=3)))
    db.commit()
    row = esu.slot_usage(db, now=NOW)["rows"][0]
    assert row["exhaust_eta_days"] == pytest.approx(90.0, rel=0.01)  # 30칸 ÷ (10/30일)
    assert "상한" in row["exhaust_eta_reason"]


def test_full_group_eta_is_zero(db):
    db.add(_target(restrict_keyword_count=70))
    db.commit()
    assert esu.slot_usage(db, now=NOW)["rows"][0]["exhaust_eta_days"] == 0.0


# ══════════════ E. 귀속 3분 표기 (ref 66 §5-3) ══════════════

def test_ours_agency_and_unattributed_are_kept_apart(db):
    """★라이브가 정본이고 원장은 적게 나온다 — 그 차이가 «우리가 모르는 남의 칸»이다."""
    db.add(_target(restrict_keyword_count=10))
    db.add(_excl(search_term="ours-1"))
    db.add(_excl(search_term="agency-1", source="console_import"))
    db.add(_excl(search_term="agency-2", source="console_import"))
    db.commit()
    row = esu.slot_usage(db, now=NOW)["rows"][0]
    assert (row["ours"], row["agency"]) == (1, 2)
    assert row["unattributed"] == 7
    assert "반납하지 않는다" in esu.slot_usage(db, now=NOW)["reclaim_note"]


def test_unknown_source_is_not_folded_into_ours_or_agency(db):
    db.add(_target(restrict_keyword_count=1))
    db.add(_excl(search_term="x", source="something_else"))
    db.commit()
    row = esu.slot_usage(db, now=NOW)["rows"][0]
    assert (row["ours"], row["agency"], row["other_source"]) == (0, 0, 1)


# ══════════════ F. 닿는 층 — 화면까지 가는가 (교훈 #362) ══════════════

def test_health_route_actually_returns_exclusion_slots(db):
    """★`SchedulerHealthOut`에서 키를 빼면 여기서 죽는다 — 서비스층만 물리면 그 침묵을 못 본다."""
    db.add(_target(restrict_keyword_count=70))
    db.commit()
    try:
        r = _client(db).get("/api/scheduler/health")
        assert r.status_code == 200
        body = r.json()
        assert "exclusion_slots" in body, "response_model이 지웠다 — 화면까지 안 간다"
        assert body["exclusion_slots"]["exhausted"] == 1
        assert body["exclusion_slots"]["cap"] == 70
    finally:
        from app.main import app
        app.dependency_overrides.clear()


def test_exhausted_slots_actually_turn_the_banner_red(db):
    """★배너가 세기만 하고 판정에 안 넣으면 숫자는 뜨는데 아무도 안 본다."""
    from app.services.scheduler_health import build_health

    healthy_when_full = build_health(
        [], [], set(), True, NOW,
        exclusion_slots={"healthy": False, "exhausted": 1},
    )["healthy"]
    assert healthy_when_full is False


def test_slots_route_returns_usage(db):
    db.add(_target(restrict_keyword_count=42))
    db.commit()
    try:
        r = _client(db).get("/api/naver/ad/search-term/exclusion-slots")
        assert r.status_code == 200
        assert r.json()["rows"][0]["used"] == 42
        assert r.json()["rows"][0]["usage_pct"] == 60.0
    finally:
        from app.main import app
        app.dependency_overrides.clear()


# ══════════════ G. S6-b 켜기 선행 검사 ══════════════

def test_empty_scope_warns_that_everything_opens(db):
    """★「행이 없음」은 「아무것도 안 열림」이 아니라 「전부 열림」이다."""
    out = pre.check(db, CAMPAIGN)
    assert out["safe_to_ignite"] is False
    assert [w["code"] for w in out["warnings"]] == [pre.WARN_SCOPE_EMPTY]


def test_scope_rows_clear_the_warning(db):
    db.add(NaverAdgroupScope(campaign_id=CAMPAIGN, adgroup_id=GROUP, enabled=True))
    db.commit()
    codes = [w["code"] for w in pre.check(db, CAMPAIGN)["warnings"]]
    assert pre.WARN_SCOPE_EMPTY not in codes


def test_disabled_scope_row_still_counts_as_narrowed(db):
    """★enabled=False도 «행은 있다» — 전 그룹 폴백이 아니라 전 그룹 OFF가 의도다."""
    db.add(NaverAdgroupScope(campaign_id=CAMPAIGN, adgroup_id=GROUP, enabled=False))
    db.commit()
    codes = [w["code"] for w in pre.check(db, CAMPAIGN)["warnings"]]
    assert pre.WARN_SCOPE_EMPTY not in codes


def test_full_slots_warn_that_there_is_no_brake_left(db):
    db.add(NaverAdgroupScope(campaign_id=CAMPAIGN, adgroup_id=GROUP, enabled=True))
    db.add(_target(restrict_keyword_count=70))
    db.commit()
    codes = [w["code"] for w in pre.check(db, CAMPAIGN)["warnings"]]
    assert codes == [pre.WARN_SLOTS_EXHAUSTED]


def test_preflight_reads_gates_fail_closed(db):
    """★행이 없으면 «꺼짐»이다 — 없는 설정을 켜진 것으로 읽으면 안 된다."""
    out = pre.check(db, "no-such-campaign")
    assert out["auto_operate"] is False
    assert out["optimizer"] == "none"


def test_optimizer_switch_carries_the_preflight(db):
    """★켜는 요청엔 경고가 **응답에 실려야** 한다 — 창구가 둘이어도 판정기는 하나다."""
    try:
        c = _client(db)
        r = c.put("/api/naver/ad/campaign-settings/optimizer",
                  json={"campaign_id": CAMPAIGN, "optimizer": "ours"})
        assert r.status_code == 200
        codes = [w["code"] for w in r.json()["ignition_preflight"]["warnings"]]
        assert pre.WARN_SCOPE_EMPTY in codes
    finally:
        from app.main import app
        app.dependency_overrides.clear()


def test_turning_off_carries_no_warning(db):
    """★닫는 데 안전 경고를 붙이면 소음이고, 소음은 경고를 죽인다."""
    try:
        r = _client(db).put("/api/naver/ad/campaign-settings/optimizer",
                            json={"campaign_id": CAMPAIGN, "optimizer": "none"})
        assert r.status_code == 200
        assert "ignition_preflight" not in r.json()
    finally:
        from app.main import app
        app.dependency_overrides.clear()


def test_preflight_route_answers_before_anyone_flips_anything(db):
    """★`auto_operate`를 켜는 API는 없다(점화는 직접 UPDATE). 그러니 먼저 물어볼 창구가 필요하다."""
    try:
        r = _client(db).get("/api/naver/ad/campaign-settings/ignition-preflight",
                            params={"campaign_id": CAMPAIGN})
        assert r.status_code == 200
        assert r.json()["safe_to_ignite"] is False
    finally:
        from app.main import app
        app.dependency_overrides.clear()


def test_preflight_writes_nothing(db):
    """★검사가 상태를 바꾸면 그건 검사가 아니다."""
    db.add(NaverCampaignSettings(campaign_id=CAMPAIGN, optimizer="none", auto_operate=False))
    db.commit()
    pre.check(db, CAMPAIGN)
    row = db.query(NaverCampaignSettings).filter_by(campaign_id=CAMPAIGN).first()
    assert (row.optimizer, bool(row.auto_operate)) == ("none", False)
    assert db.query(NaverAdgroupScope).count() == 0
