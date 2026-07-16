# keyword_hourly_sweep.py — keyword_hourly_accrual Harness (키워드/쇼핑그룹 시간별 축적, D-NAO-46②)
# 역할: naver_ad_daily D-1의 imp>0 유닛(build_sweep_targets, 순수) → 유닛별 hh24 시간대 곡선을
#   fetch_entity_hh24로 1콜씩 조회해 naver_keyword_hourly에 영구 축적(sweep_keyword_hourly,
#   쓰기 Harness). hh24 상세는 네이버가 최근 7일만 보존 — 캐치업으로 [D-6,D-2] 결손 유닛만
#   보수적으로 재수집한다. 전부 GET(관찰 전용), 입찰/예산/상태에 손대지 않는다.
# 근거: docs/PLAN_naver-ad-keyword-hourly-accrual.md §4, docs/references/32_naver_sa_hh24_breakdown_recon_20260717.md
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverKeywordHourly
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.services.naver_sa_ad_fetcher import fetch_entity_hh24
from app.utils.kst import kst_today

log = logging.getLogger(__name__)

_CALL_CAP = 3500  # 1실행당 hh24 콜 상한(가드) — 초과분은 다음 실행으로 미룸
_RETAIN_DAYS = 365  # naver_hourly_snapshot과 동일 상수(D-NAO-46, 영구 축적 방침)
_CATCHUP_LOOKBACK_START = 6  # D-6까지만(보수적 — hh24 보존은 최근 7일, ref 32 §4)
_CATCHUP_LOOKBACK_END = 2   # D-2까지(D-1=sweep_date 본진행분 제외)


