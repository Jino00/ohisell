# otao_po_export.py — 발주서 PDF 폴더 → 적재 페이로드(JSON). **Mac에서 돈다.**
#
# prod 서버는 Jino의 Google Drive 동기화 폴더를 못 본다. 그래서 PDF를 읽는 일만 여기서 하고,
# DB 쓰기는 `otao_po_import.py`가 prod에서 한다(계약 §4 S1 · `app/services/otao_po/ingest.py`).
#
# 이 스크립트는 **DB를 건드리지 않는다** — 읽기 전용이고, 산출은 JSON 파일 하나다.
# 그 파일 자체가 근거 보존물이다: 무엇을 심었는지 나중에 파일로 되짚을 수 있다.
#
#   pypdf가 필요하다:  python3 -m venv .venv && .venv/bin/pip install pypdf==6.16.1
#
#   .venv/bin/python scripts/otao_po_export.py \
#       --folder "/Users/jino/Library/CloudStorage/GoogleDrive-.../1. 발주" \
#       --out /tmp/otao_po_payload.json
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, ".")

from app.services.otao_po.ingest import build_payload  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="발주서 PDF 폴더 → 적재 페이로드(JSON)")
    ap.add_argument("--folder", required=True, help="발주서 PDF 루트 폴더")
    ap.add_argument("--out", required=True, help="쓸 JSON 경로")
    args = ap.parse_args()

    payload = build_payload(args.folder)
    files = payload["files"]
    orders = [f for f in files if f["parsed"] is not None]
    lines = sum(len(f["parsed"]["lines"]) for f in orders)
    mismatch = [
        f["rel"]
        for f in orders
        if f["parsed"]["header_qty"] is not None
        and f["parsed"]["header_qty"] != f["parsed"]["line_qty_sum"]
    ]
    blank = [
        (f["parsed"]["serial"], line["code"])
        for f in orders
        for line in f["parsed"]["lines"]
        if line["qty"] is None
    ]

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)

    print(f"PDF {len(files)}개 → 발주서 {len(orders)}건 / 라인 {lines}")
    print(f"고유 발주번호 {len({f['parsed']['serial'] for f in orders})}")
    print(f"ECOUNT 사본 {sum(1 for f in orders if f['source_kind'] == 'ecount')}건")
    # ★검산 실패·빈 수량은 «건수 0»이어도 찍는다 — 안 찍으면 「본 적 없음」과 구별이 안 된다.
    print(f"수량 검산 불일치 {len(mismatch)}건" + (f": {mismatch}" if mismatch else ""))
    print(f"빈 수량 라인 {len(blank)}건" + (f": {blank}" if blank else ""))
    print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
