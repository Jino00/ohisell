# test_naver_searchterm_ss1.py — SS1(검색어 ROAS 레이어 수집층, docs/PLAN_naver-ad-searchterm-ss.md §3) 테스트
# 커버: ①STCONV_COL_* ±2 오프셋 파싱 정확성(15컬럼 fixture) ②purchase/cart 분리·직/간접 분리 집계
#   ③성과행 병합 UPDATE + 성과행 없는 검색어 0-성과 INSERT ④멱등(재수집 시 비가산).
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverSearchTermDaily
from app.services.naver_ad import search_term_ingest
from app.services import naver_sa_ad_fetcher as fetcher


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


# ── SHOPPINGKEYWORD_CONVERSION_DETAIL 행: 15컬럼
#    (0일자 2캠페인 3그룹 4검색어 10기기 11직간접 12액션 13전환수 14전환매출) ──
def _stconv_row(date_s, camp, grp, term, dir_indir, action, cnt, val, device="M"):
    return [date_s, "1313769", camp, grp, term, "nad-x", "bsn-x", "x7", "x8", "x9",
            device, dir_indir, action, str(cnt), str(val)]


# ── ①±2 오프셋 파싱 정확성 ──
def test_stconv_col_constants_offset_from_ad_conversion():
    """SS1 최대 실수 지점: STCONV_COL_*는 CONV_COL_*(13컬럼)와 값이 겹치면 안 된다.
    특히 col10(기기)이 끼어들어 CONV_COL_ACTION=10을 그대로 재사용하면 액션을 기기값으로
    잘못 읽는다 — 상수 자체가 서로 다른 값을 가리키는지 고정 검증."""
    assert fetcher.STCONV_COL_DATE == 0
    assert fetcher.STCONV_COL_CAMPAIGN == 2
    assert fetcher.STCONV_COL_ADGROUP == 3
    assert fetcher.STCONV_COL_SEARCH_TERM == 4
    assert fetcher.STCONV_COL_DEVICE == 10
    assert fetcher.STCONV_COL_DIRINDIR == 11
    assert fetcher.STCONV_COL_ACTION == 12
    assert fetcher.STCONV_COL_CNT == 13
    assert fetcher.STCONV_COL_VALUE == 14
    # CONV_COL_ACTION(AD_CONVERSION 13컬럼)=10은 STCONV에서는 기기 컬럼 — 재사용 시
    # 액션 문자열이 아니라 "M"/"P"가 읽혀 전부 스킵되는 회귀를 상수 비교로 고정.
    assert fetcher.STCONV_COL_ACTION != fetcher.CONV_COL_ACTION


def test_fetch_search_term_conversion_parses_15col_with_offset(monkeypatch):
    """15컬럼 fixture — device(col10)에 액션 문자열과 겹치지 않는 값을 심어 ±2 오프셋이
    실제로 col12(액션)을 읽는지, col10을 잘못 읽지 않는지를 실증한다."""
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "x")
    monkeypatch.setattr(fetcher, "ensure_reports_built", lambda *a, **k: None)
    monkeypatch.setattr(fetcher, "_list_reports_by_type",
                        lambda tp, a, b: [{"date": "2026-07-21", "downloadUrl": "u"}])
    rows = [
        _stconv_row("20260721", "cmp-01", "grp-1", "아이폰 지문방지필름", "1", "purchase", 1, 15900, device="M"),
    ]
    monkeypatch.setattr(fetcher, "_download_tsv", lambda url: rows)

    out = fetcher.fetch_search_term_conversion(date(2026, 7, 21), date(2026, 7, 21))
    assert len(out) == 1
    r = out[0]
    assert r["date"] == "2026-07-21"
    assert r["campaign_id"] == "cmp-01" and r["adgroup_id"] == "grp-1"
    assert r["search_term"] == "아이폰 지문방지필름"
    assert r["conv_purchase_cnt"] == 1 and r["conv_purchase_amt"] == 15900
    assert r["conv_direct_cnt"] == 1  # 직접(dirindir="1")


