# rocket_supplier_sync.py — 쿠팡 로켓배송(1P) 발주/정산 ingest+store Harness (트랙 rocket-1p S2)
#
# 소스: supplier.coupang.com (ref 20, D-9). 발주+납품 = list JSON, 정산 = SSR DOM rows.
# 런타임 경계(D-1): Akamai 봇방어 → 백엔드 requests 직접 호출 금지.
#   Mac 헤드풀 CDP 페처(S3)가 수집 → raw push(아래 ingest) → 파서(clients/coupang/rocket_supplier.py)
#   정규화 → snapshot upsert. 이 Harness는 push 수신·저장·조회만(읽기전용·net_profit 불변).
#
# 단일 책임 조합(원칙18-2): 파서 SA 호출 → 모델 upsert. grain: 발주=purchase_order_seq,
#   정산=invoice_seq. 같은 seq 재수신 시 확정치 교체(per-row upsert, 멱등).
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.clients.coupang import rocket_supplier as parser
from app.models import (
    CoupangRocketPoChangeLog,
    CoupangRocketPoIngestRound,
    CoupangRocketPurchaseOrder,
    CoupangRocketPurchaseOrderItem,
    CoupangRocketSettlement,
    CoupangRocketSettlementItem,
    CoupangWingCookie,
)
from app.services.coupang import refresh_contract
from app.utils.kst import kst_now

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════
# ① + ② 발주/납품 ingest
# ════════════════════════════════════════════════
# ★관측 원장이 기록하는 8종(계약 CONTRACT_1p_po_status_history §1 — 늘리지 않는다).
#   상태 1 + 수량 3 + 금액 3. 수량·금액을 넣는 이유: RP에서 납품가능수량을 깎으면 **상태는
#   그대로인데 ①금액이 준다** — 상태만 보면 「①이 줄어든 이유 3종(확정/감액/수집누락)」 중
#   감액이 통째로 안 보인다.
_TRACKED_FIELDS = (
    "purchase_order_status",
    "order_qty", "receiving_qty", "vendor_confirmed_qty",
    "sum_of_order_amount", "sum_of_receiving_amount", "sum_of_vendor_confirmed_amount",
)


def _s(v) -> "str | None":
    """비교·저장용 문자열화. None은 None으로 남긴다(빈 문자열과 구분 — 원칙22)."""
    return None if v is None else str(v)


def _po_change_events(row: "CoupangRocketPurchaseOrder | None", rec: dict, now) -> list[dict]:
    """덮어쓰기 «직전» row와 새 rec를 비교해 관측 이벤트를 만든다. 순수 함수(테스트가 여기를 겨눈다).

    ★«우리가 본 것»만 만든다 — 시점을 단정하지 않는다. 모든 변화는
      `prev_observed_at ~ observed_at` **구간**에 귀속되고, 신규는 전이가 아니라 **출현**이다.
      「PA로 처음 관측됨」과 「RP에서 PA로 바뀌는 것을 봄」은 **다른 사실**이다(계약 §2).
    ★diff가 있을 때만 행을 만든다 — 그래서 재수신이 저절로 멱등이다.
    ★추가 쿼리 0회: 호출자가 덮어쓰기 직전에 이미 로드해 둔 row를 그대로 쓴다.
    """
    seq = rec["purchase_order_seq"]
    vid = rec["vendor_id"]
    if row is None:
        # 처음 봤다. 직전 관측이 없으므로 prev_observed_at은 NULL이고 before도 없다.
        # ★이것은 «신규 발주 발생»이 아니라 «처음 관측»이다(계약 §3 금지선).
        return [{
            "purchase_order_seq": seq, "vendor_id": vid,
            "event": "first_seen", "field": "",
            "before_value": None,
            "after_value": _s(rec["purchase_order_status"]),
            "observed_at": now, "prev_observed_at": None,
        }]

    prev_seen = row.synced_at  # 덮어쓰기 전이라 이게 «직전 관측 시각»이다
    out: list[dict] = []
    for f in _TRACKED_FIELDS:
        # ★`rec[f]`로 통일한다(적대 리뷰 1R P2-4): `_upsert_po`는 `rec[f]`로 읽으므로,
        #   여기서만 `.get`을 쓰면 파서가 키를 빠뜨렸을 때 «384 → None»이라는 **없던 변화**를
        #   먼저 기록하고 직후 KeyError로 죽는다. 같은 계약으로 읽어 그 틈을 없앤다.
        before, after = getattr(row, f, None), rec[f]
        if _s(before) == _s(after):
            continue
        out.append({
            "purchase_order_seq": seq, "vendor_id": vid,
            "event": "field_change", "field": f,
            "before_value": _s(before), "after_value": _s(after),
            "observed_at": now, "prev_observed_at": prev_seen,
        })
    return out


