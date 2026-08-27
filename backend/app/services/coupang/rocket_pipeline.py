# rocket_pipeline.py — 로켓배송(1P) «열린 파이프라인» 조회 Harness (계약 CONTRACT_1p_invoice_gap).
#
# 목적(Jino 원문 2026-08-27): "1P의 경우 발행 후 계산서가 미발행된 내역을 sellC에서 보고 싶어" /
#   "거래명세서확인요청 내용을 SellC에서 모아서 볼 수 있나" / "이것까지 넣어서 종합적으로 보여줘".
#   발주가 돈이 되기까지 네 칸이 있는데, 지금은 어느 칸에 얼마가 걸려 있는지 화면이 없다.
#
# ★기존 `rocket_recon.py`와 무엇이 다른가 — **축이 다르다.**
#   recon = 발주일(po_created_at) KST 윈도우 × SKU 그레인 × 「발주≠입고 드리프트」.
#   여기  = 창 없음(열려 있는 것 전부) × PO 그레인 × 「지금 어느 칸에 얼마」.
#   recon은 계산서 미연결을 **건수**로만 냈고, 발송(ASN) 축과 계산서 축이 교차한 적이 없다
#   (`rocket_recon.py`에 shipped_at grep 0건). 그 빈칸이 이 모듈이다.
#
# 단일 책임 SA(원칙18-1):
#   ① _shipped_by_po   : 발송(ASN) 라인 → PO별 발송수량·발송금액(단가는 발주상세에서)
#   ② _pipeline_rows   : PO 그레인 원장 한 판 (확정·발송·입고·계산서연결·신선도)
#   ③ _stage_of        : 한 PO를 칸으로 분류 + 칸별 금액 (분류 규칙의 단일 자리)
#   ④ compute_rocket_pipeline     : 요약(칸 4개 + 소계 + 별도 덩어리 + clamp + 신선도)
#   ⑤ compute_rocket_pipeline_rows: 한 칸의 PO 목록 (행 확장용)
#   ⑥ compute_rocket_ri_queue     : 「확인요청함」 — RI 상태 PO + 살아있음/굳음 판정
#
# ★★칸은 서로 겹치지 않는다 (계약 §2). 겹치는 것은 칸이 아니라 «액션 목록»으로 낸다 —
#   RI 5,351,520원은 이미 계산서가 나가 ④지급대기에 포함돼 있으므로 칸으로 더하면 **중복**이다.
#   그래서 RI는 `compute_rocket_ri_queue`가 따로 내고 파이프라인 합계에 안 들어간다.
#
# ★★미종결과 종결단계를 가른다. `rocket_recon.SETTLED_STAGE_STATUSES`({CI,RI})가 이미
#   「입고가 끝난 단계」를 전수 실측으로 정의해 뒀다. 그 자를 그대로 쓴다:
#     · 미종결(RP·PA)의 conf−ship = **발송 대기**(앞으로 보낼 것)
#     · 미종결(PA)의 ship−recv    = **입고 대기**(쿠팡이 아직 안 잡음 = 계산서 미발행)
#     · 종결단계(RI·CI)의 conf−ship = **영영 못 보내는 잔재**(확정 시 취소분이 아니라, 확정했는데
#       발송 없이 닫힌 것). 대기가 아니므로 소계에서 뺀다.
#     · 종결단계(RI·CI)의 ship−recv = **미해명**. ①덜 보냄 ②반송 ③진짜 미수금이 구별 불가로
#       섞여 있고, 가르는 열쇠(발주상세 「입고 메세지」·「회송 정보」·「변경 이력」)를 우리는
#       수집하지 않는다. 2026-08-05에 이 값만 믿고 미수금을 5,763,290원 과대계상한 전례가 있다.
#       **확정 숫자가 아니므로 소계에 절대 합산하지 않는다**(계약 §3 금지선).
#
# ★원칙22(0으로 접지 않기):
#   1) 발송 라인의 단가는 발주상세(`coupang_rocket_purchase_order_item`)에서 온다. 매칭이 없으면
#      금액을 0으로 더하지 않고 `unpriced_shipped_qty`로 **센다**(2026-08-27 실측 결손 0건이지만
#      수집이 늦은 PO에서 언제든 생긴다).
#   2) `shipment_item.received_qty`는 **쓰지 않는다.** ASN 화면 값이라 재발송분 입고를 못 본다.
#      입고 판정의 정본은 PO 헤더 `sum_of_receiving_amount`뿐이다(계약 §3 금지선).
#   3) max(0, …) clamp가 발생하면 숨기지 않고 `clamp`로 **건수·금액을 표면화**한다. 조용한 절단
#      금지(계약 §2).
#
# ★신선도 = 「지금 참인 상태」와 「마지막으로 본 상태」를 가르는 축. 수집 창이 **발주일 기준
#   기본 30일**이라 그보다 오래된 미종결 PO는 상태가 영구히 굳는다(2026-08-27 실측: RP 24 ·
#   PA 9 · RI 8 = 41건). 굳은 것을 산 것처럼 보여주면 Jino가 이미 돈까지 받은 줄을 누른다.
#   판정자는 「오늘」이 아니라 **마지막 수집일**(max(date(synced_at)))이다 — 수집이 하루 걸러
#   돌아도 오탐이 안 나게.
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import and_, case, func
from sqlalchemy.orm import Session

