# test_naver_diary.py — D-NAO-54 P1 운영 일기 기록층(diary.py) + 훅 단위테스트.
# 실 API 호출 0 — naver_sa_writer(라이브 재조회)·naver_execution_harness.execute(실행)의
# writer는 전부 mock. env_snapshot_sa는 순수 조회 SA라 시드 데이터만으로 검증한다.
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import holidays as holidays_lib
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdDaily,
    NaverCampaignSettings,
    NaverChangeLog,
    NaverEntity,
    NaverHourlySnapshot,
    NaverKeywordHourly,
    NaverProposal,
    NaverRetroSignal,
    OpsDiaryEntry,
)
from app.services.naver_ad import auto_operator, diary
from app.services.naver_ad import naver_execution_harness as harness
from app.services.naver_ad import naver_sa_writer


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


# ══════════════════════════ env_snapshot_sa ══════════════════════════


def _pick_2026_kr_holiday_and_plain_weekday() -> tuple[date, date]:
    """holidays.SouthKorea()에서 2026년 실제 공휴일 하나와, 공휴일이 아닌 평일 하나를 동적으로
    고른다(하드코딩 금지 — 라이브러리가 진실). diary._KR_HOLIDAYS와 동일 클래스라 멤버십 동일."""
    kr = holidays_lib.SouthKorea()
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(365)]
    holiday = next(d for d in days if d in kr)
    plain = next(d for d in days if d not in kr and d.weekday() < 5)
    return holiday, plain


def test_env_snapshot_is_kr_holiday_true_on_real_holiday_false_on_weekday(db):
    holiday, plain = _pick_2026_kr_holiday_and_plain_weekday()

    env_holiday = diary.env_snapshot_sa(db, datetime.combine(holiday, datetime.min.time()), "cmp1")
    env_plain = diary.env_snapshot_sa(db, datetime.combine(plain, datetime.min.time()), "cmp1")

    assert env_holiday["is_kr_holiday"] is True, f"{holiday} 는 공휴일이어야 함"
    assert env_plain["is_kr_holiday"] is False, f"{plain} 는 평일이어야 함"
    # weekday도 KST now 기준으로 정확히 매핑되는지 부수 확인
    assert env_plain["weekday"] == plain.weekday()


@pytest.mark.parametrize("month,expected_season", [
    (3, "spring"),
    (8, "summer"),
    (11, "autumn"),
    (12, "winter"),
    (2, "winter"),
])
def test_env_snapshot_season_boundaries(db, month, expected_season):
    now = datetime(2026, month, 15, 10, 0, 0)
    env = diary.env_snapshot_sa(db, now, "cmp1")
    assert env["season"] == expected_season


@pytest.mark.parametrize("now_date,expected_offset", [
    (date(2025, 9, 25), 6),   # 출시(2025-09-19) 후 6일
    (date(2025, 9, 10), -9),                                   # 가장 가까운 출시(2025-09-19) 9일 전
])
def test_env_snapshot_iphone_offset_signed_and_nearest(db, now_date, expected_offset):
    """config(2023-09-22/2024-09-20/2025-09-19) 기준 — 부호(±)와 '가장 가까운(절댓값 최소)'
    출시일 선택을 검증."""
    env = diary.env_snapshot_sa(db, datetime.combine(now_date, datetime.min.time()), "cmp1")
    assert env["iphone_launch_offset_days"] == expected_offset


def test_env_snapshot_spend_pacing_pct_from_latest_snapshot(db):
    today = date(2026, 7, 15)
    now = datetime.combine(today, datetime.min.time()).replace(hour=10)
    db.add(NaverHourlySnapshot(
        snapshot_at=now, ad_date=today, snapshot_hour=9, campaign_id="cmp1",
        campaign_type="", cost=2500, clk=5, imp=100, daily_budget=10000,
    ))
    db.commit()
    env = diary.env_snapshot_sa(db, now, "cmp1")
    assert env["spend_pacing_pct"] == 25.0


def test_env_snapshot_spend_pacing_none_when_no_snapshot(db):
    now = datetime(2026, 7, 15, 10, 0, 0)
    env = diary.env_snapshot_sa(db, now, "cmp1")
    assert env["spend_pacing_pct"] is None


