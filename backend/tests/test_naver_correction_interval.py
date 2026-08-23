# test_naver_correction_interval.py — D-NAO-230 「총이익의 자」 안3 «구간 자» + D-NAO-231 배정
#
# 계약: docs/PLAN_naver-profit-yardstick-review.md (승인됨) · 전수표: docs/references/93_*.md
#
# ## 이 파일이 지키는 약속 셋
# ①`correction_factor`는 점추정 하나가 아니라 **구간**을 준다(하한=min(1,p) · 상한=max(1,p)).
# ②**D-NAO-231(Jino 결정 2026-08-23)**: 「액셀=하한」은 «후보 선정»이 아니라 «실쓰기의 크기»에
#   적용한다 ⇒ 진단 보드는 **전부 상한**이고, 하한은 실쓰기 층만 쓴다. 이걸 어기면 라이브에서
#   액셀 후보가 220→195건으로 줄어 브레이크 편중이 3.02:1 → 3.41:1로 벌어진다(실측 2026-08-23)
#   — 그게 북극성 §7의 「ROAS 방어로의 표류」이자 D-NAO-85(ROAS +7%·매출 −52%)의 볼륨판 재현이고,
#   계약 §6 금지선 2가 막으려던 바로 그 배포다.
# ③응답이 구간 양끝을 **끝까지 들고 나간다**(표면 계약 §5-5) — 키가 하나라도 빠지면 화면이
#   점추정으로 되돌아간다(D-NAO-204 `response_model` 키 삭제 사고와 같은 자리).
from datetime import date
from decimal import Decimal

import pytest

from app.services.naver_ad import bid_simulator, diagnosis
from app.services.naver_ad.diagnosis import _as_interval, _factor_payload


# ══════════════════════════════════════════════════════════════════
# ① 구간의 불변식
# ══════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "point,expect_low,expect_high",
    [
        ("1.3133", "1", "1.3133"),   # 라이브 방향(계수>1): 하한이 「보정 없음」
        ("1", "1", "1"),             # 폴백: 구간이 한 점으로 접힌다 = 종전과 동일
        ("0.72", "0.72", "1"),       # ★계수<1로 재확정돼도 «하한=상향 쪽 보수값» 불변식 유지
    ],
)
def test_interval_endpoints(point, expect_low, expect_high):
    got = _as_interval(Decimal(point), source="t")
    assert got["factor_low"] == Decimal(expect_low)
    assert got["factor_high"] == Decimal(expect_high)
    assert got["factor_point"] == Decimal(point), "점추정 원값은 감사용으로 보존된다"
    assert got["factor"] == got["factor_high"], "하위호환 키 `factor`는 상한이다"
    assert got["factor_low"] <= got["factor_high"]


def test_interval_never_inverts_for_any_point():
    """어떤 점추정이 와도 하한 ≤ 상한이고 점추정은 구간 안에 있다."""
    for p in ["0.01", "0.5", "0.999", "1", "1.001", "2.6", "10"]:
        iv = _as_interval(Decimal(p))
        assert iv["factor_low"] <= iv["factor_point"] <= iv["factor_high"]


# ══════════════════════════════════════════════════════════════════
# ③ 표면 계약 — 응답이 구간을 끝까지 들고 나간다
# ══════════════════════════════════════════════════════════════════
def test_factor_payload_carries_every_end_as_float():
    payload = _factor_payload(_as_interval(Decimal("1.3133"), source="actual_revenue_ratio"))
    for key in ("factor", "factor_low", "factor_high", "factor_point"):
        assert key in payload, f"{key}가 응답에서 사라지면 화면이 점추정으로 되돌아간다"
        assert isinstance(payload[key], float)
    assert payload["factor_low"] == 1.0
    assert payload["factor_high"] == pytest.approx(1.3133)


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
