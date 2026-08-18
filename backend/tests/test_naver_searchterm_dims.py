# test_naver_searchterm_dims.py — D-NAO-198 ① 시간대·지역·매체 축 적재
# 커버: ①컬럼 상수 고정(기존 ST_COL_*와 충돌 금지) ②축별 마진이 원본 총합과 일치
#   ③결합표는 clk>0 or cost>0 칸만 ④delete 대상이 «리포트가 준 날짜»뿐(요청 범위 아님)
#   ⑤멱등 ⑥콤마 표기 숫자 0-낙하 관측
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverSearchTermDimCellDaily, NaverSearchTermDimDaily
from app.services import naver_sa_ad_fetcher as fetcher
from app.services.naver_ad import search_term_dim_ingest


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    # ★prod와 같은 autoflush=False (교훈 #292: 관대한 픽스처는 query-then-add 결함을 못 잡는다)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


# SHOPPINGKEYWORD_DETAIL 16컬럼:
# 0일자 1고객ID 2캠페인 3그룹 4검색어 5소재 6비즈채널 7시간대 8지역 9매체 10기기
# 11노출 12클릭 13비용 14순위합 15미상
def _row(d, camp, grp, term, hour, region, media, imp, clk, cost, rank=0):
    return [d, "1313769", camp, grp, term, "nad-x", "bsn-x", hour, region, media,
            "M", str(imp), str(clk), str(cost), str(rank), ""]


def _patch(monkeypatch, rows_by_date):
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "y")
    monkeypatch.setattr(fetcher, "ensure_reports_built", lambda *a, **k: None)
    monkeypatch.setattr(fetcher, "_list_reports_by_type",
                        lambda tp, f, t: [{"date": d, "downloadUrl": d} for d in rows_by_date])
    monkeypatch.setattr(fetcher, "_download_tsv", lambda url: rows_by_date[url])


# ── ① 컬럼 상수 ──────────────────────────────────────────────────────────
def test_dim_column_constants_fixed_and_distinct():
    """col7/8/9는 D-NAO-182가 45,200행 실물 distinct로 확정한 자리다. 기존 상수와 겹치면
    엉뚱한 컬럼을 축으로 읽는다(EXPKEYWORD 재사용 사고와 같은 모양)."""
    assert (fetcher.ST_COL_HOUR, fetcher.ST_COL_REGION, fetcher.ST_COL_MEDIA) == (7, 8, 9)
    existing = {fetcher.ST_COL_DATE, fetcher.ST_COL_CAMPAIGN, fetcher.ST_COL_ADGROUP,
                fetcher.ST_COL_SEARCH_TERM, fetcher.ST_COL_IMP, fetcher.ST_COL_CLK,
                fetcher.ST_COL_COST, fetcher.ST_COL_RANK_SUM}
    assert existing.isdisjoint({7, 8, 9})


# ── ② 축별 마진 = 원본 총합 ──────────────────────────────────────────────
def test_marginals_each_axis_totals_match_raw(monkeypatch):
    rows = [
        _row("20260801", "c1", "g1", "필름", "09", "01", "111111", 10, 1, 100),
        _row("20260801", "c1", "g1", "케이스", "09", "02", "222222", 5, 0, 0),
        _row("20260801", "c1", "g1", "필름", "10", "01", "111111", 7, 2, 300),
    ]
    _patch(monkeypatch, {"2026-08-01": rows})
    out = fetcher.fetch_search_term_dimensions(date(2026, 8, 1), date(2026, 8, 1))
    m = out["marginals"]

    for dim in ("h", "r", "m"):
        sub = [r for r in m if r["dim_type"] == dim]
        assert sum(r["imp"] for r in sub) == 22, dim
        assert sum(r["clk"] for r in sub) == 3, dim
        assert sum(r["cost"] for r in sub) == 400, dim

    # 축 값이 검색어를 가로질러 합쳐진다(09시 = 필름10 + 케이스5)
    h09 = next(r for r in m if r["dim_type"] == "h" and r["dim_value"] == "09")
    assert (h09["imp"], h09["clk"], h09["cost"]) == (15, 1, 100)


# ── ③ 결합표는 «돈이 난 칸»만 ────────────────────────────────────────────
def test_cells_keep_only_money_bearing(monkeypatch):
    rows = [
        _row("20260801", "c1", "g1", "a", "09", "01", "111111", 10, 1, 100),   # 유료
        _row("20260801", "c1", "g1", "b", "11", "03", "333333", 99, 0, 0),     # 노출 전용
        _row("20260801", "c1", "g1", "c", "12", "04", "444444", 1, 1, 0),      # 클릭만
    ]
    _patch(monkeypatch, {"2026-08-01": rows})
    out = fetcher.fetch_search_term_dimensions(date(2026, 8, 1), date(2026, 8, 1))
    keys = {(c["hour_code"], c["region_code"], c["media_code"]) for c in out["cells"]}
    assert keys == {("09", "01", "111111"), ("12", "04", "444444")}
    # 노출 전용 칸이 결합에선 빠져도 마진에는 남아야 한다
    h11 = next(r for r in out["marginals"] if r["dim_type"] == "h" and r["dim_value"] == "11")
    assert h11["imp"] == 99


