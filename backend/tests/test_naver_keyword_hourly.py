# test_naver_keyword_hourly.py — D-NAO-46② 키워드 시간별 축적 단위 테스트
# 커버: fetcher.fetch_entity_hh24(hh24 파싱)·fetch_campaign_stats avg_rank 회귀,
#   keyword_hourly_sweep.build_sweep_targets(순수)·sweep_keyword_hourly(교체 upsert·
#   캐치업·콜 상한·365d 롤링).
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverKeywordHourly
from app.services import naver_sa_ad_fetcher as fetcher
from app.services.naver_ad import keyword_hourly_sweep
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP


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


# ══════════════════════════════════════════════════════════════════
# fetch_entity_hh24 (naver_sa_ad_fetcher.py)
# ══════════════════════════════════════════════════════════════════
class _FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def _set_creds(monkeypatch):
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "x")


def test_fetch_entity_hh24_parses_both_label_formats(monkeypatch):
    _set_creds(monkeypatch)
    payload = {"data": [{
        "id": "nkw-1", "impCnt": 30,
        "breakdowns": [
            {"name": "00시~01시", "avgRnk": 1.8, "impCnt": 22, "clkCnt": 1, "salesAmt": 100},
            {"name": "1시~2시", "avgRnk": 2.3, "impCnt": 8, "clkCnt": 0, "salesAmt": 0},
        ],
    }]}
    monkeypatch.setattr(fetcher, "_get", lambda path, params=None: _FakeResp(payload=payload))

    out = fetcher.fetch_entity_hh24("nkw-1", date(2026, 7, 15))
    assert out == [
        {"hour": 0, "imp": 22, "clk": 1, "cost": 100, "avg_rank": 1.8},
        {"hour": 1, "imp": 8, "clk": 0, "cost": 0, "avg_rank": 2.3},
    ]


def test_fetch_entity_hh24_avg_rank_zero_becomes_none(monkeypatch):
    _set_creds(monkeypatch)
    payload = {"data": [{
        "id": "nkw-1", "impCnt": 5,
        "breakdowns": [{"name": "03시~04시", "avgRnk": 0, "impCnt": 5, "clkCnt": 0, "salesAmt": 0}],
    }]}
    monkeypatch.setattr(fetcher, "_get", lambda path, params=None: _FakeResp(payload=payload))

    out = fetcher.fetch_entity_hh24("nkw-1", date(2026, 7, 15))
    assert out[0]["avg_rank"] is None


def test_fetch_entity_hh24_empty_data_returns_empty_list(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setattr(fetcher, "_get", lambda path, params=None: _FakeResp(payload={"data": []}))

    out = fetcher.fetch_entity_hh24("nkw-1", date(2026, 7, 15))
    assert out == []


def test_fetch_entity_hh24_raises_when_imp_positive_but_breakdowns_missing(monkeypatch):
    """breakdown 파라미터는 조건이 안 맞으면 무언 무시(200+집계 1행) — imp>0인데
    breakdowns가 없으면 무언 데이터손실 방어를 위해 예외를 던져야 한다(ref 32 §4)."""
    _set_creds(monkeypatch)
    payload = {"data": [{"id": "nkw-1", "impCnt": 40}]}  # breakdowns 키 자체 없음
    monkeypatch.setattr(fetcher, "_get", lambda path, params=None: _FakeResp(payload=payload))

    with pytest.raises(RuntimeError):
        fetcher.fetch_entity_hh24("nkw-1", date(2026, 7, 15))


def test_fetch_entity_hh24_raises_when_imp_positive_but_breakdowns_empty(monkeypatch):
    _set_creds(monkeypatch)
    payload = {"data": [{"id": "nkw-1", "impCnt": 40, "breakdowns": []}]}
    monkeypatch.setattr(fetcher, "_get", lambda path, params=None: _FakeResp(payload=payload))

    with pytest.raises(RuntimeError):
        fetcher.fetch_entity_hh24("nkw-1", date(2026, 7, 15))


def test_fetch_entity_hh24_skips_unparseable_label(monkeypatch):
    _set_creds(monkeypatch)
    payload = {"data": [{
        "id": "nkw-1", "impCnt": 10,
        "breakdowns": [
            {"name": "정렬안됨", "avgRnk": 1.0, "impCnt": 2, "clkCnt": 0, "salesAmt": 0},
            {"name": "05시~06시", "avgRnk": 1.5, "impCnt": 8, "clkCnt": 0, "salesAmt": 0},
        ],
    }]}
    monkeypatch.setattr(fetcher, "_get", lambda path, params=None: _FakeResp(payload=payload))

    out = fetcher.fetch_entity_hh24("nkw-1", date(2026, 7, 15))
    assert len(out) == 1
    assert out[0]["hour"] == 5


def test_fetch_entity_hh24_no_credentials_returns_empty(monkeypatch):
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "")
    out = fetcher.fetch_entity_hh24("nkw-1", date(2026, 7, 15))
    assert out == []


