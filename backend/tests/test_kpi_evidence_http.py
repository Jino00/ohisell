# test_kpi_evidence_http.py — 근거가 «카드와 같은 숫자»인지를 경계에서 잰다
# (계약 CONTRACT_kpi_evidence_page.md §2-1·§3 금지선 1·§4 A7 · 적대 리뷰 1R P2-1)
#
# 왜 이 파일이 따로 있나: 초판의 `checks` 3종은 **같은 payload를 자기 자신과** 비교할 뿐이었다.
# 즉 화면의 「✓ 카드와 일치」는 **카드를 한 번도 본 적이 없었다.** 실제로 적대 리뷰가
#   ①라우터가 `_kpi_totals` 대신 두 번째 계산 경로를 신설하는 변이
#   ②근거 엔드포인트가 `rocket_basis`를 무시하는 변이
# 를 주입했는데 **둘 다 전건 초록으로 생존**했다 — 계약의 핵심 금지선에 집행층이 0이었다.
# 여기서는 같은 창으로 `/kpi`와 `/kpi/evidence`를 **둘 다 호출해** 4값을 원 단위로 맞춘다.
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.routers import dashboard as dash

ROCKET_ID = 5

# 축(rocket_basis)에 따라 로켓1P 행이 갈리는 실제 구조를 그대로 흉내낸다 —
# 계산서 축은 손익을 못 재고(하한), 판매 축은 원가가 붙어 손익이 난다.
_COMMON = [
    {"channel_id": 6, "channel_name": "네이버", "revenue": "1000000",
     "product_revenue": "950000", "shipping_revenue": "50000",
     "cost": "400000", "commission": "100000", "ad_spend": "50000",
     "shipping": "30000", "fixed_cost": "20000",
     "payable_vat": "36363.63636363636363636363636",
     "unmapped_revenue": "0",
     "net_profit": "363636.3636363636363636363636", "order_count": 40},
]
_ROCKET = {
    "settlement": {
        "channel_id": ROCKET_ID, "channel_name": "쿠팡 로켓배송", "revenue": "1578000",
        "product_revenue": "1578000", "shipping_revenue": "0",
        # ★★실제 producer가 내는 모양 그대로다(리뷰 2R P1-1 — 초판 픽스처가 여기서 갈라졌다).
        #   `compute_rocket_1p_summary_row`는 원가를 못 믿는 분기에서 `cost`를 **0으로 싣고**,
        #   `net_profit`은 **None으로 두지 않는다** — 하한(−광고비)을 «자기가 만들어» 넣는다:
        #       elif ad_spend > ZERO: net, scope = -ad_spend, NET_SCOPE_AD_ONLY
        #   초판 픽스처는 `net_profit: None`이라 **producer가 한 번도 만들지 않는 모양**이었고,
        #   그래서 로켓1P의 「원가 0원」 거짓말을 통과시켰다.
        "cost": "0", "commission": "0", "ad_spend": "597888", "shipping": "0",
        "net_profit": "-597888", "net_scope": "ad_only", "net_basis_revenue": "0",
        "unmapped_revenue": "1578000", "order_count": 0, "revenue_basis": "settlement",
    },
    "sales": {
        "channel_id": ROCKET_ID, "channel_name": "쿠팡 로켓배송", "revenue": "3885820",
        "product_revenue": "3885820", "shipping_revenue": "0",
        "cost": "2100000", "commission": "0", "ad_spend": "597888", "shipping": "0",
        "promo_burden": "12000",
        "net_profit": "1069029.09", "net_scope": "full", "net_basis_revenue": "3885820",
        "unmapped_revenue": "0", "order_count": 311, "revenue_basis": "sales",
    },
}
_CMAP = {
    6: ("오하이", "오하이 · 네이버 스마트스토어", True),
    ROCKET_ID: ("오하이테크", "오하이테크 · 쿠팡 로켓배송(1P)", False),
}


class _Ch:
    id = ROCKET_ID


@pytest.fixture
def client(monkeypatch):
    def rows(db, ad_db, df, dt, basis):
        return [dict(r) for r in _COMMON] + [dict(_ROCKET[basis])]

    monkeypatch.setattr(dash, "_channel_rows", rows)
    monkeypatch.setattr(dash, "get_channel_company_map", lambda db: _CMAP)
    monkeypatch.setattr(dash, "rocket_1p_channel", lambda db: _Ch())
    monkeypatch.setattr(dash, "_resolve_ad_db", lambda: None)
    monkeypatch.setattr(dash, "_sync_orders_recent", lambda db: None)
    monkeypatch.setattr(dash, "_sync_ad_costs_for_period", lambda db, a, b: None)

    app = FastAPI()
    app.include_router(dash.router)
    app.dependency_overrides[get_db] = lambda: None
    return TestClient(app)