def test_env_snapshot_spend_pacing_none_when_daily_budget_missing(db):
    today = date(2026, 7, 15)
    now = datetime.combine(today, datetime.min.time()).replace(hour=10)
    db.add(NaverHourlySnapshot(
        snapshot_at=now, ad_date=today, snapshot_hour=9, campaign_id="cmp1",
        campaign_type="", cost=2500, clk=5, imp=100, daily_budget=None,
    ))
    db.commit()
    env = diary.env_snapshot_sa(db, now, "cmp1")
    assert env["spend_pacing_pct"] is None


def test_env_snapshot_avg_rank_latest_for_keyword_target(db):
    today = date(2026, 7, 15)
    now = datetime.combine(today, datetime.min.time()).replace(hour=10)
    # 옛 행(작은 순위) + 최신 행(큰 순위) — 최신(ad_date desc, hour desc)이 선택돼야 함
    db.add(NaverKeywordHourly(
        ad_date=today - timedelta(days=1), hour=5, entity_type="keyword", entity_id="nkw-1",
        adgroup_id="grp-1", campaign_id="cmp1", campaign_type="WEB_SITE",
        imp=10, clk=1, cost=100, avg_rank=Decimal("2.0"),
    ))
    db.add(NaverKeywordHourly(
        ad_date=today, hour=8, entity_type="keyword", entity_id="nkw-1",
        adgroup_id="grp-1", campaign_id="cmp1", campaign_type="WEB_SITE",
        imp=10, clk=1, cost=100, avg_rank=Decimal("3.5"),
    ))
    db.commit()
    env = diary.env_snapshot_sa(db, now, "cmp1", target_type="keyword", target_id="nkw-1")
    assert env["avg_rank"] == 3.5


def test_env_snapshot_avg_rank_none_when_target_not_keyword(db):
    today = date(2026, 7, 15)
    now = datetime.combine(today, datetime.min.time()).replace(hour=10)
    db.add(NaverKeywordHourly(
        ad_date=today, hour=8, entity_type="keyword", entity_id="nkw-1",
        adgroup_id="grp-1", campaign_id="cmp1", campaign_type="WEB_SITE",
        imp=10, clk=1, cost=100, avg_rank=Decimal("3.5"),
    ))
    db.commit()
    # target_type 없음 → None(같은 행이 있어도 keyword 대상이 아니면 조회하지 않음)
    assert diary.env_snapshot_sa(db, now, "cmp1")["avg_rank"] is None
    # target_type='adgroup' → None
    assert diary.env_snapshot_sa(
        db, now, "cmp1", target_type="adgroup", target_id="nkw-1"
    )["avg_rank"] is None


# ══════════════════════════ write_diary_entry (fail-open 계약) ══════════════════════════


def test_write_diary_entry_records_row_with_env_and_core_fields(db):
    today = date(2026, 7, 15)  # 7월 = summer, 평일
    now = datetime.combine(today, datetime.min.time()).replace(hour=10)
    db.add(NaverHourlySnapshot(
        snapshot_at=now, ad_date=today, snapshot_hour=9, campaign_id="cmp1",
        campaign_type="", cost=2500, clk=5, imp=100, daily_budget=10000,
    ))
    db.add(NaverKeywordHourly(
        ad_date=today, hour=8, entity_type="keyword", entity_id="nkw-1",
        adgroup_id="grp-1", campaign_id="cmp1", campaign_type="WEB_SITE",
        imp=10, clk=1, cost=100, avg_rank=Decimal("3.5"),
    ))
    db.commit()

    diary.write_diary_entry(
        db, "execute", "cmp1", actor="console", target_type="keyword", target_id="nkw-1",
        adgroup_id="grp-1", action="update_bid", before_value='{"bidAmt": 190}',
        after_value='{"bidAmt": 210}', rationale="테스트 근거", source_ref=42, now=now,
    )

    rows = db.query(OpsDiaryEntry).all()
    assert len(rows) == 1
    row = rows[0]
    # core 필드
    assert row.event_type == "execute"
    assert row.campaign_id == "cmp1"
    assert row.actor == "console"
    assert row.action == "update_bid"
    assert row.target_type == "keyword"
    assert row.target_id == "nkw-1"
    assert row.adgroup_id == "grp-1"
    assert row.source_ref == 42
    assert row.before_value == '{"bidAmt": 190}'
    assert row.after_value == '{"bidAmt": 210}'
    # env 컬럼 채워짐
    assert row.weekday == today.weekday()
    assert row.season == "summer"
    assert row.is_kr_holiday is False
    assert row.spend_pacing_pct == 25.0
    assert row.avg_rank == 3.5
    assert row.iphone_launch_offset_days is not None  # config 존재 → None 아님


