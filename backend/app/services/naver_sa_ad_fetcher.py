# naver_sa_ad_fetcher.py — 네이버 검색광고(SA) /stat-reports API 일별 캠페인 spend 수집 SA
# 역할: /stat-reports 목록 조회 → AD 보고서 다운로드 → 캠페인별 일별 광고비 반환 (순수 함수)
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import time
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlparse, parse_qs

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.searchad.naver.com"
ACCESS_LICENSE = os.getenv("NAVER_SA_ACCESS_LICENSE", "")
SECRET_KEY_B64 = os.getenv("NAVER_SA_SECRET_KEY", "")
CUSTOMER_ID = os.getenv("NAVER_SA_CUSTOMER_ID", "1313769")

# AD 보고서 컬럼 인덱스 (헤더 없음, TSV)
COL_DATE = 0
COL_CAMPAIGN_ID = 2
COL_COST = 11

# AD_CONVERSION 보고서 컬럼 인덱스 (13컬럼, 헤더 없음, TSV)
# 0:일자 9:직접/간접 10:전환액션(purchase/add_to_cart) 11:전환수 12:전환매출액
CONV_COL_DATE = 0
CONV_COL_ACTION = 10
CONV_COL_VALUE = 12
CONV_PURCHASE_ACTION = "purchase"  # 실구매만 매출로 집계(장바구니 등 제외)


