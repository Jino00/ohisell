# test_naver_diary_reflection.py — D-NAO-54 P2 해석층(outcome_backfill_sa·daily_reflection_sa·
# reflection_loop harness·크론 등록) 단위 테스트. LLM은 전부 주입경계(invoke)로 몽키패치 —
# 실 claude CLI 호출 0. correction_factor도 결정성 위해 픽스처에서 고정 factor로 패치.
from __future__ import annotations

import inspect
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverRetroSignal, NaverSearchTermDaily, OpsDiaryEntry
from app.services import scheduler_service
from app.services.naver_ad import diary_outcome, diary_reflection, reflection_loop
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

NOW = datetime(2026, 7, 20, 8, 35)  # KST
TODAY = NOW.date()


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


@pytest.fixture(autouse=True)
def _fixed_cf(monkeypatch):
    """보정계수를 2.0으로 고정 — roas_c = (conv/cost)×2가 결정적으로 나오게 한다."""
    monkeypatch.setattr(diary_outcome, "correction_factor", lambda db, d: {"factor": Decimal("2")})


def _utc_for(action_date: date) -> datetime:
    """_kst_date(created_at)==action_date가 되도록 UTC created_at(=KST 12시)을 만든다."""
    return datetime.combine(action_date, time(3, 0))  # +9h → 12:00 KST 같은 날


def _entry(db, *, action_date, event_type="execute", campaign_id="cmp1",
           target_type="keyword", target_id="nkw-1", action="bid_up",
           outcome_json=None, actor="daily", adgroup_id=None):
    e = OpsDiaryEntry(
        created_at=_utc_for(action_date), event_type=event_type, campaign_id=campaign_id,
        target_type=target_type, target_id=target_id, action=action,
        outcome_json=outcome_json, actor=actor, adgroup_id=adgroup_id,
    )
    db.add(e)
    db.flush()
    return e


def _daily(db, ad_date, *, keyword_id="nkw-1", campaign_id="cmp1", cost, clk, conv,
           adgroup_id="grp1"):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type="WEB_SITE",
        adgroup_id=adgroup_id, keyword_id=keyword_id, imp=max(cost // 10, 1), clk=clk,
        cost=cost, rank_sum=0, conv_direct_amt=conv, conv_indirect_amt=0,
    ))


# ══════════════════════════ outcome_backfill_sa ══════════════════════════


def test_d1_fills_next_completion_day_metrics(db):
    # 문턱 age>=4(D-NAO-178: 원료 3일 정정 창이 닫힌 뒤에만 채점) → D-4 행의 d1 창 = D-3(07-17).
    _entry(db, action_date=TODAY - timedelta(days=4))
    _daily(db, TODAY - timedelta(days=3), cost=1000, clk=5, conv=500)
    db.commit()

    res = diary_outcome.backfill_outcomes(db, now=NOW)

    assert res["d1_filled"] == 1
    row = db.query(OpsDiaryEntry).one()
    d1 = json.loads(row.outcome_json)["d1"]
    assert d1 == {"cost": 1000, "clk": 5, "conv": 500, "roas_c": 1.0}  # (500/1000)*2


def test_d7_fills_seven_day_window_and_preserves_existing_d1(db):
    # 병합 갱신 검증: d1이 이미 있는 D-8 행에 d7만 추가되고 d1은 보존된다.
    _entry(db, action_date=TODAY - timedelta(days=8),
           outcome_json=json.dumps({"d1": {"cost": 999, "clk": 9, "conv": 9, "roas_c": 0.5}}))
    for i in range(1, 8):  # D-8+1..D-8+7 = 07-13..07-19
        _daily(db, TODAY - timedelta(days=8) + timedelta(days=i), cost=100, clk=1, conv=50)
    db.commit()

    res = diary_outcome.backfill_outcomes(db, now=NOW)

    assert res["d7_filled"] == 1 and res["d1_filled"] == 0  # d1 이미 존재 → 재기입 안 함
    outcome = json.loads(db.query(OpsDiaryEntry).one().outcome_json)
    assert outcome["d1"]["cost"] == 999  # 보존
    assert outcome["d7"] == {"cost": 700, "clk": 7, "conv": 350, "roas_c": 1.0}  # (350/700)*2


