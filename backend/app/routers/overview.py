# routers/overview.py — 쿠팡 종합 조망(Command Center) API (트랙 P7, D-2/D-3)
# GET /api/overview/command-center?from&to → 3축(회계·광고·상품) 단일 응답.
# 결합 엔진은 services/coupang/intelligence.py. 이 라우터는 기간 파싱·직렬화만(Agent 계층).
# D-3: 사실/지표만 — 추천 없음. Decimal은 문자열로 직렬화(금액 정밀도 보존, settlements 패턴).
from __future__ import annotations

from app.utils.kst import kst_now, kst_today
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.coupang.intelligence import compute_command_center
from app.services.coupang.revenue_reconcile import reconcile_revenue

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/overview", tags=["overview"])



def _parse_date(s: str | None, default: date) -> date:
    if not s:
        return default
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail=f"잘못된 날짜 형식: {s} (YYYY-MM-DD)")


def _jsonify(v):
    """Decimal → str(정밀도 보존), 중첩 dict/list 재귀. None/숫자/bool은 그대로."""
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, dict):
        return {k: _jsonify(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonify(x) for x in v]
    return v


# S1(트랙 reconciliation D-4): 허용 계정 — 쿠팡 대시보드(계정별)와 1:1 비교용.
_VALID_ACCOUNTS = {"COUPANG_WING1", "COUPANG_WING2"}  # 오픽스 / 오하이테크


@router.get("/command-center")
def command_center(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    account: str | None = Query(
        None, description="계정 필터: COUPANG_WING1(오픽스)·COUPANG_WING2(오하이테크). 생략=전체 합산."
    ),
    db: Session = Depends(get_db),
):
    """옵션ID 결합 엔진으로 3축 조망 반환. 기본 기간=최근 7일(KST).

    회계: 옵션별 매출−반품차감−실측수수료−광고비−원가=순이익(원가 있으면).
    광고: 비용·노출·클릭·전환매출·ROAS·CTR (사실, D-3).
    상품: 주문수·반품률·재고·판매상태.
    S1: account 주면 계정별 분리 뷰. 생략 시 전체 합산(기존 동작 불변).
    """
    today = kst_today()
    dto = _parse_date(to, today)
    dfrom = _parse_date(from_, dto - timedelta(days=6))
    if dfrom > dto:
        raise HTTPException(status_code=422, detail="from이 to보다 늦습니다")
    if account is not None and account not in _VALID_ACCOUNTS:
        raise HTTPException(
            status_code=422,
            detail=f"잘못된 account: {account} (허용: {', '.join(sorted(_VALID_ACCOUNTS))} 또는 생략)",
        )
    result = compute_command_center(db, dfrom, dto, account)
    return _jsonify(result)


@router.get("/revenue-reconcile")
def revenue_reconcile(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    account: str | None = Query(
        None, description="계정 필터: COUPANG_WING1(오픽스)·COUPANG_WING2(오하이테크). 생략=전체 합산."
    ),
    db: Session = Depends(get_db),
):
    """우리 매출(revenue_3p/rg) vs 쿠팡 공식 GMV(vendor-summary) 닫힌일 드리프트% 대조.

    Wing 세션 자동화 트랙 S2. 읽기전용(net_profit 등 종합조망 값 불변). 닫힌 과거일만 비교(D-3).
    드리프트% = (우리−쿠팡)/쿠팡. 사실·지표만(D-2). 기본 기간=최근 7일(KST).
    """
    today = kst_today()
    dto = _parse_date(to, today)
    dfrom = _parse_date(from_, dto - timedelta(days=6))
    if dfrom > dto:
        raise HTTPException(status_code=422, detail="from이 to보다 늦습니다")
    if account is not None and account not in _VALID_ACCOUNTS:
        raise HTTPException(
            status_code=422,
            detail=f"잘못된 account: {account} (허용: {', '.join(sorted(_VALID_ACCOUNTS))} 또는 생략)",
        )
    result = reconcile_revenue(db, dfrom, dto, account)
    return _jsonify(result)
