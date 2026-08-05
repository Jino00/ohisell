# rocket_shipment.py — 쿠팡 로켓배송(1P) 발송(ASN) 쉽먼트 순수 파서 SA (트랙 rocket-1p, ref 45)
#
# 왜 필요한가: 우리가 **보낸 수량**은 지금까지 어디에도 저장되지 않았다. 발주(쿠팡이 시킨 양)와
#   입고(쿠팡이 받았다고 인정한 양)만 있어서, 그 사이의 "보냈는데 안 잡힌 것"=미수금은
#   2026-08-05 일회성 정찰로만 8,033,970원을 찾아냈다(ref 45). 상시 수집이 없으면 다음 달에
#   같은 일이 생겨도 아무도 못 본다.
#
# 런타임 경계(D-1): supplier는 Akamai 봇방어 → 백엔드 직접 fetch 금지.
#   Mac 헤드풀 CDP 페처가 DOM을 긁어 raw push → 이 SA가 정규화. HTTP 없음.
#
# ★파싱 원칙 = **헤더 이름 시그니처**로 표를 고른다(위치·id 둘 다 못 믿는다):
#   - id를 못 믿는 이유: 쉽먼트 상세는 `id="shipmentTotalTable"`을 **두 표에 중복**해서 쓴다
#     (정보표 class=productInfo + 요약표 class=basic). `getElementById`면 요약표를 영영 못 본다.
#     2026-08-05 라이브 실측.
#   - 위치를 못 믿는 이유: ref 44 §8-6·ref 45 §10과 같은 계열의 함정. 표가 하나 늘면 전부 밀린다.
#
# ★rowspan 처리 = **셀 수가 헤더 수보다 정확히 1 적으면 rowspan 컬럼이 빠진 것**으로 보고
#   직전 값을 이월한다. 라인 표의 「박스」 셀은 첫 행에만 있고(rowspan=N) 이후 행엔 없다.
#   "7이면 있고 6이면 없다"는 하드코딩 대신 헤더 수와의 차이로 판정하므로, 컬럼이 늘거나
#   줄어도 같은 규칙이 성립한다. **차이가 0도 1도 아니면 그 행을 버리고 경고한다** —
#   조용히 한 칸 밀린 채 적재하면 발주번호 자리에 SKU가 들어간다(ref 45 §10의 실제 사고 모양).
from __future__ import annotations

import logging
import re
from typing import Any

# 값 변환 헬퍼는 같은 패키지의 1P 파서 것을 그대로 쓴다 — 셀 공백 복구(_despace_numeric)까지
# 같은 규칙이어야 두 파서가 같은 DOM에서 다른 숫자를 내지 않는다(교훈 #140 계열).
from app.clients.coupang.rocket_supplier import _to_dt_utc_naive, _to_int

log = logging.getLogger(__name__)

# ── 표 식별용 헤더 시그니처(전부 포함해야 그 표) ──────────────────────
_SIG_SUMMARY = ("총 납품 수량", "총 입고 수량")
_SIG_LINES = ("발주번호", "납품수량")
_SIG_BOXES = ("집하", "하차")


def _norm(s: Any) -> str:
    """헤더·라벨 비교용 정규화 — 공백을 접는다(SSR 들여쓰기·개행 흡수)."""
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _has_all(headers: list[str], sig: tuple[str, ...]) -> bool:
    joined = [_norm(h) for h in headers]
    return all(any(s == h or s in h for h in joined) for s in sig)


def pick_table(tables: list[dict], sig: tuple[str, ...]) -> dict | None:
    """헤더 시그니처로 표 하나를 고른다. 없으면 None(호출부가 '모름'으로 처리).

    두 개 이상 맞으면 첫 번째를 쓰고 경고한다 — 조용히 아무거나 고르지 않는다.
    """
    hits = [t for t in tables if _has_all(t.get("headers") or [], sig)]
    if not hits:
        return None
    if len(hits) > 1:
        log.warning("쉽먼트 표 시그니처 %s에 %d개가 걸렸다 — 첫 번째를 쓴다", sig, len(hits))
    return hits[0]


def _index_of(headers: list[str], *names: str) -> int | None:
    """헤더 이름으로 컬럼 위치를 찾는다(부분일치 허용). 없으면 None."""
    normed = [_norm(h) for h in headers]
    for name in names:
        for i, h in enumerate(normed):
            if h == name:
                return i
        for i, h in enumerate(normed):
            if name in h:
                return i
    return None


# ════════════════════════════════════════════════
# ① 목록 — 쉽먼트 헤더 그레인
# ════════════════════════════════════════════════
_BOX_QTY_RE = re.compile(r"-?[\d,]+")


def _leading_int(cell: Any) -> int:
    """'1 박스' · '122 개' → 1 · 122. 단위 글자를 떼고 앞 숫자만."""
    m = _BOX_QTY_RE.search(str(cell or ""))
    return _to_int(m.group(0)) if m else 0


