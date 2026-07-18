# test_probe_cell_segmenter.py — probe_cell_segmenter SA 단위테스트 (D-NAO-58 CD4)
# LLM 세분화 판사: 표본 임계 필터 + invoke 주입(fail-open) + max_per_run 상한을 검증.
# 실 LLM 호출 없음(invoke는 항상 stub 주입).
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverKeywordHourly
from app.services.naver_ad import probe_cell_segmenter as seg

WEEKDAY = date(2026, 7, 15)
WEEKEND = date(2026, 7, 18)
HOLIDAY = date(2026, 1, 1)


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


def _hour_row(db, *, ad_date, hour=10, avg_rank=Decimal("2.2"), imp=100, clk=10,
              entity_id="nkw-1", campaign_id="cmp-1"):
    db.add(NaverKeywordHourly(
        ad_date=ad_date, hour=hour, entity_type="keyword", entity_id=entity_id,
        adgroup_id="grp-1", campaign_id=campaign_id, campaign_type="WEB_SITE",
        imp=imp, clk=clk, cost=100, avg_rank=avg_rank,
    ))
    db.commit()


def _seed_eligible_cell(db, *, campaign_id="cmp-1", n_days=3):
    """imp≥100·days≥3 충족하는 "weekday" 셀 시드(임계 통과용)."""
    for i in range(n_days):
        _hour_row(db, ad_date=WEEKDAY - timedelta(days=i), hour=9, imp=50, clk=5,
                  campaign_id=campaign_id)


def _seed_eligible_dates(db, dates, *, campaign_id="cmp-1"):
    """지정 날짜(같은 day_class일 것)마다 imp=50 행 1개씩 — 3일 이상이면 임계 통과."""
    for i, d in enumerate(dates):
        _hour_row(db, ad_date=d, hour=9, imp=50, clk=5, campaign_id=campaign_id)


def _stub_invoke(verdict="split", axis="season", rationale="하위셀 곡선이 유의하게 다름"):
    def invoke(prompt, *, system, schema, model, timeout):
        return {"json": {"verdict": verdict, "axis": axis, "rationale": rationale}}
    return invoke


def _raising_invoke(prompt, *, system, schema, model, timeout):
    raise RuntimeError("claude CLI 타임아웃(테스트)")


# ══════════════════════════ 임계 필터 ══════════════════════════

def test_below_threshold_cell_excluded_and_skipped_counted(db):
    # imp=100 미만·days=1 — 두 임계 모두 미달
    _hour_row(db, ad_date=WEEKDAY, imp=10, clk=1)
    result = seg.judge_cell_segmentation(db, as_of=WEEKDAY, window_days=1,
                                         invoke=_stub_invoke())
    assert result["judged"] == []
    assert result["skipped"] == 1


def test_eligible_cell_included_in_judged(db):
    _seed_eligible_cell(db)
    result = seg.judge_cell_segmentation(db, as_of=WEEKDAY, window_days=30,
                                         invoke=_stub_invoke())
    assert len(result["judged"]) == 1
    assert result["judged"][0]["cell"] == "weekday"
    assert result["skipped"] == 0


# ══════════════════════════ invoke stub 반영 ══════════════════════════

def test_invoke_split_verdict_reflected(db):
    _seed_eligible_cell(db)
    result = seg.judge_cell_segmentation(
        db, as_of=WEEKDAY, window_days=30,
        invoke=_stub_invoke(verdict="split", axis="season", rationale="계절별 차이 뚜렷"),
    )
    judged = result["judged"][0]
    assert judged["verdict"] == "split"
    assert judged["axis"] == "season"
    assert judged["rationale"] == "계절별 차이 뚜렷"


def test_invoke_keep_verdict_reflected(db):
    _seed_eligible_cell(db)
    result = seg.judge_cell_segmentation(
        db, as_of=WEEKDAY, window_days=30,
        invoke=_stub_invoke(verdict="keep", axis=None, rationale="표본 얇음"),
    )
    judged = result["judged"][0]
    assert judged["verdict"] == "keep"
    assert judged["rationale"] == "표본 얇음"


# ══════════════════════════ fail-open ══════════════════════════

def test_invoke_exception_fails_open_to_keep(db):
    _seed_eligible_cell(db)
    result = seg.judge_cell_segmentation(db, as_of=WEEKDAY, window_days=30,
                                         invoke=_raising_invoke)
    judged = result["judged"][0]
    assert judged["verdict"] == "keep"  # 예외 전파 안 됨, fail-open


def test_invoke_malformed_verdict_falls_open_to_keep(db):
    _seed_eligible_cell(db)

    def bad_invoke(prompt, *, system, schema, model, timeout):
        return {"json": {"verdict": "explode", "rationale": "?"}}

    result = seg.judge_cell_segmentation(db, as_of=WEEKDAY, window_days=30, invoke=bad_invoke)
    assert result["judged"][0]["verdict"] == "keep"


# ══════════════════════════ max_per_run 상한 ══════════════════════════

def test_max_per_run_cap_respected(db):
    # weekday·weekend 두 셀 모두 임계 통과하게 시드(서로 다른 day_class → 2 후보 셀)
    _seed_eligible_dates(db, [WEEKDAY, WEEKDAY - timedelta(days=1), WEEKDAY - timedelta(days=2)])
    _seed_eligible_dates(db, [WEEKEND, WEEKEND - timedelta(days=7), WEEKEND - timedelta(days=14)])
    result_uncapped = seg.judge_cell_segmentation(db, as_of=WEEKEND, window_days=30,
                                                   invoke=_stub_invoke())
    assert len(result_uncapped["judged"]) == 2  # 상한 없으면 weekday+weekend 둘 다 후보

    result = seg.judge_cell_segmentation(db, as_of=WEEKEND, window_days=30,
                                         invoke=_stub_invoke(), max_per_run=1)
    assert len(result["judged"]) == 1
