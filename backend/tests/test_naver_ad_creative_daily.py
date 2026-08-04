# test_naver_ad_creative_daily.py — D-NAO-140 S1: 소재(광고) 단위 성과 수집
#
# 왜 필요한가: 우리 자동화는 소재 단위로 입찰을 바꾸는데 소재 단위 성과가 없었다(naver_ad_daily의
# grain은 광고그룹까지). 그래서 상향 브레이크가 대리 지표로 걸려 있었다 — 07-30 전면 정지의
# 구조적 원인 중 하나.
#
# 커버: (1) 파서가 두 보고서의 컬럼 5(소재 ID)로 묶는다 · 기본 grain은 회귀 0
#       (2) 적재 멱등 · 소재 ID 없는 행은 안 만든다
#       (3) ★대조 — 소재합 == 그룹합. 센티넬은 `adgroup_id` 칼럼에 산다(2배 함정)
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdCreativeDaily, NaverAdDaily
from app.services import naver_sa_ad_fetcher as fetcher
from app.services.naver_ad import ad_creative_daily_sync, report_collector

DAY = date(2026, 8, 3)

# 라이브 실측 형식(2026-08-04에 08-03 보고서를 직접 내려받아 확인한 14/13컬럼 배치).
# 0:일자 1:고객 2:캠페인 3:그룹 4:키워드 5:★소재 6:비즈채널 7:? 8:기기 9:노출 10:클릭 11:비용 12:순위합 13:?
def _ad_row(ad_id, *, kw="-", imp=10, clk=1, cost=100, rank=20, grp="grp-1"):
    return ["20260803", "1313769", "cmp-1", grp, kw, ad_id,
            "bsn-1", "0", "M", str(imp), str(clk), str(cost), str(rank), "0"]


# 0:일자 1:고객 2:캠페인 3:그룹 4:키워드 5:★소재 6:비즈채널 7:? 8:기기 9:직/간접 10:액션 11:수 12:금액
def _conv_row(ad_id, *, kw="-", action="purchase", direct="1", cnt=1, amt=10000, grp="grp-1"):
    return ["20260803", "1313769", "cmp-1", grp, kw, ad_id,
            "bsn-1", "0", "M", direct, action, str(cnt), str(amt)]


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def reports(monkeypatch):
    """AD·AD_CONVERSION 보고서를 주입한다(HTTP 없이 파서만 검증)."""
    def _install(ad_rows, conv_rows):
        monkeypatch.setattr(fetcher, "ensure_reports_built", lambda *a, **k: None)
        monkeypatch.setattr(fetcher, "list_ad_reports",
                            lambda *a, **k: [{"date": "2026-08-03", "downloadUrl": "u-ad"}])
        monkeypatch.setattr(fetcher, "_list_reports_by_type",
                            lambda tp, *a, **k: [{"date": "2026-08-03", "downloadUrl": "u-cv"}])
        monkeypatch.setattr(fetcher, "_download_tsv",
                            lambda url: ad_rows if url == "u-ad" else conv_rows)
        monkeypatch.setattr(fetcher, "ACCESS_LICENSE", "x")
        monkeypatch.setattr(fetcher, "SECRET_KEY_B64", "y")
        monkeypatch.setattr(fetcher, "get_campaign_types", lambda *a, **k: {"cmp-1": "SHOPPING"})
    return _install


# ══════════════════════════════════════════════════════════════════
# (1) 파서 — 컬럼 5로 묶는다 / 기본 grain 회귀 0
# ══════════════════════════════════════════════════════════════════
def test_ad_grain_groups_by_column_five(reports):
    """★두 보고서 모두 컬럼 5가 소재 ID다(라이브 실측). 같은 소재의 여러 기기·키워드 행은 합산."""
    reports([_ad_row("nad-A", kw="nkw-1"), _ad_row("nad-A", kw="nkw-2"), _ad_row("nad-B")],
            [_conv_row("nad-A"), _conv_row("nad-B", action="add_to_cart")])
    rows = report_collector.collect_daily_rows(DAY, DAY, grain=fetcher.GRAIN_AD)
    by_ad = {r["ad_id"]: r for r in rows}
    assert set(by_ad) == {"nad-A", "nad-B"}
    assert by_ad["nad-A"]["cost"] == 200  # 키워드 2행 롤업
    assert by_ad["nad-A"]["conv_direct_amt"] == 10000
    # 장바구니는 매출에 안 섞인다(naver_ad_daily와 같은 규율)
    assert by_ad["nad-B"]["cart_direct_amt"] == 10000
    assert by_ad["nad-B"]["conv_direct_amt"] == 0
    assert "keyword_id" not in by_ad["nad-A"]


