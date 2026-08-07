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


# ══════════════════════════════════════════════════════════════════
# 2026-08-07 적대 리뷰 3렌즈 P1 회귀
#
# 셋 다 "돈이 화면에서 사라지거나, 화면이 사실과 다른 것을 단언한다"의 다른 얼굴이다.
# ══════════════════════════════════════════════════════════════════
def test_ad_of_options_with_no_sales_is_not_swallowed(db):
    """★그 창에 안 팔린 옵션의 광고비가 손익에서 통째로 사라지던 것.

    라이브 실측 2026-08-01~07: 광고 옵션 418개 중 **295개가 판매행 없음**, 282,794원.
    예전 판은 판매행을 돌면서만 광고비를 소비해서 이 돈이 한 번도 안 들어갔고,
    커버리지 100%(basis='full', 화면 배지 「기간 전체」)에서도 영영 안 들어왔다.
    재현: 실제 −181,818원이 **+272,727원으로 부호가 뒤집혔다.**
    """
    _sale(db, "A", "S1", 10, "1000000")
    _price(db, "S1", "60000", 1)
    _cost(db, "S1", 20000)             # 커버리지 100% → basis='full'
    _ad_option(db, "A", "100000")
    _ad_option(db, "GHOST", "500000")  # 광고만 돌고 그 창에 판매 0
    db.commit()
    p = compute_rocket_1p_revenue(db, D, D)["pnl"]
    assert p["basis"] == "full"
    # 금액이 화면에서 사라지지 않는다.
    assert Decimal(p["ad_no_sales"]) == Decimal("500000")
    assert p["ad_no_sales_included"] is True          # full이면 차감된다
    assert Decimal(p["ad_spend"]) == Decimal("600000")
    # 부호가 뒤집히지 않는다 — 600,000−200,000−600,000−VAT
    assert Decimal(p["net_profit"]) < ZERO_D
    expected = Decimal("600000") - Decimal("200000") - Decimal("600000") \
        - payable_vat(Decimal("600000"), Decimal("200000"), ZERO_D, Decimal("600000"))
    assert Decimal(p["net_profit"]) == expected.quantize(Decimal("0.01"))


def test_no_sales_ad_is_disclosed_even_when_not_included(db):
    """부분집합일 땐 귀속할 판매가 없어 안 섞지만, **금액은 반드시 싣는다** — 안 보이면 없는 돈이 된다."""
    _sale(db, "A", "S1", 10, "1000000")
    _price(db, "S1", "60000", 1)
    _cost(db, "S1", 20000)
    _sale(db, "B", "S2", 10, "900000")
    _price(db, "S2", "50000", 1)       # 원가 미상 → costed_subset
    _ad_option(db, "GHOST", "300000")
    db.commit()
    p = compute_rocket_1p_revenue(db, D, D)["pnl"]
    assert p["basis"] == "costed_subset"
    assert p["ad_no_sales_included"] is False
    assert Decimal(p["ad_no_sales"]) == Decimal("300000")   # 숨기지 않는다


def test_cost_coverage_is_not_poisoned_by_unknown_promo_burden(db):
    """★분담금을 모른다고 **원가 커버리지가 0%로 뜨면** 안 된다.

    라이브 재현(2026-02-01~08-07): 화면 0.0000 vs 실제 0.7581. 원가는 다 등록돼 있는데
    배지가 「원가 확인 0.0%분만」을 사실처럼 냈고, 사용자를 **엉뚱한 작업**으로 보냈다.
    커버리지는 원가에 대한 사실이지 분담금 수집 상태에 대한 사실이 아니다.
    """
    from sqlalchemy import text as _t
    _sale(db, "A", "S1", 10, "1000000")
    _price(db, "S1", "60000", 1)
    _cost(db, "S1", 20000)             # 원가 커버리지는 100%
    db.execute(_t("INSERT INTO coupang_rocket_promotion (request_id, vendor_id, start_at, end_at) "
                  "VALUES ('686180', :v, '2026-08-01 00:00:00', '2026-08-15 23:59:59')"),
               {"v": VENDOR})           # 할인액 없음 → 분담금 «모름»
    db.commit()
    p = compute_rocket_1p_revenue(db, D, D)["pnl"]
    assert p["promo_burden_known"] is False
    assert p["net_profit"] is None                       # 손익은 안 낸다(그건 맞다)
    assert Decimal(p["cost_coverage"]) == Decimal("1.0000")   # 0.0000이 아니다
    # 사유도 원가가 아니라 분담금을 가리켜야 한다.
    assert p["blocked"]["code"] == "promo_burden_unknown"


