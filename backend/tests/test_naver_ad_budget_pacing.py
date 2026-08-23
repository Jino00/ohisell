# test_naver_ad_budget_pacing.py — BP(예산 페이싱) 레인 단위테스트 (D-NAO-102)
# 실 API 호출 0. budget_pacing(SA)은 순수 판정이라 mock 없이 픽스처로 검증하고,
# 하니스(auto_operator._run_budget_pacing_lane)는 naver_execution_harness.execute를 mock한다.
# 단, "가드레일 관통" 통합 테스트 1건만 harness.execute를 실제로 태워
# guardrail_gate._check_budget까지 관통하는 것을 증명한다(naver_sa_writer만 최하단에서 mock).
from __future__ import annotations

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
    NaverHourlySnapshot,
    NaverProductBep,
    NaverProposal,
    Order,
)
from app.services.naver_ad import auto_operator, budget_pacing

CAMPAIGN = "cmp-bp"
CPID = "1000001"
TODAY = date(2026, 7, 27)
NOW = datetime(2026, 7, 27, 20, 20, 0)  # 20:20 KST(시간당 레인 크론 시각)
NAVER_CHANNEL = 6


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


# ────────────────────────── 시드 헬퍼 ──────────────────────────
def _settings(db, *, campaign_id=CAMPAIGN, auto_operate=True, optimizer="ours",
              base_daily_budget=None, target_roas_override=None, seed_chain=True):
    db.add(NaverCampaignSettings(
        campaign_id=campaign_id, auto_operate=auto_operate, optimizer=optimizer,
        base_daily_budget=base_daily_budget, target_roas_override=target_roas_override,
    ))
    if seed_chain:
        db.add(NaverEntity(entity_type="campaign", entity_id=campaign_id,
                           campaign_id=campaign_id, status="on"))
    db.commit()


def _snap(db, hour, cost, budget=100_000, *, campaign_id=CAMPAIGN, ad_date=TODAY, clk=20):
    """clk 기본 20 = 프록시 최소 볼륨 게이트(MIN_CLICKS_FOR_PROXY=5) 기본 통과.
    표본 게이트 자체를 검증하는 테스트만 clk를 낮춘다."""
    db.add(NaverHourlySnapshot(
        snapshot_at=datetime.combine(ad_date, datetime.min.time()) + timedelta(hours=hour),
        ad_date=ad_date, snapshot_hour=hour, campaign_id=campaign_id, campaign_type="SHOPPING",
        cost=cost, clk=clk, imp=0, daily_budget=budget,
    ))
    db.commit()


def _mapping(db, *, campaign_id=CAMPAIGN, cpid=CPID, adgroup_id="grp-1"):
    db.add(NaverAdgroupProduct(
        adgroup_id=adgroup_id, campaign_id=campaign_id, mall_product_id=cpid, product_name="p",
    ))
    db.commit()


def _bep(db, *, cpid=CPID, target_roas="2.0", bep_roas="1.5"):
    db.add(NaverProductBep(
        channel_id=NAVER_CHANNEL, channel_product_id=cpid, product_name="p",
        selling_price=Decimal("20000"), cost_price=Decimal("8000"),
        commission_rate=Decimal("0.0589"), contribution_margin=Decimal("10000"),
        bep_roas=Decimal(bep_roas), target_roas=Decimal(target_roas), has_cost=True,
    ))
    db.commit()


def _order(db, amount, *, cpid=CPID, when=None, status="delivered", n=1):
    when = when or datetime.combine(TODAY, datetime.min.time()) + timedelta(hours=12)
    db.add(Order(
        channel_id=NAVER_CHANNEL, order_number=f"ord-{cpid}-{amount}-{n}",
        platform_product_id=cpid, platform_order_line_id=str(n), quantity=1,
        selling_price=Decimal(str(amount)), order_date=when, status=status,
    ))
    db.commit()


def _daily(db, ad_date, cost, conv_amt, *, campaign_id=CAMPAIGN, adgroup_id="grp-1"):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, adgroup_id=adgroup_id, keyword_id="",
        campaign_type="SHOPPING", imp=100, clk=10, cost=cost,
        conv_direct_cnt=1, conv_direct_amt=conv_amt, conv_indirect_cnt=0, conv_indirect_amt=0,
    ))
    db.commit()


def _pacing_log(db, *, campaign_id=CAMPAIGN, changed_at, action=None, after='{"dailyBudget": 150000}'):
    db.add(NaverChangeLog(
        entity_type="campaign", entity_id=campaign_id, campaign_id=campaign_id,
        action=action or budget_pacing.ACTION_BUDGET_UP_PACING,
        rationale=f"{budget_pacing.BUDGET_PACING_PREFIX} 테스트",
        dry_run=False, before_value='{"dailyBudget": 100000}', after_value=after,
        changed_at=changed_at, executed_at=changed_at,
    ))
    db.commit()


def _ready(db, *, cost=95_000, budget=100_000, revenue=250_000, target="2.0", base=None):
    """트리거·성과·사이징이 모두 통과하는 기본 상태(소진율 95%, 프록시 ROAS 2.63 ≥ 2.0)."""
    _settings(db, base_daily_budget=base)
    _mapping(db)
    _bep(db, target_roas=target)
    # 3시간 창: 17시 → 20시 누적. 시간당 5,000원 소진.
    _snap(db, 17, cost - 15_000, budget)
    _snap(db, 18, cost - 10_000, budget)
    _snap(db, 19, cost - 5_000, budget)
    _snap(db, 20, cost, budget)
    _order(db, revenue)


# ══════════════════════ SA: 트리거 경계 ══════════════════════
def test_trigger_below_90_percent_no_raise(db):
    _ready(db, cost=89_000)
    d = budget_pacing.evaluate(db, now=NOW)[0]
    assert d["needs_raise"] is False
    assert "트리거 미달" in d["reason"]


def test_trigger_exactly_90_percent_raises(db):
    _ready(db, cost=90_000)
    d = budget_pacing.evaluate(db, now=NOW)[0]
    assert d["needs_raise"] is True
    assert d["depletion_ratio"] == Decimal("0.9")


