# test_naver_display_ad_costs.py — ADVoost 쇼핑·GFA 광고비(비즈머니 실차감) 적재 가드
#
# 가드 대상(2026-08-03 라이브 발견): 검색광고 리포트가 못 덮는 ADVoost·GFA 광고비가
#   수동 CSV 업로드에만 의존하다 06-04에 멈춰 **59일간 4,881,848원이 손익에서 누락**됐다.
# 이 파일이 고정하는 두 가지 실패 모드:
#   ① 이중계상 — NCC를 함께 담거나(이미 naver_sa:*로 적재 중), 수동 CSV가 채운 날짜에
#      덮어쓰면 그날 광고비가 2배가 된다. 라이브 실측상 CSV의 'ADVoost 쇼핑' 컬럼은
#      PMAX(+05-29부터 GFA)와 원 단위로 같은 돈이다.
#   ② 부호·타임존 — 차감은 음수로 오고 settleDt는 unix ms다. 그대로 담으면 광고비가
#      음수가 되고, UTC로 읽으면 KST 09:00 이전 차감이 전날로 밀린다.
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Channel
from app.services import naver_display_ad_costs as svc
from app.services.naver_display_ad_costs import (
    LEGACY_CSV_SOURCE,
    SOURCE_ADVOOST,
    SOURCE_DISPLAY,
    sync_display_ad_costs,
)
from app.services.naver_sa_ad_fetcher import fetch_bizmoney_exhaust_daily
from app.services.profit_calculator import _get_gfa_ad_spend_daily

D = Decimal
NAVER_ID = 6

# KST 2026-07-05 00:00 = unix ms. exhaust의 settleDt는 그날의 KST 자정으로 온다(라이브 실측).
TS_0705 = 1783177200000
TS_0706 = TS_0705 + 86_400_000


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    s.add(Channel(id=NAVER_ID, code="NAVER", name="네이버 스마트스토어",
                  platform="naver", channel_type="own"))
    s.commit()
    try:
        yield s
    finally:
        s.close()


def _row(prod, amt, ts=TS_0705, *, free=0):
    """exhaust 응답 1행. 차감이라 금액은 음수로 온다."""
    return {"customerId": 1313769, "activityCd": 40171, "campaignTp": 0,
            "prodInfoCd": prod, "settleDt": ts,
            "useRefundableAmt": -amt, "useNonrefundableAmt": -free}


def _stub_fetch(monkeypatch, payload):
    class _Resp:
        status_code = 200
        text = "[]"

        def raise_for_status(self):
            return None

    def fake_get(path, params=None):
        assert path == "/billing/bizmoney/histories/exhaust"
        return _Resp()

    monkeypatch.setattr("app.services.naver_sa_ad_fetcher.ACCESS_LICENSE", "k")
    monkeypatch.setattr("app.services.naver_sa_ad_fetcher.SECRET_KEY_B64", "s")
    monkeypatch.setattr("app.services.naver_sa_ad_fetcher._get", fake_get)
    monkeypatch.setattr("app.services.naver_sa_ad_fetcher._json_list", lambda r: payload)


def _stub_daily(monkeypatch, daily):
    monkeypatch.setattr(svc, "fetch_bizmoney_exhaust_daily", lambda a, b: daily)


def _legacy_csv(db, day, amount):
    db.execute(
        text("""INSERT INTO ad_costs (channel_id, product_id, ad_date, ad_spend, ad_revenue, source)
                VALUES (:cid, NULL, :dt, :sp, NULL, :src)"""),
        {"cid": NAVER_ID, "dt": day, "sp": str(amount), "src": LEGACY_CSV_SOURCE},
    )
    db.commit()


def _spend(db, source=None):
    q = "SELECT COALESCE(SUM(CAST(ad_spend AS REAL)), 0) FROM ad_costs WHERE channel_id = :cid"
    p = {"cid": NAVER_ID}
    if source:
        q += " AND source = :src"
        p["src"] = source
    return D(str(db.execute(text(q), p).scalar()))


# ── fetch: 파싱 ────────────────────────────────────────────────────────────
def test_fetch_flips_sign_and_splits_by_product(monkeypatch):
    _stub_fetch(monkeypatch, [_row("PMAX", 806651), _row("GFA", 506554), _row("NCC", 19723213)])
    out = fetch_bizmoney_exhaust_daily(date(2026, 7, 5), date(2026, 7, 5))
    assert out == {"2026-07-05": {"PMAX": D("806651"), "GFA": D("506554"),
                                  "NCC": D("19723213")}}


def test_fetch_sums_refundable_and_free_money(monkeypatch):
    # 무상 비즈머니(useNonrefundableAmt)로 빠진 돈도 광고비다 — 한쪽만 읽으면 과소계상.
    _stub_fetch(monkeypatch, [_row("PMAX", 100, free=25)])
    out = fetch_bizmoney_exhaust_daily(date(2026, 7, 5), date(2026, 7, 5))
    assert out["2026-07-05"]["PMAX"] == D("125")


def test_fetch_groups_by_kst_day(monkeypatch):
    _stub_fetch(monkeypatch, [_row("PMAX", 10, TS_0705), _row("PMAX", 20, TS_0706)])
    out = fetch_bizmoney_exhaust_daily(date(2026, 7, 5), date(2026, 7, 6))
    assert out == {"2026-07-05": {"PMAX": D("10")}, "2026-07-06": {"PMAX": D("20")}}


