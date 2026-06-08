# rg_settlement_sync.py — RG 정산 수수료 동기화 Harness (트랙 RG-Fee-Accounting S3)
# 흐름: 계정별 → 쿠키 로드·복호화(재사용) → Wing status/api 호출(SA) → 파싱·검산 → upsert.
# D-8: parser 별도 SA 없음 — 파싱은 이 Harness 책임. D-10: 매출인식일(searchDateType=SALES).
# D-9: 판매수수료(B)+풀필먼트(J) 둘 다 수집. D-13: 방어적 파싱+스키마 드리프트 감지.
# D-12: fixture 기반 테스트(tests/test_rg_settlement_sync.py) — 머니코드라 예외.
# fail-soft: 302/401=status red + 예외 전파(호출자가 catch). 성공=last_success_at 기록.
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.clients.coupang.inbound import WingAuthError, WingReadError, parse_curl_cookies
from app.clients.coupang.rg_settlement import CoupangWingRgSettlementClient
from app.models import CoupangRgSettlementFee, CoupangWingCookie
from app.utils.crypto import decrypt_secret
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")
RG_ACCOUNTS = ["COUPANG_WING1", "COUPANG_WING2"]

# status/api 응답 → fee_type 매핑 (D-9: 판매수수료+풀필먼트 포함)
# key=응답 필드명, value=(fee_type, 부호: 1=비용[양수저장], -1=환급[음수저장])
_FEE_FIELD_MAP: dict[str, tuple[str, int]] = {
    "totalTakeRateAmountWithVat": ("sale_fee", 1),           # 판매수수료(B, VAT포함)
    "totalFulfillmentFeeDeductionAmount": ("fulfillment", 1), # 풀필먼트 비용(J) 합계
    "totalStorageFeeDeductionAmount": ("storage", 1),         # 보관비
    "totalWarehousingFeeDeductionAmount": ("warehousing", 1), # 입출고비
    "totalCreturnReverseShippingFeeDeductionAmount": ("return_shipping", 1),  # 반품배송비
    "totalVreturnHandlingFeeDeductionAmount": ("return_handling", 1),          # 반출처리비
    "totalAdSalesDeductionAmount": ("ad_sales", 1),           # 광고비(D-11: dedup 대상, 표시만)
}

# 정산주기 최대 조회 주 수 (폭주 방지)
_MAX_WEEKS = 52


# ═══════════════════════════════════════════
# 파싱 헬퍼
# ═══════════════════════════════════════════

def _dec(value) -> Decimal:
    """숫자 값 → Decimal. None/변환불가=0. D-13 방어."""
    if value is None:
        return Decimal(0)
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(0)


def _parse_date(value) -> date | None:
    """UTC ISO "YYYY-MM-DDTHH:mm:ssZ" → KST date. None=파싱실패."""
    if not value:
        return None
    s = str(value).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.astimezone(_KST).date()
    except (ValueError, TypeError):
        log.warning("RG settlement date 파싱 실패: %s", value)
        return None


def _extract_fees(detail: dict, period_start: date, period_end: date, account_key: str) -> list[CoupangRgSettlementFee]:
    """settlementStatusReportDetail → CoupangRgSettlementFee 리스트.

    D-9: sale_fee+fulfillment 둘 다. D-10: recognition_date_from/to=매출인식일.
    D-13: 키 없으면 0으로 처리(warn만, 중단 안 함).
    """
    rows = []
    for field, (fee_type, sign) in _FEE_FIELD_MAP.items():
        raw = detail.get(field)
        if raw is None:
            log.debug("RG settlement field 없음(스키마 변동 가능): %s", field)
        amount = _dec(raw) * sign
        rows.append(CoupangRgSettlementFee(
            account_key=account_key,
            recognition_date_from=period_start,
            recognition_date_to=period_end,
            fee_type=fee_type,
            raw_type=field,
            amount=amount,
        ))
    return rows


