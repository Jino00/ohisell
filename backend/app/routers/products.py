# products.py — 상품 원가표 CRUD + 채널 매핑 + 엑셀 업로드/다운로드
import io
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from openpyxl import Workbook, load_workbook
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import ProductMaster, ProductChannelMapping, Channel
from app.schemas import (
    ProductCreate,
    ProductUpdate,
    ProductOut,
    MappingCreate,
    MappingOut,
)

router = APIRouter(prefix="/api/products", tags=["products"])


def _product_to_out(p: ProductMaster) -> ProductOut:
    mappings = []
    for m in p.channel_mappings:
        mappings.append(
            MappingOut(
                id=m.id,
                channel_id=m.channel_id,
                channel_name=m.channel.name if m.channel else None,
                channel_product_id=m.channel_product_id,
                channel_product_name=m.channel_product_name,
                channel_sku=m.channel_sku,
                selling_price=m.selling_price,
                is_active=m.is_active,
            )
        )
    return ProductOut(
        id=p.id,
        internal_sku=p.internal_sku,
        product_name=p.product_name,
        cost_price=p.cost_price,
        category=p.category,
        memo=p.memo,
        created_at=p.created_at,
        updated_at=p.updated_at,
        mappings=mappings,
    )


@router.get("", response_model=list[ProductOut])
def list_products(db: Session = Depends(get_db)):
    products = (
        db.query(ProductMaster)
        .options(
            joinedload(ProductMaster.channel_mappings).joinedload(
                ProductChannelMapping.channel
            )
        )
        .order_by(ProductMaster.id)
        .all()
    )
    return [_product_to_out(p) for p in products]


