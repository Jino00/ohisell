#!/usr/bin/env python3
# rocket_settlement_item_backfill.py — 쿠팡 로켓배송(1P) 계산서 **라인**(입고상세내역)을
#   supplier.coupang.com에서 전량 수집해 prod `coupang_rocket_settlement_item`에 적재하는
#   1회성 백필 스크립트 (D-CPP-20).
#
# 설계·검증은 이미 끝났다(이 파일은 그 결과를 그대로 구현한다 — 새 조사·설계 변경 없음):
#   원천: GET https://supplier.coupang.com/scm/receive/detail (폼-GET SSR HTML)
#   쿼리: vendorPaymentInfoSeq(계산서번호) · cplbYn=N · page(1부터) · totalCount(2페이지부터)
#   페이지 크기: 10행/페이지
#
# ★함정1(페이징): page만 넘기면 20/48행에서 조용히 멈춘다. 1페이지는 totalCount 없이 호출해
#   hidden input(name="totalCount")에서 읽고, 2페이지부터는 그 값을 같이 실어야 전량이 온다.
#   (2026-08-06 라이브 확인: invoice=30608513, hidden totalCount=48, 1페이지 10행 → 5페이지.)
# ★함정2(표 선택): 이 페이지엔 보통 표가 둘이다(요약 표 + 라인 표) — 데이터가 있을 땐
#   **두 번째**(0-based index 1)가 라인 표다. 그런데 **인덱스로 고정하면 깨진다**: 라인 0건인
#   계산서(역발행 차감분 — 아래 참고)는 요약 표 자체가 렌더되지 않아 표가 **1개뿐**이고 그
#   유일한 표가 라인 표라, index=1을 고집하면 못 찾는다(2026-08-06 라이브로 실제 발견한 함정).
#   그래서 인덱스가 아니라 **헤더 토큰("SKU 번호"+"총 단가") 매칭**으로 표를 찾는다 — 표가
#   둘일 때도 결국 같은 표(=index 1)를 집으므로 원 규칙과 결과는 같고, 표가 하나뿐인 0행
#   케이스에서만 견고해진다.
#   실측 헤더(16열, 확정 12열 이후에도 총 공급가액·총 세액·계산서번호·지급일이 더 있다):
#   구분|번호|SKU 번호|SKU 명|입고/반출일자|물류센터|세금타입|수량|단가|공급가액|세액|총 단가|
#   총 공급가액|총 세액|계산서번호|지급일 — 백엔드 파서(clients/coupang/rocket_supplier.py)는
#   헤더 이름 매칭이라 여분 컬럼은 무시하므로 문제 없다.
#
# ★라인 0건 계산서(역발행 차감분): 판정 완료(코디네이터, docs/references/44_*.md §8-3) —
#   결함이 아니라 원천의 성격이다. 쿠팡이 역발행 차감분을 SKU 단위로 쪼개 주지 않으므로
#   `/scm/receive/detail`엔 애초에 라인이 없다. 백엔드 매출귀속(`rocket_1p_channel_pnl.py`)의
#   `NOT EXISTS(라인)` 폴백이 이런 계산서를 자동으로 **작성일 기준**으로 잡아 총액을 보존한다.
#   → `total_price=0`이면서 DB `payment_amount<0`인 계산서는 실패도 금액불일치도 아니고
#   **"라인없음(차감계산서, 작성일 폴백)"**으로 따로 분류한다(아래 process_invoice).
#
# CDP 연결: 로켓 supplier 전용 Chrome, 포트 9225(~/.ohisell_rocket_fetcher.json cdp_port).
#   rocket_supplier_recon.py cmd_fetch와 동일한 **원시 websocket + Runtime.evaluate** 패턴을
#   재사용(Playwright 미사용) — 이미 열려 있는 탭의 page-context fetch(credentials:include)를
#   그대로 반복 호출한다(수십~수백 회 호출을 위해 웹소켓 연결을 유지·재사용하도록 확장).
#
# 적재: POST /api/coupang/ops/rocket/settlement-item/ingest (이미 배포됨).
#   body: {"vendor_id","invoice_seq","rows":[[헤더...],[셀...]],"expected_total":<hidden totalCount>}
#   응답: {"lines","expected","truncated","total_price"} — truncated=True는 실패로 다룬다
#   (재시도 목록에 담아 계속 진행. 숫자를 지어내지 않는다).
#
# 세션 자가복구: 응답이 로그인 페이지로 보이면(looksLogin) 또는 표 개수가 모자라면
#   POST /api/coupang/ops/rocket/request-refresh 로 launchd 상주 페처(com.ohisell.rocket)를
#   깨워 SSO/Keychain 경로로 자가복구시킨 뒤, 탭이 살아있는지 폴링해서 재개한다.
#
# 사용:
#   python3 tools/rocket_settlement_item_backfill.py --mode test [--limit 30]   # 시험(최신 N건)
#   python3 tools/rocket_settlement_item_backfill.py --mode all                # 전건(약 487건)
#
# 진행상황·결과는 ~/.ohisell_rocket_settlement_item_backfill/ 아래에 남는다:
#   run.log(전체 로그, tail -f로 진행 확인) · results.jsonl(계산서별 결과 1행씩) ·
#   failed.json(마지막까지 실패한 계산서 목록 — 재시도용).
from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import websocket  # websocket-client — rocket_supplier_recon.py와 동일 의존성

