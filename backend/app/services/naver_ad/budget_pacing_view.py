# budget_pacing_view.py — 예산 소진 곡선 SA (D-NAO-105 Phase 2, 계획서 §4-ⓒ).
"""역할(SA·단일 책임·읽기 전용): 선택한 날짜의 **시간별 누적 소진 곡선**과 "예산을 다 써서
멈춘 구간(암전)"을 캠페인별로 만든다. 판정 문장은 이 곡선에서 직접 읽히는 사실만 말한다 —
증액 이력·상태 배지 같은 다른 축은 하니스가 합친다.

데이터 원천은 `naver_hourly_snapshot` 하나다(당일·과거 공통). 이 테이블은 **누적값**이라
시간당 지출은 인접 스냅샷의 차분이다 — 그냥 cost를 시간별로 그리면 매시간 하루치를 쓴 것처럼
보인다.

★암전 검출 재설계(2026-07-28 리뷰 실측). 처음엔 "증분 0 **이고** 소진율 ≥0.98"로 잡았는데
  **구조적 미탐**이 있었다: 03 아이폰_강화유리 07-27은 19시에 48,699원(예산 50,000의 97.4%)을
  쓰고 20~23시 4시간을 통째로 멈췄는데, 0.98에 0.6%p 모자라 한 건도 안 잡혔다. 네이버는
  예산 100%를 채우고 멈추는 게 아니라 **임박 지점에서 서빙을 멈춘다** — 소진율 단일 임계로는
  이 미탐이 계속 남는다. 그래서 두 신호의 역할을 바꿨다:

    1차 신호(트리거) = **증분 0이 연속 MIN_BLACKOUT_HOURS시간**  ← 실제로 멈췄다는 사실
    2차 신호(귀속)   = 멈추기 **직전** 소진율 ≥ NEAR_BUDGET_SPEND_RATIO ← 예산 탓이라는 근거

  ★소진율은 **멈춘 시점의 값**을 쓴다(그날 최종값이 아니다). 07-27 실측에서 최종 158%인
    캠페인이 새벽 5시에 5시간 멈춘 적이 있다 — 최종값으로 판정하면 그 새벽 정지가 "예산
    소진"이 되는데, 그 시각엔 예산의 몇 %도 안 쓴 상태였다.
  ★증분 0 구간 자체는 흔하다(07-27 실측 37건). 대부분 그 시점 소진율이 1~19%인 저볼륨
    구간이라 예산과 무관하다 — 귀속 근거 없이 "예산 때문에 멈췄다"고 말하지 않는다. 애매한
    구간(UNCERTAIN_SPEND_RATIO~NEAR_BUDGET)은 **단언하지 않고** 관찰 문장만 낸다(원칙22).

★수집 결번(오탐 방지): 시각이 이어지지 않으면 "몇 시간"을 셀 수 없다. 구간은 **시각이 연속인
  경우에만** 이어 붙이고, 구간 끝 다음 시각이 관측되지 않았으면 "적어도 N시간"으로 물러선다 —
  없는 정밀도를 주장하지 않는다.
★집행이 시작되기 전(누적 0원)의 0증분은 구간으로 세지 않는다 — 새벽 무노출을 '멈췄다'고
  말하면 매일 모든 캠페인이 멈춘 것이 된다.

★일예산이 없으면 소진율도 암전도 판정하지 않는다(None). 0%로 그리면 거짓이다(원칙22).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models import NaverHourlySnapshot

# ── 검출 임계(전부 노출 — 이 숫자가 바뀌면 화면이 하는 말이 바뀐다) ──────────────
# 1차 신호: 증분 0이 몇 시간 연속이어야 "멈췄다"고 보는가. 1시간은 그냥 한산한 시간과 구분이
# 안 된다.
MIN_BLACKOUT_HOURS = 2
# 2차 신호: 멈추기 직전 소진율이 이 이상이면 **예산 때문**이라고 말한다. 0.98이 아니라 0.90인
# 이유는 위 03 실측(97.4%에서 멈춤).
NEAR_BUDGET_SPEND_RATIO = 0.90
# 이 사이(0.60~0.90)에서 멈춘 것은 예산 탓이라 **단언하지 않고** 관찰만 한다.
UNCERTAIN_SPEND_RATIO = 0.60

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


def find_stall_runs(points: list[dict]) -> list[dict]:
    """증분 0이 연속인 구간 목록. **순수 함수**(DB 없음 — 판정 규칙만 여기서 검증된다).

    반환 1건: {hours, start_hour, length, ratio_before, truncated}
      · hours        : 멈춰 있던 **시각들**(전부 관측된 시각. 결번은 구간을 끊는다)
      · ratio_before : 멈추기 **직전** 소진율(귀속 판단의 유일한 근거. 일예산 없으면 None)
      · truncated    : 구간 끝 다음 시각이 관측되지 않았고 그 뒤에 관측이 더 있다
                       → 실제로는 더 길 수 있다("적어도 N시간"으로 물러선다)

    ★비교는 `cost` 동일성으로 한다(`hour_cost == 0`이 아니라). 재집계로 누적이 **줄어든**
      시각을 hour_cost가 0으로 깎아 보여주는데, 그건 '멈춤'이 아니라 '보정'이다.
    """
    observed = {p["hour"] for p in points}
    runs: list[dict] = []
    run: list[int] = []
    ratio_before: float | None = None
    prev: dict | None = None

    def _flush() -> None:
        if len(run) < MIN_BLACKOUT_HOURS:
            return
        end = run[-1]
        runs.append({
            "hours": list(run),
            "start_hour": run[0],
            "length": len(run),
            "ratio_before": ratio_before,
            # 다음 시각이 관측 안 됐는데 그 뒤엔 관측이 있다 = 중간 결번으로 구간이 잘렸다.
            # 하루의 마지막 관측이면 자른 게 아니라 그냥 끝(또는 진행 중)이다.
            "truncated": (end + 1) not in observed and any(h > end for h in observed),
        })

    for p in points:
        stalled = (
            prev is not None
            and p["hour"] == prev["hour"] + 1     # 시각이 이어질 때만(결번은 구간을 끊는다)
            and p["cost"] == prev["cost"]
            and prev["cost"] > 0                  # 집행이 시작된 뒤에만
        )
        if stalled:
            if not run:
                ratio_before = prev["spend_ratio"] if prev else None
            run.append(p["hour"])
        else:
            _flush()
            run, ratio_before = [], None
        prev = p
    _flush()
    return runs


def is_budget_caused(run: dict, *, confirmed: bool = False) -> bool:
    """멈춘 이유를 예산으로 **귀속할 근거가 있는가**. 근거 없이는 귀속하지 않는다.

    confirmed = 네이버 statusReason이 '예산 도달'(D-NAO-97) — 그 자체가 확증이라 소진율을
    따지지 않는다(일예산 값을 못 구한 캠페인도 이 경로로는 잡힌다)."""
    if confirmed:
        return True
    ratio = run["ratio_before"]
    return ratio is not None and ratio >= NEAR_BUDGET_SPEND_RATIO


def is_uncertain(run: dict) -> bool:
    """예산 탓이라 말하기엔 모자라지만 무시하기엔 큰 정지(0.60~0.90)."""
    ratio = run["ratio_before"]
    return ratio is not None and UNCERTAIN_SPEND_RATIO <= ratio < NEAR_BUDGET_SPEND_RATIO


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
        for r in rows:
            cost = int(r.cost or 0)
            delta = cost - prev_cost
            points.append({
                "hour": int(r.snapshot_hour),
                "cost": cost,
                "hour_cost": max(delta, 0),   # 음수 증분(재집계 보정)은 0으로 — 그래프 왜곡 방지
                "spend_ratio": round(cost / budget, 4) if budget and budget > 0 else None,
                "imp": int(r.imp or 0),
                "clk": int(r.clk or 0),
            })
            prev_cost = cost

        confirmed = cid in limited
        runs = find_stall_runs(points)
        budget_runs = [r for r in runs if is_budget_caused(r, confirmed=confirmed)]
        uncertain_runs = [
            r for r in runs if is_uncertain(r) and not is_budget_caused(r, confirmed=confirmed)
        ]

        final_cost = points[-1]["cost"] if points else 0
        final_ratio = points[-1]["spend_ratio"] if points else None
        curves.append({
            "campaign_id": cid,               # 화면 미표시(딥링크·title 전용, D-NAO-103①)
            "campaign_name": names.get(cid) or "이름 없는 광고",
            "daily_budget": budget,
            "spend_total": final_cost,
            "spend_ratio": final_ratio,
            "points": points,
            "blackout_hours": sorted({h for r in budget_runs for h in r["hours"]}),
            "blackout_sentence": _blackout_sentence(
                budget_runs, budget, final_ratio, confirmed=confirmed
            ),
            # 예산 탓이라 단언할 근거가 없는 정지 — 사실만 말하고 원인은 말하지 않는다.
            "stall_sentence": _stall_sentence(uncertain_runs),
        })

    curves.sort(key=lambda c: (-c["spend_total"], c["campaign_name"]))
    return curves


def _hours_phrase(run: dict) -> str:
    """'4시간' / 결번으로 잘렸으면 '적어도 4시간'."""
    return f"적어도 {run['length']}시간" if run["truncated"] else f"{run['length']}시간"


def _blackout_sentence(
    budget_runs: list[dict], budget: int | None, final_ratio: float | None, *, confirmed: bool
) -> str | None:
    """암전 한 문장. 판정 불가(일예산 미설정)면 그 사실을 말하고, 멈춘 적 없으면 None."""
    if not budget_runs:
        if budget is None:
            return "하루 예산이 정해져 있지 않아 '예산 때문에 멈췄는지'는 판단할 수 없습니다."
        if final_ratio is not None and final_ratio >= NEAR_BUDGET_SPEND_RATIO:
            return "예산을 거의 다 썼습니다. 아직 멈춘 구간은 없습니다."
        return None

    first = min(budget_runs, key=lambda r: r["start_hour"])
    total = sum(r["length"] for r in budget_runs)
    body = (
        f"{_hour_words(first['start_hour'])}쯤 하루 예산을 다 써서 그 뒤로 "
        f"{_hours_phrase(first)} 동안 광고가 나가지 않았습니다."
    )
    if len(budget_runs) > 1:
        body += f" 이날 멈춰 있던 시간은 모두 {total}시간입니다."
    if any(r["truncated"] for r in budget_runs):
        body += " (중간에 기록이 빠진 시간대가 있어 실제로는 더 길 수 있습니다.)"
    if confirmed:
        body += " 네이버 광고 상태도 '예산 도달'로 나옵니다."
    return body


def _stall_sentence(uncertain_runs: list[dict]) -> str | None:
    """예산 탓이라 단언할 수 없는 정지 — **사실만** 말한다(원인 추정 금지, 원칙22)."""
    if not uncertain_runs:
        return None
    r = max(uncertain_runs, key=lambda x: x["length"])
    pct = round((r["ratio_before"] or 0) * 100)
    return (
        f"{_hour_words(r['start_hour'])}쯤부터 {_hours_phrase(r)} 동안 집행이 없었습니다. "
        f"그때 예산의 {pct}%를 쓴 상태라, 예산 때문인지는 확실하지 않습니다."
    )