def test_write_diary_entry_fail_open_swallows_error_no_row(db, monkeypatch, caplog):
    """세션 생성이 예외를 던져도(=일기 insert 실패) 예외가 전파되지 않고, 행도 안 생긴다.
    fail-open: 일기 실패가 집행을 절대 막지 않는다."""
    def _boom(_db):
        raise RuntimeError("모의 일기 세션 실패")

    monkeypatch.setattr(diary, "_new_diary_session", _boom)

    with caplog.at_level("WARNING"):
        # 예외 전파 없음 — 그냥 반환
        diary.write_diary_entry(db, "execute", "cmp1", actor="console")

    assert db.query(OpsDiaryEntry).count() == 0
    assert any("fail-open" in r.message for r in caplog.records)


def test_write_diary_entry_records_row_even_when_env_snapshot_raises(db, monkeypatch):
    """env_snapshot_sa가 예외를 던져도 일기 행은 남고(env 컬럼 전부 None), 예외는 전파 안 됨."""
    def _boom(*a, **k):
        raise RuntimeError("모의 env 실패")

    monkeypatch.setattr(diary, "env_snapshot_sa", _boom)

    diary.write_diary_entry(
        db, "blocked", "cmp1", actor="daily", target_type="keyword", target_id="nkw-1",
        action="bid_up", rationale="차단 사유",
    )

    rows = db.query(OpsDiaryEntry).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.event_type == "blocked"
    assert row.action == "bid_up"
    # env 컬럼은 전부 None(스냅샷 실패 → 컬럼 None으로라도 기록)
    assert row.weekday is None
    assert row.is_kr_holiday is None
    assert row.season is None
    assert row.iphone_launch_offset_days is None
    assert row.spend_pacing_pct is None
    assert row.avg_rank is None


# ══════════════════════════ actor_from_approval_source ══════════════════════════


@pytest.mark.parametrize("approval_source,expected_actor", [
    ("auto_op", "daily"),
    ("auto_op_hr", "hourly"),
    (None, "console"),
    ("delegation", "delegation"),
])
def test_actor_from_approval_source(approval_source, expected_actor):
    assert diary.actor_from_approval_source(approval_source) == expected_actor


# ══════════════════════════ 훅 — auto_operator 레인 ══════════════════════════
# 아래 헬퍼는 test_naver_auto_operator.py의 픽스처 스타일을 그대로 재사용한다(로컬 복제 —
# 테스트 파일 간 import 결합 회피).

CAMPAIGN = "cmp-04"
TODAY = date(2026, 7, 20)
NOW = datetime(2026, 7, 20, 8, 50, 0)  # 일 레인 크론 시각(KST naive)
DAY_START_UTC = datetime.combine(TODAY, datetime.min.time()) - timedelta(hours=9)


def _lane_settings(db, *, campaign_id=CAMPAIGN, auto_operate=True, optimizer="ours",
                   target_roas_override=None, seed_chain=True, seed_snapshot=True):
    db.add(NaverCampaignSettings(
        campaign_id=campaign_id, auto_operate=auto_operate, optimizer=optimizer,
        target_roas_override=target_roas_override,
    ))
    if seed_chain:
        db.add(NaverEntity(entity_type="campaign", entity_id=campaign_id,
                           campaign_id=campaign_id, status="on"))
        db.add(NaverEntity(entity_type="adgroup", entity_id="grp-1", parent_id=campaign_id,
                           campaign_id=campaign_id, status="on"))
    if seed_snapshot:
        db.add(NaverHourlySnapshot(
            snapshot_at=NOW, ad_date=TODAY, snapshot_hour=23, campaign_id=campaign_id,
            campaign_type="", cost=0, clk=0, imp=0,
        ))
    db.commit()


