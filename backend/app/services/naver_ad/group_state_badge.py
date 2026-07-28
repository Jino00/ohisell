# group_state_badge.py — 광고그룹 상태 배지 SA (D-NAO-105 Phase 2, 계획서 §4-ⓑ).
"""역할(SA·단일 책임·**순수 판정**): 광고그룹 한 줄의 재료를 받아 4상태 중 하나 + 한글 사유를
돌려준다. DB를 읽지 않고 부수효과가 0이다 — 원료(성과·현재 상태·최근 변경 이력·기준선)는
전부 perf_campaign_harness가 모아서 넘긴다(원칙18-6/18-8).

4상태와 판정 순서(위가 이긴다 — 아래로 갈수록 약한 신호):
  ① 차단됨(blocked)   광고가 실제로 멈춰 있거나, 최근 우리 시도를 안전장치가 막았다.
  ② 증액 보류(hold)   광고는 돌지만 지금 성과가 손익분기 아래라 더 키우지 않는다.
  ③ 확장 중(expanding) 최근 우리가 실제로 **올려서** 노출을 넓히는 중이다.
  ④ 관망(watching)    나머지 — 지켜보는 중.
  (+) 관찰만(observed) **우리가 운영하는 광고가 아니다** — 위 4상태는 전부 "우리가 무엇을
      하고 있다"는 말이라 애초에 성립하지 않는다. 성과 사실만 진술한다.

★"확장 중"은 성과가 좋다는 뜻이 아니라 **우리가 최근에 올렸다**는 뜻이다(집행된 상향 이력이
  있을 때만). 성과가 목표 위인데 아무것도 안 건드렸으면 그건 '관망'이다 — 하지도 않은 일을
  했다고 말하지 않는다(원칙22).

★그 거울상도 막는다(2026-07-28 리뷰 실측): 집행이 **0원**인 그룹은 올린 이력이 있어도
  '확장 중'이 아니다. 03 그룹 24개 중 노출·클릭·지출이 전부 0인 3개가 "반응이 나오는지 보는
  중입니다"로 떴는데, 반응을 볼 노출 자체가 없었다. `cost<=0`을 상향 판정보다 **먼저** 본다.

★관리 주체 반영(2026-07-28 리뷰 실측): 이 API는 46캠페인 전부에 열려 있는데 43개가
  optimizer='none'(우리가 손대지 않는 광고)이다. 그 그룹에 "더 키우지 않고 있습니다"·"지금
  설정을 유지하고 있습니다"를 붙이면 **하지도 않는 관리를 한다고 말하는 것**이다 — Phase 1
  카드의 `managed_by_label`("직접 관리")과도 모순된다. 우리 광고가 아니면 'observed'로
  강등하고 성과 사실만 말한다.

★"차단됨"과 "모름"을 섞지 않는다: 쓰기 실패(unknown)는 반영 여부를 모르는 상태라 차단이
  아니다(naver_execution_harness :205 주석과 같은 규율). unknown 이력만 있으면 관망으로 두고
  사유 문장에서 확인이 필요하다고 말한다.
"""
from __future__ import annotations

STATE_EXPANDING = "expanding"
STATE_WATCHING = "watching"
STATE_HOLD = "hold"
STATE_BLOCKED = "blocked"
STATE_OBSERVED = "observed"

STATE_LABEL = {
    STATE_EXPANDING: "확장 중",
    STATE_WATCHING: "관망",
    STATE_HOLD: "증액 보류",
    STATE_BLOCKED: "차단됨",
    STATE_OBSERVED: "관찰만",
}

# 우리가 운영하지 않는 광고의 문장 머리. 성과 서술 앞에 항상 붙여 오해를 원천 차단한다.
NOT_OURS_PREFIX = "우리가 운영하는 광고가 아닙니다."

# 광고가 멈춰 있는 이유(네이버 statusReason 원문 → 사람 말). perf_today_harness의 캠페인용
# 사전과 **의도적으로 분리**한다: 여기는 그룹 문장이라 주어가 다르다("이 그룹은 …").
_OFF_SENTENCE = {
    "ADGROUP_PAUSED": "이 그룹은 지금 멈춰 있습니다.",
    "CAMPAIGN_PAUSED": "광고 전체가 멈춰 있어 이 그룹도 나가지 않습니다.",
    "CAMPAIGN_LIMITED_BY_BUDGET": "오늘 예산을 다 써서 지금은 나가지 않습니다.",
}
_OFF_FALLBACK = "이 그룹은 지금 멈춰 있습니다."


