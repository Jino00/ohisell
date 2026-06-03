# settlement_sync.py — 쿠팡 정산 동기화 Harness (회계 진짜 순이익 — P4, D-13)
# 흐름: 계정별 → [매출내역(7일 인식일 윈도우) → coupang_revenue_fee upsert] →
#       [지급내역(월별) → coupang_settlement_payout upsert] → [수수료 자기기준선 감사].
# ★수수료 감사(D-13, D-10/D-11 기준선 교체): saleAgentCommission(등록율)이 라이브에서 전부 0이라
#   기준선으로 쓸 수 없음(판매대행 수수료, 카테고리 판매수수료 아님). → 기준선 = 각 옵션의 정착
#   실측율(service_fee_ratio history mode). 한 옵션이 기간 내 여러 율을 보이면=변동/이상 →
#   coupang_fee_change_log에 rate_drift 플래그 + Jino 보고. 자동 판단·자동 수용 금지(D-3·D-11 정신).
# 트랙 D-8: vendor 2계정(WING1·WING2) 순회(RG 중복 불필요). 호출은 서버 IP에서만(로컬 403).
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.clients.coupang import CoupangSettlementClient
from app.clients.coupang._base import CoupangReadError
from app.config import get_coupang_config
from app.models import (
    CoupangFeeChangeLog,
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


def _log_fee_change(
    db: Session, account_key: str, vendor_id: str, vii: str,
    baseline: Decimal, outlier: Decimal, txn: dict, note: str,
) -> None:
    """수수료 이상 1건(한 옵션의 두 율쌍)을 coupang_fee_change_log에 upsert (중복 방지).

    D-13: 컬럼은 **의미 보존** — registered_ratio=기준선(정착 mode), observed_ratio=이탈율
    (API가 이 의미로 노출). dedup은 **양방향 조회**로 — 같은 옵션의 같은 율쌍이면 (baseline,
    outlier) 순서가 뒤바뀌어 저장돼 있어도 찾아서 갱신한다. codex[P2]: 시간이 지나 mode가
    뒤집혀도(7.8기준↔9.0기준) 1행만 유지하되, 컬럼엔 현재 방향(기준선/이탈)을 반영한다.
    reauthored_ratio 미사용(None; 카테고리율 교차 P6 여지). resolved는 기존값 유지(자동 판단 금지).
    """
    row = (
        db.query(CoupangFeeChangeLog)
        .filter(
            CoupangFeeChangeLog.vendor_item_id == vii,
            or_(
                and_(
                    CoupangFeeChangeLog.registered_ratio == baseline,
                    CoupangFeeChangeLog.observed_ratio == outlier,
                ),
                and_(
                    CoupangFeeChangeLog.registered_ratio == outlier,
                    CoupangFeeChangeLog.observed_ratio == baseline,
                ),
            ),
        )
        .first()
    )
    if row is None:
        row = CoupangFeeChangeLog(vendor_item_id=vii)
        db.add(row)
    # 현재 방향 반영(플립 시 갱신) — registered=기준선·observed=이탈
    row.registered_ratio = baseline
    row.observed_ratio = outlier
    row.account_key = account_key
    row.vendor_id = vendor_id
    row.reauthored_ratio = None
    row.change_type = "rate_drift"
    row.order_id = str(txn.get("orderId") or "") or None
    row.recognition_date = _parse_date(txn.get("recognitionDate"))
    row.note = note
    # resolved는 기존 값 유지(Jino가 한번 resolve한 건 재감지로 되돌리지 않음). 신규는 모델 기본 False.


def _audit_fee_baseline(db: Session, account_key: str, vendor_id: str) -> dict:
    """옵션 자기 기준선(정착 실측율) 감사 (D-13). saleAgentCommission(전부 0)을 기준선으로 못 쓰므로
    각 옵션의 service_fee_ratio 이력에서 정착율(최빈 mode)을 기준선으로 삼고, 같은 옵션이 기간 내
    다른 율을 보이면 = 변동/이상 → coupang_fee_change_log에 rate_drift 플래그(자동 판단 금지, Jino 보고).

    100% 커버(실측율은 옵션마다 있음). 멱등(grain vii+baseline+이탈율 — 재실행시 같은 행 갱신).
    반환: {'options_checked', 'anomaly'}.
    """
    rows = (
        db.query(
            CoupangRevenueFee.vendor_item_id,
            CoupangRevenueFee.service_fee_ratio,
            func.count(CoupangRevenueFee.id),
            func.min(CoupangRevenueFee.recognition_date),
            func.max(CoupangRevenueFee.recognition_date),
            func.max(CoupangRevenueFee.order_id),
        )
        .filter(
            CoupangRevenueFee.account_key == account_key,
            CoupangRevenueFee.service_fee_ratio.isnot(None),
        )
        .group_by(
            CoupangRevenueFee.vendor_item_id, CoupangRevenueFee.service_fee_ratio
        )
        .all()
    )
    by_opt: dict[str, list[dict]] = {}
    for vii, ratio, cnt, dmin, dmax, oid in rows:
        r = _opt_dec(ratio)
        if r is None:
            continue
        by_opt.setdefault(str(vii), []).append(
            {"ratio": r, "count": int(cnt or 0), "dmin": dmin, "dmax": dmax, "order_id": oid}
        )

    stats = {"options_checked": 0, "anomaly": 0}
    for vii, dist in by_opt.items():
        stats["options_checked"] += 1
        if len(dist) <= 1:
            continue  # 단일 정착율 — 정상(기준선 확립). 자기기준선과 일치.

        # 기준선 = 최빈(건수 desc). 동률이면 시작 이른 율(원래 정착), 그래도 동률이면 낮은 율(결정적).
        def _bl_key(d: dict) -> tuple:
            dmin_ord = d["dmin"].toordinal() if d["dmin"] else 10**9
            return (-d["count"], dmin_ord, float(d["ratio"]))

        baseline = min(dist, key=_bl_key)
        for d in dist:
            if d is baseline:
                continue
            if abs(d["ratio"] - baseline["ratio"]) <= _RATIO_EPSILON:
                continue  # 부동오차 내 동일 — 변동 아님
            note = (
                f"옵션 {vii} 수수료율 변동 감지: 기준 {baseline['ratio']}%"
                f"({baseline['count']}건, {baseline['dmin']}~{baseline['dmax']}) ↔ "
                f"이탈 {d['ratio']}%({d['count']}건, {d['dmin']}~{d['dmax']}). "
                "정당변동/과오청구 — 자동판단 금지, Jino 확인."
            )
            txn = {
                "orderId": d["order_id"],
                "recognitionDate": d["dmax"].isoformat() if d["dmax"] else None,
            }
            # 의미 보존: baseline=기준선(mode)·outlier=이탈. dedup은 _log_fee_change가 양방향 조회로
            # 처리(mode 플립에도 1행 유지·현재 방향 반영, codex[P2]).
            _log_fee_change(db, account_key, vendor_id, vii, baseline["ratio"], d["ratio"], txn, note)
            stats["anomaly"] += 1
    return stats


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
    vendor_id = cfg.vendor_id
    stats = {
        "account": account_key, "vendor_id": vendor_id,
        "txns": 0, "fee_items": 0, "payouts": 0,
        "fee_options_checked": 0, "fee_anomaly": 0,
        "errors": 0, "api_failures": 0,
    }
    seen_rev: dict = {}
    seen_pay: dict = {}

    # ① 매출내역(7일 인식일 윈도우) → 적재 (감사는 적재 완료 후 자기기준선 스윕으로 D-13)
    for win_from, win_to in _revenue_windows(days):
        try:
            for txn in settle_client.iter_revenue_history(
                recognition_date_from=win_from, recognition_date_to=win_to
            ):
                stats["txns"] += 1
                for item in txn.get("items", []) or []:
                    if _upsert_revenue_fee(db, account_key, vendor_id, txn, item, seen_rev):
                        stats["fee_items"] += 1
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

    # ③ 수수료 자기기준선 감사(D-13) — 적재된 매출내역 전체(기존+이번분)에서 옵션별 정착율 확인.
    #    autoflush로 이번 적재분 포함. 율 변동 옵션은 rate_drift 플래그(자동판단 금지, Jino 보고).
    try:
        audit = _audit_fee_baseline(db, account_key, vendor_id)
        stats["fee_options_checked"] = audit["options_checked"]
        stats["fee_anomaly"] = audit["anomaly"]
    except Exception:  # noqa: BLE001 — 감사 실패가 적재분 커밋을 막지 않게
        log.exception("수수료 자기기준선 감사 오류 %s", account_key)
        stats["errors"] += 1

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
