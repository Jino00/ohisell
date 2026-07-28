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
import math
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.utils.kst import KST

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════
# 값 변환 헬퍼 (방어적) — '없음'(None)과 '0'을 구분한다
# ════════════════════════════════════════════════
def _s(v: Any, limit: int | None = None) -> str | None:
    """자유 텍스트 정규화. 빈 문자열·'-'는 None. limit이 있으면 **잘라낸다**(표시용 필드 전용)."""
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "-"):
        return None
    return s[:limit] if limit else s


def _sid(v: Any, limit: int) -> str | None:
    """식별자(조인 키) 정규화 — 길이를 넘으면 **자르지 않고 None**(= 없는 값으로 취급).

    ★왜 자르지 않나: option_id/sku_id/coupon_id는 그레인 키이자 조인 키다. 잘린 ID는
      '다른 ID'가 되어 영원히 잘못된 행에 붙거나 아무데도 안 붙는다(조용한 오염).
      없는 값이면 필수키는 행 skip으로, 선택키는 NULL로 **눈에 보이게** 남는다.
    """
    s = _s(v)
    if s is None:
        return None
    if len(s) > limit:
        log.warning("식별자 길이 초과(%d>%d) → 없는 값 처리: %r", len(s), limit, s)
        return None
    return s


def _int(v: Any, default: int | None = 0) -> int | None:
    """'1,234' / 1234 / None → int. 실패는 default('없음' 필드는 None을 넘긴다).

    ★NaN/Inf 방어: 엑셀 폴백 경로(PLAN §2.5)의 빈 숫자셀은 float('nan')으로 온다.
      int(nan)은 ValueError, int(inf)는 OverflowError로 **배치 전체를 죽인다**.
      한 행이 배치를 죽이지 않는다는 이 모듈의 계약을 지키려면 여기서 접어야 한다.
    """
    if v is None:
        return default
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            return default
        return int(v)
    s = str(v).strip().replace(",", "").replace("%", "")
    if s in ("", "-"):
        return default
    try:
        f = float(s)
    except (ValueError, TypeError):
        return default
    return int(f) if math.isfinite(f) else default


