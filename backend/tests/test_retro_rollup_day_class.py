# test_retro_rollup_day_class.py — 평시/주말/공휴일 분리 집계 (D-NAO-267)
#
# M2 계약 §4-A T1 = ref 65 S2-ⓐ. 북극성 §5-3 ②가 「확정 지식의 판정면 주입」 **첫 사례**로
# 이름까지 지목한 배선이다: *"첫 사례는 이미 설계돼 있다: A#8 주말·공휴일 → 성적표·retro·
# 밴드 판정의 분리 집계(ref 65 S2-ⓐ)"*.
#
# ★이 파일이 지키는 것은 **항등식**이다. 분리 자체는 눈으로 보이지만 「합이 맞는가」는 안
#   보인다 — 한 날짜가 두 칸에 들어가도(이중계상) 세 칸은 멀쩡해 보이고, 보는 사람은 칸을
#   각각만 읽는다. ref 63 §1-3이 라벨 상호배타를 못 박은 이유가 그것이다.
from __future__ import annotations

from datetime import date, timedelta

from app.services.naver_ad import probe_cell_aggregate
from app.services.naver_ad.retro_rollup import (
    DAY_CLASSES,
    board_rollup,
    day_class_rollup,
)


class _Row:
    """NaverRetroSignal 스텁 — rollup은 순수 함수라 DB가 필요 없다."""

    def __init__(self, asof_date: date, *, verdict: str | None = "correct",
                 direction: str = "down", bleed: int = 0):
        self.asof_date = asof_date
        self.direction = direction
        self.verdict_d3 = verdict
        self.bleed_post3 = bleed
        self.verdict_d7 = verdict
        self.bleed_post7 = bleed


def _a_date_of(day_class: str, *, start: date | None = None) -> date:
    """해당 day_class인 날짜 하나를 찾는다 — 달력을 하드코딩하지 않는다.

    공휴일은 해마다 움직여서(음력 명절) 상수로 박으면 다음 해에 조용히 틀린다.
    """
    d = start or date(2026, 1, 1)
    for _ in range(400):
        if probe_cell_aggregate.env_cell_of_date(d) == day_class:
            return d
        d += timedelta(days=1)
    raise AssertionError(f"400일 안에 {day_class}인 날짜가 없다 — 달력 판정이 깨졌다")


def test_every_row_lands_in_exactly_one_bucket():
    """세 칸의 n 합 = 전체 n. 한 날짜가 두 칸에 들어가면 여기서 죽는다."""
    rows = [
        _Row(_a_date_of("weekday")),
        _Row(_a_date_of("weekend")),
        _Row(_a_date_of("holiday")),
    ]
    out = day_class_rollup(rows, 3)

    assert sum(out[dc]["n"] for dc in DAY_CLASSES) == 3
    assert out["weekday"]["n"] == 1
    assert out["weekend"]["n"] == 1
    assert out["holiday"]["n"] == 1


def test_identity_holds_and_is_reported():
    """★「평시+주말+공휴일 = 전체」 항등식이 성립하고, **응답이 그 검산을 같이 싣는다**.

    계약 §4-C S2-①이 요구하는 것이 정확히 이 항등식이다(ref 63 §1-2 검산과 같은 방식).
    """
    rows = [
        _Row(_a_date_of("weekday"), verdict="correct", bleed=500),
        _Row(_a_date_of("weekday") + timedelta(days=1), verdict="wrong"),
        _Row(_a_date_of("weekend"), verdict="correct", bleed=300),
        _Row(_a_date_of("holiday"), verdict="no_spend"),
    ]
    out = day_class_rollup(rows, 3)
    total = board_rollup(rows, 3)

    assert out["identity"]["ok"] is True
    for key in ("n", "correct", "gray", "wrong", "no_spend", "bleed_sum"):
        assert out["identity"]["sum_of_parts"][key] == total[key], key
        assert out["identity"]["total"][key] == total[key], key


def test_ratio_is_not_summed_across_buckets():
    """precision_spenders는 «비율»이라 칸끼리 더하지 않는다.

    ref 63 §1-1: 비율은 가법이 아니라서 요인별로 쪼개 합산할 수 없다 — 그게 이 트랙이
    ROAS 대신 이익 절대액을 축으로 삼은 이유다. 항등식 검산에 비율이 섞이면 그 자체가 오류다.
    """
    out = day_class_rollup([_Row(_a_date_of("weekday"))], 3)
    assert "precision_spenders" not in out["identity"]["total"]
    assert "precision_spenders" not in out["identity"]["sum_of_parts"]
    # 칸 «안»에는 그대로 있다 — 칸별 정밀도는 읽을 수 있어야 한다
    assert "precision_spenders" in out["weekday"]


def test_unscored_rows_are_excluded_from_every_bucket():
    """아직 채점 전(verdict=None)인 행은 어느 칸에도 안 센다 — board_rollup과 같은 규칙.

    분리 집계가 «미채점»을 세면 세 칸의 합이 전체보다 커진다.
    """
    rows = [_Row(_a_date_of("weekday"), verdict=None),
            _Row(_a_date_of("weekend"), verdict="correct")]
    out = day_class_rollup(rows, 3)

    assert out["weekday"]["n"] == 0
    assert out["weekend"]["n"] == 1
    assert out["identity"]["ok"] is True


def test_empty_input_is_a_clean_zero_not_a_crash():
    """행이 없어도 세 칸이 다 있어야 한다 — 키가 사라지면 화면이 「열이 없다」와
    「값이 0이다」를 구분 못 한다."""
    out = day_class_rollup([], 7)
    for dc in DAY_CLASSES:
        assert out[dc]["n"] == 0
    assert out["identity"]["ok"] is True


def test_day_class_judgment_is_not_a_second_copy():
    """★판정을 두 벌 만들지 않았다 — probe_cell_aggregate.env_cell_of_date가 유일 출처다.

    두 벌이 되면 같은 날짜가 표면마다 다른 칸에 들어간다(계약 §2-3 「기존 숫자를 재사용하고
    새 상수를 발명하지 않는다」). 이 테스트는 버킷 배정이 그 함수의 답과 «완전히» 일치하는지
    본다 — retro_rollup이 자기만의 요일 판정을 몰래 들이면 여기서 죽는다.
    """
    probe_dates = [date(2026, 1, 1) + timedelta(days=i) for i in range(120)]
    rows = [_Row(d) for d in probe_dates]
    out = day_class_rollup(rows, 3)

    expected: dict[str, int] = {dc: 0 for dc in DAY_CLASSES}
    for d in probe_dates:
        expected[probe_cell_aggregate.env_cell_of_date(d)] += 1

    for dc in DAY_CLASSES:
        assert out[dc]["n"] == expected[dc], dc
