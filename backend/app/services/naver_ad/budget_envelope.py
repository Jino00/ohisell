# budget_envelope.py — 예산 봉투 순수 SA (스프린트 EX, D-NAO-87 §4-5)
# 역할(SA·순수·DB 읽기만): auto_operate 캠페인별로 "예산 봉투"(천장 개방 레버)를 계산한다.
#   봉투 = max(과거 30일(오늘 제외 D-30~D-1) NaverAdDaily 일평균 지출 × 1.5, 50,000원). 현재
#   일예산(daily_budget)이 봉투 미만이면 needs_raise=True + target_budget(봉투까지 램프, +100%캡
#   정합)을 낸다. **판정만** 하고 제안 생성/집행은 하지 않는다(제안 배선=proposal_pipeline,
#   자동 심사=auto_operator.run_daily_lane). 광고 API 호출 0.
#
#   ★자동 감액 절대 없음(§4-5): 봉투는 천장을 여는 레버지 조이는 레버가 아니다. current ≥ 봉투
#     (여유 없음)·uncapped(daily_budget=0)·미확보(daily_budget None)는 전부 needs_raise=False +
#     사유(관찰만). 감액은 기존 Confirm 경로가 담당(이 SA는 손대지 않는다).
#
#   ★current_budget 소스 = 당일 NaverHourlySnapshot 최신 행의 daily_budget(budget_allocator.
#     _latest_hourly_rows 관례 — 누적 스냅샷이라 최신=당일 확정 예산). 부재면 fail-closed
#     (needs_raise=False + 사유). 30일 일평균 = 창 내 관측일수(distinct ad_date)를 분모로 나눈다
#     (sentinel 행 제외 = 2배 집계 함정 방지).
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverCampaignSettings, NaverChangeLog
from app.services.naver_ad import budget_allocator
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

# rationale 접두(D-NAO-87) — 일 레인 자동 심사(auto_operator)가 이 접두로 봉투 budget_up만 골라
# 자동 승인·집행한다(비태그 budget_up은 Confirm 전용 불변). proposal_pipeline이 제안 생성 시 접두.
BUDGET_ENVELOPE_PREFIX = "[예산봉투]"

# ── 봉투 상수(PLAN §4-5·§6 실측) ──
_ENVELOPE_LOOKBACK_DAYS = 30          # 과거 30일 창(오늘 제외 D-30~D-1)
_ENVELOPE_MULTIPLE = Decimal("1.5")   # 일평균 × 1.5 — 봉투 상단(램프 목표)
_ENVELOPE_FLOOR = 50_000              # 바닥값(저지출 캠페인도 최소 5만원 봉투)
# +100% 캡 정합(guardrail_gate._BUDGET_UP_MAX_CHANGE_PCT=1.0 = current×2) — 봉투까지 여러 날에
# 걸쳐 스텝 램프(한 번에 봉투 직행 금지). 값의 단일 진실은 guardrail_gate이나(하드코딩 중복
# 금지) 여기선 배수만 로컬 상수로 두고 테스트가 정합을 고정한다.
_BUDGET_UP_MAX_MULTIPLE = 2