def _upsert_po(db: Session, rec: dict, *, events: list[dict] | None = None, now=None) -> None:
    row = (
        db.query(CoupangRocketPurchaseOrder)
        .filter(CoupangRocketPurchaseOrder.purchase_order_seq == rec["purchase_order_seq"])
        .first()
    )
    # ★덮어쓰기 «전»에 관측 이벤트를 뽑는다 — 이 순서가 이 기능의 전부다.
    #   아래 대입이 시작되면 직전 값은 이 프로세스 어디에도 남지 않는다.
    if events is not None:
        events.extend(_po_change_events(row, rec, now or kst_now()))
    if row is None:
        row = CoupangRocketPurchaseOrder(purchase_order_seq=rec["purchase_order_seq"])
        db.add(row)
    row.vendor_id = rec["vendor_id"]
    row.sum_of_order_amount = rec["sum_of_order_amount"]
    row.sum_of_receiving_amount = rec["sum_of_receiving_amount"]
    row.sum_of_vendor_confirmed_amount = rec["sum_of_vendor_confirmed_amount"]
    row.order_qty = rec["order_qty"]
    row.receiving_qty = rec["receiving_qty"]
    row.vendor_confirmed_qty = rec["vendor_confirmed_qty"]
    row.purchase_order_status = rec["purchase_order_status"]
    row.purchase_order_status_description = rec["purchase_order_status_description"]
    row.purchase_type = rec["purchase_type"]
    row.center_code = rec["center_code"]
    row.center_name = rec["center_name"]
    row.first_sku_name = rec["first_sku_name"]
    row.sku_count = rec["sku_count"]
    row.po_created_at = rec["po_created_at"]
    row.expected_delivery_date = rec["expected_delivery_date"]
    # 실입고 시각 — 옛 파서가 만든 rec에는 키가 없을 수 있어 .get으로 읽는다(배포 순서 무관하게 안전).
    row.receiving_started_at = rec.get("receiving_started_at")
    row.receiving_finished_at = rec.get("receiving_finished_at")
    row.vendor_payment_seqs = rec["vendor_payment_seqs"]
    # ★한 회차는 한 시각이다. `now`가 오면 그걸 쓴다 — 안 그러면 원장의 `synced_at`(레코드마다
    #   제각각)과 이벤트의 `observed_at`(회차 하나)이 갈리고, **다음 회차의 «구간 왼쪽 끝»이
    #   어긋난다**(prev_observed_at은 이 값에서 읽는다).
    row.synced_at = now or kst_now()


