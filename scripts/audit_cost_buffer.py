#!/usr/bin/env python3
"""audit_cost_buffer.py — `product_master.cost_price`가 **원가표 정본**과 어긋나는지 본다 (읽기 전용).

## 왜 있나 (2026-08-10, D-CPP-30)

원가의 정본은 엑셀 `MD_원가 계산_Jino_260807.xlsx`의 「제품 원가표」다. 그런데 DB의
`product_master.cost_price`에 **버퍼가 얹힌 값**이 오래 남아 있었다 — 정본 + 265.3(폰) /
+232.6(도어락·플립) / +96.4(폴드) 같은 **일정한 차이**다.

그 결과 원가가 실제보다 높게 잡혀 **이익이 과소 계상**됐다. 2026-08-10 실측:
전 채널 90일 **+1,012,405원**(스마트스토어 941,659 · 자사몰 61,372 · 쿠팡 9,374) + 로켓1P 186,279원.
★취소·반품·입금전(`REVENUE_EXCLUDED`)은 빼고 센 값이다 — 손익 엔진이 그 주문을 건너뛰므로
  그 원가 과대는 이익에 반영된 적이 없다.
177건을 정본으로 내려 해소했다.

★★**그런데 되돌아올 수 있다.** 2026-08-07 인계본이 이미 경고했다 —
  *"그 엑셀을 지금 그대로 업로드하면 오늘 고친 값이 되돌아간다."*
  옛 매핑 엑셀(`ohisell_mapping_template*.xlsx`)을 올리는 순간 버퍼가 통째로 복귀한다.
  **그리고 그건 조용하다** — 아무 에러도 안 나고 이익만 줄어든다.
  이 검사기가 그 복귀를 잡는다.

## 무엇을 하나

정본 스냅샷(`docs/references/data/cost_truth_20260807.json`)과 DB를 대조해 세 갈래로 가른다:

  · `ok`           — 정본 값과 정확히 일치
  · **`buffered`** — 정본 + **알려진 버퍼**와 일치 → **드리프트. 이게 잡으려는 것.**
  · `undetermined` — 둘 다 아님. 케이스·거치대·강화유리(오타오) 등 원가표가 CNY·환율로
                     따로 계산하는 계열이라 단순 값 대조가 안 된다(08-07 결정: 마스터 값 유지).

★**«판정 불가»를 «정상»으로 세지 않는다.** 셋을 따로 낸다 — 합치면 드리프트가 묻힌다
  (이 프로젝트가 반복해 당한 형태: 발견 0건과 실행 안 됨이 같은 숫자로 보인다).

★**이름 매칭을 하지 않는다.** 정본 항목명과 마스터 상품명은 표기가 달라(「지문방지필름 TPU
  3매」 ↔ 「빛반사, 지문방지 매트 필름 3매, 아이폰에어」) 이름으로 이으면 또 오판한다
  (2026-08-07에 그렇게 36건을 틀렸다). **값 산술만** 본다 — 근거가 좁은 대신 틀리지 않는다.
  그래서 이 검사기는 «어느 상품의 원가가 얼마여야 하나»는 답하지 못한다. 답하는 것은
  «지금 값이 정본 + 알려진 버퍼인가»뿐이다.

## 읽기 전용

`sqlite3.connect(f"file:{path}?mode=ro", uri=True)`로만 연다. INSERT/UPDATE/DELETE 없음.

## 사용

    python3 scripts/audit_cost_buffer.py --db <DB 경로>
    python3 scripts/audit_cost_buffer.py --db <DB> --format json --out drift.json

종료 코드: 드리프트 0건이면 0, 있으면 1 (CI·크론에서 게이트로 쓸 수 있게).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
from collections import defaultdict

# 정본 스냅샷 기본 경로(리포 상대). --truth로 덮을 수 있다.
_DEFAULT_TRUTH = pathlib.Path(__file__).resolve().parents[1] / "docs/references/data/cost_truth_20260807.json"

# 값 비교 허용 오차. 원가는 소수 1자리까지 쓴다(2350.7) — float 왕복 오차만 흡수한다.
_EPS = 0.05


def load_truth(path: pathlib.Path) -> dict:
    snap = json.loads(path.read_text(encoding="utf-8"))
    # ★100원 미만은 제외한다 — 오타오 강화유리 섹션의 CNY 단가(12.2 등)가 섞여 있고,
    #   그걸 원가로 보면 «12.2 + 265.3 = 277.5»류의 헛 매칭이 생긴다.
    snap["_values"] = sorted({i["cost"] for i in snap["items"] if i["cost"] >= 100})
    snap["_name_of"] = {}
    for i in snap["items"]:
        snap["_name_of"].setdefault(i["cost"], f"{i['section']} / {i['item']}")
    return snap


def classify(cost: float, truth: dict) -> tuple[str, dict]:
    """한 원가값을 ok / buffered / undetermined로 가른다."""
    for t in truth["_values"]:
        if abs(cost - t) < _EPS:
            return "ok", {"truth": t, "truth_item": truth["_name_of"][t]}
    for t in truth["_values"]:
        d = round(cost - t, 1)
        for label, buf in truth["known_buffers"].items():
            if abs(d - buf) < _EPS:
                return "buffered", {"truth": t, "buffer": buf, "buffer_label": label,
                                    "truth_item": truth["_name_of"][t]}
    return "undetermined", {}


def scan(db_path: str, truth: dict) -> list[dict]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT internal_sku, product_name, cost_price FROM product_master "
            "WHERE cost_price IS NOT NULL"
        ).fetchall()
    finally:
        con.close()
    out = []
    for sku, name, cp in rows:
        verdict, info = classify(round(float(cp), 1), truth)
        out.append({"internal_sku": sku, "product_name": name,
                    "cost_price": round(float(cp), 1), "verdict": verdict, **info})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", required=True, help="SQLite DB 경로 (읽기 전용으로 연다)")
    ap.add_argument("--truth", default=str(_DEFAULT_TRUTH), help="정본 스냅샷 JSON")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--out", help="출력 파일 (없으면 stdout)")
    a = ap.parse_args()

    truth = load_truth(pathlib.Path(a.truth))
    rows = scan(a.db, truth)
    drift = [r for r in rows if r["verdict"] == "buffered"]

    by_buf: dict[str, list] = defaultdict(list)
    for r in drift:
        by_buf[r["buffer_label"]].append(r)
    counts = {v: sum(1 for r in rows if r["verdict"] == v)
              for v in ("ok", "buffered", "undetermined")}

    if a.format == "json":
        payload = {"source": truth["source_file"], "source_sha256_16": truth["source_sha256_16"],
                   "counts": counts, "drift": drift}
        text = json.dumps(payload, ensure_ascii=False, indent=1)
    else:
        L = [f"원가 정본 대조 — {truth['source_file']} (sha {truth['source_sha256_16']}) · 정본 {len(truth['_values'])}개 값",
             f"  ✅ 정본과 일치        {counts['ok']:5}",
             f"  ⚠️  버퍼가 얹혀 있음   {counts['buffered']:5}   ← 드리프트",
             f"  ·  판정 불가          {counts['undetermined']:5}   (케이스·거치대 등 — 정상일 수 있다)"]
        if drift:
            L.append("")
            L.append("=== 드리프트 상세 (버퍼별) ===")
            for label, items in sorted(by_buf.items(), key=lambda x: -len(x[1])):
                L.append(f"  [{label} +{truth['known_buffers'][label]}] {len(items)}건")
                for r in items[:5]:
                    L.append(f"     {r['internal_sku']:34} {r['cost_price']:>9} → {r['truth']:<9} "
                             f"{(r['product_name'] or '')[:34]}")
                if len(items) > 5:
                    L.append(f"     … 외 {len(items)-5}건")
            L.append("")
            L.append("★옛 매핑 엑셀(ohisell_mapping_template*.xlsx)이 업로드됐을 수 있다 —")
            L.append("  그걸 올리면 2026-08-10에 정본으로 내린 177건이 버퍼로 되돌아간다(08-07 인계본 경고).")
        else:
            L.append("")
            L.append("드리프트 없음 — 마스터가 원가표 정본과 맞다.")
        text = "\n".join(L)

    if a.out:
        pathlib.Path(a.out).write_text(text + "\n", encoding="utf-8")
        print(f"→ {a.out} ({len(drift)}건 드리프트)")
    else:
        print(text)
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
