# test_naver_wisdom.py — D-NAO-54 P3 승격·망각층(candidate/judge/writer/retention·wisdom_loop
# 하니스·크론 5지점·백필 스크립트) 단위 테스트. LLM은 전부 주입경계(invoke)로 몽키패치 — 실
# claude CLI 호출 0. BEP 해석은 결정성 위해 픽스처에서 고정값으로 패치(D-NAO-223 이후
# 기준자가 target_roas가 아니라 bep_roas다 — _fixed_bep).
from __future__ import annotations

import inspect
import json
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverChangeLog,
    NaverProposal,
    OpsDiaryEntry,
    OpsWisdomCandidate,
    OpsWisdomEntry,
)
from app.services import scheduler_service
from app.services.naver_ad import (
    wisdom_candidates,
    wisdom_judge,
    wisdom_loop,
    wisdom_retention,
    wisdom_writer,
)

NOW = datetime(2026, 7, 20, 8, 45)  # KST


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
def _fixed_bep(monkeypatch):
    """캠페인 **bep_roas**를 2.0으로 고정 — outcome_direction good/bad가 결정적으로 나오게.

    ★D-NAO-223(M3-b 축 ⓑ): 기준자가 `target_roas` → `bep_roas`로 바뀌었다. 픽스처 이름도
      같이 고친다 — 이 교정의 요점이 「target != bep」이라, 이름이 `_fixed_bep`으로 남으면
      다음 사람이 «목표»와 «본전»을 다시 같은 것으로 읽는다.
    """
    monkeypatch.setattr(
        wisdom_candidates.campaign_target_resolver, "resolve_bep_roas",
        lambda db, cid: {"bep_roas": 2.0, "source": "product_bep"},
    )


def _utc_for(action_date: date) -> datetime:
    """_kst_date(created_at)==action_date가 되도록 UTC created_at(=KST 12시)."""
    return datetime.combine(action_date, time(3, 0))  # +9h → 12:00 KST 같은 날


def _diary(db, *, action_date=date(2026, 7, 12), event_type="execute", campaign_id="cmp1",
           target_type="keyword", target_id="nkw-1", action="bid_up",
           weekday=6, is_kr_holiday=False, season="summer", iphone_offset=None,
           outcome=None):
    e = OpsDiaryEntry(
        created_at=_utc_for(action_date), event_type=event_type, campaign_id=campaign_id,
        target_type=target_type, target_id=target_id, action=action,
        weekday=weekday, is_kr_holiday=is_kr_holiday, season=season,
        iphone_launch_offset_days=iphone_offset,
        outcome_json=json.dumps(outcome) if outcome is not None else None,
    )
    db.add(e)
    db.flush()
    return e


def _good(cost=1000, roas_c=3.0):
    return {"d7": {"cost": cost, "clk": 10, "conv": 3000, "roas_c": roas_c}}


def _bad():
    return {"d7": {"cost": 1000, "clk": 10, "conv": 500, "roas_c": 0.5}}


# ══════════════════════════ candidate_sa ══════════════════════════


def test_harvest_skips_search_term_grain_rows(db):
    """D-NAO-178: 검색어 제외 행의 d1은 `_grain_and_target` 캠페인 폴백 탓에 **그 캠페인 전체**의
    성과다 — 승률에 남의 성적표가 쌓인다. 수확 자체를 막는다(판정 규칙은 그대로, S8에서 해제)."""
    _diary(db, target_type="search_term", target_id="골프", action="search_term_exclude",
           outcome=_good())
    res = wisdom_candidates.harvest_candidates(db, now=NOW)

    assert res["skipped_search_term_grain"] == 1
    assert res["scanned"] == 0 and res["new"] == 0
    assert db.query(OpsWisdomCandidate).count() == 0


