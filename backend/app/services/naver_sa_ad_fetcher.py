# naver_sa_ad_fetcher.py — 네이버 검색광고(SA) /stat-reports API 일별 캠페인 spend 수집 SA
# 역할: /stat-reports 목록 조회 → AD 보고서 다운로드 → 캠페인별 일별 광고비 반환 (순수 함수)
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
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
CONV_PURCHASE_ACTION = "purchase"  # 실구매만 매출로 집계(conv_* → BEP/ROAS)
CONV_ADDTOCART_ACTION = "add_to_cart"  # 장바구니(D-NAO-58 CD1): cart_*로 별도 수집, 매출엔 불섞

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


def _headers(path: str, method: str = "GET") -> dict:
    """HMAC 서명 헤더. 서명 문자열은 실제 HTTP 메서드와 반드시 일치해야 함(실측: POST에
    'GET'으로 서명하면 signature invalid 403 — Naver는 시그니처의 메서드를 검증)."""
    ts = str(int(time.time() * 1000))
    secret = SECRET_KEY_B64.encode("utf-8")
    sig = base64.b64encode(
        hmac.new(secret, f"{ts}.{method}.{path}".encode(), hashlib.sha256).digest()
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


def _json_list(resp: requests.Response) -> list:
    """목록 응답 바디를 파싱해 리스트로 반환. 계정에 보고서가 0개면 /stat-reports가
    HTTP 200 + 빈 바디(0바이트)를 반환한다(실측, 2026-07-11 수집장애 근본원인) — 이 경우
    resp.json()이 JSONDecodeError로 크래시하므로, 빈/공백 바디는 크래시 대신 []로 처리한다."""
    text = resp.text
    if not text or not text.strip():
        return []
    return resp.json()


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
    all_reports = _json_list(resp)

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
        ensure_reports_built("AD", date_from, date_to)
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
    for rep in _json_list(resp):
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
        ensure_reports_built("AD_CONVERSION", date_from, date_to)
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
    """캠페인 전체 정보 반환: [{campaign_id, name, campaign_type, daily_budget, status}]."""
    resp = _get("/ncc/campaigns")
    resp.raise_for_status()
    return [{
        "campaign_id": c["nccCampaignId"],
        "name": c.get("name", ""),
        "campaign_type": c.get("campaignTp", ""),
        "daily_budget": c.get("dailyBudget"),
        "status": c.get("status", ""),
    } for c in resp.json()]


_STATS_FIELDS = '["impCnt","clkCnt","salesAmt","ccnt","convAmt","avgRnk"]'


def _parse_avg_rank(raw) -> float | None:
    """avgRnk 원본값 → float | None. avgRnk<=0은 무의미(순위는 1부터) → None(D-NAO-46②)."""
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def fetch_campaign_stats(
    campaign_ids: list[str],
    *,
    date_preset: str = "today",
    time_range: str | None = None,
) -> list[dict]:
    """캠페인별 /stats 집계(당일 누적 등) — 명명 필드(id 단수 반복 호출).

    date_preset="today"면 당일 누적(빠른 루프용). time_range 주면 그 범위. /stats는 id 단수만
    데이터 반환(ids 복수는 빈 응답, 실측). salesAmt=비용(원, AD리포트 cost와 정합 실증).

    Returns: [{"campaign_id","imp","clk","cost","conv_cnt","conv_amt","avg_rank"}, ...]
    (데이터 있는 캠페인만). avg_rank는 avgRnk<=0 또는 필드 부재 시 None(기존 키 의미 불변,
    avg_rank 키만 추가 — D-NAO-46②).
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
            "avg_rank": _parse_avg_rank(d.get("avgRnk")),
        })
    return out


_STATS_HH24_FIELDS = '["impCnt","clkCnt","salesAmt","ccnt","avgRnk"]'
_HOUR_LABEL_RE = re.compile(r"^(\d{1,2})시")


def fetch_entity_hh24(entity_id: str, stat_date: date) -> list[dict]:
    """키워드/애드그룹 1건의 단일일 시간대별(hh24) imp/clk/cost/conv_cnt/avgRnk 곡선
    (D-NAO-46②, conv_cnt는 D-NAO-60 RL1).

    GET /stats?id=<entity_id>&fields=[...]&timeRange={단일일}&timeIncrement=allDays&breakdown=hh24
    — 1콜로 그 날의 실적 있는 시간대만 반환(ref 32 §4). 과거 데이터는 네이버가 최근 7일만
    보존(초과 시 400).

    ★회계 불변(CD1 계승): conv_cnt는 시간당 전환 "건수"만 — convAmt(금액)는 일별 필드로만
    존재해 hh24에서는 못 받는다. 매출/BEP/ROAS 등 회계 집계에 절대 합산하지 않는다.

    Returns: [{"hour","imp","clk","cost","conv_cnt","avg_rank"}, ...] (실적 있는 시간대만, 없으면 []).

    ⚠️ breakdown은 조건이 안 맞으면 무언 무시(200 + 집계 1행)라 무언 데이터손실을 방어한다:
    응답 data가 있고 그 날 imp>0인데 breakdowns 키가 없거나 비어 있으면 예외를 던진다
    (호출측 sweep_keyword_hourly가 유닛 단위로 잡아 실패로 카운트).
    라벨("00시~01시"/"0시~1시")을 정규식으로 못 읽는 엔트리는 skip+warn(그 시간대만 유실).
    """
    if not ACCESS_LICENSE or not SECRET_KEY_B64:
        log.warning("Naver SA 자격증명 없음 — hh24 수집 건너뜀")
        return []

    d_iso = stat_date.isoformat()
    params = {
        "id": entity_id,
        "fields": _STATS_HH24_FIELDS,
        "timeRange": f'{{"since":"{d_iso}","until":"{d_iso}"}}',
        "timeIncrement": "allDays",
        "breakdown": "hh24",
    }
    resp = _get("/stats", params)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Naver SA hh24 {entity_id} {d_iso}: HTTP {resp.status_code} {resp.text[:200]}"
        )
    data = resp.json().get("data") or []
    if not data:
        return []

    d = data[0]
    top_imp = _safe_int(d.get("impCnt", 0))
    breakdowns = d.get("breakdowns")
    if top_imp > 0 and not breakdowns:
        raise RuntimeError(
            f"Naver SA hh24 {entity_id} {d_iso}: imp={top_imp}인데 breakdowns 없음(무언 무시 방어)"
        )

    out: list[dict] = []
    for b in breakdowns or []:
        name = b.get("name", "") or ""
        m = _HOUR_LABEL_RE.match(name)
        if not m:
            log.warning("Naver SA hh24 라벨 파싱 실패 skip: entity=%s name=%r", entity_id, name)
            continue
        out.append({
            "hour": int(m.group(1)),
            "imp": _safe_int(b.get("impCnt", 0)),
            "clk": _safe_int(b.get("clkCnt", 0)),
            "cost": _safe_int(b.get("salesAmt", 0)),
            "conv_cnt": _safe_int(b.get("ccnt", 0)),
            "avg_rank": _parse_avg_rank(b.get("avgRnk")),
        })
    return out


def _safe_int(v) -> int:
    try:
        return int(v)
    except (ValueError, TypeError):
        # 과거 날짜 백필 보고서는 cost 등을 float 표기('9670.0')로 내려준다(2026-07-21 실측
        # — int() 단독이면 ValueError→0으로 조용히 소실돼 cost=0 오적재). float 경유 재시도.
        try:
            return int(float(v))
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

    ensure_reports_built("AD", date_from, date_to)
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

    구매(purchase)→conv_* 와 장바구니(add_to_cart)→cart_* 를 **분리** 집계한다(D-NAO-58 CD1).
    그 외 전환 액션은 무시. 직접(col9="1")/간접(col9="2")은 양쪽 모두 분리 유지.
    ★매출/ROAS/BEP 회계는 여전히 구매(conv_*)만 — cart_*는 선행지표(장바구니→구매 전환율)
    산출 재료일 뿐 어떤 매출 합계에도 섞이지 않는다.
    keyword_id="-"는 "" sentinel. avg_rank 조인 키는 fetch_ad_performance_daily와 동일.

    Returns:
        [{"date","campaign_id","adgroup_id","keyword_id",
          "conv_direct_cnt","conv_indirect_cnt","conv_direct_amt","conv_indirect_amt",
          "cart_direct_cnt","cart_indirect_cnt","cart_direct_amt","cart_indirect_amt"}, ...]
    """
    if not ACCESS_LICENSE or not SECRET_KEY_B64:
        log.warning("Naver SA 자격증명 없음 — 전환 수집 건너뜀")
        return []

    ensure_reports_built("AD_CONVERSION", date_from, date_to)
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
            action = cols[CONV_COL_ACTION]
            if action == CONV_PURCHASE_ACTION:
                prefix = "conv"
            elif action == CONV_ADDTOCART_ACTION:
                prefix = "cart"
            else:  # 그 외 전환 액션(회원가입 등)은 수집 대상 아님
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
                       "conv_direct_amt": 0, "conv_indirect_amt": 0,
                       "cart_direct_cnt": 0, "cart_indirect_cnt": 0,
                       "cart_direct_amt": 0, "cart_indirect_amt": 0}
                agg[key] = row
            cnt = _safe_int(cols[CONV_COL_CNT])
            amt = _safe_int(cols[CONV_COL_VALUE])
            if cols[CONV_COL_DIRINDIR] == "2":  # 간접
                row[f"{prefix}_indirect_cnt"] += cnt
                row[f"{prefix}_indirect_amt"] += amt
            else:  # 직접("1") 및 기타
                row[f"{prefix}_direct_cnt"] += cnt
                row[f"{prefix}_direct_amt"] += amt

    log.info("Naver SA 전환 %d행 집계 (%s~%s)", len(agg), date_from, date_to)
    return list(agg.values())