def test_basis_full_is_structural_not_rounded(db):
    """★0.99996이 1.0000으로 반올림돼 「기간 전체」가 되던 것 — 판정을 구조로 한다.

    그때 화면엔 「원가 확인 100% · 기간 전체」와 「원가 미등록 SKU 1개」가 **동시에** 떴다.
    """
    _sale(db, "A", "S1", 100000, "200000000")
    _price(db, "S1", "1000", 1)        # 우리 매출 100,000,000
    _cost(db, "S1", 300)
    _sale(db, "B", "S2", 1, "2000")
    _price(db, "S2", "1000", 2)        # 우리 매출 1,000 — 원가 미상
    db.commit()
    p = compute_rocket_1p_revenue(db, D, D)["pnl"]
    assert Decimal(p["cost_coverage"]) == Decimal("1.0000")   # 반올림되면 1.0000이 된다
    assert p["basis"] == "costed_subset"                      # 그래도 full이 아니다
    assert p["uncosted"]["actionable_skus"] == 1


def test_confirmed_loss_is_not_drawn_as_unknown(db):
    """★원가를 몰라도 **부호가 확정**인 경우가 있다 — 광고비가 우리 매출을 넘으면 적자다.

    라이브에 그런 행이 있었다(광고비 73,593원 > 우리 매출 48,000원). 순이익 "—"로 떠서,
    적자가 빨강으로 칠해지는 표에서 «문제 없음»으로 읽혔다(은폐형 결함).
    """
    _sale(db, "A", "S1", 4, "80000")
    _price(db, "S1", "12000", 1)       # 우리 매출 48,000 · 원가 미등록
    _ad_option(db, "A", "73593")
    db.commit()
    r = compute_rocket_1p_revenue(db, D, D)
    o = r["options"][0]
    assert o["net_profit"] is None                      # 정확한 값은 여전히 모른다
    assert Decimal(o["net_profit_upper"]) < ZERO_D      # 그러나 적자는 확정이다
    assert r["pnl"]["uncosted"]["loss_confirmed_skus"] == 1
    assert r["pnl"]["uncosted"]["top"][0]["loss_confirmed"] is True


def test_profitable_uncosted_option_gets_no_false_bound(db):
    """상한이 양수면 싣지 않는다 — «≤ +X»는 이익으로 오독된다(모르는 건 모르는 거다)."""
    _sale(db, "A", "S1", 10, "1000000")
    _price(db, "S1", "60000", 1)
    _ad_option(db, "A", "10000")       # 광고비가 작다 → 상한이 양수
    db.commit()
    o = compute_rocket_1p_revenue(db, D, D)["options"][0]
    assert o["net_profit"] is None and o["net_profit_upper"] is None


def test_ignored_skus_are_not_put_on_the_worklist(db):
    """★`ignored`는 "원가 제외로 **이미 결정한**" SKU다 — 등록하라고 시키면 안 된다.

    라이브: 8월에 팔린 SKU 중 11개가 ignored. 목록에 넣으면 "등록하면 100% 된다"가
    지킬 수 없는 약속이 되고, 사용자는 이미 안 하기로 한 것을 다시 쫓는다.
    """
    from sqlalchemy import text as _t
    _sale(db, "A", "S1", 10, "500000")
    _price(db, "S1", "30000", 1)
    _sale(db, "B", "S2", 5, "300000")
    _price(db, "S2", "20000", 2)
    db.execute(_t("INSERT INTO rocket_product_cost_map (product_number, internal_sku, status) "
                  "VALUES ('S2', 'OHI-SAMPLE', 'ignored')"))
    db.commit()
    u = compute_rocket_1p_revenue(db, D, D)["pnl"]["uncosted"]
    assert u["skus"] == 2                       # 둘 다 원가는 없다
    assert u["actionable_skus"] == 1            # 그러나 시킬 건 하나뿐
    assert u["excluded_skus"] == 1
    assert [t["sku_id"] for t in u["top"]] == ["S1"]


