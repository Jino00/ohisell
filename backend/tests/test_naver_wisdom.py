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
    """promoted는 완전 terminal — 재등장해도 tally조차 갱신하지 않는다.

    ★D-NAO-251: 구판은 rejected도 여기 함께 묶여 있었으나 분리됐다(아래 재개방 테스트군).
    promoted가 남은 이유는 답이 달라야 하기 때문이다 — 승격 지혜의 사후 성적은 지혜 성적표가
    «집행 결과»로 재는 것이고, 승격↔기각 플립플롭은 브리핑 주입을 흔든다.
    """
    _diary(db, outcome=_good())
    wisdom_candidates.harvest_candidates(db, now=NOW)
    cand = db.query(OpsWisdomCandidate).one()
    cand.status = "promoted"  # 판사가 이미 승격
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
    """평시(숙성 ≤5)엔 회차 상한 5가 그대로 적용된다 — 적응형이 평시를 안 건드린다.

    ★D-NAO-251 §4-③: 상한은 이제 «적체가 있을 때만» 15로 올라간다. 그 경계를 두 방향으로
    잠근다 — 이 테스트가 «평시 불변», 아래가 «적체 시 캐치업»과 «하드캡».
    """
    for i in range(5):
        _cand(db, signature=f"c{i}", occurrences=5)
    db.commit()
    res = wisdom_judge.judge_ripe_candidates(db, now=NOW, invoke=_promote_invoke)
    assert res["ripe"] == 5 and res["promoted"] == 5
    assert res["cap_applied"] == wisdom_judge._MAX_PER_RUN == 5
    assert res["backlog_remaining"] == 0
    assert db.query(OpsWisdomCandidate).filter_by(status="pending").count() == 0


def test_judge_catches_up_when_backlog_and_still_hard_caps(db):
    """★D-NAO-251 §4-③ — 적체(숙성 >5)면 같은 회차에서 더 소화하되 하루 하드캡 15를 넘지 않는다.

    구판은 pending 17건이면 회당 5건 × 1일 1회라 소화에 4일이 걸렸고, 크론이 하루 못 뜨면
    (08-24 prod 1h48m 다운 실전례) 그날 슬롯이 그냥 사라졌다. 주기를 늘리는 건 답이 아니다
    (재료 grain이 D-1 — 북극성 §5-2 「주기를 부풀리는 것은 5,403배 오류와 같은 부류」).
    """
    for i in range(20):
        _cand(db, signature=f"c{i}", occurrences=5)
    db.commit()
    res = wisdom_judge.judge_ripe_candidates(db, now=NOW, invoke=_promote_invoke)
    assert res["cap_applied"] == wisdom_judge._MAX_PER_RUN_BACKLOG == 15
    assert res["ripe"] == 15 and res["promoted"] == 15
    assert res["ripe_available"] == 20
    assert res["backlog_remaining"] == 5  # 익일로 넘긴 건수가 «값»으로 보인다(침묵 금지)
    assert db.query(OpsWisdomCandidate).filter_by(status="pending").count() == 5


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
    """★D-NAO-248 §1·§2 — 판사 재료에 형제 버킷(조건 대조군 dict 구조)과 by_campaign 분해가
    실린다(이질성 가시화, 부록 Q2). db가 없으면(레거시 호출부 호환) sibling_buckets의 4키가
    전부 빈 값(리스트 []·카운터 0)으로 실린다(스펙 1 — 침묵이 아니라 명시적 빈 구조)."""
    target = _cand(
        db, signature="g|WEB_SITE|bid_up|weekday|summer|normal|", occurrences=3,
        good_count=2, bad_count=1, grain="global", campaign_type="WEB_SITE",
        by_campaign_json=json.dumps({"cA": {"good": 2, "bad": 0}, "cB": {"good": 0, "bad": 1}}),
    )
    # 진짜 조건 대조군이 되려면 형제도 grain='global' ∧ 같은 campaign_type ∧ experiment_batch 없음이어야 한다.
    _cand(db, signature="sib1", occurrences=5, good_count=1, bad_count=4,
          grain="global", campaign_type="WEB_SITE",
          env={"day_class": "holiday", "season": "summer", "iphone_window": "normal"})
    db.commit()

    prompt_with_db = wisdom_judge._prompt(target, NOW, db)
    assert '"signature": "sib1"' in prompt_with_db
    assert '"n": 5' in prompt_with_db
    assert '"condition_controls"' in prompt_with_db
    assert '"cA": {"good": 2, "bad": 0}' in prompt_with_db  # by_campaign 병기

    prompt_without_db = wisdom_judge._prompt(target, NOW)  # db 생략 — 레거시 호출부 호환
    assert (
        '"sibling_buckets": {"condition_controls": [], "other_campaign_types": [], '
        '"excluded_from_controls": {"experiment_batch": 0, "legacy_grain": 0, "unknown_boundary": 0, '
        '"candidate_not_eligible": 0, "no_action": 0}, '
        '"truncated": {"condition_controls": 0, "other_campaign_types": 0}}'
    ) in prompt_without_db