def ingest_purchase_orders(db: Session, pages: list[dict]) -> dict:
    """Mac 페처가 보낸 발주 list 페이지들(raw JSON) → 파싱 → snapshot upsert.

    pages: [{발주 list API 한 페이지 raw JSON}, ...] (page=1..lastPageNumber 루프 결과).
    멱등: 같은 purchase_order_seq 재수신 시 확정치로 교체.
    반환: {ingested(PO 수), pages(페이지 수), changes(적재된 관측 이벤트), changes_dropped(버린 수)}.

    ★관측 원장(`coupang_rocket_po_change_log`) — 계약 CONTRACT_1p_po_status_history.
      덮어쓰기 «직전»에 diff를 떠서 이벤트로 남긴다. 이게 없으면 원장은 현재 단면만 갖고,
      「①이 왜 줄고 ②가 왜 늘었나」에 아무도 답하지 못한다(2026-08-28 실사고).
    ★★**이벤트 실패는 본 수집을 막지 않는다**(계약 §2·§3): 이 저장소는 부가 경로가 본 ingest를
      통째로 침묵시킨 사고 이력이 있다. 그래서 이벤트 적재를 통째로 감싸고, 실패하면 이벤트만
      버린 뒤 **WARNING + 카운트로 자백**한다 — 조용히 0으로 접지 않는다.
    """
    n = 0
    now = kst_now()          # 한 회차는 한 시각으로 묶는다(구간의 오른쪽 끝)
    events: list[dict] = []
    for payload in pages or []:
        if not isinstance(payload, dict):
            continue
        for rec in parser.parse_purchase_order_list(payload):
            _upsert_po(db, rec, events=events, now=now)
            n += 1
    db.commit()

    dropped, err = _persist_po_change_events(db, events)
    # ★회차 결과를 **저장**한다 — 로그·응답에만 두면 화면이 원리적으로 못 읽는다
    #   (적대 리뷰 1R P1-1: 적재가 통째로 실패한 회차에도 화면이 「달라진 발주가 없습니다」를
    #   단언했다. 침묵이 아니라 거짓말이라 더 나쁘다).
    _record_ingest_round(db, now, records=n, changes=len(events) - dropped,
                         dropped=dropped, error=err)
    log.info(
        "rocket PO ingest: pages=%d records=%d changes=%d dropped=%d",
        len(pages or []), n, len(events) - dropped, dropped,
    )
    return {
        "ingested": n, "pages": len(pages or []),
        "changes": len(events) - dropped, "changes_dropped": dropped,
    }


def _persist_po_change_events(db: Session, events: list[dict]) -> tuple[int, "str | None"]:
    """관측 이벤트를 적재한다. 반환 = (버린 개수, 실패 사유). 정상이면 (0, None).

    ★본 수집(위에서 이미 commit됨)을 절대 되돌리지 않는다 — 실패하면 rollback 후 0건으로 끝낸다.
    ★유니크 제약(seq, event, field, observed_at)은 diff-only 위의 안전망이다. 같은 회차를 두 번
      돌려도 행이 안 는다.
    """
    if not events:
        return 0, None
    try:
        db.bulk_insert_mappings(CoupangRocketPoChangeLog, events)
        db.commit()
        return 0, None
    except Exception as e:  # noqa: BLE001 — 이력은 부가 산출물이지 수집의 전제가 아니다
        db.rollback()
        msg = str(e)[:200]
        log.warning(
            "[po-change] 관측 이벤트 %d건 적재 실패 — 이벤트만 버린다(수집은 유지): %s",
            len(events), msg,
        )
        return len(events), msg


def _record_ingest_round(db: Session, observed_at, *, records: int, changes: int,
                         dropped: int, error: "str | None") -> None:
    """회차 결과 1행. ★이것도 실패해도 본 수집을 막지 않는다(같은 이유).

    ★`dropped > 0`인 회차를 화면이 「달라진 게 없다」로 말하지 못하게 하는 것이 이 행의 전부다.
    """
    try:
        row = (
            db.query(CoupangRocketPoIngestRound)
            .filter(CoupangRocketPoIngestRound.observed_at == observed_at).first()
        )
        if row is None:
            row = CoupangRocketPoIngestRound(observed_at=observed_at)
            db.add(row)
        row.records, row.changes, row.dropped, row.error = records, changes, dropped, error
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.warning("[po-change] 회차 결과 기록 실패(수집은 유지): %s", str(e)[:200])


