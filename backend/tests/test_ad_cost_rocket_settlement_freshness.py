# test_ad_cost_rocket_settlement_freshness.py — 쿠팡 광고비·로켓1P 정산이 신선도 감시에
#   실제로 들어와 있는지 지킨다 (2026-08-11 신설).
#
# ## 왜 이 파일이 따로 있나
#
# `DATA_FRESHNESS_RULES`는 애초에 RG 정산 한 곳에만 있었고, 그 결과 쿠팡 Wing 판매분석이
# 13일 멈췄는데도 `healthy: true`였다(D-CPP-36, test_vendor_item_axis.py가 그 재발을 막는다).
# 같은 구멍이 광고비·로켓1P 정산에도 있었다 — 라이브 실측(2026-08-11)으로 광고비가 2일,
# 로켓1P 정산이 3일 밀려 있는데 `data_stale=0건`이었다. 이 파일은 `test_cost_drift_wiring.py`
# 와 같은 배선 검증 형태를 따른다: 규칙이 있는가 → `_latest_for_rule`이 맞는 테이블/필터를
# 읽는가 → 임계 판정이 실제로 `data_stale`에 반영되는가.
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import CoupangAdCostDaily, CoupangRocketSettlement
from app.services.coupang.ad_cost_sync import _SALES_KEY
from app.services.coupang.rocket_1p_channel_pnl import ROCKET_1P_VENDOR_ID
from app.services.scheduler_health import (
    DATA_FRESHNESS_RULES,
    _latest_for_rule,
    build_health,
    compute_scheduler_health,
)

NOW = datetime(2026, 8, 11, 12, 0, 0)


@pytest.fixture
def db():
    # StaticPool + check_same_thread=False: TestClient가 라우트를 다른 스레드에서 돌리므로
    # 같은 연결을 공유해야 픽스처가 심은 행이 라우트에서 보인다(빈 인메모리 DB «no such table» 방지,
    # test_cost_drift_wiring.py·test_vendor_item_axis.py와 동일 패턴).
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


class _FakeScheduler:
    running = True

    def get_jobs(self):
        return []


def _add_ad_cost(db, cost_date_str: str, vendor_id: str):
    db.add(CoupangAdCostDaily(
        cost_date=date.fromisoformat(cost_date_str),
        vendor_id=vendor_id,
        day_cost=1000,
        all_day_cost=1000,
        conv_sales=5000,
        month_cost=0,
    ))
    db.commit()


def _add_rocket_settlement(db, invoice_seq: int, issue_date_str: str, vendor_id: str):
    db.add(CoupangRocketSettlement(
        invoice_seq=invoice_seq,
        vendor_id=vendor_id,
        issue_date=date.fromisoformat(issue_date_str),
    ))
    db.commit()


# ═══ ① 규칙 존재 단언 — 목록에 없으면 배너는 조용하다 ═══


def test_freshness_rules_actually_cover_ad_cost_and_rocket_settlement():
    """★★목록 부재 재발 방지. 「감시선이 있다」는 「이 파이프라인을 본다」는 뜻이 아니다."""
    names = {r["name"] for r in DATA_FRESHNESS_RULES}
    assert "coupang_ad_cost_sales" in names, "쿠팡 광고비가 신선도 판정에 없다"
    assert "rocket_1p_settlement" in names, "로켓1P 정산이 신선도 판정에 없다"

    ad_cost_rule = next(r for r in DATA_FRESHNESS_RULES if r["name"] == "coupang_ad_cost_sales")
    rocket_rule = next(r for r in DATA_FRESHNESS_RULES if r["name"] == "rocket_1p_settlement")
    assert ad_cost_rule["source"] == "ad_cost"
    assert ad_cost_rule["max_age_days"] == 3.0
    assert rocket_rule["source"] == "rocket_settlement"
    assert rocket_rule["max_age_days"] == 10.0


# ═══ ② _latest_for_rule이 실제로 올바른 테이블/필터를 읽는가 ═══


def test_ad_cost_freshness_reads_sales_key_only(db):
    """★report/SALES 축(vendor_id=_SALES_KEY)만 본다 — vendor별 축은 대상이 아니다.

    다른 vendor_id에 더 최신 행을 심어, 필터가 실제로 걸리는지 본다(필터가 없으면
    이 테스트가 더 최신인 다른 vendor_id 값을 잘못 돌려줘 죽는다).
    """
    _add_ad_cost(db, "2026-08-08", _SALES_KEY)
    _add_ad_cost(db, "2026-08-11", "OTHER_VENDOR")  # 더 최신이지만 대상 밖

    rule = next(r for r in DATA_FRESHNESS_RULES if r["name"] == "coupang_ad_cost_sales")
    assert _latest_for_rule(db, rule) == date(2026, 8, 8)


