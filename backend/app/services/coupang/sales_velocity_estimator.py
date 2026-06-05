# sales_velocity_estimator.py — RG 일판매속도 추정 SA (트랙 RG-Replenishment S3, D-3·D-6)
# 단일 책임: rg_order_item(읽기전용 일자별 판매) + rg_inventory.sold_30d(폴백)으로
#            옵션(vendor_item_id)별 일판매속도를 평일/주말/휴일 구간으로 추정.
# D-6(매일 고도화): 평일/주말/휴일 요일계수는 "신뢰도 게이트"로 자동 승격 —
#   글로벌 구간 관측일이 임계치를 넘으면 그 구간 계수 활성, 미달이면 1.0(구분 안 함, collecting).
# ★RG 매출버그(paidDateTo 배타적) 수정일(2026-06-04) 이전 order_item은 과소적재 → 신뢰표본 제외.
#   매일 sync로 깨끗한 일자가 1일씩 누적되며 정확도가 점진 향상.
from __future__ import annotations

import logging
from datetime import date, timedelta

import holidays
from sqlalchemy.orm import Session

from app.models import CoupangRgInventory, CoupangRgOrderItem
from app.utils.kst import kst_today

log = logging.getLogger(__name__)

# ════════════════════════════════════════════════
# 상수
# ════════════════════════════════════════════════
# RG 매출버그 수정일. 이전 일자 판매는 paidDateTo 배타 버그로 과소적재 → 신뢰표본 제외(트랙 D-6).
TRUST_START = date(2026, 6, 4)
# 구간별 요일계수를 활성화할 최소 관측 달력일수. 미달이면 factor=1.0(구분 안 함, collecting).
SEGMENT_MIN_DAYS = {"weekday": 8, "weekend": 4, "holiday": 2}
# 옵션 base_rate를 order_item으로 쓸 최소 신뢰기간 달력일수. 미달이면 sold_30d/30 폴백.
OPTION_MIN_DAYS = 14
SOLD_WINDOW = 30  # sold_30d 집계 창(쿠팡 명세 §3)
SEGMENTS = ("weekday", "weekend", "holiday")

# 한국 공휴일(음력 설·추석 포함). years 미지정 → 조회 연도 자동 확장.
_KR_HOLIDAYS = holidays.SouthKorea()


# ════════════════════════════════════════════════
# 요일 분류
# ════════════════════════════════════════════════
def _classify_day(d: date) -> str:
    """날짜 → 'holiday' | 'weekend' | 'weekday'. 공휴일 우선(토/일과 겹쳐도 holiday)."""
    if d in _KR_HOLIDAYS:
        return "holiday"
    if d.weekday() >= 5:  # 5=토, 6=일
        return "weekend"
    return "weekday"


def _segment_day_counts(since: date, until: date) -> dict[str, int]:
    """[since, until](양끝 포함) 기간의 구간별 달력 일수. since>until이면 전부 0."""
    counts = {s: 0 for s in SEGMENTS}
    d = since
    while d <= until:
        counts[_classify_day(d)] += 1
        d += timedelta(days=1)
    return counts


# ════════════════════════════════════════════════
# 조회 (읽기 전용)
# ════════════════════════════════════════════════
def _load_daily_sales(
    db: Session, since: date, until: date, account_key: str | None = None
) -> dict[str, dict[str, int]]:
    """신뢰기간 [since, until] order_item을 옵션별·구간별 판매수량 합으로 집계.

    반환: {vii: {weekday/weekend/holiday: qty}}. paid_at NULL은 SQL에서 제외."""
    q = db.query(
        CoupangRgOrderItem.vendor_item_id,
        CoupangRgOrderItem.paid_at,
        CoupangRgOrderItem.sales_quantity,
    ).filter(CoupangRgOrderItem.paid_at.isnot(None))
    if account_key:
        q = q.filter(CoupangRgOrderItem.account_key == account_key)

    by_option: dict[str, dict[str, int]] = {}
    for vii, paid_at, qty in q.all():
        d = paid_at.date()
        if d < since or d > until:
            continue
        seg = _classify_day(d)
        slot = by_option.setdefault(str(vii), {s: 0 for s in SEGMENTS})
        slot[seg] += int(qty or 0)
    return by_option


def _load_sold30(db: Session, account_key: str | None = None) -> dict[str, int]:
    """옵션별 sold_30d(최근30일 판매수). NULL/음수 제외."""
    q = db.query(CoupangRgInventory.vendor_item_id, CoupangRgInventory.sold_30d)
    if account_key:
        q = q.filter(CoupangRgInventory.account_key == account_key)
    return {str(v): int(s) for v, s in q.all() if s is not None and s > 0}


# ════════════════════════════════════════════════
# 추정 핵심
# ════════════════════════════════════════════════
def _segment_factors(
    global_seg_qty: dict[str, int], seg_days: dict[str, int]
) -> tuple[dict[str, dict], float]:
    """글로벌 구간별 일평균 ÷ 전체 일평균 = 요일계수. 표본 임계 미달 구간은 1.0(collecting).

    반환: ({seg: {factor·sample_days·confidence·min_days}}, overall_daily_rate)."""
    total_qty = sum(global_seg_qty.values())
    total_days = sum(seg_days.values())
    overall_rate = total_qty / total_days if total_days else 0.0

    factors: dict[str, dict] = {}
    for seg in SEGMENTS:
        days = seg_days[seg]
        if days >= SEGMENT_MIN_DAYS[seg] and overall_rate > 0:
            factor = round((global_seg_qty[seg] / days) / overall_rate, 3)
            conf = "ok"
        else:
            factor = 1.0
            conf = "collecting"
        factors[seg] = {
            "factor": factor,
            "sample_days": days,
            "confidence": conf,
            "min_days": SEGMENT_MIN_DAYS[seg],
        }
    return factors, overall_rate


