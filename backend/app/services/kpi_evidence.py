"""KPI 카드 4칸의 «근거» — 카드 숫자가 어디서 나왔는지 채널별로 펼치고 검산한다.

계약: `docs/contracts/CONTRACT_kpi_evidence_page.md` (Jino 승인 2026-08-23)

★이 모듈은 **아무것도 새로 계산하지 않는다.** 입력은 `_channel_rows()`가 만든 그 행들이고,
  손익은 `net_contribution()`이 낸 그 값이다 — 즉 KPI 카드가 실제로 더한 것과 **같은 물건**이다.
  왜 이렇게까지 하나: 경로가 둘이면 언젠가 반드시 갈라지고(D-22 실사고 — 카드 569,635원 vs
  표 −28,253원), **카드와 다른 숫자를 말하는 근거는 근거가 아니라 두 번째 버그**이기 때문이다.

★두 번째 규율: **행이 안 가진 항목은 0으로 채우지 않는다.** 채널 행은 세 갈래에서 온다 —
  `calculate_channel_summary`(주문 축)·RG 평행 엔진·로켓1P 평행 엔진 — 그리고 뒤의 둘은
  분해 항목을 **원래 다 갖고 있지 않다.** 없는 걸 0으로 채우면 「원가 0원」이라는 거짓말이
  되고, 이 저장소는 그 거짓말(원가 미상 매출이 순이익을 부풀린 것)을 이미 한 번 겪었다.
  ⇒ 없으면 `None`으로 내보내고 화면이 "—"로 그린다. 그 대신 **잔차(residual)**를 계산해
  「설명 못 한 금액」을 숫자로 자백한다. 못 설명하는 것은 숨기는 게 아니라 이름을 붙인다.
"""

from __future__ import annotations

from decimal import Decimal

from app.services.profit_calculator import (
    NET_SCOPE_AD_ONLY,
    net_contribution,
)

ZERO = Decimal("0")

#: 잔차를 재는 눈금. **원 단위 아래 2자리**로 끊는다.
#: ★왜 필요한가: 납부 부가세가 `×10/110` 나눗셈이라 순이익에 **28자리 먼지**가 붙는다
#:   (실측: 검산이 정확히 맞는 행의 잔차가 `0E-22`로 나온다). 먼지를 그대로 두면
#:   ①화면에 `0E-22`가 찍히고 ②`residual == 0` 비교가 **라이브에서 거짓**이 되어 검산 ✓가
#:   ✗로 뒤집힌다 — 즉 「맞는데 틀렸다고 말하는 화면」이 된다. 단위 테스트의 딱 떨어지는
#:   픽스처로는 안 드러나고 실제 금액에서만 드러나는 자리다.
RESIDUAL_SCALE = Decimal("0.01")

#: 순이익 검산식에서 매출에서 «빼는» 항목들. 표시 순서 = 검산식에 쓰는 순서.
#: 행이 이 키를 안 갖고 있으면 그 행은 그 항목을 **모르는** 것이다(0이 아니다).
DEDUCTION_KEYS: tuple[str, ...] = (
    "cost",           # 원가
    "commission",     # 판매 수수료
    "ad_spend",       # 광고비
    "shipping",       # 판매자 배송비
    "fixed_cost",     # 월 고정비(3PL) 일할 배분분
    "promo_burden",   # 프로모션 분담금 — 로켓1P 판매 축에만 있다
    "payable_vat",    # 납부 부가세 = 매출세액 − 매입세액공제
)


def _opt_dec(value) -> Decimal | None:
    """행의 값 → Decimal. 키가 없거나 None이면 **모른다**는 뜻이라 None을 돌려준다."""
    if value is None:
        return None
    return Decimal(str(value))