def test_uncosted_total_does_not_fold_unknown_into_zero(db):
    """단가까지 모르는 SKU를 0원으로 더하면 합계가 **작게** 보인다 — 그건 이 파일의 원칙 위반이다."""
    _sale(db, "A", "S1", 10, "500000")
    _price(db, "S1", "30000", 1)       # 우리 매출 300,000 · 원가 미상
    _sale(db, "B", "S2", 5, "300000")  # 단가도 원가도 미상
    db.commit()
    u = compute_rocket_1p_revenue(db, D, D)["pnl"]["uncosted"]
    assert Decimal(u["our_revenue"]) == Decimal("300000")   # 0을 더하지 않았다
    assert u["our_revenue_partial"] is True                 # 그리고 그 사실을 말한다
    assert u["top"][0]["sku_id"] == "S1"                    # known을 먼저 — 축을 섞지 않는다


def test_blocked_reason_points_at_the_real_blocker(db):
    """★사유를 backend가 정한다 — 우선순위가 틀리면 사용자를 엉뚱한 작업으로 보낸다."""
    _sale(db, "A", "S1", 10, "500000")   # 납품단가 자체가 없다(원가 문제가 아니다)
    db.commit()
    p = compute_rocket_1p_revenue(db, D, D)["pnl"]
    assert p["blocked"]["code"] == "no_priced_sku"
    assert "원가가 아니라" in p["blocked"]["reason"]


def test_fine_grain_promo_burden_totals_match_the_accounting_engine(db):
    """★화면이 쓰는 「날짜×옵션」 분담금이 회계 엔진의 일별 분담금과 **같은 돈**을 세는지.

    예전엔 이 SQL을 문자열 replace로 파생했고, 원본이 한 글자만 바뀌면 조용히 빗나가
    분담금이 전 옵션 0이 되면서 경고도 안 떴다(라이브 7일 1,926,000원이 사라지는 크기).
    문자열이 같은지 보는 것보다 **두 경로의 합이 같은지** 보는 쪽이 강한 보증이다.
    """
    from sqlalchemy import text as _t
    for i, (sku, price, disc) in enumerate([("S1", 10000, 1500), ("S2", 20000, 700)], 1):
        _price(db, sku, str(price), i)
        _sale(db, f"O{i}a", sku, 3, "100000", d=date(2026, 8, 4))
        _sale(db, f"O{i}b", sku, 2, "80000", d=date(2026, 8, 5))
        db.execute(_t("INSERT INTO coupang_promo_discount_item "
                      "(request_id, product_number, discount_type, discount_value) "
                      f"VALUES ('686180', '{sku}', 'FIXED', {disc})"))
    db.execute(_t("INSERT INTO coupang_rocket_promotion (request_id, vendor_id, start_at, end_at) "
                  "VALUES ('686180', :v, '2026-08-01 00:00:00', '2026-08-15 23:59:59')"),
               {"v": VENDOR})
    db.commit()
    a, b = date(2026, 8, 4), date(2026, 8, 5)
    fine = pnl.promo_burden_by_day_option(db, a, b, VENDOR)
    coarse = pnl._promo_burden_by_day(db, a, b, VENDOR)
    assert fine is not None and coarse is not None
    assert sum(fine.values()) == sum(coarse.values()) == Decimal("11000")
    # 날짜로 접어도 회계 엔진의 일별 값과 하루씩 같다.
    rolled: dict[str, Decimal] = {}
    for (d_, _o), amt in fine.items():
        rolled[d_] = rolled.get(d_, ZERO_D) + amt
    assert rolled == coarse
    # 옵션으로 접은 것도 같은 원자에서 나온다(따로 계산하지 않는다).
    assert sum(pnl.promo_burden_by_option(db, a, b, VENDOR).values()) == Decimal("11000")


