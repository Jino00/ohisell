# test_naver_today_hourly_sweep.py — D-NAO-122 당일 그룹 grain 시간별 축적 단위 테스트
# 커버: build_today_targets(어제 adgroup grain ∪ 살아있는 출시창·키워드 제외·만료 배제)·
#   rotate_for_hour(상한 초과 시 회전)·drop_incomplete_hour(진행 중/미래 버킷 제거)·
#   sweep_adgroup_hourly_today(교체 upsert·빈 응답 보존·완결 0건 보존·회전·킬스위치·롤링)·
#   ★naver_keyword_hourly 완결 불변식 비오염(codex P1 회귀)·스케줄러 등록.
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdDaily,
    NaverAdgroupHourlyToday,
    NaverAdgroupProduct,
    NaverKeywordHourly,
    NaverLearningState,
)
from app.services.naver_ad import today_hourly_sweep as ths
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.services.naver_ad.launch_rank_floor import LAUNCH_WINDOW_DAYS, METRIC_TARGET_RANK

TODAY = date(2026, 7, 29)
YESTERDAY = TODAY - timedelta(days=1)
NOW = datetime(2026, 7, 29, 12, 24)  # hour=12 진행 중


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _daily(db, *, adgroup_id, keyword_id="", imp=10, ad_date=YESTERDAY,
           campaign_id="cmp-1", campaign_type="SHOPPING"):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type=campaign_type,
        adgroup_id=adgroup_id, keyword_id=keyword_id, imp=imp, clk=0, cost=0,
    ))
    db.commit()


def _launch_target(db, *, ad_id, adgroup_id, campaign_id="cmp-2", rank=4):
    db.add(NaverLearningState(
        scope="entity", scope_key=ad_id, metric=METRIC_TARGET_RANK, current_value=rank,
    ))
    db.add(NaverAdgroupProduct(
        adgroup_id=adgroup_id, campaign_id=campaign_id, mall_product_id="p1", ad_id=ad_id,
    ))
    db.commit()


def _hh(hour, imp=10, clk=1, cost=100, conv_cnt=0, avg_rank=3.0):
    return {"hour": hour, "imp": imp, "clk": clk, "cost": cost, "conv_cnt": conv_cnt, "avg_rank": avg_rank}


def _fetch_ok(eid, d):
    return [_hh(10)]


# ══════════════════════════════════════════════════════════════════
# build_today_targets — 타깃 도출
# ══════════════════════════════════════════════════════════════════
def test_targets_take_yesterday_adgroup_grain(db):
    _daily(db, adgroup_id="grp-1")
    targets = ths.build_today_targets(db, TODAY)
    assert [t["entity_id"] for t in targets] == ["grp-1"]
    assert targets[0]["entity_type"] == "adgroup"


def test_targets_exclude_keyword_grain(db):
    """★키워드 유닛은 매시 실행 시 콜 폭증 — Jino 확정 2026-07-29로 제외한다."""
    _daily(db, adgroup_id="grp-1", keyword_id="nkw-9", campaign_type="WEB_SITE")
    assert ths.build_today_targets(db, TODAY) == []


def test_targets_exclude_backfill_sentinel(db):
    _daily(db, adgroup_id=BACKFILL_SENTINEL_ADGROUP)
    assert ths.build_today_targets(db, TODAY) == []


def test_targets_exclude_zero_impression_units(db):
    _daily(db, adgroup_id="grp-quiet", imp=0)
    assert ths.build_today_targets(db, TODAY) == []


def test_targets_add_launch_window_group_absent_yesterday(db):
    """★콜드스타트 보강: 오늘 처음 도는 출시창 그룹은 어제 ad_daily에 없어 관측 밖에 남는다."""
    _launch_target(db, ad_id="nad-new", adgroup_id="grp-new")

    targets = {t["entity_id"]: t for t in ths.build_today_targets(db, TODAY)}
    assert "grp-new" in targets
    assert targets["grp-new"]["campaign_id"] == "cmp-2"


def test_targets_exclude_expired_launch_window(db):
    """★codex P2: launch_target_rank 행은 21일 뒤에도 남는다 — 만료 그룹이 노출 0인 채로
    영구히 매시 조회되면 콜 예산을 먹고 현재 출시 그룹을 상한 밖으로 민다."""
    _launch_target(db, ad_id="nad-old", adgroup_id="grp-old")
    # 첫 노출이 창(21일)보다 오래됨 → 출시창 종료
    _daily(db, adgroup_id="grp-old", imp=5,
           ad_date=TODAY - timedelta(days=LAUNCH_WINDOW_DAYS + 5))

    assert [t["entity_id"] for t in ths.build_today_targets(db, TODAY)] == []