def get_adgroups(campaign_id: str) -> list[dict]:
    """캠페인의 광고그룹 목록 (P2-S1 entity_sync). 실측(docs/references/22): 필드 확정.

    Returns: [{"adgroup_id","campaign_id","name","status","user_lock","bid_amt"}, ...]
    """
    resp = _get("/ncc/adgroups", {"nccCampaignId": campaign_id})
    resp.raise_for_status()
    return [{
        "adgroup_id": a["nccAdgroupId"],
        "campaign_id": a["nccCampaignId"],
        "name": a.get("name", ""),
        "status": a.get("status", ""),
        "user_lock": bool(a.get("userLock", False)),
        "bid_amt": a.get("bidAmt"),
    } for a in resp.json()]


def get_keywords(adgroup_id: str) -> list[dict]:
    """광고그룹의 키워드 목록 (P2-S1 entity_sync, WEB_SITE만 호출 — SHOPPING은 개별 키워드 무의미,
    실측: 계정 전체 등록 키워드 90,379건 중 90,150건이 WEB_SITE 소속, docs/references/22).

    Returns: [{"keyword_id","adgroup_id","keyword","status","user_lock","bid_amt","qi_grade"}, ...]
    qi_grade(1~7, 품질지수)는 /ncc/keywords 응답 nccQi.qiGrade — entity_sync 기존 일일
    열거에 무상 편승(추가 콜 0, D-NAO-46②, ref 32 §5).
    """
    resp = _get("/ncc/keywords", {"nccAdgroupId": adgroup_id})
    resp.raise_for_status()
    return [{
        "keyword_id": k["nccKeywordId"],
        "adgroup_id": k["nccAdgroupId"],
        "keyword": k.get("keyword", ""),
        "status": k.get("status", ""),
        "user_lock": bool(k.get("userLock", False)),
        "bid_amt": k.get("bidAmt"),
        "qi_grade": (k.get("nccQi") or {}).get("qiGrade"),
    } for k in resp.json()]