# ════════════════════════════════════════════════
# ③ 정산 ingest
# ════════════════════════════════════════════════
def _upsert_settlement(db: Session, vendor_id: str, rec: dict) -> None:
    row = (
        db.query(CoupangRocketSettlement)
        .filter(CoupangRocketSettlement.invoice_seq == rec["invoice_seq"])
        .first()
    )
    if row is None:
        row = CoupangRocketSettlement(invoice_seq=rec["invoice_seq"])
        db.add(row)
    row.vendor_id = vendor_id
    row.supply_amount = rec["supply_amount"]
    row.vat = rec["vat"]
    row.payment_amount = rec["payment_amount"]
    row.issue_date = rec["issue_date"]
    row.payment_date = rec["payment_date"]
    row.tax_invoice_confirmed_date = rec["tax_invoice_confirmed_date"]
    row.settlement_type = rec["settlement_type"]
    row.bill_issue_type = rec["bill_issue_type"]
    row.tax_type = rec["tax_type"]
    row.first_payment_amount = rec["first_payment_amount"]
    row.second_payment_amount = rec["second_payment_amount"]
    row.tax_invoice_transmitted = rec["tax_invoice_transmitted"]
    row.synced_at = kst_now()


def ingest_settlements(db: Session, vendor_id: str, rows: list[list]) -> dict:
    """Mac 페처가 보낸 정산 DOM rows(헤더 포함) → 파싱 → snapshot upsert.

    rows: 정산 테이블 DOM(rows[0]=헤더). vendor_id는 계정축(정산 row엔 거래처명만 있어 별도 주입).
    멱등: 같은 invoice_seq 재수신 시 확정치로 교체.
    반환: {ingested(계산서 수)}.
    """
    recs = parser.parse_settlement_rows(rows)
    for rec in recs:
        _upsert_settlement(db, vendor_id, rec)
    db.commit()
    log.info("rocket settlement ingest: vendor=%s records=%d", vendor_id, len(recs))
    return {"ingested": len(recs)}


# ════════════════════════════════════════════════
# 발주상세 per-SKU ingest (S4.5a, D-13) — snapshot replace
# ════════════════════════════════════════════════
def ingest_po_items(db: Session, purchase_order_seq: int, vendor_id: str, rows: list[list]) -> dict:
    """Mac 페처가 보낸 발주상세 Table[7] DOM rows → 파싱 → 해당 PO **snapshot replace**.

    rows: 발주상세 DOM(헤더·SKU·합계행 혼재). 위치 기반 파서가 SKU 행만 추출.
    vendor_id: 계정축(발주상세 DOM엔 거래처 없어 별도 주입, 정산 ingest와 동일).
    멱등: 같은 purchase_order_seq의 기존 라인아이템 전부 삭제 후 재삽입(SKU 제거 반영, 누적 방지).
    반환: {ingested(SKU 수), purchase_order_seq}.
    """
    recs = parser.parse_po_item_rows(rows)
    # snapshot replace: 이 PO의 기존 라인 삭제(타 PO 불변) → 재삽입.
    #   ORM 로드 후 delete + flush(동일 세션 재적재 시 identity-map 충돌 회피, per-PO N≤50 소량).
    existing = (
        db.query(CoupangRocketPurchaseOrderItem)
        .filter(CoupangRocketPurchaseOrderItem.purchase_order_seq == purchase_order_seq)
        .all()
    )
    for old in existing:
        db.delete(old)
    db.flush()
    now = kst_now()
    for rec in recs:
        db.add(CoupangRocketPurchaseOrderItem(
            purchase_order_seq=purchase_order_seq,
            vendor_id=vendor_id,
            line_no=rec["line_no"],
            product_number=rec["product_number"],
            barcode=rec["barcode"] or None,
            product_name=rec["product_name"],
            purchase_type=rec["purchase_type"],
            order_qty=rec["order_qty"],
            vendor_confirmed_qty=rec["vendor_confirmed_qty"],
            unit_purchase_price=rec["unit_purchase_price"],
            line_order_amount=rec["line_order_amount"],
            line_supply_amount=rec["line_supply_amount"],
            line_vat=rec["line_vat"],
            synced_at=now,
        ))
    db.commit()
    log.info("rocket PO 발주상세 ingest: po=%d vendor=%s skus=%d", purchase_order_seq, vendor_id, len(recs))
    return {"ingested": len(recs), "purchase_order_seq": purchase_order_seq}


