# group_state_badge.py — 광고그룹 상태 배지 SA (D-NAO-105 Phase 2, 계획서 §4-ⓑ).
"""역할(SA·단일 책임·**순수 판정**): 광고그룹 한 줄의 재료를 받아 4상태 중 하나 + 한글 사유를
돌려준다. DB를 읽지 않고 부수효과가 0이다 — 원료(성과·현재 상태·최근 변경 이력·기준선)는
전부 perf_campaign_harness가 모아서 넘긴다(원칙18-6/18-8).

4상태와 판정 순서(위가 이긴다 — 아래로 갈수록 약한 신호):
  ① 차단됨(blocked)   광고가 실제로 멈춰 있거나, 최근 우리 시도를 안전장치가 막았다.
  ② 증액 보류(hold)   광고는 돌지만 지금 성과가 손익분기 아래라 더 키우지 않는다.
  ③ 확장 중(expanding) 최근 우리가 실제로 **올려서** 노출을 넓히는 중이다.
  ④ 관망(watching)    나머지 — 지켜보는 중.

★"확장 중"은 성과가 좋다는 뜻이 아니라 **우리가 최근에 올렸다**는 뜻이다(집행된 상향 이력이
  있을 때만). 성과가 목표 위인데 아무것도 안 건드렸으면 그건 '관망'이다 — 하지도 않은 일을
  했다고 말하지 않는다(원칙22).

★"차단됨"과 "모름"을 섞지 않는다: 쓰기 실패(unknown)는 반영 여부를 모르는 상태라 차단이
  아니다(naver_execution_harness :205 주석과 같은 규율). unknown 이력만 있으면 관망으로 두고
  사유 문장에서 확인이 필요하다고 말한다.
"""
from __future__ import annotations

STATE_EXPANDING = "expanding"
STATE_WATCHING = "watching"
STATE_HOLD = "hold"
STATE_BLOCKED = "blocked"

STATE_LABEL = {
    STATE_EXPANDING: "확장 중",
    STATE_WATCHING: "관망",
    STATE_HOLD: "증액 보류",
    STATE_BLOCKED: "차단됨",
}

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
) -> dict:
    """그룹 1개의 상태 판정. 반환 {name, state, state_label, reason_sentence}.

    blocked_reasons: 창 안에서 안전장치가 막은 사유(이미 한글 — change_log_narrator.block_reason
      결과). 빈 리스트면 차단 이력 없음.
    raised/lowered/unknown_recently: 창 안에 **집행된** 상향/하향/결과 미상 이력이 있는가.
    roas/bep/target: None은 '알 수 없음'이다 — 없으면 성과 근거 판정을 하지 않는다.
    """
    off = (status or "").lower() == "off"

    # ① 차단됨 — 실제로 안 나가고 있거나, 우리 시도를 안전장치가 막았다.
    if off:
        return _out(name, STATE_BLOCKED,
                    _OFF_SENTENCE.get((status_reason or "").upper(), _OFF_FALLBACK))
    if blocked_reasons:
        # 사유가 여러 개면 가장 최근 것 하나만 말한다(나열하면 문장이 아니라 목록이 된다).
        return _out(name, STATE_BLOCKED,
                    f"바꾸려다 안전장치가 막았습니다 — {blocked_reasons[-1]}.")

    # ② 증액 보류 — 손익분기 아래. 측정된 성과가 있을 때만 말한다.
    if roas is not None and bep_roas is not None and roas < bep_roas:
        return _out(name, STATE_HOLD,
                    f"최근 {window_days}일 성과가 남는 기준({bep_roas:.2f}배) 아래라 "
                    "더 키우지 않고 있습니다.")
    if cost > 0 and roas is None:
        return _out(name, STATE_HOLD,
                    f"최근 {window_days}일 동안 돈은 썼는데 전환이 잡히지 않아 "
                    "더 키우지 않고 있습니다.")

    # ③ 확장 중 — 우리가 실제로 올렸을 때만.
    if raised_recently:
        if target_roas is not None and roas is not None and roas >= target_roas:
            return _out(name, STATE_EXPANDING,
                        f"목표({target_roas:.2f}배)를 넘고 있어 최근 입찰을 올려 "
                        "노출을 넓히는 중입니다.")
        return _out(name, STATE_EXPANDING, "최근 입찰을 올려 반응이 나오는지 보는 중입니다.")

    # ④ 관망 — 나머지. 왜 관망인지는 재료에 따라 다르게 말한다(전부 같은 문장이면 정보가 없다).
    if unknown_recently:
        return _out(name, STATE_WATCHING,
                    "최근 변경이 실제로 반영됐는지 확인되지 않아 결과를 지켜보는 중입니다.")
    if lowered_recently:
        return _out(name, STATE_WATCHING, "최근 입찰을 낮춰 지출을 줄이고 지켜보는 중입니다.")
    if cost <= 0:
        return _out(name, STATE_WATCHING,
                    f"최근 {window_days}일 집행이 없어 판단할 근거가 없습니다.")
    if target_roas is not None and roas is not None and roas >= target_roas:
        return _out(name, STATE_WATCHING,
                    f"목표({target_roas:.2f}배)를 넘고 있어 지금 설정을 유지하고 있습니다.")
    return _out(name, STATE_WATCHING, "손해는 아니지만 목표에는 못 미쳐 지켜보는 중입니다.")


def _out(name: str, state: str, sentence: str) -> dict:
    return {
        "name": name,
        "state": state,                    # 내부 코드 — 화면에는 state_label만 쓴다
        "state_label": STATE_LABEL[state],
        "reason_sentence": sentence,
    }