def _parse_ad_attr(raw: object) -> tuple[int | None, bool | None]:
    """소재 Ad.adAttr에서 (bidAmt, useGroupBidAmt) 추출 (B1, D-NAO-65).

    adAttr는 공식 apidoc상 JSON **문자열** `{"bidAmt":N,"useGroupBidAmt":bool}`이지만,
    이미 dict로 온 경우/문자열이 깨진 경우/필드 부재를 모두 방어한다(부분응답 견고, 추정 금지):
      - 문자열 → json.loads 시도, 실패 시 (None, None)
      - dict 아님 / adAttr 부재 → (None, None)
      - bidAmt/useGroupBidAmt 각 필드는 독립적으로 없으면 None(부분 필드 견고)
    회귀 안전: 실패는 예외 없이 None으로 강등(소재 하나 파싱 실패가 sync 전체를 깨지 않음).
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return None, None
    if not isinstance(raw, dict):
        return None, None
    bid_raw = raw.get("bidAmt")
    try:
        bid = int(bid_raw) if bid_raw is not None and not isinstance(bid_raw, bool) else None
    except (ValueError, TypeError):
        bid = None
    ugba_raw = raw.get("useGroupBidAmt")
    ugba = bool(ugba_raw) if isinstance(ugba_raw, bool) else None
    return bid, ugba


def get_ads(adgroup_id: str) -> list[dict]:
    """광고그룹의 소재(ad) 목록 → 쇼핑 상품 매핑 원료 (D-NAO-57 A) + 소재-레벨 입찰(B1).

    GET /ncc/ads?nccAdgroupId=... . 쇼핑 상품 소재(type=SHOPPING_PRODUCT_AD)는
    referenceData.mallProductId(예: "13365319468")를 담는데, 이 값이
    naver_product_bep.channel_product_id와 정확히 일치한다(라이브 실증, 트랙 D-NAO-57 A).

    반환: [{"ad_id","adgroup_id","ad_type","mall_product_id","product_name",
            "ad_bid_amt","use_group_bid_amt","ad_user_lock"}, ...]
    (mall_product_id가 있는 소재만 — 쇼핑 상품 소재가 아니거나 referenceData에 상품번호가
    없으면 제외). product_name은 referenceData에서 best-effort로 추출(표시용, 매핑 정확성엔
    무관 — 확정 필드가 아니라 여러 후보 키를 시도하고 없으면 빈 문자열).

    ★B1(D-NAO-65): adAttr(bidAmt·useGroupBidAmt)·userLock 추가 파싱(추가 API 콜 0 — 기존 응답
    재사용). adAttr 부재/파싱 실패 시 입찰 필드 None(read-only 인식층, 행위변경 없음).
    """
    resp = _get("/ncc/ads", {"nccAdgroupId": adgroup_id})
    resp.raise_for_status()
    out: list[dict] = []
    for a in resp.json():
        ref = a.get("referenceData") or {}
        mall_pid = ref.get("mallProductId")
        if not mall_pid:
            continue  # 쇼핑 상품 소재가 아니거나 상품번호 부재 → 매핑 대상 아님
        # product_name: referenceData의 확정 키가 실측 전이라 후보 키를 순서대로 시도(추정
        # 하드코딩 아님 — 첫 존재값 채택, 전부 없으면 ""). 매핑 정확성은 mall_product_id로만 판단.
        name = ref.get("productTitle") or ref.get("productName") or ref.get("name") or ""
        ad_bid_amt, use_group_bid_amt = _parse_ad_attr(a.get("adAttr"))
        out.append({
            "ad_id": a.get("nccAdId", ""),
            "adgroup_id": a.get("nccAdgroupId", adgroup_id),
            "ad_type": a.get("type", ""),
            "mall_product_id": str(mall_pid),
            "product_name": name,
            "ad_bid_amt": ad_bid_amt,
            "use_group_bid_amt": use_group_bid_amt,
            "ad_user_lock": bool(a.get("userLock", False)),
        })
    return out


def create_stat_report(report_tp: str, stat_date: date) -> dict:
    """지정 타입의 stat-report 생성 요청(자체생성, 2026-07-11 수집장애 근본원인 수정).

    POST /stat-reports {"reportTp": report_tp, "statDt": YYYYMMDD}. 즉시 BUILT 아님(REGIST/RUNNING
    → 폴링 필요, ensure_reports_built 참조). 실측(docs/PLAN_naver-report-self-create.md §0):
    AD·AD_CONVERSION·SHOPPINGKEYWORD_DETAIL·EXPKEYWORD 전부 이 방식으로 자체생성됨 —
    수집이 외부(MOP/네이버 UI 정기보고서)가 생성해준 보고서에 기생하던 것을 자족 구조로 전환.
    반환: {"reportJobId","status", ...}(생성 응답 원본).
    """
    resp = requests.post(
        BASE_URL + "/stat-reports",
        headers=_headers("/stat-reports", method="POST"),
        json={"reportTp": report_tp, "statDt": stat_date.strftime("%Y%m%d")},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def create_expkeyword_report(stat_date: date) -> dict:
    """EXPKEYWORD(파워링크 확장검색어) 리포트 생성 요청 — create_stat_report 하위호환 래퍼."""
    return create_stat_report("EXPKEYWORD", stat_date)


def get_report_job(report_job_id: int | str) -> dict:
    """생성한 리포트 잡 상태 조회 (폴링용). status: REGIST/RUNNING/BUILT/NONE/ERROR."""
    resp = _get(f"/stat-reports/{report_job_id}")
    resp.raise_for_status()
    return resp.json()


def list_report_jobs(report_tp: str) -> list[dict]:
    """/stat-reports 전체 목록 중 지정 타입만 반환 (상태 무관 — 중복생성 방지용 조회)."""
    resp = _get("/stat-reports")
    resp.raise_for_status()
    return [rep for rep in _json_list(resp) if rep.get("reportTp") == report_tp]


def _stat_dt_to_kst_date(stat_dt_raw: str) -> date | None:
    """statDt(UTC ISO 문자열)를 KST 날짜로 변환. 파싱 실패 시 None.
    list_ad_reports/_list_reports_by_type과 동일 규칙(UTC→KST, T15:00 이상이면 +1일)."""
    stat_dt_utc = stat_dt_raw[:10]
    try:
        d_utc = date.fromisoformat(stat_dt_utc)
    except ValueError:
        return None
    time_part = stat_dt_raw[11:16] if len(stat_dt_raw) > 10 else "00:00"
    return d_utc + timedelta(days=1) if time_part >= "15:00" else d_utc


def _report_status_by_kst_date(report_tp: str) -> dict[str, tuple[str, object]]:
    """report_tp의 기존 잡들을 KST 날짜별 (status, reportJobId)로 매핑.

    같은 날짜에 복수 잡이 있으면 BUILT를 최우선으로 채택(dedup 판정에 쓰임).
    """
    by_date: dict[str, tuple[str, object]] = {}
    for rep in list_report_jobs(report_tp):
        d = _stat_dt_to_kst_date(rep.get("statDt", ""))
        if d is None:
            continue
        iso = d.isoformat()
        status = rep.get("status")
        prev = by_date.get(iso)
        if prev is None or (status == "BUILT" and prev[0] != "BUILT"):
            by_date[iso] = (status, rep.get("reportJobId"))
    return by_date


def _poll_until_built(report_job_id, *, timeout: float, poll_interval: float) -> bool:
    """report_job_id가 BUILT 될 때까지 폴링. BUILT 도달 시 True, ERROR/NONE/timeout/조회실패는
    False(예외 전파 없음 — 호출측이 개별 skip 처리)."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            job = get_report_job(report_job_id)
        except Exception as e:
            log.warning("Naver SA 리포트 상태 조회 실패 jobId=%s: %s", report_job_id, e)
            return False
        status = job.get("status")
        if status == "BUILT":
            return True
        if status in ("ERROR", "NONE"):
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_interval)


