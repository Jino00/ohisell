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
# ★P1-1 수정 후: 상품명에서 파생된 vocab 단어는 화이트리스트에도 그대로 들어간다(둘 다 같은
# NaverProductBep(has_cost=True).product_name 소스라서 — 아래 test_whitelist_blocks_semantic_
# unit_pooling_leak 참조). 그래서 「풀링으로 unlock되는데 화이트리스트엔 안 걸리는」 시나리오를
# 만들려면 vocab 단어를 **상품명이 아니라 SHOPPING 그룹명으로만** 공급해야 한다(margin 산정용
# 상품은 그와 무관한 중립 이름을 쓴다) — build_vocab은 그룹명도 사전에 태우지만 _build_whitelist는
# 그룹명을 보지 않으므로, 그룹명 전용 단어는 vocab엔 있고 화이트리스트엔 없다(의도된 비대칭).
def _neutral_margin_product(db, adgroup_id, cpid, margin="500"):
    _map_product(db, adgroup_id, cpid, name="무관상품", margin=margin)


def test_unlocked_by_pooling_true_when_no_individual_member_passes(db):
    _neutral_margin_product(db, "grp-1", "P1")
    _shopping_group(db, "grp-vocab", "갤럭시 폴드8")  # vocab만 공급(화이트리스트엔 안 들어감)
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
    _neutral_margin_product(db, "grp-1", "P1")
    _shopping_group(db, "grp-vocab", "갤럭시 폴드8")
    _daily(db, "grp-1", "갤럭시폴드8세트", clk=20, cost=9000)  # 개별 grain 단독으로도 clk>=10
    _daily(db, "grp-1", "폴드8갤럭시메이트", clk=6, cost=3000)
    out = judge.judge_semantic_units(db, now=_NOW)
    units = {u["key"]: u for u in out["units"]}
    u = units["갤럭시"]
    assert u["members_passing_individual_gate"] == 1
    assert u["unlocked_by_pooling"] is False


def test_semantic_fail_closed_when_margin_absent(db):
    # vocab만 다른 그룹명으로 전역 채우고(그룹명은 화이트리스트를 안 타므로 이 테스트가 순수하게
    # margin 부재만 검증한다), 검색어가 속한 grp-nomap엔 상품 매핑이 없어 margin=None →
    # 풀링 게이트를 충족해도 제외한다(자르지 않는다, fail-closed).
    _shopping_group(db, "grp-vocab", "갤럭시 폴드8")
    _daily(db, "grp-nomap", "갤럭시폴드8세트", clk=6, cost=9000)
    _daily(db, "grp-nomap", "폴드8갤럭시메이트", clk=6, cost=9000)
    out = judge.judge_semantic_units(db, now=_NOW)
    assert out["units"] == []
    assert out["pairs"] == []
    assert out["residual"] == []


def test_semantic_excludes_rows_with_live_conversion(db):
    _neutral_margin_product(db, "grp-1", "P1")
    _shopping_group(db, "grp-vocab", "갤럭시 폴드8")
    _daily(db, "grp-1", "갤럭시폴드8세트", clk=20, cost=9000, pconv=1)  # 전환 있음 → 집계 제외
    out = judge.judge_semantic_units(db, now=_NOW)
    assert out["units"] == [] and out["pairs"] == [] and out["residual"] == []


# ══════════════════════════════════════════════════════════════════
# 적대 리뷰 1R P1-1 — 화이트리스트가 의미 단위 경로에서 무력화되던 결함(수정 확인)
# ══════════════════════════════════════════════════════════════════
def test_whitelist_blocks_semantic_unit_pooling_leak(db):
    """리뷰어 재현 그대로: 상품 핵심어(하드코딩 화이트리스트 토큰 "아이폰")를 포함한 검색어
    11건이 개별 grain에선 전부 화이트리스트로 보호돼 후보가 0건인데, 그 11건을 의미 단위
    "아이폰" 하나로 풀링하면 clk 합산이 게이트를 넘는다. 수정 전엔 이게 그대로 새서 "아이폰"을
    제외하라는 제안이 나왔다(오컷 방지 정면 위반) — 수정 후엔 화이트리스트가 unit/pair에도
    걸려 0건이어야 한다."""
    _map_product(db, "grp-1", "P1", name="무관상품")  # margin만, "아이폰"과 무관한 이름
    for i in range(11):
        _daily(db, "grp-1", f"아이폰{i}케이스", clk=5, cost=1000)  # 개별로는 clk<10(보호 이전에도 게이트 미달)
    out_individual = judge.judge_search_terms(db, now=_NOW)
    assert out_individual["exclude_candidates"]["shopping"] == []  # 개별 grain: 화이트리스트+게이트 이중 보호

    out = judge.judge_semantic_units(db, now=_NOW)
    unit_keys = {u["key"] for u in out["units"]}
    assert "아이폰" not in unit_keys  # ★수정 확인: 풀링으로도 "아이폰"이 새지 않는다
    pair_keys = {p["key"] for p in out["pairs"]}
    assert not any("아이폰" in k for k in pair_keys)


