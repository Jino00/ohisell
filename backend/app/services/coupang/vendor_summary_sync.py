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
from app.services.coupang import refresh_contract
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# Wing 판매분석 페처(별도 데몬 com.ohisell.wing, D-5)의 heartbeat/refresh 상태 행.
# 광고(COUPANG_ADS1)와 분리 — 도메인·세션·방어체계가 달라(D-5) 상태를 섞지 않는다.
_VS_ACCOUNT = "COUPANG_WING_VS"
# ★계정 차원 큐 분리(2026-07-27, WING2 데몬 편입): 상태행이 계정 무구분 1개면 WING2 인스턴스가
#   WING1('오픽스')의 판매분석 버튼 요청을 claim해 가져간다(claim=원자적 → 먼저 집는 쪽이 이김).
#   판매분석 WING2는 현재 휴면(UI가 요청을 안 만듦)이지만, 큐를 계정별로 갈라 두면 WING2 데몬이
#   폴링해도 자기 행만 보므로 도난이 구조적으로 불가능해진다. WING1은 기존 행 그대로(하위호환),
#   WING2 행은 on-demand 생성(마이그레이션 없음 — RG와 동일 패턴).
_VS_ACCOUNT_BY_ACCOUNT: dict[str, str] = {
    "COUPANG_WING1": _VS_ACCOUNT,
    "COUPANG_WING2": "COUPANG_WING_VS2",
}
# 페처가 이 시간(h) 이상 push 안 하면 stale. Mac 야간 off 오탐 방지 위해 26h(광고와 동일).
_STALE_HOURS = 26
# 등록유형(ref 18): NORMAL=3P 마켓플레이스, RFM=로켓그로스(RG).
_TYPE_3P = "NORMAL"
_TYPE_RG = "RFM"


# ════════════════════════════════════════════════
# 상태 행(heartbeat/refresh) — 광고 페처 패턴 복제
# ════════════════════════════════════════════════
def _vs_state_key(account_key: str) -> str:
    """계정(COUPANG_WING1/2) → 판매분석 상태행 account_key. 그 밖이면 ValueError(라우터가 400)."""
    key = _VS_ACCOUNT_BY_ACCOUNT.get(account_key)
    if key is None:
        raise ValueError(f"지원하지 않는 account_key: {account_key}")
    return key


def _state_row(db: Session, account_key: str = "COUPANG_WING1") -> CoupangWingCookie | None:
    return (
        db.query(CoupangWingCookie)
        .filter(CoupangWingCookie.account_key == _vs_state_key(account_key))
        .first()
    )


def _ensure_state_row(db: Session, account_key: str = "COUPANG_WING1") -> CoupangWingCookie:
    row = _state_row(db, account_key)
    if row is None:
        row = CoupangWingCookie(account_key=_vs_state_key(account_key))
        db.add(row)
    return row


def _mark_heartbeat(db: Session, account_key: str = "COUPANG_WING1") -> None:
    """push 성공 시각 갱신(=배너 staleness 기준). ingest 성공마다 호출.

    ★lease 계약(refresh_contract): 갱신 요청이 소멸하는 정상 경로는 여기 하나뿐이다
    (claim은 더 이상 요청을 소비하지 않는다 — 실패 시 재시도해야 하므로).
    ★계정별(codex R1 [P2], 2026-07-27): 실패 보고(mark_fetch_error)만 계정 차원이고 성공은 아니면
    비대칭이 생긴다 — WING2 ingest(예: wing2 인스턴스의 1회 실행·cmd_login 직후 push)가 WING1 행에
    성공을 찍고 WING1의 실패 흔적까지 지운다. 오픽스는 실제로 안 왔는데 배너는 '신선'이라 말한다.
    """
    row = _ensure_state_row(db, account_key)
    row.status = "green"
    row.last_error = None
    row.last_error_at = None  # 성공 = 실패 흔적 클리어(안 지우면 오래된 실패가 화면에 남는다)
    row.last_success_at = kst_now()
    db.commit()
    refresh_contract.mark_success(db, _vs_state_key(account_key))


