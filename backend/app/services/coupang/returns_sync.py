# returns_sync.py — 반품/취소/교환 동기화 Harness (순매출 차감 회계축 적재)
# 흐름: 계정별 → [RETURN·CANCEL 목록(SA) → coupang_return_item upsert] →
#       [반품철회 이력(SA) → withdrawn 마킹] → [교환 목록(SA) → coupang_exchange upsert].
# 트랙 D-8: vendor 2계정(WING1·WING2) 순회(RG 중복 불필요). 호출은 서버 IP에서만(로컬 403).
# 트랙 D-3: 사실/지표 정리만 — 전략판단 없음.
from __future__ import annotations

from app.utils.kst import kst_now, kst_today
import logging
import time
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.clients.coupang import CoupangExchangeClient, CoupangReturnClient
from app.clients.coupang._base import CoupangReadError
from app.config import get_coupang_config
from app.models import CoupangExchange, CoupangReturnItem

log = logging.getLogger(__name__)

RETURN_ACCOUNTS = ["COUPANG_WING1", "COUPANG_WING2"]
# 라이브 실측(2026-06-03, 서버 프로브)으로 확정한 조회기간 제약:
_RETURN_SPAN_DAYS = 31   # 반품/취소 목록(returnRequests): 최대 31일
_SHORT_SPAN_DAYS = 7     # 반품철회·교환: "less then 7day" → 7일 윈도우(6일 차이)
# 반품(cancelType=RETURN)은 status 파라미터 필수("OrderId can't be null if doesn't pass status").
# 취소(CANCEL)는 status 미사용. 전 상태를 덮기 위해 status별 순회.
RETURN_STATUSES = ["RU", "UC", "CC", "PR"]  # 출고중지요청·반품접수·반품완료·쿠팡확인요청
_CALL_DELAY = 0.3  # 쿠팡 속도제한(429) 대응 — 호출 간 간격
# datetime.now()(UTC)는 KST 기준 하루 전 날짜라 조회범위가 하루 stale. 경계는 KST로 명시
# (settlement_sync._kst_today와 동일 패턴).


def kst_today():
    """KST 기준 오늘 date. 조회 윈도우 경계 계산용(서버 UTC ↔ KST 날짜 경계 어긋남 방지)."""
    return datetime.now(_KST).date()


