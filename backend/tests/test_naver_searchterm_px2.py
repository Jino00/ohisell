# test_naver_searchterm_px2.py — PX2 파워링크 자동 발사 + 상태 테이블(search_term_ss_lane) 테스트
# 커버(PLAN_naver-ad-powerlink-autoexclude.md §2·§3, GATE): 자동 발사(approved+ss_exclude→harness)
#   ·restrict_kwd_id 회수·상태 행 upsert(cycle=1·next_review=+30d)·일일캡 사전제한·중복(활성 제외
#   행)·그룹 슬롯 캡·대행사 브리핑. 스코프 누출 0은 ss3 test_lane_powerlink_out_of_scope_never_fires.
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdDaily,
    NaverAdgroupProduct,
    NaverCampaignSettings,
    NaverChangeLog,
    NaverEntity,
    NaverProductBep,
    NaverProposal,
    NaverSearchTermDaily,
    NaverSearchTermExclusion,
)
from app.services.naver_ad import naver_execution_harness as harness
from app.services.naver_ad import naver_sa_writer
from app.services.naver_ad import search_term_judge as judge
from app.services.naver_ad import search_term_ss_lane as lane
from app.services.naver_ad.campaign_target_resolver import NAVER_CHANNEL_ID

_NOW = datetime(2026, 7, 22, 9, 0, 0)
_TYPE = judge.SEARCH_TERM_EXCLUDE_TYPE


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


def _scope(db, adgroup_id="grp-web", campaign_id="cmp1", auto_operate=True):
    """auto_operate=1·optimizer=ours·WEB_SITE 엔티티·상품 매핑(target_roas)·그룹 순손실 데이터 —
    파워링크 §1 통과 스코프 완비."""
    db.add(NaverCampaignSettings(campaign_id=campaign_id, optimizer="ours", auto_operate=auto_operate))
    db.add(NaverEntity(entity_type="adgroup", entity_id=adgroup_id, parent_id=campaign_id,
                       campaign_id=campaign_id, campaign_type="WEB_SITE", name="grp", status="on"))
    db.add(NaverProductBep(
        channel_id=NAVER_CHANNEL_ID, channel_product_id="PW", has_cost=True, product_name="필름 정품",
        selling_price=Decimal("1000"), contribution_margin=Decimal("500"), cost_price=0,
        commission_rate=0, logistics_cost=0, target_roas=Decimal("2.0"), bep_roas=Decimal("1.5"),
    ))
    db.add(NaverAdgroupProduct(adgroup_id=adgroup_id, mall_product_id="PW", campaign_id=campaign_id))
    db.add(NaverAdDaily(
        ad_date=date(2026, 7, 10), campaign_id=campaign_id, campaign_type="WEB_SITE",
        adgroup_id=adgroup_id, keyword_id="nkw-1", imp=500, clk=50, cost=10000, rank_sum=0,
        conv_direct_cnt=0, conv_indirect_cnt=0, conv_direct_amt=1000, conv_indirect_amt=0,
    ))
    db.commit()


def _pl_term(db, *, term, adgroup_id="grp-web", campaign_id="cmp1", clk=10, cost=6000,
             ad_date=date(2026, 7, 10)):
    db.add(NaverSearchTermDaily(
        ad_date=ad_date, campaign_id=campaign_id, adgroup_id=adgroup_id, search_term=term,
        source="expkeyword", imp=100, clk=clk, cost=cost, rank_sum=0,
        conv_purchase_cnt=0, conv_direct_cnt=0, conv_purchase_amt=0, cart_cnt=0, cart_amt=0,
    ))
    db.commit()


def _write_result(term, kwd_id="rkw-1"):
    after = [{"nccAdgroupRestrictKwdId": kwd_id, "keyword": term}]
    return naver_sa_writer.WriteResult(
        action="add_restricted_keywords", before=[], response=after, after=after, created_ids=[kwd_id],
    )


