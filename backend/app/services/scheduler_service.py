# scheduler_service.py — APScheduler 기반 자동 동기화/이익률 계산 스케줄러
from __future__ import annotations

import logging
import os
import threading
from app.utils.kst import kst_now, kst_today
from datetime import date, timedelta

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

# 네이버 시간별 주문 동기화 전용 misfire 유예 — 전역 3600s를 상속하면 재시작 직후 최대
# 1시간 늦게 발화한다. 근시간 보정 레인이라 그 시점엔 이미 다음 정시가 임박했으므로
# _AUTO_OPERATOR_HOURLY_MISFIRE_GRACE와 같은 패턴으로 300초만 허용(catch-up 정책과 정합).
_NAVER_ORDERS_HOURLY_MISFIRE_GRACE = 300


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


def sync_naver_orders_hourly_job():
    """네이버 스마트스토어 주문 시간별 동기화 (스케줄러 작업) — 근시간 공백 메움 전용.

    ★왜 스마트스토어 전용인가: 2026-07-28 실사고 — 근시간 동기화의 유일한 트리거가
    프런트엔드 마운트 시 호출되는 POST /api/sync/realtime였고, 서버 자동 실행은
    auto_sync_orders 일 1회(06:00)뿐이라, 아무도 대시보드를 열지 않으면 7시간 넘게
    동기화 시도 자체가 0건이었다(sync_log 6채널 전부 공백 실증). 네이버 광고 성과를
    당일 스마트스토어 실주문으로 대조하는 작업이 이 공백에 막혀 스마트스토어부터
    좁혀서 잡는다(Jino 확정 2026-07-28). 쿠팡(Wing/RG)·cafe24는 같은 공백에 걸리지만
    이번 스코프 밖 — "이왕 하는 김에" 확대하지 않는다.
    ★왜 7일 창(date_from=None)인가: sync_channel_orders 기본창(kst_today()-7일)을 그대로
    쓴다. 06:00 일 배치(sync_all_channels_job)는 취소 반영을 위해 30일 넓은 창을 쓰지만,
    그건 하루 1회면 충분한 몫이다. 이 잡은 시간마다 도는 좁은 보정 레인이라 좁은 창으로
    충분하고, 30일 창을 매시간 돌리면 불필요한 API 부하만 늘어난다.
    ★왜 RG 미포함인가: RG 주문 동기화(sync_all_rg_orders)는 이번 스코프 밖(Jino 확정) —
    sync_all_channels_job을 참고 삼되 그 호출은 절대 가져오지 않는다.
    ★왜 catch-up 제외인가: 시간성이 소멸하는 근시간 보정 레인이다. 놓친 회차를 뒤늦게
    따라잡아 봐야 그 시각의 "근시간" 가치가 없다 — 다음 정시가 재기회(_AUTO_OPERATOR_HOURLY_MISFIRE_GRACE와
    같은 사상, run_naver_auto_operator_hourly_job 참고).
    역할 분담: 06:00 일 배치 = 넓은 30일 창 + 전 채널 + RG(취소 정합 포함) / 이 잡 = 좁은
    7일 창 + 스마트스토어 단독(근시간 공백 메움).
    """
    db = _get_own_db_session()
    try:
        from app.services.sync_service import sync_channel_orders

        # ★channel_id 하드코딩 금지(DB 시퀀스라 취약) — platform으로 대상 선정.
        # api_type != "excel"은 방어적 계약 명시(현재 네이버 채널은 excel 아님이 라이브 실측이나,
        # 엑셀 전용 채널이 실수로 naver platform으로 등록돼도 sync_channel_orders 호출을 막는다).
        channels = db.query(Channel).filter(
            Channel.platform == "naver", Channel.api_type != "excel"
        ).all()
        if not channels:
            log.info("[스케줄러] sync_naver_orders_hourly_job: 대상 네이버 채널 없음 — 건너뜀")
            return

        for ch in channels:
            try:
                result = sync_channel_orders(db, ch.id, None, None)
                log.info(
                    "[스케줄러] 네이버 시간별 주문 동기화 완료: 채널 %s → %s (신규: %s, 취소반영: %s)",
                    ch.name, result.get("status"), result.get("new_orders"),
                    result.get("reconciled_cancelled"),
                )
            except Exception as e:
                log.error("[스케줄러] 네이버 시간별 주문 동기화 채널 %s 에러: %s", ch.name, e)

    except Exception as e:
        log.exception("[스케줄러] sync_naver_orders_hourly_job 에러: %s", e)
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


def sync_naver_display_ad_costs_job():
    """ADVoost 쇼핑·성과형 디스플레이(GFA) 광고비 어제치 적재 (07:10 KST).

    검색광고(07:00)와 분리된 잡인 이유: 출처가 다르다(리포트 vs 비즈머니 실차감). 한 잡에
    묶으면 성과형 조회가 실패했을 때 검색광고 적재까지 같이 죽는다.
    """
    db = _get_own_db_session()
    try:
        from app.services.naver_display_ad_costs import sync_display_ad_costs

        yesterday = kst_today() - timedelta(days=1)
        result = sync_display_ad_costs(db, yesterday, yesterday)
        db.commit()
        log.info("[스케줄러] 성과형 광고비 %d건 적재 완료 (%s, 합계 %s)",
                 result["written"], yesterday, result["total"])
    except Exception as e:
        log.exception("[스케줄러] sync_naver_display_ad_costs_job 에러: %s", e)
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

        # D-NAO-140: 소재(광고) grain 적재 + 같은 날 대조. **같은 창·같은 보고서**를 쓴다.
        # ★fail-open으로 감싼다 — 이건 측정 부가 축이고, 여기서 터져도 위의 머니 경로
        #   (naver_ad_daily·BEP)는 이미 커밋됐다. 부가기능이 본 기능을 죽이면 안 된다.
        try:
            from app.services.naver_ad.ad_creative_daily_sync import sync as sync_creative

            log.info("[스케줄러] naver_ad_creative_daily: %s",
                     sync_creative(db, date_from=start, date_to=end))
        except Exception as e:  # noqa: BLE001
            log.exception("[스케줄러] naver_ad_creative_daily 적재 실패(무시하고 진행): %s", e)
            db.rollback()
    except Exception as e:
        log.exception("[스케줄러] sync_naver_ad_daily_job 에러: %s", e)
        raise  # 삼킴 정렬(S5b): cron 경로 EVENT_JOB_ERROR로 표면화
    finally:
        db.close()


def snapshot_naver_ad_hourly_job():
    """네이버 SA 시간별 캠페인 스냅샷 (매시간, 당일 누적). 빠른 루프(D-NAO-4) 데이터 기반.

    ★결과 dict를 **반환한다** — 수동 트리거(routers/scheduler.trigger_job)가 그걸 응답에 실어
      "적재했는가 / 같은 시각이라 건너뛰었는가"를 누른 사람에게 보여준다. 반환을 삼키면
      skip이 "작업 실행 완료"로만 보이고, 그건 가드가 있으나 없으나 화면이 같다는 뜻이다.
      (APScheduler는 잡의 반환값을 무시하므로 크론 경로엔 영향 없다.)
    """
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.hourly_snapshot import snapshot_hourly

        result = snapshot_hourly(db)
        log.info("[스케줄러] naver_ad hourly snapshot: %s", result)
        return result
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
    """네이버 SA 엔티티(캠페인/그룹/키워드) 인벤토리 동기화 (07:35 KST, P2-S1).

    ★완료 직후 BM 레이어를 체이닝 호출한다(구 07:37 독립 크론 폐지). 이유 = 구조적 경합:
    entity_sync는 45캠페인+1,004그룹+~91,099키워드를 순차 fetch한 뒤 **맨 끝에서 한 번**
    commit해서(entity_sync.py 단일 commit) 실제 commit 시각이 07:37을 넘긴다 → 구 07:37 크론의
    naver_entity_snapshot(D)이 항상 entity_sync(D-1) 값을 담았다(2026-07-23~27 5/5일 라이브
    실측, 스냅샷 commit이 entity_sync commit보다 +2.9s~+50.9s 먼저). 크론 시각을 뒤로 미루는
    것은 fetch 소요가 가변이라 근본 해결이 아니므로, '완료'를 신호로 삼는 체이닝으로 바꾼다.

    sync가 실패해도 BM은 실행한다 — 구 07:37 크론이 sync 성공 여부와 무관하게 발화하던 것과
    동일(스냅샷 연속성 유지). 단 체이닝은 이 잡의 성공/실패 판정을 절대 바꾸면 안 되므로
    non-throwing 경계로 감싼다(codex R1 [P1]): run_naver_bm_layer_job은 세션 생성이 try 밖이고
    finally의 db.close()도 안 삼켜서, 그 둘 중 하나가 던지면 finally에서 성공한 sync를 실패로
    뒤집거나 진행 중인 sync 예외를 BM 예외로 교체한다(예외 마스킹).
    """
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
        # 세션을 닫은 뒤(=단일 commit 확정 후) 체이닝 — 이 순서라야 스냅샷이 당일 sync 결과를 본다.
        _chain_bm_layer_after_entity_sync()


def _chain_bm_layer_after_entity_sync():
    """entity_sync 완료 후 BM 레이어 체이닝 — 어떤 예외도 상류로 새지 않는 경계.

    finally에서 호출되므로 여기서 예외가 나가면 sync 잡의 결과를 덮어쓴다(성공→실패 오판,
    또는 sync 예외 교체 → EVENT_JOB_ERROR가 엉뚱한 원인을 보고). BM은 관찰 전용이라
    sync의 last_run_at·에러 표면화에 영향을 줄 자격이 없다(§0 금지선 5와 동일 정신).
    """
    try:
        run_naver_bm_layer_job()
    except Exception as e:  # noqa: BLE001 — 관찰 잡이 상류 sync 판정을 바꾸면 안 됨(codex R1 [P1])
        log.exception("[스케줄러] BM 체이닝 실패(무시, sync 판정 보존): %s", e)


def run_naver_bm_layer_job():
    """BM(벤치마크) 학습 레이어 (D-NAO-78, PLAN_naver-ad-bm-layer.md §7).

    ★별도 크론이 아니다 — sync_naver_entity_job(07:35) 완료 직후 체이닝으로 실행된다(구 07:37
    크론은 폐지, 사유는 sync_naver_entity_job docstring 참조). naver_entity(DB)를 읽어 계정 전체
    45캠페인(대행사 포함)의 캠페인·그룹 grain 구조를 날짜별 스냅샷(SA-1, Phase 3 예산·확장검색
    GET 포함·관찰 전용). run_bm_layer 내부가 전면 fail-open이라 catch-up 체인에 독립 항목으로는
    넣지 않는다(관찰 잡, 놓치면 다음날 스냅샷이 이어짐 — 상류 sync_naver_entity가 catch-up되면
    체이닝으로 함께 따라잡힌다)."""
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.bm_harness import run_bm_layer

        result = run_bm_layer(db)
        log.info("[스케줄러] naver bm_layer: %s", result)
    except Exception as e:
        log.exception("[스케줄러] run_naver_bm_layer_job 에러(fail-open): %s", e)
    finally:
        db.close()


def run_naver_bm_deep_job():
    """BM 벤치마크 레이어 주간 deep 차원 (일요일 09:20 KST, D-NAO-78, PLAN_naver-ad-bm-layer.md §7).

    무거운 그룹별 GET(제외키워드·소재수, ~1,100 GET/주)을 일별 체이닝 레인(entity_sync 직후,
    구 07:37 크론)과 분리해 아침 집행 레인이 다 끝난 한산한 시간대(일요일)에 실행 — 07:40
    검색어 잡 지연 방지(§7). run_bm_deep
    내부가 전면 fail-open이라 catch-up 체인에는 넣지 않는다(관찰 잡, 놓쳐도 다음 주에 이어짐)."""
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.bm_harness import run_bm_deep

        result = run_bm_deep(db)
        log.info("[스케줄러] naver bm_deep: %s", result)
    except Exception as e:
        log.exception("[스케줄러] run_naver_bm_deep_job 에러(fail-open): %s", e)
    finally:
        db.close()


def shopping_ad_product_sync_job():
    """쇼핑 광고그룹↔상품 매핑 동기화 (07:45 KST, D-NAO-57 A, 관찰성 sync).

    관측 스코프 쇼핑 캠페인의 활성 그룹 /ncc/ads → naver_adgroup_product **upsert**(삭제 없음 —
    2026-08-03 codex 2R 이후 정리 계층 제거, 근거는 shopping_ad_product_sync 모듈 docstring).
    campaign_target_resolver 우선순위 ②(상품 파생 target)의 데이터 소스라 **08:00 제안 생성보다
    먼저** 돌아야 그날 제안이 최신 매핑 기반 target을 쓴다(리뷰 P2-2 — 07:30 BEP 산출 뒤·
    08:00 proposals 앞). fail-open — 매핑 sync 실패가 catch-up 체인 하류 집행 잡을 막으면 안
    된다(관찰성 잡, 실패 시 그날은 기존 매핑/③ 폴백으로 동작)."""
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.shopping_ad_product_sync import sync_adgroup_products

        # as_of는 필수 인자다(codex 2R P1-4 — 기본값 자체를 제거했다). 이 잡은 라이브 소재를
        # 수집하므로 sync_adgroup_products가 오늘이 아닌 날짜를 거부한다: catch-up이 날짜를
        # 넘겨 도는 일이 있으면 조용히 섞이지 않고 예외로 드러난다(fail-open 로그에 남는다).
        result = sync_adgroup_products(db, as_of=kst_today())
        log.info("[스케줄러] naver shopping_ad_product sync: %s", result)
    except Exception as e:
        log.exception("[스케줄러] shopping_ad_product_sync_job 에러(fail-open): %s", e)
    finally:
        db.close()