CONFIG_PATH = Path(os.path.expanduser("~/.ohisell_rocket_fetcher.json"))
CDP_PORT = int(os.environ.get("SUPPLIER_CDP_PORT", "9225"))
SUPPLIER_ORIGIN = "https://supplier.coupang.com"
RECEIVE_DETAIL_PATH = "/scm/receive/detail"
INGEST_PATH = "/api/coupang/ops/rocket/settlement-item/ingest"
REQUEST_REFRESH_PATH = "/api/coupang/ops/rocket/request-refresh"
PAGE_SIZE = 10
REQUEST_INTERVAL_S = 0.2  # 요구(>=0.15s)보다 여유 있게
KST = timezone(timedelta(hours=9))

STATE_DIR = Path(os.path.expanduser("~/.ohisell_rocket_settlement_item_backfill"))
STATE_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = STATE_DIR / "run.log"
RESULTS_FILE = STATE_DIR / "results.jsonl"
FAILED_FILE = STATE_DIR / "failed.json"

SSH_HOST = "sellc.ohitech.co.kr"
SSH_DB_CMD = (
    "cd /home/ubuntu/ohisell/backend && sqlite3 -noheader -csv ohisell.db "
    "\"SELECT invoice_seq, payment_amount FROM coupang_rocket_settlement "
    "WHERE vendor_id='A01029796' ORDER BY issue_date DESC;\""
)

# 세션 이상으로 간주해 자가복구를 트리거하는 에러 표식
_SESSION_ERR_MARKERS = ("looksLogin", "세션 만료", "라인 표를 찾지 못함", "CDP eval 실패")


def log(msg: str) -> None:
    ts = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def append_result(rec: dict) -> None:
    with open(RESULTS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load_config() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    for k in ("prod_base_url", "ingest_token", "vendor_id"):
        if not cfg.get(k):
            raise SystemExit(f"설정 {CONFIG_PATH}에 '{k}' 없음 — push 불가")
    return cfg


def fetch_invoice_targets() -> list[tuple[int, int]]:
    """prod DB에서 대상 계산서 목록(invoice_seq, payment_amount) — issue_date desc."""
    out = subprocess.run(
        ["ssh", SSH_HOST, SSH_DB_CMD],
        capture_output=True, text=True, timeout=60,
    )
    if out.returncode != 0:
        raise SystemExit(f"prod DB 조회 실패(rc={out.returncode}): {out.stderr[:400]}")
    targets: list[tuple[int, int]] = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        seq_s, amt_s = line.split(",", 1)
        targets.append((int(seq_s), int(round(float(amt_s)))))
    return targets


# ════════════════════════════════════════════════════════════════════
# CDP 원시 websocket — rocket_supplier_recon.py cmd_fetch 패턴을 반복호출용으로 확장
# ════════════════════════════════════════════════════════════════════
def _cdp_pages(port: int) -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5) as r:
        return [t for t in json.load(r) if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]


def _find_supplier_ws(port: int) -> str:
    for t in _cdp_pages(port):
        if "supplier.coupang.com" in (t.get("url") or ""):
            return t["webSocketDebuggerUrl"]
    raise RuntimeError(f"CDP {port}에 supplier.coupang.com 탭이 없음(로그인 필요)")