def test_retro_signal_linked_by_target_and_date(db):
    # P3-3 방향 일치 필터: action=bid_down(→down)과 direction=down 신호가 일치 → 연결.
    action_date = TODAY - timedelta(days=2)
    _entry(db, action_date=action_date, action="bid_down")
    _daily(db, TODAY - timedelta(days=1), cost=1000, clk=5, conv=500)
    db.add(NaverRetroSignal(
        created_at=NOW, asof_date=action_date, board="bleeding_keywords", direction="down",
        grain="keyword", target_id="nkw-1", campaign_id="cmp1", cf_asof=1.0, bep_asof=2.0,
        target_asof=4.0, cost_asof=1000, roas_c_asof=None, verdict_d3="correct",
    ))
    db.commit()

    res = diary_outcome.backfill_outcomes(db, now=NOW)

    assert res["retro_linked"] == 1
    retro = json.loads(db.query(OpsDiaryEntry).one().outcome_json)["retro"]
    assert retro == {"board": "bleeding_keywords", "direction": "down", "verdict_d3": "correct"}


def test_retro_signal_direction_mismatch_not_linked(db):
    # P2 리뷰 P3-3: retro는 '제안 방향' 추적 — direction=down 신호를 action=bid_up(→up) 행에
    # 붙이면 오연결. 방향 매핑이 존재하는데 일치 신호가 없으면 붙이지 않는다.
    action_date = TODAY - timedelta(days=4)
    _entry(db, action_date=action_date, action="bid_up")
    _daily(db, TODAY - timedelta(days=3), cost=1000, clk=5, conv=500)
    db.add(NaverRetroSignal(
        created_at=NOW, asof_date=action_date, board="bleeding_keywords", direction="down",
        grain="keyword", target_id="nkw-1", campaign_id="cmp1", cf_asof=1.0, bep_asof=2.0,
        target_asof=4.0, cost_asof=1000, roas_c_asof=None, verdict_d3="correct",
    ))
    db.commit()

    res = diary_outcome.backfill_outcomes(db, now=NOW)

    assert res["retro_linked"] == 0
    assert "retro" not in json.loads(db.query(OpsDiaryEntry).one().outcome_json)


def test_out_of_window_row_untouched(db):
    # D-1(age 1) 캠페인 행·retro 없음 → d1/d7/retro 아무것도 안 채워지고 outcome_json None 유지.
    _entry(db, action_date=TODAY - timedelta(days=1), target_type="campaign", target_id=None)
    db.commit()

    res = diary_outcome.backfill_outcomes(db, now=NOW)

    # probation_* = 복귀 관찰창 성적(계약 §4-A S3-b). 이 행은 캠페인 grain이라 복귀 채점 대상이
    # 아니고, 「아무것도 안 채워졌다」에 두 키가 0으로 함께 서는 것이 이 테스트의 뜻 그대로다.
    assert res == {"d1_filled": 0, "d7_filled": 0, "retro_linked": 0,
                   "d1_st_filled": 0, "d1_st_no_data": 0,
                   "probation_filled": 0, "probation_silent": 0, "errors": 0}
    assert db.query(OpsDiaryEntry).one().outcome_json is None


def test_per_row_failure_isolated(db, monkeypatch):
    _entry(db, action_date=TODAY - timedelta(days=4), target_id="boom")
    _entry(db, action_date=TODAY - timedelta(days=4), target_id="nkw-ok")
    _daily(db, TODAY - timedelta(days=3), keyword_id="nkw-ok", cost=1000, clk=5, conv=500)
    db.commit()

    orig = diary_outcome._window_agg

    def _boom(db, grain, target_id, campaign_id, date_from, date_to):
        if target_id == "boom":
            raise RuntimeError("의도된 실패")
        return orig(db, grain, target_id, campaign_id, date_from, date_to)

    monkeypatch.setattr(diary_outcome, "_window_agg", _boom)
    res = diary_outcome.backfill_outcomes(db, now=NOW)

    assert res["errors"] == 1 and res["d1_filled"] == 1  # 한 행 실패, 나머지 정상
    ok = db.query(OpsDiaryEntry).filter(OpsDiaryEntry.target_id == "nkw-ok").one()
    boom = db.query(OpsDiaryEntry).filter(OpsDiaryEntry.target_id == "boom").one()
    assert ok.outcome_json is not None and boom.outcome_json is None  # boom은 롤백