# ══════════════════════════════════════════════════════════════════════════
# D-NAO-248(2026-08-25) §2 — _sibling_buckets 재구조화(조건 대조군 분류)
# ══════════════════════════════════════════════════════════════════════════


def test_sibling_buckets_differs_in_picks_only_the_differing_env_dims(db):
    """형제가 day_class만 다르면 differs_in == ["day_class"] (다른 축은 안 실린다)."""
    target = _cand(
        db, signature="target", grain="global", campaign_type="WEB_SITE",
        env={"day_class": "weekday", "season": "summer", "iphone_window": "normal"},
    )
    _cand(
        db, signature="sib_day", grain="global", campaign_type="WEB_SITE",
        env={"day_class": "holiday", "season": "summer", "iphone_window": "normal"},
    )
    db.commit()
    out = wisdom_judge._sibling_buckets(db, target)
    assert len(out["condition_controls"]) == 1
    assert out["condition_controls"][0]["differs_in"] == ["day_class"]
    # ★P2-1 — 후보가 적격(grain='global' ∧ experiment_batch 없음)이면 candidate_not_eligible은 0.
    assert out["excluded_from_controls"]["candidate_not_eligible"] == 0


def test_sibling_buckets_experiment_batch_excluded_from_controls(db):
    """experiment_batch 있는 형제는 condition_controls에 안 들어가고
    excluded_from_controls["experiment_batch"]가 오른다(풀링 경계, 계약 §2)."""
    target = _cand(db, signature="target", grain="global", campaign_type="WEB_SITE")
    _cand(
        db, signature="sib_batch", grain="global", campaign_type="WEB_SITE",
        experiment_batch="iphone-philosophy-ab:mop",
    )
    db.commit()
    out = wisdom_judge._sibling_buckets(db, target)
    assert out["condition_controls"] == []
    assert out["excluded_from_controls"]["experiment_batch"] == 1


def test_sibling_buckets_legacy_grain_vs_unknown_boundary(db):
    """grain != 'global' 형제는 legacy_grain으로 가되, signature가 "g?"로 시작하면
    unknown_boundary로 별도로 센다(경계 미상 분리, 부록 규칙 1)."""
    target = _cand(db, signature="target", grain="global", campaign_type="WEB_SITE")
    _cand(db, signature="legacy1", grain=None)                 # 레거시(경계 안 미상)
    _cand(db, signature="g?|cX|bid_up|weekend|summer|normal", grain=None)  # 경계 미상
    db.commit()
    out = wisdom_judge._sibling_buckets(db, target)
    assert out["excluded_from_controls"]["legacy_grain"] == 1
    assert out["excluded_from_controls"]["unknown_boundary"] == 1
    assert out["condition_controls"] == []


