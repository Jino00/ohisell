# demand_classifier.py — RG 수요 형태 분류 SA (트랙 RG-Replenishment Phase 2 S8, D-14·X1)
# 단일 책임: 옵션별 일판매 시계열 → ADI(평균 발생간격)·CV²(수량 변동계수²)로
#            smooth/erratic/intermittent/lumpy 4분면 분류 + X1 진단 버킷.
# 읽기전용, 새 테이블 없음. 순수함수(ADI/CV²/분류)는 fixture로 경계값 잠금(R4).
#
# 핵심 설계 결정(Jino 승인 2026-06-18 "이대로 구현"):
#   ① ADI/CV² = Syntetos-Boylan(2005) 표준.
#        ADI = 관측 일수 ÷ 판매발생 일수(nonzero 일). CV² = (nonzero 수량 std ÷ nonzero 수량 mean)².
#        CV²는 0인 날 제외하고 nonzero 수량들로만 계산. std=모집단(ddof=0, statsforecast 관례·컷 0.49 정합).
#        컷: ADI 1.32 / CV² 0.49 → smooth/erratic/intermittent/lumpy.
#   ② unknown 게이트: nonzero 판매일 < 2이면 CV² 정의 불가 → 'unknown' 정직 표기.
#   ③ 리딩 제로: [TRUST_START, 어제] 전 구간을 0 포함해 카운트(옵션별 등록일 미보유) →
#        14일 단기 데이터에선 ADI가 다소 부풀 수 있음(lumpy/intermittent 쏠림). unknown 게이트로
#        노이즈 차단 + 한계 명시. S8은 진단 전용(돈 안 건드림)이라 허용(X1 정직성).
#   ④ X1 버킷(forecasting 도달범위 진단): trusted 일별 시계열의 nonzero 일수 기준.
#        zero_signal(0일=어떤 간헐예측도 불가) / sparse(1~소수) / active(규칙적). sold_30d는 참고만.
from __future__ import annotations

import logging
from datetime import date, timedelta
from statistics import pstdev

from sqlalchemy.orm import Session

from app.models import CoupangRgInventory, CoupangRgOrderItem
# TRUST_START는 RG 매출버그(paidDateTo 배타) 수정일 — 두 SA가 같은 신뢰기준일을 공유(드리프트 방지).
from app.services.coupang.sales_velocity_estimator import TRUST_START
from app.utils.kst import kst_today

log = logging.getLogger(__name__)

# ════════════════════════════════════════════════
# 상수 — Syntetos-Boylan(2005) 분류 컷 + 게이트
# ════════════════════════════════════════════════
ADI_CUT = 1.32   # 이상=간헐(발생간격 큼)
CV2_CUT = 0.49   # 이상=변동성 큼(수량 불규칙)
MIN_NONZERO_DAYS = 2  # CV²(표준편차) 정의 최소 표본 — 미만이면 unknown(②)
ACTIVE_MIN_NONZERO_DAYS = 7  # X1: nonzero 일수 ≥이면 active(규칙적). 튜닝 가능(④)

RG_ACCOUNTS = ["COUPANG_WING1", "COUPANG_WING2"]


# ════════════════════════════════════════════════
# 순수함수 — ADI / CV² / 분류 / 버킷 (fixture 테스트 대상, R4)
# ════════════════════════════════════════════════
def _adi(series: list[int]) -> float | None:
    """평균 발생간격 = 관측 일수 ÷ 판매발생 일수(nonzero). nonzero 0이면 None.

    Syntetos-Boylan 표준 추정량(p̂ = N / 수요발생수). 값↑ = 더 간헐적."""
    n = len(series)
    nonzero_days = sum(1 for q in series if q > 0)
    if nonzero_days == 0 or n == 0:
        return None
    return n / nonzero_days


def _cv2_nonzero(series: list[int]) -> float | None:
    """수량 변동계수² = (nonzero 수량 모집단std ÷ nonzero 수량 mean)². nonzero<2면 None.

    0인 날은 제외 — 수요가 *발생했을 때* 수량이 얼마나 들쭉날쭉한지(Syntetos-Boylan)."""
    nz = [q for q in series if q > 0]
    if len(nz) < MIN_NONZERO_DAYS:
        return None
    mean = sum(nz) / len(nz)
    if mean == 0:
        return None
    std = pstdev(nz)  # 모집단 표준편차(ddof=0) — statsforecast 관례·컷 0.49 정합
    return (std / mean) ** 2


def _classify(adi: float | None, cv2: float | None) -> str:
    """ADI·CV² → 4분면 라벨. 둘 중 하나라도 None이면 unknown(② 게이트)."""
    if adi is None or cv2 is None:
        return "unknown"
    intermittent = adi >= ADI_CUT
    erratic = cv2 >= CV2_CUT
    if not intermittent and not erratic:
        return "smooth"
    if not intermittent and erratic:
        return "erratic"
    if intermittent and not erratic:
        return "intermittent"
    return "lumpy"


def _bucket(nonzero_days: int) -> str:
    """X1 진단 버킷 — trusted 시계열 nonzero 일수 기준(forecasting 도달범위).

    zero_signal: 0일(어떤 간헐예측도 불가 — 설계상 insufficient 유지).
    sparse: 1~소수(S9 예측이 살릴 수 있는 모집단).
    active: ACTIVE_MIN_NONZERO_DAYS 이상(규칙적 수요·표준 방법)."""
    if nonzero_days == 0:
        return "zero_signal"
    if nonzero_days < ACTIVE_MIN_NONZERO_DAYS:
        return "sparse"
    return "active"


