# test_naver_searchterm_px4.py — PX4 파워링크 자동 제외 예외 브리핑 + 대행사 주간 브리핑 테스트
# 커버(PLAN_naver-ad-powerlink-autoexclude.md §4, GATE): ①실행(제외/개방/재제외/restored)이
#   있었던 날만 diary — ss dict 카운트 트리거 ②실행 없으면(또는 ss={} — 상위 레인 실패 시)
#   완전 침묵(D-NAO-79, diary 0건) ③대행사 주간 브리핑=일요일 게이트만(평일엔 judge 재호출조차
#   안 함 — 읽기 비용 0) ④일요일이라도 후보 0건이면 침묵. 라우터 응답형은
#   test_naver_ad_px_router.py가 커버.
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverSearchTermExclusion, OpsDiaryEntry
from app.services.naver_ad import search_term_px_briefing as briefing

_NOW = datetime(2026, 7, 22, 9, 0, 0)      # 수요일(2026-07-22)
_SUNDAY = datetime(2026, 7, 26, 9, 20, 0)  # 일요일(2026-07-26) — bm_deep과 같은 리듬


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


def _excl_row(db, *, term, status, last_transition_at, cost=15000, cycle=1,
              adgroup_id="grp-a", campaign_id="cmp1"):
    row = NaverSearchTermExclusion(
        campaign_id=campaign_id, adgroup_id=adgroup_id, search_term=term, status=status,
        cycle=cycle, excluded_at=last_transition_at, last_transition_at=last_transition_at,
        cost_at_exclusion=cost,
    )
    db.add(row)
    db.commit()
    return row


def _zero_ss() -> dict:
    return {"powerlink_fired": 0, "reexam_opened": 0, "reexam_reexcluded": 0, "reexam_restored": 0}


# ══════════════════════════════════════════════════════════════════
# §4 1 — 자동 제외/복귀 예외 브리핑
# ══════════════════════════════════════════════════════════════════

def test_exclusion_briefing_silent_when_no_activity(db):
    res = briefing.run_exclusion_exception_briefing(db, _zero_ss(), now=_NOW)
    assert res == {"briefed": False, "excluded": 0, "opened": 0, "restored": 0, "slack_sent": False}
    assert db.query(OpsDiaryEntry).count() == 0


def test_exclusion_briefing_silent_with_empty_ss_dict(db):
    # 상위 레인(run_search_term_ss_lane) 실패로 scheduler가 ss={}를 넘긴 경우도 완전 침묵(fail-open).
    res = briefing.run_exclusion_exception_briefing(db, {}, now=_NOW)
    assert res["briefed"] is False
    assert db.query(OpsDiaryEntry).count() == 0


def test_exclusion_briefing_fires_on_new_exclusion(db):
    _excl_row(db, term="손실검색어", status="excluded", last_transition_at=_NOW, cost=15000)
    ss = {**_zero_ss(), "powerlink_fired": 1}
    res = briefing.run_exclusion_exception_briefing(db, ss, now=_NOW)
    assert res["briefed"] is True
    assert res["excluded"] == 1 and res["opened"] == 0 and res["restored"] == 0
    entry = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.action == briefing.ACTION_EXCLUSION_BRIEFING).one()
    assert "파워링크 자동 제외 1건 · 복귀 0건" in entry.rationale
    assert "손실검색어" in entry.rationale
    assert "15000원" in entry.rationale
    assert "[제외]" in entry.rationale
    assert "[복귀" not in entry.rationale  # 복귀 0건이면 그 섹션 자체를 안 만든다


def test_exclusion_briefing_counts_reexcluded_as_excluded(db):
    # PX3 재제외(probation→excluded)도 "제외" 카운트에 합산된다(§4 1 "제외 또는 복귀" 중 제외).
    _excl_row(db, term="재제외된것", status="excluded", last_transition_at=_NOW, cost=9000, cycle=2)
    ss = {**_zero_ss(), "reexam_reexcluded": 1}
    res = briefing.run_exclusion_exception_briefing(db, ss, now=_NOW)
    assert res["excluded"] == 1
    entry = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.action == briefing.ACTION_EXCLUSION_BRIEFING).one()
    assert "재제외된것" in entry.rationale
    assert "cycle=2" in entry.rationale


def test_exclusion_briefing_fires_on_opened_only(db):
    _excl_row(db, term="복귀후보", status="probation", last_transition_at=_NOW, cost=8000)
    ss = {**_zero_ss(), "reexam_opened": 1}
    res = briefing.run_exclusion_exception_briefing(db, ss, now=_NOW)
    assert res["briefed"] is True
    assert res["excluded"] == 0 and res["opened"] == 1
    entry = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.action == briefing.ACTION_EXCLUSION_BRIEFING).one()
    assert "복귀 1건" in entry.rationale
    assert "[복귀(재노출 관찰 개시)]" in entry.rationale
    assert "복귀후보" in entry.rationale
    assert "[제외]" not in entry.rationale


