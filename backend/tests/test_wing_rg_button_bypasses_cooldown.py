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

import importlib.util
import os
import sys
import types
import re
from pathlib import Path

import pytest

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


def test_request_is_marked_served_before_the_claim_call():
    """«집어봤다» 기록은 claim 호출 **앞**이어야 한다 (적대 리뷰 1R P1-1).

    ★2026-08-20 초판은 이 순서가 **반대**였다: claim 뒤에 기록했더니 `_prod_rg_claim`이
    `raise_for_status()`로 예외를 던질 때(prod 5xx) 그 줄을 건너뛰어 같은 요청을 15초마다
    무한 재claim했다. 소스 위치 검사만으로는 부족해 아래 런타임 테스트가 짝으로 있다.
    """
    i_mark = SRC.index("rg_served_request_at = _rg_requested_at")
    i_claim = SRC.index("_rg_claimed = _prod_rg_claim(cfg)")
    assert i_mark < i_claim, "«집어봤다» 기록이 claim 뒤로 갔다 — 예외 경로에서 유실된다"
    i_lastrg = SRC.index("last_rg = time.monotonic()", i_mark)
    assert i_lastrg < i_claim, (
        "쿨다운 시작도 claim 앞이어야 한다 — `last_rg is None`이 남으면 _rg_ready의 다른 문이 열린다")


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


# ══════════════════════════════════════════════════════════════════
# 런타임 테스트 — 위 소스 검사들의 **사각지대**를 닫는다 (적대 리뷰 1R P1-1)
#
# 리뷰어가 잡은 것: `rg_served_request_at` 기록이 `_prod_rg_claim()` **뒤**에 있으면,
# 그 함수가 `raise_for_status()`로 예외를 던질 때(prod 5xx·네트워크 순단) 그 줄을 건너뛴다.
# → `_rg_fresh`가 계속 True → **같은 요청을 15초마다 무한 재claim**(리뷰어 재현 20회전 20호출).
# 소스 문자열 검사는 «위치»만 보므로 이 런타임 경로를 원리적으로 못 본다. 그래서 여기서 돈다.
# ══════════════════════════════════════════════════════════════════


class _StopPoll(Exception):
    """폴 루프의 sleep에서 던져 1회전만 돌린다."""


