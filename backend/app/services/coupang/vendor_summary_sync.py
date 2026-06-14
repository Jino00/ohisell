# vendor_summary_sync.py — 쿠팡 Wing 판매분석(vendor-summary) 공식 GMV ingest+store SA
# (Wing 세션 자동화 트랙 S2, D-4/D-5).
#
# 소스: m-wing.coupang.com /tenants/rfm-ss/api/business-insight/vendor-summary (ref 18).
# 런타임 경계(D-4): Mac 헤드풀 페처(tools/wing_browser_fetcher.py)가 브라우저측 fetch → prod push.
#   이 SA는 push 수신·스냅샷 저장·조회만(백엔드 requests 직접 호출 금지 — cf_clearance 재생 불가, D-5).
#
# 단일 책임(원칙18-1): vendor-summary 행 적재(snapshot upsert) + 닫힌일 GMV 합계 조회 + heartbeat/refresh.
# 드리프트 산출(우리 매출과 대조)은 revenue_reconcile Harness가 담당(이 SA는 쿠팡 공식값만).
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import CoupangVendorSummaryDaily, CoupangWingCookie
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# Wing 판매분석 페처(별도 데몬 com.ohisell.wing, D-5)의 heartbeat/refresh 상태 행.
# 광고(COUPANG_ADS1)와 분리 — 도메인·세션·방어체계가 달라(D-5) 상태를 섞지 않는다.
_VS_ACCOUNT = "COUPANG_WING_VS"
# 페처가 이 시간(h) 이상 push 안 하면 stale. Mac 야간 off 오탐 방지 위해 26h(광고와 동일).
_STALE_HOURS = 26
# 등록유형(ref 18): NORMAL=3P 마켓플레이스, RFM=로켓그로스(RG).
_TYPE_3P = "NORMAL"
_TYPE_RG = "RFM"


# ════════════════════════════════════════════════
# 상태 행(heartbeat/refresh) — 광고 페처 패턴 복제
# ════════════════════════════════════════════════
def _state_row(db: Session) -> CoupangWingCookie | None:
    return (
        db.query(CoupangWingCookie)
        .filter(CoupangWingCookie.account_key == _VS_ACCOUNT)
        .first()
    )


def _ensure_state_row(db: Session) -> CoupangWingCookie:
    row = _state_row(db)
    if row is None:
        row = CoupangWingCookie(account_key=_VS_ACCOUNT)
        db.add(row)
    return row


def _mark_heartbeat(db: Session) -> None:
    """push 성공 시각 갱신(=배너 staleness 기준). ingest 성공마다 호출."""
    row = _ensure_state_row(db)
    row.status = "green"
    row.last_error = None
    row.last_success_at = kst_now()
    db.commit()


# ════════════════════════════════════════════════
# ingest (SA 본체) — push 수신·스냅샷 저장
# ════════════════════════════════════════════════
def _upsert_row(db: Session, summary_date: date, account_key: str,
                registration_type: str, gmv: int, units_sold: int,
                last_refresh: str | None) -> None:
    row = (
        db.query(CoupangVendorSummaryDaily)
        .filter(
            CoupangVendorSummaryDaily.summary_date == summary_date,
            CoupangVendorSummaryDaily.account_key == account_key,
            CoupangVendorSummaryDaily.registration_type == registration_type,
        )
        .first()
    )
    if row is None:
        row = CoupangVendorSummaryDaily(
            summary_date=summary_date, account_key=account_key,
            registration_type=registration_type,
        )
        db.add(row)
    row.gmv = gmv
    row.units_sold = units_sold
    row.last_refresh = last_refresh
    row.synced_at = kst_now()


def ingest_vendor_summary(db: Session, account_key: str, rows: list[dict]) -> dict:
    """Mac 페처가 보낸 vendor-summary 일별×등록유형 행을 upsert(snapshot 교체).

    rows: [{"date": date, "registration_type": "NORMAL"|"RFM",
            "gmv": int(원), "units_sold": int, "last_refresh"?: str|None}].
    같은 (date, account_key, type)를 다시 받으면 확정치로 교체(준실시간 회전). 성공 시 heartbeat green.
    """
    n = 0
    for r in rows:
        rt = str(r.get("registration_type") or "")
        if rt not in (_TYPE_3P, _TYPE_RG):
            continue  # 알 수 없는 등록유형은 무시(스키마 방어)
        _upsert_row(
            db, r["date"], account_key, rt,
            int(r.get("gmv") or 0), int(r.get("units_sold") or 0),
            r.get("last_refresh"),
        )
        n += 1
    db.commit()
    _mark_heartbeat(db)
    log.info("vendor-summary ingest: account=%s rows=%d", account_key, n)
    return {"account_key": account_key, "ingested": n}


