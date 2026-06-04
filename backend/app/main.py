# main.py — FastAPI 앱 엔트리포인트
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import ad_costs, channels, oauth, orders, products, sync
from app.routers import dashboard, scheduler, settlements, manual_revenue
from app.routers import coupang_report, overview, coupons, p6_meta, coupang_ops, naver_ops

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작/종료 시 스케줄러 관리"""
    from app.services.scheduler_service import start_scheduler, stop_scheduler

    try:
        start_scheduler()
        log.info("스케줄러 시작 완료")
    except Exception as e:
        log.error("스케줄러 시작 실패: %s", e)
    yield
    try:
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


@app.get("/health")
def health_check():
    return {"status": "ok"}