def test_fetch_entity_hh24_non_200_raises(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setattr(fetcher, "_get", lambda path, params=None: _FakeResp(status_code=400, text="bad"))
    with pytest.raises(RuntimeError):
        fetcher.fetch_entity_hh24("nkw-1", date(2026, 7, 15))


# ══════════════════════════════════════════════════════════════════
# fetch_campaign_stats avg_rank 키 회귀(기존 키 불변)
# ══════════════════════════════════════════════════════════════════
def test_fetch_campaign_stats_adds_avg_rank_without_breaking_existing_keys(monkeypatch):
    _set_creds(monkeypatch)
    payload = {"data": [{"impCnt": 100, "clkCnt": 5, "salesAmt": 1000, "ccnt": 1, "convAmt": 20000, "avgRnk": 2.4}]}
    monkeypatch.setattr(fetcher, "_get", lambda path, params=None: _FakeResp(payload=payload))

    out = fetcher.fetch_campaign_stats(["cmp-1"], date_preset="today")
    assert len(out) == 1
    r = out[0]
    assert r["campaign_id"] == "cmp-1"
    assert r["imp"] == 100 and r["clk"] == 5 and r["cost"] == 1000
    assert r["conv_cnt"] == 1 and r["conv_amt"] == 20000
    assert r["avg_rank"] == 2.4


def test_fetch_campaign_stats_avg_rank_zero_or_missing_is_none(monkeypatch):
    _set_creds(monkeypatch)
    payload = {"data": [{"impCnt": 0, "clkCnt": 0, "salesAmt": 0, "ccnt": 0, "convAmt": 0, "avgRnk": 0}]}
    monkeypatch.setattr(fetcher, "_get", lambda path, params=None: _FakeResp(payload=payload))
    out = fetcher.fetch_campaign_stats(["cmp-1"], date_preset="today")
    assert out[0]["avg_rank"] is None

    payload2 = {"data": [{"impCnt": 10, "clkCnt": 1, "salesAmt": 100, "ccnt": 0, "convAmt": 0}]}  # avgRnk 필드 부재
    monkeypatch.setattr(fetcher, "_get", lambda path, params=None: _FakeResp(payload=payload2))
    out2 = fetcher.fetch_campaign_stats(["cmp-1"], date_preset="today")
    assert out2[0]["avg_rank"] is None


# ══════════════════════════════════════════════════════════════════
# build_sweep_targets (순수)
# ══════════════════════════════════════════════════════════════════
D = date(2026, 7, 16)


def _ad_row(db, *, campaign_id, campaign_type, adgroup_id, keyword_id, imp, clk=1, cost=100, ad_date=D):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type=campaign_type,
        adgroup_id=adgroup_id, keyword_id=keyword_id, imp=imp, clk=clk, cost=cost,
    ))


def test_build_sweep_targets_web_site_uses_keyword_grain(db):
    _ad_row(db, campaign_id="cmp-w", campaign_type="WEB_SITE", adgroup_id="grp-1",
            keyword_id="nkw-1", imp=10)
    db.commit()

    targets = keyword_hourly_sweep.build_sweep_targets(db, D)
    assert len(targets) == 1
    t = targets[0]
    assert t["entity_type"] == "keyword"
    assert t["entity_id"] == "nkw-1"
    assert t["adgroup_id"] == "grp-1"
    assert t["campaign_id"] == "cmp-w"


def test_build_sweep_targets_shopping_uses_adgroup_grain(db):
    _ad_row(db, campaign_id="cmp-s", campaign_type="SHOPPING", adgroup_id="grp-shop",
            keyword_id="", imp=20)
    db.commit()

    targets = keyword_hourly_sweep.build_sweep_targets(db, D)
    assert len(targets) == 1
    t = targets[0]
    assert t["entity_type"] == "adgroup"
    assert t["entity_id"] == "grp-shop"
    assert t["campaign_id"] == "cmp-s"


