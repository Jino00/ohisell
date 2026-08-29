"""ownership_timeline / perf_ownership_bands — 「그날 PAO가 실제로 맡고 있었나」 (성과분리 목표).

★이 테스트가 지키는 것은 「밴드가 계산된다」가 아니라 **「지금 관할을 과거에 소급하지 않는다」**이다.
그 구분이 이 계약의 전부다 — 실측(2026-08-29)에서 현재 스코프 소급은 2,170,514원, 당시 관할은
0원으로 **11배** 갈렸고, 현재 스코프는 그날 00:25에 생긴 것이었다.

픽스처 숫자·시각은 prod 실측에서 가져왔다(교훈: 픽스처가 prod 모양과 달라지면 결함을 못 잡는다).
"""
from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverAdgroupScope, NaverCampaignSettings, NaverChangeLog
from app.services.naver_ad import ownership_timeline as ot
from app.services.naver_ad import perf_ownership_bands as bands

# prod 실측 좌표
CAMP = "cmp-a001-02-000000008425541"
SCOPED_GROUP = "grp-a001-02-000000070523564"
OTHER_GROUP = "grp-a001-02-000000070000001"


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()


def _log(db, when: str, action: str, before, *, campaign_id=CAMP, entity_id="", after="__auto__"):
    """★`after`는 기본으로 «되감기와 일치하는 값»을 쓰지 않고 None을 쓴다.

    None이면 모순 검사가 «비교 대상 없음»으로 지나간다. 모순을 일부러 만들 때만 값을 준다.
    """
    db.add(
        NaverChangeLog(
            changed_at=datetime.fromisoformat(when),
            entity_type="adgroup" if action == ot.ACTION_SCOPE else "campaign",
            entity_id=entity_id or campaign_id,
            campaign_id=campaign_id,
            action=action,
            before_value=before,
            after_value=(None if after == "__auto__" else after),
        )
    )


def _daily(db, on: str, cost: int, *, campaign_id=CAMP, adgroup_id=SCOPED_GROUP):
    db.add(
        NaverAdDaily(
            ad_date=date.fromisoformat(on),
            campaign_id=campaign_id,
            campaign_type="SHOPPING",
            adgroup_id=adgroup_id,
            keyword_id="-",
            imp=100,
            clk=10,
            cost=cost,
            rank_sum=100,
            conv_direct_cnt=0,
            conv_indirect_cnt=0,
            conv_direct_amt=0,
            conv_indirect_amt=0,
        )
    )


def _settings(db, *, optimizer="none", auto_operate=False, campaign_id=CAMP):
    db.add(
        NaverCampaignSettings(
            campaign_id=campaign_id, optimizer=optimizer, auto_operate=auto_operate
        )
    )


# ══════════════════════════════════════════════════════════════════════════
# 1. 진리표 4행 + 「행 있는데 전부 disabled」
# ══════════════════════════════════════════════════════════════════════════


def _state(optimizer="ours", auto=True, scope=()):
    return ot.CampaignState(optimizer=optimizer, auto_operate=auto, scope=tuple(scope))


@pytest.mark.parametrize(
    "auto, scope, group, expected, why",
    [
        (False, (), SCOPED_GROUP, False, "마스터 OFF — 스코프 무엇이든 전 그룹 OFF"),
        (False, ((SCOPED_GROUP, True),), SCOPED_GROUP, False, "마스터 OFF가 항상 이긴다"),
        (True, (), SCOPED_GROUP, True, "ON + 행 없음 → 전 그룹 ON(소급 0)"),
        (True, (), OTHER_GROUP, True, "ON + 행 없음 → 정말 전 그룹"),
        (True, ((SCOPED_GROUP, True),), SCOPED_GROUP, True, "ON + g ∈ enabled"),
        (True, ((SCOPED_GROUP, True),), OTHER_GROUP, False, "ON + g ∉ enabled → OFF"),
        (True, ((SCOPED_GROUP, False),), SCOPED_GROUP, False, "행 있음·전부 disabled → 전 그룹 OFF"),
        (True, ((SCOPED_GROUP, False),), OTHER_GROUP, False, "전체 폴백 없음(되돌리기 사다리 첫 칸)"),
    ],
)
def test_truth_table(auto, scope, group, expected, why):
    assert ot.group_in_scope(_state(auto=auto, scope=scope), group) is expected, why


