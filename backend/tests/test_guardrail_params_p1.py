# test_guardrail_params_p1.py — 봉투 파라미터 층 (D-NAO-172 P1)
#
# ══ 이 파일이 지키는 것 ══
# 봉투(±15%·쿨다운 2h·자동하향 3회/일·누적 2.0×)를 코드 상수에서 「DB로 조절 가능한 기준」으로
# 옮겼다. 위험은 둘이다:
#   ① **조용한 전면 정지** — 레인이 만드는 스텝과 게이트가 허용하는 폭이 어긋나면 모든 제안이
#      「변경폭 초과」로 죽는다. 파라미터를 조인 순간 광고가 멈추는데 아무도 모른다.
#   ② **조용한 봉투 해제** — DB에 이상한 값이 들어갔는데 조용히 먹히면 브레이크가 사라진다.
# 그래서 이 파일의 중심은 「값이 읽힌다」가 아니라 **「폴백이 제대로 떨어지고, 범위 밖은
# 거부되며, 생성과 게이트가 같은 값을 쓴다」**이다.
from __future__ import annotations

import re
import subprocess
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAccountSettings
from app.services.naver_ad import auto_operator, guardrail_gate, guardrail_params

NOW = datetime(2026, 8, 10, 21, 40)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _kv(db, payload: str):
    db.add(NaverAccountSettings(key=guardrail_params.SETTINGS_KEY, value_json=payload))
    db.commit()


# ── 폴백(fail-to-current) — 이 층의 유일하게 안전한 기본값 ─────────────────────
def test_no_row_falls_back_to_code_constants(db):
    """KV 행이 없으면 코드 상수 그대로 — **배포 직후 행위 변화 0**이 P1의 계약이다."""
    p = guardrail_params.get_params(db)
    assert p["cooldown_hours"] == guardrail_gate._COOLDOWN_HOURS
    assert p["max_daily_auto_bid_downs"] == guardrail_gate._MAX_DAILY_AUTO_BID_DOWNS
    assert p["max_auto_up_multiple"] == guardrail_gate._MAX_AUTO_UP_MULTIPLE


def test_broken_json_falls_back_and_does_not_raise(db):
    """JSON이 깨져도 예외를 올리지 않는다 — 설정 한 줄이 광고 집행 경로를 죽이면 안 된다."""
    _kv(db, "{이건 JSON이 아니다")
    assert guardrail_params.get_params(db)["cooldown_hours"] == guardrail_gate._COOLDOWN_HOURS


def test_partial_failure_only_drops_that_key(db):
    """한 칸이 깨져도 **그 칸만** 폴백한다.

    전부 되돌리면 「부분 실패가 전체 롤백」이 되어 오히려 예측이 어렵다 — 사람은 자기가 고친
    두 값 중 하나만 틀렸는데 둘 다 사라진 이유를 못 찾는다.
    """
    _kv(db, '{"cooldown_hours": 4, "max_auto_up_multiple": "말이 안 되는 값"}')
    p = guardrail_params.get_params(db)
    assert p["cooldown_hours"] == 4                                                  # 살아남고
    assert p["max_auto_up_multiple"] == guardrail_gate._MAX_AUTO_UP_MULTIPLE         # 이건 폴백


@pytest.mark.parametrize("payload", [
    '{"cooldown_hours": 0}',                # 진동 방어 해제
    '{"cooldown_hours": 999}',              # 사실상 정지
    '{"max_daily_auto_bid_downs": 99}',     # 하루에 바닥까지
    '{"max_auto_up_multiple": "10"}',       # 누적 상향 브레이크 해제
])
def test_out_of_range_is_rejected(db, payload):
    """★범위는 **배포로만** 바뀐다 — DB가 자기 상한을 넓힐 수 없다는 것이 되먹임 차단의 마지막 층.

    풀기를 사람이 승인한다 해도, 사람이 실수로 10배를 넣을 수 있다. 그때 조용히 먹히면
    codex 3R이 닫았던 「자동화가 되돌려 올림」 구멍이 설정 한 줄로 되살아난다.
    """
    _kv(db, payload)
    p = guardrail_params.get_params(db)
    defaults = {k: s.default for k, s in guardrail_params.SPECS.items()}
    assert p == defaults, "범위 밖 값이 먹혔다"


