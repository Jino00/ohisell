# test_ad_spend_reaches_net_profit.py — D-22(2026-08-19)
#
# 지키는 계약 한 줄: **부모 소계가 자식의 광고비를 흡수했으면 그 손익도 흡수해야 한다.**
#
# 무엇이 있었나(라이브 2026-08-18, Jino 발견): 로켓배송 1P는 계산서 축에서 순이익을 못 내
# `net_profit=None`이었는데, `group_summary_by_company`는 그런 자식의 `ad_spend`는 부모에
# 더하면서 `net_profit`은 건너뛰었다. 결과로 하루 597,888원이 광고비 칸에는 있는데 순이익
# 어디에서도 안 빠졌고, 회사 순이익이 186,825원(=자사몰 것 그대로)으로 떴다. 실제로는
# -411,063원이다. RoAS도 분자(매출)엔 로켓이 없고 분모(광고비)엔 있어 함께 오염됐다.
#
# 왜 「이익률 분모」를 따로 두는가: 계산서 축 로켓은 **매출은 있지만 그 매출의 원가를 모른다.**
# 그 매출을 분모에 넣으면 「광고비만 반영된 하한 ÷ 원가 미상 매출」이라는 뜻 없는 비율이 된다.
from __future__ import annotations

from decimal import Decimal

from app.services.profit_calculator import (
    NET_SCOPE_AD_ONLY,
    NET_SCOPE_FULL,
    NET_SCOPE_PARTIAL,
    group_summary_by_company,
)

# 라이브 2026-08-18 재현값 (prod /api/dashboard/channel-breakdown 실측)
CMAP = {
    6: ("주식회사 오하이", "주식회사 오하이 · 네이버 스마트스토어", True),
    7: ("주식회사 오하이테크", "주식회사 오하이테크 · 자사몰(cafe24)", True),
    5: ("주식회사 오하이테크", "주식회사 오하이테크 · 쿠팡 로켓배송", True),
}


def _rows():
    return [
        {"channel_id": 6, "channel_name": "네이버", "revenue": "1755400.00",
         "product_revenue": "1539400.00", "shipping_revenue": "216000.00",
         "ad_spend": "606874.54034", "net_profit": "382809.5087818181818181818182",
         "order_count": 102, "unmapped_revenue": "0"},
        {"channel_id": 7, "channel_name": "자사몰", "revenue": "277300.00",
         "product_revenue": "277300.00", "shipping_revenue": "0",
         "ad_spend": "31174.0", "net_profit": "186825.3636363636363636363636",
         "order_count": 7, "unmapped_revenue": "180000"},
        # 로켓1P 계산서 축: 매출 0(그날 계산서 없음)·광고비만 나갔다 → 하한 -광고비
        {"channel_id": 5, "channel_name": "쿠팡 로켓배송", "revenue": "0",
         "product_revenue": "0", "shipping_revenue": "0",
         "ad_spend": "597888.00", "net_profit": "-597888.00",
         "net_scope": NET_SCOPE_AD_ONLY, "net_basis_revenue": "0",
         "order_count": 0, "unmapped_revenue": "0", "revenue_basis": "settlement"},
    ]


def _by(rows, kind, label=None):
    return next(r for r in rows if r["kind"] == kind and (label is None or r["label"] == label))


def test_child_ad_spend_reaches_parent_net_profit():
    """★이 파일의 존재 이유. 회사·전체 순이익이 로켓 광고비만큼 내려가야 한다."""
    out = group_summary_by_company(_rows(), CMAP)

    tech = _by(out, "company", "주식회사 오하이테크")
    assert Decimal(tech["ad_spend"]) == Decimal("629062.00")          # 광고비는 종전과 같고
    assert Decimal(tech["net_profit"]) == Decimal("186825.3636363636363636363636") - Decimal("597888")
    assert Decimal(tech["net_profit"]) < 0                            # 흑자로 보이던 것이 적자다

    total = _by(out, "total")
    assert Decimal(total["net_profit"]) == (
        Decimal("382809.5087818181818181818182")
        + Decimal("186825.3636363636363636363636")
        - Decimal("597888")
    )
    assert Decimal(total["net_profit"]).quantize(Decimal("0.01")) == Decimal("-28253.13")


def test_profit_rate_denominator_excludes_unmeasured_revenue():
    """이익률 분모 = **손익을 실제로 잰 매출**. 로켓의 계산서 매출은 여기 안 들어간다."""
    rows = _rows()
    rows[2]["revenue"] = "1578000.00"       # 계산서가 나온 날: 매출은 있지만 원가를 모른다
    rows[2]["product_revenue"] = "1578000.00"
    out = group_summary_by_company(rows, CMAP)

    total = _by(out, "total")
    assert Decimal(total["revenue"]) == Decimal("3610700.00")            # 표의 총매출엔 들어가고
    assert Decimal(total["net_basis_revenue"]) == Decimal("2032700.00")  # 손익 분모엔 안 들어간다
    rate = Decimal(total["net_profit"]) / Decimal(total["net_basis_revenue"]) * 100
    assert Decimal(total["profit_rate"]) == rate.quantize(Decimal("0.01"))


