#!/usr/bin/env python3
"""Billboard 옵션 보고서를 **reportType=nca**로 받아 판매방식을 가른다.

ref 46 §5-1④는 "ReportType enum은 pa·da 둘뿐"이라 적었으나 실측(2026-08-06)에서 enum 오류
메시지가 `"DA", "NCA", "PA", "da", "nca"`를 돌려줬다 — NCA도 보고서로 받을 수 있다.
그러면 비-PA(=NCA)를 비례배분 없이 Retail/3P로 **측정**할 수 있다.

사용: ohitech_nca_report.py YYYYMMDD YYYYMMDD out.xlsx

⚠️**구간을 길게 잡지 말 것 — 다운로드가 조용히 깨진다.** 2025-10 한 달을 통으로 받으면
  72MB가 오는데 zip이 아니다(매직 `22eb82a0`). 아래 base64 경로가 `String.fromCharCode`로
  바이트를 한 글자씩 잇기 때문에 그 크기를 못 견딘다. **주 단위로 쪼개면** 각 2~4MB로
  정상 zip이 온다(같은 달을 5주로 받아 합계가 원천 비-PA와 0.086% 일치). 파일이 생겼다고
  성공이 아니다 — `zipfile.is_zipfile()`로 확인할 것.
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
RT = "nca"

Q_CAMPAIGNS = ("query GetCampaignListInBillboard($startDate: Int!, $endDate: Int!, $reportType: ReportType!) {\n"
               "  getCampaignList(startDate: $startDate endDate: $endDate reportType: $reportType) { id name __typename }\n}\n")
M_REQUEST = ("mutation ($startDate: Int!, $endDate: Int!, $campaignIds: [ID], $reportType: ReportType!, "
             "$dateGroup: DateGroup!, $granularity: Granularity, $excludeIfNoClickCount: Boolean) {\n"
             "  requestReport(data: {startDate: $startDate, endDate: $endDate, campaignIds: $campaignIds, "
             "reportType: $reportType, dateGroup: $dateGroup, granularity: $granularity, "
             "excludeIfNoClickCount: $excludeIfNoClickCount}) { id status __typename }\n}\n")
Q_LIST = ("query ($reportType: ReportType!, $page: Int!, $pageSize: Int!, $duration: Int!) {\n"
          "  reportList(data: {reportType: $reportType, page: $page, pageSize: $pageSize, duration: $duration}) "
          "{ total reports { id status __typename } __typename }\n}\n")


def connect():
    ts = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=5))
    tab = [t for t in ts if t.get("type") == "page"
           and "advertising.coupang.com" in t.get("url", "")][0]
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
    call("Page.enable")
    call("Page.reload", {"ignoreCache": True})
    for _ in range(20):
        time.sleep(2)
        href = call("Runtime.evaluate", {"expression": "location.href",
                                         "returnByValue": True})["result"]["result"].get("value", "")
        if href and "/user/login" not in href:
            break
    else:
        raise SystemExit("로그인 페이지 — 페처 갱신 필요")
    return call


def js(call, expr):
    r = call("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True})
    v = r.get("result", {}).get("result", {})
    if "value" not in v:
        raise RuntimeError(json.dumps(r)[:300])
    return v["value"]


def gql(call, body):
    expr = ("(async(p)=>{const r=await fetch(%s,{method:'POST',headers:{'content-type':'application/json'},"
            "body:JSON.stringify(p),credentials:'include'});return JSON.stringify({s:r.status,b:await r.text()});})(%s)"
            % (json.dumps(GQL), json.dumps(body)))
    v = js(call, expr)
    d = v if isinstance(v, dict) else json.loads(v)
    if d.get("s") != 200:
        raise RuntimeError(f"HTTP {d.get('s')}: {str(d.get('b'))[:200]}")
    return json.loads(d["b"])


def main():
    sd, ed = int(sys.argv[1]), int(sys.argv[2])
    out = sys.argv[3]
    call = connect()
    d = gql(call, {"operationName": "GetCampaignListInBillboard",
                   "variables": {"startDate": sd, "endDate": ed, "reportType": RT},
                   "query": Q_CAMPAIGNS})
    camps = (d.get("data") or {}).get("getCampaignList") or []
    print(f"NCA 캠페인 {len(camps)}개", file=sys.stderr)
    if not camps:
        print("캠페인 0 — 응답:" + json.dumps(d)[:200], file=sys.stderr)
        return 1
    ids = [str(c["id"]) for c in camps if c.get("id")]
    d = gql(call, {"variables": {"reportType": RT, "startDate": sd, "endDate": ed,
                                 "dateGroup": "daily", "granularity": "keyword",
                                 "excludeIfNoClickCount": True, "campaignIds": ids},
                   "query": M_REQUEST})
    req = (d.get("data") or {}).get("requestReport")
    if not req or not req.get("id"):
        print("requestReport 실패: " + json.dumps(d)[:300], file=sys.stderr)
        return 1
    rid = str(req["id"])
    for _ in range(120):
        time.sleep(3)
        d = gql(call, {"variables": {"reportType": RT, "page": 1, "pageSize": 20, "duration": 30},
                       "query": Q_LIST})
        reports = ((d.get("data") or {}).get("reportList") or {}).get("reports") or []
        me = [r for r in reports if str(r.get("id")) == rid]
        st = (me[0].get("status") if me else "?")
        if str(st).lower() in ("completed", "done", "success"):
            break
    else:
        print("폴링 타임아웃", file=sys.stderr)
        return 1
    expr = ("(async()=>{const r=await fetch(%s+%s,{credentials:'include'});"
            "const b=await r.arrayBuffer();let s='';const u=new Uint8Array(b);"
            "for(let i=0;i<u.length;i++)s+=String.fromCharCode(u[i]);"
            "return JSON.stringify({s:r.status,b64:btoa(s)});})()" % (json.dumps(EXCEL), json.dumps(rid)))
    v = js(call, expr)
    res = v if isinstance(v, dict) else json.loads(v)
    if res["s"] >= 400:
        print("다운로드 실패", res["s"], file=sys.stderr)
        return 1
    data = base64.b64decode(res["b64"])
    open(out, "wb").write(data)
    print(f"저장: {out} ({len(data):,} bytes)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
