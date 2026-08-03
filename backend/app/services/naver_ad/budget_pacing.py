# budget_pacing.py — budget_pacing SA (BP 레인, D-NAO-102 "예산 페이싱 자동 증액")
"""역할(SA·단일책임·순수 판정): auto_operate 캠페인의 **장중 예산 소진 속도**를 보고
"오늘 예산이 모자라 광고가 자정 전에 꺼질 판인데 성과는 목표 이상"인 캠페인에 대해
증액 제안(목표 일예산)을 산출한다. **판정만** 한다 — 제안 생성·승인·집행·DB 쓰기는 전부
하니스(auto_operator._run_budget_pacing_lane) 몫이고, 이 파일은 DB 읽기 + 순수 계산뿐이다
(budget_envelope.py와 동일 규율, 광고 API 호출 0).

트리거·산식(Jino 확정 D-NAO-102):
  · 트리거   당일 소진율 = cost_today / current_budget ≥ 90%(잔여 ≤10%).
  · 성과조건 당일 프록시 ROAS ≥ 캠페인 target_roas(campaign_target_resolver 우선순위).
  · 사이징   최근 3시간 소진 속도(원/시간) × 자정까지 잔여 시간 × 여유계수 1.2 = 추가 필요분.
             신규 예산 = min(cost_today + 추가 필요분, base_daily_budget × 2).
  · 증액만   감액은 이 레인에 없다(원복은 별도 00:05 리셋 — restore_candidates).

★프록시 ROAS의 정직 경계(반드시 rationale/로그에 병기 — 소비 전 숙지):
  당일 매출은 "스마트스토어 실주문(orders, channel 6) × naver_adgroup_product 매핑 상품"
  으로 잡는다([[naver-ad-today-conversion-via-smartstore]]). 이건 **광고 귀속 매출이 아니라
  그 상품의 전체 판매액**이라 구조적으로 상한 프록시다(광고 외 유입분 포함). 그래서 이
  신호는 "증액해도 되는가"의 **필요조건**으로만 쓰고, 실집행 방어선은 여전히
  guardrail_gate._check_budget(BEP 이익하한·스톱로스·+100%캡·쿨다운)이 담당한다.

★fail-closed 원칙(돈 경로 보수): 아래 중 하나라도 걸리면 증액하지 않는다 + 사유를 남긴다.
  당일 스냅샷 부재 / daily_budget 미확보 / uncapped(=0) / 상품 매핑 없음 / target_roas 미해결 /
  당일 소진 0(ROAS 분모 없음) / 소진 속도 산출 불가 / 잔여 시간 없음.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import (
    NaverAdgroupProduct,
    NaverCampaignSettings,
    NaverChangeLog,
    NaverHourlySnapshot,
    Order,
)
from app.services.cafe24_status_mapper import REVENUE_EXCLUDED
from app.services.naver_ad import (
    adgroup_product_freshness, campaign_target_resolver, product_campaign_share,
)

log = logging.getLogger(__name__)

# rationale 접두 — change_log.rationale에 그대로 복사되므로(harness:1430·1437) 이 접두가
# BP 레인의 유일한 식별자다. 예산 봉투([예산봉투], D-NAO-87)와 스코프가 겹치지 않게 분리.
BUDGET_PACING_PREFIX = "[예산페이싱]"
BUDGET_PACING_RESTORE_PREFIX = "[예산페이싱복원]"

# naver_proposals.approval_source(String(12)) — BP 레인의 승인 출처. 이 모듈이 소유하고
# auto_operator/naver_execution_harness가 참조한다(exploration.APPROVAL_SOURCE_EXPLORE 관례):
# harness가 auto_operator를 module-level import하면 순환이라 값의 집은 SA 쪽이어야 한다.
APPROVAL_SOURCE_PACING = "pace_op"  # 7자

# naver_change_log.action — BP 쓰기는 사후 식별을 위해 별도 라벨로 기록한다(D-NAO-102 ⑥).
# ★실행 경로는 여전히 update_budget 하나(naver_execution_harness._WRITE_EXECUTORS 키·
#   OPEN_ACTIONS·proposal_type 전부 불변) — 바뀌는 건 change_log에 남는 **라벨**뿐이다.
#   쿨다운/일일상한(compute_change_cadence)은 action을 필터하지 않으므로(entity_type·
#   entity_id·dry_run·after_value만) 이 라벨 분리가 가드레일 카운트에 구멍을 내지 않는다.
ACTION_BUDGET_UP_PACING = "budget_up_pacing"
ACTION_BUDGET_DOWN_PACING = "budget_down_pacing"
PACING_ACTIONS = (ACTION_BUDGET_UP_PACING, ACTION_BUDGET_DOWN_PACING)

_NAVER_CHANNEL_ID = 6

# ── 트리거·산식 상수(D-NAO-102) ──
TRIGGER_DEPLETION_RATIO = Decimal("0.90")  # 소진율 ≥90%(잔여 ≤10%)에서 발동
SAFETY_FACTOR = Decimal("1.2")             # 추가 필요분 여유계수
BASE_BUDGET_MAX_MULTIPLE = 2               # 하루 총 증액 상한 = base × 2(+100% 가드레일 정합)
RECENT_HOURS = 3                           # 소진 속도 산출 창(최근 3시간)
BUDGET_ROUND_INCREMENT = 100               # 100원 단위(네이버 침묵 반올림 방어, PLAN §5-B/G)

# 회당 라운드 봉투(proposal_writer._ROUND_BUDGET_AUTO_CAP와 동일 값) — 한 번의 BP 런에서
# 전 캠페인 증액 합계가 이 값을 넘지 못한다. 값의 단일 진실은 proposal_writer이나 순환/무거운
# import를 피해 배수만 로컬 상수로 두고 테스트가 정합을 고정한다(budget_envelope 관례 동형).
ROUND_BUDGET_CAP = 100_000

# 계정 단위 BP 일일 총량 캡(리뷰 P2-4) — 하루 동안 BP가 계정 전체에서 늘릴 수 있는 총액.
# 라운드 봉투(회당 ≤10만)는 "한 번의 런"만 제한하므로 매시 발동이 쌓이면 하루 총액이 무한정
# 커진다. change_log의 당일 budget_up_pacing Δ 합으로 판정한다(성공 집행분만).
DAILY_ACCOUNT_RAISE_CAP = 100_000

# 프록시 최소 볼륨 게이트(리뷰 P2-2) — 표본이 얇으면 자연판매 하나로 ROAS가 폭등해 증액이
# 통과한다(실사고 재현: 2,900원 소진 + 자연판매 50만 → ROAS 172). 당일 클릭·소진 둘 다
# 최소치를 넘어야 프록시 ROAS를 판정 재료로 쓴다(미달 = fail-closed).
MIN_CLICKS_FOR_PROXY = 5
MIN_COST_FOR_PROXY = 5_000

_DRY_RUN_ENV = "NAVER_BP_DRY_RUN"


def dry_run_enabled() -> bool:
    """NAVER_BP_DRY_RUN=1/true/yes/on 이면 실쓰기 없이 제안·기록만(하니스가 소비)."""
    return (os.getenv(_DRY_RUN_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


def _auto_operate_campaigns(db: Session) -> list[str]:
    return [
        r[0]
        for r in db.query(NaverCampaignSettings.campaign_id)
        .filter(NaverCampaignSettings.auto_operate.is_(True))
        .order_by(NaverCampaignSettings.campaign_id.asc())
        .all()
    ]


def _today_snapshots(db: Session, campaign_id: str, today: date) -> list[NaverHourlySnapshot]:
    """당일 캠페인 시간별 스냅샷(snapshot_hour 오름차순). 누적 지표라 최신=당일 확정값."""
    return (
        db.query(NaverHourlySnapshot)
        .filter(
            NaverHourlySnapshot.campaign_id == campaign_id,
            NaverHourlySnapshot.ad_date == today,
        )
        .order_by(NaverHourlySnapshot.snapshot_hour.asc())
        .all()
    )


def spend_rate_per_hour(rows: list[NaverHourlySnapshot]) -> tuple[Decimal | None, str]:
    """최근 3시간 소진 속도(원/시간)와 산출 근거 문자열.

    스냅샷 cost는 **당일 누적**이라 속도 = (최신 누적 − 3시간 전 누적) / 경과 시간.
    창 안에 이전 스냅샷이 없으면(그날 첫 스냅샷뿐) 하루 평균 속도(누적 ÷ (hour+1))로 폴백
    — 폴백임을 근거 문자열에 명시한다. 스냅샷이 없거나 속도 ≤0이면 (None, 사유)."""
    if not rows:
        return None, "당일 스냅샷 부재 — 소진 속도 산출 불가"
    latest = rows[-1]
    baseline = None
    for r in rows[:-1]:
        if r.snapshot_hour >= latest.snapshot_hour - RECENT_HOURS:
            baseline = r
            break
    if baseline is not None and latest.snapshot_hour > baseline.snapshot_hour:
        span = Decimal(latest.snapshot_hour - baseline.snapshot_hour)
        delta = Decimal(int(latest.cost) - int(baseline.cost))
        rate = delta / span
        note = (
            f"최근 {span}시간 소진 {delta}원({baseline.snapshot_hour}시 {baseline.cost}원"
            f"→{latest.snapshot_hour}시 {latest.cost}원) → {rate:.0f}원/시간"
        )
    else:
        elapsed = Decimal(latest.snapshot_hour + 1)
        rate = Decimal(int(latest.cost)) / elapsed
        note = (
            f"3시간 창 내 이전 스냅샷 없음 — 하루 평균 폴백({latest.cost}원÷{elapsed}시간)"
            f" → {rate:.0f}원/시간"
        )
    if rate <= 0:
        return None, f"소진 속도 0 이하 — 증액 불요({note})"
    return rate, note


def remaining_hours(now: datetime) -> Decimal:
    """자정(KST)까지 잔여 시간(소수). 0 이하면 오늘 남은 시간 없음."""
    elapsed = Decimal(now.hour) + (Decimal(now.minute) / Decimal(60))
    return Decimal(24) - elapsed


def campaign_product_ids(db: Session, campaign_id: str) -> list[str]:
    """캠페인에 매핑된 판매 상품(mall_product_id = orders.platform_product_id) 목록.

    소스 = naver_adgroup_product(shopping_ad_product_sync가 **관측 스코프** 쇼핑 캠페인을
    누적 upsert). 매핑이 없으면 빈 목록 → 프록시 ROAS 판정 불가(fail-closed).
    ★07-27 03 리플레이 실측: 03은 D-NAO-92로 auto_operate=0이라 **애초에 이 레인의 1단계
    (auto_operate 필터)에서 제외**됐다. 갓 편입돼 다음 sync 전인 캠페인도 같은 케이스 —
    매핑이 도착할 때까지 증액하지 않는다.

    ★신선도 필터(codex 3R P1): 매핑 테이블이 누적 upsert(삭제 없음)라 옛 상품이 계속 남는다.
    필터 없이 읽으면 이미 빠진 상품의 매출까지 프록시 ROAS에 들어가 **증액이 부당 통과**한다.
    필터 후 0행이면 위 fail-closed 경로 그대로 — 증액하지 않는다(보수 방향)."""
    return sorted(
        {
            r[0]
            for r in db.query(NaverAdgroupProduct.mall_product_id)
            .filter(NaverAdgroupProduct.campaign_id == campaign_id,
                    adgroup_product_freshness.fresh_condition())
            .all()
            if r[0]
        }
    )


def product_campaign_counts(db: Session, campaign_ids: list[str]) -> dict[str, int]:
    """주어진 캠페인들이 광고하는 상품별 "그 상품을 매핑한 **모든** 캠페인 수"
    (리뷰 P2-3 이중계상 방어 + P2-1/P3-5 분모 통일).

    한 상품이 캠페인 두 곳에 매핑돼 있으면 당일 매출을 양쪽에 100%씩 계상해 두 캠페인 모두
    ROAS 게이트를 부당 통과한다. 보수적으로 캠페인 수만큼 균등 분할한다(실제 기여 비율은
    알 수 없다 — 추정 금지 원칙상 균등이 가장 정직한 보수 배분).

    ★분모는 auto_operate 여부를 **보지 않는다**(2026-07-28 정정, product_campaign_share):
    종전엔 auto_operate 캠페인만 셌는데, 같은 상품을 대행사·수동 캠페인이 함께 광고하면
    그 몫이 분모에서 빠져 **자기 매출을 과대 계상**했다(증액이 부당 통과하는 방향). 분모는
    조회 목적과 무관한 사실이어야 하고, 성과 뷰(today_proxy_revenue)도 같은 값을 쓴다.
    ★동작 변화 방향은 보수화(분모 ≥ 종전 → 프록시 ROAS ≤ 종전 → 증액이 덜 열린다)다."""
    if not campaign_ids:
        return {}
    pids = [
        r[0]
        for r in db.query(NaverAdgroupProduct.mall_product_id)
        # ★여기엔 신선도 필터를 걸지 않는다 — 이 pid 목록은 **분모**(campaigns_per_product)로
        # 들어간다. 걸면 분모가 줄어 프록시 ROAS가 부풀고 증액이 부당 통과한다
        # (product_campaign_share docstring의 '의도된 비대칭' 참조).
        .filter(NaverAdgroupProduct.campaign_id.in_(campaign_ids))
        .distinct()
        .all()
        if r[0]
    ]
    return product_campaign_share.campaigns_per_product(db, pids)


def today_proxy_revenue(
    db: Session, product_ids: list[str], today: date, *, shares: dict[str, int] | None = None
) -> tuple[int, list[str]]:
    """당일(KST) 그 상품들의 스마트스토어 실주문 매출 합(원) — **상한 프록시**.

    actual_revenue.naver_order_revenue와 동일 회계 규약(채널6·매출제외 상태 제외·
    selling_price=라인총액이라 ×수량 2중계상 없음)이되, 상품 필터가 추가된 캠페인 스코프판.
    광고 귀속이 아니라 전체 판매액이므로 과대추정 방향(모듈 docstring 정직 경계).

    shares: {상품: 그 상품을 매핑한 auto_operate 캠페인 수}. 2 이상이면 그 상품 매출을
    캠페인 수로 나눠 계상한다(리뷰 P2-3). 반환 = (매출, 분할된 상품 목록)."""
    if not product_ids:
        return 0, []
    start = datetime.combine(today, time.min)
    end = datetime.combine(today, time.max)
    rows = (
        db.query(
            Order.platform_product_id,
            sqlfunc.coalesce(sqlfunc.sum(Order.selling_price), 0),
        )
        .filter(
            Order.channel_id == _NAVER_CHANNEL_ID,
            Order.status.notin_(tuple(REVENUE_EXCLUDED)),
            Order.platform_product_id.in_(product_ids),
            Order.order_date >= start,
            Order.order_date <= end,
        )
        .group_by(Order.platform_product_id)
        .all()
    )
    total = Decimal(0)
    split: list[str] = []
    for pid, revenue in rows:
        amount = Decimal(str(revenue or 0))
        n = int((shares or {}).get(pid, 1) or 1)
        if n > 1 and amount > 0:
            split.append(f"{pid}÷{n}")
            amount = amount / Decimal(n)
        total += amount
    return int(total), sorted(split)


def resolve_target_roas(db: Session, campaign_id: str) -> Decimal | None:
    """캠페인 목표 ROAS(override > 상품BEP파생 > 계정기본값). 전부 미해결이면 None(fail-closed)."""
    resolved = campaign_target_resolver.resolve_target_roas(db, campaign_id)
    target = resolved.get("target_roas")
    # ★fail-closed — auto_operator._resolve_target_roas와 동일 근거(2026-08-03).
    if target is None and resolved.get("source") != campaign_target_resolver.SOURCE_STALE_MAPPING:
        target = campaign_target_resolver.account_default_target_roas(db)
    return Decimal(str(target)) if target is not None else None


def _last_pacing_write(db: Session, campaign_id: str, action: str) -> NaverChangeLog | None:
    """캠페인의 마지막 **성공** BP 쓰기 1건(dry_run=False ∧ after_value 존재 — harness
    _execute_update_budget 성공 행 규격. 실패·가드거부 행은 after_value=None)."""
    return (
        db.query(NaverChangeLog)
        .filter(
            NaverChangeLog.campaign_id == campaign_id,
            NaverChangeLog.action == action,
            NaverChangeLog.dry_run.is_(False),
            NaverChangeLog.after_value.isnot(None),
        )
        .order_by(NaverChangeLog.changed_at.desc())
        .first()
    )


def unrestored_raise(
    db: Session, campaign_id: str, *, current_budget: int | None = None
) -> NaverChangeLog | None:
    """아직 base로 되돌려지지 않은 마지막 BP 증액(없으면 None) — 리뷰 P1-1의 핵심 판정.

    current_budget을 주면 **우리 증액이 지금도 서 있는지**까지 확인한다(리뷰 R2 P2-R2-1):
    change_log 순서만 보면, 원복 실패 후 Jino가 콘솔에서 예산을 올려놓은 경우(50,000→200,000)
    그 200,000을 "우리 미복원 증액"으로 오인해 되돌려버린다(사람 조작 침범 + base 영구 교착).
    증액 행의 after_value.dailyBudget(우리가 설정한 값)과 현재 예산이 다르면 우리 증액은 더
    이상 서 있지 않다 — None을 돌려 ①원복 후보에서 빠지고 ②base가 정상 재시드된다(사람이
    정한 값을 새 base로 흡수 — ⑤ 재시드 철학 그대로).
    ★after_value 파싱 불가(값을 못 읽음)면 대조를 포기하고 보수적으로 "미복원"으로 남긴다.

    ★왜 필요한가: base 재시드를 "오늘 증액했는가"로만 막으면, **어제 증액 + 원복 실패**
    상태에서 다음 날 첫 평가가 부풀린 예산을 새 base로 흡수해버린다. 그러면
    restore_candidates의 ④(observed ≤ base)가 성립해 원복 후보가 **영구 소멸**하고,
    base×2 캡의 기준선도 부풀어 오른 채 굳는다(래칫). 실제 발동 경로 2개:
      ① 원복 시 get_campaign/PUT 예외로 실쓰기 실패
      ② **가드레일 쿨다운 2h** — 23:20 증액 → 00:20 원복이 쿨다운으로 차단(그 다음 재시도
         시각인 01:20에는 이미 base가 재시드돼 후보가 사라져 있었다). 늦은 저녁 증액이면
         언제나 성립하는 경로라 이론적 코너케이스가 아니다.
    그래서 "미복원 증액이 남아 있으면 재시드 금지"가 불변식이다."""
    last_raise = _last_pacing_write(db, campaign_id, ACTION_BUDGET_UP_PACING)
    if last_raise is None:
        return None
    last_restore = _last_pacing_write(db, campaign_id, ACTION_BUDGET_DOWN_PACING)
    if last_restore is not None and last_restore.changed_at >= last_raise.changed_at:
        return None
    if current_budget is not None:
        our_value = _budget_from_value(last_raise.after_value)
        if our_value is not None and int(current_budget) != our_value:
            # 우리 증액값이 더 이상 서 있지 않다 = 그 사이 사람이 예산을 바꿨다.
            return None
    return last_raise


def _budget_from_value(raw: str | None) -> int | None:
    """change_log before/after_value({"dailyBudget": N}, writer 재조회 응답 그대로) → 정수."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    value = parsed.get("dailyBudget")
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def raised_amount_today(db: Session, *, today: date) -> int:
    """오늘(KST) BP가 계정 전체에서 성공적으로 늘린 총액(원) — DAILY_ACCOUNT_RAISE_CAP 판정 원료.

    Δ = after.dailyBudget − before.dailyBudget(성공 행만). 파싱 불가 행은 0으로 취급하지
    않고 **건너뛰지 않는다** — 값을 못 읽으면 캡을 과소집계하게 되므로, 그런 행은 보수적으로
    캡 소진으로 볼 수 없어 로그만 남기고 0으로 센다(값 부재는 봉투 계산의 원료가 없는 상태)."""
    today_start = datetime.combine(today, time.min)
    rows = (
        db.query(NaverChangeLog.before_value, NaverChangeLog.after_value)
        .filter(
            NaverChangeLog.action == ACTION_BUDGET_UP_PACING,
            NaverChangeLog.dry_run.is_(False),
            NaverChangeLog.after_value.isnot(None),
            NaverChangeLog.changed_at >= today_start,
        )
        .all()
    )
    total = 0
    for before_raw, after_raw in rows:
        before = _budget_from_value(before_raw)
        after = _budget_from_value(after_raw)
        if before is None or after is None:
            log.warning("budget_pacing: 당일 캡 집계 — 예산 값 파싱 불가(before=%r after=%r)",
                        before_raw, after_raw)
            continue
        total += max(after - before, 0)
    return total