def test_whitelist_blocks_semantic_pair_containing_whitelisted_unit(db):
    # 쌍(phrase)도 구성 단위 중 하나가 화이트리스트 토큰이면 막힌다(phrase 문자열에 부분문자열로
    # 포함되므로 _is_whitelisted가 그대로 잡는다).
    _map_product(db, "grp-1", "P1", name="무관상품")
    _shopping_group(db, "grp-vocab", "케이스")  # "케이스"는 화이트리스트가 아닌 순수 vocab 단어
    for i in range(6):
        _daily(db, "grp-1", "아이폰케이스", clk=6, cost=2000, ad_date=date(2026, 8, 10 + i))
    out = judge.judge_semantic_units(db, now=_NOW)
    assert out["pairs"] == []  # "아이폰+케이스" phrase="아이폰 케이스" → 화이트리스트에 걸림


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
    _neutral_margin_product(db, "grp-1", "P1")
    _shopping_group(db, "grp-vocab", "갤럭시 폴드8")  # vocab만(화이트리스트엔 안 들어감)
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
    _neutral_margin_product(db, "grp-1", "P1")
    _shopping_group(db, "grp-vocab", "갤럭시 폴드8")
    _daily(db, "grp-1", "갤럭시폴드8세트", clk=6, cost=6000)
    _daily(db, "grp-1", "폴드8갤럭시메이트", clk=6, cost=6000)
    lane.run_search_term_ss_lane(db, now=_NOW)
    p = db.query(NaverProposal).filter(
        NaverProposal.target_type == judge.SEARCH_TERM_EXCLUDE_SEMANTIC_TARGET_TYPE,
    ).first()
    assert p is not None
    from app.services.naver_ad import naver_execution_harness as harness
    assert harness.real_write_blocker(p) is not None  # 구조 가드가 콘솔에서도 비실행으로 막는다


# ══════════════════════════════════════════════════════════════════
# 라이브 실측 수정(2026-08-21) — 의미 단위 경로는 auto_operate 스코프를 안 건다(합격기준 ⑤ 회귀 잠금)
# ══════════════════════════════════════════════════════════════════
def test_semantic_proposals_survive_when_auto_operate_is_off_but_stay_blocked_at_execution(db):
    """★라이브 실측(2026-08-21)이 드러낸 결함의 회귀 잠금 — PAO 전면 정지(D-NAO-132)로
    naver_campaign_settings 전체가 auto_operate=0인 게 지금 실제 상태다. 수정 전엔 의미 단위
    경로도 개별 grain과 같은 `_in_scope`(auto_operate)를 타서 이 상태에서 semantic 제안이
    **영원히 0건**이었다(판정층 전체가 무소비 부품이 됨). 수정 후: ①auto_operate=0이어도 의미
    단위 pending 제안은 생성된다 ②status='pending' ③approval_source is None(잠김 2 유지)
    ④harness 실행 경로(target_type 구조 가드)에서는 여전히 차단된다 — 그 차단이 계약
    (docs/PLAN_naver-m2-l2-wiring.md §4-4)이 요구하는 «잠김 3이 실행 경로에서 막는 라이브
    증거»고, 여기선 잠김 3 대신 harness 구조 가드가 같은 역할을 한다."""
    _settings(db, auto_operate=False)  # 라이브와 동일한 상태(PAO 전면 정지, 등록은 돼 있음)
    _neutral_margin_product(db, "grp-1", "P1")
    _shopping_group(db, "grp-vocab", "갤럭시 폴드8")
    _daily(db, "grp-1", "갤럭시폴드8세트", clk=6, cost=6000)
    _daily(db, "grp-1", "폴드8갤럭시메이트", clk=6, cost=6000)
    res = lane.run_search_term_ss_lane(db, now=_NOW)
    assert res["semantic_proposals_created"] >= 1  # ★수정 전엔 0(전부 out_of_scope로 죽었다)
    assert res["semantic_invalid_adgroup"] == 0
    p = db.query(NaverProposal).filter(
        NaverProposal.target_type == judge.SEARCH_TERM_EXCLUDE_SEMANTIC_TARGET_TYPE,
    ).first()
    assert p is not None
    assert p.status == "pending"
    assert p.approval_source is None  # 잠김 2 유지
    from app.services.naver_ad import naver_execution_harness as harness
    assert harness.real_write_blocker(p) is not None  # ★실행 경로에선 여전히 차단(잠김 3의 대체)


