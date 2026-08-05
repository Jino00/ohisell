#!/usr/bin/env python3
"""report/SALES 계정 총액 일별 조회(소급 검산 분모, D-21). CDP 9224 same-origin.

`ALL_DELIVERED_AD_COST`(전체=비-PA 포함, D-10 머니룰 값)와 `DELIVERED_AD_COST`(PA)를 둘 다
받아 적는다 — Billboard 옵션 보고서는 **PA 전용**이라 완결성 검산의 분모는 PA 쪽이다.
(둘의 차이 = 비-PA. 2025-07~2026-03 8개월 4,527,079원 = 1.17%, ref 46 §5-1④.)

사용: ohitech_ad_sales_totals.py YYYY-MM-DD YYYY-MM-DD out.json
⚠️탭이 오래 유휴면 Akamai stale로 fetch가 죽는다 → 광고센터 탭을 리로드해 오리진을 재무장할 것.
"""
import json, sys, urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import websocket

KST = ZoneInfo("Asia/Seoul")
PORT = 9224
URL = "https://advertising.coupang.com/marketing/cmg-api/report/SALES"


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


def main():
    d0 = datetime.strptime(sys.argv[1], "%Y-%m-%d").replace(tzinfo=KST)
    d1 = datetime.strptime(sys.argv[2], "%Y-%m-%d").replace(tzinfo=KST)
    out = sys.argv[3]
    payload = {"start": int(d0.timestamp() * 1000), "end": int((d1 + timedelta(days=1)).timestamp() * 1000)}
    ws, call = connect()
    expr = ("(async(p)=>{const r=await fetch(%s,{method:'POST',headers:{'content-type':'application/json',"
            "'accept':'application/json, text/plain, */*'},body:JSON.stringify(p),credentials:'include'});"
            "return JSON.stringify({s:r.status,b:await r.text()});})(%s)" % (json.dumps(URL), json.dumps(payload)))
    r = call("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True})
    v = r.get("result", {}).get("result", {})
    if "value" not in v:
        print("CDP 실패:", json.dumps(r)[:400], file=sys.stderr)
        return 1
    d = v["value"] if isinstance(v["value"], dict) else json.loads(v["value"])
    if d["s"] >= 400:
        print("HTTP", d["s"], d["b"][:200], file=sys.stderr)
        return 1
    data = json.loads(d["b"])
    result = data.get("result", data)
    days = {}
    for epoch_ms, m in result.items():
        try:
            ts = int(epoch_ms)
        except (TypeError, ValueError):
            continue
        day = datetime.fromtimestamp(ts / 1000, KST).date().isoformat()
        if not isinstance(m, dict):
            continue
        days[day] = {
            "all": float(m.get("ALL_DELIVERED_AD_COST") or 0),
            "pa": float(m.get("DELIVERED_AD_COST") or 0),
            "sales": float(m.get("AD_ATTRIBUTED_SALES") or 0),
        }
    with open(out, "w") as f:
        json.dump(days, f, ensure_ascii=False, indent=0, sort_keys=True)
    tot_all = sum(x["all"] for x in days.values())
    tot_pa = sum(x["pa"] for x in days.values())
    print(f"{len(days)}일  ALL={tot_all:,.0f}  PA={tot_pa:,.0f}  → {out}", file=sys.stderr)
    ws.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
