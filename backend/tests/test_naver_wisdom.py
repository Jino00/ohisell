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
    NaverCampaignSettings,
    NaverChangeLog,
    NaverEntity,
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


def _settings(db, campaign_id, *, experiment_batch=None):
    """D-NAO-248 전역 풀 참여 자격 — naver_campaign_settings 행이 있어야(fail-closed 미상분리를
    피하려면) 하고, experiment_batch가 NULL이어야 전역 풀에 참여한다(값이 있으면 분리 버킷)."""
    s = NaverCampaignSettings(campaign_id=campaign_id, experiment_batch=experiment_batch)
    db.add(s)
    db.flush()
    return s


def _entity(db, campaign_id, *, campaign_type="WEB_SITE"):
    """D-NAO-248 경계 축 ⓐ — naver_entity(entity_type='campaign') 행이 있어야 campaign_type을
    읽는다(없으면 fail-closed 미상분리)."""
    e = NaverEntity(
        entity_type="campaign", entity_id=campaign_id, parent_id="", campaign_id=campaign_id,
        campaign_type=campaign_type, name=campaign_id, status="on",
    )
    db.add(e)
    db.flush()
    return e


# ══════════════════════════ candidate_sa ══════════════════════════


def _st_outcome(status, *, cost_total=0, matched_terms=1):
    """d1_st만 담은 outcome(d1은 절대 안 넣는다 — search_term 행에서 d1이 읽히면 그건 금지선
    위반의 증거가 된다)."""
    return {"d1_st": {"window": "2026-07-13", "match": {"term": "골프", "mode": "exact"},
                       "by_source": {}, "required_sources": [], "cost_total": cost_total,
                       "status": status}}


def test_harvest_search_term_stopped_is_good(db):
    """S8(D-NAO-178 해제): d1_st.status=='stopped' → good tally 기여."""
    _diary(db, target_type="search_term", target_id="골프", action="search_term_exclude",
           outcome=_st_outcome("stopped"))
    res = wisdom_candidates.harvest_candidates(db, now=NOW)

    assert res["search_term_good"] == 1 and res["search_term_bad"] == 0
    assert res["scanned"] == 1
    cand = db.query(OpsWisdomCandidate).one()
    assert cand.good_count == 1 and cand.bad_count == 0


def test_harvest_search_term_leaking_is_bad(db):
    """d1_st.status=='leaking' → bad tally 기여."""
    _diary(db, target_type="search_term", target_id="골프", action="search_term_exclude",
           outcome=_st_outcome("leaking", cost_total=5000))
    res = wisdom_candidates.harvest_candidates(db, now=NOW)

    assert res["search_term_bad"] == 1 and res["search_term_good"] == 0
    cand = db.query(OpsWisdomCandidate).one()
    assert cand.bad_count == 1 and cand.good_count == 0


def test_harvest_search_term_ambiguous_is_skipped(db):
    """d1_st.status=='ambiguous' → skip + 카운터, 후보 생성 없음."""
    _diary(db, target_type="search_term", target_id="골프", action="search_term_exclude",
           outcome=_st_outcome("ambiguous", cost_total=1200, matched_terms=2))
    res = wisdom_candidates.harvest_candidates(db, now=NOW)

    assert res["skipped_search_term_ambiguous"] == 1
    assert res["scanned"] == 0 and res["new"] == 0
    assert db.query(OpsWisdomCandidate).count() == 0


def test_harvest_search_term_no_data_is_skipped(db):
    """d1_st.status=='no_data'(원료 마감까지 못 채움) → skip + 카운터."""
    _diary(db, target_type="search_term", target_id="골프", action="search_term_exclude",
           outcome=_st_outcome("no_data"))
    res = wisdom_candidates.harvest_candidates(db, now=NOW)

    assert res["skipped_search_term_no_data"] == 1
    assert res["scanned"] == 0 and res["new"] == 0
    assert db.query(OpsWisdomCandidate).count() == 0


