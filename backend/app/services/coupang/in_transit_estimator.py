# in_transit_estimator.py — 발송중(in-transit) 수량 추정 SA (Phase 2 D-13·D-15, X4·X5·X6)
# 단일 책임: coupang_rg_inbound → 옵션별 "아직 판매개시 안 된 발송중 수량 + 판매개시 예정일"을 산출한다.
# 읽기전용, 새 테이블 없음. S5 Harness가 배치 주입(원칙 18-8)해 calc의 유효재고에 반영한다.
#
# 핵심 설계 결정:
#   D-13: 발송중 수량 = Σ(requested_qty - stowed_qty) — 판매개시 안 된 파이프라인 물량.
#   X5 freshness-gate: 마지막 성공 fetch 시각(CoupangWingCookie.last_success_at) 기준.
#         <2일 = fresh(차감 적용) / stale = 차감 스킵(과발송 편향=D-12 품절회피 우선) + 배지.
#   X6 종료의미: receivedQty/stowedQty 반영 + 리드 p90 + EXPIRY_BUFFER_DAYS 초과 미적치 = 만료.
#         → phantom 잔존(만성 과소발송·silent 품절) 방지.
#   판매개시 예정일 = shipment_created_at + mean_lead(발송→판매개시 전체 리드타임).
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import CoupangRgInbound, CoupangWingCookie
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# X6: 리드 p90 + 이 버퍼(일) 초과 미적치 입고 = 만료(분실/취소 간주, phantom 제거).
EXPIRY_BUFFER_DAYS = 7
# X5: 마지막 성공 fetch가 이 일수 이내면 fresh(차감 적용).
FRESHNESS_THRESHOLD_DAYS = 2

RG_ACCOUNTS = ["COUPANG_WING1", "COUPANG_WING2"]


# ════════════════════════════════════════════════
# X5 freshness-gate
# ════════════════════════════════════════════════
def _fetch_freshness(db: Session, account_key: str | None) -> tuple[bool, str | None]:
    """CoupangWingCookie.last_success_at → (fresh, last_fetch_at_iso).

    fresh=True이면 in-transit 차감 적용. False면 차감 스킵(과발송 편향=품절회피 우선, D-12).
    입고 이벤트는 본래 희소(반년 6건)라 inbound 행 나이로 판정하지 않음(X5).
    account_key=None이면 WING1/WING2 중 더 최신 값 사용."""
    keys = [account_key] if account_key else RG_ACCOUNTS
    latest: datetime | None = None
    for key in keys:
        row = (
            db.query(CoupangWingCookie.last_success_at)
            .filter(CoupangWingCookie.account_key == key)
            .scalar()
        )
        if row and (latest is None or row > latest):
            latest = row
    if latest is None:
        return False, None
    now = kst_now()
    age_days = (now - latest).total_seconds() / 86400
    fresh = age_days < FRESHNESS_THRESHOLD_DAYS
    return fresh, latest.isoformat()


# ════════════════════════════════════════════════
# 옵션별 발송중 계산 (X6 만료 적용)
# ════════════════════════════════════════════════
def _in_transit_for_row(row: CoupangRgInbound, now: datetime, lead_p90_days: float) -> int:
    """입고 1행의 "아직 판매개시 안 된 수량" 산출. X6 만료·취소 제외.

    0을 반환하면 이 행은 발송중에서 제외한다.
    - 이미 판매개시(stowing_at is not None): 0.
    - X6 만료: shipment_created_at이 (lead_p90 + EXPIRY_BUFFER)보다 오래됨 → 0.
    - 유효: max(0, requested_qty - stowed_qty). stowed_qty is None → 0으로 취급."""
    # 판매개시 완료
    if row.stowing_at is not None:
        return 0

    # X6 만료 판정: 발송 시점 기준 경과일수
    if row.shipment_created_at is not None:
        age_days = (now - row.shipment_created_at).total_seconds() / 86400
        expiry_days = lead_p90_days + EXPIRY_BUFFER_DAYS
        if age_days > expiry_days:
            log.debug(
                "in_transit 만료 스킵 account=%s inbound_id=%s vii=%s age=%.1f expiry=%.1f",
                row.account_key, row.inbound_id, row.vendor_item_id, age_days, expiry_days,
            )
            return 0

    requested = row.requested_qty or 0
    stowed = row.stowed_qty or 0
    return max(0, requested - stowed)