def sync_naver_search_term_job():
    """네이버 SA 검색어 단위 성과 수집 (07:40 KST, P2-S1). 최근 3일 창(사후정정 흡수).

    ★D-NAO-198: 같은 SHOPPINGKEYWORD_DETAIL 리포트의 col7/8/9(시간대·지역·매체) 축 적재를
    **같은 잡·같은 창**에 붙였다. 별도 크론으로 떼면 두 표의 커버리지가 갈라지는데, 원료
    리포트가 180일 뒤 사라지므로 그 갈라짐은 나중에 메울 수 없다.
    """
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.search_term_ingest import ingest_search_term_daily
        from app.services.naver_ad.search_term_dim_ingest import ingest_search_term_dimensions

        end = kst_today() - timedelta(days=1)
        start = end - timedelta(days=2)
        result = ingest_search_term_daily(db, start, end)
        log.info("[스케줄러] naver_search_term_daily ingest: %s", result)
        dim_result = ingest_search_term_dimensions(db, start, end)
        log.info("[스케줄러] naver_search_term_dim ingest: %s", dim_result)
    except Exception as e:
        log.exception("[스케줄러] sync_naver_search_term_job 에러: %s", e)
        raise
    finally:
        db.close()
def sync_naver_criterion_job():
    """연령·성별·관심사(CRITERION) 성과 분해 수집 (10:37 KST, D-NAO-203 · D-NAO-197 ②).

    ★창을 **`naver_ad_daily`와 똑같이 3일(D-1~D-3)로 맞춘다.** 이 표의 정합성 검산이
    「AG축 합계 ≡ `naver_ad_daily` 계정 합계」인데, 두 표의 사후정정 흡수 창이 다르면
    오래된 날짜에서 등식이 저절로 깨진다 — 그러면 «파싱이 틀렸나 창이 다른가»를 못 가른다.

    ★_CATCHUP_ORDER 제외: 3일 창이라 하루 유실은 다음 정상 발화가 그대로 메운다(D-1이
    내일의 D-2가 된다). ⚠️단 **3일을 넘는 정지는 스스로 못 메운다** — 리포트 재생성 한도가
    365일이라 그런 구멍은 손으로 백필해야 하고, 늦으면 영구 소실이다.

    ★코드 사전은 같은 잡에서 갱신한다(하루 1회, 2,202행). 별도 크론으로 떼면 사전과 성과의
    커버리지가 갈라지는데, 갈라진 사전은 「사전에 없는 코드」를 만들어 분석을 [미상]으로
    떨어뜨린다. fail-open으로 감싼다 — 사전 갱신 실패가 성과 적재를 죽이면 안 된다.
    """
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.criterion_ingest import (
            ingest_criterion_range,
            sync_criterion_dict,
        )

        end = kst_today() - timedelta(days=1)
        start = end - timedelta(days=2)  # 3일 창 — naver_ad_daily와 동일(위 docstring 참조)
        result = ingest_criterion_range(db, start, end, deadline_s=10 * 60)
        # ★skipped(리포트를 못 받은 날)는 «성공»이 아니다 — 로그 레벨로 갈라 둔다(교훈 #123).
        if result["skipped"] or result["failed"] or result["aborted"]:
            log.warning("[스케줄러] naver_criterion ingest(주의): %s", result)
        else:
            log.info("[스케줄러] naver_criterion ingest: %s", result)

        try:
            log.info("[스케줄러] naver_criterion_dict: %s", sync_criterion_dict(db))
        except Exception as e:  # noqa: BLE001
            log.exception("[스케줄러] criterion 사전 갱신 실패(성과 적재는 유지): %s", e)
            db.rollback()
    except Exception as e:
        log.exception("[스케줄러] sync_naver_criterion_job 에러: %s", e)
        raise
    finally:
        db.close()


def sync_naver_keyword_baseline_job():
    """★머리 키워드 검색량 «기준선» 시계열 적재 (매일, D-NAO-186 ①).

    ★위 `sync_naver_keyword_volume_job`과 **다른 잡이다 — 합치지 않는다**:
      그 잡은 «저클릭 키워드»(30일 클릭<10)를 골라 `NaverEntity.monthly_volume` 한 칸을
      **덮어쓴다**(D-NAO-18 3단분류 입력). 이 잡은 **돈이 닿은 머리 키워드**를 골라
      `naver_keyword_volume_daily`에 **하루 한 행씩 쌓는다**(기준선). 대상도 저장 모양도
      목적도 반대라, 한 함수로 비틀면 둘 다 애매해진다.
    ★왜 매일인가: 검색량은 «월» 단위 수치라 하루로는 잘 안 움직인다. 그래도 매일 받는 이유는
      네이버가 이 값을 언제 갱신하는지 `[미상]`이고, **아이폰 출시(매년 9월) 전후의 급변을
      놓치면 소급이 원리적으로 불가능**하기 때문이다(D-NAO-186 마감). 값이 안 변하면 행이
      같을 뿐 손해가 없다.
    ★09:50인 이유: `sync_naver_ad_daily`(07:30, 대상 선정의 원료)와 `sync_naver_entity`
      (07:35, keyword_id→이름 해석)가 끝난 뒤여야 한다. 그리고 :20·:45 혼잡대를 피한다.
    """
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.keyword_volume_baseline import sync_baseline

        result = sync_baseline(db)
        log.info("[스케줄러] keyword_volume_baseline: %s", result)
    except Exception as e:
        log.exception("[스케줄러] sync_naver_keyword_baseline_job 에러: %s", e)
        raise  # 삼킴 정렬(S5b): 실패를 EVENT_JOB_ERROR로 표면화
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
        # ★breakdown 없이 캠페인 수만 찍으면 전원 스킵도 정상 완주로 읽힌다(flight_loop
        # 실사고 2026-07-25~28). 결정/스킵/오류를 분해해서 남긴다 — 잡 로그만 보고도
        # "돌긴 돌았는데 아무 결정도 안 났다"가 보여야 한다.
        log.info(
            "[스케줄러] naver flight_loop: %d캠페인 (결정 %s, 스킵 %s %s, 오류 %s), dry_run=%s",
            result["campaigns_processed"], result.get("decided"), result.get("skipped"),
            result.get("skip_breakdown"), result.get("errors"), result.get("dry_run", True),
        )
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


def run_naver_profit_scorecard_job():
    """P7 일일 이익 스코어카드 — profit_scorecard.run_profit_scorecard (08:40 KST, D-NAO-85/
    ref39 P7, run_naver_diary_reflection 08:35 뒤·run_naver_wisdom 08:45 앞).

    목적함수(D-NAO-59 총이익 절대액)를 캠페인별로 매일 diary+Slack 표면화한다(관찰 전용,
    실쓰기 0). diary_reflection이 어제 일기를 해석문으로 소급 기입한 뒤 돌아야 볼트 노트가
    같은 날짜에 정합적으로 쌓인다(reflection 08:35 → 이 잡 08:40 → wisdom 08:45 순서).

    ★fail-open(관찰 전용, 집행 아님): reflection/wisdom 잡과 동일 이유로 예외를 다시 던지지
    않는다 — 스코어카드 실패가 catch-up 체인의 집행 잡(auto_operator_daily)을 막으면 안 된다
    (D-NAO-54 금지선과 동형: "관찰 실패가 집행을 막으면 안 됨").
    """
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.profit_scorecard import run_profit_scorecard

        result = run_profit_scorecard(db)
        log.info("[스케줄러] naver profit_scorecard: %s", result)
    except Exception as e:  # noqa: BLE001 — fail-open: 관찰 전용 잡, 집행 체인 보호(raise 없음)
        log.exception("[스케줄러] run_naver_profit_scorecard_job 에러(fail-open): %s", e)
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


def sweep_naver_today_hourly_job():
    """당일 그룹 grain 시간별(hh24) 축적 — 매시 :10 KST (D-NAO-122).

    :05 캠페인 스냅샷 뒤, :20 auto_operator 시간당 레인 앞에 둔다 — 레인이 같은 시각의
    당일 그룹 데이터를 읽을 수 있게. 진행 중인 시간 버킷은 저장하지 않고, 네이버 반영
    지연으로 빠진 시간은 다음 실행의 교체 upsert가 보완한다(관찰 전용, 쓰기 API 0).
    """
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.today_hourly_sweep import sweep_adgroup_hourly_today

        result = sweep_adgroup_hourly_today(db)
        log.info("[스케줄러] naver today_hourly sweep: %s", result)
    except Exception as e:
        log.exception("[스케줄러] sweep_naver_today_hourly_job 에러: %s", e)
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

    # SS3(검색어 제외 브리핑·제안 생성) — 일 레인과 같은 흐름에 편입(별 세션·fail-open:
    # 브리핑 실패가 일 레인 집행을 막지 않는다). 실쓰기 0(Confirm 전용) — 제안·diary만 생성.
    ss: dict = {}  # PX4 브리핑이 이 라운드 결과를 소비(원칙18-8) — 위가 실패해도 빈 dict로 침묵.
    # C6(codex 1R[P2] 자정 경계): 레인·브리핑이 하나의 now를 공유하도록 여기서 1회 산출한다.
    # 레인이 23:59:59에 돌고 브리핑이 방금 kst_now()로 자정을 넘기면 날짜가 어긋나(주간 일요일
    # 게이트·"오늘 제외/복귀" 카운트) 잘못 발화/침묵한다 — 같은 now를 그대로 전달해 봉합한다.
    ss_now = kst_now()
    db2 = _get_own_db_session()
    try:
        from app.services.naver_ad import bm_benchmark
        from app.services.naver_ad.search_term_ss_lane import run_search_term_ss_lane

        # BM P4(D-NAO-78): 대행사 검증 키워드셋을 승격 교차 프라이어로 주입(harness 유통·optional).
        # 조회 실패는 빈 셋(verified_keyword_set 자체 fail-open) → SS4 기존과 동일 출력(회귀 0).
        bm_prior = bm_benchmark.verified_keyword_set(db2)
        ss = run_search_term_ss_lane(db2, now=ss_now, bm_prior=bm_prior)
        log.info(
            "[스케줄러] naver 검색어 제외: shopping=%s powerlink=%s 자동발사=%s dedup=%s slot=%s "
            "capover=%s fail=%s / 재심사 개방=%s 재제외=%s 복귀=%s / 대행사=%s / 승격=%s bm교차=%s",
            ss["shopping_candidates"], ss["powerlink_candidates"],
            ss["powerlink_fired"], ss["deduped"], ss["slot_skipped"],
            ss["autofire_over_cap"], ss["autofire_failed"],
            ss["reexam_opened"], ss["reexam_reexcluded"], ss["reexam_restored"],
            ss["agency_powerlink_candidates"],
            ss["promote_proposals_created"], ss["promote_bm_crossed"],
        )
    except Exception as e:  # noqa: BLE001 — 브리핑 실패는 일 레인과 분리(fail-open)
        log.exception("[스케줄러] run_search_term_ss_lane 에러(fail-open): %s", e)
    finally:
        db2.close()

    # PX4(§4, D-NAO-80 후속): 파워링크 자동 제외/복귀 예외 브리핑 + 대행사 고비용 주간 브리핑.
    # 별도 세션·독립 try(fail-open) — 위 레인이 이미 커밋을 끝낸 뒤라 이 블록 실패는 PX2/PX3
    # 실쓰기를 되돌리지 않는다. ss가 빈 dict(위 실패)여도 run_exclusion_exception_briefing은
    # 전부 0으로 읽어 조용히 침묵(완전 fail-open, §4 1).
    db3 = _get_own_db_session()
    try:
        from app.services.naver_ad.search_term_px_briefing import (
            run_agency_powerlink_weekly_briefing,
            run_exclusion_exception_briefing,
        )

        excl_brief = run_exclusion_exception_briefing(db3, ss, now=ss_now)
        agency_brief = run_agency_powerlink_weekly_briefing(db3, now=ss_now)
        log.info("[스케줄러] naver PX4 브리핑: 제외/복귀=%s 대행사주간=%s", excl_brief, agency_brief)
    except Exception as e:  # noqa: BLE001 — 브리핑 실패는 실쓰기 레인과 분리(fail-open)
        log.exception("[스케줄러] PX4 브리핑 에러(fail-open): %s", e)
    finally:
        db3.close()

    # CS(콜드 스타트 첫 입찰) — ① 시장가 사다리 수집(npla-estimate) → ② 콜드 소재 첫 입찰 레인.
    # 별도 세션·독립 try(fail-open, 위 블록들과 동일 관례) — 이 레인이 실패해도 이미 커밋된
    # 일 레인/SS/PX 결과를 되돌리지 않는다. 수집(①)이 실패해도 레인(②)은 돌린다 —
    # load_today_ladder가 행 없음을 "시세 없음"으로 읽어 전건 보류하므로 안전하게 no-op이 된다.
    #
    # ★dry_run 기본값 = True(관측만·쓰기 없음). 실집행 전환은 prod .env에
    #   `NAVER_CS_DRY_RUN=0`을 넣고 재시작하는 것으로만 열린다(코드 배포 없이 되돌릴 수 있게).
    #   첫 배포 후에는 반드시 dry-run 회차의 제안값을 눈으로 확인한 뒤 전환한다.
    db4 = _get_own_db_session()
    try:
        from app.services.naver_ad.cold_start_bid_lane import (
            collect_market_bids_daily, run_cold_start_lane,
        )

        # ★리뷰 P3-13: today를 now(ss_now)에서 파생시킨다. 종전엔 now=ss_now(과거 시각)와
        #   today=kst_today()(호출 시점)를 섞어 자정 경계에서 어긋났다 — 바로 위 693줄이 정확히
        #   이 문제(C6) 때문에 ss_now를 도입했는데 CS가 그 계약을 깼다.
        cs_today = ss_now.date()
        collected = collect_market_bids_daily(db4, today=cs_today)
        log.info(
            "[스케줄러] naver CS 시장가 수집: 소재=%s 행=%s floor=%s",
            collected.get("ads"), collected.get("rows"), collected.get("floor_ads"),
        )
        cs_dry_run = os.getenv("NAVER_CS_DRY_RUN", "1") != "0"
        cs = run_cold_start_lane(db4, dry_run=cs_dry_run, now=ss_now, today=cs_today)
        log.info(
            "[스케줄러] naver CS 콜드 첫 입찰(dry_run=%s): 후보=%s 제안=%s 집행=%s 실패=%s "
            "경제성없음=%s 보류=%s",
            cs_dry_run, cs["candidates"], cs["proposed"], cs["executed"], cs["failed"],
            cs["not_viable"], cs["held"],
        )
        # 상세는 제안/경보 건만 남긴다(리뷰 P3-15: 소재 수백 개면 전건 로깅은 폭주).
        # 단순 보류(시세 없음·상한 없음·상향 아님)는 위 집계 카운터로 충분하다.
        _CS_LOUD = {"propose", "not_viable"}
        for r in (x for x in cs["rows"] if x["decision"] in _CS_LOUD):
            log.info(
                "[스케줄러] naver CS 상세: ad=%s grp=%s 현재=%s 판정=%s 목표=%s "
                "상한=%s 시장가=%s 사다리최저=%s RPC출처=%s(clk=%s) — %s",
                r["ad_id"], r["adgroup_id"], r["current_bid"], r["decision"], r["target_bid"],
                r["ceiling_cpc"], r["market_bid"], r["ladder_min"], r["rpc_source"],
                r["sample_clk"], r["reason"],
            )
    except Exception as e:  # noqa: BLE001 — CS 실패는 다른 레인과 분리(fail-open)
        log.exception("[스케줄러] naver CS 레인 에러(fail-open): %s", e)
    finally:
        db4.close()