class CdpEval:
    """CDP 웹소켓 1개를 재사용해 Runtime.evaluate(비동기 page-context fetch)를 반복 호출."""

    def __init__(self, port: int):
        self.port = port
        self.ws = None
        self.mid = 0
        self._connect()

    def _connect(self) -> None:
        ws_url = _find_supplier_ws(self.port)
        self.ws = websocket.create_connection(ws_url, suppress_origin=True, max_size=None, timeout=40)
        self.mid = 0
        self._call("Runtime.enable")

    def _call(self, method: str, params: dict | None = None) -> dict:
        self.mid += 1
        mid = self.mid
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            raw = self.ws.recv()
            if not raw:
                continue
            msg = json.loads(raw)
            if msg.get("id") == mid:
                return msg

    def eval_json(self, expr: str, retries: int = 2):
        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                r = self._call("Runtime.evaluate", {
                    "expression": expr, "awaitPromise": True, "returnByValue": True,
                })
                res = r.get("result", {})
                if "exceptionDetails" in res:
                    raise RuntimeError(f"JS 예외: {json.dumps(res['exceptionDetails'], ensure_ascii=False)[:300]}")
                inner = res.get("result", {})
                if inner.get("value") is None and "value" not in inner:
                    raise RuntimeError(f"CDP 응답에 값 없음: {json.dumps(r, ensure_ascii=False)[:300]}")
                return inner.get("value")
            except Exception as e:  # noqa: BLE001 — 일시 실패 재연결 후 재시도
                last_exc = e
                if attempt < retries:
                    log(f"    CDP eval 실패({attempt}/{retries}) 재연결 후 재시도: {str(e)[:150]}")
                    time.sleep(2)
                    with contextlib.suppress(Exception):
                        self.ws.close()
                    try:
                        self._connect()
                    except Exception as e2:  # noqa: BLE001
                        last_exc = e2
        raise last_exc  # type: ignore[misc]

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.ws.close()


# ── 셀 텍스트 헬퍼: rocket_supplier_fetcher.py의 _CELL_HELPERS_JS와 동일 로직(cellText만) ──
#   자손 텍스트노드를 명시적으로 ' '로 조인(요소 경계 공백을 textContent에 의존하지 않는다).
#   <br>는 분리자로 취급하지 않는다(앞 조각에 붙인다) — 헤더 토큰 계약 보존과 동일 이유.
_CELL_TEXT_JS = r"""
      const cellText = (el) => {
        const parts = [];
        let glue = false;
        const walk = (node) => {
          for (const c of node.childNodes) {
            if (c.nodeType === 3) {
              const raw = c.textContent || '';
              const t = raw.trim();
              if (!t) { if (raw) glue = false; continue; }
              if (glue && parts.length) parts[parts.length - 1] += t;
              else parts.push(t);
              glue = false;
              continue;
            }
            if (c.nodeType !== 1) continue;
            const tag = c.tagName;
            if (tag === 'SCRIPT' || tag === 'STYLE') continue;
            if (tag === 'BR') { glue = true; continue; }
            walk(c);
          }
        };
        walk(el);
        return parts.join(' ').replace(/\s+/g, ' ').trim();
      };
"""


def _build_fetch_expr(path: str) -> str:
    """입고상세내역 SSR HTML fetch → 라인 표(헤더에 'SKU 번호'·'총 단가' 모두 있는 표) 추출 JS.

    ★함정2 원래 기술(index 1=두 번째 표)은 **데이터가 있는** 계산서에서만 성립한다.
      2026-08-06 라이브 확인: totalCount=0(0행)인 계산서는 요약 표(필터 결과 합계)가 아예
      렌더되지 않아 표가 **1개뿐**이고, 그 유일한 표가 라인 표다(값은 없이 헤더+
      "해당하는 검색조건에 대한 결과가 없습니다" 안내행만). index 고정이면 이 케이스에서
      엉뚱한 표를 집거나(표 1개뿐이라 tables[1]이 없어 못 찾음) 실패로 오판된다.
      → 헤더 토큰(SKU 번호·총 단가)으로 표를 찾는다: 표가 2개든 1개든 항상 같은 표를 집고,
      결과적으로 표가 2개일 때는 그게 바로 두 번째 표(index 1)와 동일하다 — 규칙은 그대로,
      표현만 위치(index)가 아니라 내용(헤더)으로 견고화했다.
    반환 형태: {status, header:[..]|null, dataRows:[[셀...],...], totalCount:"48"|null,
               foundTable, looksLogin}.
    """
    return f"""(async () => {{
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 25000);
  try {{
    const r = await fetch({json.dumps(path)}, {{ credentials: 'include', signal: ctrl.signal }});
    const html = await r.text();
    const lower = html.toLowerCase();
    const looksLogin = (lower.includes('login') || lower.includes('signin') || lower.includes('xauth')) &&
                       (lower.includes('password') || lower.includes('passport') || lower.includes('xauth'));
    let header = null;
    let dataRows = [];
    let totalCount = null;
    let foundTable = false;
    try {{
      {_CELL_TEXT_JS}
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const tables = [...doc.querySelectorAll('table')];
      for (const tb of tables) {{
        const trs = [...tb.querySelectorAll('tr')].map(
          tr => [...tr.querySelectorAll('td,th')].map(cellText));
        if (!trs.length) continue;
        const flat = trs[0].map(c => c.replace(/\\s+/g, '')).join('|');
        if (flat.indexOf('SKU번호') >= 0 && flat.indexOf('총단가') >= 0) {{
          foundTable = true;
          header = trs[0];
          dataRows = trs.slice(1);
          break;
        }}
      }}
      const inp = doc.querySelector('input[name="totalCount"]');
      if (inp) totalCount = inp.value;
    }} catch (e) {{ /* 파싱 실패 시 foundTable=false */ }}
    return {{ status: r.status, header, dataRows, totalCount, foundTable, looksLogin }};
  }} finally {{ clearTimeout(t); }}
}})()"""


