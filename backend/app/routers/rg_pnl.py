# routers/rg_pnl.py — RG(로켓그로스) «상품(옵션) 단위 일별 손익» HTTP 경계.
# GET /api/coupang/rg/option-pnl (D-CPP-54, CONTRACT_2p_own_screens §1-A-4)
#
# ★이 라우터는 «얇다» — `rg_option_pnl()`을 부르고 직렬화만 한다. 새 손익 계산을
#   하나도 안 만든다(계약 §3 금지선). `rocket_1p_pnl_audit.py`가 같은 규율을 쓴다.
#
# Jino 원문(계약 §0): "어제 어떤 제품이 몇개가 팔리고 그 판매분의 정산공제, 원가, 세금,
#   기타비용등을 빼고 남는 이익이 있잖아" — 기본 창은 그래서 **단일일(어제, KST)**이다.
from __future__ import annotations

import logging
import os
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.routers.dashboard import _WING_ACCOUNTS, _vendor_id_for_account
from app.schemas import RgOptionPnlResponse
from app.services.coupang.intelligence import _cost_master
from app.services.coupang.rg_daily_pnl import rg_option_pnl
from app.utils.kst import kst_today

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/coupang/rg", tags=["rg-option-pnl"])


def _parse(s: str, label: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise HTTPException(status_code=422,
                            detail=f"잘못된 날짜 형식: {label}={s} (YYYY-MM-DD)")


def _window(date_from: str | None, date_to: str | None) -> tuple[date, date]:
    """조회 창. 기본은 **KST 어제 하루**(Jino 원문 "어제 어떤 제품이…") — date_to 생략 시
    KST 어제, date_from 생략 시 date_to와 같은 날."""
    dto = (kst_today() - timedelta(days=1)) if not date_to else _parse(date_to, "date_to")
    dfrom = dto if not date_from else _parse(date_from, "date_from")
    if dfrom > dto:
        raise HTTPException(status_code=422, detail="date_from이 date_to보다 늦습니다")
    return dfrom, dto


@router.get("/option-pnl", response_model=RgOptionPnlResponse)
def rg_option_pnl_endpoint(
    account: str = Query(..., description="COUPANG_WING1(오픽스) 또는 COUPANG_WING2(오하이테크)"),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """RG 화면 A 재료 — 옵션별 일별 손익 + 계정 공통 행. 계산은 `rg_option_pnl()`이 전부 한다."""
    if account not in _WING_ACCOUNTS:
        raise HTTPException(
            status_code=422,
            detail=f"account는 {list(_WING_ACCOUNTS)} 중 하나여야 합니다: {account!r}",
        )
    dfrom, dto = _window(date_from, date_to)

    cost_master = _cost_master(db)
    vendor_id = os.getenv(f"{account}_VENDOR_ID") or _vendor_id_for_account(db, account)

    # ★vendor_id를 못 찾아도 500을 내지 않는다 — 대신 광고비를 「0원」이 아니라 「모름」으로
    #   자백한다(위임문 지시: 추정으로 0을 채우고 침묵 금지). 빈 문자열로 넘기면
    #   `_agg_ads`가 `vendor_id == ''`로 필터해 자연히 광고비 0을 내는데(`intelligence._agg_ads`,
    #   `vendor_id is not None`이면 항상 필터), 그 0이 "정말 안 썼다"가 아니라 "vendor_id를
    #   몰라서 못 찾았다"는 뜻이라는 사실을 아래 경고 필드가 명시적으로 말한다.
    ad_spend_warning: str | None = None
    if not vendor_id:
        log.warning("account %s의 vendor_id를 못 찾았다 — 광고비를 싣지 않는다", account)
        ad_spend_warning = (
            "vendor_id를 확인하지 못해 광고비(ad_spend·ad_unallocated 등)를 싣지 못했습니다 — "
            "아래 광고비 관련 값은 «0원»이 아니라 «모름»입니다."
        )
        vendor_id = ""

    result = rg_option_pnl(db, account, dfrom, dto, cost_master, vendor_id)

    return {
        **result,
        "account": account,
        "date_from": dfrom.isoformat(),
        "date_to": dto.isoformat(),
        "ad_spend_warning": ad_spend_warning,
    }