# ── ②purchase/cart 분리 · 직/간접 분리 집계 ──
def test_fetch_search_term_conversion_routes_purchase_and_cart(monkeypatch):
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "x")
    monkeypatch.setattr(fetcher, "ensure_reports_built", lambda *a, **k: None)
    monkeypatch.setattr(fetcher, "_list_reports_by_type",
                        lambda tp, a, b: [{"date": "2026-07-21", "downloadUrl": "u"}])
    rows = [
        _stconv_row("20260721", "cmp-01", "grp-1", "맥세이프", "1", "purchase", 1, 15900),      # 직접 구매
        _stconv_row("20260721", "cmp-01", "grp-1", "맥세이프", "2", "purchase", 1, 10000),      # 간접 구매
        _stconv_row("20260721", "cmp-01", "grp-1", "맥세이프", "1", "add_to_cart", 2, 5000),    # 직접 장바구니
        _stconv_row("20260721", "cmp-01", "grp-1", "맥세이프", "2", "add_to_cart", 3, 7000),    # 간접 장바구니
        _stconv_row("20260721", "cmp-01", "grp-1", "맥세이프", "1", "signup", 9, 99999),        # 기타 → 무시
    ]
    monkeypatch.setattr(fetcher, "_download_tsv", lambda url: rows)

    out = fetcher.fetch_search_term_conversion(date(2026, 7, 21), date(2026, 7, 21))
    assert len(out) == 1
    r = out[0]
    # 구매는 직+간 합산(제외 게이트=보수적 판정), 직접전환수는 별도 보존(SS4 승격 신호)
    assert r["conv_purchase_cnt"] == 2 and r["conv_purchase_amt"] == 25900
    assert r["conv_direct_cnt"] == 1
    # 장바구니는 직+간 합산 1쌍(cnt/amt)만 — naver_ad_daily의 direct/indirect 4분리와 다름
    assert r["cart_cnt"] == 5 and r["cart_amt"] == 12000


def test_fetch_search_term_conversion_cart_only_row_kept(monkeypatch):
    """구매 0인데 장바구니만 있는 grain도 보존."""
    monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
    monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "x")
    monkeypatch.setattr(fetcher, "ensure_reports_built", lambda *a, **k: None)
    monkeypatch.setattr(fetcher, "_list_reports_by_type",
                        lambda tp, a, b: [{"date": "2026-07-21", "downloadUrl": "u"}])
    rows = [_stconv_row("20260721", "cmp-02", "grp-9", "강화유리 케이스", "1", "add_to_cart", 4, 8000)]
    monkeypatch.setattr(fetcher, "_download_tsv", lambda url: rows)

    out = fetcher.fetch_search_term_conversion(date(2026, 7, 21), date(2026, 7, 21))
    assert len(out) == 1
    r = out[0]
    assert r["conv_purchase_cnt"] == 0 and r["conv_purchase_amt"] == 0
    assert r["cart_cnt"] == 4 and r["cart_amt"] == 8000


# ── ③성과행 병합 UPDATE + 0-성과 INSERT ──
def test_ingest_search_term_conversion_merges_into_existing_perf_row(db, monkeypatch):
    db.add(NaverSearchTermDaily(
        ad_date=date(2026, 7, 21), campaign_id="cmp-01", adgroup_id="grp-1",
        search_term="맥세이프", source="shopping", imp=50, clk=5, cost=2500, rank_sum=15,
    ))
    db.commit()

    monkeypatch.setattr(search_term_ingest, "fetch_search_term_conversion", lambda a, b: [
        {"date": "2026-07-21", "campaign_id": "cmp-01", "adgroup_id": "grp-1", "search_term": "맥세이프",
         "conv_purchase_cnt": 1, "conv_direct_cnt": 1, "conv_purchase_amt": 15900,
         "cart_cnt": 2, "cart_amt": 5000},
    ])

    result = search_term_ingest.ingest_search_term_conversion(db, date(2026, 7, 21), date(2026, 7, 21))
    assert result == {"conv_rows": 1, "merged": 1, "inserted": 0}

    rows = db.query(NaverSearchTermDaily).filter(NaverSearchTermDaily.source == "shopping").all()
    assert len(rows) == 1  # 새 행 안 생기고 기존 성과행에 병합
    r = rows[0]
    assert r.imp == 50 and r.clk == 5 and r.cost == 2500  # 성과 컬럼 불변
    assert r.conv_purchase_cnt == 1 and r.conv_direct_cnt == 1 and r.conv_purchase_amt == 15900
    assert r.cart_cnt == 2 and r.cart_amt == 5000


