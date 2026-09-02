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


def test_gate_does_not_read_a_truncated_sample(db):
    """★★적대 리뷰 1R P1-1의 재현을 고정한다.

    초판 W2는 `slot_usage()["rows"]`(표본 상한 20건)를 훑었다. 계정 전체 exhausted가 21개를
    넘으면 **점검 대상 캠페인의 70/70 그룹이 표본 밖으로 밀려나** 경고가 통째로 사라지고
    `safe_to_ignite: true`가 나갔다 — 「검사했는데 깨끗하다」와 「검사가 놓쳤다」가 응답에서
    구분되지 않았다. 초판 주석의 「정렬이 빨강 우선이라 표본에 남을 확률이 높다」는 **확률이지
    보장이 아니었고**, 라이브는 이미 그 문턱에 붙어 있다(70/70 도달 15개 vs 상한 20).
    """
    # 다른 캠페인들에 표본을 가득 채운다 — adgroup_id가 알파벳순으로 앞서게 둔다.
    for i in range(esu.SAMPLE_CAP + 6):
        db.add(_target(adgroup_id=f"aaa-{i:03d}", campaign_id=f"other-{i}",
                       restrict_keyword_count=esu.EXCLUSION_SLOT_CAP))
    # 점검 대상 캠페인의 70/70 그룹은 정렬상 **맨 뒤**에 온다.
    db.add(_target(adgroup_id="zzz-target-group", campaign_id=CAMPAIGN,
                   restrict_keyword_count=esu.EXCLUSION_SLOT_CAP))
    db.add(NaverAdgroupScope(campaign_id=CAMPAIGN, adgroup_id="zzz-target-group", enabled=True))
    db.commit()

    # 전제 확인: 표본은 실제로 잘렸고, 대상 그룹은 그 표본 «밖»에 있다.
    usage = esu.slot_usage(db, now=NOW)
    assert usage["rows_truncated"] > 0
    assert "zzz-target-group" not in [r["adgroup_id"] for r in usage["rows"]]

    out = pre.check(db, CAMPAIGN)
    codes = [w["code"] for w in out["warnings"]]
    assert pre.WARN_SLOTS_EXHAUSTED in codes, "표본 밖이라고 경고가 사라지면 안 된다"
    assert out["safe_to_ignite"] is False


def test_exhausted_lookup_is_scoped_to_the_campaign(db):
    """★남의 캠페인 빨강을 이 캠페인 경고로 옮기면 매번 빨강이라 아무도 안 읽는다."""
    db.add(_target(adgroup_id="g-other", campaign_id="other-cmp",
                   restrict_keyword_count=esu.EXCLUSION_SLOT_CAP))
    db.add(_target(adgroup_id="g-mine", campaign_id=CAMPAIGN, restrict_keyword_count=1))
    db.commit()
    assert esu.exhausted_adgroups(db, CAMPAIGN, now=NOW) == []
    assert esu.exhausted_adgroups(db, "other-cmp", now=NOW) == ["g-other"]


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


# ══════════════ F. 계정 수준 귀속 3분할 · 스윕 시각 (슬라이스 2) ══════════════
#
# ★왜 필요했나: 설계서 §5-4가 「귀속 3분할 — 미귀속을 0으로 뭉개지 않는다」를 요구하는데,
#   `totals`엔 ours·agency뿐이라 화면이 `used - ours - agency`로 «추정»할 수밖에 없었다.
#   그 추정은 other_source를 미귀속에 뭉갠다 — 이 모듈 머리말이 뭉개지 말라고 적은 바로
#   그 값을 화면이 뭉개게 된다. 그래서 누계를 모듈이 직접 낸다.

def test_totals_carry_the_third_bucket_so_the_screen_need_not_guess(db):
    """미귀속이 «추정»이 아니라 «집계»로 나온다 — other_source와 섞이지 않는다."""
    db.add(_target(restrict_keyword_count=70))
    db.add(_excl(search_term="우리1"))                              # ours
    db.add(_excl(search_term="대행사1", source="console_import"))    # agency
    db.add(_excl(search_term="기타1", source="somewhere_else"))      # other_source
    db.commit()
    t = esu.slot_usage(db, now=NOW)["totals"]
    assert t["used"] == 70
    assert (t["ours"], t["agency"], t["other_source"]) == (1, 1, 1)
    # 70 − 1 − 1 − 1 = 67. other_source를 미귀속에 뭉갰다면 68이 된다.
    assert t["unattributed"] == 67, "other_source가 미귀속에 뭉개졌다"
    assert t["ours"] + t["agency"] + t["other_source"] + t["unattributed"] == t["used"], \
        "네 칸의 합이 라이브 총계와 같아야 한다 — 아니면 어딘가가 새고 있다"