def test_default_grain_is_unchanged(reports):
    """★기본 grain은 기존 동작 그대로 — 머니 경로(naver_ad_daily)는 한 글자도 안 바뀐다."""
    reports([_ad_row("nad-A", kw="nkw-1"), _ad_row("nad-B", kw="nkw-1")], [_conv_row("nad-A", kw="nkw-1")])
    rows = report_collector.collect_daily_rows(DAY, DAY)
    assert len(rows) == 1  # 같은 (캠페인·그룹·키워드) → 소재가 달라도 한 행
    assert rows[0]["keyword_id"] == "nkw-1"
    assert rows[0]["cost"] == 200
    assert "ad_id" not in rows[0]


def test_shopping_keyword_sentinel_still_normalized(reports):
    """쇼핑 행의 keyword='-'는 기본 grain에서 여전히 "" 로 정규화된다(회귀 방지)."""
    reports([_ad_row("nad-A")], [])
    assert report_collector.collect_daily_rows(DAY, DAY)[0]["keyword_id"] == ""


# ══════════════════════════════════════════════════════════════════
# (2) 적재
# ══════════════════════════════════════════════════════════════════
def _rows(*specs):
    return [{"ad_date": DAY, "campaign_id": "cmp-1", "campaign_type": "SHOPPING",
             "adgroup_id": "grp-1", "ad_id": a, "imp": i, "clk": c, "cost": co, "rank_sum": 0,
             "conv_direct_cnt": 0, "conv_indirect_cnt": 0, "conv_direct_amt": rev,
             "conv_indirect_amt": 0, "cart_direct_cnt": 0, "cart_indirect_cnt": 0,
             "cart_direct_amt": 0, "cart_indirect_amt": 0} for a, i, c, co, rev in specs]


def test_sync_is_idempotent(db):
    ad_creative_daily_sync.sync(db, date_from=DAY, date_to=DAY, rows=_rows(("nad-A", 10, 1, 100, 5000)))
    res = ad_creative_daily_sync.sync(db, date_from=DAY, date_to=DAY,
                                      rows=_rows(("nad-A", 20, 2, 300, 9000)))
    assert (res["inserted"], res["updated"]) == (0, 1)
    row = db.query(NaverAdCreativeDaily).one()
    assert (row.cost, row.imp, row.conv_direct_amt) == (300, 20, 9000)


def test_same_ad_across_adgroups_is_summed_not_overwritten(db):
    """★2026-08-04 라이브 대조가 잡은 결함: 같은 (날짜·소재)로 여러 행이 떨어지면 초판은
    **덮어써서 뒤 행만 남겼다**. 수집 grain은 그룹까지 포함하므로 소재가 그룹을 걸치면 흔하다.
    비용·클릭이 0인 행에서만 티가 나서 **대조가 없었으면 못 봤다**(노출만 13·4씩 사라짐)."""
    rows = _rows(("nad-A", 10, 1, 100, 5000))
    rows.append({**rows[0], "adgroup_id": "grp-2", "imp": 7, "clk": 0, "cost": 0,
                 "conv_direct_amt": 0})
    ad_creative_daily_sync.sync(db, date_from=DAY, date_to=DAY, rows=rows)
    row = db.query(NaverAdCreativeDaily).one()
    assert (row.imp, row.cost) == (17, 100)


def test_unattributed_rows_are_kept_not_dropped(db):
    """소재 귀속이 없는 행(`ad_id='-'`, 노출만 있고 비용 0)도 담는다 — 버리면 그만큼 대조가
    어긋나고, 그 어긋남은 '수집이 새고 있다'와 구분되지 않는다."""
    rows = _rows(("nad-A", 10, 1, 100, 0))
    rows.append({**rows[0], "ad_id": "-", "imp": 13, "clk": 0, "cost": 0})
    rows.append({**rows[0], "ad_id": "", "imp": 5, "clk": 0, "cost": 0})  # 빈 값도 같은 키로
    _daily(db, imp=28, clk=1, cost=100, rev=0)
    res = ad_creative_daily_sync.sync(db, date_from=DAY, date_to=DAY, rows=rows)
    unattr = db.query(NaverAdCreativeDaily).filter(
        NaverAdCreativeDaily.ad_id == ad_creative_daily_sync.AD_ID_UNATTRIBUTED
    ).one()
    assert unattr.imp == 18
    assert res["reconcile"][0]["ok"] is True  # 버리지 않아야 합계가 맞는다


# ══════════════════════════════════════════════════════════════════
# (3) ★대조 — 계약의 핵심 증거
# ══════════════════════════════════════════════════════════════════
def _daily(db, *, adgroup="grp-1", keyword="", imp=0, clk=0, cost=0, rev=0):
    db.add(NaverAdDaily(ad_date=DAY, campaign_id="cmp-1", adgroup_id=adgroup, keyword_id=keyword,
                        imp=imp, clk=clk, cost=cost, conv_direct_amt=rev))
    db.commit()


def test_reconcile_matches_when_totals_agree(db):
    _daily(db, imp=30, clk=3, cost=400, rev=14000)
    res = ad_creative_daily_sync.sync(
        db, date_from=DAY, date_to=DAY,
        rows=_rows(("nad-A", 10, 1, 100, 5000), ("nad-B", 20, 2, 300, 9000)),
    )
    assert res["reconcile"][0]["ok"] is True


