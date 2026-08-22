"""수입건 원장 API — D-CPP-48 / 계약 `docs/PLAN_import-cost-ledger.md`.

★**응답에 `response_model`을 쓰지 않는다.** 교훈 #321(2026-08-19): `response_model`이 선언 안 된
키를 HTTP body에서 조용히 지워, 서비스층 테스트 9건이 전부 초록인데 화면엔 배너가 통째로 안 뜨는
사고가 났다. 이 원장은 검산 리포트·배부 결과처럼 «있으면 반드시 보여야 하는» 중첩 필드가 많아
같은 함정의 표면이 넓다. 요청(입력)만 Pydantic으로 검증하고, 응답은
`services/import_cost/ledger.py`의 payload 함수가 만든 dict를 그대로 낸다.
대신 테스트가 **HTTP body를 단언**한다(서비스층 dict만 보면 못 잡는다).

★요청 스키마를 `app/schemas.py`가 아니라 이 파일에 둔다 — 병행 세션이 같은 파일을 건드릴 때
충돌하는 자리라서다(2026-08-07 공유 파일 혼입 사고와 같은 결). 이 라우터 밖에서 쓰이지 않는다.
"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import (
    ImportCostLine,
    ImportDocument,
    ImportInvoiceLine,
    ImportPackingLine,
    ImportShipment,
)
from app.services.import_cost import ledger
from app.services.import_cost.allocator import ALLOCATION_BASES

router = APIRouter(prefix="/api/import-cost", tags=["import-cost"])

# 업로드 상한 — 서류 3종은 합쳐 1MB를 넘지 않는다(8/18 실건 = 521KB).
# 넉넉히 두되 무제한은 아니다: prod 디스크가 빠듯하고 이 테이블은 파일 본문을 담는다.
MAX_DOC_BYTES = 20 * 1024 * 1024
ALLOWED_DOC_TYPES = ("ci", "pl", "expense", "etc")


# ──────────────────────────────────────────────
# 요청 스키마 (입력 검증 전용)
# ──────────────────────────────────────────────
class CostLineIn(BaseModel):
    seq: int
    item_name: str
    supply_amount: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    # ★기본값을 True로 두지 않는다 — 부가세 라인을 실수로 배부에 넣으면 원가가 통째로 부푼다.
    #   호출자가 매 줄 명시하게 한다.
    is_costing: bool
    note: Optional[str] = None


class InvoiceLineIn(BaseModel):
    seq: int
    item_name: str
    quantity: Decimal
    unit_price_foreign: Decimal
    order_no: Optional[str] = None
    line_type: Literal["product", "material", "unknown"] = "unknown"
    internal_sku: Optional[str] = None
    gross_weight_kg: Optional[Decimal] = None
    cbm: Optional[Decimal] = None


class PackingLineIn(BaseModel):
    seq: int
    item_name: str
    quantity: Decimal
    carton_range: Optional[str] = None
    qty_per_carton: Optional[Decimal] = None
    carton_count: Optional[Decimal] = None
    gross_weight_kg: Optional[Decimal] = None
    measure: Optional[str] = None
    cbm: Optional[Decimal] = None
    remark: Optional[str] = None


class ShipmentIn(BaseModel):
    hbl_no: str = Field(min_length=1, max_length=50)
    fx_rate: Decimal = Field(gt=0)
    currency: str = "CNY"
    declaration_no: Optional[str] = None
    declaration_date: Optional[date] = None
    eta: Optional[date] = None
    shipper_name: Optional[str] = None
    invoice_no: Optional[str] = None
    vessel: Optional[str] = None
    declared_inv_value: Optional[Decimal] = None
    customs_value_krw: Optional[Decimal] = None
    carton_count: Optional[int] = None
    gross_weight_kg: Optional[Decimal] = None
    cbm: Optional[Decimal] = None
    allocation_basis: Literal["amount", "weight", "volume", "quantity"] = "amount"
    memo: Optional[str] = None
    cost_lines: list[CostLineIn] = Field(default_factory=list)
    invoice_lines: list[InvoiceLineIn] = Field(default_factory=list)
    packing_lines: list[PackingLineIn] = Field(default_factory=list)


# ──────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────
def _load(db: Session, shipment_id: int) -> ImportShipment:
    try:
        return ledger.get_shipment(db, shipment_id)
    except ledger.LedgerError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


def _guard_draft(ship: ImportShipment) -> None:
    """확정된 건은 편집할 수 없다 — 풀고(reopen) 고쳐야 한다.

    편집을 그냥 허용하면 「확정 시점의 입력」과 「저장된 단가」가 어긋나고, 그 어긋남은
    화면 어디에도 안 보인다. 확정의 의미를 지키는 것이 이 가드의 전부다.
    """
    if ship.status == "confirmed":
        raise HTTPException(
            status_code=409,
            detail="확정된 수입건은 편집할 수 없다. 먼저 확정을 풀어라(POST .../reopen).",
        )


def _replace_lines(db: Session, ship: ImportShipment, body: ShipmentIn) -> None:
    """라인을 통째로 교체한다(부분 갱신을 지원하지 않는다).

    왜 통째인가: 서류 한 장이 통째로 다시 오는 도메인이고, 부분 갱신은 «어떤 라인이 남았나»를
    사람이 추적해야 해서 검산의 의미가 흐려진다.
    """
    for coll in (ship.cost_lines, ship.invoice_lines, ship.packing_lines):
        for row in list(coll):
            db.delete(row)
    db.flush()

    for c in body.cost_lines:
        db.add(ImportCostLine(shipment_id=ship.id, **c.model_dump()))
    for i in body.invoice_lines:
        db.add(ImportInvoiceLine(shipment_id=ship.id, **i.model_dump()))
    for p in body.packing_lines:
        db.add(ImportPackingLine(shipment_id=ship.id, **p.model_dump()))
    db.flush()


def _apply_head(ship: ImportShipment, body: ShipmentIn) -> None:
    for field in (
        "hbl_no", "fx_rate", "currency", "declaration_no", "declaration_date", "eta",
        "shipper_name", "invoice_no", "vessel", "declared_inv_value", "customs_value_krw",
        "carton_count", "gross_weight_kg", "cbm", "allocation_basis", "memo",
    ):
        setattr(ship, field, getattr(body, field))


# ──────────────────────────────────────────────
# 엔드포인트
# ──────────────────────────────────────────────
@router.get("/shipments")
def list_shipments(
    db: Session = Depends(get_db),
    limit: int = Query(100, ge=1, le=500),
    status: Optional[Literal["draft", "confirmed"]] = None,
):
    q = db.query(ImportShipment).options(
        selectinload(ImportShipment.invoice_lines),
        selectinload(ImportShipment.documents),
    )
    if status:
        q = q.filter(ImportShipment.status == status)
    rows = q.order_by(ImportShipment.id.desc()).limit(limit).all()
    return {
        "items": [ledger.shipment_payload(s, detail=False) for s in rows],
        "count": len(rows),
    }


@router.post("/shipments", status_code=201)
def create_shipment(body: ShipmentIn, db: Session = Depends(get_db)):
    dup = db.query(ImportShipment).filter(ImportShipment.hbl_no == body.hbl_no).first()
    if dup is not None:
        raise HTTPException(status_code=409, detail=f"HBL {body.hbl_no}은 이미 등록돼 있다(id={dup.id}).")
    ship = ImportShipment(status="draft", hbl_no=body.hbl_no, fx_rate=body.fx_rate)
    _apply_head(ship, body)
    db.add(ship)
    db.flush()
    _replace_lines(db, ship, body)
    db.commit()
    return ledger.shipment_payload(_load(db, ship.id))


@router.get("/shipments/{shipment_id}")
def get_shipment(shipment_id: int, db: Session = Depends(get_db)):
    return ledger.shipment_payload(_load(db, shipment_id))


@router.put("/shipments/{shipment_id}")
def update_shipment(shipment_id: int, body: ShipmentIn, db: Session = Depends(get_db)):
    ship = _load(db, shipment_id)
    _guard_draft(ship)
    other = (
        db.query(ImportShipment)
        .filter(ImportShipment.hbl_no == body.hbl_no, ImportShipment.id != shipment_id)
        .first()
    )
    if other is not None:
        raise HTTPException(status_code=409, detail=f"HBL {body.hbl_no}은 다른 건(id={other.id})의 것이다.")
    _apply_head(ship, body)
    _replace_lines(db, ship, body)
    db.commit()
    return ledger.shipment_payload(_load(db, shipment_id))


@router.delete("/shipments/{shipment_id}")
def delete_shipment(shipment_id: int, db: Session = Depends(get_db)):
    ship = _load(db, shipment_id)
    _guard_draft(ship)
    db.delete(ship)
    db.commit()
    return {"deleted": True, "id": shipment_id}


@router.post("/shipments/{shipment_id}/confirm")
def confirm_shipment(shipment_id: int, db: Session = Depends(get_db)):
    """검산 3종을 돌리고 **통과할 때만** 확정한다.

    미통과면 200으로 `confirmed=false` + 리포트를 돌려준다 — 4xx가 아니다.
    왜냐하면 «검산이 걸렸다»는 오류가 아니라 이 API의 정상 산출물이고, 화면은 어느 항목이
    왜 걸렸는지를 그대로 보여줘야 하기 때문이다.
    """
    ship = _load(db, shipment_id)
    if ship.status == "confirmed":
        raise HTTPException(status_code=409, detail="이미 확정된 건이다.")
    try:
        result = ledger.confirm(db, ship)
    except ledger.LedgerError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    if result["confirmed"]:
        db.commit()
    else:
        db.rollback()
    payload = ledger.shipment_payload(_load(db, shipment_id))
    payload["confirm_result"] = result
    return payload


@router.post("/shipments/{shipment_id}/reopen")
def reopen_shipment(shipment_id: int, db: Session = Depends(get_db)):
    ship = _load(db, shipment_id)
    if ship.status != "confirmed":
        raise HTTPException(status_code=409, detail="확정 상태가 아니다.")
    ledger.reopen(db, ship)
    db.commit()
    return ledger.shipment_payload(_load(db, shipment_id))


@router.get("/shipments/{shipment_id}/basis-comparison")
def compare_bases(shipment_id: int, db: Session = Depends(get_db)):
    """배부기준 4종 비교 — D-CPP-48의 근거를 매 건 재현할 수 있게."""
    ship = _load(db, shipment_id)
    return {"bases": list(ALLOCATION_BASES), "comparison": ledger.basis_comparison(ship)}


# ── 원본 서류 (계약 §3: 파일 없이 저장 금지 / 합격기준 ⓔ) ──
@router.post("/shipments/{shipment_id}/documents", status_code=201)
async def upload_document(
    shipment_id: int,
    doc_type: Literal["ci", "pl", "expense", "etc"] = Query(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    ship = _load(db, shipment_id)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="빈 파일이다.")
    if len(data) > MAX_DOC_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"파일이 상한({MAX_DOC_BYTES // 1024 // 1024}MB)을 넘는다: {len(data)} bytes",
        )
    doc = ImportDocument(
        shipment_id=ship.id,
        doc_type=doc_type,
        filename=file.filename or "unnamed",
        content_type=file.content_type,
        size_bytes=len(data),
        content=data,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return {
        "id": doc.id,
        "shipment_id": ship.id,
        "doc_type": doc.doc_type,
        "filename": doc.filename,
        "content_type": doc.content_type,
        "size_bytes": doc.size_bytes,
    }


@router.get("/shipments/{shipment_id}/documents/{document_id}")
def download_document(shipment_id: int, document_id: int, db: Session = Depends(get_db)):
    doc = (
        db.query(ImportDocument)
        .filter(ImportDocument.id == document_id, ImportDocument.shipment_id == shipment_id)
        .first()
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="서류가 없다.")
    # 파일명은 비ASCII(한글·중국어)가 흔하다 — RFC 5987 형식으로 낸다.
    from urllib.parse import quote

    disposition = f"attachment; filename*=UTF-8''{quote(doc.filename)}"
    return StreamingResponse(
        io.BytesIO(doc.content),
        media_type=doc.content_type or "application/octet-stream",
        headers={"Content-Disposition": disposition},
    )


@router.delete("/shipments/{shipment_id}/documents/{document_id}")
def delete_document(shipment_id: int, document_id: int, db: Session = Depends(get_db)):
    ship = _load(db, shipment_id)
    _guard_draft(ship)
    doc = (
        db.query(ImportDocument)
        .filter(ImportDocument.id == document_id, ImportDocument.shipment_id == shipment_id)
        .first()
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="서류가 없다.")
    db.delete(doc)
    db.commit()
    return {"deleted": True, "id": document_id}