def _classify_series(series: list[int]) -> dict:
    """시계열 1개 → {adi, cv2, demand_class, bucket, nonzero_days, observed_qty}. 순수함수."""
    nonzero_days = sum(1 for q in series if q > 0)
    adi = _adi(series)
    cv2 = _cv2_nonzero(series)
    return {
        "adi": round(adi, 3) if adi is not None else None,
        "cv2": round(cv2, 3) if cv2 is not None else None,
        "demand_class": _classify(adi, cv2),
        "bucket": _bucket(nonzero_days),
        "nonzero_days": nonzero_days,
        "observed_qty": sum(series),
    }


# ════════════════════════════════════════════════
# 조회 (읽기 전용)
# ════════════════════════════════════════════════
def _load_daily_series(
    db: Session, since: date, until: date, account_key: str | None = None
) -> dict[str, list[int]]:
    """신뢰기간 [since, until] order_item을 옵션별 일별 판매수량 리스트(0 포함)로 집계.

    반환: {vii: [day0_qty, day1_qty, ...]} — 길이 = 기간 달력일수(모든 옵션 동일).
    paid_at NULL은 SQL에서 제외. 구간 밖 일자는 무시."""
    n_days = (until - since).days + 1
    if n_days <= 0:
        return {}

    q = db.query(
        CoupangRgOrderItem.vendor_item_id,
        CoupangRgOrderItem.paid_at,
        CoupangRgOrderItem.sales_quantity,
    ).filter(CoupangRgOrderItem.paid_at.isnot(None))
    if account_key:
        q = q.filter(CoupangRgOrderItem.account_key == account_key)

    series: dict[str, list[int]] = {}
    for vii, paid_at, qty in q.all():
        d = paid_at.date()
        if d < since or d > until:
            continue
        idx = (d - since).days
        slot = series.setdefault(str(vii), [0] * n_days)
        slot[idx] += int(qty or 0)
    return series


def _load_inventory_options(
    db: Session, account_key: str | None = None
) -> dict[str, int]:
    """재고 보유 옵션 전체(vii → sold_30d). 판매 0 옵션도 zero_signal로 분류하기 위해 모집단 확보.

    sold_30d는 참고치(버킷에 영향 없음, ④). NULL은 0으로."""
    q = db.query(CoupangRgInventory.vendor_item_id, CoupangRgInventory.sold_30d)
    if account_key:
        q = q.filter(CoupangRgInventory.account_key == account_key)
    return {str(v): int(s or 0) for v, s in q.all()}


# ════════════════════════════════════════════════
# 공통 컨텍스트
# ════════════════════════════════════════════════
def _trust_window() -> tuple[date, date, int]:
    """(since, until, n_days). until=어제(오늘은 부분일 → 제외, velocity와 동일)."""
    until = kst_today() - timedelta(days=1)
    n_days = (until - TRUST_START).days + 1
    return TRUST_START, until, n_days


# ════════════════════════════════════════════════
# 공개 API
# ════════════════════════════════════════════════
def classify_demand(db: Session, account_key: str | None = None) -> dict:
    """전체 옵션 수요형태 분류 + X1 버킷 진단 요약(UI/검증/엔드포인트용).

    재고 보유 옵션 전체를 모집단으로(판매 0 = zero_signal/unknown 정직 표기).
    반환: {trust_start, until, trust_days, cuts, options:{vii:{...}}, summary:{by_class, by_bucket, total}}."""
    since, until, n_days = _trust_window()
    series_map = _load_daily_series(db, since, until, account_key)
    inv = _load_inventory_options(db, account_key)

    viis = set(series_map.keys()) | set(inv.keys())
    options: dict[str, dict] = {}
    by_class: dict[str, int] = {}
    by_bucket: dict[str, int] = {}
    for vii in viis:
        series = series_map.get(vii, [0] * n_days if n_days > 0 else [])
        info = _classify_series(series)
        info["sold_30d"] = inv.get(vii, 0)  # 참고치(버킷 무영향)
        options[vii] = info
        by_class[info["demand_class"]] = by_class.get(info["demand_class"], 0) + 1
        by_bucket[info["bucket"]] = by_bucket.get(info["bucket"], 0) + 1

    return {
        "trust_start": since.isoformat(),
        "until": until.isoformat(),
        "trust_days": n_days,
        "cuts": {"adi": ADI_CUT, "cv2": CV2_CUT,
                 "min_nonzero_days": MIN_NONZERO_DAYS,
                 "active_min_nonzero_days": ACTIVE_MIN_NONZERO_DAYS},
        "options": options,
        "summary": {"by_class": by_class, "by_bucket": by_bucket, "total": len(options)},
    }


def classify_demand_one(
    db: Session, vendor_item_id: str, account_key: str | None = None
) -> dict | None:
    """단일 옵션 수요형태 분류 (Harness 미주입 시 직접 호출 — 원칙 18-8 등가성).

    재고·판매 둘 다 없으면 None. 있으면 {adi, cv2, demand_class, bucket, nonzero_days, observed_qty, sold_30d}."""
    vii = str(vendor_item_id)
    since, until, n_days = _trust_window()
    series_map = _load_daily_series(db, since, until, account_key)
    inv = _load_inventory_options(db, account_key)

    if vii not in series_map and vii not in inv:
        return None
    series = series_map.get(vii, [0] * n_days if n_days > 0 else [])
    info = _classify_series(series)
    info["sold_30d"] = inv.get(vii, 0)
    return info
