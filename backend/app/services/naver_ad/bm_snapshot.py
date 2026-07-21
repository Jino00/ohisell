# bm_snapshot.py — SA-1 구조 스냅샷러 (BM 벤치마크 레이어, D-NAO-78)
# 역할: 07:35 entity_sync 직후 naver_entity(DB)를 읽어 캠페인·그룹 grain 구조를 날짜별
#   naver_entity_snapshot에 upsert(멱등, 같은 날 재실행=중복 없음). Phase 1/2 필드는 DB만
#   읽어 0 GET. Phase 3(예산·확장검색, 일별)은 캠페인당 GET 추가(≈46 GET/일, §7) — 실패는
#   전면 fail-open(NULL 유지, SA-1 나머지 필드는 그대로 진행). 주간 deep(제외키워드·소재수)은
#   update_deep_dimensions가 별도 레인(bm_deep, 일요일)에서 채운다. 관찰 전용(§0 금지선 1) —
#   실행 손(naver_sa_writer/naver_execution_harness)은 import조차 하지 않는다.
from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import date

from sqlalchemy.orm import Session

from app.models import NaverCampaignSettings, NaverEntity, NaverEntitySnapshot
from app.services.naver_sa_ad_fetcher import (
    get_ad_count,
    get_adgroups,
    get_campaigns_full,
    get_restricted_keyword_count,
)
from app.utils.kst import kst_now, kst_today

log = logging.getLogger(__name__)

_DEEP_SLEEP_S = 0.05  # 주간 deep GET 그룹 간 짧은 sleep(rate limit 배려, §7)


def _norm_bid(v) -> int | None:
    """입찰가를 int|None으로 정규화 — 집계 전 통일.

    ★SQLite는 동적 타입이라 fetcher가 넘긴 값이 str로 저장될 수 있다(entity_sync._norm_bid
    docstring 참조). 정규화 없이 평균 내면 str+int 오류가 난다. 파싱 불가는 None(fail-safe).
    """
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _optimizer_map(db: Session) -> dict[str, str]:
    """campaign_id → optimizer(none/ours/mop). settings 없는 캠페인=none(대행사 관찰 대상)."""
    return {
        s.campaign_id: (s.optimizer or "none")
        for s in db.query(NaverCampaignSettings).all()
    }


def _keyword_aggregates(entities: list[NaverEntity]) -> dict[str, tuple[int, int | None]]:
    """adgroup_id → (활성 키워드 수, 평균 입찰). status='on' 키워드만(off/deleted 제외).

    키워드 행은 naver_entity에 WEB_SITE(파워링크)만 동기화됨(NaverEntity docstring) →
    SHOPPING/BRAND 그룹은 여기 안 잡힌다(호출부에서 NULL 처리)."""
    counts: dict[str, int] = defaultdict(int)
    bid_sums: dict[str, int] = defaultdict(int)
    bid_ns: dict[str, int] = defaultdict(int)
    for e in entities:
        if e.entity_type != "keyword" or e.status != "on":
            continue
        counts[e.parent_id] += 1
        b = _norm_bid(e.bid_amt)
        if b is not None:
            bid_sums[e.parent_id] += b
            bid_ns[e.parent_id] += 1
    return {
        gid: (n, round(bid_sums[gid] / bid_ns[gid]) if bid_ns[gid] else None)
        for gid, n in counts.items()
    }


def _fetch_campaign_budgets(campaigns_full: list[dict] | None) -> tuple[dict[str, int | None], int]:
    """캠페인 dailyBudget(get_campaigns_full, +1 GET, Phase 3 일별) → campaign_id→daily_budget.

    campaigns_full 주입 시 GET 생략(테스트/재사용, 원칙18-8 — entity_sync.collect_entities와
    동일 관례). 실패는 로그+빈 dict(fail-open, daily_budget=NULL 유지 — SA-1 나머지 필드는
    그대로 진행, §0 금지선 5). Returns: (campaign_id→daily_budget, 실제 GET 호출 수)."""
    if campaigns_full is not None:
        return {c["campaign_id"]: c.get("daily_budget") for c in campaigns_full}, 0
    try:
        rows = get_campaigns_full()
    except Exception as e:  # noqa: BLE001 — Phase3 GET 실패가 SA-1 전체를 막지 않음
        log.warning("[BM] SA-1 P3 캠페인 예산 GET 실패(fail-open, daily_budget=NULL 유지): %s", e)
        return {}, 0
    return {c["campaign_id"]: c.get("daily_budget") for c in rows}, 1