def _cost_is_unknown(row: dict) -> bool:
    """이 행의 `cost: "0"`이 «원가가 0원»이 아니라 «원가를 모른다»인가.

    ★적대 리뷰 1R P1-1 — 초판은 「키가 없으면 모른다」만 봤는데, **두 평행 엔진은 원가를 못 믿는
      분기에서 `cost`를 «0으로 실어 보낸다».** 그 분기의 주석이 스스로 그렇게 말한다:
        · `rocket_1p_channel_pnl.py` — *"basis=settlement(기본): … 순이익 없음(원가 축이 안 닿는다)"*
        · `rg_channel_pnl.py`        — *"원가를 못 믿는다 → 순이익 없음. … `cost`를 0으로 실어
                                        보내면 화면이 「원가 0」으로 읽으므로"*
      (RG는 그렇게 적어 놓고 바로 아래에서 `cost = ZERO`를 싣는다.)
      그래서 초판 화면은 로켓1P 원가 칸에 **「0원」**을 찍었다 — 계약 §2-2가 금지한 바로 그 거짓말.

    판정 신호는 **행이 스스로 낸 자백 둘** 중 하나다:
      · `net_scope == "ad_only"` — 「손익을 못 재는 구간이라 확정 비용인 광고비만 반영된 하한」
      · `net_profit is None`     — 「잴 것이 아무것도 없다」(RG의 원가 미신뢰 분기가 여기다)
    둘 다 아니면(=`full`이거나 평범한 주문 축 행) 그 `cost`는 실측이므로 손대지 않는다.

    ★`net_scope`가 **키로 없는 것**과 **값이 None인 것**을 같게 보면 안 된다 —
      `calculate_channel_summary`(주문 축) 행은 이 키를 아예 안 싣는다. `not in (None, AD_ONLY)`로
      쓰면 그 행 전부가 조건을 통과해, 원가가 진짜 0인 평범한 채널까지 "—"로 덮인다.

    ★★신호를 두 번 틀렸다 — 그 이력을 남긴다(적대 리뷰 2R P1-1 미해소).
      초판 신호는 「`net_profit is None`」이었는데, **로켓1P producer는 `net_profit`을 None으로
      내지 않는다** — 계산서 축에서 하한을 «자기가 만들어» 넣는다
      (`elif ad_spend > ZERO: net, scope = -ad_spend, NET_SCOPE_AD_ONLY` → `"net_profit": str(net)`).
      그래서 RG만 고쳐지고 로켓1P는 **한 글자도 안 바뀌었다.** 게다가 부작용이 더 나빴다:
      같은 「원가 미상」이 **광고비 유무로 갈렸다** — 광고비 0이면 `net_profit=None`이라 "—",
      광고비가 있으면 "0원". **광고를 돌린 날만 거짓말이 뜬다**(라이브의 상시 조건이 그쪽이다).
      ⇒ 행이 «이미 말하고 있는» 자백(`net_scope`)을 읽는다. 새 신호를 발명하지 않는다.

    ★고치는 자리를 producer가 아니라 여기로 잡은 이유: producer의 `cost`는 다른 화면들이 이미 읽고
      있어 건드리면 이번 계약의 「숫자 자체 수정 안 함」을 넘는다. 여기서 내리면 **근거 화면에서만**
      «모른다»가 되고 어떤 합계도 안 바뀐다(0을 빼는 것이라 잔차가 불변이다).
    """
    cannot_measure = (
        row.get("net_scope") == NET_SCOPE_AD_ONLY or row.get("net_profit") is None
    )
    if not cannot_measure:
        return False
    cost = _opt_dec(row.get("cost"))
    return cost is not None and cost == ZERO


