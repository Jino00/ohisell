# scheduler_service.py — APScheduler 기반 자동 동기화/이익률 계산 스케줄러
from __future__ import annotations

import logging
from app.utils.kst import kst_now, kst_today
from datetime import date, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.database import SessionLocal, get_ad_db
from app.models import Channel, OAuthToken, SchedulerState

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
    """전체 API 채널 주문 자동 동기화 (스케줄러 작업) — Wing + RG 포함"""
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

        # RG 주문도 함께 동기화 (Wing과 매출 일치를 위해)
        try:
            from app.services.coupang.rg_order_sync import sync_all_rg_orders
            rg_results = sync_all_rg_orders(db, days=3)
            log.info("[스케줄러] RG 주문 동기화 완료: %s", rg_results)
        except Exception as e:
            log.error("[스케줄러] RG 주문 동기화 에러: %s", e)

        # 실행 시각 기록
        state = db.query(SchedulerState).filter(
            SchedulerState.job_name == "auto_sync_orders"
        ).first()
        if state:
            state.last_run_at = kst_now()
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

        date_to = kst_today()
        date_from = date_to - timedelta(days=7)

        result = calculate_daily_trend(db, ad_db, None, date_from, date_to)
        log.info("[스케줄러] 이익률 재계산 완료: %d일치 데이터", len(result))

        state = db.query(SchedulerState).filter(
            SchedulerState.job_name == "auto_profit_calc"
        ).first()
        if state:
            state.last_run_at = kst_now()
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

        state = db.query(SchedulerState).filter(
            SchedulerState.job_name == "sync_coupang_products"
        ).first()
        if state:
            state.last_run_at = kst_now()
            db.commit()
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

        state = db.query(SchedulerState).filter(
            SchedulerState.job_name == "sync_coupang_returns"
        ).first()
        if state:
            state.last_run_at = kst_now()
            db.commit()
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

        state = db.query(SchedulerState).filter(
            SchedulerState.job_name == "sync_coupang_settlement"
        ).first()
        if state:
            state.last_run_at = kst_now()
            db.commit()
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

        state = db.query(SchedulerState).filter(
            SchedulerState.job_name == "sync_coupang_rg_sizes"
        ).first()
        if state:
            state.last_run_at = kst_now()
            db.commit()
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

        state = db.query(SchedulerState).filter(
            SchedulerState.job_name == "sync_coupang_rg_inventory"
        ).first()
        if state:
            state.last_run_at = kst_now()
            db.commit()
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

        state = db.query(SchedulerState).filter(
            SchedulerState.job_name == "sync_coupang_rg_orders"
        ).first()
        if state:
            state.last_run_at = kst_now()
            db.commit()
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

        state = db.query(SchedulerState).filter(
            SchedulerState.job_name == "sync_coupang_rg_inbound"
        ).first()
        if state:
            state.last_run_at = kst_now()
            db.commit()
    except Exception as e:
        log.exception("[스케줄러] sync_coupang_rg_inbound_job 에러: %s", e)
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

        state = db.query(SchedulerState).filter(
            SchedulerState.job_name == "sync_coupang_coupons"
        ).first()
        if state:
            state.last_run_at = kst_now()
            db.commit()
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

        state = db.query(SchedulerState).filter(
            SchedulerState.job_name == "sync_coupang_cs"
        ).first()
        if state:
            state.last_run_at = kst_now()
            db.commit()
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
    finally:
        db.close()


def _ensure_default_states(db):
    """기본 스케줄러 상태 DB 레코드 생성"""
    defaults = [
        ("auto_sync_orders", "0 6 * * *"),
        ("auto_profit_calc", "30 6 * * *"),
        ("cafe24_token_refresh", "*/30 * * * *"),
        ("sync_naver_sa_ad_costs", "0 7 * * *"),
        ("sync_naver_settlement", "25 5 * * *"),
        ("sync_naver_case_settlement", "30 5 * * *"),
        ("sync_meta_ad_costs", "0 7 * * *"),
        ("sync_coupang_rg_inbound", "20 5 * * *"),
        ("sync_coupang_products", "30 5 * * *"),
        ("sync_coupang_rg_sizes", "35 5 * * *"),
        ("sync_coupang_rg_inventory", "40 5 * * *"),
        ("sync_coupang_returns", "45 5 * * *"),
        ("sync_coupang_settlement", "50 5 * * *"),
        ("sync_coupang_rg_orders", "0 */2 * * *"),
        ("sync_coupang_coupons", "0 6 * * *"),
        ("sync_coupang_cs", "5 6 * * *"),
        ("sync_coupang_ad_cost", "10 0 * * *"),
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
            elif state.job_name == "cafe24_token_refresh":
                job_func = cafe24_proactive_refresh_job
            elif state.job_name == "sync_naver_sa_ad_costs":
                job_func = sync_naver_sa_ad_costs_job
            elif state.job_name == "sync_naver_settlement":
                job_func = sync_naver_settlement_job
            elif state.job_name == "sync_naver_case_settlement":
                job_func = sync_naver_case_settlement_job
            elif state.job_name == "sync_meta_ad_costs":
                job_func = sync_meta_ad_costs_job
            elif state.job_name == "sync_coupang_products":
                job_func = sync_coupang_products_job
            elif state.job_name == "sync_coupang_returns":
                job_func = sync_coupang_returns_job
            elif state.job_name == "sync_coupang_settlement":
                job_func = sync_coupang_settlement_job
            elif state.job_name == "sync_coupang_rg_sizes":
                job_func = sync_coupang_rg_sizes_job
            elif state.job_name == "sync_coupang_rg_inventory":
                job_func = sync_coupang_rg_inventory_job
            elif state.job_name == "sync_coupang_rg_orders":
                job_func = sync_coupang_rg_orders_job
            elif state.job_name == "sync_coupang_rg_inbound":
                job_func = sync_coupang_rg_inbound_job
            elif state.job_name == "sync_coupang_coupons":
                job_func = sync_coupang_coupons_job
            elif state.job_name == "sync_coupang_cs":
                job_func = sync_coupang_cs_job
            elif state.job_name == "sync_coupang_ad_cost":
                job_func = sync_coupang_ad_cost_job

            if job_func:
                try:
                    # from_crontab는 timezone 미지정 시 서버 로컬 TZ(프로덕션=UTC)로
                    # 폴백한다. 미리 만든 trigger를 add_job에 넘기면 스케줄러의
                    # 기본 timezone이 적용되지 않으므로 KST를 명시한다.
                    trigger = CronTrigger.from_crontab(
                        state.cron_expression, timezone="Asia/Seoul"
                    )
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