def test_campaign_grain_excludes_backfill_sentinel(db):
    # 캠페인 폴백 집계는 상세행만 — sentinel 롤업 행과 이중계상하지 않는다.
    _entry(db, action_date=TODAY - timedelta(days=4), target_type="campaign", target_id=None)
    _daily(db, TODAY - timedelta(days=3), cost=1000, clk=5, conv=500)  # 상세행
    db.add(NaverAdDaily(  # sentinel 롤업(합산되면 2배)
        ad_date=TODAY - timedelta(days=3), campaign_id="cmp1", campaign_type="WEB_SITE",
        adgroup_id=BACKFILL_SENTINEL_ADGROUP, keyword_id="", imp=100, clk=99,
        cost=8888, rank_sum=0, conv_direct_amt=7777, conv_indirect_amt=0,
    ))
    db.commit()

    diary_outcome.backfill_outcomes(db, now=NOW)
    d1 = json.loads(db.query(OpsDiaryEntry).one().outcome_json)["d1"]
    assert d1["cost"] == 1000 and d1["conv"] == 500  # sentinel 미포함


# ══════════════════ d1_st — 검색어 grain D+1 (D-NAO-178) ══════════════════
# 왜 이 블록이 있나: search_term 행의 d1은 `_grain_and_target`의 campaign 폴백 탓에 **그 캠페인
#   전체**의 하루 성과다(라이브 diary 4371: d1 43,084원 vs 「골프」 30일 누적 31,411원). d1_st는
#   같은 행에 검색어 grain 결과를 additive로 덧붙인다. 판정은 ROAS가 아니라 status 4값이다.

ST_CAMP = "cmp-st"
ST_ADG = "grp-st"


def _st_entry(db, *, action_date, target_id="골프", adgroup_id=ST_ADG, outcome_json=None):
    return _entry(db, action_date=action_date, campaign_id=ST_CAMP, adgroup_id=adgroup_id,
                  target_type="search_term", target_id=target_id,
                  action="search_term_exclude", actor="console", outcome_json=outcome_json)


def _st_daily(db, ad_date, *, term, source="shopping", cost=0, clk=0, imp=0, conv=0,
              campaign_id=ST_CAMP, adgroup_id=ST_ADG):
    db.add(NaverSearchTermDaily(
        ad_date=ad_date, campaign_id=campaign_id, adgroup_id=adgroup_id, search_term=term,
        source=source, imp=imp, clk=clk, cost=cost, rank_sum=0, conv_purchase_amt=conv,
    ))


def _d1_st(db):
    return json.loads(db.query(OpsDiaryEntry).one().outcome_json)["d1_st"]


def test_d1_st_stopped_when_report_present_and_term_cost_zero(db):
    # 제외가 돈을 끊은 성공 케이스 — 그 검색어 행은 없고 그룹의 그날 보고서는 실재한다.
    # (라이브 「골프」가 정확히 이 모양: 8/12 그룹 shopping 352행 존재, 「골프」 행 0건.)
    action = TODAY - timedelta(days=4)
    _st_entry(db, action_date=action)
    _st_daily(db, action - timedelta(days=1), term="골프", cost=31411, clk=14)  # 30일 기왕력
    _st_daily(db, action + timedelta(days=1), term="다른검색어", cost=40535, clk=20)  # 보고서 실재
    db.commit()

    res = diary_outcome.backfill_outcomes(db, now=NOW)

    assert res["d1_st_filled"] == 1 and res["d1_st_no_data"] == 0
    st = _d1_st(db)
    assert st["status"] == "stopped"
    assert st["cost_total"] == 0
    assert st["required_sources"] == ["shopping"]  # 30일 기왕력이 shopping뿐
    assert st["by_source"]["shopping"]["present"] is True
    assert st["by_source"]["expkeyword"]["present"] is False
    assert st["window"] == (action + timedelta(days=1)).isoformat()
    assert st["match"] == {"term": "골프", "mode": "exact", "matched_terms": 0}


def test_d1_st_leaking_when_term_still_costs(db):
    action = TODAY - timedelta(days=4)
    _st_entry(db, action_date=action)
    _st_daily(db, action - timedelta(days=1), term="골프", cost=31411)
    _st_daily(db, action + timedelta(days=1), term="골프", cost=1200, clk=3, imp=90, conv=5000)
    db.commit()

    diary_outcome.backfill_outcomes(db, now=NOW)

    st = _d1_st(db)
    assert st["status"] == "leaking" and st["cost_total"] == 1200
    assert st["by_source"]["shopping"]["conv_amt"] == 5000


