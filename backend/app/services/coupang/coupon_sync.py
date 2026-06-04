# coupon_sync.py — 쿠팡 쿠폰/예산 동기화 Harness (P5 쿠폰 운영 현황 — 보조축).
# 흐름: 계정별 → [즉시할인쿠폰 상태별 목록 → coupang_coupon upsert → 각 쿠폰 아이템 목록 →
#       coupang_coupon_item upsert(옵션 결합 D-8)] → [계약서 목록 → 월별 예산현황 → coupang_coupon_budget].
# ★회계축 아님(D-3): 셀러 부담 할인액은 정산(P4) revenue-history에 이미 실측 차감. 이건 운영 현황만.
# ⚠️ 다운로드쿠폰은 목록 API 없어 자동 sync 불가(명세 §E) → 즉시할인쿠폰+예산/계약 중심.
# 트랙 D-8: vendor 2계정(WING1·WING2) 순회. 호출은 서버 IP에서만(로컬 403).
from __future__ import annotations

from app.utils.kst import kst_now, kst_today
import logging
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.clients.coupang import CoupangCouponClient
from app.clients.coupang._base import CoupangReadError
from app.clients.coupang.coupons import _fms_ok
from app.config import get_coupang_config
from app.models import CoupangCoupon, CoupangCouponBudget, CoupangCouponItem

log = logging.getLogger(__name__)

COUPON_ACCOUNTS = ["COUPANG_WING1", "COUPANG_WING2"]
# 즉시할인쿠폰 상태 — 운영 현황 전수. DETACHED 포함(codex[P2]: APPLIED→DETACHED 전환 시
# 옛 status 잔존 방지 — 전 상태 스윕으로 현재 상태 정확 반영).
_INSTANT_STATUSES = ["STANDBY", "APPLIED", "PAUSED", "EXPIRED", "DETACHED"]
# 아이템 조회 상태 — 전 상태 스윕. EXPIRED 포함(codex[P1]: APPLIED 아이템이 API에서 EXPIRED로
# 바뀌어도 DB에 APPLIED로 잔존 = 옵션이 할인중인 것처럼 거짓 표시 → couponItemId upsert로 갱신).
_ITEM_STATUSES = ["STANDBY", "APPLIED", "PAUSED", "EXPIRED"]
_CALL_DELAY = 0.3  # 쿠팡 속도제한(429) 대응




def _dec(v) -> Decimal | None:
    """숫자/문자 → Decimal. None/파싱불가는 None('없음'과 '0' 구분)."""
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        log.warning("쿠폰 금액/율 파싱 실패 → None: %r", v)
        return None


def _parse_dt(s):
    """쿠팡 일시("2018-04-05 23:59:00" 또는 "yyyy-MM-dd") → datetime. 실패 None."""
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19] if len(s) >= 19 else s, fmt)
        except ValueError:
            continue
    log.warning("쿠폰 일시 파싱 실패 → None: %r", s)
    return None


def _as_bool(v) -> bool | None:
    """'false'/'true'/bool → bool. None은 None."""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() == "true"


def _target_months(n: int) -> list[str]:
    """오늘(KST) 기준 과거 n개월의 'yyyy-MM'(이번 달 포함)."""
    today = kst_today()
    y, m = today.year, today.month
    out: list[str] = []
    for _ in range(max(n, 1)):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return out


def _upsert_coupon(
    db: Session, account_key: str, vendor_id: str, kind: str, c: dict,
    seen: dict | None = None,
) -> bool:
    """즉시할인쿠폰/다운로드쿠폰 1건을 coupang_coupon에 upsert. kind=INSTANT/DOWNLOAD."""
    coupon_id = str(c.get("couponId") or "")
    if not coupon_id:
        return False
    key = (kind, coupon_id)
    row = seen.get(key) if seen is not None else None
    if row is None:
        row = (
            db.query(CoupangCoupon)
            .filter(
                CoupangCoupon.account_key == account_key,
                CoupangCoupon.coupon_kind == kind,
                CoupangCoupon.coupon_id == coupon_id,
            )
            .first()
        )
    if row is None:
        row = CoupangCoupon(
            account_key=account_key, vendor_id=vendor_id,
            coupon_kind=kind, coupon_id=coupon_id,
        )
        db.add(row)
    row.account_key = account_key
    row.vendor_id = vendor_id
    if kind == "INSTANT":
        row.contract_id = str(c.get("contractId")) if c.get("contractId") is not None else None
        row.promotion_name = (c.get("promotionName") or "")[:300] or None
        row.status = c.get("status")
        row.discount_type = c.get("type")
        row.discount = _dec(c.get("discount"))
        row.max_discount_price = _dec(c.get("maxDiscountPrice"))
        row.start_at = _parse_dt(c.get("startAt"))
        row.end_at = _parse_dt(c.get("endAt"))
        row.wow_exclusive = _as_bool(c.get("wowExclusive"))
    else:  # DOWNLOAD
        row.promotion_name = (c.get("title") or "")[:300] or None
        row.status = c.get("couponStatus")
        row.start_at = _parse_dt(c.get("startDate"))
        row.end_at = _parse_dt(c.get("endDate"))
        row.applied_option_count = int(c.get("appliedOptionCount") or 0) or None
        row.usage_amount = _dec(c.get("usageAmount"))
        # 정책 첫 항목에서 할인 메타(다운로드쿠폰은 couponPolicies[])
        policies = c.get("couponPolicies") or []
        if policies:
            p0 = policies[0]
            row.discount_type = p0.get("typeOfDiscount")
            row.discount = _dec(p0.get("discount"))
            row.max_discount_price = _dec(p0.get("maximumDiscountPrice"))
    if seen is not None:
        seen[key] = row
    return True


