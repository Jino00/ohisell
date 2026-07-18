# scheduler_service.py — APScheduler 기반 자동 동기화/이익률 계산 스케줄러
from __future__ import annotations

import logging
import threading
from app.utils.kst import kst_now, kst_today
from datetime import date, datetime, timedelta

from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MISSED,
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.database import SessionLocal, get_ad_db
from app.models import Channel, OAuthToken, SchedulerState

log = logging.getLogger(__name__)

# job_defaults: APScheduler 기본 misfire_grace_time은 1초라, 백엔드가 크론 발화 시각에
# 다운/재시작 중이면 그 잡이 catch-up 없이 조용히 드롭된다. 2026-07-13 실사고 — pm2가
# 08:53에 재생성(재시작 48회)되며 08:00 제안·07:50 예측·08:05 전문가·08:10 학습 크론이
# 전부 미발동(account_brief 누락으로 사후 확인). 유예 1시간(3600s)이면 아침 배치 창 내
# 재시작 시 복구 직후 1회 따라잡는다. coalesce=True(APScheduler 기본이지만 명시)로 누락된
# 다중 발화를 1회로 합쳐 중복을 막고, 재실행분은 proposal_writer.persist dedup + 계정
# 브리프 싱글톤으로 멱등(신규 0 → slack_notifier "no_proposals" → Slack 미발송).
# 한계: 1시간을 넘는 장기 정지는 여전히 드롭(D-1 일배치라 무해). 완전 견고화(재시작 간
# 마지막 실행 추적)는 영속 jobstore가 정답 — 후속 과제.
scheduler = BackgroundScheduler(
    timezone="Asia/Seoul",
    job_defaults={"misfire_grace_time": 3600, "coalesce": True},
)

# codex 9R[P2](D-NAO-49): auto_operator 시간당 레인 전용 misfire 유예 — 전역 3600s를 상속하면
# 스케줄러 지연/스톨 시 놓친 :20 실행이 최대 1시간 늦게 발화해 의도한 케이던스 밖 실입찰이
# 나간다(이 레인의 문서화된 정책 = catch-up 제외, 놓친 회차는 폐기·다음 정시가 재기회).
# 5분(:20 잡이 :25 내에 못 돌면 폐기)만 허용. 일 레인(08:50)은 전역 기본+catch-up 정책 유지.
_AUTO_OPERATOR_HOURLY_MISFIRE_GRACE = 300


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


