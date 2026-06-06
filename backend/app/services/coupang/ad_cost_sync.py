# ad_cost_sync.py — 쿠팡 광고비 일별 동기화 SA
# advertising.coupang.com /marketing/cmg-api/report/cost (Wing 내부 API, S0 실증 2026-06-05)
# 흐름: 쿠키 로드·복호화 → POST report/cost → coupang_ad_cost_daily upsert.
# 쿠키: CoupangWingCookie(account_key="COUPANG_ADS1") — Fernet 암호화 저장(원칙18 D-5).
# fail-soft: 302/401(만료)=status red + 예외 흡수(이전 이력 유지).
from __future__ import annotations

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests
from sqlalchemy.orm import Session

from app.models import CoupangAdCostDaily, CoupangWingCookie
from app.utils.crypto import CookieCryptoError, decrypt_secret, encrypt_secret
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")
_ADS_ACCOUNT = "COUPANG_ADS1"
# 로컬 페처가 이 시간(h) 이상 push 안 하면 stale → 전역 배너 경고.
# Mac 야간 off(최대 ~12h) 오탐 방지 위해 26h(하루 초과)로 설정.
_STALE_HOURS = 26
_ADS_URL = "https://advertising.coupang.com/marketing/cmg-api/report/cost"
# S0 실증 vendor IDs — 응답 키로 자동 수집하므로 하드코딩 불필요.
# 단, Payload에 넣을 parentNodes는 저장된 vendor_ids 사용.
_DEFAULT_VENDOR_IDS = ["104438581", "104997005"]


# ════════════════════════════════════════════════
# 쿠키 CRUD
# ════════════════════════════════════════════════
def _cookie_row(db: Session) -> CoupangWingCookie | None:
    return (
        db.query(CoupangWingCookie)
        .filter(CoupangWingCookie.account_key == _ADS_ACCOUNT)
        .first()
    )


def save_cookie(db: Session, curl_or_cookie: str) -> dict:
    """cURL(또는 쿠키 문자열) → 쿠키 추출·Fernet 암호화 저장.

    advertising.coupang.com은 x-xsrf-token 불필요 — 쿠키만 추출.
    xsrf 필드는 공백으로 저장(CoupangWingCookie 스키마 재사용).
    """
    from app.clients.coupang.inbound import parse_curl_cookies

    cookie, _xsrf = parse_curl_cookies(curl_or_cookie, require_xsrf=False)
    if not cookie:
        raise ValueError("쿠키를 추출할 수 없습니다. cURL 전체를 붙여넣으세요.")
    enc_cookie = encrypt_secret(cookie)
    row = _cookie_row(db)
    if row is None:
        row = CoupangWingCookie(account_key=_ADS_ACCOUNT)
        db.add(row)
    row.cookie_blob = enc_cookie
    row.xsrf_token = encrypt_secret("") if _xsrf == "" else encrypt_secret(_xsrf)
    row.last_saved_at = kst_now()
    row.status = "unknown"
    row.last_error = None
    db.commit()
    return {"account": _ADS_ACCOUNT, "saved": True, "cookie_len": len(cookie)}


def cookie_status(db: Session) -> dict:
    """쿠키 설정·상태 요약 (민감값 미포함).

    로컬 페처 아키텍처(Akamai로 prod 직접 fetch 불가): 광고비는 Jino Mac 로컬 페처가
    ingest로 push. last_success_at = 마지막 push 시각. stale=push가 _STALE_HOURS 초과로
    끊김(페처 다운) → 전역 배너. age_hours/stale은 배너 트리거에 사용.
    """
    row = _cookie_row(db)
    if row is None:
        return {"account": _ADS_ACCOUNT, "configured": False, "status": "none",
                "last_saved_at": None, "last_success_at": None, "last_error": None,
                "age_hours": None, "stale": False}
    age_hours = None
    stale = False
    if row.last_success_at:
        # kst_now()·last_success_at 모두 naive KST(utils/kst.py). 시계 스큐 음수만 0으로 클램프.
        age_hours = max(0.0, round((kst_now() - row.last_success_at).total_seconds() / 3600, 1))
        stale = age_hours > _STALE_HOURS
    return {
        "account": _ADS_ACCOUNT,
        "configured": bool(row.cookie_blob),
        "status": row.status,
        "last_saved_at": row.last_saved_at.isoformat() if row.last_saved_at else None,
        "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
        "last_error": row.last_error,
        "age_hours": age_hours,
        "stale": stale,
    }


