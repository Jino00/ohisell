"""accel_gate_view — 액셀 게이트 관측 표면 (D-NAO-232, 계약 §4-④).

★이 테스트가 지키는 것은 「값이 계산된다」가 아니라 **「막힌 것이 막힌 것으로 세어진다」**이다.
세션 39 적대 리뷰에서 실쓰기 배선 7줄과 API 배선을 각각 끊어도 전건 초록이었던 전례(변이 M7·M8)가
있으므로, 여기서는 **게이트 판정식을 뒤집으면 반드시 깨지는** 단언을 쓴다.
"""
from __future__ import annotations

import pytest

from app.services.naver_ad import accel_gate_view

# 라이브 실측(2026-08-23 17:1x KST)에서 가져온 값 — 픽스처가 prod 모양과 같아야 결함을 잡는다.
BEP = 1.6833747072015681
TARGET = 1.9358841828557574
LOW, HIGH = 1.0, 1.3213


def _row(roas_naver: float, cost: float = 1000.0, conv_amt: float | None = None) -> dict:
    """★conv_amt는 기본적으로 roas_naver × cost로 «파생»시킨다.

    prod에서 `roas_naver = conv_amt ÷ cost`이므로, 셋을 독립으로 주면 픽스처가 prod에 없는
    조합을 만들고 그런 픽스처는 결함을 못 잡는다(교훈: 테스트 픽스처는 prod 모양과 같아야 한다)."""
    return {
        "roas_naver": roas_naver,
        "cost": cost,
        "conv_amt": roas_naver * cost if conv_amt is None else conv_amt,
        "campaign_id": "cmp-x",
    }


def _boards(*, starving=(), growth=(), bleeding=0, group_bep=0) -> dict:
    return {
        "starving_winners": list(starving),
        "shopping_group_growth": list(growth),
        "bleeding_keywords": [{} for _ in range(bleeding)],
        "shopping_group_bep": [{} for _ in range(group_bep)],
        "resume_candidates": [],
        "shopping_resume_candidates": [],
        "shopping_pause_candidates": [],
    }


def _build(boards, resolve=None):
    return accel_gate_view.build(
        boards, factor_low=LOW, factor_high=HIGH, target_roas=TARGET, bep_roas=BEP,
        resolve_target_roas=resolve,
    )


def test_하한에서만_막히는_행이_그_통에_들어간다():
    """roas_naver가 하한에선 목표 미달, 상한에선 통과하는 값 — 이 통이 이 표면의 존재 이유다."""
    # 1.5 × 1.0 = 1.5 < 1.9359 (막힘) · 1.5 × 1.3213 = 1.982 > 1.9359 (통과)
    out = _build(_boards(starving=[_row(1.5)]))
    assert out["buckets"]["blocked_low_only"]["count"] == 1
    assert out["buckets"]["passing_both"]["count"] == 0
    assert out["buckets"]["blocked_both"]["count"] == 0
    assert out["survive_low"] == 0
    assert out["survive_high"] == 1


def test_양끝_모두_통과_양끝_모두_차단이_갈린다():
    out = _build(_boards(starving=[_row(3.0), _row(0.5)]))
    assert out["buckets"]["passing_both"]["count"] == 1   # 3.0 × 1.0 > 목표
    assert out["buckets"]["blocked_both"]["count"] == 1   # 0.5 × 1.3213 < 목표
    assert out["buckets"]["blocked_low_only"]["count"] == 0


def test_경계값_은_목표와_정확히_같으면_통과다():
    """`roas_corrected < target_roas`가 차단 조건이므로 «같음»은 통과 — 게이트 코드와 같은 부등호."""
    exact = TARGET / LOW
    out = _build(_boards(starving=[_row(exact)]))
    assert out["buckets"]["passing_both"]["count"] == 1


def test_roas가_없는_행은_통과로_세지_않는다():
    """★교훈 #123 — 측정 못 함과 발견 0건은 같은 숫자로 쓰지 않는다."""
    out = _build(_boards(starving=[{"cost": 100, "conv_amt": 0}, _row(3.0)]))
    assert out["buckets"]["unmeasurable"] == 1
    assert out["buckets"]["passing_both"]["count"] == 1
    # 판정 불가 행은 어느 통의 건수에도 안 들어간다
    total_bucketed = sum(
        out["buckets"][k]["count"] for k in ("passing_both", "blocked_low_only", "blocked_both")
    )
    assert total_bucketed == 1
    assert out["accel_total"] == 2  # 후보 «수»에는 남아 있다(사라지면 분모가 조용히 줄어든다)


