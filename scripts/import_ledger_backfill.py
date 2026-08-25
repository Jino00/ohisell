#!/usr/bin/env python3
"""OTAO 수입 서류 폴더 → 수입건 원장 적재 (D-CPP-57 · 계약 `CONTRACT_import_ledger_backfill.md`).

## 무엇을 하나

Google Drive의 결산 폴더(`2. IV & PL/<YYYYMMDD> 입금건`)를 훑어 **CI · PL · 자금정산서**를
짝지어 수입건 1건씩을 만든다. 결과는 `POST /api/import-cost/shipments`로 들어가고 상태는
**draft**다 — 확정(confirm)은 사람이 화면에서 한다(계약 A′ §2-2와 같은 규율).

    폴더 훑기 → 파싱 → 짝짓기(결제금액 CNY) → 관세율 규칙 검산 → payload → POST(draft)

## 왜 Mac에서 도나

그 폴더는 **이 Mac에만 마운트**돼 있다(`~/Library/CloudStorage/GoogleDrive-…`) — prod(OCI)는
못 본다. 그래서 이 스크립트는 Mac launchd 잡이고 **Mac이 깨어 있어야** 돈다(2026-08-23 확정).
동종 잡 7개가 이미 같은 조건으로 돈다(`com.ohisell.*`).

## 안전선

- **기본이 예행이다.** `--apply` 없이는 아무것도 안 쓴다. 자동이 조용하면 방치이므로
  예행 출력이 곧 「무엇이 들어갈 것인가」의 사후 가시성 재료다.
- **멱등**: 이미 있는 HBL은 건너뛴다(API가 409로 거절하는 것에 기대지 않고 먼저 조회한다 —
  409를 실패로 세면 매일 도는 잡의 로그가 빨갛게 물들어 진짜 실패가 묻힌다).
- **빈칸을 0으로 채우지 않는다.** 못 읽은 값은 payload에서 빼고 «모름»으로 남긴다.
- **관세율은 검산을 통과할 때만** 채운다(`duty_rules.verify_against_document`).
  어긋나면 그 수입건은 관세율 없이 올리고 사유를 memo에 적는다.
- **OCR 없음**: 스캔본은 「글자 없음」으로 남기고 사람에게 넘긴다.

## 쓰기

    python3 scripts/import_ledger_backfill.py                 # 예행(기본)
    python3 scripts/import_ledger_backfill.py --apply         # 실제 적재
    python3 scripts/import_ledger_backfill.py --since 20260121 --json out.json

prod API는 공개 HTTPS가 IP 허용목록에 막혀 있어 **SSH 경유**로 부른다(`--ssh-host`).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.services.import_cost import duty_rules  # noqa: E402
from app.services.import_cost.parser import (  # noqa: E402
    CustomsDocParseError,
    parse_commercial_invoice,
    parse_expense_document,
    parse_packing_list,
)

DEFAULT_ROOT = Path.home() / (
    "Library/CloudStorage/GoogleDrive-jino.kim@ohitech.co.kr/.shortcut-targets-by-id/"
    "1-sXdQoAGFvN14IET1A7DqeUoKD66GJ2K/Ohi/11. 거래업체/1. OTAO_China (필름)/2. IV & PL"
)
#: Jino 지시(2026-08-25): 2025-09~12 4개월은 제외한다. 폴더명 앞 8자리가 결산일이다.
DEFAULT_SINCE = "20260121"
DEFAULT_SSH_HOST = "sellc.ohitech.co.kr"
#: prod 앱은 8011에서 돈다(`uvicorn app.main:app --port 8011`, 2026-08-25 실측).
#: 공개 HTTPS는 이 머신 IP가 허용목록 밖이라 401이므로 SSH로 localhost를 부른다.
DEFAULT_API = "http://localhost:8011/api/import-cost"

_ZERO = Decimal("0")


def nfc(s: str) -> str:
    """macOS 파일명은 NFD다 — NFC로 접어야 한글 매칭이 된다.

    실측(2026-08-25): `"수입신고필증" in p.name`이 **전건 False**였다. 파일이 눈앞에 있는데
    0건으로 읽히는 종류의 버그라 원인을 찾기 어렵다.
    """
    return unicodedata.normalize("NFC", s)


@dataclass
class Doc:
    folder: str
    name: str
    parsed: object


@dataclass
class Candidate:
    """수입건 1건 후보 — 서류 셋이 짝지어진 상태."""

    expense: Doc
    invoice: Doc | None = None
    packing: Doc | None = None
    notes: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────
# 1. 폴더 훑기
# ──────────────────────────────────────────────
def scan(root: Path, since: str) -> tuple[list[Doc], list[Doc], list[Doc], list[tuple[str, str, str]]]:
    expenses: list[Doc] = []
    invoices: list[Doc] = []
    packings: list[Doc] = []
    skipped: list[tuple[str, str, str]] = []

    if not root.exists():
        raise SystemExit(f"폴더를 찾을 수 없다: {root}")

    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        if folder.name < since:
            continue
        for f in sorted(folder.iterdir()):
            name, suffix = nfc(f.name), f.suffix.lower()
            if suffix == ".pdf":
                try:
                    expenses.append(Doc(folder.name, name, parse_expense_document(f.read_bytes())))
                except CustomsDocParseError as exc:
                    skipped.append((folder.name, name, str(exc)))
                except Exception as exc:  # 파일이 깨졌거나 미다운로드
                    skipped.append((folder.name, name, f"읽기 실패: {exc}"))
            elif suffix in (".xls", ".xlsx"):
                data = f.read_bytes()
                # ★서류 종류를 파일명으로 가르지 않는다 — 같은 CI가 세 가지 이름으로 저장돼
                #   있었다(실측). 파싱이 성공하는 쪽이 그 서류다.
                try:
                    invoices.append(Doc(folder.name, name, parse_commercial_invoice(data)))
                    continue
                except Exception:
                    pass
                try:
                    packings.append(Doc(folder.name, name, parse_packing_list(data)))
                except Exception as exc:
                    skipped.append((folder.name, name, f"CI도 PL도 아니다: {exc}"))
    return expenses, invoices, packings, skipped


# ──────────────────────────────────────────────
# 2. 짝짓기
# ──────────────────────────────────────────────
def match(expenses: list[Doc], invoices: list[Doc], packings: list[Doc]) -> tuple[list[Candidate], list[Doc]]:
    """정산서 ↔ CI를 **결제금액(CNY)**으로 잇는다.

    왜 그 열인가: 정산서에는 invoice_no 칸이 없고 CI에는 HBL 칸이 없다. 두 서류가 공유하는
    유일한 강한 값이 결제금액이다. 실측 7건에서 소수점까지 정확히 일치했고 애매한 짝이 0건이었다.
    ★그래도 «정확히 1건»일 때만 잇는다 — 여럿이면 사람 몫이다(matcher.py와 같은 규율).

    ★★**한 서류는 한 수입건에만 붙는다** (적대 리뷰 1R P1-1, 2026-08-25). 초판은 `used_inv`를
    **채우기만 하고 매칭에서 빼지 않아**, 두 정산서의 결제금액이 우연히 같으면 **같은 CI가 둘 다에**
    붙었다(재현됨). 그러면 다른 화물의 물품 라인이 통째로 원장에 실리고, 관세 검산까지 그 틀린 CI로
    계산돼 `duty_rate`가 **잘못된 근거로 채워진다.** 실제 폴더에서는 값이 전부 유일해 안 터졌지만,
    이 스크립트는 **매일 자동으로 돌 예정**이라 「지금까지 안 터졌다」는 근거가 못 된다.
    PL도 같다 — 두 CI의 수량합이 우연히 같으면 하나의 PL이 둘 다에 붙었다.
    """
    cands: list[Candidate] = []
    seen_hbl: set[str] = set()
    used_inv: set[int] = set()
    used_pack: set[int] = set()

    for ex_doc in expenses:
        ex = ex_doc.parsed
        hbl = getattr(ex, "hbl_no", None)
        if hbl and hbl in seen_hbl:
            continue  # 같은 정산서가 두 폴더에 중복 보관돼 있다(실측: SETR2605170105)
        if hbl:
            seen_hbl.add(hbl)
        cand = Candidate(expense=ex_doc)

        value = getattr(ex, "declared_inv_value", None)
        hits = [
            (i, d)
            for i, d in enumerate(invoices)
            # ★이미 다른 수입건에 붙은 CI는 후보에서 뺀다(P1-1).
            if i not in used_inv
            and value is not None
            and getattr(d.parsed, "declared_total", None) is not None
            and Decimal(str(d.parsed.declared_total)) == Decimal(str(value))
        ]
        if len(hits) == 1:
            idx, doc = hits[0]
            cand.invoice = doc
            used_inv.add(idx)
            # PL은 수량합으로 고른다(`_pick_packing` 참조). 못 찾아도 적재는 되지만,
            # 원장의 3중 검산 첫 항목이 「CI 수량합 = PL 수량합」이라 **확정이 거부된다**.
            pack_idx = _pick_packing(packings, doc, used_pack)
            if pack_idx is None:
                cand.notes.append("PL 미첨부 — 중량·부피 기준 배부는 할 수 없다(금액 기준은 가능).")
            else:
                cand.packing = packings[pack_idx]
                used_pack.add(pack_idx)
        elif len(hits) > 1:
            cand.notes.append(f"CI 후보 {len(hits)}건 — 자동으로 고르지 않는다. 사람이 확정해야 한다.")
        else:
            cand.notes.append("짝이 되는 CI를 못 찾았다 — 물품 라인 없이 비용만 올린다.")
        cands.append(cand)

    orphan_invoices = [d for i, d in enumerate(invoices) if i not in used_inv]
    return cands, orphan_invoices


def _qty_total(doc: Doc) -> Decimal:
    return sum((Decimal(str(l.quantity)) for l in (getattr(doc.parsed, "lines", []) or [])), _ZERO)


def _pick_packing(packings: list[Doc], invoice: Doc, used: set[int]) -> int | None:
    """CI ↔ PL은 **수량합**으로 잇는다. 이미 쓰인 PL은 `used`로 제외한다(P1-1).

    ★왜 그 값인가: 원장의 3중 검산 첫 항목이 「CI 수량합 = PL 수량합」이다. 그 등식으로
    짝을 고르면 «짝짓기»와 «검산»이 같은 사실을 보므로, 잘못 붙인 PL이 검산을 통과하는
    일이 원리적으로 없다.

    ★초판은 «같은 폴더 + 라인 수 일치»로 골랐고 **16건 중 5건이 안 붙었다**(라이브 실측
    2026-08-25). PL이 CI와 다른 폴더에 있거나(월 경계로 갈린다) 라인 병합 방식이 달라
    라인 수가 어긋나는 경우가 있었기 때문이다. 수량합으로 바꾸니 **16/16이 1:1**이 됐다.
    파일명이 아니라 «내용»으로 잇는다는 점에서 `detect_expense_form`과 같은 결이다.
    """
    want = _qty_total(invoice)
    if want <= _ZERO:
        return None
    hits = [i for i, p in enumerate(packings) if i not in used and _qty_total(p) == want]
    # 여럿이면 고르지 않는다 — 같은 수량의 다른 선적을 붙이면 중량·부피가 통째로 틀린다.
    return hits[0] if len(hits) == 1 else None  # 인덱스다(호출부가 used에 넣는다)


# ──────────────────────────────────────────────
# 3. payload
# ──────────────────────────────────────────────
def _dec(v) -> str | None:
    return None if v is None else str(v)


def build_payload(cand: Candidate) -> dict:
    ex = cand.expense.parsed
    inv = cand.invoice.parsed if cand.invoice else None

    line_amounts: list[tuple[str, Decimal]] = []
    if inv is not None:
        for line in inv.lines:
            amount = Decimal(str(line.quantity)) * Decimal(str(line.unit_price_foreign))
            line_amounts.append((line.item_name, amount))

    document_duty = next(
        (c.supply_amount for c in ex.cost_lines if getattr(c, "is_duty", False)), None
    )
    verdict = duty_rules.verify_against_document(
        line_amounts_foreign=line_amounts,
        customs_value_krw=getattr(ex, "customs_value_krw", None),
        document_duty_krw=document_duty,
    )

    payload: dict = {
        "hbl_no": ex.hbl_no,
        "fx_rate": _dec(ex.fx_rate),
        "currency": ex.currency or "CNY",
        "allocation_basis": "amount",
        "cost_lines": [
            {
                "seq": i + 1,
                "item_name": c.item_name,
                "supply_amount": str(c.supply_amount),
                "tax_amount": str(c.tax_amount),
                "is_costing": c.is_costing,
                "is_duty": c.is_duty,
            }
            for i, c in enumerate(ex.cost_lines)
        ],
        "invoice_lines": [],
        "packing_lines": [],
    }
    for name in (
        "declaration_no", "shipper_name", "vessel",
        "declared_inv_value", "customs_value_krw", "carton_count", "gross_weight_kg", "cbm",
    ):
        v = getattr(ex, name, None)
        if v is not None:
            payload[name] = v if isinstance(v, (int, str)) else _dec(v)
    for name in ("declaration_date", "eta"):
        v = getattr(ex, name, None)
        if v is not None:
            payload[name] = v.isoformat()
    if inv is not None and getattr(inv, "invoice_no", None):
        payload["invoice_no"] = " ".join(str(inv.invoice_no).split())

    if inv is not None:
        for i, line in enumerate(inv.lines):
            row: dict = {
                "seq": i + 1,
                "item_name": line.item_name,
                "quantity": str(line.quantity),
                "unit_price_foreign": str(line.unit_price_foreign),
                "line_type": "material" if duty_rules.is_material(line.item_name) else "product",
            }
            order_no = (inv.order_nos[i] if i < len(inv.order_nos) else None)
            if order_no:
                row["order_no"] = " ".join(str(order_no).split())
            # ★검산을 통과할 때만 관세율을 채운다. 어긋나면 «모름»으로 두는 편이
            #   틀린 세율로 원가를 굳히는 것보다 낫다.
            if verdict.ok:
                row["duty_rate"] = str(duty_rules.duty_rate_for(line.item_name))
            payload["invoice_lines"].append(row)

    if cand.packing is not None:
        for i, line in enumerate(getattr(cand.packing.parsed, "lines", []) or []):
            row = {"seq": i + 1, "item_name": line.item_name, "quantity": str(line.quantity)}
            for name in ("carton_count", "gross_weight_kg", "cbm"):
                v = getattr(line, name, None)
                if v is not None:
                    row[name] = str(v)
            payload["packing_lines"].append(row)

    memo = [f"백필 {cand.expense.folder}/{cand.expense.name}"]
    if cand.invoice:
        memo.append(f"CI {cand.invoice.name}")
    if cand.packing:
        memo.append(f"PL {cand.packing.name}")
    memo.append(("관세율 D-CPP-57 적용 — " if verdict.ok else "관세율 미적용 — ") + verdict.reason)
    memo.extend(cand.notes)
    payload["memo"] = " · ".join(memo)[:1000]
    return payload


# ──────────────────────────────────────────────
# 4. 적재
# ──────────────────────────────────────────────
def _ssh_json(host: str, url: str, method: str = "GET", body: str | None = None) -> object:
    cmd = ["ssh", "-o", "BatchMode=yes", host, f"curl -sS -X {method} '{url}'"]
    if body is not None:
        cmd[-1] += " -H 'Content-Type: application/json' --data-binary @-"
    proc = subprocess.run(cmd, input=body, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"SSH/curl 실패({proc.returncode}): {proc.stderr.strip()[:400]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise SystemExit(f"응답이 JSON이 아니다: {proc.stdout[:400]}")


def existing_shipments(host: str, api: str) -> dict[str, tuple[int, str]]:
    """HBL → (id, status). status를 같이 보는 이유: **확정된 건은 절대 건드리지 않는다.**"""
    data = _ssh_json(host, f"{api}/shipments?limit=500")
    return {
        row["hbl_no"]: (row.get("id"), row.get("status") or "draft")
        for row in (data or {}).get("items", [])
        if row.get("hbl_no")
    }


# ──────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--since", default=DEFAULT_SINCE, help="이 폴더명 이상만 본다(YYYYMMDD)")
    ap.add_argument("--apply", action="store_true", help="실제로 적재한다(없으면 예행)")
    ap.add_argument("--ssh-host", default=DEFAULT_SSH_HOST)
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--refresh-drafts", action="store_true",
                    help="이미 있는 **draft** 수입건을 새 payload로 갱신한다(확정된 건은 안 건드린다)")
    ap.add_argument("--json", type=Path, help="payload를 이 파일에 쓴다")
    args = ap.parse_args()

    expenses, invoices, packings, skipped = scan(args.root, args.since)
    cands, orphans = match(expenses, invoices, packings)
    payloads = [build_payload(c) for c in cands]

    print(f"■ 서류: 정산서 {len(expenses)} · CI {len(invoices)} · PL {len(packings)} · 건너뜀 {len(skipped)}")
    print(f"■ 수입건 후보 {len(payloads)}건 (기준일 {args.since} 이상)\n")

    known: dict[str, tuple[int, str]] = {}
    try:
        known = existing_shipments(args.ssh_host, args.api)
        conf = sum(1 for _, s in known.values() if s == "confirmed")
        print(f"■ 원장에 이미 있는 HBL {len(known)}건 (확정 {conf} · draft {len(known) - conf})\n")
    except SystemExit as exc:
        print(f"⚠️ 기존 원장 조회 실패 — 멱등 확인 없이는 적재하지 않는다: {exc}")
        if args.apply:
            return 2

    created = updated = skippedn = failed = 0
    for p in payloads:
        hbl = p.get("hbl_no") or "[HBL미상]"
        duty = "관세율 ✅" if any("duty_rate" in r for r in p["invoice_lines"]) else "관세율 —"
        head = (
            f"{hbl:<16} fx {p.get('fx_rate'):<9} CNY {p.get('declared_inv_value'):<10} "
            f"물품 {len(p['invoice_lines']):>2}줄 · PL {len(p['packing_lines']):>2}줄 · {duty}"
        )
        if not p.get("hbl_no"):
            print(f"  ❌ {head}  → HBL을 못 읽어 적재하지 않는다")
            failed += 1
            continue

        hit = known.get(hbl)
        if hit is not None:
            ship_id, status = hit
            if status == "confirmed":
                # ★확정된 건은 손대지 않는다. 고쳐야 하면 사람이 먼저 확정을 푼다 —
                #   자동이 확정을 뒤집으면 「확정」이라는 말이 의미를 잃는다.
                print(f"  ⏭️  {head}  → 이미 확정됨(id={ship_id})")
                skippedn += 1
                continue
            if not args.refresh_drafts:
                print(f"  ⏭️  {head}  → 이미 있다(draft id={ship_id}) · 갱신하려면 --refresh-drafts")
                skippedn += 1
                continue
            if not args.apply:
                print(f"  ○ {head}  → 예행: draft id={ship_id} 갱신 예정")
                continue
            res = _ssh_json(
                args.ssh_host, f"{args.api}/shipments/{ship_id}", "PUT",
                json.dumps(p, ensure_ascii=False),
            )
            if isinstance(res, dict) and res.get("id"):
                print(f"  ♻️  {head}  → id={ship_id} 갱신")
                updated += 1
            else:
                print(f"  ❌ {head}  → {str(res)[:200]}")
                failed += 1
            continue

        if not args.apply:
            print(f"  ○ {head}  → 예행(쓰지 않음)")
            continue
        res = _ssh_json(args.ssh_host, f"{args.api}/shipments", "POST", json.dumps(p, ensure_ascii=False))
        if isinstance(res, dict) and res.get("id"):
            print(f"  ✅ {head}  → id={res['id']} (draft)")
            created += 1
        else:
            print(f"  ❌ {head}  → {str(res)[:200]}")
            failed += 1

    if orphans:
        print(f"\n■ 짝이 되는 정산서가 없는 CI {len(orphans)}건 — 통관비를 모른다")
        for d in orphans:
            print(f"   · {d.folder}/{d.name[:56]}  총액 {getattr(d.parsed, 'declared_total', None)}")
    if skipped:
        print(f"\n■ 건너뛴 파일 {len(skipped)}건")
        for folder, name, why in skipped:
            print(f"   · {folder}/{name[:46]} — {why[:80]}")

    if args.json:
        args.json.write_text(json.dumps(payloads, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n■ payload → {args.json}")
    if args.apply:
        print(f"\n■ 적재 {created} · 갱신 {updated} · 건너뜀 {skippedn} · 실패 {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