def _lane_proposal(db, *, proposal_type="bid_up", campaign_id=CAMPAIGN, target_type="keyword",
                   target_id="nkw-1", target_bid=None,
                   rationale="[bleeding_keywords] 보정ROAS=1.0 cost=100원 clk=15 — 시뮬 근거=x",
                   status="pending", created_at=None, adgroup_id=None):
    p = NaverProposal(
        proposal_type=proposal_type, target_type=target_type, target_id=target_id,
        campaign_id=campaign_id, adgroup_id=adgroup_id, rationale=rationale,
        expected_effect="테스트 예상효과", status=status, target_bid=target_bid,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    ts = created_at if created_at is not None else DAY_START_UTC + timedelta(hours=1)
    db.query(NaverProposal).filter(NaverProposal.id == p.id).update({"created_at": ts})
    db.commit()
    db.refresh(p)
    return p


def _ad_row(db, *, campaign_id=CAMPAIGN, campaign_type="WEB_SITE", adgroup_id="grp-1",
            keyword_id="nkw-1", ad_date, imp=10, clk=5, cost=1000,
            conv_direct_amt=0, conv_indirect_amt=0):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type=campaign_type,
        adgroup_id=adgroup_id, keyword_id=keyword_id, imp=imp, clk=clk, cost=cost,
        conv_direct_amt=conv_direct_amt, conv_indirect_amt=conv_indirect_amt,
    ))
    db.commit()


def _retro_signal(db, *, asof_date, board, target_id, campaign_id=CAMPAIGN, grain="keyword",
                  direction="down"):
    db.add(NaverRetroSignal(
        created_at=NOW, asof_date=asof_date, board=board, direction=direction, grain=grain,
        target_id=target_id, campaign_id=campaign_id, cf_asof=1.0, bep_asof=1.0,
        target_asof=1.5, cost_asof=100,
    ))
    db.commit()


def _hour(h, *, imp, clk, cost, avg_rank=None):
    return {"hour": h, "imp": imp, "clk": clk, "cost": cost, "avg_rank": avg_rank}


def _seed_bid_up(db, *, target_id="nkw-1", current_bid=1000, target_bid=1100, clk=15):
    _lane_settings(db, target_roas_override=Decimal("2.0"))
    window_from = auto_operator._settlement_window(TODAY)[0]
    rationale = f"[bleeding_keywords] 보정ROAS=1.0 cost=100원 clk={clk} — 시뮬 근거=x, 추천입찰={target_bid}원"
    p = _lane_proposal(db, proposal_type="bid_up", target_id=target_id, target_bid=target_bid,
                       rationale=rationale)
    _ad_row(db, keyword_id=target_id, ad_date=window_from + timedelta(days=1),
            imp=50, clk=20, cost=1000, conv_direct_amt=3000)
    _retro_signal(db, asof_date=TODAY - timedelta(days=1), board="bleeding_keywords",
                  target_id="nkw-other")
    return p, current_bid