def _mark_cookie(db: Session, *, status: str | None, error: str | None,
                 success: bool = False) -> None:
    row = _cookie_row(db)
    if row is None:
        return
    if status is not None:
        row.status = status
    if error is not None:
        row.last_error = error[:300]
    if success:
        row.last_success_at = kst_now()
        row.last_error = None
    db.commit()


# ════════════════════════════════════════════════
# 동기화 (SA 본체)
# ════════════════════════════════════════════════
def _upsert_cost(db: Session, cost_date: date, vendor_id: str,
                 day_cost: int, month_cost: int) -> None:
    row = (
        db.query(CoupangAdCostDaily)
        .filter(
            CoupangAdCostDaily.cost_date == cost_date,
            CoupangAdCostDaily.vendor_id == vendor_id,
        )
        .first()
    )
    if row is None:
        row = CoupangAdCostDaily(cost_date=cost_date, vendor_id=vendor_id)
        db.add(row)
    row.day_cost = day_cost
    row.month_cost = month_cost
    row.synced_at = kst_now()


def sync_ad_cost(db: Session) -> dict:
    """오늘 광고비를 advertising.coupang.com에서 가져와 coupang_ad_cost_daily에 upsert.

    반환: {date, vendors: [{vendor_id, day_cost, month_cost}], error?}
    fail-soft: 인증 실패·네트워크 오류는 status red + 예외 흡수.
    """
    today = datetime.now(_KST).date()
    stats: dict = {"date": str(today), "vendors": []}

    row = _cookie_row(db)
    if row is None or not row.cookie_blob:
        stats["error"] = "cookie_missing"
        log.warning("광고비 sync: 쿠키 미설정(%s)", _ADS_ACCOUNT)
        return stats

    try:
        cookie = decrypt_secret(row.cookie_blob)
    except CookieCryptoError as e:
        _mark_cookie(db, status="red", error=str(e))
        stats["error"] = "crypto_error"
        return stats

    payload = {
        "parentNodes": [
            {"adNodeId": int(vid), "campaignType": "PA"}
            for vid in _DEFAULT_VENDOR_IDS
        ]
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Cookie": cookie,
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/148.0.0.0 Safari/537.36"
        ),
        "Referer": "https://advertising.coupang.com/marketing/dashboard/sales",
        "Origin": "https://advertising.coupang.com",
    }

    try:
        resp = requests.post(_ADS_URL, json=payload, headers=headers, timeout=15)
    except requests.RequestException as e:
        _mark_cookie(db, status="red", error=f"network: {e}")
        stats["error"] = f"network_error: {e}"
        log.error("광고비 sync 네트워크 오류: %s", e)
        return stats

    if resp.status_code in (301, 302, 401, 403):
        _mark_cookie(db, status="red", error=f"auth_expired: HTTP {resp.status_code}")
        stats["error"] = f"auth_expired: {resp.status_code}"
        log.warning("광고비 sync 인증 만료: HTTP %s", resp.status_code)
        return stats

    if resp.status_code != 201:
        _mark_cookie(db, status="red", error=f"unexpected: HTTP {resp.status_code}")
        stats["error"] = f"unexpected_status: {resp.status_code}"
        log.error("광고비 sync 예상 외 응답: HTTP %s — %s", resp.status_code, resp.text[:200])
        return stats

    try:
        data: dict = resp.json()
    except Exception as e:
        _mark_cookie(db, status="red", error=f"json_parse: {e}")
        stats["error"] = f"json_parse: {e}"
        return stats

    # data = {"104438581": {"all":0, "day":43531, "month":0}, ...}
    for vendor_id, costs in data.items():
        if not isinstance(costs, dict):
            continue
        day_cost = int(costs.get("day") or 0)
        month_cost = int(costs.get("month") or 0)
        _upsert_cost(db, today, str(vendor_id), day_cost, month_cost)
        stats["vendors"].append({
            "vendor_id": vendor_id,
            "day_cost": day_cost,
            "month_cost": month_cost,
        })

    db.commit()
    _mark_cookie(db, status="green", error=None, success=True)
    log.info("광고비 sync 완료: %s %s", today, stats["vendors"])
    return stats