def test_fetch_skips_unparsable_rows_instead_of_crashing(monkeypatch):
    _stub_fetch(monkeypatch, [
        {"prodInfoCd": "PMAX"},                                  # settleDt 결측
        {"settleDt": TS_0705},                                   # prodInfoCd 결측
        {"prodInfoCd": "PMAX", "settleDt": "not-a-timestamp"},   # 파싱 불가
        _row("PMAX", 500),
    ])
    out = fetch_bizmoney_exhaust_daily(date(2026, 7, 5), date(2026, 7, 5))
    assert out == {"2026-07-05": {"PMAX": D("500")}}


def test_fetch_returns_empty_without_credentials(monkeypatch):
    monkeypatch.setattr("app.services.naver_sa_ad_fetcher.ACCESS_LICENSE", "")
    monkeypatch.setattr("app.services.naver_sa_ad_fetcher.SECRET_KEY_B64", "")
    assert fetch_bizmoney_exhaust_daily(date(2026, 7, 5), date(2026, 7, 5)) == {}


# ── sync: 이중계상 방지 ────────────────────────────────────────────────────
def test_search_ad_is_never_written(db, monkeypatch):
    """★NCC는 이미 naver_sa:*로 적재 중이다 — 여기서 담으면 검색광고가 2배가 된다."""
    _stub_daily(monkeypatch, {"2026-07-05": {"NCC": D("19723213"), "PMAX": D("806651")}})
    result = sync_display_ad_costs(db, date(2026, 7, 5), date(2026, 7, 5))
    db.commit()
    assert result["written"] == 1
    assert _spend(db, SOURCE_ADVOOST) == D("806651")
    assert _spend(db) == D("806651")  # NCC 몫은 어디에도 없다


def test_legacy_csv_dates_are_skipped(db, monkeypatch):
    """수동 CSV가 채운 날짜엔 쓰지 않는다 — 그 행이 이미 PMAX+GFA와 같은 돈이다."""
    _legacy_csv(db, "2026-07-05", 155729)
    _stub_daily(monkeypatch, {
        "2026-07-05": {"PMAX": D("141850"), "GFA": D("13880")},
        "2026-07-06": {"PMAX": D("29070")},
    })
    result = sync_display_ad_costs(db, date(2026, 7, 5), date(2026, 7, 6))
    db.commit()
    assert result["skipped_legacy"] == 1
    assert _spend(db, LEGACY_CSV_SOURCE) == D("155729")   # 원본 그대로
    assert _spend(db, SOURCE_ADVOOST) == D("29070")       # 07-06만 들어감
    assert _spend(db) == D("155729") + D("29070")


def test_rerun_is_idempotent(db, monkeypatch):
    _stub_daily(monkeypatch, {"2026-07-05": {"PMAX": D("806651"), "GFA": D("506554")}})
    for _ in range(3):
        sync_display_ad_costs(db, date(2026, 7, 5), date(2026, 7, 5))
        db.commit()
    assert _spend(db) == D("1313205")
    n = db.execute(text("SELECT COUNT(*) FROM ad_costs")).scalar()
    assert n == 2


def test_non_positive_net_deduction_is_not_written(db, monkeypatch):
    """환불·조정으로 순차감이 0 이하인 날 — 음수 광고비를 만들지 않는다."""
    _stub_daily(monkeypatch, {"2026-07-05": {"PMAX": D("-2400"), "GFA": D("0")}})
    result = sync_display_ad_costs(db, date(2026, 7, 5), date(2026, 7, 5))
    db.commit()
    assert result["written"] == 0
    assert _spend(db) == D("0")


def test_dry_run_writes_nothing_but_reports(db, monkeypatch):
    _stub_daily(monkeypatch, {"2026-07-05": {"PMAX": D("806651")}})
    result = sync_display_ad_costs(db, date(2026, 7, 5), date(2026, 7, 5), dry_run=True)
    db.commit()
    assert result["total"] == D("806651")
    assert _spend(db) == D("0")


# ── 손익 배선 ──────────────────────────────────────────────────────────────
def test_profit_engine_reads_both_legacy_and_new_sources(db, monkeypatch):
    """★배선 회귀: 손익 엔진이 `gfa:%` 계열을 한 줄기로 읽어야 06-04 경계에서 끊기지 않는다."""
    _legacy_csv(db, "2026-06-04", 155729)
    _stub_daily(monkeypatch, {"2026-06-05": {"PMAX": D("120000"), "GFA": D("15000")}})
    sync_display_ad_costs(db, date(2026, 6, 5), date(2026, 6, 5))
    db.commit()

    daily = _get_gfa_ad_spend_daily(db, NAVER_ID, date(2026, 6, 1), date(2026, 6, 30))
    assert daily["2026-06-04"] == D("155729")     # 수동 CSV 시절
    assert daily["2026-06-05"] == D("135000")     # 자동 적재분(ADVoost+GFA 합)


def test_sources_share_the_gfa_prefix():
    """접두사가 갈리면 손익 엔진의 `gfa:%` 필터가 신규 행을 통째로 놓친다."""
    assert SOURCE_ADVOOST.startswith("gfa:")
    assert SOURCE_DISPLAY.startswith("gfa:")
    assert SOURCE_ADVOOST != SOURCE_DISPLAY != LEGACY_CSV_SOURCE