def test_bool_is_not_accepted_as_int(db):
    """`true`는 파이썬에서 int의 하위형이라 1로 통과해 버린다 — 명시적으로 막는다."""
    _kv(db, '{"cooldown_hours": true}')
    assert guardrail_params.get_params(db)["cooldown_hours"] == guardrail_gate._COOLDOWN_HOURS


def test_revert_switch_ignores_db_entirely(db):
    """되돌림 스위치 한 줄 — 사고 시 전부 코드 상수로."""
    _kv(db, '{"cooldown_hours": 7}')
    assert guardrail_params.get_params(db)["cooldown_hours"] == 7
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(guardrail_params, "_PARAMS_FROM_DB", False)
        assert guardrail_params.get_params(db)["cooldown_hours"] == guardrail_gate._COOLDOWN_HOURS


# ── 현황판 — 「값이 어디서 왔나」가 이 화면의 존재 이유 ────────────────────────
def test_describe_reports_source_and_rejection(db):
    """`source`와 `rejected`가 없으면 **DB를 고쳤는데 코드 상수가 이기고 있는 상태**를 못 본다."""
    _kv(db, '{"cooldown_hours": 4, "max_auto_up_multiple": "99"}')
    rows = {r["key"]: r for r in guardrail_params.describe(db)}
    assert (rows["cooldown_hours"]["source"], rows["cooldown_hours"]["value"]) == ("db", 4.0)
    assert rows["cooldown_hours"]["rejected"] is False
    # 범위 밖은 «조용히 무시»가 아니라 «거부됨»으로 보여야 한다
    assert rows["max_auto_up_multiple"]["source"] == "code"
    assert rows["max_auto_up_multiple"]["rejected"] is True
    # 손대지 않은 것은 code이고 rejected도 아니다(셋을 섞어 세면 지표가 부푼다)
    assert (rows["max_daily_auto_bid_downs"]["source"],
            rows["max_daily_auto_bid_downs"]["rejected"]) == ("code", False)


def test_describe_marks_unparseable_value_as_code_not_db(db):
    """★타입이 깨진 값도 «db에서 온 값»으로 보이면 안 된다.

    범위 밖(위 테스트)과 **파싱 실패**는 코드 경로가 다르다. 파싱 실패가 조용히 `source="db"`로
    뜨면, 화면은 「DB 값으로 돌고 있다」고 말하는데 실제로는 코드 상수가 돌고 있다 —
    이 화면이 막으려던 바로 그 착시다(«기록됐다 ≠ 코드가 읽는다»).
    """
    _kv(db, '{"max_auto_up_multiple": "말이 안 되는 값"}')
    row = {r["key"]: r for r in guardrail_params.describe(db)}["max_auto_up_multiple"]
    assert row["source"] == "code"
    assert row["rejected"] is True
    assert row["value"] == float(guardrail_gate._MAX_AUTO_UP_MULTIPLE)


def test_describe_carries_the_why(db):
    """근거(`why`)를 화면까지 실어 나른다 — 「판단의 근거를 만들자」는 지시의 이행부다.

    값만 보이면 다음 사람이 "왜 2시간이지?"에 답하지 못하고, 답 못 하는 값은 결국 성역이 된다.
    """
    for row in guardrail_params.describe(db):
        assert row["why"].strip(), f"{row['key']}에 근거가 비어 있다"
        assert row["min"] < row["max"]


# ── ★핵심: 생성과 게이트가 같은 값을 쓰는가 ───────────────────────────────────
def test_gate_reads_param_from_context(db):
    """게이트가 컨텍스트의 파라미터를 읽는다(쿨다운 예시)."""
    ctx = {"last_change_at": NOW - timedelta(hours=3), "changes_today_count": 0,
           "guardrail_params": {"cooldown_hours": 5}}
    assert guardrail_gate._check_cooldown_and_cap(ctx, NOW, "bid_up") is not None
    ctx["guardrail_params"] = {"cooldown_hours": 2}
    assert guardrail_gate._check_cooldown_and_cap(ctx, NOW, "bid_up") is None