# ══════════════════════════════════════════════════════════════════════════
# 2. ★∧ 반례 — auto_operate ON인데 optimizer='none'이면 «관할 아님»
#    prod 실사례: 2026-07-30 10:48 ~ 08-29 12:53 (한 달)
# ══════════════════════════════════════════════════════════════════════════


def test_auto_operate_on_but_optimizer_none_is_not_pao():
    st = _state(optimizer="none", auto=True, scope=())
    # 진리표만 보면 «스코프 안»이다 — 그래서 진리표만 쓰면 이 한 달이 통째로 오답이 된다.
    assert ot.group_in_scope(st, SCOPED_GROUP) is True
    # 세 축의 ∧이므로 «PAO가 돌린다»는 아니다.
    assert ot.is_pao_managed(st, SCOPED_GROUP) is False


def test_optimizer_ours_but_auto_operate_off_is_not_pao():
    st = _state(optimizer="ours", auto=False, scope=())
    assert ot.is_pao_managed(st, SCOPED_GROUP) is False


def test_all_three_axes_on_is_pao():
    assert ot.is_pao_managed(_state(optimizer="ours", auto=True, scope=()), OTHER_GROUP) is True


# ══════════════════════════════════════════════════════════════════════════
# 3. before/after 포맷 3종 파싱 (실측 포맷)
# ══════════════════════════════════════════════════════════════════════════


def test_parse_scalar_format():
    assert ot._parse_optimizer("ours") == ({"optimizer": "ours"}, True)
    assert ot._parse_auto_operate("true") == ({"auto_operate": True}, True)
    assert ot._parse_auto_operate("false") == ({"auto_operate": False}, True)


def test_parse_json_format():
    """prod id 771의 실제 값."""
    fields, ok = ot._parse_optimizer('{"optimizer": "ours", "auto_operate": true}')
    assert ok and fields == {"optimizer": "ours", "auto_operate": True}


def test_parse_scope_string_format():
    """prod id 7640의 실제 값."""
    assert ot._parse_scope("role=None enabled=True") == (True, True)
    assert ot._parse_scope("role=accel enabled=False") == (False, True)


def test_parse_none_means_row_absent():
    """None = 그 시점엔 행이 없었다 → 기본값(fail-closed)."""
    assert ot._parse_optimizer(None) == ({"optimizer": "none", "auto_operate": False}, True)
    assert ot._parse_scope(None) == (None, True)


def test_parse_garbage_is_reported_not_guessed():
    assert ot._parse_optimizer("???")[1] is False
    assert ot._parse_auto_operate("maybe")[1] is False
    assert ot._parse_scope("역할만 있고 상태가 없음")[1] is False


# ══════════════════════════════════════════════════════════════════════════
# 4. 되감기 — 당시 관할 재구성 (prod 타임라인 축소판)
# ══════════════════════════════════════════════════════════════════════════


def _prod_shaped_history(db):
    """prod 실제 이력의 축소판:
    07-28 22:37 auto false→true · 07-30 10:48 optimizer ours→none ·
    08-29 00:25 scope 행 생성 · 08-29 12:53 optimizer none→ours
    현재 상태 = optimizer 'ours', auto True, scope {SCOPED_GROUP: True}
    """
    _settings(db, optimizer="ours", auto_operate=True)
    db.add(NaverAdgroupScope(campaign_id=CAMP, adgroup_id=SCOPED_GROUP, enabled=True))
    _log(db, "2026-07-28 22:37:16", ot.ACTION_AUTO_OPERATE, "false")
    _log(db, "2026-07-30 10:48:30", ot.ACTION_OPTIMIZER, "ours")
    _log(db, "2026-08-29 00:25:03", ot.ACTION_SCOPE, None, entity_id=SCOPED_GROUP)
    _log(db, "2026-08-29 12:53:11", ot.ACTION_OPTIMIZER, "none")
    db.commit()


