"""청구 대상 123 PO의 계산서 연결을 **라이브**로 재확인 (DB 값이 낡았는지 검증).

DB의 vendor_payment_seqs와 supplier 화면의 vendorPaymentList를 PO별로 대조한다.
"""
import csv, json, pathlib, time
from urllib.parse import urlencode
from playwright.sync_api import sync_playwright

SC = pathlib.Path(__file__).parent
ORIGIN = "https://supplier.coupang.com"
FETCH_JS = r"""async (path) => {
  const r = await fetch(path, {credentials:'include', headers:{'Accept':'application/json'}});
  const t = await r.text();
  return {status:r.status, body:t.slice(0,900000)};
}"""

db = {}
dates = {}
for r in csv.reader(open(SC / "po_meta.csv", encoding="utf-8")):
    db[r[0]] = sorted(json.loads(r[3] or "[]"))
claim = {}
src = pathlib.Path("/Users/jino/Library/CloudStorage/GoogleDrive-jino.kim@ohitech.co.kr/"
                   ".shortcut-targets-by-id/1-sXdQoAGFvN14IET1A7DqeUoKD66GJ2K/Ohi/24. 회계/"
                   "coupang_claim_manifests_20260805/_구판_20260805/01_미입고내역_원장대조_20260805.csv")
import io
for r in csv.DictReader(io.open(src, encoding="utf-8-sig")):
    dates[r["발주번호"]] = r["발주일"]

out = []
with sync_playwright() as p:
    br = p.chromium.connect_over_cdp("http://localhost:9225")
    page = br.contexts[0].new_page()
    try:
        page.goto(ORIGIN + "/po-web/purchase/order/list", wait_until="domcontentloaded", timeout=30000)
        for i, po in enumerate(sorted(db), 1):
            d = dates.get(po, "2025-07-01")
            q = urlencode({
                "page": 1, "searchDateType": "PURCHASE_ORDER_DATE",
                "searchStartDate": d, "searchEndDate": d,
                "centerCode": "", "purchaseOrderIdArray": po, "vendorPaymentInfoSeq": "",
                "purchaseOrderStatus": "", "purchaseOrderType": "", "skuIdArray": "",
                "crossdock": "", "transportType": "",
            })
            res = page.evaluate(FETCH_JS, f"/po-web/app/purchase-order/list?{q}")
            if res["status"] != 200:
                out.append({"po": po, "err": f"status {res['status']}"}); continue
            body = json.loads(res["body"]).get("body", {}).get("body", [])
            row = next((x for x in body if str(x.get("purchaseOrderSeq")) == po), None)
            if row is None:
                out.append({"po": po, "err": "행 없음"}); continue
            live = sorted(int(v["vendorPaymentInfoSeq"]) for v in (row.get("vendorPaymentList") or []))
            out.append({
                "po": po, "live": live, "db": db[po],
                "same": live == db[po],
                "status": row.get("purchaseOrderStatusDescription"),
                "order_qty": row.get("sumOfOrderQty"), "recv_qty": row.get("sumOfReceivingQty"),
                "order_amt": row.get("sumOfOrderAmount"), "recv_amt": row.get("sumOfReceivingAmount"),
                "doc": [v.get("documentStatus") for v in (row.get("vendorPaymentList") or [])],
            })
            if i % 20 == 0:
                print(f"  {i}/{len(db)}", flush=True)
            time.sleep(0.15)
    finally:
        page.close(); br.close()

json.dump(out, open(SC / "live_po_check.json", "w"), ensure_ascii=False, indent=1)
err = [x for x in out if x.get("err")]
diff = [x for x in out if not x.get("err") and not x["same"]]
print(f"조회 {len(out)} · 오류 {len(err)} · DB와 불일치 {len(diff)}")
for x in diff[:30]:
    print(f"  PO {x['po']}: live={x['live']} db={x['db']} status={x['status']}")