def test_ingest_search_term_conversion_inserts_zero_perf_row_when_missing(db, monkeypatch):
    """간접전환 귀속 검색어처럼 성과행이 없는 grain은 0-성과 행으로 INSERT해 전환 보존."""
    monkeypatch.setattr(search_term_ingest, "fetch_search_term_conversion", lambda a, b: [
        {"date": "2026-07-21", "campaign_id": "cmp-02", "adgroup_id": "grp-9", "search_term": "강화유리",
         "conv_purchase_cnt": 1, "conv_direct_cnt": 0, "conv_purchase_amt": 12000,
         "cart_cnt": 0, "cart_amt": 0},
    ])

    result = search_term_ingest.ingest_search_term_conversion(db, date(2026, 7, 21), date(2026, 7, 21))
    assert result == {"conv_rows": 1, "merged": 0, "inserted": 1}
    db.commit()  # 신규 INSERT는 명시 flush/commit 전엔 raw SQL 재조회에 안 잡힘(autoflush=False)

    row = db.query(NaverSearchTermDaily).filter(
        NaverSearchTermDaily.source == "shopping", NaverSearchTermDaily.search_term == "강화유리",
    ).one()
    assert row.imp == 0 and row.clk == 0 and row.cost == 0 and row.rank_sum == 0
    assert row.conv_purchase_cnt == 1 and row.conv_purchase_amt == 12000


def test_ingest_search_term_conversion_no_conv_rows_is_noop(db, monkeypatch):
    monkeypatch.setattr(search_term_ingest, "fetch_search_term_conversion", lambda a, b: [])
    result = search_term_ingest.ingest_search_term_conversion(db, date(2026, 7, 21), date(2026, 7, 21))
    assert result == {"conv_rows": 0, "merged": 0, "inserted": 0}
    assert db.query(NaverSearchTermDaily).count() == 0


# ── ④멱등(재수집 시 비가산) ──
def test_ingest_search_term_conversion_idempotent_on_rerun(db, monkeypatch):
    db.add(NaverSearchTermDaily(
        ad_date=date(2026, 7, 21), campaign_id="cmp-01", adgroup_id="grp-1",
        search_term="맥세이프", source="shopping", imp=50, clk=5, cost=2500, rank_sum=15,
    ))
    db.commit()

    conv_payload = [
        {"date": "2026-07-21", "campaign_id": "cmp-01", "adgroup_id": "grp-1", "search_term": "맥세이프",
         "conv_purchase_cnt": 1, "conv_direct_cnt": 1, "conv_purchase_amt": 15900,
         "cart_cnt": 2, "cart_amt": 5000},
    ]
    monkeypatch.setattr(search_term_ingest, "fetch_search_term_conversion", lambda a, b: conv_payload)

    search_term_ingest.ingest_search_term_conversion(db, date(2026, 7, 21), date(2026, 7, 21))
    search_term_ingest.ingest_search_term_conversion(db, date(2026, 7, 21), date(2026, 7, 21))
    search_term_ingest.ingest_search_term_conversion(db, date(2026, 7, 21), date(2026, 7, 21))

    rows = db.query(NaverSearchTermDaily).filter(NaverSearchTermDaily.source == "shopping").all()
    assert len(rows) == 1  # 재실행해도 새 행 안 생김
    r = rows[0]
    # 3번 재실행해도 SET 의미론이라 값이 3배로 누적되지 않음
    assert r.conv_purchase_cnt == 1 and r.conv_purchase_amt == 15900
    assert r.cart_cnt == 2 and r.cart_amt == 5000


def test_ingest_search_term_conversion_idempotent_zero_perf_rerun(db, monkeypatch):
    """0-성과 INSERT 케이스도 재실행 시 행이 중복 생성되지 않음(uq 위반 없이 UPDATE로 전환)."""
    conv_payload = [
        {"date": "2026-07-21", "campaign_id": "cmp-02", "adgroup_id": "grp-9", "search_term": "강화유리",
         "conv_purchase_cnt": 1, "conv_direct_cnt": 0, "conv_purchase_amt": 12000,
         "cart_cnt": 0, "cart_amt": 0},
    ]
    monkeypatch.setattr(search_term_ingest, "fetch_search_term_conversion", lambda a, b: conv_payload)

    r1 = search_term_ingest.ingest_search_term_conversion(db, date(2026, 7, 21), date(2026, 7, 21))
    db.commit()
    assert r1 == {"conv_rows": 1, "merged": 0, "inserted": 1}

    r2 = search_term_ingest.ingest_search_term_conversion(db, date(2026, 7, 21), date(2026, 7, 21))
    assert r2 == {"conv_rows": 1, "merged": 1, "inserted": 0}  # 두 번째부터는 기존 행에 병합

    rows = db.query(NaverSearchTermDaily).filter(
        NaverSearchTermDaily.source == "shopping", NaverSearchTermDaily.search_term == "강화유리",
    ).all()
    assert len(rows) == 1