def test_rewind_reconstructs_past_state(db):
    _prod_shaped_history(db)
    tl = ot.build(db)

    # 07-29: optimizer 'ours' ∧ auto ON ∧ 스코프 행 없음 = 캠페인 통째 관할
    s = tl.state_at(CAMP, date(2026, 7, 29))
    assert (s.optimizer, s.auto_operate, s.scope) == ("ours", True, ())
    assert tl.band(date(2026, 7, 29), CAMP, OTHER_GROUP) == ot.BAND_PAO, "통째 관할이면 아무 그룹이나 PAO"

    # 08-15: optimizer 'none'으로 끊긴 뒤 — auto는 켜져 있어도 관할 아님
    s = tl.state_at(CAMP, date(2026, 8, 15))
    assert (s.optimizer, s.auto_operate) == ("none", True)
    assert tl.band(date(2026, 8, 15), CAMP, SCOPED_GROUP) == ot.BAND_NOT_PAO

    # 07-27: auto가 켜지기 전
    assert tl.state_at(CAMP, date(2026, 7, 27)).auto_operate is False


def test_change_days_are_transition_not_guessed(db):
    _prod_shaped_history(db)
    tl = ot.build(db)
    for d in (date(2026, 7, 28), date(2026, 7, 30), date(2026, 8, 29)):
        assert tl.band(d, CAMP, SCOPED_GROUP) == ot.BAND_TRANSITION, f"{d}은 장중 전환일"


def test_current_scope_is_not_applied_retroactively(db):
    """★이 계약의 핵심 반증 — 오늘 만들어진 스코프가 과거를 좁히면 안 된다."""
    _prod_shaped_history(db)
    tl = ot.build(db)
    # 현재 스코프는 SCOPED_GROUP 하나뿐이지만, 07-29엔 행이 없었으므로 OTHER_GROUP도 관할이다.
    assert tl.band(date(2026, 7, 29), CAMP, OTHER_GROUP) == ot.BAND_PAO


def test_dates_before_history_start_are_unknown(db):
    _prod_shaped_history(db)
    tl = ot.build(db)
    assert tl.history_start == date(2026, 7, 28)
    assert tl.band(date(2026, 7, 27), CAMP, SCOPED_GROUP) == ot.BAND_UNKNOWN


def test_unparsable_event_makes_earlier_dates_unknown(db):
    """해석 못 한 이벤트를 건너뛰면 «틀린 상태로 계속 되감는다» — 그 앞은 모름이어야 한다."""
    _settings(db, optimizer="ours", auto_operate=True)
    _log(db, "2026-07-15 09:00:00", ot.ACTION_OPTIMIZER, "ours")  # 정상(가장 오래된 = 이력 시작)
    _log(db, "2026-08-01 09:00:00", ot.ACTION_OPTIMIZER, "쓰레기값")
    db.commit()
    tl = ot.build(db)

    assert tl.unparsable_count == 1
    assert tl.diagnostics()["unparsable_samples"][0]["before_value"] == "쓰레기값"
    # 08-01보다 과거는 못 믿는다
    assert tl.band(date(2026, 7, 20), CAMP, SCOPED_GROUP) == ot.BAND_UNKNOWN
    # 그 이후는 여전히 판정 가능
    assert tl.band(date(2026, 8, 10), CAMP, SCOPED_GROUP) == ot.BAND_PAO


def test_inconsistent_log_makes_earlier_dates_unknown(db):
    """★적대 리뷰 P1-1 — 로그가 「직후 auto=true」라 적었는데 되감기가 False를 들고 있으면,
    그 사이에 **기록 안 된 변경**이 있었다는 뜻이다. 확신에 찬 오답 대신 모름을 낸다.

    prod 실사례: `auto_operate`에는 앱 writer가 없어(grep 0건) 스크립트가 바꾼 변경이
    로그에 안 남는다 — 이벤트 865가 after='true'인데 그 뒤 끈 로그가 0건이고 현재는 False다.
    """
    _settings(db, optimizer="ours", auto_operate=False)  # 현재 auto=False
    _log(db, "2026-07-15 09:00:00", ot.ACTION_OPTIMIZER, "none")
    _log(db, "2026-07-28 12:44:41", ot.ACTION_AUTO_OPERATE, "false", after="true")
    db.commit()
    tl = ot.build(db)

    assert tl.inconsistent_count == 1
    assert tl.diagnostics()["inconsistent_samples"][0]["after_value"] == "true"
    # 07-28보다 과거는 못 믿는다 — 종전엔 not_pao라고 «확신»했다
    assert tl.band(date(2026, 7, 20), CAMP, SCOPED_GROUP) == ot.BAND_UNKNOWN
    assert tl.band(date(2026, 7, 29), CAMP, SCOPED_GROUP) == ot.BAND_NOT_PAO  # 이후는 판정 가능