# ── 자동 발사 성공: change_log + 상태 행(cycle=1·next_review=+30d·restrict_kwd_id 회수) ──
def test_autofire_creates_changelog_and_exclusion_row(db):
    _scope(db)
    _pl_term(db, term="파워손실", clk=10, cost=6000)
    with patch.object(harness.naver_sa_writer, "add_restricted_keywords",
                      return_value=_write_result("파워손실")) as mock_write:
        res = lane.run_search_term_ss_lane(db, now=_NOW)
    mock_write.assert_called_once_with("grp-web", ["파워손실"])
    assert res["powerlink_fired"] == 1
    assert res["proposals_created"] == 1  # 하위호환 키

    # change_log 실집행 1건(exclude_search_term, dry_run=False, created_ids 회수)
    cl = db.query(NaverChangeLog).filter(NaverChangeLog.action == "exclude_search_term").one()
    assert cl.dry_run is False
    assert json.loads(cl.after_value)["created_ids"] == ["rkw-1"]

    # 상태 행 upsert(§2)
    row = db.query(NaverSearchTermExclusion).one()
    assert row.status == "excluded"
    assert row.cycle == 1
    assert row.restrict_kwd_id == "rkw-1"
    assert row.next_review_at == date(2026, 8, 21)  # today(7/22) + 30
    assert row.cost_at_exclusion == 6000

    # 자동 발사 제안: approved + ss_exclude 승인원
    p = db.query(NaverProposal).filter(NaverProposal.proposal_type == _TYPE).one()
    assert p.approval_source == judge.APPROVAL_SOURCE_SS_EXCLUDE
    assert p.executed_change_log_id == cl.id


# ── 일일캡 사전 제한: 오늘 이미 캡 도달이면 자동 발사 0(over_cap 카운트) ──
def test_daily_cap_pre_limits_autofire(db):
    _scope(db)
    _pl_term(db, term="파워손실", clk=10, cost=6000)
    for i in range(harness._SS_DAILY_EXCLUDE_CAP):  # 오늘 이미 10건 성공
        db.add(NaverChangeLog(
            entity_type="search_term", entity_id=f"t{i}", campaign_id="cmp1",
            action="exclude_search_term", dry_run=False, after_value="{}",
            changed_at=_NOW, executed_at=_NOW,
        ))
    db.commit()
    with patch.object(harness.naver_sa_writer, "add_restricted_keywords") as mock_write:
        res = lane.run_search_term_ss_lane(db, now=_NOW)
    mock_write.assert_not_called()  # 캡 소진 → 무의미한 발사 억제
    assert res["powerlink_fired"] == 0
    assert res["autofire_over_cap"] == 1


# ── 중복 방지: 활성 제외 상태 행(excluded/probation)이 있으면 재발사 안 함 ──
def test_active_exclusion_row_dedups(db):
    _scope(db)
    _pl_term(db, term="파워손실", clk=10, cost=6000)
    db.add(NaverSearchTermExclusion(
        campaign_id="cmp1", adgroup_id="grp-web", search_term="파워손실", status="excluded",
        cycle=1, restrict_kwd_id="rkw-old", excluded_at=_NOW, last_transition_at=_NOW,
        next_review_at=date(2026, 8, 21),
    ))
    db.commit()
    with patch.object(harness.naver_sa_writer, "add_restricted_keywords") as mock_write:
        res = lane.run_search_term_ss_lane(db, now=_NOW)
    mock_write.assert_not_called()
    assert res["powerlink_fired"] == 0
    assert res["deduped"] == 1


# ── 그룹 슬롯 캡: 그룹 excluded 행 60 도달이면 이번 라운드 스킵 ──
def test_group_slot_cap_skips(db):
    _scope(db)
    _pl_term(db, term="새손실", clk=10, cost=6000)
    for i in range(lane._PL_GROUP_SLOT_CAP):
        db.add(NaverSearchTermExclusion(
            campaign_id="cmp1", adgroup_id="grp-web", search_term=f"기존{i}", status="excluded",
            cycle=1, restrict_kwd_id=f"rkw-{i}", excluded_at=_NOW, last_transition_at=_NOW,
        ))
    db.commit()
    with patch.object(harness.naver_sa_writer, "add_restricted_keywords") as mock_write:
        res = lane.run_search_term_ss_lane(db, now=_NOW)
    mock_write.assert_not_called()
    assert res["powerlink_fired"] == 0
    assert res["slot_skipped"] == 1


# ── restrict_kwd_id 회수: WriteResult.created_ids가 상태 행에 저장 ──
def test_restrict_kwd_id_recovered_from_write_result(db):
    _scope(db)
    _pl_term(db, term="파워손실", clk=10, cost=6000)
    with patch.object(harness.naver_sa_writer, "add_restricted_keywords",
                      return_value=_write_result("파워손실", kwd_id="rkw-777")):
        lane.run_search_term_ss_lane(db, now=_NOW)
    row = db.query(NaverSearchTermExclusion).one()
    assert row.restrict_kwd_id == "rkw-777"


