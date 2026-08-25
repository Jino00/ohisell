"""OTAO 발주 로스터 API — 계약 `docs/contracts/CONTRACT_inventory_unified.md` §4 **S1**.

합격기준 원문: *"콘솔 발주 메뉴(신설)에서 Jino가 SKU별 **발주 누계 · 픽업 누계 · OTAO 예약
잔량**을 **3칸으로 나뉘어** 본다."*

★**3칸을 합산한 단일 숫자를 내지 않는다** — 계약 §3-9 금지선이다. *"합산하면 ②픽업 결정이
화면에서 사라진다."* 그래서 이 응답엔 `ordered + picked` 같은 파생 총계가 없고, 있는 것은
세 칸과 그 셋으로만 만들어지는 `reserved`뿐이다.

★**`response_model`을 쓰지 않는다** (교훈 #321): `response_model`이 선언 안 된 키를 HTTP
body에서 조용히 지워, 서비스층 테스트가 전부 초록인데 화면엔 배너가 통째로 안 뜨는 사고가 났다.
이 응답은 «있으면 반드시 보여야 하는» 자백 필드(`window_start`·`unmapped`·`notes`·
`out_of_window_ordered`)가 많아 같은 함정의 표면이 넓다. 대신 테스트가 **HTTP body를
단언한다** — 서비스층 dict만 보면 못 잡는다.

★읽기 전용이다. 적재는 `scripts/otao_po_import.py`가 하고 그것은 사람이 실행한다 —
발주는 돈이 나가는 축이라 HTTP 표면에 쓰기를 열지 않는다(계약 §3-2 「자동 «실행» 금지」).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import OtaoItemNameMap, OtaoPurchaseOrder
from app.services.otao_po.roster import build_roster

router = APIRouter(prefix="/api/otao-po", tags=["otao-po"])


@router.get("/roster")
def get_roster(db: Session = Depends(get_db)) -> dict:
    """SKU별 3칸 + 화면이 자백해야 하는 것들.

    화면이 반드시 말해야 하는 3가지(계약 §2-8·§2-9·모듈 docstring):
      ① 데이터 구간 — 잔량은 통관 원장이 덮는 창 안의 발주분만 센다(`window_start`)
      ② 매핑 필요 — 픽업 칸에 못 붙은 원장 품목명과 그 수량(`unmapped`)
      ③ 음수 잔량의 의미 — 창이 어긋났다는 신호이고 0으로 깎지 않았다(`notes`)
    """
    roster = build_roster(db)

    orders_total = db.scalar(select(func.count()).select_from(OtaoPurchaseOrder)) or 0
    orders_authoritative = (
        db.scalar(
            select(func.count())
            .select_from(OtaoPurchaseOrder)
            .where(OtaoPurchaseOrder.is_authoritative.is_(True))
        )
        or 0
    )
    last_order_date = db.scalar(
        select(func.max(OtaoPurchaseOrder.order_date)).where(
            OtaoPurchaseOrder.is_authoritative.is_(True)
        )
    )
    map_total = db.scalar(select(func.count()).select_from(OtaoItemNameMap)) or 0
    map_resolved = (
        db.scalar(
            select(func.count())
            .select_from(OtaoItemNameMap)
            .where(OtaoItemNameMap.product_code.is_not(None))
        )
        or 0
    )

    return {
        # ★원장이 비어 있는 것과 「전부 0인 로스터」는 다르다. 앞의 것은 **적재를 안 돌린
        #   것**이고 화면이 그렇게 말해야 한다 — 0을 보여 주면 「발주가 없다」로 읽힌다.
        "ledger_empty": orders_total == 0,
        "window_start": roster.window_start.isoformat() if roster.window_start else None,
        "rows": [
            {
                "product_code": r.product_code,
                "ordered": r.ordered,
                "picked": r.picked,
                "reserved": r.reserved,
                "out_of_window_ordered": r.out_of_window_ordered,
                "last_order_date": r.last_order_date.isoformat() if r.last_order_date else None,
                "order_count": r.order_count,
            }
            for r in roster.rows
        ],
        "totals": roster.totals,
        "unmapped": [{"item_name": k, "quantity": v} for k, v in roster.unmapped.items()],
        "notes": roster.notes,
        "source": {
            "orders_total": orders_total,
            "orders_authoritative": orders_authoritative,
            "orders_superseded": orders_total - orders_authoritative,
            "last_order_date": last_order_date.isoformat() if last_order_date else None,
            "name_map_total": map_total,
            "name_map_resolved": map_resolved,
        },
    }