def test_trigger_above_90_percent_raises(db):
    _ready(db, cost=95_000)
    d = budget_pacing.evaluate(db, now=NOW)[0]
    assert d["needs_raise"] is True


# ══════════════════════ SA: fail-closed 각 사유 ══════════════════════
def test_fail_closed_no_snapshot(db):
    _settings(db)
    _mapping(db)
    _bep(db)
    d = budget_pacing.evaluate(db, now=NOW)[0]
    assert d["needs_raise"] is False
    assert "스냅샷 부재" in d["reason"]


def test_fail_closed_daily_budget_none(db):
    _settings(db)
    _mapping(db)
    _bep(db)
    _snap(db, 20, 95_000, None)
    d = budget_pacing.evaluate(db, now=NOW)[0]
    assert d["needs_raise"] is False
    assert "일예산 미확보" in d["reason"]


def test_uncapped_budget_is_not_a_pacing_case(db):
    _settings(db)
    _mapping(db)
    _bep(db)
    _snap(db, 20, 95_000, 0)
    d = budget_pacing.evaluate(db, now=NOW)[0]
    assert d["needs_raise"] is False
    assert "uncapped" in d["reason"]


def test_fail_closed_no_product_mapping(db):
    """매핑 sync 전(optimizer 갓 전환/신규) 캠페인 — 증액하지 않는다.

    ★07-27 03은 auto_operate=0이라 1단계에서 이미 제외였고, 강제 편입해도 여기서 또 막힌다
    (매핑 부재는 03 배제의 1차 사유가 아니라 2차 방어선)."""
    _settings(db)
    _bep(db)
    _snap(db, 20, 95_000, 100_000)
    d = budget_pacing.evaluate(db, now=NOW)[0]
    assert d["needs_raise"] is False
    assert "상품 매핑 부재" in d["reason"]
    assert d["product_count"] == 0


def test_fail_closed_target_roas_unresolved(db):
    _settings(db)
    _mapping(db)  # BEP 행 없음 → 상품파생·계정기본값 전부 None
    _snap(db, 20, 95_000, 100_000)
    d = budget_pacing.evaluate(db, now=NOW)[0]
    assert d["needs_raise"] is False
    assert "target_roas 미해결" in d["reason"]


def test_fail_closed_zero_cost_today(db):
    """소진 0인데 예산도 0<... 트리거는 못 넘지만, 예산 미소진 분모 방어를 직접 확인."""
    _settings(db)
    _mapping(db)
    _bep(db)
    _snap(db, 20, 0, 100_000)
    d = budget_pacing.evaluate(db, now=NOW)[0]
    assert d["needs_raise"] is False


def test_fail_closed_zero_spend_rate(db):
    """소진율은 90% 넘었지만 최근 3시간 증분이 0(소진 정지) — 추가 필요분 없음."""
    _settings(db)
    _mapping(db)
    _bep(db, target_roas="2.0")
    _snap(db, 17, 95_000, 100_000)
    _snap(db, 20, 95_000, 100_000)
    _order(db, 250_000)
    d = budget_pacing.evaluate(db, now=NOW)[0]
    assert d["needs_raise"] is False
    assert "소진 속도" in d["reason"]


def test_hold_when_proxy_roas_below_target(db):
    _ready(db, revenue=100_000, target="2.0")  # 100,000/95,000 = 1.0526 < 2.0
    d = budget_pacing.evaluate(db, now=NOW)[0]
    assert d["needs_raise"] is False
    assert "프록시 ROAS" in d["reason"]
    assert d["proxy_roas"] == Decimal("1.0526")


def test_proxy_revenue_excludes_cancelled_and_other_days(db):
    _ready(db, revenue=250_000)
    _order(db, 999_999, status="cancelled", n=2)
    _order(db, 888_888, when=datetime.combine(TODAY - timedelta(days=1), datetime.min.time()), n=3)
    _order(db, 777_777, cpid="other-product", n=4)
    assert budget_pacing.today_proxy_revenue(db, [CPID], TODAY)[0] == 250_000


def test_no_raise_when_kill_switch_off(db):
    _ready(db)
    db.query(NaverCampaignSettings).update({"auto_operate": False})
    db.commit()
    assert budget_pacing.evaluate(db, now=NOW) == []


# ══════════════════════ SA: 증액 산출 산식 ══════════════════════
def test_sizing_formula_exact(db):
    """rate 5,000원/시 × 잔여 3.667시간 × 1.2 = 22,000원 → 95,000+22,000=117,000."""
    _ready(db, cost=95_000)
    d = budget_pacing.evaluate(db, now=NOW)[0]
    assert d["spend_rate"] == Decimal("5000")
    assert d["remaining_hours"] == Decimal(24) - (Decimal(20) + Decimal(20) / Decimal(60))
    # 5000 × 11/3 × 1.2 = 22,000
    assert d["extra_needed"] == Decimal("22000")
    assert d["target_budget"] == 117_000


def test_sizing_rounds_up_to_100(db):
    _ready(db, cost=95_001)
    d = budget_pacing.evaluate(db, now=NOW)[0]
    assert d["target_budget"] % 100 == 0
    assert d["target_budget"] > 95_001


def test_spend_rate_falls_back_to_day_average_with_single_snapshot(db):
    _settings(db)
    _mapping(db)
    _bep(db)
    _snap(db, 20, 95_000, 100_000)
    _order(db, 250_000)
    d = budget_pacing.evaluate(db, now=NOW)[0]
    # 95,000 ÷ 21시간 ≈ 4,523.8원/시간(폴백 명시)
    assert "하루 평균 폴백" in d["spend_rate_note"]
    assert d["needs_raise"] is True


def test_remaining_hours_shrinks_through_the_day(db):
    assert budget_pacing.remaining_hours(datetime(2026, 7, 27, 0, 0)) == Decimal(24)
    assert budget_pacing.remaining_hours(NOW) == Decimal(24) - (Decimal(20) + Decimal(20) / Decimal(60))
    assert budget_pacing.remaining_hours(datetime(2026, 7, 27, 23, 0)) == Decimal(1)