def test_gate_without_params_key_behaves_as_before(db):
    """컨텍스트에 키가 없으면 코드 상수 — 기존 호출부·테스트가 안 깨진다(fail-to-current)."""
    ctx = {"last_change_at": NOW - timedelta(hours=1), "changes_today_count": 0}
    assert guardrail_gate._check_cooldown_and_cap(ctx, NOW, "bid_up") is not None


def test_max_change_pct_is_deliberately_not_tunable(db):
    """★적대 리뷰 P1-1의 못 — `max_change_pct`를 SPECS에 **다시 넣으면 이 테스트가 죽는다.**

    ±15%는 게이트 전용이 아니라 스텝 «생성기» 7곳이 자기들끼리 재현한다(`proposal_writer`
    _step_down_bid/_ad_step_bid/step_cap · `proposal_pipeline` 확장 bid_up ·
    `auto_operator` 소생 UP/`_check_bid_up_conditions`). `_clamp_step` 하나만 파라미터를 보게 하고
    DB로 값을 내리면 나머지 생성기의 제안이 게이트에서 전건 「변경폭 초과」로 죽고, 그것도
    **`failed` 영구 종결**이라 다음 회차에 회복되지 않는다. 하필 `_step_down_bid`가 **손실 하향**
    산식이라 **조이려고 값을 내리면 조이는 레버가 가장 먼저 죽는다.**
    되살리려면 생성기 전수를 먼저 배선하고 정합 테스트를 전수로 확장할 것.
    """
    assert "max_change_pct" not in guardrail_params.SPECS
    _kv(db, '{"max_change_pct": "0.10"}')
    # DB에 넣어도 무시된다 — 알 수 없는 키는 get_params가 보지 않는다
    assert "max_change_pct" not in guardrail_params.get_params(db)


# ── 파라미터 갈라짐 탐지 (D-NAO-172 적대 리뷰 P1-2로 수리) ───────────────────────────────
# ★이 두 테스트의 **원본은 통과하면서 아무것도 안 지켰다.** 두 겹으로 공허했다:
#   ①`grep`을 상대경로 `app/services/naver_ad/`로 돌려 **프로세스 CWD에 의존**했다. repo 루트에서
#     pytest를 돌리면 grep이 rc=2(`No such file or directory`)로 죽고 stdout이 비어 assert가
#     그냥 통과한다 — 「발견 0건」과 「실행 안 됨」이 같은 초록으로 보인다(교훈 #123).
#   ②패턴이 `guardrail_gate import.*<상수>` **한 형태만** 봤다. 정작 실제 위반이었던
#     `exploration._EXPLORATION_COOLDOWN_HOURS = 2`(값 자체를 복제)도, 모듈 import 후 속성 접근
#     (`guardrail_gate._COOLDOWN_HOURS`)도 전부 통과시켰다.
# 수리: 경로를 **파일 기준 절대경로**로 고정 + **grep이 실제로 돌았는지 rc로 확인** + 패턴을
#   import·속성접근 둘 다로 확장 + 「같은 뜻의 값을 자기 상수로 복제」를 잡는 인벤토리 테스트 추가.
_NAVER_AD_DIR = Path(__file__).resolve().parents[1] / "app" / "services" / "naver_ad"


