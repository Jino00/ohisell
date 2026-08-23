# test_naver_correction_interval.py — D-NAO-230 「총이익의 자」 안3 «구간 자» + D-NAO-231 배정
#
# 계약: docs/PLAN_naver-profit-yardstick-review.md (승인됨) · 전수표: docs/references/93_*.md
#
# ## 이 파일이 지키는 약속 넷
# ①`correction_factor`는 점추정 하나가 아니라 **구간**을 준다.
#   ★★**끝값은 D-NAO-234(Jino 결정 2026-08-23)로 개정됐다** — 하한 = `min(0.827, p)`.
#   옛 하한 1.0은 「보정 없음」을 하한이라 부른 것이지 근거가 아니었고, inflowPath 실측
#   (ref 95: 「광고>」5종 매출 ÷ direct 전환매출 = 0.8289)이 그보다 낮은 **정합한 측정**을
#   주면서 「하한 1.0은 보수적이지 않다」가 확정됐다.
# ②**층은 셋이다 — 둘이 아니다**(D-NAO-234 ⓐ가 D-NAO-231을 개정):
#     선정=상한 · **게이트(통과/차단)=상한** · 크기=하한.
#   D-NAO-231은 «선정»과 «크기» 둘로만 갈랐고 그 사이 «게이트»에 배정이 없었다. 배정이 없으니
#   게이트들이 옛 코드대로 하한을 계속 썼고, 게이트에서 하한은 «보수적 크기»가 아니라
#   **차단 증가**로 뒤집힌다 — ref 94 §6 실측: 액셀 221→195건 · 대칭 3.005:1 → 3.405:1.
#   그게 북극성 §7의 「ROAS 방어로의 표류」이자 D-NAO-85(ROAS +7%·매출 −52%)의 볼륨판이다.
# ③응답이 구간 양끝을 **끝까지 들고 나간다**(표면 계약 §5-5) — 키가 하나라도 빠지면 화면이
#   점추정으로 되돌아간다(D-NAO-204 `response_model` 키 삭제 사고와 같은 자리).
# ④★**하한의 유도식은 한 곳에만 있다**(`correction_interval`) — 예전엔 `diagnosis`와
#   `bid_simulator` 두 곳에 복사돼 있어서, 상수를 한쪽만 바꾸면 다른 경로가 옛 하한으로
#   계속 돌면서 테스트는 양쪽 다 초록이 된다.
import ast
from datetime import date, datetime
from decimal import Decimal
import pathlib

import pytest

from app.services.naver_ad import bid_simulator, correction_interval, diagnosis
from app.services.naver_ad.diagnosis import _as_interval, _factor_payload

FLOOR = correction_interval.CORRECTION_FACTOR_FLOOR  # 0.827 (D-NAO-234)


# ══════════════════════════════════════════════════════════════════
# ① 구간의 불변식
# ══════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "point,expect_low,expect_high",
    [
        ("1.3133", "0.827", "1.3133"),  # 라이브 방향(계수>1): 하한 = inflowPath 실측(D-NAO-234)
        ("1", "0.827", "1"),            # ★옛 「1이면 한 점으로 접힘」은 더 이상 참이 아니다
        ("0.72", "0.72", "0.827"),      # ★계수<0.827로 재확정돼도 «하한=상향 쪽 보수값» 불변식 유지
    ],
)
def test_interval_endpoints(point, expect_low, expect_high):
    got = _as_interval(Decimal(point), source="t")
    assert got["factor_low"] == Decimal(expect_low)
    assert got["factor_high"] == Decimal(expect_high)
    assert got["factor_point"] == Decimal(point), "점추정 원값은 감사용으로 보존된다"
    assert got["factor"] == got["factor_high"], "하위호환 키 `factor`는 상한이다"
    assert got["factor_low"] <= got["factor_high"]


