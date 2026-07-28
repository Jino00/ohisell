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


_INT64_MAX = 2**63 - 1   # SQLite INTEGER 상한. 넘으면 flush 때 OverflowError로 배치가 죽는다.


# 빈 숫자셀의 텍스트 표현들. 엑셀/판다스가 문자열로 직렬화되면 이 꼴로 온다.
#   ★Infinity는 여기 없다(의도적): 엑셀 빈 셀은 **NaN**으로 오지 Inf로 오지 않는다. Inf는
#     "안 적힌 값"이 아니라 **계산 사고**(0으로 나눔·오버플로)다. 빈 셀로 분류해 0으로 접으면
#     "18개 팔렸는데 매출 0원"이 아무 신호 없이 적재된다 — 상한 초과(1E+999)는 사고로 보면서
#     한 자리 더 간 Inf는 0으로 접는 건 앞뒤가 안 맞는다. Inf는 아래에서 '적혀 있음'으로
#     떨어져 파싱 실패 → 행 skip으로 눈에 보이게 남는다.
_BLANK_TOKENS = {"", "-", "nan", "-nan", "none", "null"}


def _present(v: Any) -> bool:
    """값이 '적혀 있는가' — 빈 셀(None·''·'-'·NaN·'nan')이 아닌가.

    빈 셀과 '읽으려다 실패한 값'을 가르는 데 쓴다. 전자는 0(관측된 0판매일), 후자는 사고다.
    NaN은 숫자로 오든 그 문자열 표현으로 오든 **빈 셀**이다 — 직렬화 방식이 의미를 바꾸면 안 된다.
    반대로 Inf는 **적혀 있는 값**으로 본다(위 _BLANK_TOKENS 주석).
    """
    if v is None:
        return False
    if isinstance(v, float) and math.isnan(v):
        return False
    if isinstance(v, Decimal) and v.is_nan():
        return False
    return str(v).strip().lower() not in _BLANK_TOKENS


def _int(v: Any, default: int | None = 0) -> int | None:
    """'1,234' / 1234 / None → int. 실패는 default('없음' 필드는 None을 넘긴다).

    ★NaN/Inf 방어: 엑셀 폴백 경로(PLAN §2.5)의 빈 숫자셀은 float('nan')으로 온다.
      int(nan)은 ValueError, int(inf)는 OverflowError로 **배치 전체를 죽인다**.
      한 행이 배치를 죽이지 않는다는 이 모듈의 계약을 지키려면 여기서 접어야 한다.
    ★크기 상한도 본다: 유한하다고 담을 수 있는 건 아니다. int64를 넘는 값은 파싱 사고이지
      판매수량이 아니다 — 파싱 시점에 접어야 flush 시점에 배치가 죽지 않는다.
    """
    if v is None:
        return default
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            return default
        n = int(v)
        return n if abs(n) <= _INT64_MAX else default
    s = str(v).strip().replace(",", "").replace("%", "")
    if s in ("", "-"):
        return default
    try:
        f = float(s)
    except (ValueError, TypeError):
        return default
    if not math.isfinite(f):
        return default
    n = int(f)
    return n if abs(n) <= _INT64_MAX else default


