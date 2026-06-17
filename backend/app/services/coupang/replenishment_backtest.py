# replenishment_backtest.py — RG 발송관제 P4 백테스트 Harness (트랙 D-18 게이트②)
# 단일 책임: 파는 옵션의 발송추천 정책이 과거에 좋았는지를 walk-forward로 채점한다(읽기전용, 머니 무영향).
#
# 데이터 제약(원칙22): rg_inventory는 현재 스냅샷뿐(과거 재고 없음) → 재고 깊이 replay 불가.
#   대신 "과거 시점 D까지 데이터로 산정한 목표재고 S가, D 이후 보호구간 실제수요 A를 덮었나(품절)
#   + 얼마나 남겼나(과잉)"를 검증(target-vs-demand, D-결정-A).
#
# 충실성(plan-eng-review A1): 목표재고 S는 라이브 엔진과 동일한 compute_target_level를 호출하고,
#   base_rate는 S3 _option_base_rate(임계 로직 동일)를 재사용 → '실제 프로덕션 정책'을 검증.
#   단 sold_30d는 현재 스냅샷이라 시간 역행 불가(D-결정-B) → sold30=None으로 호출(order_item-only).
#   base_source=="order_item" 옵션은 백테스트=프로덕션 동일 예측, sold_30d 의존 옵션은 base 부족→skip+표기.
#
# 흐름:
#   _load_daily_series(전 구간 1회) ─┐  슬라이스로 학습[≤D]·미래수요[D+1..D+H] 둘 다 산출(원칙18-8)
#   estimate_lead_times(1회)         ├─→ 옵션×cutoff마다 _score_window
#   compute_target_level(공유)       ─┘
from __future__ import annotations

import logging
import math
from datetime import timedelta

from sqlalchemy.orm import Session

from app.models import CoupangProductItem
from app.services.coupang.demand_classifier import _load_daily_series, _load_inventory_options
from app.services.coupang.lead_time_estimator import estimate_lead_times
from app.services.coupang.replenishment_calc import (
    DEFAULT_REVIEW_PERIOD_DAYS,
    compute_target_level,
)
from app.services.coupang.sales_velocity_estimator import (
    SEGMENTS,
    TRUST_START,
    _option_base_rate,
)
from app.utils.kst import kst_today

log = logging.getLogger(__name__)

DEFAULT_TRAIN_MIN_DAYS = 7   # D 이전 최소 학습 달력일수(평일/주말 신호 확보 최소선)
INDICATIVE_MIN_WINDOWS = 10  # 전체 valid 윈도우가 이 미만이면 "indicative_only"(데이터 부족, 은폐 금지)
_DEFAULT_LEAD_MEAN = 2.16    # 글로벌 리드 표본 전무 시 폴백(라이브 관측 기반, lead_time_estimator와 동일)
_DEFAULT_LEAD_P90 = 3.0


# ════════════════════════════════════════════════
# 한 윈도우 채점 (순수함수)
# ════════════════════════════════════════════════
def _score_window(target_level: float, base_rate: float, actual_demand: float) -> dict:
    """한 (옵션, cutoff) 윈도우 채점.

    target_level = 주문상한 S(compute_target_level), actual_demand = 보호구간 실제수요 A.
    - stockout ⟺ A > S (목표가 실현수요 못 덮음).
    - overstock_days = A < S일 때 (S − A) / base_rate (윈도우끝 잔여를 일수로 환산, 보유 근사). 그 외 0.
    base_rate>0 보장(호출부가 none/0 skip)이나 방어적으로 0이면 None."""
    stockout = actual_demand > target_level
    if base_rate > 0:
        overstock_days = max(0.0, (target_level - actual_demand) / base_rate)
    else:
        overstock_days = None
    return {
        "stockout": stockout,
        "overstock_days": (round(overstock_days, 2) if overstock_days is not None else None),
        "target_level": round(target_level, 2),
        "actual_demand": actual_demand,
    }


