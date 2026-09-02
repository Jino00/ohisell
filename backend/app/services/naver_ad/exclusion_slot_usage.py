# exclusion_slot_usage.py — 제외 슬롯 사용률·소진 예상일 (S6-a, D-NAO-264 · ref 66 §5)
#
# 역할: 「그룹당 70칸의 제외 슬롯이 몇 칸 찼고, 언제 바닥나는가」를 **DB만 읽어** 낸다.
#
# ★왜 네이버를 안 부르나: 배너·API 렌더 경로에서 외부 호출은 감시가 아니라 부하다
#   (exclusion_survival 모듈 주석과 같은 규율). 라이브 count는 일일 스윕
#   (`adgroup_target_ingest`, 09:35)이 이미 적재한다 — 추가 API 콜 0.
#
# ★왜 원장이 아니라 라이브가 정본인가 (ref 66 §5-1): 원장(`NaverSearchTermExclusion`)은
#   편입 누락·대행사 신규분만큼 **적게** 나온다. 원장만 보면 70/70 그룹이 「43칸」으로 보인다.
#   그래서 사용량은 라이브(`restrict_keyword_count`)로 재고, **원장↔라이브 차이 자체를
#   같이 실어 보낸다**(§5-3) — 그 차이가 곧 「우리가 모르는 남의 칸」이다.
#
# ★귀속은 원장 `source`가 이미 가른다 (ref 66 §5-3): NULL = 우리 실행분 / 'console_import' =
#   대행사 축적분(D-NAO-176). 라이브 총계에서 원장 귀속분을 빼면 «미귀속»이 남는다 —
#   0으로 뭉개지 않고 그대로 표기한다.
#
# ★소진 예상일의 분모는 «관측된 유입률»뿐이다 (ref 66 §5-2): 고정 % 문턱(80% 같은)은 근거를
#   발명해야 하므로 쓰지 않는다. 대신 «며칠 남았다»로 말하되, **우리 유입은 실집행이 멈춰
#   0으로 관측된다** — 그래서 지금 나오는 예상일은 «대행사 유입만 반영한 상한»이고, 점화하면
#   짧아진다. 그 한계를 값과 함께 실어 보내지 않으면 화면이 거짓말을 한다.
#
# ★70/70은 문턱 불요의 무조건 빨강 (ref 66 §5-2): 그 그룹의 음의 레버가 그 순간 소멸한 것이다.
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import NaverAdgroupTargetCurrent, NaverSearchTermExclusion
from app.services.naver_ad.exclusion_survival import (
    BREACH_SAMPLE_CAP as SAMPLE_CAP,
    MONITORED_STATUS,
    STALE_HOURS,
)
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# 그룹당 제외키워드 상한. **우리가 정한 값이 아니라 네이버 플랫폼 제약**이다
# (ref 24 「쇼핑 제외키워드 … 최대 70개」 · ref 30 §2 「소재 단위 최대 70개」).
EXCLUSION_SLOT_CAP = 70

# 유입률 창(일). 새 숫자를 발명하지 않고 이 저장소의 기존 관용 창을 그대로 쓴다 —
# `budget_envelope._ENVELOPE_LOOKBACK_DAYS`·`account_diagnosis.LOW_CLICK_LOOKBACK_DAYS`·
# `bid_rank_curve._LOOKBACK_DAYS`가 전부 30이다(ref 66 §5-2의 「최근 N주」에 채운 값).
INFLOW_LOOKBACK_DAYS = 30

# 상태 — 「모름」이 「여유」로 보이지 않게 가른다.
STATE_EXHAUSTED = "exhausted"   # 70/70 — 무조건 빨강
STATE_UNKNOWN = "unknown"       # 못 셌다(프로브 비-200·스키마 이상) — 0이 아니다
STATE_STALE = "stale"           # 관측이 묵었다 — 지금 값이라 말할 수 없다
STATE_OK = "ok"

_BAD_STATES = (STATE_EXHAUSTED, STATE_UNKNOWN, STATE_STALE)