def _parse_status_response(data: dict, account_key: str) -> list[CoupangRgSettlementFee]:
    """status/api 응답 dict → CoupangRgSettlementFee 리스트.

    D-13: 'settlementStatusReports' 키 없거나 list 아님 → WingReadError(스키마 드리프트).
    정산주기 하나당 여러 row(fee_type별). 같은 기간 중복은 upsert로 처리.
    """
    if "settlementStatusReports" not in data:
        raise WingReadError(
            "status/api 응답에 settlementStatusReports 키 없음(스키마 드리프트 의심)"
        )
    reports = data["settlementStatusReports"]
    if not isinstance(reports, list):
        raise WingReadError(
            f"settlementStatusReports가 list 아님: {type(reports).__name__}"
        )

    all_rows: list[CoupangRgSettlementFee] = []
    seen_keys: set[tuple] = set()

    for report in reports:
        if not isinstance(report, dict):
            log.warning("RG settlement report가 dict 아님, 건너뜀: %s", type(report))
            continue

        period_start = _parse_date(report.get("settlementPeriodStartDate"))
        period_end = _parse_date(report.get("settlementPeriodEndDate"))
        if period_start is None or period_end is None:
            log.warning("RG settlement 기간 파싱 실패, 건너뜀: %s", report.get("settlementGroupKey"))
            continue

        detail = report.get("settlementStatusReportDetail")
        if not isinstance(detail, dict):
            log.warning("settlementStatusReportDetail 없음, 건너뜀: %s / %s", period_start, period_end)
            continue

        rows = _extract_fees(detail, period_start, period_end, account_key)
        for row in rows:
            key = (account_key, period_start, period_end, row.fee_type)
            if key not in seen_keys:
                seen_keys.add(key)
                all_rows.append(row)
            # 같은 period에 70%+30% 분할정산 레코드가 2개 오는 경우 — 합산
            else:
                for existing in all_rows:
                    if (existing.account_key == row.account_key
                            and existing.recognition_date_from == row.recognition_date_from
                            and existing.recognition_date_to == row.recognition_date_to
                            and existing.fee_type == row.fee_type):
                        existing.amount += row.amount
                        break

    return all_rows


# ═══════════════════════════════════════════
# 쿠키 CRUD (rg_inbound_sync 패턴 재사용)
# ═══════════════════════════════════════════

def save_settlement_cookie(db: Session, account_key: str, curl_text: str) -> CoupangWingCookie:
    """cURL 붙여넣기 → 쿠키 암호화 저장. parse_curl_cookies + encrypt_secret 재사용."""
    from app.utils.crypto import encrypt_secret
    cookie, xsrf = parse_curl_cookies(curl_text)
    enc_blob = encrypt_secret(cookie)
    row = db.query(CoupangWingCookie).filter_by(account_key=account_key).first()
    if row is None:
        row = CoupangWingCookie(account_key=account_key)
        db.add(row)
    row.cookie_blob = enc_blob
    row.xsrf_token = xsrf
    row.status = "green"
    row.last_updated_at = kst_now()
    db.commit()
    db.refresh(row)
    return row


def _load_client(db: Session, account_key: str) -> CoupangWingRgSettlementClient:
    """DB에서 쿠키 로드·복호화 → CoupangWingRgSettlementClient 반환.

    쿠키 없거나 status=red → WingAuthError(호출자가 fail-soft 처리).
    """
    row = db.query(CoupangWingCookie).filter_by(account_key=account_key).first()
    if row is None or not row.cookie_blob:
        raise WingAuthError(f"{account_key} Wing 쿠키 없음 — 쿠키 등록 필요")
    if row.status == "red":
        raise WingAuthError(f"{account_key} Wing 쿠키 만료(status=red) — 재등록 필요")
    cookie = decrypt_secret(row.cookie_blob)
    # xsrf_token은 Fernet 암호화 저장(inbound_sync.py:176 동일 패턴). 복호화 필수.
    xsrf = decrypt_secret(row.xsrf_token) if row.xsrf_token else ""
    return CoupangWingRgSettlementClient(cookie_header=cookie, xsrf_token=xsrf)