def test_no_raise_late_at_night_when_extra_need_is_negligible(db):
    """23:59에는 잔여 시간이 거의 없어 추가 필요분이 현재 예산을 못 넘는다 — 증액 안 함."""
    _ready(db, cost=95_000)
    d = budget_pacing.evaluate(db, now=datetime(2026, 7, 27, 23, 59))[0]
    assert d["needs_raise"] is False
    assert "증액 여지 없음" in d["reason"]


# ══════════════════════ SA: base 예산 · +100% 캡 ══════════════════════
def test_target_is_capped_at_base_times_two(db):
    """소진 속도가 폭발적이어도 base×2를 못 넘는다."""
    _settings(db, base_daily_budget=None)
    _mapping(db)
    _bep(db)
    _snap(db, 17, 20_000, 100_000)
    _snap(db, 20, 95_000, 100_000)  # 25,000원/시간
    _order(db, 400_000)
    d = budget_pacing.evaluate(db, now=NOW)[0]
    assert d["base_budget"] == 100_000
    assert d["target_budget"] == 200_000  # min(95,000+110,000=205,000 → 캡 200,000)


def test_no_raise_when_already_at_cap(db):
    """오늘 이미 base(100,000)의 2배까지 올려놨으면 더 안 올린다."""
    _settings(db, base_daily_budget=100_000)
    _mapping(db)
    _bep(db)
    _snap(db, 17, 150_000, 200_000)
    _snap(db, 20, 195_000, 200_000)
    _order(db, 500_000)
    _pacing_log(db, changed_at=datetime(2026, 7, 27, 15, 20))  # 오늘 BP 증액 이력
    d = budget_pacing.evaluate(db, now=NOW)[0]
    assert d["base_budget"] == 100_000  # 재시드 금지(복리 차단)
    assert d["needs_raise"] is False
    assert "증액 캡 도달" in d["reason"]


def test_base_is_reseeded_when_no_raise_today(db):
    """사람이 콘솔에서 예산을 바꿔놨으면 그날 첫 평가가 그 값을 새 base로 흡수한다."""
    _settings(db, base_daily_budget=50_000)  # 어제 base(stale)
    _mapping(db)
    _bep(db)
    _snap(db, 20, 190_000, 200_000)
    _order(db, 600_000)
    d = budget_pacing.evaluate(db, now=NOW)[0]
    assert d["base_budget"] == 200_000
    assert d["base_seeded"] is True


def test_base_kept_when_raised_today(db):
    _settings(db, base_daily_budget=100_000)
    _mapping(db)
    _bep(db)
    _snap(db, 17, 100_000, 150_000)
    _snap(db, 20, 140_000, 150_000)
    _order(db, 400_000)
    _pacing_log(db, changed_at=datetime(2026, 7, 27, 15, 20))
    d = budget_pacing.evaluate(db, now=NOW)[0]
    assert d["base_budget"] == 100_000
    assert d["base_seeded"] is False
    # 캡 200,000 안에서 추가 증액 가능(같은 날 재발동 허용)
    assert d["needs_raise"] is True
    assert d["target_budget"] <= 200_000


def test_restored_yesterday_raise_allows_reseed(db):
    """어제 증액이 **원복까지 끝났으면** 오늘의 현재 예산이 정상 base로 재시드된다."""
    _settings(db, base_daily_budget=100_000)
    _mapping(db)
    _bep(db)
    _snap(db, 20, 140_000, 150_000)
    _order(db, 400_000)
    _pacing_log(db, changed_at=datetime(2026, 7, 26, 15, 20))
    _pacing_log(db, changed_at=datetime(2026, 7, 27, 0, 5),
                action=budget_pacing.ACTION_BUDGET_DOWN_PACING, after='{"dailyBudget": 100000}')
    d = budget_pacing.evaluate(db, now=NOW)[0]
    assert d["base_budget"] == 150_000  # 사람이 150,000으로 올려둔 상태를 흡수
    assert d["base_seeded"] is True


# ══════════════════════ P1-1 회귀: 원복 실패 래칫 방어 ══════════════════════
def test_unrestored_raise_blocks_reseed(db):
    """★리뷰 P1-1: 어제 증액 + 원복 실패 상태에서 다음 날 첫 평가가 부풀린 예산을 base로
    흡수하면(래칫) 원복 후보가 영구 소멸하고 캡 기준선도 굳는다 — 재시드 금지."""
    _settings(db, base_daily_budget=100_000)
    _mapping(db)
    _bep(db)
    _snap(db, 20, 140_000, 150_000)  # 어제 증액분 150,000이 그대로 남아 있음
    _order(db, 400_000)
    _pacing_log(db, changed_at=datetime(2026, 7, 26, 23, 20))  # 어제 증액, 원복 없음
    d = budget_pacing.evaluate(db, now=NOW)[0]
    assert d["base_budget"] == 100_000
    assert d["base_seeded"] is False
    assert "미복원" in d["base_keep_reason"]
    # 원복 후보도 살아 있어야 한다(소멸 금지)
    assert [c["campaign_id"] for c in budget_pacing.restore_candidates(db, now=NOW)] == [CAMPAIGN]


