# perf_today_harness.py — 광고 성과(사장님 뷰) ①한눈에 + ②시스템이 한 일 + 날짜 비교 하니스
# (D-NAO-104 Phase 1 · D-NAO-105 Phase 2, docs/PLAN_naver-ad-performance-view.md §2 H1).
"""역할(Harness): SA들을 조합해 "그날 광고 잘 돌았나 / 예산 다 썼나 / 그날 시스템이 뭘 했나"
세 질문에 한 번에 답하는 응답을 만든다. **읽기 전용** — 쓰기·조작은 이 페이지의 스코프 밖이다
(조작은 커맨드 센터·최적화 콘솔이 계속 담당한다, 계획서 §0-1).

조합하는 SA(원칙18-6: SA끼리 직접 부르지 않는다 — 원료는 이 하니스가 모아서 넘긴다):
  · campaign_roster       이름·광고종류·상태·관리주체·자동운영(최근 30일 성과는 참고)
  · today_proxy_revenue   캠페인별 당일 매출 프록시(매핑 없으면 None=알 수 없음)
  · metrics_aggregator    과거 날짜의 확정 지표(naver_ad_daily, sentinel 제외)
  · campaign_roas_lines   목표/손익분기 ROAS(override → 상품 파생 → 계정 기본값)
  · NaverHourlySnapshot read  당일 누적 비용·노출·클릭·일예산(D-0 유일 원천)
  · change_log_narrator   그날 변경 이력 → 한글 문장

★D-NAO-105(Jino "날짜 선택, 날짜 비교를 할 수 있게"): 오늘 고정이던 하니스를 **날짜 일반화**
  했다. 날짜에 따라 **숫자의 출처가 통째로 다르다**는 것이 이 확장의 핵심 위험이다:

    오늘(D-0)      비용·노출·클릭 = 시간별 스냅샷 / 매출 = 스마트스토어 실주문 **상한 프록시**
    과거(D-1 이하) 비용·노출·클릭·전환매출 = naver_ad_daily **확정치**(네이버 귀속)

  두 값은 정의가 다르다(프록시는 광고 외 유입 포함, 확정치는 네이버가 귀속한 전환). 화면이
  라벨 없이 나란히 놓으면 사장님은 같은 지표로 읽는다 — 그래서 응답이 **출처를 실어 보낸다**
  (`source`/`source_label`/`roas_label`/`revenue_label`). 프론트는 이 라벨을 그대로 쓴다.

★간접전환 정착(계획서 §4·[[naver-ad-data-cadence]]): 확정치라도 최근 2일은 간접전환이 아직
  들어오는 중이라 **올라갈 일만 남았다**. 그 구간은 `settling`으로 표시해 "아직 확정 중"이라고
  말한다 — 확정으로 말하고 나중에 숫자가 바뀌면 화면이 거짓말한 것이 된다(원칙22).

★창 관례(계획서 §4 공통 — 어긋나면 화면끼리 숫자가 안 맞는다): 당일(D-0) 숫자는
  naver_ad_daily에서 오지 않는다(그날 확정 적재 전).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import NaverChangeLog, NaverHourlySnapshot
from app.services.naver_ad import (
    budget_pacing,
    campaign_roas_lines,
    campaign_roster,
    change_log_narrator,
    metrics_aggregator,
    naver_execution_harness,
    today_proxy_revenue,
)
from app.services.naver_ad.alert_humanizer import (
    campaign_type_label,
    clean_name,
    managed_by_label,
)
from app.utils.kst import kst_now

ROSTER_WINDOW_DAYS = 30

# ── 날짜별 숫자 출처(D-NAO-105) ─────────────────────────────────────
SOURCE_TODAY_PROXY = "today_proxy"   # 오늘 — 실주문 상한 프록시
SOURCE_SETTLING = "settling"         # 최근 확정치 — 간접전환이 아직 들어오는 중
SOURCE_CONFIRMED = "confirmed"       # 확정치

# 간접전환이 정착하는 데 걸리는 일수. 이 이내의 확정치는 '확정 중'으로 말한다.
SETTLING_DAYS = 2

DATA_NOTE = (
    "오늘 매출은 스마트스토어 실주문 기준 추정치입니다 — 광고로 생긴 매출의 상한이라 "
    "실제 광고 성과는 이보다 낮습니다. 확정 성과는 다음 날 광고 리포트에서 확인됩니다."
)
_DATA_NOTE_SETTLING = (
    "이날 숫자는 네이버 광고가 확정한 값이지만, 나중에 잡히는 전환(간접전환)이 아직 "
    "들어오는 중이라 앞으로 조금 올라갈 수 있습니다."
)
_DATA_NOTE_CONFIRMED = (
    "이날 숫자는 네이버 광고가 확정한 값입니다. 매출은 네이버가 이 광고 덕분이라고 "
    "인정한 전환 금액입니다."
)

_SOURCE_META = {
    SOURCE_TODAY_PROXY: {
        "source_label": "오늘 추정",
        "roas_label": "오늘 ROAS(추정)",
        "revenue_label": "오늘 매출(추정)",
        "data_note": DATA_NOTE,
        "day_word": "오늘",
        # 조사·시제 때문에 문장을 통째로 둔다 — day_word만 갈아끼우면 "이날은 아직 집행된"처럼
        # 어색해지고, 어색한 문장은 D-NAO-103 ③("숫자 나열이 아니라 문장")이 무너지는 자리다.
        "no_spend_sentence": "오늘은 아직 집행된 광고비가 없습니다.",
        "unknown_revenue_sentence": "오늘 매출은 아직 알 수 없습니다.",
        "below_bep_sentence": "지금은 손익분기 아래입니다.",
    },
    SOURCE_SETTLING: {
        "source_label": "확정 중",
        "roas_label": "ROAS(확정 중)",
        "revenue_label": "전환매출(확정 중)",
        "data_note": _DATA_NOTE_SETTLING,
        "day_word": "이날",
        "no_spend_sentence": "이날은 집행된 광고비가 없습니다.",
        "unknown_revenue_sentence": "이날 매출은 알 수 없습니다.",
        "below_bep_sentence": "손익분기 아래였습니다.",
    },
    SOURCE_CONFIRMED: {
        "source_label": "확정",
        "roas_label": "ROAS(확정)",
        "revenue_label": "전환매출(확정)",
        "data_note": _DATA_NOTE_CONFIRMED,
        "day_word": "이날",
        "no_spend_sentence": "이날은 집행된 광고비가 없습니다.",
        "unknown_revenue_sentence": "이날 매출은 알 수 없습니다.",
        "below_bep_sentence": "손익분기 아래였습니다.",
    },
}

QUIET_REASON = "오늘은 바꿀 만한 신호가 없었습니다."
_QUIET_REASON_PAST = "이날은 바꿀 만한 신호가 없었습니다."

# ② "시스템이 한 일"이 세는 액션 집합.
#   · EXECUTION_ACTIONS  = 우리가 광고 API에 실제로 쓴 것(update_bid/update_budget/…)
#   · budget_pacing.PACING_ACTIONS = BP 레인이 **라벨만 분리해** 남기는 예산 증액/원복
#     (budget_up_pacing 등). 실행 경로는 update_budget 하나지만 change_log에 다른 이름으로
#     찍히므로 EXECUTION_ACTIONS만 보면 **BP가 한 일이 통째로 안 보인다**(D-NAO-102 ⑥).
TODAY_ACTIONS: frozenset[str] = frozenset(
    naver_execution_harness.EXECUTION_ACTIONS | set(budget_pacing.PACING_ACTIONS)
)

# ④예산 섹션이 세는 '예산을 바꾼 일'만 추린 부분집합(perf_campaign_harness가 쓴다).
BUDGET_ACTIONS: frozenset[str] = frozenset(
    {"update_budget"} | set(budget_pacing.PACING_ACTIONS)
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

# 비교(③)에서 증감을 낼 지표. ROAS는 비율이라 '절대 증감'의 단위가 배수다 — 금액과 같은
# 칸에 넣지 않도록 프론트가 구분해 렌더한다.
COMPARE_METRICS = ("spend", "imp", "clk", "revenue", "roas")


def resolve_source(day: date, today: date) -> str:
    """그날 숫자가 어디서 오는가. 날짜만 보고 정한다(데이터 유무와 무관 — 없으면 없다고 말하는
    것이 이 함수의 일이 아니다)."""
    if day >= today:
        return SOURCE_TODAY_PROXY
    return SOURCE_SETTLING if (today - day).days <= SETTLING_DAYS else SOURCE_CONFIRMED


def _status_labels(status: str | None, status_reason: str | None) -> tuple[str, str | None]:
    """(상태 라벨, 검수 라벨). 검수는 별도 축이라 따로 돌려준다(정상 노출과 배타가 아님)."""
    reason = (status_reason or "").upper()
    review = _REVIEW_LABEL if reason.endswith("_UNDER_REVIEW") else None
    label = _STATUS_REASON_LABEL.get(reason)
    if label is None:
        label = "정지됨" if (status or "") == "off" else "운영 중"
    return label, review


# 관리 주체 라벨은 alert_humanizer가 유일 출처다(D-NAO-105 리뷰) — ③캠페인 상세의 그룹 배지도
# 같은 함수를 봐야 "우리가 자동으로 운영"과 "직접 관리"가 화면마다 갈리지 않는다.
_managed_by_label = managed_by_label


def _latest_snapshots(db: Session, day: date) -> dict[str, NaverHourlySnapshot]:
    """캠페인별 그날 **마지막** 시간별 스냅샷(누적값이라 최신이 그날 확정치). 1쿼리."""
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


def _change_rows(db: Session, day: date, campaign_id: str | None = None) -> list[NaverChangeLog]:
    """그날 우리가 한 시도 전건(집행 + 가드 차단 + 쓰기 실패).

    필터 규약은 `/change-log`의 actor='ours' · include_dry_run=False · include_blocked=True와
    같다(계획서 §4-ⓐ): dry-run을 섞으면 아무것도 안 했는데 일한 것처럼 보이고(D-47-h),
    외부 감지(entity_sync)·내부 설정 변경(optimizer_change)을 섞으면 남이 한 일을 우리가
    했다고 말하게 된다. 차단·실패 행은 after_value가 없고 outcome='failed'인 모양만 받는다 —
    '원인 불명으로 after가 빈 행'까지 주워 담아 차단 배지를 달지 않는다."""
    start = datetime.combine(day, datetime.min.time())
    end = datetime.combine(day, datetime.max.time())
    q = db.query(NaverChangeLog).filter(
        NaverChangeLog.changed_at >= start,
        NaverChangeLog.changed_at <= end,
        NaverChangeLog.action.in_(sorted(TODAY_ACTIONS)),
        NaverChangeLog.dry_run.is_(False),
    )
    if campaign_id:
        q = q.filter(NaverChangeLog.campaign_id == campaign_id)
    rows = q.order_by(NaverChangeLog.changed_at.asc()).all()
    return [r for r in rows if r.after_value is not None or r.outcome == "failed"]


DATA_GAP_NOTE = (
    "이날의 광고 성과 기록이 한 건도 없습니다. 정말 아무것도 집행하지 않았을 수도 있고, "
    "그날 수집이 안 됐을 수도 있습니다 — 둘 중 무엇인지는 이 화면으로 알 수 없습니다."
)


def _day_metrics(
    db: Session, day: date, today: date, campaign_ids: list[str]
) -> tuple[str, dict[str, dict]]:
    """(source, {campaign_id: 지표}) — 그날 캠페인별 비용·노출·클릭·매출·ROAS·예산.

    ★출처가 하나로 통일되지 않는다는 것이 이 함수의 존재 이유다. 오늘은 스냅샷+실주문,
    과거는 naver_ad_daily 확정치다. 호출부가 분기를 다시 하지 않도록 모양을 맞춰 돌려준다.

    revenue=None은 '알 수 없음'이다(상품 매핑 부재). 과거 날짜는 네이버 귀속 전환매출이라
    항상 값이 있고(0이면 측정된 0), 그 대신 광고비가 0이면 ROAS가 정의되지 않는다.
    """
    source = resolve_source(day, today)
    snapshots = _latest_snapshots(db, day)
    out: dict[str, dict] = {}

    if source == SOURCE_TODAY_PROXY:
        proxy = today_proxy_revenue.build(db, campaign_ids, day)
        for cid in campaign_ids:
            snap = snapshots.get(cid)
            spend = int(snap.cost or 0) if snap else 0
            rev = proxy.get(cid, {})
            revenue = rev.get("revenue")
            out[cid] = _metrics_row(
                spend=spend,
                imp=int(snap.imp or 0) if snap else 0,
                clk=int(snap.clk or 0) if snap else 0,
                revenue=revenue,
                daily_budget=int(snap.daily_budget) if snap and snap.daily_budget else None,
                shared_product_count=rev.get("shared_product_count", 0),
                unknown_reason=rev.get("reason") if revenue is None else None,
            )
        return source, out

    agg = {
        r["campaign_id"]: r
        for r in metrics_aggregator.aggregate(db, day, day, grain="campaign")["rows"]
    }
    for cid in campaign_ids:
        r = agg.get(cid)
        snap = snapshots.get(cid)
        out[cid] = _metrics_row(
            spend=int(r["cost"]) if r else 0,
            imp=int(r["imp"]) if r else 0,
            clk=int(r["clk"]) if r else 0,
            # 확정치에는 매핑 개념이 없다 — 행이 없으면 그날 집행 자체가 없었다는 뜻이라 0이
            # 아니라 None(알 수 없음)으로 두지 않는다. 집행이 없으면 매출도 없는 게 맞다.
            revenue=int(r["conv_amt"]) if r else 0,
            daily_budget=int(snap.daily_budget) if snap and snap.daily_budget else None,
            shared_product_count=0,
            unknown_reason=None,
        )
    return source, out


def _metrics_row(
    *, spend: int, imp: int, clk: int, revenue: int | None,
    daily_budget: int | None, shared_product_count: int, unknown_reason: str | None,
) -> dict:
    """지표 한 줄 + ROAS 파생. 광고비 0이면 ROAS는 **정의되지 않는다**(0.0이 아니다)."""
    roas = round(revenue / spend, 4) if revenue is not None and spend > 0 else None
    if roas is None and unknown_reason is None and spend <= 0:
        unknown_reason = "광고비가 없어 ROAS는 계산되지 않습니다."
    return {
        "spend": spend, "imp": imp, "clk": clk, "revenue": revenue, "roas": roas,
        "daily_budget": daily_budget,
        "spend_ratio": (
            round(spend / daily_budget, 4) if daily_budget and daily_budget > 0 else None
        ),
        "shared_product_count": shared_product_count,
        "unknown_reason": unknown_reason,
    }


def _verdict(
    *, meta: dict, status_label: str, spend: int, spend_ratio: float | None,
    roas: float | None, target: float | None, bep: float | None, unknown_reason: str | None,
    include_status: bool,
) -> str:
    """카드 한 줄 평. 판단 근거가 없으면 **판단하지 않는다**(모름을 성과로 바꾸지 않는다).

    include_status: 상태(정지·검수)는 **지금**의 사실이라 과거 날짜 카드에 붙이면 시점이
      어긋난다("2주 전 성과인데 지금은 멈춰 있습니다"). 오늘 카드에서만 붙인다.
    """
    day_word = meta["day_word"]
    parts: list[str] = []
    if include_status:
        status_sentence = _STATUS_SENTENCE.get(status_label)
        if status_sentence:
            parts.append(status_sentence)
    if spend <= 0:
        parts.append(meta["no_spend_sentence"])
        return " ".join(parts)

    if roas is None:
        parts.append(f"{day_word} {spend:,}원을 썼습니다.")
        parts.append(unknown_reason or meta["unknown_revenue_sentence"])
    else:
        if target is not None and roas >= target:
            parts.append("목표를 넘고 있습니다.")
        elif bep is not None and roas >= bep:
            parts.append("남기는 하지만 목표에는 못 미칩니다.")
        elif bep is not None:
            parts.append(meta["below_bep_sentence"])
        else:
            parts.append("비교할 목표치가 없어 좋고 나쁨은 판단하지 않았습니다.")
        parts.append(f"{day_word} {spend:,}원을 썼습니다.")
    if spend_ratio is not None:
        parts.append(f"하루 예산의 {round(spend_ratio * 100)}%입니다.")
    else:
        parts.append("하루 예산은 정해져 있지 않습니다.")
    return " ".join(parts)


def _roster(db: Session, today: date, campaign_id: str | None) -> list[dict]:
    """명부(선택 시 해당 캠페인만). 필터를 여기서 한 번에 걸어 아래 SA 호출들이 전부 좁아진다."""
    rows = campaign_roster.build(db, days=ROSTER_WINDOW_DAYS, today=today)
    if campaign_id:
        rows = [r for r in rows if r["campaign_id"] == campaign_id]
    return rows


def build(
    db: Session,
    *,
    now: datetime | None = None,
    day: date | None = None,
    campaign_id: str | None = None,
) -> dict:
    """①그날 한눈에 + ②그날 시스템이 한 일.

    day=None이면 오늘(Phase 1 계약 그대로). campaign_id를 주면 그 캠페인만 — 카드·문장·합계가
    전부 좁아진다(화면의 캠페인 선택기가 이 파라미터를 쓴다, D-NAO-105).
    """
    now = now or kst_now()
    today = now.date()
    day = day or today
    is_today = day >= today

    roster = _roster(db, today, campaign_id)
    campaign_ids = [r["campaign_id"] for r in roster]
    source, metrics = _day_metrics(db, day, today, campaign_ids)
    meta = _SOURCE_META[source]
    mapped = set(today_proxy_revenue.product_ids_by_campaign(db, campaign_ids))
    lines = campaign_roas_lines.resolve(db, campaign_ids, mapped_campaign_ids=mapped)

    cards: list[dict] = []
    for r in roster:
        cid = r["campaign_id"]
        m = metrics.get(cid) or _metrics_row(
            spend=0, imp=0, clk=0, revenue=None, daily_budget=None,
            shared_product_count=0, unknown_reason=None,
        )
        status_label, review_label = _status_labels(r.get("status"), r.get("status_reason"))
        line = lines.get(cid, {})

        cards.append({
            "campaign_id": cid,  # 화면 미표시 — 딥링크·툴팁 전용(D-NAO-103①)
            "name": clean_name(r.get("name")) or "이름 없는 광고",
            "type_label": campaign_type_label(r.get("campaign_type")) or "기타 지면",
            "status_label": status_label,
            "review_label": review_label,
            "managed_by_label": _managed_by_label(
                r.get("optimizer", "none"), r.get("auto_operate", False)
            ),
            "auto_operate": bool(r.get("auto_operate")),
            "spend_today": m["spend"],
            "daily_budget": m["daily_budget"],
            "spend_ratio": m["spend_ratio"],
            "imp_today": m["imp"],
            "clk_today": m["clk"],
            "revenue_today_proxy": m["revenue"],
            "roas_today_proxy": m["roas"],
            "roas_unknown_reason": m["unknown_reason"] if m["roas"] is None else None,
            "target_roas": line.get("target_roas"),
            "bep_roas": line.get("bep_roas"),
            "shared_product_count": m["shared_product_count"],
            "active_today": m["spend"] > 0,
            # ★출처 라벨(D-NAO-105): 같은 칸에 프록시와 확정치가 번갈아 들어오므로 화면이
            #   무엇을 보고 있는지 말할 수 있어야 한다.
            "source": source,
            "source_label": meta["source_label"],
            "roas_label": meta["roas_label"],
            "revenue_label": meta["revenue_label"],
            "verdict_sentence": _verdict(
                meta=meta, status_label=status_label, spend=m["spend"],
                spend_ratio=m["spend_ratio"], roas=m["roas"],
                target=line.get("target_roas"), bep=line.get("bep_roas"),
                unknown_reason=m["unknown_reason"], include_status=is_today,
            ),
        })

    # 그날 쓴 돈이 큰 순 → 그다음 최근 30일 지출 순(0원 캠페인도 숨기지 않는다).
    cost_30d = {r["campaign_id"]: int(r.get("cost") or 0) for r in roster}
    cards.sort(key=lambda c: (-c["spend_today"], -cost_30d.get(c["campaign_id"], 0), c["name"]))

    rows = _change_rows(db, day, campaign_id)
    sentences = change_log_narrator.narrate(db, rows)
    executed = sum(1 for s in sentences if s["state"] == change_log_narrator.STATE_EXECUTED)
    blocked = sum(1 for s in sentences if s["state"] == change_log_narrator.STATE_BLOCKED)
    unknown = sum(1 for s in sentences if s["state"] == change_log_narrator.STATE_UNKNOWN)
    quiet = QUIET_REASON if is_today else _QUIET_REASON_PAST

    return {
        "as_of": now.isoformat(),
        "date": day.isoformat(),
        "is_today": is_today,
        "source": source,
        "source_label": meta["source_label"],
        "data_note": meta["data_note"],
        "campaign_filter": campaign_id,
        # ★"수집이 안 된 날"과 "아무것도 안 한 날"은 다르다(원칙22). 과거 날짜인데 확정 기록이
        #   한 줄도 없으면 화면이 "0원 집행"이라고 **단언하지 않도록** 사실을 알려준다.
        #   오늘(D-0)은 아직 확정 적재 전이 정상이라 이 판정을 하지 않는다.
        "data_gap_note": (
            DATA_GAP_NOTE
            if not is_today and all(m["spend"] == 0 and m["imp"] == 0 for m in metrics.values())
            else None
        ),
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
            "quiet_reason": quiet if not sentences else None,
        },
    }


def campaign_options(db: Session) -> dict:
    """화면 상단 캠페인 선택기 목록 — 이름·광고종류·관리주체·최근 30일 지출.

    ★날짜와 무관하게 **항상 전체**를 준다: 선택기가 그날 집행이 있던 광고만 보여주면
    "어제는 있었는데 오늘은 목록에서 사라진" 광고가 생겨 사장님이 고를 수가 없다.
    `campaign_id`는 select의 value로만 쓰인다 — 사람이 읽는 자리엔 이름만 나간다(D-NAO-103①).
    """
    roster = campaign_roster.build(db, days=ROSTER_WINDOW_DAYS)
    return {
        "campaigns": [
            {
                "campaign_id": r["campaign_id"],
                "name": clean_name(r.get("name")) or "이름 없는 광고",
                "type_label": campaign_type_label(r.get("campaign_type")) or "기타 지면",
                "managed_by_label": _managed_by_label(
                    r.get("optimizer", "none"), r.get("auto_operate", False)
                ),
                "cost_30d": int(r.get("cost") or 0),
            }
            for r in roster
        ],
        "window_days": ROSTER_WINDOW_DAYS,
    }


def _delta(base: float | int | None, against: float | int | None) -> dict:
    """{abs, pct} — 비교일 대비 기준일의 증감. 한쪽이라도 모르면 둘 다 None(0으로 안 채운다).

    pct는 **분수**다(0.12 = +12%). 프론트 `pctFromFraction` 계약과 맞춘다."""
    if base is None or against is None:
        return {"abs": None, "pct": None}
    diff = base - against
    return {
        "abs": round(diff, 4) if isinstance(diff, float) else diff,
        "pct": round(diff / against, 4) if against else None,
    }


def compare(
    db: Session,
    *,
    base: date,
    against: date,
    campaign_id: str | None = None,
    now: datetime | None = None,
) -> dict:
    """③날짜 비교 — 기준일 vs 비교일의 캠페인별·합계 증감(D-NAO-105).

    ★두 날짜의 출처가 다르면(오늘 프록시 vs 과거 확정치) 증감은 **지표가 아니라 정의 차이**를
    재는 것이 된다. 그때 `mixed_source_note`로 경고 문장을 실어 보낸다 — 숫자를 감추지는
    않되(사장님이 감으로 보는 값이 있다) 무엇을 비교하는지는 말한다(원칙22).

    기간 범위 비교는 이 슬라이스의 스코프 밖이다(계획서 승계 — 하루 대 하루만).
    """
    now = now or kst_now()
    today = now.date()

    roster = _roster(db, today, campaign_id)
    campaign_ids = [r["campaign_id"] for r in roster]
    base_source, base_m = _day_metrics(db, base, today, campaign_ids)
    against_source, against_m = _day_metrics(db, against, today, campaign_ids)
    names = {r["campaign_id"]: clean_name(r.get("name")) or "이름 없는 광고" for r in roster}
    types = {
        r["campaign_id"]: campaign_type_label(r.get("campaign_type")) or "기타 지면"
        for r in roster
    }

    rows: list[dict] = []
    for cid in campaign_ids:
        b, a = base_m.get(cid), against_m.get(cid)
        if not b or not a:
            continue
        if b["spend"] == 0 and a["spend"] == 0:
            continue  # 양쪽 다 집행이 없으면 비교할 것이 없다(0 vs 0 행으로 화면을 채우지 않는다)
        rows.append({
            "campaign_id": cid,
            "name": names.get(cid, "이름 없는 광고"),
            "type_label": types.get(cid, "기타 지면"),
            "base": {k: b[k] for k in ("spend", "imp", "clk", "revenue", "roas")},
            "against": {k: a[k] for k in ("spend", "imp", "clk", "revenue", "roas")},
            "deltas": {k: _delta(b[k], a[k]) for k in COMPARE_METRICS},
        })
    rows.sort(key=lambda r: -max(r["base"]["spend"], r["against"]["spend"]))

    base_total = _totals(base_m, campaign_ids)
    against_total = _totals(against_m, campaign_ids)
    # ★"출처가 다르다"와 "정의가 다르다"는 다른 사건이다(07-27 라이브 스모크에서 잡힌 오탐):
    #   settling ↔ confirmed는 **둘 다 naver_ad_daily 확정치**이고 성숙도만 다르다 — 이걸
    #   "매출 기준이 다르다"고 경고하면 매일 뜨는 늑대소년이 된다.
    #   today_proxy ↔ 나머지만 진짜 정의 불일치다(실주문 상한 프록시 vs 네이버 귀속 전환).
    mixed = (SOURCE_TODAY_PROXY in (base_source, against_source)) and base_source != against_source
    settling_side = next(
        (d for d, s in ((base, base_source), (against, against_source)) if s == SOURCE_SETTLING),
        None,
    )

    return {
        "base": {
            "date": base.isoformat(), "source": base_source,
            "source_label": _SOURCE_META[base_source]["source_label"],
            "data_note": _SOURCE_META[base_source]["data_note"],
            "totals": base_total,
        },
        "against": {
            "date": against.isoformat(), "source": against_source,
            "source_label": _SOURCE_META[against_source]["source_label"],
            "data_note": _SOURCE_META[against_source]["data_note"],
            "totals": against_total,
        },
        "deltas": {k: _delta(base_total[k], against_total[k]) for k in COMPARE_METRICS},
        "campaign_filter": campaign_id,
        "rows": rows,
        "mixed_source_note": (
            "두 날짜의 매출 기준이 다릅니다 — 오늘은 실주문 추정치, 지난 날짜는 네이버 확정 "
            "전환매출입니다. 매출·ROAS 증감은 참고로만 보세요."
            if mixed else None
        ),
        "settling_note": (
            f"{settling_side.isoformat()}은 아직 전환이 들어오는 중이라 매출·ROAS가 앞으로 "
            "조금 올라갈 수 있습니다."
            if settling_side is not None else None
        ),
        "empty_reason": (
            "두 날짜 모두 집행된 광고가 없습니다." if not rows else None
        ),
    }


def _totals(metrics: dict[str, dict], campaign_ids: list[str]) -> dict:
    """합계. ★매출은 **아는 것만** 더한다(모름을 0으로 더하면 합계가 조용히 과소가 된다) —
    몇 개를 못 셌는지 함께 실어 보내 화면이 그 사실을 말할 수 있게 한다."""
    spend = imp = clk = 0
    revenue = 0
    revenue_unknown = 0
    known_any = False
    for cid in campaign_ids:
        m = metrics.get(cid)
        if not m:
            continue
        spend += m["spend"]
        imp += m["imp"]
        clk += m["clk"]
        if m["revenue"] is None:
            if m["spend"] > 0:
                revenue_unknown += 1
        else:
            revenue += m["revenue"]
            known_any = True
    return {
        "spend": spend, "imp": imp, "clk": clk,
        "revenue": revenue if known_any else None,
        "roas": round(revenue / spend, 4) if known_any and spend > 0 else None,
        "revenue_unknown_campaigns": revenue_unknown,
    }