from app.models import (
    CoupangRocketPurchaseOrder,
    CoupangRocketPurchaseOrderItem,
    CoupangRocketSettlement,
    CoupangRocketShipment,
    CoupangRocketShipmentItem,
)
from app.services.coupang.rocket_recon import SETTLED_STAGE_STATUSES

log = logging.getLogger(__name__)

_Z = Decimal("0")
_KST = timedelta(hours=9)

# 칸 정의 — key는 프론트 계약이다(라벨은 화면 몫, 여기 든 것은 «설명»이지 표시 문구가 아니다).
STAGE_AWAIT_CONFIRM = "await_confirm"   # ① 발주 왔고 우리가 아직 확정 안 함 (RP)
STAGE_AWAIT_SHIP = "await_ship"         # ② 확정했고 아직 안 보냄 (PA)
STAGE_AWAIT_RECEIVE = "await_receive"   # ③ 보냈는데 쿠팡이 아직 안 잡음 = 계산서 미발행 (PA)
STAGE_AWAIT_PAYMENT = "await_payment"   # ④ 계산서 나감, 지급일 미도래 (계산서 그레인)

# ①②③만 소계에 든다. ④는 계산서 그레인이라 PO 소계와 축이 다르다.
PRE_INVOICE_STAGES = (STAGE_AWAIT_CONFIRM, STAGE_AWAIT_SHIP, STAGE_AWAIT_RECEIVE)


def _f(v) -> Decimal:
    """None/숫자 → Decimal. 집계(func.sum)가 None(행 없음)이면 0."""
    if v is None:
        return _Z
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


# ★★이 저장소의 1P 시간 필드는 **두 규약이 섞여 있다.** 하나로 알고 쓰면 하루가 밀린다.
#   실측(2026-08-27, prod 전수)으로 확정한 표 — 추정이 아니다:
#     · po_created_at        = **UTC naive**. JSON `createdAt`(+00:00)을 `_to_dt_utc_naive`가
#       실제로 환산한다. 시각 분포가 3·6·0·23시대에 몰림(= KST 12·15·9·8시) ⇒ UTC.
#     · receiving_finished_at= **UTC naive**. 같은 JSON·같은 파서.
#     · shipped_at           = **KST naive**. 원천이 JSON이 아니라 **발주상세 DOM 셀 「발송일」**
#       이라 문자열에 tz가 없고, `_to_dt_utc_naive`는 tzinfo가 있을 때만 환산하므로 **그대로
#       통과**한다(함수 이름이 여기서는 사실과 다르다). 992건 중 **965건이 16시대** = 16:00
#       발송 마감(KST). UTC로 읽으면 새벽 1시 발송이 되어 말이 안 된다.
#     · synced_at            = **KST naive**. `rocket_supplier_sync.py:62,109`가 `kst_now()`를
#       직접 넣는다. 실측: max(synced_at) 16:19 > `datetime('now')` 10:01(UTC) ⇒ KST.
#   ⇒ 헬퍼를 둘로 나눈다. 어느 것을 쓸지는 **필드의 원천**(JSON인가 DOM인가, 파서가 넣나
#     kst_now()가 넣나)이 정하지 이름이 정하지 않는다.


def _kst_date_str(dt) -> "str | None":
    """**UTC naive** datetime → KST 날짜 문자열. None이면 None(미상 유지 — 0으로 접지 않는다).

    쓸 곳: po_created_at · receiving_finished_at.
    """
    if dt is None:
        return None
    return (dt + _KST).date().isoformat()


def _kst_naive_date_str(dt) -> "str | None":
    """**이미 KST naive**인 datetime → 날짜 문자열. 환산하지 않는다.

    쓸 곳: synced_at · shipped_at. 여기에 `_kst_date_str`를 쓰면 하루가 밀려, 마지막 수집일이
      내일로 나오고 신선도 판정(오늘 갱신분 ↔ 굳은 것)이 **전건 오답**이 된다.
    """
    if dt is None:
        return None
    return dt.date().isoformat()


