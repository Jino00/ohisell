# search_term_ingest.py — naver_search_term_daily_harness (검색어 단위 성과 수집→적재, P2-S1)
# 역할: SHOPPINGKEYWORD_DETAIL(자동 BUILT, 실측 docs/references/22)은 바로 GET 수집.
#   EXPKEYWORD(파워링크 확장검색어)는 자동 생성 안 됨 → 없는 날짜는 생성 요청만 하고
#   (폴링 없이) 다음 크론 실행에서 BUILT 상태를 다시 확인해 수집(비동기 자기치유 패턴,
#   기존 07:30 크론과 동일한 "확정치 나올 때 잡아채는" 흐름).
#   SS1(docs/PLAN_naver-ad-searchterm-ss.md §3): SHOPPINGKEYWORD_CONVERSION_DETAIL 전환을
#   source='shopping' 성과행에 병합(UPDATE) — 성과행 없는 간접전환 귀속 검색어는 0-성과
#   행으로 INSERT해 전환을 보존한다. 파워링크는 전환 귀속 불가(§0.5 확정)라 대상 아님.
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import NaverSearchTermDaily
from app.services.naver_sa_ad_fetcher import (
    _list_reports_by_type,
    create_expkeyword_report,
    fetch_search_term_conversion,
    fetch_search_term_daily,
    list_report_jobs,
)
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

_CONV_SOURCE = "shopping"  # 전환 귀속 가능한 유일 source(§0.5 — 파워링크 검색어='-' 확정)
_CONV_REPORT_TP = "SHOPPINGKEYWORD_CONVERSION_DETAIL"
# 전환 컬럼(성과행 위 병합/리셋 대상) — 성과 컬럼(imp/clk/cost/rank_sum)은 절대 건드리지 않는다.
_CONV_COLS = ("conv_purchase_cnt", "conv_direct_cnt", "conv_purchase_amt", "cart_cnt", "cart_amt")


def _conversion_report_dates(date_from: date, date_to: date) -> set[date]:
    """SHOPPINGKEYWORD_CONVERSION_DETAIL 보고서가 실재(BUILT)하는 날짜 집합(codex 2R[P2]).

    "보고서 있음 = 보고서가 진실(전환 0 포함), 보고서 없음 = 이전 값 보존"의 판정 기준.
    보고서에서 grain이 소멸(전환 0 소급)해도 conv_rows에는 그 날짜가 아예 안 잡힐 수 있으므로
    (특히 그 날짜의 모든 전환이 0이 되면 conv_rows에 날짜 자체가 없음), 캐리포워드가 옛 전환을
    되살려 SS2 게이트를 왜곡한다. 그래서 conv_rows 날짜가 아니라 보고서 실재 여부를 별도로
    조회한다. 조회 실패는 빈 집합 반환 = 전면 보존(fail-open, 절대 잘못 0으로 지우지 않음)."""
    try:
        return {
            date.fromisoformat(r["date"])
            for r in _list_reports_by_type(_CONV_REPORT_TP, date_from, date_to)
        }
    except Exception as e:  # noqa: BLE001 — 목록 조회 실패는 캐리포워드 전면 보존(잘못 0 리셋 금지)
        log.warning("%s 보고서 목록 조회 실패(캐리포워드 전면 보존): %s", _CONV_REPORT_TP, e)
        return set()


def _ingest_rows(db: Session, source: str, rows: list[dict]) -> int:
    dates = sorted({r["date"] for r in rows})
    if dates:
        db.execute(delete(NaverSearchTermDaily).where(
            NaverSearchTermDaily.source == source,
            NaverSearchTermDaily.ad_date.in_([date.fromisoformat(d) for d in dates]),
        ))
    now = kst_now()
    for r in rows:
        db.add(NaverSearchTermDaily(
            ad_date=date.fromisoformat(r["date"]), campaign_id=r["campaign_id"],
            adgroup_id=r["adgroup_id"], search_term=r["search_term"], source=source,
            imp=r["imp"], clk=r["clk"], cost=r["cost"], rank_sum=r["rank_sum"],
            synced_at=now,
        ))
    return len(rows)