def test_harvest_search_term_missing_d1_st_is_skipped(db):
    """d1_st 자체가 outcome에 없는 search_term 행(아직 diary_outcome 스윕 전) → skip + 카운터.
    d1만 있고 d1_st가 없는 경우도 포함 — 이때 d1이 있어도 절대 읽지 않는다(불변식 테스트가
    d1 소비 금지를 별도로 고정한다)."""
    _diary(db, target_type="search_term", target_id="골프", action="search_term_exclude",
           outcome=_good())  # d7만 있고 d1_st 없음
    res = wisdom_candidates.harvest_candidates(db, now=NOW)

    assert res["skipped_search_term_no_d1_st"] == 1
    assert res["scanned"] == 0 and res["new"] == 0
    assert db.query(OpsWisdomCandidate).count() == 0


def test_harvest_search_term_never_consumes_d1_campaign_fallback(db):
    """★불변식(금지선) — search_term 행의 outcome에 d1(good으로 읽힐 cost/roas_c)이 있어도
    d1_st.status가 leaking이면 결과는 bad여야 한다. d1이 소비됐다면 good이 됐을 것이므로,
    이 결과가 bad라는 사실 자체가 d1이 안 읽혔다는 증거다."""
    outcome = {
        "d1": {"cost": 43084, "clk": 50, "conv": 200000, "roas_c": 4.6},  # good으로 읽힐 값
        "d1_st": {"window": "2026-07-13", "match": {"term": "골프", "mode": "exact"},
                   "by_source": {}, "required_sources": [], "cost_total": 5000,
                   "status": "leaking"},
    }
    _diary(db, target_type="search_term", target_id="골프", action="search_term_exclude",
           outcome=outcome)
    res = wisdom_candidates.harvest_candidates(db, now=NOW)

    assert res["search_term_bad"] == 1 and res["search_term_good"] == 0
    cand = db.query(OpsWisdomCandidate).one()
    assert cand.bad_count == 1 and cand.good_count == 0


def test_harvest_search_term_never_consumes_d1_campaign_fallback_reverse(db):
    """반대 조합 — d1이 bad로 읽힐 값이어도 d1_st.status가 stopped면 결과는 good이어야 한다."""
    outcome = {
        "d1": {"cost": 43084, "clk": 50, "conv": 1000, "roas_c": 0.02},  # bad로 읽힐 값
        "d1_st": {"window": "2026-07-13", "match": {"term": "골프", "mode": "exact"},
                   "by_source": {}, "required_sources": [], "cost_total": 0,
                   "status": "stopped"},
    }
    _diary(db, target_type="search_term", target_id="골프", action="search_term_exclude",
           outcome=outcome)
    res = wisdom_candidates.harvest_candidates(db, now=NOW)

    assert res["search_term_good"] == 1 and res["search_term_bad"] == 0
    cand = db.query(OpsWisdomCandidate).one()
    assert cand.good_count == 1 and cand.bad_count == 0


def test_harvest_non_search_term_direction_unchanged(db):
    """회귀 — 기존 비-search_term(keyword) 행의 판정 로직은 이번 변경으로 안 바뀐다
    (d1/d7 기반 _outcome_window/_outcome_direction 경로 그대로)."""
    _diary(db, target_type="keyword", target_id="nkw-1", action="bid_up", outcome=_good())
    res = wisdom_candidates.harvest_candidates(db, now=NOW)

    assert res["new"] == 1
    cand = db.query(OpsWisdomCandidate).one()
    assert cand.good_count == 1 and cand.bad_count == 0
    assert res["search_term_good"] == 0 and res["search_term_bad"] == 0