def test_the_floor_is_the_inflowpath_measurement_not_no_correction():
    """★D-NAO-234의 본체 — 하한이 「보정 없음(1.0)」이 아니라 **실측값**인가.

    이 단언이 없으면 하한을 슬그머니 1.0으로 되돌려도 위 파라미터 테스트만 고치면 초록이
    된다. 값과 «그 값이 무엇인가»를 따로 못 박는다.
    """
    assert FLOOR == Decimal("0.827")
    assert FLOOR < correction_interval.NO_CORRECTION, (
        "하한이 「보정 없음」보다 크면 그건 하한이 아니다 — 실측이 반증한 바로 그 상태다"
    )
    assert _as_interval(Decimal("1.3291"))["factor_low"] == FLOOR


def test_interval_never_inverts_for_any_point():
    """어떤 점추정이 와도 하한 ≤ 상한이고 점추정은 구간 안에 있다."""
    for p in ["0.01", "0.5", "0.999", "1", "1.001", "2.6", "10"]:
        iv = _as_interval(Decimal(p))
        assert iv["factor_low"] <= iv["factor_point"] <= iv["factor_high"]


def test_unavailable_correction_degenerates_to_no_correction_not_to_the_floor(monkeypatch):
    """★★근거가 없으면 하한도 없다 — 계약 §4 금지선 5.

    실주문 매출이 없어 계수를 못 만드는 경우까지 0.827을 씌우면, 아무 근거 없이 매출을
    17.3% 깎는 보정이 전 소비처에 걸린다. 그때는 구간이 **[1, 1]로 퇴화**해야 한다.
    """
    monkeypatch.setattr(diagnosis.diag, "earliest_real_data_date", lambda *a, **k: None)
    out = diagnosis.correction_factor(None, date(2026, 8, 23))
    assert out["source"] == "unavailable"
    assert out["factor_low"] == Decimal("1") and out["factor_high"] == Decimal("1"), (
        "근거 없는 하한을 씌우면 안 된다"
    )
    assert "factor_low_source" not in _factor_payload(out), (
        "없는 근거를 화면이 말하게 하면 안 된다"
    )


def test_the_floor_formula_lives_in_exactly_one_place():
    """★유도식이 두 곳에 복사돼 있으면 한쪽만 고쳐도 테스트가 전부 초록이다(교훈 #348의 값 버전).

    `diagnosis`도 `bid_simulator`도 자기 파일에 하한 상수·유도식을 다시 적지 않는다.
    """
    base = pathlib.Path(diagnosis.__file__).parent
    for fname in ("diagnosis.py", "bid_simulator.py"):
        src = (base / fname).read_text()
        # 주석·docstring은 「왜 0.827인가」를 **설명해야** 하므로 문자열 스캔으로는 못 가른다.
        # ast로 파싱해 docstring을 걷어낸 뒤 **실행되는 코드**만 본다.
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                body = node.body
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                        and isinstance(body[0].value.value, str):
                    body.pop(0)
        code = ast.unparse(tree)
        assert "min(Decimal('1')" not in code, f"{fname}에 하한 유도식이 복사돼 있다"
        assert "0.827" not in code, f"{fname}에 하한 상수가 복사돼 있다 — 정본은 correction_interval"
        assert "correction_interval" in code, f"{fname}이 하한 정본을 안 쓴다"


# ══════════════════════════════════════════════════════════════════
# ③ 표면 계약 — 응답이 구간을 끝까지 들고 나간다
# ══════════════════════════════════════════════════════════════════
def test_factor_payload_carries_every_end_as_float():
    payload = _factor_payload(_as_interval(Decimal("1.3133"), source="actual_revenue_ratio"))
    for key in ("factor", "factor_low", "factor_high", "factor_point"):
        assert key in payload, f"{key}가 응답에서 사라지면 화면이 점추정으로 되돌아간다"
        assert isinstance(payload[key], float)
    assert payload["factor_low"] == pytest.approx(0.827)
    assert payload["factor_high"] == pytest.approx(1.3133)


