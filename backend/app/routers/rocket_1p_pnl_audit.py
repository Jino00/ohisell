# routers/rocket_1p_pnl_audit.py — 로켓1P 손익 «근거 화면» 라우터.
# GET /api/coupang/ops/rocket/pnl-audit/{checks,atoms,atom}
#
# ★이 라우터는 «얇다»보다 **«합성한다»**가 맞다: 검사·원자 서비스는 D-CPP-2 가드
#   (`app/services/` 아래에서 1P 매출·손익 화면 SA를 참조하는 것을 금지) 때문에 화면 함수를
#   부를 수 없어, **여기서 부른 결과를 주입**한다. `app/routers/overview.py`가 같은 참조를
#   하는 것과 같은 패턴이다. 부수 효과가 본질이다 — 서비스가 화면 모듈을 참조할 수 없으므로
#   「근거 창은 화면이 낸 숫자를 재도출하지 않는다」가 문서 규칙이 아니라 구조가 된다.
#   (서비스가 아무 계산도 못 한다는 뜻은 아니다 — db는 받는다.)
#
# ★★**창을 정하는 곳이 여기 하나다.** 세 엔드포인트가 `_window` 하나로 창을 정하고, 그 창으로
#   화면 응답과 원자를 뽑는다. 창이 갈리면 분담금 «모름» 판정이 갈려 근거가 화면과 **다른
#   답**을 낸다(원자 파생 SA의 창 종속성 계약). 검사 서비스는 주입된 화면의 period를 대조해
#   그 사고를 조용히 통과시키지 않고 죽는다 — 그 방어가 실제로 도는지 라우터 테스트가 본다.
#
# ★vendor도 화면 엔드포인트와 **같은 방식**으로 정한다(`COUPANG_ROCKET_VENDOR_ID`) — 축이
#   갈리면 근거와 화면이 다른 판매자를 세게 된다.
from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.coupang.rocket_1p_pnl_audit import (
    ATOM_LIMIT, compute_pnl_audit_atom_detail, compute_pnl_audit_atoms,
    compute_pnl_audit_checks)
from app.services.coupang.rocket_1p_revenue import (
    compute_rocket_1p_revenue, day_option_atoms)
from app.utils.kst import kst_today

router = APIRouter(prefix="/api/coupang/ops/rocket/pnl-audit", tags=["rocket-1p-pnl-audit"])

# 로켓배송(1P) 단일 계정 = 오하이테크(D-6). overview 라우터의 화면 엔드포인트와 같은 해석.
_ROCKET_VENDOR_ID = os.getenv("COUPANG_ROCKET_VENDOR_ID") or None


def _jsonify(v):
    """Decimal → str(정밀도 보존), 중첩 dict/list 재귀. None/숫자/bool은 그대로.

    (overview 라우터와 같은 규약 — 금액을 float으로 내보내면 원 단위가 흔들린다.)
    """
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, dict):
        return {k: _jsonify(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonify(x) for x in v]
    return v


def _parse(s: str, label: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise HTTPException(status_code=422,
                            detail=f"잘못된 날짜 형식: {label}={s} (YYYY-MM-DD)")


def _window(date_from: str | None, date_to: str | None) -> tuple[date, date]:
    """조회 창. **세 엔드포인트가 이 하나를 쓴다** — 창이 갈리면 분담금 판정이 갈린다."""
    dto = kst_today() if not date_to else _parse(date_to, "date_to")
    dfrom = (dto - timedelta(days=6)) if not date_from else _parse(date_from, "date_from")
    if dfrom > dto:
        raise HTTPException(status_code=422, detail="date_from이 date_to보다 늦습니다")
    return dfrom, dto


@router.get("/checks")
def pnl_audit_checks(date_from: str | None = Query(None), date_to: str | None = Query(None),
                     db: Session = Depends(get_db)):
    """1단 — 산술 검사. 통과해도 좌·우변을 싣고, 판정 불가는 pass가 아니라 undetermined다.

    ★화면 응답은 **`ATOM_LIMIT`으로** 부른다 — 옵션 표가 잘리면 A2·A7이 합을 낼 수 없어
      undetermined가 되는데, 라우터가 자기 숫자를 쓰면 그 계약이 두 곳으로 흩어진다.
    """
    dfrom, dto = _window(date_from, date_to)
    screen = compute_rocket_1p_revenue(db, dfrom, dto, _ROCKET_VENDOR_ID, ATOM_LIMIT)
    return _jsonify(compute_pnl_audit_checks(db, dfrom, dto, screen, _ROCKET_VENDOR_ID))


@router.get("/atoms")
def pnl_audit_atoms(date_from: str | None = Query(None), date_to: str | None = Query(None),
                    sort: str = Query("revenue", pattern="^(revenue|net|date)$"),
                    flt: str = Query("all", pattern="^(all|loss|suggested|uncosted|unpriced)$"),
                    option_id: str | None = Query(None),
                    db: Session = Depends(get_db)):
    """3단 — 「날짜×옵션」 원자 목록(원가 출처 배지 포함)."""
    dfrom, dto = _window(date_from, date_to)
    ctx = day_option_atoms(db, dfrom, dto, _ROCKET_VENDOR_ID)
    return _jsonify(compute_pnl_audit_atoms(db, dfrom, dto, ctx,
                                            sort=sort, flt=flt, option_id=option_id))


@router.get("/atom")
def pnl_audit_atom(date_: str = Query(..., alias="date"), option_id: str = Query(...),
                   date_from: str = Query(...), date_to: str = Query(...),
                   db: Session = Depends(get_db)):
    """4단 — 원자 1개의 다섯 갈래 원천 행.

    ★★`date_from`·`date_to`는 **필수**다(다른 둘과 달리 기본 창이 없다). 이 응답의
      `atom.net_profit`·`burden_known`·`promo_burden`은 **창-종속**이라, 창을 라우터가
      대신 정해 주면 화면이 «—»로 그린 행에 근거가 숫자를 찍어도 **응답만 보고는 알 수
      없다**. 생략을 422로 막고, 쓴 창을 `period`로 되돌려준다.
    ★`date`가 그 창 밖이면 **422**다. 안 막으면 원자는 없는데 원천 행은 붙어 나오는 응답이
      생기고, `atom: null`이 「그날 판매 없음」과 「창 밖」 두 가지를 뜻하게 된다. 막았으므로
      이 엔드포인트에서 `atom: null`은 **「그날 그 옵션에 판매행이 없다」 하나**를 뜻한다.
    """
    d = _parse(date_, "date")
    dfrom, dto = _window(date_from, date_to)
    if not (dfrom <= d <= dto):
        raise HTTPException(
            status_code=422,
            detail=f"date={date_}가 조회 창 [{dfrom.isoformat()}, {dto.isoformat()}] 밖입니다 — "
                   "원자 상세는 화면이 보고 있는 창 안의 날짜만 답합니다(창-종속 값이라서).")
    ctx = day_option_atoms(db, dfrom, dto, _ROCKET_VENDOR_ID)
    return _jsonify(compute_pnl_audit_atom_detail(db, dfrom, dto, d, option_id, ctx,
                                                  _ROCKET_VENDOR_ID))
