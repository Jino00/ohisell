# meta_ad_fetcher.py — Meta 광고 캠페인 일별 spend 수집 SA
# 역할: Meta Marketing API → 캠페인별 일별 광고비 반환 (순수 함수)
from __future__ import annotations

import logging
import os
from datetime import date
from decimal import Decimal

log = logging.getLogger(__name__)

META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_AD_ACCOUNT_ID = os.getenv("META_AD_ACCOUNT_ID", "")

CAMPAIGN_FIELDS = ["campaign_id", "campaign_name", "spend", "date_start", "date_stop"]


def fetch_campaign_daily_spend(
    date_from: date,
    date_to: date,
) -> list[dict]:
    """Meta API에서 캠페인별 일별 spend를 가져온다.

    Returns:
        [{"campaign_id": str, "campaign_name": str, "date": str, "spend": Decimal}, ...]
    """
    if not META_ACCESS_TOKEN or not META_AD_ACCOUNT_ID:
        log.warning("Meta 자격증명 없음 — META_ACCESS_TOKEN / META_AD_ACCOUNT_ID 확인")
        return []

    try:
        from facebook_business.api import FacebookAdsApi
        from facebook_business.adobjects.adaccount import AdAccount

        FacebookAdsApi.init(access_token=META_ACCESS_TOKEN)
        account = AdAccount(META_AD_ACCOUNT_ID)

        params = {
            "level": "campaign",
            "fields": CAMPAIGN_FIELDS,
            "time_range": {
                "since": date_from.isoformat(),
                "until": date_to.isoformat(),
            },
            "time_increment": 1,
        }

        insights = account.get_insights(params=params)
        results = []
        for row in insights:
            data = row.export_all_data()
            spend = Decimal(str(data.get("spend", "0")))
            if spend == 0:
                continue
            results.append({
                "campaign_id": data.get("campaign_id", ""),
                "campaign_name": data.get("campaign_name", ""),
                "date": data.get("date_start", ""),
                "spend": spend,
            })
        log.info("Meta 캠페인 일별 spend %d건 수집 (%s ~ %s)", len(results), date_from, date_to)
        return results

    except Exception as e:
        log.error("Meta API 조회 실패: %s", e)
        return []


def extract_keyword(campaign_name: str, known_keywords: list[str]) -> str | None:
    """캠페인명에서 알려진 상품 키워드를 추출한다."""
    name_lower = campaign_name.lower()
    for kw in known_keywords:
        if kw.lower() in name_lower:
            return kw
    return None
