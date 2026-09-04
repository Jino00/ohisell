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
    COLD_START_STEP_TYPES as _COLD_START_STEP_TYPES,
    EXPLORATION_STEP_TYPES as _EXPLORATION_STEP_TYPES,
    RANK_STEP_TYPES as _RANK_STEP_TYPES,
)

# D-NAO-5: 회당 변경폭 상한(운영 키워드). D-NAO-20-③에 따라 신규/육성 트랙(growth_bid_up)은
# 비적용 — 절대액 스톱로스가 실질 안전장치이므로 여기서는 변경폭 검사만 면제한다.
# ★UP 타입·DOWN 타입·±15% 면제 집합은 bid_step_types 레지스트리(단일 소스, IU-R R0)에서 import.
_MAX_CHANGE_PCT = Decimal("0.15")

# B-X BX2(D-NAO-71): 탐색 스텝(EXPLORATION_STEP_TYPES)의 변경폭 상한 = 30%. "15%는 순위 실이동에
# 너무 작음"(Jino 원문) — 목적은 순위가 실제로 오르내리는 크기. ★완전 면제가 아니라 상한을
# 15%→30%로 바꾼 것뿐 — 30% 초과는 여전히 fail-closed 차단(탐색 폭주 방지). 잔여 가격 브레이크 =
# product_bep 연동 경제성 상한(exploration.exploration_ceiling, BX3 레인에서 target_bid를 상한까지
# 클램프). exploration.py의 _EXPLORATION_STEP_PCT(0.30)와 값이 일치해야 한다(정합 테스트로 고정).
_EXPLORATION_MAX_CHANGE_PCT = Decimal("0.30")

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
# VT4 D-NAO-82①(codex 1R P1-1): campaign_type 인지형 입찰 하한. SHOPPING adgroup grain은 50원
# 유효입찰이 라이브(prod 158그룹 bid=50 실증, 2026-07-22 라이브 해부). _MIN_BID(70)은 파워링크
# 키워드 grain·bid_simulator._MIN_BID 규격 출처로 보존 — context["campaign_type"]가 SHOPPING이
# 아니거나 미확보(WEB_SITE·BRAND_SEARCH·None)면 70원(보수 fail-closed) 유지. 70원 하한을 쇼핑에
# 적용해 50원 그룹의 60원 explore 발사가 "유효 범위 밖"으로 영구 차단되던 결함 수정.
_MIN_BID_SHOPPING = 50
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

# ★D-NAO-125 codex[P1] 2026-07-29 — DL3 면제의 전제가 바뀌었다.
# DL3은 하향이 **사람 Confirm을 거치는 동안** 설계됐다: 8스텝을 쭉 낮추더라도 매 스텝을
# 사람이 봤다. 소재 하향 자동 실행이 열리면 그 눈이 사라져, 판정이 틀린 채 반복되면
# 아무도 안 보는 사이 하루 8스텝(0.85⁸ ≈ 27%)까지 파고든다 — 800원짜리가 220원이 되면
# 그 소재는 사실상 노출에서 사라진다("지출이 주는 방향"이 안전하다는 논리가 성립하지 않는
# 지점: 매출도 같이 0이 된다).
# 그래서 **자동 실행 경로에만** 별도 일일 상한을 건다. 사람이 승인한 하향은 종전 DL3 면제
# 그대로다(Jino 원문 "쭉 낮추다가"의 원래 맥락이 유지된다).
# 3스텝 = 0.85³ ≈ 하루 최대 -39%. 그 이상 필요하면 사람이 콘솔에서 이어서 내린다.
_MAX_DAILY_AUTO_BID_DOWNS = 3

