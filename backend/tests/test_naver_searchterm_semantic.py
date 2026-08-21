# test_naver_searchterm_semantic.py — M2-c 의미 단위 분절(ⓒ) + 판정(judge_semantic_units) +
#   제안 배선(ⓔ, search_term_ss_lane) 테스트 (D-NAO-191·219).
# 커버: ①segment()가 프로토타입(docs/references/data/70_ngram_grain/semantic.py seg())과 동일
#   동작(최장일치·잔여) ②build_index/segment_indexed가 segment()와 동일 결과(성능 경로 회귀 0)
#   ③build_vocab 원료 3종(화이트리스트·상품명·SHOPPING 그룹명) ④게이트 fail-closed(margin 부재)
#   ⑤unlocked_by_pooling 판정(개별판정 통과 멤버 유무) ⑥전환 보호 상속 ⑦judge_search_terms 기존
#   키 불변(회귀 잠금) + semantic_units 키 추가 ⑧ss_lane: 쇼핑 pending 생성·잠김 3(스코프)·중복·
#   길이·상한 ⑨의미단위 제안은 콘솔에서도 비실행(target_type 구조 가드).
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdgroupProduct,
    NaverCampaignSettings,
    NaverEntity,
    NaverProductBep,
    NaverProposal,
    NaverSearchTermDaily,
)
from app.services.naver_ad import search_term_judge as judge
from app.services.naver_ad import search_term_ss_lane as lane
from app.services.naver_ad import semantic_units
from app.services.naver_ad.campaign_target_resolver import NAVER_CHANNEL_ID

_NOW = datetime(2026, 8, 21, 9, 0, 0)


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


def _map_product(db, adgroup_id, cpid, name, margin="500", campaign_id="cmp1"):
    db.add(NaverProductBep(
        channel_id=NAVER_CHANNEL_ID, channel_product_id=cpid, has_cost=True,
        product_name=name, selling_price=Decimal("1000"),
        contribution_margin=Decimal(margin), cost_price=0, commission_rate=0, logistics_cost=0,
    ))
    db.add(NaverAdgroupProduct(adgroup_id=adgroup_id, mall_product_id=cpid, campaign_id=campaign_id))
    db.commit()


def _shopping_group(db, adgroup_id, name, campaign_id="cmp1"):
    db.add(NaverEntity(
        entity_type="adgroup", entity_id=adgroup_id, parent_id=campaign_id, campaign_id=campaign_id,
        campaign_type="SHOPPING", name=name, status="on",
    ))
    db.commit()


def _daily(db, adgroup_id, term, *, clk=20, cost=6000, pconv=0, source="shopping",
           campaign_id="cmp1", ad_date=date(2026, 8, 15)):
    db.add(NaverSearchTermDaily(
        ad_date=ad_date, campaign_id=campaign_id, adgroup_id=adgroup_id, search_term=term,
        source=source, imp=100, clk=clk, cost=cost, rank_sum=0,
        conv_purchase_cnt=pconv, conv_direct_cnt=0, conv_purchase_amt=0, cart_cnt=0, cart_amt=0,
    ))
    db.commit()


def _settings(db, campaign_id="cmp1", auto_operate=True):
    db.add(NaverCampaignSettings(campaign_id=campaign_id, optimizer="ours", auto_operate=auto_operate))
    db.commit()


# ══════════════════════════════════════════════════════════════════
# ① segment() — 프로토타입 seg()와 동작 동일(고정 vocab, DB 무관)
# ══════════════════════════════════════════════════════════════════
_VOCAB = ["사생활보호", "갤럭시", "폴드8", "와이드", "필름", "아이폰", "악세사리"]


def test_segment_longest_match_basic():
    units, resid = semantic_units.segment("갤럭시폴드8와이드필름", _VOCAB)
    assert units == ["갤럭시", "폴드8", "와이드", "필름"]
    assert resid == []


def test_segment_prefers_longer_match_over_shorter():
    units, resid = semantic_units.segment("사생활보호필름", _VOCAB)
    assert units == ["사생활보호", "필름"]
    assert resid == []


def test_segment_residual_for_unknown_span():
    units, resid = semantic_units.segment("아이폰악세사리골프용품", _VOCAB)
    assert units == ["아이폰", "악세사리"]
    assert resid == ["골프용품"]