def test_harvest_creates_new_candidate_with_signature(db):
    """★D-NAO-248 이후: 픽스처 campaign_id="cmp1"에 naver_campaign_settings 행이 없으므로
    fail-closed 미상분리 버킷("g?|"접두사, 캠페인 단위 고립)으로 간다 — 전역 풀 형식은
    test_harvest_pools_across_campaigns_by_global_signature 참조."""
    _diary(db, outcome=_good())
    res = wisdom_candidates.harvest_candidates(db, now=NOW)
    assert res["new"] == 1
    assert res["separated_unknown"] == 1
    cand = db.query(OpsWisdomCandidate).one()
    # 시그니처 = 조건만(방향 제외, 리뷰 P2-2) — "g?|"는 fail-closed 미상분리(캠페인 단위)
    assert cand.signature == "g?|cmp1|bid_up|weekend|summer|unknown"
    assert cand.occurrences == 1
    assert cand.good_count == 1 and cand.bad_count == 0
    assert cand.status == "pending"
    assert json.loads(cand.source_entry_ids_json) == [1]
    # ★grain은 시그니처와 «일치»해야 한다 — 이 픽스처엔 naver_campaign_settings 행이 없어
    #   fail-closed 미상분리(`g?|`, 캠페인 1개 단위)를 탄다. 여기에 "global"을 적으면 라벨과
    #   실체가 모순이고, 소비층이 grain으로 거를 때 미상분리분이 전역 통계에 섞인다.
    assert cand.grain == "campaign"
    assert cand.campaign_id == "cmp1"  # 미상분리 후보만 캠페인 id를 남긴다
    assert json.loads(cand.by_campaign_json) == {"cmp1": {"good": 1, "bad": 0}}


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


# ══════════════════════════════════════════════════════════════════════════
# D-NAO-248(2026-08-25) — 전역 시그니처(끊김 1 수리): 계약 부록 Q2 처분 (b′)
# 「전역 시그니처 «단일» grain + 캠페인별 분해를 후보 «안에» 병기」
# ══════════════════════════════════════════════════════════════════════════


def test_harvest_pools_across_campaigns_by_global_signature(db):
    """합산 — 서로 다른 캠페인 3개의 같은(유형·액션·환경) 일기가 후보 1행으로 합쳐지고
    occurrences가 합계와 같다(§1의 91회 4캠페인 분산 문제를 정면으로 되짚는 케이스)."""
    for cid in ("cA", "cB", "cC"):
        _settings(db, cid)  # experiment_batch=NULL → 전역 풀 참여 자격
        _entity(db, cid, campaign_type="WEB_SITE")
    _diary(db, campaign_id="cA", outcome=_good())
    _diary(db, campaign_id="cB", action_date=date(2026, 7, 13), outcome=_good())
    _diary(db, campaign_id="cC", action_date=date(2026, 7, 14), outcome=_bad())
    res = wisdom_candidates.harvest_candidates(db, now=NOW)

    assert res["new"] == 1 and res["updated"] == 2  # 1건 신규 + 2건 같은 후보 갱신
    assert res["separated_experiment"] == 0 and res["separated_unknown"] == 0
    cand = db.query(OpsWisdomCandidate).one()
    assert cand.signature == "g|WEB_SITE|bid_up|weekend|summer|unknown|"
    assert cand.grain == "global" and cand.campaign_type == "WEB_SITE"
    assert cand.experiment_batch is None
    assert cand.campaign_id == ""  # 전역 후보는 캠페인 ID를 담지 않는다
    assert cand.occurrences == 3
    assert cand.good_count == 2 and cand.bad_count == 1
    by_campaign = json.loads(cand.by_campaign_json)
    assert by_campaign == {
        "cA": {"good": 1, "bad": 0}, "cB": {"good": 1, "bad": 0}, "cC": {"good": 0, "bad": 1},
    }
    assert "cmp" not in cand.observation and "cA" not in cand.observation  # 캠페인 ID가 요약문에 안 박힌다


