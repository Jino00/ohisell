# event_impact_scorer.py — SA ⑥개선 타임라인의 전후 비교 (성과뷰 Phase 3, 계획서 §3-3)
"""역할(SA·**순수 계산**): 이벤트 하나의 **전후 7일 지표**를 내고, 그 숫자를 얼마나 믿어도
되는지 라벨을 함께 단다.

★DB를 읽지 않는다(원칙 18-6, 계획서 §2가 든 예시가 정확히 이 SA다 —
  `event_impact_scorer(events, daily_rows)`). 일별 행은 **하니스가 한 번에 집계해 넘긴다.**
  종전엔 이 SA가 metrics_aggregator를 직접 불러 창마다 쿼리를 돌았다(날짜 카드 17장 = 34쿼리).

★이 파일의 핵심은 계산이 아니라 **정직 규약**이다(계획서 §3-3 · 원칙22).
  이 시스템은 이벤트가 거의 하루 1건씩 나온다 — 전후 창은 반드시 서로 겹친다. 그래서:
    · confounded_count — 창 안 **다른 날**의 변경을 전부 센다.
    · same_day_count — 같은 날 **함께** 확정된 변경 수. 날짜 단위로 접어 계산하므로 그날의
      결정들은 서로를 겹침으로 세지 않지만(자기 자신이다), 여러 건이 한날에 몰렸다는 사실은
      비교 가능성을 떨어뜨리므로 문장이 반드시 말한다(리뷰 P3-1).
    · post_days_available < 7 — 아직 안 지난 날은 세지 않고 "관찰 중 (N/7일)"로 말한다.
    · 문구는 "개선됐습니다"가 아니라 **"이 변경 전후 7일은 이랬습니다"**.
  인과 주장은 카나리 몫이다(ref 31 정직 경계 승계). 이 화면은 관찰까지만 한다.

★**"측정된 0"과 "데이터 없음"을 구분한다**(원칙22 · 계획서 §0-5). 달력상 창이 7일이어도
  그 기간에 적재된 행이 하나도 없으면 값은 `None`이다 — `metrics_aggregator`의
  `coalesce(sum,0)`을 그대로 흘리면 화면이 "광고비 0원 → 0원"이라고 **거짓을 단언**한다.
  `days`(달력)와 `days_with_data`(실제 적재된 날)를 따로 낸다.

★창 규약:
  · 사전 = [D-7, D-1], 사후 = [D+1, D+7]. **변경일 D 자신은 양쪽 어디에도 넣지 않는다** —
    장중에 적용되면 그날은 전도 후도 아닌 반쪽이라, 어느 쪽에 넣어도 그쪽을 오염시킨다.
  · 사후 상한은 **어제**다. naver_ad_daily는 오늘(D-0) 확정 적재 전이라 오늘을 세면
    "매출이 급감했다"는 거짓 신호가 나온다(계획서 §4 창 관례).
"""
from __future__ import annotations

from datetime import date, timedelta

WINDOW_DAYS = 7
# 화면에 나열할 겹친 이벤트 최대 개수. 전체 개수는 confounded_count로 따로 나간다.
MAX_CONFOUNDED_LISTED = 5


def _empty(days: int) -> dict:
    return {"days": max(0, days), "days_with_data": 0,
            "cost": None, "conv_amt": None, "roas": None}


def _window(daily: dict[str, dict], start: date, end: date) -> dict:
    """[start, end] 합산. **적재된 행이 하나도 없으면 값은 None**(0이 아니다).

    daily: {"YYYY-MM-DD": {"cost": int, "conv_amt": int}, ...} — 하니스가 넘긴 일별 집계.
    """
    days = (end - start).days + 1
    if days <= 0:
        return _empty(0)
    cost = conv = 0
    seen = 0
    d = start
    while d <= end:
        row = daily.get(d.isoformat())
        if row is not None:
            cost += int(row.get("cost") or 0)
            conv += int(row.get("conv_amt") or 0)
            seen += 1
        d += timedelta(days=1)
    if seen == 0:
        return _empty(days)
    return {
        "days": days,
        "days_with_data": seen,
        "cost": cost,
        "conv_amt": conv,
        "roas": round(conv / cost, 4) if cost > 0 else None,
    }