def test_segment_residual_short_fragment_kept_as_is():
    # segment() 자체는 길이 필터를 하지 않는다(judge_semantic_units가 len>=2만 집계) — 그대로 반환.
    units, resid = semantic_units.segment("아이폰x", _VOCAB)
    assert units == ["아이폰"]
    assert resid == ["x"]


def test_segment_empty_term():
    assert semantic_units.segment("", _VOCAB) == ([], [])


def test_segment_no_match_returns_all_residual():
    units, resid = semantic_units.segment("전혀무관", _VOCAB)
    assert units == []
    assert resid == ["전혀무관"]


# ── ② build_index/segment_indexed(성능 경로) — segment()와 동일 결과 ──
def test_segment_indexed_matches_segment_for_same_vocab():
    index = semantic_units.build_index(_VOCAB)
    cases = ["갤럭시폴드8와이드필름", "사생활보호필름", "아이폰악세사리골프용품", "전혀무관", ""]
    for term in cases:
        assert semantic_units.segment_indexed(term, index) == semantic_units.segment(term, _VOCAB)


# ══════════════════════════════════════════════════════════════════
# ③ build_vocab — 원료 3종
# ══════════════════════════════════════════════════════════════════
def test_build_vocab_includes_whitelist_product_and_group_tokens(db):
    _map_product(db, "grp-1", "P1", name="갤럭시 폴드8 와이드")
    _shopping_group(db, "grp-2", "폴드8 와이드 그룹")
    vocab = semantic_units.build_vocab(db)
    assert "아이폰" in vocab  # 화이트리스트(len>=2, search_term_judge._SS_WHITELIST_TOKENS)
    assert "갤럭시" in vocab and "폴드8" in vocab and "와이드" in vocab  # 상품명 토큰
    assert "그룹" in vocab  # 그룹명 토큰("폴드8와이드그룹" 분해)
    assert vocab == sorted(vocab, key=len, reverse=True)  # 길이 내림차순(최장일치 전제)


def test_build_vocab_excludes_pure_numeric_and_short_tokens(db):
    _map_product(db, "grp-1", "P1", name="15 케이스 a")
    vocab = semantic_units.build_vocab(db)
    assert "15" not in vocab  # 순수숫자 토큰 제외
    assert "a" not in vocab  # len<2 제외
    assert "케이스" in vocab


def test_build_vocab_excludes_non_shopping_group_names(db):
    db.add(NaverEntity(
        entity_type="adgroup", entity_id="grp-web", parent_id="cmp2", campaign_id="cmp2",
        campaign_type="WEB_SITE", name="웹사이트전용토큰", status="on",
    ))
    db.commit()
    vocab = semantic_units.build_vocab(db)
    assert "웹사이트전용토큰" not in vocab
    assert "웹사이트전용토" not in vocab  # 토큰화돼도 안 들어간다(전체 문자열 기준 확인)


# ══════════════════════════════════════════════════════════════════
# ④~⑦ judge_semantic_units
# ══════════════════════════════════════════════════════════════════
def test_unlocked_by_pooling_true_when_no_individual_member_passes(db):
    _map_product(db, "grp-1", "P1", name="갤럭시 폴드8")
    _daily(db, "grp-1", "갤럭시폴드8세트", clk=6, cost=3000)
    _daily(db, "grp-1", "폴드8갤럭시메이트", clk=6, cost=3000)
    out = judge.judge_semantic_units(db, now=_NOW)
    units = {u["key"]: u for u in out["units"]}
    assert "갤럭시" in units
    u = units["갤럭시"]
    assert u["clk"] == 12 and u["cost"] == 6000
    assert u["member_terms"] == 2
    assert u["members_passing_individual_gate"] == 0
    assert u["unlocked_by_pooling"] is True
    assert "[검색어제외 의미단위" in u["reason"]


def test_unlocked_by_pooling_false_when_a_member_already_passes_individually(db):
    _map_product(db, "grp-1", "P1", name="갤럭시 폴드8")
    _daily(db, "grp-1", "갤럭시폴드8세트", clk=20, cost=9000)  # 개별 grain 단독으로도 clk>=10
    _daily(db, "grp-1", "폴드8갤럭시메이트", clk=6, cost=3000)
    out = judge.judge_semantic_units(db, now=_NOW)
    units = {u["key"]: u for u in out["units"]}
    u = units["갤럭시"]
    assert u["members_passing_individual_gate"] == 1
    assert u["unlocked_by_pooling"] is False