def _grep(pattern: str) -> list[str]:
    """naver_ad 패키지 전수 grep. **실행되지 않은 것을 «발견 0건»으로 착각하지 않는다.**"""
    assert _NAVER_AD_DIR.is_dir(), f"스캔 대상 디렉터리가 없다: {_NAVER_AD_DIR}"
    r = subprocess.run(["grep", "-rlE", pattern, str(_NAVER_AD_DIR)],
                       capture_output=True, text=True)
    # grep rc: 0=찾음 · 1=못 찾음 · 2+=오류(경로 없음 등). 2를 「못 찾음」으로 읽으면 안 된다.
    assert r.returncode in (0, 1), (
        f"grep이 실행되지 않았다(rc={r.returncode}) — 이 테스트는 아무것도 검증하지 못했다: {r.stderr}")
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def test_remaining_params_are_not_reimported_by_generators(db):
    """남긴 셋을 **게이트 밖에서 직접 읽는 모듈**이 생기면 파라미터가 갈라진다.

    import 형태든 속성 접근 형태든, 게이트 상수를 직접 집어 오는 순간 그 모듈은 DB 파라미터가
    아니라 코드 상수로 판정하게 된다 — 현황판이 말하는 값과 실효값이 어긋난다.
    """
    allowed = {  # 이 셋의 «정본»과 그 정본을 참조하는 폴백 정의처만 허용
        "guardrail_gate.py",   # 상수의 정의처
        "guardrail_params.py", # SPECS의 default가 정본을 **참조**한다(복사 금지 규율의 이행부)
        "exploration.py",      # _EXPLORATION_COOLDOWN_HOURS = 정본 참조(최후 폴백). 실효값은 주입받는다
    }
    for key, const in (("cooldown_hours", "_COOLDOWN_HOURS"),
                       ("max_daily_auto_bid_downs", "_MAX_DAILY_AUTO_BID_DOWNS"),
                       ("max_auto_up_multiple", "_MAX_AUTO_UP_MULTIPLE")):
        hits = _grep(f"(guardrail_gate import[^\\n]*{const}|guardrail_gate\\.{const})")
        offenders = sorted({Path(h).name for h in hits} - allowed)
        assert not offenders, (
            f"{const}({key})를 게이트 밖에서 직접 읽는 모듈이 생겼다 — 파라미터가 갈라진다: {offenders}. "
            f"코드 상수 대신 guardrail_params.get_params(db)로 읽고, 순수 함수면 호출부가 주입할 것.")


def test_no_module_redefines_the_cooldown_value_as_its_own_constant(db):
    """★P1-2가 실제로 났던 자리 — **import가 아니라 «값의 복제»**로 갈라졌다.

    `exploration._EXPLORATION_COOLDOWN_HOURS = 2`는 게이트 상수를 import하지 않아 위 테스트를
    통과하면서, 쿨다운을 DB로 1h로 풀면 이 레인만 2h를 고수했다. 그래서 **이름이 쿨다운-시간인
    모듈 상수의 인벤토리**를 못 박는다. 새 상수가 생기면 여기서 실패하고, 「같은 축인가 다른
    축인가」를 사람이 한 번 판정해서 아래 목록에 근거와 함께 올려야 한다.
    """
    known_different_axes = {
        # (모듈, 상수) : 왜 봉투 쿨다운과 «다른 축»인가
        ("auto_operator.py", "_VITALITY_COOLDOWN_HOURS"):
            "소생(vitality) 재발사 간격 48h — 같은 유닛 재조정 간격이 아니라 «죽은 그룹 되살리기» 주기",
        ("trigger_watch.py", "TRIGGER_COOLDOWN_HOURS"):
            "트리거 감시 알림 재발송 간격 5h — 입찰 변경이 아니라 «사람에게 알리는» 주기",
    }
    pat = r"^_?[A-Za-z_]*COOLDOWN_HOURS[A-Za-z_]*[ ]*="
    found: set[tuple[str, str]] = set()
    for path in _grep(pat):
        name = Path(path).name
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            m = re.match(r"^(_?[A-Za-z_]*COOLDOWN_HOURS[A-Za-z_]*)\s*=\s*(.+)$", line)
            if not m:
                continue
            const, rhs = m.group(1), m.group(2).split("#")[0].strip()
            if name == "guardrail_gate.py":
                continue  # 정본
            # 정본을 **참조**하는 정의는 복제가 아니다 — 값이 따라온다
            if "guardrail_gate." in rhs:
                continue
            found.add((name, const))
    unexpected = sorted(found - set(known_different_axes))
    assert not unexpected, (
        f"쿨다운-시간 상수가 값으로 복제됐다 — 봉투 파라미터를 바꿔도 그 모듈만 옛 값으로 남는다: "
        f"{unexpected}. 같은 축이면 guardrail_gate 상수를 참조하고 실효값은 파라미터에서 주입받을 것. "
        f"다른 축이면 known_different_axes에 «왜 다른가»를 적어 올릴 것.")