def _snapshot_shopping_conversions(db: Session, dates: set[date]) -> dict[tuple, dict]:
    """delete+insert(초기화) 직전에 기존 shopping 성과행의 전환 컬럼을 grain 키로 스냅샷.

    _ingest_rows("shopping", ...)는 대상 날짜의 shopping 행을 통째로 delete+insert 하면서
    전환 컬럼을 default 0으로 초기화한다. 이 함수가 그 직전 값을 떠 두면, 뒤이은
    ingest_search_term_conversion이 보고서 []를 받아 아무것도 덮지 못하는 경우에도
    _apply_conversion_carryforward가 전환 기록을 되살릴 수 있다(fail-safe). 전환이 하나라도
    0이 아닌 grain만 보존한다(전부 0인 행은 되살릴 것이 없음)."""
    if not dates:
        return {}
    snapshot: dict[tuple, dict] = {}
    for row in db.query(NaverSearchTermDaily).filter(
        NaverSearchTermDaily.source == _CONV_SOURCE,
        NaverSearchTermDaily.ad_date.in_(dates),
    ).all():
        if (row.conv_purchase_cnt or row.conv_direct_cnt or row.conv_purchase_amt
                or row.cart_cnt or row.cart_amt):
            snapshot[(row.ad_date, row.campaign_id, row.adgroup_id, row.search_term)] = {
                "conv_purchase_cnt": row.conv_purchase_cnt,
                "conv_direct_cnt": row.conv_direct_cnt,
                "conv_purchase_amt": row.conv_purchase_amt,
                "cart_cnt": row.cart_cnt,
                "cart_amt": row.cart_amt,
            }
    return snapshot


def _apply_conversion_carryforward(
    db: Session, snapshot: dict[tuple, dict], report_dates: set[date] | None = None,
) -> int:
    """delete+insert로 초기화된 shopping 성과행에 스냅샷 전환을 재적용(fail-safe).

    스냅샷 grain이 재삽입된 성과행과 매칭되면 UPDATE, 없으면(성과 소멸·간접전환 전용 grain)
    0-성과 행으로 INSERT — ingest_search_term_conversion의 병합 규율과 동일하게 전환을 보존한다.
    이후 ingest_search_term_conversion이 신선한 보고서가 있으면 SET으로 덮어써 최신값을
    유지하므로(멱등 유지), 이 캐리포워드는 **보고서 일시 부재 시에만 실효**한다 —
    그 결과가 SS2 전환 보호 게이트(purchase 전환≥1 검색어 제외 금지)의 데이터 기반 보존이다.

    ★report_dates 스킵(codex 2R[P2]): 보고서가 실재(BUILT)하는 날짜는 보고서가 진실(전환 0
    소급 포함)이므로 캐리포워드를 적용하지 않는다. 그 날짜의 전환은 ingest_search_term_conversion
    이 0으로 리셋 후 보고서 rows로 재적용한다 — 캐리포워드가 옛 전환을 되살리면 보고서에서
    사라진(전환 0이 된) grain이 조용히 부활해 SS2 게이트가 왜곡된다. report_dates=None이면
    스킵 없음(하위호환 — 호출측이 항상 전달)."""
    if not snapshot:
        return 0
    skip = report_dates or set()
    db.flush()  # 직전 _ingest_rows(shopping) delete+insert를 조회에 반영(autoflush=False)
    dates = {k[0] for k in snapshot if k[0] not in skip}
    existing: dict[tuple, NaverSearchTermDaily] = {
        (row.ad_date, row.campaign_id, row.adgroup_id, row.search_term): row
        for row in db.query(NaverSearchTermDaily).filter(
            NaverSearchTermDaily.source == _CONV_SOURCE,
            NaverSearchTermDaily.ad_date.in_(dates),
        ).all()
    }
    now = kst_now()
    applied = 0
    for key, conv in snapshot.items():
        if key[0] in skip:  # 보고서 실재 날짜 = 보고서가 진실, 캐리포워드 스킵(위 dates 필터와 일관)
            continue
        row = existing.get(key)
        if row is None:
            ad_date, campaign_id, adgroup_id, term = key
            row = NaverSearchTermDaily(
                ad_date=ad_date, campaign_id=campaign_id, adgroup_id=adgroup_id,
                search_term=term, source=_CONV_SOURCE,
                imp=0, clk=0, cost=0, rank_sum=0, synced_at=now,
            )
            db.add(row)
            existing[key] = row
        row.conv_purchase_cnt = conv["conv_purchase_cnt"]
        row.conv_direct_cnt = conv["conv_direct_cnt"]
        row.conv_purchase_amt = conv["conv_purchase_amt"]
        row.cart_cnt = conv["cart_cnt"]
        row.cart_amt = conv["cart_amt"]
        applied += 1
    return applied


