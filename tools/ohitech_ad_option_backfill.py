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
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ohitech_ad_cdp import sniff_format  # noqa: E402
from ohitech_ad_option_report import RC_EMPTY, RC_SESSION  # noqa: E402

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
            # 재무장은 보고서 도구가 스스로 한다(`ohitech_ad_cdp.connect`). 여기선 결과 코드만 가른다 —
            # ★"세션이 죽었다"와 "그 달엔 정말 캠페인이 없다"를 절대 같은 성공으로 접지 않는다.
            rc = subprocess.call(
                [sys.executable, str(REPORT_TOOL),
                 s.strftime("%Y%m%d"), e.strftime("%Y%m%d"), str(xlsx)]
            )
            if rc == RC_EMPTY:
                print(f"  · {tag} 캠페인 0건(세션은 살아 있음) — 그 달엔 광고가 없었다", flush=True)
                summary.append({"month": tag, "empty": True})
                continue
            if rc == RC_SESSION:
                print(f"  ✗ {tag} 세션 죽음 — 재시도해도 같다. 페처 갱신으로 자가복구시킬 것:\n"
                      f"    curl -X POST {cfg['prod_base_url'].rstrip('/')}"
                      "/api/coupang/ops/rocket/ad-cost/request-refresh", flush=True)
                summary.append({"month": tag, "error": "세션 죽음(로그인 필요)"})
                continue
            if rc != 0 or not xlsx.is_file():
                print(f"  ✗ {tag} 수집 실패(rc={rc}) — 건너뜀. 나중에 이 달만 다시 실행할 것", flush=True)
                summary.append({"month": tag, "error": f"fetch rc={rc}"})
                continue
        else:
            print(f"\n=== {tag} 캐시 재사용({xlsx.stat().st_size:,} bytes) ===", flush=True)
        # ★prod `option-ingest`는 **xlsx만** 파싱한다. 행이 많은 달은 서버가 TSV를 주므로
        #   그대로 보내면 400이 난다 — 보내기 전에 판정해서 무엇을 해야 하는지 말해 준다.
        with open(xlsx, "rb") as fh:
            fmt = sniff_format(fh.read(4096))
        if fmt != "xlsx":
            print(f"  ✗ {tag} 서버가 {fmt.upper()}를 줬다(행이 너무 많다) — prod ingest는 xlsx만 받는다.\n"
                  f"    이 달만 주 단위로 쪼개 다시 받을 것: "
                  f"ohitech_ad_option_report.py {s:%Y%m%d} {e:%Y%m%d} …", flush=True)
            summary.append({"month": tag, "error": f"포맷 {fmt} — 구간을 쪼갤 것"})
            continue
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
