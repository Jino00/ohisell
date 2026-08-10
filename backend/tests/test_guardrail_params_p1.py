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

from datetime import datetime, timedelta
from decimal import Decimal

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
    assert p["max_change_pct"] == guardrail_gate._MAX_CHANGE_PCT
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
    _kv(db, '{"cooldown_hours": 4, "max_change_pct": "말이 안 되는 값"}')
    p = guardrail_params.get_params(db)
    assert p["cooldown_hours"] == 4                                    # 살아남고
    assert p["max_change_pct"] == guardrail_gate._MAX_CHANGE_PCT       # 이건 폴백


@pytest.mark.parametrize("payload", [
    '{"max_change_pct": "0.9"}',            # 상한(0.30) 초과 — 폭주
    '{"max_change_pct": "0.01"}',           # 하한(0.05) 미만 — 사실상 정지
    '{"cooldown_hours": 0}',                # 진동 방어 해제
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
    assert (rows["max_change_pct"]["source"], rows["max_change_pct"]["rejected"]) == ("code", False)


def test_describe_marks_unparseable_value_as_code_not_db(db):
    """★타입이 깨진 값도 «db에서 온 값»으로 보이면 안 된다.

    범위 밖(위 테스트)과 **파싱 실패**는 코드 경로가 다르다. 파싱 실패가 조용히 `source="db"`로
    뜨면, 화면은 「DB 값으로 돌고 있다」고 말하는데 실제로는 코드 상수가 돌고 있다 —
    이 화면이 막으려던 바로 그 착시다(«기록됐다 ≠ 코드가 읽는다»).
    """
    _kv(db, '{"max_change_pct": "말이 안 되는 값"}')
    row = {r["key"]: r for r in guardrail_params.describe(db)}["max_change_pct"]
    assert row["source"] == "code"
    assert row["rejected"] is True
    assert row["value"] == float(guardrail_gate._MAX_CHANGE_PCT)


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