def request_missing_expkeyword_reports(date_from: date, date_to: date) -> list[str]:
    """범위 내 EXPKEYWORD 미생성 날짜를 찾아 생성 요청(POST). 반환: 요청한 날짜 목록.

    이미 REGIST/RUNNING/BUILT 상태인 날짜는 stat-reports 목록에 잡혀 재요청하지 않는다
    (BUILT만 반환하는 _list_reports_by_type 대신 전체 목록 조회로 중복생성 방지).
    """
    built_or_pending: set[str] = set()
    try:
        for rep in list_report_jobs("EXPKEYWORD"):
            stat_dt_raw = rep.get("statDt", "")
            if not stat_dt_raw:
                continue
            d_utc = date.fromisoformat(stat_dt_raw[:10])
            time_part = stat_dt_raw[11:16] if len(stat_dt_raw) > 10 else "00:00"
            d_kst = d_utc + timedelta(days=1) if time_part >= "15:00" else d_utc
            built_or_pending.add(d_kst.isoformat())
    except Exception as e:
        log.warning("EXPKEYWORD 기존 목록 조회 실패(계속): %s", e)

    requested = []
    cur = date_from
    while cur <= date_to:
        iso = cur.isoformat()
        if iso not in built_or_pending:
            try:
                create_expkeyword_report(cur)
                requested.append(iso)
            except Exception as e:
                log.warning("EXPKEYWORD 생성 요청 실패 %s: %s", iso, e)
        cur += timedelta(days=1)
    if requested:
        log.info("EXPKEYWORD 리포트 생성 요청: %s (다음 크론에서 BUILT 확인 후 수집)", requested)
    return requested


