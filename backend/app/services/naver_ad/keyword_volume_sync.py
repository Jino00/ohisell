# keyword_volume_sync.py — keyword_volume_sync_harness (저클릭 키워드 월검색량 보강, P2-S1⑤)
# 역할: naver_entity(WEB_SITE, keyword, on) 중 최근 클릭이 없거나 적은 키워드를 골라
#   keywordstool로 월검색량·경쟁도를 조회해 채운다 — D-NAO-18 3단 분류(판정가능/육성후보/정리)의
#   입력 데이터. 전량(수만 건) 대상은 API 호출량이 과도해 저클릭 키워드로 스코프 한정.
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverEntity
from app.services.naver_sa_ad_fetcher import fetch_keyword_volumes
from app.utils.kst import kst_now, kst_today

log = logging.getLogger(__name__)

_LOOKBACK_DAYS = 30  # D-NAO-9 모수 게이트 창(7~30일)의 상한 재사용
_LOW_CLICK_THRESHOLD = 10  # 30일 누적 클릭 10 미만 = "저클릭"(D-NAO-18 대상)


def _low_click_keyword_ids(db: Session) -> set[str]:
    cutoff = kst_today() - timedelta(days=_LOOKBACK_DAYS)
    clicks = dict(
        db.query(NaverAdDaily.keyword_id, sqlfunc.sum(NaverAdDaily.clk))
        .filter(NaverAdDaily.ad_date >= cutoff, NaverAdDaily.keyword_id != "")
        .group_by(NaverAdDaily.keyword_id).all()
    )
    all_kw_ids = {
        e.entity_id for e in db.query(NaverEntity.entity_id).filter(
            NaverEntity.entity_type == "keyword", NaverEntity.status == "on",
        )
    }
    return {kid for kid in all_kw_ids if clicks.get(kid, 0) < _LOW_CLICK_THRESHOLD}


def sync_keyword_volumes(db: Session, *, limit: int = 1000) -> dict:
    """저클릭 키워드 최대 limit개의 월검색량을 keywordstool로 갱신.

    limit으로 1회 실행당 API 호출량을 제한(5개/콜이므로 limit=1000→콜 200회).
    이미 volume_updated_at이 있는 키워드는 후순위(미조회 우선)로 갱신.
    """
    low_click_ids = _low_click_keyword_ids(db)
    if not low_click_ids:
        return {"targeted": 0, "updated": 0}

    entities = db.query(NaverEntity).filter(
        NaverEntity.entity_type == "keyword",
        NaverEntity.entity_id.in_(low_click_ids),
    ).order_by(NaverEntity.volume_updated_at.is_(None).desc(), NaverEntity.id).limit(limit).all()
    if not entities:
        return {"targeted": 0, "updated": 0}

    by_name: dict[str, list[NaverEntity]] = {}
    for e in entities:
        by_name.setdefault(e.name, []).append(e)

    volumes = fetch_keyword_volumes(list(by_name.keys()))
    now = kst_now()
    updated = 0
    for name, vol in volumes.items():
        for e in by_name.get(name, []):
            e.monthly_volume = vol["monthly_volume"]
            e.competition = vol["competition"]
            e.volume_updated_at = now
            updated += 1
    db.commit()

    log.info("keyword_volume_sync: 저클릭대상=%d 조회=%d 갱신=%d",
              len(low_click_ids), len(entities), updated)
    return {"targeted": len(entities), "updated": updated}
