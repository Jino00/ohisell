# test_naver_keyword_volume_baseline.py — D-NAO-186 ① 검색량 기준선 시계열
"""★이 테스트가 지켜야 하는 것 넷:
  ① **대상이 「돈이 닿은 키워드」다** — 기존 `keyword_volume_sync`가 «저클릭»만 봐서 비용이
     나가는 키워드를 구조적으로 배제하던 것이 이 슬라이스의 발단이다. 대상 선정이 뒤집히면
     테이블은 차는데 정작 필요한 행이 없다.
  ② **시계열이다** — 날짜가 다르면 다른 행. 덮어쓰기로 퇴화하면 기준선이 아니다.
  ③ **같은 날 재실행은 멱등** — 크론이 두 번 돌아도 행이 늘지 않는다.
  ④ **`__backfill__` 센티널 배제** — 공용 필터가 없어 집계마다 다시 적어야 하고, 잊으면
     에러 없이 조용히 틀린다.
★세션 픽스처는 prod와 같은 `autoflush=False`(app/database.py:16) — 교훈 #292.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverEntity, NaverKeywordVolumeDaily
from app.services.naver_ad import keyword_volume_baseline as kvb
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

TODAY = date(2026, 8, 18)


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Testing = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Testing()
    try:
        yield db
    finally:
        db.close()


def _kw(db, entity_id: str, name: str):
    db.add(NaverEntity(entity_id=entity_id, entity_type="keyword", name=name, status="on"))
    db.commit()


def _daily(db, *, keyword_id: str, on: date, cost: int = 0, clk: int = 0,
           adgroup_id: str = "grp-1"):
    db.add(NaverAdDaily(
        ad_date=on, campaign_id="cmp-1", adgroup_id=adgroup_id,
        keyword_id=keyword_id, imp=100, clk=clk, cost=cost,
    ))
    db.commit()


# ── 대상 선정 ────────────────────────────────────────────────────────────────────


def test_head_keywords_are_the_ones_money_reached(session):
    """★핵심 — 기존 잡이 배제하던 「비용이 나가는 키워드」가 여기서는 대상이다."""
    _kw(session, "nkw-spend", "아이폰16프로필름")
    _kw(session, "nkw-clicks", "갤럭시폴드7필름")
    _kw(session, "nkw-idle", "안팔리는키워드")
    _daily(session, keyword_id="nkw-spend", on=TODAY - timedelta(days=2), cost=50000, clk=0)
    _daily(session, keyword_id="nkw-clicks", on=TODAY - timedelta(days=3), cost=0, clk=7)
    _daily(session, keyword_id="nkw-idle", on=TODAY - timedelta(days=2), cost=0, clk=0)

    got = kvb.head_keywords(session, today=TODAY)
    assert got == ["갤럭시폴드7필름", "아이폰16프로필름"]
    assert "안팔리는키워드" not in got, "비용도 클릭도 0이면 대상이 아니다"


def test_backfill_sentinel_rows_are_excluded(session):
    """센티널을 안 빼면 에러 없이 조용히 틀린다(2026-08-18 하루 2회 발생)."""
    _kw(session, BACKFILL_SENTINEL_ADGROUP, "센티널")
    _kw(session, "nkw-real", "진짜키워드")
    _daily(session, keyword_id=BACKFILL_SENTINEL_ADGROUP, on=TODAY, cost=999999, clk=99)
    _daily(session, keyword_id="nkw-real", on=TODAY, cost=1000, clk=1)
    _daily(session, keyword_id="nkw-real", on=TODAY, cost=1000, clk=1,
           adgroup_id=BACKFILL_SENTINEL_ADGROUP)

    assert kvb.head_keywords(session, today=TODAY) == ["진짜키워드"]


def test_outside_the_lookback_window_is_not_a_head_keyword(session):
    _kw(session, "nkw-old", "옛날키워드")
    _daily(session, keyword_id="nkw-old", on=TODAY - timedelta(days=31), cost=90000, clk=40)
    assert kvb.head_keywords(session, today=TODAY) == []


def test_keyword_ids_without_a_name_are_dropped_not_guessed(session):
    """이름을 못 찾은 id는 추측하지 않는다 — 키워드 텍스트가 없으면 조회 자체가 불가능하다."""
    _daily(session, keyword_id="nkw-ghost", on=TODAY, cost=5000, clk=3)
    assert kvb.head_keywords(session, today=TODAY) == []


# ── 시계열 적재 ──────────────────────────────────────────────────────────────────


def _stub(monkeypatch, payload: dict):
    monkeypatch.setattr(kvb, "fetch_keyword_volumes_detailed", lambda kws: payload)


def test_sync_writes_one_row_per_day_and_splits_pc_mobile(session, monkeypatch):
    _kw(session, "nkw-a", "아이폰16프로필름")
    _daily(session, keyword_id="nkw-a", on=TODAY, cost=1000, clk=2)
    _stub(monkeypatch, {"아이폰16프로필름": {
        "pc": 300, "mobile": 4700, "total": 5000,
        "competition": "높음", "below_threshold": False}})

    r = kvb.sync_baseline(session, today=TODAY)
    assert r == {"targeted": 1, "fetched": 1, "inserted": 1, "updated": 0, "unmatched": 0}

    row = session.query(NaverKeywordVolumeDaily).one()
    assert (row.pc_volume, row.mobile_volume, row.total_volume) == (300, 4700, 5000)
    assert row.competition == "높음" and row.is_below_threshold is False


def test_a_second_day_makes_a_second_row_not_an_overwrite(session, monkeypatch):
    """★기준선의 정의 — 덮어쓰기로 퇴화하면 이 적재를 한 이유가 사라진다."""
    _kw(session, "nkw-a", "아이폰16프로필름")
    _daily(session, keyword_id="nkw-a", on=TODAY, cost=1000, clk=2)

    _stub(monkeypatch, {"아이폰16프로필름": {
        "pc": 100, "mobile": 900, "total": 1000, "competition": "중간", "below_threshold": False}})
    kvb.sync_baseline(session, today=TODAY)
    _stub(monkeypatch, {"아이폰16프로필름": {
        "pc": 500, "mobile": 9500, "total": 10000, "competition": "높음", "below_threshold": False}})
    kvb.sync_baseline(session, today=TODAY + timedelta(days=1))

    rows = session.query(NaverKeywordVolumeDaily).order_by(
        NaverKeywordVolumeDaily.measured_date).all()
    assert [r.total_volume for r in rows] == [1000, 10000], "두 날의 값이 둘 다 남아야 한다"


def test_same_day_rerun_is_idempotent(session, monkeypatch):
    _kw(session, "nkw-a", "아이폰16프로필름")
    _daily(session, keyword_id="nkw-a", on=TODAY, cost=1000, clk=2)
    _stub(monkeypatch, {"아이폰16프로필름": {
        "pc": 1, "mobile": 2, "total": 3, "competition": None, "below_threshold": False}})
    kvb.sync_baseline(session, today=TODAY)

    _stub(monkeypatch, {"아이폰16프로필름": {
        "pc": 10, "mobile": 20, "total": 30, "competition": "낮음", "below_threshold": False}})
    r = kvb.sync_baseline(session, today=TODAY)

    assert r["inserted"] == 0 and r["updated"] == 1
    row = session.query(NaverKeywordVolumeDaily).one()
    assert row.total_volume == 30, "같은 날 재실행은 갱신(멱등)"


def test_below_threshold_is_recorded_not_flattened_into_a_measurement(session, monkeypatch):
    """「측정값 5」와 「10 미만이라는 것만 안다」를 섞으면 추세가 거짓말을 한다."""
    _kw(session, "nkw-tiny", "아주작은키워드")
    _daily(session, keyword_id="nkw-tiny", on=TODAY, cost=100, clk=1)
    _stub(monkeypatch, {"아주작은키워드": {
        "pc": 5, "mobile": 5, "total": 10, "competition": "낮음", "below_threshold": True}})

    kvb.sync_baseline(session, today=TODAY)
    assert session.query(NaverKeywordVolumeDaily).one().is_below_threshold is True


def test_unmatched_keywords_are_counted_not_silently_dropped(session, monkeypatch):
    """「검색량이 없다」와 「우리가 못 받고 있다」는 다른 문제다 — 세지 않으면 구분이 안 된다."""
    _kw(session, "nkw-a", "받아지는키워드")
    _kw(session, "nkw-b", "안받아지는키워드")
    _daily(session, keyword_id="nkw-a", on=TODAY, cost=1000, clk=2)
    _daily(session, keyword_id="nkw-b", on=TODAY, cost=1000, clk=2)
    _stub(monkeypatch, {"받아지는키워드": {
        "pc": 1, "mobile": 1, "total": 2, "competition": None, "below_threshold": False}})

    r = kvb.sync_baseline(session, today=TODAY)
    assert r["targeted"] == 2 and r["fetched"] == 1 and r["unmatched"] == 1


def test_no_targets_makes_no_api_call(session, monkeypatch):
    def _boom(_kws):
        raise AssertionError("대상이 없으면 API를 부르면 안 된다")
    monkeypatch.setattr(kvb, "fetch_keyword_volumes_detailed", _boom)
    assert kvb.sync_baseline(session, today=TODAY)["targeted"] == 0
