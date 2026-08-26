"""S3 — 채널 통합 SKU별 판매수량 시계열 · 채널별 매핑률 · 결손일 구분.

계약 `CONTRACT_inventory_unified.md` §4 **S3** 원문:

    "같은 메뉴에서 **채널 통합 SKU별 판매수량 시계열**과 채널별 매핑률이 보이고,
     **결손일이 「0」이 아니라 「데이터 없음」으로 구분 표시**된다."

## 축은 `product_master.internal_sku`다 — 발주 축과 «다르다»

★★**이 트랙에서 가장 중요한 사실 하나**: 발주 축의 라벨(`otao_purchase_order_line.product_code`,
`GAPIP15PR` 꼴)은 prod 전체에서 **이 트랙이 만든 두 테이블에만 산다**(전수 문자열 스캔
2026-08-26: `otao_purchase_order_line` 1,100행 · `otao_item_name_map` 43행, 그 밖엔 0건).
판매 쪽 라벨은 `product_master.internal_sku`(`OHI-0001` 꼴, 963행)이고 **둘은 0% 겹친다.**

⇒ 이 모듈은 **판매 축의 언어로만** 말한다. 발주 축과의 결합은 그 다리가 생기기 전엔
원리적으로 불가능하고, 그 사실을 `order_axis`로 **화면이 자백한다**. 조용히 이어 붙이면
「발주 30,090 vs 판매 6,092」 같은 **말이 되는 것처럼 보이는 거짓 대비**가 만들어진다.
다리를 «만드는» 것은 계약 「안 함」의 **상품 연결맵 «구축»**이라 이 슬라이스 밖이다.

## 채널 5축 (실측 2026-08-26, 최근 60일)

| 채널 | 정본 테이블 | 다리 | 수량 매핑률 |
|---|---|---|---|
| 네이버 스마트스토어(ch6) | `orders` | `orders.product_id` | 99.97% |
| 자사몰 cafe24(ch7) | `orders` | `orders.product_id` | 98.4% |
| Wing 3P 오픽스/오하이테크 | `coupang_vendor_item_sales_daily` | `vendor_item_id` → `product_channel_mapping` | 99.9% / 100% |
| RG 2P 오픽스/오하이테크 | `coupang_rg_order_item` | `vendor_item_id` → `product_channel_mapping` | 99.9% / 100% |
| 로켓 1P | `coupang_rocket_sales_daily` | `sku_id` → `rocket_product_cost_map.internal_sku` | 99.76% |

★쿠팡 3P·RG의 다리는 `product_id`가 **아니라** `vendor_item_id`다. `coupang_vendor_item_sales_daily.
product_id`는 «쿠팡 플랫폼 상품 ID»(10자리 문자열)이지 `product_master.id`가 아니어서, 그걸로
조인하면 **매핑률이 0%로 나온다** — 실제로 이 세션의 첫 조사가 그 함정에 빠졌고 재측정으로
뒤집혔다. 잘못된 조인은 예외를 안 내고 «0»을 낸다.

## 결손일 — 「0」과 「데이터 없음」을 가르는 근거는 채널마다 «있거나 없다»

`sync_log`는 `sync_type`이 DB 전체에 **`'orders'` 하나뿐**이고 `channel_id`·`date_from`·`date_to`를
갖는다. 그래서 `orders` 테이블을 쓰는 채널(네이버·cafe24)은 **「그 날짜를 덮은 성공 run이
있었는가」**를 되짚을 수 있다.

⇒ **쿠팡 3축(3P·RG·1P)은 그 근거가 없다.** 그 테이블들을 채우는 수집이 `sync_log`에 안 남는다.
`channel_id` 1~4의 `'orders'` run이 그 수집과 같은 잡인지는 **[미상]**이라 근거로 쓰지 않는다 —
근거가 아닌 것을 근거로 쓰면 「구분했다」는 거짓말이 된다. 화면은 그 채널에 대해
**「구분 근거 없음」**이라고 말한다(계약 §2-8의 정직한 이행: 모르는 것을 0으로도, 결손으로도
단정하지 않는다).

## 취소·반품은 빼되 «조용히» 빼지 않는다

`cancelled`·`returned`는 수요가 아니므로 판매수량에서 제외하되, 제외한 몫을 채널별로 따로
싣는다. 조용히 빼면 화면의 숫자가 원장과 안 맞고 그 차이를 아무도 못 되짚는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import String, cast, func, select
from sqlalchemy.orm import Session

from app.models import (
    CoupangRgOrderItem,
    CoupangRocketSalesDaily,
    CoupangVendorItemSalesDaily,
    Order,
    OtaoItemNameMap,
    OtaoPurchaseOrderLine,
    ProductChannelMapping,
    ProductMaster,
    RocketProductCostMap,
    SyncLog,
)

# 수요가 아닌 상태 — 빼되 따로 센다.
NON_DEMAND_STATUSES = {"cancelled", "returned"}


@dataclass
class ChannelHealth:
    key: str
    label: str
    company: str
    sell_type: str
    source_table: str
    bridge: str
    quantity: int = 0
    quantity_mapped: int = 0
    quantity_excluded: int = 0  # 취소·반품
    # ★한 채널 상품 ID가 서로 다른 상품 여러 개를 가리키는 경우의 수량(적대 리뷰 P1-1).
    #   붙이지 않고 「매핑 모호」로 드러낸다 — 고르면 «조용한 발주 오염»이다.
    quantity_ambiguous: int = 0
    rows: int = 0
    days_with_rows: int = 0
    # ★「그 날짜에 수집이 있었는가」를 되짚을 수 있는 채널인가. False면 화면이 그렇게 말한다.
    missing_day_evidence: bool = False
    # 근거가 있는 채널에 한해: 수집은 됐는데 판매가 0이던 날 / 수집 근거조차 없는 날
    days_collected_zero: list[str] = field(default_factory=list)
    days_no_data: list[str] = field(default_factory=list)

    @property
    def mapping_rate(self) -> float | None:
        """수량 기준. 분모가 0이면 «0%»가 아니라 «잴 수 없음»이다."""
        if self.quantity <= 0:
            return None
        return round(self.quantity_mapped / self.quantity * 100, 2)


@dataclass
class SalesTimeseries:
    window_start: date
    window_end: date
    # 창의 날짜 축 — `rows[*].series`가 이 배열과 **자리로** 대응한다.
    dates: list[str] = field(default_factory=list)
    channels: list[ChannelHealth] = field(default_factory=list)
    # SKU별 합계 — {internal_sku, product_name, total, by_channel}
    rows: list[dict] = field(default_factory=list)
    # 날짜별 합계 — {date, total, by_channel}
    daily: list[dict] = field(default_factory=list)
    # 매핑 못 붙은 몫(채널별 수량). 조용히 빼지 않는다(계약 §2-9).
    unmapped: dict[str, int] = field(default_factory=dict)
    # ★발주 축과의 다리 상태. 화면이 자백해야 하는 것.
    order_axis: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _daterange(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _collected_days(session: Session, channel_id: int, days: list[date]) -> set[date]:
    """성공한 수집 run이 «덮은» 날짜들.

    `sync_log`는 run 1건이 `date_from~date_to` 창을 덮는다(롤링창이라 한 날짜를 수십 번
    다시 조회한다). 그래서 「그 날짜에 run이 돌았나」가 아니라 **「그 날짜를 덮은 성공 run이
    하나라도 있었나」**를 묻는 것이 옳다 — 앞의 물음으로 세면 롤링 재조회가 통째로 안 보인다.
    """
    runs = session.execute(
        select(SyncLog.date_from, SyncLog.date_to).where(
            SyncLog.channel_id == channel_id, SyncLog.status == "success"
        )
    ).all()
    covered: set[date] = set()
    for f, t in runs:
        if f is None or t is None:
            continue
        f_d = f.date() if hasattr(f, "date") else f
        t_d = t.date() if hasattr(t, "date") else t
        for d in days:
            if f_d <= d <= t_d:
                covered.add(d)
    return covered


def _orders_channel(
    session: Session, *, channel_id: int, start: date, end: date
) -> tuple[list[tuple[date, str | None, int]], int, int]:
    """`orders` 파이프라인 채널. 반환 = (셀 목록, 취소·반품 수량, 행수)."""
    q = (
        select(
            func.date(Order.order_date),
            ProductMaster.internal_sku,
            Order.quantity,
            Order.status,
        )
        .select_from(Order)
        .outerjoin(ProductMaster, ProductMaster.id == Order.product_id)
        .where(
            Order.channel_id == channel_id,
            func.date(Order.order_date) >= start.isoformat(),
            func.date(Order.order_date) <= end.isoformat(),
        )
    )
    cells: list[tuple[date, str | None, int]] = []
    excluded = 0
    rows = 0
    for d, sku, qty, status in session.execute(q):
        rows += 1
        n = int(qty or 0)
        if (status or "").lower() in NON_DEMAND_STATUSES:
            excluded += n
            continue
        cells.append((date.fromisoformat(str(d)), sku, n))
    return cells, excluded, rows


def _channel_sku_index(session: Session) -> dict[str, set[str]]:
    """`channel_product_id` → 그 키가 가리키는 `internal_sku` **집합**.

    ★★**조인으로 풀면 안 된다** (적대 리뷰 P1-1). `product_channel_mapping.channel_product_id`엔
    unique 제약이 없고 prod에 **중복 55키·121행**이 실재한다(한 키가 서로 다른 상품 **5개**를
    가리키는 경우까지). outerjoin은 그 행마다 판매를 복제해 **같은 수량을 N번 더한다** —
    실측으로 Wing 3P 오픽스가 원장 1,980 → 화면 2,099(+6.0%)로 부풀어 있었고, 그 몫이
    **팔린 적 없는 SKU에도 배분**됐다. 조용히 넣으면 발주 오염이다(계약 §2-9).

    그래서 «집합»으로 들고 와서 파이썬에서 가른다:
      - 집합 크기 1 → 그 상품에 붙인다(단순 중복 행은 여기서 안전하게 접힌다)
      - 집합 크기 ≥2 → **붙이지 않는다.** 다수결로 고르지 않는다 — 9:1이어도 소수 쪽이 옳을 수
        있고, 발주 수량이 걸린 자리에서 「아마 이것」은 근거가 아니다(D-INV-5의 `ambiguous`와
        같은 규율). 화면이 「매핑 모호」로 드러낸다.
    """
    idx: dict[str, set[str]] = {}
    for cpid, sku in session.execute(
        select(ProductChannelMapping.channel_product_id, ProductMaster.internal_sku).join(
            ProductMaster, ProductMaster.id == ProductChannelMapping.product_id
        )
    ):
        if cpid is None or sku is None:
            continue
        idx.setdefault(str(cpid), set()).add(sku)
    return idx


def _resolve(idx: dict[str, set[str]], key) -> tuple[str | None, bool]:
    """(internal_sku, 모호한가). 모호하면 `(None, True)` — 「모름」이지 「없음」이 아니다."""
    if key is None:
        return None, False
    skus = idx.get(str(key))
    if not skus:
        return None, False
    if len(skus) > 1:
        return None, True
    return next(iter(skus)), False


def _wing_3p(
    session: Session, *, account_key: str, start: date, end: date, idx: dict[str, set[str]]
) -> tuple[list[tuple[date, str | None, int]], int, int, int]:
    """쿠팡 Wing 3P 정본. 다리는 `vendor_item_id`이지 `product_id`가 아니다."""
    q = select(
        CoupangVendorItemSalesDaily.sale_date,
        CoupangVendorItemSalesDaily.vendor_item_id,
        CoupangVendorItemSalesDaily.units_sold,
    ).where(
        CoupangVendorItemSalesDaily.account_key == account_key,
        CoupangVendorItemSalesDaily.sale_date >= start,
        CoupangVendorItemSalesDaily.sale_date <= end,
    )
    cells, rows, ambiguous = [], 0, 0
    for d, vid, qty in session.execute(q):
        rows += 1
        sku, amb = _resolve(idx, vid)
        n = int(qty or 0)
        if amb:
            ambiguous += n
        cells.append((d if isinstance(d, date) else date.fromisoformat(str(d)), sku, n))
    return cells, 0, rows, ambiguous


def _rg_2p(
    session: Session, *, account_key: str, start: date, end: date, idx: dict[str, set[str]]
) -> tuple[list[tuple[date, str | None, int]], int, int, int]:
    """쿠팡 로켓그로스(2P). 같은 다리(`vendor_item_id`)를 쓴다."""
    q = select(
        func.date(CoupangRgOrderItem.paid_at),
        CoupangRgOrderItem.vendor_item_id,
        CoupangRgOrderItem.sales_quantity,
    ).where(
        CoupangRgOrderItem.account_key == account_key,
        func.date(CoupangRgOrderItem.paid_at) >= start.isoformat(),
        func.date(CoupangRgOrderItem.paid_at) <= end.isoformat(),
    )
    cells, rows, ambiguous = [], 0, 0
    for d, vid, qty in session.execute(q):
        rows += 1
        sku, amb = _resolve(idx, vid)
        n = int(qty or 0)
        if amb:
            ambiguous += n
        cells.append((date.fromisoformat(str(d)), sku, n))
    return cells, 0, rows, ambiguous


def _rocket_1p(
    session: Session, *, start: date, end: date
) -> tuple[list[tuple[date, str | None, int]], int, int]:
    """로켓배송 1P 소비자 판매. 다리는 `rocket_product_cost_map`(→ `internal_sku` 문자열)."""
    q = (
        select(
            CoupangRocketSalesDaily.date,
            ProductMaster.internal_sku,
            CoupangRocketSalesDaily.qty,
        )
        .select_from(CoupangRocketSalesDaily)
        .outerjoin(
            RocketProductCostMap,
            RocketProductCostMap.product_number == CoupangRocketSalesDaily.sku_id,
        )
        .outerjoin(
            ProductMaster, ProductMaster.internal_sku == RocketProductCostMap.internal_sku
        )
        .where(
            CoupangRocketSalesDaily.date >= start,
            CoupangRocketSalesDaily.date <= end,
        )
    )
    cells = []
    rows = 0
    for d, sku, qty in session.execute(q):
        rows += 1
        cells.append((d if isinstance(d, date) else date.fromisoformat(str(d)), sku, int(qty or 0)))
    return cells, 0, rows


# (key, 라벨, 회사, 판매유형, 원천 테이블, 다리, sync_log로 결손을 되짚을 수 있는 channel_id)
_SPECS = [
    ("naver", "네이버 스마트스토어", "주식회사 오하이", "스마트스토어", "orders", "orders.product_id", 6),
    ("cafe24", "자사몰 (cafe24)", "주식회사 오하이테크", "자사몰", "orders", "orders.product_id", 7),
    ("wing3p_ofix", "쿠팡 Wing 3P — 오픽스", "개인회사 오픽스", "3P",
     "coupang_vendor_item_sales_daily", "vendor_item_id → product_channel_mapping", None),
    ("wing3p_ohitech", "쿠팡 Wing 3P — 오하이테크", "주식회사 오하이테크", "3P",
     "coupang_vendor_item_sales_daily", "vendor_item_id → product_channel_mapping", None),
    ("rg2p_ofix", "쿠팡 로켓그로스 2P — 오픽스", "개인회사 오픽스", "2P",
     "coupang_rg_order_item", "vendor_item_id → product_channel_mapping", None),
    ("rg2p_ohitech", "쿠팡 로켓그로스 2P — 오하이테크", "주식회사 오하이테크", "2P",
     "coupang_rg_order_item", "vendor_item_id → product_channel_mapping", None),
    ("rocket1p", "쿠팡 로켓배송 1P", "주식회사 오하이테크", "1P",
     "coupang_rocket_sales_daily", "sku_id → rocket_product_cost_map", None),
]


def _fetch(session: Session, key: str, start: date, end: date, idx: dict[str, set[str]]):
    """반환 = (셀, 취소·반품 수량, 행수, 모호 수량)."""
    if key == "naver":
        return (*_orders_channel(session, channel_id=6, start=start, end=end), 0)
    if key == "cafe24":
        return (*_orders_channel(session, channel_id=7, start=start, end=end), 0)
    if key == "wing3p_ofix":
        return _wing_3p(session, account_key="COUPANG_WING1", start=start, end=end, idx=idx)
    if key == "wing3p_ohitech":
        return _wing_3p(session, account_key="COUPANG_WING2", start=start, end=end, idx=idx)
    if key == "rg2p_ofix":
        return _rg_2p(session, account_key="COUPANG_WING1", start=start, end=end, idx=idx)
    if key == "rg2p_ohitech":
        return _rg_2p(session, account_key="COUPANG_WING2", start=start, end=end, idx=idx)
    if key == "rocket1p":
        return (*_rocket_1p(session, start=start, end=end), 0)
    raise ValueError(f"모르는 채널 키: {key}")  # pragma: no cover


def _order_axis_bridge(session: Session) -> dict:
    """발주 축(GAPIP) ↔ 판매 축(OHI)이 이어지는가 — 화면이 자백해야 하는 것."""
    codes = set(
        session.scalars(select(OtaoPurchaseOrderLine.product_code).distinct()).all()
    )
    skus = set(session.scalars(select(ProductMaster.internal_sku).distinct()).all())
    mapped_codes = set(
        c for c in session.scalars(select(OtaoItemNameMap.product_code).distinct()).all() if c
    )
    overlap = codes & skus
    return {
        "order_axis_codes": len(codes),
        "sales_axis_skus": len(skus),
        "overlap": len(overlap),
        "order_codes_reached_by_name_map": len(mapped_codes),
        "note": (
            "발주 축 라벨(`product_code`)과 판매 축 라벨(`internal_sku`)은 겹치는 값이 "
            f"{len(overlap)}개다. 다리가 없으면 이 판매 시계열을 예약 잔량·발주 누계와 "
            "**같은 줄에 놓을 수 없다** — 억지로 이으면 말이 되는 것처럼 보이는 거짓 대비가 된다."
        ),
    }


def build_sales_timeseries(session: Session, *, days: int = 60, today: date | None = None) -> SalesTimeseries:
    end = today or date.today()
    start = end - timedelta(days=days - 1)
    all_days = _daterange(start, end)

    out = SalesTimeseries(window_start=start, window_end=end)
    out.dates = [d.isoformat() for d in all_days]
    day_pos = {d: i for i, d in enumerate(all_days)}
    per_sku: dict[str, dict] = {}
    per_day: dict[date, dict] = {d: {} for d in all_days}
    idx = _channel_sku_index(session)

    for key, label, company, sell_type, table, bridge, sync_channel_id in _SPECS:
        cells, excluded, rows, ambiguous = _fetch(session, key, start, end, idx)
        health = ChannelHealth(
            key=key,
            label=label,
            company=company,
            sell_type=sell_type,
            source_table=table,
            bridge=bridge,
            quantity_excluded=excluded,
            quantity_ambiguous=ambiguous,
            rows=rows,
        )
        seen_days: set[date] = set()
        for d, sku, qty in cells:
            health.quantity += qty
            seen_days.add(d)
            if sku:
                health.quantity_mapped += qty
                row = per_sku.setdefault(
                    sku,
                    {
                        "internal_sku": sku,
                        "product_name": None,
                        "total": 0,
                        "by_channel": {},
                        # ★S3 원문의 첫 요구는 «시계열»이다 — SKU×창합계만으로는 그게 아니다
                        #   (적대 리뷰 P1-2). 창 길이만큼의 일별 배열을 여기서 만든다.
                        "series": [0] * len(all_days),
                    },
                )
                row["total"] += qty
                row["by_channel"][key] = row["by_channel"].get(key, 0) + qty
                row["series"][day_pos[d]] += qty
            else:
                # 「모름」이지 「0」이 아니다 — 조용히 빼면 수요가 그만큼 사라진다(§2-9).
                # ★수량 0인 미매핑 행도 «세되»(카운터는 정직해야 한다) 화면 목록에는 안 싣는다 —
                #   아래에서 0인 채널을 걷어낸다. 「0개가 빠져 있다」는 정보가 아니라 잡음이고,
                #   진짜 결손을 그 줄들 사이에 묻는다.
                out.unmapped[key] = out.unmapped.get(key, 0) + qty
            if qty:
                per_day[d][key] = per_day[d].get(key, 0) + qty
        health.days_with_rows = len(seen_days)

        if sync_channel_id is not None:
            health.missing_day_evidence = True
            covered = _collected_days(session, sync_channel_id, all_days)
            for d in all_days:
                if d in seen_days:
                    continue
                (health.days_collected_zero if d in covered else health.days_no_data).append(
                    d.isoformat()
                )
        else:
            # ★근거가 없는 채널은 «있는 척» 하지 않는다. 빈 날을 0으로도 결손으로도 안 적는다.
            health.days_no_data = []
            health.days_collected_zero = []

        out.channels.append(health)

    names = dict(
        session.execute(
            select(ProductMaster.internal_sku, ProductMaster.product_name)
        ).all()
    )
    for sku, row in per_sku.items():
        row["product_name"] = names.get(sku)
    out.rows = sorted(per_sku.values(), key=lambda r: (-r["total"], r["internal_sku"]))
    out.daily = [
        {"date": d.isoformat(), "by_channel": per_day[d], "total": sum(per_day[d].values())}
        for d in all_days
    ]
    out.unmapped = {k: v for k, v in out.unmapped.items() if v}
    out.order_axis = _order_axis_bridge(session)

    out.notes.append(
        f"판매 축은 `product_master.internal_sku`다. 창은 {start.isoformat()} ~ {end.isoformat()}"
        f"({days}일)이고 채널마다 수집 시작일이 달라 창 전체를 덮지 않는 채널이 있다."
    )
    blind = [c.label for c in out.channels if not c.missing_day_evidence]
    if blind:
        out.notes.append(
            "결손일과 「판매 0」을 구분할 근거가 **없는** 채널: "
            + ", ".join(blind)
            + ". 이 채널들의 원천 테이블은 `sync_log`가 덮지 않는다 — 빈 날을 0으로도 "
            "결손으로도 단정하지 않는다(계약 §2-8)."
        )
    if out.unmapped:
        total_unmapped = sum(out.unmapped.values())
        out.notes.append(
            f"상품코드에 못 붙은 판매 {total_unmapped:,}개가 SKU 시계열에서 빠져 있다 — "
            "채널별 내역은 「매핑 필요」 칸에 있다."
        )
    amb = sum(c.quantity_ambiguous for c in out.channels)
    if amb:
        out.notes.append(
            f"채널 상품 ID 하나가 **서로 다른 상품 여러 개**를 가리키는 판매가 {amb:,}개다 — "
            "다수결로 고르지 않고 「매핑 모호」로 남겼다. 고르면 그만큼이 조용한 발주 오염이 된다."
        )
    if out.order_axis.get("overlap", 0) == 0:
        out.notes.append(
            "★발주 축(`product_code`)과 이 판매 축(`internal_sku`)을 잇는 다리가 아직 없다"
            f"(겹치는 값 {out.order_axis['overlap']}개). 그래서 이 화면은 판매만 말하고, "
            "예약 잔량·발주 누계와 **같은 줄에 놓지 않는다.**"
        )
    return out