def test_promo_burden_guard_uses_the_same_vendor_as_the_query(db):
    """가드만 1P로 하드코딩하면 다른 vendor에서 «모름»이 «분담금 0»으로 둔갑한다."""
    from sqlalchemy import text as _t
    db.execute(_t("INSERT INTO coupang_rocket_promotion (request_id, vendor_id, start_at, end_at) "
                  "VALUES ('999', 'OTHERVENDOR', '2026-08-01 00:00:00', '2026-08-15 23:59:59')"))
    db.commit()
    # OTHERVENDOR엔 할인액 원천이 없다 → None(모름)이어야 한다. {}(=0)이면 안 된다.
    assert pnl.promo_burden_by_option(db, D, D, "OTHERVENDOR") is None


def test_uncosted_reason_separates_missing_link_from_missing_cost(db):
    """★★원가가 안 붙는 이유가 셋인데 할 일이 다 다르다 (2026-08-07 실사고).

    Jino가 화면이 시키는 대로 **원가를 등록했는데 아무것도 안 움직였다.** 그 SKU들은
    원가가 없는 게 아니라 **쿠팡 상품번호 ↔ 내부 SKU 연결이 없었다**(라이브 178건).
    원가는 `product_master`(내부 SKU 그레인)에 붙고 판매는 쿠팡 상품번호로 들어오므로,
    다리가 없으면 원가를 아무리 넣어도 안 붙는다. 화면이 "원가를 등록하세요" 하나로
    뭉뚱그리면 사용자가 헛일을 한다.
    """
    from sqlalchemy import text as _t
    # ⓐ 연결 없음 — 원가를 등록해도 안 붙는다
    _sale(db, "A", "S1", 10, "500000")
    _price(db, "S1", "30000", 1)
    # ⓑ 연결은 있는데 그 내부 SKU에 원가가 없다
    #    (`product_master.cost_price`가 NOT NULL이라, 실제로는 매핑이 **없는 내부 SKU를
    #     가리키는** 형태로 나타난다 — 마스터에서 지워졌거나 오타로 들어간 경우.)
    _sale(db, "B", "S2", 10, "400000")
    _price(db, "S2", "20000", 2)
    db.execute(_t("INSERT INTO rocket_product_cost_map (product_number, internal_sku, status) "
                  "VALUES ('S2', 'OHI-DANGLING', 'confirmed')"))
    # ⓒ 제외로 이미 결정 — 아무것도 하면 안 된다
    _sale(db, "C", "S3", 10, "300000")
    _price(db, "S3", "10000", 3)
    db.execute(_t("INSERT INTO rocket_product_cost_map (product_number, internal_sku, status) "
                  "VALUES ('S3', 'OHI-SAMPLE', 'ignored')"))
    db.commit()
    u = compute_rocket_1p_revenue(db, D, D)["pnl"]["uncosted"]
    assert u["link_missing_skus"] == 1
    assert u["cost_missing_skus"] == 1
    assert u["excluded_skus"] == 1
    assert u["actionable_skus"] == 2          # 제외는 시키지 않는다
    by_sku = {t["sku_id"]: t["reason"] for t in u["top"]}
    assert by_sku == {"S1": "no_link", "S2": "no_cost"}


# ══════════════════════════════════════════════════════════════════
# 일별 손익 (Jino 2026-08-07: "나는 … 우리의 일일 손익을 보고 싶은거야")
# ══════════════════════════════════════════════════════════════════
def test_daily_and_option_sums_and_tile_all_agree(db):
    """★★세 숫자가 어긋나면 사용자는 셋 다 안 믿는다.

    일별 표·옵션별 표·기간 타일이 **같은 「날짜×옵션」 원자**를 각각 다른 방향으로 접은
    것이라 반올림까지 정확히 같아야 한다. 각 그레인에서 따로 반올림하면 어긋난다
    (VAT가 ÷11이라 순이익엔 무한소수가 붙는다).
    """
    for i in range(1, 4):
        sku = f"S{i}"
        _price(db, sku, str(7000 + i * 111), i)
        _cost(db, sku, 2000 + i * 37)
        for dnum, day in enumerate((date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5))):
            _sale(db, f"O{i}", sku, 3 + i + dnum, str(90000 + i * 1000 + dnum * 700), d=day)
            _ad_option(db, f"O{i}", str(3300 + i * 101 + dnum * 17), d=day)
    db.commit()
    r = compute_rocket_1p_revenue(db, date(2026, 8, 3), date(2026, 8, 5))
    tile = r["pnl"]
    day_sum = sum(Decimal(d["net_profit"]) for d in r["daily"] if d["net_profit"] is not None)
    opt_sum = sum(Decimal(o["net_profit"]) for o in r["options"] if o["net_profit"] is not None)
    assert str(day_sum) == str(opt_sum) == tile["net_profit"]
    # 매출·원가·광고비도 같은 원자에서 나온다.
    assert sum(Decimal(d["cost"]) for d in r["daily"]) == Decimal(tile["cost"])
    assert sum(Decimal(d["ad_spend"]) for d in r["daily"]) == Decimal(tile["ad_spend"])
    assert sum(d["qty"] for d in r["daily"]) == r["totals"]["qty"]