def build_row_evidence(row: dict, rocket_channel_id: int | None) -> dict:
    """채널 행 1개 → 근거 행 1개.

    돌려주는 것:
      · `deductions`  — 항목별 금액(모르면 None)
      · `net_profit`  — **KPI가 실제로 더한 값**(`net_contribution`). 하한(−광고비)도 여기 담긴다
      · `residual`    — net − (매출 − 아는 항목 합). 0이면 검산이 맞은 것
      · `explains_net`·`missing` — 화면이 「이 행은 왜 안 맞나」를 말할 수 있게 하는 근거
    """
    revenue = Decimal(str(row["revenue"]))
    net, basis_rev, floor_ad = net_contribution(row, revenue)

    deductions = {k: _opt_dec(row.get(k)) for k in DEDUCTION_KEYS}
    if _cost_is_unknown(row):
        deductions["cost"] = None   # 「0원」이 아니라 「모른다」 — 위 함수 주석 참조
    missing = [k for k, v in deductions.items() if v is None]
    known_sum = sum((v for v in deductions.values() if v is not None), ZERO)

    if net is None:
        # 잴 것이 아무것도 없다(매출도 광고비도 0) — 검산할 대상 자체가 없다.
        residual: Decimal | None = None
        explains = False
    else:
        residual = _q(net - (revenue - known_sum))
        # ★`explains_net`은 **화면에 그린 산술이 순이익을 재현하는가**만 묻는다 — `missing`이
        #   비었는지는 묻지 않는다. 왜냐하면 `promo_burden`처럼 **한 채널에만 있는 항목**은
        #   나머지 행에서 영원히 비어 있고, 그걸 미달로 치면 정상 행이 전부 ✗가 되어
        #   ✓/✗ 표시가 신호를 잃는다. 「안 실린 항목이 있다」는 `missing`이 따로 말한다.
        explains = residual == ZERO

    return {
        "channel_id": row.get("channel_id"),
        "channel_name": row.get("channel_name") or "",
        "revenue": str(revenue),
        "product_revenue": _str_or_none(row.get("product_revenue")),
        "shipping_revenue": _str_or_none(row.get("shipping_revenue")),
        "deductions": {k: (None if v is None else str(v)) for k, v in deductions.items()},
        "missing": missing,
        "net_profit": None if net is None else str(net),
        "net_scope": row.get("net_scope"),
        "net_floor_ad": str(floor_ad),
        "net_basis_revenue": str(basis_rev),
        "unmapped_revenue": _str_or_none(row.get("unmapped_revenue")),
        "residual": None if residual is None else str(residual),
        "explains_net": explains,
        "order_count": int(row.get("order_count") or 0),
        # ★로켓1P의 그 칸은 주문 건수가 아니라 **판매수량**이라 카드에서 빠진다(`_kpi_totals`).
        #   화면이 이 사실을 말하지 않으면 「채널 합이 카드보다 크다」가 오류로 보인다.
        "counted_in_order_card": row.get("channel_id") != rocket_channel_id,
        "revenue_basis": row.get("revenue_basis"),
    }


def _str_or_none(value) -> str | None:
    return None if value is None else str(value)