def test_consistent_log_is_not_flagged(db):
    """after_value가 되감기와 맞으면 모순이 아니다 — 오탐이 나면 화면이 전부 모름이 된다."""
    _settings(db, optimizer="ours", auto_operate=True)
    _log(db, "2026-07-15 09:00:00", ot.ACTION_OPTIMIZER, "none", after="ours")
    db.commit()
    tl = ot.build(db)
    assert tl.inconsistent_count == 0
    assert tl.band(date(2026, 8, 10), CAMP, SCOPED_GROUP) == ot.BAND_PAO


def test_ownership_logging_gap_pushes_history_start(db):
    """로그는 도는데 관할 기록만 한참 뒤에 시작했으면 그 사이는 모름이다(적대 리뷰 P2 채택)."""
    _settings(db, optimizer="ours", auto_operate=True)
    # 관할과 무관한 로그가 훨씬 먼저 시작
    db.add(
        NaverChangeLog(
            changed_at=datetime.fromisoformat("2026-05-01 09:00:00"),
            entity_type="adgroup", entity_id="grp-x", campaign_id=CAMP,
            action="update_bid", before_value="100", after_value="200",
        )
    )
    _log(db, "2026-07-15 09:00:00", ot.ACTION_OPTIMIZER, "none")
    db.commit()
    tl = ot.build(db)

    assert tl.history_start == date(2026, 7, 15), "관할 기록 시작으로 밀려야 한다"
    assert tl.band(date(2026, 6, 1), CAMP, SCOPED_GROUP) == ot.BAND_UNKNOWN


def test_unparsable_events_are_all_counted(db):
    """★캠페인당 1건만 세면 화면이 「1건」이라 말하는데 실제로는 여럿인 상태가 된다(P2 채택)."""
    _settings(db, optimizer="ours", auto_operate=True)
    _log(db, "2026-07-15 09:00:00", ot.ACTION_OPTIMIZER, "none")
    for when in ("2026-08-01 09:00:00", "2026-08-02 09:00:00", "2026-08-03 09:00:00"):
        _log(db, when, ot.ACTION_OPTIMIZER, "쓰레기값")
    db.commit()
    assert ot.build(db).unparsable_count == 3


def test_campaign_without_settings_row_is_never_pao(db):
    _log(db, "2026-07-15 09:00:00", ot.ACTION_OPTIMIZER, "none", campaign_id="cmp-other")
    db.commit()
    tl = ot.build(db)
    assert tl.band(date(2026, 8, 10), "cmp-nosettings", "grp-x") == ot.BAND_NOT_PAO


# ══════════════════════════════════════════════════════════════════════════
# 5. 항등식 — 전체 = PAO + 비관할 + 전환일 + 모름
# ══════════════════════════════════════════════════════════════════════════


def _band(result, name):
    return next(b for b in result["bands"] if b["band"] == name)


def test_bands_partition_the_total(db):
    _prod_shaped_history(db)
    _daily(db, "2026-07-27", 1000)  # 이력 시작(07-28) 이전 → 모름
    _daily(db, "2026-07-29", 5000)  # PAO
    _daily(db, "2026-07-30", 3000)  # 전환일
    _daily(db, "2026-08-15", 7000)  # 비관할
    db.commit()

    r = bands.bands(db, date(2026, 7, 20), date(2026, 8, 28))

    assert r["identity"]["ok"] is True
    assert r["identity"]["diff"] == 0
    assert r["total"]["cost"] == 16000
    assert _band(r, ot.BAND_PAO)["cost"] == 5000
    assert _band(r, ot.BAND_NOT_PAO)["cost"] == 7000
    assert _band(r, ot.BAND_TRANSITION)["cost"] == 3000
    assert _band(r, ot.BAND_UNKNOWN)["cost"] == 1000


