# p6_meta.py — P6 도메인 조회 라우터: 물류·카테고리·브랜드·CS 현황.
# 물류/카테고리/브랜드: 온디맨드 조회(DB 미적재 — 조망 직접 관련 낮음, D-7).
# CS: DB 조회(coupang_inquiry 미답변 현황) + SA 온디맨드.
# 트랙 D-8: 쿠팡 Open API는 서버 IP 화이트리스트 → 라이브 호출은 서버에서만(로컬 403).
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import get_coupang_config
from app.database import get_db
from app.models import CoupangInquiry
from app.routers._coupang_write_http import handle_write as _handle_write

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/p6", tags=["p6-meta"])

_DEFAULT_ACCOUNT = "COUPANG_WING2"  # 조회 기본 계정(Wing2가 주 계정)


def _get_account(account_key: str = _DEFAULT_ACCOUNT):
    cfg = get_coupang_config(account_key)
    if not cfg:
        raise HTTPException(status_code=503, detail=f"계정 설정 없음: {account_key}")
    return cfg


# ════════════════════════════════════════════════════════════════════════
# 물류 (logistics)
# ════════════════════════════════════════════════════════════════════════

@router.get("/logistics/outbound-places")
def get_outbound_places(
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    page_num: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=50),
):
    """출고지 목록 조회 (#1). 온디맨드."""
    from app.clients.coupang.logistics import CoupangLogisticsClient

    cfg = _get_account(account_key)
    client = CoupangLogisticsClient(cfg)
    try:
        return client.list_outbound_places(page_num=page_num, page_size=page_size)
    except Exception as e:
        log.warning("출고지 목록 조회 실패: %s", e)
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/logistics/return-places")
def get_return_places(
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    page_num: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=50),
):
    """반품지 목록 조회 (#5). 온디맨드."""
    from app.clients.coupang.logistics import CoupangLogisticsClient

    cfg = _get_account(account_key)
    client = CoupangLogisticsClient(cfg)
    try:
        return client.list_return_places(page_num=page_num, page_size=page_size)
    except Exception as e:
        log.warning("반품지 목록 조회 실패: %s", e)
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/logistics/return-places/{center_codes}")
def get_return_place(
    center_codes: str,
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
):
    """반품지 단건(복수) 조회 (#6). center_codes = 콤마 구분 코드."""
    from app.clients.coupang.logistics import CoupangLogisticsClient

    cfg = _get_account(account_key)
    client = CoupangLogisticsClient(cfg)
    codes = [c.strip() for c in center_codes.split(",") if c.strip()]
    try:
        return client.get_return_place(return_center_codes=codes)
    except Exception as e:
        log.warning("반품지 단건 조회 실패: %s", e)
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/logistics/courier-codes")
def get_courier_codes():
    """택배사 코드표 (#8) — 정적 상수."""
    from app.clients.coupang.logistics import COURIER_CODES

    return {"courier_codes": COURIER_CODES}


# ── 물류 쓰기 (트랙 D-16, W1) — dry_run 기본. 라이브 실행은 dry_run=false & confirm 토큰 필요 ──
# 공통 예외 핸들러 _handle_write는 _coupang_write_http.handle_write로 추출(상단 import).