@router.post("", response_model=ProductOut, status_code=201)
def create_product(data: ProductCreate, db: Session = Depends(get_db)):
    exists = db.query(ProductMaster).filter_by(internal_sku=data.internal_sku).first()
    if exists:
        raise HTTPException(400, f"SKU '{data.internal_sku}' 이미 존재합니다")
    p = ProductMaster(**data.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    return _product_to_out(p)


@router.put("/{product_id}", response_model=ProductOut)
def update_product(
    product_id: int, data: ProductUpdate, db: Session = Depends(get_db)
):
    p = db.query(ProductMaster).get(product_id)
    if not p:
        raise HTTPException(404, "상품을 찾을 수 없습니다")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    p = (
        db.query(ProductMaster)
        .options(
            joinedload(ProductMaster.channel_mappings).joinedload(
                ProductChannelMapping.channel
            )
        )
        .filter_by(id=product_id)
        .first()
    )
    return _product_to_out(p)


@router.delete("/{product_id}", status_code=204)
def delete_product(product_id: int, db: Session = Depends(get_db)):
    p = db.query(ProductMaster).get(product_id)
    if not p:
        raise HTTPException(404, "상품을 찾을 수 없습니다")
    db.query(ProductChannelMapping).filter_by(product_id=product_id).delete()
    db.delete(p)
    db.commit()


# ── 채널 매핑 ──
@router.post("/{product_id}/mappings", response_model=MappingOut, status_code=201)
def add_mapping(
    product_id: int, data: MappingCreate, db: Session = Depends(get_db)
):
    p = db.query(ProductMaster).get(product_id)
    if not p:
        raise HTTPException(404, "상품을 찾을 수 없습니다")
    ch = db.query(Channel).get(data.channel_id)
    if not ch:
        raise HTTPException(404, "채널을 찾을 수 없습니다")
    m = ProductChannelMapping(product_id=product_id, **data.model_dump())
    db.add(m)
    db.commit()
    db.refresh(m)
    return MappingOut(
        id=m.id,
        channel_id=m.channel_id,
        channel_name=ch.name,
        channel_product_id=m.channel_product_id,
        channel_product_name=m.channel_product_name,
        channel_sku=m.channel_sku,
        selling_price=m.selling_price,
        is_active=m.is_active,
    )


@router.delete("/{product_id}/mappings/{mapping_id}", status_code=204)
def delete_mapping(
    product_id: int, mapping_id: int, db: Session = Depends(get_db)
):
    m = (
        db.query(ProductChannelMapping)
        .filter_by(id=mapping_id, product_id=product_id)
        .first()
    )
    if not m:
        raise HTTPException(404, "매핑을 찾을 수 없습니다")
    db.delete(m)
    db.commit()


# ── 매핑 템플릿 다운로드 (주문 데이터 기반) ──
@router.get("/mapping-template")
def download_mapping_template(db: Session = Depends(get_db)):
    """주문 데이터에서 채널별 상품 목록을 추출한 매핑 템플릿 엑셀"""
    from app.models import Order
    from sqlalchemy import func, distinct

    # 채널별 고유 상품 집계
    rows = (
        db.query(
            Channel.code,
            Channel.name,
            Order.platform_product_id,
            Order.platform_product_name,
            func.count(Order.id).label("order_count"),
            func.round(func.avg(Order.selling_price), 0).label("avg_price"),
        )
        .join(Channel, Channel.id == Order.channel_id)
        .filter(Order.platform_product_id != "")
        .group_by(Channel.code, Order.platform_product_id)
        .order_by(Channel.code, func.count(Order.id).desc())
        .all()
    )

    channels = db.query(Channel).order_by(Channel.id).all()

    wb = Workbook()

    # 시트1: 상품 원가표 (사용자가 채울 부분)
    ws1 = wb.active
    ws1.title = "상품 원가표"
    ws1.append(["사내SKU", "상품명", "원가", "카테고리", "메모"])
    # 고유 상품명 기반으로 후보 행 생성 (중복 제거)
    seen_names: set[str] = set()
    for r in rows:
        # 상품명에서 옵션 부분(콤마 이후) 제거하여 대표명 추출
        base_name = r.platform_product_name.split(",")[0].strip() if r.platform_product_name else ""
        if base_name and base_name not in seen_names:
            seen_names.add(base_name)
            ws1.append(["", base_name, "", "", ""])

    # 시트2: 채널 매핑 (주문 데이터에서 자동 채움, 사용자는 사내SKU만 입력)
    ws2 = wb.create_sheet("채널 매핑")
    ws2.append([
        "사내SKU", "채널코드", "채널상품번호", "채널상품명", "채널SKU", "판매가", "활성",
    ])
    for r in rows:
        ws2.append([
            "",  # 사내SKU — 사용자가 채울 부분
            r.code,
            r.platform_product_id,
            r.platform_product_name,
            "",  # 채널SKU
            float(r.avg_price) if r.avg_price else 0,
            "Y",
        ])

    # 시트3: 채널 목록 (참고용)
    ws3 = wb.create_sheet("채널 목록")
    ws3.append(["채널코드", "채널명", "플랫폼", "수수료율(%)"])
    for ch in channels:
        ws3.append([ch.code, ch.name, ch.platform, float(ch.commission_rate)])

    # 스타일: 사용자가 채울 셀 강조
    from openpyxl.styles import PatternFill
    yellow = PatternFill(start_color="FFFFCC", end_color="FFFFCC", fill_type="solid")
    for row in ws1.iter_rows(min_row=2, min_col=1, max_col=3):
        for cell in row:
            cell.fill = yellow
    for row in ws2.iter_rows(min_row=2, min_col=1, max_col=1):
        for cell in row:
            cell.fill = yellow

    # 열 너비 조정
    ws1.column_dimensions["A"].width = 15
    ws1.column_dimensions["B"].width = 50
    ws1.column_dimensions["C"].width = 10
    ws2.column_dimensions["A"].width = 15
    ws2.column_dimensions["B"].width = 18
    ws2.column_dimensions["C"].width = 18
    ws2.column_dimensions["D"].width = 60
    ws2.column_dimensions["F"].width = 12

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=ohisell_mapping_template.xlsx"},
    )


