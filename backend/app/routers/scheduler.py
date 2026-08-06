# routers/scheduler.py — 스케줄러 상태 및 제어 API
from __future__ import annotations

import logging

from app.utils.kst import kst_now, kst_today
from datetime import datetime

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import SchedulerState
from app.schemas import SchedulerHealthOut, SchedulerJobOut, SchedulerStatusOut
from app.services.scheduler_service import job_func_for, job_kwargs_for, scheduler

log = logging.getLogger(__name__)

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


@router.get("/health", response_model=SchedulerHealthOut)
def scheduler_health(db: Session = Depends(get_db)):
    """워치독 헬스 — 필수 잡의 실패/stale/미등록/스케줄러 정지를 1급 신호로 노출(S5b S4).

    HTTP는 항상 200, body의 healthy:bool로 판정한다(Mac 페처가 폴링). 에러는 sanitized 한 줄
    요약만 노출하고 전체 traceback은 DB에만 남긴다(누출 방지). 읽기 전용·머니로직 불변.
    """
    from app.services.scheduler_health import compute_scheduler_health

    return compute_scheduler_health(db, scheduler, kst_now())


@router.post("/trigger/{job_id}")
def trigger_job(job_id: str, db: Session = Depends(get_db)):
    """작업 즉시 실행"""
    state = db.query(SchedulerState).filter(SchedulerState.job_name == job_id).first()
    if not state:
        raise HTTPException(status_code=404, detail=f"작업을 찾을 수 없습니다: {job_id}")

    # 즉시 실행 — ★매핑은 `job_func_for` **하나만** 본다.
    # 종전에는 이 라우터가 자기 job_map(11개)을 따로 들고 있어, 스케줄러에 등록된 잡인데도
    # 수동 실행은 "실행할 수 없는 작업입니다"로 거부되는 잡이 있었다(예: snapshot_naver_ad_hourly).
    # 이건 `job_func_for` 주석이 경고한 바로 그 실패 모드다 — "한쪽만 알면 toggle이 재시작 없이
    # 살릴 잡을 못 찾아 DB만 바뀌고 실제 미가동(쿠팡 광고비 13일 정지의 뿌리)". 부분집합 복제는
    # 시간이 지날수록 어긋나기만 한다.
    from app.services.scheduler_service import job_func_for

    func = job_func_for(job_id)
    if not func:
        raise HTTPException(status_code=400, detail=f"실행할 수 없는 작업입니다: {job_id}")

    try:
        # ★반환 dict가 있으면 응답에 싣는다 — 잡이 "실행했지만 아무것도 안 했다"(예: 같은 시각
        #   슬롯이라 건너뛴 스냅샷)를 구분해 보여주기 위함이다. 고정 문구만 돌려주면 누른 사람은
        #   가드가 걸렸는지 알 수 없고, 그건 가드가 없는 것과 화면상 같다.
        job_result = func()
        # 수동 트리거 성공 — 워치독 상태도 정리한다(cron 경로는 리스너가 담당, S5b).
        # 직전 cron 실패의 stale 'error'가 남아 evaluator가 거짓 'failed'로 보지 않게.
        now = kst_now()
        state.last_run_at = now
        state.last_status = "ok"
        state.last_error = None
        state.last_status_at = now
        db.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"작업 실행 에러: {e}")

    if isinstance(job_result, dict) and job_result.get("skipped"):
        return {"detail": f"작업 건너뜀: {job_id} — {job_result.get('reason', '사유 없음')}",
                "skipped": True, "result": job_result}
    return {"detail": f"작업 실행 완료: {job_id}",
            "result": job_result if isinstance(job_result, dict) else None}


@router.put("/toggle/{job_id}")
def toggle_job(job_id: str, db: Session = Depends(get_db)):
    """작업 일시정지/재개"""
    state = db.query(SchedulerState).filter(SchedulerState.job_name == job_id).first()
    if not state:
        raise HTTPException(status_code=404, detail=f"작업을 찾을 수 없습니다: {job_id}")

    prev_enabled = state.is_enabled
    state.is_enabled = not state.is_enabled
    db.commit()

    # 라이브 등록 여부(None=비적용/disable). enable인데 APScheduler에 잡이 없으면 resume은
    # 조용히 실패해 DB만 바뀌고 실제 미가동(쿠팡 광고비 13일 정지의 뿌리) — 그땐 start_scheduler와
    # 동일 옵션으로 라이브 add_job해 재시작 없이 살린다.
    live_registered: bool | None = None
    warning: str | None = None
    if scheduler.running:
        try:
            if not state.is_enabled:
                scheduler.pause_job(job_id)
            elif scheduler.get_job(job_id) is not None:
                scheduler.resume_job(job_id)
                live_registered = True
            else:
                func = job_func_for(job_id)
                if func is not None:
                    # start_scheduler와 동일 트리거(KST 명시)·id — misfire/coalesce는 scheduler
                    # job_defaults가 공통 적용하므로 여기서 재지정 불필요.
                    trigger = CronTrigger.from_crontab(
                        state.cron_expression, timezone="Asia/Seoul"
                    )
                    scheduler.add_job(func, trigger=trigger, id=job_id,
                                      replace_existing=True, **job_kwargs_for(job_id))
                    live_registered = True
                else:
                    # 매핑 밖 — 재시작 시 등록되는 잡일 수 있으니 DB 토글만 유지(500 금지).
                    live_registered = False
                    warning = f"'{job_id}'는 라이브 등록 매핑에 없습니다 — 백엔드 재시작 후 반영됩니다."
        except Exception as e:
            log.warning("[스케줄러] toggle 라이브 등록/재개 실패(%s): %s", job_id, e)

    log.info(
        "[스케줄러] toggle %s: enabled %s→%s, live_registered=%s",
        job_id, prev_enabled, state.is_enabled, live_registered,
    )
    action = "재개" if state.is_enabled else "일시정지"
    resp = {"detail": f"작업 {action}: {job_id}", "is_enabled": state.is_enabled,
            "live_registered": live_registered}
    if warning:
        resp["warning"] = warning
    return resp