# ── ingest_search_term_daily 크론 편입 확인 ──
def test_ingest_search_term_daily_includes_conversion_merge(db, monkeypatch):
    def fake_fetch(report_tp, date_from, date_to):
        if report_tp == "SHOPPINGKEYWORD_DETAIL":
            return [{"date": "2026-07-21", "campaign_id": "cmp-01", "adgroup_id": "grp-1",
                      "search_term": "맥세이프", "imp": 10, "clk": 1, "cost": 100, "rank_sum": 3}]
        return []

    monkeypatch.setattr(search_term_ingest, "fetch_search_term_daily", fake_fetch)
    monkeypatch.setattr(search_term_ingest, "request_missing_expkeyword_reports", lambda a, b: [])
    monkeypatch.setattr(search_term_ingest, "_conversion_report_dates", lambda a, b: set())  # 실HTTP 차단
    monkeypatch.setattr(search_term_ingest, "fetch_search_term_conversion", lambda a, b: [
        {"date": "2026-07-21", "campaign_id": "cmp-01", "adgroup_id": "grp-1", "search_term": "맥세이프",
         "conv_purchase_cnt": 1, "conv_direct_cnt": 1, "conv_purchase_amt": 15900,
         "cart_cnt": 0, "cart_amt": 0},
    ])

    result = search_term_ingest.ingest_search_term_daily(db, date(2026, 7, 21), date(2026, 7, 21))
    assert result["conversion"] == {"conv_rows": 1, "merged": 1, "inserted": 0}

    row = db.query(NaverSearchTermDaily).filter(NaverSearchTermDaily.source == "shopping").one()
    assert row.cost == 100  # 성과 병존
    assert row.conv_purchase_cnt == 1 and row.conv_purchase_amt == 15900  # 전환 병합


# ── ⑤ 전환 보존 캐리포워드(fail-safe, SS2 전환 보호 게이트 데이터 보존) ──
def _prior_shopping_row_with_conv(db):
    """직전 런에서 적재된 shopping 성과행 + 전환(맥세이프, clk=12·cost=3000·purchase 전환 2)."""
    db.add(NaverSearchTermDaily(
        ad_date=date(2026, 7, 21), campaign_id="cmp-01", adgroup_id="grp-1",
        search_term="맥세이프", source="shopping", imp=50, clk=12, cost=3000, rank_sum=15,
        conv_purchase_cnt=2, conv_direct_cnt=1, conv_purchase_amt=31800, cart_cnt=1, cart_amt=5000,
    ))
    db.commit()


def _fresh_shopping_perf(report_tp, date_from, date_to):
    """SHOPPINGKEYWORD_DETAIL는 성과만 신선 수집(전환 컬럼 없음 → delete+insert가 0 초기화)."""
    if report_tp == "SHOPPINGKEYWORD_DETAIL":
        return [{"date": "2026-07-21", "campaign_id": "cmp-01", "adgroup_id": "grp-1",
                  "search_term": "맥세이프", "imp": 60, "clk": 15, "cost": 3600, "rank_sum": 18}]
    return []