@router.post("/logistics/outbound-places")
def create_outbound_place(
    body: dict,
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """출고지 생성 (#2) — ⚠️쓰기. dry_run=true(기본)면 미리보기만."""
    from app.services.coupang import logistics_ops

    cfg = _get_account(account_key)
    return _handle_write(
        lambda: logistics_ops.create_outbound_place(
            cfg, body, dry_run=dry_run, confirm=confirm
        )
    )


@router.put("/logistics/outbound-places/{code}")
def update_outbound_place(
    code: str,
    body: dict,
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """출고지 수정 (#3) — ⚠️쓰기. dry_run 기본."""
    from app.services.coupang import logistics_ops

    cfg = _get_account(account_key)
    return _handle_write(
        lambda: logistics_ops.update_outbound_place(
            cfg, code, body, dry_run=dry_run, confirm=confirm
        )
    )


@router.post("/logistics/return-places")
def create_return_place(
    body: dict,
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """반품지 생성 (#4) — ⚠️쓰기. dry_run 기본."""
    from app.services.coupang import logistics_ops

    cfg = _get_account(account_key)
    return _handle_write(
        lambda: logistics_ops.create_return_place(
            cfg, body, dry_run=dry_run, confirm=confirm
        )
    )


@router.put("/logistics/return-places/{return_center_code}")
def update_return_place(
    return_center_code: str,
    body: dict,
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """반품지 수정 (#7) — ⚠️쓰기. dry_run 기본."""
    from app.services.coupang import logistics_ops

    cfg = _get_account(account_key)
    return _handle_write(
        lambda: logistics_ops.update_return_place(
            cfg, return_center_code, body, dry_run=dry_run, confirm=confirm
        )
    )


# ════════════════════════════════════════════════════════════════════════
# 카테고리 (category)
# ════════════════════════════════════════════════════════════════════════

@router.get("/categories")
def list_categories(account_key: str = Query(default=_DEFAULT_ACCOUNT)):
    """카테고리 전체 트리 조회 (#1)."""
    from app.clients.coupang.category import CoupangCategoryClient

    cfg = _get_account(account_key)
    client = CoupangCategoryClient(cfg)
    try:
        return {"data": client.list_categories()}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/categories/{category_code}")
def get_category(
    category_code: str,
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
):
    """카테고리 단건 조회 (#2)."""
    from app.clients.coupang.category import CoupangCategoryClient

    cfg = _get_account(account_key)
    client = CoupangCategoryClient(cfg)
    try:
        return {"data": client.get_category(category_code)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/categories/{category_code}/status")
def check_category_status(
    category_code: str,
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
):
    """카테고리 유효성 검사 (#3)."""
    from app.clients.coupang.category import CoupangCategoryClient

    cfg = _get_account(account_key)
    client = CoupangCategoryClient(cfg)
    try:
        return {"usable": client.check_category_status(category_code)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/categories/{category_code}/meta")
def get_category_meta(
    category_code: str,
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
):
    """카테고리 메타정보 조회 (#4) — 상품생성 필수."""
    from app.clients.coupang.category import CoupangCategoryClient

    cfg = _get_account(account_key)
    client = CoupangCategoryClient(cfg)
    try:
        return {"data": client.get_category_meta(category_code)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/categories/predict")
def predict_category(
    body: dict,
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
):
    """카테고리 추천 (#5) — 상품명→카테고리. body: {productName, ...}."""
    from app.clients.coupang.category import CoupangCategoryClient

    cfg = _get_account(account_key)
    client = CoupangCategoryClient(cfg)
    product_name = body.get("productName") or body.get("product_name")
    if not product_name:
        raise HTTPException(status_code=400, detail="productName 필수")
    try:
        return {
            "data": client.predict_category(
                product_name=product_name,
                product_description=body.get("productDescription"),
                brand=body.get("brand"),
                attributes=body.get("attributes"),
            )
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ════════════════════════════════════════════════════════════════════════
# 브랜드 (brand)
# ════════════════════════════════════════════════════════════════════════

@router.get("/brands/enrolled")
def list_enrolled_brands(account_key: str = Query(default=_DEFAULT_ACCOUNT)):
    """등록 브랜드 목록 (#2)."""
    from app.clients.coupang.brand import CoupangBrandClient

    cfg = _get_account(account_key)
    client = CoupangBrandClient(cfg)
    try:
        return {"data": client.list_enrolled_brands()}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/brands/search")
def search_brands(
    body: dict,
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
):
    """브랜드 검색 (#1). body: {brandName}."""
    from app.clients.coupang.brand import CoupangBrandClient

    cfg = _get_account(account_key)
    client = CoupangBrandClient(cfg)
    brand_name = body.get("brandName") or body.get("brand_name")
    if not brand_name:
        raise HTTPException(status_code=400, detail="brandName 필수")
    try:
        return {"data": client.search_brands(brand_name=brand_name)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/brands/{brand_id}")
def get_brand(
    brand_id: str,
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
):
    """브랜드 단건 조회 (#3). brand_id 예: KR-5."""
    from app.clients.coupang.brand import CoupangBrandClient

    cfg = _get_account(account_key)
    client = CoupangBrandClient(cfg)
    try:
        return {"data": client.get_brand(brand_id=brand_id)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ════════════════════════════════════════════════════════════════════════
# CS 현황 (DB 조회)
# ════════════════════════════════════════════════════════════════════════

@router.get("/inquiries")
def list_inquiries(
    answered: bool | None = Query(default=None),
    inquiry_type: str | None = Query(default=None),
    vendor_id: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
):
    """CS 문의 현황 조회 (DB). answered=false로 미답변만 필터."""
    q = db.query(CoupangInquiry)
    if answered is not None:
        q = q.filter(CoupangInquiry.answered == answered)
    if inquiry_type:
        q = q.filter(CoupangInquiry.inquiry_type == inquiry_type)
    if vendor_id:
        q = q.filter(CoupangInquiry.vendor_id == vendor_id)

    total = q.count()
    rows = q.order_by(CoupangInquiry.inquired_at.desc().nullslast()).limit(limit).all()

    unanswered_count = (
        db.query(CoupangInquiry)
        .filter(CoupangInquiry.answered.is_(False))
        .count()
    )

    return {
        "total": total,
        "unanswered": unanswered_count,
        "items": [
            {
                "id": r.id,
                "account_key": r.account_key,
                "vendor_id": r.vendor_id,
                "inquiry_type": r.inquiry_type,
                "inquiry_id": r.inquiry_id,
                "status": r.status,
                "title": r.title,
                "answered": r.answered,
                "inquired_at": r.inquired_at.isoformat() if r.inquired_at else None,
                "synced_at": r.synced_at.isoformat() if r.synced_at else None,
            }
            for r in rows
        ],
    }


# ── CS 쓰기 (트랙 D-16, W2) — dry_run 기본. ⚠️ 라이브 실행 시 실제 고객/문의에 전송 ──

@router.post("/inquiries/online/{inquiry_id}/reply")
def reply_online_inquiry(
    inquiry_id: str,
    body: dict,
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """상품별 고객문의 답변 (#2) — ⚠️쓰기. body: {content, replyBy}. dry_run 기본."""
    from app.services.coupang import cs_ops

    cfg = _get_account(account_key)
    content = body.get("content") or ""
    reply_by = body.get("replyBy") or body.get("reply_by") or ""
    return _handle_write(
        lambda: cs_ops.reply_online_inquiry(
            cfg, inquiry_id, content, reply_by, dry_run=dry_run, confirm=confirm
        )
    )


@router.post("/inquiries/call-center/{inquiry_id}/reply")
def reply_call_center_inquiry(
    inquiry_id: str,
    body: dict,
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """쿠팡 고객센터 문의 답변 (#4) — ⚠️쓰기. body: {content, replyBy, parentAnswerId}. dry_run 기본."""
    from app.services.coupang import cs_ops

    cfg = _get_account(account_key)
    content = body.get("content") or ""
    reply_by = body.get("replyBy") or body.get("reply_by") or ""
    parent_answer_id = body.get("parentAnswerId") or body.get("parent_answer_id")
    return _handle_write(
        lambda: cs_ops.reply_call_center_inquiry(
            cfg, inquiry_id, content, reply_by, parent_answer_id,
            dry_run=dry_run, confirm=confirm,
        )
    )


@router.post("/inquiries/call-center/{inquiry_id}/confirm")
def confirm_call_center_inquiry(
    inquiry_id: str,
    body: dict,
    account_key: str = Query(default=_DEFAULT_ACCOUNT),
    dry_run: bool = Query(default=True),
    confirm: str | None = Query(default=None),
):
    """쿠팡 고객센터 문의 확인 (#5) — ⚠️쓰기. body: {confirmBy}. dry_run 기본."""
    from app.services.coupang import cs_ops

    cfg = _get_account(account_key)
    confirm_by = body.get("confirmBy") or body.get("confirm_by") or ""
    return _handle_write(
        lambda: cs_ops.confirm_call_center_inquiry(
            cfg, inquiry_id, confirm_by, dry_run=dry_run, confirm=confirm
        )
    )