# 대행사 칸의 반납(delete)은 소유권 분리 협의 전 금지선이다(ref 66 §5-3 · ref 65 §10-⑧).
# 화면이 「회수하면 되지」로 읽히지 않게 같은 문장을 API가 들고 다닌다.
RECLAIM_NOTE = (
    "대행사 칸은 우리가 반납하지 않는다 — 남의 조치를 되돌리는 것이라 "
    "소유권 분리 협의(Jino 몫) 전엔 금지선이다."
)


def _state(used: int | None, observed_at: datetime | None, now: datetime) -> str:
    """사용량·신선도 → 상태. ★판정 순서가 규율이다: «모름»을 «여유»보다 먼저 본다."""
    if used is None:
        return STATE_UNKNOWN
    if observed_at is None or (now - observed_at) > timedelta(hours=STALE_HOURS):
        # ★묵은 관측을 초록으로 두면 스윕이 죽은 날부터 화면이 조용해진다.
        #   단 «묵었지만 이미 70칸»은 여전히 빨강이다 — 칸이 저절로 비지는 않는다.
        return STATE_EXHAUSTED if used >= EXCLUSION_SLOT_CAP else STATE_STALE
    if used >= EXCLUSION_SLOT_CAP:
        return STATE_EXHAUSTED
    return STATE_OK


def _eta(remaining: int | None, inflow: int) -> tuple[float | None, str]:
    """(소진 예상일, 사유). ★못 내는 경우엔 빈칸이 아니라 **사유**를 돌려준다.

    화면이 사유를 스스로 말하지 않으면 「왜 비었지」를 매번 사람이 되물어야 한다.
    """
    if remaining is None:
        return None, "사용량을 못 셌다(프로브 실패 또는 스키마 이상) — 예상일의 분자가 없다"
    if remaining <= 0:
        return 0.0, "이미 70/70 — 남은 칸이 없다"
    if inflow <= 0:
        return None, (
            f"최근 {INFLOW_LOOKBACK_DAYS}일 신규 등록 0건 — 소진 «속도»가 관측되지 않는다"
            "(속도가 0이라는 뜻이 아니라, 잴 근거가 없다는 뜻이다)"
        )
    per_day = inflow / INFLOW_LOOKBACK_DAYS
    return round(remaining / per_day, 1), (
        f"최근 {INFLOW_LOOKBACK_DAYS}일 신규 {inflow}건 기준 — "
        "우리 실집행이 멈춰 있어 사실상 대행사 유입만 반영된 **상한**이다(점화하면 짧아진다)"
    )


# 상태 정렬 우선순위 — **나쁜 것이 먼저 보여야** 표면이 일을 한다(상한에 잘려도 빨강은 남는다).
_STATE_ORDER = {STATE_EXHAUSTED: 0, STATE_UNKNOWN: 1, STATE_STALE: 2, STATE_OK: 3}


def _ledger_by_group(db: Session, now: datetime) -> dict[str, dict]:
    """원장을 그룹별로 접는다 — 귀속(우리/대행사/기타)·등급 분포·최근 유입.

    ★`status='excluded'`만 센다: probation은 우리가 **의도적으로 개방한** 관찰창이고 restored는
      복귀 확정이라, 지금 «칸을 차지하고 있는» 것이 아니다(exclusion_survival과 같은 잣대).
    """
    since = now - timedelta(days=INFLOW_LOOKBACK_DAYS)
    out: dict[str, dict] = {}
    rows = (
        db.query(NaverSearchTermExclusion)
        .filter(NaverSearchTermExclusion.status == MONITORED_STATUS)
        .all()
    )
    for r in rows:
        if not r.adgroup_id:
            continue
        g = out.setdefault(r.adgroup_id, {
            "ours": 0, "agency": 0, "other_source": 0,
            "grades": {}, "inflow": 0, "inflow_ours": 0, "inflow_agency": 0,
        })
        if r.source is None:
            g["ours"] += 1
            stamp = r.excluded_at
            bucket = "inflow_ours"
        elif r.source == "console_import":
            g["agency"] += 1
            # ★실제 등록 시각은 `console_excluded_at`(D-NAO-177)이다. 없으면 편입 시각으로
            #   떨어지는데, 그건 「대행사가 언제 걸었나」가 아니라 「우리가 언제 알았나」다 —
            #   유입률이 편입일에 몰려 과대평가될 수 있다. 0으로 버리지 않고 그 한계를 안고 센다.
            stamp = r.console_excluded_at or r.excluded_at
            bucket = "inflow_agency"
        else:
            # ★모르는 source를 우리/대행사 어느 쪽에도 섞지 않는다 — 섞으면 귀속이 조용히 틀린다.
            g["other_source"] += 1
            stamp = r.excluded_at
            bucket = None
        if r.grade:
            g["grades"][r.grade] = g["grades"].get(r.grade, 0) + 1
        if stamp is not None and stamp >= since:
            g["inflow"] += 1
            if bucket:
                g[bucket] += 1
    return out