def _dec(v: Any, default: Decimal | None = None, *, max_abs: Decimal | None = None) -> Decimal | None:
    """'12,345.6' / Decimal / None → Decimal. 실패는 default. 통화기호·콤마·% 제거.

    ★NaN/Inf 방어: Decimal('nan')·Decimal('Infinity')는 **예외 없이 생성된다** — 아래
      except가 잡지 못한다. 그대로 흘리면 ①NUMERIC 컬럼에 NaN이 적재되고(Phase 2 합계 오염)
      ②`amount < 0` 같은 비교에서 InvalidOperation으로 배치가 죽는다. 유한값만 통과시킨다.
    ★max_abs(크기 상한)도 본다: **유한한 것과 담기는 것은 다르다.** Decimal('1E+999')는
      is_finite()를 통과하지만 SQLite에선 inf로 적재돼 sum()을 오염시키고(NaN 방어와 같은
      사고가 자리만 옮긴 것), PostgreSQL에선 NUMERIC 정밀도 초과로 commit이 통째로 죽는다.
      호출자가 목적지 컬럼의 정밀도를 넘겨준다(아래 _MAX_* 상수).
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
    if max_abs is not None and abs(d) >= max_abs:
        log.warning("수치 크기 상한 초과(>=%s) → 없는 값 처리: %r", max_abs, v)
        return default
    return d


# 목적지 NUMERIC(precision, scale)의 정수부 상한 = 10**(precision-scale).
_MAX_AMOUNT = Decimal(10) ** 12    # NUMERIC(14,2): revenue · budget_amount · used_amount
_MAX_DISCOUNT = Decimal(10) ** 10  # NUMERIC(12,2): discount_value
_MAX_RATIO = Decimal(10) ** 5      # NUMERIC(7,2) : share_ratio
_MAX_RATE = Decimal(10) ** 3       # NUMERIC(7,4) : conversion_rate


def _date(v: Any) -> date | None:
    """'2026-07-24' / '2026-07-24 00:01:00' / date / datetime → date|None.

    ★tz 환산 후 날짜를 딴다(_dt 경유). date는 판매 테이블의 **그레인 키**라, tz가 붙은
      '2026-07-24T23:00:00Z'(=KST 07-25 08:00)를 07-24로 적으면 **다른 날 행에 적재**되고
      페처 포맷이 바뀔 때마다 멱등성이 깨진다. 시각만 KST로 맞추고 날짜를 빼먹으면
      계약(§4 "시각은 KST")이 반만 지켜진다.
    """
    if v is None:
        return None
    if isinstance(v, datetime):
        return _dt(v).date() if v.tzinfo else v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if s in ("", "-"):
        return None
    s = s.replace("/", "-")
    # warn=False: 날짜 전용 표기('2026-07-24 (수)' 등)는 아래 fromisoformat 폴백이 정상
    #   처리한다. 여기서 _dt가 "일시 파싱 실패"를 찍으면 성공한 파싱을 실패로 보고하는
    #   거짓 경보가 된다(경보가 거짓말하면 아무도 안 본다).
    parsed = _dt(s, warn=False)   # tz가 붙어 있으면 KST로 환산된 뒤 날짜가 나온다
    if parsed is not None:
        return parsed.date()
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        # 폴백까지 실패하면 진짜 실패다 — 여기서는 침묵하지 않는다. 거짓 경보를 없앤다고
        #   진짜 실패까지 조용해지면(예: settlement_date가 NULL로 조용히 저장) 안 된다.
        log.warning("날짜 파싱 실패 → None: %r", v)
        return None


def _dt(v: Any, *, warn: bool = True) -> datetime | None:
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
    if warn:
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
    blank_obs = 0
    blank_qty = 0
    blank_rev = 0
    for r in rows or []:
        if not isinstance(r, dict):
            skipped += 1
            continue
        option_id = _sid(r.get("option_id"), 30)
        d = _date(r.get("date"))
        # 관측 유무는 **키 존재**로 본다(값이 아니라). 빈 셀('-'·NaN)은 '0으로 팔린 날'이라는
        #   관측이므로 살리고(visitors 같은 동반 신호까지 버리지 않는다), 키 자체가 없는 것만
        #   '페처 매핑이 필드명을 놓쳤다'로 보고 skip한다 — 이 둘을 값으로 판별하면 정상적인
        #   0판매일이 매번 skipped에 잡혀 수집 건강 경보가 상시 거짓말을 한다.
        has_observation = "qty" in r or "revenue" in r
        qty = _int(r.get("qty"), None)
        revenue = _dec(r.get("revenue"), None, max_abs=_MAX_AMOUNT)
        # 값이 **적혀 있는데 못 읽은** 경우(쓰레기 문자열·상한 초과)는 빈 셀과 다르다. 0으로
        #   접으면 파싱 사고가 '0원 팔린 날'로 둔갑한다 → 행을 skip해서 눈에 보이게 남긴다.
        botched = (_present(r.get("qty")) and qty is None) or (
            _present(r.get("revenue")) and revenue is None
        )
        if not option_id or d is None or not has_observation or botched:
            skipped += 1
            continue
        accepted += 1
        # 필드별로 따로 센다: 컬럼명이 **한쪽만** 바뀌면 다른 쪽이 행을 살려 둘 다 빈 경우에만
        #   세는 카운터를 빠져나간다(그러면 '18개 팔렸는데 매출 0원'이 무신호로 쌓인다 —
        #   Inf를 사고로 분류한 것과 같은 이유). 한쪽만 전멸해도 잡히게 한다.
        # ★키가 있는데 값이 빈 경우만 센다. 키 자체를 안 보내는 건 그 페처의 선언된 모양이지
        #   사고가 아니다 — 그것까지 세면 qty만 보내는 배치마다 경보가 울려 경보가 죽는다.
        #   잡으려는 건 {"revenue": row.get("매출액")}가 컬럼명 변경으로 None을 뱉는 경우다.
        if "qty" in r and not _present(r.get("qty")):
            blank_qty += 1
        if "revenue" in r and not _present(r.get("revenue")):
            blank_rev += 1
        if not _present(r.get("qty")) and not _present(r.get("revenue")):
            blank_obs += 1
        out[(option_id, d)] = {
            "option_id": option_id,
            "date": d,
            "sku_id": _sid(r.get("sku_id"), 30),
            "qty": qty if qty is not None else 0,
            "revenue": revenue if revenue is not None else Decimal(0),
            "visitors": _int(r.get("visitors"), None),
            "conversion_rate": _dec(r.get("conversion_rate"), None, max_abs=_MAX_RATE),
            "product_name": _s(r.get("product_name"), 300),
            "source": _s(source, 20) or "sales_analysis",
        }
    deduped = accepted - len(out)
    if skipped:
        log.warning("1P 판매 레코드 skip %d건(option_id/date/관측값 누락)", skipped)
    if deduped:
        log.info("1P 판매 레코드 중복 흡수 %d건(같은 option_id×date — 뒤 행 우선)", deduped)
    # ★배치 수준 경보(행 수준 gate로는 못 잡는 것): 페처 매핑이 dict 리터럴로 짜이면
    #   {"qty": row.get("판매수량")} 처럼 **키는 항상 있고 값만 None**이 된다. 쿠팡이 컬럼명을
    #   바꾸면 행 단위로는 전부 합법(키 존재·빈 셀=0)이라 skipped=0으로 조용히 0원 테이블이
    #   쌓인다. 전 행이 빈 관측이면 그건 0판매일이 아니라 매핑 사고다 — 배치로 봐야 보인다.
    if accepted and (blank_qty == accepted or blank_rev == accepted):
        dead = "qty·revenue 전부" if blank_obs == accepted else (
            "qty 전부" if blank_qty == accepted else "revenue 전부")
        log.warning(
            "1P 판매: 배치 %d행에서 %s가 빈 값 — 0판매일이 아니라 페처 매핑 사고를 의심할 것"
            "(쿠팡 컬럼명 변경 등). 한쪽 필드만 전멸해도 나머지가 행을 살리므로 배치로만 보인다.",
            accepted, dead,
        )
    if stats is not None:
        stats["skipped"] = skipped
        stats["deduped"] = deduped
        stats["accepted"] = accepted
        stats["blank_observations"] = blank_obs
        stats["blank_qty"] = blank_qty
        stats["blank_revenue"] = blank_rev
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
            "share_ratio": _dec(r.get("share_ratio"), None, max_abs=_MAX_RATIO),
            "discount_method": _s(r.get("discount_method"), 40),
            "discount_value": _dec(r.get("discount_value"), None, max_abs=_MAX_DISCOUNT),
            "budget_amount": _dec(r.get("budget_amount"), None, max_abs=_MAX_AMOUNT),
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
        amount = _dec(r.get("used_amount"), None, max_abs=_MAX_AMOUNT)
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