def _kst_window_naive(dfrom: date, dto: date) -> tuple[datetime, datetime]:
    """KST 날짜 윈도우 [dfrom, dto] → **환산 없는** naive 경계. shipped_at 전용.

    `rocket_recon._kst_window_utc`와 일부러 다르다 — 그쪽은 po_created_at(UTC 저장)용이고
      여기는 shipped_at(KST 저장)용이다. 같은 함수를 돌려 쓰면 창이 9시간 어긋난다.
    """
    return datetime.combine(dfrom, time.min), datetime.combine(dto, time.max)


# ──────────────────────────────────────────────
# ① 발송(ASN) → PO별 발송수량·발송금액
# ──────────────────────────────────────────────
def _shipped_by_po(
    db: Session,
    vendor_id: str | None,
    ship_from: date | None = None,
    ship_to: date | None = None,
):
    """PO별 발송 집계 서브쿼리.

    금액 = Σ(발송수량 × 발주상세 매입단가). 단가는 `coupang_rocket_purchase_order_item`에만 있어
      **outer join**으로 붙이고, 매칭이 없으면 금액에 0을 더하는 대신 `unpriced_qty`로 센다
      (원칙22 — 미수집을 0원으로 접으면 발송액이 조용히 작아지고 미발행이 과소해진다).

    ★`received_qty`는 읽지 않는다. ASN 화면 값이라 재발송 입고를 못 본다(계약 §3).

    ship_from/ship_to를 주면 **그 창에 발송된 라인만** 집계한다. 창을 주면 「그 창에 발송이 있는
      PO」를 고르는 용도이지 PO 전체 발송액을 대체하지 않는다 — 호출부가 그 구분을 진다.
    """
    SI = CoupangRocketShipmentItem
    PI = CoupangRocketPurchaseOrderItem
    priced = SI.shipped_qty * PI.unit_purchase_price
    q = (
        db.query(
            SI.purchase_order_seq.label("po_seq"),
            func.coalesce(func.sum(SI.shipped_qty), 0).label("ship_qty"),
            func.coalesce(
                func.sum(case((PI.id.isnot(None), priced), else_=0)), 0
            ).label("ship_gross"),
            func.coalesce(
                func.sum(case((PI.id.is_(None), SI.shipped_qty), else_=0)), 0
            ).label("unpriced_qty"),
        )
        .outerjoin(
            PI,
            and_(
                PI.purchase_order_seq == SI.purchase_order_seq,
                PI.product_number == SI.product_number,
            ),
        )
    )
    if vendor_id is not None:
        q = q.filter(SI.vendor_id == vendor_id)
    if ship_from is not None or ship_to is not None:
        # 발송일 창 — 헤더(CoupangRocketShipment.shipped_at)가 날짜를 가진다.
        dfrom = ship_from or date(2000, 1, 1)
        dto = ship_to or date(2999, 12, 31)
        start, end = _kst_window_naive(dfrom, dto)
        q = q.join(CoupangRocketShipment, CoupangRocketShipment.shipment_seq == SI.shipment_seq).filter(
            CoupangRocketShipment.shipped_at.isnot(None),
            CoupangRocketShipment.shipped_at >= start,
            CoupangRocketShipment.shipped_at <= end,
        )
    return q.group_by(SI.purchase_order_seq).subquery()


def _last_collection_day(db: Session, vendor_id: str | None) -> "str | None":
    """마지막 수집일(KST) = max(date(synced_at)). 신선도 판정의 기준선.

    「오늘」이 아니라 이 값을 쓰는 이유: 수집은 Jino Mac의 버튼-only라 매일 돌지 않는다(D-17).
      「오늘 갱신됐나」로 물으면 어제 한 번에 다 받은 날 전건이 «굳음»으로 오탐한다.
    """
    q = db.query(func.max(CoupangRocketPurchaseOrder.synced_at))
    if vendor_id is not None:
        q = q.filter(CoupangRocketPurchaseOrder.vendor_id == vendor_id)
    return _kst_naive_date_str(q.scalar())