def _fetch_adgroup_dims(
    campaign_ids: list[str],
    adgroups_by_campaign: dict[str, list[dict]] | None,
) -> tuple[dict[str, dict], int]:
    """그룹별 daily_budget·extended_search(get_adgroups 확장, 캠페인당 +1 GET, Phase 3 일별).

    캠페인 단위 fail-open — 한 캠페인의 GET이 실패해도 나머지 캠페인은 계속 진행(그 캠페인
    소속 그룹은 이번 회차 NULL 유지). adgroups_by_campaign 주입 시 GET 생략(테스트/재사용).
    Returns: ({adgroup_id: {"daily_budget","extended_search"}}, 실제 GET 호출 수)."""
    out: dict[str, dict] = {}
    get_count = 0
    for cid in campaign_ids:
        try:
            if adgroups_by_campaign is not None:
                ags = adgroups_by_campaign.get(cid) or []
            else:
                ags = get_adgroups(cid)
                get_count += 1
        except Exception as e:  # noqa: BLE001 — 캠페인 1건 GET 실패는 skip(§0 금지선 5)
            log.warning("[BM] SA-1 P3 그룹 확장차원 GET 실패(campaign=%s, skip): %s", cid, e)
            continue
        for a in ags:
            out[a["adgroup_id"]] = {
                "daily_budget": a.get("daily_budget"),
                "extended_search": a.get("extended_search"),
            }
    return out, get_count


def _snapshot_fields(e: NaverEntity, opt_map, kw_agg, now, budget_map, adgroup_dims) -> dict:
    """엔티티 1건 → 스냅샷 컬럼 dict. Phase 1: name/status/optimizer/bid_amt/키워드집계.
    Phase 3(D-NAO-78): 캠페인 daily_budget(budget_map)·그룹 daily_budget+extended_search
    (adgroup_dims) 추가 채움 — 미수집/실패 시 NULL(하위호환, §2 nullable 규약).

    그룹 키워드 집계는 WEB_SITE만 유효(다른 유형은 키워드 미동기화 → NULL, 0으로 오도 금지)."""
    kw_count: int | None = None
    kw_avg: int | None = None
    bid_amt: int | None = None
    daily_budget: int | None = None
    extended_search: bool | None = None
    if e.entity_type == "campaign":
        daily_budget = budget_map.get(e.entity_id)
    if e.entity_type == "adgroup":
        bid_amt = _norm_bid(e.bid_amt)
        if e.campaign_type == "WEB_SITE":
            kw_count, kw_avg = kw_agg.get(e.entity_id, (0, None))
        dims = adgroup_dims.get(e.entity_id)
        if dims:
            daily_budget = dims.get("daily_budget")
            extended_search = dims.get("extended_search")
    return {
        "parent_id": e.parent_id or "",
        "campaign_id": e.campaign_id or "",
        "campaign_type": e.campaign_type or "",
        "optimizer": opt_map.get(e.campaign_id, "none"),
        "name": e.name or "",
        "status": e.status or "on",
        "daily_budget": daily_budget,
        "bid_amt": bid_amt,
        "extended_search": extended_search,
        "keyword_count": kw_count,
        "keyword_avg_bid": kw_avg,
        "synced_at": now,  # ★kst_now 명시(server_default는 UTC — stale 판정 오독 회피)
    }