# ★D-NAO-129 codex 적대[P1] — 자동 상향의 **누적** 상한(기준가 대비 배수).
# 상향의 유일한 경제적 브레이크인 "BEP 미달 증액 금지"는 소재가 아니라 **부모 광고그룹의
# 30일 집계**로 판정한다. 같은 그룹에 매출을 만드는 소재가 하나라도 있으면 전환 0인 소재도
# 그 실적을 빌려 계속 통과하고, 그러면 남는 절대 상한은 네이버 API 최대값(100,000원)뿐이다
# — ±15%×하루 3회는 복리라 800원이 약 12일이면 거기 닿는다. "언제 멈추나"에 답하는 값이
# 코드에 없었다는 것이 이 상수의 존재 이유다.
# 2.0배에서 멈추고 사람에게 넘긴다(사람 승인 상향은 auto_exec=False라 이 상한 대상이 아니다 —
# 더 올릴 근거가 있으면 사람이 콘솔에서 올리고, 그 값이 새 기준점이 된다).
_MAX_AUTO_UP_MULTIPLE = Decimal("2.0")
# 이 상한이 걸리는 타입 = D-NAO-129가 개방한 성과 상향뿐. harness._AD_UP_OPEN_TYPES와
# 같은 값이어야 한다(그 모듈은 이 모듈을 import 하므로 역방향 import를 피해 값으로 고정 —
# 정합은 테스트로 지킨다: test_cumulative_cap_types_match_ad_up_open_types).
_CUMULATIVE_CAP_TYPES: frozenset[str] = frozenset({"bid_up"})
# ★D-NAO-286 — 표본 하한 게이트(북극성 M2 S2-ⓑ · `ref 65` §6 S2 목표 ⓑ의 집행).
# 값 15는 **`ref 33` [6] 원문(최근 30일 15전환 ≈ 주 15건, tROAS 활성화 최소)**이고 `ref 65` §4
# 판정표가 이미 게이트 상수로 지목한 수다 — 이 세션이 발명한 숫자가 아니다.
#
# ★왜 두 개인가: `ref 65` 원문은 **캠페인** grain만 적었는데, 2026-09-04 실측으로 그것만으론
#   막히지 않는 것이 확인됐다 — PAO가 실제로 도는 두 캠페인이 주 174.1·47.4전환으로 **둘 다
#   통과**한다. 정작 사고(2026-09-01, Jino가 `auto_operate`를 끈 사건)는 **소재 grain**에서
#   났다: 이틀·14클릭 표본으로 입찰을 +49% 올렸다. ⇒ Jino 결정(2026-09-04) 「캠페인 + 대상그룹
#   둘 다, 양방향 차단」. 계약 `docs/contracts/CONTRACT_sample_floor_gate.md`.
#
# ★왜 «양방향»인가: 증액만 막으면 하향은 계속 돌아 브레이크만 남는다 — 그게 D-NAO-85 실측
#   (ROAS +7% · 매출 −52%)이 나온 바로 그 모양이고 북극성 §7이 매번 검사하라고 못 박은 축이다.
#   이 게이트의 문장은 「표본이 없으면 **아무 판단도 하지 않는다**」이지 「올리지 마라」가 아니다.
_MIN_WEEKLY_CONV_CAMPAIGN = 15
_MIN_WEEKLY_CONV_TARGET = 15
# ★게이트 대상 = **성과를 «판단»하는 레인만**(Jino 결정 2026-09-04 「탐색·콜드는 면제」).
#   탐색(bid_up_explore)·콜드스타트(bid_up_cold)는 «표본이 없는 유닛에 표본을 만들러 가는»
#   레인이라 전환 하한을 걸면 **입찰→노출→클릭→전환 길이 막혀 하한을 영원히 못 넘는다**
#   (닫힌 고리). ★이 면제는 이 저장소의 선례와 같다 — 이익하한 게이트 자리에 이미
#   *「표본 없는 그룹에 표본-기반 이익하한 강제 = 탐색 원천 봉쇄」*(PLAN §1 가드6)라 적혀 있다.
#   그 둘은 자체 상한이 따로 있다: 경제성 ceiling(exploration_ceiling·cold_ceiling) · 한 스텝
#   30% · 쿨다운 2h. 그리고 2026-09-01 사고는 explore가 아니라 `auto_op_hr`의 `bid_up`이었다.
_FLOOR_GATED_TYPES: frozenset[str] = (
    _BID_TYPES - _EXPLORATION_STEP_TYPES - _COLD_START_STEP_TYPES
)

# 외부변경 확인 판정값(harness._EXT_* 와 같은 값 — 역방향 import 회피, 정합은 테스트로 고정).
_EXT_CHECK_OURS = "ours"
_EXT_CHECK_EXTERNAL = "external"

