# search_term_dim_ingest.py — 쇼핑 검색어 리포트의 «버리던 세 축» 적재 (D-NAO-198 ①)
#
# 역할: SHOPPINGKEYWORD_DETAIL은 07:40 크론이 이미 매일 받고 있다(추가 네이버 API 콜 0 —
#   리포트 생성·목록은 기존 경로와 공유하고 다운로드만 한 번 더 한다). 그 리포트의
#   col7(시간대)·col8(지역)·col9(매체)를 기존 파서가 매일 버려 왔는데(집계 grain이
#   일자×캠페인×그룹×검색어라 뭉개진다), 이 모듈이 축 grain으로 따로 적재한다.
#
# ★시한: 리포트 재생성 한도가 **정확히 180일**이다(day-180 BUILT ↔ day-181 400/10004로
#   경계 실측 확정). 매일 창이 굴러가 앞이 사라진다 — 안 받은 날은 영구 소실이다.
#
# ★두 표로 가른 이유는 models.py의 NaverSearchTermDimDaily docstring 참조
#   (결합 전건 586MB · 그중 98.2%가 노출 전용 · prod 디스크 92%).
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import NaverSearchTermDimCellDaily, NaverSearchTermDimDaily
from app.services.naver_sa_ad_fetcher import fetch_search_term_dimensions
from app.utils.kst import kst_now

log = logging.getLogger(__name__)


def ingest_search_term_dimensions(db: Session, date_from: date, date_to: date) -> dict:
    """축(시간대·지역·매체) 적재 — 리포트에 실재하는 날짜만 snapshot 교체.

    ★delete 대상은 «요청 범위»가 아니라 «리포트가 실제로 준 날짜»다. 범위 전체를 지우면
    리포트가 일시적으로 안 나온 날(생성 실패·폴링 timeout — ensure_reports_built는 그 날짜만
    건너뛰고 계속한다)의 기존 적재분이 **원본이 사라진 뒤에 지워진다**. 180일 한도가 있는
    자료라 그 삭제는 복구가 안 된다.

    멱등: 같은 범위를 다시 돌리면 같은 결과(그 날짜만 delete 후 재삽입).
    """
    payload = fetch_search_term_dimensions(date_from, date_to)
    marg_rows = payload["marginals"]
    cell_rows = payload["cells"]

    present = sorted({r["date"] for r in marg_rows})
    if not present:
        log.warning("검색어 축 적재: %s~%s 리포트 행 0 — 기존 적재분 보존하고 종료",
                    date_from, date_to)
        return {"dates": [], "marginal_rows": 0, "cell_rows": 0}

    present_dates = [date.fromisoformat(d) for d in present]
    db.execute(delete(NaverSearchTermDimDaily).where(
        NaverSearchTermDimDaily.ad_date.in_(present_dates)))
    db.execute(delete(NaverSearchTermDimCellDaily).where(
        NaverSearchTermDimCellDaily.ad_date.in_(present_dates)))

    now = kst_now()
    for r in marg_rows:
        db.add(NaverSearchTermDimDaily(
            ad_date=date.fromisoformat(r["date"]), campaign_id=r["campaign_id"],
            adgroup_id=r["adgroup_id"], dim_type=r["dim_type"], dim_value=r["dim_value"],
            imp=r["imp"], clk=r["clk"], cost=r["cost"], rank_sum=r["rank_sum"],
            synced_at=now,
        ))
    for r in cell_rows:
        db.add(NaverSearchTermDimCellDaily(
            ad_date=date.fromisoformat(r["date"]), campaign_id=r["campaign_id"],
            adgroup_id=r["adgroup_id"], hour_code=r["hour_code"],
            region_code=r["region_code"], media_code=r["media_code"],
            imp=r["imp"], clk=r["clk"], cost=r["cost"], rank_sum=r["rank_sum"],
            synced_at=now,
        ))
    db.commit()

    result = {"dates": present, "marginal_rows": len(marg_rows), "cell_rows": len(cell_rows)}
    log.info("naver_search_term_dim ingest: %s", result)
    return result
