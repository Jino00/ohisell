# test_rocket_1p_revenue.py — 1P 매출 두 축 대조 화면의 데이터 SA (S2)
#
# 이 파일이 지키는 것:
#   ① 소비자 판매가와 우리 매출은 **나란히** 나오고 **절대 더해지지 않는다**(이중계상)
#   ② 납품단가를 못 붙인 옵션도 소비자 매출은 살아 있다 — 우리 매출만 None(0이 아니다)
#   ③ RoAS는 **우리 매출 기준** — 소비자 매출로 내면 못 번 돈으로 광고를 정당화하게 된다
#   ④ 합계는 대시보드와 **같은 함수**에서 온다(재도출 금지)
#   ⑤ 이 모듈은 회계(net_profit)에 결합되지 않는다
#   ⑥ 손익은 **원가 확인분만** 더한다 — 원가 미상을 0으로 넣으면 이익이 부풀어 보인다(2026-08-07)
#
# 값은 2026-08-04 라이브 실측: 소비자 6,536,000 · 우리 3,885,820 · 판매 342.
# 옵션 1위 95752961189(Z폴드8) 소비자 3,024,800 · 판매 152.
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (Channel, CoupangAdOptionDaily, CoupangAdReport,
                        CoupangRocketPurchaseOrderItem, CoupangRocketSalesDaily)
from app.services.coupang import rocket_1p_channel_pnl as pnl
from app.services.coupang.rocket_1p_revenue import compute_rocket_1p_revenue

VENDOR = pnl.ROCKET_1P_VENDOR_ID
ZERO_D = Decimal("0")
D = date(2026, 8, 4)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(Channel(id=5, code="COUPANG_ROCKET", name="쿠팡 로켓배송", platform="coupang",
                  channel_type="consignment", company="주식회사 오하이테크"))
    s.commit()
    yield s
    s.close()


def _sale(s, option_id, sku, qty, consumer, *, d=D, visitors=None):
    s.add(CoupangRocketSalesDaily(
        vendor_id=VENDOR, option_id=option_id, sku_id=sku, date=d,
        qty=qty, revenue=Decimal(consumer), visitors=visitors,
        product_name=f"상품 {option_id}", source="sales_analysis"))


def _price(s, sku, unit_price, seq):
    """발주 라인 = 납품단가의 원천(PO 최신순 1건)."""
    s.add(CoupangRocketPurchaseOrderItem(
        purchase_order_seq=seq, vendor_id=VENDOR, product_number=sku,
        unit_purchase_price=Decimal(unit_price), order_qty=1))


def _ad_account(s, spend, d=D):
    s.add(CoupangAdReport(report_date=d, sell_type="Retail", vendor_id=VENDOR,
                          impressions=0, clicks=0, ad_spend=Decimal(spend),
                          orders=0, sales_qty=0, conversion_revenue=Decimal("0")))


def _ad_option(s, option_id, spend, d=D):
    s.add(CoupangAdOptionDaily(
        report_date=d, vendor_id=VENDOR, sell_type="Retail",
        ad_option_id=option_id, conv_option_id=option_id,
        impressions=0, clicks=0, ad_spend=Decimal(spend),
        orders=0, sales_qty=0, conversion_revenue=Decimal("0")))


# ═══ ① 두 축이 나란히 — 그리고 더해지지 않는다 ═══
def test_two_axes_are_reported_side_by_side(db):
    _sale(db, "95752961189", "76350897", 152, "3024800", visitors=765)
    _price(db, "76350897", "11800", 1)     # 152 × 11,800 = 1,793,600
    db.commit()
    r = compute_rocket_1p_revenue(db, D, D)
    t = r["totals"]
    assert Decimal(t["consumer_revenue"]) == Decimal("3024800")   # 쿠팡가
    assert Decimal(t["our_revenue"]) == Decimal("1793600")        # 납품가
    # ★합계 어디에도 둘을 더한 값이 없다(이중계상 금지).
    total_sum = Decimal(t["consumer_revenue"]) + Decimal(t["our_revenue"])
    assert not any(v is not None and str(v) == str(total_sum) for v in t.values())


