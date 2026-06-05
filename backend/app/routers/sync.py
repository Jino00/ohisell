# routers/sync.py — 채널 동기화 API
from __future__ import annotations

from datetime import date

import concurrent.futures
import logging

from fastapi import APIRouter, Depends
from sqlalchemy import desc
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

from app.database import get_db
from app.models import Channel, SyncLog
from app.schemas import SyncRequest, SyncResult, SyncStatusOut
from app.services.sync_service import sync_channel_orders

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.post("/channel/{channel_id}", response_model=SyncResult)
def sync_channel(channel_id: int, body: SyncRequest | None = None, db: Session = Depends(get_db)):
    """단일 채널 주문 동기화"""
    date_from = None
    date_to = None
    if body:
        if body.date_from:
            date_from = date.fromisoformat(body.date_from)
        if body.date_to:
            date_to = date.fromisoformat(body.date_to)

    result = sync_channel_orders(db, channel_id, date_from, date_to)
    return result


@router.post("/all", response_model=list[SyncResult])
def sync_all(body: SyncRequest | None = None, db: Session = Depends(get_db)):
    """API 연동 가능한 전체 채널 동기화"""
    channels = db.query(Channel).filter(Channel.api_type != "excel").all()
    results = []

    date_from = None
    date_to = None
    if body:
        if body.date_from:
            date_from = date.fromisoformat(body.date_from)
        if body.date_to:
            date_to = date.fromisoformat(body.date_to)

    for ch in channels:
        result = sync_channel_orders(db, ch.id, date_from, date_to)
        results.append(result)

    return results


@router.post("/coupang-products")
def sync_coupang_products(
    refresh_inventory: bool = True,
    max_products: int | None = None,
    db: Session = Depends(get_db),
):
    """쿠팡 상품 마스터+채널매핑 동기화 (조망 결합축 적재).

    트랙 D-8: 쿠팡 Open API는 서버 IP 화이트리스트 — 서버에서만 동작(로컬 403).
    max_products: 계정별 상한(드라이런/부분동기화용). refresh_inventory: 재고/판매상태 새로고침.
    """
    from app.services.coupang.product_sync import sync_all_products, sync_account_products, PRODUCT_ACCOUNTS

    if max_products is not None:
        return [
            sync_account_products(
                db, key, refresh_inventory=refresh_inventory, max_products=max_products
            )
            for key in PRODUCT_ACCOUNTS
        ]
    return sync_all_products(db, refresh_inventory=refresh_inventory)


@router.post("/coupang-returns")
def sync_coupang_returns(
    days: int = 35,
    db: Session = Depends(get_db),
):
    """쿠팡 반품/취소/교환 동기화 (순매출 차감 회계축 적재).

    트랙 D-8: 쿠팡 Open API는 서버 IP 화이트리스트 — 서버에서만 동작(로컬 403).
    트랙 D-3: 사실/지표 정리만(전략판단 없음). days: 과거 동기화 기간(31일 윈도우로 자동 분할).
    """
    from app.services.coupang.returns_sync import sync_all_returns

    return sync_all_returns(db, days=days)


@router.post("/coupang-settlement")
def sync_coupang_settlement(
    days: int = 90,
    months: int = 6,
    db: Session = Depends(get_db),
):
    """쿠팡 정산(매출내역+지급내역) 동기화 + 수수료 감사 (회계 진짜 순이익 — D-13).

    트랙 D-8: 쿠팡 Open API는 서버 IP 화이트리스트 — 서버에서만 동작(로컬 403).
    트랙 D-3: 사실/지표 정리만(전략판단 없음). days: 매출내역 인식일 과거기간(7일 윈도우 분할),
    months: 지급내역 인식월 수. 수수료 감사(D-13)=각 옵션 자기 정착 실측율(mode) 기준선 대비
    율 변동 감지 → rate_drift 플래그(자동판단 금지, Jino 확인). stats fee_options_checked/fee_anomaly.
    """
    from app.services.coupang.settlement_sync import sync_all_settlement

    return sync_all_settlement(db, days=days, months=months)


@router.post("/coupang-rg-sizes")
def sync_coupang_rg_sizes(
    max_products: int | None = None,
    db: Session = Depends(get_db),
):
    """로켓그로스 상품 사이즈 동기화 (보관비 CBM 토대 — P3/D-14).

    트랙 D-8: 쿠팡 Open API는 서버 IP 화이트리스트 — 서버에서만 동작(로컬 403).
    RG 상품조회 skuInfo(width/length/height_mm·weight_g) → coupang_product_item 사이즈·cbm 적재.
    max_products: 계정별 상한(드라이런/부분동기화용).
    """
    from app.services.coupang.rg_size_sync import sync_all_rg_sizes, sync_account_rg_sizes, RG_ACCOUNTS

    if max_products is not None:
        return [sync_account_rg_sizes(db, key, max_products=max_products) for key in RG_ACCOUNTS]
    return sync_all_rg_sizes(db)


