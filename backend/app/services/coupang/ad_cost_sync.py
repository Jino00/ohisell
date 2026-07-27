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
from app.services.coupang import refresh_contract
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
# report/SALES(vendor 합계 일별 확정) 정규행 키. 날짜당 이 키 1행으로 교체 →
# 옛 report/cost per-campaign 스냅샷 행과 중복합산 방지.
_SALES_KEY = "ADV_SALES"


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


def _refresh_cron_enabled(db: Session) -> bool | None:
    """갱신 크론(request_ad_cost_refresh) on/off. 행 없으면 None(판정 불가).

    ★크론이 꺼져 있으면 쿠키가 멀쩡해도 갱신 요청이 안 나가 push가 끊긴다(13일 정지의 실제
    원인) — 쿠키 red로 오진단하지 않도록 배너가 이 값을 우선 본다. 순환 import 회피 위해 지연 import.
    """
    from app.models import SchedulerState

    row = (
        db.query(SchedulerState)
        .filter(SchedulerState.job_name == "request_ad_cost_refresh")
        .first()
    )
    return None if row is None else bool(row.is_enabled)


def cookie_status(db: Session) -> dict:
    """쿠키 설정·상태 요약 (민감값 미포함).

    로컬 페처 아키텍처(Akamai로 prod 직접 fetch 불가): 광고비는 Jino Mac 로컬 페처가
    ingest로 push. last_success_at = 마지막 push 시각. stale=push가 _STALE_HOURS 초과로
    끊김(페처 다운) → 전역 배너. age_hours/stale은 배너 트리거에 사용.
    """
    cron_enabled = _refresh_cron_enabled(db)
    row = _cookie_row(db)
    if row is None:
        return {"account": _ADS_ACCOUNT, "configured": False, "status": "none",
                "last_saved_at": None, "last_success_at": None, "last_error": None,
                "last_error_at": None, "age_hours": None, "stale": False,
                "refresh_cron_enabled": cron_enabled}
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
        "last_error_at": row.last_error_at.isoformat() if row.last_error_at else None,
        "age_hours": age_hours,
        "stale": stale,
        "refresh_cron_enabled": cron_enabled,
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
        row.last_error_at = kst_now()  # last_success_at의 짝 — "언제 실패했나"
    if success:
        row.last_success_at = kst_now()
        row.last_error = None
        row.last_error_at = None  # 성공하면 과거 실패 흔적을 남기지 않는다(배너 오진단 방지)
    db.commit()
    if success:
        # ★lease 계약: 갱신 요청이 소멸하는 정상 경로는 성공 하나뿐(claim은 더 이상 소비 안 함).
        refresh_contract.mark_success(db, _ADS_ACCOUNT)


def mark_fetch_error(db: Session, error: str, kind: str | None = None) -> None:
    """Mac 페처 run 실패 보고 → last_error/last_error_at 기록(UI가 실패를 감지하는 유일 경로).

    ★존재 이유(2026-07-17 13:02 실사고): 페처가 브라우저 에러로 죽어도 prod에 알리는 경로가
    없어 last_error가 계속 null이었다. UI는 "실패"와 "아직 진행 중"을 구분할 수단이 없어
    215초를 헛기다린 뒤 "Mac 응답 없음"이라는 뭉뚱그린 문구만 냈다(진짜 원인은 화면 어디에도
    안 나옴). 형제 fetch-success(mark_fetch_success)의 짝.

    ★status는 일부러 건드리지 않는다(codex 1R[P2]): 이 행의 status=red는 Layout 배너에서
    곧바로 "쿠키 만료(재설정 필요)" + 쿠키 재설정 CTA로 렌더된다(Layout.tsx:201/206).
    브라우저 크래시·Mac 세션 만료는 쿠키 문제가 아니라 재설정해도 헛수고다(07-17에 실제로
    그 헛수고를 했다 — 쿠키는 멀쩡했고 창이 닫힌 게 원인이었다). 지속 실패는 워치독이
    last_success_at 경과로 잡고(status 미의존), 배너는 stale→"로컬 페처 확인 필요"로 흐른다.

    ★kind(옵션): "login_required"면 재시도하지 않고 요청 소멸(§0 금지선 — 재시도해도 실패하고
    창만 반복해서 뜬다). 그 외 실패는 lease만 반납해 다음 폴에서 재시도된다(최대 3회).
    """
    refresh_contract.report_failure(db, _ADS_ACCOUNT, error, kind)


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
    # 오늘 running(report/cost)은 PA day_cost만 → 전체=집행으로 둠(비-PA는 확정일 SALES에만, S5a).
    row.all_day_cost = day_cost
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
    row.last_error_at = None  # 성공 = 실패 흔적 클리어(_mark_cookie(success=True)와 동일 규칙)
    row.last_success_at = kst_now()
    db.commit()
    refresh_contract.mark_success(db, _ADS_ACCOUNT)  # 갱신 요청 소멸(성공만이 소멸시킨다)
    log.info("광고비 ingest: %s vendors=%d", cost_date, n)
    return {"date": str(cost_date), "ingested": n}