# ════════════════════════════════════════════════
# ④ 페처 갱신 트리거(버튼-poll) — Wing 패턴 복제(ohitech_ad_sync 동형)
#   로켓배송 supplier.coupang.com은 Akamai 봇방어(D-1) → 백엔드 직접 fetch 불가.
#   UI '로켓 갱신' 버튼 → refresh_requested_at set → Mac 헤드풀 CDP 페처(S3)가 다음 폴에서
#   claim(소비) → headful fetch·push → run 성공 시 mark_rocket_fetch_success로 완료 감지.
#   상태는 CoupangWingCookie(account_key="COUPANG_ROCKET") 한 행에 저장(민감값 없음).
# ════════════════════════════════════════════════
_ROCKET_ACCOUNT = "COUPANG_ROCKET"


def _rocket_state_row(db: Session):
    return (
        db.query(CoupangWingCookie)
        .filter(CoupangWingCookie.account_key == _ROCKET_ACCOUNT)
        .first()
    )


def _ensure_rocket_state_row(db: Session):
    row = _rocket_state_row(db)
    if row is None:
        row = CoupangWingCookie(account_key=_ROCKET_ACCOUNT)
        db.add(row)
    return row


def request_rocket_refresh(db: Session) -> dict:
    """UI '로켓 갱신' 버튼 → 갱신 요청 set. 성공하거나 3회 실패할 때까지 살아있다(lease 계약)."""
    return refresh_contract.request_refresh(db, _ROCKET_ACCOUNT)


def rocket_refresh_status(db: Session) -> dict:
    """갱신 요청/완료 상태. UI 폴링·페처 소비 공용(민감값 없음).

    last_error_at=마지막 실패 시각(버튼 후 이 값이 올라가면 갱신 실패) — UI가 성공/실패 둘 중
    무엇이 왔는지 이 두 시각의 변화로 가른다. 없으면 실패를 못 보고 폴링 창을 헛기다린다.
    """
    row = _rocket_state_row(db)
    if row is None:
        return {"requested": False, "requested_at": None, "last_success_at": None,
                "status": "none", "last_error": None, "last_error_at": None,
                **refresh_contract.status_fields(None)}
    return {
        "requested": row.refresh_requested_at is not None,
        "requested_at": row.refresh_requested_at.isoformat() if row.refresh_requested_at else None,
        "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
        "status": row.status,
        "last_error": row.last_error,
        "last_error_at": row.last_error_at.isoformat() if row.last_error_at else None,
        **refresh_contract.status_fields(row),
    }


def claim_rocket_refresh(db: Session) -> dict:
    """페처가 갱신 요청을 **임대**(lease). 플래그는 성공/소진까지 보존(refresh_contract)."""
    return refresh_contract.claim_refresh(db, _ROCKET_ACCOUNT)


def mark_rocket_fetch_success(db: Session) -> None:
    """페처 run 성공 시 last_success_at 갱신(UI 폴링 완료 감지용).

    ★lease 계약: 갱신 요청이 소멸하는 정상 경로는 여기 하나뿐이다(claim은 소비하지 않는다).
    """
    row = _ensure_rocket_state_row(db)
    row.last_success_at = kst_now()
    row.status = "green"
    row.last_error = None
    row.last_error_at = None  # 성공 = 실패 흔적 클리어(안 지우면 오래된 실패가 화면에 남는다)
    db.commit()
    refresh_contract.mark_success(db, _ROCKET_ACCOUNT)