# ── ④ delete는 «리포트가 준 날짜»만 ──────────────────────────────────────
def test_ingest_does_not_delete_dates_absent_from_report(monkeypatch, db):
    """리포트 생성 실패로 어떤 날짜가 빠졌을 때 그 날짜의 기존 적재분을 지우면 안 된다 —
    원본이 180일 뒤 사라지므로 그 삭제는 복구 불가다."""
    db.add(NaverSearchTermDimDaily(
        ad_date=date(2026, 7, 31), campaign_id="c1", adgroup_id="g1",
        dim_type="h", dim_value="09", imp=1, clk=0, cost=0, rank_sum=0))
    db.commit()

    rows = [_row("20260801", "c1", "g1", "a", "09", "01", "111111", 10, 1, 100)]
    _patch(monkeypatch, {"2026-08-01": rows})
    search_term_dim_ingest.ingest_search_term_dimensions(db, date(2026, 7, 31), date(2026, 8, 1))

    survived = db.query(NaverSearchTermDimDaily).filter(
        NaverSearchTermDimDaily.ad_date == date(2026, 7, 31)).count()
    assert survived == 1, "리포트에 없던 날짜의 기존 행이 지워졌다"


def test_empty_report_is_surfaced_not_silent(monkeypatch, db, caplog):
    """리포트 0행은 «정상 0»과 «수집 실패»를 구분 못 한다 — 조용히 넘기면 방치된다.
    (동치 변이 대비: 빈 리포트 가드를 없애도 delete는 in_([])라 무연산이므로 동작이 같다.
     그래서 이 가드의 실제 값은 경고 로그이고, 그 로그를 여기서 고정한다.)"""
    _patch(monkeypatch, {})
    import logging
    with caplog.at_level(logging.WARNING):
        search_term_dim_ingest.ingest_search_term_dimensions(db, date(2026, 8, 1), date(2026, 8, 1))
    assert any("기존 적재분 보존" in r.getMessage() for r in caplog.records), \
        "빈 리포트가 경고 없이 지나갔다"


def test_ingest_empty_report_preserves_everything(monkeypatch, db):
    db.add(NaverSearchTermDimDaily(
        ad_date=date(2026, 8, 1), campaign_id="c1", adgroup_id="g1",
        dim_type="h", dim_value="09", imp=1, clk=0, cost=0, rank_sum=0))
    db.commit()
    _patch(monkeypatch, {})
    r = search_term_dim_ingest.ingest_search_term_dimensions(db, date(2026, 8, 1), date(2026, 8, 1))
    assert r == {"dates": [], "marginal_rows": 0, "cell_rows": 0}
    assert db.query(NaverSearchTermDimDaily).count() == 1


# ── ⑤ 멱등 ───────────────────────────────────────────────────────────────
def test_ingest_is_idempotent(monkeypatch, db):
    rows = [
        _row("20260801", "c1", "g1", "a", "09", "01", "111111", 10, 1, 100),
        _row("20260801", "c1", "g1", "b", "10", "02", "222222", 5, 1, 50),
    ]
    _patch(monkeypatch, {"2026-08-01": rows})
    first = search_term_dim_ingest.ingest_search_term_dimensions(db, date(2026, 8, 1), date(2026, 8, 1))
    n_m1 = db.query(NaverSearchTermDimDaily).count()
    n_c1 = db.query(NaverSearchTermDimCellDaily).count()
    second = search_term_dim_ingest.ingest_search_term_dimensions(db, date(2026, 8, 1), date(2026, 8, 1))
    assert first == second
    assert db.query(NaverSearchTermDimDaily).count() == n_m1
    assert db.query(NaverSearchTermDimCellDaily).count() == n_c1
    # 값도 두 배가 되면 안 된다
    h09 = db.query(NaverSearchTermDimDaily).filter(
        NaverSearchTermDimDaily.dim_type == "h", NaverSearchTermDimDaily.dim_value == "09").one()
    assert h09.imp == 10


# ── ⑥ 콤마 표기 0-낙하 관측 ──────────────────────────────────────────────
def test_comma_formatted_number_is_flagged(caplog):
    """`_safe_int`는 '1,234'를 조용히 0으로 만든다(기존 부채). 값 동작은 바꾸지 않되
    180일 뒤 원본이 사라지는 자료이므로 «0으로 떨어졌다»는 사실은 반드시 관측돼야 한다."""
    bad: list[str] = []
    assert fetcher._num_or_flag("1,234", bad) == 0
    assert bad == ["1,234"]
    bad2: list[str] = []
    assert fetcher._num_or_flag("9670.0", bad2) == 9670   # float 표기는 정상 경로
    assert fetcher._num_or_flag("0", bad2) == 0
    assert fetcher._num_or_flag("", bad2) == 0
    assert bad2 == [], "정상 입력이 오탐되면 로그가 무의미해진다"