def test_net_scope_tells_which_rows_are_only_a_floor():
    out = group_summary_by_company(_rows(), CMAP)
    assert _by(out, "leaf", "주식회사 오하이 · 네이버 스마트스토어")["net_scope"] == NET_SCOPE_FULL
    rocket = _by(out, "leaf", "주식회사 오하이테크 · 쿠팡 로켓배송")
    assert rocket["net_scope"] == NET_SCOPE_AD_ONLY
    assert rocket["profit_rate"] is None            # 분모가 없으므로 비율을 지어내지 않는다
    assert Decimal(rocket["net_floor_ad"]) == Decimal("597888.00")
    for kind in ("company", "total"):
        row = _by(out, kind) if kind == "total" else _by(out, "company", "주식회사 오하이테크")
        assert row["net_scope"] == NET_SCOPE_PARTIAL
        assert Decimal(row["net_floor_ad"]) == Decimal("597888.00")


def test_unmapped_revenue_reaches_every_level():
    """원가를 못 붙인 매출은 이익률을 위로 부풀린다 — 행마다 자백해야 화면이 경고를 켠다."""
    out = group_summary_by_company(_rows(), CMAP)
    leaf = _by(out, "leaf", "주식회사 오하이테크 · 자사몰(cafe24)")
    assert Decimal(leaf["unmapped_revenue"]) == Decimal("180000")
    # 자사몰 이익률 67.4%의 정체 — 매출의 64.9%가 원가 0으로 계산된 것
    ratio = Decimal(leaf["unmapped_revenue"]) / Decimal(leaf["product_revenue"])
    assert ratio.quantize(Decimal("0.001")) == Decimal("0.649")
    assert Decimal(_by(out, "total")["unmapped_revenue"]) == Decimal("180000")


def test_rows_without_the_new_fields_keep_old_behavior():
    """구형 행(net_basis_revenue 없음)은 종전대로 매출 전액이 분모다 — 회귀 방지."""
    rows = [r for r in _rows() if r["channel_id"] != 5]
    out = group_summary_by_company(rows, CMAP)
    total = _by(out, "total")
    assert Decimal(total["net_basis_revenue"]) == Decimal("2032700.00")
    assert total["net_scope"] == NET_SCOPE_FULL
    assert Decimal(total["net_floor_ad"]) == 0


def test_any_row_with_ad_spend_but_no_net_still_leaks_nothing():
    """★적대 리뷰 1R P1-1 — 하한을 만드는 곳이 **집계층 한 곳**이어야 하는 이유.

    `calculate_channel_summary`의 수동매출-only 채널(profit_calculator.py, `manual_by_channel`
    루프)은 `net_profit=None` + `ad_spend`(ad_costs 테이블에서 채움)를 그대로 낸다 — 이번에
    고친 로켓1P와 **완전히 같은 모양**이다. producer마다 하한을 기억하게 두면 한 곳만 빠져도
    그 채널의 광고비가 조용히 샌다. 그래서 `net_contribution`이 집계 시점에 만든다.
    """
    rows = [r for r in _rows() if r["channel_id"] != 5]
    rows.append({           # net_scope도 net_basis_revenue도 없는 「구형·무지성」 행
        "channel_id": 9, "channel_name": "수동매출 채널", "revenue": "500000",
        "product_revenue": "500000", "shipping_revenue": "0", "ad_spend": "300000",
        "net_profit": None, "profit_rate": None, "order_count": 0,
    })
    cmap = {**CMAP, 9: ("주식회사 오하이테크", "주식회사 오하이테크 · 수동매출", True)}
    out = group_summary_by_company(rows, cmap)

    leaf = _by(out, "leaf", "주식회사 오하이테크 · 수동매출")
    assert Decimal(leaf["net_profit"]) == Decimal("-300000")   # 새지 않는다
    assert leaf["net_scope"] == NET_SCOPE_AD_ONLY
    assert Decimal(leaf["net_basis_revenue"]) == 0            # 원가를 모르므로 분모엔 안 들어간다

    total = _by(out, "total")
    assert Decimal(total["net_floor_ad"]) == Decimal("300000")
    assert Decimal(total["net_profit"]) == (
        Decimal("382809.5087818181818181818182")
        + Decimal("186825.3636363636363636363636")
        - Decimal("300000")
    )


def test_no_ad_spend_and_no_net_stays_unknown():
    """광고비도 0이면 하한이랄 것도 없다 — 0원 손익을 지어내지 않는다."""
    rows = [{
        "channel_id": 9, "channel_name": "매출만 있는 채널", "revenue": "500000",
        "product_revenue": "500000", "shipping_revenue": "0", "ad_spend": "0",
        "net_profit": None, "profit_rate": None, "order_count": 0,
    }]
    out = group_summary_by_company(rows, {9: ("회사", "회사 · 채널", True)})
    leaf = _by(out, "leaf", "회사 · 채널")
    assert leaf["net_profit"] is None and leaf["profit_rate"] is None
    assert leaf["net_scope"] is None