def mark_fetch_error(db: Session, error: str, account_key: str = "COUPANG_WING1",
                     kind: str | None = None, lease: str | None = None) -> None:
    """Wing 페처 run 실패 보고 → last_error/last_error_at 기록(UI가 실패를 감지하는 유일 경로).

    ★존재 이유(PR #30이 광고비에서 먼저 고친 것과 같은 구멍): 페처가 갱신 요청을 claim한
    뒤(=플래그 이미 clear) 브라우저 에러로 죽으면 prod에 아무 흔적도 안 남았다. 성공에만
    시각(last_success_at)이 있고 실패엔 짝이 없어서 UI는 "실패"와 "아직 진행 중"을 구분할
    수단이 없었다 — 215초를 헛기다린 뒤 "Mac 응답 없음"이라는 뭉뚱그린 문구만 냈다.

    ★status는 일부러 건드리지 않는다(PR #30 codex 1R[P2]): status=red는 Layout 배너에서
    곧바로 "쿠키 만료(재설정 필요)" + 쿠키 재설정 CTA로 렌더된다(Layout.tsx:201/206).
    브라우저 크래시는 쿠키 문제가 아니라 재설정해도 헛수고다. 지속 실패는 워치독이
    last_success_at 경과로 잡는다(status 미의존).

    ★kind(옵션): "login_required"면 재시도하지 않고 요청을 소멸시킨다(§0 금지선 — 재시도해도
    실패하고 창만 반복해서 뜬다). 그 외 실패는 lease만 반납해 다음 폴에서 재시도된다.
    """
    _ensure_state_row(db, account_key)
    db.commit()  # 행이 없던 경우를 대비(계약 SA는 기존 행에만 쓴다)
    refresh_contract.report_failure(db, _vs_state_key(account_key), error, kind, lease=lease)


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
    _mark_heartbeat(db, account_key)  # ★적재한 계정의 행에 찍는다(codex R1 [P2] — 남의 신선도 위조 방지)
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
                "last_error": None, "last_error_at": None, "age_hours": None, "stale": False}
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
        "last_error_at": row.last_error_at.isoformat() if row.last_error_at else None,
        "age_hours": age_hours,
        "stale": stale,
    }


def request_refresh(db: Session, account_key: str = "COUPANG_WING1") -> dict:
    """UI '판매분석 갱신' 버튼 → 갱신 요청 set. 성공하거나 3회 실패할 때까지 살아있다(lease 계약).

    ★UI는 WING1만 요청한다(WING2 판매분석은 휴면 — 요청이 안 생기니 WING2 데몬은 claim할 게 없다).
    시그니처만 계정 차원으로 맞춰 두어 나중에 WING2를 켤 때 여기만 열면 되게 한다.
    """
    return refresh_contract.request_refresh(db, _vs_state_key(account_key))


def refresh_status(db: Session, account_key: str = "COUPANG_WING1") -> dict:
    """갱신 요청/완료 상태. UI(버튼 후 폴링)·Wing 페처(요청 확인) 공용.

    last_error_at=마지막 실패 시각(버튼 후 이 값이 올라가면 갱신 실패) — UI가 성공/실패 둘 중
    무엇이 왔는지 이 두 시각의 변화로 가른다. 없으면 실패를 못 보고 폴링 창을 헛기다린다.
    """
    row = _state_row(db, account_key)
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


def claim_refresh(db: Session, account_key: str = "COUPANG_WING1") -> dict:
    """Wing 페처가 갱신 요청을 **임대**(lease)한다. 플래그는 성공/소진까지 보존(refresh_contract).

    ★account_key로 상태행이 갈리므로 WING2 데몬이 WING1의 버튼 요청을 훔쳐가지 않는다(큐 격리).
    """
    return refresh_contract.claim_refresh(db, _vs_state_key(account_key))