def run_naver_auto_operator_hourly_job():
    """auto_operator 시간당 레인 — 핫셋 intraday 밴드 관제 실입찰 (매시 :20 KST, D-NAO-49).

    catch-up 제외(시간성 소멸 — 다음 정시가 곧 재기회, PLAN §5). auto_operate=True 캠페인의
    핫셋(클릭≥10 그룹)만, 순위·CPC·페이싱 기반 스텝 제안을 생성 즉시 심사·집행한다."""
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.auto_operator import run_hourly_lane

        result = run_hourly_lane(db)
        # D-NAO-85 관측 갭①: 탐색/탐침 카운터도 함께 출력 — 레벨 소실(main.py에서 해소)에 더해,
        # explored_ghost_hold 등 신규 카운터는 애초에 이 로그 라인에 없어 레벨만 고쳐선 안 보였다.
        log.info(
            "[스케줄러] naver auto_operator hourly: reviewed=%s approved=%s executed=%s "
            "held=%s skipped=%s failed=%s probed=%s | explored=%s explored_held=%s "
            "explored_capped=%s explored_not_rank=%s ghost_hold=%s(groups=%s)",
            result["reviewed"], result["approved"], result["executed"],
            len(result["held"]), result["skipped"], result["failed"], result["probed"],
            result["explored"], result["explored_held"], result["explored_capped"],
            result["explored_not_rank"], result["explored_ghost_hold"],
            len(result["ghost_hold_groups"]),
        )
        # ★D-NAO-130 관측 구멍 수정(2026-07-29 실측): 소재 자동 실행 카운터 4종이 어느 로그
        # 라인에도 없어서, 레인 캡이 걸렸는지·중복으로 skip됐는지를 **로그로 알 수 없었다**
        # (approved=5가 캡 도달인지 우연인지 구분 불가 → DB를 봐야만 했다). 별도 라인으로 낸다.
        log.info(
            "[스케줄러] naver 소재(ad) 자동실행: reserved=%s capped=%s confirm_pending=%s "
            "dup_skipped=%s inflight_skipped=%s",
            result["ad_auto_exec_reserved"], result["ad_auto_exec_capped"],
            result["ad_confirm_pending"], result["ad_confirm_pending_dup_skipped"],
            result["ad_auto_exec_inflight_skipped"],
        )
        # BP(D-NAO-102) 예산 페이싱 카운터 — 별도 라인(위 라인이 이미 길어 소실 위험).
        log.info(
            "[스케줄러] naver BP 예산페이싱: reviewed=%s raised=%s failed=%s dry_run=%s "
            "held=%s | restore reviewed=%s restored=%s failed=%s",
            result["budget_pacing_reviewed"], result["budget_pacing_raised"],
            result["budget_pacing_failed"], result["budget_pacing_dry_run"],
            len(result["budget_pacing_held"]), result["budget_pacing_restore_reviewed"],
            result["budget_pacing_restored"], result["budget_pacing_restore_failed"],
        )
    except Exception as e:
        log.exception("[스케줄러] run_naver_auto_operator_hourly_job 에러: %s", e)
        raise
    finally:
        db.close()


def run_naver_budget_pacing_reset_job():
    """BP 익일 예산 원복 — auto_operator._run_budget_pacing_restore (매일 00:05 KST, D-NAO-102 ⑤).

    전날 장중 페이싱 증액분을 naver_campaign_settings.base_daily_budget으로 되돌린다.
    ★멱등·자가치유: 판정이 change_log 이력 기반이라 이 잡이 죽어도 시간당 레인(:20)이 같은
    함수를 호출해 따라잡는다 — catch-up 목록(아침배치 전용)에 넣지 않는 이유."""
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.auto_operator import run_budget_pacing_reset_lane

        result = run_budget_pacing_reset_lane(db)
        log.info(
            "[스케줄러] naver BP 예산 원복: reviewed=%s restored=%s failed=%s dry_run=%s held=%s",
            result["budget_pacing_restore_reviewed"], result["budget_pacing_restored"],
            result["budget_pacing_restore_failed"], result["budget_pacing_dry_run"],
            len(result["budget_pacing_held"]),
        )
    except Exception as e:
        log.exception("[스케줄러] run_naver_budget_pacing_reset_job 에러: %s", e)
        raise
    finally:
        db.close()


def run_naver_probe_settlement_job():
    """탐침 성과 정산 판정 — probe_revert.run_settlement (08:55 KST, D-NAO-58 CD3 Stage 2).

    D+1 정산 완료 데이터로 standing probe 유지(kept)/되돌림(reverted)/보류(deferred) 판정.
    fail-open(관찰·집행 혼합이나 되돌림은 안전방향 — 실패가 catch-up 하류를 막지 않게 예외를
    다시 던지지 않는다. 일 레인(08:50)·retro(08:30) 이후라 정산 데이터·성적표가 최신)."""
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.probe_revert import run_settlement

        result = run_settlement(db)
        log.info(
            "[스케줄러] naver probe settlement: checked=%s kept=%s reverted=%s deferred=%s errors=%s",
            result["checked"], result["kept"], result["reverted"],
            result["deferred"], result["errors"],
        )
    except Exception as e:  # noqa: BLE001 — fail-open(re-raise 안 함 — catch-up 하류 비블록)
        log.exception("[스케줄러] run_naver_probe_settlement_job 에러(무시): %s", e)
    finally:
        db.close()


def run_naver_probe_learning_job():
    """환경별 학습·세분화층 — probe_learning_loop.run_probe_learning (09:03 KST, D-NAO-58 CD4).

    CD4 환경 셀×순위 밴드 클릭곡선 집계→세분화 판정→승격→observe 일기 요약(정산 08:55·
    keyword_hourly D-1 스윕(전날 09:10치가 최신)이 끝난 뒤 재계산). fail-open(관찰 전용 —
    실패가 catch-up 하류를 막지 않게 예외를 다시 던지지 않는다)."""
    db = _get_own_db_session()
    try:
        from app.services.naver_ad.probe_learning_loop import run_probe_learning

        result = run_probe_learning(db)
        log.info(
            "[스케줄러] naver probe learning: cells=%s promoted=%s stage_status=%s",
            result.get("cells"), len(result.get("promoted") or []), result.get("stage_status"),
        )
    except Exception as e:  # noqa: BLE001 — fail-open(re-raise 안 함 — catch-up 하류 비블록)
        log.exception("[스케줄러] run_naver_probe_learning_job 에러(무시): %s", e)
    finally:
        db.close()


def sync_naver_adgroup_targets_job():
    """광고그룹 타겟팅 설정 전수 스윕 — 매체 블랙리스트(A5)·PC/모바일(A6) 적재 (09:30 KST, D-NAO-201).

    `/ncc/targets`를 non-deleted 광고그룹(약 1,013개) 전건에 1회씩 GET한다. 네이버에 쓰지 않는다.

    ★왜 전수 스윕인가(「추가 API 콜 0」을 포기한 이유): 같은 endpoint를 쇼핑 제외 관리가 이미
    부르지만, 그 편승 지점(08:25 생존감시)은 **제외 원장에 행이 있는 131그룹만** 돈다 —
    성과축 307그룹 중 116(37.8%)·계정 전체 대비 24.6%다(2026-08-19 실측). 편승만 하면
    「적재했다」가 4분의 1을 뜻하게 된다. Jino 결정 = 커버리지를 산다.

    ★09:35인 이유: 09:10 keyword hh24 스윕(~09:22) · 09:20 시간별 관제·쿠팡 watchdog ·
    **09:30 `cafe24_token_refresh`(*/30)** · 09:50 keyword_baseline 을 전부 피하는 자리다.
    초판은 09:30을 「빈 슬롯」이라 적었는데 */30 잡을 안 본 것이었다(적대 리뷰 P2-1).
    스윕은 약 4~5분(1,013콜)이고 **데드라인 12분**이라 09:50을 침범하지 않는다.
    상류 의존은 `naver_entity`(07:35 동기화) 하나뿐이라 오전 어느 시각이든 신선하다.

    ★_CATCHUP_ORDER 제외 — 관측 전용이고 매일 전량을 다시 읽어 현재상태를 통째로 갱신하므로
    하루 유실이 현재상태에 구멍을 남기지 않는다. (다만 그날 일어났다가 되돌아간 변경은
    변경 원장에서 영구히 안 보인다 — 소급이 원리적으로 불가능한 자료의 대가다.)

    fail-open(관찰 전용 — 실패가 catch-up 하류를 막지 않게 예외를 다시 던지지 않는다)."""
    db = _get_own_db_session()
    try:
        from app.services.naver_ad import adgroup_target_ingest

        result = adgroup_target_ingest.sync_adgroup_targets(db)
        log.info(
            "[스케줄러] 타겟팅 스윕: swept=%s ok=%s failed=%s(db=%s) new=%s changed=%s "
            "black_rows=%s aborted=%s",
            result["swept"], result["ok"], result["failed"], result["db_failed"],
            result["new"], result["changed"], result["black_rows"], result["aborted"],
        )
    except Exception as e:  # noqa: BLE001 — fail-open(re-raise 안 함 — catch-up 하류 비블록)
        log.exception("[스케줄러] sync_naver_adgroup_targets_job 에러(무시): %s", e)
    finally:
        db.close()