def parse_shipment_list(entries: list[dict]) -> list[dict]:
    """목록 행 → 쉽먼트 헤더 레코드.

    entries[i] = {"headers": [...], "cells": [...], "data": {"id","status","type"},
                  "po_content": "138140027<br>138140185<br>…"}
    ★`data-id`(쉽먼트번호)·`data-status`(READY/CONFIRMED/…)·`data-type`(PARCEL/TRUCK)는
      화면 텍스트가 아니라 **속성**이라 한국어 라벨이 바뀌어도 안 흔들린다 → 이쪽을 정본으로 쓴다.
    ★발주서 셀은 "138140027 외 7건"으로 **잘려 있다**. 전체 목록은 popover의 `data-content`에
      `<br>`로 들어 있다(라이브 확인) — 쉽먼트:발주 = 1:N이므로 이걸 놓치면 발주 연결이 샌다.
      다만 라인 표(상세)에 발주번호가 다시 나오므로 여기 값은 **보조**다.
    """
    out: list[dict] = []
    for e in entries or []:
        data = e.get("data") or {}
        cells = e.get("cells") or []
        headers = e.get("headers") or []
        seq = _to_int(data.get("id"))
        if seq <= 0:
            log.warning("쉽먼트 목록 행에 data-id가 없다 — 건너뛴다: %r", cells[:3])
            continue

        def col(*names: str) -> Any:
            i = _index_of(headers, *names)
            return cells[i] if i is not None and i < len(cells) else None

        out.append({
            "shipment_seq": seq,
            "status_code": (str(data.get("status") or "").strip() or None),
            "status_label": _norm(col("쉽먼트 상태", "상태")) or None,
            "shipment_type": (str(data.get("type") or "").strip() or None),
            "invoice_number": _norm(col("송장 번호", "송장")) or None,
            "center_name": _norm(col("센터")) or None,
            "shipped_at": _to_dt_utc_naive(col("발송일")),
            "estimated_arrival_at": _to_dt_utc_naive(col("입고예정일")),
            "box_count": _leading_int(col("박스수")),
            "total_shipped_qty": _leading_int(col("총 납품 수량", "납품 수량")),
            "purchase_order_seqs": _split_po_content(e.get("po_content"), col("발주서")),
        })
    return out


_PO_SPLIT_RE = re.compile(r"<br\s*/?>|[\s,]+", re.I)


def _split_po_content(raw: Any, fallback_cell: Any = None) -> list[int]:
    """popover `data-content`의 `<br>` 구분 발주번호 목록 → int[]. 없으면 셀에서 앞 하나만."""
    src = raw if raw else fallback_cell
    if not src:
        return []
    seqs = []
    for tok in _PO_SPLIT_RE.split(str(src)):
        tok = tok.strip()
        if tok.isdigit():
            seqs.append(int(tok))
    # "138140027 외 7건" → 숫자 토큰이 138140027·7 두 개가 되는데 7은 발주번호가 아니다.
    #   발주번호는 8자리 이상이므로 길이로 거른다(라이브 전건 9자리).
    return [s for s in dict.fromkeys(seqs) if s >= 10_000_000]


# ════════════════════════════════════════════════
# ② 상세 — 라인(박스×발주×SKU)·박스 추적·요약
# ════════════════════════════════════════════════
def parse_shipment_detail(shipment_seq: int, tables: list[dict]) -> dict:
    """상세 페이지의 표들 → {summary, lines, boxes}. 못 찾은 표는 None/빈 리스트.

    ★부분 실패를 허용한다: 라인 표만 있고 박스 추적 표가 없어도(트럭 쉽먼트 등) 라인은 적재한다.
      전부-아니면-전무로 만들면 표 하나가 바뀔 때 수집이 통째로 멈춘다.
    """
    return {
        "shipment_seq": shipment_seq,
        "summary": _parse_summary(tables),
        "lines": _parse_lines(shipment_seq, tables),
        "boxes": _parse_boxes(shipment_seq, tables),
    }


def _parse_summary(tables: list[dict]) -> dict:
    t = pick_table(tables, _SIG_SUMMARY)
    if not t or not (t.get("rows") or []):
        return {}
    headers, row = t.get("headers") or [], (t.get("rows") or [])[0]

    def col(*names: str) -> Any:
        i = _index_of(headers, *names)
        return row[i] if i is not None and i < len(row) else None

    return {
        "po_count": _to_int(col("발주 종수")),
        "sku_count": _to_int(col("SKU 종수")),
        "box_count": _to_int(col("박스수")),
        "total_shipped_qty": _to_int(col("총 납품 수량")),
        "total_received_qty": _to_int(col("총 입고 수량")),
    }


_BOX_LABEL_RE = re.compile(r"박스\s*#?\s*(\d+)")