def _headers(path: str) -> dict:
    ts = str(int(time.time() * 1000))
    secret = SECRET_KEY_B64.encode("utf-8")
    sig = base64.b64encode(
        hmac.new(secret, f"{ts}.GET.{path}".encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "X-Timestamp": ts,
        "X-API-KEY": ACCESS_LICENSE,
        "X-Signature": sig,
        "X-Customer": str(CUSTOMER_ID),
    }


def _get(path: str, params: dict | None = None) -> requests.Response:
    return requests.get(BASE_URL + path, headers=_headers(path), params=params, timeout=30)


def get_campaigns() -> dict[str, str]:
    """캠페인 ID → 캠페인명 매핑 반환."""
    resp = _get("/ncc/campaigns")
    resp.raise_for_status()
    return {c["nccCampaignId"]: c.get("name", "") for c in resp.json()}


def list_ad_reports(date_from: date, date_to: date) -> list[dict]:
    """/stat-reports에서 AD 타입 보고서 목록을 날짜 범위로 필터링해 반환."""
    resp = _get("/stat-reports")
    resp.raise_for_status()
    all_reports = resp.json()

    result = []
    for rep in all_reports:
        if rep.get("reportTp") != "AD" or rep.get("status") != "BUILT":
            continue
        # statDt는 UTC: "2026-06-03T15:00:00Z" = KST 2026-06-04 00:00
        # T15:00 이상이면 KST 날짜는 UTC날짜 +1일
        stat_dt_raw = rep.get("statDt", "")
        stat_dt_utc = stat_dt_raw[:10]
        try:
            d_utc = date.fromisoformat(stat_dt_utc)
        except ValueError:
            continue
        time_part = stat_dt_raw[11:16] if len(stat_dt_raw) > 10 else "00:00"
        d_kst = d_utc + timedelta(days=1) if time_part >= "15:00" else d_utc
        if date_from <= d_kst <= date_to:
            result.append({"date": d_kst.isoformat(), "downloadUrl": rep["downloadUrl"], "reportJobId": rep["reportJobId"]})

    return result


def download_report(download_url: str) -> list[tuple[str, str, int]]:
    """AD 보고서를 다운로드하여 (date, campaign_id, cost) 튜플 목록 반환."""
    parsed = urlparse(download_url)
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

    resp = _get("/report-download", params=params)
    resp.raise_for_status()

    rows = []
    content = resp.content.decode("utf-8-sig", errors="replace")
    for line in content.strip().split("\n"):
        cols = line.split("\t")
        if len(cols) <= COL_COST:
            continue
        try:
            raw_date = cols[COL_DATE]  # YYYYMMDD
            date_str = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
            campaign_id = cols[COL_CAMPAIGN_ID]
            cost = int(cols[COL_COST])
            if cost > 0:
                rows.append((date_str, campaign_id, cost))
        except (ValueError, IndexError):
            continue
    return rows


def fetch_campaign_daily_spend(date_from: date, date_to: date) -> list[dict]:
    """날짜 범위 내 캠페인별 일별 광고비를 반환.

    Returns:
        [{"campaign_id": str, "campaign_name": str, "date": str, "spend": Decimal}, ...]
    """
    if not ACCESS_LICENSE or not SECRET_KEY_B64:
        log.warning("Naver SA 자격증명 없음 — NAVER_SA_ACCESS_LICENSE / NAVER_SA_SECRET_KEY 확인")
        return []

    try:
        campaign_map = get_campaigns()
        reports = list_ad_reports(date_from, date_to)

        if not reports:
            log.warning("Naver SA: %s~%s 기간 AD 보고서 없음", date_from, date_to)
            return []

        log.info("Naver SA: %d개 일별 보고서 발견 (%s~%s)", len(reports), date_from, date_to)

        # 날짜별 캠페인 비용 집계 (같은 날 중복 보고서 방지)
        seen_dates: set[str] = set()
        results: list[dict] = []

        for rep in sorted(reports, key=lambda r: r["date"]):
            d = rep["date"]
            if d in seen_dates:
                continue
            seen_dates.add(d)

            rows = download_report(rep["downloadUrl"])
            for date_str, campaign_id, cost in rows:
                results.append({
                    "campaign_id": campaign_id,
                    "campaign_name": campaign_map.get(campaign_id, campaign_id),
                    "date": date_str,
                    "spend": Decimal(cost),
                })

        log.info("Naver SA 광고비 %d건 수집 (%s~%s)", len(results), date_from, date_to)
        return results

    except Exception as e:
        log.error("Naver SA API 조회 실패: %s", e)
        raise


def _list_reports_by_type(report_tp: str, date_from: date, date_to: date) -> list[dict]:
    """/stat-reports에서 지정 타입(BUILT) 보고서를 날짜 범위로 필터링해 반환."""
    resp = _get("/stat-reports")
    resp.raise_for_status()
    result = []
    for rep in resp.json():
        if rep.get("reportTp") != report_tp or rep.get("status") != "BUILT":
            continue
        stat_dt_raw = rep.get("statDt", "")
        stat_dt_utc = stat_dt_raw[:10]
        try:
            d_utc = date.fromisoformat(stat_dt_utc)
        except ValueError:
            continue
        time_part = stat_dt_raw[11:16] if len(stat_dt_raw) > 10 else "00:00"
        d_kst = d_utc + timedelta(days=1) if time_part >= "15:00" else d_utc
        if date_from <= d_kst <= date_to:
            result.append({"date": d_kst.isoformat(), "downloadUrl": rep["downloadUrl"]})
    return result


def fetch_daily_conversion_revenue(date_from: date, date_to: date) -> dict[str, Decimal]:
    """AD_CONVERSION 보고서에서 일별 '구매' 전환매출(직접+간접 합산)을 반환.

    Returns: {"YYYY-MM-DD": Decimal(전환매출액), ...}
    장바구니 등 비구매 액션은 제외(CONV_PURCHASE_ACTION). 전환추적 미설정 계정은 빈 dict.
    """
    if not ACCESS_LICENSE or not SECRET_KEY_B64:
        log.warning("Naver SA 자격증명 없음 — 전환매출 수집 건너뜀")
        return {}

    try:
        reports = _list_reports_by_type("AD_CONVERSION", date_from, date_to)
        if not reports:
            log.warning("Naver SA: %s~%s AD_CONVERSION 보고서 없음", date_from, date_to)
            return {}

        seen_dates: set[str] = set()
        daily: dict[str, Decimal] = {}
        for rep in sorted(reports, key=lambda r: r["date"]):
            if rep["date"] in seen_dates:
                continue
            seen_dates.add(rep["date"])

            parsed = urlparse(rep["downloadUrl"])
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            resp = _get("/report-download", params=params)
            resp.raise_for_status()
            content = resp.content.decode("utf-8-sig", errors="replace")
            for line in content.strip().split("\n"):
                cols = line.split("\t")
                if len(cols) <= CONV_COL_VALUE:
                    continue
                if cols[CONV_COL_ACTION] != CONV_PURCHASE_ACTION:
                    continue
                try:
                    raw_date = cols[CONV_COL_DATE]
                    date_str = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
                    value = Decimal(cols[CONV_COL_VALUE] or "0")
                except (ValueError, IndexError):
                    continue
                daily[date_str] = daily.get(date_str, Decimal("0")) + value

        log.info("Naver SA 전환매출 %d일치 수집 (%s~%s)", len(daily), date_from, date_to)
        return daily

    except Exception as e:
        log.error("Naver SA 전환매출 조회 실패: %s", e)
        raise


def extract_keyword(campaign_name: str, known_keywords: list[str]) -> str | None:
    """캠페인명에서 알려진 상품 키워드를 추출한다."""
    name_lower = campaign_name.lower()
    for kw in known_keywords:
        if kw.lower() in name_lower:
            return kw
    return None