# ── 엑셀 다운로드 ──
@router.get("/download")
def download_excel(db: Session = Depends(get_db)):
    products = (
        db.query(ProductMaster)
        .options(
            joinedload(ProductMaster.channel_mappings).joinedload(
                ProductChannelMapping.channel
            )
        )
        .order_by(ProductMaster.id)
        .all()
    )
    channels = db.query(Channel).order_by(Channel.id).all()

    wb = Workbook()
    # 시트1: 상품 원가표
    ws = wb.active
    ws.title = "상품 원가표"
    ws.append(["사내SKU", "상품명", "원가", "카테고리", "메모"])
    for p in products:
        ws.append([p.internal_sku, p.product_name, float(p.cost_price), p.category, p.memo])

    # 시트2: 채널 매핑
    ws2 = wb.create_sheet("채널 매핑")
    ws2.append([
        "사내SKU", "채널코드", "채널상품번호", "채널상품명", "채널SKU", "판매가", "활성"
    ])
    for p in products:
        for m in p.channel_mappings:
            ws2.append([
                p.internal_sku,
                m.channel.code if m.channel else "",
                m.channel_product_id,
                m.channel_product_name,
                m.channel_sku,
                float(m.selling_price),
                "Y" if m.is_active else "N",
            ])

    # 시트3: 채널 목록 (참고용)
    ws3 = wb.create_sheet("채널 목록")
    ws3.append(["채널코드", "채널명", "플랫폼", "수수료율(%)"])
    for ch in channels:
        ws3.append([ch.code, ch.name, ch.platform, float(ch.commission_rate)])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=ohisell_products.xlsx"},
    )


# ── 엑셀 업로드 ──
@router.post("/upload")
async def upload_excel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(400, "xlsx 파일만 업로드 가능합니다")

    content = await file.read()
    wb = load_workbook(io.BytesIO(content))

    results = {"created": 0, "updated": 0, "mappings_created": 0, "errors": []}

    # 시트1: 상품 원가표
    if "상품 원가표" in wb.sheetnames:
        ws = wb["상품 원가표"]
        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not row[0]:
                continue
            sku, name, cost, category, memo = (
                str(row[0]).strip(),
                str(row[1]).strip() if row[1] else "",
                row[2],
                str(row[3]).strip() if row[3] else None,
                str(row[4]).strip() if row[4] else None,
            )
            try:
                cost_decimal = Decimal(str(cost)) if cost else Decimal("0")
            except (InvalidOperation, ValueError):
                results["errors"].append(f"행 {row_idx}: 원가 '{cost}' 변환 실패")
                continue

            existing = db.query(ProductMaster).filter_by(internal_sku=sku).first()
            if existing:
                existing.product_name = name
                existing.cost_price = cost_decimal
                existing.category = category
                existing.memo = memo
                results["updated"] += 1
            else:
                db.add(ProductMaster(
                    internal_sku=sku,
                    product_name=name,
                    cost_price=cost_decimal,
                    category=category,
                    memo=memo,
                ))
                results["created"] += 1

    # 시트2: 채널 매핑
    if "채널 매핑" in wb.sheetnames:
        ws2 = wb["채널 매핑"]
        for row_idx, row in enumerate(ws2.iter_rows(min_row=2, values_only=True), start=2):
            if not row[0] or not row[1]:
                continue
            sku = str(row[0]).strip()
            ch_code = str(row[1]).strip()
            ch_pid = str(row[2]).strip() if row[2] else ""
            ch_pname = str(row[3]).strip() if row[3] else None
            ch_sku = str(row[4]).strip() if row[4] else None
            try:
                price = Decimal(str(row[5])) if row[5] else Decimal("0")
            except (InvalidOperation, ValueError):
                price = Decimal("0")
            is_active = str(row[6]).strip().upper() != "N" if row[6] else True

            product = db.query(ProductMaster).filter_by(internal_sku=sku).first()
            channel = db.query(Channel).filter_by(code=ch_code).first()
            if not product:
                results["errors"].append(f"행 {row_idx}: SKU '{sku}' 없음")
                continue
            if not channel:
                results["errors"].append(f"행 {row_idx}: 채널코드 '{ch_code}' 없음")
                continue

            existing_mapping = (
                db.query(ProductChannelMapping)
                .filter_by(
                    product_id=product.id,
                    channel_id=channel.id,
                    channel_product_id=ch_pid,
                )
                .first()
            )
            if existing_mapping:
                existing_mapping.channel_product_name = ch_pname
                existing_mapping.channel_sku = ch_sku
                existing_mapping.selling_price = price
                existing_mapping.is_active = is_active
            else:
                db.add(ProductChannelMapping(
                    product_id=product.id,
                    channel_id=channel.id,
                    channel_product_id=ch_pid,
                    channel_product_name=ch_pname,
                    channel_sku=ch_sku,
                    selling_price=price,
                    is_active=is_active,
                ))
                results["mappings_created"] += 1

    db.commit()
    return results