def write_naver_pooled_estimates_job():
    """[9] 계층 EB 풀링 산출 기록 — 키워드 grain CTR/CVR/RPC (09:30 KST, D-NAO-214 · ref 65 S1-ⓑ).

    `hierarchical_pooling.pool_all`을 창 30일 키워드 전수에 돌려 `naver_pooled_estimate_daily`에
    남긴다. **판정하지 않고 추정치만 남긴다** — 자동 쓰기 경로에 연결되지 않는다(계약 §3
    「신규 자동 쓰기 0건」). 소비는 M2-d(성적표 축)부터다.

    ★09:30인 이유(prod 실측 2026-08-21로 골랐다 — 교훈 #326: 슬롯은 배포 «전»에만 무료):
    분 30의 daily 잡은 05:30(3건)·08:30(retro)뿐이고 9시대엔 없다. 매시 잡 :05·:07·:20·:45·:57도
    :30에 안 닿는다. 진짜 위험은 길이가 긴 이웃인데 — `sweep_naver_keyword_hourly`(09:10 발화)가
    **11분**을 써 09:21:03에 끝난다. 09:30은 그로부터 9분 뒤다. 다음 잡 `sync_naver_adgroup_targets`
    (09:35, 실측 3.4분)와는 5분 간격이지만 이 잡 자체가 초 단위라(창 30일 키워드 6,343개·45,616행,
    2026-08-21 실측) 겹칠 여지가 없다.

    ★원료는 `naver_ad_daily`이고 그 수집은 07:30(실측 14초)에 끝난다 — 창이 «어제까지»라
    당일 수집 진행 여부와 무관하다.

    ★_CATCHUP_ORDER 제외 — 매일 창 전체를 다시 계산해 그날 행을 통째로 upsert하므로 하루 유실이
    «현재»에 구멍을 남기지 않는다. 다만 그날치 target_date 행은 영영 안 생긴다(추정치의 일별
    스냅샷이 하루 빈다) — 소급 재계산은 원장이 남아 있어 언제든 가능하다.

    ★**예외를 삼키지 않는다.** `complete=False`면 raise 해서 `last_status='error'`가 남게 한다 —
    부분 산출을 success로 굳히는 것이 교훈 #319(8/18 절단: sync_log 전부 success)·#321(D-NAO-204:
    판정이 HTTP 경계를 못 넘음)·D-NAO-212 1R P1의 **네 번째** 같은 모양이다. 결과 dict를 반환하는
    이유도 같다 — 수동 트리거 라우터가 그 dict를 응답에 실어야 누른 사람이 완주 여부를 안다."""
    db = _get_own_db_session()
    try:
        from app.services.naver_ad import pooled_estimate_writer

        result = pooled_estimate_writer.write_pooled_estimates(db)
        line = ("[스케줄러] 계층 풀링 산출: window=%s~%s candidates=%s written=%s updated=%s "
                "skipped_no_signal=%s complete=%s")
        args = (result["window_from"], result["window_to"], result["candidates"],
                result["written"], result["updated"], result["skipped_no_signal"],
                result["complete"])
        if not result["complete"]:
            log.error(line + " reason=%s", *args, result["incomplete_reason"])
            raise RuntimeError(f"계층 풀링 산출 미완주: {result['incomplete_reason']}")
        log.info(line, *args)
        return result
    finally:
        db.close()


def sync_naver_product_meta_job():
    """네이버 커머스 상품 메타 전건 폴링 — C10 적재 (09:55 KST, D-NAO-212 · 북극성 M1 ④).

    `POST /v1/products/search`를 **필터 없이** 전건 순회해 채널상품 현재 단면을 upsert하고
    변경분만 원장에 append한다. 상품 도메인 **쓰기 endpoint 21종은 건드리지 않는다**(실판매 카탈로그).

    ★왜 지금 여는가: 상품 도메인 전체에 변경-피드가 없어(75건 전건 개봉 실측 2026-08-19)
    **소급이 원리적으로 불가능**하다 — 폴링 개통일이 곧 관측 창의 시작일이고, 하루 미룰 때마다
    창이 하루 짧아진다.

    ★09:55인 이유(전례가 만든 검사를 그대로 밟았다 — 교훈 #326: 크론 슬롯은 배포 «전»에만
    무료로 고칠 수 있다): 분 55를 쓰는 잡은 `run_naver_probe_settlement`(08:55) 하나뿐이고
    9시대엔 없다. 매시 발화 잡(:05 `snapshot_naver_ad_hourly` · :07 `trigger_watch` ·
    :45 `sync_naver_orders_hourly` · */30 `cafe24_token_refresh`)도 :55에 안 닿는다.
    직전 09:50 `sync_naver_keyword_baseline`은 prod 실측 09:51:21 완료(≈1.3분)라 여유 4분.
    이 폴링 자체는 7페이지(~1,213 원상품)라 1분 안에 끝난다.

    ★_CATCHUP_ORDER 제외 — 관측 전용이고 매일 전량을 다시 읽어 현재 단면을 통째로 갱신하므로
    하루 유실이 «현재»에 구멍을 남기지 않는다. (다만 그날 일어났다가 되돌아간 변경은 변경
    원장에서 영구히 안 보인다 — 소급 불가 자료의 대가다. 타겟팅 스윕과 같은 성질.)

    ★**fail-open이 아니다 — 예외를 삼키지 않는다**(적대 리뷰 1R P1-1). 초판은 이 파일의 다른
    관측 잡들을 따라 `except: log.exception` 으로 덮었는데, 그러면 잡이 예외 없이 `None`을
    돌려주고 `_apply_job_event`가 `last_status='ok'`를 쓴다 — 수집기가 애써 만든 «미완주» 판정이
    **어떤 지속 표면에도 못 닿고** 로그 한 줄에서 끝난다. 그건 계약 §2-3·§4-3이 금지한 바로
    그것(「부분 적재를 success로 기록」)이고, 8/18 절단 사고(교훈 #319 — 그날 sync_log는 전부
    `success`였다)와 D-NAO-204(교훈 #321 — 판정을 만들었는데 HTTP 경계를 못 넘었다)의 **세 번째
    재현**이다. fail-open의 명분(«catch-up 하류 비블록»)도 이 잡엔 성립하지 않는다 —
    `_CATCHUP_ORDER` **밖**이라 막을 하류가 애초에 없다. 파는 것은 실패 신호 전부인데 사는 것이 0이다.
    ⇒ 이 파일의 원 관례를 따른다: *"잡 자체의 raise는 유지한다 — 정상 크론에서는 실패가
    last_status로 드러나야 한다 … 성공으로 위장 금지"*(scheduler_service.py `_CATCHUP_NON_BLOCKING` 주석).

    ★**결과 dict를 반환한다** — 수동 트리거 라우터가 반환 dict를 응답에 싣는다(`routers/scheduler.py`:
    *"고정 문구만 돌려주면 누른 사람은 가드가 걸렸는지 알 수 없고, 그건 가드가 없는 것과 화면상 같다"*).
    계약 §5 ⓑ의 라이브 증거가 그 응답에서 바로 나온다."""
    db = _get_own_db_session()
    try:
        from app.services import naver_product_meta_ingest

        result = naver_product_meta_ingest.sync_product_meta(db)
        line = ("[스케줄러] 상품 메타 폴링: pages=%s/%s origins=%s/%s channel_rows=%s "
                "new=%s changed=%s unchanged=%s dup_in_run=%s complete=%s")
        args = (result["pages"], result["total_pages"], result["origins"],
                result["total_elements"], result["channel_rows"], result["new"],
                result["changed"], result["unchanged"], result["dup_in_run"],
                result["complete"])
        if not result["complete"]:
            log.error(line + " reason=%s", *args, result["incomplete_reason"])
            # ★여기서 던져야 `last_status='error'`가 남는다. 삼키면 미완주가 «성공»으로 굳는다.
            raise RuntimeError(
                f"상품 메타 폴링 미완주: {result['incomplete_reason']} "
                f"(pages={result['pages']}/{result['total_pages']} "
                f"origins={result['origins']}/{result['total_elements']})"
            )
        log.info(line, *args)
        return result
    finally:
        db.close()


def verify_search_term_exclusions_job():
    """조치 생존 감시 — 우리가 건 검색어 제외가 라이브에 아직 걸려 있나 (08:25 KST, D-NAO-173 P1-①).

    광고그룹당 GET 1회(제외키워드 재조회)로 대조하고 결과를 naver_search_term_exclusion 행에
    적는다. 네이버에 쓰지 않는다(읽기 전용) — 어긋남을 발견해도 자동 복구는 하지 않고 헬스
    배너로 사람에게 올린다(이번 스프린트는 실행 주체가 사람이다, PLAN §3).

    fail-open(관찰 전용 — 실패가 catch-up 하류를 막지 않게 예외를 다시 던지지 않는다). 대조가
    멈춘 사실 자체는 요약의 `stale`이 표면화하므로 실패가 조용해지지 않는다."""
    db = _get_own_db_session()
    try:
        from app.services.naver_ad import exclusion_survival

        result = exclusion_survival.check_survival(db)
        log.info(
            "[스케줄러] 제외 생존 대조: checked=%s groups=%s alive=%s missing=%s deleted=%s "
            "unknown=%s errors=%s",
            result["checked"], result["groups"], result["alive"], result["missing"],
            result["deleted"], result["unknown"], len(result["errors"]),
        )
    except Exception as e:  # noqa: BLE001 — fail-open(re-raise 안 함 — catch-up 하류 비블록)
        log.exception("[스케줄러] verify_search_term_exclusions_job 에러(무시): %s", e)
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


def request_wing_vendor_summary_daily_job():
    """쿠팡 Wing 판매분석 갱신을 **매일 자동으로 요청**한다 (D-CPP-36, 05:20 KST).

    ★왜 이 잡이 필요한가 (2026-08-10 발견): 판매분석 페처는 2026-07-27부터 «순수 버튼-only»다.
      그런데 **WING2(오하이테크)는 UI가 요청을 만들지 않는다** — 즉 사람이 누를 버튼조차 없었다.
      그래서 판매분석 요약축이 07-26 이후 **13일간 멈췄고**, 그 사이 헬스는 `healthy: true`,
      `refresh-status`는 `green`이었다. 「버튼-only」는 «사람이 매일 누른다»를 전제하는데
      그 전제가 애초에 성립하지 않는 계정이 있었다.
      → 요청을 만드는 주체를 사람에서 크론으로 옮긴다. 페처·큐·lease는 그대로 쓴다
        (새 경로를 만들지 않는다 — 검증된 경로에 트리거만 붙이는 게 실패 표면이 가장 작다).

    ★창(window)을 여기서 정하지 않는다: 요청은 «갱신해라» 신호일 뿐이고 어느 날짜를 받을지는
      페처 config(vs_days / vi_days)가 정한다. 서버가 창까지 지시하면 두 곳이 진실을 다툰다.

    ★05:20 KST인 이유: 쿠팡 수집 크론대(05:30~06:05)보다 **앞**에 요청을 걸어 두면, Mac이
      깨어 있는 시간대에 페처가 집어 그날 손익 계산 전에 정본이 들어온다. Mac이 자고 있어도
      lease 계약이 요청을 살려 두므로(성공 또는 3회 실패까지) 깨어난 뒤에 집는다 — 그래서
      이 잡은 «성공»을 기다리지 않는다. 요청 set 자체가 이 잡의 산출물이다.
    """
    db = _get_own_db_session()
    try:
        from app.services.coupang import vendor_summary_sync

        results = []
        # ★WING1 편입 (D-CPP-40, 2026-08-12). 종전 제외 근거는 «오픽스 3P는 RG로 이관돼 판매분석
        #   3P가 사실상 비어 있다(90일 3P 옵션 3개)»였다. 그 관찰 자체는 참이지만(WING1 NORMAL
        #   90일 5,139,710원), **거기서 계정 전체를 빼는 결론까지는 근거가 없었다** — 같은 판매분석이
        #   싣는 RFM(RG)축이 63일 39,312,430원으로 이 계정의 지배 축이고, 오픽스 매출의 98.9%가
        #   RG다. 3P만 보고 계정을 판정한 것이 누락의 원인이다(ref 55 §3).
        # ★"수집 불가"가 아니었다: 수집 경로는 계정 무관 범용이고(데몬·ingest·UI 버튼 `ofix_sales`),
        #   적재도 126행 06-07~08-08 **연속**이었다. 사람이 버튼을 눌러야만 갱신됐고 마지막
        #   클릭이 08-09였을 뿐이다. 창을 적재 범위 안으로 잡으면 `revenue_canonical.wing_used=true`,
        #   `factor_rg=0.925`로 안분이 실제로 걸린다(즉 대조 상대는 이미 있었다).
        # ★단 «세션이 green이다»를 근거로 쓰지 말 것(적대 리뷰 P2-3): `coupang_wing_cookie`의
        #   `status`는 green인 채로 `last_error`에 「로그인 필요」가 쌓인다 — 이 리포가 이미
        #   두 번 겪은 green-while-dead다. 이 잡을 켠 직후 실측(2026-08-12 01:20 KST)이 그랬다:
        #   요청은 데몬이 **즉시 집었고**(로그 「갱신 요청 감지 — fetch 시작」) 3분 뒤
        #   「자동 회복 실패 — 창에서 로그인하세요」로 죽었다. 즉 이 잡의 성패는 쿠팡 Wing
        #   **로그인 수명**에 달려 있고, 그건 사람이 창에서 푸는 것이다(180초 대기).
        for account_key in ("COUPANG_WING1", "COUPANG_WING2"):
            results.append(vendor_summary_sync.request_refresh(db, account_key))
        log.info("[스케줄러] Wing 판매분석 갱신 요청: %s", results)
    except Exception as e:
        log.exception("[스케줄러] request_wing_vendor_summary_daily_job 에러: %s", e)
        raise
    finally:
        db.close()


