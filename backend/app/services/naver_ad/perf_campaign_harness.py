# perf_campaign_harness.py — 광고 성과(사장님 뷰) ③캠페인 상세 + ④예산 하니스
# (D-NAO-105 Phase 2, docs/PLAN_naver-ad-performance-view.md §2 H2).
"""역할(Harness): "이 광고 요즘 어떤가 / 왜 안 늘리나 / 예산 다 써서 멈췄나" 세 질문에 답할
재료를 SA들에서 모아 조립한다. **읽기 전용** — 이 페이지에는 쓰기 경로가 없다(계획서 §0-1).

조합하는 SA(원칙18-6: SA끼리 직접 부르지 않는다 — 원료는 이 하니스가 모아서 넘긴다):
  · metrics_aggregator     일별 시계열(③ 차트) · 그룹별 집계(배지 판정 원료)
  · campaign_roas_lines    BEP선·목표선(차트 기준선, 캠페인 카드와 같은 값)
  · today_proxy_revenue    상품 매핑 유무(기준선 파생 가능 여부 — 값이 아니라 '매핑 집합'만 씀)
  · alert_humanizer        엔티티 ID → 이름(D-NAO-103①)
  · change_log_narrator    최근 변경 이력 → 3상태 + 차단 사유 한글
  · group_state_badge      4상태 순수 판정(확장 중/관망/증액 보류/차단됨)
  · budget_pacing_view     시간별 소진 곡선 · 암전 구간

★창 관례(계획서 §4 공통): 일별 시계열은 **D-0 제외**(naver_ad_daily는 그날 확정 적재 전)
  + BACKFILL_SENTINEL_ADGROUP 제외(metrics_aggregator가 이미 건다 — 2배 함정).
  당일 소진 곡선만 naver_hourly_snapshot(D-0 유일 원천)에서 온다.

★소재(ad) 레벨 변경을 그룹에 접어 올린다: 쇼핑 캠페인의 실제 레버는 소재 입찰이라
  (실측 96%가 소재-레벨) entity_type='adgroup' 행만 세면 **그룹이 아무 일도 안 한 것처럼
  보인다**. naver_adgroup_product로 소재→그룹을 되짚어 같은 그룹의 이력으로 합친다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import (
    NaverAdgroupProduct,
    NaverCampaignSettings,
    NaverChangeLog,
    NaverEntity,
)
from app.services.naver_ad import (
    budget_pacing,
    budget_pacing_view,
    campaign_roas_lines,
    change_log_narrator,
    group_state_badge,
    metrics_aggregator,
    naver_execution_harness,
    today_proxy_revenue,
)
from app.services.naver_ad.alert_humanizer import (
    campaign_type_label,
    clean_name,
    managed_by_label,
)
from app.utils.kst import kst_now, kst_today

DEFAULT_SERIES_DAYS = 30
MAX_SERIES_DAYS = 180

# "최근 변경"의 창. 성과 창(days, 기본 30)과 **의도적으로 다르다** — 30일 전에 한 번 올린 것을
# "지금 확장 중"이라고 말하면 거짓이다. 상태 배지는 지금 무슨 일이 벌어지는지를 말해야 한다.
CHANGE_WINDOW_DAYS = 7

# 상태 배지가 세는 액션 = 우리가 실제로 광고 API에 쓴 것 + BP 레인 라벨. ①한눈에(H1)와 같은
# 원천에서 **파생**시킨다 — 하드코딩하면 레인이 늘 때 이 화면만 조용히 못 세게 된다.
_TRACKED_ACTIONS: frozenset[str] = frozenset(
    naver_execution_harness.EXECUTION_ACTIONS | set(budget_pacing.PACING_ACTIONS)
)
# ④예산이 세는 '예산을 바꾼 일'.
_BUDGET_ACTIONS: frozenset[str] = frozenset(
    {"update_budget"} | set(budget_pacing.PACING_ACTIONS)
)


class CampaignNotFound(Exception):
    """요청한 캠페인이 naver_entity에 없다(삭제됐거나 오타). 라우터가 404로 옮긴다."""


def _campaign_entity(db: Session, campaign_id: str) -> NaverEntity:
    row = (
        db.query(NaverEntity)
        .filter(NaverEntity.entity_type == "campaign", NaverEntity.entity_id == campaign_id)
        .one_or_none()
    )
    if row is None:
        raise CampaignNotFound(campaign_id)
    return row


def _change_rows(db: Session, campaign_id: str, since: datetime) -> list[NaverChangeLog]:
    """최근 창의 우리 변경 시도 전건(집행·차단·실패). 필터 규약은 perf_today_harness와 동일."""
    rows = (
        db.query(NaverChangeLog)
        .filter(
            NaverChangeLog.campaign_id == campaign_id,
            NaverChangeLog.changed_at >= since,
            NaverChangeLog.action.in_(sorted(_TRACKED_ACTIONS)),
            NaverChangeLog.dry_run.is_(False),
        )
        .order_by(NaverChangeLog.changed_at.asc())
        .all()
    )
    return [r for r in rows if r.after_value is not None or r.outcome == "failed"]


def _adgroup_of_ads(db: Session, ad_ids: list[str]) -> dict[str, str]:
    """{ad_id: adgroup_id} — 1쿼리. 소재 변경을 소속 그룹 이력으로 접어 올리기 위한 역참조."""
    if not ad_ids:
        return {}
    rows = (
        db.query(NaverAdgroupProduct.ad_id, NaverAdgroupProduct.adgroup_id)
        .filter(NaverAdgroupProduct.ad_id.in_(list(dict.fromkeys(ad_ids))))
        .all()
    )
    return {aid: gid for aid, gid in rows if aid and gid}


def _group_change_signals(
    db: Session, campaign_id: str, group_ids: list[str], since: datetime
) -> dict[str, dict]:
    """{adgroup_id: {blocked_reasons, raised, lowered, unknown}} — 배지 판정의 이력 원료.

    캠페인-레벨 변경(예산)은 특정 그룹의 이력이 아니므로 **어느 그룹에도 붙이지 않는다**
    (붙이면 모든 그룹이 같은 이유로 같은 배지를 단다 — 정보가 0이 된다)."""
    rows = _change_rows(db, campaign_id, since)
    ad_ids = [r.entity_id for r in rows if r.entity_type == "ad" and r.entity_id]
    group_of_ad = _adgroup_of_ads(db, ad_ids)
    known = set(group_ids)

    signals: dict[str, dict] = {
        gid: {"blocked_reasons": [], "raised": False, "lowered": False, "unknown": False}
        for gid in group_ids
    }
    for row in rows:
        if row.entity_type == "adgroup":
            gid = row.entity_id
        elif row.entity_type == "ad":
            gid = group_of_ad.get(row.entity_id or "", "")
        else:
            continue  # campaign/keyword/search_term 은 그룹 배지의 근거가 아니다
        if gid not in known:
            continue
        sig = signals[gid]
        state = change_log_narrator.execution_state(row)
        if state == change_log_narrator.STATE_BLOCKED:
            sig["blocked_reasons"].append(change_log_narrator.block_reason(row.rationale))
        elif state == change_log_narrator.STATE_UNKNOWN:
            sig["unknown"] = True
        else:
            d = change_log_narrator.change_direction(row)
            if d > 0:
                sig["raised"] = True
            elif d < 0:
                sig["lowered"] = True
    return signals


def build_campaign(
    db: Session, campaign_id: str, *, days: int = DEFAULT_SERIES_DAYS, today: date | None = None
) -> dict:
    """③캠페인 상세 — 일별 ROAS 추이(+BEP선·목표선) + 그룹 상태 배지.

    series의 ROAS는 **네이버 확정 기준**(직접+간접 전환매출 ÷ 광고비)이다 — 카드의 당일
    프록시와 정의가 다르므로 화면이 둘을 같은 선에 그리면 안 된다(라벨로 구분한다).
    """
    days = max(1, min(int(days), MAX_SERIES_DAYS))
    today = today or kst_today()
    date_to = today - timedelta(days=1)          # D-0 제외(창 관례)
    date_from = date_to - timedelta(days=days - 1)

    entity = _campaign_entity(db, campaign_id)
    mapped = set(today_proxy_revenue.product_ids_by_campaign(db, [campaign_id]))
    lines = campaign_roas_lines.resolve(db, [campaign_id], mapped_campaign_ids=mapped).get(
        campaign_id, {"target_roas": None, "bep_roas": None}
    )
    # ★관리 주체(D-NAO-105 리뷰): 46캠페인 전부에 이 API가 열려 있는데 43개는 우리가 손대지
    #   않는 광고다. 그 그룹에 "우리가 …하고 있습니다"를 붙이면 하지도 않는 관리를 한다고
    #   말하는 것이라, 배지를 성과 서술('관찰만')로 강등한다. 설정 행이 없으면 optimizer='none'
    #   (naver_execution_harness._resolve_optimizer와 같은 시맨틱 — 단일 진실).
    settings = (
        db.query(NaverCampaignSettings)
        .filter(NaverCampaignSettings.campaign_id == campaign_id)
        .one_or_none()
    )
    optimizer = settings.optimizer if settings else "none"
    auto_operate = bool(settings.auto_operate) if settings else False
    managed_by_us = optimizer == "ours"

    date_agg = metrics_aggregator.aggregate(
        db, date_from, date_to, grain="date", campaign_filter=campaign_id
    )
    series = [
        {
            "date": r["ad_date"], "cost": r["cost"], "imp": r["imp"], "clk": r["clk"],
            "conv_amt": r["conv_amt"], "roas": r["roas_naver"], "avg_rank": r["avg_rank"],
        }
        for r in date_agg["rows"]
    ]

    group_agg = metrics_aggregator.aggregate(
        db, date_from, date_to, grain="adgroup", campaign_filter=campaign_id
    )
    group_ids = [r["adgroup_id"] for r in group_agg["rows"] if r["adgroup_id"]]
    group_entities = {
        e.entity_id: e
        for e in db.query(NaverEntity)
        .filter(NaverEntity.entity_type == "adgroup", NaverEntity.campaign_id == campaign_id)
        .all()
    }
    # 창 안에 집행이 없어 집계에는 없지만 지금 살아있는 그룹도 배지를 단다(0은 '없는 것'이
    # 아니다 — D-47-h). 삭제된 그룹은 제외한다.
    for gid, e in group_entities.items():
        if gid not in group_ids and (e.status or "") != "deleted":
            group_ids.append(gid)

    since = datetime.combine(today - timedelta(days=CHANGE_WINDOW_DAYS), datetime.min.time())
    signals = _group_change_signals(db, campaign_id, group_ids, since)
    perf_by_group = {r["adgroup_id"]: r for r in group_agg["rows"] if r["adgroup_id"]}

    groups: list[dict] = []
    for gid in group_ids:
        e = group_entities.get(gid)
        perf = perf_by_group.get(gid, {})
        sig = signals.get(gid, {})
        name = clean_name(e.name if e else None) or "이름 없는 그룹"
        badge = group_state_badge.judge(
            name=name,
            status=(e.status if e else None),
            status_reason=(e.status_reason if e else None),
            cost=int(perf.get("cost") or 0),
            roas=perf.get("roas_naver"),
            bep_roas=lines["bep_roas"],
            target_roas=lines["target_roas"],
            blocked_reasons=list(sig.get("blocked_reasons") or []),
            raised_recently=bool(sig.get("raised")),
            lowered_recently=bool(sig.get("lowered")),
            unknown_recently=bool(sig.get("unknown")),
            window_days=days,
            managed_by_us=managed_by_us,
        )
        groups.append({
            **badge,
            "adgroup_id": gid,  # 화면 미표시(title 전용, D-NAO-103①)
            "cost": int(perf.get("cost") or 0),
            "imp": int(perf.get("imp") or 0),
            "clk": int(perf.get("clk") or 0),
            "conv_amt": int(perf.get("conv_amt") or 0),
            "roas": perf.get("roas_naver"),
        })
    groups.sort(key=lambda g: (-g["cost"], g["name"]))

    return {
        "campaign_id": campaign_id,
        "name": clean_name(entity.name) or "이름 없는 광고",
        "type_label": campaign_type_label(entity.campaign_type) or "기타 지면",
        "managed_by_label": managed_by_label(optimizer, auto_operate),
        "managed_by_us": managed_by_us,
        "managed_note": (
            None if managed_by_us else
            "이 광고는 우리 시스템이 운영하지 않습니다. 아래는 성과를 보여줄 뿐, 우리가 한 "
            "조치가 아닙니다."
        ),
        "window": {"from": date_from.isoformat(), "to": date_to.isoformat(), "days": days},
        "change_window_days": CHANGE_WINDOW_DAYS,
        "lines": lines,
        "series": series,
        "series_note": (
            "일별 성과는 네이버 광고가 확정한 값입니다(전날까지). 오늘 숫자는 위 카드의 "
            "추정치를 보세요."
        ),
        "groups": groups,
        "totals": {
            "cost": date_agg["totals"]["cost"],
            "conv_amt": date_agg["totals"]["conv_amt"],
            "roas": date_agg["totals"]["roas_naver"],
            "imp": date_agg["totals"]["imp"],
            "clk": date_agg["totals"]["clk"],
        },
    }


def build_budget(
    db: Session,
    *,
    day: date | None = None,
    campaign_id: str | None = None,
    now: datetime | None = None,
) -> dict:
    """④예산 — 선택 날짜의 시간별 누적 소진 곡선 + 암전 구간 + 그날의 예산 변경 이력.

    ★`budget_changes`는 BP 레인(D-NAO-102) 배포 전에는 **빈 배열이 정상**이다. 빈 배열을
    에러로 다루지 않는다 — 화면은 "아직 자동 증액 기록이 없습니다"로 말한다(계획서 §4-ⓒ).
    """
    now = now or kst_now()
    day = day or now.date()
    campaign_ids = [campaign_id] if campaign_id else None

    entities = db.query(NaverEntity).filter(NaverEntity.entity_type == "campaign").all()
    names = {e.entity_id: clean_name(e.name) or "" for e in entities}
    # statusReason은 **현재 상태**라 오늘 날짜에서만 확증으로 쓸 수 있다(어제 곡선에 오늘의
    # 상태를 갖다 붙이면 시점이 어긋난다 — 원칙22).
    limited = (
        {e.entity_id for e in entities
         if (e.status_reason or "") == budget_pacing_view.LIMITED_BY_BUDGET_REASON}
        if day == now.date()
        else set()
    )

    curves = budget_pacing_view.build(
        db, day, campaign_ids=campaign_ids, names=names, limited_by_budget=limited
    )

    start = datetime.combine(day, datetime.min.time())
    end = datetime.combine(day, datetime.max.time())
    q = (
        db.query(NaverChangeLog)
        .filter(
            NaverChangeLog.changed_at >= start,
            NaverChangeLog.changed_at <= end,
            NaverChangeLog.action.in_(sorted(_BUDGET_ACTIONS)),
            NaverChangeLog.dry_run.is_(False),
        )
    )
    if campaign_id:
        q = q.filter(NaverChangeLog.campaign_id == campaign_id)
    budget_rows = [
        r for r in q.order_by(NaverChangeLog.changed_at.asc()).all()
        if r.after_value is not None or r.outcome == "failed"
    ]
    budget_changes = [
        {
            **item,
            "hour": int(item["time_label"].split(":")[0]) if item["time_label"][:2].isdigit() else None,
        }
        for item in change_log_narrator.narrate(db, budget_rows)
    ]

    return {
        "date": day.isoformat(),
        "is_today": day == now.date(),
        "curves": curves,
        "budget_changes": budget_changes,
        "budget_changes_empty_reason": (
            None if budget_changes else "이날은 예산을 자동으로 바꾼 기록이 없습니다."
        ),
        "data_note": (
            "시간별 소진은 매시간 저장한 누적 광고비입니다. 관측이 없는 시간대는 선이 이어지지 "
            "않습니다."
        ),
    }