def test_identity_is_reported_false_when_a_band_goes_missing(db, monkeypatch):
    """★항등식 «검사»가 살아 있는지 — 밴드 하나가 집계에서 새면 ok=False로 화면까지 가야 한다.

    이 단언이 없으면 「전체 = 합」을 계산만 하고 아무도 안 보는 코드가 된다(계약 §3 항등식).
    """
    _prod_shaped_history(db)
    _daily(db, "2026-07-29", 5000)  # PAO
    _daily(db, "2026-08-15", 7000)  # 비관할
    db.commit()

    real_band = ot.OwnershipTimeline.band

    def _leak_pao(self, on, campaign_id, adgroup_id):
        b = real_band(self, on, campaign_id, adgroup_id)
        return "밴드가아닌값" if b == ot.BAND_PAO else b

    monkeypatch.setattr(ot.OwnershipTimeline, "band", _leak_pao, raising=True)
    r = bands.bands(db, date(2026, 7, 20), date(2026, 8, 28))

    assert r["identity"]["ok"] is False, "밴드가 샜는데 항등식이 통과하면 검사가 죽은 것이다"
    assert r["identity"]["diff"] == 5000
    assert r["total"]["cost"] == 12000
    assert any("맞지 않습니다" in n for n in r["notes"]), "화면이 그 사실을 말해야 한다"


def test_bands_never_include_unconfirmed_today(db):
    """오늘치 혼입 0 — date_to가 최신 확정일로 잘리고 truncated가 그 사실을 말한다."""
    _prod_shaped_history(db)
    _daily(db, "2026-08-15", 7000)
    db.commit()

    r = bands.bands(db, date(2026, 7, 20), date(2026, 12, 31))
    assert r["window"]["latest_confirmed"] == "2026-08-15"
    assert r["window"]["date_to"] == "2026-08-15"
    assert r["window"]["truncated"] is True
    assert any("확정" in n for n in r["notes"])


def test_recent_window_counts_confirmed_days_not_calendar_days(db):
    """★적대 리뷰 P1-2 — 「N일」이라 적었으면 **확정 N일**이어야 한다.

    창 기준점을 오늘로 잡고 date_to만 최신 확정일로 자르면, 확정이 D-1까지인 이상 실제 창은
    항상 N-1일이 되어 **가장 오래된 하루가 말없이 빠진다**. 실사례에서 빠진 날이 하필
    07-30 — 관할이 끊긴 바로 그날 — 이었다.
    """
    _prod_shaped_history(db)
    _daily(db, "2026-07-30", 3000)  # 창의 첫날(전환일)
    _daily(db, "2026-08-15", 7000)
    _daily(db, "2026-08-28", 5000)  # 최신 확정일
    db.commit()

    r = bands.recent(db, 30)
    assert r["window"]["latest_confirmed"] == "2026-08-28"
    assert r["window"]["date_to"] == "2026-08-28"
    # 08-28에서 30일 = 07-30 포함
    assert r["window"]["date_from"] == "2026-07-30"
    assert _band(r, ot.BAND_TRANSITION)["cost"] == 3000, "창 첫날이 빠지면 안 된다"
    assert r["total"]["cost"] == 15000


def test_band_denominators_are_reported(db):
    """금액만 내면 「얼마나 맡고 있나」가 안 읽힌다 — 분모가 함께 나와야 한다."""
    _prod_shaped_history(db)
    _daily(db, "2026-07-29", 5000, adgroup_id=SCOPED_GROUP)
    _daily(db, "2026-07-29", 2000, adgroup_id=OTHER_GROUP)
    db.commit()

    pao = _band(bands.bands(db, date(2026, 7, 29), date(2026, 7, 29)), ot.BAND_PAO)
    assert pao["adgroups"] == 2
    assert pao["campaigns"] == 1
    assert pao["days"] == 1
    assert pao["share_of_cost"] == 1.0


def test_campaign_bands_expose_partial_ownership(db):
    """Jino: "광고그룹만도 가져올 수 있잖아" — 부분 관할이 숫자로 보여야 한다."""
    _prod_shaped_history(db)
    _daily(db, "2026-08-28", 5000, adgroup_id=SCOPED_GROUP)
    _daily(db, "2026-08-28", 2000, adgroup_id=OTHER_GROUP)
    # 08-28은 optimizer 'none' 구간이라 비관할 — 관할 구간을 만들려면 08-30 이후가 필요하다.
    db.commit()

    r = bands.campaign_bands(db, as_of=date(2026, 8, 28))
    assert r["as_of"] == "2026-08-28"
    slot = r["campaigns"][CAMP]
    assert slot["adgroups"] == 2
    assert slot["band"] == ot.BAND_NOT_PAO
    assert slot["partial"] is False
