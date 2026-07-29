# ctr_alert_briefing.py — 소재 CTR 경보를 "사람이 읽는 브리핑"으로 조립하는 harness (D-NAO-103).
#   역할: ctr_alert(판정 SA)의 결과를 받아 ①그룹당 1행으로 합치고 ②ctr_alert_state로 신규/만성을
#   갈라 발화를 억제하고 ③alert_humanizer로 이름·용어를 사람 말로 바꿔 메시지를 만든다.
#   실제 발송(diary+Slack)은 호출부(auto_operator 일 레인) 몫 — 이 모듈은 텍스트만 돌려준다.
#
#   ★왜 개편했나(Jino 2026-07-28 실측): 구 브리핑은 "○ P. 아이패드 파워링크"에서 07-23부터
#   매일 5~8그룹을 같은 문구로 반복 발화했고, 메시지가 ID·W1/W3·"밴드 내≤4.0"·D-NAO 코드라
#   해석이 불가능했으며, 붙어 있던 "해당 그룹 탐색 래더 추가 UP 중지"는 파워링크에는 원천
#   미적용(탐색 래더=SHOPPING 전용)인 거짓 문구였다. 그래서 이 harness의 규칙은 셋이다:
#     1) 발화는 **신규 진입만**. 만성은 월요일 1회 요약으로 모아 보낸다.
#     2) 한 캠페인에서 3그룹 이상이면 **캠페인 1줄 + 그룹 상세 최대 3개**로 압축한다.
#     3) 쇼핑이 아닌 캠페인에는 탐색 래더 문구를 **쓰지 않는다**(실제로 적용되지 않으므로).
#   메시지 골격은 항상 "무슨 일 → 숫자 근거 → 권하는 행동" 순이다.
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily
from app.services.naver_ad import alert_humanizer, ctr_alert, ctr_alert_state
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

log = logging.getLogger(__name__)

_WEEKLY_WEEKDAY = 0        # 월요일(datetime.weekday(): 0=월…6=일) — 만성 건 주간 요약 발송일
_CAMPAIGN_COMPRESS_MIN = 3  # 한 캠페인에서 이 수 이상 동시 발화하면 캠페인 요약 + 상세 압축
_GROUP_DETAIL_TOP_N = 3     # 압축 시 표시할 그룹 상세 수(나머지는 "외 N개")
_RECENT_DAYS = 7            # 숫자 근거로 붙이는 캠페인 최근 창(지출·클릭·전환)
_RANK_BAND_TOP = 4.0        # ctr_alert._RANK_BAND_TOP과 같은 값 — 표시 문구용(판정은 SA가 이미 함)
_INDENT = "   "


def dedupe_alerts(alerts: list[dict]) -> list[dict]:
    """같은 (campaign_id, adgroup_id)가 W1·W3 동시 발화하면 한 그룹 한 건으로 합친다.
    window는 'W1+W3'처럼 병기하고, 숫자는 노출이 큰 쪽(=더 넓은 창)을 대표로 남긴다.
    입력 순서(첫 등장 순)를 보존한다 — 브리핑 출력이 결정적이어야 하므로."""
    merged: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for a in alerts:
        key = (a["campaign_id"], a["adgroup_id"])
        existing = merged.get(key)
        if existing is None:
            merged[key] = dict(a)
            order.append(key)
            continue
        windows = existing["window"].split("+")
        if a["window"] not in windows:
            existing["window"] = "+".join(windows + [a["window"]])
        if int(a.get("imp", 0)) > int(existing.get("imp", 0)):
            for f in ("imp", "clk", "avg_rank", "expected_clk", "kind", "trailing_imp"):
                if f in a:
                    existing[f] = a[f]
    return [merged[k] for k in order]


def _escalated(alert: dict, baseline: dict | None) -> bool:
    """만성이라 조용히 있던 건이 "눈에 띄게 나빠졌나" 판정(P2-2).

    직전 **통지 시점** 대비 노출 또는 기대클릭이 배수 이상으로 커졌으면 다시 알린다 —
    같은 문제라도 규모가 3배가 되면 그건 새 소식이다. 기준선이 없으면(통지 이력 없음)
    에스컬레이션하지 않는다(억제 유지)."""
    if not baseline:
        return False
    base_imp = int(baseline.get("imp") or 0)
    if base_imp > 0 and int(alert.get("imp", 0)) >= base_imp * ctr_alert_state.ESCALATION_MULTIPLE:
        return True
    base_exp = baseline.get("expected_clk")
    cur_exp = alert.get("expected_clk")
    return (
        base_exp is not None and base_exp > 0 and cur_exp is not None
        and float(cur_exp) >= float(base_exp) * ctr_alert_state.ESCALATION_MULTIPLE
    )