# ──────────────────────────────────────────────
# ② PO 그레인 원장 (칸 분류의 입력)
# ──────────────────────────────────────────────
def _pipeline_rows(
    db: Session,
    vendor_id: str | None,
    ship_from: date | None = None,
    ship_to: date | None = None,
) -> list[dict]:
    """열린 파이프라인 판정에 필요한 PO 한 판.

    ★발송액은 **항상 전 기간**으로 잰다. 창(ship_from/ship_to)은 «어떤 PO를 볼까»만 고르고
      금액을 자르지 않는다 — 자르면 창 앞에 일부를 보낸 PO에서 「안 보낸 것처럼」 보인다.
      대신 창 밖 발송이 섞인 PO는 `has_out_of_window_shipment=True`로 표시해 화면이 자백한다.
    """
    PO = CoupangRocketPurchaseOrder
    ship_all = _shipped_by_po(db, vendor_id)  # 금액 축(전 기간)

    q = db.query(
        PO.purchase_order_seq,
        PO.purchase_order_status,
        PO.purchase_order_status_description,
        PO.po_created_at,
        PO.receiving_finished_at,
        PO.synced_at,
        PO.order_qty,
        PO.vendor_confirmed_qty,
        PO.receiving_qty,
        PO.sum_of_order_amount,
        PO.sum_of_vendor_confirmed_amount,
        PO.sum_of_receiving_amount,
        PO.vendor_payment_seqs,
        PO.center_name,
        PO.first_sku_name,
        PO.sku_count,
        ship_all.c.ship_qty,
        ship_all.c.ship_gross,
        ship_all.c.unpriced_qty,
    ).outerjoin(ship_all, ship_all.c.po_seq == PO.purchase_order_seq)
    if vendor_id is not None:
        q = q.filter(PO.vendor_id == vendor_id)

    if ship_from is not None or ship_to is not None:
        ship_win = _shipped_by_po(db, vendor_id, ship_from, ship_to)
        win_seqs = {r.po_seq for r in db.query(ship_win.c.po_seq).all()}
        if not win_seqs:
            return []
        q = q.filter(PO.purchase_order_seq.in_(win_seqs))

    rows: list[dict] = []
    for r in q.all():
        conf = _f(r.sum_of_vendor_confirmed_amount)
        recv = _f(r.sum_of_receiving_amount)
        ship = _f(r.ship_gross)
        ship_qty = int(r.ship_qty or 0)
        seqs = r.vendor_payment_seqs or []
        rows.append(
            {
                "purchase_order_seq": r.purchase_order_seq,
                "status": r.purchase_order_status,
                "status_label": r.purchase_order_status_description,
                "po_date": _kst_date_str(r.po_created_at),
                "receiving_finished_date": _kst_date_str(r.receiving_finished_at),
                "synced_date": _kst_naive_date_str(r.synced_at),
                "order_qty": int(r.order_qty or 0),
                "confirmed_qty": int(r.vendor_confirmed_qty or 0),
                "received_qty": int(r.receiving_qty or 0),
                "shipped_qty": ship_qty,
                "order_amount": _f(r.sum_of_order_amount),
                "confirmed_amount": conf,
                "shipped_amount": ship,
                "received_amount": recv,
                "unpriced_shipped_qty": int(r.unpriced_qty or 0),
                "invoice_seqs": list(seqs),
                "has_invoice": bool(seqs),
                "center_name": r.center_name,
                "first_sku_name": r.first_sku_name,
                "sku_count": int(r.sku_count or 0),
                # ★★입고는 «발송의 하한»이다 — 쿠팡이 받았다면 우리는 분명히 보냈다.
                #   ASN(발송) 수집은 2025-07-22부터이고 상세를 못 받은 쉽먼트도 있어서
                #   `shipped_amount == 0`이 **「안 보냄」이 아니라 「발송 기록 없음」**인 PO가
                #   실재한다(2026-08-27 실측 2건: 123977085 15개 161,100원 · 127009073 1개
                #   9,540원 — 둘 다 전량 입고 완료인데 ASN 라인이 0건). 그 0을 그대로 쓰면
                #   **전량 입고된 PO가 「영영 못 보낸 분」으로 계상된다**(원칙22 위반).
                #   ⇒ 「안 보낸 양」의 자는 shipped가 아니라 max(shipped, received)다.
                "effective_shipped_amount": max(ship, recv),
                "asn_missing": ship == _Z and recv > _Z,
                # 부호를 살린 원값 — clamp 전. 화면·검산이 둘 다 볼 수 있게 남긴다.
                "unshipped_raw": conf - max(ship, recv),
                "unreceived_raw": ship - recv,
            }
        )
    return rows


# ──────────────────────────────────────────────
# ③ 칸 분류 — 규칙의 단일 자리
# ──────────────────────────────────────────────
def _stage_of(row: dict) -> list[tuple[str, Decimal]]:
    """한 PO → [(칸, 금액)…]. 한 PO가 두 칸에 걸릴 수 있다(보낼 것도 남고 입고 대기도 있는 PO).

    ★칸끼리는 금액이 안 겹친다: conf−ship(아직 안 보냄)과 ship−recv(보냈는데 안 잡힘)는
      서로 다른 구간이다. 그래서 같은 PO가 ②와 ③에 동시에 들어가도 이중계상이 아니다.

    ★종결단계(RI·CI)는 어느 칸에도 안 들어간다 — `_extra_buckets`가 별도로 낸다.
    """
    st = row["status"]
    out: list[tuple[str, Decimal]] = []
    if st == "RP":
        # 아직 우리가 납품가능수량을 확정하지 않은 단계. 확정하며 깎으면 줄어든다(화면이 자백).
        out.append((STAGE_AWAIT_CONFIRM, row["confirmed_amount"]))
        return out
    if st in SETTLED_STAGE_STATUSES:
        return out
    # 미종결(PA 등)
    if row["unshipped_raw"] > 0:
        out.append((STAGE_AWAIT_SHIP, row["unshipped_raw"]))
    if row["unreceived_raw"] > 0:
        out.append((STAGE_AWAIT_RECEIVE, row["unreceived_raw"]))
    return out