# P2(D-NAO-42-f): 예산 통제 — PLAN_naver-ad-budget-control.md §5-C. bid_up/down과 병렬 구조.
_BUDGET_UP_TYPES = frozenset({"budget_up"})
_BUDGET_DOWN_TYPES = frozenset({"budget_down"})
_BUDGET_TYPES = _BUDGET_UP_TYPES | _BUDGET_DOWN_TYPES

# D-NAO-42-f③: 캠페인당 회당 변경폭 상한 = +100%(최대 2배). bid의 _MAX_CHANGE_PCT(±15%)와
# 별도 상수 — 예산은 Jino가 명시적으로 더 넓은 자율폭을 확정했다(계획서 §2②).
_BUDGET_UP_MAX_CHANGE_PCT = Decimal("1.0")


# ── D-NAO-172 P1: 봉투 파라미터를 «컨텍스트에서» 읽는다 ─────────────────────────
# 왜 컨텍스트인가: 이 모듈은 순수 판정기라 DB를 모른다(SA는 DB를 모른다 — 위 docstring 규약).
# harness가 `_build_guardrail_context`에서 `guardrail_params`를 실어 주고, 없으면 **코드 상수**로
# 떨어진다(fail-to-current). 그래서 이 배선은 «컨텍스트를 안 채우는 호출부»를 깨뜨리지 않는다 —
# 기존 테스트·직접 호출 경로가 종전 그대로 돈다는 뜻이고, 그게 P1이 행위 변화 0인 이유다.
def _param(context: dict, key: str, code_default):
    params = context.get("guardrail_params")
    if not isinstance(params, dict):
        return code_default
    val = params.get(key)
    return code_default if val is None else val


def _check_data_floor(context: dict, proposal_type: str) -> str | None:
    """D-NAO-286 표본 하한 — 정착창 주간 전환이 하한 미달이면 자동 입찰 변경을 **양방향 모두**
    차단(사유 문자열), 통과면 None.

    적용 경계 넷(계약 §3 금지선 그대로):
      ⓪ `_FLOOR_GATED_TYPES`(성과 판단 레인 5종)가 아니면 적용 안 함 — 탐색·콜드는 면제다
         (위 집합 주석의 «닫힌 고리» 근거).
      ① `auto_exec`가 아니면 적용 안 함 — **사람 승인(console) 경로는 종전 그대로**다.
         사람은 표본이 얇은 것을 알고도 누를 수 있고, 이 게이트가 막으려는 것은 무인 판정이다.
      ② `floor_exempt`면 적용 안 함 — **되돌리기·출혈 정지 레인**이다(손실 고삐 하향·탐침
         되돌림). 표본 하한이 막으려는 것은 「표본 없이 «새로» 판단해서 값을 움직이는 것」이지
         「이전 판단을 취소하는 것」이 아니다. 막으면 «올려놓고 못 내리는» 상태가 된다.
      ③ 두 원료 키가 **하나도 없으면** 통과 — 컨텍스트를 안 채우는 기존 호출부·테스트의 행위를
         바꾸지 않는다(`_param` fail-to-current 관례와 같은 결).

    ★그러나 «키가 있는데 값이 None»이면 **차단**한다. 「측정 안 함」과 「측정했는데 모름」은
      다르다 — 후자를 통과로 읽는 것이 이 저장소가 반복해 밟은 「모름=괜찮음」이다(교훈 #123).
    """
    if proposal_type not in _FLOOR_GATED_TYPES:
        return None
    if not context.get("auto_exec"):
        return None
    if context.get("floor_exempt"):
        return None
    if "campaign_weekly_conv" not in context and "target_weekly_conv" not in context:
        return None

    campaign_floor = _param(context, "min_weekly_conv_campaign", _MIN_WEEKLY_CONV_CAMPAIGN)
    target_floor = _param(context, "min_weekly_conv_target", _MIN_WEEKLY_CONV_TARGET)
    campaign_conv = context.get("campaign_weekly_conv")
    target_conv = context.get("target_weekly_conv")

    if campaign_conv is None:
        return (
            "표본 하한 — 캠페인 정착창 전환 «측정 불가»(D-NAO-286). "
            "모름은 통과가 아니다 — 올리지도 내리지도 않는다"
        )
    if target_conv is None:
        return (
            "표본 하한 — 대상 정착창 전환 «측정 불가»(D-NAO-286). "
            "모름은 통과가 아니다 — 올리지도 내리지도 않는다"
        )
    if campaign_conv < campaign_floor:
        return (
            f"표본 하한 — 캠페인 정착창 전환 {campaign_conv}건 < 하한 {campaign_floor}건"
            f"(D-NAO-286, ref 33 [6] 주 15전환). 표본이 없으면 올리지도 내리지도 않는다"
        )
    if target_conv < target_floor:
        return (
            f"표본 하한 — 대상 정착창 전환 {target_conv}건 < 하한 {target_floor}건"
            f"(D-NAO-286, ref 33 [6] 주 15전환). 표본이 없으면 올리지도 내리지도 않는다"
        )
    return None


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
       int(이 대상의 오늘 변경 건수), "campaign_type": str|None(VT4 P1-1 — SHOPPING이면 입찰
       하한 50원, 그 외/None이면 70원 보수 하한)}.
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
        # D-NAO-286: 표본 하한을 **입찰 검사보다 먼저** 본다 — 「표본이 없다」는 「값이 나쁘다」의
        # 앞선 사유이고, 뒤에 두면 클램프·방향 사유가 먼저 떠서 진짜 이유가 로그에서 가려진다.
        reason = _check_data_floor(context, proposal_type)
        if reason:
            return reason
        return _check_bid(proposal, context, proposal_type)
    if proposal_type in _BUDGET_TYPES:
        return _check_budget(proposal, context, proposal_type)
    return _check_lock(proposal, proposal_type)


