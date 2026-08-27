"""S4 파생 현재고 — 「초기 실사 + 픽업 입고 − 판매」와 **실사 표본 대조 오차**.

계약 `docs/contracts/CONTRACT_inventory_unified.md` §4 **S4** · 트랙 `track_inventory-management.md`
· 체인 `발주예측` n=8.

합격기준 원문: *"같은 메뉴에서 **파생 현재고**(초기 실사 + 픽업 입고 − 판매)가 보이고, 실사 표본
**10 SKU 대조 오차**가 화면에 표시된다."*

## ★한 문장 안에 요구가 둘이고, 둘의 «막힘»이 다르다

**이 모듈이 가장 먼저 말해야 하는 것이 이것이다.**

    ① 파생 현재고 = 초기 실사 + 픽업 입고 − 판매      ← 「판매」 항에서 막힌다
    ② 실사 표본 10 SKU 대조 오차                      ← ★막히지 않는다

①이 막히는 이유는 **라벨 공간이 둘이기 때문**이다. 발주·픽업은 OTAO 품목코드(`GAPIP…`) 축이고
판매는 우리 SKU(`internal_sku`) 축인데, n=8 실측(2026-08-27, prod 123테이블 전수)에서

    otao_purchase_order_line.product_code (75종) ∩ product_master.internal_sku (963종) = 0
    `GAPIP` 문자열은 prod 전체에서 이 트랙의 두 테이블에만 산다 (그 밖 0건)

이고, 둘을 잇는 다리는 **DB 어디에도 없다.** 다리 «구축»은 이 계약의 「안 함」이다
(→ `docs/tracks/active/track_product-connection-map.md`, 본 트랙은 소비만). n=6이 같은 것을
관측하고 같은 이유로 손대지 않았다.

②는 다르다. 「실사 대조 오차」는 **ECOUNT가 말하는 재고 ↔ 사람이 센 재고**의 차이이고
**판매 축을 타지 않는다.** 그리고 그 숫자가 이 트랙에서 실제로 필요한 것이다 — 계약 §2-7C ④가
*"ECOUNT 오차의 크기는 §4 S4의 「실사 표본 10 SKU 대조」가 잰다. **그전까지 재고 기반 문턱을
추천에 싣지 않는다**"*라고 S5의 선행조건으로 못 박아 뒀다.

⇒ 그래서 이 모듈은 ①을 억지로 세우지 않고 `derived_quantity=None`(「산출 불가」)로 자백하되,
②는 **완전히** 세운다. 값이 오면 코드 수정 없이 채워지는 모양으로 연다 —
`build_stock(session, counted={"GAPIP16PR": 120, ...})`. S2의 `payments=`와 같은 손잡이다.

## ★「없다」와 「0」을 가른다 (계약 §2-8·§3-4)

- 판매를 못 붙였으면 `sold_quantity=None`이지 `0`이 아니다. 0으로 두면 파생 현재고가
  **재고+입고**로 부풀고, 그 부푼 숫자는 「이만큼 있으니 발주하지 마라」로 읽힌다.
- 스냅샷이 없는 코드는 `baseline_quantity=None`이다. 「재고 0」이 아니다.
- 그래서 파생값은 **세 항이 다 있을 때만** 숫자가 된다. 하나라도 None이면 None이고,
  `derived_blocked_by`가 어느 항 때문인지 이름으로 말한다.

## ★창고를 합치지 않는다 (계약 §1 창고 5개 표)

같은 1,008개라도 「본사에 있는 것」과 「이미 쿠팡 제트배송에 나가 있는 것」은 발주 판단에서
정반대 의미다. 초판 실측이 전 창고 합계를 내서 틀렸던 자리다. 그래서

    본사            → own       ★차감항의 본체. 파생 현재고의 기준이 되는 유일한 창고다.
    본사-포장       → material  부자재(cleaning kit) 축. 강화유리 발주와 별개다.
    쿠팡 제트배송   → channel   판매 가능하나 **우리 창고가 아니다**. 정합성 미검증(§1 ⚠️).
    반품창고        → excluded  Jino: "예전에 사용했지만 지금 사용하지 않음"
    아마존          → excluded  Jino: "사용안함. 재고 없음"

표에 없는 창고 이름이 나오면 **`unknown`으로 갈라서 자백**한다 — 조용히 `own`에 넣으면
차감항이 오염되고, 조용히 버리면 재고가 사라진다. 둘 다 안 된다.

## ★기준 시점(t0)은 «가장 이른 스냅샷»이지 «가장 최근»이 아니다

되감기가 아니라 **되감기의 반대**를 한다: t0의 재고에서 그 이후 입고를 더하고 판매를 빼서
「지금 이만큼이어야 한다」를 만들고, 그것을 실제(최신 스냅샷 또는 실사)와 대조한다.
t0를 최신으로 잡으면 더할 구간이 0이라 파생값이 기준값과 같아져 **대조가 항등식이 된다.**

⇒ 스냅샷이 **하나뿐이면 t0 == 최신**이고, 그 사실을 `notes`가 말한다. 오차 측정은 두 번째
스냅샷부터 시작된다 — 그게 이 원장을 쌓는 이유다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    ImportInvoiceLine,
    ImportShipment,
    OtaoItemNameMap,
    OtaoStockSnapshot,
)

_ZERO = Decimal("0")

# 창고 이름 → 역할. **정본은 계약 §1의 Jino 진술 표**이고 데이터엔 안 적혀 있다.
# 코드가 아니라 이름으로 거는 이유: 2026-08-25 라이브 응답에서 확인된 것은 `WH_DES`(이름)이고
# `WH_CD`(코드)는 문서에 남아 있지 않다. 코드가 확인되면 그때 키를 늘린다.
_WAREHOUSE_ROLE: dict[str, str] = {
    "본사": "own",
    "본사-포장": "material",
    "본사포장": "material",
    "쿠팡 제트배송": "channel",
    "쿠팡제트배송": "channel",
    "반품창고": "excluded",
    "아마존": "excluded",
}

# 파생 현재고의 기준이 되는 역할. 하나뿐이다 — 「우리가 배분할 수 있는 몫」(§1).
_BASELINE_ROLE = "own"


def warehouse_role(name: str | None, code: str | None = None) -> str:
    """창고 이름 → 역할. 모르는 창고는 **`unknown`**이지 `own`도 `excluded`도 아니다.

    조용히 `own`으로 접으면 차감항이 오염되고(없던 재고가 생긴다), 조용히 `excluded`로
    접으면 재고가 사라진다. 둘 다 계약 §2-8이 금지하는 「모름을 아는 값으로 바꾸기」다.
    """
    for key in (name, code):
        if key is None:
            continue
        role = _WAREHOUSE_ROLE.get(key.strip())
        if role:
            return role
    return "unknown"


@dataclass
class StockRow:
    product_code: str

    # ── ① 기준 재고 (t0) ──────────────────────────────────────────────────
    # None = 이 코드가 스냅샷에 없다. **「재고 0」이 아니다.**
    baseline_quantity: Decimal | None = None
    # 역할별 분해. 합계로 주지 않는다(§1 창고 표·§3-9와 같은 결).
    baseline_by_role: dict[str, Decimal] = field(default_factory=dict)

    # ── ② 픽업 입고 (t0 «이후») ───────────────────────────────────────────
    inbound_quantity: Decimal = _ZERO

    # ── ③ 판매 (t0 이후) ─────────────────────────────────────────────────
    # None = 근거 없음. 다리 부재라 **원리적으로** 못 붙는다(모듈 docstring).
    sold_quantity: Decimal | None = None

    # ── 파생 현재고 = ① + ② − ③ ─────────────────────────────────────────
    derived_quantity: Decimal | None = None
    # 파생이 None인 이유를 항 이름으로 말한다: 'baseline' | 'sold'
    derived_blocked_by: str | None = None
    # 판매를 «차감하지 않았을 때»의 상한. ★이것은 현재고가 아니다 —
    # 판매는 항상 ≥0이므로 실제 재고는 이 값을 넘을 수 없다는 뜻일 뿐이다.
    # 금지선 4(결손을 판매 0으로 «예측에 넣기») 회피: 이 값은 추천 입력이 아니라 표시용이고,
    # 이름과 화면 라벨 둘 다 「판매 미차감 상한」이라고 못 박는다.
    upper_bound_if_no_sales: Decimal | None = None

    # ── 실사 대조 (다리 없이도 성립하는 쪽) ───────────────────────────────
    counted_quantity: Decimal | None = None  # 사람이 센 값
    counted_at: datetime | None = None  # 그 코드를 «언제» 셌나 (코드마다 다를 수 있다)
    # ★어느 창고를 셌나. 이게 없으면 「본사 스냅샷 ↔ 본사-포장 실사」를 비교한 숫자가
    #   「대조 오차」라는 이름으로 화면에 설 수 있다(적대 리뷰 1R P1-2).
    counted_warehouse: str | None = None
    counted_warehouse_role: str | None = None
    # 실사 창고가 기준 창고(본사)와 다르면 True — 화면이 그 행을 오차로 읽지 않게 한다.
    counted_axis_mismatch: bool = False
    latest_snapshot_quantity: Decimal | None = None  # 최신 스냅샷의 `own` 수량
    # ★계약 §2-7C ④가 요구하는 그 숫자: ECOUNT가 말한 값 − 사람이 센 값
    variance_vs_snapshot: Decimal | None = None
    variance_pct: float | None = None
    # 파생값이 설 때만 채워진다(지금은 다리 부재로 항상 None).
    variance_vs_derived: Decimal | None = None


@dataclass
class Stock:
    rows: list[StockRow] = field(default_factory=list)
    baseline_at: datetime | None = None  # t0 — 가장 이른 스냅샷 시각
    latest_at: datetime | None = None  # 최신 스냅샷 시각
    # 실사 시각의 범위. 코드마다 다를 수 있으므로 한 값이 아니다(나눠 세는 것이 현실 경로다).
    counted_at: datetime | None = None  # 가장 최근 실사 시각
    counted_from: datetime | None = None  # 가장 이른 실사 시각
    # 기준 창고(본사)가 «아닌» 곳을 센 코드들 — 이 행들의 오차는 축이 다르다.
    counted_axis_mismatches: list[str] = field(default_factory=list)
    snapshot_count: int = 0  # 서로 다른 `snapshot_at`의 개수
    inbound_window_start: date | None = None  # 입고를 세기 시작한 날 (= t0의 날짜)
    # 역할표에 없는 창고 → 수량. 있으면 화면이 자백한다.
    unknown_warehouses: dict[str, Decimal] = field(default_factory=dict)
    # 판매 항이 왜 None인지 — 화면이 그대로 읽는다.
    sold_unavailable_reason: str | None = None
    totals: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


_SOLD_UNAVAILABLE_REASON = (
    "판매를 이 축에 못 붙인다 — 발주·픽업은 OTAO 품목코드(GAPIP…) 축이고 판매는 우리 "
    "SKU(internal_sku) 축인데 두 집합의 교집합이 0이고 이어 주는 표가 prod에 없다. "
    "다리 구축은 이 계약의 「안 함」이다(소관: track_product-connection-map.md). "
    "그래서 판매는 0이 아니라 「근거 없음」이고, 파생 현재고는 산출하지 않는다."
)


# ★사람이 센 값(`manual`)은 «시스템이 말한 재고»가 아니다 — 대조의 **상대편**이다.
# 같은 테이블에 살되 스냅샷 축에서는 반드시 빠져야 한다. 섞으면 ECOUNT가 말한 값과 사람이
# 센 값을 서로 대조하는 대신 **자기 자신과 대조**하게 되고, 오차가 항상 0으로 나온다.
_MANUAL_SOURCE = "manual"


def _snapshot_times(session: Session) -> tuple[datetime | None, datetime | None, int]:
    """(가장 이른 스냅샷, 최신 스냅샷, 서로 다른 스냅샷 개수) — `manual` 제외."""
    not_manual = OtaoStockSnapshot.source != _MANUAL_SOURCE
    earliest = session.scalar(
        select(func.min(OtaoStockSnapshot.snapshot_at)).where(not_manual)
    )
    latest = session.scalar(
        select(func.max(OtaoStockSnapshot.snapshot_at)).where(not_manual)
    )
    count = session.scalar(
        select(func.count(func.distinct(OtaoStockSnapshot.snapshot_at))).where(not_manual)
    )
    return earliest, latest, int(count or 0)


def _rows_at(session: Session, at: datetime) -> list[OtaoStockSnapshot]:
    """그 시각의 **ECOUNT 스냅샷** 행들. 사람이 센 값은 여기 안 온다."""
    return list(
        session.scalars(
            select(OtaoStockSnapshot)
            .where(OtaoStockSnapshot.snapshot_at == at)
            .where(OtaoStockSnapshot.source != _MANUAL_SOURCE)
        ).all()
    )


@dataclass
class ManualCount:
    """사람이 센 값 한 건 — 수량만이 아니라 **언제·어느 창고를** 셌는지까지."""

    quantity: Decimal
    at: datetime
    warehouse_name: str | None
    warehouse_role: str


def _latest_manual_counts(session: Session) -> dict[str, ManualCount]:
    """**상품코드별로 «가장 최근» 실사**를 하나씩 고른다.

    ★초판은 `max(snapshot_at)` **1회분만** 살렸는데, 적대 리뷰 1R P1-3이 그걸 깼다:
    10 SKU를 두 번에 나눠 세면(10:00에 둘, 14:00에 하나) **앞 회차가 화면에서 「실사 미실시」로
    사라진다.** 경고 한 줄 없이 «센 것»이 «안 셌다»가 된다 — 계약 §4 S4가 요구하는 건 10 SKU이고
    나눠 세는 쪽이 현실 경로다. 그래서 회차가 아니라 **코드별로** 최신을 고른다.

    ★창고 역할로 거르지 않는다 — 사람이 어느 창고를 셌든 그 수고를 버리지 않는다. 대신
    **어느 창고였는지를 값과 함께 실어 보낸다**(P1-2). 초판은 안 실어서, 본사 스냅샷과
    본사-포장 실사를 비교한 숫자가 「대조 오차」라는 이름으로 화면에 설 수 있었다.
    """
    out: dict[str, ManualCount] = {}
    for snap in session.scalars(
        select(OtaoStockSnapshot)
        .where(OtaoStockSnapshot.source == _MANUAL_SOURCE)
        .order_by(OtaoStockSnapshot.snapshot_at)
    ).all():
        code = snap.product_code
        qty = Decimal(snap.quantity or 0)
        prev = out.get(code)
        # 같은 시각에 같은 코드가 여러 창고로 오면 더한다(창고를 나눠 센 경우).
        if prev is not None and prev.at == snap.snapshot_at:
            prev.quantity += qty
            continue
        out[code] = ManualCount(
            quantity=qty,
            at=snap.snapshot_at,
            warehouse_name=snap.warehouse_name,
            warehouse_role=warehouse_role(snap.warehouse_name, snap.warehouse_code),
        )
    return out


def build_stock(
    session: Session,
    *,
    counted: dict[str, Decimal | int] | None = None,
) -> Stock:
    """파생 현재고 + 실사 대조.

    `counted`는 **사람이 센 값**이다(`{product_code: 수량}`). 계약 §4 S4가 요구하는
    「실사 표본 10 SKU 대조 오차」가 이 인자 하나로 열린다 — 값이 오면 코드 수정 없이 채워진다.
    비어 있으면 대조 칸은 `None`이고 화면이 「실사 미실시」라고 말한다. **0이 아니다.**
    """
    out = Stock(sold_unavailable_reason=_SOLD_UNAVAILABLE_REASON)

    baseline_at, latest_at, snapshot_count = _snapshot_times(session)
    out.baseline_at = baseline_at
    out.latest_at = latest_at
    out.snapshot_count = snapshot_count

    rows: dict[str, StockRow] = {}

    def row(code: str) -> StockRow:
        return rows.setdefault(code, StockRow(product_code=code))

    # ── ① 기준 재고 (t0 스냅샷) ───────────────────────────────────────────
    if baseline_at is not None:
        out.inbound_window_start = baseline_at.date()
        for snap in _rows_at(session, baseline_at):
            role = warehouse_role(snap.warehouse_name, snap.warehouse_code)
            qty = Decimal(snap.quantity or 0)
            r = row(snap.product_code)
            r.baseline_by_role[role] = r.baseline_by_role.get(role, _ZERO) + qty
            if role == "unknown":
                label = snap.warehouse_name or snap.warehouse_code
                out.unknown_warehouses[label] = (
                    out.unknown_warehouses.get(label, _ZERO) + qty
                )
        for r in rows.values():
            # 기준은 `own`뿐이다. 다른 역할은 갈라서 실려 가되 차감항에 안 들어간다.
            if _BASELINE_ROLE in r.baseline_by_role:
                r.baseline_quantity = r.baseline_by_role[_BASELINE_ROLE]

    # ── 최신 스냅샷 (실사 대조의 상대) ────────────────────────────────────
    if latest_at is not None:
        for snap in _rows_at(session, latest_at):
            if warehouse_role(snap.warehouse_name, snap.warehouse_code) != _BASELINE_ROLE:
                continue
            r = row(snap.product_code)
            r.latest_snapshot_quantity = (
                r.latest_snapshot_quantity or _ZERO
            ) + Decimal(snap.quantity or 0)

    # ── ② 픽업 입고 (t0 «이후») ───────────────────────────────────────────
    # 원장은 계약 A′/B 소관이라 **읽기만** 한다(§3-8). 사전을 거쳐야 코드가 된다.
    if baseline_at is not None:
        name_to_code = {
            m.raw_name: m.product_code
            for m in session.scalars(select(OtaoItemNameMap)).all()
            if m.product_code
        }
        q = (
            select(ImportInvoiceLine.item_name, ImportInvoiceLine.quantity)
            .join(ImportShipment, ImportInvoiceLine.shipment_id == ImportShipment.id)
            .where(ImportInvoiceLine.line_type == "product")
            .where(ImportShipment.declaration_date > baseline_at.date())
        )
        for item_name, qty in session.execute(q):
            code = name_to_code.get(item_name)
            if code is None:
                # 매핑 미확정은 조용히 빼지 않는다 — S1 로스터가 「매핑 필요」로 이미
                # 드러내고 있으므로 여기서는 입고에 안 얹는 것으로 족하다. 얹으면
                # 어느 코드인지 모르는 수량이 특정 코드에 붙는다(오염).
                continue
            row(code).inbound_quantity += Decimal(qty or 0)

    # ── ③ 판매 — 붙일 수 없다. None으로 둔다(0이 아니다). ────────────────
    #     (다리가 생기면 여기서 채운다. 그때까지 `sold_quantity`는 None이다.)

    # ── 파생 + 실사 대조 ──────────────────────────────────────────────────
    # 인자가 오면 그것이 이긴다(임시 대조·테스트용). 없으면 원장에 적재된 실사를 쓴다 —
    # 그래야 Jino가 한 번 센 값이 화면에 «남는다».
    counted_norm: dict[str, Decimal] = {}
    if counted is None:
        manual = _latest_manual_counts(session)
        for code, mc in manual.items():
            counted_norm[code] = mc.quantity
            r = row(code)
            r.counted_at = mc.at
            r.counted_warehouse = mc.warehouse_name
            r.counted_warehouse_role = mc.warehouse_role
            r.counted_axis_mismatch = mc.warehouse_role != _BASELINE_ROLE
            if r.counted_axis_mismatch:
                out.counted_axis_mismatches.append(code)
        if manual:
            times = [mc.at for mc in manual.values()]
            out.counted_at = max(times)
            out.counted_from = min(times)
    else:
        counted_norm = {k: Decimal(str(v)) for k, v in counted.items()}
    for code, qty in counted_norm.items():
        row(code).counted_quantity = qty

    for r in rows.values():
        if r.baseline_quantity is None:
            r.derived_blocked_by = "baseline"
        elif r.sold_quantity is None:
            r.derived_blocked_by = "sold"
            r.upper_bound_if_no_sales = r.baseline_quantity + r.inbound_quantity
        else:
            r.derived_quantity = (
                r.baseline_quantity + r.inbound_quantity - r.sold_quantity
            )

        if r.counted_quantity is not None and r.latest_snapshot_quantity is not None:
            r.variance_vs_snapshot = r.latest_snapshot_quantity - r.counted_quantity
            if r.counted_quantity != 0:
                r.variance_pct = float(r.variance_vs_snapshot / r.counted_quantity * 100)
        if r.counted_quantity is not None and r.derived_quantity is not None:
            r.variance_vs_derived = r.derived_quantity - r.counted_quantity

    out.rows = sorted(
        rows.values(),
        key=lambda x: (-(x.latest_snapshot_quantity or _ZERO), x.product_code),
    )

    matched = [r for r in out.rows if r.variance_vs_snapshot is not None]
    out.totals = {
        "sku_count": len(out.rows),
        "baseline_own": sum(
            (r.baseline_quantity or _ZERO) for r in out.rows
        ),
        "latest_own": sum((r.latest_snapshot_quantity or _ZERO) for r in out.rows),
        "inbound": sum(r.inbound_quantity for r in out.rows),
        # ★판매·파생은 «합계도 None»이다. 0으로 합치면 「합계는 0」으로 읽힌다.
        "sold": None,
        "derived": None,
        "counted_sku_count": len(counted_norm),
        "variance_sku_count": len(matched),
        "variance_abs_sum": sum(abs(r.variance_vs_snapshot) for r in matched)
        if matched
        else None,
    }
    # 실사했는데 스냅샷에 없는 코드 — 대조가 성립 안 한 것이지 오차 0이 아니다.
    out.totals["counted_without_snapshot"] = [
        code
        for code in counted_norm
        if rows[code].latest_snapshot_quantity is None
    ]

    # ── 화면이 읽을 자백문 ────────────────────────────────────────────────
    if snapshot_count == 0:
        out.notes.append(
            "ECOUNT 재고 스냅샷이 아직 하나도 없다 — 「재고 0」이 아니라 «찍은 적 없음»이다. "
            "scripts/ecount_stock_snapshot.py 로 1회 적재하면 이 표가 선다."
        )
    elif snapshot_count == 1:
        out.notes.append(
            f"스냅샷이 1개뿐이다({baseline_at:%Y-%m-%d %H:%M} KST) — 기준 시점이 곧 최신이라 "
            "되감을 구간이 없고 오차 측정은 두 번째 스냅샷부터 시작된다. "
            "지금 보이는 것은 «그 시각의 ECOUNT 재고»다."
        )
    else:
        out.notes.append(
            f"기준 {baseline_at:%Y-%m-%d %H:%M} → 최신 {latest_at:%Y-%m-%d %H:%M} KST, "
            f"서로 다른 스냅샷 {snapshot_count}개."
        )
    out.notes.append(_SOLD_UNAVAILABLE_REASON)
    if not counted_norm:
        out.notes.append(
            "실사 대조는 아직 «미실시»다 — 오차 칸이 비어 있는 것은 오차가 0이어서가 아니다. "
            "10개 SKU를 세어 주면 코드 수정 없이 그 자리에 오차가 뜬다."
        )
    if out.unknown_warehouses:
        out.notes.append(
            "역할을 모르는 창고가 있다("
            + ", ".join(sorted(out.unknown_warehouses))
            + ") — 본사 재고에 합치지 않았다. 계약 §1 창고 표에 없는 이름이다."
        )
    if out.totals["counted_without_snapshot"]:
        out.notes.append(
            "실사값은 있는데 스냅샷에 없는 코드가 있다("
            + ", ".join(out.totals["counted_without_snapshot"][:5])
            + ") — 대조가 성립하지 않은 것이지 오차 0이 아니다."
        )
    if out.counted_axis_mismatches:
        # ★기준은 «본사»인데 다른 창고를 센 것이면 그 차이는 오차가 아니라 «다른 축»이다.
        out.notes.append(
            "기준 창고(본사)가 아닌 곳을 센 코드가 있다("
            + ", ".join(out.counted_axis_mismatches[:5])
            + ") — 그 행의 차이는 «오차»가 아니라 서로 다른 창고를 뺀 값이다."
        )
    if out.counted_from and out.counted_at and out.counted_from != out.counted_at:
        out.notes.append(
            f"실사가 여러 회차에 나뉘어 있다({out.counted_from:%Y-%m-%d %H:%M} ~ "
            f"{out.counted_at:%Y-%m-%d %H:%M} KST) — 코드마다 «그 코드의 최신» 실사를 쓴다."
        )
    return out