def test_rocket_settlement_freshness_reads_rocket_1p_vendor_only(db):
    """★ROCKET_1P_VENDOR_ID 축만 본다 — 다른 vendor_id 행이 섞이면 안 된다."""
    _add_rocket_settlement(db, 1, "2026-08-05", ROCKET_1P_VENDOR_ID)
    _add_rocket_settlement(db, 2, "2026-08-11", "OTHER_VENDOR")  # 더 최신이지만 대상 밖

    rule = next(r for r in DATA_FRESHNESS_RULES if r["name"] == "rocket_1p_settlement")
    assert _latest_for_rule(db, rule) == date(2026, 8, 5)


# ═══ ③ 임계 판정 — 나이가 넘으면 data_stale, 안 넘으면 조용 ═══


def test_stale_ad_cost_shows_up_in_data_stale(db):
    """★★라이브 재현: 광고비가 2일 밀린 상태(3.0 임계 초과 시나리오는 여유 두고 더 늙힘)."""
    _add_ad_cost(db, "2026-08-06", _SALES_KEY)  # NOW(08-11) 기준 나이 5일 > 3.0
    h = compute_scheduler_health(db, _FakeScheduler(), NOW)
    names = {d["name"] for d in h["data_stale"]}
    assert "coupang_ad_cost_sales" in names
    verdict = next(d for d in h["data_stale"] if d["name"] == "coupang_ad_cost_sales")
    assert verdict["state"] == "stale"
    assert verdict["impact"], "impact 문구가 없으면 배너에 «무엇이 문제인지»가 안 뜬다"


def test_fresh_ad_cost_is_silent(db):
    _add_ad_cost(db, "2026-08-10", _SALES_KEY)  # 나이 1일 < 3.0
    h = compute_scheduler_health(db, _FakeScheduler(), NOW)
    names = {d["name"] for d in h["data_stale"]}
    assert "coupang_ad_cost_sales" not in names


def test_stale_rocket_settlement_shows_up_in_data_stale(db):
    """★★라이브 재현: 로켓1P 정산이 밀린 상태(10.0 임계 초과)."""
    _add_rocket_settlement(db, 1, "2026-07-25", ROCKET_1P_VENDOR_ID)  # 나이 17일 > 10.0
    h = compute_scheduler_health(db, _FakeScheduler(), NOW)
    names = {d["name"] for d in h["data_stale"]}
    assert "rocket_1p_settlement" in names
    verdict = next(d for d in h["data_stale"] if d["name"] == "rocket_1p_settlement")
    assert verdict["state"] == "stale"
    assert verdict["impact"]


def test_fresh_rocket_settlement_is_silent(db):
    _add_rocket_settlement(db, 1, "2026-08-05", ROCKET_1P_VENDOR_ID)  # 나이 6일 < 10.0
    h = compute_scheduler_health(db, _FakeScheduler(), NOW)
    names = {d["name"] for d in h["data_stale"]}
    assert "rocket_1p_settlement" not in names


def test_never_collected_ad_cost_and_rocket_settlement_are_no_data(db):
    """★한 번도 수집 안 된 것도 시끄럽게 잡는다 — «없음»이 «정상»으로 보이면 안 된다."""
    h = compute_scheduler_health(db, _FakeScheduler(), NOW)
    by_name = {d["name"]: d for d in h["data_stale"]}
    assert by_name["coupang_ad_cost_sales"]["state"] == "no_data"
    assert by_name["rocket_1p_settlement"]["state"] == "no_data"


# ═══ 순수층 — build_health 직접 호출로 임계 경계값도 확인 ═══


def _snap(name, account_key, latest, max_age_days, impact="i"):
    return {
        "name": name, "account_key": account_key, "latest": latest,
        "max_age_days": max_age_days, "impact": impact,
    }


def test_build_health_ad_cost_threshold_boundary():
    snaps = [
        _snap("coupang_ad_cost_sales", "COUPANG_WING1", NOW - timedelta(days=2.9), 3.0),
    ]
    h = build_health([], [], set(), True, NOW, data_snapshots=snaps)
    assert h["data_stale"] == []
    assert h["healthy"] is True

    snaps = [
        _snap("coupang_ad_cost_sales", "COUPANG_WING1", NOW - timedelta(days=3.1), 3.0),
    ]
    h = build_health([], [], set(), True, NOW, data_snapshots=snaps)
    assert len(h["data_stale"]) == 1
    assert h["healthy"] is False


def test_build_health_rocket_settlement_threshold_boundary():
    snaps = [
        _snap("rocket_1p_settlement", "COUPANG_WING2", NOW - timedelta(days=9.9), 10.0),
    ]
    h = build_health([], [], set(), True, NOW, data_snapshots=snaps)
    assert h["data_stale"] == []
    assert h["healthy"] is True

    snaps = [
        _snap("rocket_1p_settlement", "COUPANG_WING2", NOW - timedelta(days=10.1), 10.0),
    ]
    h = build_health([], [], set(), True, NOW, data_snapshots=snaps)
    assert len(h["data_stale"]) == 1
    assert h["healthy"] is False