def precheck_cooldown_and_cap(
    last_change_at: datetime | None, changes_today_count: int, now: datetime,
    proposal_type: str | None, *, auto_exec: bool = False,
) -> str | None:
    """쿨다운·일일상한 사전점검(IU-R R2 공용 — auto_operator.run_hourly_lane의 rank-step
    prefilter가 estimate 호출 전 재사용). 임계·면제 규칙은 _check_cooldown_and_cap 단일 소스를
    그대로 태운다(중복 금지) — prefilter와 실행 시점 guardrail이 **같은 판정**을 공유한다.
    (차단사유, or None=통과). last_change_at/changes_today_count는 compute_change_cadence 산출.

    auto_exec: 이 제안을 사람 승인 없이 레인이 바로 쏘는가(D-NAO-125). True면 하향에도
    _MAX_DAILY_AUTO_BID_DOWNS 상한이 걸린다 — prefilter가 이걸 안 넘기면 레인은 통과라고
    보고 estimate까지 간 뒤 실행 시점 guardrail에서 막혀 **판정이 갈린다**(중복 금지 원칙 위반).
    """
    return _check_cooldown_and_cap(
        {"last_change_at": last_change_at, "changes_today_count": changes_today_count,
         "auto_exec": auto_exec},
        now, proposal_type,
    )


