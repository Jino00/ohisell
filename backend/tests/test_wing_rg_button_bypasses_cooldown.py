# test_wing_rg_button_bypasses_cooldown.py — 2026-08-20
#
# 지키는 계약 한 줄: **사람이 누른 RG 요청은 1시간 쿨다운을 기다리지 않는다.**
#
# 무엇이 있었나(라이브 2026-08-20, Jino 발견): 09:12:51·09:13:27에 두 계정의 RG 수집이
# 실제로 **성공**했다. 그러자 `last_rg`가 찍혀 `rg_cooldown`(기본 3600초)이 걸렸고, 09:14:37에
# 「전체 갱신」이 만든 요청부터는 폴 루프가 **로그 한 줄 없이** 건너뛰었다
# (prod 실측: requested=true · claimed_at=null · attempt_count=0). UI는 215초 뒤
# 「⚠️ 응답 없음 — Mac이 켜져 있는지 확인하세요」로 오진했다 — Mac은 켜져 있었고 방금 성공했다.
#
# 쿨다운의 목적은 «실패 재시도 폭주 방지»다. 그래서 면제는 **새 요청 1회**에만 준다:
#   · prod의 `requested_at`이 내가 마지막으로 집어본 것과 다르면 = 사람이 새로 누른 것 → 즉시
#   · claim을 시도한 순간 «집어봤다»로 기록 → 같은 요청이 실패해도 15초마다 무한 재claim 안 함
#     (그 뒤는 종전대로 `rg_retry_at` 백오프가 맡는다)
#
# 폴 루프 전체는 무한 while이라 그대로 못 돌린다 → **판정 식만** 원본과 같은 형태로 재현해
# 고정한다. 코드가 바뀌면 이 파일의 참조 문자열 검사가 먼저 깨진다.
from __future__ import annotations

import re
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools"
SRC = (TOOLS / "wing_browser_fetcher.py").read_text(encoding="utf-8")


def _ready(*, fresh: bool, last_rg, now: float, cooldown: float, retry_at) -> bool:
    """폴 루프의 `_rg_ready` 판정과 **같은 식**(아래 test_source_still_has_this_shape가 동기화를 지킨다)."""
    return (
        fresh
        or last_rg is None
        or now - last_rg >= cooldown
        or (retry_at is not None and now >= retry_at)
    )


def test_human_press_right_after_success_is_not_swallowed():
    """★이 파일의 존재 이유 — 성공 1분 뒤에 눌러도 즉시 처리돼야 한다."""
    # 09:13:27 성공 → last_rg=0.0 / 09:14:37 버튼 → 70초 경과, 쿨다운은 3600초
    assert _ready(fresh=True, last_rg=0.0, now=70.0, cooldown=3600, retry_at=None) is True


def test_same_request_does_not_loop_every_poll():
    """이미 집어본 요청은 면제를 못 받는다 — 실패한 요청을 15초마다 재claim하면 폭주다."""
    assert _ready(fresh=False, last_rg=0.0, now=70.0, cooldown=3600, retry_at=None) is False


def test_retry_backoff_still_works():
    """실패 재시도 경로(짧은 백오프)는 종전대로 살아 있어야 한다."""
    assert _ready(fresh=False, last_rg=0.0, now=100.0, cooldown=3600, retry_at=95.0) is True
    assert _ready(fresh=False, last_rg=0.0, now=90.0, cooldown=3600, retry_at=95.0) is False


def test_cooldown_still_expires_on_its_own():
    """면제와 무관하게 쿨다운 자체는 여전히 만료된다(자동 재시도 경로)."""
    assert _ready(fresh=False, last_rg=0.0, now=3600.0, cooldown=3600, retry_at=None) is True


def test_first_run_of_process_is_ready():
    assert _ready(fresh=False, last_rg=None, now=0.0, cooldown=3600, retry_at=None) is True


