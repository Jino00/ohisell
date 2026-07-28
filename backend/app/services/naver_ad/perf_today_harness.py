# perf_today_harness.py — 광고 성과(사장님 뷰) ①오늘 한눈에 + ②오늘 시스템이 한 일 하니스
# (D-NAO-104 Phase 1, docs/PLAN_naver-ad-performance-view.md §2 H1).
"""역할(Harness): SA들을 조합해 "오늘 광고 잘 돌고 있나 / 예산 다 썼나 / 오늘 시스템이 뭘 했나"
세 질문에 한 번에 답하는 응답을 만든다. **읽기 전용** — 쓰기·조작은 이 페이지의 스코프 밖이다
(조작은 커맨드 센터·최적화 콘솔이 계속 담당한다, 계획서 §0-1).

조합하는 SA(원칙18-6: SA끼리 직접 부르지 않는다 — 원료는 이 하니스가 모아서 넘긴다):
  · campaign_roster       이름·광고종류·상태·관리주체·자동운영(최근 30일 성과는 참고)
  · today_proxy_revenue   캠페인별 당일 매출 프록시(매핑 없으면 None=알 수 없음)
  · campaign_target_resolver  목표/손익분기 ROAS(override → 상품 파생 → 계정 기본값)
  · NaverHourlySnapshot read  당일 누적 비용·노출·클릭·일예산(D-0 유일 원천)
  · change_log_narrator   오늘 변경 이력 → 한글 문장

★창 관례(계획서 §4 공통 — 어긋나면 화면끼리 숫자가 안 맞는다): **당일(D-0) 숫자는
  naver_ad_daily에서 오지 않는다**(그날 확정 적재 전). 비용·노출·클릭은 시간별 스냅샷,
  매출은 스마트스토어 실주문 프록시가 유일 원천이다.

★정직 규약(원칙22·계획서 §0-5): 모르는 값은 0이 아니라 None으로 내보낸다. 특히
  파워링크·브랜드검색은 상품 매핑이 원리적으로 없어 `roas_today_proxy=None`이다 — 이걸
  0.00배로 렌더하면 "성과가 바닥"이라는 **거짓 단언**이 된다.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import (
    NaverCampaignSettings,
    NaverChangeLog,
    NaverHourlySnapshot,
    NaverProductBep,
)
from app.services.naver_ad import (
    budget_pacing,
    campaign_roster,
    campaign_target_resolver,
    change_log_narrator,
    naver_execution_harness,
    today_proxy_revenue,
)
from app.services.naver_ad.alert_humanizer import campaign_type_label, clean_name
from app.utils.kst import kst_now

ROSTER_WINDOW_DAYS = 30

DATA_NOTE = (
    "오늘 매출은 스마트스토어 실주문 기준 추정치입니다 — 광고로 생긴 매출의 상한이라 "
    "실제 광고 성과는 이보다 낮습니다. 확정 성과는 다음 날 광고 리포트에서 확인됩니다."
)

QUIET_REASON = "오늘은 바꿀 만한 신호가 없었습니다."

# ② "오늘 시스템이 한 일"이 세는 액션 집합.
#   · EXECUTION_ACTIONS  = 우리가 광고 API에 실제로 쓴 것(update_bid/update_budget/…)
#   · budget_pacing.PACING_ACTIONS = BP 레인이 **라벨만 분리해** 남기는 예산 증액/원복
#     (budget_up_pacing 등). 실행 경로는 update_budget 하나지만 change_log에 다른 이름으로
#     찍히므로 EXECUTION_ACTIONS만 보면 **BP가 한 일이 통째로 안 보인다**(D-NAO-102 ⑥).
TODAY_ACTIONS: frozenset[str] = frozenset(
    naver_execution_harness.EXECUTION_ACTIONS | set(budget_pacing.PACING_ACTIONS)
)

# 네이버 statusReason → 사람 말(D-NAO-97 원문 → D-NAO-103 표기). 모르는 코드는 **원문을
# 노출하지 않고** status(on/off)만으로 말한다 — 내부 코드가 화면에 새는 것보다 덜 구체적인
# 편이 낫다.
_STATUS_REASON_LABEL = {
    "ELIGIBLE": "정상 노출 중",
    "CAMPAIGN_PAUSED": "정지됨",
    "CAMPAIGN_LIMITED_BY_BUDGET": "오늘 예산을 다 써서 멈춤",
    "ADGROUP_PAUSED": "정지됨",
    "CAMPAIGN_UNDER_REVIEW": "검수 중",
    "ADGROUP_UNDER_REVIEW": "검수 중",
    "AD_UNDER_REVIEW": "검수 중",
}
_REVIEW_LABEL = "검수 중"

# 상태 라벨 → 한 줄 평 앞머리 문장. 라벨을 그대로 문장에 끼우면 "지금은 정지됨입니다"처럼
# 어색해진다(라이브 실측) — 문장은 문장으로 따로 둔다(D-NAO-103 ③).
_STATUS_SENTENCE = {
    "정지됨": "지금은 광고가 멈춰 있습니다.",
    "검수 중": "지금은 검수를 기다리는 중입니다.",
    "오늘 예산을 다 써서 멈춤": "오늘 예산을 다 써서 지금은 광고가 멈춰 있습니다.",
}


def _status_labels(status: str | None, status_reason: str | None) -> tuple[str, str | None]:
    """(상태 라벨, 검수 라벨). 검수는 별도 축이라 따로 돌려준다(정상 노출과 배타가 아님)."""
    reason = (status_reason or "").upper()
    review = _REVIEW_LABEL if reason.endswith("_UNDER_REVIEW") else None
    label = _STATUS_REASON_LABEL.get(reason)
    if label is None:
        label = "정지됨" if (status or "") == "off" else "운영 중"
    return label, review


def _managed_by_label(optimizer: str, auto_operate: bool) -> str:
    """관리 주체 한 줄. optimizer와 auto_operate는 **다른 축**이다 — 우리 소유인데 자동 레인이
    꺼진 상태(D-NAO-92의 03)가 실재하므로 하나로 뭉뚱그리지 않는다."""
    if optimizer == "ours":
        return "우리가 자동으로 운영" if auto_operate else "우리 담당 · 자동 운영은 꺼둠"
    if optimizer == "mop":
        return "대행사가 운영"
    return "직접 관리(자동 운영 안 함)"


def _latest_snapshots(db: Session, day: date) -> dict[str, NaverHourlySnapshot]:
    """캠페인별 그날 **마지막** 시간별 스냅샷(누적값이라 최신이 당일 확정치). 1쿼리."""
    rows = (
        db.query(NaverHourlySnapshot)
        .filter(NaverHourlySnapshot.ad_date == day)
        .order_by(NaverHourlySnapshot.snapshot_hour.asc())
        .all()
    )
    latest: dict[str, NaverHourlySnapshot] = {}
    for r in rows:
        latest[r.campaign_id] = r  # 오름차순이므로 마지막 대입이 최신
    return latest


def _today_change_rows(db: Session, day: date) -> list[NaverChangeLog]:
    """오늘 우리가 한 시도 전건(집행 + 가드 차단 + 쓰기 실패).

    필터 규약은 `/change-log`의 actor='ours' · include_dry_run=False · include_blocked=True와
    같다(계획서 §4-ⓐ): dry-run을 섞으면 아무것도 안 했는데 일한 것처럼 보이고(D-47-h),
    외부 감지(entity_sync)·내부 설정 변경(optimizer_change)을 섞으면 남이 한 일을 우리가
    했다고 말하게 된다. 차단·실패 행은 after_value가 없고 outcome='failed'인 모양만 받는다 —
    '원인 불명으로 after가 빈 행'까지 주워 담아 차단 배지를 달지 않는다."""
    start = datetime.combine(day, datetime.min.time())
    end = datetime.combine(day, datetime.max.time())
    rows = (
        db.query(NaverChangeLog)
        .filter(
            NaverChangeLog.changed_at >= start,
            NaverChangeLog.changed_at <= end,
            NaverChangeLog.action.in_(sorted(TODAY_ACTIONS)),
            NaverChangeLog.dry_run.is_(False),
        )
        .order_by(NaverChangeLog.changed_at.asc())
        .all()
    )
    return [r for r in rows if r.after_value is not None or r.outcome == "failed"]


def _resolve_roas_lines(
    db: Session, campaign_ids: list[str]
) -> dict[str, dict[str, float | None]]:
    """캠페인별 {target_roas, bep_roas} — 우선순위는 campaign_target_resolver와 동일
    (① override ② 상품 파생 ③ 계정 기본값).

    ★계정 기본값은 주문 테이블 전체 집계라 **캠페인마다 부르면 안 된다**(46캠페인 × 전체
    스캔). 하니스가 한 번 구해 폴백으로 나눠 쓴다 — resolve_target_roas를 캠페인마다 호출하지
    않는 이유가 이것이다(값 자체는 동일)."""
    overrides = {
        s.campaign_id: s.target_roas_override
        for s in db.query(NaverCampaignSettings).all()
        if s.target_roas_override is not None
    }
    account_target = campaign_target_resolver.account_default_target_roas(db)
    account_bep = campaign_target_resolver.account_default_bep_roas(db)
    mapped = set(today_proxy_revenue.product_ids_by_campaign(db, campaign_ids))

    out: dict[str, dict[str, float | None]] = {}
    for cid in campaign_ids:
        target: Decimal | None = overrides.get(cid)
        bep: Decimal | None = None
        if cid in mapped:
            if target is None:
                target = campaign_target_resolver.weighted_product_value_for_campaign(
                    db, cid, NaverProductBep.target_roas
                )
            bep = campaign_target_resolver.weighted_product_value_for_campaign(
                db, cid, NaverProductBep.bep_roas
            )
        if target is None:
            target = account_target
        if bep is None:
            bep = account_bep
        out[cid] = {
            "target_roas": round(float(target), 4) if target is not None else None,
            "bep_roas": round(float(bep), 4) if bep is not None else None,
        }
    return out


def _verdict(
    *, status_label: str, spend_today: int, spend_ratio: float | None,
    roas: float | None, target: float | None, bep: float | None, unknown_reason: str | None,
) -> str:
    """카드 한 줄 평. 판단 근거가 없으면 **판단하지 않는다**(모름을 성과로 바꾸지 않는다)."""
    parts: list[str] = []
    status_sentence = _STATUS_SENTENCE.get(status_label)
    if status_sentence:
        parts.append(status_sentence)
    if spend_today <= 0:
        parts.append("오늘은 아직 집행된 광고비가 없습니다.")
        return " ".join(parts)

    if roas is None:
        parts.append(f"오늘 {spend_today:,}원을 썼습니다.")
        parts.append(unknown_reason or "오늘 매출은 아직 알 수 없습니다.")
    else:
        if target is not None and roas >= target:
            parts.append("목표를 넘고 있습니다.")
        elif bep is not None and roas >= bep:
            parts.append("남기는 하지만 목표에는 못 미칩니다.")
        elif bep is not None:
            parts.append("지금은 손익분기 아래입니다.")
        else:
            parts.append("비교할 목표치가 없어 좋고 나쁨은 판단하지 않았습니다.")
        parts.append(f"오늘 {spend_today:,}원을 썼습니다.")
    if spend_ratio is not None:
        parts.append(f"하루 예산의 {round(spend_ratio * 100)}%입니다.")
    else:
        parts.append("하루 예산은 정해져 있지 않습니다.")
    return " ".join(parts)


def build(db: Session, *, now: datetime | None = None) -> dict:
    """①오늘 한눈에 + ②오늘 시스템이 한 일. 파라미터 없음(오늘 고정, 계획서 §4-ⓐ)."""
    now = now or kst_now()
    today = now.date()

    roster = campaign_roster.build(db, days=ROSTER_WINDOW_DAYS, today=today)
    campaign_ids = [r["campaign_id"] for r in roster]
    snapshots = _latest_snapshots(db, today)
    proxy = today_proxy_revenue.build(db, campaign_ids, today)
    lines = _resolve_roas_lines(db, campaign_ids)

    cards: list[dict] = []
    for r in roster:
        cid = r["campaign_id"]
        snap = snapshots.get(cid)
        spend_today = int(snap.cost or 0) if snap else 0
        daily_budget = int(snap.daily_budget) if snap and snap.daily_budget else None
        spend_ratio = (
            round(spend_today / daily_budget, 4) if daily_budget and daily_budget > 0 else None
        )
        rev = proxy.get(cid, {})
        revenue = rev.get("revenue")
        # 광고비 0이면 ROAS는 정의되지 않는다 — 0.0이 아니라 '알 수 없음'.
        roas = round(revenue / spend_today, 4) if revenue is not None and spend_today > 0 else None
        status_label, review_label = _status_labels(r.get("status"), r.get("status_reason"))
        roas_unknown_reason = None
        if roas is None:
            roas_unknown_reason = (
                rev.get("reason")
                if revenue is None
                else "광고비가 없어 오늘 ROAS는 계산되지 않습니다."
            )

        cards.append({
            "campaign_id": cid,  # 화면 미표시 — 딥링크·툴팁 전용(D-NAO-103①)
            "name": clean_name(r.get("name")) or "이름 없는 광고",
            "type_label": campaign_type_label(r.get("campaign_type")) or "기타 지면",
            "status_label": status_label,
            "review_label": review_label,
            "managed_by_label": _managed_by_label(r.get("optimizer", "none"), r.get("auto_operate", False)),
            "auto_operate": bool(r.get("auto_operate")),
            "spend_today": spend_today,
            "daily_budget": daily_budget,
            "spend_ratio": spend_ratio,
            "imp_today": int(snap.imp or 0) if snap else 0,
            "clk_today": int(snap.clk or 0) if snap else 0,
            "revenue_today_proxy": revenue,
            "roas_today_proxy": roas,
            "roas_unknown_reason": roas_unknown_reason,
            "target_roas": lines.get(cid, {}).get("target_roas"),
            "bep_roas": lines.get(cid, {}).get("bep_roas"),
            "shared_product_count": rev.get("shared_product_count", 0),
            "active_today": spend_today > 0,
            "verdict_sentence": _verdict(
                status_label=status_label, spend_today=spend_today, spend_ratio=spend_ratio,
                roas=roas, target=lines.get(cid, {}).get("target_roas"),
                bep=lines.get(cid, {}).get("bep_roas"), unknown_reason=roas_unknown_reason,
            ),
        })

    # 오늘 쓴 돈이 큰 순 → 그다음 최근 30일 지출 순(오늘 0원 캠페인도 숨기지 않는다).
    cost_30d = {r["campaign_id"]: int(r.get("cost") or 0) for r in roster}
    cards.sort(key=lambda c: (-c["spend_today"], -cost_30d.get(c["campaign_id"], 0), c["name"]))

    rows = _today_change_rows(db, today)
    sentences = change_log_narrator.narrate(db, rows)
    executed = sum(1 for s in sentences if s["state"] == change_log_narrator.STATE_EXECUTED)
    blocked = sum(1 for s in sentences if s["state"] == change_log_narrator.STATE_BLOCKED)
    unknown = sum(1 for s in sentences if s["state"] == change_log_narrator.STATE_UNKNOWN)

    return {
        "as_of": now.isoformat(),
        "date": today.isoformat(),
        "data_note": DATA_NOTE,
        "campaigns": cards,
        "totals": {
            "spend_today": sum(c["spend_today"] for c in cards),
            "campaigns_active_today": sum(1 for c in cards if c["active_today"]),
            "campaigns_total": len(cards),
        },
        "today_actions": {
            "executed_count": executed,
            "blocked_count": blocked,
            "unknown_count": unknown,
            "items": sentences,
            "quiet_reason": QUIET_REASON if not sentences else None,
        },
    }
