# scheduler_service.py — APScheduler 기반 자동 동기화/이익률 계산 스케줄러
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.database import SessionLocal, get_ad_db
from app.models import Channel, SchedulerState

log = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone="Asia/Seoul")


def _get_own_db_session():
    """FastAPI Depends 없이 직접 DB 세션 생성"""
    return SessionLocal()


def _get_own_ad_session():
    """ad_data.db 세션을 직접 가져오기"""
    gen = get_ad_db()
    if gen is None:
        return None
    try:
        return next(gen)
    except StopIteration:
        return None


def sync_all_channels_job():
    """전체 API 채널 주문 자동 동기화 (스케줄러 작업)"""
    db = _get_own_db_session()
    try:
        from app.services.sync_service import sync_channel_orders

        channels = db.query(Channel).filter(Channel.api_type != "excel").all()
        for ch in channels:
            try:
                result = sync_channel_orders(db, ch.id)
                log.info(
                    "[스케줄러] 채널 %s 동기화 완료: %s (신규: %s)",
                    ch.name, result.get("status"), result.get("new_orders"),
                )
            except Exception as e:
                log.error("[스케줄러] 채널 %s 동기화 에러: %s", ch.name, e)

        # 실행 시각 기록
        state = db.query(SchedulerState).filter(
            SchedulerState.job_name == "auto_sync_orders"
        ).first()
        if state:
            state.last_run_at = datetime.now()
            db.commit()
    except Exception as e:
        log.exception("[스케줄러] sync_all_channels_job 에러: %s", e)
    finally:
        db.close()


def recalculate_profit_job():
    """최근 7일 이익률 재계산 (스케줄러 작업)"""
    db = _get_own_db_session()
    ad_db = _get_own_ad_session()
    try:
        from app.services.profit_calculator import calculate_daily_trend

        date_to = date.today()
        date_from = date_to - timedelta(days=7)

        result = calculate_daily_trend(db, ad_db, None, date_from, date_to)
        log.info("[스케줄러] 이익률 재계산 완료: %d일치 데이터", len(result))

        state = db.query(SchedulerState).filter(
            SchedulerState.job_name == "auto_profit_calc"
        ).first()
        if state:
            state.last_run_at = datetime.now()
            db.commit()
    except Exception as e:
        log.exception("[스케줄러] recalculate_profit_job 에러: %s", e)
    finally:
        db.close()
        if ad_db is not None:
            try:
                ad_db.close()
            except Exception:
                pass


def _ensure_default_states(db):
    """기본 스케줄러 상태 DB 레코드 생성"""
    defaults = [
        ("auto_sync_orders", "0 6 * * *"),
        ("auto_profit_calc", "30 6 * * *"),
    ]
    for name, cron in defaults:
        existing = db.query(SchedulerState).filter(
            SchedulerState.job_name == name
        ).first()
        if not existing:
            db.add(SchedulerState(job_name=name, cron_expression=cron, is_enabled=True))
    db.commit()


def start_scheduler():
    """스케줄러 시작 — 기본 작업 2개 등록"""
    db = _get_own_db_session()
    try:
        _ensure_default_states(db)

        states = db.query(SchedulerState).all()
        for state in states:
            if not state.is_enabled:
                continue

            job_func = None
            if state.job_name == "auto_sync_orders":
                job_func = sync_all_channels_job
            elif state.job_name == "auto_profit_calc":
                job_func = recalculate_profit_job

            if job_func:
                try:
                    trigger = CronTrigger.from_crontab(state.cron_expression)
                    scheduler.add_job(
                        job_func,
                        trigger=trigger,
                        id=state.job_name,
                        replace_existing=True,
                    )
                    log.info("스케줄러 작업 등록: %s (%s)", state.job_name, state.cron_expression)
                except Exception as e:
                    log.error("스케줄러 작업 등록 실패 (%s): %s", state.job_name, e)
    finally:
        db.close()

    scheduler.start()
    log.info("스케줄러 시작됨")


def stop_scheduler():
    """스케줄러 종료"""
    try:
        scheduler.shutdown(wait=False)
        log.info("스케줄러 종료됨")
    except Exception as e:
        log.error("스케줄러 종료 에러: %s", e)