def test_source_still_has_this_shape():
    """위 판정식이 **실제 코드와 같은 모양**인지 고정한다.

    폴 루프를 통째로 돌릴 수 없으므로 이 검사가 동기화 장치다 — 코드에서 `_rg_fresh`를
    빼거나 `_rg_ready`에서 그 항을 지우면 여기서 깨진다.
    """
    assert "_rg_fresh = bool(_rg_requested_at) and _rg_requested_at != rg_served_request_at" in SRC
    m = re.search(r"_rg_ready = \(\s*(.*?)\)\s*\n", SRC, re.S)
    assert m, "_rg_ready 판정식을 못 찾았다"
    body = m.group(1)
    assert "_rg_fresh" in body, "새 요청 면제 항이 사라졌다"
    assert "rg_cooldown" in body and "rg_retry_at" in body, "폭주 방지 항이 사라졌다"


def test_claim_attempt_marks_the_request_as_served():
    """claim 시도 = 집어봤다. 성공 여부와 무관해야 무한 재claim이 안 난다."""
    i_claim = SRC.index("_rg_claimed = _prod_rg_claim(cfg)")
    i_mark = SRC.index("rg_served_request_at = _rg_requested_at", i_claim)
    i_if = SRC.index('if _rg_claimed.get("claimed", False):', i_claim)
    assert i_claim < i_mark < i_if, "«집어봤다» 기록이 claim 성공 분기 안으로 들어가면 안 된다"


def test_cooldown_skip_is_no_longer_silent():
    """건너뛴 사실이 로그에 남아야 한다 — 이 침묵이 「Mac 응답 없음」 오진의 원인이었다."""
    assert 'if bool(rg_st.get("requested")) and not _rg_ready:' in SRC
    assert "rg_skip_log_every" in SRC, "로그 조이기(스팸 방지)가 없다"


def _prod_request_blocks() -> list[str]:
    """소스에서 `requests.get(...)`/`requests.post(...)` 호출 본문을 괄호 균형으로 잘라낸다."""
    out = []
    for marker in ("requests.get(", "requests.post("):
        i = 0
        while True:
            i = SRC.find(marker, i)
            if i < 0:
                break
            j = i + len(marker)
            depth = 1
            while j < len(SRC) and depth:
                if SRC[j] == "(":
                    depth += 1
                elif SRC[j] == ")":
                    depth -= 1
                j += 1
            out.append(SRC[i:j])
            i = j
    return out


def test_every_prod_call_carries_basic_auth():
    """prod로 나가는 호출은 **예외 없이** `auth=_basic_auth(cfg)`를 실어야 한다.

    ★왜 여기서 고정하나(2026-08-20): 2026-08-13 prod Basic Auth 작업이 Mac 가동본에만
    반영되고 repo에는 안 들어와 있었다. 그 상태의 테스트 스텁은 `auth=`를 안 받는 좁은
    시그니처였고, 실물을 repo에 맞춰 넣자 22건이 TypeError로 깨졌다 — 즉 **이 기능은
    repo의 어떤 테스트도 검증한 적이 없다.** 스텁만 넓히면 다음에 누가 `auth=`를 빼도
    조용히 통과하므로, 개수가 아니라 «빠진 것이 하나도 없다»를 잰다(호출이 늘어도 산다).
    """
    missing = [
        b[:80].replace("\n", " ")
        for b in _prod_request_blocks()
        if "prod_base_url" in b and "auth=_basic_auth(cfg)" not in b
    ]
    assert not missing, f"Basic Auth 없이 prod로 나가는 호출: {missing}"

    covered = [b for b in _prod_request_blocks() if "auth=_basic_auth(cfg)" in b]
    assert len(covered) >= 11, f"prod 호출이 {len(covered)}곳으로 줄었다 — 누가 뺐는지 확인할 것"
    assert "def _basic_auth(cfg: dict)" in SRC
    # 설정에 키가 없으면 None — 인증을 켜기 전에도 이 코드가 배포될 수 있어야 한다(순서 보장).
    assert "return (u, p) if u and p else None" in SRC
