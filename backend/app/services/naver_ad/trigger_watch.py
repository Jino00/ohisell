# trigger_watch.py — trigger_watch Harness (듀얼모드 스프린트 Phase 4, D-NAO-3-②/D-NAO-22-④ 뒷부분)
# 역할: snapshot_naver_ad_hourly(매시 :05) 직후 실행되는 조건발동 즉시 알림. 08:00 정시
#   proposal_pipeline(느린 루프, D-NAO-4 "학습·전략")과 분리된 빠른 루프("관찰·제어") —
#   이 harness는 사실을 감지해 즉시 알릴 뿐 입찰/예산 방향을 결정하지 않는다(그건 느린 루프의
#   몫, D-NAO-4). 알림 자체는 정보성 NaverProposal(target_type="campaign", D-3)로 남아
#   콘솔·Slack에 노출된다. SA간 직접 호출 금지(원칙18) — 이 harness가 hourly_pacing(SA
#   재사용)·proposal_writer(제안 저장은 여기서 직접, 스키마만 공유)·slack_notifier를 조합한다.
#
# ⚠️ 순위 이탈(원래 D-NAO-3-② 스펙의 3번째 트리거)은 이번 Phase에서 구현하지 않는다 —
#   naver_hourly_snapshot은 cost/clk/imp만 수집하고 avg_rank(rank_sum/imp)는 일별
#   stat-report 파일(AD 리포트)에서만 나온다(hourly_snapshot.py/hourly_pacing.py 확인,
#   docs/references/21). 실시간 순위 데이터가 수집되지 않아 즉시감지가 불가능함을 Jino에게
#   확인(2026-07-08) — 소진 이상 + CPC 급등만 이번 스코프로 진행하기로 결정. 순위 이탈은
#   트랙 "다음 액션"에 재검토 항목으로 남긴다(hourly_snapshot에 rank_sum 필드 추가 검토).
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Callable

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverForecastDaily, NaverHourlySnapshot, NaverProposal
from app.services.naver_ad import alert_humanizer, hourly_pattern, slack_notifier
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.utils.kst import kst_now, kst_today

log = logging.getLogger(__name__)

_Q2 = Decimal("0.01")
_Q4 = Decimal("0.0001")

PROPOSAL_TYPE_PACING = "trigger_pacing"
PROPOSAL_TYPE_CPC = "trigger_cpc_spike"

# dashboard_overview 실측 결과의 단일 진실 소스(코드리뷰 P2 재조사, 타임스탬프 이중변환
# 버그 수정) — run_hourly()가 NaverProposal.created_at=kst_now()로 명시 스탬프하는 유일한
# 두 유형. 나머지 SA(proposal_writer.persist/account_brief_singleton)는
# server_default=func.now() UTC 관례를 그대로 따른다 — dashboard_overview는 이 상수로
# "이미 KST라 변환 금지" 대상을 판정한다(proposal_writer.INFORMATIONAL_PROPOSAL_TYPES와는
# 목적이 다르다: 그건 실행형/정보성 분류, 이건 시간대 소스 분류 — 둘이 겹치지 않는다).
KST_STAMPED_PROPOSAL_TYPES: frozenset[str] = frozenset({PROPOSAL_TYPE_PACING, PROPOSAL_TYPE_CPC})

# 페이싱 이탈 배수 — anomaly_feed.SPEND_SPIKE_RATIO/SPEND_DROP_RATIO(Phase3, codex review
# 통과 완료)와 동일한 "사람이 한 번 볼 만큼 크게 움직였다"는 상식적 배수(2배/절반) 전례를
# 그대로 재사용한다(자의적 신규 상수 발명 대신 기존 승인 임계값 재사용). 실제 페이스(당일
# 누적소진÷일예산) vs 이론적 선형 페이스(경과시간÷24h) 비율로 판정.
PACE_OVERPACE_RATIO = Decimal("2")
PACE_UNDERPACE_RATIO = Decimal("0.5")
_MIN_HOUR_FOR_UNDERPACE = 12  # 오전 중엔 안 써도 정상 페이스라 오탐 방지(정오 이후만 underpace 판정)

