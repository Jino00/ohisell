# budget_pacing_view.py — 예산 소진 곡선 SA (D-NAO-105 Phase 2, 계획서 §4-ⓒ).
"""역할(SA·단일 책임·읽기 전용): 선택한 날짜의 **시간별 누적 소진 곡선**과 "예산을 다 써서
멈춘 구간(암전)"을 캠페인별로 만든다. 판정 문장은 이 곡선에서 직접 읽히는 사실만 말한다 —
증액 이력·상태 배지 같은 다른 축은 하니스가 합친다.

데이터 원천은 `naver_hourly_snapshot` 하나다(당일·과거 공통). 이 테이블은 **누적값**이라
시간당 지출은 인접 스냅샷의 차분이다 — 그냥 cost를 시간별로 그리면 매시간 하루치를 쓴 것처럼
보인다.

★암전(멈춤) 판정(계획서 §4-ⓒ): 그 시간의 지출 증분이 0 **이고** 직전 소진율이 0.98 이상.
  둘 중 하나만으로는 안 된다 — 증분 0만 보면 새벽 무노출이 전부 '예산 소진'이 되고, 소진율만
  보면 예산을 다 쓰고도 계속 돌던 구간(네이버가 초과 집행하는 경우)까지 멈췄다고 말한다.
  캠페인의 현재 statusReason이 CAMPAIGN_LIMITED_BY_BUDGET이면 **확증**으로 문장에 반영한다
  (D-NAO-97에서 이 필드가 생긴 이유가 정확히 이 상황이다).

★일예산이 없으면 소진율도 암전도 판정하지 않는다(None). 0%로 그리면 거짓이다(원칙22).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models import NaverHourlySnapshot

# 직전 소진율이 이 값 이상일 때만 "증분 0 = 예산 때문에 멈췄다"고 말한다.
BLACKOUT_SPEND_RATIO = 0.98
LIMITED_BY_BUDGET_REASON = "CAMPAIGN_LIMITED_BY_BUDGET"


def _hour_words(hour: int) -> str:
    """13 → '오후 1시'. 화면 문장용(24시제 숫자는 사장님 뷰의 말투가 아니다)."""
    if hour == 0:
        return "밤 12시"
    if hour < 12:
        return f"오전 {hour}시"
    if hour == 12:
        return "낮 12시"
    return f"오후 {hour - 12}시"


def _snapshots(db: Session, day: date, campaign_ids: list[str] | None) -> dict[str, list]:
    """{campaign_id: [스냅샷…시간 오름차순]} — 1쿼리(N+1 금지)."""
    q = db.query(NaverHourlySnapshot).filter(NaverHourlySnapshot.ad_date == day)
    if campaign_ids is not None:
        if not campaign_ids:
            return {}
        q = q.filter(NaverHourlySnapshot.campaign_id.in_(campaign_ids))
    rows = q.order_by(NaverHourlySnapshot.snapshot_hour.asc()).all()
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(r.campaign_id, []).append(r)
    return out


def build(
    db: Session,
    day: date,
    *,
    campaign_ids: list[str] | None = None,
    names: dict[str, str] | None = None,
    limited_by_budget: set[str] | None = None,
) -> list[dict]:
    """캠페인별 소진 곡선 목록. 그날 스냅샷이 한 줄도 없는 캠페인은 **결과에 없다**
    (0원 곡선을 그리면 "0원 썼다"는 단언이 되는데, 실은 관측이 없는 것이다).

    names: {campaign_id: 표시 이름}. 없으면 호출부가 이름을 못 구한 것이므로 '이름 없는 광고'.
    limited_by_budget: 지금 statusReason이 예산 소진인 캠페인 집합(오늘 날짜에서만 의미 있음).
    """
    names = names or {}
    limited = limited_by_budget or set()
    curves: list[dict] = []

    for cid, rows in _snapshots(db, day, campaign_ids).items():
        # 일예산은 그날 마지막으로 관측된 값(중간에 증액되면 마지막 값이 현재 계약).
        budget = next((int(r.daily_budget) for r in reversed(rows) if r.daily_budget), None)
        points: list[dict] = []
        prev_cost = 0
        prev_ratio: float | None = None
        blackout_hours: list[int] = []

        for r in rows:
            cost = int(r.cost or 0)
            delta = cost - prev_cost
            ratio = round(cost / budget, 4) if budget and budget > 0 else None
            if (
                delta <= 0
                and prev_ratio is not None
                and prev_ratio >= BLACKOUT_SPEND_RATIO
            ):
                blackout_hours.append(int(r.snapshot_hour))
            points.append({
                "hour": int(r.snapshot_hour),
                "cost": cost,
                "hour_cost": max(delta, 0),   # 음수 증분(재집계 보정)은 0으로 — 그래프 왜곡 방지
                "spend_ratio": ratio,
                "imp": int(r.imp or 0),
                "clk": int(r.clk or 0),
            })
            prev_cost, prev_ratio = cost, ratio

        final_cost = points[-1]["cost"] if points else 0
        final_ratio = points[-1]["spend_ratio"] if points else None
        curves.append({
            "campaign_id": cid,               # 화면 미표시(딥링크·title 전용, D-NAO-103①)
            "campaign_name": names.get(cid) or "이름 없는 광고",
            "daily_budget": budget,
            "spend_total": final_cost,
            "spend_ratio": final_ratio,
            "points": points,
            "blackout_hours": blackout_hours,
            "blackout_sentence": _blackout_sentence(
                blackout_hours, budget, final_ratio, confirmed=cid in limited
            ),
        })

    curves.sort(key=lambda c: (-c["spend_total"], c["campaign_name"]))
    return curves


def _blackout_sentence(
    blackout_hours: list[int], budget: int | None, ratio: float | None, *, confirmed: bool
) -> str | None:
    """암전 한 문장. 판정 불가(일예산 미설정)면 그 사실을 말하고, 멈춘 적 없으면 None."""
    if budget is None:
        return "하루 예산이 정해져 있지 않아 '예산 때문에 멈췄는지'는 판단할 수 없습니다."
    if not blackout_hours:
        if ratio is not None and ratio >= BLACKOUT_SPEND_RATIO:
            return "예산을 거의 다 썼습니다. 아직 멈춘 구간은 없습니다."
        return None
    start = _hour_words(min(blackout_hours))
    tail = "네이버 광고 상태도 '예산 도달'로 나옵니다." if confirmed else ""
    body = (
        f"{start}쯤 하루 예산을 다 써서 그 뒤로 {len(blackout_hours)}시간 동안 "
        "광고가 나가지 않았습니다."
    )
    return f"{body} {tail}".strip()
