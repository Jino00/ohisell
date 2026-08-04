# test_scheduler_catchup_ad_costs.py — 광고비 3잡의 catch-up 편입 계약 (2026-08-03)
#
# ★존재 이유(라이브 사고 재현): 2026-08-03 03:32 KST 디스크 포화(ENOSPC)로 서버 쓰기가
#   3시간 40분 마비됐고, PM2가 07:11:43에야 스택을 되살렸다. 그 사이 05:20~07:00에 예정된
#   자동수집 12개가 통째로 유실됐다.
#   그중 11개는 최근 30~35일 창을 다시 긁는 잡이라 다음 날 스스로 메워진다. 그러나
#   **광고비 3잡은 '어제 하루치만' 적재**하므로 다음 발화가 구멍을 영영 메우지 않는다 —
#   그날 광고비가 손익에서 영구 누락되고 아무 에러도 남지 않는다. 같은 세션에서 발견한
#   ADVoost·GFA 488만원(59일) 누락과 소멸 방식이 정확히 같다.
#   그래서 이 셋을 _CATCHUP_ORDER에 넣었고, 이 파일이 그 계약을 고정한다.
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import SchedulerState
from app.services import scheduler_service

AD_COST_JOBS = (
    "sync_naver_sa_ad_costs",       # 07:00
    "sync_meta_ad_costs",           # 07:00
    "sync_naver_display_ad_costs",  # 07:10
)

# 사고 당일 유실된 12개 중 자가치유되는 쿠팡 잡들 — blast radius 제외를 유지해야 한다.
SELF_HEALING_JOBS = (
    "sync_coupang_rg_inbound",
    "sync_coupang_products",
    "sync_coupang_rg_sizes",
    "sync_coupang_rg_inventory",
    "sync_coupang_returns",
    "sync_coupang_settlement",
    "sync_coupang_rg_orders",
    "sync_coupang_cs",
    "sync_coupang_coupons",
)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _seed(db, rows):
    for name, cron, last_run in rows:
        db.add(SchedulerState(
            job_name=name, cron_expression=cron, is_enabled=True, last_run_at=last_run
        ))
    db.commit()


# ── ① 사고 재현: 07:11 재시작 시점에 광고비 3잡이 '놓친 잡'으로 잡혀야 한다 ──────────
def test_ad_cost_jobs_detected_as_missed_at_incident_restart_time(db):
    """08-03 07:11:43 KST 복귀 — 07:00/07:10 광고비 잡은 어제가 마지막 성공이었다."""
    now = datetime(2026, 8, 3, 7, 11, 43)
    yesterday = now - timedelta(days=1)
    _seed(db, [
        ("sync_naver_sa_ad_costs", "0 7 * * *", yesterday.replace(hour=7, minute=0)),
        ("sync_meta_ad_costs", "0 7 * * *", yesterday.replace(hour=7, minute=0)),
        ("sync_naver_display_ad_costs", "10 7 * * *", yesterday.replace(hour=7, minute=10)),
    ])
    missed = scheduler_service._missed_morning_jobs(db, now)
    for job in AD_COST_JOBS:
        assert job in missed, f"{job}이 catch-up 대상으로 안 잡힘 — 그날 광고비가 영구 누락된다"


# ── ② 이미 오늘 성공한 잡은 다시 돌리지 않는다(중복 적재 방지) ─────────────────────
def test_ad_cost_jobs_not_recatchup_when_already_ran_today(db):
    now = datetime(2026, 8, 3, 7, 11, 43)
    _seed(db, [
        ("sync_naver_sa_ad_costs", "0 7 * * *", now.replace(hour=7, minute=0, second=5)),
        ("sync_meta_ad_costs", "0 7 * * *", now.replace(hour=7, minute=0, second=9)),
        # 07:10 발화는 07:11 시점에 아직 안 돈 상태(어제가 마지막) → 이건 잡혀야 한다
        ("sync_naver_display_ad_costs", "10 7 * * *",
         (now - timedelta(days=1)).replace(hour=7, minute=10)),
    ])
    missed = scheduler_service._missed_morning_jobs(db, now)
    assert "sync_naver_sa_ad_costs" not in missed
    assert "sync_meta_ad_costs" not in missed
    assert "sync_naver_display_ad_costs" in missed


# ── ③ 아직 예정 시각 전이면 '놓친 것'이 아니다 ────────────────────────────────────
def test_ad_cost_jobs_not_missed_before_scheduled_time(db):
    now = datetime(2026, 8, 3, 6, 30, 0)  # 07:00 이전
    yesterday = now - timedelta(days=1)
    _seed(db, [(j, c, yesterday.replace(hour=7, minute=0))
               for j, c in zip(AD_COST_JOBS, ("0 7 * * *", "0 7 * * *", "10 7 * * *"))])
    assert scheduler_service._missed_morning_jobs(db, now) == []


# ── ④ 순서 계약: 정산 → 광고비 → 나머지 (회계 입력이 하류 관찰·집행보다 앞) ─────────
def test_catchup_order_ad_costs_after_settlement_before_downstream():
    order = scheduler_service._CATCHUP_ORDER
    idx = {name: i for i, name in enumerate(order)}
    for job in AD_COST_JOBS:
        assert job in idx, f"{job}이 _CATCHUP_ORDER에 없다"
    assert idx["sync_naver_case_settlement"] < idx["sync_naver_sa_ad_costs"]
    assert idx["sync_naver_display_ad_costs"] < idx["shopping_ad_product_sync"]
    # 07:00 두 잡이 07:10 잡보다 앞(cron 순서 = 의존 순서 관례 유지)
    assert idx["sync_naver_sa_ad_costs"] < idx["sync_naver_display_ad_costs"]
    assert idx["sync_meta_ad_costs"] < idx["sync_naver_display_ad_costs"]


# ── ⑤ 비블록 계약: 외부 API 장애가 광고 자동운영 복구 전체를 막으면 안 된다 ──────────
def test_ad_cost_jobs_are_non_blocking():
    for job in AD_COST_JOBS:
        assert job in scheduler_service._CATCHUP_NON_BLOCKING, (
            f"{job}이 블로킹이면 네이버 API 장애 하나가 하류 집행 복구를 통째로 막는다"
        )


# ── ⑥ blast radius 유지: 자가치유되는 쿠팡 잡은 넣지 않는다 ───────────────────────
def test_self_healing_coupang_jobs_stay_excluded():
    """목록의 기준은 '중요도'가 아니라 '복구 가능성'이다 — 중요도로 고르면 목록이 무한히 자란다."""
    for job in SELF_HEALING_JOBS:
        assert job not in scheduler_service._CATCHUP_ORDER, (
            f"{job}은 30~35일 창을 다시 긁어 자가치유된다 — catch-up에 넣을 이유가 없다"
        )
