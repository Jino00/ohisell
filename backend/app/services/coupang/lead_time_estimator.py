# lead_time_estimator.py — RG 입고 리드타임 추정 SA (트랙 RG-Replenishment S2, D-1·D-2)
# 단일 책임: coupang_rg_inbound(읽기 전용)에서 발송→판매개시 리드타임 분포를 산출.
# 출력: 옵션(vendor_item_id)별 {count·mean·p50·p90·min·max·latest} + 글로벌 분포.
# 폴백(D): 옵션 표본 < MIN_SAMPLES → 글로벌 분포로 폴백(source="global"). 글로벌도 없으면 None.
# 안전재고(D-2): mean=기대 리드, p90=보수적 리드(변동성 흡수). S4 replenishment_calc가 둘 다 사용.
# ★lead_time_days NULL(판매개시 미도달 입고)은 분포에서 제외(S1에서 NULL로 적재).
from __future__ import annotations

import logging

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models import CoupangRgInbound

log = logging.getLogger(__name__)

# 옵션 표본이 이 미만이면 글로벌 분포로 폴백(라이브 실측: 옵션당 표본 1~2개가 대부분 → 옵션 단독 추정 빈약).
MIN_SAMPLES = 2


# ════════════════════════════════════════════════
# 통계 헬퍼 (numpy 의존 없음 — 순수 구현)
# ════════════════════════════════════════════════
def _percentile(sorted_vals: list[float], q: float) -> float:
    """정렬된 표본의 q분위수(0~100). 선형보간(numpy 기본 'linear'와 동일). 빈 리스트 가드는 호출측."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    rank = (q / 100.0) * (n - 1)
    lo = int(rank)
    frac = rank - lo
    if lo + 1 >= n:
        return sorted_vals[-1]
    return sorted_vals[lo] + frac * (sorted_vals[lo + 1] - sorted_vals[lo])


def _summarize(samples: list[tuple[float, object]]) -> dict | None:
    """(lead_days, stowing_at) 표본 리스트 → 분포 요약. 표본 0개면 None.

    latest = stowing_at(판매개시 시각) 최신 표본의 lead — 최근 추세 반영용.
    stowing_at이 전부 None이어도 mean/p50 등은 산출(latest만 None)."""
    if not samples:
        return None
    leads = sorted(s[0] for s in samples)
    n = len(leads)
    # 최신 표본(stowing_at 기준). None은 뒤로 밀어 유효값 우선.
    dated = [s for s in samples if s[1] is not None]
    latest = max(dated, key=lambda s: s[1])[0] if dated else None
    return {
        "count": n,
        "mean": round(sum(leads) / n, 2),
        "p50": round(_percentile(leads, 50), 2),
        "p90": round(_percentile(leads, 90), 2),
        "min": round(leads[0], 2),
        "max": round(leads[-1], 2),
        "latest": round(latest, 2) if latest is not None else None,
    }


# ════════════════════════════════════════════════
# 조회 (읽기 전용 — coupang_rg_inbound)
# ════════════════════════════════════════════════
def _load_samples(
    db: Session, account_key: str | None = None
) -> dict[str, list[tuple[float, object]]]:
    """옵션(vendor_item_id)별 (lead_days, stowing_at) 표본 수집. lead NULL은 SQL에서 제외."""
    q = db.query(
        CoupangRgInbound.vendor_item_id,
        CoupangRgInbound.lead_time_days,
        CoupangRgInbound.stowing_at,
    ).filter(CoupangRgInbound.lead_time_days.isnot(None))
    if account_key:
        q = q.filter(CoupangRgInbound.account_key == account_key)

    by_option: dict[str, list[tuple[float, object]]] = {}
    for vii, lead, stow in q.all():
        if lead is None:
            continue
        by_option.setdefault(str(vii), []).append((float(lead), stow))
    return by_option


def estimate_lead_times(db: Session, account_key: str | None = None) -> dict:
    """전체 옵션의 리드타임 추정 + 글로벌 분포. UI/검증/엔드포인트용.

    반환: {
      "global": {count·mean·p50·p90·min·max·latest} | None,
      "options": { vii: {...summary, "source": "option"|"global"} },
      "min_samples": MIN_SAMPLES,
    }
    옵션 표본 < MIN_SAMPLES → 글로벌로 폴백(source="global"). 글로벌도 None이면 그 옵션은 제외."""
    by_option = _load_samples(db, account_key)
    all_samples: list[tuple[float, object]] = [s for lst in by_option.values() for s in lst]
    global_summary = _summarize(all_samples)

    options: dict[str, dict] = {}
    for vii, samples in by_option.items():
        if len(samples) >= MIN_SAMPLES:
            summary = _summarize(samples)
            summary["source"] = "option"
            options[vii] = summary
        elif global_summary is not None:
            options[vii] = {**global_summary, "source": "global"}
        # 옵션 표본 부족 + 글로벌도 없음 → 제외(추정 불가)

    return {
        "global": global_summary,
        "options": options,
        "min_samples": MIN_SAMPLES,
    }


def estimate_lead_time(
    db: Session, vendor_item_id: str, account_key: str | None = None
) -> dict | None:
    """단일 옵션 리드타임 추정 (S4 replenishment_calc가 호출 — 원칙 18-8 optional 입력 설계).

    옵션 표본 ≥ MIN_SAMPLES → 옵션 추정, 미만 → 글로벌 폴백, 둘 다 없으면 None."""
    vii = str(vendor_item_id)
    by_option = _load_samples(db, account_key)
    samples = by_option.get(vii, [])
    if len(samples) >= MIN_SAMPLES:
        summary = _summarize(samples)
        summary["source"] = "option"
        return summary
    # 폴백: 글로벌(전체 옵션 합산)
    all_samples = [s for lst in by_option.values() for s in lst]
    global_summary = _summarize(all_samples)
    if global_summary is None:
        return None
    return {**global_summary, "source": "global"}