def test_build_sweep_targets_excludes_backfill_sentinel(db):
    _ad_row(db, campaign_id="cmp-w", campaign_type="WEB_SITE",
            adgroup_id=BACKFILL_SENTINEL_ADGROUP, keyword_id="", imp=999)
    db.commit()

    targets = keyword_hourly_sweep.build_sweep_targets(db, D)
    assert targets == []


def test_build_sweep_targets_excludes_zero_imp(db):
    _ad_row(db, campaign_id="cmp-w", campaign_type="WEB_SITE", adgroup_id="grp-1",
            keyword_id="nkw-zero", imp=0)
    db.commit()

    targets = keyword_hourly_sweep.build_sweep_targets(db, D)
    assert targets == []


def test_build_sweep_targets_blank_campaign_type_with_keyword_id_is_keyword_grain(db):
    """codex 2R[P2-2]: 일별 수집기가 타입 조회 실패 시 campaign_type=''로 적재할 수 있고
    구버전 행도 공백 가능 — keyword_id가 실재(nkw-…)하면 campaign_type과 무관하게 keyword
    grain이어야 한다(아니면 adgroup grain으로 오분류되어 키워드 곡선을 영영 안 모음)."""
    _ad_row(db, campaign_id="cmp-x", campaign_type="", adgroup_id="grp-1",
            keyword_id="nkw-legacy", imp=10)
    db.commit()

    targets = keyword_hourly_sweep.build_sweep_targets(db, D)
    assert len(targets) == 1
    t = targets[0]
    assert t["entity_type"] == "keyword"
    assert t["entity_id"] == "nkw-legacy"
    assert t["adgroup_id"] == "grp-1"


def test_build_sweep_targets_blank_campaign_type_without_keyword_id_is_adgroup_grain(db):
    """공백 campaign_type + keyword_id='' sentinel → adgroup grain (P2-2 대칭 케이스)."""
    _ad_row(db, campaign_id="cmp-x", campaign_type="", adgroup_id="grp-legacy",
            keyword_id="", imp=10)
    db.commit()

    targets = keyword_hourly_sweep.build_sweep_targets(db, D)
    assert len(targets) == 1
    assert targets[0]["entity_type"] == "adgroup"
    assert targets[0]["entity_id"] == "grp-legacy"


def test_build_sweep_targets_brand_search_uses_adgroup_grain(db):
    _ad_row(db, campaign_id="cmp-b", campaign_type="BRAND_SEARCH", adgroup_id="grp-brand",
            keyword_id="", imp=5)
    db.commit()

    targets = keyword_hourly_sweep.build_sweep_targets(db, D)
    assert len(targets) == 1
    assert targets[0]["entity_type"] == "adgroup"
    assert targets[0]["entity_id"] == "grp-brand"


# ══════════════════════════════════════════════════════════════════
# sweep_keyword_hourly (쓰기 Harness)
# ══════════════════════════════════════════════════════════════════
def _fake_fetch_factory(by_entity: dict[str, list[dict]], calls: list):
    def _fetch(entity_id, stat_date):
        calls.append((entity_id, stat_date))
        return by_entity.get(entity_id, [])
    return _fetch


def test_sweep_writes_rows_for_target_and_is_idempotent(db):
    _ad_row(db, campaign_id="cmp-w", campaign_type="WEB_SITE", adgroup_id="grp-1",
            keyword_id="nkw-1", imp=10)
    db.commit()

    calls: list = []
    fetch = _fake_fetch_factory(
        {"nkw-1": [{"hour": 9, "imp": 10, "clk": 1, "cost": 100, "avg_rank": 2.1}]}, calls,
    )

    r1 = keyword_hourly_sweep.sweep_keyword_hourly(db, sweep_date=D, fetch=fetch)
    assert r1["targets"] == 1 and r1["rows"] == 1 and r1["failed"] == 0
    rows1 = db.query(NaverKeywordHourly).filter(NaverKeywordHourly.ad_date == D).all()
    assert len(rows1) == 1
    assert float(rows1[0].avg_rank) == pytest.approx(2.1)

    # 재실행(같은 날) — 행 수 불변(교체 멱등)
    r2 = keyword_hourly_sweep.sweep_keyword_hourly(db, sweep_date=D, fetch=fetch)
    assert r2["rows"] == 1
    rows2 = db.query(NaverKeywordHourly).filter(NaverKeywordHourly.ad_date == D).all()
    assert len(rows2) == 1


