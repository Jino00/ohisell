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

# ── 키워드/그룹 grain 상세 컬럼 인덱스 (recon 확정, docs/references/21) ──
# AD 보고서(14컬럼): 위 COL_* 재사용 + 아래. avg_rank = rank_sum / imp.
COL_ADGROUP_ID = 3
COL_KEYWORD_ID = 4  # nkw-... / "-"(쇼핑=그룹 단위)
COL_DEVICE = 8      # M/P
COL_IMP = 9
COL_CLK = 10
COL_RANK_SUM = 12
KEYWORD_NONE = "-"  # 쇼핑/브랜드 등 키워드 없음 sentinel → 저장 시 ""

# AD_CONVERSION 보고서(13컬럼) 상세 컬럼
CONV_COL_CAMPAIGN = 2
CONV_COL_ADGROUP = 3
CONV_COL_KEYWORD = 4
CONV_COL_DIRINDIR = 9  # "1"=직접 / "2"=간접
CONV_COL_CNT = 11

_RETRY_STATUS = {429, 500, 502, 503, 504}


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
    """SA API GET. rate limit(429)·일시 5xx는 지수 백오프 재시도(최대 3회).

    GET는 멱등이라 재시도 안전. 4xx(인증·잘못된 요청)는 즉시 반환(재시도 무의미).
    """
    last: requests.Response | None = None
    for attempt in range(3):
        resp = requests.get(BASE_URL + path, headers=_headers(path), params=params, timeout=30)
        if resp.status_code not in _RETRY_STATUS:
            return resp
        last = resp
        wait = 2 ** attempt  # 1s, 2s, 4s
        log.warning("Naver SA %s %d — %ds 후 재시도(%d/3)", path, resp.status_code, wait, attempt + 1)
        time.sleep(wait)
    return last  # type: ignore[return-value]


def _download_tsv(download_url: str) -> list[list[str]]:
    """report-download URL을 다운로드해 TSV를 컬럼 리스트의 리스트로 반환(공통 헬퍼)."""
    parsed = urlparse(download_url)
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    resp = _get("/report-download", params=params)
    resp.raise_for_status()
    content = resp.content.decode("utf-8-sig", errors="replace")
    return [line.split("\t") for line in content.strip().split("\n") if line]


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


def get_campaign_types() -> dict[str, str]:
    """캠페인 ID → campaignTp(WEB_SITE/SHOPPING/BRAND_SEARCH) 매핑 반환."""
    resp = _get("/ncc/campaigns")
    resp.raise_for_status()
    return {c["nccCampaignId"]: c.get("campaignTp", "") for c in resp.json()}


def get_campaigns_full() -> list[dict]:
    """캠페인 전체 정보 반환: [{campaign_id, name, campaign_type, daily_budget}]."""
    resp = _get("/ncc/campaigns")
    resp.raise_for_status()
    return [{
        "campaign_id": c["nccCampaignId"],
        "name": c.get("name", ""),
        "campaign_type": c.get("campaignTp", ""),
        "daily_budget": c.get("dailyBudget"),
    } for c in resp.json()]


_STATS_FIELDS = '["impCnt","clkCnt","salesAmt","ccnt","convAmt"]'


def fetch_campaign_stats(
    campaign_ids: list[str],
    *,
    date_preset: str = "today",
    time_range: str | None = None,
) -> list[dict]:
    """캠페인별 /stats 집계(당일 누적 등) — 명명 필드(id 단수 반복 호출).

    date_preset="today"면 당일 누적(빠른 루프용). time_range 주면 그 범위. /stats는 id 단수만
    데이터 반환(ids 복수는 빈 응답, 실측). salesAmt=비용(원, AD리포트 cost와 정합 실증).

    Returns: [{"campaign_id","imp","clk","cost","conv_cnt","conv_amt"}, ...] (데이터 있는 캠페인만).
    """
    if not ACCESS_LICENSE or not SECRET_KEY_B64:
        log.warning("Naver SA 자격증명 없음 — /stats 수집 건너뜀")
        return []
    out: list[dict] = []
    for cid in campaign_ids:
        params = {"id": cid, "fields": _STATS_FIELDS}
        if time_range:
            params["timeRange"] = time_range
        else:
            params["datePreset"] = date_preset
        resp = _get("/stats", params)
        if resp.status_code != 200:
            log.warning("Naver SA /stats %s %d", cid, resp.status_code)
            continue
        data = resp.json().get("data") or []
        if not data:
            continue
        d = data[0]
        out.append({
            "campaign_id": cid,
            "imp": _safe_int(d.get("impCnt", 0)),
            "clk": _safe_int(d.get("clkCnt", 0)),
            "cost": _safe_int(d.get("salesAmt", 0)),
            "conv_cnt": _safe_int(d.get("ccnt", 0)),
            "conv_amt": _safe_int(d.get("convAmt", 0)),
        })
    return out


def _safe_int(v) -> int:
    try:
        return int(v)
    except (ValueError, TypeError):
        return 0


def _row_date_iso(raw: str) -> str | None:
    """YYYYMMDD → YYYY-MM-DD. 실패 시 None."""
    if len(raw) != 8 or not raw.isdigit():
        return None
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"


