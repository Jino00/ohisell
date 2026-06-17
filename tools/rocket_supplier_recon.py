#!/usr/bin/env python3
# rocket_supplier_recon.py — 쿠팡 1P 공급사 포털(supplier.coupang.com) 정찰(spike) 도구.
# 트랙: track_coupang-rocket-1p.md S1(D-8/D-9). 발주/납품/정산 화면이 호출하는 내부 API를
# 라이브로 캡처해 데이터 형태를 실측한다(추측 구현 금지). 결과 ref: docs/references/20_*.
#
# ★정찰로 확정된 캡처 방법(2026-06-17, 함정 3종 우회 — ref20 §7):
#   1) Playwright connect_over_cdp 는 "자기가 안 띄운 기존 페이지"의 response 이벤트를 못 받음
#      → 원시 CDP 웹소켓 Network.responseReceived 직접 도청으로 우회.
#   2) Chrome 디버깅 ws 는 Origin 헤더 붙은 연결을 403 거부
#      → websocket.create_connection(url, suppress_origin=True).
#   3) 최상위 navigation 문서(SSR HTML)는 Network.getResponseBody 빈 본문 반환
#      → 정산 같은 폼-GET SSR 페이지는 Runtime.evaluate 로 DOM 테이블 추출.
#
# 사용:
#   1) 전용 Chrome:  backend/.venv/bin/python3 tools/rocket_supplier_recon.py chrome
#      → 열린 브라우저에서 supplier.coupang.com 로그인 + 발주/납품/정산 이동
#   2) JSON 캡처:    backend/.venv/bin/python3 tools/rocket_supplier_recon.py capture
#      → 발주/납품 list 등 JSON API 기록. 결과 JSONL: ~/.ohisell_supplier_recon.jsonl
#   3) 정산 DOM:     backend/.venv/bin/python3 tools/rocket_supplier_recon.py dom [URL키워드]
#      → 현재 열린 SSR 페이지(기본 settlement)의 <table> 헤더+행을 추출(폼-GET SSR용).
import base64
import json
import os
import sys
import threading
import time
from urllib.request import urlopen

CDP_PORT = int(os.getenv("SUPPLIER_CDP_PORT", "9223"))
CDP = f"http://localhost:{CDP_PORT}"
PROFILE = os.path.expanduser("~/.ohisell_supplier_chrome")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
START_URL = "https://supplier.coupang.com"
OUT = os.path.expanduser("~/.ohisell_supplier_recon.jsonl")
_SKIP_EXT = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
             ".woff", ".woff2", ".ttf", ".ico", ".map")
_SKIP_HINT = ("/log", "/tr?", "collector", "beacon", "analytics", "sentry",
              "/event", "px.coupang", "image.coupang", "static.coupang", "assets",
              "wcs.coupang", "ljc.coupang")