def _option_base_rate(
    opt_seg_qty: dict[str, int], total_days: int, sold30: int | None
) -> tuple[float, str]:
    """옵션 일판매속도 기본값(옵션 단위 — 글로벌 합산 폴백은 쓰지 않음, codex S3 리뷰 반영).

    폴백 순서: ① 관측일 충분+판매>0 → order_item(최고신뢰) ② sold_30d>0 → 30일집계
    ③ 관측일 짧아도 신뢰기간 실판매 있음 → order_item_low(신상품 안전망) ④ 데이터 전무 → none."""
    opt_total = sum(opt_seg_qty.values())
    if total_days >= OPTION_MIN_DAYS and opt_total > 0:
        return round(opt_total / total_days, 3), "order_item"
    if sold30 is not None and sold30 > 0:
        return round(sold30 / SOLD_WINDOW, 3), "sold_30d"
    if opt_total > 0 and total_days > 0:  # sold_30d 비었지만 신뢰기간 실판매 존재(신상품/sync 타이밍)
        return round(opt_total / total_days, 3), "order_item_low"
    return 0.0, "none"


def _option_estimate(
    opt_seg_qty: dict[str, int], total_days: int, sold30: int | None, factors: dict[str, dict]
) -> dict:
    """옵션 1개 추정: base_rate × 요일계수 = 구간별 예상 일판매수."""
    base_rate, base_source = _option_base_rate(opt_seg_qty, total_days, sold30)
    segments = {seg: round(base_rate * factors[seg]["factor"], 3) for seg in SEGMENTS}
    return {
        "base_rate": base_rate,
        "base_source": base_source,
        "segments": segments,
        "observed_qty": sum(opt_seg_qty.values()),
    }


def _compute_context(db: Session, account_key: str | None):
    """공통 컨텍스트 산출(신뢰기간·일자분류·판매집계·요일계수). 전체/단일 추정이 공유."""
    until = kst_today() - timedelta(days=1)  # 오늘은 부분일 → 제외
    seg_days = _segment_day_counts(TRUST_START, until)
    by_option = _load_daily_sales(db, TRUST_START, until, account_key)
    sold30 = _load_sold30(db, account_key)
    global_seg = {s: 0 for s in SEGMENTS}
    for slot in by_option.values():
        for s in SEGMENTS:
            global_seg[s] += slot[s]
    factors, overall_rate = _segment_factors(global_seg, seg_days)
    return until, seg_days, by_option, sold30, factors, overall_rate


# ════════════════════════════════════════════════
# 공개 API
# ════════════════════════════════════════════════
def estimate_sales_velocities(db: Session, account_key: str | None = None) -> dict:
    """전체 옵션 일판매속도 추정 + 글로벌 요일계수(UI/검증/엔드포인트용).

    옵션 base_rate = order_item(관측일 충분) → sold_30d/30 폴백. 요일계수는 글로벌 신뢰도 게이트."""
    until, seg_days, by_option, sold30, factors, overall_rate = _compute_context(db, account_key)
    total_days = sum(seg_days.values())

    viis = set(by_option.keys()) | set(sold30.keys())
    options: dict[str, dict] = {}
    for vii in viis:
        opt_seg = by_option.get(vii, {s: 0 for s in SEGMENTS})
        est = _option_estimate(opt_seg, total_days, sold30.get(vii), factors)
        if est["base_rate"] > 0 or est["observed_qty"] > 0:
            options[vii] = est

    return {
        "trust_start": TRUST_START.isoformat(),
        "until": until.isoformat(),
        "trust_days": total_days,
        "segment_days": seg_days,
        "segment_factors": factors,
        "global_daily_rate": round(overall_rate, 3),
        "options": options,
        "option_min_days": OPTION_MIN_DAYS,
    }


def estimate_sales_velocity(
    db: Session, vendor_item_id: str, account_key: str | None = None
) -> dict | None:
    """단일 옵션 일판매속도 추정 (S4 replenishment_calc가 호출 — 원칙 18-8 optional 입력 설계).

    옵션 order_item/sold_30d로 base_rate 산출. 둘 다 없으면 None(글로벌 합산 폴백은 차원오류라 미사용).
    반환: {base_rate·base_source·segments(요일별 예상 일판매수)·segment_factors·observed_qty·trust_days}."""
    vii = str(vendor_item_id)
    _until, seg_days, by_option, sold30, factors, _overall = _compute_context(db, account_key)
    total_days = sum(seg_days.values())

    opt_seg = by_option.get(vii, {s: 0 for s in SEGMENTS})
    est = _option_estimate(opt_seg, total_days, sold30.get(vii), factors)

    # ★order_item·sold_30d 둘 다 없으면 추정 불가 → None. 글로벌 합산(overall_rate)을 단일 옵션
    #   prior로 쓰면 포트폴리오 전체속도를 옵션1개에 부여하는 차원 오류 → S4 과발송(codex S3 리뷰 반영).
    #   데이터 전무 옵션은 정직하게 '추정 불가'로 두고 S4/Jino가 수동 판단(D-4).
    if est["base_source"] == "none":
        return None

    est["segment_factors"] = factors
    est["trust_days"] = total_days
    return est