def fetch_invoice_lines(cdp: CdpEval, invoice_seq: int) -> tuple[list | None, list[list], int | None, str | None]:
    """계산서 1건의 전 페이지 수집. 반환 (header, data_rows, total_count, error)."""
    header: list | None = None
    data: list[list] = []
    total_count: int | None = None
    page_no = 1
    max_pages = 80  # 페이지당 10행 → 최대 800행(안전판). totalCount로 정상 종료가 기본 경로.
    while page_no <= max_pages:
        params = {"vendorPaymentInfoSeq": str(invoice_seq), "cplbYn": "N", "page": str(page_no)}
        if page_no > 1 and total_count is not None:
            params["totalCount"] = str(total_count)
        path = f"{RECEIVE_DETAIL_PATH}?{urllib.parse.urlencode(params)}"
        try:
            res = cdp.eval_json(_build_fetch_expr(path))
        except Exception as e:  # noqa: BLE001
            return header, data, total_count, f"CDP eval 실패: {str(e)[:200]}"
        if not isinstance(res, dict):
            return header, data, total_count, f"CDP 응답 형식 이상: {str(res)[:150]}"
        if res.get("looksLogin"):
            return header, data, total_count, "세션 만료(looksLogin)"
        if res.get("status") != 200:
            return header, data, total_count, f"HTTP {res.get('status')}"
        if not res.get("foundTable"):
            return header, data, total_count, "라인 표를 찾지 못함(페이지 구조 이상 — 세션 의심)"
        if header is None:
            header = [str(c) for c in (res.get("header") or [])]
        if page_no == 1:
            tc_raw = res.get("totalCount")
            try:
                total_count = int(str(tc_raw).strip()) if tc_raw not in (None, "") else None
            except ValueError:
                total_count = None
            if total_count is None:
                return header, data, total_count, "totalCount 히든인풋 없음(절단 위험 — 확정 계약 위반)"
            if total_count == 0:
                # 요약 표가 아예 렌더되지 않는 0행 케이스: 라인 표엔 "결과가 없습니다" 안내행
                # 하나뿐이고 그건 실데이터가 아니므로 더 볼 것 없이 종료.
                break
        page_data = res.get("dataRows") or []
        if not page_data:
            break  # 빈 페이지 = 더 없음
        data.extend(page_data)
        if total_count is not None and len(data) >= total_count:
            break
        time.sleep(REQUEST_INTERVAL_S)
        page_no += 1
    else:
        log(f"    ★invoice={invoice_seq} 페이지 상한({max_pages})에 걸림 — totalCount={total_count}, 수집={len(data)}")
    return header, data, total_count, None


def push_invoice(cfg: dict, invoice_seq: int, header: list | None, data: list[list],
                  total_count: int | None) -> tuple[dict | None, str | None]:
    rows = ([header] + data) if header is not None else []
    body: dict = {"vendor_id": cfg["vendor_id"], "invoice_seq": invoice_seq, "rows": rows}
    if total_count is not None:
        body["expected_total"] = total_count
    try:
        pr = requests.post(
            cfg["prod_base_url"].rstrip("/") + INGEST_PATH,
            json=body, headers={"X-Ingest-Token": cfg["ingest_token"]}, timeout=60,
        )
    except requests.RequestException as e:
        return None, f"push 네트워크 오류: {e}"
    if pr.status_code != 200:
        return None, f"push 실패 HTTP {pr.status_code}: {pr.text[:200]}"
    try:
        return pr.json(), None
    except ValueError:
        return None, f"push 응답 파싱 실패: {pr.text[:200]}"