def exhausted_adgroups(db: Session, campaign_id: str, *, now: datetime | None = None) -> list[str]:
    """그 캠페인에서 슬롯이 바닥난 광고그룹 id **전건** (표본 절단 없음).

    ★★왜 `slot_usage()["rows"]`를 쓰면 안 되는가 (적대 리뷰 1R P1-1, 재현됨): 저쪽 `rows`는
      배너 payload용이라 `SAMPLE_CAP`(20)에서 **잘린다.** 계정 전체 exhausted가 21개를 넘으면
      점검 대상 캠페인의 70/70 그룹이 표본 밖으로 밀려나 **경고가 통째로 사라지고
      `safe_to_ignite: true`가 나간다** — 「검사했는데 깨끗하다」와 「검사가 놓쳤다」가
      응답에서 구분되지 않는다(교훈 #123의 변형).
      초판은 「정렬이 빨강 우선이라 표본에 남을 확률이 높다」고 자백해 뒀는데, 그건 **확률이지
      보장이 아니다.** 그리고 라이브가 이미 그 문턱에 붙어 있다 — 70/70 도달 그룹 **15개**
      (ref 103 §4)에 상한이 20이다.
    ⇒ **게이트 판정에는 절단된 컬렉션을 절대 쓰지 않는다.** 표본 절단은 목록·배너 몫이다.

    ★판정기는 늘리지 않는다 — 상태 판정은 `_state()` 한 벌을 그대로 쓴다(두 벌이 되면 갈린다).
    """
    now = now or kst_now()
    return [
        t.adgroup_id
        for t in db.query(NaverAdgroupTargetCurrent)
        .filter(NaverAdgroupTargetCurrent.campaign_id == campaign_id)
        .all()
        if _state(t.restrict_keyword_count, t.observed_at, now) == STATE_EXHAUSTED
    ]