def _ensure_one_report_built(
    report_tp: str, stat_date: date, existing: tuple[str, object] | None,
    *, timeout: float, poll_interval: float,
) -> None:
    """단일 날짜의 report_tp 보고서 BUILT를 보장(필요 시 생성+폴링). 실패는 log.warning만
    (예외 전파 없음 — ensure_reports_built가 나머지 날짜를 계속 진행할 수 있게)."""
    iso = stat_date.isoformat()
    if existing and existing[0] == "BUILT":
        return  # dedup: 이미 BUILT

    # REGIST/RUNNING(진행 중)만 재사용해 폴링. 터미널(ERROR/NONE)·그 외 상태는 낡은 잡을
    # 버리고 새로 생성한다 — codex[P2]: ERROR 잡 id를 재사용하면 _poll_until_built가 즉시
    # False→그 날짜가 낡은 잡 소멸까지 영구 skip돼 자체생성 복구가 무력화됨.
    job_id = existing[1] if (existing and existing[0] in ("REGIST", "RUNNING")) else None
    if job_id is None:
        try:
            created = create_stat_report(report_tp, stat_date)
        except Exception as e:
            log.warning("Naver SA %s %s 보고서 생성 실패(건너뜀): %s", report_tp, iso, e)
            return
        job_id = created.get("reportJobId")
        if job_id is None:
            log.warning("Naver SA %s %s 보고서 생성 응답에 reportJobId 없음(건너뜀)", report_tp, iso)
            return

    if not _poll_until_built(job_id, timeout=timeout, poll_interval=poll_interval):
        log.warning("Naver SA %s %s 보고서 BUILT 대기 실패(건너뜀, jobId=%s)", report_tp, iso, job_id)


def ensure_reports_built(
    report_tp: str, date_from: date, date_to: date,
    *, timeout: float = 60.0, poll_interval: float = 2.0,
) -> None:
    """report_tp의 stat-report가 date_from~date_to 각 날짜에 대해 BUILT 상태가 되도록 보장한다
    (자체생성 POST + BUILT까지 폴링). 부수효과만(생성·빌드 보장) — 반환 없음.

    - 자격증명 없으면 즉시 no-op(fetch_* 가드와 일관).
    - 이미 BUILT인 날짜는 skip(dedup). REGIST/RUNNING 중인 날짜는 기존 잡을 재사용해 폴링만.
    - 생성 실패·폴링 timeout/ERROR/NONE은 해당 날짜만 log.warning 후 skip, 나머지 날짜는 계속.
    """
    if not ACCESS_LICENSE or not SECRET_KEY_B64:
        return

    try:
        by_date = _report_status_by_kst_date(report_tp)
    except Exception as e:
        log.warning("Naver SA %s 기존 잡 목록 조회 실패(계속 진행): %s", report_tp, e)
        by_date = {}

    cur = date_from
    while cur <= date_to:
        _ensure_one_report_built(report_tp, cur, by_date.get(cur.isoformat()),
                                  timeout=timeout, poll_interval=poll_interval)
        cur += timedelta(days=1)


