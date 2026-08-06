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


# ══════════════════════════════════════════════════════════════════
# 2026-08-06 적대 리뷰 — 판정 대상을 데이터에서 **수집기**로 옮긴다
#
# 왜: `ad_costs`의 '행 없음'은 「그날 소진 0」과 「수집 실패」를 **겸한다**. 그래서 날짜로
#   판정하면 반드시 한쪽으로 틀린다 — 소스별로 보면 거짓 빨강, 계열 합집합으로 보면
#   **거짓 초록**(형제 소스가 죽어도 초록). 위 ①②는 거짓 빨강을 없앤 대신 거짓 초록을 만들었다.
#   "우리가 물어봤는가"(scheduler_state.last_run_at)는 그것과 독립이라 둘 다 안 틀린다.
# ══════════════════════════════════════════════════════════════════
from datetime import timedelta

from app.models import SchedulerState
from app.routers.ad_costs import _GFA_SYNC_JOB
from app.utils.kst import kst_now


def _job(db, *, hours_ago: float | None = 1.0, status: str = "ok", enabled: bool = True,
         error: str | None = None) -> None:
    last = None if hours_ago is None else kst_now().replace(tzinfo=None) - timedelta(hours=hours_ago)
    db.add(SchedulerState(job_name=_GFA_SYNC_JOB, cron_expression="10 7 * * *",
                          is_enabled=enabled, last_run_at=last, last_status=status,
                          last_status_at=last, last_error=error))
    db.commit()


def test_zero_spend_day_is_not_a_failure(db):
    """★거짓 빨강 금지 — 잡이 방금 성공했으면 그 소스 행이 없는 건 '소진 0'이다.

    라이브 실측(2026-08-05): advoost가 30k→5.9k→행 없음으로 잦아들고 da만 적재됐다.
    같은 날 잡은 정상 성공했다 → 관측했는데 소진이 없었다는 뜻이다.
    """
    _put(db, "2026-08-04", "5925", "gfa:advoost")
    _put(db, "2026-08-05", "23030", "gfa:da")      # advoost는 이 날 행이 없다
    _job(db, hours_ago=4)

    out = get_gfa_status(db)
    assert out["collection"]["stale"] is False      # 초록이 맞다
    assert out["collection"]["last_status"] == "ok"
    # 소스별 날짜는 **사실 진술**로 그대로 남는다(판정에는 안 쓴다).
    by = {s["source"]: s for s in out["by_source"]}
    assert by["gfa:advoost"]["date_to"] == "2026-08-04"
    assert by["gfa:da"]["date_to"] == "2026-08-05"


def test_dead_collector_is_red_even_when_data_looks_fresh(db):
    """★★거짓 초록 금지 — 이게 이번 수정의 핵심이다.

    데이터는 어제까지 있어 예전 판정(계열 MAX)으로는 초록인데, 잡은 사흘째 실패다.
    """
    _put(db, "2026-08-05", "23030", "gfa:da")
    _job(db, hours_ago=72, status="error", error="bizmoney 401")

    out = get_gfa_status(db)
    assert out["date_to"] == "2026-08-05"           # 데이터는 '최신'으로 보인다
    assert out["collection"]["stale"] is True       # 그래도 빨강이다
    assert "실패" in out["collection"]["reason"]
    assert out["collection"]["last_error"] == "bizmoney 401"


def test_skipped_run_is_red(db):
    """하루 1회 잡이 만 하루+여유를 넘겨 성공이 없으면 최소 한 번은 건너뛴 것이다."""
    _put(db, "2026-08-05", "23030", "gfa:da")
    _job(db, hours_ago=40)                          # 임계 30시간 초과
    out = get_gfa_status(db)
    assert out["collection"]["stale"] is True
    assert "마지막 성공" in out["collection"]["reason"]


def test_disabled_job_is_red(db):
    """비활성화된 잡은 '조용히 안 도는' 상태다 — 데이터가 남아 있어도 빨강."""
    _put(db, "2026-08-05", "23030", "gfa:da")
    _job(db, hours_ago=1, enabled=False)
    out = get_gfa_status(db)
    assert out["collection"]["stale"] is True
    assert "비활성화" in out["collection"]["reason"]


def test_unregistered_job_is_not_green(db):
    """판정 근거가 아예 없으면 초록으로 넘기지 않는다 — 모르면 모른다고 한다."""
    _put(db, "2026-08-05", "23030", "gfa:da")       # 잡 행 자체가 없다
    out = get_gfa_status(db)
    assert out["collection"]["registered"] is False
    assert out["collection"]["stale"] is True


def test_never_succeeded_is_red(db):
    _put(db, "2026-08-05", "23030", "gfa:da")
    _job(db, hours_ago=None, status=None)
    out = get_gfa_status(db)
    assert out["collection"]["stale"] is True
    assert "한 번도" in out["collection"]["reason"]