# CPC 급등 — 이번 시간 순증분 CPC vs 최근 7일(anomaly_feed.FRESHNESS_BASELINE_LOOKBACK_DAYS와
# 동일 전례) 같은 캠페인의 평균 CPC. 배수도 anomaly_feed와 동일(2배) 재사용.
CPC_SPIKE_LOOKBACK_DAYS = 7
CPC_SPIKE_RATIO = Decimal("2")
_MIN_CLICKS_FOR_CPC = 5  # 소수 클릭 표본의 CPC는 노이즈(1클릭 고가=오탐) — D-NAO-9 모수게이트 정신 재사용

# 쿨다운 — 같은 (proposal_type, campaign_id) 재알림 최소 간격(알림 피로 방지, D-NAO-19-②
# "빈번한 변경 비권장" 정신을 재알림 빈도에 적용). MOP Pro 실측(docs/references/24,
# "Pro=시간별·5회+"=일 5회 이상 조정)을 알림 빈도 상한으로 채택 — 24h/5회=4.8h, 보수적으로
# 반올림한 5시간(그 이상 잦은 재알림은 경쟁 벤치마크 최고빈도보다도 잦아 "알림 피로"로 판단,
# 자의적 상수 아님).
TRIGGER_COOLDOWN_HOURS = 5


def _latest_snapshot_rows(db: Session, ad_date: date) -> list[NaverHourlySnapshot]:
    """캠페인별 당일 최신 시각 스냅샷 1행(budget_allocator._latest_hourly_rows와 동일 로직 —
    SA간 직접 참조 금지(원칙18-6)에 따라 harness 전용으로 별도 구현, 로직은 의도적으로 중복)."""
    sub = (
        db.query(
            NaverHourlySnapshot.campaign_id,
            sqlfunc.max(NaverHourlySnapshot.snapshot_hour).label("latest_hour"),
        )
        .filter(NaverHourlySnapshot.ad_date == ad_date)
        .group_by(NaverHourlySnapshot.campaign_id)
        .subquery()
    )
    return (
        db.query(NaverHourlySnapshot)
        .join(
            sub,
            (NaverHourlySnapshot.campaign_id == sub.c.campaign_id)
            & (NaverHourlySnapshot.snapshot_hour == sub.c.latest_hour),
        )
        .filter(NaverHourlySnapshot.ad_date == ad_date)
        .all()
    )


def _linear_expected_pace(snapshot_at: datetime) -> Decimal:
    """선형 폴백 — 스냅샷 실제 경과시간(snapshot_at의 시:분)÷24h(그 시각까지 "정상"이라면
    이만큼 썼어야 한다는 가장 단순한 가정). codex 지적: snapshot_hour(정수 시각)만 쓰고 +1로
    시간이 "완료"됐다고 가정하면 판정이 부정확해져 분 단위까지 반영한다."""
    elapsed_minutes = snapshot_at.hour * 60 + snapshot_at.minute
    return Decimal(elapsed_minutes) / Decimal(24 * 60)


