# revenue_canonical.py — 닫힌 과거일 매출 정본화 Harness (Wing 매출 정합 트랙 S2, D-1/D-9 A안).
#
# 목적(D-1): 닫힌 과거일(≤어제, Wing 판매분석 데이터 있는 날)의 **표시 매출**을 우리 주문기반(gross,
#   취소포함)이 아니라 **Wing GMV(net)** 로 정본화한다. 당일/실시간은 주문기반 유지(추정).
# A안(D-9): net_profit·기존 account.summary.revenue는 **불변**. 정본 매출은 별도 오버레이(읽기전용).
#   순이익 정밀 정합(수수료·원가)은 S4. → 가산적 변경, 기존 동작 회귀 0.
#
# 원칙18-6 허브: 두 SA 출력을 결합한다 —
#   ① intelligence._agg_orders/_agg_rg_orders (우리 주문기반 매출, 닫힌/당일 윈도우 각각)
#   ② vendor_summary_sync.get_vendor_summary_totals (Wing 공식 GMV, 닫힌 윈도우)
# 윈도우를 닫힌부분(≤어제)·당일부분으로 쪼개 닫힌부분만 Wing으로 교체, 옵션은 타입 팩터로 안분.
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import ROUND_FLOOR, Decimal

from sqlalchemy.orm import Session

from app.services.coupang import intelligence, vendor_summary_sync
from app.utils.kst import kst_today

log = logging.getLogger(__name__)

_Z = Decimal(0)


def _factor(canon: Decimal, ours: Decimal) -> Decimal:
    """표시용 안분 팩터 = Wing정본 / 우리닫힌. 우리닫힌=0이면 1(스케일 불가). 배분 자체는 _allocate."""
    if ours == _Z:
        return Decimal(1)
    return canon / ours


def _allocate(target: Decimal, weights: dict[str, Decimal]) -> dict[str, Decimal]:
    """target(정수 won)을 weights 비율로 정수 won 배분 — 최대잔여법(Σ배분==target 정확, codex P1#2).

    반복소수 팩터(예 1/3) 배분 시 Σ가 target과 미세하게 어긋나는 머니 불변식 위반을 차단한다.
    각 옵션 floor 후 남은 won을 소수부 큰 순서(동률은 vid 사전순 — 결정론적)로 1won씩 분배.
    가중치 합 0이면 전부 0(target은 호출부 summary가 보유 → apportion_residual로 가시화).
    """
    total_w = sum(weights.values(), _Z)
    if total_w == _Z or target == _Z:
        return {k: _Z for k in weights}
    raw = {k: target * w / total_w for k, w in weights.items()}
    floors = {k: v.to_integral_value(rounding=ROUND_FLOOR) for k, v in raw.items()}
    remainder = int(target - sum(floors.values(), _Z))
    order = sorted(weights, key=lambda k: (raw[k] - floors[k], k), reverse=True)
    out = dict(floors)
    for i in range(remainder):
        out[order[i % len(order)]] += 1
    return out