def test_A1_restore_api_failure_keeps_base_and_candidate_until_retry_succeeds(db):
    """A1: D1 증액 → D2 원복이 get_campaign 예외로 실패 → D2·D3 base 불변·후보 유지 →
    재시도 성공 시 후보 소멸. (복리 없음 — 캡 기준선이 100,000 그대로)"""
    from app.services.naver_ad import naver_sa_writer as writer

    _settings(db, base_daily_budget=100_000)
    _mapping(db)
    _bep(db)
    _snap(db, 23, 145_000, 150_000, ad_date=date(2026, 7, 26))
    _pacing_log(db, changed_at=datetime(2026, 7, 26, 20, 20))

    # D2 00:05 원복 시도 → 라이브 재조회 실패(fail-closed)
    d2 = datetime(2026, 7, 27, 0, 5)
    result = _new_result()
    with patch.object(auto_operator.naver_execution_harness, "compute_correction_factor",
                      return_value={"factor": Decimal("1"), "factor_low": Decimal("1"), "factor_high": Decimal("1"), "factor_point": Decimal("1")}), \
         patch.object(auto_operator.naver_execution_harness.naver_sa_writer, "get_campaign",
                      side_effect=RuntimeError("network")), \
         patch.object(auto_operator.naver_execution_harness.naver_sa_writer,
                      "update_campaign_budget") as mock_write:
        auto_operator._run_budget_pacing_restore(db, d2, result)
    assert mock_write.call_count == 0
    assert result["budget_pacing_restore_failed"] == 1

    # D2 낮 평가: base 재시드 금지 + 후보 유지
    _snap(db, 12, 10_000, 150_000, ad_date=date(2026, 7, 27))
    d = budget_pacing.evaluate(db, now=datetime(2026, 7, 27, 12, 20))[0]
    assert d["base_budget"] == 100_000 and d["base_seeded"] is False
    assert len(budget_pacing.restore_candidates(db, now=datetime(2026, 7, 27, 12, 20))) == 1

    # D3에도 여전히 유지
    d3 = datetime(2026, 7, 28, 0, 5)
    assert len(budget_pacing.restore_candidates(db, now=d3)) == 1

    # D3 재시도 성공 → 후보 소멸
    write_result = writer.WriteResult(
        action="update_budget", before={"dailyBudget": 150_000}, response={},
        after={"dailyBudget": 100_000}, created_ids=[],
    )
    result = _new_result()
    with patch.object(auto_operator.naver_execution_harness, "compute_correction_factor",
                      return_value={"factor": Decimal("1"), "factor_low": Decimal("1"), "factor_high": Decimal("1"), "factor_point": Decimal("1")}), \
         patch.object(auto_operator.naver_execution_harness.naver_sa_writer, "get_campaign",
                      return_value={"dailyBudget": 150_000, "sharedBudgetId": None}), \
         patch.object(auto_operator.naver_execution_harness.naver_sa_writer,
                      "update_campaign_budget", return_value=write_result) as mock_write:
        auto_operator._run_budget_pacing_restore(db, d3, result)
    assert mock_write.call_args[0] == (CAMPAIGN, 100_000)
    assert result["budget_pacing_restored"] == 1
    assert budget_pacing.restore_candidates(db, now=datetime(2026, 7, 28, 1, 5)) == []


def test_A3_cooldown_blocked_restore_retries_successfully_next_hour(db):
    """A3: 23:20 증액 → 00:20 원복이 가드레일 쿨다운 2h로 차단 → 01:20 재시도 성공.
    ★쿨다운 차단 뒤에도 base가 재시드되지 않아야 후보가 살아남는다(P1-1 불변식)."""
    from app.services.naver_ad import naver_sa_writer as writer

    _settings(db, base_daily_budget=100_000)
    _mapping(db)
    _bep(db)
    _snap(db, 23, 145_000, 150_000, ad_date=date(2026, 7, 26))
    _pacing_log(db, changed_at=datetime(2026, 7, 26, 23, 20))

    # 00:20 — 마지막 변경(어제 23:20)에서 1시간 경과 → 쿨다운 2h 차단
    t1 = datetime(2026, 7, 27, 0, 20)
    result = _new_result()
    with patch.object(auto_operator.naver_execution_harness, "compute_correction_factor",
                      return_value={"factor": Decimal("1"), "factor_low": Decimal("1"), "factor_high": Decimal("1"), "factor_point": Decimal("1")}), \
         patch.object(auto_operator.naver_execution_harness.naver_sa_writer, "get_campaign",
                      return_value={"dailyBudget": 150_000, "sharedBudgetId": None}), \
         patch.object(auto_operator.naver_execution_harness.naver_sa_writer,
                      "update_campaign_budget") as mock_write:
        auto_operator._run_budget_pacing_restore(db, t1, result)
    assert mock_write.call_count == 0
    assert result["budget_pacing_restore_failed"] == 1
    assert "쿨다운" in (db.query(NaverChangeLog).order_by(NaverChangeLog.id.desc()).first().rationale or "")

    # 01:20 재시도 — 쿨다운 해제(2h 경과) → 성공. 그 사이 평가가 돌아도 base 불변.
    _snap(db, 1, 500, 150_000, ad_date=date(2026, 7, 27))
    mid = budget_pacing.evaluate(db, now=datetime(2026, 7, 27, 1, 20))[0]
    assert mid["base_budget"] == 100_000 and mid["base_seeded"] is False

    write_result = writer.WriteResult(
        action="update_budget", before={"dailyBudget": 150_000}, response={},
        after={"dailyBudget": 100_000}, created_ids=[],
    )
    result = _new_result()
    with patch.object(auto_operator.naver_execution_harness, "compute_correction_factor",
                      return_value={"factor": Decimal("1"), "factor_low": Decimal("1"), "factor_high": Decimal("1"), "factor_point": Decimal("1")}), \
         patch.object(auto_operator.naver_execution_harness.naver_sa_writer, "get_campaign",
                      return_value={"dailyBudget": 150_000, "sharedBudgetId": None}), \
         patch.object(auto_operator.naver_execution_harness.naver_sa_writer,
                      "update_campaign_budget", return_value=write_result) as mock_write:
        auto_operator._run_budget_pacing_restore(db, datetime(2026, 7, 27, 1, 25), result)
    assert result["budget_pacing_restored"] == 1
    assert mock_write.call_args[0] == (CAMPAIGN, 100_000)
    # 복리 없음: 원복 후 base는 여전히 100,000이고 캡도 200,000
    assert db.query(NaverCampaignSettings).one().base_daily_budget == 100_000


def test_H1_manual_budget_change_after_failed_restore_is_not_reverted(db):
    """★리뷰 R2 P2-R2-1(H1): 원복 실패 뒤 Jino가 콘솔에서 50,000→200,000으로 올린 상황.

    change_log 순서만 보면 200,000을 '우리 미복원 증액'으로 오인해 되돌린다(사람 조작 침범).
    증액 행의 after_value(150,000)와 현재 예산(200,000)이 다르므로 우리 증액은 더 이상
    서 있지 않다 → ①원복 후보 제외 ②base를 200,000으로 정상 재시드(교착 해소)."""
    _settings(db, base_daily_budget=100_000)
    _mapping(db)
    _bep(db)
    _pacing_log(db, changed_at=datetime(2026, 7, 26, 20, 20), after='{"dailyBudget": 150000}')
    # 원복 실패(성공 budget_down_pacing 없음) + 사람이 200,000으로 수동 변경
    _snap(db, 12, 10_000, 200_000)

    assert budget_pacing.restore_candidates(db, now=datetime(2026, 7, 27, 12, 20)) == []
    d = budget_pacing.evaluate(db, now=datetime(2026, 7, 27, 12, 20))[0]
    assert d["base_budget"] == 200_000
    assert d["base_seeded"] is True
    assert d["base_keep_reason"] == ""


