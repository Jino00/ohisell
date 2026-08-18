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
from app.services.naver_sa_ad_fetcher import _parse_qc, _parse_qc_flagged

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
    # 비용 내림차순(적대 리뷰 P1-1 수정) — 비용 50,000인 쪽이 앞, 클릭만 있는 쪽(비용 0)이 뒤.
    assert got == ["아이폰16프로필름", "갤럭시폴드7필름"]
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
    assert r == {"targeted": 1, "fetched": 1, "inserted": 1, "updated": 0,
                 "unmatched": 0, "truncated": 0}

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


# ── 상한·정렬 (적대 리뷰 P1-1: 역변이 R1·R2가 둘 다 통과하던 자리) ──────────────────


def test_truncation_keeps_the_expensive_keywords_not_the_alphabetical_ones(session, monkeypatch):
    """★초판은 `sorted(names)`(가나다순)이라 상한에 걸리면 무작위나 다름없이 잘렸다.

    실측 근거: prod 12개월을 한 달씩 밀어 재니 대상이 초판 상한 1,500을 넘는 창이 6개였고
    그중 하나가 **아이폰17 출시 창(2,006건)**이다. 이 슬라이스의 존재 이유가 출시철 기준선인데
    바로 그때 약 25%가 잘려 나갔고, 그 구멍은 소급 불가다.
    """
    # 이름이 가나다 역순이 되도록: 비싼 것이 뒤로 가게 만든다.
    for i, (name, cost) in enumerate([("ㄱ싼키워드", 100), ("ㄴ중간키워드", 5000),
                                      ("ㄷ비싼키워드", 900000)]):
        _kw(session, f"nkw-{i}", name)
        _daily(session, keyword_id=f"nkw-{i}", on=TODAY, cost=cost, clk=1)

    assert kvb.head_keywords(session, today=TODAY) == [
        "ㄷ비싼키워드", "ㄴ중간키워드", "ㄱ싼키워드"], "비용 내림차순이어야 한다"

    seen: list[list[str]] = []
    monkeypatch.setattr(kvb, "fetch_keyword_volumes_detailed",
                        lambda kws: seen.append(list(kws)) or {})
    r = kvb.sync_baseline(session, limit=1, today=TODAY)
    assert seen[0] == ["ㄷ비싼키워드"], "잘릴 땐 돈이 큰 쪽이 남아야 한다"
    assert r["truncated"] == 2, "잘린 수가 결과에 드러나야 한다"


def test_no_truncation_reports_zero(session, monkeypatch):
    _kw(session, "nkw-a", "키워드")
    _daily(session, keyword_id="nkw-a", on=TODAY, cost=1000, clk=1)
    monkeypatch.setattr(kvb, "fetch_keyword_volumes_detailed", lambda kws: {})
    assert kvb.sync_baseline(session, limit=10, today=TODAY)["truncated"] == 0


def test_default_limit_covers_the_observed_release_season_peak():
    """상한이 실수요 아래로 내려가면 출시철에 조용히 잘린다 — 숫자를 테스트로 못 박는다.

    prod 실측 최대 대상 2,243건(2025-10-22 창), 아이폰17 출시 창 2,006건.
    """
    assert kvb._DEFAULT_LIMIT >= 2243, "관측된 최대 대상보다 커야 한다"


def test_same_name_from_several_keyword_ids_sums_its_cost(session):
    """한 이름이 여러 그룹에 별개 엔티티로 존재한다 — 비용을 합쳐야 순위가 옳다."""
    _kw(session, "nkw-a1", "같은이름")
    _kw(session, "nkw-a2", "같은이름")
    _kw(session, "nkw-b", "다른이름")
    _daily(session, keyword_id="nkw-a1", on=TODAY, cost=300, clk=1)
    _daily(session, keyword_id="nkw-a2", on=TODAY, cost=300, clk=1)
    _daily(session, keyword_id="nkw-b", on=TODAY, cost=500, clk=1)

    assert kvb.head_keywords(session, today=TODAY) == ["같은이름", "다른이름"]


# ── P2-1 채택: 응답 키가 요청 키와 달라도 재실행이 죽지 않는다 ────────────────────


def test_rerun_survives_a_response_key_that_differs_from_the_request(session, monkeypatch):
    """`existing`을 요청 키로 좁히면, 네이버가 다른 표기로 답한 행이 매번 INSERT로 가서
    같은 날 재실행이 UNIQUE 위반으로 **그 실행분 전체를 커밋 전에 날린다**."""
    _kw(session, "nkw-a", "아이폰16프로필름")
    _daily(session, keyword_id="nkw-a", on=TODAY, cost=1000, clk=2)
    _stub(monkeypatch, {"아이폰 16 프로 필름": {  # ← 요청과 다른 표기로 응답
        "pc": 1, "mobile": 2, "total": 3, "competition": None, "below_threshold": False}})

    assert kvb.sync_baseline(session, today=TODAY)["inserted"] == 1
    r2 = kvb.sync_baseline(session, today=TODAY)          # 죽으면 안 된다
    assert r2["inserted"] == 0 and r2["updated"] == 1
    assert session.query(NaverKeywordVolumeDaily).count() == 1


# ── P2-3 채택: 파서 단위(그동안 fetcher를 전부 stub해 커버리지 0이었다) ──────────


@pytest.mark.parametrize(
    "raw,value,below",
    [
        (1234, 1234, False), (0, 0, False), (12.7, 12, False),
        (True, 0, False),                 # bool은 int 서브클래스 — 검색량 아님
        (None, 0, False), ("", 0, False),
        ("< 10", 5, True), ("<10", 5, True), (" < 10 ", 5, True), ("<", 5, True),
        ("990", 990, False),
    ],
)
def test_parse_qc_flagged_matches_parse_qc_and_flags_the_sentinel(raw, value, below):
    got, flag = _parse_qc_flagged(raw)
    assert got == value == _parse_qc(raw), "값은 원본 파서와 항상 같아야 한다"
    assert flag is below