def test_harvest_skip_does_not_revive_hidden_candidate(db):
    """순서 제약의 근거: hidden은 터미널이 아니라 같은 시그니처의 **새 행**이 오면 pending으로
    부활한다. skip이 걸린 뒤에는 검색어 제외 행이 아무리 와도 부활 창이 열리지 않는다."""
    sig = "cmp1|search_term_exclude|weekend|summer|unknown"
    db.add(OpsWisdomCandidate(
        signature=sig, campaign_id="cmp1", action="search_term_exclude",
        env_bucket_json="{}", observation="-", occurrences=1, good_count=1, bad_count=0,
        first_seen_at=NOW, last_seen_at=NOW, source_entry_ids_json="[999]", status="hidden",
        importance=5, strength=7.0,
    ))
    db.flush()
    _diary(db, target_type="search_term", target_id="골프2", action="search_term_exclude",
           outcome=_good())

    res = wisdom_candidates.harvest_candidates(db, now=NOW)

    assert res["revived"] == 0 and res["skipped_search_term_grain"] == 1
    cand = db.query(OpsWisdomCandidate).filter_by(signature=sig).one()
    assert cand.status == "hidden" and cand.good_count == 1  # tally 불변


def test_harvest_creates_new_candidate_with_signature(db):
    _diary(db, outcome=_good())
    res = wisdom_candidates.harvest_candidates(db, now=NOW)
    assert res["new"] == 1
    cand = db.query(OpsWisdomCandidate).one()
    # signature = 조건만(campaign|action|day_class|season|iphone_window) — 방향은 tally로(리뷰 P2-2)
    assert cand.signature == "cmp1|bid_up|weekend|summer|unknown"
    assert cand.occurrences == 1
    assert cand.good_count == 1 and cand.bad_count == 0
    assert cand.status == "pending"
    assert json.loads(cand.source_entry_ids_json) == [1]


def test_harvest_reoccurrence_increments_occurrences(db):
    _diary(db, outcome=_good())
    wisdom_candidates.harvest_candidates(db, now=NOW)
    # 같은 시그니처(같은 캠페인/액션/환경/방향) 새 diary 행
    _diary(db, action_date=date(2026, 7, 13), outcome=_good())
    res = wisdom_candidates.harvest_candidates(db, now=NOW)
    assert res["updated"] == 1
    cand = db.query(OpsWisdomCandidate).one()  # 여전히 1건(같은 시그니처)
    assert cand.occurrences == 2
    assert set(json.loads(cand.source_entry_ids_json)) == {1, 2}


def test_harvest_same_entry_rescan_does_not_inflate(db):
    _diary(db, outcome=_good())
    wisdom_candidates.harvest_candidates(db, now=NOW)
    # 같은 행을 다시 스캔(멱등) — occurrences 부풀리면 안 됨
    wisdom_candidates.harvest_candidates(db, now=NOW)
    cand = db.query(OpsWisdomCandidate).one()
    assert cand.occurrences == 1
    assert json.loads(cand.source_entry_ids_json) == [1]


def test_harvest_terminal_signature_ignored(db):
    _diary(db, outcome=_good())
    wisdom_candidates.harvest_candidates(db, now=NOW)
    cand = db.query(OpsWisdomCandidate).one()
    cand.status = "rejected"  # 판사가 이미 기각
    db.commit()
    _diary(db, action_date=date(2026, 7, 14), outcome=_good())  # 같은 시그니처 재등장
    res = wisdom_candidates.harvest_candidates(db, now=NOW)
    assert res["skipped_terminal"] >= 1
    db.refresh(cand)
    assert cand.occurrences == 1  # 갱신 안 됨(완전 무시)


def test_harvest_revives_hidden_on_reoccurrence(db):
    """hidden은 terminal이 아니다 — 시그니처 재등장 시 pending으로 부활하고 tally를 누적한다
    (리뷰 P2-1: 망각↔TTL 데드락 해소 + Ebbinghaus 재노출 강화)."""
    _diary(db, outcome=_good())
    wisdom_candidates.harvest_candidates(db, now=NOW)
    cand = db.query(OpsWisdomCandidate).one()
    cand.status = "hidden"  # retention이 망각시켰다고 가정
    db.commit()
    _diary(db, action_date=date(2026, 7, 14), outcome=_good())  # 같은 시그니처 재등장(새 diary 행)
    res = wisdom_candidates.harvest_candidates(db, now=NOW)
    assert res["revived"] == 1
    db.refresh(cand)
    assert cand.status == "pending"                 # 부활
    assert cand.occurrences == 2 and cand.good_count == 2
    assert set(json.loads(cand.source_entry_ids_json)) == {1, 2}