def test_총이익은_정본_산식과_같다():
    """총이익 = (Σconv_amt × factor) ÷ bep_roas − Σcost (profit_scorecard.py:133).

    산식을 두 곳에 적으면 한쪽만 고쳐지는 날이 온다 — 그래서 값으로 못 박는다."""
    # roas 1.5 → conv 1,500 / cost 1,000. BEP 1.6834이므로 하한에선 적자, 상한(×1.3213)에선 흑자 —
    # ★라이브 26건이 정확히 이 모양이었다(상한 +887,679원 / 하한 −10,589원, ref 94 §5).
    out = _build(_boards(starving=[_row(1.5, cost=1000.0)]))
    b = out["buckets"]["blocked_low_only"]
    assert b["profit_high"] == round(1500.0 * HIGH / BEP - 1000.0)
    assert b["profit_low"] == round(1500.0 * LOW / BEP - 1000.0)
    # 이 픽스처는 부호가 갈리는 구간이다 — 그게 이 표면이 존재하는 이유다
    assert b["profit_high"] > 0 > b["profit_low"]


def test_대칭_비율이_게이트_전후로_벌어진다():
    """★북극성 §7의 검사 항목 — 게이트가 하한을 쓰면 액셀만 줄어 비대칭이 벌어진다."""
    out = _build(_boards(starving=[_row(1.5), _row(3.0)], bleeding=6))
    assert out["brake_total"] == 6
    assert out["accel_total"] == 2
    assert out["ratio_selection"] == 3.0          # 6 / 2
    assert out["ratio_after_gate_low"] == 6.0     # 6 / 1  ← 하한에서 하나가 죽는다
    assert out["ratio_after_gate_high"] == 3.0    # 6 / 2
    assert out["ratio_after_gate_low"] > out["ratio_after_gate_high"]


def test_보드_집합은_세션39와_같다():
    """비교 가능해야 한다 — ref 93 §2의 집합(정지·재개 제외)이 primary."""
    assert accel_gate_view.ACCEL_BOARDS == ("starving_winners", "shopping_group_growth")
    assert accel_gate_view.BRAKE_BOARDS == ("bleeding_keywords", "shopping_group_bep")
    # 확장 정의는 별도 키로만 나간다 — 조용히 primary를 바꾸지 않는다
    out = _build(_boards(starving=[_row(3.0)], bleeding=1, group_bep=1))
    assert out["accel_total"] == 1 and out["brake_total"] == 2
    assert out["accel_total_ext"] == 1 and out["brake_total_ext"] == 2


def test_보드별_내역이_어느_보드에서_죽는지_보여준다():
    out = _build(_boards(starving=[_row(1.5)], growth=[_row(3.0)]))
    by = {r["board"]: r for r in out["by_board"]}
    assert by["starving_winners"]["blocked_low_only"] == 1
    assert by["shopping_group_growth"]["blocked_low_only"] == 0


@pytest.mark.parametrize("boards,target,bep", [
    (None, TARGET, BEP),
    ({}, TARGET, BEP),
    (_boards(starving=[_row(3.0)]), None, BEP),
    (_boards(starving=[_row(3.0)]), TARGET, None),
    (_boards(starving=[_row(3.0)]), TARGET, 0.0),
])
def test_재료가_없으면_None이지_0이_아니다(boards, target, bep):
    """0으로 위장하면 화면이 「막힌 게 없다」로 읽힌다 — 그게 제일 나쁜 거짓말이다."""
    assert accel_gate_view.build(
        boards, factor_low=LOW, factor_high=HIGH, target_roas=target, bep_roas=bep
    ) is None


def test_게이트가_읽는_끝이_페이로드에_명시된다():
    """화면이 실제 동작과 다른 것을 그리면 안 된다 — 세션 39 적대 리뷰 P1-1이 그 사고였다.

    ★★D-NAO-234 ⓐ로 **끝이 뒤집혔다**: 게이트는 «크기»가 아니라 «통과/차단»이므로 상한을 쓴다.
    이 값과 `GATE_NOTE` 문구와 실제 실행 게이트(`naver_execution_harness`)가 **셋 다 같아야**
    한다 — 셋 중 하나만 뒤처지면 화면이 배포 동작과 반대인 문장을 말한다(n=39 P1-1·n=40 P1-2).
    """
    out = _build(_boards(starving=[_row(3.0)]))
    assert out["gate_end"] == "factor_high"
    assert out["factor_low"] == LOW and out["factor_high"] == HIGH
    assert "상한" in out["gate_note"], "설명 문구가 gate_end와 반대를 말하면 안 된다"
    assert "하한을 쓰면" in out["gate_note"], "왜 상한인지(차단 증가)까지 화면이 말해야 한다"