def test_harvest_separates_experiment_batch_from_global_pool(db):
    """경계 축 ⓑ — experiment_batch가 붙은 캠페인의 일기는 전역 풀에 안 섞이고 분리 후보로
    가며 separated_experiment 카운터가 오른다. 같은(유형·액션·환경) 조건이라도 배치 없는
    캠페인과 signature가 갈린다(대조군 오염 방지, 부록 Q3)."""
    _settings(db, "cMop", experiment_batch="iphone-philosophy-ab:mop")
    _entity(db, "cMop", campaign_type="WEB_SITE")
    _settings(db, "cOurs")  # experiment_batch=NULL → 전역 풀
    _entity(db, "cOurs", campaign_type="WEB_SITE")

    _diary(db, campaign_id="cMop", outcome=_good())
    _diary(db, campaign_id="cOurs", action_date=date(2026, 7, 13), outcome=_good())
    res = wisdom_candidates.harvest_candidates(db, now=NOW)

    assert res["new"] == 2
    assert res["separated_experiment"] == 1
    cands = {c.experiment_batch: c for c in db.query(OpsWisdomCandidate).all()}
    assert set(cands.keys()) == {None, "iphone-philosophy-ab:mop"}
    mop_cand = cands["iphone-philosophy-ab:mop"]
    pool_cand = cands[None]
    assert mop_cand.signature != pool_cand.signature
    assert mop_cand.signature == "g|WEB_SITE|bid_up|weekend|summer|unknown|iphone-philosophy-ab:mop"
    assert mop_cand.occurrences == 1 and pool_cand.occurrences == 1  # 안 섞였다


def test_harvest_unknown_campaign_fails_closed_and_separates(db):
    """경계 미상 — naver_campaign_settings 행이 아예 없는 캠페인은 전역 풀에 넣지 않고
    fail-closed 분리 버킷("g?|")으로 가며 separated_unknown 카운터가 오른다. campaign_type이
    없어 못 읽는 경우(설정 행은 있는데 entity 행이 없는 경우)도 같은 카운터로 잡힌다."""
    _settings(db, "cKnown")
    _entity(db, "cKnown", campaign_type="SHOPPING")
    _diary(db, campaign_id="cKnown", outcome=_good())              # 전역 풀
    _diary(db, campaign_id="cNoSettings", action_date=date(2026, 7, 13), outcome=_good())  # settings 행 없음
    _settings(db, "cNoEntity")  # settings는 있지만 naver_entity 행이 없다
    _diary(db, campaign_id="cNoEntity", action_date=date(2026, 7, 14), outcome=_good())

    res = wisdom_candidates.harvest_candidates(db, now=NOW)

    assert res["separated_unknown"] == 2  # cNoSettings + cNoEntity
    assert res["separated_experiment"] == 0
    sigs = {c.campaign_id: c.signature for c in db.query(OpsWisdomCandidate).all() if c.campaign_id}
    assert sigs["cNoSettings"] == "g?|cNoSettings|bid_up|weekend|summer|unknown"
    assert sigs["cNoEntity"] == "g?|cNoEntity|bid_up|weekend|summer|unknown"
    # 전역 풀(cKnown)과 fail-closed 분리(cNoSettings/cNoEntity)는 별개 행 3건
    assert db.query(OpsWisdomCandidate).count() == 3


def test_harvest_global_signature_reharvest_is_idempotent(db):
    """멱등 재수확 — 같은 일기를 두 번 수확해도 occurrences가 안 부풀고 by_campaign_json도
    안 부푼다(entry id dedup은 전역 시그니처에서도 그대로 유효해야 한다)."""
    _settings(db, "cA")
    _entity(db, "cA", campaign_type="WEB_SITE")
    _diary(db, campaign_id="cA", outcome=_good())
    wisdom_candidates.harvest_candidates(db, now=NOW)
    res2 = wisdom_candidates.harvest_candidates(db, now=NOW)  # 같은 행 재스캔

    assert res2["new"] == 0 and res2["updated"] == 0
    cand = db.query(OpsWisdomCandidate).one()
    assert cand.occurrences == 1
    assert json.loads(cand.by_campaign_json) == {"cA": {"good": 1, "bad": 0}}