def test_semantic_fail_closed_when_margin_absent(db):
    # 사전은 다른 그룹의 상품 매핑으로 채우되(전역 사전), 검색어가 속한 grp-nomap엔 상품 매핑이
    # 없어 margin=None → 풀링 게이트를 충족해도 제외한다(자르지 않는다, fail-closed).
    _map_product(db, "grp-vocab-only", "P1", name="갤럭시 폴드8")
    _daily(db, "grp-nomap", "갤럭시폴드8세트", clk=6, cost=9000)
    _daily(db, "grp-nomap", "폴드8갤럭시메이트", clk=6, cost=9000)
    out = judge.judge_semantic_units(db, now=_NOW)
    assert out["units"] == []
    assert out["pairs"] == []
    assert out["residual"] == []


def test_semantic_excludes_rows_with_live_conversion(db):
    _map_product(db, "grp-1", "P1", name="갤럭시 폴드8")
    _daily(db, "grp-1", "갤럭시폴드8세트", clk=20, cost=9000, pconv=1)  # 전환 있음 → 집계 제외
    out = judge.judge_semantic_units(db, now=_NOW)
    assert out["units"] == [] and out["pairs"] == [] and out["residual"] == []


def test_semantic_residual_captures_off_dictionary_span(db):
    _map_product(db, "grp-1", "P1", name="갤럭시")
    _daily(db, "grp-1", "갤럭시골프용품", clk=20, cost=9000)  # "골프용품" 사전 밖 → 잔여
    out = judge.judge_semantic_units(db, now=_NOW)
    resid_keys = {r["key"] for r in out["residual"]}
    assert "골프용품" in resid_keys
    unit_keys = {u["key"] for u in out["units"]}
    assert "골프용품" not in unit_keys  # 잔여는 단일단위 목록과 섞이지 않는다


def test_semantic_sample_gate_low_click_excluded(db):
    _map_product(db, "grp-1", "P1", name="갤럭시")
    _daily(db, "grp-1", "갤럭시단독", clk=5, cost=9000)  # clk<10
    out = judge.judge_semantic_units(db, now=_NOW)
    assert out["units"] == []


# ── ⑦ judge_search_terms 기존 키 불변(회귀 잠금) + semantic_units 키 추가 ──
def test_judge_search_terms_existing_keys_unchanged_and_semantic_key_added(db):
    _map_product(db, "grp-1", "P1", name="무관템")
    _daily(db, "grp-1", "손실검색어", clk=20, cost=9000)
    out = judge.judge_search_terms(db, now=_NOW)
    assert set(out.keys()) == {
        "window", "exclude_candidates", "agency_powerlink", "promote_candidates", "semantic_units",
    }
    assert len(out["exclude_candidates"]["shopping"]) == 1
    assert out["exclude_candidates"]["shopping"][0]["search_term"] == "손실검색어"
    assert out["promote_candidates"] == []
    assert out["agency_powerlink"] == []
    sem = out["semantic_units"]
    assert set(["window", "vocab_size", "units", "pairs", "residual"]) <= set(sem.keys())


# ══════════════════════════════════════════════════════════════════
# ⑧~⑨ ss_lane — 쇼핑 pending 생성·잠김 3(스코프)·중복·길이·상한·의미단위 비실행
# ══════════════════════════════════════════════════════════════════
def test_lane_creates_pending_shopping_and_semantic_proposals_when_in_scope(db):
    _settings(db, auto_operate=True)
    _map_product(db, "grp-1", "P1", name="갤럭시 폴드8")
    _daily(db, "grp-1", "손실검색어", clk=20, cost=9000)  # 개별 grain 후보
    _daily(db, "grp-1", "갤럭시폴드8세트", clk=6, cost=6000)
    _daily(db, "grp-1", "폴드8갤럭시메이트", clk=6, cost=6000)  # 풀링으로 unlock되는 의미단위 후보
    res = lane.run_search_term_ss_lane(db, now=_NOW)
    assert res["shopping_proposals_created"] == 1
    assert res["shopping_out_of_scope"] == 0
    assert res["semantic_proposals_created"] >= 1
    props = db.query(NaverProposal).filter(
        NaverProposal.proposal_type == judge.SEARCH_TERM_EXCLUDE_TYPE,
    ).all()
    assert len(props) == res["shopping_proposals_created"] + res["semantic_proposals_created"]
    assert all(p.status == "pending" for p in props)
    assert all(p.approval_source is None for p in props)  # 잠김 2: 자동 승인원 미배선
    target_types = {p.target_type for p in props}
    assert "search_term" in target_types
    assert judge.SEARCH_TERM_EXCLUDE_SEMANTIC_TARGET_TYPE in target_types