def test_payload_carries_the_basis_of_the_floor_not_just_its_value():
    """★D-NAO-234 표면 요건 — 값만 내보내면 화면이 「0.827이 어디서 왔나」를 못 말한다.

    계약 §4 금지선 5: 가정(마지막터치·창·플러스스토어 처분) 병기 없이 새 표면에
    내보내지 않는다. 근거 문자열이 사라지는 변이를 이 단언이 잡는다.
    """
    payload = _factor_payload(_as_interval(Decimal("1.3291"), source="actual_revenue_ratio"))
    for key in ("factor_low_source", "factor_low_window", "factor_low_evidence",
                "factor_low_caveat", "factor_low_window_spread"):
        assert payload.get(key), f"{key}가 없으면 화면이 근거 없는 숫자를 그린다"
    assert "2026-07-25" in payload["factor_low_window"], "계수는 창과 함께 말한다(계약 §3-5)"
    assert "플러스스토어" in payload["factor_low_caveat"], (
        "하한에 붙박인 [미상]을 화면이 말해야 한다 — 포함하면 1.067로 올라간다"
    )
    assert "95_inflowpath" in payload["factor_low_evidence"], "재현 문서 좌표가 실려야 한다"


# ══════════════════════════════════════════════════════════════════
# ② D-NAO-231 — 진단 보드(«선정»)는 전부 상한, 하한은 한 보드도 받지 않는다
# ══════════════════════════════════════════════════════════════════
_LOW = Decimal("7")    # 구별용 표지값 — 실제 계수와 무관
_HIGH = Decimal("9")

_BOARD_FNS = [
    "bleeding_keywords", "starving_winners", "expansion_bucket", "shopping_group_bep",
    "shopping_group_growth", "vicious_cycle_flags", "resume_candidates",
    "shopping_pause_candidates", "shopping_resume_candidates", "floor_wait_units",
]


@pytest.fixture()
def board_spy(monkeypatch):
    """보드 함수마다 «받은 계수»를 기록한다."""
    seen: dict[str, list] = {}

    def make(name):
        def _fn(*args, **kwargs):
            vals = [a for a in args if isinstance(a, Decimal)]
            vals += [v for v in kwargs.values() if isinstance(v, Decimal)]
            seen[name] = vals
            return [] if name != "expansion_bucket" else {}
        return _fn

    for name in _BOARD_FNS:
        monkeypatch.setattr(diagnosis.diag, name, make(name))
    # 보드가 아닌 나머지(계수를 안 받는 것들)는 빈 결과로 눌러 둔다.
    for name in ("exclusion_candidates", "keyword_triage", "pause_candidates",
                 "shopping_lever_resume_candidates"):
        monkeypatch.setattr(diagnosis.diag, name, lambda *a, **k: [])

    monkeypatch.setattr(
        diagnosis, "correction_factor",
        lambda db, d: {"factor": _HIGH, "factor_low": _LOW, "factor_high": _HIGH,
                       "factor_point": _HIGH, "source": "actual_revenue_ratio"},
    )
    monkeypatch.setattr(diagnosis.campaign_target_resolver,
                        "account_default_bep_roas", lambda db: Decimal("1.68"))
    monkeypatch.setattr(diagnosis.campaign_target_resolver,
                        "account_default_target_roas", lambda db: Decimal("1.94"))
    monkeypatch.setattr(diagnosis.campaign_target_resolver, "resolve_target_roas",
                        lambda db, cid: {"target_roas": None, "source": "account_default"})
    return seen


def test_no_board_ever_receives_the_lower_end(board_spy):
    """★핵심 회귀 가드 — 보드가 하한을 받으면 액셀 후보가 조용히 줄어든다(D-NAO-231)."""
    diagnosis.build_diagnosis(None, date(2026, 8, 9), date(2026, 8, 23))
    assert set(board_spy) == set(_BOARD_FNS), "보드 하나라도 안 불리면 이 가드가 헛돈다"
    for name, vals in board_spy.items():
        assert _LOW not in vals, (
            f"{name}이(가) 구간 하한을 받았다 — 선정층은 상한이어야 한다(D-NAO-231). "
            "이대로 배포하면 브레이크는 그대로인데 액셀만 줄어 3.02:1 → 3.41:1로 벌어진다."
        )