def test_ingest_daily_carryforward_preserves_conv_when_report_absent(db, monkeypatch):
    """전환 보고서 일시 부재([]) 시에도 delete+insert 전 전환이 캐리포워드로 보존된다.
    이게 없으면 성과행 교체로 전환이 0이 되어 SS2 전환 보호 게이트가 무력화된다."""
    _prior_shopping_row_with_conv(db)
    monkeypatch.setattr(search_term_ingest, "fetch_search_term_daily", _fresh_shopping_perf)
    monkeypatch.setattr(search_term_ingest, "request_missing_expkeyword_reports", lambda a, b: [])
    monkeypatch.setattr(search_term_ingest, "_conversion_report_dates", lambda a, b: set())  # 보고서 부재
    monkeypatch.setattr(search_term_ingest, "fetch_search_term_conversion", lambda a, b: [])  # 보고서 부재

    result = search_term_ingest.ingest_search_term_daily(db, date(2026, 7, 21), date(2026, 7, 21))
    assert result["conversion_carryforward"] == 1
    assert result["conversion"] == {"conv_rows": 0, "merged": 0, "inserted": 0}

    row = db.query(NaverSearchTermDaily).filter(NaverSearchTermDaily.source == "shopping").one()
    assert row.imp == 60 and row.clk == 15 and row.cost == 3600  # 성과는 신선값으로 교체
    # 전환은 캐리포워드로 보존(0으로 소실되지 않음)
    assert row.conv_purchase_cnt == 2 and row.conv_direct_cnt == 1 and row.conv_purchase_amt == 31800
    assert row.cart_cnt == 1 and row.cart_amt == 5000


def test_ingest_daily_fresh_report_overwrites_carryforward(db, monkeypatch):
    """캐리포워드 후 신선한 CONVERSION 보고서가 오면 최신값으로 SET(멱등 유지 — 3배 누적 없음)."""
    _prior_shopping_row_with_conv(db)
    monkeypatch.setattr(search_term_ingest, "fetch_search_term_daily", _fresh_shopping_perf)
    monkeypatch.setattr(search_term_ingest, "request_missing_expkeyword_reports", lambda a, b: [])
    # 보고서 목록 조회 실패(fail-open) 경로: report_dates=set() → 캐리포워드 실행 후 신선 rows가 SET으로 덮음.
    monkeypatch.setattr(search_term_ingest, "_conversion_report_dates", lambda a, b: set())
    monkeypatch.setattr(search_term_ingest, "fetch_search_term_conversion", lambda a, b: [
        {"date": "2026-07-21", "campaign_id": "cmp-01", "adgroup_id": "grp-1", "search_term": "맥세이프",
         "conv_purchase_cnt": 5, "conv_direct_cnt": 3, "conv_purchase_amt": 79500,
         "cart_cnt": 4, "cart_amt": 12000},
    ])

    result = search_term_ingest.ingest_search_term_daily(db, date(2026, 7, 21), date(2026, 7, 21))
    assert result["conversion_carryforward"] == 1     # 캐리포워드는 실행됐지만(fail-open 경로)
    assert result["conversion"]["merged"] == 1        # 보고서가 최신값으로 덮음

    row = db.query(NaverSearchTermDaily).filter(NaverSearchTermDaily.source == "shopping").one()
    # 캐리포워드값(2/1/31800/1/5000)이 아니라 신선 보고서값
    assert row.conv_purchase_cnt == 5 and row.conv_direct_cnt == 3 and row.conv_purchase_amt == 79500
    assert row.cart_cnt == 4 and row.cart_amt == 12000


def test_ingest_daily_carryforward_reinserts_zero_perf_when_perf_dropped(db, monkeypatch):
    """성과가 사라진(오늘 신선 보고서에 없는) 전환 grain은 0-성과 행으로 재삽입해 전환 보존."""
    _prior_shopping_row_with_conv(db)

    def _perf_without_maxsafe(report_tp, date_from, date_to):
        if report_tp == "SHOPPINGKEYWORD_DETAIL":
            # "맥세이프"는 오늘 성과 보고서에 없음 → delete로 사라짐 → 캐리포워드가 0-성과로 되살림
            return [{"date": "2026-07-21", "campaign_id": "cmp-01", "adgroup_id": "grp-1",
                      "search_term": "다른검색어", "imp": 10, "clk": 1, "cost": 100, "rank_sum": 3}]
        return []
    monkeypatch.setattr(search_term_ingest, "fetch_search_term_daily", _perf_without_maxsafe)
    monkeypatch.setattr(search_term_ingest, "request_missing_expkeyword_reports", lambda a, b: [])
    monkeypatch.setattr(search_term_ingest, "_conversion_report_dates", lambda a, b: set())  # 보고서 부재
    monkeypatch.setattr(search_term_ingest, "fetch_search_term_conversion", lambda a, b: [])

    result = search_term_ingest.ingest_search_term_daily(db, date(2026, 7, 21), date(2026, 7, 21))
    assert result["conversion_carryforward"] == 1

    row = db.query(NaverSearchTermDaily).filter(
        NaverSearchTermDaily.source == "shopping", NaverSearchTermDaily.search_term == "맥세이프",
    ).one()
    assert row.imp == 0 and row.clk == 0 and row.cost == 0  # 성과 소멸 → 0-성과 행
    assert row.conv_purchase_cnt == 2 and row.conv_purchase_amt == 31800  # 전환 보존