_WINDOW = "date_from=2026-08-22&date_to=2026-08-22"


def _both(client, basis: str):
    q = f"{_WINDOW}&rocket_basis={basis}"
    kpi = client.get(f"/api/dashboard/kpi?{q}")
    ev = client.get(f"/api/dashboard/kpi/evidence?{q}")
    assert kpi.status_code == 200, kpi.text
    assert ev.status_code == 200, ev.text
    return kpi.json(), ev.json()


@pytest.mark.parametrize("basis", ["settlement", "sales"])
def test_evidence_totals_equal_the_card_over_http(client, basis):
    """근거 합계 ↔ 카드 4값이 **원 단위로** 같다. 갈라지면 근거가 아니라 두 번째 숫자다."""
    kpi, ev = _both(client, basis)
    assert Decimal(ev["totals"]["revenue"]) == Decimal(kpi["total_revenue"])
    assert Decimal(ev["totals"]["net_profit"]) == Decimal(kpi["net_profit"])
    assert Decimal(ev["totals"]["profit_rate"]) == Decimal(kpi["profit_rate"])
    assert ev["totals"]["order_count"] == kpi["order_count"]
    assert Decimal(ev["totals"]["floor_ad"]) == Decimal(kpi["net_floor_ad"])


@pytest.mark.parametrize("basis", ["settlement", "sales"])
def test_profit_rate_badge_is_true_against_the_real_card(client, basis):
    """★이익률 배지가 «카드를 실제로 본» 뒤에 참인가 (2026-08-23 — 네 칸 중 이 칸만 없었다).

    서비스층 테스트는 `build_kpi_evidence`에 totals를 손으로 넘기지만, 여기선 라우터가
    카드와 **같은 두 줄**로 만든 그 totals가 쓰인다. 두 엔드포인트의 이익률이 원 단위로
    같은데 배지가 ✗면, 그건 판정식이 카드와 다른 눈금을 쓰고 있다는 뜻이다.
    """
    kpi, ev = _both(client, basis)
    assert Decimal(ev["totals"]["profit_rate"]) == Decimal(kpi["profit_rate"])
    assert ev["checks"]["profit_rate_matches"] is True, (
        "카드와 값이 같은데 화면은 「불일치」라고 말한다 — 배지가 카드가 아닌 걸 보고 있다"
    )


def test_profit_rate_badge_goes_false_when_a_second_path_appears(client, monkeypatch):
    """★계약 §3 금지선 1 — 근거가 카드와 «다른 경로»로 분모를 만들면 화면이 ✗를 찍어야 한다.

    초판 `checks`가 죽이지 못한 변이가 정확히 이것이다(같은 payload끼리 비교하니 언제나 ✓).
    여기선 라우터가 쓰는 `_kpi_totals`를 **두 번째 계산 경로**로 갈아치운다 — 행은 그대로인데
    카드 쪽 분모만 달라진 날의 모양이다.
    """
    real = dash._kpi_totals

    def second_path(db, rows, rocket_ch_id):
        out = real(db, rows, rocket_ch_id)
        return dict(out, basis_revenue=out["basis_revenue"] + Decimal("250000"))

    monkeypatch.setattr(dash, "_kpi_totals", second_path)
    _, ev = _both(client, "settlement")
    assert ev["checks"]["profit_rate_matches"] is False, (
        "분모가 행 합과 갈라졌는데 ✓ — 「카드와 일치」가 아무것도 안 보고 있다"
    )


def test_evidence_honours_rocket_basis(client):
    """축을 무시하면 근거가 카드와 «다른 창»을 말한다 — 두 축의 값이 실제로 갈려야 한다."""
    _, settle = _both(client, "settlement")
    _, sales = _both(client, "sales")
    assert settle["rocket_basis"] == "settlement"
    assert sales["rocket_basis"] == "sales"
    assert Decimal(settle["totals"]["revenue"]) != Decimal(sales["totals"]["revenue"])
    # 그리고 각 축은 그 축의 카드와 맞아야 한다(위 테스트가 축별로 이미 검사한다).