def test_unknown_group_does_not_inflate_unattributed(db):
    """못 센 그룹(used=None)은 미귀속을 부풀리지 않는다 — «모름»은 «남의 칸»이 아니다."""
    db.add(_target(adgroup_id="grp-known", restrict_keyword_count=10))
    db.add(_target(adgroup_id="grp-unknown", restrict_keyword_count=None, probe_status=404))
    db.commit()
    t = esu.slot_usage(db, now=NOW)["totals"]
    assert t["used"] == 10
    assert t["unattributed"] == 10, "못 센 그룹이 미귀속에 섞였다"


def test_sweep_window_is_reported_apart_from_response_time(db):
    """★`as_of`는 «응답을 만든 시각»이다. 화면이 그걸 기준 시각이라 하면 09:35에 본 것을
    20:00 기준이라 말하는 거짓말이 된다 — 그래서 관측 창을 따로 낸다."""
    early = NOW - timedelta(hours=3)
    db.add(_target(adgroup_id="grp-early", observed_at=early))
    db.add(_target(adgroup_id="grp-late", observed_at=NOW))
    db.commit()
    out = esu.slot_usage(db, now=NOW)
    assert out["observed_from"] == early.isoformat()
    assert out["observed_to"] == NOW.isoformat()
    assert out["observed_to"] != out["as_of"] or early != NOW  # 둘은 다른 개념이다


def test_sweep_window_is_null_when_there_is_nothing_to_observe(db):
    """관측 행이 하나도 없으면 시각을 «지어내지» 않는다 — null이지 now가 아니다.

    ★초판은 `observed_at=None`인 행을 넣어 이걸 재려 했는데 그 열은
    `Mapped[datetime]` + `server_default=func.now()`라 **None이 될 수 없다**(실측으로
    드러났다). 즉 「관측 시각이 빈 행」은 이 스키마에서 불가능한 상태이고, 실제로 창이
    비는 경우는 «행이 없을 때»뿐이다. 못 일어나는 일을 재는 테스트는 통과해도 아무것도
    보증하지 않는다."""
    out = esu.slot_usage(db, now=NOW)
    assert out["groups"] == 0
    assert out["observed_from"] is None and out["observed_to"] is None
    assert out["totals"]["unattributed"] == 0


def test_existing_keys_are_untouched(db):
    """가산이다 — 기존 소비처가 읽던 키가 하나도 사라지지 않았다."""
    db.add(_target())
    db.commit()
    out = esu.slot_usage(db, now=NOW)
    for k in ("cap", "as_of", "groups", "exhausted", "unknown", "stale",
              "healthy", "rows", "rows_truncated", "totals", "reclaim_note"):
        assert k in out, f"기존 키 {k} 가 사라졌다"
    for k in ("used", "ours", "agency", "capacity"):
        assert k in out["totals"], f"기존 totals 키 {k} 가 사라졌다"


# ══════════════ G. 광고그룹 드릴다운 — «무엇이 걸려 있나» (Jino 2026-09-02 21:23) ══════════════
#
# Jino 원문: *"광고그룹이 나와야 할꺼고 그 광고그룹에 등록된 제외키워드들이 보여야 하는거 아니야?"*
# ★슬롯 화면은 「몇 칸 찼나」만 말했다. 「무엇이 걸려 있나」는 이 창구가 답한다.
# ★필터가 캠페인 단위뿐이면 한 캠페인에 그룹이 수십 개라 `limit` 안에서 그 그룹 몫이 잘려
#   「없다」로 보인다 — `exclude_console_import`가 이미 한 번 밟은 병과 같은 모양이다.

def test_drilldown_can_narrow_to_one_adgroup(db):
    """그룹 단위로 좁혀진다 — 캠페인 단위로만 되면 옆 그룹 것이 섞인다."""
    db.add(_excl(adgroup_id="grp-A", search_term="이쪽"))
    db.add(_excl(adgroup_id="grp-B", search_term="저쪽"))
    db.commit()
    c = _client(db)
    r = c.get("/api/naver/ad/search-term/exclusions", params={"adgroup_id": "grp-A"})
    assert r.status_code == 200
    terms = [x["search_term"] for x in r.json()["rows"]]
    assert terms == ["이쪽"], f"그룹 필터가 안 걸렸다: {terms}"