def test_d1_st_has_no_roas_key(db):
    # 금지선 4 — roas_c가 있으면 캠페인 판정 규칙(roas_c >= target)이 이 키를 실수로 먹는다.
    action = TODAY - timedelta(days=4)
    _st_entry(db, action_date=action)
    _st_daily(db, action - timedelta(days=1), term="골프", cost=500)  # 기왕력 → 필요 source=shopping
    _st_daily(db, action + timedelta(days=1), term="골프", cost=1200, conv=99999)
    db.commit()

    diary_outcome.backfill_outcomes(db, now=NOW)

    st = _d1_st(db)
    assert "roas_c" not in st
    assert all("roas_c" not in v for v in st["by_source"].values())


def test_d1_st_expkeyword_carries_no_conversion_field(db):
    # 파워링크는 전환 귀속이 원리적으로 불가(SS0 §0.5) — 0을 적으면 「전환 없었음」으로 오독된다.
    action = TODAY - timedelta(days=4)
    _st_entry(db, action_date=action)
    _st_daily(db, action - timedelta(days=1), term="골프", source="expkeyword", cost=500)
    _st_daily(db, action + timedelta(days=1), term="골프", source="expkeyword", cost=700, clk=2)
    db.commit()

    diary_outcome.backfill_outcomes(db, now=NOW)

    st = _d1_st(db)
    assert st["required_sources"] == ["expkeyword"]
    assert "conv_amt" not in st["by_source"]["expkeyword"]
    assert st["status"] == "leaking" and st["cost_total"] == 700


def test_d1_st_prefix50_ambiguous_when_upper_bound_positive(db):
    # 50자 절단 다의 + 비용 > 0 → 누구 돈인지 모른다. 「0」이 아니라 「모른다」로 표면화(§6-B).
    action = TODAY - timedelta(days=4)
    trunc = "가" * 50
    _st_entry(db, action_date=action, target_id=trunc)
    _st_daily(db, action - timedelta(days=1), term=trunc + "원문A", cost=100)
    _st_daily(db, action + timedelta(days=1), term=trunc + "원문A", cost=300)
    _st_daily(db, action + timedelta(days=1), term=trunc + "원문B", cost=400)
    db.commit()

    diary_outcome.backfill_outcomes(db, now=NOW)

    st = _d1_st(db)
    assert st["match"]["mode"] == "prefix50" and st["match"]["matched_terms"] == 2
    assert st["status"] == "ambiguous" and st["cost_total"] == 700


def test_d1_st_prefix50_stopped_when_upper_bound_zero(db):
    # 매칭 집합의 비용 합은 진짜 비용의 **상한** — 상한이 0이면 다의여도 stopped가 성립한다.
    action = TODAY - timedelta(days=4)
    trunc = "가" * 50
    _st_entry(db, action_date=action, target_id=trunc)
    _st_daily(db, action - timedelta(days=1), term=trunc + "원문A", cost=100)
    _st_daily(db, action + timedelta(days=1), term="무관한검색어", cost=5000)  # 보고서 실재
    db.commit()

    diary_outcome.backfill_outcomes(db, now=NOW)

    st = _d1_st(db)
    assert st["status"] == "stopped" and st["cost_total"] == 0


def test_d1_st_skips_when_report_absent_then_confirms_no_data_at_deadline(db):
    # 보고서 부재는 「비용 0」이 아니다 — 키를 안 쓰고 재시도하다가 마감(age 5)에 no_data로 확정.
    action = TODAY - timedelta(days=4)
    _st_entry(db, action_date=action)
    _st_daily(db, action - timedelta(days=1), term="골프", cost=31411)  # 기왕력만, d1일 보고서 없음
    db.commit()

    res = diary_outcome.backfill_outcomes(db, now=NOW)
    assert res["d1_st_filled"] == 0
    assert "d1_st" not in json.loads(db.query(OpsDiaryEntry).one().outcome_json or "{}")

    later = diary_outcome.backfill_outcomes(db, now=NOW + timedelta(days=1))  # age 5 = 마감
    assert later["d1_st_filled"] == 1 and later["d1_st_no_data"] == 1
    st = _d1_st(db)
    assert st["status"] == "no_data" and st["by_source"]["shopping"]["present"] is False


def test_d1_st_not_written_before_correction_window_closes(db):
    # 문턱 age>=4 — 원료 3일 정정 창이 닫히기 전에는 쓰지 않는다(D-NAO-178).
    action = TODAY - timedelta(days=3)
    _st_entry(db, action_date=action)
    _st_daily(db, action + timedelta(days=1), term="다른검색어", cost=1000)
    db.commit()

    res = diary_outcome.backfill_outcomes(db, now=NOW)

    assert res["d1_st_filled"] == 0 and res["d1_filled"] == 0
    assert db.query(OpsDiaryEntry).one().outcome_json is None