def test_rocket_settlement_cost_is_unknown_not_zero(client):
    """★리뷰 1R P1-1 — producer가 «0»으로 싣는 원가가 화면에 「0원」으로 뜨면 안 된다.

    이 축은 스스로 「원가 축이 안 닿는다」고 선언한 축이다. 0으로 그리면 「원가 0원」 거짓말이고,
    그건 이 저장소가 순이익을 부풀려 보이게 했던 바로 그 결함 모양이다.
    """
    _, ev = _both(client, "settlement")
    rocket = next(r for r in ev["rows"] if r["channel_id"] == ROCKET_ID)
    assert rocket["deductions"]["cost"] is None, "「모른다」가 「0원」으로 굳었다"
    assert "cost" in rocket["missing"]
    # 광고비는 확정 비용이라 **여전히 사실**이다 — 통째로 «모른다»로 만들면 안 된다.
    assert Decimal(rocket["deductions"]["ad_spend"]) == Decimal("597888")


def test_unknown_cost_does_not_flip_with_ad_spend(client, monkeypatch):
    """★리뷰 2R P1-1 — 같은 「원가 미상」이 **광고비 유무로 갈리면 안 된다.**

    초판 신호(`net_profit is None`)는 광고비가 0일 때만 참이었다. producer는 광고비가 있으면
    하한을 자기가 만들어 `net_profit`을 채우므로, **광고를 돌린 날만 「원가 0원」 거짓말**이 떴다 —
    하필 라이브의 상시 조건이 그쪽이다.
    """
    from app.services.kpi_evidence import build_row_evidence

    base = dict(_ROCKET["settlement"])
    with_ad = build_row_evidence(base, ROCKET_ID)
    # 광고비 0 → producer는 net_profit을 None으로 둔다(하한이랄 것도 없다)
    no_ad = build_row_evidence(
        dict(base, ad_spend="0", net_profit=None, net_scope=None), ROCKET_ID
    )
    assert with_ad["deductions"]["cost"] is None
    assert no_ad["deductions"]["cost"] is None, "광고비 유무로 「모른다」가 갈렸다"


def test_ordinary_channel_with_zero_cost_is_not_overwritten():
    """평범한 주문 축 행은 `net_scope` 키가 **아예 없다** — 그 행까지 «모른다»로 덮으면 안 된다."""
    from app.services.kpi_evidence import build_row_evidence

    row = {
        "channel_id": 9, "revenue": "1000", "cost": "0", "commission": "0",
        "ad_spend": "0", "shipping": "0", "fixed_cost": "0",
        "payable_vat": "90.909090909090909090909091",
        "net_profit": "909.090909090909090909090909", "order_count": 1,
    }
    ev = build_row_evidence(row, ROCKET_ID)
    assert ev["deductions"]["cost"] == "0", "손익을 온전히 잰 행의 원가 0은 사실이다"


def test_rocket_sales_cost_is_a_fact(client):
    """반대로 원가가 실측인 축에서는 값을 그대로 보인다 — 무조건 «모른다»로 덮으면 안 된다."""
    _, ev = _both(client, "sales")
    rocket = next(r for r in ev["rows"] if r["channel_id"] == ROCKET_ID)
    assert Decimal(rocket["deductions"]["cost"]) == Decimal("2100000")
    assert "cost" not in rocket["missing"]


def test_response_keeps_every_evidence_field_over_http(client):
    """★response_model이 칸을 지우면 화면 자백이 **조용히** 사라진다(교훈 #321).

    개별 키를 나열하는 대신 **집합**을 못 박는다 — 그래야 「이 칸도 지웠는데 아무도 안 잡네」가
    안 생긴다(리뷰 1R에서 `has_floor`·`deduction_unknown_rows`·`order_count_excluded`·`missing`
    삭제 변이 4종이 전부 생존했다).
    """
    _, ev = _both(client, "settlement")
    assert set(ev) == {
        "date_from", "date_to", "rocket_basis", "rows", "deduction_keys",
        "deduction_totals", "deduction_unknown_rows", "totals", "checks",
        "order_count_excluded", "has_floor",
    }
    assert set(ev["rows"][0]) == {
        "channel_id", "channel_name", "company", "label", "revenue",
        "product_revenue", "shipping_revenue", "deductions", "missing",
        "net_profit", "net_scope", "net_floor_ad", "net_basis_revenue",
        "unmapped_revenue", "residual", "explains_net", "order_count",
        "counted_in_order_card", "revenue_basis",
    }
    assert set(ev["totals"]) == {
        "revenue", "net_profit", "basis_revenue", "floor_ad", "profit_rate",
        "order_count", "residual", "unmeasured_revenue",
    }
    # ★검산 칸도 집합으로 못 박는다 — 배지 하나를 응답에서 지우면 화면은 조용히 «없는 칸»을
    #   읽고, 그 탭의 ✓/✗가 통째로 사라진다(교훈 #321의 검산판).
    assert set(ev["checks"]) == {
        "revenue_matches", "net_matches", "order_count_matches", "net_fully_explained",
        "profit_rate_matches",
    }
    assert ev["has_floor"] is True, "하한이 섞였는데 배너 근거가 안 실렸다"