def test_step_and_gate_agree_on_the_same_pct(db):
    """★★조용한 전면 정지 방지 — 레인이 만드는 스텝이 게이트를 **통과해야 한다.**

    여기서 15%를 만드는데 게이트가 10%만 허용하면 레인의 모든 제안이 「변경폭 초과」로 죽는다.
    파라미터를 조인 순간 광고가 조용히 멈추는 셈이라, 이 정합은 편의가 아니라 계약이다.
    """
    for pct in (Decimal("0.05"), Decimal("0.15"), Decimal("0.30")):
        for direction in ("up", "down"):
            current = 1000
            stepped = auto_operator._clamp_step(current, direction, pct)
            assert stepped is not None
            ptype = "bid_up" if direction == "up" else "bid_down"
            reason = guardrail_gate._check_bid(
                {"proposal_type": ptype, "target_bid": stepped},
                {"current_bid": current, "campaign_type": "SHOPPING",
                 "guardrail_params": {"max_change_pct": pct}},
                ptype,
            )
            assert reason is None, f"pct={pct} {direction}: 자기가 만든 스텝이 막혔다 — {reason}"


def test_clamp_step_default_is_unchanged(db):
    """인자를 안 주면 종전과 완전히 같다 — 기존 호출부 보호."""
    assert auto_operator._clamp_step(1000, "down") == auto_operator._clamp_step(
        1000, "down", guardrail_gate._MAX_CHANGE_PCT)


# ══ 적대 리뷰가 살려낸 변이를 닫는다 (2026-08-10) ═══════════════════════════════
# 리뷰어가 12종을 주입해 **7종이 살아남았다.** 공통 형태: 「읽는 쪽(게이트) 절반과 쓰는 쪽
# (PUT·배너)이 통째로 무방비」. 초록인 테스트가 지키던 것은 사실상 `_coerce`의 폴백 규칙과
# `cooldown_hours` 한 키의 왕복뿐이었다 — 이 리포의 시그니처 실패(claimed↔wired)의 정확한 형태다.

def test_every_param_actually_wins_over_code_constant(db):
    """★M-H: 「DB 값이 이긴다」가 **키마다** 성립하는가.

    종전엔 `cooldown_hours` 하나로만 단언해서, 나머지 셋을 코드 상수로 되돌려도 초록이었다.
    """
    _kv(db, '{"cooldown_hours": 5, "max_daily_auto_bid_downs": 6, "max_auto_up_multiple": "2.5"}')
    p = guardrail_params.get_params(db)
    assert p["cooldown_hours"] == 5
    assert p["max_daily_auto_bid_downs"] == 6
    assert p["max_auto_up_multiple"] == Decimal("2.5")
    assert {r["key"]: r["source"] for r in guardrail_params.describe(db)} == {
        k: "db" for k in guardrail_params.SPECS
    }


def test_gate_daily_down_cap_reads_param(db):
    """★M-L: 자동 하향 일일 상한이 파라미터를 본다(배선만 있고 못이 없었다).

    P2의 조이기 자동화가 정확히 이 값을 3→5로 올린다 — 여기가 안 먹으면 P2가 무의미해진다.
    """
    base = {"changes_today_count": 0, "auto_exec": True, "auto_bid_down_today": 4}
    assert guardrail_gate._check_cooldown_and_cap(
        {**base, "guardrail_params": {"max_daily_auto_bid_downs": 3}}, NOW, "bid_down") is not None
    assert guardrail_gate._check_cooldown_and_cap(
        {**base, "guardrail_params": {"max_daily_auto_bid_downs": 5}}, NOW, "bid_down") is None