def campaigns_raised_today(db: Session, *, today: date) -> set[str]:
    """오늘(KST) [예산페이싱] 경로로 **성공 집행**된 update_budget이 있는 캠페인 집합.

    성공 판별 = dry_run=False ∧ after_value 존재(harness _execute_update_budget 성공 행 규격
    — 실패·가드거부 행은 after_value=None). budget_envelope.campaigns_raised_today와 동일
    관례. BP는 같은 날 재발동을 허용하므로 이 집합은 "차단"이 아니라 **base 재시드 여부**를
    가르는 데 쓴다(오늘 이미 올렸으면 저장된 base 유지, 아니면 현재 예산을 base로 재시드)."""
    today_start = datetime.combine(today, time.min)
    rows = (
        db.query(NaverChangeLog.campaign_id)
        .filter(
            NaverChangeLog.action == ACTION_BUDGET_UP_PACING,
            NaverChangeLog.dry_run.is_(False),
            NaverChangeLog.after_value.isnot(None),
            NaverChangeLog.changed_at >= today_start,
        )
        .all()
    )
    return {r[0] for r in rows}


def _round_up_100(value: Decimal) -> int:
    """100원 단위 올림(네이버 침묵 반올림 방어 — 올림이라 방향 불일치가 생기지 않는다)."""
    inc = Decimal(BUDGET_ROUND_INCREMENT)
    return int((value / inc).to_integral_value(rounding="ROUND_CEILING") * inc)