# SHOPPINGKEYWORD_DETAIL 보고서 컬럼(16열, 실측 확정 docs/references/22):
#   0일자 1고객ID 2캠페인 3그룹 4검색어(텍스트) 5소재 6비즈채널 7·8(미상,불필요)
#   9(품목/상품 식별자, 미상·불필요) 10기기(M/P) 11노출수 12클릭수 13비용 14순위합 15(미상)
ST_COL_DATE = 0
ST_COL_CAMPAIGN = 2
ST_COL_ADGROUP = 3
ST_COL_SEARCH_TERM = 4
ST_COL_IMP = 11
ST_COL_CLK = 12
ST_COL_COST = 13
ST_COL_RANK_SUM = 14

# EXPKEYWORD(파워링크 확장검색어) 보고서 컬럼(12열 — 실측 확정 docs/references/36 §0·§1.7.
# ★SHOPPINGKEYWORD_DETAIL(16열)과 레이아웃이 다르다 — 소재ID·비즈채널ID 컬럼이 아예 없는 더 짧은
# 구조. ST_COL_* 재사용 금지(2026-07-22까지 이 재사용 가정이 EXPKEYWORD 수집을 0행으로 전량
# 드롭시킨 근본원인, 길이가드 `len(cols) <= ST_COL_RANK_SUM(14)`가 12열 행을 전부 스킵했다).
# imp/clk/cost는 같은 날 AD 리포트 WEB_SITE 합계 대비 96.7%/97.3%/98.8% 일치로 col8/9/10 확정.
# rank_sum에 해당하는 컬럼은 이 12열 레이아웃에 없음(idx7·idx11 둘 다 어떤 지표와도 정합 안 됨,
# 미상) — EXPKEYWORD 행은 항상 rank_sum=0으로 적재:
#   0일자 1고객ID 2캠페인 3그룹 4검색어(텍스트) 5미상 6기기(M/P) 7미상 8노출수 9클릭수 10비용 11미상
EXP_COL_DATE = 0
EXP_COL_CAMPAIGN = 2
EXP_COL_ADGROUP = 3
EXP_COL_SEARCH_TERM = 4
EXP_COL_IMP = 8
EXP_COL_CLK = 9
EXP_COL_COST = 10


def fetch_search_term_daily(report_tp: str, date_from: date, date_to: date) -> list[dict]:
    """SHOPPINGKEYWORD_DETAIL 또는 EXPKEYWORD 보고서를 (일자×캠페인×그룹×검색어) grain으로 집계.

    SHOPPINGKEYWORD_DETAIL은 매일 자동 BUILT(16일 보관, GET만). EXPKEYWORD는 자동 생성 안 되므로
    사전에 create_expkeyword_report로 생성+대기 후 호출해야 함(이 함수는 이미 BUILT된 리포트만 조회).
    두 report_tp는 TSV 컬럼 레이아웃이 다르다(SHOPPINGKEYWORD_DETAIL=16열 vs EXPKEYWORD=12열) —
    report_tp별로 ST_COL_*/EXP_COL_* 상수셋을 분기해서 파싱한다(docs/references/36 §0, 2026-07-22
    수정 — 이전엔 EXPKEYWORD도 ST_COL_*를 재사용해 길이가드가 전 행을 스킵, 0행 적재됨).
    컬럼 실측: SHOPPINGKEYWORD_DETAIL=docs/references/22, EXPKEYWORD=docs/references/36.

    Returns: [{"date","campaign_id","adgroup_id","search_term","imp","clk","cost","rank_sum"}, ...]
    (EXPKEYWORD는 rank_sum 컬럼이 레이아웃에 없어 항상 0)
    """
    if not ACCESS_LICENSE or not SECRET_KEY_B64:
        log.warning("Naver SA 자격증명 없음 — 검색어 리포트 수집 건너뜀")
        return []

    is_exp = report_tp == "EXPKEYWORD"
    if is_exp:
        col_date, col_campaign, col_adgroup, col_term = (
            EXP_COL_DATE, EXP_COL_CAMPAIGN, EXP_COL_ADGROUP, EXP_COL_SEARCH_TERM)
        col_imp, col_clk, col_cost = EXP_COL_IMP, EXP_COL_CLK, EXP_COL_COST
        min_len_guard = EXP_COL_COST  # 12열 레이아웃 기준(len(cols) <= 10 → skip)
    else:
        col_date, col_campaign, col_adgroup, col_term = (
            ST_COL_DATE, ST_COL_CAMPAIGN, ST_COL_ADGROUP, ST_COL_SEARCH_TERM)
        col_imp, col_clk, col_cost = ST_COL_IMP, ST_COL_CLK, ST_COL_COST
        min_len_guard = ST_COL_RANK_SUM  # 16열 레이아웃 기준(len(cols) <= 14 → skip)

    ensure_reports_built(report_tp, date_from, date_to)
    reports = _list_reports_by_type(report_tp, date_from, date_to)
    if not reports:
        log.warning("Naver SA: %s~%s %s 보고서 없음", date_from, date_to, report_tp)
        return []

    agg: dict[tuple, dict] = {}
    seen_dates: set[str] = set()
    for rep in sorted(reports, key=lambda r: r["date"]):
        if rep["date"] in seen_dates:
            continue
        seen_dates.add(rep["date"])
        for cols in _download_tsv(rep["downloadUrl"]):
            if len(cols) <= min_len_guard:
                continue
            d = _row_date_iso(cols[col_date])
            if d is None:
                continue
            key = (d, cols[col_campaign], cols[col_adgroup], cols[col_term])
            row = agg.get(key)
            if row is None:
                row = {"date": d, "campaign_id": cols[col_campaign],
                       "adgroup_id": cols[col_adgroup], "search_term": cols[col_term],
                       "imp": 0, "clk": 0, "cost": 0, "rank_sum": 0}
                agg[key] = row
            row["imp"] += _safe_int(cols[col_imp])
            row["clk"] += _safe_int(cols[col_clk])
            row["cost"] += _safe_int(cols[col_cost])
            if not is_exp:
                row["rank_sum"] += _safe_int(cols[ST_COL_RANK_SUM])

    log.info("Naver SA %s 검색어 %d행 집계 (%s~%s)", report_tp, len(agg), date_from, date_to)
    return list(agg.values())