# ════════════════════════════════════════════════
# 로컬 페처 인제스트 (Akamai 우회 — prod 직접 fetch 불가, D-5 보강)
# ════════════════════════════════════════════════
def ingest_ad_cost(db: Session, cost_date: date, vendors: list[dict]) -> dict:
    """로컬 페처(Jino Mac, residential IP)가 보낸 광고비 숫자를 upsert.

    advertising.coupang.com은 Akamai가 데이터센터 IP를 차단 → prod 직접 fetch 불가.
    Jino Mac에서 fetch(201)한 결과만 받아 적재. 성공 시 status=green + last_success(=heartbeat,
    배너 staleness 기준). vendors: [{"vendor_id","day_cost","month_cost"}].
    """
    n = 0
    for v in vendors:
        vid = str(v.get("vendor_id") or "").strip()
        if not vid:
            continue
        _upsert_cost(db, cost_date, vid,
                     int(v.get("day_cost") or 0), int(v.get("month_cost") or 0))
        n += 1
    db.commit()
    # 상태 행 보장(로컬 아키텍처에선 prod에 cookie_blob 없을 수 있음) — heartbeat용.
    row = _cookie_row(db)
    if row is None:
        row = CoupangWingCookie(account_key=_ADS_ACCOUNT)
        db.add(row)
    row.status = "green"
    row.last_error = None
    row.last_success_at = kst_now()
    db.commit()
    log.info("광고비 ingest: %s vendors=%d", cost_date, n)
    return {"date": str(cost_date), "ingested": n}


# ════════════════════════════════════════════════
# 갱신 트리거 (대시보드 버튼 → Mac 페처 데몬, "볼 때만 클릭" 방식)
# ════════════════════════════════════════════════
def request_refresh(db: Session) -> dict:
    """대시보드 '광고비 갱신' 버튼 → 갱신 요청 플래그 set. Mac 페처가 다음 폴링에서 소비."""
    row = _cookie_row(db)
    if row is None:
        row = CoupangWingCookie(account_key=_ADS_ACCOUNT)
        db.add(row)
    row.refresh_requested_at = kst_now()
    db.commit()
    return {"requested": True, "requested_at": row.refresh_requested_at.isoformat()}


def refresh_status(db: Session) -> dict:
    """갱신 요청/완료 상태. 대시보드(버튼 후 폴링)·Mac 페처(요청 확인) 공용.

    requested=요청 대기 중. last_success_at=마지막 push(버튼 후 이 값이 올라가면 갱신 완료).
    """
    row = _cookie_row(db)
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
    """Mac 페처가 갱신 요청을 '소비'(플래그 clear)하고 작업 시작. 중복 트리거/루프 방지.

    claimed=True면 요청이 있었고 지금 clear됨 → 페처가 headful fetch 진행. False면 요청 없음.
    claim 후 fetch가 실패(예: 로그인 필요)해도 플래그는 이미 clear라 창이 반복해 뜨지 않는다.
    """
    row = _cookie_row(db)
    if row is None or row.refresh_requested_at is None:
        return {"claimed": False, "requested_at": None}
    requested_at = row.refresh_requested_at.isoformat()
    row.refresh_requested_at = None
    db.commit()
    return {"claimed": True, "requested_at": requested_at}


# ════════════════════════════════════════════════
# 조회 헬퍼
# ════════════════════════════════════════════════
def get_ad_cost_range(db: Session, start: date, end: date) -> list[dict]:
    """날짜 범위 광고비 조회. 날짜별 vendor_id 합산."""
    from sqlalchemy import func as sqlfunc

    rows = (
        db.query(
            CoupangAdCostDaily.cost_date,
            sqlfunc.sum(CoupangAdCostDaily.day_cost).label("day_cost"),
        )
        .filter(
            CoupangAdCostDaily.cost_date >= start,
            CoupangAdCostDaily.cost_date <= end,
        )
        .group_by(CoupangAdCostDaily.cost_date)
        .order_by(CoupangAdCostDaily.cost_date)
        .all()
    )
    return [{"date": str(r.cost_date), "day_cost": int(r.day_cost)} for r in rows]