def combine_canonical(
    closed_our: dict[str, dict],
    open_our: dict[str, dict],
    wing: dict | None,
) -> dict:
    """A안 정본화 순수 계산 (DB 없음 — fixture 테스트 가능, D-12 머니룰).

    입력:
      closed_our / open_our: {vid: {"p3": Decimal, "rg": Decimal}} — 우리 주문기반 매출(옵션별·타입별).
        closed=닫힌 윈도우(≤어제), open=당일부분 윈도우.
      wing: {"gmv_3p": int, "gmv_rg": int} 또는 None — 닫힌 윈도우 Wing 공식 GMV(없으면 폴백).
    규칙(D-1/D-9):
      닫힌부분 매출 = Wing GMV(net). Wing 없으면 우리 주문기반 폴백(정본화 불가).
      당일부분 매출 = 우리 주문기반(불변).
      옵션 안분 = 닫힌옵션매출 × 타입팩터(Wing/우리닫힌) + 당일옵션매출.
    반환: summary(canonical_3p/rg/total, closed/open 분해, factor, wing_used) + by_option{vid:Decimal}.
    ★summary == Σby_option 불변식(codex P1#2): 닫힌 정본을 최대잔여법(_allocate)으로 정수 won 배분해
      반복소수 잔차 0. 닫힌옵션 가중치 합>0이면 정확히 성립. 우리닫힌=0인데 Wing>0이면(주문 없는 매출)
      옵션 귀속 불가 → by_option 합 < summary (apportion_residual로 가시화, silent 누락 방지).
    """
    our_closed_3p = sum((v.get("p3", _Z) for v in closed_our.values()), _Z)
    our_closed_rg = sum((v.get("rg", _Z) for v in closed_our.values()), _Z)
    open_3p = sum((v.get("p3", _Z) for v in open_our.values()), _Z)
    open_rg = sum((v.get("rg", _Z) for v in open_our.values()), _Z)

    wing_used = wing is not None
    if wing_used:
        canon_closed_3p = Decimal(str(wing.get("gmv_3p", 0)))
        canon_closed_rg = Decimal(str(wing.get("gmv_rg", 0)))
    else:
        canon_closed_3p = our_closed_3p
        canon_closed_rg = our_closed_rg

    factor_3p = _factor(canon_closed_3p, our_closed_3p)  # 표시용
    factor_rg = _factor(canon_closed_rg, our_closed_rg)

    # 옵션 안분(codex P1#2): 정본화 시 닫힌 정본을 옵션 닫힌매출 비율로 정수 won 배분(최대잔여법,
    # Σ==정본 정확). 폴백(wing 없음) 시엔 닫힌부분도 주문기반 그대로(반올림 없이 정확). 당일부분은 항상 그대로.
    if wing_used:
        alloc_3p = _allocate(canon_closed_3p, {v: c.get("p3", _Z) for v, c in closed_our.items()})
        alloc_rg = _allocate(canon_closed_rg, {v: c.get("rg", _Z) for v, c in closed_our.items()})
    else:
        alloc_3p = {v: c.get("p3", _Z) for v, c in closed_our.items()}
        alloc_rg = {v: c.get("rg", _Z) for v, c in closed_our.items()}
    by_option: dict[str, Decimal] = {}
    for vid in set(closed_our) | set(open_our):
        o = open_our.get(vid, {})
        by_option[vid] = (alloc_3p.get(vid, _Z) + alloc_rg.get(vid, _Z)
                          + o.get("p3", _Z) + o.get("rg", _Z))

    canonical_3p = canon_closed_3p + open_3p
    canonical_rg = canon_closed_rg + open_rg
    canonical_total = canonical_3p + canonical_rg
    by_option_sum = sum(by_option.values(), _Z)
    # 옵션 귀속 잔차(정상=0): 우리닫힌=0인데 Wing>0이면 옵션에 못 붙은 정본 매출.
    apportion_residual = canonical_total - by_option_sum

    return {
        "summary": {
            "canonical_3p": canonical_3p,
            "canonical_rg": canonical_rg,
            "canonical_total": canonical_total,
            "closed_3p": canon_closed_3p,
            "closed_rg": canon_closed_rg,
            "open_3p": open_3p,
            "open_rg": open_rg,
            "our_closed_3p": our_closed_3p,
            "our_closed_rg": our_closed_rg,
            "factor_3p": factor_3p,
            "factor_rg": factor_rg,
            "wing_used": wing_used,
            "apportion_residual": apportion_residual,
        },
        "by_option": by_option,
    }


def _agg_by_type(db: Session, dfrom: date, dto: date, acc: dict) -> dict[str, dict]:
    """우리 주문기반 매출을 옵션별·타입별로 집계 — {vid: {"p3", "rg"}}.

    기존 SA 재사용(원칙18-4): _agg_orders=3P(채널), _agg_rg_orders=RG(account_key).
    같은 vid가 3P·RG 양쪽이면 각각 보존(타입 팩터가 달라 분리 필수).
    """
    p3 = intelligence._agg_orders(db, dfrom, dto, acc["channel_ids"])
    rg = intelligence._agg_rg_orders(db, dfrom, dto, acc["account_key"])
    out: dict[str, dict] = {}
    for vid, o in p3.items():
        out.setdefault(vid, {"p3": _Z, "rg": _Z})["p3"] += o.get("revenue", _Z)
    for vid, o in rg.items():
        out.setdefault(vid, {"p3": _Z, "rg": _Z})["rg"] += o.get("revenue", _Z)
    return out