def test_daily_lane_bid_up_hold_records_blocked_diary_row(db):
    """일 레인 bid_up 4조건 미충족(조건② clk<10) hold → diary 'blocked' 1건
    (actor='daily', action='bid_up')."""
    p, current_bid = _seed_bid_up(db, clk=3)  # 조건② 미충족 → hold
    with patch.object(auto_operator.naver_sa_writer, "get_keyword",
                      return_value={"bidAmt": current_bid}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_daily_lane(db, now=NOW)

    mock_exec.assert_not_called()
    blocked = db.query(OpsDiaryEntry).filter(OpsDiaryEntry.event_type == "blocked").all()
    assert len(blocked) == 1
    row = blocked[0]
    assert row.actor == "daily"
    assert row.action == "bid_up"
    assert row.target_type == "keyword"
    assert row.target_id == "nkw-1"
    assert row.campaign_id == CAMPAIGN


def test_daily_lane_rejected_stale_records_reject_diary_rows(db):
    """레인 말미 잔존 pending → rejected 처리, 그 수만큼 diary 'reject' 행."""
    _lane_settings(db)
    _lane_proposal(db, proposal_type="bid_down",
                   created_at=DAY_START_UTC - timedelta(hours=1))  # 어제 생성 stale
    result = auto_operator.run_daily_lane(db, now=NOW)

    assert result["rejected_stale"] == 1
    reject_rows = db.query(OpsDiaryEntry).filter(OpsDiaryEntry.event_type == "reject").all()
    assert len(reject_rows) == result["rejected_stale"] == 1
    assert reject_rows[0].actor == "daily"
    assert reject_rows[0].action == "bid_down"


def test_hourly_lane_circuit_breaker_records_campaign_level_blocked(db):
    """시간당 레인 소진 서킷브레이커 hold → 'blocked'(actor='hourly', campaign 수준 —
    target_type/target_id 없음)."""
    _lane_settings(db, seed_snapshot=False)
    window_from, window_to = auto_operator._settlement_window(TODAY)
    for i in range(7):  # 직전 7일 일평균 = 700/7 = 100원
        _ad_row(db, keyword_id="", adgroup_id="grp-x", campaign_type="SHOPPING",
                ad_date=window_to - timedelta(days=i), clk=1, cost=100)
    db.add(NaverHourlySnapshot(
        snapshot_at=NOW, ad_date=TODAY, snapshot_hour=8, campaign_id=CAMPAIGN,
        campaign_type="SHOPPING", cost=500, clk=10, imp=100,  # 500 > 100×3 → 브레이커
    ))
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-never", parent_id="grp-1",
                       campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-never", ad_date=window_from, clk=99, cost=100)
    db.commit()

    def fetch(tid, d):
        raise AssertionError("서킷브레이커가 걸리면 intraday 조회 자체가 없어야 함")

    result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=fetch)

    assert result["reviewed"] == 0
    blocked = db.query(OpsDiaryEntry).filter(OpsDiaryEntry.event_type == "blocked").all()
    assert len(blocked) == 1
    row = blocked[0]
    assert row.actor == "hourly"
    assert row.campaign_id == CAMPAIGN
    assert row.target_type is None
    assert row.target_id is None
    assert "서킷브레이커" in (row.rationale or "")


def test_hourly_lane_neutral_band_hold_writes_no_diary_row(db):
    """소음 차단 설계: 판정 hold(밴드 정상 — 순위 2.5~4)는 액션 의도가 없어 diary 행 없음."""
    _lane_settings(db)
    window_from = auto_operator._settlement_window(TODAY)[0]
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-neutral", parent_id="grp-1",
                       campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    # baseline CPC=1000/10=100원 → 당일 CPC(50원)가 급등(×2=200원)에 못 미쳐 DOWN 미발동
    _ad_row(db, keyword_id="nkw-neutral", ad_date=window_from, clk=10, cost=1000)
    db.commit()

    # avg_rank=3.0 (2.5~4.0 사이 = 중립밴드), imp 충분 → 판정 hold(기본)
    curve = [
        _hour(6, imp=15, clk=2, cost=100, avg_rank=3.0),
        _hour(7, imp=15, clk=2, cost=100, avg_rank=3.0),
        _hour(8, imp=15, clk=2, cost=100, avg_rank=3.0),
    ]
    result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda tid, d: curve)

    assert result["approved"] == 0
    assert any("판정 조건 미충족" in h["reason"] for h in result["held"])
    assert db.query(OpsDiaryEntry).count() == 0  # 관찰 소음 — 기록 안 함