def fetch_ad_performance_daily(date_from: date, date_to: date) -> list[dict]:
    """AD 보고서에서 (일자×캠페인×광고그룹×키워드) grain 성과를 집계 반환.

    소재(ad)·기기(M/P)는 롤업 합산. keyword_id="-"(쇼핑/브랜드)는 "" sentinel로 정규화
    → 쇼핑은 그룹 단위로 집계됨. avg_rank는 rank_sum/imp로 소비측에서 계산.
    컬럼 실측 근거: docs/references/21. 순수 함수(HTTP만, DB 없음).

    Returns:
        [{"date","campaign_id","adgroup_id","keyword_id","imp","clk","cost","rank_sum"}, ...]
    """
    if not ACCESS_LICENSE or not SECRET_KEY_B64:
        log.warning("Naver SA 자격증명 없음 — AD 성과 수집 건너뜀")
        return []

    reports = list_ad_reports(date_from, date_to)
    if not reports:
        log.warning("Naver SA: %s~%s AD 보고서 없음", date_from, date_to)
        return []

    agg: dict[tuple, dict] = {}
    seen_dates: set[str] = set()
    for rep in sorted(reports, key=lambda r: r["date"]):
        if rep["date"] in seen_dates:
            continue
        seen_dates.add(rep["date"])
        for cols in _download_tsv(rep["downloadUrl"]):
            if len(cols) <= COL_RANK_SUM:
                continue
            d = _row_date_iso(cols[COL_DATE])
            if d is None:
                continue
            kw = cols[COL_KEYWORD_ID]
            keyword_id = "" if kw == KEYWORD_NONE else kw
            key = (d, cols[COL_CAMPAIGN_ID], cols[COL_ADGROUP_ID], keyword_id)
            row = agg.get(key)
            if row is None:
                row = {"date": d, "campaign_id": cols[COL_CAMPAIGN_ID],
                       "adgroup_id": cols[COL_ADGROUP_ID], "keyword_id": keyword_id,
                       "imp": 0, "clk": 0, "cost": 0, "rank_sum": 0}
                agg[key] = row
            row["imp"] += _safe_int(cols[COL_IMP])
            row["clk"] += _safe_int(cols[COL_CLK])
            row["cost"] += _safe_int(cols[COL_COST])
            row["rank_sum"] += _safe_int(cols[COL_RANK_SUM])

    log.info("Naver SA AD 성과 %d행 집계 (%s~%s)", len(agg), date_from, date_to)
    return list(agg.values())


def fetch_conversion_daily(date_from: date, date_to: date) -> list[dict]:
    """AD_CONVERSION 보고서에서 (일자×캠페인×광고그룹×키워드) grain 전환을 집계 반환.

    구매(purchase)만 집계(장바구니 제외). 직접(col9="1")/간접(col9="2") 분리.
    keyword_id="-"는 "" sentinel. avg_rank 조인 키는 fetch_ad_performance_daily와 동일.

    Returns:
        [{"date","campaign_id","adgroup_id","keyword_id",
          "conv_direct_cnt","conv_indirect_cnt","conv_direct_amt","conv_indirect_amt"}, ...]
    """
    if not ACCESS_LICENSE or not SECRET_KEY_B64:
        log.warning("Naver SA 자격증명 없음 — 전환 수집 건너뜀")
        return []

    reports = _list_reports_by_type("AD_CONVERSION", date_from, date_to)
    if not reports:
        log.warning("Naver SA: %s~%s AD_CONVERSION 보고서 없음", date_from, date_to)
        return []

    agg: dict[tuple, dict] = {}
    seen_dates: set[str] = set()
    for rep in sorted(reports, key=lambda r: r["date"]):
        if rep["date"] in seen_dates:
            continue
        seen_dates.add(rep["date"])
        for cols in _download_tsv(rep["downloadUrl"]):
            if len(cols) <= CONV_COL_VALUE:
                continue
            if cols[CONV_COL_ACTION] != CONV_PURCHASE_ACTION:
                continue
            d = _row_date_iso(cols[CONV_COL_DATE])
            if d is None:
                continue
            kw = cols[CONV_COL_KEYWORD]
            keyword_id = "" if kw == KEYWORD_NONE else kw
            key = (d, cols[CONV_COL_CAMPAIGN], cols[CONV_COL_ADGROUP], keyword_id)
            row = agg.get(key)
            if row is None:
                row = {"date": d, "campaign_id": cols[CONV_COL_CAMPAIGN],
                       "adgroup_id": cols[CONV_COL_ADGROUP], "keyword_id": keyword_id,
                       "conv_direct_cnt": 0, "conv_indirect_cnt": 0,
                       "conv_direct_amt": 0, "conv_indirect_amt": 0}
                agg[key] = row
            cnt = _safe_int(cols[CONV_COL_CNT])
            amt = _safe_int(cols[CONV_COL_VALUE])
            if cols[CONV_COL_DIRINDIR] == "2":  # 간접
                row["conv_indirect_cnt"] += cnt
                row["conv_indirect_amt"] += amt
            else:  # 직접("1") 및 기타
                row["conv_direct_cnt"] += cnt
                row["conv_direct_amt"] += amt

    log.info("Naver SA 전환 %d행 집계 (%s~%s)", len(agg), date_from, date_to)
    return list(agg.values())


def extract_keyword(campaign_name: str, known_keywords: list[str]) -> str | None:
    """캠페인명에서 알려진 상품 키워드를 추출한다."""
    name_lower = campaign_name.lower()
    for kw in known_keywords:
        if kw.lower() in name_lower:
            return kw
    return None