# ── SHOPPINGKEYWORD_CONVERSION_DETAIL 컬럼(15열, 헤더없음, 실측 확정
#    docs/PLAN_naver-ad-searchterm-ss.md §0.5) — AD_CONVERSION(CONV_COL_*, 13컬럼) 대비 +2
#    오프셋이지만 기기 컬럼이 끼어들어 단순 +2가 col9 이후는 깨진다(±2 오프셋 함정,
#    SS1 최대 실수 지점). 반드시 이 전용 상수만 쓴다 — CONV_COL_* 재사용 절대 금지.
STCONV_COL_DATE = 0
STCONV_COL_CAMPAIGN = 2
STCONV_COL_ADGROUP = 3
STCONV_COL_SEARCH_TERM = 4  # 검색어 텍스트(등록 키워드 아님)
STCONV_COL_DEVICE = 10      # M/P — 이 함수는 기기 롤업 합산(참고용, 미사용)
STCONV_COL_DIRINDIR = 11    # "1"=직접 / "2"=간접
STCONV_COL_ACTION = 12      # purchase / add_to_cart
STCONV_COL_CNT = 13
STCONV_COL_VALUE = 14


def fetch_search_term_conversion(date_from: date, date_to: date) -> list[dict]:
    """SHOPPINGKEYWORD_CONVERSION_DETAIL 보고서에서 (일자×캠페인×그룹×검색어) grain 전환을 집계.

    fetch_search_term_daily(위)와 동일 형태로 미러: ensure_reports_built 자기치유(없으면
    생성요청+폴링, EXPKEYWORD식 관례) → _list_reports_by_type → _download_tsv. purchase/
    add_to_cart를 분리 집계하고, purchase는 직접+간접을 합산해 conv_purchase_cnt/amt로
    (제외 게이트=보수적 "전환 있으면 안 자름" 판정, 간접도 살아있는 증거), 직접전환수만
    별도 conv_direct_cnt로도 축적(SS4 승격 신호=품질 있는 전환). 그 외 전환 액션(회원가입
    등)은 무시. 파워링크는 검색어 컬럼이 항상 '-'(확장검색 버킷)라 이 보고서를 쓰지 않음
    (§0.5 확정 — 호출측은 쇼핑에만 사용).

    Returns: [{"date","campaign_id","adgroup_id","search_term",
               "conv_purchase_cnt","conv_direct_cnt","conv_purchase_amt","cart_cnt","cart_amt"}, ...]
    """
    if not ACCESS_LICENSE or not SECRET_KEY_B64:
        log.warning("Naver SA 자격증명 없음 — 검색어 전환 수집 건너뜀")
        return []

    ensure_reports_built("SHOPPINGKEYWORD_CONVERSION_DETAIL", date_from, date_to)
    reports = _list_reports_by_type("SHOPPINGKEYWORD_CONVERSION_DETAIL", date_from, date_to)
    if not reports:
        log.warning("Naver SA: %s~%s SHOPPINGKEYWORD_CONVERSION_DETAIL 보고서 없음", date_from, date_to)
        return []

    agg: dict[tuple, dict] = {}
    seen_dates: set[str] = set()
    for rep in sorted(reports, key=lambda r: r["date"]):
        if rep["date"] in seen_dates:
            continue
        seen_dates.add(rep["date"])
        for cols in _download_tsv(rep["downloadUrl"]):
            if len(cols) <= STCONV_COL_VALUE:
                continue
            action = cols[STCONV_COL_ACTION]
            if action not in (CONV_PURCHASE_ACTION, CONV_ADDTOCART_ACTION):
                continue  # 그 외 전환 액션은 수집 대상 아님
            d = _row_date_iso(cols[STCONV_COL_DATE])
            if d is None:
                continue
            key = (d, cols[STCONV_COL_CAMPAIGN], cols[STCONV_COL_ADGROUP], cols[STCONV_COL_SEARCH_TERM])
            row = agg.get(key)
            if row is None:
                row = {"date": d, "campaign_id": cols[STCONV_COL_CAMPAIGN],
                       "adgroup_id": cols[STCONV_COL_ADGROUP], "search_term": cols[STCONV_COL_SEARCH_TERM],
                       "conv_purchase_cnt": 0, "conv_direct_cnt": 0, "conv_purchase_amt": 0,
                       "cart_cnt": 0, "cart_amt": 0}
                agg[key] = row
            cnt = _safe_int(cols[STCONV_COL_CNT])
            amt = _safe_int(cols[STCONV_COL_VALUE])
            is_direct = cols[STCONV_COL_DIRINDIR] != "2"  # "1"=직접, 그 외는 직접 취급(기존 관례)
            if action == CONV_PURCHASE_ACTION:
                row["conv_purchase_cnt"] += cnt  # 직+간 합산(§1 게이트=보수적 판정)
                row["conv_purchase_amt"] += amt
                if is_direct:
                    row["conv_direct_cnt"] += cnt  # 직접만(SS4 승격 신호)
            else:  # add_to_cart — 직+간 합산, 회계 불활성(★매출 아님)
                row["cart_cnt"] += cnt
                row["cart_amt"] += amt

    log.info("Naver SA 검색어 전환 %d행 집계 (%s~%s)", len(agg), date_from, date_to)
    return list(agg.values())


_STATS_BACKFILL_MAX_DAYS = 730  # 실측(원칙22): "데이터는 최근 730일 이내 기간에서만 조회 가능"
_STATS_BACKFILL_CHUNK_DAYS = 90  # 실측: timeIncrement=1은 호출당 92일 한도(안전마진 90일)
_BACKFILL_FIELDS = '["impCnt","clkCnt","salesAmt","ccnt","convAmt"]'