# ── ⑥ 보고서 실재(BUILT) 날짜 = 보고서가 진실(전환 0 포함, codex 2R[P2]) ──
def test_ingest_daily_report_present_disappeared_grain_reset_to_zero(db, monkeypatch):
    """보고서 실재 날짜에서 grain이 소멸(전환 0 소급)하면 캐리포워드로 부활하지 않고 0으로 확정.
    이전 버그: 캐리포워드가 옛 전환을 되살려 SS2 게이트가 반대로 오작동(잘못된 보존)."""
    _prior_shopping_row_with_conv(db)  # 맥세이프 conv 2/1/31800/1/5000
    monkeypatch.setattr(search_term_ingest, "fetch_search_term_daily", _fresh_shopping_perf)  # 맥세이프 성과만 신선
    monkeypatch.setattr(search_term_ingest, "request_missing_expkeyword_reports", lambda a, b: [])
    # 보고서는 실재(BUILT)하지만 rows는 0(전환이 소급 0이 됨) → 리셋만 적용, 병합 대상 없음.
    monkeypatch.setattr(search_term_ingest, "_conversion_report_dates", lambda a, b: {date(2026, 7, 21)})
    monkeypatch.setattr(search_term_ingest, "fetch_search_term_conversion", lambda a, b: [])

    result = search_term_ingest.ingest_search_term_daily(db, date(2026, 7, 21), date(2026, 7, 21))
    assert result["conversion_carryforward"] == 0  # 보고서 실재 날짜라 캐리포워드 스킵

    row = db.query(NaverSearchTermDaily).filter(NaverSearchTermDaily.source == "shopping").one()
    assert row.imp == 60 and row.clk == 15 and row.cost == 3600  # 성과는 신선값 유지
    # 전환은 0으로 확정(보고서가 진실, 사라진 grain 부활 금지)
    assert row.conv_purchase_cnt == 0 and row.conv_direct_cnt == 0 and row.conv_purchase_amt == 0
    assert row.cart_cnt == 0 and row.cart_amt == 0


def test_ingest_daily_report_present_surviving_grain_takes_report_value(db, monkeypatch):
    """보고서 실재 날짜에서 살아남은 grain은 리셋 후 보고서 최신값으로 SET(캐리포워드 스킵)."""
    _prior_shopping_row_with_conv(db)  # 맥세이프 conv 2/1/31800/1/5000
    monkeypatch.setattr(search_term_ingest, "fetch_search_term_daily", _fresh_shopping_perf)
    monkeypatch.setattr(search_term_ingest, "request_missing_expkeyword_reports", lambda a, b: [])
    monkeypatch.setattr(search_term_ingest, "_conversion_report_dates", lambda a, b: {date(2026, 7, 21)})
    monkeypatch.setattr(search_term_ingest, "fetch_search_term_conversion", lambda a, b: [
        {"date": "2026-07-21", "campaign_id": "cmp-01", "adgroup_id": "grp-1", "search_term": "맥세이프",
         "conv_purchase_cnt": 5, "conv_direct_cnt": 3, "conv_purchase_amt": 79500,
         "cart_cnt": 4, "cart_amt": 12000},
    ])

    result = search_term_ingest.ingest_search_term_daily(db, date(2026, 7, 21), date(2026, 7, 21))
    assert result["conversion_carryforward"] == 0  # 보고서 실재 날짜라 캐리포워드 스킵
    assert result["conversion"]["merged"] == 1     # 리셋 후 보고서값으로 재적용

    row = db.query(NaverSearchTermDaily).filter(NaverSearchTermDaily.source == "shopping").one()
    assert row.conv_purchase_cnt == 5 and row.conv_direct_cnt == 3 and row.conv_purchase_amt == 79500
    assert row.cart_cnt == 4 and row.cart_amt == 12000