def test_harvest_skips_when_bep_unavailable(db, monkeypatch):
    monkeypatch.setattr(
        wisdom_candidates.campaign_target_resolver, "resolve_bep_roas",
        lambda db, cid: {"bep_roas": None, "source": "unavailable"},
    )
    _diary(db, outcome=_good())
    res = wisdom_candidates.harvest_candidates(db, now=NOW)
    assert res["skipped_no_target"] == 1
    assert db.query(OpsWisdomCandidate).count() == 0


def test_harvest_bad_direction_tallies_bad(db):
    _diary(db, campaign_id="cA", outcome=_bad())
    wisdom_candidates.harvest_candidates(db, now=NOW)
    c = db.query(OpsWisdomCandidate).filter_by(campaign_id="cA").one()
    assert c.bad_count == 1 and c.good_count == 0 and c.occurrences == 1


def test_harvest_cost_zero_is_neutral_skip(db):
    """cost=0 관찰은 중립 — good도 bad도 아니고 후보 생성 자체를 skip(리뷰 P2-3)."""
    _diary(db, campaign_id="cB", outcome={"d7": {"cost": 0, "clk": 0, "conv": 0, "roas_c": None}})
    res = wisdom_candidates.harvest_candidates(db, now=NOW)
    assert res["skipped_neutral"] == 1
    assert res["new"] == 0
    assert db.query(OpsWisdomCandidate).count() == 0


def test_harvest_day_class_and_iphone_window_buckets(db):
    _diary(db, campaign_id="h", weekday=2, is_kr_holiday=True, iphone_offset=3, outcome=_good())
    _diary(db, campaign_id="wd", weekday=1, is_kr_holiday=False, iphone_offset=40, outcome=_good())
    wisdom_candidates.harvest_candidates(db, now=NOW)
    sigs = {c.campaign_id: c.signature for c in db.query(OpsWisdomCandidate).all()}
    # signature = campaign|action|day_class|season|iphone_window (iphone_window은 마지막 토큰)
    assert "|holiday|" in sigs["h"] and sigs["h"].endswith("|launch_window")
    assert "|weekday|" in sigs["wd"] and sigs["wd"].endswith("|normal")


def test_harvest_only_execute_blocked_and_needs_outcome(db):
    _diary(db, event_type="reject", outcome=_good())  # reject 제외(이중계상 방지)
    _diary(db, campaign_id="c2", event_type="execute", outcome=None)  # outcome 없음 → skip
    res = wisdom_candidates.harvest_candidates(db, now=NOW)
    assert db.query(OpsWisdomCandidate).count() == 0
    assert res["new"] == 0


# ══════════════════════════ judge_sa ══════════════════════════


def _cand(db, *, signature="s", occurrences=1, first_seen=NOW, status="pending", env=None,
          good_count=0, bad_count=0):
    c = OpsWisdomCandidate(
        signature=signature, campaign_id="cmp1", action="bid_up",
        env_bucket_json=json.dumps(env or {"day_class": "weekday", "season": "summer",
                                            "iphone_window": "normal"}),
        observation="obs", occurrences=occurrences, good_count=good_count, bad_count=bad_count,
        first_seen_at=first_seen,
        last_seen_at=first_seen, source_entry_ids_json="[1]", status=status,
    )
    db.add(c)
    db.flush()
    return c


def _promote_invoke(prompt, *, system, schema, model, timeout):
    return {"json": {"verdict": "promote", "principle": "휴일엔 bid_up이 좋았다", "rationale": "3회 관찰"}}


def _reject_invoke(prompt, *, system, schema, model, timeout):
    return {"json": {"verdict": "reject", "principle": "", "rationale": "표본 부족"}}


def test_judge_ripe_gate_ttl_or_occurrences(db):
    _cand(db, signature="occ3", occurrences=3, first_seen=NOW)                 # occ≥3 → ripe
    _cand(db, signature="ttl", occurrences=1, first_seen=NOW - timedelta(days=15))  # TTL 14 경과 → ripe
    _cand(db, signature="young", occurrences=1, first_seen=NOW - timedelta(days=2))  # 미숙 → skip
    db.commit()
    res = wisdom_judge.judge_ripe_candidates(db, now=NOW, invoke=_promote_invoke)
    assert res["ripe"] == 2
    assert res["promoted"] == 2
    assert db.query(OpsWisdomCandidate).filter_by(signature="young").one().status == "pending"