@router.post("/coupang-rg-inventory")
def sync_coupang_rg_inventory(db: Session = Depends(get_db)):
    """로켓창고 재고 동기화 (옵션별 주문가능수량·30일판매 — P3/D-14).

    트랙 D-8: 쿠팡 Open API는 서버 IP 화이트리스트 — 서버에서만 동작(로컬 403).
    rg/inventory/summaries → coupang_rg_inventory upsert. rg_open_api 분당 50회 제한.
    """
    from app.services.coupang.rg_inventory_sync import sync_all_rg_inventory

    return sync_all_rg_inventory(db)


@router.post("/coupang-rg-orders")
def sync_coupang_rg_orders(
    days: int = 30,
    db: Session = Depends(get_db),
):
    """로켓그로스 주문 동기화 (향후 RG 매출 — P3/D-14). 기존 Order와 분리(이중계산 방지).

    트랙 D-8: 쿠팡 Open API는 서버 IP 화이트리스트 — 서버에서만 동작(로컬 403).
    rg/orders → coupang_rg_order_item. days: 과거 기간(30일 윈도우로 자동 분할).
    """
    from app.services.coupang.rg_order_sync import sync_all_rg_orders

    return sync_all_rg_orders(db, days=days)


@router.post("/coupang-coupons")
def sync_coupang_coupons(
    budget_months: int = 3,
    db: Session = Depends(get_db),
):
    """쿠팡 쿠폰 운영 현황 동기화 (즉시할인쿠폰+아이템+예산/계약 — P5).

    트랙 D-8: 쿠팡 Open API는 서버 IP 화이트리스트 — 서버에서만 동작(로컬 403).
    트랙 D-3: 운영 현황(사실)만. 실제 할인 비용 차감은 정산(P4)이 진실 — 이건 보조축.
    즉시할인쿠폰 상태별 목록 → coupang_coupon, 각 쿠폰 아이템 → coupang_coupon_item(옵션 결합 D-8),
    계약서+월별 예산 → coupang_coupon_budget. budget_months: 예산 조회 과거 개월수.
    ⚠️ 다운로드쿠폰은 목록 API 없어 자동 sync 제외(명세 §E).
    """
    from app.services.coupang.coupon_sync import sync_all_coupons

    return sync_all_coupons(db, budget_months=budget_months)


@router.post("/coupang-cs")
def sync_coupang_cs(db: Session = Depends(get_db)):
    """쿠팡 CS(고객문의) 동기화 — 상품 Q&A + CS이관 문의 7일치 (P6).

    트랙 D-8: 쿠팡 Open API는 서버 IP 화이트리스트 — 서버에서만 동작(로컬 403).
    결과: coupang_inquiry upsert + 미답변 현황 집계.
    """
    from app.services.coupang.cs_sync import sync_all_cs

    return sync_all_cs(db)


