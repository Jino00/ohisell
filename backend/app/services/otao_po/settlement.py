"""S2 정산 창 — OTAO 지급 창(전월 20일 ~ 당월 19일)별 **픽업 합계**.

계약 `docs/contracts/CONTRACT_inventory_unified.md` §4 **S2** · 트랙 `track_inventory-management.md`
· 체인 `발주예측` n=7.

합격기준 원문: *"같은 메뉴에서 정산 창(전월 20~당월 19) **픽업 합계**가 보이고, **실제 19일
OTAO 지급액과 1개 창 이상 대조 일치**한다."*

## 창이 왜 이 모양인가

Jino 원문(2026-08-25, 계약 §0-A ④⑤):

    *"전달 20일부터 그달 19일까지 픽업한 금액"*
    *"이카운트에서 발주한 수량은 OTAO가 우리를 위해 생산해놓는 수량이고, 우리가 가져오는 수량만 지급함"*

⇒ **발주는 채무가 아니고 픽업이 채무다.** 그래서 이 모듈이 세는 것은 발주가 아니라 **픽업**이고,
단위는 수량이 아니라 **금액**이다 — 지급액과 대조할 것이 금액이기 때문이다.

## ★금액 축은 «CNY 인보이스»다 — 과세금액(KRW)이 아니다

    지급액 = OTAO에게 우리가 주는 돈 = Commercial Invoice의 외화 금액
    과세금액(`customs_value_krw`) = 관세청이 세금을 매기는 값 (CIF 기준·운임 포함)

둘은 다른 숫자이고, **OTAO는 후자를 모른다.** prod 실측(2026-08-26 23:2x KST)으로 CNY 축이
라인 단위까지 정확함을 확인했다 — **12선적 전건에서**

    `import_shipment.declared_inv_value` == Σ(`unit_price_foreign` × `quantity`)   차이 **0.00**

이라, 헤더 값을 믿고 쓰는 대신 **라인에서 다시 쌓아** 창으로 자를 수 있다(헤더만 쓰면 한 선적을
쪼갤 수 없다). `fx_rate`는 **신고환율**이지 실송금 환율이 아니고 `remittance_fx_rate`는
prod 12/12 NULL이므로(모델 docstring이 *"이번 범위가 아니다 — 필드만 예약한다"*라고 명문화),
**원화 환산을 하지 않는다.** 환산하면 우리가 안 쓰는 환율로 지어낸 숫자가 된다.

## ★`product`와 `material`을 합치지 않는다

`line_type` 분포(prod 전수): `product` 150줄 21,760개 **282,662 CNY** / `material` 8줄 35,100개
**28,080 CNY**. `material`은 전부 `cleaning kits`(@0.8 CNY)이고 Jino 확인 결과 **판매 상품이
아니라 우리 제품에 들어가는 부품**이다(`ImportInvoiceLine` docstring).

여기가 함정이다 — **S1의 픽업 누계(`roster.py`)는 `line_type='product'`만 센다.** 옳다, 그 칸은
SKU별 예약 잔량을 재는 자리니까. 그런데 **지급액은 부자재까지 포함해서 나간다**(전체의 9.0%).
그래서 이 모듈은 둘을 **갈라서** 싣고 합계도 같이 준다. 하나로 접으면 ①S1 픽업 칸과 왜 다른지
아무도 설명 못 하고 ②부자재 몫 28,080 CNY가 조용히 사라지거나 조용히 섞인다.

## ★창 배정 축의 한계 — 이 원장엔 «OTAO 픽업일»이 없다

가진 날짜는 전부 **한국 쪽** 날짜다: `declaration_date`(수입신고일) · `eta`(도착일). prod 실측상
둘의 간격은 0~2일이고 창 배정 결과가 **완전히 같다**. B/L 번호(`SETR2601250319`)에 박힌 발행일도
신고일보다 1~2일 이르다.

⇒ 우리가 만들 수 있는 축은 **「신고일 기준 창」**이지 「OTAO가 물건을 넘긴 날 기준 창」이 아니다.
평시엔 같은 창에 떨어지지만 **19/20일 경계 근처 선적은 창이 한 칸 밀릴 수 있다.** 그래서
경계 ±`_BOUNDARY_DAYS`일 안에 든 선적을 `boundary_shipment_ids`로 **지목해서** 내보낸다 —
대조가 어긋났을 때 「왜」의 첫 번째 후보이기 때문이다. 숨기면 그 창은 영문 모를 불일치가 된다.

## ★미분류(`unknown`)는 «새 선적의 기본 상태»다 — 화면에서 빠지면 안 된다

`ImportInvoiceLine.line_type`의 기본값이 `"unknown"`이고 적재 라우터도 그 값으로 넣는다.
분류는 **사람이 나중에 확정한다**(원장 계약 §2-4). ⇒ **갓 적재된 선적은 항상 전부 미분류다.**

그러니 미분류를 `product`로 접으면 「모름」이 「상품」으로 승격되고, 반대로 화면에서 빼면
**보이는 칸(상품+부자재)의 합이 픽업 합계와 안 맞는데 그 차이를 설명하는 글자가 없다.**
둘 다 안 된다 — 그래서 `other_*`로 **갈라서 싣고 화면도 칸을 따로 둔다.** 적대 리뷰 P1-1이
정확히 이 자리였다(값은 맞는데 사람에게 안 닿았다).

## ★확정 안 된 선적을 조용히 섞지 않는다

`status`는 `draft`/`confirmed` 둘뿐이고 **검산 3종을 통과해야 confirmed가 된다**(원장 계약 §3).
prod엔 `draft` 1건(id=9, 2026-01-27)이 있다. 빼지도 않고 섞지도 않는다 — 합계에는 넣되
`draft_shipment_ids`로 **그 창이 아직 굳지 않았음을 자백**한다. 빼면 픽업이 축소되고, 말없이
넣으면 확정된 창과 구별이 사라진다.

## ★「데이터 없음」과 「픽업 0」을 가른다 (계약 §2-8)

원장은 **2026-01-27부터** 덮는다(2025년 선적 0건 — n=2 실측). 그보다 이른 창은 **픽업이 0이었던
것이 아니라 원장이 모르는 것**이다. 0으로 그리면 「그 달엔 안 가져왔다」로 읽히고, 그 오독은
발주 축소로 이어진다. 그래서 창 목록은 원장이 덮는 구간에서만 만들고, 그 앞은 `covered=False`로
표시한다.

## 지급액 대조는 «대조 대상이 없으면 대조 불가»다

**실제 OTAO 지급액의 원장이 이 저장소에 없다.** prod 123개 테이블 전수 + 워크트리 파일 검색
(2026-08-26 23:1x KST)에서 지급·송금·매입대금 성격의 OTAO 원장은 **0건**이었다 — 정산 테이블들은
전부 쿠팡·네이버 «판매대금» 정산이고 `settlements`는 행 0개다.

⇒ `payment_actual_cny`는 **`None`이고 그것이 정직한 값**이다. 이 자리에 아무 숫자나 넣거나
「일치」라고 쓰면 계약 §2-8이 겨눈 바로 그 거짓 확신이 된다. Jino가 값을 주면 그때
`payments` 인자로 넣어 창별 차액이 계산된다 — **함수는 이미 그 모양으로 열려 있다.**
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ImportInvoiceLine, ImportShipment

# 창 경계에서 이 일수 안에 든 선적은 «픽업일 축 부재»로 창이 밀릴 수 있다고 지목한다.
# 2일인 근거: prod 실측에서 B/L 발행일이 신고일보다 최대 2일 이르다(`SETR2604240109` ↔ 04-27).
_BOUNDARY_DAYS = 2

_ZERO = Decimal("0")


def settlement_window_of(d: date) -> str:
    """날짜 → 그 픽업이 정산되는 **지급월** 키(`YYYY-MM`).

    창은 전월 20일 ~ 당월 19일이므로 **20일부터는 다음 달 지급**이다.
    예) 2026-01-27 픽업 → `2026-02` 창(2026-01-20~2026-02-19, 2026-02-19 지급).
    """
    year, month = d.year, d.month
    if d.day >= 20:
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return f"{year:04d}-{month:02d}"


def _window_bounds(key: str) -> tuple[date, date]:
    """지급월 키 → (창 시작 = 전월 20일, 창 끝 = 당월 19일). 끝이 곧 지급일이다."""
    year, month = int(key[:4]), int(key[5:7])
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    return date(prev_year, prev_month, 20), date(year, month, 19)


def _next_key(key: str) -> str:
    year, month = int(key[:4]), int(key[5:7])
    month += 1
    if month == 13:
        year, month = year + 1, 1
    return f"{year:04d}-{month:02d}"


@dataclass
class SettlementWindow:
    key: str  # 지급월 `YYYY-MM` — 이 달 19일에 지급한다
    start: date  # 전월 20일
    end: date  # 당월 19일 = 지급일
    shipments: int = 0
    lines: int = 0
    # ★product와 material을 «갈라서» 싣는다. 합계도 주지만 합계만 주지는 않는다.
    product_quantity: Decimal = _ZERO
    product_amount_cny: Decimal = _ZERO
    material_quantity: Decimal = _ZERO
    material_amount_cny: Decimal = _ZERO
    other_quantity: Decimal = _ZERO  # `unknown` 등 — 미분류를 product로 접지 않는다
    other_amount_cny: Decimal = _ZERO
    total_amount_cny: Decimal = _ZERO
    # 아직 검산을 통과하지 못한 선적. 합계엔 들어 있고, 그 사실을 화면이 말한다.
    draft_shipment_ids: list[int] = field(default_factory=list)
    # 창 경계 ±2일 — 픽업일 축이 없어 창이 밀릴 수 있는 선적.
    boundary_shipment_ids: list[int] = field(default_factory=list)
    shipment_ids: list[int] = field(default_factory=list)
    # 실제 지급액(Jino가 주면 채워진다). **None은 「0원 지급」이 아니라 「모른다」다.**
    payment_actual_cny: Decimal | None = None
    # None = 대조 불가(대조 대상 없음) / True = 일치 / False = 불일치
    reconciled: bool | None = None
    difference_cny: Decimal | None = None


@dataclass
class Settlement:
    windows: list[SettlementWindow] = field(default_factory=list)
    ledger_start: date | None = None  # 원장이 덮기 시작하는 날
    ledger_end: date | None = None
    currency: str = "CNY"
    # 신고일이 없어 창에 못 넣은 라인. 0으로 덮지 않는다.
    unassigned_lines: int = 0
    unassigned_quantity: Decimal = _ZERO
    unassigned_amount_cny: Decimal = _ZERO
    totals: dict = field(default_factory=dict)
    reconciliation: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def build_settlement(
    session: Session,
    payments: dict[str, Decimal | float | int] | None = None,
) -> Settlement:
    """정산 창별 픽업 합계 + (지급액이 주어지면) 창별 대조.

    `payments`: `{"2026-08": 33920}` 꼴. **없으면 대조는 「불가」이지 「불일치」가 아니다.**
    이 저장소엔 지급액 원장이 없으므로(모듈 docstring) 기본값은 대조 불가다.
    """
    rows = session.execute(
        select(
            ImportShipment.id,
            ImportShipment.declaration_date,
            ImportShipment.status,
            ImportShipment.currency,
            ImportInvoiceLine.line_type,
            ImportInvoiceLine.quantity,
            ImportInvoiceLine.unit_price_foreign,
        ).join(ImportInvoiceLine, ImportInvoiceLine.shipment_id == ImportShipment.id)
    ).all()

    out = Settlement()
    buckets: dict[str, SettlementWindow] = {}
    currencies: set[str] = set()
    seen_dates: list[date] = []

    for sid, decl, status, currency, line_type, qty, price in rows:
        quantity = Decimal(qty or 0)
        amount = quantity * Decimal(price or 0)
        if currency:
            currencies.add(currency)

        if decl is None:
            # 「모름」이지 「0」이 아니다 — 창에 못 넣었다는 사실 자체를 실어 보낸다.
            out.unassigned_lines += 1
            out.unassigned_quantity += quantity
            out.unassigned_amount_cny += amount
            continue

        decl_date = decl.date() if hasattr(decl, "date") else decl
        seen_dates.append(decl_date)
        key = settlement_window_of(decl_date)
        if key not in buckets:
            start, end = _window_bounds(key)
            buckets[key] = SettlementWindow(key=key, start=start, end=end)
        w = buckets[key]

        w.lines += 1
        if sid not in w.shipment_ids:
            w.shipment_ids.append(sid)
            w.shipments += 1
            if status != "confirmed":
                w.draft_shipment_ids.append(sid)
            # 창 경계 근처인가 — 픽업일 축이 없어 밀릴 수 있는 자리(모듈 docstring)
            if (
                (decl_date - w.start).days < _BOUNDARY_DAYS
                or (w.end - decl_date).days < _BOUNDARY_DAYS
            ):
                w.boundary_shipment_ids.append(sid)

        if line_type == "product":
            w.product_quantity += quantity
            w.product_amount_cny += amount
        elif line_type == "material":
            w.material_quantity += quantity
            w.material_amount_cny += amount
        else:
            w.other_quantity += quantity
            w.other_amount_cny += amount
        w.total_amount_cny += amount

    # ── 원장이 덮는 구간 ──────────────────────────────────────────────────
    out.ledger_start = min(seen_dates) if seen_dates else None
    out.ledger_end = max(seen_dates) if seen_dates else None
    out.currency = currencies.pop() if len(currencies) == 1 else ",".join(sorted(currencies))

    # ── 창 목록: 원장 구간 «안»의 빈 창도 채운다(픽업 0건 창이 목록에서 사라지면 안 된다) ──
    windows: list[SettlementWindow] = []
    if buckets:
        key = min(buckets)
        last = max(buckets)
        while True:
            if key in buckets:
                windows.append(buckets[key])
            else:
                start, end = _window_bounds(key)
                windows.append(SettlementWindow(key=key, start=start, end=end))
            if key == last:
                break
            key = _next_key(key)

    # ── 지급액 대조 ───────────────────────────────────────────────────────
    supplied = {k: Decimal(str(v)) for k, v in (payments or {}).items()}
    for w in windows:
        actual = supplied.get(w.key)
        if actual is None:
            # ★대조 «불가»다. False(불일치)로 적으면 없는 사실을 만든 것이 된다.
            w.payment_actual_cny = None
            w.reconciled = None
            w.difference_cny = None
            continue
        w.payment_actual_cny = actual
        w.difference_cny = w.total_amount_cny - actual
        w.reconciled = w.difference_cny == 0

    out.windows = windows
    compared = [w for w in windows if w.reconciled is not None]
    matched = [w for w in compared if w.reconciled]
    out.reconciliation = {
        # 계약 §4 S2가 요구하는 것은 «1개 창 이상» 대조 일치다.
        "payments_supplied": len(supplied),
        "windows_compared": len(compared),
        "windows_matched": len(matched),
        "matched_keys": [w.key for w in matched],
        "mismatched": [
            {"key": w.key, "expected": str(w.total_amount_cny), "actual": str(w.payment_actual_cny),
             "difference": str(w.difference_cny)}
            for w in compared
            if not w.reconciled
        ],
        # ★이 한 줄이 이 절의 핵심이다 — 「대조 대상이 없다」와 「대조했는데 틀렸다」는 다르다.
        "source": "supplied" if supplied else "none",
    }

    out.totals = {
        "windows": len(windows),
        "shipments": sum(w.shipments for w in windows),
        "lines": sum(w.lines for w in windows),
        "product_quantity": sum((w.product_quantity for w in windows), _ZERO),
        "product_amount_cny": sum((w.product_amount_cny for w in windows), _ZERO),
        "material_quantity": sum((w.material_quantity for w in windows), _ZERO),
        "material_amount_cny": sum((w.material_amount_cny for w in windows), _ZERO),
        "other_quantity": sum((w.other_quantity for w in windows), _ZERO),
        "other_amount_cny": sum((w.other_amount_cny for w in windows), _ZERO),
        "total_amount_cny": sum((w.total_amount_cny for w in windows), _ZERO),
        "draft_shipments": sum(len(w.draft_shipment_ids) for w in windows),
        "boundary_shipments": sum(len(w.boundary_shipment_ids) for w in windows),
    }

    # ── 화면이 반드시 말해야 하는 것 ──────────────────────────────────────
    if out.ledger_start:
        out.notes.append(
            f"창은 통관 원장이 덮는 구간({out.ledger_start.isoformat()} ~ "
            f"{out.ledger_end.isoformat() if out.ledger_end else '?'})에서만 만든다. "
            "그 이전 창은 픽업이 0이었던 것이 아니라 원장이 모르는 것이다."
        )
    else:
        out.notes.append("통관 원장이 비어 있다 — 창을 만들 근거가 없다(픽업 0이 아니다).")
    out.notes.append(
        "금액은 Commercial Invoice의 외화(CNY)다. 과세금액(원)은 관세청이 세금을 매기는 값이라 "
        "OTAO 지급액이 아니고, 실송금 환율은 원장에 없어(12/12 NULL) 원화 환산을 하지 않는다."
    )
    if out.totals["material_amount_cny"]:
        out.notes.append(
            f"부자재(cleaning kits 등) {out.totals['material_quantity']:,.0f}개 "
            f"{out.totals['material_amount_cny']:,.0f} CNY는 지급액에 들어가지만 "
            "S1의 «픽업 누계» 칸에는 안 들어간다(그 칸은 판매 SKU만 센다) — 두 숫자가 다른 이유다."
        )
    if out.totals["other_amount_cny"]:
        out.notes.append(
            f"아직 분류되지 않은(미분류) 라인이 {out.totals['other_quantity']:,.0f}개 "
            f"{out.totals['other_amount_cny']:,.0f} CNY 있다 — 픽업 «합계»에는 들어가 있고 "
            "상품·부자재 어느 칸에도 안 들어간다. 갓 적재된 선적은 분류 전이라 전부 여기 있다."
        )
    if out.totals["boundary_shipments"]:
        out.notes.append(
            f"창 경계(19/20일) ±{_BOUNDARY_DAYS}일 안에 신고된 선적이 "
            f"{out.totals['boundary_shipments']}건 있다. 이 원장엔 «OTAO 픽업일»이 없고 한국 쪽 "
            "신고일만 있어, 그 선적들은 실제 정산 창이 한 칸 밀려 있을 수 있다."
        )
    if out.totals["draft_shipments"]:
        out.notes.append(
            f"아직 검산을 통과하지 못한 선적(draft)이 {out.totals['draft_shipments']}건 "
            "합계에 들어 있다 — 빼지도 숨기지도 않았다."
        )
    if out.unassigned_lines:
        out.notes.append(
            f"신고일이 없어 어느 창에도 못 넣은 라인이 {out.unassigned_lines}건 "
            f"({out.unassigned_amount_cny:,.0f} CNY) 있다 — 0으로 덮지 않았다."
        )
    if out.reconciliation["source"] == "none":
        out.notes.append(
            "실제 OTAO 지급액 원장이 이 저장소에 없어 «대조 불가»다(불일치가 아니다). "
            "prod 123개 테이블 전수 검색에서 OTAO 지급·송금 원장 0건 — 기존 정산 테이블은 "
            "전부 쿠팡·네이버 판매대금이다. Jino가 창별 지급액을 주면 그 자리에서 대조된다."
        )
    return out
