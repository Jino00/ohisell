# guardrail_gate.py — X1b T2 실행 직전 가드레일 순수 판정 SA (D-NAO-5/19/20, 계획서 §3 X1b).
# 역할(SA): 제안(proposal) + harness가 precompute한 라이브 상태(context) → 통과(None) 또는
#   사람이 읽을 차단사유(문자열). 부수효과 0(DB·API 접근 없음, bid_simulator와 동일 규율).
#   SA간 직접 호출 금지(원칙18) — harness(T4)가 재조회·집계값을 이 함수에 넘겨준다.
#
#   §4 "실행은 항상: ... → OPEN_ACTIONS → 가드레일 → 쓰기 → 재조회 검증 → change_log" 순서의
#   "가드레일" 단계 전체를 이 모듈이 담당한다. 클램프(70~100,000원·10원단위)는 naver_sa_writer
#   에도 있지만(이중 방벽 — add_restricted_keywords/execution_harness 패턴과 동일), 실제
#   API 호출 전에 여기서 먼저 걸러 무의미한 네트워크 호출을 막는다.
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.services.naver_ad import growth_sweeper
from app.services.naver_ad.bid_step_types import (
    BID_DOWN_TYPES as _BID_DOWN_TYPES,
    BID_UP_TYPES as _BID_UP_TYPES,
    CHANGE_PCT_EXEMPT_TYPES as _EXEMPT_FROM_CHANGE_PCT,
    RANK_STEP_TYPES as _RANK_STEP_TYPES,
)

# D-NAO-5: 회당 변경폭 상한(운영 키워드). D-NAO-20-③에 따라 신규/육성 트랙(growth_bid_up)은
# 비적용 — 절대액 스톱로스가 실질 안전장치이므로 여기서는 변경폭 검사만 면제한다.
# ★UP 타입·DOWN 타입·±15% 면제 집합은 bid_step_types 레지스트리(단일 소스, IU-R R0)에서 import.
_MAX_CHANGE_PCT = Decimal("0.15")

# D-NAO-19 "동일 키워드 재변경 최소 간격" — 초기값 5h(trigger_watch 재사용)에서
# ★D-NAO-55(2026-07-18 Jino 승인 "그렇게 하자")로 2h 단축. 근거: 순위 데이터가 시간 단위
# (hh24)라 변경 효과가 가중 순위에 반영되는 데 1~2시간 — 2h는 유령 신호(변경 전 데이터가
# 지배하는 판정)에 연타하지 않는 최소선. 그 밑은 측정 지연 때문에 민첩성이 아니라 진동을
# 산다(07-17 실사례: 하향 1시간 뒤 재판정 2.36은 대부분 하향 전 시간대 순위였음).
# 일일상한(_MAX_DAILY_CHANGES=3)이 총 이동폭의 2차 방어선으로 유지된다.
_COOLDOWN_HOURS = 2

# §4 "폭주 방지: 일일 변경 건수 상한" 신규 상수 — 문서에서 확인 안 됨(추정 금지 원칙 상
# 정직 라벨), 보수적 기본값. 키워드/광고그룹/캠페인 단위로 harness가 집계해 전달한다.
_MAX_DAILY_CHANGES = 3

_MIN_BID = 70
_MAX_BID = 100_000
_BID_INCREMENT = 10

_BID_TYPES = _BID_UP_TYPES | _BID_DOWN_TYPES
_LOCK_TYPES = frozenset({"pause", "resume"})

# DL3(D-NAO-65 ①, Jino 원문 "쭉 낮추다가"): 안전방향(bid_down = 노출↓·지출↓)만
# _MAX_DAILY_CHANGES에서 면제한다. 근거: 하향은 출혈을 줄이는 방향이라 일일 횟수 상한이
# 오히려 "쭉 낮추다가" 일일 스톱로스를 막는다 — 활동시간 ~16h/쿨다운 2h = 유닛당 하루
# 최대 ~8 하향 스텝(0.85⁸ ≈ 밴드입찰의 27%)까지 깊은 스로틀을 허용해야 loss 국면에서
# 노출/지출을 계속 깎아 내릴 수 있다. D-NAO-4 선례(빠른 루프의 안전방향 완화)와 동형.
# ★진동 방어는 쿨다운 2h(_COOLDOWN_HOURS)가 그대로 담당 — 면제되는 건 일일 횟수 상한
# 하나뿐이고, 쿨다운·±15% 클램프·방향검증·BEP·스톱로스는 전부 불변이다.
# 면제 범위는 canonical set _BID_DOWN_TYPES를 재사용(하드코딩 문자열 반복 금지) — bid_up·
# growth_bid_up·budget 계열·pause/resume은 상한 3을 그대로 유지한다.
_DAILY_CAP_EXEMPT_TYPES = _BID_DOWN_TYPES