def test_sibling_buckets_different_campaign_type_is_not_a_control(db):
    """campaign_type이 다른 형제는 other_campaign_types로 가고 condition_controls·
    excluded_from_controls 어디에도 안 잡힌다(대조군 아님, 규칙 3)."""
    target = _cand(db, signature="target", grain="global", campaign_type="WEB_SITE")
    _cand(db, signature="sib_type", grain="global", campaign_type="SHOPPING")
    db.commit()
    out = wisdom_judge._sibling_buckets(db, target)
    assert out["condition_controls"] == []
    assert len(out["other_campaign_types"]) == 1
    assert out["other_campaign_types"][0]["signature"] == "sib_type"
    assert out["excluded_from_controls"] == {
        "experiment_batch": 0, "legacy_grain": 0, "unknown_boundary": 0, "candidate_not_eligible": 0,
    }


def test_sibling_buckets_candidate_without_controls_stays_empty(db):
    """후보 자신이 grain != 'global'이거나 experiment_batch를 가지면 condition_controls는
    항상 빈 리스트(규칙 0, fail-closed) — 진짜 조건 대조군이 될 형제가 있어도 마찬가지다.

    ★P2-1(리뷰 20260825) — 그 형제는 어느 버킷에도 안 잡힌 채 사라지지 않는다:
    excluded_from_controls["candidate_not_eligible"]가 그 건수를 센다(「대조군 없음」과
    「대조를 하지 않았다」를 구분하는 카운터 — 값이 0이면 침묵이 아니라 진짜 0이다)."""
    # 케이스 A — 레거시 후보(grain=None)
    legacy_target = _cand(db, signature="legacy_target", grain=None, campaign_type="WEB_SITE")
    _cand(db, signature="would_be_control_a", grain="global", campaign_type="WEB_SITE")
    db.commit()
    out_a = wisdom_judge._sibling_buckets(db, legacy_target)
    assert out_a["condition_controls"] == []
    assert out_a["excluded_from_controls"]["candidate_not_eligible"] == 1

    # 케이스 B — 실험 배치가 붙은 후보
    exp_target = _cand(
        db, signature="exp_target", action="bid_down", grain="global", campaign_type="WEB_SITE",
        experiment_batch="iphone-philosophy-ab:mop",
    )
    _cand(db, signature="would_be_control_b", action="bid_down", grain="global", campaign_type="WEB_SITE")
    db.commit()
    out_b = wisdom_judge._sibling_buckets(db, exp_target)
    assert out_b["condition_controls"] == []
    assert out_b["excluded_from_controls"]["candidate_not_eligible"] == 1


def test_sibling_buckets_excluded_counter_is_census_not_windowed(db):
    """excluded_from_controls는 전수 기준 — condition_controls가 상한(8)에 안 걸려도
    experiment_batch 제외 형제가 8건보다 많으면 그 전수가 그대로 잡힌다(창에 갇힌 숫자 금지)."""
    target = _cand(db, signature="target", grain="global", campaign_type="WEB_SITE")
    for i in range(12):
        _cand(
            db, signature=f"sib_batch_{i}", grain="global", campaign_type="WEB_SITE",
            experiment_batch=f"batch{i}",
        )
    db.commit()
    out = wisdom_judge._sibling_buckets(db, target)
    assert out["excluded_from_controls"]["experiment_batch"] == 12
    assert out["condition_controls"] == []


def test_sibling_buckets_truncated_reports_clipped_counts(db):
    """condition_controls·other_campaign_types가 상한(8·4)보다 많으면 truncated가 잘린
    건수를 정확히 보고한다."""
    target = _cand(
        db, signature="target", grain="global", campaign_type="WEB_SITE",
        env={"day_class": "weekday", "season": "summer", "iphone_window": "normal"},
    )
    for i in range(10):  # 상한 8 초과 → 2건 잘림
        _cand(
            db, signature=f"cc_{i}", grain="global", campaign_type="WEB_SITE",
            env={"day_class": "holiday", "season": "summer", "iphone_window": "normal"},
            occurrences=10 - i, good_count=10 - i, bad_count=0,
        )
    for i in range(6):  # 상한 4 초과 → 2건 잘림
        _cand(db, signature=f"ot_{i}", grain="global", campaign_type="SHOPPING")
    db.commit()
    out = wisdom_judge._sibling_buckets(db, target)
    assert len(out["condition_controls"]) == 8
    assert len(out["other_campaign_types"]) == 4
    assert out["truncated"] == {"condition_controls": 2, "other_campaign_types": 2}