def _dec(v: Any, default: Decimal | None = None) -> Decimal | None:
    """'12,345.6' / Decimal / None → Decimal. 실패는 default. 통화기호·콤마·% 제거.

    ★NaN/Inf 방어: Decimal('nan')·Decimal('Infinity')는 **예외 없이 생성된다** — 아래
      except가 잡지 못한다. 그대로 흘리면 ①NUMERIC 컬럼에 NaN이 적재되고(Phase 2 합계 오염)
      ②`amount < 0` 같은 비교에서 InvalidOperation으로 배치가 죽는다. 유한값만 통과시킨다.
    """
    d: Decimal | None
    if v is None:
        return default
    if isinstance(v, Decimal):
        d = v
    elif isinstance(v, bool):
        return default
    elif isinstance(v, (int, float)):
        d = Decimal(str(v))
    else:
        s = str(v).strip().replace(",", "").replace("%", "").replace("원", "").strip()
        if s in ("", "-"):
            return default
        try:
            d = Decimal(s)
        except (InvalidOperation, ValueError):
            return default
    if d is None or not d.is_finite():
        return default
    return d


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
    ★저장 규약 = **KST naive**(전역 CLAUDE.md 시간 기준). tz가 붙어 오면 **KST로 환산한 뒤**
      tzinfo를 뗀다 — 그냥 떼면 '...T00:01:00Z'가 09:01 KST 현실을 00:01로 적어 9시간 틀어진다.
      초 단위 정밀도를 지킨다면서 9시간을 잃는 건 앞뒤가 안 맞는다.
    """
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.astimezone(KST).replace(tzinfo=None) if v.tzinfo else v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    s = str(v).strip()
    if s in ("", "-"):
        return None
    s = s.replace("/", "-")
    try:
        dt = datetime.fromisoformat(s)
        return dt.astimezone(KST).replace(tzinfo=None) if dt.tzinfo else dt
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
def parse_sales_rows(
    rows: list[dict], *, source: str = "sales_analysis", stats: dict | None = None
) -> list[dict]:
    """레코드 계약(sales) → 정규화 레코드. 필수 = option_id, date, **관측값(qty|revenue) 중 하나**.

    계약: {option_id*, date*, sku_id, qty, revenue, visitors, conversion_rate, product_name}
    반환 레코드: 위 키 + source.
    같은 (option_id, date)가 배치 안에서 중복되면 **뒤에 온 행이 이긴다**(수집기 재조회 = 최신).

    ★qty도 revenue도 없는 행은 skip한다: 그런 행은 '0원 팔린 날'이 아니라 **관측 자체가 없는
      행**이다. 0으로 접으면 "정말 안 팔린 날"과 "페처 매핑이 필드명을 놓친 날"이 구분되지 않고,
      후자는 통째로 0원 테이블을 만들면서 아무 경보도 울리지 않는다(원칙22). 명시적 qty=0은 통과.
    stats(선택): {"skipped": 계약 위반 행 수, "deduped": 같은 그레인에 흡수된 행 수}를 채운다.
    """
    out: dict[tuple[str, date], dict] = {}
    skipped = 0
    accepted = 0
    for r in rows or []:
        if not isinstance(r, dict):
            skipped += 1
            continue
        option_id = _sid(r.get("option_id"), 30)
        d = _date(r.get("date"))
        qty = _int(r.get("qty"), None)
        revenue = _dec(r.get("revenue"), None)
        if not option_id or d is None or (qty is None and revenue is None):
            skipped += 1
            continue
        accepted += 1
        out[(option_id, d)] = {
            "option_id": option_id,
            "date": d,
            "sku_id": _sid(r.get("sku_id"), 30),
            "qty": qty if qty is not None else 0,
            "revenue": revenue if revenue is not None else Decimal(0),
            "visitors": _int(r.get("visitors"), None),
            "conversion_rate": _dec(r.get("conversion_rate"), None),
            "product_name": _s(r.get("product_name"), 300),
            "source": _s(source, 20) or "sales_analysis",
        }
    deduped = accepted - len(out)
    if skipped:
        log.warning("1P 판매 레코드 skip %d건(option_id/date/관측값 누락)", skipped)
    if deduped:
        log.info("1P 판매 레코드 중복 흡수 %d건(같은 option_id×date — 뒤 행 우선)", deduped)
    if stats is not None:
        stats["skipped"] = skipped
        stats["deduped"] = deduped
    return list(out.values())


# ════════════════════════════════════════════════
# ② 1P 프로모션 신청 (공급자허브)
# ════════════════════════════════════════════════
def parse_promotion_rows(rows: list[dict], *, stats: dict | None = None) -> list[dict]:
    """레코드 계약(promotion) → 정규화 레코드. 필수 = request_id.

    계약: {request_id*, contract_id, promotion_name, promotion_type, status, start_at, end_at,
           share_ratio, discount_method, discount_value, budget_amount, settlement_date,
           applied_product_count, requested_at, raw}
    raw는 dict일 때만 보존(리스트/문자열은 {"_raw": ...}로 감싸 저장 — JSON 컬럼 타입 안정).
    같은 request_id 중복 시 뒤에 온 행이 이긴다.
    stats(선택): {"skipped", "deduped"}를 채운다.
    """
    out: dict[str, dict] = {}
    skipped = 0
    accepted = 0
    for r in rows or []:
        if not isinstance(r, dict):
            skipped += 1
            continue
        request_id = _sid(r.get("request_id"), 30)
        if not request_id:
            skipped += 1
            continue
        accepted += 1
        raw = r.get("raw")
        if raw is not None and not isinstance(raw, dict):
            raw = {"_raw": raw}
        out[request_id] = {
            "request_id": request_id,
            "contract_id": _sid(r.get("contract_id"), 30),
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
    deduped = accepted - len(out)
    if skipped:
        log.warning("1P 프로모션 레코드 skip %d건(request_id 누락)", skipped)
    if deduped:
        log.info("1P 프로모션 레코드 중복 흡수 %d건(같은 request_id — 뒤 행 우선)", deduped)
    if stats is not None:
        stats["skipped"] = skipped
        stats["deduped"] = deduped
    return list(out.values())


# ════════════════════════════════════════════════
# ③ 2P RG 쿠폰 사용 금액 (D-CPP-3 권위값)
# ════════════════════════════════════════════════
def parse_coupon_usage_rows(rows: list[dict], *, stats: dict | None = None) -> list[dict]:
    """레코드 계약(coupon) → 정규화 레코드. 필수 = coupon_id + **파싱 가능한** used_amount.

    ★used_amount가 없거나 숫자가 아니면 skip한다 — 0으로 접으면 "안 쓴 쿠폰"과 "값을 못 읽은
      쿠폰"이 구분되지 않는다(원칙22: 모르는 것은 모른다고 둔다).
    음수는 skip(쿠폰 사용액은 음수가 될 수 없다 — 파싱 사고 신호).
    (_dec가 유한값만 돌려주므로 아래 `amount < 0` 비교는 NaN InvalidOperation으로 죽지 않는다.)
    stats(선택): {"skipped", "deduped"}를 채운다.
    """
    out: dict[str, dict] = {}
    skipped = 0
    accepted = 0
    for r in rows or []:
        if not isinstance(r, dict):
            skipped += 1
            continue
        coupon_id = _sid(r.get("coupon_id"), 30)
        amount = _dec(r.get("used_amount"), None)
        if not coupon_id or amount is None or amount < 0:
            skipped += 1
            continue
        accepted += 1
        out[coupon_id] = {"coupon_id": coupon_id, "used_amount": amount}
    deduped = accepted - len(out)
    if skipped:
        log.warning("쿠폰 사용금액 레코드 skip %d건(coupon_id/used_amount 누락·음수)", skipped)
    if deduped:
        log.info("쿠폰 사용금액 레코드 중복 흡수 %d건(같은 coupon_id — 뒤 행 우선)", deduped)
    if stats is not None:
        stats["skipped"] = skipped
        stats["deduped"] = deduped
    return list(out.values())