def test_judge_caps_at_five_per_run(db):
    for i in range(6):
        _cand(db, signature=f"c{i}", occurrences=5)
    db.commit()
    res = wisdom_judge.judge_ripe_candidates(db, now=NOW, invoke=_promote_invoke)
    assert res["ripe"] == 5 and res["promoted"] == 5
    assert db.query(OpsWisdomCandidate).filter_by(status="pending").count() == 1


def test_judge_promote_and_reject_recorded(db):
    _cand(db, signature="p", occurrences=3)
    db.commit()
    wisdom_judge.judge_ripe_candidates(db, now=NOW, invoke=_promote_invoke)
    c = db.query(OpsWisdomCandidate).one()
    assert c.status == "promoted"
    assert json.loads(c.judge_verdict_json)["principle"].startswith("휴일")

    _cand(db, signature="r", occurrences=3)
    db.commit()
    wisdom_judge.judge_ripe_candidates(db, now=NOW, invoke=_reject_invoke)
    r = db.query(OpsWisdomCandidate).filter_by(signature="r").one()
    assert r.status == "rejected"


def test_judge_llm_failure_keeps_pending(db):
    _cand(db, signature="p", occurrences=3)
    db.commit()

    def _boom(prompt, **kwargs):
        raise RuntimeError("LLM down")

    res = wisdom_judge.judge_ripe_candidates(db, now=NOW, invoke=_boom)
    assert res["skipped_llm"] == 1
    assert db.query(OpsWisdomCandidate).one().status == "pending"


def test_judge_insufficient_response_keeps_pending(db):
    _cand(db, signature="p", occurrences=3)
    db.commit()

    def _no_rationale(prompt, **kwargs):
        return {"json": {"verdict": "promote"}}  # rationale 누락

    res = wisdom_judge.judge_ripe_candidates(db, now=NOW, invoke=_no_rationale)
    assert res["skipped_llm"] == 1
    assert db.query(OpsWisdomCandidate).one().status == "pending"


def test_judge_prompt_exposes_win_rate_and_tally(db):
    """판사 프롬프트에 good/bad 표본과 승률이 노출된다 — 분모 없이·모순 승격을 막기 위함
    (리뷰 P2-2). good 3 vs bad 10 → win_rate 0.231."""
    c = _cand(db, signature="wr", occurrences=13, good_count=3, bad_count=10)
    db.commit()
    prompt = wisdom_judge._prompt(c, NOW)
    assert '"good_count": 3' in prompt
    assert '"bad_count": 10' in prompt
    assert '"win_rate": 0.231' in prompt  # 3/13


# ══════════════════════════ writer_sa ══════════════════════════


def test_writer_creates_entry_and_informational_proposal(db):
    c = _cand(db, signature="p", status="promoted")
    c.judge_verdict_json = json.dumps({"verdict": "promote", "principle": "P원칙", "rationale": "R근거"})
    db.commit()
    res = wisdom_writer.write_wisdom(db, now=NOW)
    assert res["entries_created"] == 1
    entry = db.query(OpsWisdomEntry).one()
    assert entry.wisdom_text == "P원칙" and entry.source_candidate_id == c.id
    prop = db.query(NaverProposal).filter_by(proposal_type="wisdom_promoted").one()
    assert "P원칙" in prop.rationale and prop.status == "pending"


def test_writer_is_idempotent(db):
    c = _cand(db, signature="p", status="promoted")
    c.judge_verdict_json = json.dumps({"principle": "P", "rationale": "R"})
    db.commit()
    wisdom_writer.write_wisdom(db, now=NOW)
    res2 = wisdom_writer.write_wisdom(db, now=NOW)  # 두 번째는 skip
    assert res2["skipped_existing"] == 1
    assert db.query(OpsWisdomEntry).count() == 1


def test_wisdom_promoted_not_wired_to_execution():
    """★금지선: 지혜→실행 직접 쓰기 금지. wisdom_promoted는 정보성 집합에만 있고 실행 매핑·
    개방 액션에는 절대 없다."""
    from app.services.naver_ad.naver_execution_harness import (
        OPEN_ACTIONS,
        _ACTION_BY_PROPOSAL_TYPE,
        _WRITE_EXECUTORS,
    )
    from app.services.naver_ad.proposal_writer import (
        INFORMATIONAL_PROPOSAL_TYPES,
        _WISDOM_PROMOTED,
    )

    assert _WISDOM_PROMOTED in INFORMATIONAL_PROPOSAL_TYPES
    assert _WISDOM_PROMOTED not in _ACTION_BY_PROPOSAL_TYPE
    assert _WISDOM_PROMOTED not in OPEN_ACTIONS
    assert _WISDOM_PROMOTED not in _WRITE_EXECUTORS


