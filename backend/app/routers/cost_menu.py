"""원가 메뉴 API — D-CPP-53 / 계약 `docs/PLAN_cost-menu-standard-cost.md` (S1: 부자재 층).

★**응답에 `response_model`을 쓰지 않는다.** 교훈 #321(2026-08-19): `response_model`이 선언 안 된
키를 HTTP body에서 조용히 지워, 서비스층 테스트 9건이 전부 초록인데 화면엔 배너가 통째로 안 뜨는
사고가 났다. 이 층은 「미매칭」·「미확인」·「제안 이유」처럼 **있으면 반드시 보여야 하는** 자백
필드가 많아 같은 함정의 표면이 넓다. 요청(입력)만 Pydantic으로 검증하고, 응답은
`services/cost_menu/materials.py`의 payload 함수가 만든 dict를 그대로 낸다.
대신 테스트가 **HTTP body를 단언**한다(서비스층 dict만 보면 못 잡는다).

★요청 스키마를 `app/schemas.py`가 아니라 이 파일에 둔다 — 병행 세션이 같은 파일을 건드릴 때
충돌하는 자리라서다(계약 B 라우터와 같은 이유). 이 라우터 밖에서 쓰이지 않는다.

★**`product_master.cost_price`를 읽지도 쓰지도 않는다** — S1은 부자재 층까지고, 대조 표시는
표준원가 보드(S2·S3) 몫이다. 쓰기는 어느 슬라이스에서도 없다(계약 §3 금지선).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.cost_menu import materials as M

router = APIRouter(prefix="/api/cost", tags=["cost-menu"])


# ──────────────────────────────────────────────
# 요청 스키마 (입력 검증 전용)
# ──────────────────────────────────────────────
class MaterialIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    unit: Optional[str] = None
    category: Optional[str] = None
    # ★기본값은 `unconfirmed`다 — 새로 만든 종이 곧바로 승인분 행세를 하지 않는다(계약 §2-2).
    status: Literal["unconfirmed", "approved"] = "unconfirmed"
    excel_label: Optional[str] = None
    match_rule: Optional[str] = None
    form_factor: Optional[str] = None
    part: Optional[str] = None
    note: Optional[str] = None


class MaterialPatch(BaseModel):
    """부분 갱신. **주지 않은 필드는 안 건드린다** — `None`을 「비워라」로 읽지 않는다.

    (「값을 지워라」가 필요해지면 그때 명시적 표현을 만든다. 지금 `None`을 삭제로 읽으면
    화면의 부분 저장이 다른 칸을 조용히 지운다.)
    """

    name: Optional[str] = None
    unit: Optional[str] = None
    category: Optional[str] = None
    status: Optional[Literal["unconfirmed", "approved"]] = None
    excel_label: Optional[str] = None
    match_rule: Optional[str] = None
    form_factor: Optional[str] = None
    part: Optional[str] = None
    note: Optional[str] = None


class LinkIn(BaseModel):
    """원장 라인 → 부자재 종 **사람이 하는 확정**(계약 §5-2)."""

    import_invoice_line_id: int
    note: Optional[str] = None


class ManualPriceIn(BaseModel):
    """국내 구매 부자재 등 원장 파생이 불가한 종의 단가 (계약 §4 하이브리드 ②)."""

    unit_price_ex_vat: Optional[Decimal] = None
    unit_price_inc_vat: Optional[Decimal] = None
    supplier: Optional[str] = None
    effective_date: Optional[date] = None
    note: Optional[str] = None


# ──────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────
def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except M.CostMenuConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except M.CostMenuError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ──────────────────────────────────────────────
# 엔드포인트
# ──────────────────────────────────────────────
@router.get("/materials")
def list_materials(db: Session = Depends(get_db)):
    """부자재 종 전건 + 단가 이력(로트별).

    화면 탭1의 원료다 — 「승인 상태」·「로트 수」·「최신 단가」가 전부 여기서 온다.
    """
    return {"items": M.list_materials(db)}


@router.post("/materials", status_code=201)
def create_material(body: MaterialIn, db: Session = Depends(get_db)):
    m = _guard(M.create_material, db, **body.model_dump())
    db.commit()
    return M.material_payload(M.get_material(db, m.id), list(m.prices))


@router.get("/materials/{material_id}")
def get_material(material_id: int, db: Session = Depends(get_db)):
    try:
        m = M.get_material(db, material_id)
    except M.CostMenuError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return M.material_payload(m, list(m.prices))


@router.patch("/materials/{material_id}")
def patch_material(material_id: int, body: MaterialPatch, db: Session = Depends(get_db)):
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="바꿀 필드가 없다.")
    m = _guard(M.update_material, db, material_id, **fields)
    db.commit()
    m = M.get_material(db, material_id)
    return M.material_payload(m, list(m.prices))


@router.get("/ledger-material-lines")
def ledger_material_lines(db: Session = Depends(get_db)):
    """확정 수입건의 부자재 라인 전건 + 링크 상태 + **제안**.

    ★제안은 제안이다 — 이 GET은 아무것도 연결하지 않는다. 연결은 아래 POST를 화면의
    「연결」 버튼이 부를 때만 생긴다(계약 §5-2: 확정은 사람).
    ★미매칭 라인도 빠짐없이 실린다 — 안 보이면 단가 이력이 조용히 비어 있게 된다.
    """
    return {"items": M.ledger_material_lines(db)}


@router.post("/materials/{material_id}/prices/link", status_code=201)
def link_price(material_id: int, body: LinkIn, db: Session = Depends(get_db)):
    p = _guard(
        M.link_ledger_line, db, material_id, body.import_invoice_line_id, body.note
    )
    db.commit()
    m = M.get_material(db, material_id)
    return {"linked_price_id": p.id, "material": M.material_payload(m, list(m.prices))}


@router.post("/materials/{material_id}/prices", status_code=201)
def add_manual_price(material_id: int, body: ManualPriceIn, db: Session = Depends(get_db)):
    p = _guard(M.add_manual_price, db, material_id, **body.model_dump())
    db.commit()
    m = M.get_material(db, material_id)
    return {"price_id": p.id, "material": M.material_payload(m, list(m.prices))}


@router.delete("/materials/{material_id}/prices/{price_id}")
def delete_price(material_id: int, price_id: int, db: Session = Depends(get_db)):
    _guard(M.delete_price, db, material_id, price_id)
    db.commit()
    m = M.get_material(db, material_id)
    return {"deleted": True, "id": price_id, "material": M.material_payload(m, list(m.prices))}


@router.get("/settings")
def list_settings(db: Session = Depends(get_db)):
    """설정 전건 — `confirmed=false`가 화면의 자백 배지 원료다(계약 §9-1·합격 8)."""
    return {"items": M.list_settings(db)}