def _curve_expected_pace(
    forecasts: dict[str, int], campaign_id: str, daily_budget: int, snapshot_at: datetime,
    fraction_at: Callable[[int], "Decimal | None"],
) -> Decimal | None:
    """예측곡선(F2b ⓒ, D-NAO-26) — hourly_pattern의 요일별 지출분포 누적비율×오늘 예측(pred_cost)을
    daily_budget에 대한 비율로 환산. 선형모델(균등 소진 가정)보다 실제 소진 곡선에 가까운 기대치.

    codex review(F2b) 지적 반영: 정각(hour:00)에 그 시간 몫을 전부 반영하면(=hour까지의
    누적비율을 그대로 씀) _linear_expected_pace(분 단위 반영)보다 정밀도가 낮다 — 이전
    시간까지의 누적비율(hour-1)과 이번 시간까지의 누적비율(hour) 사이를 분 단위로 선형
    보간한다(hour=0이면 이전 시간이 없으니 0).

    fraction_at: hourly_pattern.expected_cost_fraction(weekday=고정, hour=인자)를 감싼
    캐시 함수 — 호출자(find_pacing_anomalies)가 캠페인마다 같은 (weekday, hour) 조합을
    반복 쿼리하지 않도록 메모이즈해서 넘긴다(codex 지적, N+1 방지).

    campaign에 오늘자 예측이 없거나(fallback/미가동) hourly_pattern이 그 요일 데이터가 아직
    없으면(초기 상태) None — 호출자가 _linear_expected_pace로 폴백한다(추정 금지, 데이터 없이
    억지로 곡선을 만들지 않음).
    """
    pred_cost = forecasts.get(campaign_id)
    if pred_cost is None:
        return None
    hour = snapshot_at.hour
    frac_curr = fraction_at(hour)
    if frac_curr is None:
        return None
    frac_prev = fraction_at(hour - 1) if hour > 0 else Decimal(0)
    if frac_prev is None:
        frac_prev = Decimal(0)
    minute_fraction = Decimal(snapshot_at.minute) / Decimal(60)
    interpolated = frac_prev + (frac_curr - frac_prev) * minute_fraction
    return (interpolated * Decimal(pred_cost) / Decimal(daily_budget)).quantize(_Q4)


def find_pacing_anomalies(db: Session, ad_date: date, *, today: date | None = None) -> list[dict]:
    """daily_budget이 설정된 캠페인 중 페이싱(경과시간 대비 소진 속도)이 정상 범위를 벗어난 것.

    expected_pace: 오늘자 캠페인 grain 예측(forecast_model_builder pred_cost)과 hourly_pattern
    요일별 지출분포가 모두 있으면 예측곡선(_curve_expected_pace)을, 없으면 선형(_linear_
    expected_pace, 균등 소진 가정)으로 폴백한다(F2b ⓒ, D-NAO-26).
    actual_pace = 당일 누적cost/daily_budget. ratio=actual/expected. overpace(≥2배)는 이 속도로
    가면 예산이 하루 중 일찍 소진될 수 있다는 신호, underpace(정오 이후·≤0.5배)는 예산을
    다 못 쓸 수 있다는 신호 — 둘 다 원인은 판단하지 않는다(추정 금지, 사실만 알림).
    반환: [{campaign_id, ad_date, hour, cost, daily_budget, actual_pace, expected_pace, ratio, kind}],
    |ratio-1| 내림차순.

    당일 가드(F2b 1-a④): ad_date가 오늘이 아니면 빈 목록을 반환한다 — 마감된 과거일에 이
    함수를 돌리면(리플레이/백테스트 등) 그날은 이미 끝났는데도 "아직 못 썼다"는 underpace가
    대량 오발생한다(실측). 이 harness는 라이브 관찰 전용(D-NAO-4 빠른 루프)이라 과거일 재현이
    필요하면 별도 분석 스크립트를 써야 한다.
    today: "오늘" 판정 기준 — 미지정 시 kst_today(). codex review 지적: 함수 내부에서
    kst_today()를 다시 계산하면 호출자(run_hourly)가 이미 확정한 시각과 자정 경계에서
    어긋나는 레이스가 이론상 있다 — run_hourly가 한 번 계산한 값을 명시적으로 넘긴다.
    """
    today = today if today is not None else kst_today()
    if ad_date != today:
        return []

    rows = _latest_snapshot_rows(db, ad_date)
    if not rows:
        return []

    weekday = ad_date.weekday()
    forecasts = {
        f.scope_key: f.pred_cost
        for f in db.query(NaverForecastDaily).filter(
            NaverForecastDaily.grain == "campaign", NaverForecastDaily.target_date == ad_date,
        ).all()
    }
    # hour별 hourly_pattern 조회 캐시(codex 지적, N+1 방지) — 캠페인이 몇 개든 (weekday, hour)
    # 조합당 최대 1쿼리.
    fraction_cache: dict[int, "Decimal | None"] = {}

    def _fraction_at(hour: int) -> "Decimal | None":
        if hour not in fraction_cache:
            fraction_cache[hour] = hourly_pattern.expected_cost_fraction(db, weekday=weekday, hour=hour)
        return fraction_cache[hour]

    out = []
    for r in rows:
        if r.daily_budget is None or r.daily_budget <= 0:
            continue
        expected_pace = _curve_expected_pace(forecasts, r.campaign_id, r.daily_budget, r.snapshot_at, _fraction_at)
        if expected_pace is None:
            expected_pace = _linear_expected_pace(r.snapshot_at)
        if expected_pace <= 0:
            continue  # 자정 직후(경과 0분)는 비교 불가 — 다음 시간대에 재판정
        actual_pace = Decimal(r.cost) / Decimal(r.daily_budget)
        ratio = (actual_pace / expected_pace).quantize(_Q4)
        if ratio >= PACE_OVERPACE_RATIO:
            kind = "overpace"
        elif r.snapshot_hour >= _MIN_HOUR_FOR_UNDERPACE and ratio <= PACE_UNDERPACE_RATIO:
            kind = "underpace"
        else:
            continue
        out.append({
            "campaign_id": r.campaign_id, "ad_date": ad_date.isoformat(), "hour": r.snapshot_hour,
            "cost": r.cost, "daily_budget": r.daily_budget,
            "actual_pace": float(actual_pace.quantize(_Q4)), "expected_pace": float(expected_pace.quantize(_Q4)),
            "ratio": float(ratio), "kind": kind,
        })
    out.sort(key=lambda x: abs(x["ratio"] - 1), reverse=True)
    return out


