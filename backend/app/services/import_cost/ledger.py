"""수입건 원장 — DB ↔ 순수 SA 사이의 얇은 층 (D-CPP-48).

산술은 `allocator.py`, 판정은 `reconciler.py`에 있다. **이 모듈은 그 둘을 부르고 결과를 담을 뿐**
자기 산술을 갖지 않는다 — 사본 두 벌은 «감시자가 감시 대상보다 낡는» 형태다(ref 54 §9).

## 확정(confirm)의 규약
1. 검산 3종을 돌린다. **하나라도 통과 못 하면 `status`를 바꾸지 않고 리포트만 돌려준다**
   (계약 §3 금지선: 검산 미통과 건의 「확정」 저장 금지).
2. 통과하면 배부를 계산해 **인보이스 라인에 단가 두 값을 확정 저장**하고 `status='confirmed'`.
3. draft로 되돌리면(`reopen`) 계산 결과를 **지운다** — 낡은 단가가 확정된 값인 척 남는 게
   이 도메인에서 가장 위험하다(stale 증거를 현재로 착각하는 것, 원칙 §2).
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.orm import Session, selectinload

from app.models import (
    ImportCostLine,
    ImportDocument,
    ImportInvoiceLine,
    ImportPackingLine,
    ImportShipment,
)
from app.services.import_cost.allocator import (
    ALLOCATION_BASES,
    AllocationBasis,
    AllocationError,
    CostLine,
    InvoiceLine,
    actual_vat_pool,
    allocate,
    costing_pool,
)
from app.services.import_cost.reconciler import ReconcileReport, reconcile

LINE_TYPES = ("product", "material", "unknown")


class LedgerError(ValueError):
    """호출자 잘못(입력 오류). 라우터가 400으로 옮긴다."""


# ──────────────────────────────────────────────
# 모델 → 순수 값 객체
# ──────────────────────────────────────────────
def _to_cost_lines(rows: list[ImportCostLine]) -> list[CostLine]:
    return [
        CostLine(
            item_name=r.item_name,
            supply_amount=Decimal(r.supply_amount or 0),
            tax_amount=Decimal(r.tax_amount or 0),
            is_costing=bool(r.is_costing),
        )
        for r in rows
    ]


def _to_invoice_lines(rows: list[ImportInvoiceLine]) -> list[InvoiceLine]:
    return [
        InvoiceLine(
            seq=r.seq,
            item_name=r.item_name,
            quantity=Decimal(r.quantity),
            unit_price_foreign=Decimal(r.unit_price_foreign),
            gross_weight_kg=None if r.gross_weight_kg is None else Decimal(r.gross_weight_kg),
            cbm=None if r.cbm is None else Decimal(r.cbm),
        )
        for r in sorted(rows, key=lambda x: x.seq)
    ]


def get_shipment(db: Session, shipment_id: int) -> ImportShipment:
    ship = (
        db.query(ImportShipment)
        .options(
            selectinload(ImportShipment.cost_lines),
            selectinload(ImportShipment.invoice_lines),
            selectinload(ImportShipment.packing_lines),
            selectinload(ImportShipment.documents),
        )
        .filter(ImportShipment.id == shipment_id)
        .first()
    )
    if ship is None:
        raise LedgerError(f"수입건 {shipment_id}이 없다.")
    return ship


# ──────────────────────────────────────────────
# 검산 · 배부
# ──────────────────────────────────────────────
def build_reconcile(ship: ImportShipment, basis: AllocationBasis | None = None) -> tuple[
    ReconcileReport, Any | None, str
]:
    """검산 리포트 + (가능하면) 배부 결과를 만든다. **저장하지 않는다.**

    배부가 원리적으로 불가능하면(중량 결측 등) 배부 결과는 None이고 그 사유가 3번째 값에 담긴다.
    조용히 0을 반환하지 않는다 — 「발견 0건」과 「실행 안 됨」을 구분해야 한다(교훈 #123).
    """
    basis = basis or (ship.allocation_basis or "amount")  # type: ignore[assignment]
    cost_lines = _to_cost_lines(list(ship.cost_lines))
    inv_lines = _to_invoice_lines(list(ship.invoice_lines))
    # ★배부기가 쓰는 것과 **같은 단위**로 맞춘다 — allocate()는 pool을 원 단위로 quantize한다.
    #   초판은 quantize 전 값을 검산에 넘겨 「기대 661620.00 vs 실제 661620」이 뜰 수 있었다
    #   (적대 리뷰 P1-1 부수 지적).
    pool = costing_pool(cost_lines).quantize(Decimal("1"), ROUND_HALF_UP)
    has_costing_lines = any(c.is_costing for c in cost_lines)

    result = None
    error = ""
    if inv_lines:
        try:
            result = allocate(inv_lines, cost_lines, Decimal(ship.fx_rate), basis)
        except AllocationError as exc:
            error = str(exc)

    report = reconcile(
        invoice_rows=[(r.item_name, Decimal(r.quantity)) for r in ship.invoice_lines],
        packing_rows=[(r.item_name, Decimal(r.quantity)) for r in ship.packing_lines],
        invoice_total_foreign=(
            sum((Decimal(r.quantity) * Decimal(r.unit_price_foreign) for r in ship.invoice_lines),
                Decimal("0"))
            if ship.invoice_lines
            else None
        ),
        declared_inv_value=(
            None if ship.declared_inv_value is None else Decimal(ship.declared_inv_value)
        ),
        pool_krw=(result.pool_krw if result else pool),
        allocated_total_krw=(result.allocated_total_krw if result else Decimal("0")),
        # ★배부가 «돌았나»와 «돌릴 원료가 있었나»를 검산에 넘긴다 — 이걸 안 넘겨서
        #   `0 == 0`이 통과로 접히던 것이 적대 리뷰 P1-1이다.
        allocation_ran=result is not None,
        has_costing_lines=has_costing_lines,
    )
    return report, result, error


def basis_comparison(ship: ImportShipment) -> list[dict]:
    """배부기준 4종을 전부 돌려 나란히 보여준다.

    D-CPP-48이 «금액»을 고른 근거가 「다른 기준과 0.3% 이내로 수렴하고 수량만 왜곡된다」였다.
    그 근거를 **화면에서 매 건 재현**할 수 있어야 결정이 검증 가능하다 — 그래서 남긴다.
    """
    out = []
    cost_lines = _to_cost_lines(list(ship.cost_lines))
    inv_lines = _to_invoice_lines(list(ship.invoice_lines))
    for basis in ALLOCATION_BASES:
        try:
            r = allocate(inv_lines, cost_lines, Decimal(ship.fx_rate), basis)
        except AllocationError as exc:
            out.append({"basis": basis, "available": False, "reason": str(exc), "lines": []})
            continue
        out.append(
            {
                "basis": basis,
                "available": True,
                "reason": "",
                "unallocated_krw": str(r.unallocated_krw),
                "lines": [
                    {
                        "seq": ln.seq,
                        "item_name": ln.item_name,
                        "allocated_cost_krw": str(ln.allocated_cost_krw),
                        "unit_cost_ex_vat": str(ln.unit_cost_ex_vat),
                    }
                    for ln in r.lines
                ],
            }
        )
    return out


def confirm(db: Session, ship: ImportShipment) -> dict:
    """검산 통과 시에만 확정. 실패하면 상태를 **바꾸지 않고** 리포트를 돌려준다."""
    report, result, error = build_reconcile(ship)
    if not report.passed or result is None:
        return {
            "confirmed": False,
            "reason": error or "검산 미통과",
            "reconcile": _report_payload(report),
        }

    by_seq = {ln.seq: ln for ln in result.lines}
    for row in ship.invoice_lines:
        calc = by_seq.get(row.seq)
        if calc is None:  # pragma: no cover - allocate가 전 라인을 돌려주므로 도달 불가
            raise LedgerError(f"배부 결과에 seq={row.seq} 라인이 없다 — 산술 결함이다.")
        row.goods_amount_krw = calc.goods_amount_krw
        row.allocated_cost_krw = calc.allocated_cost_krw
        row.unit_cost_ex_vat = calc.unit_cost_ex_vat
        row.unit_cost_inc_vat = calc.unit_cost_inc_vat

    ship.status = "confirmed"
    ship.confirmed_at = datetime.now()
    db.flush()
    return {"confirmed": True, "reason": "", "reconcile": _report_payload(report)}


def reopen(db: Session, ship: ImportShipment) -> None:
    """확정을 풀고 **계산 결과를 지운다.**

    낡은 단가가 「확정된 값」인 척 남는 것이 이 도메인에서 가장 위험하다 — 확정 시점의 입력이
    바뀌었는데 결과만 남으면 그건 stale 증거다.
    """
    ship.status = "draft"
    ship.confirmed_at = None
    for row in ship.invoice_lines:
        row.goods_amount_krw = None
        row.allocated_cost_krw = None
        row.unit_cost_ex_vat = None
        row.unit_cost_inc_vat = None
    db.flush()


# ──────────────────────────────────────────────
# 직렬화 (라우터가 쓴다 — Decimal은 전부 str로 낸다)
# ──────────────────────────────────────────────
def _d(v) -> str | None:
    return None if v is None else str(v)


def _report_payload(report: ReconcileReport) -> dict:
    return {
        "passed": report.passed,
        "checks": [
            {
                "key": c.key,
                "label": c.label,
                "status": c.status,
                "passed": c.passed,
                "expected": _d(c.expected),
                "actual": _d(c.actual),
                "detail": c.detail,
                "rows": c.rows,
            }
            for c in report.checks
        ],
    }


def shipment_payload(ship: ImportShipment, *, detail: bool = True) -> dict:
    """수입건 1건의 응답 본문.

    ★`response_model`을 쓰지 않고 dict를 그대로 낸다 — 교훈 #321(2026-08-19): `response_model`이
    선언 안 된 키를 **HTTP body에서 조용히 지워** 서비스층 테스트는 초록인데 화면엔 아무것도
    안 뜨는 사고가 났다. 이 원장은 검산 리포트처럼 «있으면 반드시 보여야 하는» 필드가 많아
    같은 함정을 원천 차단한다. 대신 테스트가 **HTTP body를 단언**한다.
    """
    head = {
        "id": ship.id,
        "hbl_no": ship.hbl_no,
        "declaration_no": ship.declaration_no,
        "declaration_date": ship.declaration_date.isoformat() if ship.declaration_date else None,
        "eta": ship.eta.isoformat() if ship.eta else None,
        "shipper_name": ship.shipper_name,
        "invoice_no": ship.invoice_no,
        "vessel": ship.vessel,
        "currency": ship.currency,
        "fx_rate": _d(ship.fx_rate),
        "remittance_fx_rate": _d(ship.remittance_fx_rate),
        "declared_inv_value": _d(ship.declared_inv_value),
        "customs_value_krw": _d(ship.customs_value_krw),
        "carton_count": ship.carton_count,
        "gross_weight_kg": _d(ship.gross_weight_kg),
        "cbm": _d(ship.cbm),
        "allocation_basis": ship.allocation_basis,
        "status": ship.status,
        "memo": ship.memo,
        "confirmed_at": ship.confirmed_at.isoformat() if ship.confirmed_at else None,
        "line_count": len(ship.invoice_lines),
        "document_count": len(ship.documents),
    }
    if not detail:
        return head

    cost_lines = _to_cost_lines(list(ship.cost_lines))
    report, result, error = build_reconcile(ship)
    head.update(
        {
            "cost_lines": [
                {
                    "id": r.id,
                    "seq": r.seq,
                    "item_name": r.item_name,
                    "supply_amount": _d(r.supply_amount),
                    "tax_amount": _d(r.tax_amount),
                    "is_costing": bool(r.is_costing),
                    "note": r.note,
                }
                for r in sorted(ship.cost_lines, key=lambda x: x.seq)
            ],
            "invoice_lines": [
                {
                    "id": r.id,
                    "seq": r.seq,
                    "order_no": r.order_no,
                    "item_name": r.item_name,
                    "quantity": _d(r.quantity),
                    "unit_price_foreign": _d(r.unit_price_foreign),
                    "line_type": r.line_type,
                    "internal_sku": r.internal_sku,
                    "gross_weight_kg": _d(r.gross_weight_kg),
                    "cbm": _d(r.cbm),
                    # 확정 전엔 null이다 — 0으로 채우지 않는다(0=미계산 혼동 금지)
                    "goods_amount_krw": _d(r.goods_amount_krw),
                    "allocated_cost_krw": _d(r.allocated_cost_krw),
                    "unit_cost_ex_vat": _d(r.unit_cost_ex_vat),
                    "unit_cost_inc_vat": _d(r.unit_cost_inc_vat),
                }
                for r in sorted(ship.invoice_lines, key=lambda x: x.seq)
            ],
            "packing_lines": [
                {
                    "id": r.id,
                    "seq": r.seq,
                    "carton_range": r.carton_range,
                    "item_name": r.item_name,
                    "quantity": _d(r.quantity),
                    "qty_per_carton": _d(r.qty_per_carton),
                    "carton_count": _d(r.carton_count),
                    "gross_weight_kg": _d(r.gross_weight_kg),
                    "measure": r.measure,
                    "cbm": _d(r.cbm),
                    "remark": r.remark,
                }
                for r in sorted(ship.packing_lines, key=lambda x: x.seq)
            ],
            "documents": [
                {
                    "id": d.id,
                    "doc_type": d.doc_type,
                    "filename": d.filename,
                    "content_type": d.content_type,
                    "size_bytes": d.size_bytes,
                    "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
                }
                for d in sorted(ship.documents, key=lambda x: x.id)
            ],
            "reconcile": _report_payload(report),
            "allocation": (
                None
                if result is None
                else {
                    "basis": result.basis,
                    "pool_krw": _d(result.pool_krw),
                    "allocated_total_krw": _d(result.allocated_total_krw),
                    "unallocated_krw": _d(result.unallocated_krw),
                    "lines": [
                        {
                            "seq": ln.seq,
                            "item_name": ln.item_name,
                            "quantity": _d(ln.quantity),
                            "goods_amount_krw": _d(ln.goods_amount_krw),
                            "allocated_cost_krw": _d(ln.allocated_cost_krw),
                            "unit_cost_ex_vat": _d(ln.unit_cost_ex_vat),
                            "unit_cost_inc_vat": _d(ln.unit_cost_inc_vat),
                        }
                        for ln in result.lines
                    ],
                }
            ),
            "allocation_error": error,
            # ★참고값 — 배부에 쓰이지 않는다. ×1.1 규약과 실제 세액의 차이를 화면에서 볼 수 있게.
            "actual_vat_krw": _d(actual_vat_pool(cost_lines)),
        }
    )
    return head
