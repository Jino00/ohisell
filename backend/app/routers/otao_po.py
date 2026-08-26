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

from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import OtaoItemNameMap, OtaoPurchaseOrder
from app.services.otao_po.roster import build_roster
from app.services.otao_po.sales import build_sales_timeseries
from app.services.otao_po.settlement import build_settlement

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


@router.get("/sales")
def get_sales(
    days: int = Query(60, ge=1, le=365), db: Session = Depends(get_db)
) -> dict:
    """S3 — 채널 통합 SKU별 판매수량 시계열 · 채널별 매핑률 · 결손일 구분.

    화면이 반드시 말해야 하는 것(계약 §2-8·§2-9 · `sales.py` docstring):
      ① **결손 구분 근거가 없는 채널이 어디인가**(`missing_day_evidence=false`) — 그 채널의 빈
         날을 0으로도 결손으로도 단정하지 않는다.
      ② **매핑 못 붙은 판매 수량**(`unmapped`) — 조용히 빼면 그만큼 수요가 사라진다.
      ③ **발주 축과의 다리 상태**(`order_axis`) — 겹치는 값이 0이면 이 판매 숫자를 예약 잔량과
         같은 줄에 놓을 수 없다는 뜻이고, 화면이 그렇게 말해야 한다.

    ★`response_model`을 쓰지 않는 이유는 `/roster`와 같다(교훈 #321) — 자백 필드가 조용히
    지워지면 화면엔 아무 일도 없는 것처럼 보인다. 테스트가 **HTTP body를** 단언한다.
    """
    ts = build_sales_timeseries(db, days=days)
    return {
        "window_start": ts.window_start.isoformat(),
        "window_end": ts.window_end.isoformat(),
        "days": days,
        # ★`rows[*].series`가 이 배열과 «자리로» 대응한다. 없으면 시계열이 좌표를 잃는다.
        "dates": ts.dates,
        "channels": [
            {
                "key": c.key,
                "label": c.label,
                "company": c.company,
                "sell_type": c.sell_type,
                "source_table": c.source_table,
                "bridge": c.bridge,
                "rows": c.rows,
                "quantity": c.quantity,
                "quantity_mapped": c.quantity_mapped,
                "quantity_excluded": c.quantity_excluded,
                # ★한 채널 상품 ID가 여러 상품을 가리켜 «안 붙인» 수량(적대 리뷰 P1-1)
                "quantity_ambiguous": c.quantity_ambiguous,
                "mapping_rate": c.mapping_rate,
                "days_with_rows": c.days_with_rows,
                # ★false면 「결손일과 판매 0을 구분할 근거가 없다」는 뜻이다.
                "missing_day_evidence": c.missing_day_evidence,
                "days_collected_zero": c.days_collected_zero,
                "days_no_data": c.days_no_data,
            }
            for c in ts.channels
        ],
        "rows": ts.rows,
        "daily": ts.daily,
        "unmapped": [{"channel": k, "quantity": v} for k, v in ts.unmapped.items()],
        "order_axis": ts.order_axis,
        "notes": ts.notes,
    }


@router.get("/settlement")
def get_settlement(db: Session = Depends(get_db)) -> dict:
    """S2 — 정산 창(전월 20~당월 19)별 픽업 합계 + 지급액 대조 상태.

    화면이 반드시 말해야 하는 것(계약 §2-8 · `settlement.py` docstring):
      ① **대조 대상이 없다는 사실**(`reconciliation.source == "none"`) — 「대조 불가」와
         「대조했는데 틀렸다」는 다른 상태다. 지급액 원장이 이 저장소에 없다.
      ② **`product`와 `material`이 갈라져 있는 것** — 부자재는 지급액엔 들어가고 S1의 픽업
         누계 칸엔 안 들어간다. 합치면 두 숫자가 왜 다른지 아무도 설명 못 한다.
      ③ **창 경계 ±2일 선적**(`boundary_shipment_ids`) — 이 원장엔 OTAO 픽업일이 없어
         창이 밀렸을 수 있다. 대조가 어긋나면 첫 번째 후보다.
      ④ **draft 선적이 합계에 들어 있다는 것** — 빼지도 숨기지도 않는다.

    ★`response_model`을 쓰지 않는 이유는 `/roster`·`/sales`와 같다(교훈 #321) — 위 자백
    필드가 조용히 지워지면 화면엔 아무 일도 없는 것처럼 보인다. 테스트가 **HTTP body를** 단언한다.

    ★읽기 전용이다. 지급액을 «입력»받는 표면은 여기 열지 않는다 — 돈이 나가는 축이고,
    값의 정본이 정해지기 전에 쓰기를 열면 그 숫자가 곧 정본 행세를 한다(계약 §3-2).
    """
    s = build_settlement(db)

    def money(v) -> float | None:
        return None if v is None else float(round(v, 2))

    def qty(v) -> float:
        return float(v)

    return {
        # ★원장이 비어 있는 것과 「픽업 0」은 다르다(`/roster`의 `ledger_empty`와 같은 결).
        "ledger_empty": s.ledger_start is None,
        "ledger_start": s.ledger_start.isoformat() if s.ledger_start else None,
        "ledger_end": s.ledger_end.isoformat() if s.ledger_end else None,
        "currency": s.currency,
        "windows": [
            {
                "key": w.key,
                "start": w.start.isoformat(),
                "end": w.end.isoformat(),  # = 지급일(19일)
                "shipments": w.shipments,
                "lines": w.lines,
                "product_quantity": qty(w.product_quantity),
                "product_amount_cny": money(w.product_amount_cny),
                "material_quantity": qty(w.material_quantity),
                "material_amount_cny": money(w.material_amount_cny),
                "other_quantity": qty(w.other_quantity),
                "other_amount_cny": money(w.other_amount_cny),
                "total_amount_cny": money(w.total_amount_cny),
                "shipment_ids": w.shipment_ids,
                "draft_shipment_ids": w.draft_shipment_ids,
                "boundary_shipment_ids": w.boundary_shipment_ids,
                # ★null은 「0원 지급」이 아니라 「모른다」다.
                "payment_actual_cny": money(w.payment_actual_cny),
                "difference_cny": money(w.difference_cny),
                "reconciled": w.reconciled,
            }
            for w in s.windows
        ],
        "unassigned": {
            "lines": s.unassigned_lines,
            "quantity": qty(s.unassigned_quantity),
            "amount_cny": money(s.unassigned_amount_cny),
        },
        "totals": {
            **{k: v for k, v in s.totals.items() if not isinstance(v, Decimal)},
            "product_quantity": qty(s.totals["product_quantity"]),
            "product_amount_cny": money(s.totals["product_amount_cny"]),
            "material_quantity": qty(s.totals["material_quantity"]),
            "material_amount_cny": money(s.totals["material_amount_cny"]),
            "other_quantity": qty(s.totals["other_quantity"]),
            "other_amount_cny": money(s.totals["other_amount_cny"]),
            "total_amount_cny": money(s.totals["total_amount_cny"]),
        },
        "reconciliation": s.reconciliation,
        "notes": s.notes,
    }