def process_invoice(cdp: CdpEval, cfg: dict, invoice_seq: int, payment_amount: int) -> dict:
    header, data, total_count, err = fetch_invoice_lines(cdp, invoice_seq)
    if err:
        return {"invoice_seq": invoice_seq, "payment_amount": payment_amount, "ok": False,
                "category": "fetch_failed", "error": err}
    resp, perr = push_invoice(cfg, invoice_seq, header, data, total_count)
    if perr:
        return {"invoice_seq": invoice_seq, "payment_amount": payment_amount, "ok": False,
                "category": "push_failed", "error": perr}
    truncated = bool(resp.get("truncated"))
    lines = resp.get("lines")
    total_price = resp.get("total_price")
    diff = (total_price - payment_amount) if isinstance(total_price, (int, float)) else None
    # ★역발행 차감분(코디네이터 판정, docs/references/44_*.md §8-3): payment_amount<0인 계산서는
    #   쿠팡이 SKU 라인을 아예 안 준다 → lines=0/total_price=0이 **정상**이다. 실패도 금액불일치도
    #   아니라 "라인없음(차감계산서)"로 따로 분류한다(백엔드 NOT EXISTS 폴백이 작성일로 귀속).
    if lines == 0 and payment_amount < 0:
        return {
            "invoice_seq": invoice_seq,
            "payment_amount": payment_amount,
            "ok": True,
            "category": "no_lines_deduction",
            "lines": lines,
            "expected": resp.get("expected"),
            "truncated": truncated,
            "total_price": total_price,
            "diff": diff,
            "error": None,
        }
    return {
        "invoice_seq": invoice_seq,
        "payment_amount": payment_amount,
        "ok": (not truncated) and diff == 0,
        "category": "matched" if (not truncated and diff == 0) else "mismatch",
        "lines": lines,
        "expected": resp.get("expected"),
        "truncated": truncated,
        "total_price": total_price,
        "diff": diff,
        "error": "truncated" if truncated else (None if diff == 0 else "금액불일치"),
    }


# ════════════════════════════════════════════════════════════════════
# 세션 자가복구 — request-refresh 트리거 후 launchd 상주 페처(com.ohisell.rocket)가
#   SSO/Keychain 경로로 복구할 때까지 폴링.
# ════════════════════════════════════════════════════════════════════
def self_heal(cfg: dict) -> bool:
    log("  세션 이상 감지 — request-refresh 트리거")
    try:
        r = requests.post(cfg["prod_base_url"].rstrip("/") + REQUEST_REFRESH_PATH, timeout=30)
        log(f"    request-refresh 응답: {r.status_code} {r.text[:200]}")
    except requests.RequestException as e:
        log(f"    request-refresh 호출 실패: {e}")
        return False
    deadline = time.time() + 240
    while time.time() < deadline:
        time.sleep(10)
        try:
            pages = _cdp_pages(CDP_PORT)
        except Exception as e:  # noqa: BLE001
            log(f"    CDP {CDP_PORT} 미응답(재기동 중일 수 있음): {str(e)[:100]}")
            continue
        for t in pages:
            u = (t.get("url") or "").lower()
            if "supplier.coupang.com" in u and not any(
                x in u for x in ("login", "/auth", "sso", "signin", "passport")
            ):
                log("    세션 복구 확인(탭 URL 정상) — 재개")
                return True
    log("    ★세션 복구 대기 초과(240s) — 수동 확인 필요")
    return False