def test_shopping_individual_grain_still_gated_by_auto_operate_after_fix(db):
    # ★회귀 확인: 이번 수정은 의미 단위 경로 «전용»이다 — 개별 grain은 auto_operate=0이면
    # 여전히 0건(기존 test_lane_shopping_out_of_scope_agency_creates_no_proposal과 같은 성질,
    # 여기선 auto_operate=0 «등록됨» 케이스로 한 번 더 확인 — 위 세미틱 테스트와 대칭).
    _settings(db, auto_operate=False)
    _neutral_margin_product(db, "grp-1", "P1")
    _daily(db, "grp-1", "손실검색어", clk=20, cost=9000)
    res = lane.run_search_term_ss_lane(db, now=_NOW)
    assert res["shopping_proposals_created"] == 0
    assert res["shopping_out_of_scope"] == 1
    assert db.query(NaverProposal).filter(
        NaverProposal.target_type == "search_term",
        NaverProposal.target_id == "손실검색어",
    ).count() == 0


def test_lane_semantic_proposal_zero_when_only_whitelisted_unit_pools(db):
    # ⓔ 경로 통합 확인: 화이트리스트 토큰만 pooling되는 상황에선 semantic 제안이 0건이어야 한다
    # (judge_semantic_units 단위 테스트 test_whitelist_blocks_semantic_unit_pooling_leak의
    # ss_lane 경유 재확인 — 제안 생성 직전까지 화이트리스트가 살아있는지 본다).
    _settings(db, auto_operate=True)
    _map_product(db, "grp-1", "P1", name="무관상품")
    for i in range(11):
        _daily(db, "grp-1", f"아이폰{i}케이스", clk=5, cost=1000)
    res = lane.run_search_term_ss_lane(db, now=_NOW)
    assert res["semantic_candidates"] == 0
    assert res["semantic_proposals_created"] == 0
    assert db.query(NaverProposal).filter(
        NaverProposal.target_type == judge.SEARCH_TERM_EXCLUDE_SEMANTIC_TARGET_TYPE,
    ).count() == 0


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


# ══════════════════════════════════════════════════════════════════
# 적대 리뷰 1R P1-2 — dedup 키가 target_type을 안 봐서 실행 가능한 제안이 영구히 막히던 결함
# ══════════════════════════════════════════════════════════════════
def test_lane_dedup_does_not_cross_target_types(db):
    """의미 단위 pending(target_type=SEARCH_TERM_EXCLUDE_SEMANTIC_TARGET_TYPE, 영구 비실행)이
    이미 있어도, 텍스트가 같은 개별 grain 후보(target_type='search_term', 실행 가능)는 별도로
    생성돼야 한다 — 둘은 신뢰도가 다른 별개 트랙이다. 재현: Day1에 풀링으로 「블루투스」 semantic
    pending 생성 → Day2에 실제 검색어 "블루투스"가 개별 grain 게이트를 통과 → 수정 전엔
    target_id만 보고 dedup돼 shopping 제안이 끝내 생성되지 않았다."""
    _settings(db, auto_operate=True)
    _map_product(db, "grp-1", "P1", name="무관템")
    db.add(NaverProposal(
        proposal_type=judge.SEARCH_TERM_EXCLUDE_TYPE,
        target_type=judge.SEARCH_TERM_EXCLUDE_SEMANTIC_TARGET_TYPE,
        target_id="블루투스", campaign_id="cmp1", adgroup_id="grp-1", status="pending",
    ))
    db.commit()
    _daily(db, "grp-1", "블루투스", clk=15, cost=9000)
    res = lane.run_search_term_ss_lane(db, now=_NOW)
    assert res["shopping_proposals_created"] == 1  # ★수정 전엔 0(잘못된 교차 dedup)
    assert res["shopping_deduped"] == 0
    executable = db.query(NaverProposal).filter(
        NaverProposal.target_type == "search_term", NaverProposal.target_id == "블루투스",
    ).all()
    assert len(executable) == 1
    assert executable[0].status == "pending"