def test_our_share_is_our_price_over_consumer_price(db):
    """우리 몫 비율 = 납품가 ÷ 소비자가. 나머지가 쿠팡이 얹은 마진이다."""
    _sale(db, "A", "S1", 10, "1000000")
    _price(db, "S1", "60000", 1)            # 10 × 60,000 = 600,000 → 60%
    db.commit()
    r = compute_rocket_1p_revenue(db, D, D)
    assert Decimal(r["totals"]["our_share"]) == Decimal("0.6000")


# ═══ ② 납품단가 미상 — 소비자 매출은 살고, 우리 매출만 None ═══
def test_option_without_unit_price_keeps_consumer_revenue(db):
    _sale(db, "A", "S1", 10, "500000")      # 단가 있음
    _price(db, "S1", "30000", 1)
    _sale(db, "B", "S2", 5, "300000")       # 단가 없음
    db.commit()
    r = compute_rocket_1p_revenue(db, D, D)
    by_id = {o["option_id"]: o for o in r["options"]}
    assert Decimal(by_id["B"]["consumer_revenue"]) == Decimal("300000")
    # ★0이 아니라 None — 0은 "공짜로 줬다"는 뜻이 된다.
    assert by_id["B"]["our_revenue"] is None
    assert by_id["B"]["roas"] is None
    # 소비자 축은 전량을 센다(손익용 INNER JOIN과 다르다).
    assert r["totals"]["qty"] == 15
    assert Decimal(r["totals"]["consumer_revenue"]) == Decimal("800000")


def test_coverage_surfaces_unpriced_quantity(db):
    _sale(db, "A", "S1", 10, "500000")
    _price(db, "S1", "30000", 1)
    _sale(db, "B", "S2", 5, "300000")
    db.commit()
    cov = compute_rocket_1p_revenue(db, D, D)["coverage"]
    assert cov["qty_all"] == 15 and cov["qty_priced"] == 10
    assert Decimal(cov["priced_pct"]) == Decimal("0.6667")
    assert cov["options_unpriced"] == 1


# ═══ ③ RoAS는 우리 매출 기준 ═══
def test_roas_uses_our_revenue_not_consumer_revenue(db):
    _sale(db, "A", "S1", 10, "1000000")
    _price(db, "S1", "40000", 1)            # 우리 매출 400,000
    _ad_account(db, "100000")
    _ad_option(db, "A", "100000")
    db.commit()
    r = compute_rocket_1p_revenue(db, D, D)
    assert Decimal(r["totals"]["roas"]) == Decimal("4.0000")        # 400,000 / 100,000
    assert Decimal(r["options"][0]["roas"]) == Decimal("4.0000")
    # 소비자 매출로 냈다면 10.0이 됐을 것 — 우리가 못 번 돈이다.
    assert Decimal(r["totals"]["roas"]) != Decimal("10")


# ═══ ④ 합계는 대시보드와 같은 함수에서 온다 ═══
def test_totals_match_dashboard_function_exactly(db):
    _sale(db, "A", "S1", 342, "6536000")
    _price(db, "S1", "11362", 1)
    _ad_account(db, "1358911")
    db.commit()
    r = compute_rocket_1p_revenue(db, D, D)
    row = pnl.compute_rocket_1p_summary_row(db, D, D, pnl.BASIS_SALES)
    # 재도출하지 않았다는 증거 — 문자열까지 같다.
    assert r["totals"]["our_revenue"] == row["revenue"]
    assert r["totals"]["ad_spend"] == row["ad_spend"]


