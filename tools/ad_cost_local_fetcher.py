#!/usr/bin/env python3
# ad_cost_local_fetcher.py — 쿠팡 광고비를 Jino Mac(residential IP)에서 fetch → prod로 push.
#
# 왜 로컬에서?: advertising.coupang.com은 Akamai가 데이터센터(prod) IP를 차단(403).
#   Jino 브라우저 IP에서는 201로 정상 동작 → fetch는 여기서, 저장은 prod DB로.
# 롤링 갱신: 성공 응답의 Set-Cookie를 받아 쿠키 파일을 매번 갱신 → 재붙여넣기 최소화
#   (단, Mac이 ~수시간 이상 꺼져 bm_sv/_abck 만료되면 재import 필요).
#
# 사용:
#   설정 1회:  python3 ad_cost_local_fetcher.py import curl.txt   # 브라우저 cURL 저장한 파일
#   실행:      python3 ad_cost_local_fetcher.py                   # fetch→push (launchd가 매시 호출)
from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

CONFIG_PATH = Path(os.path.expanduser("~/.ohisell_ad_fetcher.json"))
LOG_PATH = Path(os.path.expanduser("~/.ohisell_ad_fetcher.log"))
ADS_URL = "https://advertising.coupang.com/marketing/cmg-api/report/cost"
KST = ZoneInfo("Asia/Seoul")
_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "origin": "https://advertising.coupang.com",
    "referer": "https://advertising.coupang.com/marketing/dashboard/sales?_cap_client=WING",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
log = logging.getLogger("ad_fetcher")


def load_config() -> dict:
    if not CONFIG_PATH.is_file():
        log.error("설정 파일 없음: %s — 아래 형식으로 생성하세요:\n%s", CONFIG_PATH, _CONFIG_EXAMPLE)
        sys.exit(2)
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    for k in ("prod_base_url", "ingest_token", "vendor_ids", "cookie_file"):
        if not cfg.get(k):
            log.error("설정 누락: %s", k)
            sys.exit(2)
    return cfg


_CONFIG_EXAMPLE = json.dumps({
    "prod_base_url": "https://sellc.ohitech.co.kr",
    "ingest_token": "<AD_INGEST_TOKEN 과 동일한 값>",
    "vendor_ids": [104438581, 104997005],
    "cookie_file": os.path.expanduser("~/.ohisell_ad_cookie.txt"),
}, ensure_ascii=False, indent=2)


def write_private(path: Path, text: str) -> None:
    """0600 권한으로 생성·기록 (umask 경쟁 없이 — codex P2)."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)


def parse_cookie_header(s: str) -> dict:
    """'k=v; k=v' 헤더 문자열 → dict (값에 '=' 포함 가능하므로 첫 '='로만 분리)."""
    d: dict[str, str] = {}
    for part in s.strip().strip(";").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        d[k.strip()] = v.strip()
    return d


def extract_cookie_from_curl(text: str) -> str:
    """브라우저 'Copy as cURL' → cookie 헤더 추출 (-b '...' 또는 -H 'cookie: ...')."""
    m = re.search(r"-H\s+(['\"])cookie:\s*(.*?)\1", text, re.IGNORECASE | re.DOTALL)
    if not m:
        m = re.search(r"(?:-b|--cookie)\s+(['\"])(.*?)\1", text, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(2).strip()
    if "=" in text and "curl " not in text.lower():
        return text.strip()  # 쿠키 문자열만 붙여넣은 경우
    raise ValueError("cURL에서 cookie를 찾지 못했습니다")


def cmd_import(curl_file: str) -> None:
    cfg = load_config()
    text = Path(os.path.expanduser(curl_file)).read_text(encoding="utf-8")
    cookie = extract_cookie_from_curl(text)
    if not cookie:
        log.error("쿠키 추출 실패(빈 값)")
        sys.exit(2)
    cookie_path = Path(os.path.expanduser(cfg["cookie_file"]))
    write_private(cookie_path, cookie)
    log.info("쿠키 저장 완료: %s (%d bytes)", cookie_path, len(cookie))


def fetch_and_push() -> int:
    cfg = load_config()
    cookie_path = Path(os.path.expanduser(cfg["cookie_file"]))
    if not cookie_path.is_file():
        log.error("쿠키 파일 없음: %s — 먼저 'import curl.txt' 실행", cookie_path)
        return 2

    cookies = parse_cookie_header(cookie_path.read_text(encoding="utf-8"))
    if not cookies:
        log.error("쿠키 파일이 비었습니다")
        return 2

    sess = requests.Session()
    for k, v in cookies.items():
        sess.cookies.set(k, v, domain=".coupang.com")

    payload = {"parentNodes": [{"adNodeId": int(v), "campaignType": "PA"} for v in cfg["vendor_ids"]]}
    try:
        resp = sess.post(ADS_URL, json=payload, headers=_HEADERS, timeout=20)
    except requests.RequestException as e:
        log.error("fetch 네트워크 오류: %s", e)
        return 1

    if resp.status_code in (401, 403):
        log.error("fetch 차단/만료 HTTP %s — Akamai 차단 또는 쿠키 만료. 새 cURL을 'import'하세요. "
                  "(이 PC의 IP가 브라우저와 같아야 함)", resp.status_code)
        return 1
    if resp.status_code != 201:
        log.error("fetch 예상외 HTTP %s — %s", resp.status_code, resp.text[:200])
        return 1

    try:
        data = resp.json()
    except ValueError as e:
        log.error("응답 JSON 파싱 실패: %s", e)
        return 1

    # 롤링 갱신: 이번 응답이 회전시킨 쿠키(resp.cookies)만 원본 위에 덮어씀 (codex P1).
    # get_dict()로 전체 jar를 평탄화하면 도메인 충돌 시 엉뚱한 _abck/bm_sv를 저장할 수 있음.
    merged = {**cookies, **resp.cookies.get_dict()}
    write_private(cookie_path, "; ".join(f"{k}={v}" for k, v in merged.items()))

    # 응답 키 중 설정된 vendor_id만 채택 (메타데이터 키가 가짜 vendor로 새는 것 방지, codex P2).
    wanted = {str(v) for v in cfg["vendor_ids"]}
    vendors = [
        {"vendor_id": str(vid), "day_cost": int(c.get("day") or 0), "month_cost": int(c.get("month") or 0)}
        for vid, c in data.items()
        if str(vid) in wanted and isinstance(c, dict)
    ]
    if not vendors:
        log.warning("응답에 vendor 데이터 없음: %s", str(data)[:200])
        return 1

    today = datetime.now(KST).date().isoformat()
    try:
        pr = requests.post(
            cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/ad-cost/ingest",
            json={"date": today, "vendors": vendors},
            headers={"X-Ingest-Token": cfg["ingest_token"]},
            timeout=20,
        )
    except requests.RequestException as e:
        log.error("prod push 네트워크 오류: %s", e)
        return 1

    if pr.status_code != 200:
        log.error("prod push 실패 HTTP %s — %s", pr.status_code, pr.text[:200])
        return 1

    log.info("성공: %s push %s → %s", today,
             [(v["vendor_id"], v["day_cost"]) for v in vendors], pr.json())
    return 0


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "import":
        if len(sys.argv) < 3:
            log.error("사용법: python3 ad_cost_local_fetcher.py import <curl파일>")
            sys.exit(2)
        cmd_import(sys.argv[2])
        return
    sys.exit(fetch_and_push())


if __name__ == "__main__":
    main()