def _load_fetcher(home):
    if "playwright.sync_api" in sys.modules:
        pass
    else:
        pkg = types.ModuleType("playwright")
        api = types.ModuleType("playwright.sync_api")
        api.sync_playwright = lambda *_a, **_k: (_ for _ in ()).throw(
            RuntimeError("playwright stub"))
        pkg.sync_api = api
        sys.modules.setdefault("playwright", pkg)
        sys.modules["playwright.sync_api"] = api
    old = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        spec = importlib.util.spec_from_file_location(
            "_tool_wing_rt", TOOLS / "wing_browser_fetcher.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if old is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old


@pytest.fixture()
def fetcher(tmp_path_factory):
    return _load_fetcher(tmp_path_factory.mktemp("home"))


def _cfg(tmp_path):
    st = tmp_path / "state.json"
    st.write_text("{}", encoding="utf-8")
    return {"account_key": "COUPANG_WING1", "prod_base_url": "http://prod.test",
            "ingest_token": "tok", "state_file": str(st)}


def test_claim_exception_does_not_cause_infinite_reclaim(fetcher, monkeypatch, tmp_path):
    """★적대 리뷰 1R P1-1 — claim이 예외로 죽어도 같은 요청을 재claim하면 안 된다.

    폴을 **여러 회전** 돌린다. 기록이 claim 뒤에 있으면 매 회전 claim이 호출된다.
    """
    calls = {"n": 0}
    turns = {"n": 0}

    def _boom_claim(_cfg):
        calls["n"] += 1
        raise fetcher.requests.RequestException("prod 5xx")

    def _sleep(_s):
        turns["n"] += 1
        if turns["n"] >= 5:
            raise _StopPoll()

    monkeypatch.setattr(fetcher, "_prod_refresh_status", lambda _c: {"requested": False})
    monkeypatch.setattr(fetcher, "_prod_rg_refresh_status",
                        lambda _c: {"requested": True, "requested_at": "2026-08-20T10:08:37"})
    monkeypatch.setattr(fetcher, "_prod_rg_claim", _boom_claim)
    monkeypatch.setattr(fetcher.time, "sleep", _sleep)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(_cfg(tmp_path))

    assert turns["n"] >= 5, "폴이 여러 회전 돌지 않았다 — 이 테스트가 무의미해졌다"
    assert calls["n"] == 1, (
        f"같은 요청을 {calls['n']}번 claim했다 — 예외 경로에서 «집어봤다»가 기록되지 않는다")


def test_new_request_after_a_failed_claim_is_served_again(fetcher, monkeypatch, tmp_path):
    """면제가 «죽은» 것은 아니어야 한다 — 사람이 **다시** 누르면(requested_at 변경) 또 집는다."""
    seen = []
    stamps = iter(["2026-08-20T10:08:37"] * 3 + ["2026-08-20T10:20:00"] * 3)
    turns = {"n": 0}

    def _claim(_cfg):
        seen.append("claim")
        raise fetcher.requests.RequestException("prod 5xx")

    def _sleep(_s):
        turns["n"] += 1
        if turns["n"] >= 6:
            raise _StopPoll()

    monkeypatch.setattr(fetcher, "_prod_refresh_status", lambda _c: {"requested": False})
    monkeypatch.setattr(fetcher, "_prod_rg_refresh_status",
                        lambda _c: {"requested": True, "requested_at": next(stamps)})
    monkeypatch.setattr(fetcher, "_prod_rg_claim", _claim)
    monkeypatch.setattr(fetcher.time, "sleep", _sleep)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(_cfg(tmp_path))

    assert len(seen) == 2, f"요청이 두 번 눌렸는데 claim 시도가 {len(seen)}회다(2회여야 한다)"


def test_skip_log_reports_the_gate_that_is_actually_blocking(fetcher, monkeypatch, tmp_path, caplog):
    """★적대 리뷰 1R P1-3 — 백오프 구간에서 «쿨다운 3599초»라고 말하면 안 된다."""
    turns = {"n": 0}

    def _sleep(_s):
        turns["n"] += 1
        if turns["n"] >= 3:
            raise _StopPoll()

    monkeypatch.setattr(fetcher, "_prod_refresh_status", lambda _c: {"requested": False})
    monkeypatch.setattr(fetcher, "_prod_rg_refresh_status",
                        lambda _c: {"requested": True, "requested_at": "2026-08-20T10:08:37"})
    monkeypatch.setattr(fetcher, "_prod_rg_claim",
                        lambda _c: (_ for _ in ()).throw(
                            fetcher.requests.RequestException("prod 5xx")))
    monkeypatch.setattr(fetcher.time, "sleep", _sleep)

    with caplog.at_level("INFO"):
        with pytest.raises(_StopPoll):
            fetcher.cmd_poll(_cfg(tmp_path))

    skips = [r.getMessage() for r in caplog.records if "요청 있으나" in r.getMessage()]
    assert skips, "건너뛴 사실이 로그에 안 남았다"
    assert any("재시도 백오프" in m for m in skips), f"막고 있는 문을 잘못 말한다: {skips}"
    assert not any("3599" in m or "359" in m.split("약 ")[-1][:4] for m in skips), (
        f"백오프 구간인데 쿨다운(1시간) 기준으로 남은 시간을 냈다: {skips}")


def test_skip_log_is_throttled_not_every_poll(fetcher):
    """스팸 방지가 실제 값으로 살아 있어야 한다(변이 5 생존분 봉쇄)."""
    assert 'rg_skip_log_every = int(cfg.get("rg_skip_log_every_s", 300))' in SRC
    assert "time.monotonic() - rg_skip_log_at >= rg_skip_log_every" in SRC


def test_claimed_false_repeats_do_not_stall_for_an_hour(fetcher, monkeypatch, tmp_path):
    """★적대 리뷰 2R 변이 ⑤ — `claimed=False`가 반복될 때 백오프가 걸려야 한다.

    실전 시나리오: 이전 프로세스가 run 도중 죽고 launchd가 재기동하면, 새 프로세스는
    **죽은 자기 자신의 임대**(TTL 20분)를 만나 `claimed=False`를 받는다. 이때 백오프가
    없으면 쿨다운이 시도 시점부터 켜져 있으므로 다음 기회가 **1시간 뒤**가 된다 —
    이 작업이 없애려던 「버튼이 조용히 삼켜지는」 상태로 되돌아간다.
    (리뷰어 재현: else 분기를 지우면 600초 동안 claim 1회. 있으면 30초 간격.)

    코드는 옳았지만 이 경로를 검증하는 테스트가 없어 변이가 살아남았다.
    """
    calls = {"n": 0}
    turns = {"n": 0}
    clock = {"t": 0.0}

    def _claim(_cfg):
        calls["n"] += 1
        return {"claimed": False}

    def _sleep(_s):
        turns["n"] += 1
        clock["t"] += 15.0          # 폴 간격
        if turns["n"] >= 40:        # 600초 시뮬레이션
            raise _StopPoll()

    monkeypatch.setattr(fetcher.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(fetcher, "_prod_refresh_status", lambda _c: {"requested": False})
    monkeypatch.setattr(fetcher, "_prod_rg_refresh_status",
                        lambda _c: {"requested": True, "requested_at": "2026-08-20T10:28:38"})
    monkeypatch.setattr(fetcher, "_prod_rg_claim", _claim)
    monkeypatch.setattr(fetcher.time, "sleep", _sleep)

    with pytest.raises(_StopPoll):
        fetcher.cmd_poll(_cfg(tmp_path))

    # 600초 / 30초 백오프 ≈ 20회. 1회면 1시간 정지(백오프 없음), 40회면 매 폴 폭주.
    assert 5 <= calls["n"] <= 25, (
        f"600초 동안 claim {calls['n']}회 — 1회면 1시간 정지, 40회면 폭주다")