def judge(
    *,
    name: str,
    status: str | None,
    status_reason: str | None,
    cost: int,
    roas: float | None,
    bep_roas: float | None,
    target_roas: float | None,
    blocked_reasons: list[str],
    raised_recently: bool,
    lowered_recently: bool,
    unknown_recently: bool,
    window_days: int,
    managed_by_us: bool,
) -> dict:
    """그룹 1개의 상태 판정. 반환 {name, state, state_label, reason_sentence}.

    managed_by_us: 이 캠페인을 **우리 시스템이 운영**하는가(optimizer=='ours'). False면 4상태
      대신 'observed'로 강등한다 — 우리가 안 하는 일을 한다고 말하지 않는다.
    blocked_reasons: 창 안에서 안전장치가 막은 사유(이미 한글 — change_log_narrator.block_reason
      결과). 빈 리스트면 차단 이력 없음.
    raised/lowered/unknown_recently: 창 안에 **집행된** 상향/하향/결과 미상 이력이 있는가.
    roas/bep/target: None은 '알 수 없음'이다 — 없으면 성과 근거 판정을 하지 않는다.
    """
    off = (status or "").lower() == "off"

    if not managed_by_us:
        return _out(name, STATE_OBSERVED, _observed_sentence(
            off=off, status_reason=status_reason, cost=cost, roas=roas,
            bep_roas=bep_roas, target_roas=target_roas, window_days=window_days,
        ))

    # ① 차단됨 — 실제로 안 나가고 있거나, 우리 시도를 안전장치가 막았다.
    if off:
        return _out(name, STATE_BLOCKED,
                    _OFF_SENTENCE.get((status_reason or "").upper(), _OFF_FALLBACK))
    if blocked_reasons:
        # 사유가 여러 개면 가장 최근 것 하나만 말한다(나열하면 문장이 아니라 목록이 된다).
        return _out(name, STATE_BLOCKED,
                    f"바꾸려다 안전장치가 막았습니다 — {blocked_reasons[-1]}.")

    # ② 집행 0원 — 성과도 확장도 말할 수 없다. **상향 판정보다 먼저** 본다(리뷰 실측):
    #    올려둔 이력이 있어도 노출이 0이면 "반응이 나오는지 보는 중"은 거짓이다.
    if cost <= 0:
        if raised_recently:
            return _out(name, STATE_WATCHING,
                        "입찰을 올려두었지만 아직 집행이 없습니다.")
        return _out(name, STATE_WATCHING,
                    f"최근 {window_days}일 집행이 없어 판단할 근거가 없습니다.")

    # ③ 증액 보류 — 손익분기 아래. 여기부터는 cost > 0이 보장된다.
    if roas is not None and bep_roas is not None and roas < bep_roas:
        return _out(name, STATE_HOLD,
                    f"최근 {window_days}일 성과가 남는 기준({bep_roas:.2f}배) 아래라 "
                    "더 키우지 않고 있습니다.")
    if roas is None:
        # 광고비를 썼는데 ROAS를 못 구한 경우. 현재 하니스는 여기 오지 않지만(집계 ROAS는
        # cost>0이면 항상 값이 있다) 이 함수의 계약상 가능한 입력이라 남긴다 — 모름을
        # '좋음'으로 흘려보내지 않는 자리다.
        return _out(name, STATE_HOLD,
                    f"최근 {window_days}일 동안 돈은 썼는데 성과를 확인할 수 없어 "
                    "더 키우지 않고 있습니다.")

    # ④ 확장 중 — 우리가 실제로 올렸고, 집행도 일어났을 때만.
    if raised_recently:
        if target_roas is not None and roas >= target_roas:
            return _out(name, STATE_EXPANDING,
                        f"목표({target_roas:.2f}배)를 넘고 있어 최근 입찰을 올려 "
                        "노출을 넓히는 중입니다.")
        return _out(name, STATE_EXPANDING, "최근 입찰을 올려 반응이 나오는지 보는 중입니다.")

    # ⑤ 관망 — 나머지. 왜 관망인지는 재료에 따라 다르게 말한다(전부 같은 문장이면 정보가 없다).
    if unknown_recently:
        return _out(name, STATE_WATCHING,
                    "최근 변경이 실제로 반영됐는지 확인되지 않아 결과를 지켜보는 중입니다.")
    if lowered_recently:
        return _out(name, STATE_WATCHING, "최근 입찰을 낮춰 지출을 줄이고 지켜보는 중입니다.")
    if target_roas is not None and roas >= target_roas:
        return _out(name, STATE_WATCHING,
                    f"목표({target_roas:.2f}배)를 넘고 있어 지금 설정을 유지하고 있습니다.")
    return _out(name, STATE_WATCHING, "손해는 아니지만 목표에는 못 미쳐 지켜보는 중입니다.")


def _observed_sentence(
    *, off: bool, status_reason: str | None, cost: int, roas: float | None,
    bep_roas: float | None, target_roas: float | None, window_days: int,
) -> str:
    """우리 광고가 아닐 때의 문장 — **성과 사실만**. 동사는 전부 상태 서술이고, "우리가 …하고
    있다"는 표현을 쓰지 않는다."""
    parts = [NOT_OURS_PREFIX]
    if off:
        parts.append(_OFF_SENTENCE.get((status_reason or "").upper(), _OFF_FALLBACK))
        return " ".join(parts)
    if cost <= 0:
        parts.append(f"최근 {window_days}일 집행이 없었습니다.")
        return " ".join(parts)
    if roas is None:
        parts.append(f"최근 {window_days}일 광고비는 썼지만 성과를 확인할 수 없습니다.")
    elif target_roas is not None and roas >= target_roas:
        parts.append(f"최근 {window_days}일 성과는 목표({target_roas:.2f}배)를 넘습니다.")
    elif bep_roas is not None and roas >= bep_roas:
        parts.append(f"최근 {window_days}일 성과는 남는 기준({bep_roas:.2f}배)은 넘지만 "
                     "목표에는 못 미칩니다.")
    elif bep_roas is not None:
        parts.append(f"최근 {window_days}일 성과가 남는 기준({bep_roas:.2f}배) 아래입니다.")
    else:
        parts.append(f"최근 {window_days}일 성과는 {roas:.2f}배입니다.")
    return " ".join(parts)


def _out(name: str, state: str, sentence: str) -> dict:
    return {
        "name": name,
        "state": state,                    # 내부 코드 — 화면에는 state_label만 쓴다
        "state_label": STATE_LABEL[state],
        "reason_sentence": sentence,
    }
