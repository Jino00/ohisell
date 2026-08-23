# accel_gate_view.py — 「액셀이 게이트에서 얼마나 죽는가」 표면 (D-NAO-232, 계약 §4-④)
#
# ══ 왜 만드는가 ══
# 북극성 §7 원문: *"자동화는 브레이크(차단·정지)가 액셀(확장)보다 만들기 쉬워서 방치하면
# 반드시 ROAS 방어로 기운다 … 자동화 범위를 넓힐 때마다 「액셀·브레이크가 대칭인가」를
# 검사 항목으로 둘 것."* 그런데 그 검사가 **화면에 없어서** 세션마다 사람이 curl로 다시
# 셌다(세션 39 배포 전 실측 · 이 세션 §5 드라이런). 재계수가 매번 손으로 일어나면
# 그건 검사 항목이 아니라 «그때 생각난 사람이 하는 일»이다.
#
# ══ 무엇을 재나 ══
# BEP 증액금지 게이트(`guardrail_gate._check_bid`)는 `roas_corrected < target_roas`면 액셀을
# 막는다. 그리고 그 `roas_corrected`는 **구간 자의 하한**을 쓴다(D-NAO-230,
# `naver_execution_harness.py:926·1005·1093`). 하한을 쓰면 보정이 사라져 차단이 최대로 걸린다 —
# 「액셀=하한」이 «크기»에선 보수화지만 «게이트»에선 **차단 증가**로 뒤집히는 자리다(ref 94 §6).
# 그래서 이 표면은 **양끝을 나란히** 낸다: 하한에서 몇 건이 죽고, 상한이었으면 몇 건이었나.
#
# ══ 안 하는 것 ══
# - **판정하지 않는다.** 게이트를 바꾸지도, 후보를 거르지도 않는다 — 관측 전용이다.
# - **DB를 안 읽는다.** 이미 만들어진 boards 위에서만 센다(추가 쿼리 0).
# - roas_naver가 없는 행을 «통과»로 세지 않는다 — `unmeasurable`로 따로 센다.
#   발견 0건과 측정 못 함은 같은 숫자로 쓰지 않는다(교훈 #123).
from __future__ import annotations

from typing import Any, Iterable, Sequence

# 액셀·브레이크 보드 집합 — ★세션 39(ref 93 §2)와 **같은 집합**을 primary로 쓴다.
# 비교 가능해야 하기 때문이다(세션 39 기준선: 브레이크 665 · 액셀 220 · 3.023:1).
ACCEL_BOARDS: tuple[str, ...] = ("starving_winners", "shopping_group_growth")
BRAKE_BOARDS: tuple[str, ...] = ("bleeding_keywords", "shopping_group_bep")

# 정지·재개 보드는 «확장 정의»로만 센다(오늘 전부 0건이라 primary를 흔들지 않지만,
# 0이 아닌 날 비율이 조용히 달라지는 것을 막으려면 어느 집합인지 표면에 적혀 있어야 한다).
ACCEL_BOARDS_EXT: tuple[str, ...] = ACCEL_BOARDS + ("resume_candidates", "shopping_resume_candidates")
BRAKE_BOARDS_EXT: tuple[str, ...] = BRAKE_BOARDS + ("shopping_pause_candidates",)

ASSUMPTION = (
    "보정계수의 분자에 광고 귀속 조인이 없어 「채널 매출 100%를 광고가 견인」 가정과 동치다"
    " — 그래서 총이익을 구간 양끝으로 병기한다(D-NAO-230)."
)
GATE_NOTE = (
    "액셀 게이트(BEP 증액금지)는 구간의 «하한»을 쓴다 — 하한은 보정을 없애 차단을 최대로 만든다."
)
# ★적대 리뷰 1R P2-2: 이 수치는 «보드 창»의 roas_naver로 잰 근사다. 실제 게이트는
#   `account_diagnosis.keyword_window_agg`(as_of=D-1)의 다른 창을 쓴다 — 양끝이 같은
#   roas_naver를 쓰므로 «차이»(막힌 건수·부호)는 정확하고 절대 건수는 근사다.
#   자백을 페이로드에 실어야 화면이 확정값처럼 보이지 않는다(ref 94 §8-3).
WINDOW_CAVEAT = (
    "보드 창 기준 근사 — 실제 게이트는 as_of=D-1 창을 쓴다. 양끝의 «차이»는 정확하고 절대 건수는 근사."
)


def _rows(boards: dict[str, Any], names: Iterable[str]) -> list[dict]:
    out: list[dict] = []
    for n in names:
        v = boards.get(n)
        if isinstance(v, list):
            out.extend(r for r in v if isinstance(r, dict))
    return out


def _f(row: dict, key: str) -> float | None:
    v = row.get(key)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _profit(conv_amt: float, cost: float, factor: float, bep_roas: float) -> float:
    """총이익 = (Σconv_amt × factor) ÷ bep_roas − Σcost.

    ★산식 정본은 `profit_scorecard.py:133`이다 — 여기서 새로 만들지 않는다.
    두 곳에 산식을 적으면 한쪽만 고쳐지는 날이 온다."""
    return conv_amt * factor / bep_roas - cost