def test_sibling_buckets_none_db_or_no_action_returns_full_key_set(db):
    """db=None이거나 cand.action이 없으면 4키 전부(빈 리스트·카운터 0)인 dict를 돌려준다
    (None이나 빈 dict가 아니다 — 키 부재와 0건은 다르다)."""
    target = _cand(db, signature="target", grain="global", campaign_type="WEB_SITE")
    db.commit()
    out_no_db = wisdom_judge._sibling_buckets(None, target)
    assert set(out_no_db.keys()) == {
        "condition_controls", "other_campaign_types", "excluded_from_controls", "truncated",
    }
    assert out_no_db["condition_controls"] == [] and out_no_db["other_campaign_types"] == []
    assert out_no_db["excluded_from_controls"] == {
        "experiment_batch": 0, "legacy_grain": 0, "unknown_boundary": 0, "candidate_not_eligible": 0,
        "no_action": 0,  # ★D-NAO-251 §4-② ⓐ — 0이어도 키가 있다(교훈 #318)
    }
    assert out_no_db["truncated"] == {"condition_controls": 0, "other_campaign_types": 0}

    target.action = None
    out_no_action = wisdom_judge._sibling_buckets(db, target)
    assert out_no_action == out_no_db  # 형제가 없으면 no_action도 0


def test_sibling_buckets_counts_action_less_siblings_instead_of_silence(db):
    """★D-NAO-251 §4-② ⓐ — action이 없어 «형제 매칭 자체가 불가능»했던 건수를 값으로 낸다.

    구판은 `not cand.action`에서 전부 0인 dict를 그대로 돌려줬다. 그러면 판정문엔 「대조군
    없음」으로 보이는데 실제로는 «대조를 시도조차 못 했다»였다 — n=52 P2-1이 규칙 0 경로에
    대해 고친 것과 **같은 모양의 두 번째 침묵**이다. prod 실재 사례: 후보 45번
    `g|SHOPPING|None|weekday|summer|normal|`.

    ★action을 매칭 «값»으로 쓰지는 않는다(미상끼리 묶으면 서로 다른 액션이 가짜 대조군이 되어
    판사 재료가 오염된다) — 세기만 한다.
    """
    target = _cand(db, signature="t-none", grain="global", campaign_type="SHOPPING")
    target.action = None
    for i in range(3):  # 같은 처지의 형제들 — 구판에선 어느 카운터에도 안 잡혔다
        sib = _cand(db, signature=f"sib-none-{i}", grain="global", campaign_type="SHOPPING")
        sib.action = None
    _cand(db, signature="sib-has-action", grain="global", campaign_type="SHOPPING")  # action 있음
    db.commit()

    out = wisdom_judge._sibling_buckets(db, target)
    assert out["excluded_from_controls"]["no_action"] == 3  # 자기 자신 제외, action 있는 형제 제외
    assert out["condition_controls"] == []  # 매칭은 여전히 안 한다(오염 금지)


# ══════════════════════════════════════════════════════════════════════════
# D-NAO-248(2026-08-25) §3 — _SYSTEM scope 문단 교체(회귀 잠금 포함)
# ══════════════════════════════════════════════════════════════════════════


def test_system_scope_paragraph_asks_about_harm_to_other_conditions():
    """새 scope 문안의 핵심 문구가 실재한다 — 「이 지혜가 항상 참인가」가 아니라 「전역 반영이
    다른 조건들에 손해를 끼치는가」를 묻는 질문 재구성(계약 §2)."""
    assert "다른 조건들이 손해를 보는가" in wisdom_judge._SYSTEM
    assert "condition_controls" in wisdom_judge._SYSTEM
    # ★fail-closed 기본값 문장은 여전히 존재해야 한다(우기지 말라는 지침이 사라지면 안 된다).
    assert "판단이 서지 않으면" in wisdom_judge._SYSTEM
    assert '"conditional"' in wisdom_judge._SYSTEM