def cmd_chrome() -> int:
    import subprocess
    if not os.path.exists(CHROME):
        print(f"Chrome 없음: {CHROME}", file=sys.stderr)
        return 1
    os.makedirs(PROFILE, exist_ok=True)
    proc = subprocess.Popen(
        [CHROME, f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={PROFILE}",
         "--no-first-run", "--no-default-browser-check", START_URL],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print(f"Chrome 실행됨 (PID {proc.pid}, CDP {CDP_PORT}, 프로필 {PROFILE})")
    print("  1) 브라우저에서 supplier.coupang.com 로그인")
    print("  2) capture(JSON API) 또는 dom(정산 SSR) 모드로 데이터 형태 캡처")
    return 0


def _short(s, n=8000):
    if s is None:
        return None
    s = str(s)
    return s if len(s) <= n else s[:n]


def _keys(text):
    try:
        obj = json.loads(text)
    except Exception:
        return None
    if isinstance(obj, dict):
        return list(obj.keys())
    if isinstance(obj, list):
        return f"[list len={len(obj)}]"
    return type(obj).__name__


def _pages():
    return [t for t in json.load(urlopen(CDP + "/json"))
            if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]


def cmd_capture() -> int:
    """원시 CDP Network 도메인 직결 도청 — JSON API(발주/납품 list 등) 캡처."""
    import websocket  # websocket-client
    fout = open(OUT, "w")
    seen = [0]
    lock = threading.Lock()
    reqmeta = {}

    def emit(method, url, status, mime, rtype, post, keys, sample):
        rec = {"method": method, "url": url.split("?")[0],
               "query": url.split("?")[1] if "?" in url else "",
               "status": status, "mime": (mime or "").split(";")[0], "type": rtype,
               "req_body": _short(post, 2000), "resp_keys": keys, "resp_sample": sample}
        with lock:
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fout.flush()
            seen[0] += 1
            path = url.split("coupang.com")[-1].split("?")[0][:64]
            print(f"[{seen[0]}] {method} {path} ({status}) {rec['mime']} keys={keys}", flush=True)

    def listen(ws_url, title):
        ws = websocket.create_connection(ws_url, max_size=None, suppress_origin=True)
        mid = [0]
        pending = {}

        def send(method, params=None):
            mid[0] += 1
            ws.send(json.dumps({"id": mid[0], "method": method, "params": params or {}}))
            return mid[0]

        send("Network.enable")
        print(f"  ▶ 부착: {title[:30]}", flush=True)
        while True:
            try:
                raw = ws.recv()
            except Exception:
                break
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            m = msg.get("method")
            if m == "Network.requestWillBeSent":
                p = msg["params"]
                r = p.get("request", {})
                reqmeta[p["requestId"]] = {"url": r.get("url", ""), "method": r.get("method", ""), "post": r.get("postData")}
            elif m == "Network.responseReceived":
                p = msg["params"]
                resp = p.get("response", {})
                rid = p["requestId"]
                url = resp.get("url", "")
                low = url.lower().split("?")[0]
                rtype = p.get("type", "")
                if "coupang.com" not in url:
                    continue
                if rtype not in ("XHR", "Fetch", "Document"):
                    continue
                if any(low.endswith(e) for e in _SKIP_EXT):
                    continue
                if any(h in url.lower() for h in _SKIP_HINT):
                    continue
                mime = resp.get("mimeType", "")
                meta = reqmeta.get(rid, {})
                want = ("json" in mime) or ("/scm/" in url) or ("/po-web/" in url and "html" in mime)
                if want:
                    rbid = send("Network.getResponseBody", {"requestId": rid})
                    pending[rbid] = {"rid": rid, "url": url, "mime": mime, "meta": meta, "status": resp.get("status")}
                    continue
                emit(meta.get("method") or "", url, resp.get("status"), mime, rtype, meta.get("post"), None, None)
            elif "id" in msg and msg["id"] in pending:
                info = pending.pop(msg["id"])
                res = msg.get("result") or {}
                body = res.get("body", "")
                if res.get("base64Encoded"):
                    try:
                        body = base64.b64decode(body).decode("utf-8", "replace")
                    except Exception:
                        pass
                keys = _keys(body)
                emit(info["meta"].get("method") or "", info["url"], info["status"],
                     info["mime"], "XHR", info["meta"].get("post"), keys, _short(body))

    ts = _pages()
    print(f"원시 CDP 도청 — 페이지 {len(ts)}개 → {OUT}. 발주/납품 클릭+조회. Ctrl+C 종료.", flush=True)
    threads = []
    for t in ts:
        th = threading.Thread(target=listen, args=(t["webSocketDebuggerUrl"], t.get("title", "")), daemon=True)
        th.start()
        threads.append(th)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\n캡처 종료 — {seen[0]}건 → {OUT}")
    fout.close()
    return 0


def cmd_dom(url_kw="settlement") -> int:
    """폼-GET SSR 페이지(정산 등)의 DOM <table>을 Runtime.evaluate 로 추출."""
    import websocket
    ws_url = None
    for t in _pages():
        if url_kw in t.get("url", ""):
            ws_url = t["webSocketDebuggerUrl"]
            break
    if not ws_url and _pages():
        ws_url = _pages()[0]["webSocketDebuggerUrl"]
    if not ws_url:
        print("열린 페이지 없음", file=sys.stderr)
        return 1
    ws = websocket.create_connection(ws_url, suppress_origin=True, max_size=None)
    mid = [0]

    def call(method, params=None):
        mid[0] += 1
        ws.send(json.dumps({"id": mid[0], "method": method, "params": params or {}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == mid[0]:
                return m

    call("Runtime.enable")
    expr = r'''(function(){
      const out={url:location.href,title:document.title,tables:[]};
      document.querySelectorAll('table').forEach((tb,ti)=>{
        const rows=[...tb.querySelectorAll('tr')].map(tr=>[...tr.querySelectorAll('td,th')]
          .map(td=>td.innerText.trim().replace(/\s+/g,' ')));
        if(rows.length) out.tables.push({idx:ti,rowCount:rows.length,rows:rows.slice(0,6)});
      });
      return JSON.stringify(out);
    })()'''
    r = call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    val = r.get("result", {}).get("result", {}).get("value")
    if not val:
        print("DOM 추출 실패:", json.dumps(r)[:400], file=sys.stderr)
        return 1
    o = json.loads(val)
    print("URL:", o["url"][:140])
    print("TITLE:", o["title"])
    for t in o["tables"]:
        print(f"\n=== TABLE #{t['idx']} (행 {t['rowCount']}) ===")
        for row in t["rows"]:
            print("  ", row)
    ws.close()
    return 0


def main():
    arg = sys.argv[1] if len(sys.argv) >= 2 else ""
    if arg == "chrome":
        sys.exit(cmd_chrome())
    if arg == "capture":
        sys.exit(cmd_capture())
    if arg == "dom":
        sys.exit(cmd_dom(sys.argv[2] if len(sys.argv) >= 3 else "settlement"))
    print("usage: rocket_supplier_recon.py [chrome|capture|dom [urlkw]]", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
