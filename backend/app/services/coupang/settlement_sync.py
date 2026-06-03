# settlement_sync.py — 쿠팡 정산 동기화 Harness (회계 진짜 순이익 — P4, D-10/D-11)
# 흐름: 계정별 → [매출내역(7일 인식일 윈도우) → coupang_revenue_fee upsert + fee_audit] →
#       [지급내역(월별) → coupang_settlement_payout upsert].
# ★fee_audit(D-11): 실측 serviceFeeRatio ≠ 등록 sale_agent_commission 감지 → 권위 재확인(상품 API
#   saleAgentCommission 재조회) → ① 등록율이 실제로 바뀜=정당변동→자동 갱신 ② 등록율 그대로인데
#   실측만 다름=과오청구 의심→자동 수용 금지·이상 플래그·Jino 보고. (원칙22·원칙18-9·D-3)
# 트랙 D-8: vendor 2계정(WING1·WING2) 순회(RG 중복 불필요). 호출은 서버 IP에서만(로컬 403).
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.clients.coupang import CoupangProductClient, CoupangSettlementClient
from app.clients.coupang._base import CoupangReadError
from app.config import get_coupang_config
from app.models import (
    CoupangFeeChangeLog,
    CoupangProductItem,
    CoupangRevenueFee,
    CoupangSettlementPayout,
)

log = logging.getLogger(__name__)

SETTLEMENT_ACCOUNTS = ["COUPANG_WING1", "COUPANG_WING2"]
# revenue-history: token 필수, 11일 OK 확인(그 이상 미검증) → 보수적 7일 윈도우(주간 정산 단위와 일치).
_REVENUE_SPAN_DAYS = 7
_RATIO_EPSILON = Decimal("0.01")  # 수수료율 부동 비교 허용오차
_CALL_DELAY = 0.3  # 쿠팡 속도제한(429) 대응 — 호출 간 간격
_KST = ZoneInfo("Asia/Seoul")  # codex [P2]: 잡은 05:50 KST 실행, prod 서버 TZ=UTC →
# datetime.now()는 UTC날짜라 조회범위가 KST 기준 하루 stale. 조회 경계는 KST로 명시.


def _kst_today():
    """KST 기준 오늘 date. 조회 윈도우 경계 계산용(서버 UTC ↔ KST 날짜 경계 어긋남 방지)."""
    return datetime.now(_KST).date()


def _dec(v) -> Decimal:
    """숫자/문자 → Decimal. None(필드 부재)은 정상 0.

    codex [P1]: 값이 있는데 파싱 실패(필드명 변경·이상 포맷)하면 0 처리하되 log.warning으로
    표면화한다 — 핵심 금액이 조용히 0원 저장되는 silent corruption 방지(원칙22).
    """
    if v is None:
        return Decimal("0")
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        log.warning("쿠팡 정산 금액 파싱 실패 → 0 처리(데이터 확인 필요): %r", v)
        return Decimal("0")