def test_reconcile_excludes_the_sentinel_row(db):
    """★센티넬은 `adgroup_id` 칼럼에 산다 — `keyword_id`로 착각하면 그룹합이 2배가 되고
    대조가 늘 실패한다. 2026-08-04에 사람이 정확히 그 오독을 했다(69,912 → 139,824)."""
    _daily(db, imp=30, clk=3, cost=400, rev=14000)
    _daily(db, adgroup=ad_creative_daily_sync.SENTINEL_ADGROUP, imp=30, clk=3, cost=400, rev=14000)
    res = ad_creative_daily_sync.sync(
        db, date_from=DAY, date_to=DAY,
        rows=_rows(("nad-A", 10, 1, 100, 5000), ("nad-B", 20, 2, 300, 9000)),
    )
    assert res["reconcile"][0]["ok"] is True, res["reconcile"][0]["diff"]


def test_reconcile_reports_mismatch_without_raising(db):
    """불일치는 남겨서 보되 예외로 던지지 않는다 — 지우면 조사할 원자료가 사라진다."""
    _daily(db, imp=30, clk=3, cost=999, rev=14000)
    res = ad_creative_daily_sync.sync(
        db, date_from=DAY, date_to=DAY,
        rows=_rows(("nad-A", 10, 1, 100, 5000), ("nad-B", 20, 2, 300, 9000)),
    )
    r = res["reconcile"][0]
    assert r["ok"] is False and r["diff"]["cost"] == (400, 999)
    assert db.query(NaverAdCreativeDaily).count() == 2  # 적재는 살아 있다


def test_reconcile_covers_a_day_with_zero_creative_rows(db):
    """★소재 행이 0인 날도 대조한다 — 이걸 건너뛰면 '수집이 통째로 샌 날'이 초록으로 보인다.

    도달 경로: 어느 날 AD 보고서 생성이 실패하면 그날 수집 결과가 빈다. 그런데
    `ingest_ad_daily`는 **행이 있는 날짜만** 스냅샷 교체하므로 `naver_ad_daily`에는
    **이전 회차의 그날 행이 그대로 남는다.** 즉 그룹합 > 0 · 소재합 0이라는 최악의 어긋남이
    생기는데, 대조 대상 날짜를 '소재 행이 있는 날'에서 뽑으면 그날은 아예 검사되지 않아
    로그가 "대조 0일 중 불일치 0일"이라고 말한다 — 대조가 막으라고 만든 바로 그 상황이다.
    """
    _daily(db, imp=30, clk=3, cost=400, rev=14000)
    res = ad_creative_daily_sync.sync(db, date_from=DAY, date_to=DAY, rows=[])

    assert len(res["reconcile"]) == 1, "소재 행이 없어도 그날은 대조 대상이다"
    r = res["reconcile"][0]
    assert r["day"] == DAY.isoformat()
    assert r["ok"] is False and r["diff"]["cost"] == (0, 400)


def test_reconcile_is_silent_when_both_sides_are_empty(db):
    """양쪽 다 비어 있으면 불일치가 아니다 — 없는 날을 매번 경보로 만들지 않는다."""
    res = ad_creative_daily_sync.sync(db, date_from=DAY, date_to=DAY, rows=[])
    assert [r["ok"] for r in res["reconcile"]] == [True]


def test_two_reports_share_the_identifier_columns():
    """★`_grain_key`는 **AD 보고서 상수**(COL_*)로 AD_CONVERSION 행도 읽는다 — 두 보고서의
    식별자 컬럼 배치가 같다는 라이브 실측(2026-08-04)에 기댄 설계다.

    그 결과 `CONV_COL_CAMPAIGN/ADGROUP/KEYWORD`는 **아무도 안 쓰는 상수**가 됐다. 전환 보고서
    배치가 언젠가 바뀌어 누가 그 상수만 고치면 파서는 하나도 안 바뀌고, 전환이 엉뚱한 키로
    합산돼 **매출이 조용히 어긋난다**(BEP·ROAS가 전부 이 축 위에 있다). 그래서 '같다'는 전제를
    주석이 아니라 테스트로 잡아 둔다 — 어긋나는 순간 여기서 깨진다.
    """
    assert (fetcher.CONV_COL_DATE, fetcher.CONV_COL_CAMPAIGN,
            fetcher.CONV_COL_ADGROUP, fetcher.CONV_COL_KEYWORD) == (
        fetcher.COL_DATE, fetcher.COL_CAMPAIGN_ID,
        fetcher.COL_ADGROUP_ID, fetcher.COL_KEYWORD_ID)
    # 소재 컬럼은 길이 가드 안쪽이어야 한다(두 보고서 모두 인덱스 접근 전에 걸러진다).
    assert fetcher.COL_AD_ID < fetcher.COL_RANK_SUM
    assert fetcher.COL_AD_ID < fetcher.CONV_COL_VALUE
