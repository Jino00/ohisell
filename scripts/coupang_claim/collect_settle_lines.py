"""청구 대상 PO가 걸린 계산서의 「입고상세내역」(정산 근거 라인)을 전수 수집.

읽기 전용 정찰: 살아있는 supplier Chrome(CDP 9225) 새 탭에서 page-context fetch만 한다.
출력: settle_lines.csv  (계산서번호, 구분, 발주번호, SKU번호, SKU명, 입고반출일자, 물류센터, 수량, 단가, 총단가, 지급일)
"""
import csv, html, json, pathlib, re, sys, time
from playwright.sync_api import sync_playwright

OUT = pathlib.Path(__file__).parent
ORIGIN = "https://supplier.coupang.com"
FETCH_JS = r"""async (path) => {
  const r = await fetch(path, {credentials:'include', headers:{'Accept':'text/html'}});
  const t = await r.text();
  return {status:r.status, body:t.slice(0,900000)};
}"""

# ── 청구 PO → 계산서 목록 ──
invoices: dict[int, list[int]] = {}
for row in csv.reader(open(OUT / "po_meta.csv", encoding="utf-8")):
    po = int(row[0])
    invoices[po] = json.loads(row[3] or "[]")
targets = sorted({inv for seqs in invoices.values() for inv in seqs})
print(f"청구 PO {len(invoices)}건 → 계산서 {len(targets)}건 수집 시작", flush=True)


def parse(body: str):
    """(총건수, [라인]) — 라인은 셀 리스트. 헤더/합계표는 건너뛴다."""
    total = None
    m = re.search(r'name="totalCount" value="(\d+)"', body)
    if m:
        total = int(m.group(1))
    lines = []
    tables = re.findall(r"<table.*?</table>", body, re.S)
    for tb in tables:
        for r in re.findall(r"<tr.*?</tr>", tb, re.S):
            c = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x))).strip()
                 for x in re.findall(r"<t[dh][^>]*>.*?</t[dh]>", r, re.S)]
            if len(c) < 16 or c[0] in ("구분", ""):
                continue
            lines.append(c)
    return total, lines


rows_out = []
fails = []
with sync_playwright() as p:
    br = p.chromium.connect_over_cdp("http://localhost:9225")
    page = br.contexts[0].new_page()
    try:
        page.goto(ORIGIN + "/scm/settlement/general/purchase/account",
                  wait_until="domcontentloaded", timeout=30000)
        for i, inv in enumerate(targets, 1):
            got, total, page_no = 0, None, 1
            while page_no <= 60:
                path = f"/scm/receive/detail?vendorPaymentInfoSeq={inv}&cplbYn=N&page={page_no}"
                try:
                    res = page.evaluate(FETCH_JS, path)
                except Exception as e:  # noqa: BLE001
                    fails.append((inv, page_no, f"eval:{e}"))
                    break
                if res.get("status") != 200:
                    fails.append((inv, page_no, f"status:{res.get('status')}"))
                    break
                t, lines = parse(res["body"])
                if total is None:
                    total = t
                if not lines:
                    break
                for c in lines:
                    rows_out.append([inv, c[0], c[1], c[2], c[3], c[4], c[5], c[7],
                                     c[8], c[11], c[15] if len(c) > 15 else ""])
                got += len(lines)
                if total is not None and got >= total:
                    break
                page_no += 1
                time.sleep(0.25)
            if total is not None and got != total:
                fails.append((inv, "count", f"{got}/{total}"))
            if i % 10 == 0 or i == len(targets):
                print(f"  {i}/{len(targets)} 계산서 · 누적 {len(rows_out)}라인 · 실패 {len(fails)}", flush=True)
            time.sleep(0.2)
    finally:
        page.close(); br.close()

with open(OUT / "settle_lines.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["계산서번호", "구분", "발주번호", "SKU번호", "SKU명", "입고반출일자",
                "물류센터", "수량", "단가", "총단가", "지급일"])
    w.writerows(rows_out)
print(f"완료 — {len(rows_out)}라인 저장. 실패 {len(fails)}건")
for x in fails[:20]:
    print("  FAIL", x)