def _check_cooldown_and_cap(context: dict, now: datetime, proposal_type: str | None) -> str | None:
    # 쿨다운 2h는 전 유형 공통(DL3 면제 대상 아님) — 방향 무관하게 항상 검사.
    # ★D-NAO-130(Jino 2026-07-29 "평가하고 판단하면서 수정하면 되지 않을까?"): 자동 상향을 켜는
    #   근거가 바로 그 되먹임 루프다 — 올리고, 실측하고, 손실이면 자동 하향이 깎는다. 그런데
    #   UP과 DOWN이 같은 쿨다운 시계를 쓰면 **되돌리는 손이 최대 2시간 묶인다**(codex 적대 2R).
    #   루프의 교정 단계를 막는 브레이크는 브레이크가 아니라 고장이다. 그래서 "직전 변경이 우리
    #   자동 상향이었다면 하향은 쿨다운 면제".
    #   ★진동 방어는 남는다: 되돌림은 한 번이면 충분하고(그 뒤엔 직전 변경이 DOWN이라 면제 안 됨),
    #     자동 하향 일일 상한(_MAX_DAILY_AUTO_BID_DOWNS)과 ±15% 클램프가 그대로 걸린다.
    if (
        proposal_type in _BID_DOWN_TYPES
        and context.get("last_change_was_auto_up")
    ):
        last_change_at = None
    else:
        last_change_at = context.get("last_change_at")
    if last_change_at is not None:
        elapsed_hours = (now - last_change_at).total_seconds() / 3600
        cooldown_hours = _param(context, "cooldown_hours", _COOLDOWN_HOURS)
        if elapsed_hours < cooldown_hours:
            return (
                f"쿨다운 중 — 마지막 변경 {elapsed_hours:.1f}시간 전"
                f"(최소 {cooldown_hours}시간 필요, D-NAO-19)"
            )

    # DL3(D-NAO-65): 안전방향(bid_down)은 일일 횟수 상한에서만 면제 — "쭉 낮추다가"를
    # 위해서다. 진동 방어는 위 쿨다운 2h가 유지하고, 방향 불일치 stale 행(bid_down인데
    # target≥current)은 뒤이은 _check_bid의 방향검증이 여전히 fail-closed 차단한다.
    changes_today = context.get("changes_today_count", 0)
    if proposal_type not in _DAILY_CAP_EXEMPT_TYPES:
        if changes_today >= _MAX_DAILY_CHANGES:
            return f"일일 변경 건수 상한 도달({changes_today}/{_MAX_DAILY_CHANGES}건, 폭주 방지)"
    elif context.get("auto_exec") and context.get("auto_bid_down_today") is not None:
        # D-NAO-125 codex[P1]: 사람 없이 쏘는 하향에만 별도 상한(위 _MAX_DAILY_AUTO_BID_DOWNS
        # 주석). 사람이 승인한 하향은 종전 DL3 면제 유지.
        # ★세는 것은 changes_today_count(그 대상의 오늘 **모든** 변경)가 아니라 **자동 하향
        #   성공 건수**다(codex 2R[P1]): 전자를 쓰면 사람이 오늘 손으로 3번 만진 소재에서
        #   정작 손실 하향이 하루 종일 막힌다 — 열려던 행동을 정확히 막는 셈이다.
        # ★키가 None이면 상한 미적용: harness가 소재 grain에서만 채운다(그룹·키워드의 기존
        #   자동 하향 경로는 종전 DL3 면제 그대로 — 이번 변경의 스코프 밖).
        auto_downs = context["auto_bid_down_today"]
        daily_cap = _param(context, "max_daily_auto_bid_downs", _MAX_DAILY_AUTO_BID_DOWNS)
        if auto_downs >= daily_cap:
            return (
                f"자동 하향 일일 상한 도달({auto_downs}/{daily_cap}건, "
                "D-NAO-125 — 더 내리려면 콘솔에서 사람이 승인)"
            )

    return None