# P2(D-NAO-42-f): 예산 통제 — PLAN_naver-ad-budget-control.md §5-C. bid_up/down과 병렬 구조.
_BUDGET_UP_TYPES = frozenset({"budget_up"})
_BUDGET_DOWN_TYPES = frozenset({"budget_down"})
_BUDGET_TYPES = _BUDGET_UP_TYPES | _BUDGET_DOWN_TYPES

# D-NAO-42-f③: 캠페인당 회당 변경폭 상한 = +100%(최대 2배). bid의 _MAX_CHANGE_PCT(±15%)와
# 별도 상수 — 예산은 Jino가 명시적으로 더 넓은 자율폭을 확정했다(계획서 §2②).
_BUDGET_UP_MAX_CHANGE_PCT = Decimal("1.0")


def check(proposal: dict, context: dict, *, now: datetime) -> str | None:
    """제안 1건의 실행 직전 가드레일 판정 — 통과 시 None, 차단 시 한국어 사유.

    proposal: {"proposal_type", "target_bid", "target_lock", "target_budget"} — NaverProposal
      에서 harness가 추출한 부분집합(원본 ORM 객체가 아닌 값 전달 — SA는 DB를 모른다).
    context: harness가 재조회·집계해 precompute한 라이브 상태.
      {"current_bid": int|None(재조회된 현재 입찰가), "current_budget": int|None(재조회된
       현재 일예산, P2), "roas_corrected": float|None(D-NAO-21 보정ROAS), "target_roas":
       float|None(BEP×공격성), "cost_today": int|None(오늘 누적 소진), "daily_budget": int|None,
       "unconverted_spend": int|None(무전환 누적 지출, 스톱로스 원료), "last_change_at":
       datetime|None(이 대상의 마지막 change_log 시각, proposal_type 무관), "changes_today_count":
       int(이 대상의 오늘 변경 건수)}.
    now: kst_now() — harness가 한 번만 계산해 명시 전달(원칙: 자정 경계 레이스 방지,
      F2b codex 수정과 동일 패턴).

    판정 순서: ①쿨다운·일일상한(전 유형 공통, fail-open 아님 — 근거값 없으면 통과) →
      ②유형별 검사(bid: 클램프→변경폭→[up만] 스톱로스→BEP→일예산 / lock: 구조 검증만 /
      budget: 클램프→방향→[up만] +100%캡→스톱로스→BEP, PLAN §5-C).
    """
    proposal_type = proposal.get("proposal_type")
    if (
        proposal_type not in _BID_TYPES
        and proposal_type not in _LOCK_TYPES
        and proposal_type not in _BUDGET_TYPES
    ):
        return f"guardrail_gate: 지원하지 않는 proposal_type={proposal_type!r}"

    reason = _check_cooldown_and_cap(context, now, proposal_type)
    if reason:
        return reason

    if proposal_type in _BID_TYPES:
        return _check_bid(proposal, context, proposal_type)
    if proposal_type in _BUDGET_TYPES:
        return _check_budget(proposal, context, proposal_type)
    return _check_lock(proposal, proposal_type)


def precheck_cooldown_and_cap(
    last_change_at: datetime | None, changes_today_count: int, now: datetime,
    proposal_type: str | None,
) -> str | None:
    """쿨다운·일일상한 사전점검(IU-R R2 공용 — auto_operator.run_hourly_lane의 rank-step
    prefilter가 estimate 호출 전 재사용). 임계·면제 규칙은 _check_cooldown_and_cap 단일 소스를
    그대로 태운다(중복 금지) — prefilter와 실행 시점 guardrail이 **같은 판정**을 공유한다.
    (차단사유, or None=통과). last_change_at/changes_today_count는 compute_change_cadence 산출."""
    return _check_cooldown_and_cap(
        {"last_change_at": last_change_at, "changes_today_count": changes_today_count},
        now, proposal_type,
    )