def compute_canonical_revenue(db: Session, dfrom: date, dto: date,
                              account: str | None = None) -> dict:
    """[dfrom,dto] 정본 매출 블록 — 닫힌부분=Wing GMV, 당일부분=주문기반(A안, D-1/D-9).

    읽기전용(net_profit·command-center revenue 불변). 라우터에서 str 직렬화(Decimal 보존).
    반환: {period, applicable, closed_through, coverage, summary{...}, by_option{vid:str}, note}.
      applicable=윈도우에 닫힌 과거일 존재. wing_used=Wing 데이터로 실제 정본화됨(없으면 주문 폴백).
      coverage.complete=닫힌 윈도우 전 날짜 Wing 적재(부분이면 정본 비권위, D-7 패턴).
    """
    acc = intelligence._resolve_account(db, account)
    yesterday = kst_today() - timedelta(days=1)
    closed_end = min(dto, yesterday)
    has_closed = closed_end >= dfrom
    # P1#1(codex): 미래/당일전용 윈도우에서 dfrom 밖 주문 혼입 방지 — open_start는 dfrom 이상으로 클램프.
    open_start = max(dfrom, closed_end + timedelta(days=1))
    has_open = dto >= open_start and dto > yesterday

    period: dict = {"from": dfrom.isoformat(), "to": dto.isoformat(),
                    "closed_through": closed_end.isoformat() if has_closed else None}
    if account is not None:
        period["account"] = account

    # 닫힌 윈도우 우리 매출 + Wing GMV.
    if has_closed:
        closed_our = _agg_by_type(db, dfrom, closed_end, acc)
        official = vendor_summary_sync.get_vendor_summary_totals(db, account, dfrom, closed_end)
        days_with_data = official["days_with_data"]
        expected_days = (closed_end - dfrom).days + 1
        # account=None(집계)은 계정 커버리지 미검증 → 비권위(reconcile D-7과 동일).
        complete = (account is not None) and (days_with_data >= expected_days)
        # P2#3(codex): 부분 적재면 Wing 합이 일부 날짜만 담겨 factor가 왜곡 → 매출 과소계상.
        #   complete(계정지정+전 날짜 적재)일 때만 정본화. 아니면 주문기반 폴백(과소계상 방지).
        wing = ({"gmv_3p": official["gmv_3p"], "gmv_rg": official["gmv_rg"]}
                if complete else None)
        coverage = {"expected_days": expected_days, "days_with_data": days_with_data,
                    "complete": complete, "last_refresh": official["last_refresh"]}
    else:
        closed_our, wing, coverage = {}, None, None

    open_our = _agg_by_type(db, open_start, dto, acc) if has_open else {}

    combined = combine_canonical(closed_our, open_our, wing)

    note = (
        "닫힌 과거일 매출=Wing 판매분석 GMV(net) 정본(D-1/A안). 당일부분=우리 주문기반(추정). "
        "net_profit·command-center.revenue는 불변(읽기전용 오버레이). "
        "옵션 분해=우리 주문 비율 안분(타입별 팩터)."
    )
    if not has_closed:
        note = "윈도우에 닫힌 과거일 없음(당일만) — 정본화 불가, 전량 주문기반. " + note
    elif not combined["summary"]["wing_used"]:
        # wing_used=False는 이제 'complete 아님'을 의미(P2#3): 미적재(0일) vs 부분/집계 구분해 안내.
        if coverage and coverage["days_with_data"] > 0:
            note = (f"⚠ Wing 부분 적재({coverage['days_with_data']}/{coverage['expected_days']}일) 또는 "
                    "집계뷰(계정 미지정) — 정본화 보류, 주문기반 폴백(과소계상 방지). 계정 지정+전 날짜 적재 시 정본화. "
                    + note)
        else:
            note = "⚠ 닫힌 윈도우 Wing 데이터 미적재 — 주문기반 폴백(정본화 안 됨). 페처 확인 필요. " + note

    return {
        "period": period,
        "applicable": has_closed,
        "closed_through": period["closed_through"],
        "coverage": coverage,
        "summary": combined["summary"],
        "by_option": combined["by_option"],
        "note": note,
    }