def _check_bid(proposal: dict, context: dict, proposal_type: str) -> str | None:
    target_bid = proposal.get("target_bid")
    if target_bid is None:
        return "target_bid 없음 — 구조 결함(재생성 필요)"
    # VT4 codex 1R P1-1: SHOPPING이면 50원, 그 외(WEB_SITE·BRAND_SEARCH·None·미확보)면 70원 하한.
    # campaign_type은 harness가 _build_guardrail_context에서 채운다(부재 시 None → 보수 70).
    min_bid = _MIN_BID_SHOPPING if context.get("campaign_type") == "SHOPPING" else _MIN_BID
    if not (min_bid <= target_bid <= _MAX_BID) or target_bid % _BID_INCREMENT != 0:
        return f"target_bid={target_bid}는 유효 범위 밖({min_bid}~100,000원, 10원 단위)"

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
        # BX2(D-NAO-71): 탐색 스텝은 30% 상한, 그 외 15%. 탐색은 완전 면제(CHANGE_PCT_EXEMPT_TYPES)가
        # 아니라 상한만 넓힌 것 — 30% 초과는 여전히 차단(폭주 방지, 탐색 스텝 상한 고정 테스트).
        cap = (_EXPLORATION_MAX_CHANGE_PCT if proposal_type in _EXPLORATION_STEP_TYPES
               else _param(context, "max_change_pct", _MAX_CHANGE_PCT))
        if change_pct > cap:
            return (
                f"변경폭 {float(change_pct):.1%} 초과(상한 {float(cap):.0%}, D-NAO-5/71) "
                f"— 현재={current_bid}원 목표={target_bid}원"
            )

    # ── 출시창 순위 하한(D-NAO-121) ───────────────────────────────────────────
    # 인하 제안이 "목표 순위 유지에 필요한 금액" 밑으로 내려가면 차단한다. 출시 초기에는
    # 그 자리가 매출 자체를 결정하기 때문이다(2026-07-29 실사고: 머리 키워드 1.7~2.2위 →
    # 7위 추락, 원인은 입찰 하락인데 이 파이프라인엔 상한만 있고 하한이 없었다).
    # context에 키가 없거나 None이면 하한 없음 = 종전 동작(하위호환).
    # ★상한(BEP)과 충돌하면 출시창 안에서는 하한이 이긴다 — 계산된 손해를 감수하고
    #   가시성을 사는 것이 이 창의 목적이다. 창이 끝나면 하한 자체가 사라진다.
    if proposal_type in _BID_DOWN_TYPES:
        launch_floor = context.get("launch_floor_bid")
        if launch_floor is not None and target_bid < launch_floor:
            return (
                f"출시창 순위 하한 — 목표 {context.get('launch_target_rank')}위 유지에 "
                f"{launch_floor}원이 필요해 {target_bid}원으로 내릴 수 없다(D-NAO-121)"
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

        # ★D-NAO-129: 누적 상승 배수 상한 — BEP가 소재 천장이 못 되는 구멍의 마개(위 상수 주석).
        # 기준점이 없으면(최근 창에 자동 상향 이력 없음) 미적용 = 종전 동작.
        # ★D-NAO-130 codex 5R[P1]: **외부변경 확인이 성립하지 않으면 자동 상향을 하지 않는다.**
        #   이 확인이 자동 상향을 켠 선행조건이므로, 확인 실패(조회 오류·관측 부재·형식 변화)를
        #   통과시키면 조건 없이 켠 것과 같다. 사람 승인 상향은 이 검사 대상이 아니다.
        #   context에 키가 없으면(비-ad grain 등) 종전 동작 — 이 게이트는 소재 자동 상향 전용이다.
        if (
            context.get("auto_exec")
            and proposal_type in _CUMULATIVE_CAP_TYPES
            and context.get("external_check", _EXT_CHECK_OURS) != _EXT_CHECK_OURS
            and context.get("external_check") != _EXT_CHECK_EXTERNAL
        ):
            return (
                "외부 변경 확인 불가 — 이 소재의 현재 값이 우리가 아는 상태인지 확인되지 않아 "
                "자동 상향을 보류한다(D-NAO-130 fail-closed). 다음 회차에 관측이 서면 재개된다"
            )

        # ★적용 범위는 이번에 개방한 타입뿐(codex 적대 2R[P2]): 탐색(bid_up_explore)·콜드
        #   (bid_up_cold)·서보(bid_up_servo/rank)는 각자의 경제성 상한을 가진 별도 레인이라,
        #   전역 2배 천장으로 묶으면 그 레인들의 의미가 조용히 바뀐다(기존 동작 회귀).
        # ★기준점은 **과거의 고정점**이어야 한다. 초판 수정에서 current_bid를 min으로 물었더니
        #   기준점이 현재값을 따라 올라가(400→460→529…) 천장이 언제나 현재값의 2배가 되어
        #   **상한이 영영 안 걸렸다** — 방어를 넣으면서 방어를 껐다. 외부 하향 반영은 시각이
        #   아니라 **사건**으로 한다(auto_up_base_bid: 사람 쓰기 또는 D-NAO-127 소재 외부변경
        #   감지 중 최신 것이 기준점을 재설정한다).
        auto_up_base = context.get("auto_up_base_bid")
        if (
            context.get("auto_exec")
            and auto_up_base
            and proposal_type in _CUMULATIVE_CAP_TYPES
        ):
            up_multiple = _param(context, "max_auto_up_multiple", _MAX_AUTO_UP_MULTIPLE)
            ceiling = int(Decimal(auto_up_base) * Decimal(up_multiple))
            if target_bid > ceiling:
                return (
                    f"자동 상향 누적 상한 — 기준가 {auto_up_base}원의 "
                    f"{float(up_multiple):.1f}배({ceiling}원)를 넘는 {target_bid}원은 "
                    "자동으로 올리지 않는다(D-NAO-129 — 더 올리려면 콘솔에서 사람이 승인)"
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
