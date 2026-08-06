"""청구 목록 v3 — 기존 159라인에 「쿠팡 정산수량·계산서번호·지급일」을 결합.

핵심: 미입고 주장의 근거를 우리 기록이 아니라 **쿠팡 자신의 정산 근거표**(입고상세내역)로 옮긴다.
검증: 쿠팡 정산수량 == 우리가 입고 원장에서 뽑은 원장입고수량 이어야 한다(불일치는 전량 리포트).
"""
import csv, io, json, collections, pathlib

SC = pathlib.Path(__file__).parent
SRC = pathlib.Path("/Users/jino/Library/CloudStorage/GoogleDrive-jino.kim@ohitech.co.kr/"
                   ".shortcut-targets-by-id/1-sXdQoAGFvN14IET1A7DqeUoKD66GJ2K/Ohi/24. 회계/"
                   "coupang_claim_manifests_20260805")

claim = list(csv.DictReader(io.open(SRC / "01_미입고내역_원장대조_20260805.csv", encoding="utf-8-sig")))

# PO 메타(센터·계산서 목록)
po_center, po_invs = {}, {}
for r in csv.reader(open(SC / "po_meta.csv", encoding="utf-8")):
    po_center[r[0]] = r[1]
    po_invs[r[0]] = json.loads(r[3] or "[]")

# 정산 라인 집계 (발주 구분만; 반출은 별도 발주번호라 자연히 제외)
qty = collections.Counter()
amt = collections.Counter()
inv_of = collections.defaultdict(set)
pay_of = collections.defaultdict(set)
for r in csv.DictReader(open(SC / "settle_lines.csv", encoding="utf-8")):
    if r["구분"] != "발주":
        continue
    k = (r["발주번호"], r["SKU번호"])
    qty[k] += int(r["수량"].replace(",", ""))
    amt[k] += int(r["총단가"].replace(",", ""))
    inv_of[k].add(r["계산서번호"])
    pay_of[k].add(r["지급일"])

out, mismatch, no_invoice = [], [], []
for r in claim:
    po, sku = r["발주번호"], r["상품번호"]
    k = (po, sku)
    has_inv = bool(po_invs.get(po))
    settled = qty.get(k, 0)
    ledger = int(r["원장입고수량"])
    ship = int(r["발송수량"])
    unit = int(r["매입단가"])
    if not has_inv:
        status = "계산서 미발행(전량 미정산)"
        no_invoice.append(r)
    elif k not in qty:
        status = "정산명세에 없음(전량 미정산)"
    else:
        status = "정산됨(일부)"
    if settled != ledger:
        mismatch.append((po, sku, ledger, settled))
    out.append({
        "발주일": r["발주일"], "발주번호": po, "물류센터": po_center.get(po, ""),
        "상품번호": sku, "상품명": r["상품명"],
        "발송수량": ship, "쿠팡정산수량": settled, "미정산수량": ship - settled,
        "매입단가": unit, "미정산금액": (ship - settled) * unit,
        "쿠팡정산금액": amt.get(k, 0),
        "계산서번호": ";".join(sorted(inv_of.get(k, []))),
        "지급일": ";".join(sorted(pay_of.get(k, []))),
        "정산상태": status,
        "쉽먼트번호": r["쉽먼트번호"], "송장번호": r["송장번호"],
        "집하": r["집하"], "센터도착": r["센터도착"], "하차": r["하차"],
        "증거등급": r["증거등급"],
    })

out.sort(key=lambda x: -x["미정산금액"])
with open(SC / "claim_v3.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
    w.writeheader(); w.writerows(out)

tot = sum(x["미정산금액"] for x in out)
old = sum(int(r["미입고금액"]) for r in claim)
print(f"라인 {len(out)} · 미정산금액 합계 {tot:,}원 (구 미입고금액 합계 {old:,}원, 차 {tot-old:,}원)")
print(f"★검증 — 쿠팡정산수량 ≠ 원장입고수량: {len(mismatch)}건")
for m in mismatch[:15]:
    print("   ", m)
print(f"계산서 미발행 PO 라인: {len(no_invoice)}건")
by_grade = collections.Counter()
amt_grade = collections.Counter()
for x in out:
    by_grade[x["증거등급"]] += 1; amt_grade[x["증거등급"]] += x["미정산금액"]
for g in by_grade:
    print(f"   {g}: {by_grade[g]}라인 {amt_grade[g]:,}원")
ech = [x for x in out if x["물류센터"] == "이천4"]
print(f"이천4: {len(ech)}라인 {sum(x['미정산금액'] for x in ech):,}원")