def _avg_spend_30d(db: Session, campaign_id: str, today: date) -> int:
    """과거 30일(오늘 제외 [today-30, today-1]) 캠페인 일평균 지출(원, 관측일수 분모).

    sentinel 행 제외(2배 집계 함정 방지). 분모 = 창 내 관측일수(distinct ad_date) — 캠페인이
    아직 30일을 다 못 채웠어도 실제 관측된 날 수로 나눈다(빈 날을 0으로 섞어 평균을 눌러
    봉투를 과소산정하지 않는다). 관측일 0(신규/무데이터)이면 0 반환(→ 봉투는 바닥값 5만)."""
    win_from = today - timedelta(days=_ENVELOPE_LOOKBACK_DAYS)
    win_to = today - timedelta(days=1)
    total_cost, observed_days = (
        db.query(
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
            sqlfunc.count(sqlfunc.distinct(NaverAdDaily.ad_date)),
        )
        .filter(
            NaverAdDaily.ad_date >= win_from,
            NaverAdDaily.ad_date <= win_to,
            NaverAdDaily.campaign_id == campaign_id,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .one()
    )
    observed_days = int(observed_days)
    if observed_days <= 0:
        return 0
    return int(int(total_cost) / observed_days)


def campaigns_raised_today(db: Session, *, today: date) -> set[str]:
    """오늘(KST) [예산봉투] 경로로 **성공 집행**된 update_budget이 있는 캠페인 집합 — 같은 날
    복리 증액 차단(08:00 캐치업 재실행이 증액 반영 스냅샷으로 또 제안·집행하는 것 방어).

    성공 판별 = dry_run=False ∧ after_value 존재(harness _execute_update_budget 성공 행 규격,
    naver_execution_harness:1434·compute_change_cadence:394와 동일 '실제 성공한 쓰기' 필터 —
    실패·가드거부 행은 after_value=None). [예산봉투] 경로 판별 = change_log.rationale이
    proposal.rationale(접두 [예산봉투])을 그대로 복사하므로 접두 포함(harness:1430·1437).
    KST 당일 = changed_at ≥ 오늘 0시(harness가 changed_at=now(KST-naive)로 기록)."""
    today_start = datetime.combine(today, datetime.min.time())
    rows = (
        db.query(NaverChangeLog.campaign_id)
        .filter(
            NaverChangeLog.action == "update_budget",
            NaverChangeLog.dry_run.is_(False),
            NaverChangeLog.after_value.isnot(None),
            NaverChangeLog.rationale.like(f"%{BUDGET_ENVELOPE_PREFIX}%"),
            NaverChangeLog.changed_at >= today_start,
        )
        .all()
    )
    return {r[0] for r in rows}


def compute_envelopes(db: Session, *, today: date) -> list[dict]:
    """auto_operate 캠페인별 예산 봉투 판정(순수·판정만, PLAN §4-5). 반환(campaign_id 오름차순):
      [{campaign_id, current_budget, avg_spend_30d, envelope, target_budget, needs_raise, reason}, ...].

    봉투 = max(30일 일평균 × 1.5, 50,000). target_budget = min(봉투, current×2)(+100%캡 정합).
    needs_raise = current_budget>0(=capped) ∧ current<봉투. daily_budget=0(uncapped)/미확보(None)는
    needs_raise=False + 사유(관찰만, 자동 감액 없음). current_budget 소스 = 당일 최신 hourly
    snapshot daily_budget(부재 시 fail-closed)."""
    auto_ids = [
        r[0]
        for r in db.query(NaverCampaignSettings.campaign_id)
        .filter(NaverCampaignSettings.auto_operate.is_(True))
        .order_by(NaverCampaignSettings.campaign_id.asc())
        .all()
    ]
    if not auto_ids:
        return []

    # 당일 최신 hourly snapshot의 daily_budget(budget_allocator._latest_hourly_rows 관례 재사용).
    budget_by_campaign: dict[str, int | None] = {
        row.campaign_id: row.daily_budget for row in budget_allocator._latest_hourly_rows(db, today)
    }

    out: list[dict] = []
    for campaign_id in auto_ids:
        avg_spend = _avg_spend_30d(db, campaign_id, today)
        envelope = max(int(Decimal(avg_spend) * _ENVELOPE_MULTIPLE), _ENVELOPE_FLOOR)
        current_budget = budget_by_campaign.get(campaign_id)

        record = {
            "campaign_id": campaign_id,
            "current_budget": current_budget,
            "avg_spend_30d": avg_spend,
            "envelope": envelope,
            "target_budget": None,
            "needs_raise": False,
            "reason": "",
        }

        if current_budget is None:
            record["reason"] = (
                "당일 예산 미확보(hourly snapshot daily_budget 부재) — 봉투 판정 불가(fail-closed)"
            )
            out.append(record)
            continue
        if current_budget <= 0:
            record["reason"] = (
                f"uncapped(daily_budget={current_budget}) — 봉투 미적용(관찰만, 자동 감액 없음)"
            )
            out.append(record)
            continue
        if current_budget >= envelope:
            record["reason"] = (
                f"현재 예산 {current_budget}원 ≥ 봉투 {envelope}원 — 확대 불요(자동 감액 없음)"
            )
            out.append(record)
            continue

        # capped ∧ 봉투 미달 → 램프(봉투까지, +100%캡 이내).
        target_budget = min(envelope, current_budget * _BUDGET_UP_MAX_MULTIPLE)
        record["target_budget"] = target_budget
        record["needs_raise"] = True
        record["reason"] = (
            f"봉투 램프 — 30일 일평균 {avg_spend}원×{_ENVELOPE_MULTIPLE} vs 바닥 {_ENVELOPE_FLOOR}원 "
            f"→ 봉투 {envelope}원, 현재 {current_budget}원 → 목표 {target_budget}원"
        )
        out.append(record)

    return out