def _check_cooldown_and_cap(context: dict, now: datetime, proposal_type: str | None) -> str | None:
    # 쿨다운 2h는 전 유형 공통(DL3 면제 대상 아님) — 방향 무관하게 항상 검사.
    last_change_at = context.get("last_change_at")
    if last_change_at is not None:
        elapsed_hours = (now - last_change_at).total_seconds() / 3600
        if elapsed_hours < _COOLDOWN_HOURS:
            return (
                f"쿨다운 중 — 마지막 변경 {elapsed_hours:.1f}시간 전"
                f"(최소 {_COOLDOWN_HOURS}시간 필요, D-NAO-19)"
            )

    # DL3(D-NAO-65): 안전방향(bid_down)은 일일 횟수 상한에서만 면제 — "쭉 낮추다가"를
    # 위해서다. 진동 방어는 위 쿨다운 2h가 유지하고, 방향 불일치 stale 행(bid_down인데
    # target≥current)은 뒤이은 _check_bid의 방향검증이 여전히 fail-closed 차단한다.
    if proposal_type not in _DAILY_CAP_EXEMPT_TYPES:
        changes_today = context.get("changes_today_count", 0)
        if changes_today >= _MAX_DAILY_CHANGES:
            return f"일일 변경 건수 상한 도달({changes_today}/{_MAX_DAILY_CHANGES}건, 폭주 방지)"

    return None


def _check_bid(proposal: dict, context: dict, proposal_type: str) -> str | None:
    target_bid = proposal.get("target_bid")
    if target_bid is None:
        return "target_bid 없음 — 구조 결함(재생성 필요)"
    if not (_MIN_BID <= target_bid <= _MAX_BID) or target_bid % _BID_INCREMENT != 0:
        return f"target_bid={target_bid}는 유효 범위 밖(70~100,000원, 10원 단위)"

    current_bid = context.get("current_bid")
    if current_bid is None:
        return "current_bid 미확보 — 변경폭 검증 불가(fail-closed)"

    # codex[P2]: 구조 결함/stale 행 방어 — proposal_type이 주장하는 방향과 실제
    # target_bid의 방향이 어긋나면(예: bid_down인데 인상 방향) 절대변경폭 검사만으로는
    # 통과할 수 있고, bid_down은 up 전용 검사(스톱로스·BEP·일예산)를 면제받으므로 그
    # 방어망까지 우회한다. 방향 불일치는 magnitude 검사보다 먼저 fail-closed 차단.
    if proposal_type in _BID_UP_TYPES and target_bid <= current_bid:
        return f"방향 불일치 — {proposal_type}인데 target_bid={target_bid}가 현재={current_bid} 이하"
    if proposal_type in _BID_DOWN_TYPES and target_bid >= current_bid:
        return f"방향 불일치 — {proposal_type}인데 target_bid={target_bid}가 현재={current_bid} 이상"

    if proposal_type not in _EXEMPT_FROM_CHANGE_PCT and current_bid > 0:
        change_pct = abs(Decimal(target_bid) - Decimal(current_bid)) / Decimal(current_bid)
        if change_pct > _MAX_CHANGE_PCT:
            return (
                f"변경폭 {float(change_pct):.1%} 초과(상한 {float(_MAX_CHANGE_PCT):.0%}, D-NAO-5) "
                f"— 현재={current_bid}원 목표={target_bid}원"
            )

    if proposal_type in _BID_UP_TYPES:
        # IU-R R1 스톱로스 완화 방지(codex 엣지): rank-step 타입(bid_up_servo 등)은 스텝이
        # ±15%를 넘을 수 있어 target_bid 기준 스톱로스가 실질 완화된다(target_bid↑ → 상한↑).
        # rank-step은 **스텝 전 current_bid** 기준으로 산정해 더 보수적으로 막는다(PLAN §2 R1).
        # 비-rank-step(bid_up/growth_bid_up)은 종전 target_bid 기준 유지(행위 불변).
        stop_loss_base = current_bid if proposal_type in _RANK_STEP_TYPES else target_bid
        stop_loss_amount = stop_loss_base * growth_sweeper.STOP_LOSS_CLICK_MULTIPLE
        unconverted_spend = context.get("unconverted_spend")
        if unconverted_spend is not None and unconverted_spend >= stop_loss_amount:
            base_label = "current_bid" if proposal_type in _RANK_STEP_TYPES else "target_bid"
            return (
                f"스톱로스 도달 — 무전환 지출 {unconverted_spend}원 ≥ 상한 {stop_loss_amount}원"
                f"(D-NAO-20, {base_label}×{growth_sweeper.STOP_LOSS_CLICK_MULTIPLE})"
            )

        roas_corrected = context.get("roas_corrected")
        target_roas = context.get("target_roas")
        if roas_corrected is not None and target_roas is not None and roas_corrected < target_roas:
            return (
                f"BEP 미달 증액 금지 — 보정ROAS {roas_corrected} < 목표 {target_roas}"
                f"(D-NAO-1 안전선)"
            )

        cost_today = context.get("cost_today")
        daily_budget = context.get("daily_budget")
        # codex[P2]: dailyBudget=0은 budget_allocator 기존 관행(daily_budget>0 필수, D-3)상
        # "미설정"(useDailyBudget=false)을 뜻한다 — 0을 그대로 비교하면 uncapped 캠페인의
        # 정상 bid_up까지 항상 차단된다.
        if cost_today is not None and daily_budget is not None and daily_budget > 0 and cost_today >= daily_budget:
            return f"일예산 상한 불가침 — 오늘 누적 {cost_today}원 ≥ 일예산 {daily_budget}원"

    return None