def _upsert_coupon_item(
    db: Session, account_key: str, vendor_id: str, coupon_id: str, item: dict,
    seen: dict | None = None,
) -> bool:
    """쿠폰 아이템(옵션별 적용) 1건을 coupang_coupon_item에 upsert. grain=(account_key, couponItemId)."""
    coupon_item_id = str(item.get("couponItemId") or "")
    vii = str(item.get("vendorItemId") or "")
    if not coupon_item_id or not vii:
        return False
    row = seen.get(coupon_item_id) if seen is not None else None
    if row is None:
        row = (
            db.query(CoupangCouponItem)
            .filter(
                CoupangCouponItem.account_key == account_key,
                CoupangCouponItem.coupon_item_id == coupon_item_id,
            )
            .first()
        )
    if row is None:
        row = CoupangCouponItem(
            account_key=account_key, vendor_id=vendor_id,
            coupon_item_id=coupon_item_id, coupon_id=str(coupon_id),
            vendor_item_id=vii,
        )
        db.add(row)
    row.account_key = account_key
    row.vendor_id = vendor_id
    row.coupon_id = str(item.get("couponId") or coupon_id)
    row.vendor_item_id = vii
    row.status = item.get("status")
    row.start_at = _parse_dt(item.get("startAt"))
    row.end_at = _parse_dt(item.get("endAt"))
    if seen is not None:
        seen[coupon_item_id] = row
    return True


def _upsert_budget(
    db: Session, account_key: str, vendor_id: str, contract_id: str, target_month: str,
    *, budget: dict | None = None, contract: dict | None = None, seen: dict | None = None,
) -> bool:
    """예산/계약 1건을 coupang_coupon_budget에 upsert. grain=(account_key, contract_id, target_month).

    budget(#4)·contract(#6)를 합쳐 저장. target_month=''이면 계약 메타만.
    """
    key = (contract_id, target_month)
    row = seen.get(key) if seen is not None else None
    if row is None:
        row = (
            db.query(CoupangCouponBudget)
            .filter(
                CoupangCouponBudget.account_key == account_key,
                CoupangCouponBudget.contract_id == contract_id,
                CoupangCouponBudget.target_month == target_month,
            )
            .first()
        )
    if row is None:
        row = CoupangCouponBudget(
            account_key=account_key, vendor_id=vendor_id,
            contract_id=contract_id, target_month=target_month,
        )
        db.add(row)
    row.account_key = account_key
    row.vendor_id = vendor_id
    if budget is not None:
        row.vendor_share_ratio = _dec(budget.get("vendorShareRatio"))
        row.total_budget_amount = _dec(budget.get("totalBudgetAmount"))
        row.used_budget_amount = _dec(budget.get("usedBudgetAmount"))
    if contract is not None:
        row.vendor_contract_id = (
            str(contract.get("vendorContractId"))
            if contract.get("vendorContractId") is not None else None
        )
        row.seller_share_ratio = _dec(contract.get("sellerShareRatio"))
        row.coupang_share_ratio = _dec(contract.get("coupangShareRatio"))
        row.contract_type = contract.get("type")
        row.contract_start = _parse_dt(contract.get("start"))
        row.contract_end = _parse_dt(contract.get("end"))
    if seen is not None:
        seen[key] = row
    return True