def ingest_ad_cost_days(db: Session, days: list[dict]) -> dict:
    """report/SALES 기반 날짜별 확정 광고비 적재(Mac 페처가 최근 N일치 push).

    각 날짜는 정규행 1개(_SALES_KEY)로 '교체'한다 — 해당 날짜 기존 행(옛 report/cost
    per-campaign 스냅샷 포함)을 전부 삭제 후 삽입. 같은 날짜를 다시 받으면 확정치로 교정.
    days: [{"date": date, "ad_spend": int(집행/DELIVERED), "conv_sales": int, "all_cost"?: int|None(전체/ALL)}].
    S5a/D-15: all_cost = ALL_DELIVERED_AD_COST(전체, 비-PA 포함). None(미제공) 시 ad_spend 폴백(하위호환, 비-PA=0).
    가드: 전체<집행이면 데이터 이상 → 전체=집행으로 클램프(비-PA 음수 방지).
    ★머니필드 가시성(codex P2-2): 미제공/클램프를 카운트·로그(필드 깨짐 시 조용히 비-PA=0 되는 것 감지).
    """
    n = 0
    all_cost_missing = 0   # all_cost 미제공(None) — 구 페처/필드 누락
    all_cost_clamped = 0   # 전체<집행으로 클램프 — API 필드 이상(예: ALL_DELIVERED=0)
    for d in days:
        cost_date = d["date"]
        ad_spend = int(d["ad_spend"])
        raw_all = d.get("all_cost")
        if raw_all is None:
            all_cost = ad_spend
            all_cost_missing += 1
        else:
            all_cost = int(raw_all)
            if all_cost < ad_spend:  # 전체≥집행 불변식(비-PA≥0) — 이상 데이터 방어
                all_cost = ad_spend
                all_cost_clamped += 1
        db.query(CoupangAdCostDaily).filter(
            CoupangAdCostDaily.cost_date == cost_date
        ).delete(synchronize_session=False)
        db.add(CoupangAdCostDaily(
            cost_date=cost_date,
            vendor_id=_SALES_KEY,
            day_cost=ad_spend,
            all_day_cost=all_cost,
            conv_sales=int(d["conv_sales"]),
            month_cost=0,
            synced_at=kst_now(),
        ))
        n += 1
    if all_cost_missing or all_cost_clamped:
        log.warning(
            "광고비 ingest(SALES): all_cost 이상 — missing=%d clamped=%d / days=%d "
            "(비-PA가 조용히 0 처리됨, 페처 ALL_DELIVERED_AD_COST 필드 점검 필요)",
            all_cost_missing, all_cost_clamped, n,
        )
    db.commit()
    # heartbeat green(배너 staleness 기준) — ingest_ad_cost와 동일.
    row = _cookie_row(db)
    if row is None:
        row = CoupangWingCookie(account_key=_ADS_ACCOUNT)
        db.add(row)
    row.status = "green"
    row.last_error = None
    row.last_error_at = None  # 성공 = 실패 흔적 클리어(_mark_cookie(success=True)와 동일 규칙)
    row.last_success_at = kst_now()
    db.commit()
    refresh_contract.mark_success(db, _ADS_ACCOUNT)  # 갱신 요청 소멸(성공만이 소멸시킨다)
    log.info("광고비 ingest(SALES): days=%d", n)
    return {"ingested_days": n, "all_cost_missing": all_cost_missing,
            "all_cost_clamped": all_cost_clamped}


# ════════════════════════════════════════════════
# 갱신 트리거 (대시보드 버튼 → Mac 페처 데몬, "볼 때만 클릭" 방식)
# ════════════════════════════════════════════════
def request_refresh(db: Session) -> dict:
    """대시보드 '광고비 갱신' 버튼 → 갱신 요청 set.

    ★버튼 1회 = 성공하거나 3회 실패할 때까지 살아있는 요청(lease 계약, refresh_contract).
    """
    return refresh_contract.request_refresh(db, _ADS_ACCOUNT)


