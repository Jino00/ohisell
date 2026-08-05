#!/usr/bin/env python3
"""Billboard 옵션 보고서를 **임의 과거 구간**으로 받아온다 (1P/3P/RG 판별 + 광고비 소급용).

왜 있나 (2026-08-05):
  1P 광고비가 2026-03-17부터만 있었다. 매출(계산서)은 2025-07-23부터라 약 8개월 광고비가
  0으로 잡혀 손익이 과대했다(결손 350,366,257원). `report/SALES`로 소급할 수 있지만 그건
  **계정 총액**이라 판매방식이 섞인다 — 이 계정은 2025-07 전에 로켓그로스 광고를 태웠다.

  이 스크립트가 그걸 가른다. Billboard XLSX에는 **「판매방식」 컬럼**과 **광고집행 옵션ID**가
  있어 Retail(로켓배송=1P) / 3P / RG를 행 단위로 나눌 수 있다.

  ★2025-10 실측: Retail 53,465,961원(97.7%) · 3P 1,276,942원(2.3%) · **RG 0원**.
    Retail 옵션 321개 중 298개(92.8%)가 1P 채널 매핑(ch5)에 있고 RG·3P 매핑과는 교집합 0.
    → "2025년 7월부터 1P로 전환"(Jino)이 데이터로 확인됐다.

사용: ohitech_ad_option_report.py YYYYMMDD YYYYMMDD [출력.xlsx]
  CDP 9224(오하이테크 광고 Chrome)에 붙어 same-origin으로 호출한다. 보고서 생성에 20~30초.

함정 둘 (둘 다 실제로 걸렸다):
  ① 탭이 오래 유휴면 fetch가 `Failed to fetch`로 죽는다(Akamai stale) → 페이지를 리로드해
     오리진을 재무장한 뒤 호출할 것.
  ② async IIFE가 **문자열을 그대로 반환하면** CDP가 빈 객체({})를 준다 — `JSON.stringify`로
     한 겹 감싸야 값이 온다.
  ③ openpyxl로 옵션ID를 읽으면 float이 되어 `.0`이 붙는다 — 정수 문자열로 정규화할 것.
     (이걸 안 하면 카탈로그 조인이 전부 0건이 되어 "겹치지 않는다"는 거짓 결론이 난다.)
"""

import base64
import json
import sys
import time
import urllib.request

import websocket

PORT = 9224
GQL = "https://advertising.coupang.com/marketing-reporting/v2/graphql"
EXCEL = "https://advertising.coupang.com/marketing-reporting/v2/api/excel-report?id="

Q_CAMPAIGNS = (
    "query GetCampaignListInBillboard($startDate: Int!, $endDate: Int!, $reportType: ReportType!) {\n"
    "  getCampaignList(\n    startDate: $startDate\n    endDate: $endDate\n    reportType: $reportType\n"
    "  ) {\n    id\n    name\n    __typename\n  }\n}\n"
)
M_REQUEST = (
    "mutation ($startDate: Int!, $endDate: Int!, $campaignIds: [ID], $reportType: ReportType!, "
    "$dateGroup: DateGroup!, $granularity: Granularity, $excludeIfNoClickCount: Boolean) {\n"
    "  requestReport(\n    data: {startDate: $startDate, endDate: $endDate, campaignIds: $campaignIds, "
    "reportType: $reportType, dateGroup: $dateGroup, granularity: $granularity, "
    "excludeIfNoClickCount: $excludeIfNoClickCount}\n  ) {\n    id\n    status\n    __typename\n  }\n}\n"
)
Q_LIST = (
    "query ($reportType: ReportType!, $page: Int!, $pageSize: Int!, $duration: Int!) {\n"
    "  reportList(\n    data: {reportType: $reportType, page: $page, pageSize: $pageSize, "
    "duration: $duration}\n  ) {\n    total\n    reports {\n      id\n      status\n      __typename\n"
    "    }\n    __typename\n  }\n}\n"
)