def test_sweep_empty_fetch_preserves_existing_rows_and_counts_failed(db):
    """codex 2R[P2-1]: 타깃은 전부 ad_daily imp>0이므로 빈 fetch(무자격증명·일시 API 글리치)
    = 이상 신호. _replace_rows를 호출해 기존 수집분을 delete 후 insert 0으로 지우면 안 된다 —
    failed 카운트+skip으로 기존 행 보존(다음날 캐치업이 자연 재시도)."""
    _ad_row(db, campaign_id="cmp-w", campaign_type="WEB_SITE", adgroup_id="grp-1",
            keyword_id="nkw-1", imp=10)
    # 이전 스윕이 이미 수집해 둔 행
    db.add(NaverKeywordHourly(ad_date=D, hour=9, entity_type="keyword",
                               entity_id="nkw-1", adgroup_id="grp-1", campaign_id="cmp-w",
                               campaign_type="WEB_SITE", imp=10, clk=1, cost=100))
    db.commit()

    fetch = _fake_fetch_factory({}, [])  # 모든 fetch가 빈 리스트 반환(글리치 시뮬레이션)
    result = keyword_hourly_sweep.sweep_keyword_hourly(db, sweep_date=D, fetch=fetch)

    assert result["failed"] == 1, "빈 fetch는 failed로 카운트해야 한다"
    rows = db.query(NaverKeywordHourly).filter(
        NaverKeywordHourly.ad_date == D, NaverKeywordHourly.entity_id == "nkw-1"
    ).all()
    assert len(rows) == 1, "빈 fetch가 기존 수집분을 지우면 안 된다"
    assert rows[0].hour == 9 and rows[0].imp == 10


def test_sweep_unit_failure_skipped_and_counted(db):
    _ad_row(db, campaign_id="cmp-w", campaign_type="WEB_SITE", adgroup_id="grp-1",
            keyword_id="nkw-ok", imp=10)
    _ad_row(db, campaign_id="cmp-w", campaign_type="WEB_SITE", adgroup_id="grp-1",
            keyword_id="nkw-fail", imp=5)
    db.commit()

    def fetch(entity_id, stat_date):
        if entity_id == "nkw-fail":
            raise RuntimeError("boom")
        return [{"hour": 1, "imp": 10, "clk": 0, "cost": 0, "avg_rank": None}]

    result = keyword_hourly_sweep.sweep_keyword_hourly(db, sweep_date=D, fetch=fetch)
    assert result["targets"] == 2
    assert result["failed"] == 1
    rows = db.query(NaverKeywordHourly).filter(NaverKeywordHourly.ad_date == D).all()
    assert len(rows) == 1
    assert rows[0].entity_id == "nkw-ok"


def test_sweep_catchup_only_resweeps_missing_units(db, monkeypatch):
    """캐치업은 유닛 단위 완전성 — 이미 keyword_hourly 행이 있는 유닛은 재수집하지 않는다."""
    today = D
    monkeypatch.setattr(keyword_hourly_sweep, "kst_today", lambda: today)

    d_minus_4 = today - timedelta(days=4)  # [D-6, D-2] 범위 안
    _ad_row(db, campaign_id="cmp-w", campaign_type="WEB_SITE", adgroup_id="grp-1",
            keyword_id="nkw-missing", imp=10, ad_date=d_minus_4)
    # 이미 존재하는 유닛(캐치업 대상 아님)
    db.add(NaverAdDaily(ad_date=d_minus_4, campaign_id="cmp-w", campaign_type="WEB_SITE",
                         adgroup_id="grp-1", keyword_id="nkw-present", imp=8, clk=1, cost=10))
    db.add(NaverKeywordHourly(ad_date=d_minus_4, hour=3, entity_type="keyword",
                               entity_id="nkw-present", adgroup_id="grp-1", campaign_id="cmp-w",
                               campaign_type="WEB_SITE", imp=8, clk=1, cost=10))
    # sweep_date(D-1) 당일 타깃 없음(빈 스윕이라도 캐치업은 동작해야 함)
    db.commit()

    calls: list = []
    fetch = _fake_fetch_factory(
        {"nkw-missing": [{"hour": 4, "imp": 10, "clk": 1, "cost": 50, "avg_rank": 3.0}]}, calls,
    )

    result = keyword_hourly_sweep.sweep_keyword_hourly(db, sweep_date=today - timedelta(days=1), fetch=fetch)
    assert result["catchup_calls"] == 1
    assert ("nkw-missing", d_minus_4) in calls
    assert not any(c[0] == "nkw-present" for c in calls)

    rows = db.query(NaverKeywordHourly).filter(
        NaverKeywordHourly.ad_date == d_minus_4, NaverKeywordHourly.entity_id == "nkw-missing"
    ).all()
    assert len(rows) == 1


