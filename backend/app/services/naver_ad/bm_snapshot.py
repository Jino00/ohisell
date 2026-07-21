# bm_snapshot.py — SA-1 구조 스냅샷러 (BM 벤치마크 레이어, D-NAO-78)
# 역할: 07:35 entity_sync 직후 naver_entity(DB)를 읽어 캠페인·그룹 grain 구조를 날짜별
#   naver_entity_snapshot에 upsert(멱등, 같은 날 재실행=중복 없음). 네이버 API 호출 0(DB만) —
#   관찰 전용(§0 금지선 1). 키워드 grain은 저장 안 하고 그룹 행에 집계만 남긴다(§2).
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date

from sqlalchemy.orm import Session

from app.models import NaverCampaignSettings, NaverEntity, NaverEntitySnapshot
from app.utils.kst import kst_now, kst_today

log = logging.getLogger(__name__)


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


def _snapshot_fields(e: NaverEntity, opt_map, kw_agg, now) -> dict:
    """엔티티 1건 → 스냅샷 컬럼 dict. Phase 1: name/status/optimizer/bid_amt/키워드집계만.

    그룹 키워드 집계는 WEB_SITE만 유효(다른 유형은 키워드 미동기화 → NULL, 0으로 오도 금지)."""
    kw_count: int | None = None
    kw_avg: int | None = None
    bid_amt: int | None = None
    if e.entity_type == "adgroup":
        bid_amt = _norm_bid(e.bid_amt)
        if e.campaign_type == "WEB_SITE":
            kw_count, kw_avg = kw_agg.get(e.entity_id, (0, None))
    return {
        "parent_id": e.parent_id or "",
        "campaign_id": e.campaign_id or "",
        "campaign_type": e.campaign_type or "",
        "optimizer": opt_map.get(e.campaign_id, "none"),
        "name": e.name or "",
        "status": e.status or "on",
        "bid_amt": bid_amt,
        "keyword_count": kw_count,
        "keyword_avg_bid": kw_avg,
        "synced_at": now,  # ★kst_now 명시(server_default는 UTC — stale 판정 오독 회피)
    }


def snapshot_entities(db: Session, *, snapshot_date: date | None = None) -> dict:
    """naver_entity를 읽어 캠페인·그룹 grain 스냅샷을 upsert(멱등, 일 1회, 0 GET).

    같은 snapshot_date 재실행 시 기존 행을 갱신(중복 없음). 키워드 grain은 저장 안 하고
    그룹 행에 집계(keyword_count/avg_bid)만 남긴다(§2 — 3,300만행/년 회피).
    """
    sdate = snapshot_date or kst_today()
    now = kst_now()
    entities = db.query(NaverEntity).all()
    opt_map = _optimizer_map(db)
    kw_agg = _keyword_aggregates(entities)

    existing = {
        (s.entity_type, s.entity_id): s
        for s in db.query(NaverEntitySnapshot)
        .filter(NaverEntitySnapshot.snapshot_date == sdate).all()
    }

    n_campaign = n_adgroup = kw_total = 0
    for e in entities:
        if e.entity_type not in ("campaign", "adgroup"):
            continue
        fields = _snapshot_fields(e, opt_map, kw_agg, now)
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
    result = {"snapshot_date": str(sdate), "campaigns": n_campaign, "adgroups": n_adgroup, "keyword_total": kw_total}
    log.info("[BM] SA-1 스냅샷: %s", result)
    return result