# ── 대행사(스코프 밖) 고비용 파워링크 → 카운트만 반환, 실쓰기 0 ──
# PX4: 대행사 브리핑 diary는 search_term_px_briefing.run_agency_powerlink_weekly_briefing(주간
# 일요일 게이트)이 담당하도록 이전됐다(test_naver_searchterm_px4.py 참조) — 이 레인 자체는
# agency_powerlink_candidates 카운트만 반환하고 diary를 쓰지 않는다(§4 2, D-NAO-79 노이즈 억제).
def test_agency_powerlink_briefing_no_write(db):
    # settings 없음(auto_operate=False) + 고비용 → agency_powerlink 카운트만.
    db.add(NaverProductBep(
        channel_id=NAVER_CHANNEL_ID, channel_product_id="PW", has_cost=True, product_name="필름",
        selling_price=Decimal("1000"), contribution_margin=Decimal("500"), cost_price=0,
        commission_rate=0, logistics_cost=0, target_roas=Decimal("2.0"),
    ))
    db.add(NaverAdgroupProduct(adgroup_id="grp-web", mall_product_id="PW", campaign_id="cmp1"))
    db.commit()
    _pl_term(db, term="대행사고비용", clk=12, cost=40000)
    with patch.object(harness.naver_sa_writer, "add_restricted_keywords") as mock_write:
        res = lane.run_search_term_ss_lane(db, now=_NOW)
    mock_write.assert_not_called()
    assert res["powerlink_fired"] == 0
    assert res["agency_powerlink_candidates"] == 1
    from app.models import OpsDiaryEntry
    briefs = db.query(OpsDiaryEntry).filter(OpsDiaryEntry.event_type == "observe").all()
    assert not any("대행사 고비용" in b.rationale for b in briefs)


# ── P2-3 GATE: _upsert_exclusion commit이 동시 insert 경합(uq_naver_search_term_exclusion)으로
#   IntegrityError를 던져도 rollback→재조회→cycle 승계 갱신으로 단일 행 수렴(orphan 0) ──
def test_upsert_exclusion_integrity_race_converges(db):
    from sqlalchemy.exc import IntegrityError

    cand = {"campaign_id": "cmp1", "adgroup_id": "grp-web", "search_term": "경합검색어", "cost": 6000}
    orig_commit = db.commit
    calls = {"n": 0}

    def racing_commit():
        calls["n"] += 1
        if calls["n"] == 1:
            # 다른 러너가 먼저 같은 (adgroup, term) 행을 커밋한 상황 재현 → uq 위반.
            db.expunge_all()  # 우리 pending 신규 행 폐기(플러시 전 — 승자만 남긴다)
            db.add(NaverSearchTermExclusion(
                campaign_id="cmp1", adgroup_id="grp-web", search_term="경합검색어",
                status="excluded", cycle=1, restrict_kwd_id="rkw-win",
                excluded_at=_NOW, last_transition_at=_NOW, cost_at_exclusion=5000,
            ))
            orig_commit()
            raise IntegrityError("INSERT", {}, Exception("UNIQUE constraint failed"))
        return orig_commit()

    with patch.object(db, "commit", side_effect=racing_commit):
        lane._upsert_exclusion(db, cand, "rkw-mine", _NOW)

    rows = db.query(NaverSearchTermExclusion).filter(
        NaverSearchTermExclusion.search_term == "경합검색어").all()
    assert len(rows) == 1  # orphan 없이 단일 행으로 수렴
    r = rows[0]
    assert r.status == "excluded"
    assert r.cycle == 2  # 기존 승자(cycle=1) 승계 +1
    assert r.restrict_kwd_id == "rkw-mine"  # 내 개방 id로 갱신
    assert r.next_review_at == date(2026, 7, 22) + timedelta(days=60)  # min(30×2,90)