def snapshot_entities(
    db: Session,
    *,
    snapshot_date: date | None = None,
    campaigns_full: list[dict] | None = None,
    adgroups_by_campaign: dict[str, list[dict]] | None = None,
) -> dict:
    """naver_entity를 읽어 캠페인·그룹 grain 스냅샷을 upsert(멱등, 일 1회).

    같은 snapshot_date 재실행 시 기존 행을 갱신(중복 없음). 키워드 grain은 저장 안 하고
    그룹 행에 집계(keyword_count/avg_bid)만 남긴다(§2 — 3,300만행/년 회피). Phase 1/2 필드는
    0 GET(DB만). Phase 3(예산·확장검색, D-NAO-78)은 campaigns_full/adgroups_by_campaign 미주입
    시 get_campaigns_full/get_adgroups를 호출(≈46 GET/일, §7) — 주입 시 GET 생략(테스트/재사용,
    원칙18-8). 두 차원 모두 fail-open(실패해도 나머지 필드는 그대로 커밋).
    """
    sdate = snapshot_date or kst_today()
    now = kst_now()
    entities = db.query(NaverEntity).all()
    opt_map = _optimizer_map(db)
    kw_agg = _keyword_aggregates(entities)

    budget_map, n_budget_get = _fetch_campaign_budgets(campaigns_full)
    campaign_ids = [
        e.entity_id for e in entities if e.entity_type == "campaign" and e.status != "deleted"
    ]
    adgroup_dims, n_adgroup_get = _fetch_adgroup_dims(campaign_ids, adgroups_by_campaign)

    existing = {
        (s.entity_type, s.entity_id): s
        for s in db.query(NaverEntitySnapshot)
        .filter(NaverEntitySnapshot.snapshot_date == sdate).all()
    }

    n_campaign = n_adgroup = kw_total = 0
    for e in entities:
        if e.entity_type not in ("campaign", "adgroup"):
            continue
        fields = _snapshot_fields(e, opt_map, kw_agg, now, budget_map, adgroup_dims)
        row = existing.get((e.entity_type, e.entity_id))
        if row is None:
            db.add(NaverEntitySnapshot(
                snapshot_date=sdate, entity_type=e.entity_type, entity_id=e.entity_id, **fields,
            ))
        else:
            for k, v in fields.items():
                setattr(row, k, v)
        if e.entity_type == "campaign":
            n_campaign += 1
        else:
            n_adgroup += 1
            kw_total += fields["keyword_count"] or 0

    db.commit()
    result = {
        "snapshot_date": str(sdate), "campaigns": n_campaign, "adgroups": n_adgroup,
        "keyword_total": kw_total,
        "get_calls": n_budget_get + n_adgroup_get,  # 라이브 합격 실측용(§Phase3, 원칙22)
    }
    log.info("[BM] SA-1 스냅샷: %s", result)
    return result


def _try_get(fn, adgroup_id: str, label: str) -> tuple[int | None, bool]:
    """단일 GET 시도 — 실패 시 (None, False)+log.warning, 성공 시 (값, True). 그룹 단위
    fail-open의 최소 단위(§0 금지선 5) — 한 그룹의 실패가 나머지 그룹 갱신을 막지 않는다."""
    try:
        return fn(adgroup_id), True
    except Exception as e:  # noqa: BLE001 — 개별 그룹 GET 실패는 skip
        log.warning("[BM] SA-1 주간 deep %s GET 실패(adgroup=%s, skip): %s", label, adgroup_id, e)
        return None, False


def update_deep_dimensions(db: Session, *, snapshot_date: date | None = None) -> dict:
    """주간 deep 차원(제외키워드 수·소재 수) 갱신 — bm_deep 레인(일요일 09:20 KST, §7).

    오늘 스냅샷의 그룹 행(entity_type=adgroup, status!=deleted)마다 GET을 호출해
    negative_kw_count(WEB_SITE 그룹만, restricted-keywords는 파워링크 전용 API)·ad_count(전
    유형)를 당일 행에 UPDATE(신규 행 생성 안 함). ~600그룹 → 제외키워드는 WEB_SITE(~513그룹)만
    호출해 ≈513+600=1,113 GET(§7 추정 ~1,200과 정합). 그룹 단위 fail-open — 개별 GET 실패는
    그 그룹만 이번 주 갱신 skip(기존 값 유지), 크론 전체는 계속 진행. 그룹 간 짧은 sleep으로
    rate limit을 배려한다.
    """
    sdate = snapshot_date or kst_today()
    groups = (
        db.query(NaverEntitySnapshot)
        .filter(
            NaverEntitySnapshot.snapshot_date == sdate,
            NaverEntitySnapshot.entity_type == "adgroup",
            NaverEntitySnapshot.status != "deleted",
        )
        .all()
    )

    n_get = n_fail = 0
    for row in groups:
        if row.campaign_type == "WEB_SITE":  # SHOPPING/BRAND는 제외키워드 API 대상 아님(GET 절약)
            neg, ok = _try_get(get_restricted_keyword_count, row.entity_id, "제외키워드")
            if ok:
                row.negative_kw_count = neg
                n_get += 1
            else:
                n_fail += 1

        ad, ok = _try_get(get_ad_count, row.entity_id, "소재수")
        if ok:
            row.ad_count = ad
            n_get += 1
        else:
            n_fail += 1

        time.sleep(_DEEP_SLEEP_S)

    db.commit()
    result = {
        "snapshot_date": str(sdate), "groups": len(groups),
        "get_calls": n_get, "failures": n_fail,
    }
    log.info("[BM] SA-1 주간 deep 차원 갱신: %s", result)
    return result