def fetch_campaign_daily_backfill(campaign_id: str, date_from: date, date_to: date) -> list[dict]:
    """캠페인 단위 일별 성과 소급 조회(/stats timeRange, D-NAO-17 백필).

    실측 한도(docs/references/22): 오늘로부터 최근 730일까지만 조회 가능, 1회 호출당 daily
    breakdown(timeIncrement=1)은 최대 92일 — 92일 미만 청크(90일)로 분할 호출.
    stat-reports(16일 보관)보다 훨씬 긴 창이지만 캠페인 grain만 지원(그룹/키워드 세부 불가).

    Returns: [{"date","campaign_id","imp","clk","cost","conv_cnt","conv_amt"}, ...]
    """
    if not ACCESS_LICENSE or not SECRET_KEY_B64:
        log.warning("Naver SA 자격증명 없음 — 백필 건너뜀")
        return []

    earliest = date.today() - timedelta(days=_STATS_BACKFILL_MAX_DAYS - 1)
    if date_from < earliest:
        log.info("Naver SA 백필 %s: 요청 시작일 %s → API 한도로 %s로 조정", campaign_id, date_from, earliest)
        date_from = earliest
    if date_from > date_to:
        return []

    out: list[dict] = []
    cur = date_from
    while cur <= date_to:
        chunk_end = min(cur + timedelta(days=_STATS_BACKFILL_CHUNK_DAYS - 1), date_to)
        params = {
            "id": campaign_id,
            "fields": _BACKFILL_FIELDS,
            "timeRange": f'{{"since":"{cur.isoformat()}","until":"{chunk_end.isoformat()}"}}',
        }
        resp = _get("/stats", params)
        if resp.status_code != 200:
            log.warning("Naver SA 백필 %s %s~%s 실패: %d %s", campaign_id, cur, chunk_end, resp.status_code, resp.text[:200])
            cur = chunk_end + timedelta(days=1)
            continue
        for d in resp.json().get("data") or []:
            out.append({
                "date": d["dateStart"],
                "campaign_id": campaign_id,
                "imp": _safe_int(d.get("impCnt", 0)),
                "clk": _safe_int(d.get("clkCnt", 0)),
                "cost": _safe_int(d.get("salesAmt", 0)),
                "conv_cnt": _safe_int(d.get("ccnt", 0)),
                "conv_amt": _safe_int(d.get("convAmt", 0)),
            })
        cur = chunk_end + timedelta(days=1)
    return out


_KEYWORDSTOOL_BATCH = 5  # 실측 문서(ncc-keywordstool.json): hintKeywords 최대 5개/호출


def _parse_qc(v) -> int:
    """월검색량 필드(monthlyPcQcCnt 등) → 정수(하한 sentinel 5).

    네이버 keywordstool는 같은 필드를 고검색량 키워드엔 int, 저검색량엔 '< 10' 문자열로
    섞어 반환한다(2026-07-13 실사고: int일 때 .strip() → AttributeError로 sync_naver_keyword
    _volume_job 크래시). int/float/None/str 모두 방어한다.
    """
    if isinstance(v, bool):  # bool은 int 서브클래스 — 검색량 아님, 방어적으로 0
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    v = (v or "").strip()
    if v.startswith("<"):
        return 5
    try:
        return int(v)
    except ValueError:
        return 0


def fetch_keyword_volumes(keywords: list[str]) -> dict[str, dict]:
    """키워드 목록의 월검색량(PC+Mobile)·경쟁도를 keywordstool로 조회(5개씩 배치).

    Returns: {keyword: {"monthly_volume": int, "competition": str|None}, ...}
    (요청 키워드와 정확히 일치하는 relKeyword만 채택 — 응답에 연관 확장 키워드도 섞여 나옴)
    """
    if not ACCESS_LICENSE or not SECRET_KEY_B64:
        log.warning("Naver SA 자격증명 없음 — keywordstool 조회 건너뜀")
        return {}

    wanted = {kw.replace(" ", "").lower() for kw in keywords}
    out: dict[str, dict] = {}
    for i in range(0, len(keywords), _KEYWORDSTOOL_BATCH):
        batch = keywords[i:i + _KEYWORDSTOOL_BATCH]
        params = {"hintKeywords": ",".join(batch), "showDetail": 1}
        resp = _get("/keywordstool", params)
        if resp.status_code != 200:
            log.warning("keywordstool 실패 %s: %d %s", batch, resp.status_code, resp.text[:200])
            continue
        for item in resp.json().get("keywordList", []) or []:
            rel = (item.get("relKeyword") or "")
            if rel.replace(" ", "").lower() not in wanted:
                continue
            vol = _parse_qc(item.get("monthlyPcQcCnt", "0")) + _parse_qc(item.get("monthlyMobileQcCnt", "0"))
            out[rel] = {"monthly_volume": vol, "competition": item.get("compIdx")}

    log.info("keywordstool: 요청 %d개 중 %d개 매칭", len(keywords), len(out))
    return out


def extract_keyword(campaign_name: str, known_keywords: list[str]) -> str | None:
    """캠페인명에서 알려진 상품 키워드를 추출한다."""
    name_lower = campaign_name.lower()
    for kw in known_keywords:
        if kw.lower() in name_lower:
            return kw
    return None


# ── estimate API (P2-S3 T1, docs/references/23 라이브 실측 확정) ──
# 배치 상한·유효값 범위는 추측이 아니라 실제 400 응답으로 확정한 값(원칙: 추정 금지).
_ESTIMATE_AVG_POS_MAX_ITEMS = 200   # 실측: 201개부터 400 "exceeded limit of '200'"
_ESTIMATE_VALID_POSITIONS = (1, 2, 3, 4)  # 실측: position 5부터 400 "must be lower than 5"
_ESTIMATE_PERFORMANCE_MAX_BIDS = 100      # 실측: /estimate/performance/{id} bids 101개부터 400
_ESTIMATE_PERFORMANCE_BULK_MAX_ITEMS = 200  # 실측: /estimate/performance-bulk items 201개부터 400