def test_manual_change_detection_does_not_weaken_ratchet_defense(db):
    """우리 증액값이 그대로 서 있으면(사람 개입 없음) 래칫 방어는 그대로 작동한다."""
    _settings(db, base_daily_budget=100_000)
    _mapping(db)
    _bep(db)
    _pacing_log(db, changed_at=datetime(2026, 7, 26, 20, 20), after='{"dailyBudget": 150000}')
    _snap(db, 12, 10_000, 150_000)  # 우리가 설정한 값 그대로
    assert len(budget_pacing.restore_candidates(db, now=datetime(2026, 7, 27, 12, 20))) == 1
    d = budget_pacing.evaluate(db, now=datetime(2026, 7, 27, 12, 20))[0]
    assert d["base_budget"] == 100_000 and d["base_seeded"] is False


def test_unparseable_after_value_stays_conservative(db):
    """after_value를 못 읽으면 대조를 포기하고 보수적으로 '미복원'으로 남긴다."""
    _settings(db, base_daily_budget=100_000)
    _mapping(db)
    _bep(db)
    _pacing_log(db, changed_at=datetime(2026, 7, 26, 20, 20), after="not-json")
    _snap(db, 12, 10_000, 200_000)
    assert len(budget_pacing.restore_candidates(db, now=datetime(2026, 7, 27, 12, 20))) == 1
    d = budget_pacing.evaluate(db, now=datetime(2026, 7, 27, 12, 20))[0]
    assert d["base_budget"] == 100_000 and "미복원" in d["base_keep_reason"]


def test_restore_candidate_survives_stale_snapshot_at_midnight(db):
    """★리뷰 P2-1: 23:20 증액 → 00:05 리셋이 보는 최신 스냅샷은 23:05분 것(증액 전 예산).
    그 관측으로 'observed ≤ base'라고 판단해 skip하면 리셋 잡이 영원히 헛돈다."""
    _settings(db, base_daily_budget=100_000)
    _snap(db, 23, 99_000, 100_000, ad_date=date(2026, 7, 26))  # 증액 전 값
    _pacing_log(db, changed_at=datetime(2026, 7, 26, 23, 20))
    cands = budget_pacing.restore_candidates(db, now=datetime(2026, 7, 27, 0, 5))
    assert len(cands) == 1
    assert cands[0]["observed_stale"] is True
    assert "stale" in cands[0]["reason"]


# ══════════════════════ P2-2 프록시 최소 볼륨 게이트 ══════════════════════
def test_thin_sample_natural_sales_cannot_pass_roas_gate(db):
    """★리뷰 P2-2 재현: 2,900원 소진 + 자연판매 50만 → ROAS 172로 통과하던 구멍."""
    _settings(db)
    _mapping(db)
    _bep(db, target_roas="2.0")
    _snap(db, 20, 2_900, 3_000, clk=2)  # 소진율 96.7%(트리거는 통과)
    _order(db, 500_000)
    d = budget_pacing.evaluate(db, now=NOW)[0]
    assert d["needs_raise"] is False
    assert "표본 미달" in d["reason"]
    assert d["proxy_roas"] is None  # ROAS를 계산조차 하지 않는다


def test_volume_gate_clicks_boundary(db):
    _settings(db)
    _mapping(db)
    _bep(db, target_roas="2.0")
    _snap(db, 17, 80_000, 100_000, clk=4)
    _snap(db, 20, 95_000, 100_000, clk=4)  # 클릭 4 < 5
    _order(db, 250_000)
    assert budget_pacing.evaluate(db, now=NOW)[0]["needs_raise"] is False
    db.query(NaverHourlySnapshot).update({"clk": budget_pacing.MIN_CLICKS_FOR_PROXY})
    db.commit()
    assert budget_pacing.evaluate(db, now=NOW)[0]["needs_raise"] is True


def test_volume_gate_cost_boundary(db):
    _settings(db)
    _mapping(db)
    _bep(db, target_roas="2.0")
    _snap(db, 20, 4_999, 5_000, clk=20)  # 소진 4,999 < 5,000
    _order(db, 250_000)
    d = budget_pacing.evaluate(db, now=NOW)[0]
    assert d["needs_raise"] is False and "표본 미달" in d["reason"]
    assert budget_pacing.MIN_COST_FOR_PROXY == 5_000


# ══════════════════════ P2-3 상품 공유 캠페인 이중계상 ══════════════════════
def test_shared_product_revenue_is_split_between_campaigns(db):
    """같은 상품이 auto_operate 캠페인 2곳에 매핑되면 당일 매출을 균등 분할한다."""
    _settings(db)
    _settings(db, campaign_id="cmp-bp2")
    _mapping(db)
    _mapping(db, campaign_id="cmp-bp2", adgroup_id="grp-2")  # 같은 CPID
    _bep(db, target_roas="2.0")
    _snap(db, 17, 80_000, 100_000)
    _snap(db, 20, 95_000, 100_000)
    _snap(db, 17, 80_000, 100_000, campaign_id="cmp-bp2")
    _snap(db, 20, 95_000, 100_000, campaign_id="cmp-bp2")
    _order(db, 300_000)

    counts = budget_pacing.product_campaign_counts(db, [CAMPAIGN, "cmp-bp2"])
    assert counts[CPID] == 2
    revenue, shared = budget_pacing.today_proxy_revenue(db, [CPID], TODAY, shares=counts)
    assert revenue == 150_000  # 300,000 ÷ 2
    assert shared == [f"{CPID}÷2"]

    for d in budget_pacing.evaluate(db, now=NOW):
        assert d["proxy_revenue"] == 150_000
        assert d["proxy_shared_products"] == [f"{CPID}÷2"]
        assert "균등분할" in d["reason"]