def test_sweep_date_inside_catchup_window_not_double_fetched(db, monkeypatch):
    """codex 3R[P2]: sweep_date를 캐치업 창([D-6,D-2]) 안 날짜로 명시하면(수동 보수 등)
    본스윕의 pending insert(autoflush=False, 커밋 전)를 캐치업의 _existing_entity_ids가
    못 봐 같은 유닛을 재fetch+재add → commit에서 (ad_date,entity_id,hour) unique 충돌.
    캐치업은 sweep_date와 같은 날짜를 제외해야 한다 — 이중 fetch 없이 commit 완주."""
    today = D
    monkeypatch.setattr(keyword_hourly_sweep, "kst_today", lambda: today)

    manual_date = today - timedelta(days=4)  # 캐치업 창 안
    _ad_row(db, campaign_id="cmp-w", campaign_type="WEB_SITE", adgroup_id="grp-1",
            keyword_id="nkw-1", imp=10, ad_date=manual_date)
    db.commit()

    calls: list = []
    fetch = _fake_fetch_factory(
        {"nkw-1": [{"hour": 7, "imp": 10, "clk": 1, "cost": 100, "avg_rank": 2.0}]}, calls,
    )

    result = keyword_hourly_sweep.sweep_keyword_hourly(db, sweep_date=manual_date, fetch=fetch)

    same_unit_calls = [c for c in calls if c == ("nkw-1", manual_date)]
    assert len(same_unit_calls) == 1, "본스윕이 처리한 (유닛, 날짜)를 캐치업이 재fetch하면 안 된다"
    assert result["catchup_calls"] == 0
    rows = db.query(NaverKeywordHourly).filter(
        NaverKeywordHourly.ad_date == manual_date, NaverKeywordHourly.entity_id == "nkw-1"
    ).all()
    assert len(rows) == 1  # unique 충돌 없이 commit 완주 + 행 1개


def test_sweep_respects_call_cap(db, monkeypatch):
    monkeypatch.setattr(keyword_hourly_sweep, "_CALL_CAP", 1)
    _ad_row(db, campaign_id="cmp-w", campaign_type="WEB_SITE", adgroup_id="grp-1",
            keyword_id="nkw-1", imp=10)
    _ad_row(db, campaign_id="cmp-w", campaign_type="WEB_SITE", adgroup_id="grp-1",
            keyword_id="nkw-2", imp=10)
    db.commit()

    calls: list = []
    fetch = _fake_fetch_factory({}, calls)

    result = keyword_hourly_sweep.sweep_keyword_hourly(db, sweep_date=D, fetch=fetch)
    assert result["calls"] == 1
    assert len(calls) == 1


def test_sweep_rolls_off_rows_older_than_365_days(db, monkeypatch):
    monkeypatch.setattr(keyword_hourly_sweep, "kst_today", lambda: D)
    old_date = D - timedelta(days=400)
    db.add(NaverKeywordHourly(ad_date=old_date, hour=1, entity_type="keyword",
                               entity_id="nkw-old", adgroup_id="grp-1", campaign_id="cmp-w",
                               campaign_type="WEB_SITE", imp=1, clk=0, cost=0))
    db.commit()

    fetch = _fake_fetch_factory({}, [])
    keyword_hourly_sweep.sweep_keyword_hourly(db, sweep_date=D - timedelta(days=1), fetch=fetch)

    assert db.query(NaverKeywordHourly).filter(NaverKeywordHourly.entity_id == "nkw-old").count() == 0