# ════════════════════════════════════════════════
# base_rate(as-of-D) — S3 임계 로직 재사용, sold_30d 배제(D-결정-B)
# ════════════════════════════════════════════════
def _base_rate_asof(total_qty: int, total_days: int) -> tuple[float, str]:
    """[TRUST_START, D] order_item 합·달력일수로 base_rate 산출.

    S3 _option_base_rate를 재사용(OPTION_MIN_DAYS 임계·order_item_low 안전망 동일)하되
    sold30=None(백테스트는 현재 스냅샷 sold_30d를 과거에 못 씀, D-결정-B).
    base_rate는 segment 분해와 무관(합만 사용)하므로 단일 버킷 어댑터로 충분."""
    opt_seg = {SEGMENTS[0]: total_qty}
    opt_seg.update({s: 0 for s in SEGMENTS[1:]})
    return _option_base_rate(opt_seg, total_days, None)


# ════════════════════════════════════════════════
# 공개 API (Harness 본체) — 라우터가 이것만 호출(원칙18-7, 다중 SA 가로지름)
# ════════════════════════════════════════════════
def run_backtest(
    db: Session,
    account_key: str | None = None,
    *,
    train_min_days: int = DEFAULT_TRAIN_MIN_DAYS,
    protection_mode: str = "full",
    review_period: int = DEFAULT_REVIEW_PERIOD_DAYS,
) -> dict:
    """파는 옵션 walk-forward 백테스트.

    protection_mode: 'full'(H=ceil(p90리드)+review_period≈10) / 'lead_only'(H=ceil(p90리드)≈3, 얇은 데이터용).
    반환: {generated_at·params·summary·options}. valid_windows를 항상 표기(원칙22 은폐 금지)."""
    if protection_mode not in ("full", "lead_only"):
        protection_mode = "full"

    today = kst_today()
    last_complete = today - timedelta(days=1)  # 오늘은 부분일 → 제외
    n_days = (last_complete - TRUST_START).days + 1

    params = {
        "train_min_days": train_min_days,
        "protection_mode": protection_mode,
        "review_period": review_period,
        "trust_start": TRUST_START.isoformat(),
        "last_complete": last_complete.isoformat() if n_days > 0 else None,
    }
    if n_days < train_min_days + 1:
        # 학습 최소 + 최소 1일 미래도 불가 → 윈도우 0(정직).
        return {
            "generated_at": today.isoformat(), "account_key": account_key, "params": params,
            "summary": {"options_attempted": 0, "options_covered": 0, "inventory_options_total": None,
                        "total_valid_windows": 0, "mean_fill_rate": None, "mean_overstock_days": None,
                        "indicative_only": True,
                        "note": "신뢰데이터 부족(학습 최소일수+미래 1일 미만) — 백테스트 윈도우 없음.",
                        "methodology": "데이터 누적 후 재실행."},
            "options": [],
        }

    # 전 구간 일별 판매(0 포함) 1회 로드 → 학습·미래 슬라이스 공유(원칙18-8). 키 = 판매 이력 있는 옵션.
    series = _load_daily_series(db, TRUST_START, last_complete, account_key)
    # 모집단 분모(P2-4): 백테스트 대상=판매이력 옵션만. 전체 재고옵션 대비 커버리지 착시 방지.
    inventory_total = len(_load_inventory_options(db, account_key))

    # 글로벌 리드(옵션 표본 적어 글로벌 주력 — S2 결과). 보호구간 H 산정.
    g = estimate_lead_times(db, account_key).get("global") or {}
    mean_L = float(g.get("mean", _DEFAULT_LEAD_MEAN))
    p90_L = float(g.get("p90", _DEFAULT_LEAD_P90))
    lead_days = math.ceil(p90_L)
    horizon = lead_days + review_period if protection_mode == "full" else lead_days

    names = _load_names(db, list(series.keys()))

    options_out: list[dict] = []
    for vii, daily in series.items():
        windows: list[dict] = []
        skipped = {"no_velocity": 0, "incomplete_window": 0}
        # cutoff D: 학습 최소일수 확보(idx >= train_min_days-1) ~ 미래 윈도우 완전(idx+H <= n_days-1)
        for idx_d in range(train_min_days - 1, n_days):
            end_idx = idx_d + horizon
            if end_idx > n_days - 1:
                skipped["incomplete_window"] += 1
                continue
            total_qty = sum(daily[: idx_d + 1])
            total_days_d = idx_d + 1
            base_rate, base_source = _base_rate_asof(total_qty, total_days_d)
            if base_source == "none" or base_rate <= 0:
                skipped["no_velocity"] += 1
                continue
            actual = sum(daily[idx_d + 1 : end_idx + 1])
            target_level, _safety = compute_target_level(base_rate, mean_L, p90_L, review_period)
            w = _score_window(target_level, base_rate, actual)
            w["cutoff"] = (TRUST_START + timedelta(days=idx_d)).isoformat()
            w["base_source"] = base_source
            w["base_rate"] = round(base_rate, 3)
            windows.append(w)

        product_name, item_name = names.get(vii, (None, None))
        valid = len(windows)
        if valid == 0:
            options_out.append({
                "vendor_item_id": vii, "product_name": product_name, "item_name": item_name or vii,
                "valid_windows": 0, "stockout_windows": 0, "skipped_windows": skipped,
                "fill_rate": None, "mean_overstock_days": None, "base_source": None,
            })
            continue
        stockouts = sum(1 for w in windows if w["stockout"])
        overs = [w["overstock_days"] for w in windows
                 if not w["stockout"] and w["overstock_days"] is not None]
        options_out.append({
            "vendor_item_id": vii, "product_name": product_name, "item_name": item_name or vii,
            "valid_windows": valid, "stockout_windows": stockouts, "skipped_windows": skipped,
            "fill_rate": round(1 - stockouts / valid, 3),
            "mean_overstock_days": round(sum(overs) / len(overs), 2) if overs else 0.0,
            "base_source": windows[-1]["base_source"],  # 최신 cutoff 기준(참고)
            "windows": windows,
        })

    # 긴급/관심 순: 품절 많은 옵션 먼저, valid 없는 옵션 뒤로.
    options_out.sort(key=lambda o: (-(o["stockout_windows"]), -(o["valid_windows"]), o["vendor_item_id"]))

    covered = [o for o in options_out if o["valid_windows"] > 0]
    total_valid = sum(o["valid_windows"] for o in options_out)
    mean_fill = (round(sum(o["fill_rate"] for o in covered) / len(covered), 3) if covered else None)
    mean_over = (round(sum(o["mean_overstock_days"] for o in covered) / len(covered), 2)
                 if covered else None)
    indicative = total_valid < INDICATIVE_MIN_WINDOWS
    note = (f"valid 윈도우 {total_valid}개(판매이력 옵션 {len(covered)}/{len(options_out)} 채점, "
            f"전체 재고옵션 {inventory_total}개 중 판매이력만 대상) — "
            + ("데이터 부족, 지표는 참고용(indicative). 누적되면 자동 결론화."
               if indicative else "충분 표본."))
    # 방법론 정직성(P2-1·P2-2): full 모드 비대칭·정적 근사 한계를 명시해 self-verify 오판 방지.
    methodology = (
        "fill_rate=수요예측 적정성 프록시(목표재고 S vs 보호구간 실제수요 A). 라이브 동적 조기발송·"
        "요일인지 소진을 정적 S-vs-A로 축약하므로 발송 정확도 자체가 아님. "
        "★full 모드는 목표재고 S가 검토주기 R일만 budget(=compute_target_level)인데 노출 horizon은 "
        "R+리드라 완벽 예측에도 구조적 품절↑(보수적 스트레스뷰). lead_only가 정책-목표 정합 — "
        "정책 적정성은 lead_only fill_rate로 읽을 것."
    )

    return {
        "generated_at": today.isoformat(),
        "account_key": account_key,
        "params": {**params, "horizon_days": horizon, "lead_mean": round(mean_L, 2),
                   "lead_p90": round(p90_L, 2)},
        "summary": {
            "options_attempted": len(options_out),
            "options_covered": len(covered),
            "inventory_options_total": inventory_total,
            "total_valid_windows": total_valid,
            "mean_fill_rate": mean_fill,
            "mean_overstock_days": mean_over,
            "indicative_only": indicative,
            "note": note,
            "methodology": methodology,
        },
        "options": options_out,
    }


# ════════════════════════════════════════════════
# 상품명 조회 (UI 표시용, 1회)
# ════════════════════════════════════════════════
def _load_names(db: Session, viis: list[str]) -> dict[str, tuple[str | None, str | None]]:
    """vii → (seller_product_name, item_name). rg_replenishment와 동일 패턴."""
    if not viis:
        return {}
    rows = (
        db.query(
            CoupangProductItem.vendor_item_id,
            CoupangProductItem.seller_product_name,
            CoupangProductItem.item_name,
        )
        .filter(CoupangProductItem.vendor_item_id.in_(viis))
        .all()
    )
    return {str(r[0]): (r[1], r[2]) for r in rows}