def test_drilldown_keeps_the_source_so_attribution_survives(db):
    """검색어마다 «누가 걸었나»가 같이 온다 — 출처가 없으면 화면의 귀속 3분할과 갈라진다."""
    db.add(_excl(adgroup_id="grp-A", search_term="우리것"))
    db.add(_excl(adgroup_id="grp-A", search_term="대행사것", source="console_import"))
    db.commit()
    c = _client(db)
    rows = c.get("/api/naver/ad/search-term/exclusions",
                 params={"adgroup_id": "grp-A"}).json()["rows"]
    by = {x["search_term"]: x.get("source") for x in rows}
    assert by == {"우리것": None, "대행사것": "console_import"}


def test_drilldown_is_empty_when_the_ledger_knows_nothing(db):
    """★라이브가 70칸이어도 원장이 모르면 «0건»이다 — 지어내지 않는다.

    2026-09-02 실측: 70/70 소진 6그룹 중 원장이 검색어를 아는 건 **1그룹뿐**이고
    나머지 5개(TEST_ 그룹)는 원장 0건이다. 그 차이가 곧 「미귀속」이다."""
    db.add(_target(adgroup_id="grp-full", restrict_keyword_count=70))
    db.commit()
    out = esu.slot_usage(db, now=NOW)
    assert out["rows"][0]["used"] == 70
    assert out["rows"][0]["unattributed"] == 70, "원장이 0이면 70칸 전부가 미귀속이다"
    rows = _client(db).get("/api/naver/ad/search-term/exclusions",
                           params={"adgroup_id": "grp-full"}).json()["rows"]
    assert rows == []


def test_row_carries_campaign_name_so_the_screen_can_place_the_group(db):
    """★그룹 이름만으론 «어느 캠페인 것인지» 모른다.

    Jino 2026-09-02: *"어느 광고캠페인에 속해있는 광고그룹인지 알 수 없어"*.
    「01. TEST_S20」 같은 이름은 캠페인을 모르면 어디 것인지 가려낼 수 없다."""
    from app.models import NaverEntity
    db.add(_target())
    db.add(NaverEntity(entity_type="campaign", entity_id=CAMPAIGN, name="01. 갤럭시_지문방지_TPU"))
    db.add(NaverEntity(entity_type="adgroup", entity_id=GROUP, name="02. S23FE"))
    db.commit()
    r = esu.slot_usage(db, now=NOW)["rows"][0]
    assert r["campaign_name"] == "01. 갤럭시_지문방지_TPU"
    assert r["name"] == "02. S23FE"


def test_missing_campaign_name_is_blank_not_invented(db):
    """이름을 못 찾으면 빈 문자열이다 — 프론트가 id로 폴백한다. 지어내지 않는다."""
    db.add(_target())
    db.commit()
    assert esu.slot_usage(db, now=NOW)["rows"][0]["campaign_name"] == ""


# ══════════════ H. 적대 리뷰 P1-1·P1-2 — 항등식과 방향 ══════════════

def test_identity_holds_even_when_an_uncounted_group_has_ledger_rows(db):
    """★P1-1 회귀 고정. 이 자리를 지키는 «척»하던 테스트가 이미 있었다
    (`test_unknown_group_does_not_inflate_unattributed`) — 그런데 그건 **원장 행이 없는**
    못 센 그룹만 넣어서 **원리적으로 실패할 수 없었다.** prod에선 못 센 그룹에 원장 행이
    67개 붙어 있었고, 그래서 계정 합이 `2+3984+0+1838 = 5824 ≠ 5757`로 어긋났다.
    막대는 flex라 폭 합이 101%여도 눌려서 **정상으로 보인다.**"""
    db.add(_target(adgroup_id="grp-counted", restrict_keyword_count=10))
    db.add(_excl(adgroup_id="grp-counted", search_term="센그룹것"))
    # ★핵심: 못 센 그룹인데 원장 행이 «있다»
    db.add(_target(adgroup_id="grp-uncounted", restrict_keyword_count=None, probe_status=404))
    db.add(_excl(adgroup_id="grp-uncounted", search_term="못센그룹것1"))
    db.add(_excl(adgroup_id="grp-uncounted", search_term="못센그룹것2", source="console_import"))
    db.commit()
    t = esu.slot_usage(db, now=NOW)["totals"]
    assert t["ours"] + t["agency"] + t["other_source"] + t["unattributed"] == t["used"], (
        f"항등식 파괴: {t['ours']}+{t['agency']}+{t['other_source']}+{t['unattributed']}"
        f" != {t['used']}"
    )
    # 빠진 몫은 «사라지지» 않는다 — 따로 세어 화면이 말할 수 있게 한다.
    assert t["uncounted_ledger"] == 2