def _opt_dec(v) -> Decimal | None:
    """값이 있으면 Decimal, None이면 None(수수료율처럼 '없음'과 '0'을 구분해야 하는 필드용)."""
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _parse_date(s):
    """쿠팡 날짜("yyyy-MM-dd")를 date로. 실패 None."""
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _revenue_windows(days: int, max_span: int = _REVENUE_SPAN_DAYS) -> list[tuple[str, str]]:
    """과거 days일(인식일)을 ≤max_span일 윈도우들로 분할. 끝은 어제(today-1).

    ★라이브 실측 보정(원칙22): recognitionDate(인식일)는 과거 시점이라 recognitionDateTo가
    오늘이면 쿠팡이 400 반환. 끝을 어제로 제한(오늘 인식 데이터는 어차피 거의 없음).
    """
    today = _kst_today()
    end = today - timedelta(days=1)
    start = end - timedelta(days=max(days, 1))
    windows: list[tuple[str, str]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=max_span - 1), end)
        windows.append((cursor.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")))
        cursor = chunk_end + timedelta(days=1)
    return windows


def _settlement_months(months: int) -> list[str]:
    """오늘(KST) 기준 과거 months개월의 'YYYY-MM' 목록(이번 달 포함)."""
    today = _kst_today()
    y, m = today.year, today.month
    result: list[str] = []
    for _ in range(max(months, 1)):
        result.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return result


def _upsert_revenue_fee(
    db: Session, account_key: str, vendor_id: str, txn: dict, item: dict,
    seen: dict | None = None,
) -> bool:
    """매출내역 옵션 1건을 coupang_revenue_fee에 upsert.

    grain=(order_id, vendor_item_id, recognition_date, sale_type). deliveryFee는 거래(주문) 헤더값.
    seen: per-run 캐시 — 같은 grain이 페이지/윈도우에 중복 등장해도 UNIQUE 충돌 방지(P2 패턴).
    codex [P2] 한계: revenue-history 응답에 line id가 없어, 같은 grain에 복수 정산 line이 오면
    뒤 행이 앞 행을 덮어쓴다. 라이브 실측(§6-1)에선 옵션당 단일 line만 확인됨 — 복수 line이
    실데이터에 등장하면 추가 식별자/사전집계로 보정(추측 구현 금지, 라이브 실측 보정 패턴).
    """
    order_id = str(txn.get("orderId") or "")
    vii = str(item.get("vendorItemId") or "")
    sale_type = txn.get("saleType") or "SALE"
    recognition_date = _parse_date(txn.get("recognitionDate"))
    if not order_id or not vii:
        return False
    key = (order_id, vii, recognition_date, sale_type)
    row = seen.get(key) if seen is not None else None
    if row is None:
        row = (
            db.query(CoupangRevenueFee)
            .filter(
                CoupangRevenueFee.order_id == order_id,
                CoupangRevenueFee.vendor_item_id == vii,
                CoupangRevenueFee.recognition_date == recognition_date,
                CoupangRevenueFee.sale_type == sale_type,
            )
            .first()
        )
    if row is None:
        row = CoupangRevenueFee(
            order_id=order_id, vendor_item_id=vii,
            recognition_date=recognition_date, sale_type=sale_type,
        )
        db.add(row)
    elif row.vendor_id and row.vendor_id != vendor_id:
        # vendorItemId 전역유일·단일계정 소유(D-8) 위반 신호 → 경고(침묵 손상 방지).
        log.warning(
            "매출내역 order %s vendorItemId %s account 변경 %s→%s (D-8 전역유일 위반 가능)",
            order_id, vii, row.vendor_id, vendor_id,
        )
    row.account_key = account_key
    row.vendor_id = vendor_id
    row.sale_date = _parse_date(txn.get("saleDate"))
    row.settlement_date = _parse_date(txn.get("settlementDate"))
    row.final_settlement_date = _parse_date(txn.get("finalSettlementDate"))
    row.product_id = str(item.get("productId")) if item.get("productId") else None
    row.product_name = (item.get("productName") or "")[:300] or None
    row.vendor_item_name = (item.get("vendorItemName") or "")[:300] or None
    row.sale_price = _dec(item.get("salePrice"))
    row.quantity = int(item.get("quantity") or 0)
    row.sale_amount = _dec(item.get("saleAmount"))
    row.service_fee = _dec(item.get("serviceFee"))
    row.service_fee_vat = _dec(item.get("serviceFeeVat"))
    row.service_fee_ratio = _opt_dec(item.get("serviceFeeRatio"))
    row.settlement_amount = _dec(item.get("settlementAmount"))
    row.coupang_discount_coupon = _dec(item.get("coupangDiscountCoupon"))
    row.seller_discount_coupon = _dec(item.get("sellerDiscountCoupon"))
    row.downloadable_coupon = _dec(item.get("downloadableCoupon"))
    row.courantee_fee = _dec(item.get("couranteeFee"))
    row.store_fee_discount = _dec(item.get("storeFeeDiscount"))
    row.external_seller_sku_code = (item.get("externalSellerSkuCode") or "")[:100] or None
    # 배송비(거래 헤더 — 옵션 행에 반복, 합산 시 order_id distinct)
    dfee = txn.get("deliveryFee") or {}
    row.delivery_fee_amount = _opt_dec(dfee.get("amount"))
    row.delivery_fee_fee = _opt_dec(dfee.get("fee"))
    row.delivery_fee_ratio = _opt_dec(dfee.get("feeRatio"))
    if seen is not None:
        seen[key] = row
    return True


def _reauthor_commission(product_client, seller_product_id, vendor_item_id) -> Decimal | None:
    """상품 API 재조회로 해당 옵션의 등록 판매수수료율(saleAgentCommission)을 권위 재확인. 실패 None.

    D-11: 불일치 감지 시 '진짜 등록율'을 권위(상품 API)에서 다시 가져와 정당변동/이상을 가른다.
    """
    if not seller_product_id:
        return None
    try:
        data = product_client.get_product(int(seller_product_id))
    except (TypeError, ValueError):
        return None
    if not data:
        return None
    for it in data.get("items", []) or []:
        if str(it.get("vendorItemId") or "") == str(vendor_item_id):
            return _opt_dec(it.get("saleAgentCommission"))
    return None


def _log_fee_change(
    db: Session, account_key: str, vendor_id: str, vii: str,
    registered: Decimal, observed: Decimal, reauthored: Decimal | None,
    change_type: str, txn: dict, note: str, resolved: bool,
) -> None:
    """수수료 불일치 1건을 coupang_fee_change_log에 upsert (grain=vii+observed+registered, 중복 방지)."""
    row = (
        db.query(CoupangFeeChangeLog)
        .filter(
            CoupangFeeChangeLog.vendor_item_id == vii,
            CoupangFeeChangeLog.observed_ratio == observed,
            CoupangFeeChangeLog.registered_ratio == registered,
        )
        .first()
    )
    if row is None:
        row = CoupangFeeChangeLog(
            vendor_item_id=vii, observed_ratio=observed, registered_ratio=registered
        )
        db.add(row)
    row.account_key = account_key
    row.vendor_id = vendor_id
    row.reauthored_ratio = reauthored
    row.change_type = change_type
    row.order_id = str(txn.get("orderId") or "") or None
    row.recognition_date = _parse_date(txn.get("recognitionDate"))
    row.note = note
    row.resolved = resolved
    if resolved and row.resolved_at is None:
        row.resolved_at = datetime.now()


def _fee_audit(
    db: Session, account_key: str, vendor_id: str, item: dict, txn: dict,
    product_client, audited: dict, reauth_cache: dict,
) -> str | None:
    """옵션 1건의 실측 수수료율 ↔ 등록율 감사 (D-11). 반환: 'legitimate'/'anomaly'/None(일치·비교불가).

    codex [P1]: 분기 결과 캐시(audited)는 (vii, observed, registered) 단위 — vii만으로 캐싱하면
    같은 옵션의 다른 불일치(12%정당 후 15%과오청구)가 캐시 히트로 오분류된다(안전장치 우회).
    권위 재확인 API(reauth_cache)만 vii 단위로 캐시(상품 API는 옵션당 동일 결과 — 중복 호출 방지).
    """
    vii = str(item.get("vendorItemId") or "")
    observed = _opt_dec(item.get("serviceFeeRatio"))
    if not vii or observed is None:
        return None
    pi = (
        db.query(CoupangProductItem)
        .filter(CoupangProductItem.vendor_item_id == vii)
        .first()
    )
    if pi is None or pi.sale_agent_commission is None:
        return None  # 등록율 없음(상품 미동기화 옵션) → 비교 불가
    registered = _dec(pi.sale_agent_commission)
    # ★라이브 실측 보정(원칙22): 등록율 0은 '수수료 0%'가 아니라 '미설정'(쿠팡 최소 4%).
    # product_sync가 못 채운 옵션을 유효 등록율로 오인하면 false anomaly 양산 → 비교 불가 처리.
    if registered <= 0:
        return None
    if abs(observed - registered) <= _RATIO_EPSILON:
        return None  # 일치 — 정상

    # 불일치 — 분기는 (vii, observed, registered)별, 권위 재확인 API는 vii 단위 캐시
    cache_key = (vii, observed, registered)
    if cache_key in audited:
        return audited[cache_key]
    if vii in reauth_cache:
        reauthored = reauth_cache[vii]
    else:
        seller_pid = pi.seller_product_id or (
            str(item.get("productId")) if item.get("productId") else None
        )
        reauthored = _reauthor_commission(product_client, seller_pid, vii)
        reauth_cache[vii] = reauthored

    if reauthored is not None and abs(reauthored - observed) <= _RATIO_EPSILON:
        # ① 권위(상품 API)도 실측율과 같음 = 등록율이 실제로 바뀜 = 정당변동 → 자동 갱신
        change_type = "legitimate"
        note = (
            f"등록율 {registered} → 권위 재확인 {reauthored}(=실측 {observed}). "
            "정당 변동 — sale_agent_commission 자동 갱신."
        )
        pi.sale_agent_commission = reauthored
        resolved = True
    else:
        # ② 자동 수용 금지 — 이상 플래그(과오청구 의심), Jino 보고
        change_type = "anomaly"
        if reauthored is None:
            note = (
                f"등록율 {registered} vs 실측 {observed} 불일치. 권위 재확인 실패(상품 API 응답 없음) "
                "— 자동 수용 보류, Jino 확인 필요."
            )
        elif abs(reauthored - registered) <= _RATIO_EPSILON:
            note = (
                f"등록율 {registered}=권위 재확인 {reauthored} 동일한데 실측만 {observed}. "
                "과오청구 의심 — 자동 수용 금지, Jino 확인 필요."
            )
        else:
            note = (
                f"등록 {registered}·실측 {observed}·권위 재확인 {reauthored} 3값 불일치. "
                "설명 안 됨 — 자동 수용 금지, Jino 확인 필요."
            )
        resolved = False

    _log_fee_change(
        db, account_key, vendor_id, vii, registered, observed, reauthored,
        change_type, txn, note, resolved,
    )
    audited[cache_key] = change_type
    return change_type


def _upsert_payout(
    db: Session, account_key: str, vendor_id: str, row_data: dict, seen: dict | None = None
) -> bool:
    """지급내역 정산 1건을 coupang_settlement_payout에 upsert. bank 정보(PII)는 저장 안 함."""
    settlement_type = row_data.get("settlementType") or ""
    settlement_date = _parse_date(row_data.get("settlementDate"))
    rec_from = _parse_date(row_data.get("revenueRecognitionDateFrom"))
    rec_to = _parse_date(row_data.get("revenueRecognitionDateTo"))
    if not settlement_type:
        return False
    key = (settlement_type, settlement_date, rec_from, rec_to)
    row = seen.get(key) if seen is not None else None
    if row is None:
        row = (
            db.query(CoupangSettlementPayout)
            .filter(
                CoupangSettlementPayout.vendor_id == vendor_id,
                CoupangSettlementPayout.settlement_type == settlement_type,
                CoupangSettlementPayout.settlement_date == settlement_date,
                CoupangSettlementPayout.revenue_recognition_date_from == rec_from,
                CoupangSettlementPayout.revenue_recognition_date_to == rec_to,
            )
            .first()
        )
    if row is None:
        row = CoupangSettlementPayout(
            vendor_id=vendor_id, settlement_type=settlement_type,
            settlement_date=settlement_date,
            revenue_recognition_date_from=rec_from, revenue_recognition_date_to=rec_to,
        )
        db.add(row)
    row.account_key = account_key
    row.revenue_recognition_year_month = row_data.get("revenueRecognitionYearMonth")
    row.total_sale = _dec(row_data.get("totalSale"))
    row.service_fee = _dec(row_data.get("serviceFee"))
    row.settlement_target_amount = _dec(row_data.get("settlementTargetAmount"))
    row.settlement_amount = _dec(row_data.get("settlementAmount"))
    row.last_amount = _dec(row_data.get("lastAmount"))
    row.pending_released_amount = _dec(row_data.get("pendingReleasedAmount"))
    row.seller_discount_coupon = _dec(row_data.get("sellerDiscountCoupon"))
    row.downloadable_coupon = _dec(row_data.get("downloadableCoupon"))
    row.dedicated_delivery_amount = _dec(row_data.get("dedicatedDeliveryAmount"))
    row.seller_service_fee = _dec(row_data.get("sellerServiceFee"))
    row.courantee_fee = _dec(row_data.get("couranteeFee"))
    row.courantee_customer_reward = _dec(row_data.get("couranteeCustomerReward"))
    row.deduction_amount = _dec(row_data.get("deductionAmount"))
    row.debt_of_last_week = _dec(row_data.get("debtOfLastWeek"))
    row.final_amount = _dec(row_data.get("finalAmount"))
    row.store_fee_discount = _dec(row_data.get("storeFeeDiscount"))
    row.status = row_data.get("status")
    if seen is not None:
        seen[key] = row
    return True


def sync_account_settlement(
    db: Session, account_key: str, *, days: int = 90, months: int = 6
) -> dict:
    """한 계정의 매출내역(수수료 감사 포함)+지급내역을 동기화. 반환: 통계 dict.

    days: 매출내역 인식일 조회 과거기간(7일 윈도우 분할). months: 지급내역 인식월 수.
    하드 실패(config 누락·읽기 실패)는 결과 dict의 error로 표면화 → 소비자가 raise 판단(원칙22).
    """
    cfg = get_coupang_config(account_key)
    if cfg is None:
        return {"account": account_key, "error": "config_missing"}
    settle_client = CoupangSettlementClient(cfg)
    product_client = CoupangProductClient(cfg)
    vendor_id = cfg.vendor_id
    stats = {
        "account": account_key, "vendor_id": vendor_id,
        "txns": 0, "fee_items": 0, "payouts": 0,
        "fee_legitimate": 0, "fee_anomaly": 0,
        "errors": 0, "api_failures": 0,
    }
    seen_rev: dict = {}
    seen_pay: dict = {}
    audited: dict = {}       # (vii, observed, registered) → change_type (분기 결과 캐시)
    reauth_cache: dict = {}  # vendor_item_id → reauthored (권위 재확인 API 1회 캐시)

    # ① 매출내역(7일 인식일 윈도우) → 적재 + 수수료 감사
    for win_from, win_to in _revenue_windows(days):
        try:
            for txn in settle_client.iter_revenue_history(
                recognition_date_from=win_from, recognition_date_to=win_to
            ):
                stats["txns"] += 1
                for item in txn.get("items", []) or []:
                    if _upsert_revenue_fee(db, account_key, vendor_id, txn, item, seen_rev):
                        stats["fee_items"] += 1
                    ct = _fee_audit(
                        db, account_key, vendor_id, item, txn,
                        product_client, audited, reauth_cache,
                    )
                    if ct == "legitimate":
                        stats["fee_legitimate"] += 1
                    elif ct == "anomaly":
                        stats["fee_anomaly"] += 1
        except CoupangReadError:  # API 하드실패 — stale 위장 금지(원칙22)
            log.exception("매출내역 읽기 실패 %s %s~%s", account_key, win_from, win_to)
            stats["api_failures"] += 1
        except Exception:  # noqa: BLE001 — 한 호출 오류가 전체를 막지 않게
            log.exception("매출내역 오류 %s %s~%s", account_key, win_from, win_to)
            stats["errors"] += 1
        time.sleep(_CALL_DELAY)

    # ② 지급내역(인식월별) → 적재
    for ym in _settlement_months(months):
        try:
            rows = settle_client.get_settlement_histories(revenue_recognition_year_month=ym)
            if rows is None:  # None=하드 실패(빈 배열 []은 정상 미정산)
                log.error("지급내역 읽기 실패(None) %s %s", account_key, ym)
                stats["api_failures"] += 1
            else:
                for row_data in rows:
                    if _upsert_payout(db, account_key, vendor_id, row_data, seen_pay):
                        stats["payouts"] += 1
        except Exception:  # noqa: BLE001
            log.exception("지급내역 오류 %s %s", account_key, ym)
            stats["errors"] += 1
        time.sleep(_CALL_DELAY)

    # 정상 동기화분 먼저 커밋(좋은 데이터 보존), 그 후 하드 실패 표면화.
    db.commit()
    if stats["api_failures"]:
        stats["error"] = (
            f"쿠팡 정산 읽기 실패 {stats['api_failures']}건 "
            "(IP화이트리스트/인증/네트워크 가능 — 데이터 stale 위험)"
        )
    log.info("쿠팡 정산 동기화 완료 %s: %s", account_key, stats)
    return stats


def sync_all_settlement(db: Session, *, days: int = 90, months: int = 6) -> list[dict]:
    """2개 셀러계정(WING1·WING2) 정산(매출내역+지급내역) 전체 동기화 + 수수료 감사."""
    return [
        sync_account_settlement(db, key, days=days, months=months)
        for key in SETTLEMENT_ACCOUNTS
    ]