def test_unshared_product_is_not_split(db):
    _ready(db, revenue=250_000)
    d = budget_pacing.evaluate(db, now=NOW)[0]
    assert d["proxy_revenue"] == 250_000
    assert d["proxy_shared_products"] == []


# ══════════════════════ P2-4 계정 일일 총량 캡 ══════════════════════
def test_raised_amount_today_sums_deltas(db):
    _settings(db)
    _pacing_log(db, changed_at=datetime(2026, 7, 27, 10, 0), after='{"dailyBudget": 130000}')
    _pacing_log(db, changed_at=datetime(2026, 7, 27, 14, 0), after='{"dailyBudget": 160000}')
    _pacing_log(db, changed_at=datetime(2026, 7, 26, 14, 0), after='{"dailyBudget": 900000}')
    # before는 헬퍼 기본 100,000 → Δ 30,000 + 60,000 (어제분 제외)
    assert budget_pacing.raised_amount_today(db, today=TODAY) == 90_000


def test_lane_blocks_when_account_daily_cap_exhausted(db):
    _ready(db)
    _pacing_log(db, changed_at=datetime(2026, 7, 27, 10, 0), after='{"dailyBudget": 200000}')
    result = _new_result()
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        auto_operator._run_budget_pacing_lane(db, NOW, result)
    assert mock_exec.call_count == 0
    assert any("일일 총량 캡 소진" in h["reason"] for h in result["budget_pacing_held"])
    assert result["budget_pacing_daily_left"] <= 0


def test_lane_partial_daily_cap_left(db):
    """오늘 이미 90,000 증액 → 잔여 10,000. 22,000 증액 후보는 배정 불가."""
    _ready(db)
    _pacing_log(db, changed_at=datetime(2026, 7, 27, 10, 0), after='{"dailyBudget": 190000}')
    result = _new_result()
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        auto_operator._run_budget_pacing_lane(db, NOW, result)
    assert mock_exec.call_count == 0
    assert result["budget_pacing_daily_left"] == 10_000
    assert any("증액 한도 초과" in h["reason"] for h in result["budget_pacing_held"])


def test_round_cap_respects_explicit_cap():
    cands = [{"campaign_id": "a", "current_budget": 100_000, "target_budget": 130_000}]
    budget_pacing.apply_round_cap(cands, cap=20_000)
    assert cands[0]["round_eligible"] is False
    budget_pacing.apply_round_cap(cands, cap=30_000)
    assert cands[0]["round_eligible"] is True


# ══════════════════════ SA: 라운드 봉투 ══════════════════════
def test_round_cap_greedy_allocation():
    cands = [
        {"campaign_id": "a", "current_budget": 100_000, "target_budget": 160_000},  # Δ60,000
        {"campaign_id": "b", "current_budget": 100_000, "target_budget": 150_000},  # Δ50,000
        {"campaign_id": "c", "current_budget": 100_000, "target_budget": 130_000},  # Δ30,000
    ]
    budget_pacing.apply_round_cap(cands)
    by_id = {c["campaign_id"]: c["round_eligible"] for c in cands}
    assert by_id["a"] is True                 # 60,000 누적
    assert by_id["b"] is False                # +50,000 = 110,000 > 10만 → 제외
    assert by_id["c"] is True                 # 60,000+30,000 = 90,000 ≤ 10만
    assert budget_pacing.ROUND_BUDGET_CAP == 100_000


# ══════════════════════ SA: 익일 원복 ══════════════════════
def test_restore_candidate_detected_next_day(db):
    _settings(db, base_daily_budget=100_000)
    _pacing_log(db, changed_at=datetime(2026, 7, 26, 20, 20))
    _snap(db, 23, 150_000, 150_000, ad_date=date(2026, 7, 26))
    cands = budget_pacing.restore_candidates(db, now=datetime(2026, 7, 27, 0, 5))
    assert len(cands) == 1
    assert cands[0]["base_budget"] == 100_000
    assert cands[0]["observed_budget"] == 150_000


def test_restore_skipped_when_raise_is_today(db):
    _settings(db, base_daily_budget=100_000)
    _pacing_log(db, changed_at=datetime(2026, 7, 27, 15, 20))
    _snap(db, 20, 140_000, 150_000)
    assert budget_pacing.restore_candidates(db, now=NOW) == []


def test_restore_skipped_when_already_restored(db):
    _settings(db, base_daily_budget=100_000)
    _pacing_log(db, changed_at=datetime(2026, 7, 26, 20, 20))
    _pacing_log(db, changed_at=datetime(2026, 7, 27, 0, 5),
                action=budget_pacing.ACTION_BUDGET_DOWN_PACING, after='{"dailyBudget": 100000}')
    _snap(db, 23, 150_000, 150_000, ad_date=date(2026, 7, 26))
    assert budget_pacing.restore_candidates(db, now=datetime(2026, 7, 27, 1, 5)) == []


def test_restore_skipped_when_budget_already_at_base(db):
    _settings(db, base_daily_budget=100_000)
    _pacing_log(db, changed_at=datetime(2026, 7, 26, 20, 20))
    _snap(db, 23, 90_000, 100_000, ad_date=date(2026, 7, 26))  # 사람이 이미 내림
    assert budget_pacing.restore_candidates(db, now=datetime(2026, 7, 27, 0, 5)) == []


def test_restore_skipped_when_base_never_seeded(db):
    _settings(db, base_daily_budget=None)
    _pacing_log(db, changed_at=datetime(2026, 7, 26, 20, 20))
    assert budget_pacing.restore_candidates(db, now=datetime(2026, 7, 27, 0, 5)) == []


# ══════════════════════ 하니스: 배선·집행 ══════════════════════
def test_lane_creates_approved_proposal_and_executes(db):
    _ready(db)
    result = _new_result()
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        auto_operator._run_budget_pacing_lane(db, NOW, result)
    assert result["budget_pacing_raised"] == 1
    assert mock_exec.call_count == 1
    assert mock_exec.call_args.kwargs["dry_run"] is False
    p = db.query(NaverProposal).one()
    assert p.proposal_type == "budget_up"
    assert p.target_type == "campaign" and p.target_id == CAMPAIGN
    assert p.status == "approved"
    assert p.approval_source == budget_pacing.APPROVAL_SOURCE_PACING
    assert p.target_budget == 117_000
    assert p.rationale.startswith(budget_pacing.BUDGET_PACING_PREFIX)
    # 근거 3요소가 rationale에 들어있는가(소진율·프록시 ROAS·소진속도)
    assert "소진율" in p.rationale and "프록시 ROAS" in p.rationale and "원/시간" in p.rationale
    assert "상한 프록시" in p.rationale  # 정직 경계 명시


