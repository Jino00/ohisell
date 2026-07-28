# routers/overview.py — 쿠팡 종합 조망(Command Center) API (트랙 P7, D-2/D-3)
# GET /api/overview/command-center?from&to → 3축(회계·광고·상품) 단일 응답.
# 결합 엔진은 services/coupang/intelligence.py. 이 라우터는 기간 파싱·직렬화만(Agent 계층).
# D-3: 사실/지표만 — 추천 없음. Decimal은 문자열로 직렬화(금액 정밀도 보존, settlements 패턴).
from __future__ import annotations

from app.utils.kst import kst_now, kst_today
import logging
import os
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.coupang.intelligence import compute_command_center
from app.services.coupang.revenue_canonical import compute_canonical_revenue
from app.services.coupang.revenue_reconcile import reconcile_revenue
from app.services.coupang.rocket_intelligence import compute_rocket_overview
from app.services.coupang.rocket_promo_pnl import compute_promo_pnl_overview

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
    # S2(트랙 revenue-wing-truth D-1/D-9 A안): 닫힌 과거일 정본 매출(Wing GMV) 오버레이.
    # 읽기전용 가산 블록 — net_profit·account.summary.revenue 등 기존 값 불변(회귀 0).
    result["revenue_canonical"] = compute_canonical_revenue(db, dfrom, dto, account)
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


# 로켓배송(1P) 단일 계정 = 오하이테크(D-6). env override 가능, 미설정이면 None(전체 Retail/PO).
_ROCKET_VENDOR_ID = os.getenv("COUPANG_ROCKET_VENDOR_ID") or None


@router.get("/rocket-overview")
def rocket_overview(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """로켓배송(1P) 돈 축 종합조망 블록 — 매출(발주)·광고·순이익·발주↔정산 드리프트.

    트랙 rocket-1p S4(D-11/D-12). 1P는 PO그레인이라 옵션그레인 command-center와 별도 블록.
    읽기전용(3P/RG 종합조망 값 불변). 매출=Σ발주 gross(발주일 KST). net_profit=매출−광고로
    cost 미반영(has_cost=false, D-12: PO 61% multi-SKU 원가분해 불가, 발주상세 수집 후속). 기본 기간=최근 7일(KST).
    """
    today = kst_today()
    dto = _parse_date(to, today)
    dfrom = _parse_date(from_, dto - timedelta(days=6))
    if dfrom > dto:
        raise HTTPException(status_code=422, detail="from이 to보다 늦습니다")
    result = compute_rocket_overview(db, dfrom, dto, _ROCKET_VENDOR_ID)
    return _jsonify(result)


@router.get("/rocket-promo-pnl")
def rocket_promo_pnl(
    limit: int = Query(20, ge=1, le=100, description="최근 프로모션 N건"),
    request_id: str | None = Query(None, description="한 건만 보기(프로모션 Request ID)"),
    db: Session = Depends(get_db),
):
    """쿠팡 프로모션 손익 레이어 (트랙 coupang-promo-pnl Phase 2) — 프로모션별 진짜 손익·BEP ROAS.

    ★읽기 전용 신규 API다. 기존 net_profit·종합조망 회계는 **한 톨도 바뀌지 않는다**
      (1P 회계 매출은 여전히 발주 납품금액 축, D-CPP-2 / 분담금은 청구방식 미확정, D-CPP-4).

    기간 파라미터가 없는 이유: 창은 사용자가 고르는 게 아니라 **프로모션 행사기간이 정한다.**
      임의 기간을 받으면 프로모션 밖 판매가 손익에 섞인다.

    응답: promotions[](카드) · freshness(판매분석 결손·구독 체험 경고) · rg_coupons(나열).
    미상은 전부 null + 사유(blockers/unresolved_reasons)로 온다 — 0으로 접지 않는다(원칙22).
    """
    result = compute_promo_pnl_overview(
        db, _ROCKET_VENDOR_ID, limit=limit, request_id=(request_id or None)
    )
    return _jsonify(result)
