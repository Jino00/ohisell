# test_naver_searchterm_ss4.py — SS4 승격 제안(전환 검색어 → 정식 키워드 등록 제안) 테스트
# 커버(PLAN §검증 4·작업 지시): ①승격 제안 생성(rationale 근거 수치: 전환수·매출·검색어·출처
#   그룹) ②dedup(동일 adgroup+검색어) ③target_id 길이 초과 skip ④자동발사 불가(delegation
#   교집합 제외·일 레인 제외) ⑤미지원 실행 거부(fail-closed — 실행 매핑 자체가 없음)
#   ⑥drift 가드(ALL_PROPOSAL_TYPES·INFORMATIONAL_PROPOSAL_TYPES 등록).
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
    NaverProductBep,
    NaverProposal,
    NaverSearchTermDaily,
    OpsDiaryEntry,
)
from app.services.naver_ad import auto_operator, delegation_gate
from app.services.naver_ad import naver_execution_harness as harness
from app.services.naver_ad import search_term_judge as judge
from app.services.naver_ad import search_term_ss_lane as lane
from app.services.naver_ad.campaign_target_resolver import NAVER_CHANNEL_ID

_NOW = datetime(2026, 7, 22, 9, 0, 0)
_TYPE = judge.SEARCH_TERM_PROMOTE_TYPE


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


def _settings(db, campaign_id="cmp1", optimizer="ours", auto_operate=False):
    db.add(NaverCampaignSettings(campaign_id=campaign_id, optimizer=optimizer, auto_operate=auto_operate))
    db.commit()


def _map_product(db, adgroup_id="grp-1", cpid="P1", margin="500"):
    db.add(NaverProductBep(
        channel_id=NAVER_CHANNEL_ID, channel_product_id=cpid, has_cost=True, product_name="무관템",
        selling_price=Decimal("1000"), contribution_margin=Decimal(margin), cost_price=0,
        commission_rate=0, logistics_cost=0,
    ))
    db.add(NaverAdgroupProduct(adgroup_id=adgroup_id, mall_product_id=cpid, campaign_id="cmp1"))
    db.commit()


def _term(db, *, term, source="shopping", adgroup_id="grp-1", clk=30, cost=5000,
          pconv=2, dconv=2, pamt=60000, ad_date=date(2026, 7, 20)):
    db.add(NaverSearchTermDaily(
        ad_date=ad_date, campaign_id="cmp1", adgroup_id=adgroup_id, search_term=term, source=source,
        imp=100, clk=clk, cost=cost, rank_sum=0,
        conv_purchase_cnt=pconv, conv_direct_cnt=dconv, conv_purchase_amt=pamt, cart_cnt=0, cart_amt=0,
    ))
    db.commit()