def build_kpi_evidence(
    rows: list[dict],
    totals: dict,
    rocket_channel_id: int | None,
    company_map: dict[int, tuple[str, str, bool]] | None = None,
) -> dict:
    """채널 행들 + `_kpi_totals()` 산출 → 카드 4칸의 근거 한 벌.

    `totals`는 **카드가 쓴 그 dict를 그대로** 받는다 — 근거가 카드와 갈라질 수 없게 하는 자리다.
    """
    company_map = company_map or {}
    ev_rows = []
    for row in rows:
        ev = build_row_evidence(row, rocket_channel_id)
        cid = ev["channel_id"]
        classified = company_map.get(cid) if cid is not None else None
        ev["company"] = classified[0] if classified else None
        ev["label"] = classified[1] if classified else ev["channel_name"]
        ev_rows.append(ev)

    # 매출 큰 순 — 「어느 채널 탓인가」를 위에서부터 읽게 한다.
    ev_rows.sort(key=lambda r: Decimal(r["revenue"]), reverse=True)

    # 항목별 합계 — **아는 행만** 더한다. 그래서 「이 합계가 전부는 아니다」를 missing이 말한다.
    ded_totals: dict[str, str] = {}
    ded_missing: dict[str, int] = {}
    for key in DEDUCTION_KEYS:
        acc, unknown = ZERO, 0
        for r in ev_rows:
            v = r["deductions"][key]
            if v is None:
                unknown += 1
            else:
                acc += Decimal(v)
        ded_totals[key] = str(acc)
        ded_missing[key] = unknown

    revenue_total = totals["revenue"]
    net_total = totals["net_profit"]
    known_sum = sum((Decimal(v) for v in ded_totals.values()), ZERO)
    residual_total = _q(net_total - (revenue_total - known_sum))

    # 이익률 분모가 총매출과 다른 몫 — 「손익을 못 잰 매출」이다. 이 차액을 안 보여 주면
    # 사용자는 −285,507 ÷ 1,670,990을 손으로 계산해 보고 화면과 다르다고 결론 내린다.
    basis_revenue = totals["basis_revenue"]
    rate = (net_total / basis_revenue * 100) if basis_revenue > 0 else ZERO

    order_rows_total = sum(r["order_count"] for r in ev_rows)
    order_excluded = sum(
        r["order_count"] for r in ev_rows if not r["counted_in_order_card"]
    )

    return {
        "rows": ev_rows,
        "deduction_keys": list(DEDUCTION_KEYS),
        "deduction_totals": ded_totals,
        "deduction_unknown_rows": ded_missing,
        "totals": {
            "revenue": str(revenue_total),
            "net_profit": str(net_total),
            "basis_revenue": str(basis_revenue),
            "floor_ad": str(totals["floor_ad"]),
            "profit_rate": str(rate.quantize(Decimal("0.01"))),
            "order_count": totals["order_count"],
            "residual": str(residual_total),
            # 이익률 분모에서 빠진 매출 — 「원가를 몰라 손익을 못 잰」 몫이다.
            "unmeasured_revenue": str(revenue_total - basis_revenue),
        },
        # ★검산 — 화면이 사람 눈에 맡기지 않고 스스로 ✓/✗를 찍기 위한 값.
        #   합계 3종은 같은 함수에서 나오므로 «구조적으로» 참이어야 한다. 그런데도 찍는 이유:
        #   언젠가 누가 경로를 하나 더 만들면 그날 이 칸이 ✗가 되어 그 사실을 말해 준다.
        # ★비교는 **원 단위 2자리로 끊고** 한다 — 행을 정렬해 더한 순서가 카드와 달라
        #   Decimal 28자리 끝자리가 갈릴 수 있고, 그건 「금액이 다르다」가 아니라 「먼지」다.
        "checks": {
            "revenue_matches": _q(_sum(ev_rows, "revenue")) == _q(revenue_total),
            "net_matches": _q(_sum_net(ev_rows)) == _q(net_total),
            "order_count_matches": (order_rows_total - order_excluded) == totals["order_count"],
            "net_fully_explained": residual_total == ZERO,
        },
        "order_count_excluded": order_excluded,
        "has_floor": totals["floor_ad"] > ZERO
        or any(r["net_scope"] == NET_SCOPE_AD_ONLY for r in ev_rows),
    }


def _q(value: Decimal) -> Decimal:
    """원 단위 2자리로 끊는다. **음의 0(`-0.00`)은 0으로 편다** — Decimal은 부호를 보존해서
    검산이 정확히 맞는 행이 `-0.00`으로 나오고, 그 문자열이 그대로 화면·응답에 실린다."""
    q = value.quantize(RESIDUAL_SCALE)
    return ZERO.quantize(RESIDUAL_SCALE) if q == ZERO else q


def _sum(rows: list[dict], key: str) -> Decimal:
    return sum((Decimal(r[key]) for r in rows), ZERO)


def _sum_net(rows: list[dict]) -> Decimal:
    return sum(
        (Decimal(r["net_profit"]) for r in rows if r["net_profit"] is not None), ZERO
    )