def slot_usage(db: Session, *, now: datetime | None = None) -> dict:
    """제외 슬롯 사용률·소진 예상일 요약 (읽기 전용 · 외부 호출 0).

    반환: {cap, as_of, groups, exhausted, unknown, stale, healthy, rows, rows_truncated,
           observed_from, observed_to, totals, reclaim_note}
    ★`as_of`는 «응답 생성 시각»이고 `observed_from/to`가 «라이브를 마지막으로 본 창»이다.
      화면에 붙일 기준 시각은 후자다.
    """
    now = now or kst_now()
    ledger = _ledger_by_group(db, now)

    names: dict[str, str] = {}
    # ★캠페인 이름도 같이 받는다(가산). 그룹 이름만 있으면 화면에서 «어느 캠페인의 그룹인가»를
    #   알 수 없다 — 「01. TEST_S20」 같은 이름은 캠페인을 모르면 어디 것인지 가려낼 수 없다.
    #   (Jino 2026-09-02: *"어느 광고캠페인에 속해있는 광고그룹인지 알 수 없어"*)
    camp_names: dict[str, str] = {}
    try:
        from app.models import NaverEntity  # noqa: PLC0415 — 이름은 «있으면 좋은» 정보다
        for e in db.query(NaverEntity).filter(
            NaverEntity.entity_type.in_(("adgroup", "campaign"))
        ).all():
            (names if e.entity_type == "adgroup" else camp_names)[e.entity_id] = e.name
    except Exception:  # noqa: BLE001 — 이름을 못 얻어도 사용률 판정은 그대로 서야 한다
        log.warning("[제외슬롯] 엔티티 이름 조회 실패 — id로만 표기한다", exc_info=True)

    rows: list[dict] = []
    counts = {STATE_EXHAUSTED: 0, STATE_UNKNOWN: 0, STATE_STALE: 0, STATE_OK: 0}
    used_sum = ours_sum = agency_sum = other_sum = 0
    # ★미귀속을 «계정 수준»에서도 낸다. 여태 totals엔 ours·agency뿐이라 화면이 3분할을
    #   그리려면 `used - ours - agency`로 추정할 수밖에 없었는데, 그건 other_source를
    #   미귀속에 뭉개는 계산이다 — 이 모듈 머리말이 «0으로 뭉개지 않는다»고 적어 둔 바로
    #   그 값을 화면이 뭉개게 된다. 그래서 행에서 이미 재고 있는 것을 누계로도 낸다.
    #
    # ★★그런데 순액 하나로는 «0으로 뭉개는 것»과 정보량이 같아진다(적대 리뷰 P1-2).
    #   그룹별 `used - 원장`은 부호가 둘이고 뜻이 정반대다:
    #     양수 = 라이브 초과 → 「우리가 모르는 남의 칸」
    #     음수 = 원장 초과   → 「우리가 건 제외가 라이브에 안 보인다 — 지워졌을 수 있다」
    #   2026-09-02 prod 실측: +3,662 / −1,824(58그룹) → 순액 1,838. 절반이 상계된다.
    #   그래서 방향을 갈라 싣는다. 순액(`unattributed`)도 남기되 화면은 갈라 쓴다.
    unattributed_sum = 0
    live_excess_sum = ledger_excess_sum = ledger_excess_groups = 0
    # ★라이브를 «못 센» 그룹에 붙은 원장 행. 여기에 있는 것을 ours/agency 누계에 더하면
    #   `ours+agency+other+unattributed == used` 항등식이 그만큼 깨진다(P1-1, prod에서 +67).
    #   그렇다고 조용히 버리면 「우리 실행분」이 실제보다 적게 보인다 — 그래서 따로 센다.
    uncounted_ledger_sum = 0
    # ★스윕 시각 창. `as_of`는 «이 응답을 만든 시각»이지 «라이브를 마지막으로 본 시각»이
    #   아니다(호출할 때마다 바뀐다). 화면이 as_of를 「기준 시각」으로 쓰면 09:35에 본 것을
    #   20:00 기준이라고 말하는 거짓말이 된다 — 그래서 관측 시각의 범위를 따로 실어 보낸다.
    observed_min: datetime | None = None
    observed_max: datetime | None = None

    for t in db.query(NaverAdgroupTargetCurrent).all():
        used = t.restrict_keyword_count
        state = _state(used, t.observed_at, now)
        counts[state] += 1
        g = ledger.get(t.adgroup_id, {})
        ours, agency = g.get("ours", 0), g.get("agency", 0)
        remaining = None if used is None else max(EXCLUSION_SLOT_CAP - used, 0)
        eta_days, eta_reason = _eta(remaining, g.get("inflow", 0))
        other = g.get("other_source", 0)
        if used is not None:
            used_sum += used
            delta = used - ours - agency - other
            unattributed_sum += delta
            if delta >= 0:
                live_excess_sum += delta
            else:
                ledger_excess_sum += -delta
                ledger_excess_groups += 1
            # ★누계는 «센 그룹»에 대해서만 더한다 — 항등식을 지키는 유일한 방법이다.
            ours_sum += ours
            agency_sum += agency
            other_sum += other
        else:
            uncounted_ledger_sum += ours + agency + other
        if t.observed_at is not None:
            observed_min = t.observed_at if observed_min is None else min(observed_min, t.observed_at)
            observed_max = t.observed_at if observed_max is None else max(observed_max, t.observed_at)
        rows.append({
            "adgroup_id": t.adgroup_id,
            "campaign_id": t.campaign_id,
            # ★못 찾으면 빈 문자열이다 — 프론트가 id로 폴백한다(지어내지 않음).
            "campaign_name": camp_names.get(t.campaign_id, ""),
            "name": names.get(t.adgroup_id, ""),
            "state": state,
            "used": used,                       # ★None = 못 셌다(0 아님)
            "cap": EXCLUSION_SLOT_CAP,
            "remaining": remaining,
            "usage_pct": None if used is None else round(used * 100 / EXCLUSION_SLOT_CAP, 1),
            # ── 귀속 3분 표기 (ref 66 §5-3) ──
            "ours": ours,
            "agency": agency,
            "other_source": other,
            # ★라이브 총계에서 원장 귀속분을 뺀 나머지. 양수 = 원장이 모르는 남의 칸,
            #   음수 = 원장이 라이브보다 많다(우리 조치가 지워졌을 수 있다 — 생존감시 소관).
            "unattributed": None if used is None else used - ours - agency - other,
            "grades": g.get("grades", {}),
            "inflow_30d": g.get("inflow", 0),
            "inflow_30d_ours": g.get("inflow_ours", 0),
            "inflow_30d_agency": g.get("inflow_agency", 0),
            "exhaust_eta_days": eta_days,
            "exhaust_eta_reason": eta_reason,
            "probe_status": t.probe_status,
            "observed_at": t.observed_at.isoformat() if t.observed_at else None,
        })

    rows.sort(key=lambda r: (
        _STATE_ORDER.get(r["state"], 9),
        r["remaining"] if r["remaining"] is not None else -1,
        r["adgroup_id"],
    ))
    shown = rows[:SAMPLE_CAP]
    return {
        "cap": EXCLUSION_SLOT_CAP,
        "as_of": now.isoformat(),
        "groups": len(rows),
        "exhausted": counts[STATE_EXHAUSTED],
        "unknown": counts[STATE_UNKNOWN],
        "stale": counts[STATE_STALE],
        # ★«모름»도 건강하지 않다 — 못 본 것을 초록으로 두면 이 감시는 죽은 날부터 조용해진다.
        "healthy": counts[STATE_EXHAUSTED] == 0 and counts[STATE_UNKNOWN] == 0 and counts[STATE_STALE] == 0,
        "rows": shown,
        # ★잘렸다는 사실이 숨지 않게 총계를 따로 낸다(exclusion_survival과 같은 규율).
        "rows_truncated": max(len(rows) - len(shown), 0),
        # ★스윕 시각 창(가산). 화면은 `as_of`가 아니라 이것을 「기준 시각」으로 쓴다.
        "observed_from": observed_min.isoformat() if observed_min else None,
        "observed_to": observed_max.isoformat() if observed_max else None,
        "totals": {
            "used": used_sum,
            "ours": ours_sum,
            "agency": agency_sum,
            # ★가산 — 계정 수준 귀속 분할이 추정 없이 서게 한다.
            #   ours/agency/other/unattributed 는 **라이브를 센 그룹에 대해서만** 더한다:
            #   `ours + agency + other_source + unattributed == used` 가 성립해야 화면의
            #   막대가 거짓말을 안 한다.
            "other_source": other_sum,
            "unattributed": unattributed_sum,          # 순액(부호 있음)
            "live_excess": live_excess_sum,            # 양의 몫 — 「모르는 남의 칸」
            "ledger_excess": ledger_excess_sum,        # 음의 몫 — 「우리 조치가 안 보임」
            "ledger_excess_groups": ledger_excess_groups,
            # ★못 센 그룹에 붙은 원장 행. 위 누계에서 빠졌다는 사실을 숨기지 않는다.
            "uncounted_ledger": uncounted_ledger_sum,
            "capacity": len(rows) * EXCLUSION_SLOT_CAP,
        },
        "reclaim_note": RECLAIM_NOTE,
    }
