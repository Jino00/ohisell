# test_probe_learning_loop.py — probe_learning_harness 단위테스트 (D-NAO-58 CD4)
# learned_probe_rank(승격 판정)·run_probe_learning(스테이지 격리 harness)를 검증.
# 마이그레이션 없음 — 쓰기는 observe 일기 1행뿐(diary.write_diary_entry 재사용).
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverKeywordHourly, OpsDiaryEntry
from app.services.naver_ad import probe_learning_loop as loop

WEEKDAY = date(2026, 7, 15)
NOW = datetime(2026, 7, 15, 9, 5, 0)


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


def _hour_row(db, *, ad_date, hour=9, avg_rank=Decimal("2.2"), imp=50, clk=10, conv_cnt=0,
              campaign_id="cmp-1"):
    db.add(NaverKeywordHourly(
        ad_date=ad_date, hour=hour, entity_type="keyword", entity_id="nkw-1",
        adgroup_id="grp-1", campaign_id=campaign_id, campaign_type="WEB_SITE",
        imp=imp, clk=clk, cost=100, avg_rank=avg_rank, conv_cnt=conv_cnt,
    ))
    db.commit()


def _seed_promotable_cell(db):
    """imp≥100(총합)·days≥3·ctr_shrunk≥신호하한 충족 → optimal_band 확정되는 weekday 셀.
    conv_cnt=0(기본) → RL5 Part B는 CTR 폴백으로 떨어져 기존 동작(optimal_band='2.0-2.5',
    basis='ctr')과 동일하게 유지된다."""
    for i in range(3):
        _hour_row(db, ad_date=WEEKDAY - timedelta(days=i), imp=50, clk=10)


def _no_op_invoke(prompt, *, system, schema, model, timeout):
    return {"json": {"verdict": "keep", "axis": None, "rationale": "표본 부족"}}


# ══════════════════════════ learned_probe_rank ══════════════════════════

def test_learned_probe_rank_promotes_when_conditions_met(db):
    _seed_promotable_cell(db)
    band = loop.learned_probe_rank(db, env_cell="weekday", as_of=WEEKDAY, window_days=30)
    assert band == "2.0-2.5"


def test_learned_probe_rank_none_when_days_insufficient(db):
    _hour_row(db, ad_date=WEEKDAY, imp=200, clk=40)  # 하루치만(days=1<3)
    band = loop.learned_probe_rank(db, env_cell="weekday", as_of=WEEKDAY, window_days=30)
    assert band is None


def test_learned_probe_rank_none_when_cell_absent(db):
    band = loop.learned_probe_rank(db, env_cell="weekday", as_of=WEEKDAY, window_days=30)
    assert band is None


# ══════════════════════════ run_probe_learning ══════════════════════════

def test_run_probe_learning_populates_cells_and_writes_diary(db):
    _seed_promotable_cell(db)
    with patch.object(loop, "judge_cell_segmentation", return_value={"judged": [], "skipped": 0}):
        result = loop.run_probe_learning(db, now=NOW)

    assert result["cells"] > 0
    assert result["stage_status"]["aggregate"] == "ok"
    assert result["stage_status"]["diary"] == "ok"
    entries = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.event_type == "observe", OpsDiaryEntry.action == "probe_learning",
    ).all()
    assert len(entries) == 1
    assert "CD4 학습" in entries[0].rationale


def test_run_probe_learning_promotes_eligible_cell(db):
    _seed_promotable_cell(db)
    with patch.object(loop, "judge_cell_segmentation", return_value={"judged": [], "skipped": 0}):
        result = loop.run_probe_learning(db, now=NOW)
    assert {"cell": "weekday", "band": "2.0-2.5"} in result["promoted"]


def test_run_probe_learning_persists_split_verdict_to_diary_rationale(db):
    """P2-1(D-58-12): 세분화 판사의 split 판정 근거가 observe 일기 rationale(볼트가 렌더하는
    유일 필드)에 남아야 한다 — LLM 산출물이 카운트만 남고 증발하면 판사를 둔 의미가 없다."""
    _seed_promotable_cell(db)
    seg = {"judged": [{
        "cell": "weekday", "verdict": "split", "axis": "season",
        "rationale": "SEASON_SIGNAL_XYZ 계절별 곡선 급변",
        "optimal_band": "2.0-2.5", "imp": 150, "days": 3,
    }], "skipped": 0}
    with patch.object(loop, "judge_cell_segmentation", return_value=seg):
        loop.run_probe_learning(db, now=NOW)
    entry = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.event_type == "observe", OpsDiaryEntry.action == "probe_learning",
    ).one()
    # 볼트 렌더 대상(rationale)에 split·축·근거가 실제로 있어야 함
    assert "split" in entry.rationale or "세분 권고" in entry.rationale
    assert "season" in entry.rationale
    assert "SEASON_SIGNAL_XYZ" in entry.rationale
    # 기계판독용 상세(after_value)에도 판정 전량이 있어야 함
    detail = json.loads(entry.after_value)
    assert detail["segment"][0]["axis"] == "season"
    assert {"cell": "weekday", "band": "2.0-2.5"} in detail["promoted"]


