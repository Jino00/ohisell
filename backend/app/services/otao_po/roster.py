"""S1 발주 로스터 — SKU별 **발주 누계 · 픽업 누계 · OTAO 예약 잔량** 3칸.

계약 `CONTRACT_inventory_unified.md` §4 S1 · 트랙 `track_inventory-management.md` · 체인 `발주예측` n=4.

## 왜 3칸인가 (합치면 안 되는 이유)

계약 §3-9가 **합산 단일 숫자 표기를 금지선으로** 못 박았다: *"합산하면 ②픽업 결정이 화면에서
사라진다."* 우리 결정변수는 둘이고 목적함수가 다르다 —

- **①발주층**: OTAO에 생산을 예약한다. 현금·창고비가 우리 장부 밖이라 「재고 최소화」가 작동하지
  않는다. 목적함수는 `기대 데드스톡 채무 + 기대 생산품절 손실`.
- **②픽업층**: 예약 잔량 상한 안에서 언제·얼마를 가져올지 정한다. **Jino의 「재고 최소화」는
  이 층에 산다.**

그래서 「총 몇 개」는 어느 결정에도 답을 못 준다. 3칸이라야 *"창고엔 7개인데 OTAO엔 3,150개가
이미 우리 것으로 잡혀 있고, 그 상태에서 300개를 또 시켰다"*(n=2 실측, `GAPIP15` 2026-08-12)가
화면에서 보인다.

## ★창(window) 정합 — 이 모듈이 가장 조심하는 것

두 축의 시작일이 다르다:

    발주(발주서 PDF)          2023-04-26 ~   ← 66건 전체
    픽업(통관 원장)            2026-01-27 ~   ← `import_shipment` 12건, 2025년 선적 0건

그냥 `발주 전체 − 픽업 전체`를 하면 **2023~2025년 발주분의 입고가 원장에 없어서 예약 잔량이
통째로 부풀려진다.** 반대로 발주를 원장 창으로 자르면 그 이전 발주의 «진짜 미인도분»이 사라진다.
어느 쪽도 「맞는 하나」가 아니다.

⇒ **자르되, 잘라낸 것을 화면에 남긴다.** 기본 잔량은 원장이 덮는 창 안에서 계산하고,
창 밖 발주 수량은 `out_of_window_ordered`로 **따로** 실어 보낸다. 그러면 화면이
「이 잔량은 2026-01-27 이후 발주분만 센 것이고, 그 이전 발주 N개가 창 밖에 있다」를 자백한다.
계약 §2-8과 같은 결이다 — **「데이터 없음」을 0으로 바꾸지 않는다.**

## 매핑 미확정은 숨기지 않는다

픽업 누계는 `otao_item_name_map`을 거쳐야 SKU가 된다. 붙지 않은 품목명은 `unmapped` 묶음으로
수량과 함께 반환한다(계약 §2-9·§3-6). 실측 커버리지는 수량 기준 **87.2%**이고, 나머지 12.8%를
0으로 접으면 그만큼이 조용한 발주 누락이 된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    ImportInvoiceLine,
    ImportShipment,
    OtaoItemNameMap,
    OtaoPurchaseOrder,
    OtaoPurchaseOrderLine,
)


@dataclass
class RosterRow:
    product_code: str
    ordered: int = 0  # 발주 누계 (창 안, 정본 발주서만)
    picked: int = 0  # 픽업 누계 (통관 원장)
    # 예약 잔량. **음수가 나올 수 있다** — 창 밖 발주분의 입고가 창 안에 찍히면 그렇게 된다.
    # 0으로 깎지 않는다: 음수 자체가 「창이 어긋났다」는 신호이고, 깎으면 그 신호가 사라진다.
    reserved: int = 0
    out_of_window_ordered: int = 0  # 원장 창보다 이른 발주분 (잔량 계산에서 «뺀» 몫)
    last_order_date: date | None = None
    # 이 SKU가 실린 **정본 발주서의 건수**(발주일수가 아니다 — 같은 날 복수 발주가 실재한다).
    order_count: int = 0


@dataclass
class Roster:
    rows: list[RosterRow] = field(default_factory=list)
    window_start: date | None = None
    # 매핑 못 붙인 원장 품목명 → 수량. 화면에 「매핑 필요」로 뜬다.
    unmapped: dict[str, int] = field(default_factory=dict)
    totals: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _ledger_window_start(session: Session) -> date | None:
    """통관 원장이 실제로 덮기 시작하는 날. 상수로 박지 않는다 — 원장이 늘면 따라 움직여야 한다."""
    return session.scalar(select(func.min(ImportShipment.declaration_date)))


def build_roster(session: Session) -> Roster:
    window_start = _ledger_window_start(session)
    rows: dict[str, RosterRow] = {}

    def row(code: str) -> RosterRow:
        return rows.setdefault(code, RosterRow(product_code=code))

    # ── ① 발주 누계 — 정본 발주서만 센다(D-INV-3) ──────────────────────────
    # `is_authoritative=False`(개정 전 판본·중복 파일)를 같이 세면 발주가 부풀려진다.
    # 실측: 파일 95건 ↔ 고유 발주번호 66건이라 필터가 없으면 29건이 이중 계상된다.
    q = (
        select(
            OtaoPurchaseOrderLine.product_code,
            OtaoPurchaseOrderLine.quantity,
            OtaoPurchaseOrder.order_date,
            OtaoPurchaseOrder.id,
        )
        .join(OtaoPurchaseOrder, OtaoPurchaseOrderLine.order_id == OtaoPurchaseOrder.id)
        .where(OtaoPurchaseOrder.is_authoritative.is_(True))
    )
    # ★`order_count`의 그레인은 «발주 건»이지 «발주일»이 아니다 (적대 리뷰 1R P1-1).
    # 초판은 `set[date]`를 세어 **같은 날 복수 발주가 몇 건이든 1로 뭉개졌다.** 그 입력이
    # 실재한다는 근거는 `OtaoPurchaseOrder` docstring 안에 이미 있었다 — `serial` 명명 규칙
    # (`20260107-1`/`-2`)이 같은 날 복수 건을 전제하고, 개정본 4건이 정확히 그 형태다.
    # 발주서 행 id로 센다: 정본 필터를 이미 통과한 행이라 개정 전 판본은 여기 안 온다.
    seen_orders: dict[str, set[int]] = {}
    for code, qty, order_date, order_id in session.execute(q):
        r = row(code)
        in_window = (
            window_start is None or order_date is None or order_date >= window_start
        )
        if in_window:
            r.ordered += int(qty or 0)
        else:
            r.out_of_window_ordered += int(qty or 0)
        if order_date and (r.last_order_date is None or order_date > r.last_order_date):
            r.last_order_date = order_date
        seen_orders.setdefault(code, set()).add(int(order_id))

    for code, order_ids in seen_orders.items():
        row(code).order_count = len(order_ids)

    # ── ② 픽업 누계 — 통관 원장(읽기 전용) × 사전 ─────────────────────────
    # `import_invoice_line`은 계약 A′/B 소관이라 **읽기만** 한다(§3-8).
    name_to_code = {
        m.raw_name: m.product_code
        for m in session.scalars(select(OtaoItemNameMap)).all()
        if m.product_code
    }
    unmapped: dict[str, int] = {}
    pq = select(ImportInvoiceLine.item_name, ImportInvoiceLine.quantity).where(
        ImportInvoiceLine.line_type == "product"
    )
    for item_name, qty in session.execute(pq):
        n = int(qty or 0)
        code = name_to_code.get(item_name)
        if code is None:
            # 「모름」이지 「0」이 아니다. 조용히 빼면 발주 누락이 된다(계약 §2-9).
            unmapped[item_name] = unmapped.get(item_name, 0) + n
            continue
        row(code).picked += n

    # ── ③ 예약 잔량 ───────────────────────────────────────────────────────
    for r in rows.values():
        r.reserved = r.ordered - r.picked

    out = Roster(
        rows=sorted(rows.values(), key=lambda x: (-x.reserved, x.product_code)),
        window_start=window_start,
        unmapped=dict(sorted(unmapped.items(), key=lambda kv: -kv[1])),
    )
    out.totals = {
        "ordered": sum(r.ordered for r in out.rows),
        "picked": sum(r.picked for r in out.rows),
        "reserved": sum(r.reserved for r in out.rows),
        "out_of_window_ordered": sum(r.out_of_window_ordered for r in out.rows),
        "unmapped_qty": sum(unmapped.values()),
        "sku_count": len(out.rows),
        "unmapped_name_count": len(unmapped),
    }
    if window_start:
        out.notes.append(
            f"예약 잔량은 통관 원장이 덮는 창({window_start.isoformat()} 이후) 안의 발주분만 센다. "
            "그 이전 발주는 입고가 원장에 없어 잔량이 부풀려지므로 제외하고 별도 칸으로 보인다."
        )
    if out.totals["unmapped_qty"]:
        out.notes.append(
            f"품목명 {out.totals['unmapped_name_count']}종 "
            f"{out.totals['unmapped_qty']:,}개가 아직 상품코드에 안 붙었다 — 픽업 누계에서 빠져 있다."
        )
    neg = [r.product_code for r in out.rows if r.reserved < 0]
    if neg:
        out.notes.append(
            "예약 잔량이 음수인 코드가 있다(" + ", ".join(neg[:5]) +
            f"{' 외 %d개' % (len(neg) - 5) if len(neg) > 5 else ''}) — "
            "창 밖 발주분의 입고가 창 안에 찍혔다는 신호다. 0으로 깎지 않는다."
        )
    return out