def test_every_factor_consuming_board_receives_the_upper_end(board_spy):
    diagnosis.build_diagnosis(None, date(2026, 8, 9), date(2026, 8, 23))
    for name, vals in board_spy.items():
        assert _HIGH in vals, f"{name}이(가) 상한을 못 받았다"


def test_floor_wait_matches_shopping_pause_end(board_spy):
    """무액션 여집합 정합 — 이 둘이 다른 끝을 쓰면 「대기」와 「정지 후보」가 겹치거나 빈다."""
    diagnosis.build_diagnosis(None, date(2026, 8, 9), date(2026, 8, 23))
    assert board_spy["floor_wait_units"] == board_spy["shopping_pause_candidates"]


# ══════════════════════════════════════════════════════════════════
# ② bid_simulator — 방향은 상한(선정), 크기만 하한(실쓰기)
# ══════════════════════════════════════════════════════════════════
def _sim(current_bid, cf, rank_bid=None, clk=100, conv_amt=300_000, **kw):
    row = {"clk": clk, "conv_amt": conv_amt, "bid_amt": current_bid}
    agg = {"clk": clk, "conv_amt": conv_amt}
    return bid_simulator.simulate_bid(
        row, Decimal("2"), group_agg=agg, campaign_agg=agg, account_agg=agg,
        correction_factor=cf, estimate={"rank_bid": rank_bid} if rank_bid else None, **kw,
    )


def test_single_factor_of_one_is_bit_identical_to_legacy():
    """계수 1(기본값·폴백)이면 구간이 한 점 → 종전 동작과 완전히 같다(하위호환)."""
    got = _sim(1000, Decimal("1"))
    assert got["economic_ceiling_low"] == got["economic_ceiling_high"] == got["economic_ceiling"]
    assert got["direction_low"] == got["direction_high"] == got["direction"]


def test_direction_comes_from_the_upper_end_not_the_lower():
    """방향은 «선정»이라 상한이 정한다 — 하한이 정하면 상향 후보가 조용히 사라진다."""
    got = _sim(10, Decimal("1.5"))
    assert got["direction_high"] == "up"
    assert got["direction"] == got["direction_high"]


def test_up_uses_the_lower_end_for_size():
    """올릴 땐 크기만 하한으로 누른다 — 상한 크기로 올리면 실쓰기가 보수적이지 않다."""
    got = _sim(10, Decimal("1.5"))
    assert got["direction"] == "up"
    assert got["recommended_bid"] == got["economic_ceiling_low"]
    assert got["economic_ceiling_low"] < got["economic_ceiling_high"], "구간이 접히면 시험이 헛돈다"


def test_up_becomes_hold_when_the_floor_cannot_clear_current_bid():
    """상한은 올리라 하고 하한은 아니라 하면 → 올리지 않는다(현재보다 낮은 값으로 up 금지)."""
    high = bid_simulator.affordable_ceiling(
        (bid_simulator.pooled_rpc({"clk": 100, "conv_amt": 300_000}, {"clk": 100, "conv_amt": 300_000},
                                  {"clk": 100, "conv_amt": 300_000}, {"clk": 100, "conv_amt": 300_000})
         * Decimal("1.5")).quantize(Decimal("0.0001")), Decimal("2"))
    low = bid_simulator.affordable_ceiling(
        bid_simulator.pooled_rpc({"clk": 100, "conv_amt": 300_000}, {"clk": 100, "conv_amt": 300_000},
                                 {"clk": 100, "conv_amt": 300_000}, {"clk": 100, "conv_amt": 300_000}),
        Decimal("2"))
    assert low < high, "픽스처 전제: 하한 상한선 < 상한 상한선"
    current = (low + high) // 2  # 하한 위, 상한 아래
    got = _sim(current, Decimal("1.5"))
    assert got["direction_high"] == "up" and got["direction_low"] == "down"
    assert got["direction"] == "hold"
    assert got["basis"] == "interval_floor_blocks_up"
    assert got["recommended_bid"] == current, "hold는 현재 입찰을 그대로 둔다"


