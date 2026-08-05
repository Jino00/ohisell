# test_gfa_status_endpoint.py — /api/ad-costs/gfa/status 신선도 축 가드
#
# 가드 대상(2026-08-06 라이브 발견): 이 엔드포인트가 `source = 'gfa:쇼핑'`(수동 CSV)만 읽어,
#   2026-08-03부터 비즈머니 실차감 API가 매일 07:10에 적재하고 있는데도 화면 배너는
#   "마지막 업로드 2026-06-04 (63일 전) ⚠️" 라는 **거짓 빨강**을 63일간 띄웠다.
#   금액 자체는 profit_calculator가 `gfa:%`로 읽어 정상 반영 중이었다 — 틀린 건 표면이다.
# 이 파일이 고정하는 두 가지:
#   ① 신선도 축 = 자동+수동 계열 전체의 MAX(ad_date). 수동 소스만 보면 안 된다.
#   ② 소스별 판정 금지 — 소진이 0인 날은 행이 생기지 않는다(naver_display_ad_costs가
#      amount<=0을 건너뜀). 2026-08-05 라이브가 정확히 그런 날(PMAX 0, GFA만 적재)이었고,
#      소스별로 보면 "PMAX 수집이 죽었다"로 오탐한다.
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Channel
from app.routers.ad_costs import _upsert_ad_cost, get_gfa_status

D = Decimal
NAVER_ID = 6


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
    yield s
    s.close()


def _put(db, day: str, spend: str, source: str) -> None:
    _upsert_ad_cost(db, NAVER_ID, date.fromisoformat(day), D(spend), source)
    db.commit()


def test_freshness_follows_auto_ingest_not_manual_csv(db):
    """수동 CSV가 두 달 전에 멈춰도, 자동 적재가 어제까지 왔으면 최신이다."""
    _put(db, "2026-06-04", "150000", "gfa:쇼핑")
    _put(db, "2026-08-05", "23029", "gfa:da")

    out = get_gfa_status(db)

    assert out["date_to"] == "2026-08-05"          # ← 축 전체 = 자동 적재분이 끌어올린다
    assert out["manual"]["date_to"] == "2026-06-04"
    assert out["auto"]["date_to"] == "2026-08-05"
    assert out["total_spend"] == 173029             # 자동+수동 합산


def test_zero_spend_day_does_not_look_like_a_dead_source(db):
    """PMAX 소진 0인 날(행 없음)에도 축의 최신일은 전진한다 — 2026-08-05 라이브 형태."""
    _put(db, "2026-08-04", "5924", "gfa:advoost")
    _put(db, "2026-08-04", "7150", "gfa:da")
    _put(db, "2026-08-05", "23029", "gfa:da")      # advoost 행 없음(소진 0)

    out = get_gfa_status(db)

    assert out["date_to"] == "2026-08-05"
    by_src = {r["source"]: r for r in out["by_source"]}
    assert by_src["gfa:advoost"]["date_to"] == "2026-08-04"   # 소스별로는 하루 뒤처짐
    assert by_src["gfa:da"]["date_to"] == "2026-08-05"


def test_days_counts_dates_not_rows(db):
    """하루에 두 소스가 들어와도 '일수'는 1이다(구 COUNT(*)는 2로 셌다)."""
    _put(db, "2026-08-04", "5924", "gfa:advoost")
    _put(db, "2026-08-04", "7150", "gfa:da")

    out = get_gfa_status(db)

    assert out["days"] == 1
    assert out["auto"]["days"] == 1


def test_empty_db_reports_no_data(db):
    out = get_gfa_status(db)

    assert out["has_data"] is False
    assert out["date_to"] is None
    assert out["auto"]["has_data"] is False
    assert out["by_source"] == []