def _current_hour_increment_by_campaign(db: Session, ad_date: date) -> dict[str, dict]:
    """캠페인별 당일 최신 시각의 순증분(cost/clk) — 스냅샷은 누적치라 직전 기록시각과 차분.

    hourly_pacing.hourly_rows()의 누적→증분 변환과 동일 원칙(직전 기록이 없으면 0에서부터
    증가한 것으로 간주, codex 지적 — 당일 기록이 1개뿐인 캠페인을 통째로 제외하면 하루의
    첫 스냅샷 시간대가 항상 누락된다). 전체 하루가 아니라 "가장 최근 한 걸음"만 필요해
    여기서 별도 경량 구현(원칙18-6, SA간 직접 참조 금지) — 클램프(누적 감소 방지)는 동일 적용.
    """
    rows = (
        db.query(NaverHourlySnapshot)
        .filter(NaverHourlySnapshot.ad_date == ad_date)
        .order_by(NaverHourlySnapshot.campaign_id, NaverHourlySnapshot.snapshot_hour)
        .all()
    )
    by_campaign: dict[str, list[NaverHourlySnapshot]] = {}
    for r in rows:
        by_campaign.setdefault(r.campaign_id, []).append(r)

    out: dict[str, dict] = {}
    for cid, records in by_campaign.items():
        latest = records[-1]
        prev_cost, prev_clk = (records[-2].cost, records[-2].clk) if len(records) >= 2 else (0, 0)
        inc_cost = max(0, latest.cost - prev_cost)
        inc_clk = max(0, latest.clk - prev_clk)
        out[cid] = {"hour": latest.snapshot_hour, "cost": inc_cost, "clk": inc_clk}
    return out