def test_gate_cumulative_up_cap_reads_param(db):
    """★M-K: 자동 상향 누적 상한이 파라미터를 본다.

    이 브레이크는 codex 3R이 「기준점이 옛 고가에 머물러 자동화가 되돌려 올림」 구멍을 잡은
    자리이고, 2026-08-10 적대 리뷰 P1도 정확히 이걸 무력화하는 결함이었다. 배선이 죽어 있으면
    설정 화면은 「2.0배」라고 말하는데 실제로는 아무 값이나 통과한다.
    """
    ctx = {"current_bid": 1000, "campaign_type": "SHOPPING", "auto_exec": True,
           "auto_up_base_bid": 1000, "roas_corrected": 9.0, "target_roas": 1.0}
    # 1000의 2.0배=2000까지 → 1100은 통과
    assert guardrail_gate._check_bid(
        {"proposal_type": "bid_up", "target_bid": 1100},
        {**ctx, "guardrail_params": {"max_auto_up_multiple": Decimal("2.0")}}, "bid_up") is None
    # 상한을 1.0배로 조이면 같은 1100이 막힌다
    reason = guardrail_gate._check_bid(
        {"proposal_type": "bid_up", "target_bid": 1100},
        {**ctx, "guardrail_params": {"max_auto_up_multiple": Decimal("1.0")}}, "bid_up")
    assert reason is not None and "누적 상한" in reason


# ══ PUT·현황판 라우터 — 리뷰어가 「테스트 0건」이라 한 곳 (M-D/M-E/M-F/M-G) ══════
def _client(db):
    """실제 라우터를 태운 TestClient(test_cost_drift_wiring._client와 같은 관례)."""
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _cleanup(db):
    from app.main import app
    app.dependency_overrides.clear()


def test_put_rejects_unknown_key(db):
    """★M-D: 모르는 키를 조용히 저장하면 «설정한 줄 알았는데 아무 일도 안 일어나는» 상태가 된다."""
    try:
        r = _client(db).put("/api/naver/ad/settings/guardrail-params", json={"없는키": 1})
        assert r.status_code == 400
        assert "없는키" in r.text
    finally:
        _cleanup(db)


def test_put_rejects_out_of_range_with_reason(db):
    """★M-G: 범위 밖은 **400 + 이유**로 거부한다.

    저장해 놓고 조용히 폴백시키면 화면엔 코드 상수가 뜨는데 사람은 자기가 바꾼 줄 안다.
    """
    try:
        r = _client(db).put("/api/naver/ad/settings/guardrail-params",
                            json={"max_auto_up_multiple": 99})
        assert r.status_code == 400
        assert "범위" in r.text
        # 거부됐으면 저장도 안 돼야 한다
        assert guardrail_params.get_params(db)["max_auto_up_multiple"] == \
            guardrail_gate._MAX_AUTO_UP_MULTIPLE
    finally:
        _cleanup(db)


def test_put_records_change_log_for_visibility(db):
    """★M-E: 사후 가시성 — 봉투가 바뀌면 감사 이력이 남아야 한다(§1 「자동의 전제는 가시성」).

    ★`changed_at`↔`executed_at`은 B-1 가드(D-NAO-169)가 30분 초과 어긋남을 **예외로 거부**하므로
    이 단언은 그 가드와도 정합해야 한다.
    """
    from app.models import NaverChangeLog
    try:
        r = _client(db).put("/api/naver/ad/settings/guardrail-params", json={"cooldown_hours": 4})
        assert r.status_code == 200
        rows = db.query(NaverChangeLog).filter(
            NaverChangeLog.action == "update_guardrail_params").all()
        assert len(rows) == 1
        assert "4" in (rows[0].after_value or "")
        assert abs((rows[0].changed_at - rows[0].executed_at).total_seconds()) < 60
    finally:
        _cleanup(db)


def test_get_reports_retro_freshness(db):
    """★M-F: 소급채점 신선도가 응답 **body**에 실제로 실리는가.

    2026-07 실측에서 차단 3,863건 중 2,138건(55%)이 「소급채점 stale」이었는데 **상설 표면이
    없어 조용히 늙었다.** 채점 데이터가 하나도 없으면 stale로 읽혀야 한다(「모름」을 「정상」으로
    읽으면 이 배너의 존재 이유가 사라진다).
    """
    try:
        body = _client(db).get("/api/naver/ad/settings/guardrail-params").json()
        assert {"params", "from_db_enabled", "retro_freshness"} <= set(body)
        assert body["retro_freshness"]["stale"] is True     # 채점 행 0건 → 「모름」은 stale
        assert body["retro_freshness"]["latest_asof"] is None
        assert len(body["params"]) == len(guardrail_params.SPECS)
    finally:
        _cleanup(db)