def test_lane_shopping_out_of_scope_agency_creates_no_proposal(db):
    # settings 없음 = auto_operate 미등록(대행사 취급) — 잠김 3(optimizer 가드) 유지, 브리핑은 됨.
    _map_product(db, "grp-1", "P1", name="무관템")
    _daily(db, "grp-1", "손실검색어", clk=20, cost=9000)
    res = lane.run_search_term_ss_lane(db, now=_NOW)
    assert res["shopping_proposals_created"] == 0
    assert res["shopping_out_of_scope"] == 1
    assert db.query(NaverProposal).filter(
        NaverProposal.proposal_type == judge.SEARCH_TERM_EXCLUDE_TYPE,
    ).count() == 0


def test_semantic_proposal_not_executable_via_console(db):
    _settings(db, auto_operate=True)
    _map_product(db, "grp-1", "P1", name="갤럭시 폴드8")
    _daily(db, "grp-1", "갤럭시폴드8세트", clk=6, cost=6000)
    _daily(db, "grp-1", "폴드8갤럭시메이트", clk=6, cost=6000)
    lane.run_search_term_ss_lane(db, now=_NOW)
    p = db.query(NaverProposal).filter(
        NaverProposal.target_type == judge.SEARCH_TERM_EXCLUDE_SEMANTIC_TARGET_TYPE,
    ).first()
    assert p is not None
    from app.services.naver_ad import naver_execution_harness as harness
    assert harness.real_write_blocker(p) is not None  # 구조 가드가 콘솔에서도 비실행으로 막는다


def test_lane_shopping_dedup_skips_existing_pending(db):
    _settings(db, auto_operate=True)
    _map_product(db, "grp-1", "P1", name="무관템")
    _daily(db, "grp-1", "손실검색어", clk=20, cost=9000)
    db.add(NaverProposal(
        proposal_type=judge.SEARCH_TERM_EXCLUDE_TYPE, target_type="search_term",
        target_id="손실검색어", campaign_id="cmp1", adgroup_id="grp-1", status="pending",
    ))
    db.commit()
    res = lane.run_search_term_ss_lane(db, now=_NOW)
    assert res["shopping_proposals_created"] == 0
    assert res["shopping_deduped"] == 1


def test_lane_shopping_skips_too_long_term(db):
    _settings(db, auto_operate=True)
    _map_product(db, "grp-1", "P1", name="무관템")
    long_term = "손" * (lane._TARGET_ID_MAXLEN + 1)
    _daily(db, "grp-1", long_term, clk=20, cost=9000)
    res = lane.run_search_term_ss_lane(db, now=_NOW)
    assert res["shopping_proposals_created"] == 0
    assert res["shopping_skipped_too_long"] == 1


def test_lane_shopping_cap_limits_new_creation(db):
    _settings(db, auto_operate=True)
    _map_product(db, "grp-1", "P1", name="무관템")
    for i in range(lane._SS_SHOPPING_EXCLUDE_CAP + 5):
        _daily(db, "grp-1", f"손실검색어{i}", clk=20, cost=9000 + i)
    res = lane.run_search_term_ss_lane(db, now=_NOW)
    assert res["shopping_proposals_created"] == lane._SS_SHOPPING_EXCLUDE_CAP
    assert res["shopping_over_cap"] == 5


def test_lane_shopping_briefing_diary_still_written(db):
    # ⓔ 지시: 기존 브리핑 diary는 유지(제거 금지) — pending 제안 생성과 별개로 계속 남는다.
    from app.models import OpsDiaryEntry
    _settings(db, auto_operate=True)
    _map_product(db, "grp-1", "P1", name="무관템")
    _daily(db, "grp-1", "손실검색어", clk=20, cost=9000)
    lane.run_search_term_ss_lane(db, now=_NOW)
    briefs = db.query(OpsDiaryEntry).filter(OpsDiaryEntry.event_type == "observe").all()
    assert any("검색어제외 브리핑" in b.rationale for b in briefs)