def test_hourly_lane_no_imp_today_hold_writes_no_diary_row(db):
    """소음 차단 설계: '당일 imp 없음' hold도 액션 의도가 없어 diary 행 없음."""
    _lane_settings(db)
    window_from = auto_operator._settlement_window(TODAY)[0]
    db.add(NaverEntity(entity_type="keyword", entity_id="nkw-noimp", parent_id="grp-1",
                       campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    _ad_row(db, keyword_id="nkw-noimp", ad_date=window_from, clk=10, cost=100)
    db.commit()

    curve = [_hour(9, imp=0, clk=0, cost=0)]  # 당일 imp=0 → held(당일 imp 없음)
    result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda tid, d: curve)

    assert any("imp" in h["reason"] for h in result["held"])
    assert db.query(OpsDiaryEntry).count() == 0


# ══════════════════════════ 훅 — naver_execution_harness ══════════════════════════


def _hproposal(db, *, proposal_type="bid_up", campaign_id="cmp1", status="approved",
               target_type="keyword", target_id="nkw-1", adgroup_id=None,
               approval_source=None, target_bid=None, target_lock=None):
    p = NaverProposal(
        proposal_type=proposal_type, target_type=target_type, target_id=target_id,
        campaign_id=campaign_id, adgroup_id=adgroup_id, rationale="테스트 근거",
        expected_effect="테스트 예상효과", status=status, target_bid=target_bid,
        target_lock=target_lock, approval_source=approval_source,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _hsettings(db, *, campaign_id="cmp1", optimizer="ours", auto_operate=False):
    db.add(NaverCampaignSettings(campaign_id=campaign_id, optimizer=optimizer,
                                 auto_operate=auto_operate))
    db.commit()


def _kw_write_result(before=None, after=None):
    return naver_sa_writer.WriteResult(
        action="update_keyword_bid",
        before=before if before is not None else {"bidAmt": 190, "userLock": False},
        response=after, after=after if after is not None else {"bidAmt": 210, "userLock": False},
        created_ids=[],
    )


def test_harness_execute_success_writes_execute_diary_row(db):
    """실쓰기 성공 → diary 'execute' 행 + source_ref=change_log id + before/after_value 일치."""
    p = _hproposal(db, proposal_type="bid_up", target_bid=210)
    _hsettings(db, optimizer="ours")
    result = _kw_write_result(before={"bidAmt": 190, "userLock": False},
                              after={"bidAmt": 210, "userLock": False})

    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid", return_value=result):
        log_entry = harness.execute(db, p.id, dry_run=False)

    rows = db.query(OpsDiaryEntry).filter(OpsDiaryEntry.event_type == "execute").all()
    assert len(rows) == 1
    row = rows[0]
    assert row.actor == "console"  # approval_source None → console
    assert row.action == "update_bid"
    assert row.source_ref == log_entry.id
    assert row.before_value == log_entry.before_value
    assert row.after_value == log_entry.after_value
    assert json.loads(row.after_value) == {"bidAmt": 210, "userLock": False}


def test_harness_guard_failure_writes_blocked_diary_row(db):
    """가드레일 차단(_guard_failure 경로) → 'blocked' 행 + source_ref=그 [실행 불가] change_log id."""
    p = _hproposal(db, proposal_type="bid_up", target_bid=300)
    _hsettings(db, optimizer="ours")

    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value="변경폭 초과"), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_write:
        with pytest.raises(harness.MissingExecutionTargetError):
            harness.execute(db, p.id, dry_run=False)

    mock_write.assert_not_called()
    # [실행 불가] change_log 1건
    logs = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).all()
    assert len(logs) == 1
    blocked = db.query(OpsDiaryEntry).filter(OpsDiaryEntry.event_type == "blocked").all()
    assert len(blocked) == 1
    row = blocked[0]
    assert row.action == "update_bid"
    assert row.actor == "console"  # approval_source None → console
    assert row.source_ref == logs[0].id  # 방금 커밋한 change_log id를 가리킴


def test_harness_kill_switch_writes_kill_switch_diary_row(db):
    """킬스위치(execute() 쓰기 직전 재확인 거부) → 'kill_switch' 행. 쓰기·change_log 없음."""
    _hsettings(db, optimizer="ours", auto_operate=False)  # 승인 후 OFF 된 상태
    p = _hproposal(db, proposal_type="bid_down", target_bid=850,
                   approval_source=auto_operator.APPROVAL_SOURCE_HOURLY)

    with patch.object(harness, "_build_guardrail_context", return_value={}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_write:
        with pytest.raises(harness.KillSwitchEngagedError):
            harness.execute(db, p.id, dry_run=False)

    mock_write.assert_not_called()
    rows = db.query(OpsDiaryEntry).filter(OpsDiaryEntry.event_type == "kill_switch").all()
    assert len(rows) == 1
    assert rows[0].actor == "hourly"  # auto_op_hr → hourly
    assert rows[0].campaign_id == "cmp1"
    # 쓰기·change_log 없음
    assert db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).count() == 0


def test_harness_dry_run_writes_no_diary_row(db):
    """dry_run 경로(정보성/미개방 액션) → diary 행 없음."""
    p = _hproposal(db, proposal_type="bid_up", target_bid=210)
    _hsettings(db, optimizer="ours")

    with patch.object(harness.naver_sa_writer, "update_keyword_bid") as mock_write:
        log_entry = harness.execute(db, p.id)  # dry_run 기본 True

    mock_write.assert_not_called()
    assert log_entry.dry_run is True
    assert db.query(OpsDiaryEntry).count() == 0