def _sentence(pre: dict, post: dict, confounded_count: int, complete: bool,
              same_day_count: int = 1) -> str:
    """사장님 문장. **인과를 주장하지 않는다.**"""
    if post["days"] <= 0:
        return "바꾼 지 하루가 지나지 않아 아직 비교할 수치가 없습니다."
    if post["days_with_data"] == 0 or pre["days_with_data"] == 0:
        # 값이 없는데 "0원 → 0원"이라고 말하면 안 된다 — 안 쓴 것과 기록이 없는 것은 다르다.
        return "이 기간에는 쌓인 광고 기록이 없어 전후를 비교할 수 없습니다."
    head = f"이 변경 전 {pre['days']}일과 후 {post['days']}일은 이랬습니다."
    if pre["days_with_data"] < pre["days"] or post["days_with_data"] < post["days"]:
        head += (
            f" (기록이 있는 날만 셌습니다 — 전 {pre['days_with_data']}일·"
            f"후 {post['days_with_data']}일)"
        )
    if pre["roas"] is None or post["roas"] is None:
        head += " 한쪽 기간에 광고비가 없어 배수는 비교하지 못했습니다."
    if not complete:
        head += f" 아직 관찰 중입니다({post['days']}/{WINDOW_DAYS}일)."
    if same_day_count > 1:
        head += f" 이날은 {same_day_count}건을 함께 바꿔서 서로 떼어 볼 수 없습니다."
    if confounded_count:
        # ★건수를 말한다. 라이브에선 겹치는 변경이 수십 건이라 "다른 변경도 있었다"만으로는
        #   그 정도가 전해지지 않는다 — 39건이 겹쳤다면 이 비교는 사실상 못 읽는 것이고,
        #   화면은 그 사실을 숨기지 말아야 한다(계획서 §3-3 · 원칙22).
        head += f" 같은 기간에 다른 변경 {confounded_count}건이 함께 있어 이것만의 효과로 보기는 어렵습니다."
    return head


def score(
    event: dict,
    *,
    daily: dict[str, dict],
    all_events: list[dict],
    today: date,
    same_day_count: int = 1,
) -> dict | None:
    """이벤트(또는 날짜 묶음) 1건의 전후 비교. 날짜가 없으면 None(놓을 자리가 없다).

    daily      : 하니스가 넘긴 일별 집계 {"YYYY-MM-DD": {"cost","conv_amt"}}
    all_events : 겹침 판정용 전체 이벤트(자기 자신 포함 — ref_key로 걸러낸다)
    """
    eff = event.get("effective_date")
    if not eff:
        return None
    try:
        d = date.fromisoformat(eff)
    except (ValueError, TypeError):
        return None

    last_closed = today - timedelta(days=1)  # D-0은 확정 적재 전 — 사후 창의 상한
    pre = _window(daily, d - timedelta(days=WINDOW_DAYS), d - timedelta(days=1))
    post = _window(daily, d + timedelta(days=1), min(d + timedelta(days=WINDOW_DAYS), last_closed))
    complete = post["days"] >= WINDOW_DAYS

    lo, hi = (d - timedelta(days=WINDOW_DAYS)).isoformat(), (d + timedelta(days=WINDOW_DAYS)).isoformat()
    # ★겹침은 **다른 날**의 변경만 센다 — 날짜 단위로 접어 계산하므로 그날의 결정들은
    #   평가 대상 자체이지 교란 요인이 아니다. 대신 같은 날 몇 건이었는지는 문장이 말한다.
    confounded = sorted({
        other["ref_key"]
        for other in all_events
        if isinstance(other.get("effective_date"), str)
        and other["effective_date"] != eff
        and lo <= other["effective_date"] <= hi
    })

    return {
        "pre": pre,
        "post": {**post, "complete": complete},
        # 목록은 화면에 띄울 만큼만. 전체 개수는 따로 낸다 — 수십 개 키를 나열해도 못 읽는다.
        "confounded_with": confounded[:MAX_CONFOUNDED_LISTED],
        "confounded_count": len(confounded),
        "same_day_count": same_day_count,
        "sentence": _sentence(pre, post, len(confounded), complete, same_day_count),
    }