def test_targets_keep_launch_window_still_open(db):
    _launch_target(db, ad_id="nad-mid", adgroup_id="grp-mid")
    _daily(db, adgroup_id="grp-mid", imp=5, ad_date=TODAY - timedelta(days=3))

    assert "grp-mid" in [t["entity_id"] for t in ths.build_today_targets(db, TODAY)]


def test_targets_dedupe_launch_window_keeps_daily_row(db):
    """어제 실적도 있고 출시창도 걸린 그룹은 1건만 — campaign_type을 가진 ① 쪽이 이긴다."""
    _daily(db, adgroup_id="grp-1", campaign_type="SHOPPING")
    _launch_target(db, ad_id="nad-1", adgroup_id="grp-1", campaign_id="cmp-1")

    targets = ths.build_today_targets(db, TODAY)
    assert len(targets) == 1
    assert targets[0]["campaign_type"] == "SHOPPING"


def test_targets_ignore_learning_state_without_value(db):
    db.add(NaverLearningState(
        scope="entity", scope_key="nad-x", metric=METRIC_TARGET_RANK, current_value=None,
    ))
    db.add(NaverAdgroupProduct(
        adgroup_id="grp-x", campaign_id="cmp-2", mall_product_id="p1", ad_id="nad-x",
    ))
    db.commit()
    assert ths.build_today_targets(db, TODAY) == []


# ══════════════════════════════════════════════════════════════════
# rotate_for_hour — 상한 초과 시 회전(순수)
# ══════════════════════════════════════════════════════════════════
def _t(i):
    return {"entity_id": f"grp-{i}"}


def test_rotate_noop_when_under_cap():
    ts = [_t(i) for i in range(3)]
    assert ths.rotate_for_hour(ts, hour=7, cap=10) is ts


def test_rotate_shifts_window_by_hour():
    ts = [_t(i) for i in range(10)]
    h0 = [t["entity_id"] for t in ths.rotate_for_hour(ts, hour=0, cap=4)]
    h1 = [t["entity_id"] for t in ths.rotate_for_hour(ts, hour=1, cap=4)]
    assert h0 == ["grp-0", "grp-1", "grp-2", "grp-3"]
    assert h1 == ["grp-4", "grp-5", "grp-6", "grp-7"]


def test_rotate_covers_every_target_across_the_day():
    """★codex P2 회귀: 상한 뒤쪽 그룹이 영원히 조회되지 않는 기아를 막는다."""
    ts = [_t(i) for i in range(50)]
    seen: set[str] = set()
    for hour in range(24):
        seen.update(t["entity_id"] for t in ths.rotate_for_hour(ts, hour=hour, cap=8))
    assert seen == {t["entity_id"] for t in ts}


def test_rotate_wraps_around_end_of_list():
    ts = [_t(i) for i in range(5)]
    out = [t["entity_id"] for t in ths.rotate_for_hour(ts, hour=1, cap=3)]
    assert out == ["grp-3", "grp-4", "grp-0"]


# ══════════════════════════════════════════════════════════════════
# drop_incomplete_hour — 진행 중 버킷 제거(순수)
# ══════════════════════════════════════════════════════════════════
def test_drop_incomplete_hour_removes_current_and_future():
    rows = [_hh(10), _hh(11), _hh(12), _hh(13)]
    assert [h["hour"] for h in ths.drop_incomplete_hour(rows, NOW)] == [10, 11]


def test_drop_incomplete_hour_at_midnight_keeps_nothing():
    assert ths.drop_incomplete_hour([_hh(0)], datetime(2026, 7, 29, 0, 5)) == []


# ══════════════════════════════════════════════════════════════════
# sweep_adgroup_hourly_today — Harness
# ══════════════════════════════════════════════════════════════════
def test_sweep_writes_complete_hours_only(db):
    _daily(db, adgroup_id="grp-1")

    def fetch(eid, d):
        return [_hh(10, avg_rank=3.4), _hh(11, avg_rank=4.5), _hh(12, avg_rank=9.9)]

    result = ths.sweep_adgroup_hourly_today(db, today=TODAY, now=NOW, fetch=fetch)

    rows = db.query(NaverAdgroupHourlyToday).order_by(NaverAdgroupHourlyToday.hour).all()
    assert [r.hour for r in rows] == [10, 11]
    assert all(r.adgroup_id == "grp-1" and r.campaign_id == "cmp-1" for r in rows)
    assert [float(r.avg_rank) for r in rows] == [3.4, 4.5]
    assert result["rows"] == 2 and result["calls"] == 1 and result["failed"] == 0