def test_unattributed_is_split_by_direction_because_the_two_mean_opposite_things(db):
    """★P1-2 회귀 고정. 순액 하나로 뭉치면 「0으로 뭉개는 것」과 정보량이 같아진다.

    prod 실측(2026-09-02): 라이브 초과 **+3,662**(모르는 남의 칸) / 원장 초과 **−1,824**
    (58그룹 — 우리가 건 제외가 라이브에 안 보인다). 순액은 1,838이고, 그 절반이 상계다.
    백엔드 주석 자신이 음수를 *"우리 조치가 지워졌을 수 있다"*라고 다른 사실로 적어 뒀다."""
    # 라이브가 원장보다 많은 그룹: +7
    db.add(_target(adgroup_id="grp-live-more", restrict_keyword_count=10))
    db.add(_excl(adgroup_id="grp-live-more", search_term="a"))
    db.add(_excl(adgroup_id="grp-live-more", search_term="b", source="console_import"))
    db.add(_excl(adgroup_id="grp-live-more", search_term="c", source="console_import"))
    # 원장이 라이브보다 많은 그룹: −2 (우리 조치가 라이브에서 사라졌다)
    db.add(_target(adgroup_id="grp-ledger-more", restrict_keyword_count=1))
    db.add(_excl(adgroup_id="grp-ledger-more", search_term="x"))
    db.add(_excl(adgroup_id="grp-ledger-more", search_term="y"))
    db.add(_excl(adgroup_id="grp-ledger-more", search_term="z"))
    db.commit()
    t = esu.slot_usage(db, now=NOW)["totals"]
    assert t["live_excess"] == 7, "라이브 초과분이 상계돼 사라졌다"
    assert t["ledger_excess"] == 2, "원장 초과분이 상계돼 사라졌다"
    assert t["ledger_excess_groups"] == 1
    # 순액은 남기되, 그것만 보면 두 사실이 하나로 뭉개진다.
    assert t["unattributed"] == 5 == t["live_excess"] - t["ledger_excess"]
    assert t["ours"] + t["agency"] + t["other_source"] + t["unattributed"] == t["used"]


def test_all_ledger_excess_still_keeps_the_identity(db):
    """전부 원장 초과인 계정에서도 항등식은 선다(미귀속이 «음수»가 된다)."""
    db.add(_target(restrict_keyword_count=1))
    db.add(_excl(search_term="p"))
    db.add(_excl(search_term="q"))
    db.commit()
    t = esu.slot_usage(db, now=NOW)["totals"]
    assert t["unattributed"] == -1
    assert t["live_excess"] == 0 and t["ledger_excess"] == 1
    assert t["ours"] + t["agency"] + t["other_source"] + t["unattributed"] == t["used"]


def test_bar_inputs_are_never_negative(db):
    """★화면 막대에 들어가는 네 값은 **음수가 될 수 없다** — 그게 clamp가 도달 불가인 이유다.

    적대 리뷰 N4가 프론트의 폭 clamp를 지워도 안 죽는다고 지적했는데, 원인은 테스트 부재가
    아니라 **입력이 구조적으로 비음수**라서다(순액 `unattributed`는 음수가 될 수 있지만
    막대는 그걸 안 쓴다 — `live_excess`를 쓴다). 그 불변식을 «참인 자리»에서 고정한다.
    프론트 clamp는 그 위의 보험이고, 이 테스트가 깨지면 clamp가 실제로 필요해진다."""
    # 원장이 라이브보다 많아 순액이 음수가 되는 계정
    db.add(_target(adgroup_id="grp-a", restrict_keyword_count=1))
    for i, t in enumerate(("x", "y", "z")):
        db.add(_excl(adgroup_id="grp-a", search_term=t, source=None if i else "console_import"))
    db.commit()
    t = esu.slot_usage(db, now=NOW)["totals"]
    assert t["unattributed"] < 0, "이 픽스처는 순액이 음수여야 의미가 있다"
    for k in ("ours", "agency", "other_source", "live_excess", "ledger_excess",
              "uncounted_ledger", "used", "capacity"):
        assert t[k] >= 0, f"막대 입력 {k}가 음수다: {t[k]}"