def _extra_buckets(rows: list[dict]) -> dict:
    """소계에 넣으면 안 되는 두 덩어리 — 종결단계(RI·CI)에서만 나온다.

    closed_unshipped : 확정했는데 발송 없이 닫힘 = **영영 못 보내는 분**.
    unexplained      : 발송 신고 > 쿠팡 인정 입고 = **미해명**(덜 보냄/반송/진짜 미수금 구별 불가).
                       ★확정 숫자가 아니다. 계약 §3이 소계 합산을 금지선으로 둔다.
    """
    closed_n = unexp_n = 0
    closed_amt = unexp_amt = _Z
    unexp_dates: list[str] = []
    for r in rows:
        if r["status"] not in SETTLED_STAGE_STATUSES:
            continue
        if r["unshipped_raw"] > 0:
            closed_n += 1
            closed_amt += r["unshipped_raw"]
        if r["unreceived_raw"] > 0:
            unexp_n += 1
            unexp_amt += r["unreceived_raw"]
            if r["po_date"]:
                unexp_dates.append(r["po_date"])
    return {
        "closed_unshipped": {"po_count": closed_n, "amount": closed_amt},
        "unexplained": {
            "po_count": unexp_n,
            "amount": unexp_amt,
            "oldest_po_date": min(unexp_dates) if unexp_dates else None,
            "newest_po_date": max(unexp_dates) if unexp_dates else None,
            "confirmed": False,
            "reason": (
                "우리 발송 신고량 > 쿠팡 인정 입고량. 덜 보냄·반송·진짜 미수금이 구별 불가로 "
                "섞여 있다(가르는 열쇠인 발주상세 「입고 메세지」·「회송 정보」·「변경 이력」 미수집). "
                "확정 숫자가 아니므로 소계에 넣지 않는다."
            ),
        },
    }


def _clamp_report(rows: list[dict]) -> dict:
    """max(0, …)로 잘린 음수를 표면화한다(계약 §2 — 조용한 절단 금지).

    over_shipped : 발송 > 확정. 우리가 확정보다 많이 보냈다고 신고한 것.
    over_received: 입고 > 발송. ASN이 못 보는 재발송분 입고가 있으면 정상적으로 난다.
    """
    over_ship_n = over_recv_n = asn_missing_n = 0
    over_ship_amt = over_recv_amt = asn_missing_amt = _Z
    for r in rows:
        if r["unshipped_raw"] < 0:
            over_ship_n += 1
            over_ship_amt += -r["unshipped_raw"]
        if r["unreceived_raw"] < 0:
            over_recv_n += 1
            over_recv_amt += -r["unreceived_raw"]
        if r["asn_missing"]:
            asn_missing_n += 1
            asn_missing_amt += r["received_amount"]
    return {
        "over_shipped": {"po_count": over_ship_n, "amount": over_ship_amt},
        "over_received": {"po_count": over_recv_n, "amount": over_recv_amt},
        # 입고는 있는데 ASN 라인이 0건 = **발송 기록 없음**(안 보낸 것이 아니다). 이 행들은
        #   입고금액을 발송의 하한으로 써서 「못 보낸 분」에서 빠진다 — 그 보정을 화면이 말한다.
        "asn_missing": {"po_count": asn_missing_n, "received_amount": asn_missing_amt},
    }


# ──────────────────────────────────────────────
# ④ 지급 대기 (계산서 그레인 — PO 소계와 축이 다르다)
# ──────────────────────────────────────────────
def _await_payment(db: Session, vendor_id: str | None, today: date) -> dict:
    """계산서가 나갔고 지급일이 아직 안 온 것 = 확정된 채권.

    ★PO 그레인이 아니라 **계산서 그레인**이다. 그래서 ①②③ 소계에 더하지 않는다 —
      같은 돈을 PO로 한 번, 계산서로 한 번 세게 된다.
    """
    S = CoupangRocketSettlement
    q = db.query(
        func.count(S.id),
        func.coalesce(func.sum(S.payment_amount), 0),
        func.min(S.payment_date),
        func.max(S.payment_date),
    ).filter(S.payment_date.isnot(None), S.payment_date > today)
    if vendor_id is not None:
        q = q.filter(S.vendor_id == vendor_id)
    n, amt, mn, mx = q.one()
    return {
        "invoice_count": int(n or 0),
        "amount": _f(amt),
        "next_payment_date": mn.isoformat() if mn else None,
        "last_payment_date": mx.isoformat() if mx else None,
    }