def test_harvest_leaves_legacy_promoted_rejected_untouched(db):
    """이력 보존 — status가 promoted/rejected인 레거시(grain=NULL, campaign_id 선두 시그니처)
    후보 행은 재수확 후에도 카운트·status가 그대로다. 새 grain='global' 행만 별개로 생긴다
    (기존 27건 = 그대로 둔다 — 소급 재계산이 아니라 소급 재수확)."""
    legacy_promoted = OpsWisdomCandidate(
        signature="cA|bid_up|weekend|summer|unknown", campaign_id="cA", action="bid_up",
        env_bucket_json="{}", observation="레거시 승격 지혜", occurrences=5, good_count=5, bad_count=0,
        first_seen_at=NOW, last_seen_at=NOW, source_entry_ids_json="[901,902,903,904,905]",
        status="promoted", importance=5, strength=7.0, grain=None,
    )
    legacy_rejected = OpsWisdomCandidate(
        signature="cB|bid_up|weekend|summer|unknown", campaign_id="cB", action="bid_up",
        env_bucket_json="{}", observation="레거시 기각", occurrences=3, good_count=1, bad_count=2,
        first_seen_at=NOW, last_seen_at=NOW, source_entry_ids_json="[801,802,803]",
        status="rejected", importance=5, strength=7.0, grain=None,
    )
    db.add_all([legacy_promoted, legacy_rejected])
    db.commit()

    _settings(db, "cA")
    _entity(db, "cA", campaign_type="WEB_SITE")
    _diary(db, campaign_id="cA", outcome=_good())  # 신형 harvester가 다시 스캔
    res = wisdom_candidates.harvest_candidates(db, now=NOW)

    assert res["new"] == 1  # 새 grain='global' 행 1건만 생긴다(레거시 재사용 안 됨)
    db.refresh(legacy_promoted)
    db.refresh(legacy_rejected)
    assert legacy_promoted.occurrences == 5 and legacy_promoted.status == "promoted"
    assert legacy_rejected.occurrences == 3 and legacy_rejected.status == "rejected"
    assert legacy_promoted.source_entry_ids_json == "[901,902,903,904,905]"  # 손 안 댐
    global_cand = db.query(OpsWisdomCandidate).filter(OpsWisdomCandidate.grain == "global").one()
    assert global_cand.signature != legacy_promoted.signature  # 접두사가 달라 겹칠 수 없다
    assert db.query(OpsWisdomCandidate).count() == 3  # 레거시 2건 + 신형 1건


# ══════════════════════════ judge_sa ══════════════════════════


def _cand(db, *, signature="s", occurrences=1, first_seen=NOW, status="pending", env=None,
          good_count=0, bad_count=0, action="bid_up", grain=None, campaign_type=None,
          experiment_batch=None, by_campaign_json=None):
    c = OpsWisdomCandidate(
        signature=signature, campaign_id="cmp1", action=action,
        env_bucket_json=json.dumps(env or {"day_class": "weekday", "season": "summer",
                                            "iphone_window": "normal"}),
        observation="obs", occurrences=occurrences, good_count=good_count, bad_count=bad_count,
        first_seen_at=first_seen,
        last_seen_at=first_seen, source_entry_ids_json="[1]", status=status,
        grain=grain, campaign_type=campaign_type, experiment_batch=experiment_batch,
        by_campaign_json=by_campaign_json,
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


def test_judge_prompt_exposes_sibling_buckets_and_by_campaign(db):
    """★D-NAO-248 §1 — 판사 재료에 형제 버킷(같은 액션의 다른 후보)과 by_campaign 분해가
    실린다(이질성 가시화, 부록 Q2). db가 없으면(레거시 호출부 호환) sibling_buckets=[]."""
    target = _cand(
        db, signature="g|WEB_SITE|bid_up|weekday|summer|normal|", occurrences=3,
        good_count=2, bad_count=1, grain="global", campaign_type="WEB_SITE",
        by_campaign_json=json.dumps({"cA": {"good": 2, "bad": 0}, "cB": {"good": 0, "bad": 1}}),
    )
    _cand(db, signature="sib1", occurrences=5, good_count=1, bad_count=4,
          env={"day_class": "holiday", "season": "summer", "iphone_window": "normal"})
    db.commit()

    prompt_with_db = wisdom_judge._prompt(target, NOW, db)
    assert '"signature": "sib1"' in prompt_with_db
    assert '"n": 5' in prompt_with_db
    assert '"cA": {"good": 2, "bad": 0}' in prompt_with_db  # by_campaign 병기

    prompt_without_db = wisdom_judge._prompt(target, NOW)  # db 생략 — 레거시 호출부 호환
    assert '"sibling_buckets": []' in prompt_without_db


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