def test_down_uses_the_upper_end_so_the_brake_is_gentler():
    """내릴 땐 상한 — 총이익을 후하게 봐서 살릴 수 있는 키워드를 덜 죽인다(D-NAO-59 방향)."""
    got = _sim(100_000, Decimal("1.5"))
    assert got["direction"] == "down"
    assert got["recommended_bid"] == got["economic_ceiling_high"]
    assert got["recommended_bid"] > got["economic_ceiling_low"], "하한으로 내렸으면 더 깎였다"


def test_explicit_ends_override_the_derived_ones():
    got = _sim(10, Decimal("1"), correction_factor_low=Decimal("1"),
               correction_factor_high=Decimal("3"))
    assert got["economic_ceiling_high"] > got["economic_ceiling_low"]


# ══════════════════════════════════════════════════════════════════
# ★적대 리뷰 1R에서 «생존»한 변이 둘을 죽이는 가드
#   M7 = 실쓰기 하한 배선 7줄을 전부 되돌려도 전건이 초록이었다
#   M8 = build_diagnosis 응답의 구간 배선(`_factor_payload`)을 끊어도 전건이 초록이었다
#   ⇒ 이 PR의 «실제 동작 변화»가 걸린 두 자리를 아무 테스트도 안 지키고 있었다.
#      그 구멍으로 P1-2(서보 상한이 하한으로 안 바뀜)가 실제로 빠져나갔다.
# ══════════════════════════════════════════════════════════════════
_WIDE = {"factor": Decimal("2"), "factor_low": Decimal("1"), "factor_high": Decimal("2"),
         "factor_point": Decimal("2"), "source": "actual_revenue_ratio",
         "window_from": "2026-07-25", "window_to": "2026-08-23"}


def test_build_diagnosis_response_carries_the_interval(board_spy):
    """M8 상환 — Harness 출력이 구간 키를 «끝까지» 들고 나가는가.

    `_factor_payload`를 격리 호출로만 검사하면 「Harness가 그걸 쓰는가」는 아무도 안 본다
    (전역 §2: 격리 성공은 필요조건이지 충분조건이 아니다). 키가 빠지면 화면은 점추정으로
    «되돌아가는» 게 아니라 `factor_low.toFixed`에서 TypeError로 **보드가 통째 백지**가 된다
    — D-NAO-204(`response_model`이 배너 키를 삭제)와 같은 자리다.
    """
    out = diagnosis.build_diagnosis(None, date(2026, 8, 9), date(2026, 8, 23))
    cf = out["correction_factor"]
    for key in ("factor", "factor_low", "factor_high", "factor_point"):
        assert key in cf, f"응답에서 {key}가 사라지면 진단 보드가 백지가 된다"
        assert isinstance(cf[key], float)


def test_build_diagnosis_error_branch_also_carries_the_interval(board_spy, monkeypatch):
    """BEP/목표ROAS 산출 불가로 조기 반환하는 가지도 같은 계약을 지킨다."""
    monkeypatch.setattr(diagnosis.campaign_target_resolver,
                        "account_default_bep_roas", lambda db: None)
    out = diagnosis.build_diagnosis(None, date(2026, 8, 9), date(2026, 8, 23))
    assert out["boards"] is None
    for key in ("factor", "factor_low", "factor_high", "factor_point"):
        # ★적대 리뷰 2R P2-b: `key in`만 단언하면 이 가지에서 변이 M8이 살아남는다
        #   (Decimal 그대로 나가도 통과) — 정상 가지와 «같은» 강도로 단언한다.
        assert key in out["correction_factor"]
        assert isinstance(out["correction_factor"][key], float)


