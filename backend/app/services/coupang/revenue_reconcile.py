# revenue_reconcile.py — 매출 대조 Harness (Wing 세션 자동화 트랙 S2, D-2/D-3).
#
# 원칙18-2/18-6 Harness: 두 SA의 출력을 결합하는 허브.
#   ① compute_command_center (우리 계산 매출: revenue_3p / revenue_rg)
#   ② vendor_summary_sync.get_vendor_summary_totals (쿠팡 공식 GMV: NORMAL=3P / RFM=RG)
#   → 닫힌 과거일(D-3)만 같은 윈도우로 비교해 드리프트% 산출. 정합성 트랙의 "수동 1:1 대조"(ref 18)를
#     자동 드리프트 감시로 전환한다.
#
# 불변(D-2): 사실·지표·드리프트만(전략 추천 없음). 읽기전용 — net_profit 등 기존 종합조망 값 불변.
# D-3: 당일은 sync 시차로 부정확 → 닫힌 과거일(어제까지)만 대조. 윈도우를 closed_end로 클램프해
#   우리 매출도 같은 닫힌 윈도우로 재계산(apples-to-apples).
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.services.coupang import vendor_summary_sync
from app.services.coupang.intelligence import compute_command_center
from app.utils.kst import kst_today

log = logging.getLogger(__name__)

_Z = Decimal(0)


def _drift_pct(ours: Decimal, official: Decimal) -> Decimal | None:
    """드리프트% = (우리 − 쿠팡)/쿠팡 × 100. 쿠팡=0이면 비율 미정(None) — 0 나눗셈 방지."""
    if official == _Z:
        return None
    return (ours - official) / official * Decimal(100)


def reconcile_revenue(db: Session, dfrom: date, dto: date,
                      account: str | None = None) -> dict:
    """우리 매출(revenue_3p/rg)과 쿠팡 공식 GMV(vendor-summary)를 닫힌일 드리프트%로 대조.

    윈도우를 닫힌 과거일(어제까지)로 클램프(D-3) 후 양측을 같은 윈도우로 비교한다.
    반환(Decimal은 라우터에서 str 직렬화):
      {period:{from,to,closed_through,account?}, has_closed_days, has_official,
       official:{gmv_3p,gmv_rg,gmv_total,days_with_data,last_refresh},
       ours:{revenue_3p,revenue_rg,revenue_total},
       drift:{abs_3p,abs_rg,abs_total, pct_3p,pct_rg,pct_total}, note}
    drift.pct_*는 해당 쿠팡 GMV가 0이면 None. net_profit 등 기존 값은 건드리지 않는다(읽기전용).
    """
    yesterday = kst_today() - timedelta(days=1)
    closed_end = min(dto, yesterday)           # 닫힌일 상한(어제) — 당일 제외(D-3)
    has_closed_days = closed_end >= dfrom
    expected_days = (closed_end - dfrom).days + 1 if has_closed_days else 0

    period: dict = {
        "from": dfrom.isoformat(),
        "to": dto.isoformat(),
        "closed_through": closed_end.isoformat() if has_closed_days else None,
    }
    if account is not None:
        period["account"] = account

    if not has_closed_days:
        # 요청 윈도우에 닫힌 과거일이 없음(예: 오늘만) → 대조 불가.
        return {
            "period": period,
            "has_closed_days": False,
            "has_official": False,
            "coverage": None,
            "official": None,
            "ours": None,
            "drift": None,
            "note": "요청 기간에 닫힌 과거일이 없어 대조 생략(당일은 sync 시차로 제외, D-3).",
        }

    # 우리 매출 — 닫힌 윈도우로 재계산(쿠팡과 같은 기간). compute_command_center는 읽기전용.
    cc = compute_command_center(db, dfrom, closed_end, account)
    csum = cc["account"]["summary"]
    ours_3p = csum["revenue_3p"]
    ours_rg = csum["revenue_rg"]
    ours_total = ours_3p + ours_rg

    # 쿠팡 공식 GMV — 같은 닫힌 윈도우.
    official = vendor_summary_sync.get_vendor_summary_totals(db, account, dfrom, closed_end)
    off_3p = Decimal(official["gmv_3p"])
    off_rg = Decimal(official["gmv_rg"])
    off_total = Decimal(official["gmv_total"])

    # ★커버리지(codex P1): 페처가 닫힌 윈도우 전 날짜를 다 적재했는지. 부분 적재(예: 6일 중 3일만)면
    # 우리 전체 윈도우 매출 vs 쿠팡 부분 GMV를 비교해 드리프트가 허위로 커진다. 날짜 그레인으로 판정
    # (등록유형별 결측은 '그날 그 유형 판매 0'과 모호 — 쿠팡이 0 판매 유형의 행을 생략하므로 갭으로 안 봄).
    # complete=False면 드리프트는 참고치이며 권위값 아님(소비자가 부분을 전체로 오인하지 않도록 명시).
    #
    # ★집계(account=None) 뷰는 권위 판정 불가(codex P1 round2): ours는 전 계정 매출 합인데 official은
    #   official 데이터가 있는 계정만 합산한다. 매출은 있는데 vendor-summary 미적재인 계정(예: 페처가
    #   로그인 안 한 계정)이 있으면 날짜 수만으로는 그 누락을 못 잡는다 → 집계는 complete=False로 고정
    #   (드리프트는 참고치로 계산은 함). 정합 권위 판정은 계정 지정(COUPANG_WING1/2) 대조로만.
    days_with_data = official["days_with_data"]
    if account is None:
        complete = False
    else:
        complete = days_with_data >= expected_days
    coverage = {
        "expected_days": expected_days,
        "days_with_data": days_with_data,
        "complete": complete,
    }

    drift = {
        "abs_3p": ours_3p - off_3p,
        "abs_rg": ours_rg - off_rg,
        "abs_total": ours_total - off_total,
        "pct_3p": _drift_pct(ours_3p, off_3p),
        "pct_rg": _drift_pct(ours_rg, off_rg),
        "pct_total": _drift_pct(ours_total, off_total),
    }
    note = (
        "드리프트% = (우리−쿠팡)/쿠팡. 닫힌 과거일만 대조(D-3). 사실·지표만(D-2). "
        "잔차 해석: 3P 잔여 stale 취소(D-5), RG gross-vs-net(D-11)."
    )
    if account is None:
        note = (
            "⚠ 집계(전체) 뷰 — 계정별 official 커버리지 미검증이라 권위값 아님(드리프트는 참고치). "
            "정합 판정은 계정 지정(COUPANG_WING1/2) 대조로. " + note
        )
    elif not complete:
        note = (
            f"⚠ 부분 적재({days_with_data}/{expected_days}일) — 드리프트는 참고치(권위값 아님). "
            "쿠팡 공식 데이터가 닫힌 윈도우 전 날짜에 적재돼야 정합 판정 가능. " + note
        )
    return {
        "period": period,
        "has_closed_days": True,
        "has_official": days_with_data > 0,
        "coverage": coverage,
        "official": official,
        "ours": {
            "revenue_3p": ours_3p,
            "revenue_rg": ours_rg,
            "revenue_total": ours_total,
        },
        "drift": drift,
        "note": note,
    }