# ── C3(codex 1R[P2]): 자동 발사 진입부 adgroup 소속 검증 — 인벤토리 parent_id ≠ 후보 campaign_id면
#   실쓰기 0(대행사 그룹 오염 차단, 스코프 과신 방지) ──
def test_autofire_blocked_when_adgroup_membership_mismatch(db):
    _scope(db)
    _pl_term(db, term="파워손실", clk=10, cost=6000)
    # 인벤토리 소속(parent_id)을 후보 campaign_id(cmp1)와 어긋나게 오염 — judge 스코프는
    # NaverCampaignSettings.auto_operate로 판정하므로 후보 산출은 그대로, 실쓰기만 차단돼야 한다.
    db.query(NaverEntity).filter(
        NaverEntity.entity_type == "adgroup", NaverEntity.entity_id == "grp-web"
    ).update({"parent_id": "cmp-대행사"}, synchronize_session=False)
    db.commit()
    with patch.object(harness.naver_sa_writer, "add_restricted_keywords") as mock_write:
        res = lane.run_search_term_ss_lane(db, now=_NOW)
    mock_write.assert_not_called()  # 소속 미검증 → 자동 제외 skip(fail-closed)
    assert res["powerlink_fired"] == 0
    assert res["autofire_failed"] == 1
    assert db.query(NaverSearchTermExclusion).count() == 0


# ── C4(codex 1R[P2] 기아 방지): 재심사(_run_reexamination)가 신규 파워링크 자동 발사보다 먼저 ──
def test_reexamination_runs_before_new_autofire(db):
    _scope(db)
    _pl_term(db, term="새손실", clk=10, cost=6000)
    calls: list[str] = []

    def fake_reexam(db_, powerlink, now):
        calls.append("reexam")
        return {"opened": 0, "reexcluded": 0, "restored": 0, "healed": 0}

    def fake_autofire(db_, cand, now):
        calls.append("autofire")
        return None  # 발사 안 함(상태 변화 없음) — 순서만 검증

    with patch.object(lane, "_run_reexamination", side_effect=fake_reexam):
        with patch.object(lane, "_autofire_exclude", side_effect=fake_autofire):
            lane.run_search_term_ss_lane(db, now=_NOW)
    assert "reexam" in calls and "autofire" in calls
    assert calls.index("reexam") < calls.index("autofire")  # 재심사 먼저


# ── C4: 재심사 재제외가 일일캡을 먼저 소비 → 신규 후보는 잔여 0으로 밀림(over_cap) ──
def test_reexam_consumes_cap_before_new_candidates(db):
    _scope(db)
    _pl_term(db, term="새손실", clk=10, cost=6000)
    # 오늘 이미 캡-1건 제외(잔여 1칸).
    for i in range(harness._SS_DAILY_EXCLUDE_CAP - 1):
        db.add(NaverChangeLog(
            entity_type="search_term", entity_id=f"t{i}", campaign_id="cmp1",
            action="exclude_search_term", dry_run=False, after_value="{}",
            changed_at=_NOW, executed_at=_NOW,
        ))
    db.commit()

    def fake_reexam(db_, powerlink, now):
        # 재심사가 마지막 1칸을 재제외로 소비했다고 시뮬(exclude change_log 커밋).
        db_.add(NaverChangeLog(
            entity_type="search_term", entity_id="재제외", campaign_id="cmp1",
            action="exclude_search_term", dry_run=False, after_value="{}",
            changed_at=now, executed_at=now,
        ))
        db_.commit()
        return {"opened": 0, "reexcluded": 1, "restored": 0, "healed": 0}

    with patch.object(lane, "_run_reexamination", side_effect=fake_reexam):
        with patch.object(harness.naver_sa_writer, "add_restricted_keywords") as mock_write:
            res = lane.run_search_term_ss_lane(db, now=_NOW)
    mock_write.assert_not_called()  # 재심사가 잔여 캡 소진 → 신규 발사 밀림
    assert res["powerlink_fired"] == 0
    assert res["autofire_over_cap"] == 1


# ── P2-3: 경합이 아닌 진짜 무결성 위반(재조회도 없음)은 삼키지 않고 re-raise ──
def test_upsert_exclusion_integrity_non_race_reraises(db):
    from sqlalchemy.exc import IntegrityError

    cand = {"campaign_id": "cmp1", "adgroup_id": "grp-web", "search_term": "진짜오류", "cost": 6000}
    orig_commit = db.commit
    calls = {"n": 0}

    def failing_commit():
        calls["n"] += 1
        if calls["n"] == 1:
            db.expunge_all()  # 승자 행을 심지 않는다 → 재조회 시 None
            raise IntegrityError("INSERT", {}, Exception("other"))
        return orig_commit()

    with patch.object(db, "commit", side_effect=failing_commit):
        with pytest.raises(IntegrityError):
            lane._upsert_exclusion(db, cand, "rkw-mine", _NOW)