def ingest_search_term_conversion(
    db: Session, date_from: date, date_to: date, report_dates: set[date] | None = None,
) -> dict:
    """SHOPPINGKEYWORD_CONVERSION_DETAIL 전환을 source='shopping' 성과행에 병합.

    grain(ad_date, campaign_id, adgroup_id, search_term)이 uq_naver_search_term_daily와
    일치하는 기존 성과행이 있으면 전환 컬럼만 UPDATE(SET, 누적 가산 아님 — 같은 날짜
    재수집 시 최신값으로 다시 세팅해 멱등). 매칭되는 성과행이 없으면(간접전환 귀속 등)
    0-성과 행을 INSERT해 전환을 보존한다(report_collector.collect_daily_rows의
    "전환만 있고 성과행 없는 키" 관례와 동일).

    호출측(ingest_search_term_daily)이 shopping 성과행을 delete+insert(snapshot 교체)한
    직후 같은 세션에서 호출해야 한다 — db.flush()로 그 직전 insert를 조회 가능하게 만든다.

    ★report_dates 0-리셋(codex 2R[P2]): 보고서가 실재(BUILT)하는 날짜의 shopping 전환 컬럼을
    보고서 rows 적용 **전에** 먼저 0으로 리셋한다("보고서 있음 = 보고서가 진실, 전환 0 포함").
    이래야 보고서에서 사라진(전환이 소급 0이 된) grain이 이전 값(캐리포워드/직전 수집분)을
    유지하지 못하고 정확히 0이 된다. 보고서에 남아있는 grain은 뒤이은 병합 SET으로 최신값을
    다시 얻는다. report_dates=None이면 conv_rows에 실재하는 날짜에서 파생(단독 호출 하위호환) —
    이 경우 "보고서 있음=BUILT이지만 rows 0"인 빈-보고서 날짜는 감지 못하므로, 크론 경로는
    ingest_search_term_daily가 _list_reports_by_type로 계산한 report_dates를 반드시 넘긴다.
    """
    conv_rows = fetch_search_term_conversion(date_from, date_to)

    db.flush()  # 직전 _ingest_rows(shopping) delete+insert를 조회에 반영

    # 보고서 실재 날짜: 인자로 받으면 그것(빈-보고서 날짜 포함 권위), 없으면 conv_rows 날짜에서 파생.
    reset_dates = report_dates if report_dates is not None else {
        date.fromisoformat(r["date"]) for r in conv_rows
    }
    if reset_dates:
        # 성과 컬럼(imp/clk/cost/rank_sum)은 건드리지 않고 전환 컬럼만 0으로 리셋. synchronize_session
        # =False로 벌크 UPDATE — 리셋된 행이 보고서에 남아있으면 아래 병합 SET이 다시 채우고,
        # 사라진 grain은 이 0이 최종값으로 남는다(캐리포워드가 이 날짜를 스킵하므로 되살아나지 않음).
        db.query(NaverSearchTermDaily).filter(
            NaverSearchTermDaily.source == _CONV_SOURCE,
            NaverSearchTermDaily.ad_date.in_(reset_dates),
        ).update({col: 0 for col in _CONV_COLS}, synchronize_session=False)
        db.flush()  # 리셋을 아래 existing 재조회에 반영

    if not conv_rows:
        # 보고서가 BUILT이지만 rows 0인 경우에도 위 리셋(전환 0)은 이미 적용됐다 — noop 아님.
        return {"conv_rows": 0, "merged": 0, "inserted": 0}

    dates = {date.fromisoformat(r["date"]) for r in conv_rows}
    existing: dict[tuple, NaverSearchTermDaily] = {
        (row.ad_date, row.campaign_id, row.adgroup_id, row.search_term): row
        for row in db.query(NaverSearchTermDaily).filter(
            NaverSearchTermDaily.source == _CONV_SOURCE,
            NaverSearchTermDaily.ad_date.in_(dates),
        ).all()
    }

    now = kst_now()
    merged = 0
    inserted = 0
    for r in conv_rows:
        ad_date = date.fromisoformat(r["date"])
        key = (ad_date, r["campaign_id"], r["adgroup_id"], r["search_term"])
        row = existing.get(key)
        if row is not None:
            row.conv_purchase_cnt = r["conv_purchase_cnt"]
            row.conv_direct_cnt = r["conv_direct_cnt"]
            row.conv_purchase_amt = r["conv_purchase_amt"]
            row.cart_cnt = r["cart_cnt"]
            row.cart_amt = r["cart_amt"]
            merged += 1
        else:
            new_row = NaverSearchTermDaily(
                ad_date=ad_date, campaign_id=r["campaign_id"], adgroup_id=r["adgroup_id"],
                search_term=r["search_term"], source=_CONV_SOURCE,
                imp=0, clk=0, cost=0, rank_sum=0,
                conv_purchase_cnt=r["conv_purchase_cnt"], conv_direct_cnt=r["conv_direct_cnt"],
                conv_purchase_amt=r["conv_purchase_amt"], cart_cnt=r["cart_cnt"], cart_amt=r["cart_amt"],
                synced_at=now,
            )
            db.add(new_row)
            existing[key] = new_row
            inserted += 1

    log.info("naver_search_term_daily 전환 병합: %d행 중 병합=%d 신규=%d (%s~%s)",
              len(conv_rows), merged, inserted, date_from, date_to)
    return {"conv_rows": len(conv_rows), "merged": merged, "inserted": inserted}