def _freshness(db: Session, vendor_id: str | None) -> dict:
    """수집 신선도 — 화면이 「이 숫자가 언제 것인가」를 상시 자백하게 한다.

    latest_shipped_date가 오늘이 아니어도 그것이 「발송이 없었다」는 뜻은 아니다(미수집일 수 있다).
      화면은 둘을 구분하지 못한다고 말해야 한다(계약 §7 미상).
    """
    PO = CoupangRocketPurchaseOrder
    SH = CoupangRocketShipment
    poq = db.query(func.max(PO.synced_at))
    shq = db.query(func.max(SH.synced_at), func.max(SH.shipped_at))
    if vendor_id is not None:
        poq = poq.filter(PO.vendor_id == vendor_id)
        shq = shq.filter(SH.vendor_id == vendor_id)
    po_synced = poq.scalar()
    sh_synced, sh_latest = shq.one()
    return {
        "po_synced_at_kst": _kst_naive_date_str(po_synced),
        "shipment_synced_at_kst": _kst_naive_date_str(sh_synced),
        "latest_shipped_date_kst": _kst_naive_date_str(sh_latest),
        "note": (
            "발송 데이터의 최신 발송일 이후가 비어 있는 것이 「발송이 없었다」인지 "
            "「아직 수집이 안 됐다」인지는 이 데이터로 구분되지 않는다."
        ),
    }


# ──────────────────────────────────────────────
# ⑤ Harness — 요약
# ──────────────────────────────────────────────
def compute_rocket_pipeline(
    db: Session,
    vendor_id: str | None,
    today: date,
    ship_from: date | None = None,
    ship_to: date | None = None,
) -> dict:
    """열린 파이프라인 요약 — 칸 4개 + 소계 + 별도 덩어리 + clamp + 신선도. 읽기 전용.

    ship_from/ship_to는 **③입고대기 칸에만** 의미가 있다(「8/20 이후 발송분 중 미발행」).
      창을 주면 그 창에 발송이 있는 PO만 보되 금액은 PO 전체 기준이고, 창 밖 발송이 섞인 PO
      수를 함께 낸다(계약 §2 — 자르면 「안 보낸 것처럼」 보인다).
    """
    all_rows = _pipeline_rows(db, vendor_id)
    last_day = _last_collection_day(db, vendor_id)

    # 창이 있으면 ③만 창 기준으로 다시 잰다. ①②는 창과 무관한 «지금 열린 것»이다.
    win_rows = _pipeline_rows(db, vendor_id, ship_from, ship_to) if (ship_from or ship_to) else None

    buckets: dict[str, dict] = {
        k: {"po_count": 0, "amount": _Z, "fresh_amount": _Z, "stale_amount": _Z,
            "stale_po_count": 0, "oldest_stale_synced_date": None}
        for k in PRE_INVOICE_STAGES
    }

    def _accumulate(rows: list[dict], only: str | None = None) -> None:
        for r in rows:
            for stage, amt in _stage_of(r):
                if only is not None and stage != only:
                    continue
                b = buckets[stage]
                b["po_count"] += 1
                b["amount"] += amt
                if last_day is not None and r["synced_date"] == last_day:
                    b["fresh_amount"] += amt
                else:
                    b["stale_amount"] += amt
                    b["stale_po_count"] += 1
                    cur = b["oldest_stale_synced_date"]
                    if r["synced_date"] and (cur is None or r["synced_date"] < cur):
                        b["oldest_stale_synced_date"] = r["synced_date"]

    if win_rows is None:
        _accumulate(all_rows)
    else:
        _accumulate(all_rows, only=STAGE_AWAIT_CONFIRM)
        _accumulate(all_rows, only=STAGE_AWAIT_SHIP)
        _accumulate(win_rows, only=STAGE_AWAIT_RECEIVE)

    subtotal = sum((buckets[k]["amount"] for k in PRE_INVOICE_STAGES), _Z)
    unpriced = sum(r["unpriced_shipped_qty"] for r in all_rows)

    out_of_window = 0
    if win_rows is not None:
        win_only = _shipped_by_po(db, vendor_id, ship_from, ship_to)
        win_map = {r.po_seq: _f(r.ship_gross) for r in db.query(
            win_only.c.po_seq, win_only.c.ship_gross).all()}
        for r in win_rows:
            if win_map.get(r["purchase_order_seq"], _Z) != r["shipped_amount"]:
                out_of_window += 1

    return {
        "as_of_kst": today.isoformat(),
        "ship_window": (
            {"from": ship_from.isoformat() if ship_from else None,
             "to": ship_to.isoformat() if ship_to else None,
             "applies_to": STAGE_AWAIT_RECEIVE,
             "po_with_out_of_window_shipment": out_of_window}
            if win_rows is not None else None
        ),
        "stages": [
            {"key": k, **buckets[k]} for k in PRE_INVOICE_STAGES
        ] + [{"key": STAGE_AWAIT_PAYMENT, **_await_payment(db, vendor_id, today)}],
        "pre_invoice_subtotal": {
            "amount": subtotal,
            "stages": list(PRE_INVOICE_STAGES),
        },
        **_extra_buckets(all_rows),
        "clamp": _clamp_report(all_rows),
        "unpriced_shipped_qty": unpriced,
        "last_collection_date_kst": last_day,
        "freshness": _freshness(db, vendor_id),
    }