def mark_rocket_fetch_error(db: Session, error: str, kind: str | None = None, lease: str | None = None) -> None:
    """페처 run 실패 보고 → last_error/last_error_at 기록(UI가 실패를 감지하는 유일 경로).

    ★존재 이유(PR #30이 광고비에서 먼저 고친 것과 같은 구멍): 페처가 갱신 요청을 claim한
    뒤(=플래그 이미 clear) 브라우저 에러로 죽으면 prod에 아무 흔적도 안 남았다. 성공에만
    시각(last_success_at)이 있고 실패엔 짝이 없어서 UI는 "실패"와 "아직 진행 중"을 구분할
    수단이 없었다 — 215초를 헛기다린 뒤 "Mac 응답 없음"이라는 뭉뚱그린 문구만 냈다.
    형제 mark_rocket_fetch_success의 짝.

    ★status는 일부러 건드리지 않는다(PR #30 codex 1R[P2]): status=red는 Layout 배너에서
    곧바로 "쿠키 만료(재설정 필요)" + 쿠키 재설정 CTA로 렌더된다(Layout.tsx:201/206).
    브라우저 크래시는 쿠키 문제가 아니라 재설정해도 헛수고다. 지속 실패는 워치독이
    last_success_at 경과로 잡는다(status 미의존).

    ★kind(옵션): "login_required"면 재시도하지 않고 요청 소멸(§0 금지선 — 재시도해도 실패하고
    창만 반복해서 뜬다). 그 외 실패는 lease만 반납해 다음 폴에서 재시도된다(최대 3회).
    """
    _ensure_rocket_state_row(db)
    db.commit()  # 행이 없던 경우 대비(계약 SA는 기존 행에만 쓴다)
    refresh_contract.report_failure(db, _ROCKET_ACCOUNT, error, kind, lease=lease)


# ════════════════════════════════════════════════
# ⑥ 계산서 라인(입고상세내역) ingest — D-CPP-20
# ════════════════════════════════════════════════
def ingest_settlement_items(db: Session, vendor_id: str, invoice_seq: int,
                            rows: list[list], expected_total: int | None = None) -> dict:
    """한 계산서의 입고상세 DOM rows → 라인 **snapshot replace**(그 계산서 전 행 삭제 후 재삽입).

    왜 replace인가: 원천에 라인 식별자가 없다(같은 PO·SKU가 여러 줄일 수 있다). 자연키를 지어내
      upsert하면 라인이 줄어든 경우를 못 지운다 → 계산서 단위로 통째 교체하는 편이 정직하다.

    ★`expected_total`(원천 `totalCount`)을 받으면 **행 수를 대조**해 결과에 담는다.
      페이징이 조용히 잘리는 사고를 겪었다 — `page`만으로는 20/48행에서 멈췄고, 폼의
      `totalCount`를 함께 실어야 전량이 온다. 여기서 수를 안 재면 그 절단이 성공으로 보인다.

    반환: {invoice_seq, lines, expected, truncated, total_price} — truncated=True면 호출부가 실패로 다뤄야 한다.
    """
    recs = parser.parse_settlement_item_rows(rows or [])
    (db.query(CoupangRocketSettlementItem)
       .filter(CoupangRocketSettlementItem.invoice_seq == invoice_seq)
       .delete(synchronize_session=False))
    total = 0
    for rec in recs:
        db.add(CoupangRocketSettlementItem(
            invoice_seq=invoice_seq, vendor_id=vendor_id, **rec))
        total += int(rec["total_price"])
    db.commit()
    truncated = bool(expected_total) and len(recs) < int(expected_total)
    if truncated:
        log.warning("rocket 계산서라인 %s: %d/%s행만 수신 — 절단(페이징 totalCount 누락 의심)",
                    invoice_seq, len(recs), expected_total)
    return {"invoice_seq": invoice_seq, "lines": len(recs),
            "expected": expected_total, "truncated": truncated, "total_price": total}
