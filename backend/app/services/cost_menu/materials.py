"""부자재 층 — DB ↔ 순수 SA 사이의 얇은 층 (D-CPP-53, 계약 A′ S1).

매칭 판단은 `matcher.py`(순수 SA)에 있다. **이 모듈은 그것을 부르고 결과를 담을 뿐** 자기
매칭 규칙을 갖지 않는다(계약 §2-6).

## 원장 → 단가의 규약

1. **확정(`confirmed`)된 수입건의 라인만** 소비한다. 계약 §9-8: 파서는 계약 B 소관이고,
   A′는 확정 라인만 보므로 파서 실패가 A′의 실패가 되지 않는다. draft 건의 단가는 아직
   «계산된 적 없는 값»이라 링크 대상이 아니다.
2. **단가는 복사한다 — 여기서 다시 산술하지 않는다.** 원장이 이미 확정 저장한
   `unit_cost_ex_vat`·`unit_cost_inc_vat` 두 값을 그대로 옮긴다(계약 B §2-2 계승).
   산술을 한 벌 더 만들면 그 벌이 원장보다 낡는다.
3. **연결은 사람이 한다.** 이 모듈의 `link_ledger_line`은 라우터의 POST에서만 불리고,
   그 POST는 화면의 「연결」 버튼이 부른다. 제안(`matcher.suggest`)은 그 버튼 옆에 이유를
   적어 줄 뿐 스스로 링크를 만들지 않는다(계약 §5-2).
4. **미입력은 «없음»이다.** 단가가 없는 라인은 링크 자체를 거부한다 — 0원짜리 단가 행이
   생기면 그게 「0원인가 미입력인가」 혼동의 재생산이다(계약 §2-7).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session, selectinload

from app.models import (
    CostMaterial,
    CostMaterialPrice,
    CostSetting,
    ImportInvoiceLine,
    ImportShipment,
)
from app.services.cost_menu.matcher import LedgerItem, MaterialRule, Suggestion, suggest

MATERIAL_STATUSES = ("unconfirmed", "approved")
PRICE_SOURCES = ("ledger", "manual")


class CostMenuError(ValueError):
    """호출자 잘못(입력 오류). 라우터가 4xx로 옮긴다."""


class CostMenuConflict(CostMenuError):
    """이미 있는 것을 또 만들려 할 때. 라우터가 409로 옮긴다."""


# ──────────────────────────────────────────────
# 직렬화 (계약 B 관례: `response_model` 없이 dict를 그대로 낸다 — 교훈 #321)
# ──────────────────────────────────────────────
def _d(v) -> str | None:
    """Decimal → 문자열. **None은 None으로 남긴다 — 0으로 접지 않는다**(계약 §2-7)."""
    return None if v is None else str(v)


def _date(v) -> str | None:
    return None if v is None else v.isoformat()


def price_payload(p: CostMaterialPrice) -> dict:
    return {
        "id": p.id,
        "material_id": p.material_id,
        "source": p.source,
        "import_invoice_line_id": p.import_invoice_line_id,
        "supplier": p.supplier,
        "unit_price_ex_vat": _d(p.unit_price_ex_vat),
        "unit_price_inc_vat": _d(p.unit_price_inc_vat),
        "effective_date": _date(p.effective_date),
        "note": p.note,
        # ★로트 좌표를 단가 행에 실어 둔다 — 「이 단가는 어느 수입건에서 왔나」가 화면에서
        #   한 번에 보여야 추적이 끊기지 않는다. 원장 라인이 지워지면 이 행도 CASCADE로 간다.
        "shipment": _shipment_ref(p),
    }


def _shipment_ref(p: CostMaterialPrice) -> dict | None:
    line = p.invoice_line
    if line is None or line.shipment is None:
        return None
    s = line.shipment
    return {
        "id": s.id,
        "hbl_no": s.hbl_no,
        "declaration_date": _date(s.declaration_date),
        "item_name": line.item_name,
        "quantity": _d(line.quantity),
    }


def material_payload(m: CostMaterial, prices: list[CostMaterialPrice]) -> dict:
    """부자재 1종 + 단가 이력.

    ★`latest_price_*`는 **로트가 하나도 없으면 None이다 — 0이 아니다**(계약 §2-7).
    ★`lot_count`를 같이 낸다: 「이 표준의 근거는 로트 N건」을 화면이 말해야 표본 부족이
      숨지 않는다(계약 §9-5).
    """
    ordered = sorted(
        prices,
        key=lambda p: (p.effective_date or date.min, p.id),
        reverse=True,
    )
    latest = ordered[0] if ordered else None
    return {
        "id": m.id,
        "name": m.name,
        "unit": m.unit,
        "category": m.category,
        "status": m.status,
        "excel_label": m.excel_label,
        "match_rule": m.match_rule,
        "form_factor": m.form_factor,
        "part": m.part,
        "note": m.note,
        "lot_count": sum(1 for p in ordered if p.source == "ledger"),
        "price_count": len(ordered),
        "latest_price_ex_vat": _d(latest.unit_price_ex_vat) if latest else None,
        "latest_price_inc_vat": _d(latest.unit_price_inc_vat) if latest else None,
        "latest_price_source": latest.source if latest else None,
        "prices": [price_payload(p) for p in ordered],
    }


def suggestion_payload(s: Suggestion) -> dict:
    return {
        "line_id": s.line_id,
        "item_name": s.item_name,
        "material_id": s.material_id,
        "reason": s.reason,
        "candidates": list(s.candidates),
        "ambiguous": s.is_ambiguous,
        "unmatched": s.is_unmatched,
    }


# ──────────────────────────────────────────────
# 조회
# ──────────────────────────────────────────────
def _materials_query(db: Session):
    return db.query(CostMaterial).options(
        selectinload(CostMaterial.prices)
        .selectinload(CostMaterialPrice.invoice_line)
        .selectinload(ImportInvoiceLine.shipment)
    )


def get_material(db: Session, material_id: int) -> CostMaterial:
    m = _materials_query(db).filter(CostMaterial.id == material_id).first()
    if m is None:
        raise CostMenuError(f"부자재 종 id={material_id}이 없다.")
    return m


def list_materials(db: Session) -> list[dict]:
    rows = _materials_query(db).order_by(CostMaterial.name).all()
    return [material_payload(m, list(m.prices)) for m in rows]


def ledger_material_lines(db: Session) -> list[dict]:
    """확정된 수입건의 `line_type='material'` 라인 전건 + 링크 상태 + 제안.

    ★**미매칭도 결과에 실린다.** 「원장에 부자재 라인이 있는데 어느 종에도 안 붙었다」가
    화면에 안 보이면 단가 이력이 조용히 비어 있게 된다(계약 §5-3 탭1: 「미매칭」 표면화).
    """
    rows = (
        db.query(ImportInvoiceLine)
        .join(ImportShipment, ImportInvoiceLine.shipment_id == ImportShipment.id)
        .options(selectinload(ImportInvoiceLine.shipment))
        .filter(
            ImportInvoiceLine.line_type == "material",
            ImportShipment.status == "confirmed",
        )
        .order_by(ImportShipment.id.desc(), ImportInvoiceLine.seq)
        .all()
    )
    materials = db.query(CostMaterial).order_by(CostMaterial.name).all()
    rules = [
        MaterialRule(material_id=m.id, name=m.name, match_rule=m.match_rule)
        for m in materials
    ]
    sugg = {
        s.line_id: s
        for s in suggest(
            [LedgerItem(line_id=r.id, item_name=r.item_name) for r in rows], rules
        )
    }
    linked = {
        p.import_invoice_line_id: p
        for p in db.query(CostMaterialPrice)
        .filter(CostMaterialPrice.import_invoice_line_id.isnot(None))
        .all()
    }
    by_id = {m.id: m for m in materials}

    out: list[dict] = []
    for r in rows:
        p = linked.get(r.id)
        s = sugg[r.id]
        out.append(
            {
                "line_id": r.id,
                "shipment_id": r.shipment_id,
                "hbl_no": r.shipment.hbl_no,
                "declaration_date": _date(r.shipment.declaration_date),
                "item_name": r.item_name,
                "quantity": _d(r.quantity),
                "unit_cost_ex_vat": _d(r.unit_cost_ex_vat),
                "unit_cost_inc_vat": _d(r.unit_cost_inc_vat),
                "allocated_cost_krw": _d(r.allocated_cost_krw),
                "linked_material_id": p.material_id if p else None,
                "linked_material_name": (
                    by_id[p.material_id].name if p and p.material_id in by_id else None
                ),
                "linked_price_id": p.id if p else None,
                "suggestion": suggestion_payload(s),
            }
        )
    return out


def list_settings(db: Session) -> list[dict]:
    """설정 전건. **`confirmed`를 그대로 낸다** — 화면이 「미확인」을 자백하는 원료다(계약 §9-1)."""
    return [
        {
            "key": s.key,
            "value": s.value,
            "confirmed": bool(s.confirmed),
            "note": s.note,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        }
        for s in db.query(CostSetting).order_by(CostSetting.key).all()
    ]


# ──────────────────────────────────────────────
# 쓰기
# ──────────────────────────────────────────────
def create_material(db: Session, **fields) -> CostMaterial:
    name = (fields.get("name") or "").strip()
    if not name:
        raise CostMenuError("부자재 종 이름이 비었다.")
    dup = db.query(CostMaterial).filter(CostMaterial.name == name).first()
    if dup is not None:
        raise CostMenuConflict(f"「{name}」은 이미 등록돼 있다(id={dup.id}).")
    m = CostMaterial(**{**fields, "name": name})
    db.add(m)
    db.flush()
    return m


def update_material(db: Session, material_id: int, **fields) -> CostMaterial:
    m = get_material(db, material_id)
    if "status" in fields and fields["status"] not in MATERIAL_STATUSES:
        raise CostMenuError(f"알 수 없는 status: {fields['status']}")
    if "name" in fields:
        name = (fields["name"] or "").strip()
        if not name:
            raise CostMenuError("부자재 종 이름이 비었다.")
        other = (
            db.query(CostMaterial)
            .filter(CostMaterial.name == name, CostMaterial.id != material_id)
            .first()
        )
        if other is not None:
            raise CostMenuConflict(f"「{name}」은 다른 종(id={other.id})의 이름이다.")
        fields["name"] = name
    for k, v in fields.items():
        setattr(m, k, v)
    db.flush()
    return m


def link_ledger_line(
    db: Session, material_id: int, line_id: int, note: str | None = None
) -> CostMaterialPrice:
    """원장 라인 1건을 부자재 종에 **사람이 확정 연결**한다 → `source='ledger'` 단가 행 1개.

    거부하는 경우와 그 이유:
    - 라인이 없다 / 부자재 라인이 아니다 → 잘못된 대상이다.
    - 수입건이 확정 전이다 → 단가가 아직 «계산된 적 없는 값»이다(계약 §9-8).
    - 단가가 비어 있다 → 0으로 채우지 않는다(계약 §2-7). 원장을 먼저 확정해야 한다.
    - 이미 이 종에 붙어 있다 → 같은 로트가 두 번 세지면 이력이 거짓말이 된다.
    """
    m = get_material(db, material_id)
    line = (
        db.query(ImportInvoiceLine)
        .options(selectinload(ImportInvoiceLine.shipment))
        .filter(ImportInvoiceLine.id == line_id)
        .first()
    )
    if line is None:
        raise CostMenuError(f"원장 라인 id={line_id}이 없다.")
    if line.line_type != "material":
        raise CostMenuError(
            f"원장 라인 id={line_id}은 부자재(`material`)가 아니라 `{line.line_type}`이다. "
            "원장에서 분류를 먼저 고쳐야 한다."
        )
    if line.shipment is None or line.shipment.status != "confirmed":
        raise CostMenuError(
            f"수입건 id={line.shipment_id}이 확정 전이다 — 확정된 로트의 단가만 쓴다(계약 §9-8)."
        )
    if line.unit_cost_ex_vat is None or line.unit_cost_inc_vat is None:
        raise CostMenuError(
            f"원장 라인 id={line_id}에 단가가 없다 — 0으로 채우지 않는다(계약 §2-7)."
        )
    dup = (
        db.query(CostMaterialPrice)
        .filter(CostMaterialPrice.import_invoice_line_id == line_id)
        .first()
    )
    if dup is not None:
        raise CostMenuConflict(
            f"원장 라인 id={line_id}은 이미 부자재 id={dup.material_id}에 연결돼 있다."
        )

    p = CostMaterialPrice(
        material_id=m.id,
        source="ledger",
        import_invoice_line_id=line.id,
        # 공급처는 원장 shipper에서 자동으로 온다(계약 §5-1 ★공급처).
        supplier=line.shipment.shipper_name,
        # ★복사다 — 여기서 다시 산술하지 않는다(계약 B §2-2 계승).
        unit_price_ex_vat=line.unit_cost_ex_vat,
        unit_price_inc_vat=line.unit_cost_inc_vat,
        # 통관일. 없으면 ETA로 떨어지고 그것도 없으면 «없음»이다 — 오늘로 채우지 않는다.
        effective_date=line.shipment.declaration_date or line.shipment.eta,
        note=note,
    )
    db.add(p)
    db.flush()
    return p


def add_manual_price(
    db: Session,
    material_id: int,
    *,
    unit_price_ex_vat: Decimal | None = None,
    unit_price_inc_vat: Decimal | None = None,
    supplier: str | None = None,
    effective_date: date | None = None,
    note: str | None = None,
) -> CostMaterialPrice:
    """국내 구매 부자재처럼 원장 파생이 원리적으로 불가한 종의 단가 (계약 §4 하이브리드 ②).

    ★둘 중 **하나도 안 주면 거부**한다 — 빈 단가 행은 「값이 있다」는 착시만 만든다.
    하나만 주면 나머지는 «없음»으로 남는다. **여기서 ×1.1을 하지 않는다**: 그 환산은
    원장의 규약(D-NAO-150 호환)이지 사람이 입력한 값에 대한 사실이 아니고, 사람이 어느
    기준으로 입력했는지를 모델이 추측하면 그게 창작이다.
    """
    m = get_material(db, material_id)
    if unit_price_ex_vat is None and unit_price_inc_vat is None:
        raise CostMenuError(
            "단가를 하나도 주지 않았다 — 빈 단가 행은 만들지 않는다(계약 §2-7)."
        )
    p = CostMaterialPrice(
        material_id=m.id,
        source="manual",
        import_invoice_line_id=None,
        supplier=supplier,
        unit_price_ex_vat=unit_price_ex_vat,
        unit_price_inc_vat=unit_price_inc_vat,
        effective_date=effective_date,
        note=note,
    )
    db.add(p)
    db.flush()
    return p


def delete_price(db: Session, material_id: int, price_id: int) -> None:
    """연결·입력을 되돌린다 — 사람이 잘못 붙인 것을 사람이 뗀다."""
    p = (
        db.query(CostMaterialPrice)
        .filter(
            CostMaterialPrice.id == price_id,
            CostMaterialPrice.material_id == material_id,
        )
        .first()
    )
    if p is None:
        raise CostMenuError(f"단가 행 id={price_id}이 이 종에 없다.")
    db.delete(p)
    db.flush()