def test_daily_rows_are_one_per_observed_day_not_per_requested_day(db):
    """★없는 날을 0으로 만들지 않는다 — 판매분석이 안 준 날과 0판매일은 다르다."""
    _price(db, "S1", "10000", 1)
    _cost(db, "S1", 3000)
    _sale(db, "A", "S1", 5, "90000", d=date(2026, 8, 3))
    _sale(db, "A", "S1", 4, "70000", d=date(2026, 8, 6))
    db.commit()
    r = compute_rocket_1p_revenue(db, date(2026, 8, 1), date(2026, 8, 7))
    assert [d["date"] for d in r["daily"]] == ["2026-08-03", "2026-08-06"]
    # 화면이 "요청 7일 중 2일치"라고 말할 수 있게 freshness가 함께 온다.
    assert r["freshness"]["days_expected"] == 7 and r["freshness"]["days_with_data"] == 2


def test_daily_coverage_is_per_day_not_the_window_average(db):
    """★날마다 팔린 SKU가 달라 커버리지가 들쭉날쭉하다 — 그걸 안 보이면 이익률이 왜 튀는지 모른다."""
    _price(db, "S1", "10000", 1)
    _cost(db, "S1", 3000)          # 원가 있음
    _price(db, "S2", "10000", 2)   # 원가 없음
    _sale(db, "A", "S1", 10, "200000", d=date(2026, 8, 3))   # 그날은 100%
    _sale(db, "B", "S2", 10, "200000", d=date(2026, 8, 4))   # 그날은 0%
    db.commit()
    by_day = {d["date"]: d for d in
              compute_rocket_1p_revenue(db, date(2026, 8, 3), date(2026, 8, 4))["daily"]}
    assert Decimal(by_day["2026-08-03"]["cost_coverage"]) == Decimal("1.0000")
    assert Decimal(by_day["2026-08-04"]["cost_coverage"]) == Decimal("0.0000")
    # 커버리지 0인 날은 손익을 내지 않는다(0원 이익이 아니다).
    assert by_day["2026-08-04"]["net_profit"] is None
    assert by_day["2026-08-04"]["cost"] is None
    # 그래도 매출 축은 살아 있다 — 그날 안 팔린 게 아니다.
    assert Decimal(by_day["2026-08-04"]["our_revenue"]) == Decimal("100000")


def test_daily_pnl_revenue_is_the_costed_subset_not_all_revenue(db):
    """일별 이익률의 분모는 **원가 확인분**이다 — 전량 매출로 나누면 이익률이 낮게 보인다."""
    _price(db, "S1", "10000", 1)
    _cost(db, "S1", 3000)
    _price(db, "S2", "10000", 2)          # 원가 없음
    _sale(db, "A", "S1", 10, "200000", d=date(2026, 8, 3))
    _sale(db, "B", "S2", 10, "200000", d=date(2026, 8, 3))
    db.commit()
    d0 = compute_rocket_1p_revenue(db, date(2026, 8, 3), date(2026, 8, 3))["daily"][0]
    assert Decimal(d0["our_revenue"]) == Decimal("200000")     # 전량
    assert Decimal(d0["pnl_revenue"]) == Decimal("100000")     # 손익 축은 절반
    assert Decimal(d0["profit_rate"]) == (Decimal(d0["net_profit"]) / Decimal("100000")
                                          ).quantize(Decimal("0.0001"))