def build_sweep_targets(db: Session, sweep_date: date) -> list[dict]:
    """naver_ad_daily에서 sweep_date의 imp>0 유닛을 스윕 타깃으로 도출(순수 함수).

    WEB_SITE: keyword_id(nkw-…) → entity_type='keyword'(adgroup_id=소속 그룹).
    SHOPPING/BRAND_SEARCH: keyword_id='' sentinel 행의 adgroup_id → entity_type='adgroup'.
    campaign_backfill의 캠페인 grain 소급행(adgroup_id==BACKFILL_SENTINEL_ADGROUP)은
    실단위 유닛이 아니므로 제외(2배 함정 규약, PLAN §5).

    Returns: [{"entity_type","entity_id","adgroup_id","campaign_id","campaign_type"}, ...]
    """
    rows = (
        db.query(NaverAdDaily)
        .filter(
            NaverAdDaily.ad_date == sweep_date,
            NaverAdDaily.imp > 0,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .all()
    )
    targets: dict[tuple[str, str], dict] = {}
    for r in rows:
        if r.campaign_type == "WEB_SITE":
            if not r.keyword_id:
                continue
            key = ("keyword", r.keyword_id)
            targets[key] = {
                "entity_type": "keyword", "entity_id": r.keyword_id,
                "adgroup_id": r.adgroup_id, "campaign_id": r.campaign_id,
                "campaign_type": r.campaign_type,
            }
        else:  # SHOPPING/BRAND_SEARCH
            if not r.adgroup_id:
                continue
            key = ("adgroup", r.adgroup_id)
            targets[key] = {
                "entity_type": "adgroup", "entity_id": r.adgroup_id,
                "adgroup_id": "", "campaign_id": r.campaign_id,
                "campaign_type": r.campaign_type,
            }
    return list(targets.values())


def _replace_rows(db: Session, ad_date: date, target: dict, hh_rows: list[dict]) -> None:
    """(ad_date, entity_id) 기존 행을 삭제 후 재삽입 — 교체 upsert(멱등, hourly_snapshot 패턴)."""
    db.execute(delete(NaverKeywordHourly).where(
        NaverKeywordHourly.ad_date == ad_date,
        NaverKeywordHourly.entity_id == target["entity_id"],
    ))
    for h in hh_rows:
        db.add(NaverKeywordHourly(
            ad_date=ad_date, hour=h["hour"], entity_type=target["entity_type"],
            entity_id=target["entity_id"], adgroup_id=target.get("adgroup_id") or "",
            campaign_id=target["campaign_id"], campaign_type=target["campaign_type"],
            imp=h["imp"], clk=h["clk"], cost=h["cost"], avg_rank=h.get("avg_rank"),
        ))


def _existing_entity_ids(db: Session, ad_date: date) -> set[str]:
    return {
        eid for (eid,) in
        db.query(NaverKeywordHourly.entity_id).filter(NaverKeywordHourly.ad_date == ad_date).distinct().all()
    }


def sweep_keyword_hourly(db: Session, *, sweep_date: date | None = None, fetch=None) -> dict:
    """일 1회 D-1 hh24 스윕 — 타깃 도출 → 유닛별 fetch+교체 upsert → 캐치업[D-6,D-2] → 365d 롤링.

    sweep_date 기본 = kst_today()-1일. fetch 미주입 시 fetch_entity_hh24(테스트 주입, 원칙18-8).
    유닛 단위 fetch 실패는 log+skip(카운트, 크론 체인을 죽이지 않음). 캐치업은 "ad_daily
    imp>0 유닛인데 그 (ad_date, entity_id) 행이 0"인 유닛만(날짜 단위가 아닌 유닛 단위
    완전성). 콜 상한 3,500/실행(primary+catchup 합산) — 초과분은 다음 실행으로 미룸.
    """
    if fetch is None:
        fetch = fetch_entity_hh24
    today = kst_today()
    if sweep_date is None:
        sweep_date = today - timedelta(days=1)

    calls = 0
    rows_written = 0
    failed = 0
    cap_hit = False

    targets = build_sweep_targets(db, sweep_date)
    for t in targets:
        if calls >= _CALL_CAP:
            cap_hit = True
            break
        calls += 1
        try:
            hh = fetch(t["entity_id"], sweep_date)
        except Exception as e:
            log.warning("keyword_hourly_sweep 유닛 실패 skip: entity=%s date=%s: %s",
                        t["entity_id"], sweep_date, e)
            failed += 1
            continue
        _replace_rows(db, sweep_date, t, hh)
        rows_written += len(hh)

    # 캐치업: [D-6, D-2] 범위에서 ad_daily imp>0 유닛인데 naver_keyword_hourly에 그
    # (ad_date, entity_id) 행이 0인 유닛만 추가 스윕(유닛 단위 완전성, 날짜 단위 아님).
    catchup_calls = 0
    if not cap_hit:
        catchup_start = today - timedelta(days=_CATCHUP_LOOKBACK_START)
        catchup_end = today - timedelta(days=_CATCHUP_LOOKBACK_END)
        d = catchup_start
        while d <= catchup_end:
            if calls >= _CALL_CAP:
                cap_hit = True
                break
            day_targets = build_sweep_targets(db, d)
            if day_targets:
                existing = _existing_entity_ids(db, d)
                missing = [t for t in day_targets if t["entity_id"] not in existing]
                for t in missing:
                    if calls >= _CALL_CAP:
                        cap_hit = True
                        break
                    calls += 1
                    catchup_calls += 1
                    try:
                        hh = fetch(t["entity_id"], d)
                    except Exception as e:
                        log.warning("keyword_hourly_sweep 캐치업 유닛 실패 skip: entity=%s date=%s: %s",
                                    t["entity_id"], d, e)
                        failed += 1
                        continue
                    _replace_rows(db, d, t, hh)
                    rows_written += len(hh)
            d += timedelta(days=1)

    # 365일 롤링 삭제
    cutoff = today - timedelta(days=_RETAIN_DAYS)
    db.execute(delete(NaverKeywordHourly).where(NaverKeywordHourly.ad_date < cutoff))
    db.commit()

    result = {
        "date": sweep_date.isoformat(), "targets": len(targets), "calls": calls,
        "rows": rows_written, "failed": failed, "catchup_calls": catchup_calls,
    }
    log.info("naver_keyword_hourly sweep: %s%s", result, " (콜 상한 도달)" if cap_hit else "")
    return result