def test_system_promote_reject_criteria_survive_the_scope_rewrite():
    """★회귀 잠금 — promote/reject 4기준 문장은 scope 문단 교체와 무관하게 그대로 존재해야
    한다(스펙 4: 이 문장들은 1비트도 바꾸지 않는다)."""
    assert "단발 사건이 아니라" in wisdom_judge._SYSTEM
    assert "good과 bad가 모순되게 팽팽하면" in wisdom_judge._SYSTEM


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


# ══════════════════════════════════════════════════════════════════════════
# D-NAO-251(2026-08-26) 증거보전 — ①재개방 ②action 미상 ③캐치업
# 계약: docs/contracts/CONTRACT_evidence_preservation.md
# ══════════════════════════════════════════════════════════════════════════


def test_harvest_rejected_keeps_tallying_instead_of_freezing(db):
    """★D-NAO-251 §4-① 본체 — 기각된 시그니처도 증거가 계속 쌓인다.

    구판의 병: 판사가 *"45회 관찰이 단 이틀 안에 집중되어… 승격을 보류합니다"* 로 기각하면
    그 순간부터 tally 갱신까지 막혀, 그 뒤 일주일에 818건이 더 쌓였어도 판사는 다시 못 봤다.
    「재현성 불명」이라 기각해 놓고 재현을 관측할 길을 코드가 닫는 자기충족 함정이었다.
    """
    _diary(db, outcome=_good())
    wisdom_candidates.harvest_candidates(db, now=NOW)
    cand = db.query(OpsWisdomCandidate).one()
    cand.status = "rejected"
    cand.judged_occurrences = cand.occurrences  # 판정 시점 기준선(마이그레이션이 하는 일)
    db.commit()

    _diary(db, action_date=date(2026, 7, 14), outcome=_good())  # 같은 시그니처 재등장
    res = wisdom_candidates.harvest_candidates(db, now=NOW)

    db.refresh(cand)
    assert cand.occurrences == 2 and cand.good_count == 2  # ★얼지 않는다
    assert res["rejected_tally_resumed"] == 1              # 그리고 그 사실이 값으로 보인다
    assert res["skipped_terminal"] == 0                    # promoted가 아니므로 여기 안 잡힌다
    assert cand.status == "rejected"                       # 문턱(2배∧+5) 미도달이라 아직 안 열림


def test_harvest_reopens_rejected_when_evidence_doubles(db):
    """★D-NAO-251 §4-① — 증거가 판정 시점의 2배(∧+5)에 닿으면 pending으로 «복귀»한다.

    코드가 판정을 뒤집는 게 아니라 **같은 판사에게 다시 묻는 것**까지다(판정기 증식 금지,
    북극성 §6-b M5). 재심 횟수는 여기서 안 올린다 — 판사가 실제로 다시 판정했을 때 올린다
    (문을 연 것과 실제로 재심한 것은 다르다).
    """
    _diary(db, outcome=_good())
    wisdom_candidates.harvest_candidates(db, now=NOW)
    cand = db.query(OpsWisdomCandidate).one()
    cand.status = "rejected"
    cand.judged_occurrences = 1
    db.commit()

    # 2배(=2)는 넘지만 +5를 못 넘는 구간에서는 열리지 않는다 — 잔챙이 재심 차단.
    for i, d in enumerate([date(2026, 7, 13), date(2026, 7, 14), date(2026, 7, 15)]):
        _diary(db, action_date=d, outcome=_good())
    wisdom_candidates.harvest_candidates(db, now=NOW)
    db.refresh(cand)
    assert cand.occurrences == 4 and cand.status == "rejected"
    assert wisdom_candidates._reopen_ready(cand) is False

    # +5까지 채우면 열린다.
    for d in [date(2026, 7, 16), date(2026, 7, 17)]:
        _diary(db, action_date=d, outcome=_good())
    res = wisdom_candidates.harvest_candidates(db, now=NOW)
    db.refresh(cand)
    assert cand.occurrences == 6 and cand.status == "pending"  # 6 ≥ 1×2 ∧ 6 ≥ 1+5
    assert res["reopened"] == 1
    assert (cand.rejudge_count or 0) == 0  # 문을 연 것뿐 — 재심 카운트는 판사가 올린다