def test_lane_persists_base_daily_budget(db):
    _ready(db)
    with patch.object(auto_operator.naver_execution_harness, "execute"):
        auto_operator._run_budget_pacing_lane(db, NOW, _new_result())
    s = db.query(NaverCampaignSettings).one()
    assert s.base_daily_budget == 100_000


def test_lane_dry_run_flag(db, monkeypatch):
    _ready(db)
    monkeypatch.setenv("NAVER_BP_DRY_RUN", "1")
    result = _new_result()
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        auto_operator._run_budget_pacing_lane(db, NOW, result)
    assert mock_exec.call_args.kwargs["dry_run"] is True
    assert result["budget_pacing_dry_run"] == 1
    assert result["budget_pacing_raised"] == 0


def test_lane_blocks_on_kill_switch_flipped_mid_run(db):
    _ready(db)
    with patch.object(auto_operator, "_auto_operate_now", return_value=False), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = _new_result()
        auto_operator._run_budget_pacing_lane(db, NOW, result)
    assert mock_exec.call_count == 0
    assert result["budget_pacing_raised"] == 0
    assert any("킬스위치" in h["reason"] for h in result["budget_pacing_held"])
    assert db.query(NaverProposal).count() == 0


def test_lane_blocks_on_spend_circuit_breaker(db):
    """당일 소진이 직전 7일 일평균×3을 넘으면 천장을 더 열지 않는다."""
    _ready(db, cost=95_000)
    for i in range(1, 8):
        _daily(db, TODAY - timedelta(days=i), 10_000, 30_000)  # 7일 일평균 10,000 → ×3=30,000
    result = _new_result()
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        auto_operator._run_budget_pacing_lane(db, NOW, result)
    assert mock_exec.call_count == 0
    assert any("서킷브레이커" in h["reason"] for h in result["budget_pacing_held"])


def test_lane_skips_round_cap_overflow(db):
    """한 회차 총 증액이 10만원을 넘는 후보는 이번 회차에 발사하지 않는다(다음 정시 재평가)."""
    _ready(db, cost=95_000)
    # 두 번째 캠페인 — 각각 22,000/90,000 증액 후보
    _settings(db, campaign_id="cmp-bp2")
    _mapping(db, campaign_id="cmp-bp2", cpid="2000002", adgroup_id="grp-2")
    _bep(db, cpid="2000002")
    _snap(db, 17, 20_000, 100_000, campaign_id="cmp-bp2")
    _snap(db, 20, 95_000, 100_000, campaign_id="cmp-bp2")  # 25,000원/시간 → Δ~100,000
    _order(db, 400_000, cpid="2000002", n=9)
    result = _new_result()
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        auto_operator._run_budget_pacing_lane(db, NOW, result)
    assert mock_exec.call_count == 1  # 큰 쪽만 배정, 작은 쪽은 봉투 초과로 skip
    assert any("증액 한도 초과" in h["reason"] for h in result["budget_pacing_held"])


def test_lane_restores_base_next_day(db):
    _settings(db, base_daily_budget=100_000)
    _pacing_log(db, changed_at=datetime(2026, 7, 26, 20, 20))
    _snap(db, 23, 150_000, 150_000, ad_date=date(2026, 7, 26))
    result = _new_result()
    with patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        auto_operator._run_budget_pacing_restore(db, datetime(2026, 7, 27, 0, 5), result)
    assert result["budget_pacing_restored"] == 1
    assert mock_exec.call_count == 1
    p = db.query(NaverProposal).one()
    assert p.proposal_type == "budget_down"
    assert p.target_budget == 100_000
    assert p.rationale.startswith(budget_pacing.BUDGET_PACING_RESTORE_PREFIX)


def test_hourly_lane_wires_budget_pacing(db):
    """run_hourly_lane 말미에 BP 레인이 실제로 배선돼 있는가(fail-soft 포함)."""
    _ready(db)
    with patch.object(auto_operator, "_run_budget_pacing_lane") as mock_bp, \
         patch.object(auto_operator, "_auto_operate_campaigns", return_value=[]):
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda *_a, **_k: [])
    assert mock_bp.call_count == 1
    assert "budget_pacing_raised" in result


def test_hourly_lane_bp_failure_is_fail_soft(db):
    _ready(db)
    with patch.object(auto_operator, "_run_budget_pacing_lane", side_effect=RuntimeError("boom")), \
         patch.object(auto_operator, "_auto_operate_campaigns", return_value=[]):
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda *_a, **_k: [])
    assert result["budget_pacing_raised"] == 0  # 예외가 레인을 죽이지 않는다


# ══════════════════════ 가드레일 관통(통합) ══════════════════════
def _seed_guardrail_window(db, *, conv_amt):
    """가드레일 창(D-1 기준 7일)의 캠페인 실적. 일 10만원 지출이라 소진 서킷브레이커
    (7일 일평균×3 = 30만)에는 걸리지 않는다(당일 9.5만)."""
    for i in range(1, 8):
        _daily(db, TODAY - timedelta(days=i), 100_000, conv_amt)


