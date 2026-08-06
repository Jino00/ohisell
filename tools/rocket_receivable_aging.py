#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""로켓1P 미정산(확인요청) 라인의 **연령(aging)** 산정 — ref 49 재현 스크립트 (1회성 분석, 2026-08-06).

무엇을 하나
  ① 청구 159라인(`docs/references/data/45c_coupang_unsettled_claim_v3_20260806.csv`)이 **지금도
     미정산인지** prod `coupang_rocket_settlement_item` 전량과 대조해 재판정한다.
  ② 각 라인에 **연령 기준일**을 붙이고 경과일수·버킷을 낸다.

★기준일 우선순위 (ref 49 §2 — 임의로 고른 게 아니라 커버리지를 재고 정한 것):
    ①하차일(한진, 제3자가 확인한 인도 완료)
    ②센터도착일(한진)            ← 하차 기록이 없는 라인용. 하차와 중앙값 0일 차이
    ③같은 PO 정산라인 입고일(쿠팡 자신의 기록)
    ④판정불가                    ← 0이나 추정으로 접지 않는다(원칙22)
  ⚠️`purchase_order.receiving_finished_at`을 정본으로 쓰면 안 된다 — 원천이 최근 90일 창만 주므로
    청구 159라인 중 40건(1,611,220원)에만 닿아 6.4M이 판정불가가 된다.

★증거등급(집하/도착/하차)과 기준일은 **다른 축**이다. 등급은 "갔는가", 기준일은 "언제 갔는가".
  섞으면 ref 45 §17-6("분모에 안 끝난 것이 들어가면 비율이 순위를 만든다")과 같은 오염이 난다.

입력 만들기 (prod에서 뽑아 --workdir 에 둔다):
  ssh sellc.ohitech.co.kr 'cd /home/ubuntu/ohisell/backend && sqlite3 -header -csv ohisell.db "
    select purchase_order_seq po, sku_id sku, kind, sum(qty) qty, sum(total_price) amt,
           min(received_at) recv_min, max(received_at) recv_max
    from coupang_rocket_settlement_item group by purchase_order_seq, sku_id, kind;"' > prod_settle_agg.csv

사용:
  python3 tools/rocket_receivable_aging.py --workdir <dir> \
      --claim docs/references/data/45c_coupang_unsettled_claim_v3_20260806.csv \
      --asof 2026-08-06 --out docs/references/data/49_coupang_unsettled_aging_20260806.csv
"""
from __future__ import annotations

import argparse
import collections
import csv
import pathlib
import sys
from datetime import date

BUCKETS = [(0, 30), (31, 60), (61, 90), (91, 180), (181, 365), (366, 10**9)]
LABELS = ["0~30일", "31~60일", "61~90일", "91~180일", "181~365일", "366일+"]


def read_csv(path: pathlib.Path) -> list[dict]:
    # 원천 CSV는 BOM 포함(엑셀 호환) — utf-8-sig 로 열지 않으면 첫 컬럼명이 깨진다.
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def bucket_of(days: int) -> str:
    for (lo, hi), label in zip(BUCKETS, LABELS):
        if lo <= days <= hi:
            return label
    return "?"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True, help="prod_settle_agg.csv 가 있는 디렉터리")
    ap.add_argument("--claim", required=True, help="청구 159라인 CSV (45c)")
    ap.add_argument("--asof", required=True, help="연령 기준시각 YYYY-MM-DD (KST)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    asof = date.fromisoformat(args.asof)
    wd = pathlib.Path(args.workdir)
    claim = read_csv(pathlib.Path(args.claim))
    agg = read_csv(wd / "prod_settle_agg.csv")

    # (발주번호, SKU) → 쿠팡이 정산한 수량 / 그 PO가 정산에서 본 입고일들
    settled: dict[tuple[str, str], int] = collections.defaultdict(int)
    po_recv: dict[str, list[str]] = collections.defaultdict(list)
    for r in agg:
        if r["kind"] == "반출":          # 반출은 음수 라인이라 정산수량에 더하지 않는다
            continue
        settled[(r["po"], r["sku"])] += int(r["qty"] or 0)
        for k in ("recv_min", "recv_max"):
            if r[k]:
                po_recv[r["po"]].append(r[k][:10])

    rows, changed = [], []
    for c in claim:
        key = (c["발주번호"], c["상품번호"])
        shipped, price = int(c["발송수량"]), int(c["매입단가"])
        old_settled, old_amt = int(c["쿠팡정산수량"]), int(c["미정산금액"])
        new_settled = settled.get(key, 0)
        unsettled = max(shipped - new_settled, 0)
        amt = unsettled * price
        if new_settled != old_settled or amt != old_amt:
            changed.append((key, old_settled, new_settled, old_amt, amt))

        if c["하차"]:
            base, src = c["하차"][:10], "①하차일(제3자)"
        elif c["센터도착"]:
            base, src = c["센터도착"][:10], "②센터도착일(제3자)"
        elif po_recv.get(key[0]):
            base, src = min(po_recv[key[0]]), "③같은PO 정산입고일(쿠팡)"
        else:
            base, src = "", "④판정불가(추적기록 없음)"
        age = (asof - date.fromisoformat(base)).days if base else None

        rows.append(dict(c, 쿠팡정산수량_재판정=new_settled, 미정산금액_재판정=amt,
                         연령기준일=base, 연령기준일출처=src,
                         경과일수=age if age is not None else "",
                         연령버킷=bucket_of(age) if age is not None else "판정불가"))

    # ── 재판정: 금액이 움직이면 그건 발견이다. 조용히 새 숫자를 쓰지 않는다.
    print(f"[재판정] 라인 {len(rows)} · 변동 {len(changed)}건")
    for key, o, n, oa, na in changed:
        print(f"  ⚠️ {key} 정산수량 {o}→{n} · 금액 {oa:,}→{na:,}")
    old_total = sum(int(c["미정산금액"]) for c in claim)
    new_total = sum(int(r["미정산금액_재판정"]) for r in rows)
    print(f"[총액] 종전 {old_total:,} → 현재 {new_total:,} (차 {new_total - old_total:,})")

    def table(title: str, key: str, order: list[str]) -> None:
        agg2 = collections.defaultdict(lambda: [0, 0])
        for r in rows:
            agg2[r[key]][0] += 1
            agg2[r[key]][1] += int(r["미정산금액_재판정"])
        print(f"\n{title}")
        for k in order:
            if k in agg2:
                print(f"  {k:26s} {agg2[k][0]:3d}라인 {agg2[k][1]:>10,}원")

    table("기준일 출처", "연령기준일출처",
          ["①하차일(제3자)", "②센터도착일(제3자)", "③같은PO 정산입고일(쿠팡)", "④판정불가(추적기록 없음)"])
    table("연령 버킷", "연령버킷", LABELS + ["판정불가"])

    ages = [r["경과일수"] for r in rows if r["경과일수"] != ""]
    if ages:
        s = sorted(ages)
        print(f"\n연령 최소 {s[0]} · 중앙값 {s[len(s)//2]} · 최대 {s[-1]}일 (n={len(s)}, as-of {asof} KST)")

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\n→ {out}")
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