def test_d1_st_unresolved_without_adgroup(db):
    # 그룹을 모르면 범위를 좁힐 수 없다 — 캠페인으로 넓히는 것이 d1이 낸 오귀속 그 자체다.
    action = TODAY - timedelta(days=4)
    _st_entry(db, action_date=action, adgroup_id=None)
    _st_daily(db, action + timedelta(days=1), term="골프", cost=1000)
    db.commit()

    res = diary_outcome.backfill_outcomes(db, now=NOW)

    st = _d1_st(db)
    assert res["d1_st_no_data"] == 1
    assert st["status"] == "no_data" and st["match"]["mode"] == "unresolved"
    assert st["cost_total"] == 0


def test_d1_st_coexists_with_d1_and_never_rewrites_it(db):
    # 합격기준 ④ — 기존 d1은 한 글자도 안 바뀐다. 라이브 4371의 실제 값을 그대로 쓴다.
    action = TODAY - timedelta(days=4)
    frozen = {"cost": 43084, "clk": 29, "conv": 122000, "roas_c": 3.5753}
    _st_entry(db, action_date=action, outcome_json=json.dumps({"d1": frozen}))
    _st_daily(db, action - timedelta(days=1), term="골프", cost=31411)
    _st_daily(db, action + timedelta(days=1), term="다른검색어", cost=40535)
    db.commit()

    res = diary_outcome.backfill_outcomes(db, now=NOW)

    outcome = json.loads(db.query(OpsDiaryEntry).one().outcome_json)
    assert res["d1_filled"] == 0            # 멱등 가드 — 재기입 없음
    assert outcome["d1"] == frozen          # 값 불변
    assert outcome["d1_st"]["status"] == "stopped"   # 다른 숫자를 담는 별도 키


def test_d1_st_scope_excludes_other_adgroup_costs(db):
    # 제외는 그룹 단위 장치 — 옆 그룹의 같은 검색어 비용을 끌어오면 leaking 오판이 난다.
    action = TODAY - timedelta(days=4)
    _st_entry(db, action_date=action)
    _st_daily(db, action - timedelta(days=1), term="골프", cost=31411)
    _st_daily(db, action + timedelta(days=1), term="다른검색어", cost=100)   # 내 그룹 보고서
    _st_daily(db, action + timedelta(days=1), term="골프", cost=9999, adgroup_id="grp-남")
    db.commit()

    diary_outcome.backfill_outcomes(db, now=NOW)

    assert _d1_st(db)["status"] == "stopped"


def test_d1_st_prefix50_single_match_is_leaking_not_ambiguous(db):
    """적대 리뷰 살아남은 변이 — `matched_terms > 1`(다의)과 `>= 1`(단일 포함)의 경계.

    50자 절단이어도 매칭이 **하나뿐**이면 누구 돈인지 안다 → `leaking`이지 `ambiguous`가 아니다.
    이 경계가 없으면 「제외가 안 먹혔다」는 경보가 「모르겠다」로 뭉개진다.
    """
    action = TODAY - timedelta(days=4)
    trunc = "가" * 50
    _st_entry(db, action_date=action, target_id=trunc)
    _st_daily(db, action - timedelta(days=1), term=trunc + "원문A", cost=100)
    _st_daily(db, action + timedelta(days=1), term=trunc + "원문A", cost=600)  # 매칭 1건
    _st_daily(db, action + timedelta(days=1), term="무관한검색어", cost=5000)
    db.commit()

    diary_outcome.backfill_outcomes(db, now=NOW)

    st = _d1_st(db)
    assert st["match"]["mode"] == "prefix50" and st["match"]["matched_terms"] == 1
    assert st["status"] == "leaking" and st["cost_total"] == 600


def test_d1_st_like_wildcards_in_term_are_literal(db):
    """적대 리뷰 P1 — 검색어에 든 `%`·`_`가 LIKE 와일드카드로 새면 **무관한 검색어의 비용**이
    딸려 들어와 stopped가 leaking으로 뒤집힌다(「20%할인」류 표기는 실제로 흔하다)."""
    action = TODAY - timedelta(days=4)
    trunc = ("가" * 9) + "%" + ("나" * 40)  # 50자 · 리터럴 % 포함
    _st_entry(db, action_date=action, target_id=trunc)
    _st_daily(db, action - timedelta(days=1), term=trunc, cost=100)
    # %가 와일드카드로 새면 이 무관한 검색어가 매칭된다(가*9 + 아무거나 + 나*40 + …).
    _st_daily(db, action + timedelta(days=1),
              term=("가" * 9) + "Q" + ("나" * 40) + "무관", cost=99999)
    db.commit()

    diary_outcome.backfill_outcomes(db, now=NOW)

    st = _d1_st(db)
    assert st["status"] == "stopped", f"LIKE 와일드카드가 샜다: {st}"
    assert st["cost_total"] == 0 and st["match"]["matched_terms"] == 0