def _expected_stowing_at(row: CoupangRgInbound, mean_lead_days: float) -> str | None:
    """판매개시 예정일(ISO date str). shipment_created_at + mean_lead_days."""
    if row.shipment_created_at is None:
        return None
    dt = row.shipment_created_at + timedelta(days=mean_lead_days)
    return dt.date().isoformat()


# ════════════════════════════════════════════════
# 공개 API
# ════════════════════════════════════════════════
def estimate_in_transit(
    db: Session,
    account_key: str | None = None,
    *,
    lead_p90_days: float = 3.0,
    lead_mean_days: float = 2.16,
) -> dict:
    """전체 옵션의 발송중 수량·판매개시 예정 추정(배치, Harness 주입용 — 원칙 18-8).

    lead_p90_days / lead_mean_days = lead_time_estimator 글로벌값을 Harness가 주입(1회 산출).
    반환: {fresh, last_fetch_at, total_in_transit_qty, options: {vii: {in_transit_qty, expected_stowing_at}}}."""
    now = kst_now()
    fresh, last_fetch_at = _fetch_freshness(db, account_key)

    # fresh=False: 데이터 stale → 차감 스킵(빈 options 반환). UI 배지용 fresh/last_fetch_at 포함.
    if not fresh:
        return {
            "fresh": False,
            "last_fetch_at": last_fetch_at,
            "total_in_transit_qty": 0,
            "options": {},
        }

    q = db.query(CoupangRgInbound)
    if account_key:
        q = q.filter(CoupangRgInbound.account_key == account_key)
    rows = q.all()

    options: dict[str, dict] = {}
    for row in rows:
        qty = _in_transit_for_row(row, now, lead_p90_days)
        if qty == 0:
            continue
        vii = str(row.vendor_item_id)
        if vii not in options:
            options[vii] = {"in_transit_qty": 0, "expected_stowing_at": None}
        options[vii]["in_transit_qty"] += qty
        # 판매개시 예정: 여러 입고건 중 가장 빠른 것
        est = _expected_stowing_at(row, lead_mean_days)
        prev = options[vii]["expected_stowing_at"]
        if est and (prev is None or est < prev):
            options[vii]["expected_stowing_at"] = est

    total = sum(v["in_transit_qty"] for v in options.values())
    return {
        "fresh": True,
        "last_fetch_at": last_fetch_at,
        "total_in_transit_qty": total,
        "options": options,
    }


def estimate_in_transit_one(
    db: Session,
    vendor_item_id: str,
    account_key: str | None = None,
    *,
    lead_p90_days: float = 3.0,
    lead_mean_days: float = 2.16,
) -> dict | None:
    """단일 옵션 발송중 수량(calc_replenishment 미주입 시 직접 호출 — 원칙 18-8 등가성).

    stale이거나 해당 옵션 in-transit 없으면 None."""
    now = kst_now()
    fresh, last_fetch_at = _fetch_freshness(db, account_key)
    if not fresh:
        return None
    rows = (
        db.query(CoupangRgInbound)
        .filter(CoupangRgInbound.vendor_item_id == str(vendor_item_id))
        .all()
    )
    if account_key:
        rows = [r for r in rows if r.account_key == account_key]
    total = 0
    earliest: str | None = None
    for row in rows:
        qty = _in_transit_for_row(row, now, lead_p90_days)
        if qty == 0:
            continue
        total += qty
        est = _expected_stowing_at(row, lead_mean_days)
        if est and (earliest is None or est < earliest):
            earliest = est
    if total == 0 and earliest is None:
        return None
    return {
        "in_transit_qty": total,
        "expected_stowing_at": earliest,
        "fresh": True,
        "last_fetch_at": last_fetch_at,
    }