# ══════════════════════════ retention_sa ══════════════════════════


def test_retention_ttl_guard_keeps_young_candidate(db):
    """단발 후보가 9일차라 s_eff(≈0.14)는 임계 아래지만, TTL(14일) 전이라 감쇠 제외 — 판사가
    볼 기회를 보장한다(리뷰 P2-1: 망각↔TTL 데드락 해소)."""
    _cand(db, signature="young", first_seen=NOW - timedelta(days=9))  # last_seen도 9일 전
    db.commit()
    res = wisdom_retention.apply_retention(db, now=NOW)
    assert res["kept"] == 1
    assert res["hidden"] == 0
    assert db.query(OpsWisdomCandidate).one().status == "pending"


def test_retention_hides_after_ttl(db):
    """TTL(14일) 경과 + 미승격 + 재등장 없음(s_eff<0.15) 후보는 감쇠(soft-hide)."""
    _cand(db, signature="old", first_seen=NOW - timedelta(days=15))  # last_seen도 15일 전
    db.commit()
    res = wisdom_retention.apply_retention(db, now=NOW)
    assert res["hidden"] == 1
    assert db.query(OpsWisdomCandidate).one().status == "hidden"


def test_retention_never_forgets_promoted(db):
    c = _cand(db, signature="p", first_seen=NOW - timedelta(days=90), status="promoted")
    db.commit()
    wisdom_retention.apply_retention(db, now=NOW)
    db.refresh(c)
    assert c.status == "promoted"  # 승격분 불망각


def test_retention_leaves_rejected_untouched(db):
    c = _cand(db, signature="r", first_seen=NOW - timedelta(days=90), status="rejected")
    db.commit()
    wisdom_retention.apply_retention(db, now=NOW)
    db.refresh(c)
    assert c.status == "rejected"


# ══════════════════════════ wisdom_loop (하니스) ══════════════════════════


def test_loop_runs_all_stages(db):
    _diary(db, outcome=_good())
    res = wisdom_loop.run_daily_wisdom(db, now=NOW)
    assert res["stage_status"] == {
        "harvest": "ok", "judge": "ok", "writer": "ok", "retention": "ok", "apply": "ok",
    }
    assert res["harvest"]["new"] == 1


def test_loop_stage_isolation(db, monkeypatch):
    """한 단계(judge) 실패가 나머지를 막지 않는다(reflection_loop 격리 패턴)."""
    def _boom(db, **kwargs):
        raise RuntimeError("judge 폭발")

    monkeypatch.setattr(wisdom_loop, "judge_ripe_candidates", _boom)
    res = wisdom_loop.run_daily_wisdom(db, now=NOW)
    assert res["stage_status"]["judge"] == "failed"
    assert res["stage_status"]["harvest"] == "ok"
    assert res["stage_status"]["writer"] == "ok"
    assert res["stage_status"]["retention"] == "ok"


# ══════════════════════════ 크론 5지점 ══════════════════════════


def test_default_cron_is_0845_kst():
    src = inspect.getsource(scheduler_service._ensure_default_states)
    assert '("run_naver_wisdom", "45 8 * * *")' in src


def test_registered_in_catchup_order_after_money_and_reflection():
    order = scheduler_service._CATCHUP_ORDER
    assert "run_naver_wisdom" in order
    assert order.index("run_naver_wisdom") > order.index("run_naver_auto_operator_daily")
    assert order.index("run_naver_wisdom") > order.index("run_naver_diary_reflection")


def test_wired_in_job_func_for_and_catchup_funcs():
    assert scheduler_service.job_func_for("run_naver_wisdom") is scheduler_service.run_naver_wisdom_job
    src = inspect.getsource(scheduler_service._catch_up_morning_batch)
    assert '"run_naver_wisdom": run_naver_wisdom_job' in src