def refresh_status(db: Session) -> dict:
    """갱신 요청/완료 상태. 대시보드(버튼 후 폴링)·Mac 페처(요청 확인) 공용.

    requested=요청 대기 중. last_success_at=마지막 push(버튼 후 이 값이 올라가면 갱신 완료).
    last_error_at=마지막 실패 시각(버튼 후 이 값이 올라가면 갱신 실패) — UI가 성공/실패 둘 중
    무엇이 왔는지 이 두 시각의 변화로 가른다. 없으면 실패를 못 보고 폴링 창을 헛기다린다.
    """
    row = _cookie_row(db)
    if row is None:
        return {"requested": False, "requested_at": None, "last_success_at": None,
                "status": "none", "last_error": None, "last_error_at": None,
                **refresh_contract.status_fields(None)}
    return {
        "requested": row.refresh_requested_at is not None,
        "requested_at": row.refresh_requested_at.isoformat() if row.refresh_requested_at else None,
        "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
        "status": row.status,
        "last_error": row.last_error,
        "last_error_at": row.last_error_at.isoformat() if row.last_error_at else None,
        **refresh_contract.status_fields(row),
    }


def claim_refresh(db: Session) -> dict:
    """Mac 페처가 갱신 요청을 **임대**(lease)하고 작업 시작. 중복 claim은 여전히 한쪽만 성공.

    ★2026-07-27 변경(PLAN_coupang-claim-retry-lease): 예전엔 여기서 플래그를 즉시 지웠고,
    claim 직후 페처가 죽으면 요청이 통째로 유실됐다(버튼 1회 = 시도 1회). 이제 요청은
    성공(mark_success)하거나 3회 실패/로그인필요일 때만 소멸한다.
    로그인 필요는 여전히 창이 반복해 뜨지 않는다 — 페처가 kind="login_required"로 보고하면
    즉시 요청이 소멸하기 때문(refresh_contract.report_failure).
    """
    return refresh_contract.claim_refresh(db, _ADS_ACCOUNT)


# ════════════════════════════════════════════════
# 조회 헬퍼
# ════════════════════════════════════════════════
def get_ad_cost_range(db: Session, start: date, end: date) -> list[dict]:
    """날짜 범위 광고비 조회. 날짜별 vendor_id 합산. (S5a: all_day_cost=전체 포함)"""
    from sqlalchemy import func as sqlfunc

    rows = (
        db.query(
            CoupangAdCostDaily.cost_date,
            sqlfunc.sum(CoupangAdCostDaily.day_cost).label("day_cost"),
            sqlfunc.sum(CoupangAdCostDaily.all_day_cost).label("all_day_cost"),
            sqlfunc.sum(CoupangAdCostDaily.conv_sales).label("conv_sales"),
        )
        .filter(
            CoupangAdCostDaily.cost_date >= start,
            CoupangAdCostDaily.cost_date <= end,
        )
        .group_by(CoupangAdCostDaily.cost_date)
        .order_by(CoupangAdCostDaily.cost_date)
        .all()
    )
    return [
        {"date": str(r.cost_date), "day_cost": int(r.day_cost),
         "all_day_cost": int(r.all_day_cost or r.day_cost),
         "conv_sales": int(r.conv_sales or 0)}
        for r in rows
    ]


def get_ad_cost_totals(db: Session, start: date, end: date) -> dict:
    """확정일(report/SALES, _SALES_KEY) 광고비 합계 — 집행(PA)·전체(ALL)·비-PA. (S5a/D-15)

    command-center 정합성: report/SALES는 쿠팡 [광고센터]와 0.02% 일치하는 권위값.
    오늘 running(per-vendor 행)은 확정 아니므로 제외(vendor_id==_SALES_KEY만).
    pa=Σday_cost(DELIVERED), total=Σall_day_cost(ALL_DELIVERED), nonpa=max(0,total-pa).
    """
    from sqlalchemy import func as sqlfunc

    row = (
        db.query(
            sqlfunc.coalesce(sqlfunc.sum(CoupangAdCostDaily.day_cost), 0),
            sqlfunc.coalesce(sqlfunc.sum(CoupangAdCostDaily.all_day_cost), 0),
        )
        .filter(
            CoupangAdCostDaily.vendor_id == _SALES_KEY,
            CoupangAdCostDaily.cost_date >= start,
            CoupangAdCostDaily.cost_date <= end,
        )
        .one()
    )
    pa = int(row[0] or 0)
    total = int(row[1] or 0)
    if total < pa:  # 전체≥집행 불변식 방어(이상 데이터)
        total = pa
    return {"pa": pa, "total": total, "nonpa": total - pa}