def test_harvest_no_reopen_without_baseline(db):
    """기준선(judged_occurrences)이 없으면 재개방하지 않는다 — fail-closed.

    「어디서부터 2배인지」를 모르는 채 여는 것은 문턱이 없는 것과 같다. 마이그레이션이 기존
    판정분에 현재 occurrences를 기준선으로 백필하므로 이 경로는 정상 운영에선 안 나온다.
    """
    c = _cand(db, signature="nobase", occurrences=99, status="rejected")
    c.judged_occurrences = None
    db.commit()
    assert wisdom_candidates._reopen_ready(c) is False


def test_harvest_rejudge_exhausted_returns_to_terminal(db):
    """재심 상한(_MAX_REJUDGE=2)을 소진하면 다시 완전 terminal — tally도 멈춘다(행 비대 방지)."""
    _diary(db, outcome=_good())
    wisdom_candidates.harvest_candidates(db, now=NOW)
    cand = db.query(OpsWisdomCandidate).one()
    cand.status = "rejected"
    cand.judged_occurrences = 1
    cand.rejudge_count = wisdom_candidates._MAX_REJUDGE
    db.commit()

    _diary(db, action_date=date(2026, 7, 14), outcome=_good())
    res = wisdom_candidates.harvest_candidates(db, now=NOW)
    db.refresh(cand)
    assert cand.occurrences == 1  # 얼었다
    # ★skip 카운터는 «일기 행» 단위라 재스캔된 옛 행도 함께 센다(기존 skipped_terminal과 같은
    #   관례 — 그 테스트도 >=1로 잠근다). 반대로 tally 카운터는 dedup «뒤»에 있어 중복이
    #   안 잡힌다. 두 카운터의 분모가 다르다는 것을 여기서 잠근다.
    assert res["skipped_rejudge_exhausted"] >= 1
    assert res["rejected_tally_resumed"] == 0


def test_harvest_skips_diary_rows_without_action(db):
    """★D-NAO-251 §4-② ⓑ — action 없는 일기 행으로는 후보를 만들지 않고, 그 사실을 센다.

    action은 패턴의 «의미 축» 그 자체라 미상인 채 후보가 되면 판사가 형제를 못 찾고
    (`_sibling_buckets`가 action으로 매칭한다) 그 후보는 대조군 없이 판정만 기다린다.
    prod 실재 사례: 후보 45번 `g|SHOPPING|None|weekday|summer|normal|`.
    """
    _diary(db, outcome=_good(), action=None)
    res = wisdom_candidates.harvest_candidates(db, now=NOW)
    assert res["skipped_no_action"] == 1
    assert db.query(OpsWisdomCandidate).count() == 0  # 후보가 «안 생긴다»


def test_judge_excludes_action_less_candidates_from_queue(db):
    """★D-NAO-251 §4-② ⓓ — action 미상 후보는 판사 대기열에서 빠지고 카운터로 남는다.

    수확층(ⓑ)이 더는 이런 후보를 안 만들고 마이그레이션이 기존분을 hidden 처분하므로,
    이 필터는 그 둘 사이의 fail-closed 안전망이다.
    """
    ok = _cand(db, signature="ok", occurrences=5)
    bad = _cand(db, signature="noact", occurrences=5)
    bad.action = None
    db.commit()

    res = wisdom_judge.judge_ripe_candidates(db, now=NOW, invoke=_promote_invoke)
    assert res["skipped_no_action"] == 1
    assert res["ripe"] == 1 and res["promoted"] == 1
    db.refresh(ok), db.refresh(bad)
    assert ok.status == "promoted"
    assert bad.status == "pending"  # 판정되지 않고 그대로 남는다(삭제·강제판정 아님)


