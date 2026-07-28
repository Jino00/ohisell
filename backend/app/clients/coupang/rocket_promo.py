# rocket_promo.py — 쿠팡 프로모션 손익 레이어 순수 파서 SA (트랙 coupang-promo-pnl, Phase 1)
#
# 무엇을 파싱하나: **우리가 정의한 레코드 계약**(PLAN §4)이지 쿠팡 원시 응답이 아니다.
#   ⚠️ 왜 그런가(원칙: 추측 금지): 2026-07-28 정찰에서 supplier.coupang.com 세션이 만료돼
#   ①판매분석 데이터 API ②프로모션 목록/상세 API의 경로·파라미터·응답 스키마를 특정하지 못했다.
#   모르는 스키마의 파서를 지어내면 나중에 조용히 틀린 값을 적재한다. 그래서 경계를 이렇게 나눈다:
#       [쿠팡 원시] --(페처가 매핑, 정찰 후 작성)--> [우리 레코드 계약] --(이 SA)--> [정규화 레코드]
#   정찰이 끝나면 페처의 매핑만 채우면 되고, 이 SA와 테이블·ingest는 그대로 재사용된다.
#
# 단일 책임(원칙18-1): 값 정규화·검증만. HTTP 없음, DB 없음. 적재는 services/coupang/rocket_promo_sync.py.
# 방어적 파싱: 필수키 없는 행은 **그 행만 skip**하고 계속한다 — 한 행이 배치를 죽이지 않는다.
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════
# 값 변환 헬퍼 (방어적) — '없음'(None)과 '0'을 구분한다
# ════════════════════════════════════════════════
def _s(v: Any, limit: int | None = None) -> str | None:
    """문자열 정규화. 빈 문자열·'-'는 None. limit이 있으면 잘라낸다."""
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "-"):
        return None
    return s[:limit] if limit else s


def _int(v: Any, default: int | None = 0) -> int | None:
    """'1,234' / 1234 / None → int. 실패는 default('없음' 필드는 None을 넘긴다)."""
    if v is None:
        return default
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace(",", "").replace("%", "")
    if s in ("", "-"):
        return default
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return default


def _dec(v: Any, default: Decimal | None = None) -> Decimal | None:
    """'12,345.6' / Decimal / None → Decimal. 실패는 default. 통화기호·콤마·% 제거."""
    if v is None:
        return default
    if isinstance(v, Decimal):
        return v
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    s = str(v).strip().replace(",", "").replace("%", "").replace("원", "").strip()
    if s in ("", "-"):
        return default
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return default