def test_real_guardrail_blocks_raise_when_campaign_has_no_conversion(db):
    """스톱로스(무전환 캠페인 증액 금지) — harness.execute를 실제로 태워 관통 확인.

    보정계수만 1.0으로 고정(diagnosis.correction_factor는 계정 전역 창을 훑는 별개 로직 —
    이 테스트의 관심사는 guardrail_gate._check_budget의 판정이다)."""
    _ready(db)
    _seed_guardrail_window(db, conv_amt=0)
    result = _new_result()
    with patch.object(auto_operator.naver_execution_harness, "compute_correction_factor",
                      return_value={"factor": Decimal("1"), "factor_low": Decimal("1"), "factor_high": Decimal("1"), "factor_point": Decimal("1")}), \
         patch.object(auto_operator.naver_execution_harness.naver_sa_writer, "get_campaign",
                      return_value={"dailyBudget": 100_000, "sharedBudgetId": None}), \
         patch.object(auto_operator.naver_execution_harness.naver_sa_writer,
                      "update_campaign_budget") as mock_write:
        auto_operator._run_budget_pacing_lane(db, NOW, result)
    assert mock_write.call_count == 0  # 실쓰기 0
    assert result["budget_pacing_failed"] == 1
    p = db.query(NaverProposal).one()
    assert p.status == "failed"
    log_row = db.query(NaverChangeLog).one()
    assert log_row.action == budget_pacing.ACTION_BUDGET_UP_PACING
    assert "스톱로스" in (log_row.rationale or "")


def test_real_guardrail_blocks_raise_when_roas_below_target(db):
    """BEP 이익하한 — 보정 ROAS(1.0) < 목표(2.0)면 증액 금지."""
    _ready(db)
    _seed_guardrail_window(db, conv_amt=100_000)  # ROAS 1.0
    result = _new_result()
    with patch.object(auto_operator.naver_execution_harness, "compute_correction_factor",
                      return_value={"factor": Decimal("1"), "factor_low": Decimal("1"), "factor_high": Decimal("1"), "factor_point": Decimal("1")}), \
         patch.object(auto_operator.naver_execution_harness.naver_sa_writer, "get_campaign",
                      return_value={"dailyBudget": 100_000, "sharedBudgetId": None}), \
         patch.object(auto_operator.naver_execution_harness.naver_sa_writer,
                      "update_campaign_budget") as mock_write:
        auto_operator._run_budget_pacing_lane(db, NOW, result)
    assert mock_write.call_count == 0
    assert result["budget_pacing_failed"] == 1
    assert "BEP 미달" in (db.query(NaverChangeLog).one().rationale or "")


def test_real_write_records_budget_up_pacing_action(db):
    """가드레일 전량 통과 시 change_log.action='budget_up_pacing'으로 기록된다(D-NAO-102 ⑥)."""
    from app.services.naver_ad import naver_sa_writer as writer

    _ready(db)
    _seed_guardrail_window(db, conv_amt=300_000)  # ROAS 3.0 ≥ 목표 2.0
    write_result = writer.WriteResult(
        action="update_budget", before={"dailyBudget": 100_000}, response={},
        after={"dailyBudget": 117_000}, created_ids=[],
    )
    result = _new_result()
    with patch.object(auto_operator.naver_execution_harness, "compute_correction_factor",
                      return_value={"factor": Decimal("1"), "factor_low": Decimal("1"), "factor_high": Decimal("1"), "factor_point": Decimal("1")}), \
         patch.object(auto_operator.naver_execution_harness.naver_sa_writer, "get_campaign",
                      return_value={"dailyBudget": 100_000, "sharedBudgetId": None}), \
         patch.object(auto_operator.naver_execution_harness.naver_sa_writer,
                      "update_campaign_budget", return_value=write_result) as mock_write:
        auto_operator._run_budget_pacing_lane(db, NOW, result)
    assert mock_write.call_args[0] == (CAMPAIGN, 117_000)
    assert result["budget_pacing_raised"] == 1
    log_row = db.query(NaverChangeLog).one()
    assert log_row.action == budget_pacing.ACTION_BUDGET_UP_PACING
    assert log_row.dry_run is False
    assert log_row.after_value == '{"dailyBudget": 117000}'
    # 다음 평가에서 base 재시드가 차단된다(복리 방지)
    assert budget_pacing.campaigns_raised_today(db, today=TODAY) == {CAMPAIGN}


def test_non_pacing_budget_write_keeps_update_budget_action(db):
    """봉투·콘솔 경로(approval_source≠pace_op)는 기존 action='update_budget' 그대로."""
    from app.services.naver_ad import naver_execution_harness as harness

    _settings(db)
    p = NaverProposal(
        proposal_type="budget_up", target_type="campaign", target_id=CAMPAIGN,
        campaign_id=CAMPAIGN, rationale="[예산봉투] 램프", status="approved",
        target_budget=120_000,
    )
    db.add(p)
    db.commit()
    assert harness._budget_log_action(p) == "update_budget"
    p.approval_source = budget_pacing.APPROVAL_SOURCE_PACING
    assert harness._budget_log_action(p) == budget_pacing.ACTION_BUDGET_UP_PACING
    p.proposal_type = "budget_down"
    assert harness._budget_log_action(p) == budget_pacing.ACTION_BUDGET_DOWN_PACING


def test_reset_lane_public_entry(db):
    _settings(db, base_daily_budget=100_000)
    _pacing_log(db, changed_at=datetime(2026, 7, 26, 20, 20))
    _snap(db, 23, 150_000, 150_000, ad_date=date(2026, 7, 26))
    with patch.object(auto_operator.naver_execution_harness, "execute"):
        result = auto_operator.run_budget_pacing_reset_lane(db, now=datetime(2026, 7, 27, 0, 5))
    assert result["budget_pacing_restored"] == 1


def test_scheduler_registers_budget_pacing_reset_job(db):
    """00:05 크론이 기본 상태로 시드되고 job_func_for가 실제 함수를 돌려주는가."""
    from app.models import SchedulerState
    from app.services import scheduler_service

    assert scheduler_service.job_func_for("run_naver_budget_pacing_reset") is not None
    scheduler_service._ensure_default_states(db)
    row = db.query(SchedulerState).filter(
        SchedulerState.job_name == "run_naver_budget_pacing_reset"
    ).one()
    assert row.cron_expression == "5 0 * * *"
    assert row.is_enabled is True


def _new_result() -> dict:
    return {
        "budget_pacing_reviewed": 0, "budget_pacing_raised": 0, "budget_pacing_failed": 0,
        "budget_pacing_dry_run": 0, "budget_pacing_held": [], "budget_pacing_daily_left": None,
        "budget_pacing_restore_reviewed": 0, "budget_pacing_restored": 0,
        "budget_pacing_restore_failed": 0,
    }