def test_judge_records_snapshot_and_preserves_prior_verdict(db):
    """★D-NAO-251 §4-① — 판정 시점 스냅샷을 찍고, 재심 시 이전 판정문을 이력으로 보존한다.

    `judge_verdict_json`은 덮어쓰되 «형태»는 안 바꾼다(wisdom_writer.py:51·wisdom_apply.py:72가
    그 모양에 의존). 이전 판정문은 `prior_judgments_json`에 append한다(계약 §3).
    """
    c = _cand(db, signature="s1", occurrences=3)
    db.commit()

    wisdom_judge.judge_ripe_candidates(db, now=NOW, invoke=_reject_invoke)
    db.refresh(c)
    assert c.status == "rejected"
    assert c.judged_at == NOW and c.judged_occurrences == 3
    assert c.rejudge_count == 0                 # 첫 판정은 재심이 아니다
    assert c.prior_judgments_json in (None, "[]")
    first_verdict = c.judge_verdict_json

    # 증거가 쌓여 재개방됐다고 가정하고 다시 판정.
    c.status = "pending"
    c.occurrences = 9
    db.commit()
    later = NOW + timedelta(days=3)
    wisdom_judge.judge_ripe_candidates(db, now=later, invoke=_promote_invoke)
    db.refresh(c)

    assert c.status == "promoted"
    assert c.rejudge_count == 1                 # ★이번엔 재심
    assert c.judged_occurrences == 9            # 기준선이 다시 찍혔다
    prior = json.loads(c.prior_judgments_json)
    assert len(prior) == 1
    assert prior[0]["verdict_json"] == first_verdict      # ★옛 판정문이 살아 있다
    assert prior[0]["occurrences_at_judgment"] == 3
    assert json.loads(c.judge_verdict_json)["verdict"] == "promote"  # 형태 불변


def test_judge_prompt_carries_prior_judgments_on_rejudge(db):
    """★D-NAO-251 §5 ①-c — 재심 프롬프트에 이전 판정 근거와 증거 증가폭이 «재료»로 실린다.

    재료만이고 판정 강제가 아니다 — 같은 이유로 다시 기각하는 것도 유효한 결과이며, 그 지침
    문장 자체가 프롬프트에 있어야 한다(판사가 「늘었으니 승격」으로 기울지 않게).
    """
    c = _cand(db, signature="s1", occurrences=3)
    db.commit()
    wisdom_judge.judge_ripe_candidates(db, now=NOW, invoke=_reject_invoke)
    db.refresh(c)
    c.status, c.occurrences = "pending", 12
    db.commit()

    prompt = wisdom_judge._prompt(c, NOW + timedelta(days=3), db)
    assert '"prior_judgments"' in prompt
    assert '"verdict": "reject"' in prompt          # 이전 판정
    assert '"evidence_growth": "3 \\u2192 12 (\\u00d74.0)"' in prompt or "3 → 12 (×4.0)" in prompt
    assert "같은 이유로 다시 기각하는 것도 유효한 판정입니다" in prompt

    # 처음 판정되는 후보엔 None — 「이력이 비었다([])」와 「처음이다(None)」는 다르다.
    fresh = _cand(db, signature="fresh", occurrences=3)
    db.commit()
    assert wisdom_judge._prior_judgments_view(fresh) is None


def test_reopen_ready_respects_rejudge_cap_on_its_own(db):
    """★적대 리뷰 P2 — `_reopen_ready`는 «자기 혼자서도» 재심 상한을 지켜야 한다.

    harvest 안에서는 상한 검사가 위쪽 가드에 가려 이 분기가 안 밟히지만, 성적표의
    `reopen_ready` 필드는 이 함수를 **직접** 부른다. 화면이 「재개방 가능」이라고 말하는데
    실제로는 상한 소진이라 영원히 안 열리는 상태를 막는다(표면과 실체의 불일치 금지).
    """
    c = _cand(db, signature="capped", occurrences=99, status="rejected")
    c.judged_occurrences = 1          # 문턱은 넉넉히 넘는다
    c.rejudge_count = wisdom_candidates._MAX_REJUDGE
    db.commit()
    assert wisdom_candidates._reopen_ready(c) is False   # 상한이 이긴다
    c.rejudge_count = wisdom_candidates._MAX_REJUDGE - 1
    assert wisdom_candidates._reopen_ready(c) is True