# ──────────────────────────────────────────────
# ⑥ Harness — 한 칸의 PO 목록
# ──────────────────────────────────────────────
def compute_rocket_pipeline_rows(
    db: Session,
    vendor_id: str | None,
    stage: str,
    ship_from: date | None = None,
    ship_to: date | None = None,
    limit: int = 500,
) -> dict:
    """한 칸에 걸린 PO 목록. `stage_amount`가 그 칸에 계상된 금액이다(PO 총액이 아니다)."""
    if stage in ("closed_unshipped", "unexplained"):
        rows = _pipeline_rows(db, vendor_id)
        key = "unshipped_raw" if stage == "closed_unshipped" else "unreceived_raw"
        picked = [
            {**r, "stage_amount": r[key]}
            for r in rows
            if r["status"] in SETTLED_STAGE_STATUSES and r[key] > 0
        ]
    else:
        if stage not in PRE_INVOICE_STAGES:
            raise ValueError(f"알 수 없는 칸: {stage}")
        use_window = stage == STAGE_AWAIT_RECEIVE and (ship_from or ship_to)
        rows = _pipeline_rows(db, vendor_id, ship_from, ship_to) if use_window else _pipeline_rows(db, vendor_id)
        picked = []
        for r in rows:
            for s, amt in _stage_of(r):
                if s == stage:
                    picked.append({**r, "stage_amount": amt})
    picked.sort(key=lambda r: r["stage_amount"], reverse=True)
    last_day = _last_collection_day(db, vendor_id)
    for r in picked:
        r["is_stale"] = last_day is not None and r["synced_date"] != last_day
    return {
        "stage": stage,
        "total_count": len(picked),
        "rows": picked[:limit],
        "truncated": len(picked) > limit,  # 조용한 절단 금지 — 잘렸으면 말한다
        "last_collection_date_kst": last_day,
    }


# ──────────────────────────────────────────────
# ⑦ Harness — 「확인요청함」(RI)
# ──────────────────────────────────────────────
def stale_open_po_dates(db: Session, vendor_id: str | None, limit_days: int = 400) -> dict:
    """미종결(비-CI) PO 중 **마지막 수집에 안 잡힌** 것들의 발주일(KST) 목록.

    ★왜 «날짜»를 주나 — 페처가 그 날짜만 좁게 다시 훑게 하려고. 발주 목록 API는 발주일 범위로
      거르는 `searchStartDate`/`searchEndDate`가 **검증된 파라미터**인 반면, 발주번호 배열
      (`purchaseOrderIdArray`)·상태(`purchaseOrderStatus`)는 값 형식이 라이브로 확인된 적이 없다.
      확인 안 된 파라미터로 거르면 **조용히 빈 결과**가 오고, 그건 「굳은 게 없다」로 읽힌다.
      날짜는 대개 20개 안쪽이라(2026-08-27 실측 41건이 그 정도 날짜에 몰림) 좁은 재조회로 끝난다.

    ★수집 창(발주일 기본 30~90일) 밖으로 밀려난 미종결 PO는 상태가 영구히 굳는다. 이 함수가
      그 목록의 «어디를 다시 봐야 하나»를 낸다.

    limit_days: 이보다 오래된 것은 내지 않는다(폭주 방지). 잘리면 `truncated_before`로 말한다 —
      조용한 절단 금지(계약 §2).
      ★기본 400일인 이유: 이 기능을 만든 원인인 굳은 RI 8건이 **2025-10~2026-01 발주**다.
      120일로 두면 그 8건이 통째로 잘려 나가 «고치려던 것만 못 고치는» 기본값이 된다
      (2026-08-27 실측: 120일이면 22개 날짜 중 4개·발주 8건이 잘렸고, 400일이면 22개 전부 =
      발주 41건이 들어온다 — 날짜 수가 페처 상한 40 안이라 비용도 문제되지 않는다).
    """
    PO = CoupangRocketPurchaseOrder
    last_day = _last_collection_day(db, vendor_id)
    # po_created_at은 UTC naive 저장 → KST 날짜는 +9h.
    kst_day = func.date(PO.po_created_at, "+9 hours")
    q = db.query(kst_day.label("d"), func.count(PO.id).label("n")).filter(
        PO.purchase_order_status.isnot(None),
        # ★"CI"만 뺀다. RI(거래명세서확인요청)는 «입고 끝난 단계»지만 **아직 우리 손이 남은**
        #   단계라 재훑기 대상이다 — 굳은 RI 8건이 이 기능을 만든 이유 자체다.
        #   여기에 SETTLED_STAGE_STATUSES를 그대로 쓰면 RI가 빠져 원인이 대상에서 사라진다.
        PO.purchase_order_status != "CI",
        PO.po_created_at.isnot(None),
    )
    if vendor_id is not None:
        q = q.filter(PO.vendor_id == vendor_id)
    if last_day is not None:
        # `synced_at`은 KST 저장이라 환산하지 않는다(_kst_naive_date_str와 같은 규약).
        q = q.filter(func.date(PO.synced_at) != last_day)
    rows = q.group_by("d").order_by("d").all()

    cutoff = None
    if last_day is not None:
        cutoff = (date.fromisoformat(last_day) - timedelta(days=limit_days)).isoformat()
    kept = [(r.d, int(r.n)) for r in rows if cutoff is None or r.d >= cutoff]
    dropped = [(r.d, int(r.n)) for r in rows if cutoff is not None and r.d < cutoff]
    return {
        "dates": [d for d, _ in kept],
        "po_count": sum(n for _, n in kept),
        "last_collection_date_kst": last_day,
        "limit_days": limit_days,
        # 잘린 것을 «없는 것»으로 만들지 않는다 — 페처 로그와 화면이 이 값을 그대로 낸다.
        "truncated_before": cutoff if dropped else None,
        "truncated_date_count": len(dropped),
        "truncated_po_count": sum(n for _, n in dropped),
    }