@router.post("/realtime")
def sync_realtime(db: Session = Depends(get_db)):
    """접속/새로고침 시 호출 — 모든 실시간 데이터를 병렬로 동기화.

    포함: 전체 채널 주문 · 쿠팡 광고비 · 네이버 SA 광고비 · Meta 광고비
    각 항목은 독립적으로 fail-soft(오류 발생 시 해당 항목만 error 표시, 나머지 계속).
    """
    from app.database import SessionLocal

    results: dict = {}

    def _sync_one_channel(ch_id: int) -> tuple[int, int]:
        """단일 채널 주문 sync — 채널별 독립 세션(스레드 안전). (new_or_updated, error수) 반환."""
        _db = SessionLocal()
        try:
            r = sync_channel_orders(_db, ch_id, None, None)
            return r.new_orders + r.updated_orders, 0
        except Exception as e:
            log.warning("realtime: 주문 sync 실패 ch=%s: %s", ch_id, e)
            return 0, 1
        finally:
            _db.close()

    def _run_orders():
        # 채널 목록만 짧게 조회 후 세션 닫고, 각 채널은 병렬(느린 네트워크 fetch 겹침).
        # 채널별 DB 쓰기는 SQLite busy_timeout으로 직렬 대기(database.py 하드닝).
        _db = SessionLocal()
        try:
            ch_ids = [c.id for c in _db.query(Channel).filter(Channel.api_type != "excel").all()]
        finally:
            _db.close()
        if not ch_ids:
            return {"new_or_updated": 0, "channel_errors": 0}
        ok, err = 0, 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(ch_ids))) as cex:
            for o, e in cex.map(_sync_one_channel, ch_ids):
                ok += o
                err += e
        return {"new_or_updated": ok, "channel_errors": err}

    def _run_coupang_ad():
        _db = SessionLocal()
        try:
            from app.services.coupang.ad_cost_sync import sync_ad_cost
            return sync_ad_cost(_db)
        except Exception as e:
            log.warning("realtime: 쿠팡 광고비 sync 실패: %s", e)
            return {"error": str(e)}
        finally:
            _db.close()

    def _run_naver_sa():
        _db = SessionLocal()
        try:
            from datetime import timedelta
            from decimal import Decimal
            from sqlalchemy import text
            from app.utils.kst import kst_today
            from app.routers.ad_costs import _extract_naver_sa_keyword, _upsert_ad_cost, _upsert_ad_revenue, NAVER_SA_CONV_SOURCE
            from app.services.naver_sa_ad_fetcher import fetch_campaign_daily_spend, fetch_daily_conversion_revenue

            yesterday = kst_today() - timedelta(days=1)
            naver_row = _db.execute(text("SELECT id FROM channels WHERE code='NAVER' LIMIT 1")).fetchone()
            if not naver_row:
                return {"error": "NAVER 채널 없음"}
            naver_id = naver_row[0]

            campaigns = fetch_campaign_daily_spend(yesterday, yesterday)
            agg: dict = {}
            for c in campaigns:
                kw = _extract_naver_sa_keyword(c["campaign_name"])
                key = (c["date"], f"naver_sa:{kw}")
                agg[key] = agg.get(key, Decimal("0")) + c["spend"]

            for (dt_str, source), spend in agg.items():
                from datetime import date as date_cls
                _upsert_ad_cost(_db, naver_id, date_cls.fromisoformat(dt_str), spend, source)

            try:
                conv_daily = fetch_daily_conversion_revenue(yesterday, yesterday)
                for dt_str, rev in conv_daily.items():
                    from datetime import date as date_cls
                    _upsert_ad_revenue(_db, naver_id, date_cls.fromisoformat(dt_str), rev, NAVER_SA_CONV_SOURCE)
            except Exception as ce:
                log.warning("realtime: Naver SA 전환매출 실패: %s", ce)

            _db.commit()
            return {"date": str(yesterday), "inserted": len(agg), "total_spend": int(sum(agg.values()))}
        except Exception as e:
            log.warning("realtime: 네이버 SA sync 실패: %s", e)
            return {"error": str(e)}
        finally:
            _db.close()

    def _run_meta():
        _db = SessionLocal()
        try:
            from datetime import timedelta
            from decimal import Decimal
            from sqlalchemy import text
            from app.utils.kst import kst_today
            from app.routers.ad_costs import _extract_meta_keyword, _upsert_ad_cost
            from app.services.meta_ad_fetcher import fetch_campaign_daily_spend

            yesterday = kst_today() - timedelta(days=1)
            cafe24_row = _db.execute(text("SELECT id FROM channels WHERE code='CAFE24' LIMIT 1")).fetchone()
            if not cafe24_row:
                return {"skipped": "CAFE24 채널 없음"}
            cafe24_id = cafe24_row[0]

            campaigns = fetch_campaign_daily_spend(yesterday, yesterday)
            agg: dict = {}
            for c in campaigns:
                kw = _extract_meta_keyword(c["campaign_name"])
                key = (c["date"], f"meta:{kw}")
                agg[key] = agg.get(key, Decimal("0")) + c["spend"]

            for (dt_str, source), spend in agg.items():
                from datetime import date as date_cls
                _upsert_ad_cost(_db, cafe24_id, date_cls.fromisoformat(dt_str), spend, source)
            _db.commit()
            return {"date": str(yesterday), "inserted": len(agg), "total_spend": int(sum(agg.values()))}
        except Exception as e:
            log.warning("realtime: Meta sync 실패: %s", e)
            return {"error": str(e)}
        finally:
            _db.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        f_orders = ex.submit(_run_orders)
        f_coupang_ad = ex.submit(_run_coupang_ad)
        f_naver_sa = ex.submit(_run_naver_sa)
        f_meta = ex.submit(_run_meta)
        results["orders"] = f_orders.result()
        results["coupang_ad"] = f_coupang_ad.result()
        results["naver_sa"] = f_naver_sa.result()
        results["meta"] = f_meta.result()

    log.info("realtime sync 완료: %s", results)
    return results


@router.get("/status", response_model=list[SyncStatusOut])
def sync_status(db: Session = Depends(get_db)):
    """채널별 마지막 동기화 상태"""
    channels = db.query(Channel).all()
    statuses = []

    for ch in channels:
        last_log = (
            db.query(SyncLog)
            .filter(SyncLog.channel_id == ch.id)
            .order_by(desc(SyncLog.started_at))
            .first()
        )
        statuses.append(SyncStatusOut(
            channel_id=ch.id,
            channel_name=ch.name,
            last_sync=last_log.completed_at if last_log else None,
            status=last_log.status if last_log else None,
            records_synced=last_log.records_synced if last_log else 0,
        ))

    return statuses