def test_net_fully_explained_is_false_when_a_residual_remains(client):
    """상수 True로 굳으면 「전부 설명됐다」는 거짓말이 된다(리뷰 1R P2-4 — 변이 생존)."""
    _, ev = _both(client, "settlement")
    # 계산서 축 로켓 행은 매출은 있는데 손익을 못 재므로 총계 잔차가 남는다.
    assert Decimal(ev["totals"]["residual"]) != Decimal("0")
    assert ev["checks"]["net_fully_explained"] is False
    # 그래도 **카드와는 일치한다** — 이 둘은 다른 질문이다(P1-3).
    assert ev["checks"]["net_matches"] is True


def test_three_p_ad_merge_keeps_payable_vat_in_step(monkeypatch):
    """★리뷰 1R P1-4 — 3P 행에 쿠팡 광고비를 얹을 때 `payable_vat`도 같이 줄어야 한다.

    안 그러면 근거 화면이 「이 행이 실제로 뺀 부가세」라며 **광고비 매입세액만큼 큰 값**을 보이고,
    그 차액이 잔차로 떠서 「설명 못 한 돈」에 틀린 이름이 붙는다.

    ★**`_merge_rg_summary`를 실제로 부른다**(리뷰 2R 지적): 초판은 라우터의 세 줄을 테스트 안에
      다시 적어 검증해서, **라우터에서 그 줄을 지워도 안 죽는 장식**이었다. 자기가 베낀 코드를
      자기가 검사하는 테스트는 아무것도 안 지킨다.
    """
    ad_p3 = Decimal("120000")

    class _Ch3P:
        id = 9
        code = "COUPANG_WING1"
        name = "오픽스 3P"

    class _Q:
        def filter(self, *a, **k):
            return self

        def all(self):
            return [_Ch3P()]

    class _DB:
        def query(self, *a, **k):
            return _Q()

    monkeypatch.setattr(dash, "_WING_ACCOUNTS", ("COUPANG_WING1",))
    monkeypatch.setenv("COUPANG_WING1_VENDOR_ID", "A00000000")
    monkeypatch.setattr(
        dash, "split_wing_ad_spend",
        lambda db, df, dt, account_key, vendor_id: {"p3": ad_p3, "rg": Decimal("0")},
    )
    # RG 행 자체는 이 테스트의 관심사가 아니다 — 3P 행에 광고비를 얹는 경로만 본다.
    monkeypatch.setattr(dash, "compute_rg_summary_row", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.coupang.intelligence._cost_master", lambda db: {}
    )

    row = {
        "channel_id": 9, "channel_name": "오픽스 3P",
        "revenue": "1000000", "product_revenue": "1000000", "shipping_revenue": "0",
        "cost": "400000", "commission": "100000",
        "ad_spend": "0", "shipping": "0", "fixed_cost": "0",
        "payable_vat": "45454.54545454545454545454545",
        "net_profit": "454545.4545454545454545454545",
        "unmapped_revenue": "0", "order_count": 10,
    }
    merged = dash._merge_rg_summary(_DB(), [row], date(2026, 8, 22), date(2026, 8, 22))
    row_3p = next(r for r in merged if r["channel_id"] == 9)

    assert Decimal(row_3p["ad_spend"]) == ad_p3, "광고비가 안 얹혔다 — 테스트 전제가 깨졌다"

    from app.services.kpi_evidence import build_row_evidence

    ev = build_row_evidence(row_3p, None)
    assert ev["residual"] == "0.00", "부가세 칸이 스테일이면 잔차가 광고비 매입세액만큼 뜬다"
    assert ev["explains_net"] is True
    assert "payable_vat" not in ev["missing"]