def _proposal(db, *, adgroup_id="grp-1", term="좋은검색어", status="pending", approval_source=None):
    p = NaverProposal(
        proposal_type=_TYPE, target_type="search_term", target_id=term,
        campaign_id="cmp1", adgroup_id=adgroup_id, rationale="[검색어승격] 근거",
        expected_effect="효과", status=status, approval_source=approval_source,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


# ── ① 승격 제안 생성 — rationale에 근거 수치(전환수·매출·검색어·출처 그룹) 포함 ──
def test_promote_proposal_created_with_evidence_numbers(db):
    _map_product(db)
    _term(db, term="좋은검색어", clk=30, cost=5000, pconv=2, dconv=2, pamt=60000)
    res = lane.run_search_term_ss_lane(db, now=_NOW)
    assert res["promote_candidates"] == 1
    assert res["promote_proposals_created"] == 1
    assert res["promote_deduped"] == 0
    props = db.query(NaverProposal).filter(NaverProposal.proposal_type == _TYPE).all()
    assert len(props) == 1
    p = props[0]
    assert p.target_id == "좋은검색어"
    assert p.target_type == "search_term"
    assert p.adgroup_id == "grp-1"
    assert p.campaign_id == "cmp1"
    assert p.status == "pending"
    assert p.approval_source is None  # 영구 Confirm — 자동 승인원 절대 배선 금지
    # 근거 수치 전부 rationale에 노출(콘솔에서 숫자만 보고도 판단 가능해야 함)
    assert "좋은검색어" in p.rationale
    assert "grp-1" in p.rationale
    assert "shopping" in p.rationale
    assert "직접전환=2건" in p.rationale
    assert "60000원" in p.rationale
    assert "clk=30" in p.rationale
    assert "cost=5000원" in p.rationale
    # 실행 손 미구현 사실을 정직하게 명시(자동 발사 없음)
    assert "L3" in p.expected_effect or "수동" in p.expected_effect


# ── 소스 무관(쇼핑·파워링크 모두) 전환 검색어가 승격 후보로 산출 ──
def test_promote_proposal_created_for_both_sources(db):
    _map_product(db, adgroup_id="grp-shop", cpid="PS")
    _map_product(db, adgroup_id="grp-web", cpid="PW")
    _term(db, term="쇼핑전환검색어", source="shopping", adgroup_id="grp-shop")
    _term(db, term="파워전환검색어", source="expkeyword", adgroup_id="grp-web")
    res = lane.run_search_term_ss_lane(db, now=_NOW)
    assert res["promote_proposals_created"] == 2
    terms = {p.target_id for p in db.query(NaverProposal).filter(NaverProposal.proposal_type == _TYPE).all()}
    assert terms == {"쇼핑전환검색어", "파워전환검색어"}


# ── ② dedup — 동일 (adgroup, search_term) pending 제안이 이미 있으면 재생성 금지 ──
def test_promote_dedup_skips_existing_pending(db):
    _map_product(db)
    _term(db, term="좋은검색어")
    _proposal(db, adgroup_id="grp-1", term="좋은검색어", status="pending")
    res = lane.run_search_term_ss_lane(db, now=_NOW)
    assert res["promote_proposals_created"] == 0
    assert res["promote_deduped"] == 1
    assert db.query(NaverProposal).filter(NaverProposal.proposal_type == _TYPE).count() == 1


def test_promote_dedup_skips_already_executed(db):
    _map_product(db)
    _term(db, term="좋은검색어")
    executed = _proposal(db, adgroup_id="grp-1", term="좋은검색어", status="approved")
    executed.executed_change_log_id = 999
    db.commit()
    res = lane.run_search_term_ss_lane(db, now=_NOW)
    assert res["promote_proposals_created"] == 0
    assert res["promote_deduped"] == 1


def test_promote_dedup_allows_rejected_reregeneration(db):
    # rejected/expired/failed는 재생성 허용(in-out 롤링 큐레이션, §0 2 — 제외 레인과 동일 관례).
    _map_product(db)
    _term(db, term="좋은검색어")
    _proposal(db, adgroup_id="grp-1", term="좋은검색어", status="rejected")
    res = lane.run_search_term_ss_lane(db, now=_NOW)
    assert res["promote_proposals_created"] == 1
    assert res["promote_deduped"] == 0


# ── ③ target_id(String(50)) 초과 검색어 fail-closed skip ──
def test_promote_skips_search_term_over_target_id_maxlen(db):
    _map_product(db)
    long_term = "가" * (lane._TARGET_ID_MAXLEN + 1)  # 51자
    _term(db, term=long_term)
    res = lane.run_search_term_ss_lane(db, now=_NOW)
    assert res["promote_proposals_created"] == 0
    assert res["promote_skipped_too_long"] == 1
    assert db.query(NaverProposal).filter(NaverProposal.proposal_type == _TYPE).count() == 0
    briefs = db.query(OpsDiaryEntry).filter(OpsDiaryEntry.event_type == "observe").all()
    assert any("검색어승격 skip" in b.rationale for b in briefs)


def test_promote_creates_proposal_at_exactly_maxlen(db):
    _map_product(db)
    exact_term = "가" * lane._TARGET_ID_MAXLEN  # 50자(경계) — 초과만 skip
    _term(db, term=exact_term)
    res = lane.run_search_term_ss_lane(db, now=_NOW)
    assert res["promote_proposals_created"] == 1
    assert res["promote_skipped_too_long"] == 0


# ── ④ 자동 발사 불가 — 위임(delegation)·일 레인(auto_operator) 전부 교집합 제외 ──
def test_search_term_promote_excluded_from_delegation(db):
    # 실행 매핑 자체가 없어(_ACTION_BY_PROPOSAL_TYPE 미등록) delegable_types()가 애초에
    # 이 유형을 순회하지 않는다 — 명시 제외 코드 없이도 자연히 배제됨을 확인.
    assert _TYPE not in harness._ACTION_BY_PROPOSAL_TYPE
    assert _TYPE not in delegation_gate.delegable_types()


def test_search_term_promote_not_in_daily_lane_types():
    assert _TYPE not in auto_operator._DAILY_LANE_PROPOSAL_TYPES


def test_lane_never_sets_approval_source_for_promote(db):
    # SS4 레인 자체가 approval_source를 절대 채우지 않는지(자동 승인 배선 0) 구조 확인.
    _map_product(db)
    _term(db, term="좋은검색어")
    lane.run_search_term_ss_lane(db, now=_NOW)
    p = db.query(NaverProposal).filter(NaverProposal.proposal_type == _TYPE).one()
    assert p.approval_source is None


# ── ⑤ 미지원 실행 — harness에 실행 요청되면 fail-closed 거부(ActionNotExecutableError) ──
def test_search_term_promote_execution_rejected_fail_closed(db):
    _settings(db)
    p = _proposal(db, adgroup_id="grp-1", term="좋은검색어", status="approved")
    with pytest.raises(harness.ActionNotExecutableError):
        harness.execute(db, p.id, dry_run=False, now=_NOW)
    db.refresh(p)
    # 미지원 거부는 status 판정 전에 일어난다 — change_log 기록도 없음(writer 미호출·시도 자체 없음).
    assert p.status == "approved"
    assert p.executed_change_log_id is None


def test_search_term_promote_execution_rejected_even_when_pending(db):
    # 미실행 매핑 검사가 상태 검사보다 먼저라 pending에서도 동일하게 거부된다.
    _settings(db)
    p = _proposal(db, adgroup_id="grp-1", term="좋은검색어", status="pending")
    with pytest.raises(harness.ActionNotExecutableError):
        harness.execute(db, p.id, dry_run=False, now=_NOW)


def test_search_term_promote_not_executable_via_real_write_blocker(db):
    p = _proposal(db, adgroup_id="grp-1", term="좋은검색어", status="pending")
    assert harness.real_write_blocker(p) is not None  # 정보성(실행 대상 자체가 없음)


# ── ⑦ 생성 상한(_SS_PROMOTE_CAP) — 라이브 콘솔 범람 방지(2026-07-22 354건 실측 사고) ──
def test_promote_cap_limits_new_proposals_to_20(db):
    # 후보 25개(전환수 다양) → 상위 20건만 신규 생성, 나머지 5건은 promote_over_cap으로 카운트.
    for i in range(25):
        adgroup_id = f"grp-{i}"
        _map_product(db, adgroup_id=adgroup_id, cpid=f"P{i}")
        _term(db, term=f"검색어{i:02d}", adgroup_id=adgroup_id, dconv=i + 1, pconv=i + 1, pamt=(i + 1) * 1000)
    res = lane.run_search_term_ss_lane(db, now=_NOW)
    assert res["promote_candidates"] == 25
    assert res["promote_proposals_created"] == 20
    assert res["promote_over_cap"] == 5
    assert db.query(NaverProposal).filter(NaverProposal.proposal_type == _TYPE).count() == 20


def test_promote_cap_keeps_highest_conv_direct_cnt_first(db):
    # 정렬 검증: conv_direct_cnt 내림차순으로 상위 20건이 생성되므로, 최댓값 근처 후보들이
    # 전부 살아남고 최솟값 근처 후보들이 잘려나간다.
    for i in range(25):
        adgroup_id = f"grp-{i}"
        _map_product(db, adgroup_id=adgroup_id, cpid=f"P{i}")
        _term(db, term=f"검색어{i:02d}", adgroup_id=adgroup_id, dconv=i + 1, pconv=i + 1, pamt=(i + 1) * 1000)
    lane.run_search_term_ss_lane(db, now=_NOW)
    created_terms = {
        p.target_id for p in db.query(NaverProposal).filter(NaverProposal.proposal_type == _TYPE).all()
    }
    # dconv는 검색어XX의 XX+1 → 상위 20개는 i=5..24(검색어05~검색어24), 잘리는 건 i=0..4.
    expected = {f"검색어{i:02d}" for i in range(5, 25)}
    assert created_terms == expected
    for skipped in ("검색어00", "검색어01", "검색어02", "검색어03", "검색어04"):
        assert skipped not in created_terms


def test_promote_cap_sorts_by_conv_purchase_amt_as_tiebreak(db):
    # dconv 동률이면 conv_purchase_amt 내림차순 — 매출 큰 쪽이 상한 안에 남는다.
    _map_product(db, adgroup_id="grp-hi", cpid="PH")
    _map_product(db, adgroup_id="grp-lo", cpid="PL")
    _term(db, term="매출높음", adgroup_id="grp-hi", dconv=3, pconv=3, pamt=90000)
    _term(db, term="매출낮음", adgroup_id="grp-lo", dconv=3, pconv=3, pamt=10000)
    # 상한을 1로 낮춰 확인할 수 없으므로(상수 직접 변경 지양), monkeypatch로 상한을 1로 조정.
    import app.services.naver_ad.search_term_ss_lane as lane_mod
    orig_cap = lane_mod._SS_PROMOTE_CAP
    lane_mod._SS_PROMOTE_CAP = 1
    try:
        res = lane.run_search_term_ss_lane(db, now=_NOW)
    finally:
        lane_mod._SS_PROMOTE_CAP = orig_cap
    assert res["promote_proposals_created"] == 1
    assert res["promote_over_cap"] == 1
    created = db.query(NaverProposal).filter(NaverProposal.proposal_type == _TYPE).one()
    assert created.target_id == "매출높음"


def test_promote_dedup_does_not_consume_cap_slot(db):
    # 상한을 2로 낮추고, 3개 후보 중 1개는 이미 pending(dedup 대상) — dedup은 상한을 소모하지
    # 않으므로 나머지 2개(신규)가 모두 생성돼야 한다(신규 생성 ≤ cap이 목적이지, dedup까지
    # 합쳐 cap을 세는 게 아님).
    import app.services.naver_ad.search_term_ss_lane as lane_mod
    orig_cap = lane_mod._SS_PROMOTE_CAP
    lane_mod._SS_PROMOTE_CAP = 2
    try:
        _map_product(db, adgroup_id="grp-a", cpid="PA")
        _map_product(db, adgroup_id="grp-b", cpid="PB")
        _map_product(db, adgroup_id="grp-c", cpid="PC")
        _term(db, term="기존제안", adgroup_id="grp-a", dconv=5, pconv=5, pamt=50000)
        _term(db, term="신규1", adgroup_id="grp-b", dconv=4, pconv=4, pamt=40000)
        _term(db, term="신규2", adgroup_id="grp-c", dconv=3, pconv=3, pamt=30000)
        _proposal(db, adgroup_id="grp-a", term="기존제안", status="pending")
        res = lane.run_search_term_ss_lane(db, now=_NOW)
    finally:
        lane_mod._SS_PROMOTE_CAP = orig_cap
    assert res["promote_deduped"] == 1
    assert res["promote_proposals_created"] == 2
    assert res["promote_over_cap"] == 0
    terms = {p.target_id for p in db.query(NaverProposal).filter(NaverProposal.proposal_type == _TYPE).all()}
    assert terms == {"기존제안", "신규1", "신규2"}


# ── ⑥ drift 가드 — ALL_PROPOSAL_TYPES·INFORMATIONAL_PROPOSAL_TYPES 등록 확인 ──
def test_search_term_promote_registered_in_drift_guards():
    from app.services.naver_ad.proposal_writer import ALL_PROPOSAL_TYPES, INFORMATIONAL_PROPOSAL_TYPES

    assert _TYPE in ALL_PROPOSAL_TYPES
    assert _TYPE in INFORMATIONAL_PROPOSAL_TYPES  # 실행 매핑 없음 — 정보성(wisdom_promoted와 동형)
    assert _TYPE not in harness._ACTION_BY_PROPOSAL_TYPE  # 실행 매핑 부재가 fail-closed 안전장치
