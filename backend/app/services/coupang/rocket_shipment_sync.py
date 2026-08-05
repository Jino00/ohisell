# rocket_shipment_sync.py — 쿠팡 로켓배송(1P) 발송(ASN) ingest Harness (트랙 rocket-1p, ref 45)
#
# 역할: Mac 헤드풀 페처가 긁어 보낸 쉽먼트 목록·상세 DOM을 파서 SA에 넘겨 정규화하고 적재한다.
#   런타임 경계(D-1)는 발주/정산과 같다 — 백엔드는 supplier에 직접 붙지 않는다.
#
# ★멱등 = 쉽먼트 단위 snapshot replace. 같은 쉽먼트를 다시 받으면 그 쉽먼트의 라인·박스를
#   전부 지우고 다시 넣는다. 라인이 줄어든 경우(쿠팡이 수정)를 반영하고 중복 누적을 막는다.
#   헤더는 upsert(행 하나라 replace할 게 없다).
#
# ★"모름"과 0을 구분한다: 목록만 받고 상세를 못 받은 쉽먼트는 total_received_qty를 **건드리지
#   않는다**(None 유지). 0으로 적으면 "쿠팡이 하나도 안 받았다"가 되어 미수금을 지어낸다.
#   ref 45 §12에서 실제로 이 계열의 과대계상(5,763,290원)이 났다 — 화면 하나만 보고 판정한 결과였다.
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.clients.coupang import rocket_shipment as parser
from app.models import (
    CoupangRocketShipment,
    CoupangRocketShipmentBox,
    CoupangRocketShipmentItem,
)
from app.utils.kst import kst_now

log = logging.getLogger(__name__)


def _upsert_header(db: Session, vendor_id: str, rec: dict) -> CoupangRocketShipment:
    row = (
        db.query(CoupangRocketShipment)
        .filter(CoupangRocketShipment.shipment_seq == rec["shipment_seq"])
        .first()
    )
    if row is None:
        row = CoupangRocketShipment(shipment_seq=rec["shipment_seq"], vendor_id=vendor_id)
        db.add(row)
    row.vendor_id = vendor_id
    for f in ("shipment_type", "status_code", "status_label", "invoice_number",
              "center_name", "shipped_at", "estimated_arrival_at"):
        if rec.get(f) is not None:
            setattr(row, f, rec[f])
    if rec.get("box_count"):
        row.box_count = rec["box_count"]
    # 목록의 「총 납품 수량」은 쉽먼트 합계라 그대로 쓸 수 있다(상세 요약과 같은 값).
    if rec.get("total_shipped_qty"):
        row.total_shipped_qty = rec["total_shipped_qty"]
    row.synced_at = kst_now()
    return row


def ingest_shipments(db: Session, vendor_id: str, entries: list[dict]) -> dict:
    """목록+상세를 한 번에 받는다.

    entries[i] = {
      "headers": [...], "cells": [...], "data": {"id","status","type"}, "po_content": "...",
      "detail": {"tables": [{"id","class","headers","rows"}, ...]}   # 없으면 목록만 적재
    }
    반환: {shipments, lines, boxes, details}
    """
    header_recs = parser.parse_shipment_list(entries)
    by_seq = {r["shipment_seq"]: r for r in header_recs}

    n_lines = n_boxes = n_details = 0
    now = kst_now()

    for entry in entries or []:
        seq = int((entry.get("data") or {}).get("id") or 0)
        rec = by_seq.get(seq)
        if rec is None:
            continue
        row = _upsert_header(db, vendor_id, rec)

        detail = entry.get("detail")
        if not detail:
            continue                                   # 목록만 — 상세 필드는 건드리지 않는다
        parsed = parser.parse_shipment_detail(seq, detail.get("tables") or [])
        summary = parsed.get("summary") or {}
        for f in ("po_count", "sku_count", "total_shipped_qty", "total_received_qty"):
            if summary.get(f) is not None:
                setattr(row, f, summary[f])
        if summary.get("box_count"):
            row.box_count = summary["box_count"]
        _apply_detail_header(row, detail)
        row.detail_synced_at = now
        n_details += 1

        # snapshot replace — 이 쉽먼트의 라인·박스만 지운다(다른 쉽먼트 불변).
        for old in db.query(CoupangRocketShipmentItem).filter(
                CoupangRocketShipmentItem.shipment_seq == seq).all():
            db.delete(old)
        for old in db.query(CoupangRocketShipmentBox).filter(
                CoupangRocketShipmentBox.shipment_seq == seq).all():
            db.delete(old)
        db.flush()

        for line in parsed.get("lines") or []:
            db.add(CoupangRocketShipmentItem(
                shipment_seq=seq,
                vendor_id=vendor_id,
                line_no=line["line_no"],
                box_label=line["box_label"] or None,
                purchase_order_seq=line["purchase_order_seq"],
                product_number=line["product_number"],
                product_name=line["product_name"],
                barcode=line["barcode"],
                shipped_qty=line["shipped_qty"],
                received_qty=line["received_qty"],
                synced_at=now,
            ))
            n_lines += 1
        for box in parsed.get("boxes") or []:
            db.add(CoupangRocketShipmentBox(
                shipment_seq=seq,
                vendor_id=vendor_id,
                box_label=box["box_label"],
                status_label=box["status_label"],
                invoice_number=box["invoice_number"],
                picked_up_at=box["picked_up_at"],
                arrived_at=box["arrived_at"],
                unloaded_at=box["unloaded_at"],
                synced_at=now,
            ))
            n_boxes += 1

    db.commit()
    log.info("rocket 쉽먼트 ingest: vendor=%s 헤더=%d 상세=%d 라인=%d 박스=%d",
             vendor_id, len(header_recs), n_details, n_lines, n_boxes)
    return {
        "shipments": len(header_recs),
        "details": n_details,
        "lines": n_lines,
        "boxes": n_boxes,
    }


def _apply_detail_header(row: CoupangRocketShipment, detail: dict) -> None:
    """상세 상단 정보표(택배사·센터·출고지)를 헤더에 채운다.

    이 표는 헤더 행이 없는 **라벨/값 쌍**이라(`택배사|한진택배|쿠팡 물류센터|시흥2|…`)
    시그니처로 못 고른다 → 라벨 텍스트로 직접 훑는다. 없으면 조용히 넘어간다(목록에 이미
    센터가 있어 치명적이지 않다).
    """
    labels = {
        "택배사": "carrier_name",
        "쿠팡 물류센터": "center_name",
        "출고지": "origin_address",
    }
    for t in detail.get("tables") or []:
        if t.get("headers"):
            continue                                   # 라벨/값 표는 thead가 없다
        for r in t.get("rows") or []:
            cells = [str(c or "").strip() for c in r]
            for i in range(0, len(cells) - 1, 2):
                field = labels.get(cells[i])
                if field and cells[i + 1]:
                    setattr(row, field, cells[i + 1][:200])