def _recent_campaign_stats(db: Session, campaign_id: str, as_of: date) -> dict:
    """[as_of−6, as_of] 캠페인 합계(지출·클릭·전환 건수) — 숫자 근거 1줄용. sentinel 제외."""
    start = as_of - timedelta(days=_RECENT_DAYS - 1)
    row = (
        db.query(
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_cnt), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_cnt), 0),
        )
        .filter(
            NaverAdDaily.campaign_id == campaign_id,
            NaverAdDaily.ad_date >= start, NaverAdDaily.ad_date <= as_of,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .first()
    )
    cost, clk, cd, ci = (row or (0, 0, 0, 0))
    return {"cost": int(cost or 0), "clk": int(clk or 0), "conv": int(cd or 0) + int(ci or 0)}


def _weighted_rank(alerts: list[dict]) -> float | None:
    """노출 가중 평균 순위(표시용). 순위 미상 건만 있으면 None."""
    num = den = 0.0
    for a in alerts:
        r, imp = a.get("avg_rank"), float(a.get("imp", 0) or 0)
        if r is None or imp <= 0:
            continue
        num += float(r) * imp
        den += imp
    return (num / den) if den > 0 else None


def _group_detail(label: str, a: dict, streak: int | None) -> str:
    """그룹 1줄 상세 — '무엇이 얼마나'만. 창·기대치는 사람 말로."""
    win = alert_humanizer.window_label(a.get("window", ""))
    imp = alert_humanizer.count(a.get("imp"))
    clk = int(a.get("clk", 0) or 0)
    streak_txt = f" {alert_humanizer.streak_label(streak)}" if streak and streak > 1 else ""
    if a.get("kind") == ctr_alert.KIND_NO_RESPONSE:
        # 기대클릭이 0이라 "평소라면 N건"이 무의미하다 — 누적 노출로 심각도를 말한다.
        tr_imp = alert_humanizer.count(a.get("trailing_imp"))
        return (f"{label} — {win} 노출 {imp}·클릭 0 / 지금까지 노출 {tr_imp}회 동안 "
                f"클릭이 한 번도 없음{streak_txt}")
    expected = a.get("expected_clk")
    expect_txt = f", 평소라면 {expected}건" if expected is not None else ""
    return f"{label} — {win} 노출 {imp}·클릭 {clk}{expect_txt}{streak_txt}"


def _prescription(campaign_type: str, *, has_no_response: bool) -> str:
    """권하는 행동 1줄. 쇼핑에만 탐색 래더 문구를 붙인다 — 파워링크·브랜드검색에는 탐색
    래더 자체가 적용되지 않으므로(SHOPPING 전용) 그 문구는 거짓이 된다(D-NAO-103 ③)."""
    if campaign_type == "SHOPPING":
        base = ("상품 썸네일·가격·리뷰를 점검해 주세요. "
                "자동 탐색 입찰 인상은 이 그룹들에서 중지됩니다.")
    else:
        base = "광고 문구(제목·설명)와 연결 페이지를 점검해 주세요."
    if has_no_response:
        base += " 반응이 아예 없는 그룹은 손보는 것보다 소재 교체가 빠릅니다."
    return base


def _campaign_block(
    db: Session, campaign_id: str, alerts: list[dict],
    streaks: dict[tuple[str, str], int], as_of: date,
) -> str:
    """캠페인 1개 블록 — 무슨 일(1줄) → 그룹 상세 → 숫자 근거 → 권하는 행동."""
    camp_label = alert_humanizer.campaign_name(db, campaign_id)
    ctype = str(alerts[0].get("campaign_type") or "")
    type_label = alert_humanizer.campaign_type_label(ctype)
    # 이름에 이미 지면이 들어 있으면(예: "아이패드 파워링크") 유형을 덧붙이지 않는다 — 중복 표기.
    head_name = (
        f"{camp_label}({type_label})" if type_label and type_label not in camp_label else camp_label
    )

    names = alert_humanizer.entity_names(db, "adgroup", [a["adgroup_id"] for a in alerts])
    max_streak = max((streaks.get((campaign_id, a["adgroup_id"]), 1) for a in alerts), default=1)

    lines: list[str] = []
    if len(alerts) == 1:
        a = alerts[0]
        label = names.get(a["adgroup_id"], a["adgroup_id"])
        lines.append(f"📣 {head_name} — {_group_detail(label, a, max_streak)}")
    else:
        streak_txt = f" {alert_humanizer.streak_label(max_streak)}" if max_streak > 1 else ""
        lines.append(
            f"📣 {head_name}: 광고그룹 {len(alerts)}개가 노출은 나오는데 "
            f"클릭이 거의 없습니다{streak_txt}."
        )
        shown = alerts if len(alerts) < _CAMPAIGN_COMPRESS_MIN else alerts[:_GROUP_DETAIL_TOP_N]
        for a in shown:
            label = names.get(a["adgroup_id"], a["adgroup_id"])
            lines.append(
                f"{_INDENT}· "
                f"{_group_detail(label, a, streaks.get((campaign_id, a['adgroup_id'])))}"
            )
        remainder = len(alerts) - len(shown)
        if remainder > 0:
            lines.append(f"{_INDENT}· 외 {remainder}개")

    rank = _weighted_rank(alerts)
    rank_txt = alert_humanizer.rank_label(rank, band_top=_RANK_BAND_TOP)
    has_dead = any(a.get("kind") == ctr_alert.KIND_NO_RESPONSE for a in alerts)
    lines.append(
        f"{_INDENT}{rank_txt} — 입찰로 풀 문제가 아닙니다. "
        f"{_prescription(ctype, has_no_response=has_dead)}"
    )

    stats = _recent_campaign_stats(db, campaign_id, as_of)
    lines.append(
        f"{_INDENT}최근 {_RECENT_DAYS}일 이 캠페인: {alert_humanizer.money(stats['cost'])} 지출 · "
        f"클릭 {alert_humanizer.count(stats['clk'])} · 전환 {alert_humanizer.count(stats['conv'])}건"
    )
    return "\n".join(lines)


