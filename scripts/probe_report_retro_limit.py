"""B-1 소급 한도 프로브 — **8,100콜을 쏘기 전에** 확인할 유일한 미검증 전제.

「1년치 리포트를 재생성할 수 있다」가 참이 아니면 설계의 수집 7,300콜이 통째로 헛돈다.
그래서 소수 콜로 **사다리**를 놓아 어디까지 소급되는지 먼저 잰다.

## 재는 것
- POST가 옛 날짜를 **받아 주는가**(400인가)
- BUILT까지 가는가, 그리고 **행이 실제로 있는가**(BUILT인데 0행이면 「없는 것」과 같다)
- 빌드에 걸리는 시간(수집 페이스 산정의 근거)

## 안전
- **읽기 목적의 POST만**(리포트 잡 생성). 광고 상태·입찰·예산 무접촉. DELETE 안 한다
  (남의 잡을 지울 위험을 만들지 않는다).
- 날짜 5개 × 타입 2개 = 최대 10 POST + 폴링. 루프 상한 있음.
"""
import json
import os
import sys
import time
from datetime import date, timedelta

import requests

BASE = "/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling/backend"
os.chdir(BASE)
sys.path.insert(0, BASE)
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(BASE, ".env"))
from app.services import naver_sa_ad_fetcher as f  # noqa: E402

SP = ("/private/tmp/claude-501/-Users-jino-Library-Mobile-Documents-com-apple-CloudDocs"
      "-1Personal-AI-Program-Ohiselling/de5a26ff-8af2-4b62-84de-4158289a1b67/scratchpad")
TODAY = date(2026, 8, 17)
LADDER = [30, 90, 180, 270, 365]          # 며칠 전
TYPES = ["SHOPPINGKEYWORD_DETAIL", "CRITERION"]
POLL_MAX, POLL_SLEEP = 20, 3


def post_job(tp: str, d: date):
    path = "/stat-reports"
    body = {"reportTp": tp, "statDt": d.strftime("%Y%m%d")}
    r = requests.post(f.BASE_URL + path, headers=f._headers(path, method="POST"),
                      json=body, timeout=f.HTTP_TIMEOUT_S)
    return r


def poll(job_id: str):
    for _ in range(POLL_MAX):
        r = f._get(f"/stat-reports/{job_id}")
        if r.status_code != 200:
            return {"status": f"HTTP {r.status_code}", "raw": r.text[:200]}
        j = r.json()
        if j.get("status") in ("BUILT", "ERROR", "NONE"):
            return j
        time.sleep(POLL_SLEEP)
    return {"status": "TIMEOUT"}


def rows_of(j: dict) -> str:
    """다운로드 URL이 있으면 실제 행수를 센다 — BUILT인데 0행이면 «없는 것»과 같다."""
    url = j.get("downloadUrl")
    if not url:
        return "downloadUrl 없음"
    try:
        r = requests.get(url, headers=f._headers("/report-download", method="GET"),
                         timeout=f.HTTP_TIMEOUT_S)
        if r.status_code != 200:
            return f"download HTTP {r.status_code}"
        n = sum(1 for ln in r.text.splitlines() if ln.strip())
        return f"{n}행"
    except Exception as e:  # noqa: BLE001
        return f"download 예외 {type(e).__name__}"


out = []
print(f"=== 소급 사다리 (기준일 {TODAY}) ===")
for tp in TYPES:
    for days in LADDER:
        d = TODAY - timedelta(days=days)
        t0 = time.time()
        r = post_job(tp, d)
        rec = {"type": tp, "days_ago": days, "statDt": str(d), "post_status": r.status_code}
        if r.status_code not in (200, 201):
            rec["body"] = r.text[:200]
            print(f"  {tp:26s} D-{days:<4d} {d}  POST {r.status_code} ★거부: {r.text[:120]}")
        else:
            j0 = r.json()
            jid = j0.get("reportJobId")
            res = poll(jid) if jid else {"status": "no reportJobId", "raw": str(j0)[:200]}
            rec["job_id"] = jid
            rec["status"] = res.get("status")
            rec["rows"] = rows_of(res) if res.get("status") == "BUILT" else "-"
            rec["elapsed_s"] = round(time.time() - t0, 1)
            print(f"  {tp:26s} D-{days:<4d} {d}  POST {r.status_code} → "
                  f"{rec['status']:8s} · {rec['rows']} · {rec['elapsed_s']}s")
        out.append(rec)
        time.sleep(0.5)

json.dump(out, open(f"{SP}/b1_retro_probe.json", "w"), ensure_ascii=False, indent=1)
print(f"\n저장: {SP}/b1_retro_probe.json")

built = [r for r in out if r.get("status") == "BUILT"]
print("\n=== 판정 ===")
if not built:
    print("🚨 BUILT 0건 — 소급 재생성이 안 된다. **8,100콜 수집을 중단하고 설계를 고쳐야 한다.**")
else:
    deepest = max(r["days_ago"] for r in built)
    print(f"가장 깊은 성공: D-{deepest} ({TODAY - timedelta(days=deepest)})")
    empty = [r for r in built if r.get("rows", "").startswith("0행")]
    if empty:
        print(f"⚠️ BUILT인데 0행: {[(r['type'], r['days_ago']) for r in empty]} — 「없는 것」과 같다")