def find_cpc_spikes(
    db: Session, ad_date: date, *, lookback_days: int = CPC_SPIKE_LOOKBACK_DAYS,
) -> list[dict]:
    """캠페인별 이번 시간 순증분 CPC vs 최근 lookback_days일 실측 평균 CPC(naver_ad_daily).

    베이스라인은 추정이 아니라 실측 합계(sum(cost)/sum(clk)) — 표본 클릭이 너무 적으면
    (_MIN_CLICKS_FOR_CPC 미만) 노이즈로 보고 건너뛴다(D-NAO-9 모수게이트 정신).
    반환: [{campaign_id, ad_date, hour, current_cpc, baseline_cpc, ratio, clk}], ratio 내림차순.
    """
    increments = _current_hour_increment_by_campaign(db, ad_date)
    if not increments:
        return []

    window_from = ad_date - timedelta(days=lookback_days)
    window_to = ad_date - timedelta(days=1)
    baseline_rows = (
        db.query(
            NaverAdDaily.campaign_id,
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
        )
        .filter(
            NaverAdDaily.ad_date >= window_from, NaverAdDaily.ad_date <= window_to,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .group_by(NaverAdDaily.campaign_id)
        .all()
    )
    baseline_cpc = {
        cid: Decimal(cost) / Decimal(clk) for cid, cost, clk in baseline_rows if clk and clk > 0
    }

    out = []
    for cid, inc in increments.items():
        if inc["clk"] < _MIN_CLICKS_FOR_CPC:
            continue
        base = baseline_cpc.get(cid)
        if base is None or base <= 0:
            continue
        current_cpc = Decimal(inc["cost"]) / Decimal(inc["clk"])
        ratio = (current_cpc / base).quantize(_Q4)
        if ratio < CPC_SPIKE_RATIO:
            continue
        out.append({
            "campaign_id": cid, "ad_date": ad_date.isoformat(), "hour": inc["hour"],
            "current_cpc": float(current_cpc.quantize(_Q2)), "baseline_cpc": float(base.quantize(_Q2)),
            "ratio": float(ratio), "clk": inc["clk"],
        })
    out.sort(key=lambda x: x["ratio"], reverse=True)
    return out


def _recent_trigger_keys(
    db: Session, keys: set[tuple[str, str]], now: datetime,
) -> set[tuple[str, str]]:
    """keys(=(proposal_type, campaign_id) 후보 전체) 중 TRIGGER_COOLDOWN_HOURS 이내 이미
    알림된 조합을 1회 배치 쿼리로 조회(codex 지적 — 후보마다 개별 쿼리하면 캠페인 수만큼
    N+1이 된다).

    status 무관(pending/expired 상관없이) 최근 생성 여부만 본다 — proposal_writer.persist의
    pending-only dedup과 달리, 이건 "실행 대기 중복 방지"가 아니라 "알림 피로 방지"라 시간
    기반 쿨다운이 맞는 판단 기준이다.

    ⚠️ NaverProposal.created_at은 server_default=func.now()라 DB 서버(SQLite=UTC, Postgres=
    세션 타임존)의 시각을 쓴다 — kst_now() 기준 cutoff와 그대로 비교하면 KST(UTC+9) 오프셋만큼
    어긋나 쿨다운이 사실상 무력화된다(실측 확인). 이 harness가 만드는 제안은 아래 run_hourly가
    created_at을 kst_now()로 명시적으로 채워 넣어 자체 일관성을 맞춘다(다른 harness의
    server_default 기반 created_at 관례는 건드리지 않음, 이 쿨다운 비교 전용 스코프).
    """
    if not keys:
        return set()
    cutoff = now - timedelta(hours=TRIGGER_COOLDOWN_HOURS)
    proposal_types = {k[0] for k in keys}
    campaign_ids = {k[1] for k in keys}
    rows = db.query(NaverProposal.proposal_type, NaverProposal.campaign_id).filter(
        NaverProposal.proposal_type.in_(proposal_types),
        NaverProposal.campaign_id.in_(campaign_ids),
        NaverProposal.created_at >= cutoff,
    ).all()
    return {(pt, cid) for pt, cid in rows if (pt, cid) in keys}


def _pacing_proposal(item: dict) -> dict:
    kind_label = "과속(예산 조기소진 우려)" if item["kind"] == "overpace" else "저속(예산 소진 지연)"
    rationale = (
        f"[trigger_watch] 페이싱 이탈 {kind_label} — {item['ad_date']} {item['hour']}시 기준 "
        f"소진 {item['cost']}원/{item['daily_budget']}원(실제페이스={item['actual_pace']}, "
        f"기대페이스={item['expected_pace']}(예측곡선 있으면 그걸, 없으면 선형폴백), 배수={item['ratio']})."
    )
    return {
        "proposal_type": PROPOSAL_TYPE_PACING,
        "target_type": "campaign", "target_id": item["campaign_id"], "campaign_id": item["campaign_id"],
        "rationale": rationale,
        "expected_effect": (
            "정보성 신호 — 실행 대상 아님(빠른 루프 관찰, D-NAO-4). "
            "입찰/예산 방향 결정은 08:00 느린 루프(growth_sweeper/budget_allocator) 소관."
        ),
        "status": "pending",
    }


def _cpc_proposal(item: dict) -> dict:
    rationale = (
        f"[trigger_watch] CPC 급등 — {item['ad_date']} {item['hour']}시 이번시간 "
        f"CPC={item['current_cpc']}원 vs 최근 {CPC_SPIKE_LOOKBACK_DAYS}일 평균 "
        f"CPC={item['baseline_cpc']}원(배수={item['ratio']}, 클릭 {item['clk']})."
    )
    return {
        "proposal_type": PROPOSAL_TYPE_CPC,
        "target_type": "campaign", "target_id": item["campaign_id"], "campaign_id": item["campaign_id"],
        "rationale": rationale,
        "expected_effect": (
            "정보성 신호 — 실행 대상 아님(빠른 루프 관찰, D-NAO-4). "
            "입찰 방향 결정은 08:00 느린 루프(bid_simulator) 소관."
        ),
        "status": "pending",
    }


def _ratio_text(ratio: float) -> str:
    """배수를 사람 말로. 극단 저속에서 `{:.1f}`가 「기대의 0.0배」로 뭉개지는 것을 막는다
    (적대 리뷰 R1 P2-4 실측: ratio 0.0002 → "0.0배"). 0.0배는 정보가 아니라 잡음이다."""
    r = float(ratio)
    if r < 0.05:
        return "기대치의 5%도 안 됨"
    return f"기대의 {r:.1f}배"


def _pacing_human(item: dict) -> dict:
    """Slack 본문용 사람 문장(D-NAO-249). rationale과 **별개다** — rationale은
    retro_pacing_scorer가 정규식으로 파싱하는 기계용 문자열이라 손대지 않는다.

    기대 소진액은 expected_pace(일예산 대비 비율) × 일예산으로 되돌린다 — 「배수 0.2」보다
    「6.6만 원쯤 나갔을 자리에 1.2만 원」이 사람에게 훨씬 빨리 읽힌다."""
    over = item["kind"] == "overpace"
    expected_won = int(round(float(item["expected_pace"]) * float(item["daily_budget"])))
    return {
        "headline": "예산 소진이 너무 빠름" if over else "예산 소진이 너무 느림",
        "detail": (
            f"{item['hour']}시 기준 {alert_humanizer.money(item['cost'])} 씀 "
            f"/ 하루예산 {alert_humanizer.money(item['daily_budget'])}\n"
            f"이 시각이면 {alert_humanizer.money(expected_won)}쯤 나갔을 자리 "
            f"({_ratio_text(item['ratio'])})\n"
            + (
                "→ 이 속도면 오늘 예산이 일찍 바닥나 오후 노출이 끊길 수 있습니다."
                if over else
                "→ 이대로면 오늘 예산을 다 못 씁니다."
            )
        ),
    }


def _cpc_human(item: dict) -> dict:
    """Slack 본문용 사람 문장(D-NAO-249) — CPC 급등."""
    return {
        "headline": "클릭 한 번 값이 갑자기 뛰었음",
        "detail": (
            f"{item['hour']}시 클릭 {alert_humanizer.count(item['clk'])}회 "
            f"평균 {alert_humanizer.money(item['current_cpc'])}\n"
            f"최근 {CPC_SPIKE_LOOKBACK_DAYS}일 평균은 {alert_humanizer.money(item['baseline_cpc'])} "
            f"({float(item['ratio']):.1f}배)\n"
            "→ 같은 예산으로 살 수 있는 클릭이 그만큼 줄어듭니다."
        ),
    }


_HUMAN_BY_TYPE: dict[str, Callable[[dict], dict]] = {
    PROPOSAL_TYPE_PACING: _pacing_human,
    PROPOSAL_TYPE_CPC: _cpc_human,
}


def _slack_payload(db: Session, alerts: list[tuple[str, dict, dict]]) -> list[dict]:
    """제안 dict + 사람 문장 + 캠페인 이름을 합친 **통지 전용** 사본(D-NAO-249).

    DB에 넣는 dict(NaverProposal(**p))와 갈라 둔다 — 모델에 없는 키를 섞으면 저장이 깨진다.
    이름 조회가 실패해도 통지는 나간다(ID 폴백) — 알림이 사라지는 게 더 나쁘다.
    """
    try:
        names = alert_humanizer.entity_names(
            db, "campaign", [item["campaign_id"] for _, item, _ in alerts],
        )
    except Exception as e:  # noqa: BLE001 — 이름은 가독성 향상분, 없으면 ID로 보낸다
        log.warning("trigger_watch 캠페인 이름 조회 실패(ID로 통지): %s", e)
        names = {}
    out = []
    for proposal_type, item, proposal in alerts:
        # ★새 트리거 유형이 사람 문장 없이 추가돼도 통지 자체는 나가야 한다(적대 리뷰 R1 P2-5):
        # KeyError로 죽으면 통지가 통째로 사라지는데 결과 dict은 "no_proposals"라 **원인을
        # 오보한다** — 유형 라벨만으로 떨어지는 게 훨씬 낫다(slack_notifier가 폴백을 갖는다).
        builder = _HUMAN_BY_TYPE.get(proposal_type)
        human = builder(item) if builder else {}
        out.append({**proposal, **human, "name": names.get(item["campaign_id"], "")})
    return out


def run_hourly(db: Session, *, ad_date: date | None = None) -> dict:
    """매시 :05 snapshot_naver_ad_hourly_job 직후 실행 — 페이싱 이탈 + CPC 급등 감지 →
    쿨다운 통과분만 즉시 제안 생성 + Slack. 순위 이탈은 이번 스코프 제외(모듈 docstring 참조).
    """
    now = kst_now()
    today = kst_today()  # 한 번만 계산 — find_pacing_anomalies에 그대로 넘겨 자정 경계 레이스 방지(codex 지적)
    ad_date = ad_date or today

    pacing = find_pacing_anomalies(db, ad_date, today=today)
    cpc = find_cpc_spikes(db, ad_date)

    candidates: list[tuple[str, dict, Callable[[dict], dict]]] = (
        [(PROPOSAL_TYPE_PACING, item, _pacing_proposal) for item in pacing]
        + [(PROPOSAL_TYPE_CPC, item, _cpc_proposal) for item in cpc]
    )

    recent = _recent_trigger_keys(db, {(pt, item["campaign_id"]) for pt, item, _ in candidates}, now)
    proposals: list[dict] = []
    alerts: list[tuple[str, dict, dict]] = []  # 통지 전용 — (유형, 감지 원본, 제안 dict)
    cooled_down = 0
    for proposal_type, item, builder in candidates:
        if (proposal_type, item["campaign_id"]) in recent:
            cooled_down += 1
            continue
        proposal = builder(item)
        proposals.append(proposal)
        alerts.append((proposal_type, item, proposal))

    saved: list[NaverProposal] = []
    try:
        for p in proposals:
            obj = NaverProposal(created_at=now, **p)  # _within_cooldown과 동일 시계(kst_now) 고정
            db.add(obj)
            saved.append(obj)
        db.flush()
        db.commit()
    except Exception:
        db.rollback()
        raise

    notify_result = {"sent": False, "reason": "no_proposals", "slack_ts": None, "proposal_count": 0}
    if proposals:
        try:
            # 사람이 읽는 본문으로 보낸다(D-NAO-249) — 이름·숫자 문장을 여기서 붙여 넘기는 게
            # harness의 몫이다(slack_notifier는 DB를 모르는 순수 함수, 원칙18-6).
            notify_result = slack_notifier.notify(_slack_payload(db, alerts))
        except Exception as e:  # noqa: BLE001 — slack 실패는 저하만, 제안 저장은 이미 끝남
            log.exception("trigger_watch slack 발송 실패: %s", e)

    result = {
        "ad_date": ad_date.isoformat(), "pacing_anomalies": len(pacing), "cpc_spikes": len(cpc),
        "cooled_down": cooled_down, "generated": len(saved), "slack": notify_result,
    }
    log.info("trigger_watch run_hourly: %s", result)
    return result