def ingest_search_term_daily(db: Session, date_from: date, date_to: date) -> dict:
    """검색어 단위 성과를 두 소스(shopping/expkeyword) 각각 snapshot 교체 적재.

    shopping은 매번 최신 BUILT분을 그대로 수집. expkeyword는 BUILT분만 수집하고,
    범위 내 아직 생성 안 된 날짜는 request_missing_expkeyword_reports로 생성 요청만 남긴다.

    ★전환 보존 순서(fail-safe, SS2 전환 보호 게이트 데이터 보존):
      ⓪ _conversion_report_dates — 이 범위에서 CONVERSION 보고서가 실재(BUILT)하는 날짜 집합.
         "보고서 있음=보고서가 진실(0 포함), 보고서 없음=이전 값 보존"의 기준(codex 2R[P2]).
      ① _snapshot_shopping_conversions — shopping delete+insert **전에** 기존 전환을 뜬다.
      ② _ingest_rows("shopping") — 성과행을 통째로 교체(전환 컬럼은 default 0으로 초기화).
      ③ _apply_conversion_carryforward(report_dates) — 스냅샷 전환을 재삽입 행에 되살리되,
         **보고서 부재 날짜에만** 적용한다(보고서 실재 날짜는 스킵 — 보고서가 진실).
      ④ ingest_search_term_conversion(report_dates) — 보고서 실재 날짜의 전환을 0으로 리셋 후
         보고서 rows로 재적용(SET, 멱등). 보고서에서 사라진 grain은 0으로 확정된다.
    이 순서가 없으면 ②가 전환을 0으로 지운 직후 ④가 보고서 []를 받아 전환 기록이 사라지고,
    SS2의 "purchase 전환≥1 검색어 제외 금지" 게이트가 조용히 무력화된다(잘못된 제외 후보 진입).
    반대로 report_dates 구분이 없으면(구버전) 보고서에서 grain이 소멸(전환 0 소급)해도 ③이
    옛 전환을 되살려 SS2 게이트가 반대로 오작동한다 — ⓪이 두 방향을 모두 막는다.
    """
    report_dates = _conversion_report_dates(date_from, date_to)  # ⓪ 보고서 실재 날짜
    shopping_rows = fetch_search_term_daily("SHOPPINGKEYWORD_DETAIL", date_from, date_to)
    shopping_dates = {date.fromisoformat(r["date"]) for r in shopping_rows}
    conv_snapshot = _snapshot_shopping_conversions(db, shopping_dates)  # ① delete 전 스냅샷
    n_shopping = _ingest_rows(db, "shopping", shopping_rows)            # ② 성과행 교체
    n_carried = _apply_conversion_carryforward(db, conv_snapshot, report_dates)  # ③ 보고서 부재 날짜만

    exp_rows = fetch_search_term_daily("EXPKEYWORD", date_from, date_to)
    n_exp = _ingest_rows(db, "expkeyword", exp_rows)
    requested = request_missing_expkeyword_reports(date_from, date_to)

    conv_result = ingest_search_term_conversion(db, date_from, date_to, report_dates)  # ④ 리셋+병합

    db.commit()
    log.info("naver_search_term_daily ingest: shopping=%d행 expkeyword=%d행 (%s~%s), 신규요청=%d, "
             "전환캐리포워드=%d, 전환병합=%s",
              n_shopping, n_exp, date_from, date_to, len(requested), n_carried, conv_result)
    return {
        "shopping_rows": n_shopping, "expkeyword_rows": n_exp,
        "expkeyword_requested_dates": requested,
        "conversion_carryforward": n_carried, "conversion": conv_result,
    }
