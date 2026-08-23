# correction_interval.py — 보정계수 «구간 자»의 끝값 정책 (D-NAO-234)
# 역할: 「하한이 얼마인가」와 「점추정을 구간으로 어떻게 펼치는가」를 **한 곳**에만 둔다.
#
# ★왜 별도 모듈인가 (같은 병의 다섯 번째 모양을 막는다):
#   D-NAO-230 도입 당시 하한 유도식 `min(1, 점추정)`이 **두 곳**에 각각 적혀 있었다 —
#   `diagnosis._as_interval`과 `bid_simulator`의 폴백(`correction_factor_low is None`일 때).
#   그 상태에서 하한 상수만 한쪽에서 바꾸면 다른 경로는 옛 하한을 계속 쓰고, 테스트는
#   양쪽 다 초록으로 통과한다(교훈 #348 「전수에서 세야 하는 건 파일이 아니라 호출부」의
#   값 버전). 그래서 상수·유도식·근거 문자열을 이 모듈 하나로 모은다.
#
# 이 모듈은 leaf다 — DB도 다른 SA도 import하지 않는다(순환 방지).
from __future__ import annotations

from decimal import Decimal

# ★D-NAO-234 (Jino 결정 2026-08-23, 계약 CONTRACT_yardstick_attribution.md §5-Q1 안A)
#   하한 = 「광고>」 접두 5종 유입경로 매출 ÷ naver_ad_daily direct 전환매출.
#   분자·분모의 귀속 정의가 같은 짝(둘 다 «마지막 터치가 광고») 이라 정합한 측정이다.
#   ⚠️분모를 (direct+indirect)로 바꾸면 0.686까지 내려가지만 그건 사과↔오렌지다 —
#   indirect는 정의상 마지막 터치가 광고가 «아닌» 주문이라 마지막터치 분자와 못 맞댄다
#   (계약 §3-1 짝 규율). 그 두 칸은 «구조적 과소편향 참고치»로만 문서에 보존한다.
CORRECTION_FACTOR_FLOOR = Decimal("0.827")

# 하한의 근거 — API 응답·화면에 그대로 실린다(계약 §4 금지선 5: 가정 병기 없이 내보내지 않는다).
CORRECTION_FACTOR_FLOOR_SOURCE = "inflowpath_ad_prefix_over_direct"
CORRECTION_FACTOR_FLOOR_WINDOW = "2026-07-25~2026-08-23"
CORRECTION_FACTOR_FLOOR_EVIDENCE = "docs/references/95_inflowpath_yardstick_census_20260823.md"

# 하한에 붙박인 [미상] — 「네이버플러스스토어검색>광고」 6,877,600원(446건)이 SA 추적 대상인지
# 공식 1차 출처로 확정되지 않았다(계약 §8-1 · ref 95 §7 = **보류**). 포함하면 하한이 1.067로
# 올라간다. 하한의 소임은 보수이므로 [미상]을 하한에 넣지 않는 쪽을 택했다(계약 §5-Q3 ③).
CORRECTION_FACTOR_FLOOR_CAVEAT = (
    "마지막터치 라벨 기준·창 2026-07-25~08-23 스냅샷. "
    "「네이버플러스스토어검색>광고」(6,877,600원·446건)의 SA 소속은 공식 출처 미확인이라 하한에서 제외 — "
    "포함 시 1.067."
)

# 창 4개의 실측 하한(ref 95 §5) — 「고정값이 흔들리지 않는다」고 말하지 않기 위해 같이 싣는다.
# W1 0.8289(채택 창·가장 낮음) · W2 0.8566 · W3 0.8862 · W4(60일) 0.8433 ⇒ 폭 5.7%p.
CORRECTION_FACTOR_FLOOR_WINDOW_SPREAD = "0.8289~0.8862 (창 4개, 폭 5.7%p — 채택값은 가장 보수적인 창)"

# 「보정 없음」 = 1.0. 보정계수를 산출할 수 없을 때(실주문 매출 부재)의 퇴화 구간이 이 값이다.
NO_CORRECTION = Decimal("1")


def interval_ends(point: Decimal, floor: Decimal = CORRECTION_FACTOR_FLOOR) -> tuple[Decimal, Decimal]:
    """점추정 하나를 구간 양끝으로 펼친다 — **유일한 유도 지점**.

    하한 = min(floor, point) · 상한 = max(floor, point).

    min/max로 감싸는 이유는 D-NAO-230 원문 그대로다: 점추정이 어느 쪽으로 재확정되어도
    「하한이 항상 상향(액셀) 쪽 보수값, 상한이 항상 하향(브레이크) 쪽 보수값」이라는
    불변식이 깨지지 않게 하기 위해서다. D-NAO-234가 바꾼 것은 **그 기준점이 1.0에서
    0.827로 내려간 것**뿐이고 — 구조는 그대로다.

    ★`floor`를 인자로 남겨 둔 이유: 보정계수 자체가 산출 불가일 때는 하한 근거도 없으므로
    `floor=NO_CORRECTION`을 넘겨 **퇴화 구간 [1, 1]**을 만든다. 그 경우에 0.827을 씌우면
    「근거 없이 매출을 17% 깎는」 보정이 되어 금지선 5를 정면으로 어긴다.
    """
    return min(floor, point), max(floor, point)


def floor_payload() -> dict:
    """하한 근거를 API·화면에 실을 형태로 —— 문자열 4종을 한 곳에서 만든다."""
    return {
        "factor_low_source": CORRECTION_FACTOR_FLOOR_SOURCE,
        "factor_low_window": CORRECTION_FACTOR_FLOOR_WINDOW,
        "factor_low_evidence": CORRECTION_FACTOR_FLOOR_EVIDENCE,
        "factor_low_caveat": CORRECTION_FACTOR_FLOOR_CAVEAT,
        "factor_low_window_spread": CORRECTION_FACTOR_FLOOR_WINDOW_SPREAD,
    }