def test_wisdom_cron_job_is_fail_open(monkeypatch):
    """금지선 최종 방어 — run_naver_wisdom_job은 raise하지 않는다(집행 체인 보호)."""
    def _boom(db, **kwargs):
        raise RuntimeError("wisdom 폭발(테스트)")

    monkeypatch.setattr("app.services.naver_ad.wisdom_loop.run_daily_wisdom", _boom)
    scheduler_service.run_naver_wisdom_job()  # 예외가 전파되면 테스트 실패


# ══════════════════════════ 백필 스크립트 ══════════════════════════


def _changelog(db, *, cid="cmp1", action="update_bid", rationale="정상", dry_run=False,
               executed_at=datetime(2026, 7, 12, 15, 0), proposal_id=None,
               entity_type="keyword", entity_id="nkw-1"):
    cl = NaverChangeLog(
        entity_type=entity_type, entity_id=entity_id, campaign_id=cid, action=action,
        rationale=rationale, before_value="100", after_value="120",
        dry_run=dry_run, changed_at=executed_at, executed_at=executed_at, proposal_id=proposal_id,
    )
    db.add(cl)
    db.flush()
    return cl


def test_backfill_classifies_and_reconstructs_env(db):
    import scripts.backfill_diary_from_changelog as bf

    _changelog(db, action="update_bid", rationale="입찰 인상")                        # execute
    _changelog(db, cid="c2", rationale="[실행 불가] 가드 차단")                        # blocked
    _changelog(db, cid="c3", rationale="[실행 실패] 쓰기 오류")                        # skip
    _changelog(db, cid="c4", action="external_bid_change", rationale="외부 변경")     # external skip
    db.commit()

    res = bf.backfill(db, apply=True)
    assert res["execute"] == 1 and res["blocked"] == 1
    assert res["skipped_failure"] == 1 and res["skipped_external"] == 1

    ex = db.query(OpsDiaryEntry).filter_by(event_type="execute").one()
    # created_at = executed_at(KST 07-12 15:00) − 9h = UTC 07-12 06:00
    assert ex.created_at == datetime(2026, 7, 12, 6, 0)
    assert ex.source_ref is not None
    # env는 KST 날짜(2026-07-12=일요일) 기준
    assert ex.weekday == date(2026, 7, 12).weekday()
    assert ex.season == "summer"
    assert ex.spend_pacing_pct is None and ex.avg_rank is None  # 추정 금지


def test_backfill_is_idempotent(db):
    import scripts.backfill_diary_from_changelog as bf

    _changelog(db, rationale="입찰 인상")
    db.commit()
    bf.backfill(db, apply=True)
    res2 = bf.backfill(db, apply=True)  # 두 번째는 source_ref 존재 → skip
    assert res2["skipped_existing"] == 1
    assert db.query(OpsDiaryEntry).count() == 1


def test_backfill_actor_from_proposal(db):
    import scripts.backfill_diary_from_changelog as bf

    p = NaverProposal(proposal_type="bid_up", campaign_id="cmp1", approval_source="auto_op", status="approved")
    db.add(p)
    db.flush()
    _changelog(db, rationale="입찰 인상", proposal_id=p.id)
    db.commit()
    bf.backfill(db, apply=True)
    entry = db.query(OpsDiaryEntry).filter_by(event_type="execute").one()
    assert entry.actor == "daily"  # auto_op → daily


def test_backfill_dry_run_writes_nothing(db):
    import scripts.backfill_diary_from_changelog as bf

    _changelog(db, rationale="입찰 인상")
    db.commit()
    res = bf.backfill(db, apply=False)
    assert res["execute"] == 1
    assert db.query(OpsDiaryEntry).count() == 0  # dry-run은 카운트만


# ══════════════════════════════════════════════════════════════════════════
# D-NAO-223 (M3-b 축 ⓑ) — 지혜 승격 게이트의 기준자를 target_roas → bep_roas로 교정
# 계약 `docs/PLAN_naver-m3-wisdom-scorecard.md` §4-B ④ · §8-Q6
# ══════════════════════════════════════════════════════════════════════════

def _diary_entry(campaign_id="cmp1", action="update_bid"):
    return OpsDiaryEntry(
        campaign_id=campaign_id, action=action, event_type="execute",
        target_type="adgroup", target_id="grp1",
    )