def _date(v: Any) -> date | None:
    """'2026-07-24' / '2026-07-24 00:01:00' / date / datetime → date|None."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if s in ("", "-"):
        return None
    s = s.replace("/", "-")
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _dt(v: Any) -> datetime | None:
    """'2026-07-24 00:01:00' / ISO / 'YYYY-MM-DD' → naive datetime|None.

    ★초를 버리지 않는다(D-CPP-4 인접): 프로모션 행사기간은 초 단위라
      00:01:00~23:59:59 같은 경계가 그대로 의미를 갖는다.
    tz가 붙어 오면 그대로 떼고 naive로 저장한다(기존 rocket 모델과 동일하게 저장측 통일).
    """
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.replace(tzinfo=None) if v.tzinfo else v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    s = str(v).strip()
    if s in ("", "-"):
        return None
    s = s.replace("/", "-")
    try:
        dt = datetime.fromisoformat(s)
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:len("2026-07-24 00:01:00")], fmt)
        except ValueError:
            continue
    log.warning("프로모션 일시 파싱 실패 → None: %r", v)
    return None


# ════════════════════════════════════════════════
# ① 1P 옵션×일 판매 (판매분석)
# ════════════════════════════════════════════════
def parse_sales_rows(rows: list[dict], *, source: str = "sales_analysis") -> list[dict]:
    """레코드 계약(sales) → 정규화 레코드. 필수 = option_id, date.

    계약: {option_id*, date*, sku_id, qty, revenue, visitors, conversion_rate, product_name}
    반환 레코드: 위 키 + source. option_id/date 없는 행은 skip(경고 로그).
    같은 (option_id, date)가 배치 안에서 중복되면 **뒤에 온 행이 이긴다**(수집기 재조회 = 최신).
    """
    out: dict[tuple[str, date], dict] = {}
    skipped = 0
    for r in rows or []:
        if not isinstance(r, dict):
            skipped += 1
            continue
        option_id = _s(r.get("option_id"), 30)
        d = _date(r.get("date"))
        if not option_id or d is None:
            skipped += 1
            continue
        out[(option_id, d)] = {
            "option_id": option_id,
            "date": d,
            "sku_id": _s(r.get("sku_id"), 30),
            "qty": _int(r.get("qty"), 0) or 0,
            "revenue": _dec(r.get("revenue"), Decimal(0)),
            "visitors": _int(r.get("visitors"), None),
            "conversion_rate": _dec(r.get("conversion_rate"), None),
            "product_name": _s(r.get("product_name"), 300),
            "source": _s(source, 20) or "sales_analysis",
        }
    if skipped:
        log.warning("1P 판매 레코드 skip %d건(option_id/date 누락)", skipped)
    return list(out.values())


# ════════════════════════════════════════════════
# ② 1P 프로모션 신청 (공급자허브)
# ════════════════════════════════════════════════
def parse_promotion_rows(rows: list[dict]) -> list[dict]:
    """레코드 계약(promotion) → 정규화 레코드. 필수 = request_id.

    계약: {request_id*, contract_id, promotion_name, promotion_type, status, start_at, end_at,
           share_ratio, discount_method, discount_value, budget_amount, settlement_date,
           applied_product_count, requested_at, raw}
    raw는 dict일 때만 보존(리스트/문자열은 {"_raw": ...}로 감싸 저장 — JSON 컬럼 타입 안정).
    같은 request_id 중복 시 뒤에 온 행이 이긴다.
    """
    out: dict[str, dict] = {}
    skipped = 0
    for r in rows or []:
        if not isinstance(r, dict):
            skipped += 1
            continue
        request_id = _s(r.get("request_id"), 30)
        if not request_id:
            skipped += 1
            continue
        raw = r.get("raw")
        if raw is not None and not isinstance(raw, dict):
            raw = {"_raw": raw}
        out[request_id] = {
            "request_id": request_id,
            "contract_id": _s(r.get("contract_id"), 30),
            "promotion_name": _s(r.get("promotion_name"), 300),
            "promotion_type": _s(r.get("promotion_type"), 40),
            "status": _s(r.get("status"), 30),
            "start_at": _dt(r.get("start_at")),
            "end_at": _dt(r.get("end_at")),
            "share_ratio": _dec(r.get("share_ratio"), None),
            "discount_method": _s(r.get("discount_method"), 40),
            "discount_value": _dec(r.get("discount_value"), None),
            "budget_amount": _dec(r.get("budget_amount"), None),
            "settlement_date": _date(r.get("settlement_date")),
            "applied_product_count": _int(r.get("applied_product_count"), None),
            "requested_at": _dt(r.get("requested_at")),
            "raw": raw,
        }
    if skipped:
        log.warning("1P 프로모션 레코드 skip %d건(request_id 누락)", skipped)
    return list(out.values())


# ════════════════════════════════════════════════
# ③ 2P RG 쿠폰 사용 금액 (D-CPP-3 권위값)
# ════════════════════════════════════════════════
def parse_coupon_usage_rows(rows: list[dict]) -> list[dict]:
    """레코드 계약(coupon) → 정규화 레코드. 필수 = coupon_id + **파싱 가능한** used_amount.

    ★used_amount가 없거나 숫자가 아니면 skip한다 — 0으로 접으면 "안 쓴 쿠폰"과 "값을 못 읽은
      쿠폰"이 구분되지 않는다(원칙22: 모르는 것은 모른다고 둔다).
    음수는 skip(쿠폰 사용액은 음수가 될 수 없다 — 파싱 사고 신호).
    """
    out: dict[str, dict] = {}
    skipped = 0
    for r in rows or []:
        if not isinstance(r, dict):
            skipped += 1
            continue
        coupon_id = _s(r.get("coupon_id"), 30)
        amount = _dec(r.get("used_amount"), None)
        if not coupon_id or amount is None or amount < 0:
            skipped += 1
            continue
        out[coupon_id] = {"coupon_id": coupon_id, "used_amount": amount}
    if skipped:
        log.warning("쿠폰 사용금액 레코드 skip %d건(coupon_id/used_amount 누락·음수)", skipped)
    return list(out.values())