def test_sweep_never_writes_naver_keyword_hourly(db):
    """★codex P1 회귀: 당일 진행분이 naver_keyword_hourly에 들어가면 그 테이블의
    완결 불변식('행이 있다 = 그 날이 통째로 스윕됐다')이 깨져 auto_operator의
    롤링 24h 흐름이 과소계상된다. 이 테이블은 절대 건드리지 않는다."""
    _daily(db, adgroup_id="grp-1")

    ths.sweep_adgroup_hourly_today(db, today=TODAY, now=NOW, fetch=_fetch_ok)

    assert db.query(NaverKeywordHourly).count() == 0
    assert db.query(NaverAdgroupHourlyToday).count() == 1


def test_sweep_is_idempotent_replace(db):
    """매시 재실행이 전제 — 같은 시각 두 번 돌아도 행이 불어나지 않아야 한다."""
    _daily(db, adgroup_id="grp-1")

    def fetch(eid, d):
        return [_hh(10), _hh(11)]

    ths.sweep_adgroup_hourly_today(db, today=TODAY, now=NOW, fetch=fetch)
    ths.sweep_adgroup_hourly_today(db, today=TODAY, now=NOW, fetch=fetch)
    assert db.query(NaverAdgroupHourlyToday).count() == 2


def test_sweep_later_run_fills_naver_reporting_lag(db):
    """★2026-07-29 12:24 실측: 12시가 넘었는데 11시 버킷이 아직 없었다. 지연분은 다음
    실행의 교체 upsert가 보완해야 한다."""
    _daily(db, adgroup_id="grp-1")
    ths.sweep_adgroup_hourly_today(db, today=TODAY, now=NOW, fetch=_fetch_ok)
    assert [r.hour for r in db.query(NaverAdgroupHourlyToday).all()] == [10]

    ths.sweep_adgroup_hourly_today(
        db, today=TODAY, now=datetime(2026, 7, 29, 13, 12),
        fetch=lambda eid, d: [_hh(10), _hh(11), _hh(12)],
    )
    assert sorted(r.hour for r in db.query(NaverAdgroupHourlyToday).all()) == [10, 11, 12]


def test_sweep_empty_fetch_preserves_existing_rows(db):
    """당일엔 imp>0 보증이 없다 — 빈 응답으로 기존 완결 행을 지우면 안 된다."""
    _daily(db, adgroup_id="grp-1")
    ths.sweep_adgroup_hourly_today(db, today=TODAY, now=NOW, fetch=_fetch_ok)

    result = ths.sweep_adgroup_hourly_today(db, today=TODAY, now=NOW, fetch=lambda eid, d: [])

    assert db.query(NaverAdgroupHourlyToday).count() == 1
    assert result["skipped"] == 1 and result["failed"] == 0


def test_sweep_only_incomplete_bucket_preserves_existing_rows(db):
    _daily(db, adgroup_id="grp-1")
    ths.sweep_adgroup_hourly_today(db, today=TODAY, now=NOW, fetch=_fetch_ok)

    result = ths.sweep_adgroup_hourly_today(db, today=TODAY, now=NOW, fetch=lambda eid, d: [_hh(12)])

    assert [r.hour for r in db.query(NaverAdgroupHourlyToday).all()] == [10]
    assert result["skipped"] == 1


def test_sweep_unit_failure_does_not_kill_run(db):
    _daily(db, adgroup_id="grp-bad")
    _daily(db, adgroup_id="grp-ok")

    def fetch(eid, d):
        if eid == "grp-bad":
            raise RuntimeError("HTTP 500")
        return [_hh(10)]

    result = ths.sweep_adgroup_hourly_today(db, today=TODAY, now=NOW, fetch=fetch)
    assert result["failed"] == 1 and result["rows"] == 1
    assert [r.adgroup_id for r in db.query(NaverAdgroupHourlyToday).all()] == ["grp-ok"]


def test_sweep_rotates_instead_of_truncating(db, monkeypatch):
    """상한을 넘으면 자르는 게 아니라 회전한다 — 시각이 다르면 다른 부분집합을 훑는다."""
    monkeypatch.setattr(ths, "_CALL_CAP", 2)
    for i in range(5):
        _daily(db, adgroup_id=f"grp-{i}")

    r0 = ths.sweep_adgroup_hourly_today(
        db, today=TODAY, now=datetime(2026, 7, 29, 12, 35), fetch=_fetch_ok)
    swept_h0 = {r.adgroup_id for r in db.query(NaverAdgroupHourlyToday).all()}

    ths.sweep_adgroup_hourly_today(
        db, today=TODAY, now=datetime(2026, 7, 29, 13, 35), fetch=_fetch_ok)
    swept_h1 = {r.adgroup_id for r in db.query(NaverAdgroupHourlyToday).all()}

    assert r0["targets"] == 5 and r0["swept"] == 2 and r0["calls"] == 2
    assert swept_h1 - swept_h0, "다음 시각 실행이 새 그룹을 훑어야 한다(기아 방지)"