def _estimate_post(path: str, body: dict) -> requests.Response:
    """estimate 전용 POST. 자격증명은 호출 시점에 os.getenv로 새로 읽어 기존 모듈
    top-level 캡처(ACCESS_LICENSE 등)를 재사용하지 않는다(import-time 캡처 심화 금지, codex #19).
    429/5xx는 읽기전용 추정 호출이라 재시도해도 부작용 없음 — 기존 _get과 동일 backoff."""
    access_license = os.getenv("NAVER_SA_ACCESS_LICENSE", "")
    secret_key = os.getenv("NAVER_SA_SECRET_KEY", "")
    customer_id = os.getenv("NAVER_SA_CUSTOMER_ID", CUSTOMER_ID)
    if not access_license or not secret_key:
        log.warning("Naver SA 자격증명 없음 — estimate 호출 건너뜀: %s", path)
        raise RuntimeError("Naver SA 자격증명 없음 — NAVER_SA_ACCESS_LICENSE/NAVER_SA_SECRET_KEY 확인")

    last: requests.Response | None = None
    for attempt in range(3):
        ts = str(int(time.time() * 1000))
        sig = base64.b64encode(
            hmac.new(secret_key.encode("utf-8"), f"{ts}.POST.{path}".encode(), hashlib.sha256).digest()
        ).decode()
        headers = {
            "X-Timestamp": ts, "X-API-KEY": access_license,
            "X-Signature": sig, "X-Customer": str(customer_id),
        }
        resp = requests.post(BASE_URL + path, headers=headers, json=body, timeout=30)
        if resp.status_code not in _RETRY_STATUS:
            return resp
        last = resp
        wait = 2 ** attempt
        log.warning("Naver SA estimate %s %d — %ds 후 재시도(%d/3)", path, resp.status_code, wait, attempt + 1)
        time.sleep(wait)
    return last  # type: ignore[return-value]


def estimate_average_position_bid(device: str, items: list[dict]) -> list[dict]:
    """평균 노출순위별 필요 입찰가 추정. POST /estimate/average-position-bid/id.

    items: [{"key": nccKeywordId, "position": 1~4}]. **position은 1~4만 유효**(그 밖의 값은 호출측
    책임 — 이 함수는 검증 없이 그대로 전달, bid_simulator가 진단 avg_rank를 clamp해서 넘겨야 함).
    회당 최대 200개(실측, docs/references/23) — 초과분은 이 함수가 내부적으로 청크 분할해 전량
    처리하고 청크 수를 로그로 남긴다(무언 truncation 금지). device: "MOBILE"/"PC"/"BOTH".

    Returns: [{"keyword","position","nccKeywordId","bid"}, ...] (전체 items 순서 무관, 소비측은
    nccKeywordId+position으로 재매칭).
    """
    if not items:
        return []
    n_chunks = (len(items) + _ESTIMATE_AVG_POS_MAX_ITEMS - 1) // _ESTIMATE_AVG_POS_MAX_ITEMS
    out: list[dict] = []
    for i in range(0, len(items), _ESTIMATE_AVG_POS_MAX_ITEMS):
        chunk = items[i:i + _ESTIMATE_AVG_POS_MAX_ITEMS]
        if n_chunks > 1:
            log.info("Naver SA average-position-bid 배치 %d/%d (%d건)", i // _ESTIMATE_AVG_POS_MAX_ITEMS + 1, n_chunks, len(chunk))
        resp = _estimate_post("/estimate/average-position-bid/id", {"device": device, "items": chunk})
        resp.raise_for_status()
        out.extend(resp.json().get("estimate", []))
    return out


def estimate_performance(items: list[dict]) -> list[dict]:
    """키워드×입찰가별 클릭/노출/비용 추정(전환 아님 — clicks/impressions/cost만).
    POST /estimate/performance-bulk.

    items: [{"keyword": str(키워드 **텍스트**, nccKeywordId 아님 — bulk 엔드포인트는 텍스트 전용,
    실측 docs/references/23), "bid": int, "keywordplus": bool, "device": "MOBILE"|"PC"|"BOTH"}].
    호출측(bid_simulator)이 nccKeywordId→keyword 텍스트 매핑을 미리 준비해야 함.
    회당 최대 200개(실측) — 초과분은 내부 청크 분할 전량 처리(무언 truncation 금지).

    Returns: [{"keyword","bid","device","keywordplus","clicks","impressions","cost"}, ...].
    expected_effect 계산 시 clicks × 우리 RPC로 매출 추정(전환 데이터 자체는 없음).
    """
    if not items:
        return []
    n_chunks = (len(items) + _ESTIMATE_PERFORMANCE_BULK_MAX_ITEMS - 1) // _ESTIMATE_PERFORMANCE_BULK_MAX_ITEMS
    out: list[dict] = []
    for i in range(0, len(items), _ESTIMATE_PERFORMANCE_BULK_MAX_ITEMS):
        chunk = items[i:i + _ESTIMATE_PERFORMANCE_BULK_MAX_ITEMS]
        if n_chunks > 1:
            log.info("Naver SA performance-bulk 배치 %d/%d (%d건)", i // _ESTIMATE_PERFORMANCE_BULK_MAX_ITEMS + 1, n_chunks, len(chunk))
        resp = _estimate_post("/estimate/performance-bulk", {"items": chunk})
        resp.raise_for_status()
        out.extend(resp.json().get("items", []))
    return out