def test_ingest_daily_report_present_resets_conversion_only_row(db, monkeypatch):
    """이 날짜에 신선 성과가 전혀 없어 delete+insert가 건드리지 못하는 0-성과(간접전환) 행도,
    보고서 실재 날짜면 전환 0-리셋이 유일한 교정으로 작동해 옛 전환이 소실된다(codex 2R[P2])."""
    db.add(NaverSearchTermDaily(
        ad_date=date(2026, 7, 21), campaign_id="cmp-01", adgroup_id="grp-1",
        search_term="간접전환어", source="shopping", imp=0, clk=0, cost=0, rank_sum=0,
        conv_purchase_cnt=3, conv_direct_cnt=1, conv_purchase_amt=45000, cart_cnt=0, cart_amt=0,
    ))
    db.commit()
    monkeypatch.setattr(search_term_ingest, "fetch_search_term_daily", lambda tp, a, b: [])  # 신선 성과 0
    monkeypatch.setattr(search_term_ingest, "request_missing_expkeyword_reports", lambda a, b: [])
    monkeypatch.setattr(search_term_ingest, "_conversion_report_dates", lambda a, b: {date(2026, 7, 21)})
    monkeypatch.setattr(search_term_ingest, "fetch_search_term_conversion", lambda a, b: [])  # 전환 소급 0

    search_term_ingest.ingest_search_term_daily(db, date(2026, 7, 21), date(2026, 7, 21))
    row = db.query(NaverSearchTermDaily).filter(
        NaverSearchTermDaily.source == "shopping", NaverSearchTermDaily.search_term == "간접전환어",
    ).one()
    assert row.conv_purchase_cnt == 0 and row.conv_direct_cnt == 0 and row.conv_purchase_amt == 0


# ── ⑦ report_dates는 fetch_search_term_conversion **이후**에 계산돼야 한다(codex 3R[P2]) ──
def test_ingest_daily_report_dates_computed_after_fetch_self_heal(db, monkeypatch):
    """fetch_search_term_conversion 내부의 ensure_reports_built는 미생성 CONVERSION 보고서를
    그 호출 도중 새로 BUILT시킬 수 있다(자기치유). report_dates를 fetch **전에** 계산하면 그
    사이 새로 BUILT된 날짜를 여전히 '부재'로 오판해, 캐리포워드가 옛 전환을 되살리고 0-리셋을
    건너뛴다. fetch가 실행되면 상태 플래그를 세우고 report_dates가 그 플래그를 관측하게 해
    호출 순서를 직접 재현한다: fetch 전에 report_dates가 계산되면(버그) 항상 '부재'를 보고,
    fetch 후에 계산되면(수정 후) '실재'를 정확히 보고한다."""
    _prior_shopping_row_with_conv(db)  # 맥세이프 conv 2/1/31800/1/5000(옛 전환)
    monkeypatch.setattr(search_term_ingest, "fetch_search_term_daily", _fresh_shopping_perf)
    monkeypatch.setattr(search_term_ingest, "request_missing_expkeyword_reports", lambda a, b: [])

    state = {"fetched": False}

    def fake_fetch_conv(date_from, date_to):
        state["fetched"] = True  # fetch 내부 ensure_reports_built 자기치유가 여기서 일어났다고 가정
        return []  # 전환이 소급 0이 되어 rows는 비었지만 보고서 자체는 이제 BUILT

    def fake_report_dates(date_from, date_to):
        # fetch가 이미 일어난 뒤 호출됐어야 실재를 관측한다. 순서가 뒤바뀌면 항상 부재로 오판.
        return {date(2026, 7, 21)} if state["fetched"] else set()

    monkeypatch.setattr(search_term_ingest, "fetch_search_term_conversion", fake_fetch_conv)
    monkeypatch.setattr(search_term_ingest, "_conversion_report_dates", fake_report_dates)

    result = search_term_ingest.ingest_search_term_daily(db, date(2026, 7, 21), date(2026, 7, 21))

    # 올바른 순서(fetch 먼저 → report_dates 나중)라면 report_dates가 실재를 정확히 관측해
    # 캐리포워드를 스킵하고, 0-리셋만 적용돼 옛 전환이 되살아나지 않는다.
    assert result["conversion_carryforward"] == 0

    row = db.query(NaverSearchTermDaily).filter(NaverSearchTermDaily.source == "shopping").one()
    assert row.imp == 60 and row.clk == 15 and row.cost == 3600  # 성과는 신선값 유지
    assert row.conv_purchase_cnt == 0 and row.conv_direct_cnt == 0 and row.conv_purchase_amt == 0
    assert row.cart_cnt == 0 and row.cart_amt == 0
