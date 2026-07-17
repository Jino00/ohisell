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
from app.models import NaverAdDaily, NaverRetroSignal, OpsDiaryEntry
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
           outcome_json=None, actor="daily"):
    e = OpsDiaryEntry(
        created_at=_utc_for(action_date), event_type=event_type, campaign_id=campaign_id,
        target_type=target_type, target_id=target_id, action=action,
        outcome_json=outcome_json, actor=actor,
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
    _entry(db, action_date=TODAY - timedelta(days=2))  # D-2 → d1 창 = D-1(07-19)
    _daily(db, TODAY - timedelta(days=1), cost=1000, clk=5, conv=500)
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
    action_date = TODAY - timedelta(days=2)
    _entry(db, action_date=action_date, action="bid_up")
    _daily(db, TODAY - timedelta(days=1), cost=1000, clk=5, conv=500)
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

    assert res == {"d1_filled": 0, "d7_filled": 0, "retro_linked": 0, "errors": 0}
    assert db.query(OpsDiaryEntry).one().outcome_json is None


def test_per_row_failure_isolated(db, monkeypatch):
    _entry(db, action_date=TODAY - timedelta(days=2), target_id="boom")
    _entry(db, action_date=TODAY - timedelta(days=2), target_id="nkw-ok")
    _daily(db, TODAY - timedelta(days=1), keyword_id="nkw-ok", cost=1000, clk=5, conv=500)
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
    _entry(db, action_date=TODAY - timedelta(days=2), target_type="campaign", target_id=None)
    _daily(db, TODAY - timedelta(days=1), cost=1000, clk=5, conv=500)  # 상세행
    db.add(NaverAdDaily(  # sentinel 롤업(합산되면 2배)
        ad_date=TODAY - timedelta(days=1), campaign_id="cmp1", campaign_type="WEB_SITE",
        adgroup_id=BACKFILL_SENTINEL_ADGROUP, keyword_id="", imp=100, clk=99,
        cost=8888, rank_sum=0, conv_direct_amt=7777, conv_indirect_amt=0,
    ))
    db.commit()

    diary_outcome.backfill_outcomes(db, now=NOW)
    d1 = json.loads(db.query(OpsDiaryEntry).one().outcome_json)["d1"]
    assert d1["cost"] == 1000 and d1["conv"] == 500  # sentinel 미포함


# ══════════════════════════ daily_reflection_sa ══════════════════════════


class _FakeLLM:
    def __init__(self, text="관찰: 목요일·소진 저조와 함께 움직임."):
        self.calls: list[dict] = []
        self.text = text

    def __call__(self, prompt, *, system, schema, model, timeout):
        self.calls.append({"prompt": prompt, "system": system, "model": model})
        return {"text": self.text, "json": None, "raw": "", "usage": {}}


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
    assert res["stage_status"]["daily_reflection"] == "ok"  # ①실패해도 ② 시도됨


def test_harness_happy_path_both_stages_ok(db, monkeypatch):
    monkeypatch.setattr(reflection_loop, "build_reflection", lambda db, *, now=None: {"skipped": "no_entries"})
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