@pytest.mark.parametrize("target_type", ["keyword", "campaign"])
def test_execution_guard_gate_uses_the_upper_end_at_runtime(monkeypatch, target_type):
    """★★D-NAO-234 ⓐ — 증액 가드의 `roas_corrected`가 «상한» 기준인가. **실행해서** 잰다.

    ⚠️이 테스트는 원래 **소스 grep**이었다(줄에 `factor_low`가 있나만 봤다). 직전 세션
    적대 리뷰 P2-a가 「소스 grep은 런타임을 검사하지 않는다」고 지적했고, 실제로 ref 94에서
    **배선 절단 변이가 grep 테스트를 통과한 채 생존**했다. 그래서 두 끝을 벌린 픽스처로
    `_build_guardrail_context`를 **호출**해 나온 값을 단언한다.

    증액 가드는 «얼마나 올리나»가 아니라 «올려도 되나»를 가르는 게이트다 ⇒ 상한.
    하한을 쓰면 하한이 내려갈수록 target_roas 미달 판정이 늘어 **차단이 증가**한다.
    """
    from app.services.naver_ad import naver_execution_harness as harness

    agg = {"cost": 1_000_000, "conv_amt": 2_000_000}  # roas_naver = 2.0
    low, high = Decimal("0.5"), Decimal("2.0")  # 두 끝을 크게 벌려 어느 쪽을 썼는지 갈라낸다
    monkeypatch.setattr(harness, "compute_correction_factor",
                        lambda *a, **k: {"factor_low": low, "factor_high": high, "factor": high})
    monkeypatch.setattr(harness.account_diagnosis, "keyword_window_agg", lambda *a, **k: agg)
    monkeypatch.setattr(harness.account_diagnosis, "campaign_window_agg", lambda *a, **k: agg)
    monkeypatch.setattr(harness.account_diagnosis, "adgroup_window_agg", lambda *a, **k: agg)
    monkeypatch.setattr(harness.naver_sa_writer, "get_keyword", lambda *a, **k: {"bidAmt": 100})
    monkeypatch.setattr(harness.naver_sa_writer, "get_campaign", lambda *a, **k: {"dailyBudget": 100})
    monkeypatch.setattr(harness, "_resolve_target_roas_float", lambda *a, **k: 1.9)
    monkeypatch.setattr(harness, "_latest_hourly_snapshot_fields", lambda *a, **k: (0, 0))
    monkeypatch.setattr(harness, "compute_change_cadence", lambda *a, **k: (None, 0))
    monkeypatch.setattr(harness.guardrail_params, "get_params", lambda *a, **k: {})

    class _P:
        target_type = None
        target_id = "t1"
        campaign_id = "c1"
        adgroup_id = "g1"
        proposal_type = "bid_up"
        approval_source = "console"   # is_auto_exec 판별용(사람 승인 경로)
        id = 1
        status = "pending"
    p = _P()
    p.target_type = target_type

    ctx = harness._build_guardrail_context(None, p, datetime(2026, 8, 23, 12, 0))
    assert ctx["roas_corrected"] == pytest.approx(2.0 * float(high)), (
        "게이트가 하한을 쓰면 하한이 내려갈수록 차단이 늘어 브레이크가 커진다(D-NAO-234 ⓐ) — "
        f"관측 {ctx['roas_corrected']}"
    )
    assert ctx["roas_corrected"] != pytest.approx(2.0 * float(low)), "하한을 쓰고 있다"