# ════════════════════════════════════════════════
# 조회 헬퍼 — 닫힌일 GMV 합계(3P/RG)
# ════════════════════════════════════════════════
def get_vendor_summary_totals(db: Session, account_key: str | None,
                              start: date, end: date) -> dict:
    """[start, end] 쿠팡 공식 GMV 합계 — 3P(NORMAL)/RG(RFM) 분리.

    account_key=None이면 전체 합산(저장된 모든 계정). 특정 계정이면 해당 계정만.
    반환: {gmv_3p, gmv_rg, gmv_total, days_with_data, last_refresh}.
    days_with_data=데이터가 있는 날짜 수(0이면 대조 불가 — 페처 미적재).
    """
    q = (
        db.query(
            CoupangVendorSummaryDaily.registration_type,
            sqlfunc.coalesce(sqlfunc.sum(CoupangVendorSummaryDaily.gmv), 0),
        )
        .filter(
            CoupangVendorSummaryDaily.summary_date >= start,
            CoupangVendorSummaryDaily.summary_date <= end,
        )
    )
    if account_key is not None:
        q = q.filter(CoupangVendorSummaryDaily.account_key == account_key)
    by_type = {rt: int(total or 0) for rt, total in q.group_by(
        CoupangVendorSummaryDaily.registration_type).all()}

    # 데이터가 있는 (날짜) 수와 최신 lastRefresh — 대조 가능 여부/신선도 표시용.
    meta_q = db.query(
        sqlfunc.count(sqlfunc.distinct(CoupangVendorSummaryDaily.summary_date)),
        sqlfunc.max(CoupangVendorSummaryDaily.last_refresh),
    ).filter(
        CoupangVendorSummaryDaily.summary_date >= start,
        CoupangVendorSummaryDaily.summary_date <= end,
    )
    if account_key is not None:
        meta_q = meta_q.filter(CoupangVendorSummaryDaily.account_key == account_key)
    days_with_data, last_refresh = meta_q.one()

    gmv_3p = by_type.get(_TYPE_3P, 0)
    gmv_rg = by_type.get(_TYPE_RG, 0)
    return {
        "gmv_3p": gmv_3p,
        "gmv_rg": gmv_rg,
        "gmv_total": gmv_3p + gmv_rg,
        "days_with_data": int(days_with_data or 0),
        "last_refresh": last_refresh,
    }


# ════════════════════════════════════════════════
# heartbeat·갱신 트리거 (광고 페처 패턴 복제 — S3 UI 버튼·페처 데몬 공용)
# ════════════════════════════════════════════════
def vs_status(db: Session) -> dict:
    """페처 push 상태 요약(민감값 없음). last_success_at=마지막 push, stale=push 끊김."""
    row = _state_row(db)
    if row is None:
        return {"account": _VS_ACCOUNT, "status": "none", "last_success_at": None,
                "age_hours": None, "stale": False}
    age_hours = None
    stale = False
    if row.last_success_at:
        age_hours = max(0.0, round((kst_now() - row.last_success_at).total_seconds() / 3600, 1))
        stale = age_hours > _STALE_HOURS
    return {
        "account": _VS_ACCOUNT,
        "status": row.status,
        "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
        "last_error": row.last_error,
        "age_hours": age_hours,
        "stale": stale,
    }


def request_refresh(db: Session) -> dict:
    """UI '판매분석 갱신' 버튼 → 갱신 요청 플래그 set. Wing 페처 데몬이 다음 폴링에서 소비."""
    row = _ensure_state_row(db)
    row.refresh_requested_at = kst_now()
    db.commit()
    return {"requested": True, "requested_at": row.refresh_requested_at.isoformat()}


def refresh_status(db: Session) -> dict:
    """갱신 요청/완료 상태. UI(버튼 후 폴링)·Wing 페처(요청 확인) 공용."""
    row = _state_row(db)
    if row is None:
        return {"requested": False, "requested_at": None, "last_success_at": None,
                "status": "none", "last_error": None}
    return {
        "requested": row.refresh_requested_at is not None,
        "requested_at": row.refresh_requested_at.isoformat() if row.refresh_requested_at else None,
        "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
        "status": row.status,
        "last_error": row.last_error,
    }


def claim_refresh(db: Session) -> dict:
    """Wing 페처가 갱신 요청을 '소비'(플래그 clear). 원자적 조건부 UPDATE(광고 패턴, codex P2)."""
    from sqlalchemy import update

    res = db.execute(
        update(CoupangWingCookie)
        .where(CoupangWingCookie.account_key == _VS_ACCOUNT)
        .where(CoupangWingCookie.refresh_requested_at.isnot(None))
        .values(refresh_requested_at=None)
    )
    db.commit()
    return {"claimed": (res.rowcount or 0) > 0}