def test_d1_st_history_window_is_exactly_30_days_like_the_ledger(db):
    """필요 source 판정 창이 원장 `cost_at_exclusion`(30일)과 **같은 날짜 집합**이어야 한다 —
    하루라도 어긋나면 「원장이 본 기왕력」과 「판정이 본 기왕력」이 갈라진다."""
    action = TODAY - timedelta(days=4)
    _st_entry(db, action_date=action)
    # 창 밖(31일 전) shopping 실적만 존재 → 기왕력 없음으로 봐야 하고, 필요 source는 둘 다.
    _st_daily(db, action - timedelta(days=30), term="골프", cost=7777)
    _st_daily(db, action + timedelta(days=1), term="다른검색어", cost=100)
    db.commit()

    diary_outcome.backfill_outcomes(db, now=NOW + timedelta(days=1))  # age 5 마감까지

    assert _d1_st(db)["required_sources"] == ["shopping", "expkeyword"]


def test_d1_st_without_history_requires_both_sources_and_lands_no_data(db):
    # §6-C: 30일 기왕력이 비면 어느 source로 돈이 샜는지 모르므로 **두 source 모두** 필요로 본다.
    # 그룹은 실무상 한 source만 보고서를 내므로 이 경우는 사실상 no_data로 마감된다 — 그게 맞다.
    # 「모르는 것」을 stopped(성공)로 적지 않는 것이 이 설계의 요점이기 때문이다.
    action = TODAY - timedelta(days=4)
    _st_entry(db, action_date=action)  # 기왕력 없음
    _st_daily(db, action + timedelta(days=1), term="다른검색어", cost=100)  # shopping 보고서만
    db.commit()

    assert diary_outcome.backfill_outcomes(db, now=NOW)["d1_st_filled"] == 0  # 재시도
    res = diary_outcome.backfill_outcomes(db, now=NOW + timedelta(days=1))    # age 5 마감

    assert res["d1_st_no_data"] == 1
    st = _d1_st(db)
    assert st["required_sources"] == ["shopping", "expkeyword"]
    assert st["status"] == "no_data"


def test_d1_st_ignores_non_search_term_rows(db):
    # keyword grain 행에는 d1_st를 붙이지 않는다(additive의 경계).
    _entry(db, action_date=TODAY - timedelta(days=4))
    _daily(db, TODAY - timedelta(days=3), cost=1000, clk=5, conv=500)
    db.commit()

    res = diary_outcome.backfill_outcomes(db, now=NOW)

    assert res["d1_st_filled"] == 0
    assert "d1_st" not in json.loads(db.query(OpsDiaryEntry).one().outcome_json)


# ══════════════════════════ daily_reflection_sa ══════════════════════════


class _FakeLLM:
    def __init__(self, text="관찰: 목요일·소진 저조와 함께 움직임."):
        self.calls: list[dict] = []
        self.text = text

    def __call__(self, prompt, *, system, schema, model, timeout):
        self.calls.append({"prompt": prompt, "system": system, "model": model})
        return {"text": self.text, "json": None, "raw": "", "usage": {}}


def test_reflection_prompt_carries_d1_st_and_explains_zero_cost(db):
    """★기입만 하고 아무도 안 읽으면 「측정 정합」이 아니다 — d1_st가 실제로 LLM 프롬프트에
    닿는지, 그리고 「비용 0 = 의도된 성공」이 시스템 프롬프트에 서 있는지 함께 지킨다."""
    st = {"window": "2026-07-19", "match": {"term": "골프", "mode": "exact", "matched_terms": 0},
          "by_source": {"shopping": {"present": True, "imp": 0, "clk": 0, "cost": 0, "conv_amt": 0},
                        "expkeyword": {"present": False}},
          "required_sources": ["shopping"], "cost_total": 0, "status": "stopped"}
    _st_entry(db, action_date=TODAY - timedelta(days=1),
              outcome_json=json.dumps({"d1": {"cost": 43084, "clk": 29, "conv": 122000,
                                              "roas_c": 3.5753}, "d1_st": st}))
    db.commit()

    fake = _FakeLLM()
    diary_reflection.build_reflection(db, now=NOW, invoke=fake)

    prompt = fake.calls[0]["prompt"]
    assert "d1_st" in prompt and "stopped" in prompt, "d1_st가 해석문 컨텍스트에 안 실렸다"
    system = fake.calls[0]["system"]
    assert "d1_st" in system and "stopped" in system  # 비용 0을 실패로 서술하지 않게