def _mark_red(db: Session, account_key: str) -> None:
    row = db.query(CoupangWingCookie).filter_by(account_key=account_key).first()
    if row:
        row.status = "red"
        db.commit()


def _mark_last_success(db: Session, account_key: str) -> None:
    row = db.query(CoupangWingCookie).filter_by(account_key=account_key).first()
    if row:
        row.last_success_at = kst_now()
        db.commit()


# ═══════════════════════════════════════════
# 메인 동기화 (라우터/스케줄러가 호출)
# ═══════════════════════════════════════════

def sync_rg_settlement(
    db: Session,
    account_key: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """RG 정산 수수료 수집·파싱·upsert. D-10: 매출인식일 기준.

    start_date/end_date: KST "YYYY-MM-DD". None=최근 90일.
    반환: {"synced": N, "account_key": ..., "period": ..., "status": "ok"|"auth_error"|"read_error"}
    fail-soft: WingAuthError→status=red+반환, WingReadError→반환(이력 유지).
    """
    today = kst_now().date()
    if end_date is None:
        end_date = today.strftime("%Y-%m-%d")
    if start_date is None:
        start_date = (today - timedelta(days=90)).strftime("%Y-%m-%d")

    log.info("RG 정산 sync 시작: %s %s~%s", account_key, start_date, end_date)

    try:
        client = _load_client(db, account_key)
    except WingAuthError as e:
        log.warning("RG 정산 쿠키 로드 실패: %s — %s", account_key, e)
        return {"synced": 0, "account_key": account_key, "period": f"{start_date}~{end_date}", "status": "auth_error", "error": str(e)}

    try:
        raw = client.get_settlement_status(start_date=start_date, end_date=end_date)
    except WingAuthError as e:
        log.warning("RG 정산 API 인증 실패: %s — %s", account_key, e)
        _mark_red(db, account_key)
        return {"synced": 0, "account_key": account_key, "period": f"{start_date}~{end_date}", "status": "auth_error", "error": str(e)}
    except WingReadError as e:
        log.error("RG 정산 API 읽기 실패: %s — %s", account_key, e)
        return {"synced": 0, "account_key": account_key, "period": f"{start_date}~{end_date}", "status": "read_error", "error": str(e)}

    try:
        rows = _parse_status_response(raw, account_key)
    except WingReadError as e:
        log.error("RG 정산 파싱 실패: %s — %s", account_key, e)
        return {"synced": 0, "account_key": account_key, "period": f"{start_date}~{end_date}", "status": "parse_error", "error": str(e)}

    # upsert: (account_key, recognition_date_from, recognition_date_to, fee_type) 기준
    synced = 0
    for row in rows:
        existing = db.query(CoupangRgSettlementFee).filter_by(
            account_key=row.account_key,
            recognition_date_from=row.recognition_date_from,
            recognition_date_to=row.recognition_date_to,
            fee_type=row.fee_type,
        ).first()
        if existing is None:
            db.add(row)
        else:
            existing.amount = row.amount
            existing.raw_type = row.raw_type
            existing.synced_at = kst_now()
        synced += 1

    db.commit()
    _mark_last_success(db, account_key)
    log.info("RG 정산 sync 완료: %s — %d건", account_key, synced)
    return {"synced": synced, "account_key": account_key, "period": f"{start_date}~{end_date}", "status": "ok"}


def sync_all_rg_settlements(db: Session, *, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
    """모든 RG 계정 정산 수수료 sync. 스케줄러에서 호출."""
    return [sync_rg_settlement(db, ak, start_date=start_date, end_date=end_date) for ak in RG_ACCOUNTS]