def connect():
    ts = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=5))
    tab = [t for t in ts if t.get("type") == "page" and "advertising.coupang.com" in t.get("url", "")][0]
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], suppress_origin=True, max_size=None)
    mid = [0]

    def call(m, p=None):
        mid[0] += 1
        ws.send(json.dumps({"id": mid[0], "method": m, "params": p or {}}))
        while True:
            x = json.loads(ws.recv())
            if x.get("id") == mid[0]:
                return x
    call("Runtime.enable")
    return ws, call


def js(call, expr):
    r = call("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True})
    v = r.get("result", {}).get("result", {})
    if "value" not in v:
        raise RuntimeError(json.dumps(r)[:400])
    return v["value"]


def gql(call, body):
    # ★async IIFE가 문자열을 그대로 반환하면 CDP가 빈 객체({})를 준다 — JSON.stringify로
    #   한 겹 감싸야 값이 온다(로켓 페처 cdp 헬퍼에서 겪은 것과 같은 함정).
    expr = ("(async(p)=>{const r=await fetch(%s,{method:'POST',headers:{'content-type':'application/json'},"
            "body:JSON.stringify(p),credentials:'include'});"
            "return JSON.stringify({s:r.status,b:await r.text()});})(%s)"
            % (json.dumps(GQL), json.dumps(body)))
    v = js(call, expr)
    d = v if isinstance(v, dict) else json.loads(v)
    return json.loads(d["b"]) if isinstance(d, dict) and "b" in d else d


def main():
    sd, ed = int(sys.argv[1]), int(sys.argv[2])
    out = sys.argv[3] if len(sys.argv) > 3 else "opt_report.xlsx"
    ws, call = connect()

    d = gql(call, {"operationName": "GetCampaignListInBillboard",
                   "variables": {"startDate": sd, "endDate": ed, "reportType": "pa"},
                   "query": Q_CAMPAIGNS})
    camps = (d.get("data") or {}).get("getCampaignList") or []
    print(f"캠페인 {len(camps)}개", file=sys.stderr)
    if not camps:
        print("캠페인 없음:", json.dumps(d)[:300], file=sys.stderr)
        return 1
    ids = [str(c["id"]) for c in camps if c.get("id")]

    d = gql(call, {"variables": {"reportType": "pa", "startDate": sd, "endDate": ed,
                                 "dateGroup": "daily", "granularity": "keyword",
                                 "excludeIfNoClickCount": True, "campaignIds": ids},
                   "query": M_REQUEST})
    req = (d.get("data") or {}).get("requestReport")
    if not req or not req.get("id"):
        print("requestReport 실패:", json.dumps(d)[:400], file=sys.stderr)
        return 1
    rid = str(req["id"])
    print(f"보고서 id={rid} status={req.get('status')}", file=sys.stderr)

    for _ in range(100):
        time.sleep(3)
        d = gql(call, {"variables": {"reportType": "pa", "page": 1, "pageSize": 20, "duration": 30},
                       "query": Q_LIST})
        reports = ((d.get("data") or {}).get("reportList") or {}).get("reports") or []
        me = [r for r in reports if str(r.get("id")) == rid]
        st = me[0].get("status") if me else "?"
        print(f"  status={st}", file=sys.stderr)
        if st in ("COMPLETED", "completed", "DONE", "done", "SUCCESS"):
            break
    else:
        print("폴링 타임아웃", file=sys.stderr)
        return 1

    expr = ("(async()=>{const r=await fetch(%s+%s,{credentials:'include'});"
            "const b=await r.arrayBuffer();let s='';const u=new Uint8Array(b);"
            "for(let i=0;i<u.length;i++)s+=String.fromCharCode(u[i]);"
            "return JSON.stringify({s:r.status,b64:btoa(s)});})()"
            % (json.dumps(EXCEL), json.dumps(rid)))
    _v = js(call, expr)
    res = _v if isinstance(_v, dict) else json.loads(_v)
    if res["s"] >= 400:
        print("다운로드 실패 status", res["s"], file=sys.stderr)
        return 1
    data = base64.b64decode(res["b64"])
    with open(out, "wb") as f:
        f.write(data)
    print(f"저장: {out} ({len(data):,} bytes)", file=sys.stderr)
    ws.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