def test_ad_reconciliation_is_exposed_not_hidden(db):
    """옵션 합계(PA)와 계정 총액(전체)은 정의가 달라 어긋난다 — 숨기지 않는다."""
    _sale(db, "A", "S1", 10, "100000")
    _price(db, "S1", "5000", 1)
    _ad_account(db, "50000")
    _ad_option(db, "A", "48000")
    db.commit()
    rec = compute_rocket_1p_revenue(db, D, D)["ad_reconciliation"]
    assert Decimal(rec["option_sum"]) == Decimal("48000")
    assert Decimal(rec["account_total"]) == Decimal("50000")
    assert Decimal(rec["diff"]) == Decimal("-2000")


# ═══ ⑤ 회계 결합 없음 ═══
def test_module_is_not_referenced_by_accounting_paths():
    """소비자 매출이 순이익 경로로 새지 않는지 — 참조 자체를 막는다(D-CPP-2)."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "services"
    offenders = []
    for p in root.rglob("*.py"):
        if p.name == "rocket_1p_revenue.py":
            continue
        if "rocket_1p_revenue" in p.read_text(encoding="utf-8"):
            offenders.append(str(p))
    assert offenders == [], f"회계/서비스 코드가 소비자 매출 모듈을 참조한다: {offenders}"


def test_options_sorted_by_consumer_revenue_desc(db):
    _sale(db, "A", "S1", 1, "100")
    _sale(db, "B", "S2", 1, "900")
    _sale(db, "C", "S3", 1, "500")
    db.commit()
    r = compute_rocket_1p_revenue(db, D, D)
    assert [o["option_id"] for o in r["options"]] == ["B", "C", "A"]


def test_limit_caps_rows_but_count_tells_the_truth(db):
    for i in range(5):
        _sale(db, f"O{i}", f"S{i}", 1, str(100 * (i + 1)))
    db.commit()
    r = compute_rocket_1p_revenue(db, D, D, limit=2)
    assert r["shown"] == 2 and r["option_count"] == 5   # 잘렸다는 사실이 드러난다


# ══════════════════════════════════════════════════════════════════
# 2026-08-06 적대 리뷰 P1 회귀 — 판매분석 미수집 창 · 광고 축 통일
# ══════════════════════════════════════════════════════════════════
def test_uncovered_window_reports_unknown_not_zero(db):
    """★판매분석 롤링창 밖에서 「우리 매출 0원 · RoAS 0.00」을 사실처럼 내면 안 된다.

    실제로는 0이 아니라 **관측 불가**다. 예전 화면은 "광고비 2,941만원 쓰고 매출 0원"으로 읽혔다.
    """
    _ad_account(db, "29412515", d=date(2026, 1, 15))
    db.commit()   # 판매분석 행은 하나도 없다
    r = compute_rocket_1p_revenue(db, date(2026, 1, 1), date(2026, 1, 31))
    t = r["totals"]
    assert t["our_revenue"] is None and t["consumer_revenue"] is None
    assert t["roas"] is None and t["our_share"] is None and t["qty"] is None
    assert r["coverage"]["sales_data_covered"] is False
    # 광고비는 다른 원천이라 그대로 — 그건 실측이다.
    assert Decimal(t["ad_spend"]) == Decimal("29412515")


def test_covered_window_with_zero_sales_is_a_real_zero(db):
    """★판별자는 qty가 아니라 **행 존재**다 — 행이 있고 qty=0이면 그건 진짜 0판매일이다."""
    _sale(db, "A", "S1", 0, "0")
    _price(db, "S1", "10000", 1)
    db.commit()
    r = compute_rocket_1p_revenue(db, D, D)
    assert r["coverage"]["sales_data_covered"] is True
    assert r["totals"]["consumer_revenue"] == "0"      # 모름이 아니라 관측된 0
    assert r["totals"]["qty"] == 0


def test_freshness_is_reported_so_screen_can_say_how_many_days(db):
    """"7일"이라 말하면서 5일을 보여주던 것 — 창의 실제 상태를 싣는다(적대 리뷰 P1)."""
    _sale(db, "A", "S1", 1, "1000")
    db.commit()
    f = compute_rocket_1p_revenue(db, date(2026, 8, 1), date(2026, 8, 7))["freshness"]
    assert f["days_expected"] == 7 and f["days_with_data"] == 1 and f["days_no_data"] == 6
    assert f["data_as_of"] == "2026-08-04"


def test_ad_axis_is_unified_manual_xlsx_is_not_dropped(db):
    """★1P 광고 원천은 둘이다 — `_agg_rocket_ad`가 수기 XLSX를 안 읽어 43,147,487원이 빠졌었다.

    두 엔진이 같은 축을 각자 구현한 것이 원인이라 호출을 한 함수로 모았다(적대 리뷰 P1).
    """
    from sqlalchemy import text as _text

    from app.services.coupang.rocket_intelligence import _agg_rocket_ad

    d = date(2026, 3, 20)
    db.execute(_text("INSERT INTO ad_costs (channel_id, ad_date, ad_spend, source) "
                     "VALUES (5, :d, 763873, 'excel')"), {"d": d.isoformat()})
    db.commit()
    # 자동수집(coupang_ad_report)엔 그날이 없다 — 예전엔 그래서 0원이었다.
    assert _agg_rocket_ad(db, d, d, VENDOR) == Decimal("763873")


# ══════════════════════════════════════════════════════════════════
# 손익 (Jino 2026-08-07: "옆에 원가, 그래서 우리 손익이 얼마인지까지")
#
# 이 묶음이 지키는 것 — 전부 "이익을 부풀리지 않는다"의 다른 얼굴이다:
#   ⓐ 원가 미상 SKU를 **원가 0으로 넣지 않는다**(그러면 그 매출이 통째로 이익이 된다)
#   ⓑ 그래서 손익은 **원가 확인분 부분집합**이고, 화면이 그 사실을 알 수 있게 basis를 낸다
#   ⓒ 옵션 행들의 순이익 합 = 합계 타일 (원 단위까지)
#   ⓓ 분담금을 모르면 손익 자체를 내지 않는다
#   ⓔ 소비자 매출(쿠팡가)은 손익 어디에도 들어가지 않는다
# ══════════════════════════════════════════════════════════════════
from app.services.profit_calculator import payable_vat  # noqa: E402


def _cost(s, sku, cost_price, internal_sku=None):
    """SellC 등록원가 → 상품번호 매핑(confirmed만 원가로 인정된다)."""
    from sqlalchemy import text as _t
    isku = internal_sku or f"OHI-{sku}"
    s.execute(_t("INSERT INTO product_master (internal_sku, product_name, cost_price) "
                 "VALUES (:i, :n, :c)"), {"i": isku, "n": isku, "c": cost_price})
    s.execute(_t("INSERT INTO rocket_product_cost_map (product_number, internal_sku, status) "
                 "VALUES (:p, :i, 'confirmed')"), {"p": str(sku), "i": isku})


def test_net_profit_uses_the_same_formula_as_the_accounting_engine(db):
    """순이익 = 우리 매출 − 원가 − 분담금 − 광고비 − 납부세액. 새 공식을 만들지 않는다."""
    _sale(db, "A", "S1", 10, "1000000")
    _price(db, "S1", "60000", 1)            # 우리 매출 600,000
    _cost(db, "S1", 20000)                  # 원가 200,000
    _ad_option(db, "A", "100000")
    _ad_account(db, "100000")
    db.commit()
    r = compute_rocket_1p_revenue(db, D, D)
    rev, cost, ad = Decimal("600000"), Decimal("200000"), Decimal("100000")
    expected = rev - cost - ZERO_D - ad - payable_vat(rev, cost, ZERO_D, ad)
    assert Decimal(r["pnl"]["net_profit"]) == expected.quantize(Decimal("0.01"))
    assert Decimal(r["options"][0]["cost"]) == cost
    # 이익률은 **우리 매출** 기준이다 — 소비자 매출(1,000,000)로 내면 이익률이 낮게 보인다.
    assert Decimal(r["pnl"]["profit_rate"]) == (expected / rev).quantize(Decimal("0.0001"))


def test_uncosted_sku_is_excluded_not_counted_as_zero_cost(db):
    """★ⓐ 이 테스트가 이 기능의 존재 이유다.

    원가 미상 SKU를 원가 0으로 넣으면 그 매출이 **통째로 이익**이 되어 손익이 부풀어 보인다.
    라이브 2026-08-06엔 우리 매출의 51%가 원가 미상이었다 — 그대로 냈으면 순이익이 두 배로
    보였을 것이다. 대신 매출·원가·광고비를 **같은 부분집합으로 제한**한다.
    """
    _sale(db, "A", "S1", 10, "1000000")
    _price(db, "S1", "60000", 1)
    _cost(db, "S1", 20000)                  # 원가 있음 → 손익 대상
    _sale(db, "B", "S2", 10, "900000")
    _price(db, "S2", "50000", 1)            # 우리 매출은 알지만 원가는 모른다
    _ad_option(db, "A", "100000")
    _ad_option(db, "B", "300000")
    db.commit()
    p = compute_rocket_1p_revenue(db, D, D)["pnl"]
    assert p["basis"] == "costed_subset"
    assert Decimal(p["revenue"]) == Decimal("600000")        # B의 500,000은 안 들어온다
    assert Decimal(p["revenue_priced"]) == Decimal("1100000")
    assert Decimal(p["cost_coverage"]) == Decimal("0.5455")
    # ★B의 광고비도 함께 빠진다 — 매출만 빼고 비용을 남기면 이익이 반대로 과소해진다.
    assert Decimal(p["ad_spend"]) == Decimal("100000")
    # 무엇을 등록하면 채워지는지 이름으로 말한다.
    assert p["uncosted"]["skus"] == 1
    assert p["uncosted"]["top"][0]["sku_id"] == "S2"
    assert Decimal(p["uncosted"]["our_revenue"]) == Decimal("500000")


def test_full_coverage_reports_basis_full(db):
    """커버리지 100%면 부분집합 = 전체 — 그때만 이게 창 전체의 손익이다."""
    _sale(db, "A", "S1", 10, "1000000")
    _price(db, "S1", "60000", 1)
    _cost(db, "S1", 20000)
    db.commit()
    p = compute_rocket_1p_revenue(db, D, D)["pnl"]
    assert p["basis"] == "full" and Decimal(p["cost_coverage"]) == Decimal("1.0000")
    assert p["uncosted"]["skus"] == 0


def test_option_net_profits_sum_to_the_total_tile(db):
    """★ⓒ 표와 타일이 1원이라도 다르면 사용자는 둘 다 안 믿는다.

    VAT가 ÷11이라 순이익엔 무한소수가 붙는다 — 반올림 없이 두면 누적 순서 차이만으로
    끝자리가 어긋난다(라이브 실측 2e-25원). 그래서 행을 전 단위로 못 박고 그 **합**만 낸다.
    """
    for i in range(7):
        _sale(db, f"O{i}", f"S{i}", 3 + i, str(100000 * (i + 1)))
        _price(db, f"S{i}", str(7777 + i), i + 1)
        _cost(db, f"S{i}", 3333 + i)
        _ad_option(db, f"O{i}", str(4321 * (i + 1)))
    db.commit()
    r = compute_rocket_1p_revenue(db, D, D)
    total = sum(Decimal(o["net_profit"]) for o in r["options"] if o["net_profit"] is not None)
    assert str(total) == r["pnl"]["net_profit"]


def test_unknown_promo_burden_blocks_profit_entirely(db):
    """★ⓓ 창에 걸친 프로모션의 할인액을 모르면 **손익을 내지 않는다**(0으로 접지 않는다)."""
    from sqlalchemy import text as _t
    _sale(db, "A", "S1", 10, "1000000")
    _price(db, "S1", "60000", 1)
    _cost(db, "S1", 20000)
    db.execute(_t("INSERT INTO coupang_rocket_promotion (request_id, vendor_id, start_at, end_at) "
                  "VALUES ('686180', :v, '2026-08-01 00:00:00', '2026-08-15 23:59:59')"),
               {"v": VENDOR})   # 할인액(coupang_promo_discount_item) 없음 = 모름
    db.commit()
    r = compute_rocket_1p_revenue(db, D, D)
    assert r["pnl"]["promo_burden_known"] is False
    assert r["pnl"]["net_profit"] is None and r["pnl"]["basis"] is None
    assert r["options"][0]["net_profit"] is None
    # 매출 축은 그대로 살아 있다 — 분담금을 모른다고 매출이 사라지진 않는다.
    assert Decimal(r["totals"]["our_revenue"]) == Decimal("600000")


def test_promo_burden_is_subtracted_per_option(db):
    """분담금은 실측된 아는 비용이다 — 옵션 손익에서도 빠진다."""
    from sqlalchemy import text as _t
    _sale(db, "A", "S1", 10, "1000000")
    _price(db, "S1", "60000", 1)
    _cost(db, "S1", 20000)
    db.execute(_t("INSERT INTO coupang_rocket_promotion (request_id, vendor_id, start_at, end_at) "
                  "VALUES ('686180', :v, '2026-08-01 00:00:00', '2026-08-15 23:59:59')"),
               {"v": VENDOR})
    db.execute(_t("INSERT INTO coupang_promo_discount_item "
                  "(request_id, product_number, discount_type, discount_value) "
                  "VALUES ('686180', 'S1', 'FIXED', 1500)"))
    db.commit()
    r = compute_rocket_1p_revenue(db, D, D)
    assert Decimal(r["options"][0]["promo_burden"]) == Decimal("15000")   # 10개 × 1,500
    assert Decimal(r["pnl"]["promo_burden"]) == Decimal("15000")


def test_consumer_revenue_never_enters_profit(db):
    """★ⓔ 소비자 매출로 손익을 내면 우리가 못 번 돈이 이익이 된다(D-CPP-2)."""
    _sale(db, "A", "S1", 10, "1000000")     # 소비자 축
    _price(db, "S1", "60000", 1)            # 우리 축 600,000
    _cost(db, "S1", 20000)
    db.commit()
    p = compute_rocket_1p_revenue(db, D, D)["pnl"]
    assert Decimal(p["revenue"]) == Decimal("600000")
    # 소비자 매출 기준이었다면 순이익이 이보다 훨씬 컸을 것이다.
    assert Decimal(p["net_profit"]) < Decimal("600000")


def test_uncovered_window_has_no_profit(db):
    """판매분석이 그 창을 안 덮으면 손익도 «모름»이다 — 0원 이익이 아니다."""
    _ad_account(db, "29412515", d=date(2026, 1, 15))
    db.commit()
    p = compute_rocket_1p_revenue(db, date(2026, 1, 1), date(2026, 1, 31))["pnl"]
    assert p["net_profit"] is None and p["cost_coverage"] is None and p["basis"] is None


def test_ad_axis_overlapping_day_is_not_double_counted(db):
    """겹치는 날(2026-05-18)은 두 원천 값이 같다 — 더하면 그 하루가 2배가 된다."""
    from sqlalchemy import text as _text

    from app.services.coupang.rocket_intelligence import _agg_rocket_ad

    d = date(2026, 5, 18)
    db.execute(_text("INSERT INTO ad_costs (channel_id, ad_date, ad_spend, source) "
                     "VALUES (5, :d, 552537, 'excel')"), {"d": d.isoformat()})
    _ad_account(db, "552537", d=d)
    db.commit()
    assert _agg_rocket_ad(db, d, d, VENDOR) == Decimal("552537")   # 1,105,074가 아니다