def test_vault_export_shows_d1_st_status(db):
    """같은 이유의 표시층 — 사람이 d1(캠페인 grain)만 보고 조치의 성적으로 오독하지 않게."""
    from app.services.naver_ad import vault_export

    e = _st_entry(db, action_date=TODAY - timedelta(days=1), outcome_json=json.dumps(
        {"d1": {"cost": 43084, "clk": 29, "conv": 122000, "roas_c": 3.5753},
         "d1_st": {"match": {"term": "골프"}, "cost_total": 0, "status": "stopped"}}))
    db.commit()

    summary = vault_export._outcome_summary(e)

    assert "d1_st(stopped)" in summary and "골프" in summary
    assert "d1: roas 3.5753" in summary  # 기존 표시 불변
    # 결측·깨진 JSON에서 죽지 않는다
    e.outcome_json = json.dumps({"d1_st": None})
    assert "d1_st" not in vault_export._outcome_summary(e)


def test_reflection_skips_llm_when_no_entries(db):
    fake = _FakeLLM()
    res = diary_reflection.build_reflection(db, now=NOW, invoke=fake)
    assert res == {"skipped": "no_entries"}
    assert fake.calls == []  # LLM 미호출(비용·소음 방지)


def test_reflection_dedup_blocked_reject_one_event(db):
    # 같은 (campaign,target,action,날짜)의 blocked+reject 2행 → 1사건 병합.
    y = TODAY - timedelta(days=1)
    _entry(db, action_date=y, event_type="blocked", action="bid_up")
    _entry(db, action_date=y, event_type="reject", action="bid_up")
    db.commit()

    buckets = diary_reflection._gather(db, TODAY, NOW)
    yesterday = buckets["yesterday"]
    assert len(yesterday) == 1
    assert yesterday[0]["event_type"] == "blocked+reject"


def test_reflection_writes_observe_row(db):
    now = _now_with_entries(db)  # 실제 kst_now 기준 D-1 행 1건 시드
    fake = _FakeLLM()
    res = diary_reflection.build_reflection(db, now=now, invoke=fake)

    assert res["written"] is True and len(fake.calls) == 1
    obs = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.event_type == "observe", OpsDiaryEntry.action == "daily_reflection"
    ).one()
    assert obs.actor == "system" and obs.rationale == fake.text and obs.campaign_id == ""


def test_reflection_idempotent_same_day(db):
    now = _now_with_entries(db)
    fake = _FakeLLM()
    diary_reflection.build_reflection(db, now=now, invoke=fake)
    res2 = diary_reflection.build_reflection(db, now=now, invoke=fake)

    assert res2 == {"skipped": "already_exists"}
    obs_count = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.event_type == "observe", OpsDiaryEntry.action == "daily_reflection"
    ).count()
    assert obs_count == 1  # 재실행에도 중복 생성 안 함


def test_reflection_llm_failure_fail_open(db):
    now = _now_with_entries(db)

    def _boom(prompt, *, system, schema, model, timeout):
        raise RuntimeError("claude 다운")

    res = diary_reflection.build_reflection(db, now=now, invoke=_boom)
    assert "error" in res  # 예외 삼키고 error 반환(내일 재시도)
    assert db.query(OpsDiaryEntry).filter(OpsDiaryEntry.event_type == "observe").count() == 0


def _now_with_entries(db) -> datetime:
    """write_diary_entry의 created_at은 서버 UTC now라, 쓰기 후 idempotency 판정이 맞으려면
    실제 kst_now 기준으로 today를 잡아야 한다 — D-1 이벤트 행을 실 now 기준으로 시드한다."""
    from app.utils.kst import kst_now

    now = kst_now()
    e = OpsDiaryEntry(
        created_at=(now - timedelta(hours=9)) - timedelta(days=1),  # KST D-1
        event_type="execute", campaign_id="cmp1", target_type="keyword", target_id="nkw-1",
        action="bid_up", actor="daily",
    )
    db.add(e)
    db.commit()
    return now


# ══════════════════════════ reflection_loop harness ══════════════════════════