def _date_windows(days: int, max_span: int) -> list[tuple[str, str]]:
    """오늘(KST) 기준 과거 days일을 ≤max_span일 윈도우들로 분할. 각 ("yyyy-MM-dd","yyyy-MM-dd")."""
    today = kst_today()
    start = today - timedelta(days=max(days, 1))
    windows: list[tuple[str, str]] = []
    cursor = start
    while cursor <= today:
        chunk_end = min(cursor + timedelta(days=max_span - 1), today)
        windows.append((cursor.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")))
        cursor = chunk_end + timedelta(days=1)
    return windows


def _parse_dt(value) -> datetime | None:
    """쿠팡 createdAt(ISO-8601 또는 yyyy-MM-ddThh:mm:ss)을 naive datetime으로. 실패 시 None."""
    if not value:
        return None
    s = str(value)
    try:
        dt = datetime.fromisoformat(s)
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def _upsert_return_item(
    db: Session, account_key: str, vendor_id: str, receipt: dict, item: dict,
    seen: dict | None = None,
) -> bool:
    """반품 옵션 1건을 coupang_return_item에 upsert ((receipt_id, vendor_item_id) 기준).

    seen: per-run 캐시. 같은 (receipt_id, vendor_item_id)가 여러 status 쿼리·페이지에 중복
    등장해도 동일 pending 객체를 재사용해 UNIQUE 충돌을 막는다(autoflush 타이밍 비의존).
    """
    receipt_id = str(receipt.get("receiptId") or "")
    vii = str(item.get("vendorItemId") or "")
    if not receipt_id or not vii:
        return False
    key = (receipt_id, vii)
    row = seen.get(key) if seen is not None else None
    if row is None:
        row = (
            db.query(CoupangReturnItem)
            .filter(
                CoupangReturnItem.receipt_id == receipt_id,
                CoupangReturnItem.vendor_item_id == vii,
            )
            .first()
        )
    if row is None:
        row = CoupangReturnItem(receipt_id=receipt_id, vendor_item_id=vii)
        db.add(row)
    elif row.vendor_id and row.vendor_id != vendor_id:
        # codex [P2]: vendorItemId는 D-8에서 전역유일·단일계정 소유로 라이브 검증됨.
        # 기존 행의 계정이 바뀌면 = 전역유일 가정 위반 신호 → 경고(침묵 손상 방지, CoupangProductItem 패턴).
        log.warning(
            "반품 receiptId %s vendorItemId %s account 변경 감지 %s→%s (D-8 전역유일 위반 가능)",
            receipt_id, vii, row.vendor_id, vendor_id,
        )
    row.account_key = account_key
    row.vendor_id = vendor_id
    row.order_id = str(receipt.get("orderId") or "")
    row.receipt_type = receipt.get("receiptType") or "RETURN"
    row.receipt_status = receipt.get("receiptStatus")
    row.cancel_count = int(item.get("cancelCount") or 0)
    row.purchase_count = item.get("purchaseCount")
    row.seller_product_id = (
        str(item.get("sellerProductId")) if item.get("sellerProductId") else None
    )
    row.seller_product_name = (item.get("sellerProductName") or "")[:300] or None
    row.vendor_item_name = (item.get("vendorItemName") or "")[:300] or None
    row.fault_by_type = receipt.get("faultByType")
    # 사유: 카테고리 + 상세 (사실 기록만)
    cat = receipt.get("cancelReasonCategory1") or ""
    detail = receipt.get("cancelReason") or ""
    row.cancel_reason = (f"{cat} {detail}".strip() or None)
    row.release_status = item.get("releaseStatus")
    row.pre_refund = receipt.get("preRefund")
    row.requested_at = _parse_dt(receipt.get("createdAt"))
    row.modified_at = _parse_dt(receipt.get("modifiedAt"))
    if seen is not None:
        seen[key] = row
    return True


def _mark_withdrawn(db: Session, account_key: str, vendor_id: str, withdraw: dict) -> int:
    """반품철회 1건을 받아 해당 (receipt_id, vendor_item_id) 행의 withdrawn=True. 갱신 행수 반환."""
    cancel_id = str(withdraw.get("cancelId") or "")
    if not cancel_id:
        return 0
    vii_list = [str(v) for v in (withdraw.get("vendorItemIds") or [])]
    # codex [P2]: 철회 매칭을 현재 동기화 중인 계정(vendor_id)으로 스코프.
    # vendorItemIds가 비어도 타계정 행을 마킹하지 않도록(receiptId 계정 간 충돌 방어).
    q = db.query(CoupangReturnItem).filter(
        CoupangReturnItem.receipt_id == cancel_id,
        CoupangReturnItem.vendor_id == vendor_id,
    )
    if vii_list:
        q = q.filter(CoupangReturnItem.vendor_item_id.in_(vii_list))
    count = 0
    for row in q.all():
        if not row.withdrawn:
            count += 1
        row.withdrawn = True
    return count


def _upsert_exchange(
    db: Session, account_key: str, vendor_id: str, exchange: dict,
    seen: dict | None = None,
) -> int:
    """교환 1건의 아이템들을 coupang_exchange에 upsert. 적재된 옵션 행수 반환.

    exchangeItems[] 내부 구조가 명세상 미확정 → 방어적 추출(여러 후보 키 시도).
    vendorItemId를 못 찾으면 스킵+로그(추정 금지). 라이브에서 실제 구조 확인 후 보정.
    seen: per-run 캐시 — (exchange_id, vendor_item_id) 중복 등장 시 UNIQUE 충돌 방지.
    """
    exchange_id = str(exchange.get("exchangeId") or "")
    order_id = str(exchange.get("orderId") or "")
    if not exchange_id:
        return 0
    items = (
        exchange.get("exchangeItems")
        or exchange.get("items")
        or exchange.get("orderItems")
        or []
    )
    n = 0
    for item in items:
        vii = str(item.get("vendorItemId") or "")
        if not vii:
            log.info("교환 %s 아이템에 vendorItemId 없음 → 스킵(라이브 구조 확인 필요)", exchange_id)
            continue
        key = (exchange_id, vii)
        row = seen.get(key) if seen is not None else None
        if row is None:
            row = (
                db.query(CoupangExchange)
                .filter(
                    CoupangExchange.exchange_id == exchange_id,
                    CoupangExchange.vendor_item_id == vii,
                )
                .first()
            )
        if row is None:
            row = CoupangExchange(exchange_id=exchange_id, vendor_item_id=vii)
            db.add(row)
        row.account_key = account_key
        row.vendor_id = vendor_id
        row.order_id = order_id
        row.exchange_status = exchange.get("exchangeStatus")
        row.order_delivery_status = exchange.get("orderDeliveryStatusCode")
        row.refer_type = exchange.get("referType")
        row.vendor_item_name = (item.get("vendorItemName") or "")[:300] or None
        row.exchange_count = item.get("quantity") or item.get("cancelCount")
        row.requested_at = _parse_dt(exchange.get("createdAt"))
        if seen is not None:
            seen[key] = row
        n += 1
    return n


def sync_account_returns(db: Session, account_key: str, *, days: int = 35) -> dict:
    """한 계정의 반품/취소/교환을 과거 days일 동기화. 반환: 통계 dict.

    하드에러(config 누락)는 결과 dict의 error로 표면화 → 소비자가 raise 판단.
    """
    cfg = get_coupang_config(account_key)
    if cfg is None:
        return {"account": account_key, "error": "config_missing"}
    ret_client = CoupangReturnClient(cfg)
    exc_client = CoupangExchangeClient(cfg)
    vendor_id = cfg.vendor_id
    stats = {
        "account": account_key, "vendor_id": vendor_id,
        "returns": 0, "cancels": 0, "items": 0,
        "withdrawn": 0, "exchanges": 0, "errors": 0, "api_failures": 0,
    }
    # per-run 캐시: 같은 키가 여러 status 쿼리·페이지에 중복 등장해도 UNIQUE 충돌 방지.
    seen_ret: dict = {}
    seen_exc: dict = {}

    # ① 반품/취소 목록 (31일 윈도우). RETURN은 status별 순회(필수), CANCEL은 status 없이 1회.
    for win_from, win_to in _date_windows(days, _RETURN_SPAN_DAYS):
        for status in RETURN_STATUSES:
            try:
                for receipt in ret_client.iter_return_requests(
                    created_at_from=win_from, created_at_to=win_to,
                    cancel_type="RETURN", status=status,
                ):
                    stats["returns"] += 1
                    for item in receipt.get("returnItems", []) or []:
                        if _upsert_return_item(db, account_key, vendor_id, receipt, item, seen_ret):
                            stats["items"] += 1
            except CoupangReadError:  # codex [P1]: API 하드실패는 stale 위장 금지 → 집계
                log.exception("반품 목록 읽기 실패 %s %s~%s RETURN/%s",
                              account_key, win_from, win_to, status)
                stats["api_failures"] += 1
            except Exception:  # noqa: BLE001 — 한 호출 코드오류가 전체를 막지 않게
                log.exception("반품 목록 오류 %s %s~%s RETURN/%s",
                              account_key, win_from, win_to, status)
                stats["errors"] += 1
            time.sleep(_CALL_DELAY)

        # 취소(CANCEL): status·orderId 제외(쿠팡 제약), 결제완료 단계 취소 포함.
        try:
            for receipt in ret_client.iter_return_requests(
                created_at_from=win_from, created_at_to=win_to, cancel_type="CANCEL"
            ):
                stats["cancels"] += 1
                for item in receipt.get("returnItems", []) or []:
                    if _upsert_return_item(db, account_key, vendor_id, receipt, item, seen_ret):
                        stats["items"] += 1
        except CoupangReadError:
            log.exception("취소 목록 읽기 실패 %s %s~%s", account_key, win_from, win_to)
            stats["api_failures"] += 1
        except Exception:  # noqa: BLE001
            log.exception("취소 목록 오류 %s %s~%s", account_key, win_from, win_to)
            stats["errors"] += 1
        time.sleep(_CALL_DELAY)

    # ② 반품철회 + 교환 (7일 윈도우 — 라이브 실측 'less then 7day').
    for win_from, win_to in _date_windows(days, _SHORT_SPAN_DAYS):
        # 반품철회 → withdrawn 마킹 (차감 제외)
        try:
            for withdraw in ret_client.iter_return_withdraw(
                date_from=win_from, date_to=win_to
            ):
                stats["withdrawn"] += _mark_withdrawn(db, account_key, vendor_id, withdraw)
        except CoupangReadError:
            log.exception("반품철회 읽기 실패 %s %s~%s", account_key, win_from, win_to)
            stats["api_failures"] += 1
        except Exception:  # noqa: BLE001
            log.exception("반품철회 오류 %s %s~%s", account_key, win_from, win_to)
            stats["errors"] += 1
        time.sleep(_CALL_DELAY)

        # 교환 (운영 기록 — 회계 차감 없음). createdAt 형식은 분초까지.
        try:
            ex_from = f"{win_from}T00:00:00"
            ex_to = f"{win_to}T23:59:59"
            for exchange in exc_client.iter_exchange_requests(
                created_at_from=ex_from, created_at_to=ex_to
            ):
                stats["exchanges"] += _upsert_exchange(db, account_key, vendor_id, exchange, seen_exc)
        except CoupangReadError:
            log.exception("교환 읽기 실패 %s %s~%s", account_key, win_from, win_to)
            stats["api_failures"] += 1
        except Exception:  # noqa: BLE001
            log.exception("교환 오류 %s %s~%s", account_key, win_from, win_to)
            stats["errors"] += 1
        time.sleep(_CALL_DELAY)

    # 정상 동기화분은 먼저 커밋(좋은 데이터 보존), 그 후 하드 실패를 표면화.
    db.commit()
    # codex [P1]: 읽기 하드실패가 있으면 error로 표면화 → 스케줄러가 raise(거짓 성공 방지, 원칙22).
    # (부분 코드오류 stats["errors"]는 예상 가능한 부분 실패라 raise 대상 아님)
    if stats["api_failures"]:
        stats["error"] = (
            f"쿠팡 읽기 실패 {stats['api_failures']}건 "
            "(IP화이트리스트/인증/네트워크 가능 — 데이터 stale 위험)"
        )
    log.info("쿠팡 반품/교환 동기화 완료 %s: %s", account_key, stats)
    return stats


def sync_all_returns(db: Session, *, days: int = 35) -> list[dict]:
    """2개 셀러계정(WING1·WING2) 반품/취소/교환 전체 동기화."""
    return [sync_account_returns(db, key, days=days) for key in RETURN_ACCOUNTS]