def sync_account_coupons(
    db: Session, account_key: str, *, budget_months: int = 3
) -> dict:
    """한 계정의 즉시할인쿠폰(+아이템)·예산/계약을 동기화. 반환: 통계 dict.

    하드 실패(config 누락·읽기 실패)는 결과 dict의 error/api_failures로 표면화(원칙22).
    """
    cfg = get_coupang_config(account_key)
    if cfg is None:
        return {"account": account_key, "error": "config_missing"}
    client = CoupangCouponClient(cfg)
    vendor_id = cfg.vendor_id
    stats = {
        "account": account_key, "vendor_id": vendor_id,
        "coupons": 0, "coupon_items": 0, "budgets": 0,
        "errors": 0, "api_failures": 0,
    }
    seen_c: dict = {}
    seen_i: dict = {}
    seen_b: dict = {}

    # ① 즉시할인쿠폰 상태별 목록 → 적재, 각 쿠폰의 아이템(옵션 결합) 적재
    coupon_ids: list[str] = []
    for status in _INSTANT_STATUSES:
        try:
            for coupon in client.iter_instant_coupons(status=status):
                if _upsert_coupon(db, account_key, vendor_id, "INSTANT", coupon, seen_c):
                    stats["coupons"] += 1
                    cid = str(coupon.get("couponId") or "")
                    if cid and cid not in coupon_ids:
                        coupon_ids.append(cid)
        except CoupangReadError:
            log.exception("즉시할인쿠폰 목록 읽기 실패 %s status=%s", account_key, status)
            stats["api_failures"] += 1
        except Exception:  # noqa: BLE001 — 한 호출 오류가 전체를 막지 않게
            log.exception("즉시할인쿠폰 목록 오류 %s status=%s", account_key, status)
            stats["errors"] += 1
        time.sleep(_CALL_DELAY)

    # ② 각 쿠폰의 아이템 목록(옵션 결합 D-8). 활성 상태 위주.
    for cid in coupon_ids:
        for status in _ITEM_STATUSES:
            try:
                for item in client.iter_instant_coupon_items(coupon_id=cid, status=status):
                    if _upsert_coupon_item(db, account_key, vendor_id, cid, item, seen_i):
                        stats["coupon_items"] += 1
            except CoupangReadError:
                log.exception("쿠폰아이템 읽기 실패 %s coupon=%s status=%s", account_key, cid, status)
                stats["api_failures"] += 1
            except Exception:  # noqa: BLE001
                log.exception("쿠폰아이템 오류 %s coupon=%s status=%s", account_key, cid, status)
                stats["errors"] += 1
            time.sleep(_CALL_DELAY)

    # ③ 계약서 목록 → 계약 메타 적재 + 각 계약의 월별 예산현황 적재
    try:
        resp = client.get_contract_list()
        # codex[P1]: None뿐 아니라 FMS code/success 검증 — code=500·success=false를 빈 리스트로
        #   처리하면 stale을 fresh로 위장(원칙22). iter 읽기와 동일 기준(_fms_ok).
        if not _fms_ok(resp):
            log.error("계약서 목록 읽기 실패 %s resp_code=%s",
                      account_key, (resp or {}).get("code"))
            stats["api_failures"] += 1
        else:
            contracts = ((resp.get("data") or {}).get("content")) or []
            for contract in contracts:
                cid = str(contract.get("contractId")) if contract.get("contractId") is not None else ""
                if not cid:
                    continue
                # 계약 메타(target_month='')
                if _upsert_budget(
                    db, account_key, vendor_id, cid, "", contract=contract, seen=seen_b
                ):
                    stats["budgets"] += 1
                # 월별 예산현황(#4)
                for ym in _target_months(budget_months):
                    try:
                        bresp = client.get_budgets(contract_id=cid, target_month=ym)
                        if not _fms_ok(bresp):  # codex[P1]: code/success 검증(stale 위장 방지)
                            log.error("예산현황 읽기 실패 %s contract=%s ym=%s code=%s",
                                      account_key, cid, ym, (bresp or {}).get("code"))
                            stats["api_failures"] += 1
                            continue
                        bcontent = ((bresp.get("data") or {}).get("content")) or []
                        for b in bcontent:
                            tm = b.get("targetMonth") or ym
                            if _upsert_budget(
                                db, account_key, vendor_id, cid, tm, budget=b, seen=seen_b
                            ):
                                stats["budgets"] += 1
                    except Exception:  # noqa: BLE001
                        log.exception("예산현황 오류 %s contract=%s ym=%s", account_key, cid, ym)
                        stats["errors"] += 1
                    time.sleep(_CALL_DELAY)
    except Exception:  # noqa: BLE001
        log.exception("계약서/예산 동기화 오류 %s", account_key)
        stats["errors"] += 1

    # 정상 동기화분 먼저 커밋, 그 후 하드 실패 표면화(원칙22).
    db.commit()
    if stats["api_failures"]:
        stats["error"] = (
            f"쿠팡 쿠폰 읽기 실패 {stats['api_failures']}건 "
            "(IP화이트리스트/인증/네트워크 가능 — 데이터 stale 위험)"
        )
    log.info("쿠팡 쿠폰 동기화 완료 %s: %s", account_key, stats)
    return stats


def sync_all_coupons(db: Session, *, budget_months: int = 3) -> list[dict]:
    """2개 셀러계정(WING1·WING2) 쿠폰 운영 현황(즉시할인쿠폰+아이템+예산/계약) 전체 동기화."""
    return [
        sync_account_coupons(db, key, budget_months=budget_months)
        for key in COUPON_ACCOUNTS
    ]