def _box_key(cell: Any) -> str:
    """'박스 #1 PBL0105694344' → 정규화 라벨. 공백만 접고 원문을 살린다(운송장 라벨 대조용)."""
    return _norm(cell)


def _parse_lines(shipment_seq: int, tables: list[dict]) -> list[dict]:
    """박스 × 발주번호 × SKU × 납품수량 × 입고수량.

    ★rowspan 이월: 셀 수가 헤더 수와 같으면 박스 셀이 있는 행, **정확히 1 적으면** 박스 셀이
      rowspan으로 빠진 행이라 직전 박스를 이어 쓴다. 그 외(2 이상 차이·초과)는 **버리고 경고**한다 —
      한 칸 밀린 채 적재하면 발주번호 자리에 SKU가 들어간다(ref 45 §10).
    """
    t = pick_table(tables, _SIG_LINES)
    if not t:
        log.warning("쉽먼트 %s 라인 표를 못 찾았다(헤더 시그니처 %s)", shipment_seq, _SIG_LINES)
        return []
    headers = t.get("headers") or []
    n = len(headers)
    i_box = _index_of(headers, "박스")
    if i_box is None:
        log.warning("쉽먼트 %s 라인 표에 「박스」 헤더가 없다 — rowspan 이월 불가", shipment_seq)

    out: list[dict] = []
    carried_box: str | None = None
    line_no = 0
    for row in t.get("rows") or []:
        cells = list(row)
        if len(cells) == n:
            pass                                  # 박스 셀이 있는 행
        elif len(cells) == n - 1 and i_box is not None:
            if carried_box is None:
                log.warning("쉽먼트 %s: 첫 행부터 박스 셀이 없다 — 행을 버린다: %r",
                            shipment_seq, cells[:3])
                continue
            cells.insert(i_box, carried_box)      # rowspan 이월
        else:
            log.warning("쉽먼트 %s: 셀 수 %d가 헤더 %d와 안 맞는다 — 행을 버린다: %r",
                        shipment_seq, len(cells), n, cells[:4])
            continue

        def col(*names: str) -> Any:
            i = _index_of(headers, *names)
            return cells[i] if i is not None and i < len(cells) else None

        box_label = _box_key(col("박스"))
        if box_label:
            carried_box = box_label
        po_seq = _to_int(col("발주번호"))
        # ★SKU 컬럼은 `col("SKU")`로 뽑으면 안 된다 — `_index_of`의 부분일치가 'SKU 이름'·
        #   'SKU 바코드'에도 걸린다. 정확일치가 먼저 시도되므로 실제로는 맞지만, 헤더가
        #   'SKU 코드' 따위로 바뀌는 순간 조용히 상품명을 상품번호로 넣게 된다.
        i_sku = _index_of(headers, "SKU")
        product_number = _norm(cells[i_sku]) if i_sku is not None and i_sku < len(cells) else ""
        if po_seq <= 0 or not product_number:
            log.warning("쉽먼트 %s: 발주번호/SKU 없는 행 — 버린다: %r", shipment_seq, cells[:4])
            continue

        line_no += 1
        out.append({
            "shipment_seq": shipment_seq,
            "line_no": line_no,
            "box_label": box_label or carried_box or "",
            "purchase_order_seq": po_seq,
            "product_number": product_number,
            "product_name": _norm(col("SKU 이름")) or None,
            "barcode": _norm(col("SKU 바코드")) or None,
            "shipped_qty": _to_int(col("납품수량")),
            "received_qty": _to_int(col("입고수량")),
        })
    return out


def _parse_boxes(shipment_seq: int, tables: list[dict]) -> list[dict]:
    """박스 × 상태 × 송장 × 집하·도착·하차.

    ★이 표가 **제3자(택배사·물류센터) 기록**이다 — 미수금 청구에서 "정말 보냈나"의 증거 등급을
      정하는 유일한 축이다(ref 45 §4-1: 하차 확인 130라인이 1차 청구 권장분이었다).
      「납품수량」은 우리 신고값이라 우리 말이고, 하차 시각은 쿠팡·택배사 말이다.
    """
    t = pick_table(tables, _SIG_BOXES)
    if not t:
        return []
    headers = t.get("headers") or []
    out: list[dict] = []
    for row in t.get("rows") or []:
        def col(*names: str) -> Any:
            i = _index_of(headers, *names)
            return row[i] if i is not None and i < len(row) else None

        label = _box_key(col("박스"))
        if not label:
            continue
        out.append({
            "shipment_seq": shipment_seq,
            "box_label": label,
            "status_label": _norm(col("상태")) or None,
            "invoice_number": _norm(col("송장 번호", "송장")) or None,
            "picked_up_at": _to_dt_utc_naive(col("집하")),
            "arrived_at": _to_dt_utc_naive(col("도착")),
            "unloaded_at": _to_dt_utc_naive(col("하차")),
        })
    return out