def request_coupang_ad_cost_daily_job():
    """쿠팡 오픽스 광고비(COUPANG_ADS1) 갱신을 **매일 자동으로 요청**한다 (D-CPP-45, 05:25 KST).

    ★왜 이 잡이 필요한가: 유일한 트리거가 UI 「광고비 갱신」 버튼이었다. 서버 크론
      `sync_coupang_ad_cost`(00:10)는 `is_enabled=0`이고, Akamai가 prod IP를 막아 직접 fetch가
      원리적으로 불가하다(Mac 페처를 거쳐야만 한다). 워치독(09:20)의 `RESCUE_STREAMS`은
      `frozenset({"supplier_hub"})`뿐이라 광고비는 **알림만** 가고 자동 복구는 안 된다.
      광고비를 자동 구조에서 뺀 근거는 「소급 창 30일이라 자가 복구된다」였고 그건 지금도
      참이다 — 그래서 이 잡이 막는 건 «영구 소실»이 아니라 **«며칠 동안 손익 화면이 낡은
      광고비로 구르는 것»**이다.

    ★실측(2026-08-12): push 기준 공백이 06-24→07-04 **10일**, 08-10→08-12에 **29일치가
      한꺼번에** 적재됐다. cost_date 결측은 0건 — 늦게 눌러도 30일 창이 메웠다. 즉 「사람이
      배너를 보고 누른다」가 반복적으로 안 지켜졌다는 뜻이다.

    ★신선도 배너(`coupang_ad_cost_sales`, max_age_days=3.0)는 **그대로 둔다** — 자동 트리거가
      감시를 대체하지 않는다. 로그인/Akamai로 실패하면 배너가 여전히 유일한 실토다.

    ★창(어느 날짜를 받을지)은 여기서 정하지 않는다 — 페처가 정한다. 이 잡의 산출물은
      «요청 set» 자체다(request_wing_vendor_summary_daily_job과 동일한 계약).
    """
    db = _get_own_db_session()
    try:
        from app.services.coupang import ad_cost_sync

        result = ad_cost_sync.request_refresh(db)
        log.info("[스케줄러] 쿠팡 광고비(오픽스) 갱신 요청: %s", result)
    except Exception as e:
        log.exception("[스케줄러] request_coupang_ad_cost_daily_job 에러: %s", e)
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
    """로켓그로스 주문 자동 동기화 (2시간 주기 `0 */2 * * *`) — RG 매출(P3/D-14). 트랙 D-8: 서버 IP에서만.

    ★시각 표기 주의(2026-08-17): 이 docstring은 오래 「05:55 KST」였다. 시드는 `892405c3`
    (2026-06-05)에 2시간 주기로 바뀌었는데 prod는 기존 행이라 안 받아 **73일째 05:55에**
    돌았고, docstring만 옛 시각에 멈춰 있어 코드·DB·문서 셋이 전부 달랐다. Jino가 2시간으로
    확정해 `_CRON_OWNED_BY_CODE`로 조정한다(교훈 #297과 같은 병의 두 번째 사례).
    """
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

    ★P5(2026-07-27, green-while-dead 수리): SA는 쿠키 만료를 fail-soft로
    {"status": "auth_error"|"read_error"|"parse_error", ...}로 돌려준다(raise 안 함) — 이걸
    log.info로만 찍고 삼키면 잡 자체는 예외가 없어 EVENT_JOB_EXECUTED로 기록되고 스케줄러 상
    'ok'가 된다(실사고: 죽은 서버측 쿠키 경로가 50일간 green으로 보임). 다른 쿠팡 RG 잡들의
    `_coupang_failed` 관례와 동일하게, status!=ok인 계정이 하나라도 있으면 잡을 raise로
    실패 표면화한다(EVENT_JOB_ERROR)."""
    db = _get_own_db_session()
    try:
        from app.services.coupang.rg_settlement_sync import sync_rg_settlement, RG_ACCOUNTS

        results = []
        for account_key in RG_ACCOUNTS:
            result = sync_rg_settlement(db, account_key)
            log.info("[스케줄러] RG 정산 sync (%s): %s", account_key, result)
            results.append(result)

        failed = [r for r in results if r.get("status") != "ok"]
        if failed:
            raise RuntimeError(f"쿠팡 RG 정산 sync 실패 계정: {failed}")

    except Exception as e:
        log.exception("[스케줄러] sync_coupang_rg_settlement_job 에러: %s", e)
        raise
    finally:
        db.close()


def auto_download_rg_settlement_job():
    """RG 정산 엑셀 자동 다운로드·적재 (06:15 KST) — Wing 3단계(request→poll→S3 GET).

    WAREHOUSING_SHIPPING(입출고·배송비) 옵션 단위 적재. 이미 적재된 기간은 idempotent.
    쿠키 만료(WingAuthError)는 fail-soft(per-account error 반환). 예상치 못한 예외만 raise.

    ★P5(2026-07-27, green-while-dead 수리 — sync_coupang_rg_settlement_job과 같은 패턴): SA
    결과 dict의 status를 로그만 찍고 삼키면 잡이 실제로는 죽은 상태에서도 EVENT_JOB_EXECUTED로
    green 기록된다. 단 이 SA는 "no_periods"(status/api가 아직 정산 기간을 못 채운 정상 상태 —
    실패 아님)를 legitimate 결과로 돌려주므로, ok와 함께 '실패 아님'으로 취급한다. 그 외
    (auth_error/all_failed/partial/failed 등)는 실패로 raise한다."""
    db = _get_own_db_session()
    try:
        from app.services.coupang.rg_settlement_sync import auto_download_all

        vendor_id_map = {}
        import os
        vendor_id_map["COUPANG_WING1"] = os.environ.get("COUPANG_WING1_VENDOR_ID", "")
        vendor_id_map["COUPANG_WING2"] = os.environ.get("COUPANG_WING2_VENDOR_ID", "")

        results = auto_download_all(db, vendor_id_map)
        for r in results:
            log.info("[스케줄러] RG 엑셀 자동 다운로드 (%s): requested=%s completed=%s ingested=%s errors=%d",
                     r.get("account_key"), r.get("requested"), r.get("completed"),
                     r.get("ingested"), len(r.get("errors", [])))

        failed = [r for r in results if r.get("status") not in ("ok", "no_periods")]
        if failed:
            raise RuntimeError(f"RG 엑셀 자동 다운로드 실패 계정: {failed}")

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


def coupang_collection_watchdog_job():
    """쿠팡 브라우저 수집 신선도 워치독 (09:20 KST, 하루 1회) — 알림 + 위급 시 자동 갱신.

    07-27 재설계로 수집의 유일한 트리거가 사람의 클릭이 됐는데(순수 버튼-only), 그 클릭을
    유도하는 장치가 없어 2026-08-03에 6일 침묵·51일 결손 직전까지 갔다. 이 잡이 그 구멍이다.

    ★하루 1회인 것이 곧 알림 쿨다운이다 — 별도 상태 저장 없이 "같은 건으로 하루 여러 번"이
      구조적으로 불가능하다. 시간당으로 돌리면 알림이 배너처럼 상시화돼 다시 무시된다.
    ★여기서 하는 일은 prod DB에 요청 플래그를 세우는 것뿐이라, Mac이 자고 있어도 깨어난 뒤
      집어간다(Mac은 수시로 sleep한다 — 08-03 실측).
    ★fail-soft: Slack 미연결·발송 실패는 no-op으로 삼킨다(잡 자체는 성공). 자동 갱신 요청
      실패만 로그에 남기고 계속한다 — 알림이 죽었다고 구조까지 막으면 안 된다.
    """
    db = _get_own_db_session()
    try:
        from app.services.coupang import collection_watchdog

        result = collection_watchdog.run(db)
        log.info("[스케줄러] 쿠팡 수집 워치독 결과: %s", result)
    except Exception as e:
        log.exception("[스케줄러] coupang_collection_watchdog_job 에러: %s", e)
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
    """네이버 일별 정산 내역 자동 적재 (트랙 N1, 05:25 KST). 최근 31일.

    ★창이 31일인 이유: 네이버 daily API의 조회 구간 상한이 **32일**이다(라이브 실측
      2026-08-03, 공식 문서엔 미명시). 종전 `days=34`(35일 구간)는 **매 호출 400**을 받았고,
      클라이언트가 그걸 빈 결과로 삼켜 "0건 적재 완료"를 남겼다 — 스케줄러는 ok로 기록하고
      daily 테이블은 07-27에서 멈춰 있었다. 상한 32일에 여유 1일을 둬 31일 구간으로 고정한다.
      (클라이언트가 이제 구간 초과·요청 실패를 예외로 올리므로 다시 조용히 비지 않는다.)"""
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
        dfrom = dto - timedelta(days=30)   # 31일 구간 (API 상한 32일)
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


def run_naver_nbaesong_return_probe_job():
    """N배송 반품 회수비 프로브 (관측 전용, 06:02 KST).

    최근 44일 네이버 클레임(반품/교환) 주문을 주문번호로 1회씩 훑어(단건 조회는 그 주문의
    정산 행이 유형 구분 없이 전량 온다) 정산 구조를 적재하고, **처음 보는 조합**
    (배송방식 × 멤버십 × productOrderType × settleType)만 Slack으로 올린다.
    N배송 반품 표본이 라이브 468건 중 2건뿐이고 둘 다 지금은 NORMAL_SETTLE_BEFORE_CANCEL
    (정산 전 취소)이라, 정산 성숙(D+12)에서 상태가 옮겨가는 순간을 사람이 지키는 대신
    이 잡이 잡는다. 상세 배경은 services/naver_claim_settlement_probe.py 상단.
    """
    db = _get_own_db_session()
    try:
        from app.clients.naver import NaverClient
        from app.config import get_naver_config
        from app.services.naver_claim_settlement_probe import run_probe

        cfg = get_naver_config("NAVER")
        if not cfg:
            log.error("[스케줄러] 네이버 설정 없음 — 클레임 정산 프로브 건너뜀")
            return
        result = run_probe(db, NaverClient(cfg))
        log.info(
            "[스케줄러] 클레임 정산 프로브 완료 — 주문 %d건·관측 %d행·적재 %d행·신규조합 %d건",
            result["orders"], result["observations"], result["inserted"],
            len(result["new_combos"]),
        )
    except Exception as e:
        log.exception("[스케줄러] run_naver_nbaesong_return_probe_job 에러: %s", e)
        raise  # 삼킴 정렬(S5b): EVENT_JOB_ERROR/HTTP500로 실패 표면화
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════
# 크론 요일 규약 (교훈 #297, 2026-08-17)
# ══════════════════════════════════════════════════════════════════
# ★요일은 **이름으로만** 적는다(`sun`·`mon`…). 숫자를 쓰면 안 된다.
#   표준 crontab은 **0=일요일**인데 APScheduler의 `day_of_week`는 **0=월 … 6=일**이고,
#   `CronTrigger.from_crontab()`은 5번째 필드를 **규약 변환 없이 그대로** day_of_week에 넣는다.
#   그래서 `"20 9 * * 0"`은 일요일이 아니라 **월요일**에 발화한다(prod venv APScheduler 3.10.4
#   실측: '20 9 * * 0'→2026-08-03(월) / '20 9 * * 6'→2026-08-02(일) / '20 9 * * sun'→08-23(일)).
#
#   라이브 사고: bm_deep(`20 9 * * 0`)이 「일요일 09:20 레인」으로 문서화된 채 **3주 내내
#   월요일에** 돌았다(negative_kw_count 기입일 전수 07-27·08-03·08-10·08-17 전부 월요일,
#   일요일 0회). 놓친 실행은 0건 — 주 1회 리듬이 유지되고 잡은 매번 `ok`로 끝나므로
#   **로그·상태 어디에도 흔적이 없었다.** 깨진 것은 실행이 아니라 「언제 도는가」에 대한
#   믿음이고, 그 믿음은 주석·문서에만 산다. 그래서 관측이 아니라 **이름**으로 막는다.
_NUMERIC_DOW_HINT = (
    "요일은 이름으로 적으세요(sun/mon/tue/wed/thu/fri/sat). 숫자는 crontab(0=일)과 "
    "APScheduler(0=월)에서 뜻이 달라 하루 어긋납니다 — 교훈 #297."
)


def _assert_weekday_crons_are_named(defaults) -> None:
    """요일을 지정한 크론이 숫자를 쓰면 **기동을 거부**한다(교훈 #297의 집행 지점).

    `*`(매일)은 대상이 아니다 — 규약 차이가 드러나지 않기 때문이다. 숫자·범위·목록이
    5번째 필드에 오는 경우만 막는다. 주석이나 문서로는 세 번 다 못 막았으므로 여기서 막는다.
    """
    bad = []
    for name, cron in defaults:
        parts = str(cron).split()
        if len(parts) < 5:
            continue
        dow = parts[4]
        if dow == "*":
            continue
        if any(ch.isdigit() for ch in dow):
            bad.append(f"{name}={cron!r}")
    if bad:
        raise ValueError(f"숫자 요일 크론 금지: {', '.join(bad)} — {_NUMERIC_DOW_HINT}")


# 코드가 cron_expression의 정본인 잡 — 여기 든 이름만 기존 DB 행까지 조정한다.
#
# ★왜 전량이 아니라 명단인가: `_ensure_default_states`는 원래 «없는 행만 insert»라
#   시드를 고쳐도 prod의 기존 행은 옛 크론으로 계속 발화한다(retired 주석이 같은 함정을
#   이미 적어 뒀다). 그렇다고 전량을 시드로 덮으면 의도적으로 갈라 둔 행까지 되돌린다 —
#   그렇다고 전량을 시드로 덮으면 «갈라져 있다»는 사실을 확인도 안 하고 되돌린다 —
#   발산은 사고일 수도 의도일 수도 있는데 코드가 그 판단을 삼키면 안 된다.
#   → **명단 편입이 곧 «이 잡의 스케줄은 코드가 정한다»는 명시적 결정**이고, 그래서 편입은
#     리뷰에 걸린다(테스트가 명단 전체를 단언한다).
_CRON_OWNED_BY_CODE: frozenset[str] = frozenset({
    # 교훈 #297로 요일 표기를 숫자→이름으로 고친 두 잡. prod 기존 행이 `* * 0`(=월요일)이라
    # 코드만 고쳐서는 영영 반영되지 않으므로 여기 넣어 조정한다.
    "run_naver_bm_deep",
    "sync_naver_keyword_volume",
    # ★2026-08-17 Jino 결정 — **2시간 주기로 확정**.
    #   `892405c3`(2026-06-05)이 시드를 `55 5 * * *`→`0 */2 * * *`로 바꿨는데 prod는 기존
    #   행이라 안 받아 **73일째** 하루 1회로 돌고 있었다 — 교훈 #297과 같은 병의 두 번째
    #   사례다(의도는 코드에, 동작은 DB에, 조용히 갈라짐). 어느 쪽이 맞는지는 운영 판단이라
    #   물었고 «2시간»으로 확정됐다 → 명단에 넣어 prod 행을 조정한다.
    #   빈도 12배가 안전한 근거(편입 전 확인): ①`_upsert_order_item`+`pending` 멱등 upsert라
    #   재실행이 안전하다 ②APScheduler `max_instances` 기본 1이라 앞 실행이 안 끝났으면
    #   겹치지 않고 스킵된다(job_defaults에 오버라이드 없음) ③계정 격리가 이미 있어 한
    #   계정 실패가 다른 계정을 죽이지 않는다(2026-07-11~17 WING1 실사고 대응).
    "sync_coupang_rg_orders",
})


def _ensure_default_states(db):
    """기본 스케줄러 상태 DB 레코드 생성"""
    defaults = [
        ("auto_sync_orders", "0 6 * * *"),
        # 네이버 스마트스토어 시간별 주문 동기화(근시간 공백 메움, 2026-07-28 실사고 대응).
        # :45인 이유: 기존 시간별 잡이 :5(snapshot)·:7(trigger_watch)·:15(flight_loop 2h)·
        # :20(auto_operator_hourly)에 몰려 있어 SQLite 라이터 충돌을 피하려는 것.
        ("sync_naver_orders_hourly", "45 * * * *"),
        ("auto_profit_calc", "30 6 * * *"),
        ("cafe24_token_refresh", "*/30 * * * *"),
        ("sync_naver_sa_ad_costs", "0 7 * * *"),
        # ADVoost 쇼핑·GFA 광고비(비즈머니 실차감) — 검색광고 리포트가 못 덮는 축.
        # 07:30 BEP 산출보다 앞서야 그날 BEP가 온전한 광고비를 본다.
        ("sync_naver_display_ad_costs", "10 7 * * *"),
        ("sync_naver_ad_daily", "30 7 * * *"),      # 네이버 SA 일별 성과+BEP (트랙 P0)
        ("snapshot_naver_ad_hourly", "5 * * * *"),  # 네이버 SA 시간별 스냅샷 (빠른 루프)
        ("trigger_watch", "7 * * * *"),             # 조건발동 즉시알림(페이싱·CPC급등, 트랙 P4)
        ("sync_naver_entity", "35 7 * * *"),        # 엔티티 인벤토리 동기화 (트랙 P2-S1) — 완료 직후 BM 레이어 체이닝(구 07:37 크론 폐지)
        ("run_naver_bm_deep", "20 9 * * sun"),      # BM 벤치마크 레이어 주간 deep(제외키워드·소재수, D-NAO-78, 관찰 전용·catch-up 제외). ★요일은 이름으로 — 숫자 `0`은 APScheduler에서 월요일이라 3주간 하루 어긋나 있었다(교훈 #297)
        ("sync_naver_search_term", "40 7 * * *"),   # 검색어 단위 성과 수집 (트랙 P2-S1)
        ("sync_naver_keyword_volume", "0 9 * * sun"), # 저클릭 키워드 월검색량 (주1회, 일요일 — 이름 표기 이유는 교훈 #297)
        # ★머리 키워드 검색량 기준선 시계열 (매일 09:50, D-NAO-186 ①). 위 주1회 잡과 대상·저장
        #   모양·목적이 전부 다르다(저클릭/덮어쓰기 vs 돈이닿은/시계열) — 합치지 않는다.
        #   콜 예산: 대상 약 1,193개 ÷ 5개/콜 = 약 239콜/일(승인분 2,200콜 안).
        # ★광고그룹 타겟팅 전수 스윕(매체 블랙리스트 A5 · PC/모바일 A6, D-NAO-201).
        #   콜 예산: non-deleted 광고그룹 약 1,013개 × 1콜 = 약 1,013콜/일. 데드라인 12분.
        #   ★09:35인 이유(적대 리뷰 P2-1이 초판의 «09:30 빈 슬롯» 근거를 반박했다 — prod
        #     scheduler_state 실측에 `cafe24_token_refresh = */30`이 살아 있어 :00·:30마다
        #     발화한다. 그룹마다 commit하는 이 스윕이 SQLite 라이터를 4~5분 잡으므로 겹치면
        #     안 된다 — 이 저장소는 07:45·06:02·05:25를 같은 이유로 옮긴 전례가 있다):
        #     09:10 키워드 hh24 스윕(3,500콜 ~09:22) · 09:20 시간별 관제 + 쿠팡 수집 watchdog ·
        #     09:30 cafe24 토큰 · 09:50 검색량 기준선 — 이들을 다 피하는 자리가 :35다.
        ("sync_naver_adgroup_targets", "35 9 * * *"),
        ("sync_naver_keyword_baseline", "50 9 * * *"),
        # D-NAO-203: CRITERION 벌크 리포트 2종(연령·성별·관심사) 3일 창.
        # ★분 슬롯 = **37**. 매시 발화하는 잡이 쓰는 분은 0·5·7·20·30(*/30)·45·57이고
        #   10시대엔 15(run_naver_flight_loop, `15 */2`)·0(`0 */2`)도 온다. 분 37은
        #   이 파일의 어떤 크론 표현식에도 없다(전수 grep 확인) — 충돌 0.
        #   ⚠️초판은 10:05였고 적대 리뷰가 P1으로 잡았다: `snapshot_naver_ad_hourly`가
        #   `"5 * * * *"`로 **매시** :05에 발화한다(「10시대엔 비어 있다」가 반증됐다).
        # ★★이 값은 **배포 전에만 무료로 고칠 수 있다.** `_ensure_default_states`는 기존
        #   `scheduler_state` 행의 cron을 절대 조정하지 않고(예외는 `_CRON_OWNED_BY_CODE`
        #   명단뿐), 그 명단은 «이미 잘못 seed된 것을 고치는» 용도다. 한 번 seed되면
        #   **정본이 prod DB로 넘어간다** — 교훈 #297(의도는 코드에, 동작은 DB에)의 세 번째
        #   사례가 되지 않으려면 지금 맞아야 한다.
        # ★_CATCHUP_ORDER 제외 — 3일 창이 하루 유실을 스스로 메운다(D-1이 내일의 D-2가 된다).
        # D-NAO-212: C10 상품 메타 전건 폴링(커머스 POST /v1/products/search).
        # ★분 슬롯 = **55**. 분 55를 쓰는 잡은 `run_naver_probe_settlement`(08:55)뿐이고 9시대엔
        #   없다(전수 grep). 매시 발화 잡 :05·:07·:45·*/30 어느 것도 :55에 안 닿는다.
        #   직전 09:50 keyword_baseline은 prod 실측 09:51:21 완료(≈1.3분) → 여유 4분.
        # ★★한 번 seed되면 정본이 prod DB로 넘어간다(`_ensure_default_states`는 기존 행의
        #   cron을 안 고친다) — 배포 «전»에만 무료로 고칠 수 있다(교훈 #326).
        ("sync_naver_product_meta", "55 9 * * *"),
        # M2-a(D-NAO-214): [9] 계층 EB 풀링 산출 기록. 슬롯 근거는 잡 docstring 참조.
        ("write_naver_pooled_estimates", "30 9 * * *"),
        ("sync_naver_criterion", "37 10 * * *"),
        ("run_naver_forecast_engine", "50 7 * * *"),  # 캠페인 grain 예측엔진(게이트→모델→채점, F1)
        ("generate_naver_proposals", "0 8 * * *"),  # 네이버 SA 제안 자동생성(진단→시뮬→제안→Slack, 트랙 P2-S3)
        ("generate_expert_desk", "5 8 * * *"),  # 전문가(Ava) 검토 데스크(E1a, PLAN §8)
        ("run_naver_learning_loops", "10 8 * * *"),  # 학습루프 4종(성적표·예측편향·전환성숙·시간대분포, 트랙 P6)
        ("run_naver_retro_scoring", "30 8 * * *"),  # 상설 소급 채점(진단 보드 as-of 리플레이 + 페이싱 경보, D-NAO-45)
        # 조치 생존 감시(D-NAO-173 P1-①) — 우리가 건 제외가 아직 걸려 있나. 08:25인 이유:
        # 광고그룹당 GET 1회로 가볍고 상류 의존이 없으며, :20(auto_operator_hourly)·:30(retro)
        # 사이에서 비어 있는 유일한 분 슬롯이다. ★_CATCHUP_ORDER 제외 — 관측 전용이고 매일
        # 전량을 다시 대조하므로 하루 유실이 구멍을 남기지 않는다(요약의 stale이 그 사실을 띄운다).
        ("verify_search_term_exclusions", "25 8 * * *"),
        ("run_naver_diary_reflection", "35 8 * * *"),  # 운영 일기 해석층(결과 소급 기입+해석문, D-NAO-54 P2)
        ("run_naver_profit_scorecard", "40 8 * * *"),  # P7 일일 이익 스코어카드(목적함수 표면화, D-NAO-85/ref39 P7, 관찰 전용)
        ("run_naver_wisdom", "45 8 * * *"),  # 운영 일기 지혜 승격·망각층(후보→판사→지혜+보고→망각, D-NAO-54 P3)
        ("run_naver_vault_export", "5 9 * * *"),  # 운영 일기·지혜 Obsidian 볼트 export(열람층, D-NAO-54 P5)
        ("shopping_ad_product_sync", "45 7 * * *"),  # 쇼핑 그룹↔상품 매핑(상품 파생 target 소스, D-NAO-57 A — 07:30 BEP 뒤·08:00 제안 앞. 07:45인 이유: 07:40 검색어 잡과 SQLite 라이터 충돌 회피, 리뷰 3R P3)
        ("run_naver_auto_operator_daily", "50 8 * * *"),  # D-NAO-48 4조건 심사·집행 서버화(D-NAO-49)
        ("run_naver_probe_settlement", "55 8 * * *"),  # 탐침 성과 정산 판정(유지/되돌림/보류, D-NAO-58 CD3 Stage 2 — 일 레인 08:50·retro 08:30 뒤)
        ("run_naver_probe_learning", "3 9 * * *"),  # CD4 환경별 학습·세분화층(정산 08:55 뒤·vault 09:05 앞 재계산 → observe 요약이 당일 볼트에 포함, D-NAO-58 CD4)
        ("sweep_naver_keyword_hourly", "10 9 * * *"),  # 키워드/쇼핑그룹 시간별(hh24) 축적, D-1 스윕(D-NAO-46②)
        ("sweep_naver_today_hourly", "57 * * * *"),  # 당일 그룹 grain 시간별(hh24) 축적(D-NAO-122 — 매시 멱등 교체라 catch-up 제외). ★분 슬롯을 이렇게 고른 이유(codex 리뷰 2R): 매시 최대 800콜 + SQLite 쓰기라 ①09:10 D-1 스윕(3,500콜 ~12분 = 09:10~09:22, 지연 여유 포함) ②07:35 sync_naver_entity(계정 전체 인벤토리 동기화) 둘 다와 겹치면 안 된다. 등록된 분 슬롯은 3·5·7·10·15·20·25·30·35·40·45·50·55와 */30 — :57이 유일하게 비어 있고 두 창에서 가장 멀다. 당일 레인은 hh24를 직접 호출하므로 이 잡이 :20보다 뒤여도 무방하다
        ("run_naver_auto_operator_hourly", "20 * * * *"),  # 시간당 밴드 관제 실입찰(catch-up 제외, D-NAO-49)
        ("run_naver_budget_pacing_reset", "5 0 * * *"),  # BP 익일 예산 원복(D-NAO-102 ⑤ — 멱등, 시간당 레인이 자가치유하므로 catch-up 제외)
        ("run_naver_flight_loop", "15 */2 * * *"),  # 당일 플라이트 루프 2시간 주기(X2, dry_run=True)
        ("sync_naver_settlement", "25 5 * * *"),
        ("sync_naver_case_settlement", "30 5 * * *"),
        # N배송 반품 회수비 프로브(관측 전용). 06:02인 이유: 06:00엔 auto_sync_orders와
        # sync_coupang_coupons가 이미 있어 SQLite 라이터가 겹친다. :02는 이 시간대에서 비어
        # 있고, 이 잡은 orders를 읽기만 하므로 주문 동기화보다 몇 분 늦어도 무해하다
        # (판정 대상이 D+12 정산 성숙이라 분 단위 신선도가 의미 없다).
        # ★_CATCHUP_ORDER 제외 — 관측 전용이라 하루 늦어도 무해하고, 스캔 창 44일이
        #   성숙(D+12)보다 32일 넉넉해 하루 유실로 놓치는 표본이 없다.
        ("run_naver_nbaesong_return_probe", "2 6 * * *"),
        ("sync_meta_ad_costs", "0 7 * * *"),
        ("sync_coupang_rg_inbound", "20 5 * * *"),
        ("sync_coupang_rg_settlement", "30 5 * * *"),
        ("auto_download_rg_settlement", "15 6 * * *"),
        ("sync_coupang_products", "30 5 * * *"),
        ("sync_coupang_rg_sizes", "35 5 * * *"),
        ("sync_coupang_rg_inventory", "40 5 * * *"),
        ("sync_coupang_returns", "45 5 * * *"),
        ("sync_coupang_settlement", "50 5 * * *"),
        # 판매분석 갱신 요청(D-CPP-36) — 쿠팡 수집대(05:30~) 앞에 걸어 그날 손익 전에 정본이 들어오게.
        ("request_wing_vendor_summary_daily", "20 5 * * *"),
        # 오픽스 광고비 갱신 요청(D-CPP-45) — 05:25인 이유: 쿠팡 수집대(05:30~)보다 앞이면서
        # 판매분석 요청(05:20)과는 1분 이상 벌려 SQLite 라이터 경합을 피한다.
        ("request_coupang_ad_cost_daily", "25 5 * * *"),
        ("sync_coupang_rg_orders", "0 */2 * * *"),
        ("sync_coupang_coupons", "0 6 * * *"),
        ("sync_coupang_cs", "5 6 * * *"),
        ("sync_coupang_ad_cost", "10 0 * * *"),
        # 쿠팡 브라우저 수집 신선도 워치독 — 하루 1회(09:20). 알림 + 위급 시 자동 갱신.
        # 하루 1회인 것이 곧 알림 쿨다운이다(별도 상태 저장 없음).
        ("coupang_collection_watchdog", "20 9 * * *"),
        # ofix 광고비 «자동 fetch» 크론은 여전히 제거된 채다 — 이 문장의 그 부분은 아직 참이다.
        # 다만 D-CPP-45로 **«요청 트리거»만** 되살렸다(defaults 위쪽 request_coupang_ad_cost_daily
        # 참조). 옛 크론은 서버가 창을 스스로 띄워 fetch까지 했지만, 새 잡은 request_refresh로
        # «요청 set»만 만들 뿐 실제 수집(fetch)은 여전히 Mac 페처가 한다 — 사람이 버튼을
        # 누르는 대신 크론이 같은 요청을 만드는 것뿐, 창을 스스로 띄우던 옛 자동 fetch와는 다르다.
        # 낡음/실패는 GET /collection-status → 전역 신선도 배너로 가시화(잊어버림 방지).
    ]
    # 폐지된 잡(retired) — defaults에서 빼는 것만으로는 prod에서 안 죽는다: 스케줄링의 단일
    # 주도자가 SchedulerState 행이라(start_scheduler가 DB rows를 순회하며 add_job) 이미 만들어진
    # 행은 계속 옛 크론으로 발화한다. 그래서 행 자체를 지운다(같은 commit에 포함).
    #   run_naver_bm_layer: 07:37 독립 크론 폐지 → sync_naver_entity_job 완료 직후 체이닝으로 전환
    #   (구조적 경합으로 스냅샷이 항상 entity_sync D-1 값을 담던 문제, 2026-07-23~27 5/5일 실측).
    retired = ("run_naver_bm_layer",)

    _assert_weekday_crons_are_named(defaults)

    for name, cron in defaults:
        existing = db.query(SchedulerState).filter(
            SchedulerState.job_name == name
        ).first()
        if not existing:
            db.add(SchedulerState(job_name=name, cron_expression=cron, is_enabled=True))
        elif name in _CRON_OWNED_BY_CODE and existing.cron_expression != cron:
            # ★_CRON_OWNED_BY_CODE에 든 잡만 조정한다(그 상수 주석에 이유가 있다).
            #   is_enabled·last_run_at은 건드리지 않는다 — 켜고 끄는 것은 운영 판단이고,
            #   실행 이력을 지우면 워치독의 stale 판정 근거가 함께 사라진다.
            log.warning(
                "[스케줄러] cron 조정(코드 정본): %s %r → %r",
                name, existing.cron_expression, cron,
            )
            existing.cron_expression = cron
    for name in retired:
        db.query(SchedulerState).filter(SchedulerState.job_name == name).delete()
    db.commit()


# 네이버 아침배치 catch-up. misfire_grace_time은 프로세스 재시작을 못 잡는다(in-memory
# jobstore라 재시작 시 과거 발화 기록 소실 → misfire 미인식, codex 2026-07-13 [P1]).
# 그래서 SchedulerState.last_run_at(마지막 '성공' 시각)로 오늘 예정 발화를 놓쳤는지 명시적
# 판정해 따라잡는다. 범위는 네이버 아침배치(Jino 결정 2026-07-13) + **자가치유 불가 잡**
# (2026-08-03 확장, Jino 승인) — 쿠팡 등 blast radius는 여전히 제외.
#   ★확장 판단기준: '하루 놓치면 다음 발화가 스스로 메우는가'로 가른다. 쿠팡 수집 잡들은
#   최근 30~35일 창을 다시 긁으므로 다음 날 자동 복구된다 → 목록에 넣을 이유가 없다(제외 유지).
#   반면 광고비 3잡은 '어제 하루치만' 쓰므로 구멍이 영구화된다 → 넣는다. 즉 목록의 기준은
#   '중요도'가 아니라 **복구 가능성**이다 — 중요도로 고르면 목록이 무한히 자란다. ★순서 중요(codex [P1] R2): 이 잡들은 의존 스태거(forecast→proposals→expert
# →learning). expert_desk는 pending 제안 0이면 '성공 스킵'하므로 proposals보다 먼저 돌면
# 오늘 전문가검토가 영구 스킵된다. 따라서 동시 발화 금지 — cron 순서로 순차 실행하고 상류가
# 성공해야 하류를 잇는다. keyword_hourly sweep은 다른 잡에 의존하지 않지만(자체 완결) 09:10
# 표준 cron이라 같은 catch-up 목록에 포함(D-NAO-46②) — 순서상 맨 뒤(가장 늦은 cron).
_CATCHUP_ORDER: tuple[str, ...] = (
    # ★정산 2개가 맨 앞(2026-08-03 추가): cron이 05:25/05:30으로 이 목록에서 가장 이르고,
    #   BEP(07:30)·이익 회계가 정산 실측을 입력으로 쓰므로 하류보다 먼저 복구돼야 한다.
    #   라이브 사고: 08-03 03:45 KST 백엔드 재시작 → 05:25/05:30 발화 유실 → 두 잡이 이 목록에
    #   없어 다음날 05:25까지 영영 안 돌았다(SETTLED 129건 미수집). misfire_grace_time은
    #   프로세스 재시작을 못 잡는다(위 주석) — 목록 편입이 유일한 복구 경로다.
    #   둘 다 상류 의존이 없고 upsert라 재실행이 안전하며, 실패해도 하류 체인을 끊지 않는다.
    "sync_naver_settlement",       # 05:25
    "sync_naver_case_settlement",  # 05:30
    # ★광고비 3잡(2026-08-03 추가, 정산 바로 뒤·나머지 앞): 이 셋은 **'어제 하루치만' 적재**한다
    #   (다른 수집 잡들이 최근 30~35일 창을 다시 긁는 것과 근본적으로 다르다). 그래서 하루를
    #   놓치면 다음 정상 발화가 그 구멍을 **영영 메우지 않는다** — 그날 광고비가 손익에서
    #   영구 누락되고, 아무 에러도 남지 않는다.
    #   라이브 사고(08-03 03:32 ENOSPC → 서버 3시간 40분 마비): 07:00 두 잡이 유실됐고 catch-up
    #   목록 밖이라 자동 복구가 없었다. 사람이 그날 밤 눈치채고 손으로 되살려 겨우 막았다 —
    #   같은 세션에서 발견한 ADVoost·GFA 488만원 누락(59일 침묵)과 **소멸 방식이 정확히 같다**.
    #   순서: 정산 뒤(회계 입력 우선), 하류 관찰·집행 잡 앞. 셋 다 upsert라 재실행이 안전하다.
    "sync_naver_sa_ad_costs",      # 07:00 (검색광고 NCC)
    "sync_meta_ad_costs",          # 07:00 (Meta)
    "sync_naver_display_ad_costs", # 07:10 (ADVoost 쇼핑·GFA — 비즈머니 실차감)
    "shopping_ad_product_sync",    # 07:45 (D-NAO-57 A, 리뷰 P2-2 — BEP(07:30, catch-up 밖) 뒤·proposals 앞: 그날 제안이 최신 매핑 target을 쓰게. fail-open이라 실패해도 체인 안 끊김)
    "run_naver_forecast_engine",   # 07:50
    "generate_naver_proposals",    # 08:00
    "generate_expert_desk",        # 08:05 (proposals 성공 후라야 pending>0 → 의미 있음)
    "run_naver_learning_loops",    # 08:10
    "run_naver_retro_scoring",     # 08:30 (D-NAO-45, 비정형 아닌 표준 cron이라 catch-up 포함)
    "run_naver_auto_operator_daily",  # 08:50 (D-NAO-49, 조건④ bleeding 판정이 retro_scoring 결과를 쓴다 — 그 뒤)
    "run_naver_probe_settlement",  # 08:55 (D-NAO-58 CD3 Stage 2 — 일 레인(08:50) 집행 + 어제 naver_ad_daily 정산이 끝난 뒤라야 성과 판정이 정확)
    "run_naver_probe_learning",  # 09:03 (D-NAO-58 CD4 — 재계산이라 순서 무해하나 정산(08:55) 뒤·vault(09:05) 앞 의존 명시)
    "run_naver_diary_reflection",  # 08:35 크론이지만 catch-up은 집행(08:50) *뒤*(P2 리뷰 P2-1: LLM 재시도 최대 9분이 돈 잡 복구를 지연시키면 안 됨 — 관찰 전용이라 어제/D-2/D-8 버킷은 순서 무관. fail-open은 영구 블록만 막고 지연은 못 막는다)
    "run_naver_profit_scorecard",  # 08:40 크론이지만 catch-up은 돈 잡(08:50)·reflection 뒤(D-NAO-85/ref39 P7): reflection이 어제 일기를 소급 해석한 뒤라야 같은 날짜의 볼트 노트가 정합적이고, 관찰 전용 잡이 집행 복구를 지연시키면 안 된다. fail-open은 영구 블록만 막고 지연은 못 막는다
    "sweep_naver_keyword_hourly",  # 09:10 (D-NAO-46②, 독립 잡이나 표준 cron catch-up 포함)
    # ★검색량 기준선(09:50) — 이 목록의 기준(위 주석)이 「복구 가능성」인데, 이 잡은 그 기준의
    #   **극단**이다: keywordstool은 «오늘 값»만 주므로 놓친 날은 **원리적으로 못 채운다**
    #   (D-NAO-186이 이 적재를 「소급 불가·마감 있음」으로 승인한 바로 그 성질). 다른 수집 잡은
    #   다음 발화가 30일 창을 다시 긁어 구멍을 메우지만 이 잡은 못 메운다 — 광고비 3잡이
    #   편입된 이유(「어제 하루치만 적재」)와 같은 부류이고 정도는 더 심하다.
    #   상류 의존 없음 · 같은 날 재실행은 멱등(upsert) → 재시작 다중 실행에도 안전.
    #   맨 뒤 근처인 이유: 외부 API 왕복(약 800콜 상한)이라 돈 잡(집행) 복구를 지연시키면 안 된다.
    "sync_naver_keyword_baseline",  # 09:50
    "run_naver_wisdom",  # 08:45 크론이지만 catch-up은 돈 잡(08:50)·reflection 뒤(D-NAO-54 P3): reflection이 outcome을 소급 기입해야 후보 수확 결과가 최신 + LLM 판사(최대 회당 5×재시도)가 집행 복구를 지연시키면 안 됨. 관찰·보고 전용이라 맨 뒤 배치(diary_outcome 60일 하한 내면 하루 늦어도 무관). fail-open은 영구 블록만 막고 지연은 못 막는다
    "run_naver_vault_export",  # 09:05 크론이지만 catch-up은 맨 뒤(D-NAO-54 P5): 일기·지혜를 마크다운으로 재생성하는 열람 전용 잡이라 wisdom(승격)까지 끝난 뒤에 도는 게 볼트 최신성에 맞고, 파일 IO가 집행 복구를 지연시키면 안 된다. 매 실행 전체 재생성(멱등)이라 하루 늦어도 무해. fail-open은 영구 블록만 막고 지연은 못 막는다
    "sync_naver_entity",  # 07:35 크론이지만 catch-up은 맨 뒤: 전량 fetch(45캠페인+1,004그룹+~91k 키워드, 수 분)가 돈 잡(집행) 복구를 지연시키면 안 된다(위 관찰 잡들과 같은 원칙). 맨 뒤라 실패해도 끊길 하류가 없다(체인 중단=무해, last_run_at 미전진 → 다음 재시작 재시도). 성공하면 체이닝된 BM 스냅샷도 함께 따라잡힌다
)
_CATCHUP_LOOKBACK = timedelta(hours=12)  # 오늘 예정 발화가 이보다 오래됐으면 스킵(다음 정상 발화에 위임)

# 체인을 끊지 않는 catch-up 잡. _run_chain은 예외 시 break한다(상류 실패 → 하류 중단 =
# 의존 보존)인데, **정산은 이 체인의 상류가 아니다** — 회계 입력이지 집행
# (proposals→expert→auto_operator)의 선행 조건이 아니고, 실제로 아무 잡도 정산 잡의
# 성공을 기다리지 않는다(BEP 07:30은 catch-up 목록 밖이라 별도 발화).
# 정산 API가 잠깐 죽었다고 광고 자동운영 복구가 통째로 막히면 결합이 잘못된 것이다.
# ★잡 자체의 raise는 유지한다 — 정상 크론에서는 실패가 last_status로 드러나야 한다.
#   여기서 하는 것은 "실패를 실패로 기록하되 뒤를 막지 않는다"뿐이다(성공으로 위장 금지).
_CATCHUP_NON_BLOCKING: frozenset[str] = frozenset({
    "sync_naver_settlement",
    "sync_naver_case_settlement",
    # 광고비 3잡도 같은 이유(2026-08-03): 회계 입력이지 집행(proposals→expert→auto_operator)의
    # 선행 조건이 아니다. 외부 API(네이버 SA·비즈머니·Meta)에 의존하는 잡이라 남의 장애 확률이
    # 상대적으로 높은데, 그 하나가 광고 자동운영 복구 전체를 막으면 결합이 잘못된 것이다.
    # ★잡 자체의 raise는 유지 — 정상 크론에서는 실패가 last_status로 드러나야 한다.
    "sync_naver_sa_ad_costs",
    "sync_meta_ad_costs",
    "sync_naver_display_ad_costs",
    # ★검색량 기준선(2026-08-18, 적대 리뷰 P2 채택) — 위 광고비 3잡과 **같은 사유**이고 노출은
    #   더 크다: 외부 API(keywordstool)를 최대 800콜 **순차**로 두드리므로 남의 장애를 만날
    #   확률이 높은데, 이 잡은 catch-up 순서상 17/21이라 예외가 나면 뒤의 관찰 잡 3개
    #   (wisdom·vault_export·entity)가 그날 통째로 복구되지 않는다. 이 잡의 원료는
    #   `naver_ad_daily`·`naver_entity`뿐이고 하류에 아무도 의존하지 않는다 —
    #   기준선 적재가 실패했다고 볼트 재생성까지 막는 것은 결합이 잘못된 것이다.
    #   ★잡 자체의 raise는 유지(정상 크론에서는 실패가 last_status로 드러나야 한다).
    "sync_naver_keyword_baseline",
})


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
        # 정산 2개(05:25/05:30) — _CATCHUP_ORDER 맨 앞과 짝. 여기 빠지면 아래 funcs.get()이
        # None을 돌려주고 조용히 스킵된다(목록에만 넣고 끝내면 복구가 안 된다).
        "sync_naver_settlement": sync_naver_settlement_job,
        "sync_naver_case_settlement": sync_naver_case_settlement_job,
        # 광고비 3잡(07:00/07:00/07:10) — '어제 하루치만' 적재라 놓치면 손익에서 영구 누락된다
        # (2026-08-03 ENOSPC 사고). 셋 다 upsert 멱등이라 다중 재시작에도 안전.
        "sync_naver_sa_ad_costs": sync_naver_sa_ad_costs_job,
        "sync_meta_ad_costs": sync_meta_ad_costs_job,
        "sync_naver_display_ad_costs": sync_naver_display_ad_costs_job,
        "run_naver_forecast_engine": run_naver_forecast_engine_job,
        # proposals는 완주 검증판 사용(codex R3 P1-a): stage_status로 실제 생성 확인,
        # 미완주면 예외 → 체인 중단(expert가 pending 0으로 오실행되는 것 원천 차단).
        "generate_naver_proposals": _run_proposals_catchup_verified,
        "generate_expert_desk": generate_expert_desk_job,
        "run_naver_learning_loops": run_naver_learning_loops_job,
        "shopping_ad_product_sync": shopping_ad_product_sync_job,
        "run_naver_retro_scoring": run_naver_retro_scoring_job,
        "run_naver_diary_reflection": run_naver_diary_reflection_job,
        "run_naver_profit_scorecard": run_naver_profit_scorecard_job,
        "run_naver_auto_operator_daily": run_naver_auto_operator_daily_job,
        "run_naver_probe_settlement": run_naver_probe_settlement_job,
        "run_naver_probe_learning": run_naver_probe_learning_job,
        "sweep_naver_keyword_hourly": sweep_naver_keyword_hourly_job,
        # 목록에만 넣고 여기 빠지면 funcs.get()이 None을 돌려주고 조용히 스킵된다.
        "sync_naver_keyword_baseline": sync_naver_keyword_baseline_job,
        "sweep_naver_today_hourly": sweep_naver_today_hourly_job,
        "run_naver_wisdom": run_naver_wisdom_job,
        "run_naver_vault_export": run_naver_vault_export_job,
        # 엔티티 sync는 BM 스냅샷을 체이닝하므로 이 항목 하나로 두 잡이 함께 따라잡힌다.
        "sync_naver_entity": sync_naver_entity_job,
    }
    log.warning("[스케줄러] 아침배치 catch-up 대상(cron순 순차): %s", missed)

    def _run_chain():
        for job_name in missed:  # 이미 cron(의존) 순서
            func = funcs.get(job_name)
            if func is None:
                # ★조용한 스킵 금지: _CATCHUP_ORDER에 이름만 추가하고 funcs 등록을 빠뜨리면
                #   복구가 안 되는데 아무 흔적도 안 남는다(2026-08-03 정산 잡 편입 시 경계).
                log.error("[스케줄러] catch-up 함수 미등록: %s — funcs에 추가 필요", job_name)
                continue
            try:
                log.warning("[스케줄러] catch-up 순차 실행: %s", job_name)
                func()  # 동기 실행(각 잡이 자체 db 세션·예외 처리, 성공 시 정상 반환)
            except Exception as e:  # noqa: BLE001 — 상류 실패 시 하류 중단(의존 보존)
                _record_catchup_status(job_name, ok=False, exception=e)
                if job_name in _CATCHUP_NON_BLOCKING:
                    log.exception(
                        "[스케줄러] catch-up %s 실패 — 하류와 의존 없어 체인 계속: %s", job_name, e
                    )
                    continue
                log.exception(
                    "[스케줄러] catch-up %s 실패 → 체인 중단(다음 재시작 재시도): %s", job_name, e
                )
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
        "sync_naver_orders_hourly": sync_naver_orders_hourly_job,
        "auto_profit_calc": recalculate_profit_job,
        "cafe24_token_refresh": cafe24_proactive_refresh_job,
        "sync_naver_sa_ad_costs": sync_naver_sa_ad_costs_job,
        "sync_naver_display_ad_costs": sync_naver_display_ad_costs_job,
        "sync_naver_ad_daily": sync_naver_ad_daily_job,
        "snapshot_naver_ad_hourly": snapshot_naver_ad_hourly_job,
        "trigger_watch": trigger_watch_job,
        # run_naver_bm_layer는 크론 등록 대상이 아니다 — sync_naver_entity_job이 완료 직후 함수를
        # 직접 호출(체이닝)한다. 매핑에 남기면 toggle/재등록으로 07:37 독립 발화가 되살아난다.
        "sync_naver_entity": sync_naver_entity_job,
        "run_naver_bm_deep": run_naver_bm_deep_job,
        "shopping_ad_product_sync": shopping_ad_product_sync_job,
        "sync_naver_search_term": sync_naver_search_term_job,
        "sync_naver_keyword_volume": sync_naver_keyword_volume_job,
        "sync_naver_keyword_baseline": sync_naver_keyword_baseline_job,
        "run_naver_forecast_engine": run_naver_forecast_engine_job,
        "generate_naver_proposals": generate_naver_proposals_job,
        "run_naver_learning_loops": run_naver_learning_loops_job,
        "run_naver_retro_scoring": run_naver_retro_scoring_job,
        "run_naver_diary_reflection": run_naver_diary_reflection_job,
        "run_naver_profit_scorecard": run_naver_profit_scorecard_job,
        "run_naver_wisdom": run_naver_wisdom_job,
        "run_naver_vault_export": run_naver_vault_export_job,
        "sweep_naver_keyword_hourly": sweep_naver_keyword_hourly_job,
        "sweep_naver_today_hourly": sweep_naver_today_hourly_job,
        "run_naver_auto_operator_daily": run_naver_auto_operator_daily_job,
        "run_naver_auto_operator_hourly": run_naver_auto_operator_hourly_job,
        "run_naver_budget_pacing_reset": run_naver_budget_pacing_reset_job,
        "run_naver_probe_settlement": run_naver_probe_settlement_job,
        "run_naver_probe_learning": run_naver_probe_learning_job,
        "generate_expert_desk": generate_expert_desk_job,
        # 조치 생존 감시(D-NAO-173 P1-①). catch-up 목록엔 없다 — 매일 전량 재대조라 하루 유실이
        # 구멍을 남기지 않고, 대조가 멈춘 사실은 요약의 stale이 배너로 띄운다.
        "sync_naver_adgroup_targets": sync_naver_adgroup_targets_job,
        "sync_naver_criterion": sync_naver_criterion_job,  # D-NAO-203 (10:37)
        "sync_naver_product_meta": sync_naver_product_meta_job,  # D-NAO-212 (09:55)
        "write_naver_pooled_estimates": write_naver_pooled_estimates_job,  # D-NAO-214 M2-a (09:30)
        "verify_search_term_exclusions": verify_search_term_exclusions_job,
        "run_naver_flight_loop": run_naver_flight_loop_job,
        "sync_naver_settlement": sync_naver_settlement_job,
        "sync_naver_case_settlement": sync_naver_case_settlement_job,
        "run_naver_nbaesong_return_probe": run_naver_nbaesong_return_probe_job,
        "sync_meta_ad_costs": sync_meta_ad_costs_job,
        "sync_coupang_products": sync_coupang_products_job,
        "sync_coupang_returns": sync_coupang_returns_job,
        "sync_coupang_settlement": sync_coupang_settlement_job,
        "request_wing_vendor_summary_daily": request_wing_vendor_summary_daily_job,
        "request_coupang_ad_cost_daily": request_coupang_ad_cost_daily_job,
        "sync_coupang_rg_sizes": sync_coupang_rg_sizes_job,
        "sync_coupang_rg_inventory": sync_coupang_rg_inventory_job,
        "sync_coupang_rg_orders": sync_coupang_rg_orders_job,
        "sync_coupang_rg_inbound": sync_coupang_rg_inbound_job,
        "sync_coupang_rg_settlement": sync_coupang_rg_settlement_job,
        "auto_download_rg_settlement": auto_download_rg_settlement_job,
        "sync_coupang_coupons": sync_coupang_coupons_job,
        "sync_coupang_cs": sync_coupang_cs_job,
        "sync_coupang_ad_cost": sync_coupang_ad_cost_job,
        "coupang_collection_watchdog": coupang_collection_watchdog_job,
    }.get(job_name)


def job_kwargs_for(job_name: str) -> dict:
    """job별 add_job 추가 옵션(단일 진실 — start_scheduler·toggle 라이브 등록 공용).

    hourly 레인만 per-job misfire 5분(codex 9R[P2]: 놓친 :20은 폐기 — catch-up 제외
    정책과 정합). 그 외 잡은 전역 기본(3600s) 상속.
    """
    if job_name == "run_naver_auto_operator_hourly":
        return {"misfire_grace_time": _AUTO_OPERATOR_HOURLY_MISFIRE_GRACE}
    if job_name == "sync_naver_orders_hourly":
        return {"misfire_grace_time": _NAVER_ORDERS_HOURLY_MISFIRE_GRACE}
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