# ══════════════════════════════════════════════════════════════════
# ★적대 리뷰 1R P1-1 상환 — 목표ROAS는 «캠페인별»이다
#   실제 게이트는 `_resolve_target_roas_float(db, campaign_id)`를 쓴다. 계정 기본값 하나로 재면
#   라이브에서 3그룹이 빠지고 3그룹이 새로 들어왔고(건수는 우연히 26으로 같았다),
#   하한 총이익이 −10,636 ↔ −17,691로 66% 어긋났다.
# ══════════════════════════════════════════════════════════════════
def test_캠페인별_목표가_판정을_바꾼다():
    """같은 행이 캠페인 목표에 따라 통과도 되고 차단도 된다 — 이게 P1-1의 실체다."""
    row = _row(2.0)                     # 2.0 × 하한 1.0 = 2.0
    row["campaign_id"] = "cmp-high"
    # 계정 기본값(1.9359)이면 통과
    assert _build(_boards(starving=[row]))["buckets"]["passing_both"]["count"] == 1
    # 그런데 이 캠페인의 실제 목표가 2.4261이면 하한에서 막힌다
    out = _build(_boards(starving=[row]), resolve=lambda cid: 2.4261)
    assert out["buckets"]["blocked_low_only"]["count"] == 1
    assert out["buckets"]["passing_both"]["count"] == 0


def test_어느_자로_쟀는지_페이로드가_밝힌다():
    """조용히 다른 자로 재고 화면엔 확정값처럼 그리는 것이 P1-1이었다."""
    assert _build(_boards(starving=[_row(3.0)]))["target_roas_source"] == "account_default"
    out = _build(_boards(starving=[_row(3.0)]), resolve=lambda cid: 2.0)
    assert out["target_roas_source"] == "per_campaign"
    assert out["target_roas_min"] == 2.0 and out["target_roas_max"] == 2.0


def test_리졸버가_None을_주면_계정_기본값으로_폴백한다():
    """캠페인 매핑이 없는 WEB_SITE 캠페인이 실제로 이 경로를 탄다(라이브 확인)."""
    out = _build(_boards(starving=[_row(1.5)]), resolve=lambda cid: None)
    assert out["buckets"]["blocked_low_only"]["count"] == 1  # 계정 기본값 1.9359 기준


def test_campaign_id가_없는_행도_폴백한다():
    row = _row(1.5)
    del row["campaign_id"]
    out = _build(_boards(starving=[row]), resolve=lambda cid: 99.0)
    assert out["buckets"]["blocked_low_only"]["count"] == 1  # 99.0이 아니라 계정 기본값으로 쟀다


def test_확장_보드_집합이_조용히_바뀌지_않는다():
    """★1R 변이 M6 상환 — `*_EXT` 집합을 바꿔도 아무 테스트가 안 깨졌다."""
    assert accel_gate_view.ACCEL_BOARDS_EXT == (
        "starving_winners", "shopping_group_growth", "resume_candidates", "shopping_resume_candidates",
    )
    assert accel_gate_view.BRAKE_BOARDS_EXT == (
        "bleeding_keywords", "shopping_group_bep", "shopping_pause_candidates",
    )


def test_창_근사_자백이_페이로드에_실린다():
    """★1R P2-2 — 화면이 확정값처럼 보이면 안 된다."""
    out = _build(_boards(starving=[_row(3.0)]))
    assert "근사" in out["window_caveat"]


# ══════════════════════════════════════════════════════════════════
# ★배선 가드 — Harness가 «실제로» 이 표면을 응답에 싣는가
#   세션 39 적대 리뷰 1R에서 변이 M7·M8(실쓰기 배선·응답 배선 절단)이 **살아남았다**.
#   격리 호출만 검사하면 「Harness가 그걸 쓰는가」는 아무도 안 본다
#   (전역 §2: 격리 성공은 필요조건이지 충분조건이 아니다).
#   ⇒ diagnosis.py에서 `"accel_gate": ...` 한 줄을 지우면 아래가 깨져야 한다.
# ══════════════════════════════════════════════════════════════════
from datetime import date  # noqa: E402
from decimal import Decimal  # noqa: E402

from app.services.naver_ad import diagnosis  # noqa: E402

_BOARD_FNS = [
    "bleeding_keywords", "starving_winners", "expansion_bucket", "shopping_group_bep",
    "shopping_group_growth", "vicious_cycle_flags", "resume_candidates",
    "shopping_pause_candidates", "shopping_resume_candidates", "floor_wait_units",
]


