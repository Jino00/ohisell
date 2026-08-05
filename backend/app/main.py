# main.py — FastAPI 앱 엔트리포인트
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import ad_costs, channels, oauth, orders, products, sync
from app.routers import dashboard, scheduler, settlements, manual_revenue
from app.routers import coupang_report, overview, coupons, p6_meta, coupang_ops, naver_ops
from app.routers import naver_ad, monthly_fixed_cost

# ── 앱 로깅 구성(D-NAO-85 관측 갭①) ──────────────────────────────────────────────
# 그동안 이 앱은 로깅 레벨/핸들러를 전혀 구성하지 않아, root logger가 기본 WARNING인 채로 남았다.
# uvicorn은 자기 네임스페이스 로거(uvicorn/uvicorn.access)만 구성하므로 app.* 로거의 INFO는
# lastResort(WARNING+)로 소실됐다 — scheduler_service의 시간당 레인 `reviewed=...` 카운터,
# explored_ghost_hold 등 관측 로그가 prod pm2 로그에 전 기간 미출력.
#
# basicConfig는 root에 핸들러가 이미 있으면 no-op(pytest 캡처·직접 dictConfig 환경 무간섭)이라,
# 실제로 root가 비어 있는 prod(python -m uvicorn app.main:app) 경로에서만 INFO 핸들러를 건다.
# uvicorn/uvicorn.access는 propagate=False(uvicorn 0.34.2 확인)라 root 핸들러로 중복 출력되지
# 않고, app.* 로거는 propagate를 유지(caplog 포함)해 그대로 root로 전파돼 출력된다.
# 레벨은 OHISELL_LOG_LEVEL 환경변수로 재정의 가능(기본 INFO).
logging.basicConfig(
    level=os.getenv("OHISELL_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작/종료 시 스케줄러 관리.

    ★스케줄러는 **리더 프로세스에서만** 돈다(scheduler_leader). 무중단 배포(블루-그린)는
    신·구 프로세스가 수십 초 겹치는데, 둘 다 스케줄러를 켜면 그 창의 크론이 두 번 발화한다.
    standby로 뜬 프로세스는 HTTP만 서빙하다가 구 프로세스가 종료되면 자동 승격한다.
    """
    from app.services import scheduler_leader
    from app.services.scheduler_service import start_scheduler, stop_scheduler

    try:
        if scheduler_leader.start_when_leader(start_scheduler):
            log.info("스케줄러 시작 완료(리더)")
        else:
            log.info("스케줄러 미기동 — standby(리더 승격 대기 중)")
    except Exception as e:
        log.error("스케줄러 시작 실패: %s", e)
    yield
    try:
        scheduler_leader.release()  # 먼저 락을 놓아 후임이 즉시 승격하게 한다
        stop_scheduler()
        log.info("스케줄러 종료 완료")
    except Exception as e:
        log.error("스케줄러 종료 실패: %s", e)


app = FastAPI(title="ohisell API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(channels.router)
app.include_router(products.router)
app.include_router(orders.router)
app.include_router(sync.router)
app.include_router(ad_costs.router)
app.include_router(dashboard.router)
app.include_router(settlements.router)
app.include_router(scheduler.router)
app.include_router(oauth.router)
app.include_router(manual_revenue.router)
app.include_router(coupang_report.router)
app.include_router(overview.router)
app.include_router(coupons.router)
app.include_router(p6_meta.router)
app.include_router(coupang_ops.router)
app.include_router(naver_ops.router)
app.include_router(naver_ad.router)
app.include_router(monthly_fixed_cost.router)


@app.get("/health")
def health_check():
    """라이브니스 + 스케줄러 리더 여부.

    ★DB를 건드리지 않는다 — 블루-그린 배포에서 신 프로세스의 '트래픽 받을 준비' 판정에
    쓰이므로, DB 지연이 헬스체크 실패로 둔갑하면 배포가 멈춘다.
    scheduler_leader는 배포 스크립트가 "구 프로세스 종료 후 신 프로세스가 스케줄러를
    이어받았다"를 라이브로 확인하는 유일한 표면이다(로그 대신 API로 판정).
    """
    from app.services import scheduler_leader

    return {
        "status": "ok",
        "pid": os.getpid(),
        "scheduler_leader": scheduler_leader.is_leader(),
    }
