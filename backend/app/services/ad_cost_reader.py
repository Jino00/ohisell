# ad_cost_reader.py — ohi-ad-intelligence ad_data.db 읽기 전용 조회
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import text

log = logging.getLogger(__name__)


def get_daily_ad_spend(ad_db, date_from: date, date_to: date) -> list[dict]:
    """일별 총 광고비 조회

    Returns: [{date, total_spend, total_revenue_14d}]
    """
    if ad_db is None:
        return []

    try:
        result = ad_db.execute(
            text("""
                SELECT date,
                       SUM(ad_spend) as total_spend,
                       SUM(total_revenue_14d) as total_revenue_14d
                FROM daily_ad_data
                WHERE date >= :date_from AND date <= :date_to
                GROUP BY date
                ORDER BY date
            """),
            {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
        )
        return [dict(row._mapping) for row in result]
    except Exception as e:
        log.error("ad_data.db 조회 에러: %s", e)
        return []


def get_ad_spend_by_option(
    ad_db, date_from: date, date_to: date, option_id: str | None = None
) -> list[dict]:
    """상품(option_id)별 광고비 집계

    Returns: [{option_id, product_name, total_spend, total_revenue_14d, impressions, clicks}]
    """
    if ad_db is None:
        return []

    try:
        sql = """
            SELECT option_id,
                   MAX(product_name) as product_name,
                   SUM(ad_spend) as total_spend,
                   SUM(total_revenue_14d) as total_revenue_14d,
                   SUM(impressions) as impressions,
                   SUM(clicks) as clicks
            FROM daily_ad_data
            WHERE date >= :date_from AND date <= :date_to
        """
        params: dict = {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()}

        if option_id:
            sql += " AND option_id = :option_id"
            params["option_id"] = option_id

        sql += " GROUP BY option_id ORDER BY total_spend DESC"

        result = ad_db.execute(text(sql), params)
        return [dict(row._mapping) for row in result]
    except Exception as e:
        log.error("ad_data.db 상품별 조회 에러: %s", e)
        return []