def test_outcome_direction_counts_above_bep_as_good_even_below_target(db, monkeypatch):
    """★축 ⓑ 교정의 핵심 — 본전(bep)을 넘겼으면 «목표(target)»에 못 미쳐도 good이다.

    `models.py`가 `target_roas = bep_roas x 공격성 배수`로 정의하므로 target >= bep이고,
    그 사이 구간(bep <= roas < target)은 **실제로 총이익을 낸** 조치다. 옛 게이트는 이걸
    `bad`로 세었고, 그 tally가 그대로 지혜 승격 심사로 올라갔다 — 북극성 M5의 성공 정의는
    「지혜 -> 총이익 기여 양수」인데 승격 게이트는 효율을 재고 있었다(ref 90 §3).
    """
    monkeypatch.setattr(
        wisdom_candidates.campaign_target_resolver, "resolve_bep_roas",
        lambda db, cid: {"bep_roas": 2.0, "source": "product_bep"},
    )

    entry = _diary_entry()
    # bep 2.0 < roas_c 3.0 < target(=2.0 x 공격성 2.5 = 5.0) — 옛 게이트라면 bad
    assert wisdom_candidates._outcome_direction(
        db, entry, {"cost": 10000, "clk": 20, "conv": 30000, "roas_c": 3.0}
    ) == "good"
    # 본전 미달은 그대로 bad — 게이트를 «없앤» 게 아니라 «기준자»를 바꾼 것이다
    assert wisdom_candidates._outcome_direction(
        db, entry, {"cost": 10000, "clk": 20, "conv": 15000, "roas_c": 1.5}
    ) == "bad"


def test_outcome_direction_no_longer_consults_target_roas(db, monkeypatch):
    """★「교정이 실제로 배선됐나」를 못박는다 — 옛 이음매가 불리면 그 자리에서 실패한다.

    이게 없으면 `resolve_bep_roas`를 추가만 해 두고 게이트는 여전히 target을 보는 상태가
    테스트를 통과한다(M2-b2에서 «소비 좌표가 미스사이트»였던 것과 같은 종류의 구멍).
    """
    monkeypatch.setattr(
        wisdom_candidates.campaign_target_resolver, "resolve_bep_roas",
        lambda db, cid: {"bep_roas": 2.0, "source": "product_bep"},
    )

    def _must_not_be_called(*a, **k):
        raise AssertionError("승격 게이트가 아직 target_roas를 본다 — 축 ⓑ 교정이 배선되지 않았다")

    monkeypatch.setattr(
        wisdom_candidates.campaign_target_resolver, "resolve_target_roas", _must_not_be_called
    )
    assert wisdom_candidates._outcome_direction(
        db, _diary_entry(), {"cost": 10000, "clk": 20, "conv": 30000, "roas_c": 3.0}
    ) == "good"


def test_outcome_direction_bep_cache_resolves_once_per_campaign(db, monkeypatch):
    """회차 내 캐시 — 일지 행마다 BEP를 다시 해석하지 않는다(교정 전엔 캐시가 없어 N+1이었다)."""
    calls = []
    monkeypatch.setattr(
        wisdom_candidates.campaign_target_resolver, "resolve_bep_roas",
        lambda db, cid: (calls.append(cid), {"bep_roas": 2.0, "source": "product_bep"})[1],
    )
    cache: dict = {}
    window = {"cost": 10000, "clk": 20, "conv": 30000, "roas_c": 3.0}
    for _ in range(3):
        wisdom_candidates._outcome_direction(db, _diary_entry(), window, bep_cache=cache)
    assert calls == ["cmp1"]  # 3행인데 해석은 1회


def test_outcome_direction_zero_cost_stays_neutral(db, monkeypatch):
    """cost=0 관찰은 여전히 neutral — 교정이 기존 정직 경계(리뷰 P2-3)를 건드리지 않았다."""
    def _must_not_be_called(*a, **k):
        raise AssertionError("cost=0인데 BEP를 해석했다 — 불필요한 조회")

    monkeypatch.setattr(
        wisdom_candidates.campaign_target_resolver, "resolve_bep_roas", _must_not_be_called
    )
    assert wisdom_candidates._outcome_direction(
        db, _diary_entry(), {"cost": 0, "clk": 0, "conv": 0, "roas_c": None}
    ) == "neutral"