def _render_section(
    db: Session, alerts: list[dict], streaks: dict[tuple[str, str], int],
    as_of: date, *, header: str,
) -> str:
    by_campaign: dict[str, list[dict]] = {}
    for a in alerts:
        by_campaign.setdefault(a["campaign_id"], []).append(a)
    blocks = [
        _campaign_block(db, cid, rows, streaks, as_of) for cid, rows in by_campaign.items()
    ]
    return "\n".join([header, *blocks])


def build_briefing(db: Session, alerts: list[dict], *, now: datetime) -> dict:
    """브리핑 텍스트 조립 + 판정 이력 기록.

    반환: {"text": str|None, "total": 판정 그룹 수, "fired": 메시지에 실린 그룹 수,
           "suppressed": 억제된 그룹 수, "new": 신규 진입 수, "escalated": 악화 재발화 수,
           "chronic": 억제된 만성 수}.
    text가 None이면 호출부는 완전 침묵한다(diary·Slack 둘 다 없음).

    as_of는 detect_ctr_alerts와 같은 D0(=now.date()−1)를 쓴다 — 상태 테이블의 "전일"이
    브리핑 실행일이 아니라 판정 기준일로 이어지게 하기 위함."""
    as_of = now.date() - timedelta(days=1)
    deduped = dedupe_alerts(alerts)
    if not deduped:
        return {"text": None, "total": 0, "fired": 0, "suppressed": 0, "new": 0, "chronic": 0}

    new_alerts, chronic, streaks = ctr_alert_state.classify(db, deduped, as_of)
    # P2-2: 만성 중 "직전 통지 대비 3배 이상 커진" 건은 주간까지 기다리지 않고 오늘 알린다.
    baselines = ctr_alert_state.last_notified_metrics(db, as_of)
    escalated = [a for a in chronic if _escalated(a, baselines.get((a["campaign_id"], a["adgroup_id"])))]
    escalated_keys = {(a["campaign_id"], a["adgroup_id"]) for a in escalated}
    chronic = [a for a in chronic if (a["campaign_id"], a["adgroup_id"]) not in escalated_keys]

    sections: list[str] = []
    notified: set[tuple[str, str]] = set()

    if new_alerts:
        sections.append(_render_section(
            db, new_alerts, streaks, as_of,
            header=f"[{as_of.isoformat()} 소재 CTR 경보] 새로 생긴 문제 {len(new_alerts)}건",
        ))
        notified |= {(a["campaign_id"], a["adgroup_id"]) for a in new_alerts}

    if escalated:
        sections.append(_render_section(
            db, escalated, streaks, as_of,
            header=(f"[{as_of.isoformat()} 소재 CTR 경보] 눈에 띄게 나빠진 문제 "
                    f"{len(escalated)}건 — 규모가 직전 알림의 "
                    f"{ctr_alert_state.ESCALATION_MULTIPLE}배 이상"),
        ))
        notified |= escalated_keys

    if chronic and now.weekday() == _WEEKLY_WEEKDAY:
        sections.append(_render_section(
            db, chronic, streaks, as_of,
            header=(f"[{as_of.isoformat()} 소재 CTR 주간 요약] 계속 이어지는 문제 "
                    f"{len(chronic)}건 — 매일 알리지 않고 월요일에 모아 보냅니다"),
        ))
        notified |= {(a["campaign_id"], a["adgroup_id"]) for a in chronic}

    ctr_alert_state.record(
        db, deduped, as_of=as_of, streaks=streaks, notified_keys=notified, now=now,
    )
    result = {
        "text": "\n\n".join(sections) if sections else None,
        "total": len(deduped), "fired": len(notified),
        "suppressed": len(deduped) - len(notified),
        "new": len(new_alerts), "chronic": len(chronic), "escalated": len(escalated),
    }
    log.info(
        "ctr_alert_briefing: as_of=%s 판정=%s 발화=%s 억제=%s(신규 %s·악화 %s·만성 %s)",
        as_of.isoformat(), result["total"], result["fired"], result["suppressed"],
        result["new"], result["escalated"], result["chronic"],
    )
    return result