def test_daily_row_arithmetic_closes(db):
    """화면 한 줄이 스스로 맞아야 한다: 매출−원가−분담금−광고비−VAT = 순이익."""
    _price(db, "S1", "12345", 1)
    _cost(db, "S1", 4567)
    _sale(db, "A", "S1", 7, "180000", d=date(2026, 8, 3))
    _ad_option(db, "A", "23456", d=date(2026, 8, 3))
    db.commit()
    d0 = compute_rocket_1p_revenue(db, date(2026, 8, 3), date(2026, 8, 3))["daily"][0]
    lhs = (Decimal(d0["pnl_revenue"]) - Decimal(d0["cost"]) - Decimal(d0["promo_burden"])
           - Decimal(d0["ad_spend"]) - Decimal(d0["vat"]))
    assert lhs == Decimal(d0["net_profit"])


def test_daily_row_closes_when_costed_and_uncosted_are_mixed_on_the_same_day(db):
    """★★같은 날에 원가 있는 옵션과 없는 옵션이 **섞였을 때** 행 산술이 맞는가.

    라이브에서 부가세가 **음수**(−454,262원)로 떴던 결함의 회귀. 원인은 축 혼합이었다:
    원가·분담금·광고비는 그날 **전량**을 더하고 손익기준 매출·순이익은 **원가 확인분**만
    이라, 잔차로 낸 부가세가 그 차이를 통째로 흡수했다.
    ★기존 테스트 40건이 못 잡은 이유: 전부 «그날 전부 원가 있음» 또는 «전부 없음»이라
      두 축이 우연히 같았다. 섞인 날이 없으면 이 결함은 보이지 않는다.
    """
    _price(db, "S1", "10000", 1)
    _cost(db, "S1", 3000)                       # 원가 있음 → 손익에 들어간다
    _price(db, "S2", "10000", 2)                # 원가 없음 → 손익 밖
    _sale(db, "A", "S1", 10, "200000", d=date(2026, 8, 3))
    _sale(db, "B", "S2", 10, "200000", d=date(2026, 8, 3))
    _ad_option(db, "A", "20000", d=date(2026, 8, 3))
    _ad_option(db, "B", "50000", d=date(2026, 8, 3))   # ★손익 밖 옵션의 광고비
    db.commit()
    d0 = compute_rocket_1p_revenue(db, date(2026, 8, 3), date(2026, 8, 3))["daily"][0]
    # 행이 스스로 맞는다.
    lhs = (Decimal(d0["pnl_revenue"]) - Decimal(d0["cost"]) - Decimal(d0["promo_burden"])
           - Decimal(d0["ad_spend"]) - Decimal(d0["vat"]))
    assert lhs == Decimal(d0["net_profit"])
    # 부가세는 양수다(매출VAT − 매입세액공제).
    assert Decimal(d0["vat"]) > ZERO_D
    # 손익 축 광고비는 **부분집합**이고, 그날 전량은 따로 실린다 — 둘 다 보인다.
    assert Decimal(d0["ad_spend"]) == Decimal("20000")
    assert Decimal(d0["ad_spend_all"]) == Decimal("70000")
    assert d0["pnl_qty"] == 10 and d0["qty"] == 20


# ══════════════════════════════════════════════════════════════════
# 신규 미연결 SKU 표면화 (Jino 2026-08-07)
#
# 왜: 미연결 목록이 매출 순이라 **옛 꼬리**와 **이번에 나온 신제품**이 섞여 있었다.
# 실측 — 06-18 매핑 이후 새로 들어온 상품번호는 단 3개인데 그 3개(Z폴드8)가
# 미연결 매출의 56%였다. 새 폰이 나올 때마다 반복되므로 나오자마자 보여야 한다.
# ══════════════════════════════════════════════════════════════════
def _po(s, seq, sku, created_at, unit_price="10000"):
    """발주 헤더+라인. `po_created_at`이 "상품이 생긴 날"의 원천이다."""
    from sqlalchemy import text as _t
    s.execute(_t("INSERT INTO coupang_rocket_purchase_order "
                 "(purchase_order_seq, vendor_id, sum_of_order_amount, sum_of_receiving_amount, "
                 " sum_of_vendor_confirmed_amount, order_qty, receiving_qty, vendor_confirmed_qty, "
                 " sku_count, po_created_at) "
                 "VALUES (:s, :v, 0, 0, 0, 0, 0, 0, 1, :c)"),
              {"s": seq, "v": VENDOR, "c": created_at})
    _price(s, sku, unit_price, seq)