def test_exclusion_briefing_fires_on_restored_only(db):
    # restored는 실쓰기 자체는 없지만(§2 상태 전이), 트리거 목록에 명시된 대로 브리핑을 켠다.
    _excl_row(db, term="완전회복", status="restored", last_transition_at=_NOW, cost=5000)
    ss = {**_zero_ss(), "reexam_restored": 1}
    res = briefing.run_exclusion_exception_briefing(db, ss, now=_NOW)
    assert res["briefed"] is True
    assert res["restored"] == 1
    entry = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.action == briefing.ACTION_EXCLUSION_BRIEFING).one()
    assert "확정복귀 1건" in entry.rationale
    assert "[확정복귀(재심사 회복 — 성과 자가 교정)]" in entry.rationale
    assert "완전회복" in entry.rationale


def test_exclusion_briefing_only_lists_todays_rows(db):
    # last_transition_at이 어제인 행은 오늘 브리핑에 나열되지 않는다(오늘 활동만 표면화).
    yesterday = datetime(2026, 7, 21, 9, 0, 0)
    _excl_row(db, term="어제제외", status="excluded", last_transition_at=yesterday, cost=1000)
    _excl_row(db, term="오늘제외", status="excluded", last_transition_at=_NOW, cost=2000)
    ss = {**_zero_ss(), "powerlink_fired": 1}
    briefing.run_exclusion_exception_briefing(db, ss, now=_NOW)
    entry = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.action == briefing.ACTION_EXCLUSION_BRIEFING).one()
    assert "오늘제외" in entry.rationale
    assert "어제제외" not in entry.rationale


# ══════════════════════════════════════════════════════════════════
# §4 2 — 대행사 파워링크 고비용 검색어 주간 브리핑(일요일 게이트, 단순화 선택)
# ══════════════════════════════════════════════════════════════════

def test_agency_weekly_silent_on_non_sunday_no_judge_call(db):
    with patch(
        "app.services.naver_ad.search_term_judge.judge_search_terms",
    ) as mock_judge:
        res = briefing.run_agency_powerlink_weekly_briefing(db, now=_NOW)  # 수요일
    mock_judge.assert_not_called()  # 평일엔 읽기조차 안 한다(비용 0)
    assert res == {"briefed": False, "reason": "not_sunday", "agency_candidates": 0}
    assert db.query(OpsDiaryEntry).count() == 0


def test_agency_weekly_silent_when_no_candidates_on_sunday(db):
    with patch(
        "app.services.naver_ad.search_term_judge.judge_search_terms",
        return_value={"agency_powerlink": []},
    ) as mock_judge:
        res = briefing.run_agency_powerlink_weekly_briefing(db, now=_SUNDAY)
    mock_judge.assert_called_once()
    assert res == {"briefed": False, "reason": "no_candidates", "agency_candidates": 0}
    assert db.query(OpsDiaryEntry).count() == 0


def test_agency_weekly_briefs_on_sunday_with_candidates(db):
    fake = {"agency_powerlink": [
        {"search_term": "갤럭시필름", "adgroup_id": "grp-agency", "campaign_id": "cmp-agency",
         "source": "expkeyword", "clk": 46, "cost": 460000, "reason": "..."},
    ]}
    with patch("app.services.naver_ad.search_term_judge.judge_search_terms", return_value=fake):
        res = briefing.run_agency_powerlink_weekly_briefing(db, now=_SUNDAY)
    assert res == {"briefed": True, "reason": None, "agency_candidates": 1}
    entry = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.action == briefing.ACTION_AGENCY_WEEKLY_BRIEFING).one()
    assert "갤럭시필름" in entry.rationale
    assert "460000원" in entry.rationale
    assert "실쓰기 없음·대행사 전달용" in entry.rationale


def test_agency_weekly_truncates_to_top_n_with_remainder_line(db):
    cands = [
        {"search_term": f"검색어{i}", "adgroup_id": "grp-agency", "campaign_id": "cmp-agency",
         "source": "expkeyword", "clk": 20, "cost": 100000 - i, "reason": "..."}
        for i in range(15)
    ]
    with patch(
        "app.services.naver_ad.search_term_judge.judge_search_terms",
        return_value={"agency_powerlink": cands},
    ):
        res = briefing.run_agency_powerlink_weekly_briefing(db, now=_SUNDAY)
    assert res["agency_candidates"] == 15
    entry = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.action == briefing.ACTION_AGENCY_WEEKLY_BRIEFING).one()
    assert "외 5건" in entry.rationale  # 15 - _TOP_N(10) = 5