def test_harness_stage_isolation_backfill_failure_still_runs_reflection(db, monkeypatch):
    def _boom(db, *, now=None):
        raise RuntimeError("backfill 폭발")

    monkeypatch.setattr(reflection_loop, "backfill_outcomes", _boom)
    res = reflection_loop.run_daily_reflection(db, now=NOW)  # 이벤트 0건 → reflection은 no_entries

    assert res["stage_status"]["outcome_backfill"] == "failed"
    # ①이 실패해도 ②는 시도된다(단계 격리). ②의 값은 «시도했다»가 아니라 «무슨 일이 났나»다
    # — 이벤트 0건이므로 skipped다(D-NAO-228). 초판은 여기서도 'ok'였고, 그 'ok'가
    # 2026-07-18~08-22 결번 19일을 로그에서 지웠다(계약 PLAN_naver-m5-reflection-visibility.md §3).
    assert res["stage_status"]["daily_reflection"] == "skipped:no_entries"


def test_harness_reports_skip_not_ok_when_no_entries(db, monkeypatch):
    """★D-NAO-228: 재료가 없어 «안 돈» 것을 'ok'로 적지 않는다.

    옛 이름은 test_harness_happy_path_both_stages_ok였다 — 반성이 아예 안 돌았는데
    «happy path»라 부르고 'ok'를 기대하던 것이 결함의 화석이다.
    """
    monkeypatch.setattr(reflection_loop, "build_reflection", lambda db, *, now=None: {"skipped": "no_entries"})
    res = reflection_loop.run_daily_reflection(db, now=NOW)
    assert res["stage_status"] == {"outcome_backfill": "ok", "daily_reflection": "skipped:no_entries"}


def test_harness_reports_ok_only_when_reflection_written(db, monkeypatch):
    """실제로 써졌을 때만 'ok'다 — 세 결과가 한 값으로 뭉개지지 않는지 짝으로 고정한다."""
    monkeypatch.setattr(reflection_loop, "build_reflection",
                        lambda db, *, now=None: {"written": True, "entries": 5, "text_len": 120})
    res = reflection_loop.run_daily_reflection(db, now=NOW)
    assert res["stage_status"] == {"outcome_backfill": "ok", "daily_reflection": "ok"}


# ══════════════════════════ 크론 등록 ══════════════════════════


def test_job_function_exists_with_self_contained_session():
    assert hasattr(scheduler_service, "run_naver_diary_reflection_job")
    src = inspect.getsource(scheduler_service.run_naver_diary_reflection_job)
    assert "_get_own_db_session" in src and "db.close()" in src


def test_default_cron_is_0835_kst():
    src = inspect.getsource(scheduler_service._ensure_default_states)
    assert '("run_naver_diary_reflection", "35 8 * * *")' in src


def test_registered_in_catchup_order_after_retro_and_after_money_job():
    """P2 리뷰 P2-1: catch-up 체인에서 관찰 전용 reflection(LLM 최대 9분)은 돈 나가는
    auto_operator_daily *뒤*여야 한다(retro 뒤이기도 — outcome 최신). 재배치 회귀 방지."""
    order = scheduler_service._CATCHUP_ORDER
    assert "run_naver_diary_reflection" in order
    assert order.index("run_naver_diary_reflection") > order.index("run_naver_retro_scoring")
    assert order.index("run_naver_diary_reflection") > order.index("run_naver_auto_operator_daily")


def test_wired_in_job_func_for_and_catchup_funcs():
    assert scheduler_service.job_func_for("run_naver_diary_reflection") \
        is scheduler_service.run_naver_diary_reflection_job
    src = inspect.getsource(scheduler_service._catch_up_morning_batch)
    assert '"run_naver_diary_reflection": run_naver_diary_reflection_job' in src


def test_reflection_cron_job_is_fail_open(monkeypatch):
    """P2 리뷰 P3-1: 금지선("일기 해석 실패가 집행을 막으면 안 됨")의 최종 방어 =
    run_naver_diary_reflection_job의 fail-open(raise 없음)을 실제 예외 경로로 검증 —
    catch-up 체인에서 이 잡이 raise하면 하류 08:50 집행 잡이 죽는다."""
    from app.services import scheduler_service

    def _boom(db, **kwargs):
        raise RuntimeError("reflection 폭발(테스트)")

    monkeypatch.setattr("app.services.naver_ad.reflection_loop.run_daily_reflection", _boom)
    # 예외가 전파되면 이 호출 자체가 테스트를 실패시킨다.
    scheduler_service.run_naver_diary_reflection_job()