def test_run_probe_learning_rationale_flags_ctr_fallback_when_no_conversions(db):
    """D-NAO-60 RL5(CD5) Part B: 구 P3-3("클릭 최다·이익가중 미반영" 고정 표기)이 해소되고
    basis에 따라 갱신된다 — 전환 신호 없는(conv_cnt=0) 승격은 'CTR 폴백'로 정직하게 표기."""
    _seed_promotable_cell(db)
    with patch.object(loop, "judge_cell_segmentation", return_value={"judged": [], "skipped": 0}):
        loop.run_probe_learning(db, now=NOW)
    entry = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.event_type == "observe", OpsDiaryEntry.action == "probe_learning",
    ).one()
    assert "클릭 최다" in entry.rationale
    assert "전환 신호 부족" in entry.rationale


def test_run_probe_learning_rationale_flags_conv_basis_when_conversions_present(db):
    """전환 신호(conv_cnt>0) 있는 승격은 '전환 최다(이익 방향)'로 표기(P3-3 해소 본론)."""
    for i in range(3):
        _hour_row(db, ad_date=WEEKDAY - timedelta(days=i), imp=50, clk=10, conv_cnt=5)
    with patch.object(loop, "judge_cell_segmentation", return_value={"judged": [], "skipped": 0}):
        loop.run_probe_learning(db, now=NOW)
    entry = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.event_type == "observe", OpsDiaryEntry.action == "probe_learning",
    ).one()
    assert "전환 최다" in entry.rationale
    assert "이익 방향" in entry.rationale


def test_run_probe_learning_empty_data_still_writes_diary(db):
    with patch.object(loop, "judge_cell_segmentation", return_value={"judged": [], "skipped": 0}):
        result = loop.run_probe_learning(db, now=NOW)
    assert result["cells"] == 0
    assert result["promoted"] == []
    entries = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.event_type == "observe", OpsDiaryEntry.action == "probe_learning",
    ).all()
    assert len(entries) == 1  # 무신호도 관찰로 기록


def test_run_probe_learning_idempotent_same_day(db):
    """같은 날 두 번 호출해도 observe 행은 1개만(catch-up 이중 방지).

    write_diary_entry의 created_at은 server_default UTC now()라(주입 now 무시) idempotency
    판정이 맞으려면 실 kst_now() 기준으로 today를 잡아야 한다(test_naver_diary_reflection.py
    _now_with_entries 전례)."""
    from app.utils.kst import kst_now

    real_now = kst_now()
    with patch.object(loop, "judge_cell_segmentation", return_value={"judged": [], "skipped": 0}):
        loop.run_probe_learning(db, now=real_now)
        loop.run_probe_learning(db, now=real_now + timedelta(hours=1))
    entries = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.event_type == "observe", OpsDiaryEntry.action == "probe_learning",
    ).all()
    assert len(entries) == 1


def test_run_probe_learning_stage_isolation_aggregate_failure_does_not_block_rest(db):
    """aggregate 단계가 실패해도 segment·promote·diary 단계는 계속 진행(스테이지 격리)."""
    with patch.object(loop, "aggregate_cells", side_effect=RuntimeError("boom")), \
         patch.object(loop, "judge_cell_segmentation", return_value={"judged": [], "skipped": 0}):
        result = loop.run_probe_learning(db, now=NOW)

    assert result["stage_status"]["aggregate"] == "failed"
    assert result["stage_status"]["segment"] == "ok"
    assert result["cells"] == 0
    assert result["promoted"] == []
    assert result["stage_status"]["diary"] == "ok"
    entries = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.event_type == "observe", OpsDiaryEntry.action == "probe_learning",
    ).all()
    assert len(entries) == 1  # aggregate 실패해도 무신호 관찰은 기록됨


def test_run_probe_learning_stage_isolation_segment_failure_does_not_block_promote_or_diary(db):
    _seed_promotable_cell(db)
    with patch.object(loop, "judge_cell_segmentation", side_effect=RuntimeError("llm down")):
        result = loop.run_probe_learning(db, now=NOW)

    assert result["stage_status"]["segment"] == "failed"
    assert result["stage_status"]["aggregate"] == "ok"
    assert result["cells"] > 0
    assert {"cell": "weekday", "band": "2.0-2.5"} in result["promoted"]
    assert result["stage_status"]["diary"] == "ok"