def test_lane_dedup_still_blocks_true_duplicate_within_same_target_type(db):
    # 반대 방향 회귀: target_type을 필터에 추가했다고 «같은 트랙 안»의 진짜 중복까지 새지 않는다.
    _settings(db, auto_operate=True)
    _map_product(db, "grp-1", "P1", name="무관템")
    db.add(NaverProposal(
        proposal_type=judge.SEARCH_TERM_EXCLUDE_TYPE, target_type="search_term",
        target_id="블루투스", campaign_id="cmp1", adgroup_id="grp-1", status="pending",
    ))
    db.commit()
    _daily(db, "grp-1", "블루투스", clk=15, cost=9000)
    res = lane.run_search_term_ss_lane(db, now=_NOW)
    assert res["shopping_proposals_created"] == 0
    assert res["shopping_deduped"] == 1


# ══════════════════════════════════════════════════════════════════
# 적대 리뷰 1R P2 채택 — 생존 변이를 죽이는 테스트
# ══════════════════════════════════════════════════════════════════
def test_pair_repeated_within_single_term_counts_once_not_doubled(db):
    """변이3: `_bump`의 per-term dedup 가드(`if term in bucket[...]: return`)를 무력화하면, 한
    검색어 안에서 같은 인접쌍이 반복될 때(「필름필름필름」→ 인접쌍 "필름+필름"이 zip에서 2번
    나온다) clk/cost가 그 검색어 분만큼 중복 가산된다 — unit/residual은 호출 전 set()으로 이미
    걸러지므로 이 가드가 실제로 의미를 갖는 자리는 pair뿐이다."""
    _map_product(db, "grp-1", "P1", name="무관상품")
    _shopping_group(db, "grp-vocab", "필름")
    _daily(db, "grp-1", "필름필름필름", clk=12, cost=6000)
    out = judge.judge_semantic_units(db, now=_NOW)
    pairs = {p["key"]: p for p in out["pairs"]}
    assert "필름+필름" in pairs
    p = pairs["필름+필름"]
    assert p["clk"] == 12 and p["cost"] == 6000  # 가드 없으면 24/12,000으로 2배 가산됐을 것
    assert p["member_terms"] == 1


def test_lane_semantic_cap_boundary_exact(db):
    """변이7: semantic 상한 비교(`semantic_created >= _SS_SEMANTIC_EXCLUDE_CAP`)가 `>`로
    바뀌는 off-by-one을 잡는다. cap+1건의 서로 다른 의미 단위 후보를 주고 정확히 cap건만
    생성되는지 확인한다(`_SS_SHOPPING_EXCLUDE_CAP`엔 대응 테스트가 이미 있다 —
    test_lane_shopping_cap_limits_new_creation, 같은 모양)."""
    _settings(db, auto_operate=True)
    _map_product(db, "grp-1", "P1", name="무관상품")
    n = lane._SS_SEMANTIC_EXCLUDE_CAP + 1
    _shopping_group(db, "grp-vocab", " ".join(f"단어{i}" for i in range(n)))
    for i in range(n):
        _daily(db, "grp-1", f"단어{i}단독", clk=20, cost=9000)
    res = lane.run_search_term_ss_lane(db, now=_NOW)
    assert res["semantic_candidates"] == n
    assert res["semantic_proposals_created"] == lane._SS_SEMANTIC_EXCLUDE_CAP
    assert res["semantic_over_cap"] == 1