def compute_rocket_ri_queue(db: Session, vendor_id: str | None) -> dict:
    """거래명세서확인요청(RI) 상태 PO — 우리가 눌러야 할 일 목록.

    ★파이프라인 칸이 아니다. RI PO의 입고금액은 이미 계산서가 나가 ④지급대기에 포함돼 있으므로
      합계에 더하면 중복이다(계약 §2).

    ★★살아 있는 것과 굳은 것을 가른다. 2026-08-27 실측: RI 12건 중 8건이 2026-08-05에 굳었고
      그 8건의 계산서는 확정·전송에 **지급일까지 지났다** — 이미 닫힌 건이다. 상태만 보고
      목록을 내면 Jino가 죽은 줄을 누른다. 판정 근거를 행마다 실어 보낸다(is_stale +
      invoice.payment_date + synced_date) — 화면이 «왜 죽었다고 보는지»를 말할 수 있게.
    """
    rows = [r for r in _pipeline_rows(db, vendor_id) if r["status"] == "RI"]
    last_day = _last_collection_day(db, vendor_id)

    seqs: set[int] = set()
    for r in rows:
        seqs.update(int(s) for s in r["invoice_seqs"])
    inv_map: dict[int, dict] = {}
    if seqs:
        S = CoupangRocketSettlement
        for s in db.query(S).filter(S.invoice_seq.in_(sorted(seqs))).all():
            inv_map[s.invoice_seq] = {
                "invoice_seq": s.invoice_seq,
                "issue_date": s.issue_date.isoformat() if s.issue_date else None,
                "payment_date": s.payment_date.isoformat() if s.payment_date else None,
                "tax_invoice_confirmed_date": (
                    s.tax_invoice_confirmed_date.isoformat()
                    if s.tax_invoice_confirmed_date else None
                ),
                "tax_invoice_transmitted": s.tax_invoice_transmitted,
                "payment_amount": _f(s.payment_amount),
            }

    out = []
    for r in rows:
        invs = [inv_map[int(s)] for s in r["invoice_seqs"] if int(s) in inv_map]
        # 번호는 있는데 정산행이 없으면 「미수집」이지 「미발행」이 아니다(원칙22).
        missing = [int(s) for s in r["invoice_seqs"] if int(s) not in inv_map]
        out.append({
            **r,
            "invoices": invs,
            "invoice_rows_missing": missing,
            "is_stale": last_day is not None and r["synced_date"] != last_day,
        })
    out.sort(key=lambda r: (r["is_stale"], r["po_date"] or ""))

    live = [r for r in out if not r["is_stale"]]
    stale = [r for r in out if r["is_stale"]]
    return {
        "rows": out,
        "live_count": len(live),
        "live_amount": sum((r["received_amount"] for r in live), _Z),
        "stale_count": len(stale),
        "stale_amount": sum((r["received_amount"] for r in stale), _Z),
        "last_collection_date_kst": last_day,
        "note": (
            "굳은 행은 수집 창(발주일 기준) 밖이라 상태가 마지막 수집일에 멈춰 있다. "
            "지금 참인 상태가 아니라 «마지막으로 본 상태»다 — 「미종결 PO 재수집」 후 다시 볼 것."
        ),
    }