def test_new_unlinked_sku_is_flagged_and_sorted_first(db):
    """★신규가 매출 순에 묻히지 않는다 — 목록 맨 앞에 오고 개수도 따로 센다."""
    # 옛 꼬리: 발주도 판매도 창 이전부터
    _po(db, 1, "OLD", "2025-08-01")
    _sale(db, "A", "OLD", 50, "900000", d=date(2026, 8, 3))
    _sale(db, "A", "OLD", 10, "180000", d=date(2026, 6, 5))     # 관측 시작 근처
    # 신제품: 발주가 창 안
    _po(db, 2, "NEW", "2026-08-02")
    _sale(db, "B", "NEW", 5, "90000", d=date(2026, 8, 3))
    db.commit()
    u = compute_rocket_1p_revenue(db, date(2026, 8, 1), date(2026, 8, 5))["pnl"]["uncosted"]
    assert u["new_skus"] == 1
    by_sku = {t["sku_id"]: t for t in u["top"]}
    assert by_sku["NEW"]["is_new"] is True
    assert by_sku["OLD"]["is_new"] is False
    # ★매출은 OLD가 5배지만 신규가 먼저 온다 — 급한 순서가 다르다.
    assert u["top"][0]["sku_id"] == "NEW"
    assert Decimal(u["new_our_revenue"]) == Decimal("50000")   # 5 × 10,000


def test_newness_is_not_asserted_when_observation_window_bounds_it(db):
    """★판매분석은 롤링(약 2개월)이라 **관측 시작일 = 첫 판매일**이면 그 전은 모른다.

    그때 "이번에 새로 팔리기 시작"이라고 단정하면 옛 상품이 신규로 둔갑한다.
    발주 이력이 없어 반증도 못 하는 자리라, 0/1로 접지 말고 «모른다»를 드러낸다.
    """
    # 발주 이력 없음 + 판매 첫 관측일이 곧 관측 시작일
    _price(db, "S1", "10000", 99)          # 단가만 있고 PO 헤더는 없다
    _sale(db, "A", "S1", 5, "90000", d=date(2026, 8, 1))
    db.commit()
    t = compute_rocket_1p_revenue(db, date(2026, 8, 1), date(2026, 8, 5))["pnl"]["uncosted"]["top"][0]
    assert t["first_sold_at"] == "2026-08-01"
    assert t["first_sold_at_bounded"] is True      # 그 전은 관측 자체가 없다
    assert t["is_new"] is False                    # → 단정하지 않는다


def test_po_history_beats_the_rolling_window_for_newness(db):
    """발주 이력은 전 범위라 롤링 한계를 받지 않는다 — 그쪽이 창 안이면 확실한 신규다."""
    _po(db, 1, "S1", "2026-08-02")
    _sale(db, "A", "S1", 5, "90000", d=date(2026, 8, 1))   # 판매 첫 관측 = 관측 시작일
    db.commit()
    t = compute_rocket_1p_revenue(db, date(2026, 8, 1), date(2026, 8, 5))["pnl"]["uncosted"]["top"][0]
    assert t["first_sold_at_bounded"] is True   # 판매 축만 보면 모른다
    assert t["first_po_at"] == "2026-08-02"
    assert t["is_new"] is True                  # 그래도 발주가 창 안이면 신규다


def test_old_product_that_starts_selling_now_is_not_called_new(db):
    """★"안 팔리던 게 이제 팔린다"와 "새로 나왔다"는 다르다 — 후자만 신규다.

    실측에 둘 다 있었다(아이폰17용은 2025-08 발주인데 07-08부터 판매).
    """
    _po(db, 1, "S1", "2025-08-26")                        # 상품은 작년부터 있었다
    _sale(db, "A", "S1", 5, "90000", d=date(2026, 8, 3))  # 그런데 이제 팔리기 시작
    _sale(db, "A", "S1", 1, "18000", d=date(2026, 7, 20))
    db.commit()
    t = compute_rocket_1p_revenue(db, date(2026, 8, 1), date(2026, 8, 5))["pnl"]["uncosted"]["top"][0]
    assert t["is_new"] is False and t["first_po_at"] == "2025-08-26"