def _bucket(rows: Sequence[dict], factor_low: float, factor_high: float,
            target_of, bep_roas: float) -> dict:
    """액셀 후보를 게이트 판정으로 세 통에 가른다 + 통마다 총이익 양끝.

    ★`target_of(row) -> float`는 **행마다** 목표ROAS를 준다 — 계정 기본값 하나가 아니다.
    실제 게이트(`naver_execution_harness._build_guardrail_context`)가
    `_resolve_target_roas_float(db, proposal.campaign_id)`로 **캠페인별** 목표를 넣기 때문이다.
    계정 기본값 하나로 재면 화면이 실제 게이트와 다른 그룹을 지목한다(적대 리뷰 1R P1-1:
    라이브에서 3그룹이 빠지고 3그룹이 새로 들어왔고, 하한 총이익이 −10,636 ↔ −17,691로 66% 어긋났다).
    """
    passing: list[dict] = []      # 양끝 모두 통과
    low_only: list[dict] = []     # 하한에서만 차단(= 현행 게이트가 죽이는 것)
    both: list[dict] = []         # 양끝 모두 차단
    unmeasurable = 0
    targets_seen: list[float] = []

    for r in rows:
        roas = _f(r, "roas_naver")
        if roas is None:
            unmeasurable += 1
            continue
        target_roas = target_of(r)
        targets_seen.append(target_roas)
        blocked_low = roas * factor_low < target_roas
        blocked_high = roas * factor_high < target_roas
        if blocked_low and blocked_high:
            both.append(r)
        elif blocked_low:
            low_only.append(r)
        else:
            passing.append(r)

    def agg(part: Sequence[dict]) -> dict:
        cost = sum(_f(r, "cost") or 0.0 for r in part)
        conv = sum(_f(r, "conv_amt") or 0.0 for r in part)
        return {
            "count": len(part),
            "cost": round(cost),
            "conv_amt": round(conv),
            "profit_high": round(_profit(conv, cost, factor_high, bep_roas)),
            "profit_low": round(_profit(conv, cost, factor_low, bep_roas)),
        }

    return {
        "passing_both": agg(passing),
        "blocked_low_only": agg(low_only),
        "blocked_both": agg(both),
        "unmeasurable": unmeasurable,
        "target_roas_min": round(min(targets_seen), 4) if targets_seen else None,
        "target_roas_max": round(max(targets_seen), 4) if targets_seen else None,
    }


def build(boards: dict[str, Any] | None, *, factor_low: float, factor_high: float,
          target_roas: float | None, bep_roas: float | None,
          resolve_target_roas=None) -> dict | None:
    """진단 응답에 실을 「액셀 게이트」 관측 페이로드. 재료가 없으면 None(0으로 위장 금지).

    resolve_target_roas: `campaign_id -> target_roas` 리졸버(override > 상품파생 > 계정기본값).
      **주면 행마다 캠페인별 목표를 쓴다** — 실제 게이트와 같은 값을 써야 같은 그룹을 지목한다.
      안 주면 `target_roas`(계정 기본값)로 폴백하되, 그때는 페이로드가 그 사실을 밝힌다
      (`target_roas_source`) — 조용히 다른 자로 재고 화면엔 확정값처럼 그리는 것이 P1-1이었다.
    """
    if not boards or target_roas is None or bep_roas is None or not bep_roas:
        return None

    def target_of(row: dict) -> float:
        cid = row.get("campaign_id")
        if resolve_target_roas is not None and isinstance(cid, str) and cid:
            resolved = resolve_target_roas(cid)
            if resolved is not None:
                return float(resolved)
        return target_roas

    accel = _rows(boards, ACCEL_BOARDS)
    brake_n = len(_rows(boards, BRAKE_BOARDS))
    accel_ext_n = len(_rows(boards, ACCEL_BOARDS_EXT))
    brake_ext_n = len(_rows(boards, BRAKE_BOARDS_EXT))

    b = _bucket(accel, factor_low, factor_high, target_of, bep_roas)
    accel_n = len(accel)
    survive_low = b["passing_both"]["count"]
    survive_high = survive_low + b["blocked_low_only"]["count"]

    def ratio(brake: int, acc: int) -> float | None:
        return round(brake / acc, 3) if acc else None

    # 보드별 내역 — 어느 보드에서 죽는지가 안 보이면 처분을 못 정한다.
    by_board = []
    for name in ACCEL_BOARDS_EXT:
        rows = _rows(boards, (name,))
        sub = _bucket(rows, factor_low, factor_high, target_of, bep_roas)
        by_board.append({
            "board": name,
            "total": len(rows),
            "blocked_low_only": sub["blocked_low_only"]["count"],
            "blocked_both": sub["blocked_both"]["count"],
            "unmeasurable": sub["unmeasurable"],
        })

    return {
        "gate_end": "factor_low",
        "gate_note": GATE_NOTE,
        "window_caveat": WINDOW_CAVEAT,
        "assumption": ASSUMPTION,
        "factor_low": factor_low,
        "factor_high": factor_high,
        "target_roas": target_roas,
        # ★적대 리뷰 1R P1-1 상환 — 어느 자로 쟀는지 페이로드가 밝힌다.
        "target_roas_source": "per_campaign" if resolve_target_roas is not None else "account_default",
        "target_roas_min": b["target_roas_min"],
        "target_roas_max": b["target_roas_max"],
        "bep_roas": bep_roas,
        "accel_total": accel_n,
        "brake_total": brake_n,
        "accel_total_ext": accel_ext_n,
        "brake_total_ext": brake_ext_n,
        "survive_low": survive_low,
        "survive_high": survive_high,
        "ratio_selection": ratio(brake_n, accel_n),
        "ratio_after_gate_low": ratio(brake_n, survive_low),
        "ratio_after_gate_high": ratio(brake_n, survive_high),
        "buckets": b,
        "by_board": by_board,
    }