@pytest.fixture()
def wired(monkeypatch):
    """보드가 실제 행을 내놓는 상태의 build_diagnosis — 액셀 2건(1건은 하한에서만 차단)·브레이크 6건."""
    payloads = {
        "starving_winners": [_row(1.5), _row(3.0)],   # 1.5 = 하한에서만 차단 · 3.0 = 양끝 통과
        "bleeding_keywords": [{} for _ in range(6)],
    }
    for name in _BOARD_FNS:
        monkeypatch.setattr(
            diagnosis.diag, name,
            (lambda n: (lambda *a, **k: {} if n == "expansion_bucket" else payloads.get(n, [])))(name),
        )
    for name in ("exclusion_candidates", "keyword_triage", "pause_candidates",
                 "shopping_lever_resume_candidates"):
        monkeypatch.setattr(diagnosis.diag, name, lambda *a, **k: [])
    monkeypatch.setattr(
        diagnosis, "correction_factor",
        lambda db, d: {"factor": Decimal(str(HIGH)), "factor_low": Decimal(str(LOW)),
                       "factor_high": Decimal(str(HIGH)), "factor_point": Decimal(str(HIGH)),
                       "source": "actual_revenue_ratio"},
    )
    monkeypatch.setattr(diagnosis.campaign_target_resolver,
                        "account_default_bep_roas", lambda db: Decimal(str(BEP)))
    monkeypatch.setattr(diagnosis.campaign_target_resolver,
                        "account_default_target_roas", lambda db: Decimal(str(TARGET)))
    monkeypatch.setattr(diagnosis.campaign_target_resolver, "resolve_target_roas",
                        lambda db, cid: {"target_roas": None, "source": "account_default"})


def test_harness_응답이_액셀_게이트를_싣는다(wired):
    """M8형 변이 상환 — `"accel_gate": ...` 한 줄을 지우면 여기서 죽는다."""
    out = diagnosis.build_diagnosis(None, date(2026, 8, 9), date(2026, 8, 23))
    gate = out["accel_gate"]
    assert gate is not None, "응답에서 accel_gate가 사라지면 화면에 카드가 통째로 안 뜬다"
    assert gate["accel_total"] == 2
    assert gate["brake_total"] == 6
    assert gate["survive_low"] == 1, "하한 게이트가 1건을 죽인다"
    assert gate["survive_high"] == 2
    assert gate["buckets"]["blocked_low_only"]["count"] == 1
    # ★대칭이 게이트에서 벌어지는 것이 이 표면의 요지다(북극성 §7)
    assert gate["ratio_selection"] == 3.0
    assert gate["ratio_after_gate_low"] == 6.0


def test_harness가_게이트에_하한을_넘긴다(wired, monkeypatch):
    """★배선이 상한을 넘기면 「막힌 게 없다」로 그려진다 — 실제 게이트와 반대인 화면.

    세션 39 P1-1이 정확히 그 사고(카드 문구가 배포 동작과 정반대)였고, 그때는
    **프론트 테스트가 그 거짓을 단언**하고 있었다."""
    seen = {}
    real = diagnosis.accel_gate_view.build

    def spy(boards, **kw):
        seen.update(kw)
        return real(boards, **kw)

    monkeypatch.setattr(diagnosis.accel_gate_view, "build", spy)
    diagnosis.build_diagnosis(None, date(2026, 8, 9), date(2026, 8, 23))
    assert seen["factor_low"] == LOW, "게이트 관측에 상한을 넘기면 실제 동작과 다른 화면이 된다"
    assert seen["factor_high"] == HIGH
    # ★1R P1-1 상환 배선 가드 — 리졸버를 안 넘기면 계정 기본값 하나로 재고,
    #   화면이 실제 게이트와 «다른 그룹»을 지목한다.
    assert seen.get("resolve_target_roas") is not None, "캠페인별 목표 리졸버가 안 넘어갔다"


def test_조기반환_가지도_같은_키를_낸다(wired, monkeypatch):
    """BEP 산출 불가 가지 — 키가 아예 없으면 프론트가 `undefined`를 만난다(D-NAO-204 자리)."""
    monkeypatch.setattr(diagnosis.campaign_target_resolver,
                        "account_default_bep_roas", lambda db: None)
    out = diagnosis.build_diagnosis(None, date(2026, 8, 9), date(2026, 8, 23))
    assert out["boards"] is None
    assert "accel_gate" in out and out["accel_gate"] is None