# ════════════════════════════════════════════════════════════════════
# 배치 실행
# ════════════════════════════════════════════════════════════════════
def run_batch(cfg: dict, invoices: list[tuple[int, int]], label: str) -> tuple[list[dict], list[tuple[int, int]]]:
    cdp = CdpEval(CDP_PORT)
    results: list[dict] = []
    failed: list[tuple[int, int]] = []
    n = len(invoices)
    t0 = time.time()
    for i, (seq, amt) in enumerate(invoices, 1):
        res = process_invoice(cdp, cfg, seq, amt)
        if not res["ok"] and any(m in (res.get("error") or "") for m in _SESSION_ERR_MARKERS):
            if self_heal(cfg):
                with contextlib.suppress(Exception):
                    cdp.close()
                try:
                    cdp = CdpEval(CDP_PORT)
                except Exception as e:  # noqa: BLE001
                    log(f"    자가복구 후 CDP 재연결 실패: {str(e)[:150]}")
                res = process_invoice(cdp, cfg, seq, amt)  # 재시도 1회
            else:
                log("    자가복구 실패 — 남은 건은 재시도 목록으로 이월하며 계속 진행")
        results.append(res)
        append_result(res)
        if not res["ok"]:
            failed.append((seq, amt))
        elapsed = time.time() - t0
        noteworthy = (not res["ok"]) or i == 1 or i == n or i % 10 == 0
        if noteworthy:
            log(f"[{label} {i}/{n}] invoice={seq} {'OK' if res['ok'] else 'FAIL(' + str(res.get('error')) + ')'} "
                f"lines={res.get('lines')} total_price={res.get('total_price')} payment_amount={amt} "
                f"diff={res.get('diff')} ({elapsed:.0f}s 경과)")
        time.sleep(REQUEST_INTERVAL_S)
    cdp.close()
    return results, failed


def main() -> None:
    ap = argparse.ArgumentParser(description="쿠팡 로켓1P 계산서 라인 백필")
    ap.add_argument("--mode", choices=["test", "all"], default="test")
    ap.add_argument("--limit", type=int, default=30, help="test 모드 대상 건수(최신순)")
    ap.add_argument("--retry-rounds", type=int, default=2, help="all 모드 실패건 재시도 라운드 수")
    args = ap.parse_args()

    cfg = load_config()
    log(f"=== 시작 mode={args.mode} (CDP {CDP_PORT}) ===")
    invoices = fetch_invoice_targets()
    log(f"prod DB 대상 계산서 {len(invoices)}건 확인")

    if args.mode == "test":
        batch = invoices[: args.limit]
        results, failed = run_batch(cfg, batch, "TEST")
        mismatches = [r for r in results if r.get("error") == "금액불일치"]
        log(f"=== TEST 완료 — {len(results)}건 처리 / 실패 {len(failed)}건 / 금액불일치 {len(mismatches)}건 ===")
        for r in results:
            if not r["ok"]:
                log(f"  ★invoice={r['invoice_seq']} error={r.get('error')} "
                    f"total_price={r.get('total_price')} payment_amount={r.get('payment_amount')} diff={r.get('diff')}")
        if failed:
            log("★TEST 실패 있음 — 전건 수집(--mode all) 진행 보류. 원인 확인 필요.")
            sys.exit(1)
        log("TEST 전건 통과 — 전건 수집을 진행해도 됨(--mode all)")
        return

    # mode == all
    results, failed = run_batch(cfg, invoices, "ALL")
    round_no = 1
    while failed and round_no <= args.retry_rounds:
        log(f"=== 재시도 라운드 {round_no}/{args.retry_rounds} — 대상 {len(failed)}건 ===")
        retry_results, failed = run_batch(cfg, failed, f"RETRY{round_no}")
        retried_seqs = {r["invoice_seq"] for r in retry_results}
        results = [r for r in results if r["invoice_seq"] not in retried_seqs] + retry_results
        round_no += 1

    matched = [r for r in results if r.get("category") == "matched"]
    no_lines = [r for r in results if r.get("category") == "no_lines_deduction"]
    mismatches = [r for r in results if r.get("category") == "mismatch"]
    log(f"=== ALL 완료 — 총 {len(results)}건 / 금액일치 {len(matched)}건 / "
        f"라인없음(차감계산서) {len(no_lines)}건 / 금액불일치 {len(mismatches)}건 / 최종실패 {len(failed)}건 ===")
    if failed:
        FAILED_FILE.write_text(json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"최종 실패 목록 저장: {FAILED_FILE}")
        for seq, amt in failed:
            log(f"  ★최종실패 invoice={seq} payment_amount={amt}")
    if no_lines:
        for r in no_lines:
            log(f"  라인없음(차감계산서) invoice={r['invoice_seq']} payment_amount={r.get('payment_amount')} "
                f"(작성일 폴백 — 정상)")
    if mismatches:
        log(f"★금액 불일치(음수 계산서 제외) {len(mismatches)}건:")
        for r in mismatches:
            log(f"  invoice={r['invoice_seq']} total_price={r.get('total_price')} "
                f"payment_amount={r.get('payment_amount')} diff={r.get('diff')}")


if __name__ == "__main__":
    main()