def _floor_100(value: int) -> int:
    return (value // BUDGET_ROUND_INCREMENT) * BUDGET_ROUND_INCREMENT


def evaluate(db: Session, *, now: datetime) -> list[dict]:
    """auto_operate 캠페인별 BP 판정(순수·판정만). 반환(campaign_id 오름차순):

      [{campaign_id, needs_raise, reason, current_budget, base_budget, base_seeded,
        cost_today, clicks_today, depletion_ratio, spend_rate, spend_rate_note,
        remaining_hours, extra_needed, target_budget, proxy_revenue, proxy_roas,
        proxy_shared_products, target_roas, product_count}, ...]

    needs_raise=True인 행만 하니스가 제안·집행 대상으로 삼는다. base_seeded=True면
    하니스가 naver_campaign_settings.base_daily_budget을 base_budget으로 갱신해야 한다
    (판정 SA는 쓰기 금지 — 값만 계산해서 넘긴다).
    """
    campaign_ids = _auto_operate_campaigns(db)
    if not campaign_ids:
        return []

    today = now.date()
    raised_today = campaigns_raised_today(db, today=today)
    share_counts = product_campaign_counts(db, campaign_ids)
    settings_by_id = {
        s.campaign_id: s
        for s in db.query(NaverCampaignSettings)
        .filter(NaverCampaignSettings.campaign_id.in_(campaign_ids))
        .all()
    }

    out: list[dict] = []
    for campaign_id in campaign_ids:
        record = {
            "campaign_id": campaign_id, "needs_raise": False, "reason": "",
            "current_budget": None, "base_budget": None, "base_seeded": False,
            "cost_today": None, "clicks_today": None, "depletion_ratio": None,
            "spend_rate": None, "spend_rate_note": "", "remaining_hours": None,
            "extra_needed": None, "target_budget": None, "proxy_revenue": None,
            "proxy_roas": None, "proxy_shared_products": [], "target_roas": None,
            "product_count": 0,
        }

        rows = _today_snapshots(db, campaign_id, today)
        if not rows:
            record["reason"] = "당일 시간별 스냅샷 부재 — 소진율 판정 불가(fail-closed)"
            out.append(record)
            continue

        latest = rows[-1]
        current_budget = latest.daily_budget
        cost_today = int(latest.cost or 0)
        clicks_today = int(latest.clk or 0)
        record["current_budget"] = current_budget
        record["cost_today"] = cost_today
        record["clicks_today"] = clicks_today

        if current_budget is None:
            record["reason"] = "일예산 미확보(daily_budget=None) — 소진율 판정 불가(fail-closed)"
            out.append(record)
            continue
        if current_budget <= 0:
            record["reason"] = (
                f"uncapped(daily_budget={current_budget}) — 예산 상한이 없어 페이싱 증액 불요"
            )
            out.append(record)
            continue

        # ── base 예산 결정(리뷰 P1-1 래칫 방어) ──
        # 원칙: 현재 예산을 그날 기준으로 (재)시드해 사람이 콘솔에서 바꾼 값을 흡수한다.
        # 단 **아래 두 경우엔 저장된 base를 그대로 유지**한다 — 재시드하면 우리가 부풀린
        # 예산이 새 기준선으로 굳어(래칫) 캡이 무력화되고 원복 후보가 사라진다.
        #   ① 오늘 이미 BP 증액 성공(복리 금지 — 같은 날 재발동은 허용하되 기준선은 고정)
        #   ② **미복원 증액이 남아 있음**(어제 증액 + 원복 실패/쿨다운 차단) — unrestored_raise
        #      docstring 참조. 현재 예산이 stored_base보다 클 때만 성립(사람이 이미 내렸으면
        #      재시드해도 래칫이 아니다).
        setting = settings_by_id.get(campaign_id)
        stored_base = setting.base_daily_budget if setting else None
        keep_reason = ""
        if campaign_id in raised_today and stored_base:
            base_budget, keep_reason = int(stored_base), "오늘 BP 증액분 반영 전 기준선 유지"
        elif (
            stored_base
            and int(current_budget) > int(stored_base)
            and unrestored_raise(db, campaign_id, current_budget=int(current_budget)) is not None
        ):
            base_budget = int(stored_base)
            keep_reason = "미복원 BP 증액 존재 — 재시드 금지(래칫 방어, 리뷰 P1-1)"
        else:
            base_budget = int(current_budget)
            record["base_seeded"] = stored_base != base_budget
        record["base_budget"] = base_budget
        record["base_keep_reason"] = keep_reason

        depletion = Decimal(cost_today) / Decimal(current_budget)
        record["depletion_ratio"] = depletion
        if depletion < TRIGGER_DEPLETION_RATIO:
            record["reason"] = (
                f"소진율 {depletion:.1%} < {TRIGGER_DEPLETION_RATIO:.0%} — 트리거 미달"
                f"(당일 {cost_today}원 / 예산 {current_budget}원)"
            )
            out.append(record)
            continue

        cap = _floor_100(base_budget * BASE_BUDGET_MAX_MULTIPLE)
        if current_budget >= cap:
            record["reason"] = (
                f"일일 증액 캡 도달 — 현재 {current_budget}원 ≥ base {base_budget}원"
                f"×{BASE_BUDGET_MAX_MULTIPLE}({cap}원)"
            )
            out.append(record)
            continue

        # ── 성과 조건: 당일 프록시 ROAS ≥ target_roas(fail-closed) ──
        product_ids = campaign_product_ids(db, campaign_id)
        record["product_count"] = len(product_ids)
        if not product_ids:
            record["reason"] = (
                "상품 매핑 부재(naver_adgroup_product) — 당일 프록시 ROAS 산출 불가"
                "(fail-closed, 매핑 sync 전 신규/재가동 캠페인)"
            )
            out.append(record)
            continue

        target_roas = resolve_target_roas(db, campaign_id)
        record["target_roas"] = target_roas
        if target_roas is None:
            record["reason"] = "target_roas 미해결(override·상품BEP·계정기본값 전부 부재) — fail-closed"
            out.append(record)
            continue

        if cost_today <= 0:
            record["reason"] = "당일 소진 0원 — 프록시 ROAS 분모 없음(fail-closed)"
            out.append(record)
            continue

        # 최소 볼륨 게이트(리뷰 P2-2) — 표본이 얇으면 자연판매 1건이 ROAS를 폭등시켜 게이트가
        # 무의미해진다(2,900원 소진 + 자연판매 50만 → ROAS 172 통과 실사례). fail-closed.
        if clicks_today < MIN_CLICKS_FOR_PROXY or cost_today < MIN_COST_FOR_PROXY:
            record["reason"] = (
                f"프록시 표본 미달 — 당일 클릭 {clicks_today}건(최소 {MIN_CLICKS_FOR_PROXY})·"
                f"소진 {cost_today}원(최소 {MIN_COST_FOR_PROXY}원) (fail-closed, 얇은 표본에서 "
                "자연판매가 ROAS를 부풀리는 것 방지)"
            )
            out.append(record)
            continue

        proxy_revenue, shared = today_proxy_revenue(db, product_ids, today, shares=share_counts)
        proxy_roas = (Decimal(proxy_revenue) / Decimal(cost_today)).quantize(Decimal("0.0001"))
        record["proxy_revenue"] = proxy_revenue
        record["proxy_roas"] = proxy_roas
        record["proxy_shared_products"] = shared
        shared_note = (
            f" ※공유상품 {len(shared)}종 균등분할({', '.join(shared[:5])}"
            f"{' 외' if len(shared) > 5 else ''})" if shared else ""
        )
        if shared:
            log.info(
                "budget_pacing: %s 공유상품 매출 균등분할 %s", campaign_id, ", ".join(shared),
            )
        if proxy_roas < target_roas:
            record["reason"] = (
                f"프록시 ROAS {proxy_roas} < 목표 {target_roas} — 증액 조건 미달"
                f"(당일 매출 {proxy_revenue}원/소진 {cost_today}원, 상품 {len(product_ids)}종 "
                f"상한 프록시{shared_note})"
            )
            out.append(record)
            continue

        # ── 사이징: 소진 속도 × 잔여 시간 × 1.2 ──
        rate, rate_note = spend_rate_per_hour(rows)
        record["spend_rate_note"] = rate_note
        if rate is None:
            record["reason"] = f"소진 속도 산출 불가 — {rate_note}(fail-closed)"
            out.append(record)
            continue
        record["spend_rate"] = rate

        left = remaining_hours(now)
        record["remaining_hours"] = left
        if left <= 0:
            record["reason"] = f"자정까지 잔여 시간 없음({left}) — 증액 무의미"
            out.append(record)
            continue

        # quantize: 잔여 시간이 분 단위 나눗셈(20/60)이라 28자리 유효숫자 꼬리가 남는다 —
        # 원 단위 이하는 어차피 100원 올림에 흡수되므로 여기서 소수 2자리로 고정한다.
        extra_needed = (rate * left * SAFETY_FACTOR).quantize(Decimal("0.01"))
        record["extra_needed"] = extra_needed
        target_budget = min(_round_up_100(Decimal(cost_today) + extra_needed), cap)
        if target_budget <= current_budget:
            record["reason"] = (
                f"산출 목표 {target_budget}원 ≤ 현재 {current_budget}원 — 증액 여지 없음"
                f"(추가 필요분 {extra_needed:.0f}원, 캡 {cap}원)"
            )
            out.append(record)
            continue

        record["target_budget"] = target_budget
        record["needs_raise"] = True
        record["reason"] = (
            f"소진율 {depletion:.1%}(당일 {cost_today}원/예산 {current_budget}원) ≥ "
            f"{TRIGGER_DEPLETION_RATIO:.0%} · 프록시 ROAS {proxy_roas} ≥ 목표 {target_roas}"
            f"(당일 매출 {proxy_revenue}원, 클릭 {clicks_today}건, 상품 {len(product_ids)}종 — "
            f"★광고 귀속이 아닌 전체 판매액 상한 프록시{shared_note}) · "
            f"{rate_note} × 잔여 {left:.1f}시간 × 여유 {SAFETY_FACTOR} "
            f"= 추가 필요 {extra_needed:.0f}원 → 목표 {target_budget}원"
            f"(캡 base {base_budget}원×{BASE_BUDGET_MAX_MULTIPLE}={cap}원)"
        )
        out.append(record)

    return out


def apply_round_cap(candidates: list[dict], *, cap: int | None = None) -> None:
    """회당 라운드 봉투(≤10만원) 그리디 배정 — in-place로 각 후보에 'round_eligible'을 세팅.

    proposal_writer._classify_budget_round_envelope와 동일 알고리즘(증액분 큰 순으로 정렬 후
    누적, 캡을 넘기는 후보만 False). BP는 캡 초과분을 pending으로 남기지 않고 **그냥 건너뛴다**
    (매시 재평가라 다음 정시에 다시 기회가 온다 — 매몰되는 Confirm 큐를 만들지 않는다).

    cap: 이번 런에 허용된 총 증액(원). 하니스가 min(회당 봉투, 계정 일일 잔여)를 넘긴다
    (리뷰 P2-4 — 회당 캡만으로는 매시 발동이 쌓여 하루 총액이 무한정 커진다)."""
    limit = ROUND_BUDGET_CAP if cap is None else max(int(cap), 0)
    ordered = sorted(
        candidates,
        key=lambda c: (c.get("target_budget") or 0) - (c.get("current_budget") or 0),
        reverse=True,
    )
    cumulative = 0
    for c in ordered:
        delta = (c.get("target_budget") or 0) - (c.get("current_budget") or 0)
        if delta > 0 and cumulative + delta <= limit:
            c["round_eligible"] = True
            cumulative += delta
        else:
            c["round_eligible"] = False


def restore_candidates(db: Session, *, now: datetime) -> list[dict]:
    """익일 원복 대상 — 어제 이전에 BP가 올린 예산이 아직 base로 안 돌아온 캠페인.

    판정(캠페인별):
      ① base_daily_budget이 시드돼 있고 auto_operate=True
      ② 마지막 **성공** 증액(budget_up_pacing)이 존재하고 그 날짜 < 오늘(KST) — 오늘 올린 건 유지
      ③ 그 증액 이후 성공한 원복(budget_down_pacing)이 없음(이미 되돌렸으면 skip) = unrestored_raise
      ④ 최신 스냅샷의 daily_budget > base(사전 점검 — 사람이 이미 내렸으면 skip)
      ⑤ 그 관측 예산 == 우리 증액 행의 after_value(우리 증액이 지금도 서 있는가, 리뷰 R2).
         다르면 원복 실패 뒤 사람이 따로 올려둔 값이라 되돌리지 않는다(사람 조작 침범 금지 +
         base 영구 교착 차단 — evaluate가 그 값을 새 base로 재시드한다).
         ★단 그 스냅샷 as-of가 **마지막 증액 시각보다 오래됐으면 skip하지 않는다**(리뷰 P2-1):
         23:20 증액 → 00:05 리셋 잡이 보는 최신 스냅샷은 23:05분 것이라 증액 전 예산을 담고
         있다. 그걸 근거로 "이미 base다"라고 판단하면 리셋 잡이 매번 헛돈다. 스냅샷이 stale
         이면 관측을 신뢰하지 않고 후보로 올린 뒤, 방향 검증은 실행 직전 가드레일에 맡긴다.
    ★멱등·자가치유: 00:05 잡이 죽어도 다음 시간당 레인이 같은 판정으로 따라잡는다.
    ★최종 방향 검증은 guardrail_gate._check_budget(budget_down 방향 일치)이 실행 직전에 한다.
    """
    campaign_ids = _auto_operate_campaigns(db)
    if not campaign_ids:
        return []
    today = now.date()

    settings_by_id = {
        s.campaign_id: s
        for s in db.query(NaverCampaignSettings)
        .filter(NaverCampaignSettings.campaign_id.in_(campaign_ids))
        .all()
    }

    out: list[dict] = []
    for campaign_id in campaign_ids:
        setting = settings_by_id.get(campaign_id)
        base = setting.base_daily_budget if setting else None
        if not base:
            continue

        last_raise = unrestored_raise(db, campaign_id)  # ②③ 통합 판정(순서만)
        if last_raise is None or last_raise.changed_at.date() >= today:
            continue

        latest = (
            db.query(NaverHourlySnapshot)
            .filter(
                NaverHourlySnapshot.campaign_id == campaign_id,
                NaverHourlySnapshot.daily_budget.isnot(None),
            )
            .order_by(
                NaverHourlySnapshot.ad_date.desc(), NaverHourlySnapshot.snapshot_hour.desc()
            )
            .first()
        )
        observed = latest.daily_budget if latest else None
        snapshot_as_of = (
            datetime.combine(latest.ad_date, time.min).replace(hour=latest.snapshot_hour)
            if latest else None
        )
        snapshot_stale = snapshot_as_of is None or snapshot_as_of < last_raise.changed_at
        if observed is not None and not snapshot_stale:
            if observed <= int(base):
                continue  # ④ 사람이 이미 내렸음
            # ⑤(리뷰 R2 P2-R2-1) 우리 증액이 지금도 서 있는가 — after_value와 현재 예산 대조.
            # 다르면 원복 실패 뒤 사람이 따로 올려둔 값이다. 그걸 되돌리면 사람 조작 침범이라
            # 후보에서 제외한다(같은 판정으로 evaluate는 그 값을 새 base로 정상 재시드한다).
            our_value = _budget_from_value(last_raise.after_value)
            if our_value is not None and int(observed) != our_value:
                log.info(
                    "budget_pacing: %s 원복 skip — 우리 증액값 %s원이 현재 %s원과 불일치"
                    "(사람 개입으로 판단, 되돌리지 않음)", campaign_id, our_value, observed,
                )
                continue

        out.append({
            "campaign_id": campaign_id,
            "base_budget": int(base),
            "observed_budget": observed,
            "observed_stale": snapshot_stale,
            "last_raise_at": last_raise.changed_at,
            "reason": (
                f"익일 원복 — {last_raise.changed_at:%Y-%m-%d %H:%M} BP 증액분을 base "
                f"{int(base)}원으로 복귀(관측 {observed}원"
                + (", 스냅샷 stale — 관측 불신뢰, 가드레일 방향검증에 위임" if snapshot_stale else "")
                + ")"
            ),
        })
    return out


__all__ = [
    "APPROVAL_SOURCE_PACING", "ACTION_BUDGET_UP_PACING", "ACTION_BUDGET_DOWN_PACING",
    "PACING_ACTIONS",
    "BUDGET_PACING_PREFIX", "BUDGET_PACING_RESTORE_PREFIX", "TRIGGER_DEPLETION_RATIO",
    "SAFETY_FACTOR", "BASE_BUDGET_MAX_MULTIPLE", "RECENT_HOURS", "ROUND_BUDGET_CAP",
    "DAILY_ACCOUNT_RAISE_CAP", "MIN_CLICKS_FOR_PROXY", "MIN_COST_FOR_PROXY",
    "dry_run_enabled", "evaluate", "apply_round_cap", "restore_candidates",
    "campaigns_raised_today", "raised_amount_today", "unrestored_raise",
    "spend_rate_per_hour", "remaining_hours", "campaign_product_ids",
    "product_campaign_counts", "today_proxy_revenue", "resolve_target_roas",
]