def _job_state_listener(event):
    """APScheduler 이벤트 → SchedulerState 상태 기록 (워치독 SA①, cron 경로 전 잡 자동 포착).

    데코레이터가 아닌 add_listener로 cron 실행 전체를 한 곳에서 포착한다(codex: 리스너>데코레이터).
    수동 트리거(routers/scheduler.py)는 이 리스너를 안 거치고 직접 HTTP 500으로 실패 표면화.
    last_run_at은 '마지막 성공' 의미 → EVENT_JOB_EXECUTED(예외 없이 반환)에만 갱신.
    삼키는 잡들은 S3에서 외부 except를 re-raise로 정렬해 EVENT_JOB_ERROR로 표면화한다.
    콜백 자체 예외는 격리 — 콜백이 죽어도 잡/스케줄러는 살아야 한다.
    """
    try:
        job_name = getattr(event, "job_id", None)
        if not job_name:
            return
        db = SessionLocal()
        try:
            state = db.query(SchedulerState).filter(
                SchedulerState.job_name == job_name
            ).first()
            if state is None:
                return
            _apply_job_event(
                state,
                event.code,
                kst_now(),
                traceback=getattr(event, "traceback", None),
                exception=getattr(event, "exception", None),
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        log.exception("[워치독] 리스너 콜백 에러(격리)")


def _apply_job_event(state, code, now, *, traceback=None, exception=None):
    """이벤트 종류에 따라 SchedulerState 필드를 갱신한다(순수 mutation, DB I/O 없음 — 테스트용).

    last_run_at은 EXECUTED(성공)에만 갱신 → error/missed에서는 마지막 성공 시각이 보존된다
    (staleness_evaluator가 '마지막 성공' 기준으로 stale을 판정하기 위함).
    """
    MAX_ERR = 2000
    if code == EVENT_JOB_EXECUTED:
        state.last_run_at = now  # 마지막 성공 시각
        state.last_status = "ok"
        state.last_error = None
        state.last_status_at = now
    elif code == EVENT_JOB_ERROR:
        # last_run_at은 건드리지 않음(마지막 성공 보존). traceback 잘라 저장(API는 sanitize).
        tb = traceback or (str(exception) if exception is not None else "")
        state.last_status = "error"
        state.last_error = (tb or "")[:MAX_ERR]
        state.last_status_at = now
    elif code == EVENT_JOB_MISSED:
        state.last_status = "missed"
        state.last_status_at = now


def sync_all_channels_job():
    """전체 API 채널 주문 자동 동기화 (스케줄러 작업) — Wing + RG 포함"""
    db = _get_own_db_session()
    try:
        from app.services.sync_service import sync_channel_orders

        channels = db.query(Channel).filter(Channel.api_type != "excel").all()
        # S6(트랙 D-10): 윈도우 7→30일. 취소는 주문 후 수일~수주 뒤 발생 → 7일이면 놓침.
        # 넓은 윈도우 + reconcile-by-absence로 사라진(취소) 주문을 cancelled 처리(매출 stale 제거).
        _to = kst_today()
        _from = _to - timedelta(days=30)
        for ch in channels:
            try:
                result = sync_channel_orders(db, ch.id, date_from=_from, date_to=_to)
                log.info(
                    "[스케줄러] 채널 %s 동기화 완료: %s (신규: %s, 취소반영: %s)",
                    ch.name, result.get("status"), result.get("new_orders"),
                    result.get("reconciled_cancelled"),
                )
            except Exception as e:
                log.error("[스케줄러] 채널 %s 동기화 에러: %s", ch.name, e)

        # RG 주문도 함께 동기화 (Wing과 매출 일치를 위해)
        try:
            from app.services.coupang.rg_order_sync import sync_all_rg_orders
            rg_results = sync_all_rg_orders(db, days=3)
            rg_failed = _coupang_failed(rg_results)
            if rg_failed:
                # 계정 격리(sync_all_rg_orders)로 예외 대신 error dict가 오므로 warning으로 표면화
                log.warning("[스케줄러] RG 주문 동기화 계정 실패: %s", rg_failed)
            log.info("[스케줄러] RG 주문 동기화 완료: %s", rg_results)
        except Exception as e:
            log.error("[스케줄러] RG 주문 동기화 에러: %s", e)

    except Exception as e:
        log.exception("[스케줄러] sync_all_channels_job 에러: %s", e)
        raise  # 삼킴 정렬(S5b): EVENT_JOB_ERROR/HTTP500로 실패 표면화(16일 침묵 사고 방지)
    finally:
        db.close()


def recalculate_profit_job():
    """최근 7일 이익률 재계산 (스케줄러 작업)"""
    db = _get_own_db_session()
    ad_db = _get_own_ad_session()
    try:
        from app.services.profit_calculator import calculate_daily_trend

        date_to = kst_today()
        date_from = date_to - timedelta(days=7)

        result = calculate_daily_trend(db, ad_db, None, date_from, date_to)
        log.info("[스케줄러] 이익률 재계산 완료: %d일치 데이터", len(result))

    except Exception as e:
        log.exception("[스케줄러] recalculate_profit_job 에러: %s", e)
        raise  # 삼킴 정렬(S5b): 인라인 스탬프 제거 후 reraise 없으면 실패도 EXECUTED→거짓 ok(codex P1)
    finally:
        db.close()
        if ad_db is not None:
            try:
                ad_db.close()
            except Exception:
                pass


def sync_naver_sa_ad_costs_job():
    """Naver SA 광고비 어제치 자동 적재 (07:00 KST)"""
    db = _get_own_db_session()
    try:
        from app.routers.ad_costs import _extract_naver_sa_keyword, _upsert_ad_cost, _upsert_ad_revenue, NAVER_SA_CONV_SOURCE
        from app.services.naver_sa_ad_fetcher import fetch_campaign_daily_spend, fetch_daily_conversion_revenue
        from decimal import Decimal
        from sqlalchemy import text

        yesterday = kst_today() - timedelta(days=1)
        naver_row = db.execute(
            text("SELECT id FROM channels WHERE code = 'NAVER' LIMIT 1")
        ).fetchone()
        if not naver_row:
            log.error("[스케줄러] NAVER 채널 없음")
            return
        naver_id = naver_row[0]

        campaigns = fetch_campaign_daily_spend(yesterday, yesterday)
        agg: dict[tuple[str, str], Decimal] = {}
        for c in campaigns:
            kw = _extract_naver_sa_keyword(c["campaign_name"])
            key = (c["date"], f"naver_sa:{kw}")
            agg[key] = agg.get(key, Decimal("0")) + c["spend"]

        for (dt_str, source), spend in agg.items():
            _upsert_ad_cost(db, naver_id, date.fromisoformat(dt_str), spend, source)

        # 구매 전환매출(직접+간접) 적재 — RoAS용
        conv_n = 0
        try:
            conv_daily = fetch_daily_conversion_revenue(yesterday, yesterday)
            for dt_str, rev in conv_daily.items():
                _upsert_ad_revenue(db, naver_id, date.fromisoformat(dt_str), rev, NAVER_SA_CONV_SOURCE)
            conv_n = len(conv_daily)
        except Exception as ce:
            log.warning("[스케줄러] Naver SA 전환매출 적재 실패(광고비는 저장됨): %s", ce)

        db.commit()
        log.info("[스케줄러] Naver SA 광고비 %d건 + 전환매출 %d일 적재 완료 (%s)", len(agg), conv_n, yesterday)
    except Exception as e:
        log.exception("[스케줄러] sync_naver_sa_ad_costs_job 에러: %s", e)
        raise  # 삼킴 정렬(S5b): EVENT_JOB_ERROR/HTTP500로 실패 표면화
    finally:
        db.close()


def sync_naver_ad_daily_job():
    """네이버 SA 일별 광고성과(naver_ad_daily) 수집 + BEP 산출 (07:30 KST).

    최근 3일 창(리포트 사후 정정 반영, snapshot 교체 멱등). 07:00 ad_costs sync 이후 실행
    (어제 AD/AD_CONVERSION 리포트 BUILT 보장). BEP는 매핑×원가×정산 실효율로 재산출.
    네이버 SA 광고 최적화 트랙 P0.
    """
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.ad_daily_ingest import ingest_ad_daily
        from app.services.naver_ad.bep_calculator import calculate_bep

        end = kst_today() - timedelta(days=1)
        start = end - timedelta(days=2)  # 3일 창(사후 정정 흡수)
        ing = ingest_ad_daily(db, start, end)
        bep = calculate_bep(db)
        log.info("[스케줄러] naver_ad_daily 적재 %s + BEP %s", ing, bep)
    except Exception as e:
        log.exception("[스케줄러] sync_naver_ad_daily_job 에러: %s", e)
        raise  # 삼킴 정렬(S5b): cron 경로 EVENT_JOB_ERROR로 표면화
    finally:
        db.close()


def snapshot_naver_ad_hourly_job():
    """네이버 SA 시간별 캠페인 스냅샷 (매시간, 당일 누적). 빠른 루프(D-NAO-4) 데이터 기반."""
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.hourly_snapshot import snapshot_hourly

        result = snapshot_hourly(db)
        log.info("[스케줄러] naver_ad hourly snapshot: %s", result)
    except Exception as e:
        log.exception("[스케줄러] snapshot_naver_ad_hourly_job 에러: %s", e)
        raise
    finally:
        db.close()


def trigger_watch_job():
    """네이버 SA 조건발동 즉시 알림 — 페이싱 이탈 + CPC 급등 (매시 :07, 스냅샷 :05 직후).

    빠른 루프(D-NAO-4 관찰·제어) — 정시 08:00 proposal_pipeline(느린 루프)과 무관하게
    독립 실행. 순위 이탈은 실시간 데이터 부재로 이번 스코프 제외(trigger_watch.py 참조).
    """
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.trigger_watch import run_hourly

        result = run_hourly(db)
        log.info("[스케줄러] naver trigger_watch: %s", result)
    except Exception as e:
        log.exception("[스케줄러] trigger_watch_job 에러: %s", e)
        raise
    finally:
        db.close()


def sync_naver_entity_job():
    """네이버 SA 엔티티(캠페인/그룹/키워드) 인벤토리 동기화 (07:35 KST, P2-S1)."""
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.entity_sync import sync_entities

        result = sync_entities(db)
        log.info("[스케줄러] naver_entity sync: %s", result)
    except Exception as e:
        log.exception("[스케줄러] sync_naver_entity_job 에러: %s", e)
        raise
    finally:
        db.close()


def shopping_ad_product_sync_job():
    """쇼핑 광고그룹↔상품 매핑 동기화 (08:20 KST, D-NAO-57 A, 관찰성 sync).

    optimizer='ours' 쇼핑 캠페인의 활성 그룹 /ncc/ads → naver_adgroup_product 스냅샷 교체.
    campaign_target_resolver 우선순위 ②(상품 파생 target)의 데이터 소스. fail-open — 매핑 sync
    실패가 08:50 일 레인(catch-up 체인 하류)을 막으면 안 된다(관찰성 잡이라 하루 늦어도 무해)."""
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.shopping_ad_product_sync import sync_adgroup_products

        result = sync_adgroup_products(db)
        log.info("[스케줄러] naver shopping_ad_product sync: %s", result)
    except Exception as e:
        log.exception("[스케줄러] shopping_ad_product_sync_job 에러(fail-open): %s", e)
    finally:
        db.close()


def sync_naver_search_term_job():
    """네이버 SA 검색어 단위 성과 수집 (07:40 KST, P2-S1). 최근 3일 창(사후정정 흡수)."""
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.search_term_ingest import ingest_search_term_daily

        end = kst_today() - timedelta(days=1)
        start = end - timedelta(days=2)
        result = ingest_search_term_daily(db, start, end)
        log.info("[스케줄러] naver_search_term_daily ingest: %s", result)
    except Exception as e:
        log.exception("[스케줄러] sync_naver_search_term_job 에러: %s", e)
        raise
    finally:
        db.close()


def sync_naver_keyword_volume_job():
    """저클릭 키워드 월검색량 갱신 (주1회, D-NAO-18 3단분류 입력)."""
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.keyword_volume_sync import sync_keyword_volumes

        result = sync_keyword_volumes(db)
        log.info("[스케줄러] keyword_volume_sync: %s", result)
    except Exception as e:
        log.exception("[스케줄러] sync_naver_keyword_volume_job 에러: %s", e)
        raise
    finally:
        db.close()


def generate_naver_proposals_job():
    """네이버 SA 제안 자동 생성 — 진단→시뮬→제안→Slack (08:00 KST, 트랙 P2-S3 관찰모드).

    07:30/07:35/07:40 데이터 수집(naver_ad_daily/entity/search_term) 이후 실행. run_daily
    내부에서 freshness 게이트가 수집 성공 여부를 다시 확인해 stale이면 자체적으로 스킵한다
    (이 잡은 그 결과를 로그만 남긴다 — 부분 실패도 정상 흐름, run_daily 자체가 던지는
    예외만 여기서 표면화).
    """
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.proposal_pipeline import run_daily

        result = run_daily(db)
        log.info("[스케줄러] naver proposal_pipeline: %s", result)
    except Exception as e:
        log.exception("[스케줄러] generate_naver_proposals_job 에러: %s", e)
        raise
    finally:
        db.close()


def run_naver_learning_loops_job():
    """네이버 SA 학습루프 4종 일괄 실행(08:10 KST, generate_naver_proposals 08:00 직후, 트랙 P6).

    proposal_scoreboard(제안 정확도)·estimate_calibrator(예측편향)·conversion_maturity(전환
    성숙곡선)·hourly_pattern(요일×시간 분포) — learning_loops harness가 단계격리로 조합
    실행하므로 이 잡은 그 결과를 로그만 남긴다(harness 자체는 예외를 던지지 않음, 각 루프
    실패는 stage_status로만 표면화 — proposal_pipeline과 동일 원칙).
    """
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.learning_loops import run_all

        result = run_all(db)
        log.info("[스케줄러] naver learning_loops: %s", result["stage_status"])
    except Exception as e:
        log.exception("[스케줄러] run_naver_learning_loops_job 에러: %s", e)
        raise
    finally:
        db.close()


def run_naver_flight_loop_job():
    """당일 플라이트 루프 — 2시간 주기 캠페인별 α 산출(X2, D-NAO-34).

    response_curve_builder(T1)→pacing_controller(T2) SA 조합으로
    optimizer='ours' 캠페인의 최적 입찰배수를 산출·기록한다.
    dry_run=True(기본) — Jino 전환 결정까지 실제 입찰 변경 없음(D-NAO-5).
    """
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.flight_loop import run_flight_loop

        result = run_flight_loop(db)
        log.info("[스케줄러] naver flight_loop: %d캠페인, dry_run=%s",
                 result["campaigns_processed"], result.get("dry_run", True))
    except Exception as e:
        log.exception("[스케줄러] run_naver_flight_loop_job 에러: %s", e)
        raise
    finally:
        db.close()


def generate_expert_desk_job():
    """전문가(Ava) 검토 데스크 — E1a expert_desk.run_daily (08:05 KST, generate_naver_proposals
    08:00 직후, 계획서 §8). AI_office·실 claude 무의존(E1a 자족) — invoke 인자를 넘기지 않아
    ava_reviewer 기본값인 실 claude CLI 어댑터가 그대로 쓰인다. 각 단계는 expert_desk 자체가
    격리하므로(learning_loops와 동일 원칙) 이 잡은 그 결과를 로그만 남긴다.

    codex review 발견(2026-07-09, P1 논의): generate_naver_proposals(08:00)가 5분 안에 커밋을
    못 마치면 이 잡이 그날 새로 생성된 제안 일부를 놓칠 수 있다(briefing_builder가 그 시점의
    pending 전체를 읽음). 단, 유실이 아니라 지연이다 — 놓친 제안은 여전히 pending이라 다음날
    08:05에 자동으로 재포함된다. 08:05는 계획서에 명시된 값(Jino 승인)이라 이 태스크에서
    임의로 바꾸지 않음 — 실측 runtime 확인 후 필요시 조정 대상(추정 금지).
    """
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.expert_desk import run_daily

        result = run_daily(db)
        log.info("[스케줄러] naver expert_desk: %s", result["stage_status"])
    except Exception as e:
        log.exception("[스케줄러] generate_expert_desk_job 에러: %s", e)
        raise
    finally:
        db.close()


def run_naver_forecast_engine_job():
    """네이버 SA 캠페인 grain 예측 엔진 (07:50 KST, entity/search_term 수집 이후·제안 08:00 이전, F1).

    ①최근 16일 캠페인 시계열 재백필(campaign_backfill 재사용, 신선도 유지) ②오늘 예측 생성
    (forecast_model_builder) ③어제 예측 채점+자동강등(forecast_scorer) — harness가 단계격리로
    조합 실행하므로 이 잡은 결과를 로그만 남긴다(learning_loops와 동일 원칙).
    """
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.forecast_engine import run_daily

        result = run_daily(db)
        log.info("[스케줄러] naver forecast_engine: %s", result["stage_status"])
    except Exception as e:
        log.exception("[스케줄러] run_naver_forecast_engine_job 에러: %s", e)
        raise
    finally:
        db.close()


def run_naver_retro_scoring_job():
    """상설 소급 채점 — retro_scoring_loop.run_daily_retro (08:30 KST, generate_naver_proposals
    08:00·run_naver_learning_loops 08:10 이후, D-NAO-45).

    ①진단 보드 as-of(어제) 스냅샷(retro_snapshotter) ②D+3/D+7 사후창 방향 채점(retro_scorer)
    ③trigger_pacing 경보 채점(retro_pacing_scorer) — harness가 단계격리로 조합 실행하므로
    이 잡은 결과를 로그만 남긴다(learning_loops/forecast_engine과 동일 원칙).
    """
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.retro_scoring_loop import run_daily_retro

        result = run_daily_retro(db)
        log.info("[스케줄러] naver retro_scoring: %s", result["stage_status"])
    except Exception as e:
        log.exception("[스케줄러] run_naver_retro_scoring_job 에러: %s", e)
        raise
    finally:
        db.close()


def run_naver_diary_reflection_job():
    """운영 일기 해석층 — reflection_loop.run_daily_reflection (08:35 KST,
    run_naver_retro_scoring 08:30 이후 = outcome 최신, D-NAO-54 P2).

    ①outcome_backfill_sa(어제/그제/D-8 diary 행에 D+1/D+7 결과·retro 채점 소급 기입)
    ②daily_reflection_sa(어제 일기+환경→해석문, 독립 LLM) — harness가 단계격리로 조합.

    ★fail-open(관찰 전용, 집행 아님): retro 잡과 달리 예외를 다시 던지지 않는다 — 일기 해석
    실패가 catch-up 체인의 하류 집행 잡(auto_operator_daily)을 막으면 안 되기 때문(D-NAO-54
    금지선: "일기 기록 실패가 집행을 막으면 안 됨").
    """
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.reflection_loop import run_daily_reflection

        result = run_daily_reflection(db)
        log.info("[스케줄러] naver diary_reflection: %s", result["stage_status"])
    except Exception as e:  # noqa: BLE001 — fail-open: 관찰 전용 잡, 집행 체인 보호(raise 없음)
        log.exception("[스케줄러] run_naver_diary_reflection_job 에러(fail-open): %s", e)
    finally:
        db.close()


def run_naver_wisdom_job():
    """운영 일기 지혜 승격·망각층 — wisdom_loop.run_daily_wisdom (08:45 KST, D-NAO-54 P3).

    ①candidate_sa(결과 기입 diary → 반복 패턴 후보) ②judge_sa(숙성 후보 독립 LLM 판정)
    ③writer_sa(승격분 지혜 엔트리 + 정보성 보고) ④retention_sa(미승격 Ebbinghaus 망각) —
    harness가 단계격리로 조합. 08:35 reflection이 outcome을 소급 기입한 뒤라 결과가 최신이다.

    ★fail-open(관찰·보고 전용, 집행 아님): reflection 잡과 동일 이유로 예외를 다시 던지지 않는다
    — 지혜 처리 실패가 catch-up 체인의 집행 잡(auto_operator_daily)을 막으면 안 된다(D-NAO-54
    금지선: "일기 기록 실패가 집행을 막으면 안 됨").
    """
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.wisdom_loop import run_daily_wisdom

        result = run_daily_wisdom(db)
        log.info("[스케줄러] naver wisdom: %s", result["stage_status"])
    except Exception as e:  # noqa: BLE001 — fail-open: 관찰·보고 전용 잡, 집행 체인 보호(raise 없음)
        log.exception("[스케줄러] run_naver_wisdom_job 에러(fail-open): %s", e)
    finally:
        db.close()


def run_naver_vault_export_job():
    """운영 일기·지혜 Obsidian 볼트 export — vault_export.export_vault (09:05 KST, D-NAO-54 P5).

    ops_diary_entries(최근 8일)·ops_wisdom_entries(활성/은퇴)를 사람이 읽는 마크다운으로
    <backend>/data/vault/Ohisell/에 재생성한다(Mac pull 스크립트가 iCloud Obsidian으로 미러).

    ★fail-open(관찰·열람 전용, 집행 아님): 예외를 다시 던지지 않는다 — 볼트 export 실패가
    catch-up 체인의 하류 집행 잡을 막으면 안 된다(D-NAO-54 금지선). export_vault 자체도
    내부 fail-open이라 여기서는 방어적 이중 안전망.
    """
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.vault_export import export_vault

        result = export_vault(db)
        log.info("[스케줄러] naver vault_export: %s", result)
    except Exception as e:  # noqa: BLE001 — fail-open: 열람 전용 잡, 집행 체인 보호(raise 없음)
        log.exception("[스케줄러] run_naver_vault_export_job 에러(fail-open): %s", e)
    finally:
        db.close()


def sweep_naver_keyword_hourly_job():
    """키워드/쇼핑그룹 시간별(hh24) 축적 — 일 1회 D-1 스윕 (09:10 KST, sync_naver_ad_daily
    07:30 이후. D-NAO-46②, docs/PLAN_naver-ad-keyword-hourly-accrual.md §4).

    naver_ad_daily D-1의 imp>0 유닛을 유닛별 hh24 곡선으로 영구 축적(관찰 전용, 쓰기 API 0).
    hh24 상세는 네이버가 최근 7일만 보존 — 캐치업으로 [D-6,D-2] 결손 유닛도 보수적으로 보완.
    """
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.keyword_hourly_sweep import sweep_keyword_hourly

        result = sweep_keyword_hourly(db)
        log.info("[스케줄러] naver keyword_hourly sweep: %s", result)
    except Exception as e:
        log.exception("[스케줄러] sweep_naver_keyword_hourly_job 에러: %s", e)
        raise
    finally:
        db.close()


def run_naver_auto_operator_daily_job():
    """auto_operator 일 레인 — D-NAO-48 4조건 심사·집행 서버 코드화 (08:50 KST,
    run_naver_retro_scoring 08:30 이후 — 조건④ bleeding 판정이 그 결과를 쓴다, D-NAO-49).

    auto_operate=True 캠페인의 당일 pending bid_up/bid_down/pause를 심사해 승인 시
    naver_execution_harness.execute(dry_run=False)로 즉시 집행한다."""
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.auto_operator import run_daily_lane

        result = run_daily_lane(db)
        log.info(
            "[스케줄러] naver auto_operator daily: reviewed=%s approved=%s executed=%s held=%s failed=%s",
            result["reviewed"], result["approved"], result["executed"],
            len(result["held"]), result["failed"],
        )
    except Exception as e:
        log.exception("[스케줄러] run_naver_auto_operator_daily_job 에러: %s", e)
        raise
    finally:
        db.close()


def run_naver_auto_operator_hourly_job():
    """auto_operator 시간당 레인 — 핫셋 intraday 밴드 관제 실입찰 (매시 :20 KST, D-NAO-49).

    catch-up 제외(시간성 소멸 — 다음 정시가 곧 재기회, PLAN §5). auto_operate=True 캠페인의
    핫셋(클릭≥10 그룹)만, 순위·CPC·페이싱 기반 스텝 제안을 생성 즉시 심사·집행한다."""
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.auto_operator import run_hourly_lane

        result = run_hourly_lane(db)
        log.info(
            "[스케줄러] naver auto_operator hourly: reviewed=%s approved=%s executed=%s "
            "held=%s skipped=%s failed=%s",
            result["reviewed"], result["approved"], result["executed"],
            len(result["held"]), result["skipped"], result["failed"],
        )
    except Exception as e:
        log.exception("[스케줄러] run_naver_auto_operator_hourly_job 에러: %s", e)
        raise
    finally:
        db.close()


def sync_meta_ad_costs_job():
    """Meta 광고비 어제치 자동 적재 (07:00 KST)"""
    db = _get_own_db_session()
    try:
        from app.routers.ad_costs import _extract_meta_keyword, _upsert_ad_cost
        from app.services.meta_ad_fetcher import fetch_campaign_daily_spend
        from decimal import Decimal
        from sqlalchemy import text

        yesterday = kst_today() - timedelta(days=1)
        cafe24_row = db.execute(
            text("SELECT id FROM channels WHERE code = 'CAFE24' LIMIT 1")
        ).fetchone()
        if not cafe24_row:
            log.error("[스케줄러] CAFE24 채널 없음")
            return
        cafe24_id = cafe24_row[0]

        campaigns = fetch_campaign_daily_spend(yesterday, yesterday)
        agg: dict[tuple[str, str], Decimal] = {}
        for c in campaigns:
            kw = _extract_meta_keyword(c["campaign_name"])
            key = (c["date"], f"meta:{kw}")
            agg[key] = agg.get(key, Decimal("0")) + c["spend"]

        for (dt_str, source), spend in agg.items():
            _upsert_ad_cost(db, cafe24_id, date.fromisoformat(dt_str), spend, source)
        db.commit()
        log.info("[스케줄러] Meta 광고비 %d건 적재 완료 (%s)", len(agg), yesterday)
    except Exception as e:
        log.exception("[스케줄러] sync_meta_ad_costs_job 에러: %s", e)
        raise  # 삼킴 정렬(S5b): EVENT_JOB_ERROR/HTTP500로 실패 표면화
    finally:
        db.close()


def sync_coupang_products_job():
    """쿠팡 상품 마스터+채널매핑 자동 동기화 (05:30 KST).

    주문 자동동기화(06:00)·이익계산(06:30) 전에 실행해 상품마스터/매핑을 갱신한다
    (주문 _auto_link_product가 최신 매핑을 쓰도록). 재고/판매상태도 함께 새로고침.
    트랙 D-8: 호출은 서버 IP에서만 가능(로컬 403).
    """
    db = _get_own_db_session()
    try:
        from app.services.coupang.product_sync import sync_all_products

        results = sync_all_products(db, refresh_inventory=True)
        log.info("[스케줄러] 쿠팡 상품 동기화 결과: %s", results)

        # 반환형 하드 에러(config_missing 등) 표면화 — sync는 raise 대신 dict로 반환하므로
        # 여기서 감지해 raise해야 수동 트리거가 거짓 성공을 보고하지 않음(codex [P2] R2).
        # (부분 실패 카운터 stats["errors"]는 예상 가능한 부분 성공이라 raise 대상 아님)
        failed = [r for r in results if r.get("error")]
        if failed:
            raise RuntimeError(f"쿠팡 상품 동기화 실패 계정: {failed}")

    except Exception as e:
        # cron 경로: APScheduler가 잡 예외를 관용(EVENT_JOB_ERROR, 스케줄러 생존).
        # 수동 트리거 경로(scheduler/trigger): re-raise해야 trigger_job이 실패를
        # 표면화(HTTP 500, last_run_at 미갱신)한다 — 거짓 성공 보고 방지(codex [P2]).
        log.exception("[스케줄러] sync_coupang_products_job 에러: %s", e)
        raise
    finally:
        db.close()


def sync_coupang_returns_job():
    """쿠팡 반품/취소/교환 자동 동기화 (05:45 KST).

    상품 동기화(05:30)·주문 동기화(06:00) 다음, 이익계산(06:30) 전에 실행해
    순매출 차감(반품/취소) 사실을 갱신한다. 트랙 D-8: 호출은 서버 IP에서만(로컬 403).
    트랙 D-3: 사실/지표 정리만 — 전략판단 없음.
    """
    db = _get_own_db_session()
    try:
        from app.services.coupang.returns_sync import sync_all_returns

        results = sync_all_returns(db)
        log.info("[스케줄러] 쿠팡 반품/교환 동기화 결과: %s", results)

        # 반환형 하드 에러(config_missing 등) 표면화 — sync는 raise 대신 dict로 반환하므로
        # 여기서 감지해 raise해야 수동 트리거가 거짓 성공을 보고하지 않음(codex [P2] R2 패턴).
        # (부분 실패 카운터 stats["errors"]는 예상 가능한 부분 성공이라 raise 대상 아님)
        failed = [r for r in results if r.get("error")]
        if failed:
            raise RuntimeError(f"쿠팡 반품 동기화 실패 계정: {failed}")

    except Exception as e:
        # cron 경로는 APScheduler가 관용(스케줄러 생존). 수동 트리거 경로는 re-raise로
        # 실패 표면화(HTTP 500, last_run_at 미갱신) — 거짓 성공 보고 방지(codex [P2]).
        log.exception("[스케줄러] sync_coupang_returns_job 에러: %s", e)
        raise
    finally:
        db.close()


def sync_coupang_settlement_job():
    """쿠팡 정산(매출내역+지급내역) 자동 동기화 + 수수료 감사 (05:50 KST).

    매출내역 serviceFeeRatio ↔ 등록 수수료율 비교(D-10), 불일치는 권위 재확인 후
    정당변동 자동갱신 or 이상 플래그(D-11). 스케줄 잡은 최근 위주(매출 30일·지급 2개월),
    전체 재적재는 수동 트리거(days/months 확대). 트랙 D-8: 서버 IP에서만. D-3: 사실 정리만.
    """
    db = _get_own_db_session()
    try:
        from app.services.coupang.settlement_sync import sync_all_settlement

        results = sync_all_settlement(db, days=30, months=2)
        log.info("[스케줄러] 쿠팡 정산 동기화 결과: %s", results)

        # 반환형 하드 에러(config_missing/읽기 실패) 표면화 — 거짓 성공 방지(codex [P2] 패턴).
        failed = [r for r in results if r.get("error")]
        if failed:
            raise RuntimeError(f"쿠팡 정산 동기화 실패 계정: {failed}")

    except Exception as e:
        # cron 경로는 APScheduler가 관용. 수동 트리거 경로는 re-raise로 실패 표면화(거짓 성공 방지).
        log.exception("[스케줄러] sync_coupang_settlement_job 에러: %s", e)
        raise
    finally:
        db.close()


def _coupang_failed(results: list[dict]) -> list[dict]:
    """RG 동기화 결과에서 하드 실패 추출 — config_missing(error)·읽기 실패(read_error) 모두.

    거짓 성공 방지(원칙22·codex [P2] 패턴): CoupangReadError로 표면화된 read_error도 실패로 본다.
    """
    return [r for r in results if r.get("error") or r.get("read_error")]


def sync_coupang_rg_sizes_job():
    """로켓그로스 상품 사이즈 자동 동기화 (05:35 KST) — 보관비 CBM 토대(P3/D-14).

    RG 상품조회 skuInfo → coupang_product_item 사이즈·cbm. 트랙 D-8: 서버 IP에서만.
    """
    db = _get_own_db_session()
    try:
        from app.services.coupang.rg_size_sync import sync_all_rg_sizes

        results = sync_all_rg_sizes(db)
        log.info("[스케줄러] 쿠팡 RG 사이즈 동기화 결과: %s", results)
        failed = _coupang_failed(results)
        if failed:
            raise RuntimeError(f"쿠팡 RG 사이즈 동기화 실패 계정: {failed}")

    except Exception as e:
        log.exception("[스케줄러] sync_coupang_rg_sizes_job 에러: %s", e)
        raise
    finally:
        db.close()


def sync_coupang_rg_inventory_job():
    """로켓창고 재고 자동 동기화 (05:40 KST) — 재고관리(P3/D-14). 트랙 D-8: 서버 IP에서만."""
    db = _get_own_db_session()
    try:
        from app.services.coupang.rg_inventory_sync import sync_all_rg_inventory

        results = sync_all_rg_inventory(db)
        log.info("[스케줄러] 쿠팡 RG 재고 동기화 결과: %s", results)
        failed = _coupang_failed(results)
        if failed:
            raise RuntimeError(f"쿠팡 RG 재고 동기화 실패 계정: {failed}")

    except Exception as e:
        log.exception("[스케줄러] sync_coupang_rg_inventory_job 에러: %s", e)
        raise
    finally:
        db.close()


def sync_coupang_rg_orders_job():
    """로켓그로스 주문 자동 동기화 (05:55 KST) — 향후 RG 매출(P3/D-14). 트랙 D-8: 서버 IP에서만."""
    db = _get_own_db_session()
    try:
        from app.services.coupang.rg_order_sync import sync_all_rg_orders

        results = sync_all_rg_orders(db, days=30)
        log.info("[스케줄러] 쿠팡 RG 주문 동기화 결과: %s", results)
        failed = _coupang_failed(results)
        if failed:
            raise RuntimeError(f"쿠팡 RG 주문 동기화 실패 계정: {failed}")

    except Exception as e:
        log.exception("[스케줄러] sync_coupang_rg_orders_job 에러: %s", e)
        raise
    finally:
        db.close()


def sync_coupang_rg_inbound_job():
    """Wing 입고 자동 동기화 (05:20 KST) — RG 발송관제 리드타임(트랙 D-1). 세션쿠키 인증.

    ⚠️ 쿠키 만료(auth_expired)·미설정(cookie_missing)은 fail-soft(정상 운영상태) → raise 안 함.
    SA가 cookie row status=red로 표시(D-5: 만료 측정). 예상치 못한 예외만 raise."""
    db = _get_own_db_session()
    try:
        from app.services.coupang.rg_inbound_sync import sync_all_inbound

        results = sync_all_inbound(db)
        log.info("[스케줄러] 쿠팡 RG 입고 동기화 결과: %s", results)

    except Exception as e:
        log.exception("[스케줄러] sync_coupang_rg_inbound_job 에러: %s", e)
        raise
    finally:
        db.close()


def sync_coupang_rg_settlement_job():
    """RG 정산 수수료 자동 동기화 (05:30 KST) — 윙 내부 API(status/api). D-10: 매출인식일 기준.

    쿠키 만료(WingAuthError)는 fail-soft(status red). 예상치 못한 예외만 raise."""
    db = _get_own_db_session()
    try:
        from app.services.coupang.rg_settlement_sync import sync_rg_settlement, RG_ACCOUNTS

        for account_key in RG_ACCOUNTS:
            result = sync_rg_settlement(db, account_key)
            log.info("[스케줄러] RG 정산 sync (%s): %s", account_key, result)

    except Exception as e:
        log.exception("[스케줄러] sync_coupang_rg_settlement_job 에러: %s", e)
        raise
    finally:
        db.close()


def auto_download_rg_settlement_job():
    """RG 정산 엑셀 자동 다운로드·적재 (06:15 KST) — Wing 3단계(request→poll→S3 GET).

    WAREHOUSING_SHIPPING(입출고·배송비) 옵션 단위 적재. 이미 적재된 기간은 idempotent.
    쿠키 만료(WingAuthError)는 fail-soft(per-account error 반환). 예상치 못한 예외만 raise."""
    db = _get_own_db_session()
    try:
        from app.services.coupang.rg_settlement_sync import auto_download_all, RG_ACCOUNTS

        vendor_id_map = {}
        import os
        vendor_id_map["COUPANG_WING1"] = os.environ.get("COUPANG_WING1_VENDOR_ID", "")
        vendor_id_map["COUPANG_WING2"] = os.environ.get("COUPANG_WING2_VENDOR_ID", "")

        results = auto_download_all(db, vendor_id_map)
        for r in results:
            log.info("[스케줄러] RG 엑셀 자동 다운로드 (%s): requested=%s completed=%s ingested=%s errors=%d",
                     r.get("account_key"), r.get("requested"), r.get("completed"),
                     r.get("ingested"), len(r.get("errors", [])))

    except Exception as e:
        log.exception("[스케줄러] auto_download_rg_settlement_job 에러: %s", e)
        raise
    finally:
        db.close()


def sync_coupang_ad_cost_job():
    """쿠팡 광고비 자동 동기화 (자정 00:10 KST) — advertising.coupang.com Wing 내부 API.

    쿠키 만료·미설정은 fail-soft(status red 표시). 예상치 못한 예외만 raise."""
    db = _get_own_db_session()
    try:
        from app.services.coupang.ad_cost_sync import sync_ad_cost

        result = sync_ad_cost(db)
        log.info("[스케줄러] 쿠팡 광고비 동기화 결과: %s", result)
    except Exception as e:
        log.exception("[스케줄러] sync_coupang_ad_cost_job 에러: %s", e)
        raise
    finally:
        db.close()


def request_ad_cost_refresh_job():
    """장중 오늘 광고비 자동 갱신 요청 (매시 10~20시 KST). prod는 advertising.coupang.com을
    직접 못 가져오므로(Akamai 403 라이브 확인), 갱신 플래그만 set → Jino Mac 페처 데몬이
    다음 폴링(~20초)에서 headful fetch 후 push. Mac이 꺼져 있으면 플래그는 다음 기동 시 소비
    (무해·idempotent). 버튼 수동 클릭을 대체해 '오늘 광고비'가 장중 stale로 남지 않게 한다."""
    db = _get_own_db_session()
    try:
        from app.services.coupang.ad_cost_sync import request_refresh

        result = request_refresh(db)
        log.info("[스케줄러] 쿠팡 광고비 장중 갱신 요청: %s", result)
    except Exception as e:
        log.exception("[스케줄러] request_ad_cost_refresh_job 에러: %s", e)
        raise
    finally:
        db.close()


def sync_coupang_coupons_job():
    """쿠팡 쿠폰 운영 현황 자동 동기화 (06:00 KST) — 즉시할인쿠폰+예산/계약(P5). 트랙 D-8: 서버 IP에서만."""
    db = _get_own_db_session()
    try:
        from app.services.coupang.coupon_sync import sync_all_coupons

        results = sync_all_coupons(db, budget_months=3)
        log.info("[스케줄러] 쿠팡 쿠폰 동기화 결과: %s", results)
        failed = _coupang_failed(results)
        if failed:
            raise RuntimeError(f"쿠팡 쿠폰 동기화 실패 계정: {failed}")

    except Exception as e:
        log.exception("[스케줄러] sync_coupang_coupons_job 에러: %s", e)
        raise
    finally:
        db.close()


def sync_coupang_cs_job():
    """쿠팡 CS 고객문의 자동 동기화 (06:05 KST) — 상품Q&A + CS이관 7일치 (P6). 트랙 D-8: 서버 IP에서만."""
    db = _get_own_db_session()
    try:
        from app.services.coupang.cs_sync import sync_all_cs

        results = sync_all_cs(db)
        log.info("[스케줄러] 쿠팡 CS 동기화 결과: %s", results)

        # codex[P2]: api_failures > 0이면 스케줄러 실패로 표면화(쿠폰 패턴 동일).
        total_fail = results.get("total_api_failures", 0)
        if total_fail > 0:
            raise RuntimeError(f"쿠팡 CS 동기화 API 실패 {total_fail}건")

    except Exception as e:
        log.exception("[스케줄러] sync_coupang_cs_job 에러: %s", e)
        raise
    finally:
        db.close()


def cafe24_proactive_refresh_job():
    """cafe24 Access Token 만료 30분 전 자동 갱신.

    동시성 직렬화는 cafe24.py의 _CAFE24_REFRESH_LOCK이 담당한다.
    (로컬 락 제거 — sync 경로와 공통 단일 락으로 통합)
    """
    db = _get_own_db_session()
    try:
        from app.clients.cafe24 import Cafe24Client
        from app.config import get_cafe24_config

        channel = db.query(Channel).filter(Channel.code == "CAFE24").first()
        if not channel:
            return

        token_row = db.query(OAuthToken).filter(OAuthToken.channel_id == channel.id).first()
        if not token_row or not token_row.refresh_token:
            return

        now = kst_now()

        # refresh token 만료 확인
        if token_row.refresh_token_expires_at and token_row.refresh_token_expires_at <= now:
            log.error("[스케줄러] cafe24 Refresh Token 만료! 재인증 필요")
            return

        # refresh token 만료 3일 전 경고
        if token_row.refresh_token_expires_at:
            days_left = (token_row.refresh_token_expires_at - now).days
            if days_left <= 3:
                log.warning("[스케줄러] cafe24 Refresh Token %d일 후 만료! 재인증 권장", days_left)

        # access token이 30분 이상 남았으면 스킵
        if token_row.expires_at and token_row.expires_at > now + timedelta(minutes=30):
            return

        config = get_cafe24_config("CAFE24")
        if not config:
            return

        def _on_refreshed(access_token, refresh_token, expires_at, refresh_expires_at):
            token_row.access_token = access_token
            token_row.refresh_token = refresh_token
            token_row.expires_at = expires_at
            token_row.refresh_token_expires_at = refresh_expires_at
            db.commit()

        def _token_reader():
            fresh = db.query(OAuthToken).filter(OAuthToken.channel_id == channel.id).first()
            if fresh:
                db.refresh(fresh)  # identity map 캐시 무효화 → DB 최신값 강제 로드
                return (fresh.refresh_token, fresh.access_token, fresh.expires_at)
            return (None, None, None)

        client = Cafe24Client(
            config,
            access_token=token_row.access_token,
            refresh_token=token_row.refresh_token,
            on_token_refreshed=_on_refreshed,
            token_reader=_token_reader,
        )
        new_token = client._refresh_access_token()
        if new_token:
            log.info("[스케줄러] cafe24 토큰 사전 갱신 완료")
        else:
            log.error("[스케줄러] cafe24 토큰 사전 갱신 실패")

    except Exception as e:
        log.exception("[스케줄러] cafe24_proactive_refresh 에러: %s", e)
        raise  # 삼킴 정렬(S5b): EVENT_JOB_ERROR로 표면화(워치독 allowlist 제외지만 일관성)
    finally:
        db.close()


def sync_naver_settlement_job():
    """네이버 일별 정산 내역 자동 적재 (트랙 N1, 05:25 KST). 최근 35일."""
    db = _get_own_db_session()
    try:
        from app.config import get_naver_config
        from app.clients.naver import NaverClient
        from app.routers.naver_ops import _upsert_settlement

        cfg = get_naver_config("NAVER")
        if not cfg:
            log.error("[스케줄러] 네이버 설정 없음 — 정산 동기화 건너뜀")
            return
        dto = kst_today()
        dfrom = dto - timedelta(days=34)
        rows = NaverClient(cfg).fetch_daily_settlement(dfrom, dto)
        n = _upsert_settlement(db, rows)
        log.info("[스케줄러] 네이버 일별 정산 %d건 적재 완료 (%s~%s)", n, dfrom, dto)
    except Exception as e:
        log.exception("[스케줄러] sync_naver_settlement_job 에러: %s", e)
        raise  # 삼킴 정렬(S5b): EVENT_JOB_ERROR/HTTP500로 실패 표면화
    finally:
        db.close()


def sync_naver_case_settlement_job():
    """네이버 건별 정산(실측 수수료) 자동 적재 (트랙 N1·D-6, 05:30 KST). 결제일 기준 최근 45일."""
    db = _get_own_db_session()
    try:
        from app.config import get_naver_config
        from app.clients.naver import NaverClient
        from app.routers.naver_ops import _upsert_case_settlement

        cfg = get_naver_config("NAVER")
        if not cfg:
            log.error("[스케줄러] 네이버 설정 없음 — 건별 정산 동기화 건너뜀")
            return
        dto = kst_today()
        dfrom = dto - timedelta(days=44)
        rows = NaverClient(cfg).fetch_case_settlement(dfrom, dto)
        n = _upsert_case_settlement(db, rows)
        log.info("[스케줄러] 네이버 건별 정산 %d건 적재 완료 (결제일 %s~%s)", n, dfrom, dto)
    except Exception as e:
        log.exception("[스케줄러] sync_naver_case_settlement_job 에러: %s", e)
        raise  # 삼킴 정렬(S5b): EVENT_JOB_ERROR/HTTP500로 실패 표면화
    finally:
        db.close()


def _ensure_default_states(db):
    """기본 스케줄러 상태 DB 레코드 생성"""
    defaults = [
        ("auto_sync_orders", "0 6 * * *"),
        ("auto_profit_calc", "30 6 * * *"),
        ("cafe24_token_refresh", "*/30 * * * *"),
        ("sync_naver_sa_ad_costs", "0 7 * * *"),
        ("sync_naver_ad_daily", "30 7 * * *"),      # 네이버 SA 일별 성과+BEP (트랙 P0)
        ("snapshot_naver_ad_hourly", "5 * * * *"),  # 네이버 SA 시간별 스냅샷 (빠른 루프)
        ("trigger_watch", "7 * * * *"),             # 조건발동 즉시알림(페이싱·CPC급등, 트랙 P4)
        ("sync_naver_entity", "35 7 * * *"),        # 엔티티 인벤토리 동기화 (트랙 P2-S1)
        ("sync_naver_search_term", "40 7 * * *"),   # 검색어 단위 성과 수집 (트랙 P2-S1)
        ("sync_naver_keyword_volume", "0 9 * * 0"), # 저클릭 키워드 월검색량 (주1회, 일요일)
        ("run_naver_forecast_engine", "50 7 * * *"),  # 캠페인 grain 예측엔진(게이트→모델→채점, F1)
        ("generate_naver_proposals", "0 8 * * *"),  # 네이버 SA 제안 자동생성(진단→시뮬→제안→Slack, 트랙 P2-S3)
        ("generate_expert_desk", "5 8 * * *"),  # 전문가(Ava) 검토 데스크(E1a, PLAN §8)
        ("run_naver_learning_loops", "10 8 * * *"),  # 학습루프 4종(성적표·예측편향·전환성숙·시간대분포, 트랙 P6)
        ("run_naver_retro_scoring", "30 8 * * *"),  # 상설 소급 채점(진단 보드 as-of 리플레이 + 페이싱 경보, D-NAO-45)
        ("run_naver_diary_reflection", "35 8 * * *"),  # 운영 일기 해석층(결과 소급 기입+해석문, D-NAO-54 P2)
        ("run_naver_wisdom", "45 8 * * *"),  # 운영 일기 지혜 승격·망각층(후보→판사→지혜+보고→망각, D-NAO-54 P3)
        ("run_naver_vault_export", "5 9 * * *"),  # 운영 일기·지혜 Obsidian 볼트 export(열람층, D-NAO-54 P5)
        ("shopping_ad_product_sync", "20 8 * * *"),  # 쇼핑 그룹↔상품 매핑(상품 파생 target 소스, D-NAO-57 A)
        ("run_naver_auto_operator_daily", "50 8 * * *"),  # D-NAO-48 4조건 심사·집행 서버화(D-NAO-49)
        ("sweep_naver_keyword_hourly", "10 9 * * *"),  # 키워드/쇼핑그룹 시간별(hh24) 축적, D-1 스윕(D-NAO-46②)
        ("run_naver_auto_operator_hourly", "20 * * * *"),  # 시간당 밴드 관제 실입찰(catch-up 제외, D-NAO-49)
        ("run_naver_flight_loop", "15 */2 * * *"),  # 당일 플라이트 루프 2시간 주기(X2, dry_run=True)
        ("sync_naver_settlement", "25 5 * * *"),
        ("sync_naver_case_settlement", "30 5 * * *"),
        ("sync_meta_ad_costs", "0 7 * * *"),
        ("sync_coupang_rg_inbound", "20 5 * * *"),
        ("sync_coupang_rg_settlement", "30 5 * * *"),
        ("auto_download_rg_settlement", "15 6 * * *"),
        ("sync_coupang_products", "30 5 * * *"),
        ("sync_coupang_rg_sizes", "35 5 * * *"),
        ("sync_coupang_rg_inventory", "40 5 * * *"),
        ("sync_coupang_returns", "45 5 * * *"),
        ("sync_coupang_settlement", "50 5 * * *"),
        ("sync_coupang_rg_orders", "0 */2 * * *"),
        ("sync_coupang_coupons", "0 6 * * *"),
        ("sync_coupang_cs", "5 6 * * *"),
        ("sync_coupang_ad_cost", "10 0 * * *"),
        # 03:00 야간 브릿지 추가 — keycloak 세션(~12h)이 밤사이 만료되는 빈틈 제거.
        # 20:00(마지막 주간 갱신)→03:00=7h, 03:00→10:00=7h, 둘 다 <12h라 세션이 끊기지 않는다.
        # 03:00에 Mac이 깨어 있어야 페처가 갱신 처리(pmset repeat wakeorpoweron 02:58 필요).
        ("request_ad_cost_refresh", "0 3,10-20 * * *"),
    ]
    for name, cron in defaults:
        existing = db.query(SchedulerState).filter(
            SchedulerState.job_name == name
        ).first()
        if not existing:
            db.add(SchedulerState(job_name=name, cron_expression=cron, is_enabled=True))
    db.commit()


# 네이버 아침배치 catch-up. misfire_grace_time은 프로세스 재시작을 못 잡는다(in-memory
# jobstore라 재시작 시 과거 발화 기록 소실 → misfire 미인식, codex 2026-07-13 [P1]).
# 그래서 SchedulerState.last_run_at(마지막 '성공' 시각)로 오늘 예정 발화를 놓쳤는지 명시적
# 판정해 따라잡는다. 범위는 네이버 아침배치 한정(Jino 결정 2026-07-13) — 쿠팡 등 blast
# radius 제외. ★순서 중요(codex [P1] R2): 이 잡들은 의존 스태거(forecast→proposals→expert
# →learning). expert_desk는 pending 제안 0이면 '성공 스킵'하므로 proposals보다 먼저 돌면
# 오늘 전문가검토가 영구 스킵된다. 따라서 동시 발화 금지 — cron 순서로 순차 실행하고 상류가
# 성공해야 하류를 잇는다. keyword_hourly sweep은 다른 잡에 의존하지 않지만(자체 완결) 09:10
# 표준 cron이라 같은 catch-up 목록에 포함(D-NAO-46②) — 순서상 맨 뒤(가장 늦은 cron).
_CATCHUP_ORDER: tuple[str, ...] = (
    "run_naver_forecast_engine",   # 07:50
    "generate_naver_proposals",    # 08:00
    "generate_expert_desk",        # 08:05 (proposals 성공 후라야 pending>0 → 의미 있음)
    "run_naver_learning_loops",    # 08:10
    "shopping_ad_product_sync",    # 08:20 (D-NAO-57 A, 레인 전 — 상품 파생 target 매핑 갱신. fail-open이라 실패해도 체인 안 끊김)
    "run_naver_retro_scoring",     # 08:30 (D-NAO-45, 비정형 아닌 표준 cron이라 catch-up 포함)
    "run_naver_auto_operator_daily",  # 08:50 (D-NAO-49, 조건④ bleeding 판정이 retro_scoring 결과를 쓴다 — 그 뒤)
    "run_naver_diary_reflection",  # 08:35 크론이지만 catch-up은 집행(08:50) *뒤*(P2 리뷰 P2-1: LLM 재시도 최대 9분이 돈 잡 복구를 지연시키면 안 됨 — 관찰 전용이라 어제/D-2/D-8 버킷은 순서 무관. fail-open은 영구 블록만 막고 지연은 못 막는다)
    "sweep_naver_keyword_hourly",  # 09:10 (D-NAO-46②, 독립 잡이나 표준 cron catch-up 포함)
    "run_naver_wisdom",  # 08:45 크론이지만 catch-up은 돈 잡(08:50)·reflection 뒤(D-NAO-54 P3): reflection이 outcome을 소급 기입해야 후보 수확 결과가 최신 + LLM 판사(최대 회당 5×재시도)가 집행 복구를 지연시키면 안 됨. 관찰·보고 전용이라 맨 뒤 배치(diary_outcome 60일 하한 내면 하루 늦어도 무관). fail-open은 영구 블록만 막고 지연은 못 막는다
    "run_naver_vault_export",  # 09:05 크론이지만 catch-up은 맨 뒤(D-NAO-54 P5): 일기·지혜를 마크다운으로 재생성하는 열람 전용 잡이라 wisdom(승격)까지 끝난 뒤에 도는 게 볼트 최신성에 맞고, 파일 IO가 집행 복구를 지연시키면 안 된다. 매 실행 전체 재생성(멱등)이라 하루 늦어도 무해. fail-open은 영구 블록만 막고 지연은 못 막는다
)
_CATCHUP_LOOKBACK = timedelta(hours=12)  # 오늘 예정 발화가 이보다 오래됐으면 스킵(다음 정상 발화에 위임)


def _missed_morning_jobs(db, now):
    """오늘 예정 발화를 놓친 아침배치 잡을 cron(=의존) 순서로 반환.

    판정: 오늘 예정 발화 시각(cron hour:minute) <= now 이고, last_run_at이 그 시각 이전
    (또는 없음)이며, 12h 이내면 놓친 것. 비정형 cron(*/n·범위)은 안전하게 제외.
    """
    missed: list[str] = []
    for job_name in _CATCHUP_ORDER:
        state = db.query(SchedulerState).filter(
            SchedulerState.job_name == job_name
        ).first()
        if state is None or not state.is_enabled:
            continue
        try:
            parts = (state.cron_expression or "").split()
            minute, hour = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            continue
        scheduled_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < scheduled_today:
            continue
        if (now - scheduled_today) > _CATCHUP_LOOKBACK:
            continue
        if state.last_run_at is not None and state.last_run_at >= scheduled_today:
            continue
        missed.append(job_name)
    return missed


def _record_catchup_status(job_name, *, ok, exception=None):
    """catch-up 직접 호출은 스케줄러 리스너(EVENT_JOB_*)를 안 거치므로 SchedulerState를
    수동 갱신한다. 리스너와 동일한 _apply_job_event를 재사용 → last_run_at='마지막 성공'
    의미가 일관(실패 시 last_run_at 미갱신=다음 재시작 재시도, 워치독 stale 판정과 정합).
    """
    db = _get_own_db_session()
    try:
        state = db.query(SchedulerState).filter(
            SchedulerState.job_name == job_name
        ).first()
        if state is None:
            return
        code = EVENT_JOB_EXECUTED if ok else EVENT_JOB_ERROR
        _apply_job_event(state, code, kst_now(), exception=exception)
        db.commit()
    except Exception as e:  # noqa: BLE001 — 상태 기록 실패가 체인을 죽이면 안 됨
        log.exception("[스케줄러] catch-up 상태 기록 실패(%s): %s", job_name, e)
    finally:
        db.close()


def _run_proposals_catchup_verified():
    """proposals catch-up 전용 실행 — run_daily를 직접 호출해 '실제 완주'를 확인한다.

    generate_naver_proposals_job(래퍼)는 run_daily가 freshness stale·proposal_writer 실패를
    result.stage_status로 삼켜 정상 반환해도 예외를 안 던진다(부분 실패도 정상 흐름 설계).
    그러면 catch-up 체인이 '성공'으로 오인해 last_run_at을 전진시키고 하류(expert)를 잇는다
    (codex R3 [P1-a]). 그래서 catch-up 경로에선 stage_status를 직접 검사해, 제안이 실제로
    생성·커밋되는 단계(freshness+proposal_writer)가 ok가 아니면 예외를 던져 체인을 중단시킨다
    (last_run_at 미전진 → 다음 재시작/다음날 크론 재시도). generated=0(전부 dedup)은 정상 완주.
    """
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.proposal_pipeline import run_daily

        result = run_daily(db)
        ss = result.get("stage_status", {})
        if ss.get("freshness") != "ok" or ss.get("proposal_writer") != "ok":
            raise RuntimeError(
                f"proposals 미완주 → catch-up 체인 중단: stage_status={ss} errors={result.get('errors')}"
            )
        log.info("[스케줄러] catch-up proposals 완주 확인: generated=%s", result.get("generated"))
    finally:
        db.close()


def _catch_up_morning_batch():
    """스케줄러 기동 시, 재시작으로 놓친 네이버 아침배치를 cron 순서로 순차 따라잡는다.

    2026-07-13 실사고: pm2가 08:53 재생성되며 07:50 예측·08:00 제안·08:05 전문가·08:10 학습이
    catch-up 없이 드롭(account_brief 누락). misfire_grace_time은 재시작을 못 잡아(codex [P1])
    last_run_at 기반 명시 catch-up으로 보완.

    ★순차 실행(codex [P1] R2): 별도 데몬 스레드에서 놓친 잡을 cron 순서로 하나씩 동기 실행 —
    상류가 성공해야 하류를 잇는다(상류 실패 시 체인 중단→다음 재시작 재시도). expert_desk가
    proposals보다 먼저 도는 것을 원천 차단(pending 0 오탐→영구 스킵 방지). 리스너 미경유라
    성공/실패를 _record_catchup_status로 직접 기록. 재실행은 persist dedup·account_brief
    싱글톤으로 멱등이라 다중 재시작에도 안전(성공분은 last_run_at 갱신되어 재catch-up 제외).
    """
    now = kst_now()  # naive KST — last_run_at도 kst_now()로 저장돼 동일 기준
    db = _get_own_db_session()
    try:
        missed = _missed_morning_jobs(db, now)
    except Exception as e:  # noqa: BLE001 — 감지 실패가 스케줄러 기동을 막으면 안 됨
        log.exception("[스케줄러] 아침배치 catch-up 감지 에러(무시): %s", e)
        return
    finally:
        db.close()
    if not missed:
        return

    funcs = {
        "run_naver_forecast_engine": run_naver_forecast_engine_job,
        # proposals는 완주 검증판 사용(codex R3 P1-a): stage_status로 실제 생성 확인,
        # 미완주면 예외 → 체인 중단(expert가 pending 0으로 오실행되는 것 원천 차단).
        "generate_naver_proposals": _run_proposals_catchup_verified,
        "generate_expert_desk": generate_expert_desk_job,
        "run_naver_learning_loops": run_naver_learning_loops_job,
        "shopping_ad_product_sync": shopping_ad_product_sync_job,
        "run_naver_retro_scoring": run_naver_retro_scoring_job,
        "run_naver_diary_reflection": run_naver_diary_reflection_job,
        "run_naver_auto_operator_daily": run_naver_auto_operator_daily_job,
        "sweep_naver_keyword_hourly": sweep_naver_keyword_hourly_job,
        "run_naver_wisdom": run_naver_wisdom_job,
        "run_naver_vault_export": run_naver_vault_export_job,
    }
    log.warning("[스케줄러] 아침배치 catch-up 대상(cron순 순차): %s", missed)

    def _run_chain():
        for job_name in missed:  # 이미 cron(의존) 순서
            func = funcs.get(job_name)
            if func is None:
                continue
            try:
                log.warning("[스케줄러] catch-up 순차 실행: %s", job_name)
                func()  # 동기 실행(각 잡이 자체 db 세션·예외 처리, 성공 시 정상 반환)
            except Exception as e:  # noqa: BLE001 — 상류 실패 시 하류 중단(의존 보존)
                log.exception(
                    "[스케줄러] catch-up %s 실패 → 체인 중단(다음 재시작 재시도): %s", job_name, e
                )
                _record_catchup_status(job_name, ok=False, exception=e)
                break
            _record_catchup_status(job_name, ok=True)

    threading.Thread(target=_run_chain, name="naver-morning-catchup", daemon=True).start()


def job_func_for(job_name: str):
    """job_name → 등록할 잡 함수(단일 진실). 매핑에 없으면 None.

    ★start_scheduler와 toggle 라이브 등록이 같은 매핑을 봐야 한다 — 한쪽만 알면 toggle이
    재시작 없이 살릴 잡을 못 찾아 DB만 바뀌고 실제 미가동(쿠팡 광고비 13일 정지의 뿌리).
    """
    return {
        "auto_sync_orders": sync_all_channels_job,
        "auto_profit_calc": recalculate_profit_job,
        "cafe24_token_refresh": cafe24_proactive_refresh_job,
        "sync_naver_sa_ad_costs": sync_naver_sa_ad_costs_job,
        "sync_naver_ad_daily": sync_naver_ad_daily_job,
        "snapshot_naver_ad_hourly": snapshot_naver_ad_hourly_job,
        "trigger_watch": trigger_watch_job,
        "sync_naver_entity": sync_naver_entity_job,
        "shopping_ad_product_sync": shopping_ad_product_sync_job,
        "sync_naver_search_term": sync_naver_search_term_job,
        "sync_naver_keyword_volume": sync_naver_keyword_volume_job,
        "run_naver_forecast_engine": run_naver_forecast_engine_job,
        "generate_naver_proposals": generate_naver_proposals_job,
        "run_naver_learning_loops": run_naver_learning_loops_job,
        "run_naver_retro_scoring": run_naver_retro_scoring_job,
        "run_naver_diary_reflection": run_naver_diary_reflection_job,
        "run_naver_wisdom": run_naver_wisdom_job,
        "run_naver_vault_export": run_naver_vault_export_job,
        "sweep_naver_keyword_hourly": sweep_naver_keyword_hourly_job,
        "run_naver_auto_operator_daily": run_naver_auto_operator_daily_job,
        "run_naver_auto_operator_hourly": run_naver_auto_operator_hourly_job,
        "generate_expert_desk": generate_expert_desk_job,
        "run_naver_flight_loop": run_naver_flight_loop_job,
        "sync_naver_settlement": sync_naver_settlement_job,
        "sync_naver_case_settlement": sync_naver_case_settlement_job,
        "sync_meta_ad_costs": sync_meta_ad_costs_job,
        "sync_coupang_products": sync_coupang_products_job,
        "sync_coupang_returns": sync_coupang_returns_job,
        "sync_coupang_settlement": sync_coupang_settlement_job,
        "sync_coupang_rg_sizes": sync_coupang_rg_sizes_job,
        "sync_coupang_rg_inventory": sync_coupang_rg_inventory_job,
        "sync_coupang_rg_orders": sync_coupang_rg_orders_job,
        "sync_coupang_rg_inbound": sync_coupang_rg_inbound_job,
        "sync_coupang_rg_settlement": sync_coupang_rg_settlement_job,
        "auto_download_rg_settlement": auto_download_rg_settlement_job,
        "sync_coupang_coupons": sync_coupang_coupons_job,
        "sync_coupang_cs": sync_coupang_cs_job,
        "sync_coupang_ad_cost": sync_coupang_ad_cost_job,
        "request_ad_cost_refresh": request_ad_cost_refresh_job,
    }.get(job_name)


def job_kwargs_for(job_name: str) -> dict:
    """job별 add_job 추가 옵션(단일 진실 — start_scheduler·toggle 라이브 등록 공용).

    hourly 레인만 per-job misfire 5분(codex 9R[P2]: 놓친 :20은 폐기 — catch-up 제외
    정책과 정합). 그 외 잡은 전역 기본(3600s) 상속.
    """
    if job_name == "run_naver_auto_operator_hourly":
        return {"misfire_grace_time": _AUTO_OPERATOR_HOURLY_MISFIRE_GRACE}
    return {}


def start_scheduler():
    """스케줄러 시작 — 기본 작업 2개 등록"""
    db = _get_own_db_session()
    try:
        _ensure_default_states(db)

        states = db.query(SchedulerState).all()
        for state in states:
            if not state.is_enabled:
                continue

            job_func = job_func_for(state.job_name)

            if job_func:
                try:
                    # from_crontab는 timezone 미지정 시 서버 로컬 TZ(프로덕션=UTC)로
                    # 폴백한다. 미리 만든 trigger를 add_job에 넘기면 스케줄러의
                    # 기본 timezone이 적용되지 않으므로 KST를 명시한다.
                    trigger = CronTrigger.from_crontab(
                        state.cron_expression, timezone="Asia/Seoul"
                    )
                    per_job_kwargs = job_kwargs_for(state.job_name)
                    scheduler.add_job(
                        job_func,
                        trigger=trigger,
                        id=state.job_name,
                        replace_existing=True,
                        **per_job_kwargs,
                    )
                    log.info("스케줄러 작업 등록: %s (%s)", state.job_name, state.cron_expression)
                except Exception as e:
                    log.error("스케줄러 작업 등록 실패 (%s): %s", state.job_name, e)
    finally:
        db.close()

    # 워치독 SA① — 잡 실행 결과를 SchedulerState에 중앙 기록(인라인 스탬핑 대체).
    scheduler.add_listener(
        _job_state_listener,
        EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED,
    )

    scheduler.start()
    log.info("스케줄러 시작됨")

    # 재시작으로 놓친 네이버 아침배치를 즉시 따라잡는다(misfire_grace_time이 재시작을
    # 못 잡는 구멍 보완, codex [P1] 2026-07-13). 잡 등록·start 이후라야 get_job/modify 가능.
    _catch_up_morning_batch()


def stop_scheduler():
    """스케줄러 종료"""
    try:
        scheduler.shutdown(wait=False)
        log.info("스케줄러 종료됨")
    except Exception as e:
        log.error("스케줄러 종료 에러: %s", e)