def test_no_boolean_gate_reads_the_lower_end_anywhere():
    """★★D-NAO-234 ⓐ — «게이트» 층에 하한이 한 곳도 남아 있으면 안 된다.

    D-NAO-231이 «선정»·«크기» 둘로만 갈랐을 때 그 사이의 «게이트»에 배정이 없었고, 배정이
    없으니 게이트들이 옛 하한을 계속 썼다. 그 구멍이 ref 94 §6에서 액셀 −26건·대칭
    3.005→3.405:1로 실측됐다. 새 게이트가 하한을 집어 들면 **같은 구멍이 다시 열린다**.

    ⚠️이 단언은 소스 스캔이다 — 위의 런타임 spy를 «대체»하는 게 아니라, 런타임 spy가 못 도는
    나머지 게이트에 대한 **회귀 저지선**이다. 새 게이트를 추가하면서 하한을 집으면 여기서 걸린다.
    """
    base = pathlib.Path(diagnosis.__file__).parent
    gate_files = ("expansion_allocator.py", "expansion_pressure.py", "auto_operator.py",
                  "naver_execution_harness.py")
    for fname in gate_files:
        src = (base / fname).read_text()
        offenders = [
            ln.strip() for ln in src.splitlines()
            if ('factor_info["factor_low"]' in ln or 'correction["factor_low"]' in ln)
            and not ln.strip().startswith("#")
        ]
        assert offenders == [], (
            f"{fname}: 게이트가 하한을 직접 읽고 있다 — 하한은 «크기» 산식에만 쓴다: {offenders}"
        )


def test_size_layer_still_uses_the_lower_end():
    """★반대쪽 저지선 — ⓐ 재배정이 «크기» 층까지 상한으로 밀어버리면 안 된다.

    게이트를 상한으로 옮기는 변경이 과하게 번지면 서보 경제성 상한까지 상한이 되어
    입찰이 +31%(1,030→1,350원) 뛴다. 크기 층은 하한 그대로여야 한다.
    """
    src = pathlib.Path(diagnosis.__file__).parent.joinpath("auto_operator.py").read_text()
    assert 'servo_correction_factor_low = Decimal(str(_cf["factor_low"]))' in src, (
        "서보 «크기»는 하한이다 — 상한으로 바뀌면 실입찰이 +31% 튄다"
    )


def test_servo_ceiling_uses_the_lower_end():
    """★적대 리뷰 1R P1-2 상환 — 시간당 서보의 «실입찰 크기» 상한이 하한을 쓰는가.

    방향(up/down)은 밴드 관제(`_judge_hourly`)가 이미 정했고 계수와 무관하다. 그러니 이
    자리에서 계수가 정하는 것은 오직 «얼마나 올리는가»이고, 그게 D-NAO-231이 「하한」으로
    못 박은 층이다. 상한을 쓰면 하한이 정당화하는 값보다 높은 입찰까지 올라간다
    (라이브 계수 1.3133 기준 1030원 → 1350원, +31%).
    """
    src = pathlib.Path(diagnosis.__file__).parent.joinpath("auto_operator.py").read_text()
    assert 'servo_correction_factor_low = Decimal(str(_cf["factor_low"]))' in src
    assert 'servo_correction_factor = Decimal(str(_cf["factor_high"]))' in src
    # 경제성 상한 호출은 하한을, estimate 직행 스텝은 두 끝을 다 받아야 한다.
    assert "servo_agg=servo_agg, correction_factor=servo_correction_factor_low," in src
    assert "correction_factor_low=servo_correction_factor_low," in src


def test_simulate_bid_direction_is_unchanged_when_only_the_floor_is_handed_down():
    """★서보 수정의 함정 가드 — 하한 «하나만» 넘기면 direction이 바뀐다.

    `simulate_bid`에 하한만 주면 내부 유도가 `cf_high = max(1, 하한) = 1`이 되어 상향
    판정이 사라진다(= 「액셀 판정 불변」 위반). 그래서 서보는 **두 끝을 다** 넘긴다.
    """
    both = _sim(10, Decimal("1"), correction_factor_low=Decimal("1"),
                correction_factor_high=Decimal("2"))
    floor_only = _sim(10, Decimal("1"))  # 하한만 넘긴 것과 동형(상한이 1로 접힘)
    assert both["direction_high"] == "up"
    assert both["economic_ceiling_high"] > floor_only["economic_ceiling_high"], (
        "두 끝을 안 넘기면 상한이 1로 접혀 direction 판정 재료가 달라진다"
    )