def _check_budget(proposal: dict, context: dict, proposal_type: str) -> str | None:
    """캠페인 일예산(dailyBudget) 증액·감액 판정 (P2, D-NAO-42-f, PLAN §5-C).

    판정 순서(증액 budget_up): (a)클램프(양의 정수) → (b)current_budget 미확보 fail-closed →
      (b')current_budget<=0 fail-closed(Fix 4 — "미설정/무제한"에서 +100% 정의 불가) →
      (c)방향 일치 → (d)+100%캡(③, 캠페인당 최대 2배) → (e)스톱로스(⑤, 무전환 캠페인 증액
      금지) → (f)BEP 이익하한(④, roas_corrected/target_roas 둘 중 하나라도 None이면
      Fix 3로 fail-closed 차단, 아니면 보정ROAS<목표 차단). 감액(budget_down)은 (a)~(c)만 —
      +100%캡·스톱로스·BEP는 면제(감액은 자유, 계획서 §2⑥).

    ⚠️ "회당 총 증가액 ≤100,000원(라운드 합계)" 캡(계획서 §2②)은 여기서 검사하지 않는다 —
    이 함수는 캠페인 1건 스코프의 실행 직전 판정이라 "한 라운드에서 생성된 다른 제안들의
    합"을 볼 수 없다. 그 캡은 생성 단계(proposal_pipeline, P3)에서 budget_auto_eligible로
    분류한다(PLAN §5-E) — P2는 capability만, 라운드 봉투는 P3 스코프.
    """
    target_budget = proposal.get("target_budget")
    if (
        target_budget is None
        or not isinstance(target_budget, int)
        or isinstance(target_budget, bool)
        or target_budget <= 0
    ):
        return f"target_budget={target_budget!r}는 유효하지 않음(양의 정수 필요) — 구조 결함(재생성 필요)"

    current_budget = context.get("current_budget")
    if current_budget is None:
        return "current_budget 미확보 — 변경폭 검증 불가(fail-closed)"

    if proposal_type in _BUDGET_UP_TYPES:
        # Fix 4(codex P1): current_budget<=0("미설정/무제한" — dailyBudget=0은 budget_allocator
        # 관행상 useDailyBudget=false를 뜻함, _check_bid의 daily_budget=0 취급과 동일 근거)에서는
        # "+100%"가 애당초 정의 불가(0의 2배는 여전히 0). 아래 change_ratio 계산도 0으로 나누게
        # 되므로, 이 자리에서 명시적으로 fail-closed 차단한다(과거엔 `if current_budget > 0:`
        # 가드가 이 케이스를 조용히 건너뛰어 +100%캡이 무력화됐었다 — codex 지적).
        if current_budget <= 0:
            return (
                f"current_budget={current_budget}(미설정/무제한)에서 +100% 정의 불가 — fail-closed"
            )

        if target_budget <= current_budget:
            return (
                f"방향 불일치 — {proposal_type}인데 target_budget={target_budget}가 "
                f"현재={current_budget} 이하"
            )

        change_ratio = (Decimal(target_budget) - Decimal(current_budget)) / Decimal(current_budget)
        if change_ratio > _BUDGET_UP_MAX_CHANGE_PCT:
            return (
                f"예산 증액폭 {float(change_ratio):.0%} 초과(상한 100%, D-NAO-42-f③) "
                f"— 현재={current_budget}원 목표={target_budget}원"
            )

        # ⑤ 스톱로스 — bid의 상대배수 임계치(STOP_LOSS_CLICK_MULTIPLE)와 달리 매직넘버 없는
        # 제로 톨러런스 규칙: 캠페인 창 내 무전환 지출이 조금이라도 있으면 증액 자체를
        # 금지한다(D-NAO-42-f⑤, PLAN §5-C step5). 아래 BEP 이익하한(④)을 보완하는 별도
        # 안전장치 — BEP는 ROAS 미달을 잡고, 이건 "전환이 아예 0"인 더 강한 신호를 잡는다.
        unconverted_spend = context.get("unconverted_spend")
        if unconverted_spend is not None and unconverted_spend > 0:
            return (
                f"스톱로스 — 무전환 캠페인 증액 금지(무전환 지출 {unconverted_spend}원, "
                f"D-NAO-42-f⑤)"
            )

        roas_corrected = context.get("roas_corrected")
        target_roas = context.get("target_roas")
        # Fix 3(codex P1): 증거 없음(None) 자체를 fail-closed 차단한다 — 이건 _check_bid의
        # BEP 검사(증거 없으면 그냥 통과, fail-open)와 **의도적으로 다른** 동작이다. Jino가
        # BEP 이익하한을 예산까지 확장(D-NAO-42-f④)하면서, 예산은 입찰보다 더 무거운 레버(캠페인
        # 전체 지출 상한)라 증거 부재 상태에서까지 증액을 허용하지 않기로 확정했다 — 근거값을
        # 못 구했으면(재조회 실패·target_roas 미해결 등) 증액하지 않는 쪽이 안전하다는 판단.
        if roas_corrected is None or target_roas is None:
            return (
                f"BEP 검증 불가(fail-closed) — roas_corrected={roas_corrected!r} "
                f"target_roas={target_roas!r}(D-NAO-42-f④, _check_bid의 fail-open과 의도적으로 "
                "다른 stricter 게이트)"
            )
        if roas_corrected < target_roas:
            return (
                f"BEP 미달 증액 금지 — 보정ROAS {roas_corrected} < 목표 {target_roas}"
                f"(D-NAO-1 안전선)"
            )
    else:  # budget_down — 감액은 자유(⑥): 방향 검증 + 클램프만
        if target_budget >= current_budget:
            return (
                f"방향 불일치 — {proposal_type}인데 target_budget={target_budget}가 "
                f"현재={current_budget} 이상"
            )

    return None


def _check_lock(proposal: dict, proposal_type: str) -> str | None:
    target_lock = proposal.get("target_lock")
    if target_lock is None:
        return "target_lock 없음 — 구조 결함(재생성 필요)"
    if not isinstance(target_lock, bool):
        return f"target_lock은 bool이어야 함: {target_lock!r}"

    # codex[P2]: pause는 정지(target_lock=True)만, resume은 재개(target_lock=False)만
    # 유효하다 — 값이 반대면 구조 결함/stale 행이 실행기에 그대로 흘러가 의도와 반대
    # 액션(재개하려는데 정지)이 집행될 수 있다.
    expected = True if proposal_type == "pause" else False
    if target_lock != expected:
        return (
            f"방향 불일치 — {proposal_type}인데 target_lock={target_lock}"
            f"(기대값={expected})"
        )
    return None
