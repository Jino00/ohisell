# test_cost_menu_ledger_check.py — 단가 행 ↔ 원장 재검사 판정 (순수 SA, 적대 리뷰 1R P1)
#
# ★HTTP 왕복(test_cost_menu_http.py 「P1」 절)이 «네 시나리오가 실제로 그렇게 보이나»를 재고,
#   이 파일은 «판정 자체가 옳나»를 잰다. 둘 다 필요한 이유: 왕복 테스트는 SQLite rowid
#   재사용 같은 **환경 조건**에 기대는데, 판정 규칙은 그 조건 없이도 고정돼야 한다.
from __future__ import annotations

from decimal import Decimal as D

from app.services.cost_menu import ledger_check as LC


def snap(**over) -> LC.PriceSnapshot:
    base = dict(
        source="ledger",
        import_invoice_line_id=15,
        linked_item_name="cleaning kits",
        linked_shipment_id=1,
        unit_price_ex_vat=D("190.82"),
        unit_price_inc_vat=D("209.90"),
    )
    return LC.PriceSnapshot(**{**base, **over})


def fact(**over) -> LC.LedgerFact:
    base = dict(
        shipment_id=1,
        shipment_status="confirmed",
        item_name="cleaning kits",
        unit_cost_ex_vat=D("190.82"),
        unit_cost_inc_vat=D("209.90"),
    )
    return LC.LedgerFact(**{**base, **over})


def test_matching_row_is_ok_and_counts_as_evidence():
    r = LC.check(snap(), fact())
    assert r.status == LC.STATUS_OK
    assert r.ok and r.counts_as_evidence and r.refreshable


def test_missing_line_is_missing_not_changed():
    r = LC.check(snap(), None)
    assert r.status == LC.STATUS_MISSING
    assert not r.counts_as_evidence
    assert not r.refreshable          # 옮겨 올 값 자체가 없다


def test_deleted_shipment_is_missing_even_if_the_line_row_survives():
    """수입건이 사라졌는데 「확정을 다시 해라」라고 말하면 처방이 없는 대상을 가리킨다."""
    assert LC.check(snap(), fact(shipment_status=None)).status == LC.STATUS_MISSING


def test_reopened_shipment_is_unconfirmed():
    r = LC.check(snap(), fact(shipment_status="draft"))
    assert r.status == LC.STATUS_UNCONFIRMED
    assert not r.counts_as_evidence
    assert not r.refreshable          # 확정 전 값을 옮기면 «계산된 적 없는 값»을 쓰는 것이다


def test_changed_value_is_changed_and_refreshable():
    r = LC.check(snap(), fact(unit_cost_ex_vat=D("198.91")))
    assert r.status == LC.STATUS_CHANGED
    assert not r.counts_as_evidence
    assert r.refreshable
    assert r.ledger_unit_price_ex_vat == D("198.91")
    assert "190.82" in r.detail and "198.91" in r.detail


def test_item_mismatch_beats_value_change():
    """★더 근본적인 어긋남이 덜 근본적인 것을 가린다 — 처방이 다르기 때문이다."""
    r = LC.check(snap(), fact(item_name="Glass_iP12promax", unit_cost_ex_vat=D("2921.92")))
    assert r.status == LC.STATUS_ITEM_MISMATCH
    assert not r.refreshable          # 갱신하면 다른 품목의 단가를 삼킨다
    assert "cleaning kits" in r.detail and "Glass_iP12promax" in r.detail


def test_missing_beats_everything():
    assert LC.check(snap(linked_item_name="다른것"), None).status == LC.STATUS_MISSING


def test_item_name_comparison_folds_only_case_and_space():
    """정규화는 계약 B 검산과 같은 폭이다 — «비슷하니 같다»는 추론은 하지 않는다."""
    assert LC.check(snap(), fact(item_name="Cleaning   Kits")).status == LC.STATUS_OK
    assert LC.check(snap(), fact(item_name="cleaning kit")).status == LC.STATUS_ITEM_MISMATCH


def test_legacy_row_without_snapshot_is_not_forced_stale_but_says_so():
    """★품목 대조를 «못 한» 것과 «해서 통과한» 것은 다른 사실이다(계약 §2-7의 결)."""
    r = LC.check(snap(linked_item_name=None), fact(item_name="무엇이든"))
    assert r.status == LC.STATUS_OK
    assert "대조하지 못했다" in r.detail


def test_manual_row_is_not_a_ledger_check_target():
    r = LC.check(snap(source="manual", import_invoice_line_id=None), None)
    assert r.status == LC.STATUS_MANUAL
    assert r.ok and r.counts_as_evidence and not r.refreshable


def test_zero_is_not_none():
    """★0원과 미입력은 다른 사실이다 — 대조에서도 그렇다(계약 §2-7)."""
    assert LC.check(snap(unit_price_ex_vat=D("0")), fact(unit_cost_ex_vat=None)).status == (
        LC.STATUS_CHANGED
    )
    assert LC.check(snap(unit_price_ex_vat=None), fact(unit_cost_ex_vat=D("0"))).status == (
        LC.STATUS_CHANGED
    )


def test_decimal_scale_does_not_create_a_false_mismatch():
    """190.82와 190.820은 같은 값이다 — 자릿수 차이로 「어긋났다」고 하면 오탐이다."""
    assert LC.check(snap(), fact(unit_cost_ex_vat=D("190.8200"))).status == LC.STATUS_OK