def test_sweep_killswitch_makes_zero_calls(db, monkeypatch):
    _daily(db, adgroup_id="grp-1")
    monkeypatch.setattr(ths, "TODAY_HOURLY_SWEEP_ENABLED", False)

    def fetch(eid, d):
        raise AssertionError("킬스위치가 내려갔는데 API를 불렀다")

    result = ths.sweep_adgroup_hourly_today(db, today=TODAY, now=NOW, fetch=fetch)
    assert result["enabled"] is False and result["calls"] == 0
    assert db.query(NaverAdgroupHourlyToday).count() == 0


def test_sweep_rolls_off_rows_older_than_retention(db):
    _daily(db, adgroup_id="grp-1")
    db.add(NaverAdgroupHourlyToday(
        ad_date=TODAY - timedelta(days=ths._RETAIN_DAYS + 1), hour=3,
        adgroup_id="grp-old", campaign_id="cmp-1", campaign_type="SHOPPING", imp=1,
    ))
    db.commit()

    ths.sweep_adgroup_hourly_today(db, today=TODAY, now=NOW, fetch=_fetch_ok)

    assert [r.adgroup_id for r in db.query(NaverAdgroupHourlyToday).all()] == ["grp-1"]


def test_sweep_keeps_recent_prior_days(db):
    _daily(db, adgroup_id="grp-1")
    db.add(NaverAdgroupHourlyToday(
        ad_date=TODAY - timedelta(days=2), hour=3,
        adgroup_id="grp-recent", campaign_id="cmp-1", campaign_type="SHOPPING", imp=1,
    ))
    db.commit()

    ths.sweep_adgroup_hourly_today(db, today=TODAY, now=NOW, fetch=_fetch_ok)

    assert {r.adgroup_id for r in db.query(NaverAdgroupHourlyToday).all()} == {"grp-1", "grp-recent"}


# ══════════════════════════════════════════════════════════════════
# 스케줄러 등록
# ══════════════════════════════════════════════════════════════════
def test_scheduler_job_exists_with_self_contained_session():
    import inspect

    from app.services import scheduler_service

    assert hasattr(scheduler_service, "sweep_naver_today_hourly_job")
    src = inspect.getsource(scheduler_service.sweep_naver_today_hourly_job)
    assert "_get_own_db_session" in src
    assert "db.close()" in src


def test_scheduler_cron_does_not_share_a_minute_with_any_other_job():
    """★codex 2R: 매시 최대 800콜 + SQLite 쓰기라 다른 잡과 분을 공유하면 안 된다.
    09:10 D-1 스윕(3,500콜 ~12분)·07:35 sync_naver_entity가 특히 위험하다.
    이 테스트는 등록된 크론 전체에서 분 슬롯 충돌을 직접 검사한다(주석 신뢰 금지 —
    :12·:35 두 번 다 '비어 있다'고 적어놓고 틀렸다)."""
    import inspect
    import re

    from app.services import scheduler_service

    src = inspect.getsource(scheduler_service._ensure_default_states)
    specs = re.findall(r'\("([a-z_0-9]+)", "(\S+) (\S+) (\S+) (\S+) (\S+)"\)', src)
    ours = [s for s in specs if s[0] == "sweep_naver_today_hourly"]
    assert len(ours) == 1, "잡이 정확히 한 번 등록돼야 한다"
    our_minute = ours[0][1]
    assert ours[0][2] == "*", "매시 실행이어야 한다"

    clashes = [
        s[0] for s in specs
        if s[0] != "sweep_naver_today_hourly" and s[1] == our_minute
    ]
    assert not clashes, f"분 슬롯 {our_minute} 충돌: {clashes}"

    # 09:10 스윕의 실행 창(~12분 + 지연 여유) 밖이어야 한다.
    assert not (10 <= int(our_minute) <= 30), f"분 {our_minute}이 09:10 스윕 창 안"


def test_scheduler_excluded_from_catchup():
    """매시 멱등 교체라 밀린 시각을 소급 실행할 이유가 없다(시간당 레인들과 동일 방침)."""
    from app.services import scheduler_service

    assert "sweep_naver_today_hourly" not in scheduler_service._CATCHUP_ORDER
