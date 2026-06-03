# routers/scheduler.py — 스케줄러 상태 및 제어 API
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import SchedulerState
from app.schemas import SchedulerJobOut, SchedulerStatusOut
from app.services.scheduler_service import scheduler

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


@router.get("/status", response_model=SchedulerStatusOut)
def scheduler_status(db: Session = Depends(get_db)):
    """스케줄러 실행 상태 및 작업 목록"""
    is_running = scheduler.running

    jobs = []
    states = db.query(SchedulerState).all()
    for state in states:
        # APScheduler에서 실제 next_run_time 조회
        ap_job = scheduler.get_job(state.job_name) if is_running else None
        next_run = None
        if ap_job and ap_job.next_run_time:
            next_run = ap_job.next_run_time.isoformat()
        elif state.next_run_at:
            next_run = state.next_run_at.isoformat()

        jobs.append(SchedulerJobOut(
            id=state.job_name,
            name=state.job_name,
            next_run_time=next_run,
            is_enabled=state.is_enabled,
        ))

    return SchedulerStatusOut(is_running=is_running, jobs=jobs)


@router.post("/trigger/{job_id}")
def trigger_job(job_id: str, db: Session = Depends(get_db)):
    """작업 즉시 실행"""
    state = db.query(SchedulerState).filter(SchedulerState.job_name == job_id).first()
    if not state:
        raise HTTPException(status_code=404, detail=f"작업을 찾을 수 없습니다: {job_id}")

    # 즉시 실행
    from app.services.scheduler_service import (
        recalculate_profit_job,
        sync_all_channels_job,
        sync_coupang_products_job,
        sync_coupang_returns_job,
    )

    job_map = {
        "auto_sync_orders": sync_all_channels_job,
        "auto_profit_calc": recalculate_profit_job,
        "sync_coupang_products": sync_coupang_products_job,
        "sync_coupang_returns": sync_coupang_returns_job,
    }

    func = job_map.get(job_id)
    if not func:
        raise HTTPException(status_code=400, detail=f"실행할 수 없는 작업입니다: {job_id}")

    try:
        func()
        state.last_run_at = datetime.now()
        db.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"작업 실행 에러: {e}")

    return {"detail": f"작업 실행 완료: {job_id}"}


@router.put("/toggle/{job_id}")
def toggle_job(job_id: str, db: Session = Depends(get_db)):
    """작업 일시정지/재개"""
    state = db.query(SchedulerState).filter(SchedulerState.job_name == job_id).first()
    if not state:
        raise HTTPException(status_code=404, detail=f"작업을 찾을 수 없습니다: {job_id}")

    state.is_enabled = not state.is_enabled
    db.commit()

    # APScheduler에서 실제로 pause/resume
    if scheduler.running:
        try:
            if state.is_enabled:
                scheduler.resume_job(job_id)
            else:
                scheduler.pause_job(job_id)
        except Exception as e:
            # 작업이 등록되지 않은 경우 무시
            pass

    action = "재개" if state.is_enabled else "일시정지"
    return {"detail": f"작업 {action}: {job_id}", "is_enabled": state.is_enabled}
