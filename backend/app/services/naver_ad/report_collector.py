# report_collector.py — naver_ad report_collector_sa (단일 책임: AD 성과 + 전환 병합)
# 역할: 날짜 범위의 /stat-reports AD(성과) + AD_CONVERSION(직/간접 전환)을
#   (일자×캠페인×광고그룹×키워드) grain으로 병합해 naver_ad_daily 형태 dict 리스트 반환.
# 순수 SA: fetcher(HTTP)만 호출, DB 없음. 컬럼 근거 docs/references/21.
from __future__ import annotations

import logging
from datetime import date

from app.services.naver_sa_ad_fetcher import (
    fetch_ad_performance_daily,
    fetch_conversion_daily,
    get_campaign_types,
)

log = logging.getLogger(__name__)

_CONV_FIELDS = ("conv_direct_cnt", "conv_indirect_cnt", "conv_direct_amt", "conv_indirect_amt")
# D-NAO-58 CD1: 장바구니(add_to_cart) 전환 — 구매(conv_*)와 나란히 보존하되 매출엔 불섞.
_CART_FIELDS = ("cart_direct_cnt", "cart_indirect_cnt", "cart_direct_amt", "cart_indirect_amt")


def _key(r: dict) -> tuple:
    return (r["date"], r["campaign_id"], r["adgroup_id"], r["keyword_id"])


def collect_daily_rows(
    date_from: date,
    date_to: date,
    *,
    campaign_types: dict[str, str] | None = None,
    ad_rows: list[dict] | None = None,
    conv_rows: list[dict] | None = None,
) -> list[dict]:
    """AD 성과 + 전환을 병합해 naver_ad_daily 컬럼 형태 dict 리스트 반환.

    campaign_types/ad_rows/conv_rows는 테스트·재사용을 위한 optional 주입(원칙18-8).
    미주입 시 fetcher에서 직접 조회. 전환만 있고 성과행이 없는 키(간접전환 귀속)는
    0-성과 행을 생성해 전환을 보존한다.
    """
    if ad_rows is None:
        ad_rows = fetch_ad_performance_daily(date_from, date_to)
    if conv_rows is None:
        conv_rows = fetch_conversion_daily(date_from, date_to)
    if campaign_types is None:
        try:
            campaign_types = get_campaign_types()
        except Exception as e:  # 유형 조회 실패해도 성과/전환은 적재(campaign_type만 공란)
            log.warning("campaign types 조회 실패(계속): %s", e)
            campaign_types = {}

    merged: dict[tuple, dict] = {}
    for r in ad_rows:
        merged[_key(r)] = {
            "ad_date": r["date"],
            "campaign_id": r["campaign_id"],
            "campaign_type": campaign_types.get(r["campaign_id"], ""),
            "adgroup_id": r["adgroup_id"],
            "keyword_id": r["keyword_id"],
            "imp": r["imp"], "clk": r["clk"], "cost": r["cost"], "rank_sum": r["rank_sum"],
            "conv_direct_cnt": 0, "conv_indirect_cnt": 0,
            "conv_direct_amt": 0, "conv_indirect_amt": 0,
            "cart_direct_cnt": 0, "cart_indirect_cnt": 0,
            "cart_direct_amt": 0, "cart_indirect_amt": 0,
        }

    for r in conv_rows:
        k = _key(r)
        row = merged.get(k)
        if row is None:
            row = {
                "ad_date": r["date"],
                "campaign_id": r["campaign_id"],
                "campaign_type": campaign_types.get(r["campaign_id"], ""),
                "adgroup_id": r["adgroup_id"],
                "keyword_id": r["keyword_id"],
                "imp": 0, "clk": 0, "cost": 0, "rank_sum": 0,
                "conv_direct_cnt": 0, "conv_indirect_cnt": 0,
                "conv_direct_amt": 0, "conv_indirect_amt": 0,
                "cart_direct_cnt": 0, "cart_indirect_cnt": 0,
                "cart_direct_amt": 0, "cart_indirect_amt": 0,
            }
            merged[k] = row
        for f in _CONV_FIELDS:
            row[f] += r[f]
        for f in _CART_FIELDS:  # 구버전 conv_rows(cart_* 없음)도 안전(기본 0)
            row[f] += r.get(f, 0)

    log.info("naver_ad collect: %s~%s 병합 %d행", date_from, date_to, len(merged))
    return list(merged.values())