def test_reopen_ready_boundary_zero_and_negative_baseline(db):
    """★적대 리뷰 지적 — 기준선이 0이거나 occurrences가 줄어든 경우의 경계.

    judged_occurrences=0이면 「0의 2배 = 0」이라 배수 조건은 자동 통과하는데, 그때 문턱을
    지키는 것은 절대 증분(+5)뿐이다 — 그 둘을 `and`로 묶은 이유가 여기 있다.
    """
    c = _cand(db, signature="zero", occurrences=4, status="rejected")
    c.judged_occurrences = 0
    db.commit()
    assert wisdom_candidates._reopen_ready(c) is False   # 4 < 0+5
    c.occurrences = 5
    assert wisdom_candidates._reopen_ready(c) is True    # 5 ≥ 0+5

    c2 = _cand(db, signature="shrunk", occurrences=2, status="rejected")
    c2.judged_occurrences = 10        # 관측이 줄어든 비정상 상태
    db.commit()
    assert wisdom_candidates._reopen_ready(c2) is False


def test_wisdom_cron_logs_harvest_and_judge_totals(db, caplog, monkeypatch):
    """★D-NAO-251 §5 ①-b 상환 — 크론 로그에 harvest·judge totals가 «키로» 남는다.

    구판은 `stage_status`(ok/failed)만 로깅해, 신규 카운터 4종이 회차마다 계산되고도
    **어디에도 영속화되지 않았다** — 완료 QA가 「카운터가 코드에 있다」와 「라이브 표면에
    관측된다」는 다르다고 판정한 자리다. 로그가 그 표면이다.
    """
    import logging
    monkeypatch.setattr(scheduler_service, "_get_own_db_session", lambda: db)
    monkeypatch.setattr(
        wisdom_loop, "run_daily_wisdom",
        lambda _db: {
            "stage_status": {"harvest": "ok", "judge": "ok"},
            "harvest": {"scanned": 4113, "new": 0, "updated": 2,
                        "rejected_tally_resumed": 7, "reopened": 1,
                        "skipped_rejudge_exhausted": 0, "skipped_no_action": 14},
            "judge": {"ripe": 15, "cap_applied": 15, "backlog_remaining": 5,
                      "rejudged": 2, "skipped_no_action": 1},
        },
    )
    with caplog.at_level(logging.INFO):
        scheduler_service.run_naver_wisdom_job()
    msg = "\n".join(r.getMessage() for r in caplog.records)
    for token in ("rejected_tally_resumed=7", "reopened=1", "rejudge_exhausted=0",
                  "no_action=14", "ripe=15", "cap=15", "backlog_remaining=5", "rejudged=2"):
        assert token in msg, f"크론 로그에 {token} 없음 — 카운터가 로그 표면에 안 닿는다"


def test_wisdom_cron_log_survives_stage_failure(db, caplog, monkeypatch):
    """단계 실패({"error": ...})여도 로깅이 잡을 안 죽인다 — fail-open 유지."""
    import logging
    monkeypatch.setattr(scheduler_service, "_get_own_db_session", lambda: db)
    monkeypatch.setattr(
        wisdom_loop, "run_daily_wisdom",
        lambda _db: {"stage_status": {"harvest": "failed"}, "harvest": {"error": "boom"}},
    )
    with caplog.at_level(logging.INFO):
        scheduler_service.run_naver_wisdom_job()  # 예외가 밖으로 안 나와야 한다
    assert "naver wisdom" in "\n".join(r.getMessage() for r in caplog.records)
