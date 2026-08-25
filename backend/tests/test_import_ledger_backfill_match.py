# test_import_ledger_backfill_match.py — 백필 짝짓기 (적대 리뷰 1R P1-1, 2026-08-25)
#
# ★스크립트 전체는 IO·SSH를 쓰므로 단위 테스트 대상이 아니다. **짝짓기 함수만** 잠근다 —
#   거기가 「틀렸을 때 조용히 나쁜 결과를 내는 경로」이기 때문이다: 잘못 붙은 CI/PL은 다른
#   화물의 물품 라인을 원장에 싣고, 관세 검산까지 그 틀린 라인으로 계산돼 `duty_rate`가
#   **잘못된 근거로 채워진다.**
#
# ★1R 실측: 초판은 `used_inv`를 **채우기만 하고 매칭에서 빼지 않아** 같은 CI가 두 수입건에
#   붙었다. 실제 폴더에선 값이 전부 유일해 안 터졌지만, 이 스크립트는 **매일 자동으로 돌 예정**
#   이라 「지금까지 안 터졌다」는 근거가 못 된다.
from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "import_ledger_backfill.py"


@pytest.fixture(scope="module")
def bf():
    """스크립트를 모듈로 적재한다(패키지가 아니라 파일이라 spec으로 연다)."""
    spec = importlib.util.spec_from_file_location("import_ledger_backfill", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _expense(bf, hbl: str, value: str):
    return bf.Doc("f", f"{hbl}.pdf", SimpleNamespace(hbl_no=hbl, declared_inv_value=D(value)))


def _invoice(bf, name: str, total: str, qty: str = "100"):
    lines = [SimpleNamespace(quantity=D(qty), unit_price_foreign=D("1"), item_name="Glass_X")]
    return bf.Doc("f", name, SimpleNamespace(declared_total=D(total), lines=lines, invoice_no=name))


def _packing(bf, name: str, qty: str):
    return bf.Doc("f", name, SimpleNamespace(lines=[SimpleNamespace(quantity=D(qty))]))


def test_one_invoice_is_not_reused_across_shipments(bf):
    """★P1-1: 두 정산서의 결제금액이 같아도 **하나의 CI가 둘 다에 붙지 않는다.**

    붙으면 다른 화물의 물품 라인이 통째로 원장에 실린다.
    """
    expenses = [_expense(bf, "SETR_A", "10820.00"), _expense(bf, "SETR_B", "10820.00")]
    invoices = [_invoice(bf, "CI_A.xls", "10820.00")]

    cands, orphans = bf.match(expenses, invoices, [])

    attached = [c for c in cands if c.invoice is not None]
    assert len(attached) == 1, "같은 CI가 두 수입건에 붙었다 — P1-1 재발"
    loser = next(c for c in cands if c.invoice is None)
    assert any("CI를 못 찾았다" in n for n in loser.notes), loser.notes
    assert orphans == []


def test_one_packing_list_is_not_reused_across_shipments(bf):
    """★P1-1: 두 CI의 **수량합이 같아도** 하나의 PL이 둘 다에 붙지 않는다.

    붙으면 중량·부피가 통째로 틀린 채 배부된다.
    """
    expenses = [_expense(bf, "SETR_A", "100.00"), _expense(bf, "SETR_B", "200.00")]
    invoices = [_invoice(bf, "CI_A.xls", "100.00", qty="500"),
                _invoice(bf, "CI_B.xls", "200.00", qty="500")]
    packings = [_packing(bf, "PL_A.xls", "500")]

    cands, _ = bf.match(expenses, invoices, packings)

    assert all(c.invoice is not None for c in cands), "전제: CI는 각각 정확히 붙어야 한다"
    with_pl = [c for c in cands if c.packing is not None]
    assert len(with_pl) == 1, "같은 PL이 두 수입건에 붙었다 — P1-1 재발"
    loser = next(c for c in cands if c.packing is None)
    assert any("PL 미첨부" in n for n in loser.notes), loser.notes


def test_ambiguous_invoice_is_left_to_a_person(bf):
    """후보가 여럿이면 **고르지 않는다** — 사람 몫으로 넘기고 사유를 남긴다."""
    expenses = [_expense(bf, "SETR_A", "10820.00")]
    invoices = [_invoice(bf, "CI_1.xls", "10820.00"), _invoice(bf, "CI_2.xls", "10820.00")]

    cands, orphans = bf.match(expenses, invoices, [])

    assert cands[0].invoice is None
    assert any("사람이 확정해야 한다" in n for n in cands[0].notes), cands[0].notes
    assert len(orphans) == 2, "고르지 않았으니 둘 다 미배정으로 남아야 한다"


def test_duplicate_settlement_file_makes_only_one_candidate(bf):
    """같은 정산서가 두 폴더에 중복 보관돼도 수입건은 하나다(실측: SETR2605170105)."""
    expenses = [_expense(bf, "SETR_A", "10820.00"), _expense(bf, "SETR_A", "10820.00")]
    cands, _ = bf.match(expenses, [_invoice(bf, "CI_A.xls", "10820.00")], [])
    assert len(cands) == 1


def test_duty_rate_is_only_written_when_verification_passes(bf):
    """★검산을 통과할 때만 `duty_rate`를 payload에 넣는다 — 틀린 세율로 원가를 굳히지 않는다."""
    ex = SimpleNamespace(
        hbl_no="SETR_A", fx_rate=D("220"), currency="CNY",
        customs_value_krw=D("1000000"), declared_inv_value=D("100"),
        declaration_date=None, eta=None, shipper_name=None, vessel=None,
        carton_count=None, gross_weight_kg=None, cbm=None, declaration_no=None,
        cost_lines=[SimpleNamespace(item_name="관세", supply_amount=D("56000"),
                                    tax_amount=D("0"), is_costing=True, is_duty=True)],
    )
    inv = SimpleNamespace(
        declared_total=D("100"), invoice_no="CI_A", order_nos=[None],
        lines=[SimpleNamespace(item_name="Glass_X", quantity=D("100"),
                               unit_price_foreign=D("1"))],
    )
    cand = bf.Candidate(expense=bf.Doc("f", "s.pdf", ex), invoice=bf.Doc("f", "CI_A.xls", inv))

    # 1,000,000 × 5.6% = 56,000 → 서류와 일치하므로 채워진다
    ok = bf.build_payload(cand)
    assert ok["invoice_lines"][0]["duty_rate"] == "0.056"
    assert "D-CPP-57 적용" in ok["memo"]

    # 서류 관세를 어긋나게 바꾸면 **채우지 않는다**
    ex.cost_lines[0] = SimpleNamespace(item_name="관세", supply_amount=D("999999"),
                                       tax_amount=D("0"), is_costing=True, is_duty=True)
    bad = bf.build_payload(cand)
    assert "duty_rate" not in bad["invoice_lines"][0]
    assert "관세율 미적용" in bad["memo"]
