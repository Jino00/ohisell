#!/usr/bin/env python3
"""옵션ID별 광고비를 **월 단위로 소급 수집·적재**한다 (ref 46 §5-1⑤).

왜 있나: `coupang_ad_option_daily`(A01029796)는 2026-07-04부터만 있다. 그 앞은 비어 있어
  `/api/overview/rocket-overview`의 `ad_options.reconciliation`이 소급 구간에서 `diff_pct=-100%`
  (옵션합계 0 vs 계정총액)로 뜬다. **결함이 아니라 미수집**이므로 받아 채운다.

★이 경로는 **머니 테이블을 못 건드린다.** prod `/rocket/ad-cost/option-ingest`가
  `options_only=True`로 파서를 부른다(`coupang_ops.py`) — `coupang_ad_report`·`ad_costs`는
  손대지 않는다. 계정 총액은 `report/SALES`가 **ALL 기준**으로 쓰는데 이 XLSX는 **PA 기준**이라
  같은 행을 덮으면 순이익이 조용히 흔들리기 때문이다(D-13ⓑ). 그래서 이중계상 위험이 0이다.

★한 번에 다 받지 않고 **월별로** 받는다: 보고서 생성이 구간 길이에 비례해 느려지고, 30일
  구간이 이미 3.7MB다(실측 2026-08-06). 실패해도 그 달만 다시 받으면 된다(멱등 upsert).

사용:
  ohitech_ad_option_backfill.py                    # 2025-07-01 ~ 2026-07-03 전체
  ohitech_ad_option_backfill.py 2025-10 2025-12    # 특정 달만(YYYY-MM 범위, 양끝 포함)
  ohitech_ad_option_backfill.py --dry-run          # 받기만 하고 push 안 함

전제: CDP 9224(오하이테크 광고 Chrome) 로그인 세션이 살아 있어야 한다. 죽어 있으면
  `POST /api/coupang/ops/rocket/ad-cost/request-refresh`로 페처를 한 번 돌려 자가복구시킬 것
  (SSO → Keychain 순으로 스스로 복구한다).
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import websocket

HERE = Path(__file__).resolve().parent
REPORT_TOOL = HERE / "ohitech_ad_option_report.py"
CONFIG_PATH = Path(os.path.expanduser("~/.ohisell_ohitech_ad.json"))
VENDOR = "A01029796"

# 소급 대상 = 옵션 테이블이 시작되는 2026-07-04 **직전**까지. 시작은 1P 광고 첫 달(2025-07).
DEFAULT_FROM = "2025-07"
DEFAULT_TO_DAY = date(2026, 7, 3)


def months(a: str, b: str):
    ya, ma = int(a[:4]), int(a[5:7])
    yb, mb = int(b[:4]), int(b[5:7])
    while (ya, ma) <= (yb, mb):
        yield ya, ma
        ma += 1
        if ma == 13:
            ya, ma = ya + 1, 1


def month_window(y: int, m: int) -> tuple[date, date]:
    start = date(y, m, 1)
    end = date(y + (m == 12), (m % 12) + 1, 1)
    end = date.fromordinal(end.toordinal() - 1)
    return start, min(end, DEFAULT_TO_DAY)


def rearm(port: int = 9224, timeout_s: int = 40) -> bool:
    """광고센터 탭을 리로드해 오리진을 **재무장**한다. 각 달을 받기 전에 부른다.

    왜 매번 하는가(2026-08-06 실측): 탭이 몇 분만 유휴여도 `/user/login`으로 밀려나 있고,
    그 상태에서 GraphQL을 부르면 `TypeError: Failed to fetch`가 난다. 그런데 호출부는 그걸
    빈 응답으로 받아 **"캠페인 0개"**로 읽는다 — 즉 세션이 죽은 것이 **데이터가 없는 것**으로
    둔갑한다(연속 9개월이 조용히 건너뛰어졌다). 리로드 한 번이면 `/dashboard`로 착지한다.

    ★로그인 페이지에 머무르면 False를 돌려준다 — **없다고 단정하지 않고 실패로 표면화**한다.
    """
    try:
        ts = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5))
        tab = [t for t in ts if t.get("type") == "page"
               and "advertising.coupang.com" in t.get("url", "")][0]
        ws = websocket.create_connection(tab["webSocketDebuggerUrl"],
                                         suppress_origin=True, max_size=None)
    except (urllib.error.URLError, OSError, IndexError, ValueError) as ex:
        print(f"  ! 재무장 실패(CDP 9224 접속): {str(ex)[:100]}", flush=True)
        return False
    mid = [0]

    def call(m, p=None):
        mid[0] += 1
        ws.send(json.dumps({"id": mid[0], "method": m, "params": p or {}}))
        while True:
            x = json.loads(ws.recv())
            if x.get("id") == mid[0]:
                return x

    try:
        call("Runtime.enable")
        call("Page.enable")
        call("Page.reload", {"ignoreCache": True})
        deadline = time.monotonic() + timeout_s
        href = ""
        while time.monotonic() < deadline:
            time.sleep(2)
            r = call("Runtime.evaluate", {"expression": "location.href", "returnByValue": True})
            href = r.get("result", {}).get("result", {}).get("value") or ""
            if href and "/user/login" not in href:
                return True
        print(f"  ! 재무장 후에도 로그인 페이지: {href[:80]} — 페처 갱신으로 자가복구 필요", flush=True)
        return False
    finally:
        ws.close()


def push(cfg: dict, path: Path, y: int, m: int) -> dict:
    """옵션 전용 엔드포인트로 push. 파일명 규약 = {vendor}_pa_daily_*.xlsx."""
    url = cfg["prod_base_url"].rstrip("/") + "/api/coupang/ops/rocket/ad-cost/option-ingest"
    req = urllib.request.Request(
        url,
        data=path.read_bytes(),
        headers={
            "X-Ingest-Token": cfg["ingest_token"],
            "X-Report-Filename": f"{VENDOR}_pa_daily_backfill_{y}{m:02d}.xlsx",
            "Content-Type": "application/octet-stream",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode())


def main() -> int:
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    a = argv[0] if argv else DEFAULT_FROM
    b = argv[1] if len(argv) > 1 else DEFAULT_TO_DAY.strftime("%Y-%m")

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    outdir = Path(os.path.expanduser("~/.ohisell_ohitech_ad_option_backfill"))
    outdir.mkdir(exist_ok=True)

    summary = []
    for y, m in months(a, b):
        s, e = month_window(y, m)
        tag = f"{y}-{m:02d}"
        xlsx = outdir / f"{tag}.xlsx"
        if not xlsx.is_file() or xlsx.stat().st_size == 0:
            print(f"\n=== {tag} ({s}~{e}) 보고서 요청 ===", flush=True)
            if not rearm():
                summary.append({"month": tag, "error": "세션 재무장 실패(로그인 필요)"})
                continue
            rc = subprocess.call(
                [sys.executable, str(REPORT_TOOL),
                 s.strftime("%Y%m%d"), e.strftime("%Y%m%d"), str(xlsx)]
            )
            if rc != 0 or not xlsx.is_file():
                print(f"  ✗ {tag} 수집 실패(rc={rc}) — 건너뜀. 나중에 이 달만 다시 실행할 것", flush=True)
                summary.append({"month": tag, "error": f"fetch rc={rc}"})
                continue
        else:
            print(f"\n=== {tag} 캐시 재사용({xlsx.stat().st_size:,} bytes) ===", flush=True)
        if dry:
            summary.append({"month": tag, "bytes": xlsx.stat().st_size, "pushed": False})
            continue
        try:
            res = push(cfg, xlsx, y, m)
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as ex:
            print(f"  ✗ {tag} push 실패: {str(ex)[:160]}", flush=True)
            summary.append({"month": tag, "error": f"push {str(ex)[:80]}"})
            continue
        print(f"  → {json.dumps(res, ensure_ascii=False)[:220]}", flush=True)
        summary.append({"month": tag, **{k: res.get(k) for k in
                                         ("option_rows", "option_spend", "inserted", "skipped")}})

    print("\n=== 요약 ===")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0 if all("error" not in s for s in summary) else 1


if __name__ == "__main__":
    sys.exit(main())
