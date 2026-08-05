# test_rocket_1p_channel_pnl.py — 로켓배송 1P 채널을 대시보드에 올리는 평행 엔진
#
# 이 파일이 지키는 것(전부 2026-08-05 라이브 실측에서 나온 계약, ref 44):
#   ① 매출 원천 = 세금계산서(payment_amount) — 발주액이 아니다(발주액은 +24.7% 과대)
#   ② 광고비 원천이 둘인데 **겹치는 하루를 두 번 세면 안 된다**(수기 XLSX ↔ 자동수집)
#   ③ 원가 근거가 없으므로 **순이익을 지어내지 않는다**(net None)
#   ④ 라우터는 기존 1P 행을 **갈아끼운다** — append하면 수기 레거시가 이중계상된다
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (Channel, CoupangAdReport, CoupangRocketSalesDaily,
                        CoupangRocketSettlement)
from app.services.coupang import rocket_1p_channel_pnl as svc


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


def _settle(s, seq, issue, pay):
    s.add(CoupangRocketSettlement(
        invoice_seq=seq, vendor_id=svc.ROCKET_1P_VENDOR_ID,
        supply_amount=Decimal(pay) / Decimal("1.1"), vat=Decimal("0"),
        payment_amount=Decimal(pay), issue_date=date.fromisoformat(issue),
        first_payment_amount=Decimal("0"), second_payment_amount=Decimal("0")))


def _report(s, d, spend):
    s.add(CoupangAdReport(report_date=date.fromisoformat(d), sell_type="Retail",
                          vendor_id=svc.ROCKET_1P_VENDOR_ID, impressions=0, clicks=0,
                          ad_spend=Decimal(spend), orders=0, sales_qty=0,
                          conversion_revenue=Decimal("0")))


def _adcost(s, d, spend, channel_id=5):
    s.execute(text("INSERT INTO ad_costs (channel_id, ad_date, ad_spend, source) "
                   "VALUES (:c, :d, :s, 'excel')"),
              {"c": channel_id, "d": d, "s": spend})


# ═══ ① 매출 = 세금계산서 ═══
def test_revenue_comes_from_tax_invoice(db):
    _settle(db, 1, "2026-07-24", 7581450)
    _settle(db, 2, "2026-07-25", 2048128)
    db.commit()
    row = svc.compute_rocket_1p_summary_row(db, date(2026, 7, 1), date(2026, 7, 31))
    assert Decimal(row["revenue"]) == Decimal("9629578")
    assert Decimal(row["product_revenue"]) == Decimal("9629578")  # 1P는 수수료·배송비 없음
    assert Decimal(row["shipping_revenue"]) == Decimal("0")


def test_negative_reverse_issue_invoice_is_kept(db):
    """월말 역발행 차감(음수)도 그대로 더한다 — 임의 제외가 원장에서 더 멀다."""
    _settle(db, 1, "2026-07-24", 7581450)
    _settle(db, 2, "2026-07-31", -830500)        # 라이브 실측 30573973
    db.commit()
    row = svc.compute_rocket_1p_summary_row(db, date(2026, 7, 1), date(2026, 7, 31))
    assert Decimal(row["revenue"]) == Decimal("6750950")


def test_window_is_by_issue_date(db):
    _settle(db, 1, "2026-06-30", 1000)
    _settle(db, 2, "2026-07-01", 2000)
    _settle(db, 3, "2026-08-01", 4000)
    db.commit()
    row = svc.compute_rocket_1p_summary_row(db, date(2026, 7, 1), date(2026, 7, 31))
    assert Decimal(row["revenue"]) == Decimal("2000")


# ═══ ② 광고비 두 원천 — 겹치는 날을 두 번 세지 않는다 ═══
def test_ad_sources_union_without_double_counting_overlap_day(db):
    """★라이브 실측: 2026-05-18 하루만 겹치고 두 값이 552,537로 **같다**.

    더하면 그날이 2배가 되고, 한쪽을 버리면 나머지 시대가 0원이 된다.
    """
    _adcost(db, "2026-05-17", 625020)            # 수기만
    _adcost(db, "2026-05-18", 552537)            # 겹침
    _report(db, "2026-05-18", 552537)            # 겹침(같은 값)
    _report(db, "2026-05-24", 1040375)           # 자동만
    db.commit()
    by_day = svc._ad_spend_by_day(db, date(2026, 5, 1), date(2026, 5, 31))
    assert by_day["2026-05-18"] == Decimal("552537")     # 1,105,074가 아니다
    assert svc._ad_spend_sum(db, date(2026, 5, 1), date(2026, 5, 31)) == Decimal(
        625020 + 552537 + 1040375)


def test_automated_source_wins_when_values_disagree(db):
    """두 원천이 어긋나면 자동수집(report/SALES)이 이긴다 — 수기는 폴백일 뿐."""
    _adcost(db, "2026-05-18", 999999)
    _report(db, "2026-05-18", 552537)
    db.commit()
    assert svc._ad_spend_by_day(db, date(2026, 5, 18), date(2026, 5, 18)) == {
        "2026-05-18": Decimal("552537")}


def test_other_channels_ad_costs_are_not_picked_up(db):
    """ad_costs는 채널 그레인 — 다른 채널(네이버 등) 광고비를 1P로 끌어오면 안 된다."""
    _adcost(db, "2026-05-17", 111111, channel_id=6)
    db.commit()
    assert svc._ad_spend_sum(db, date(2026, 5, 1), date(2026, 5, 31)) == Decimal("0")


# ═══ ③ 순이익을 지어내지 않는다 ═══
def test_net_profit_is_none_because_cost_basis_is_absent(db):
    """원가는 발주 라인이 2026-06부터만 있고, 있는 것도 31.7%가 오매핑이다(ref 44 §4).

    숫자를 내면 그건 지어낸 것이다 — 기존 '측정불가 → —' 계약을 그대로 쓴다.
    """
    _settle(db, 1, "2026-07-24", 7581450)
    db.commit()
    row = svc.compute_rocket_1p_summary_row(db, date(2026, 7, 1), date(2026, 7, 31))
    assert row["net_profit"] is None
    assert row["profit_rate"] is None
    assert Decimal(row["cost"]) == 0 and Decimal(row["commission"]) == 0


def test_no_row_when_window_is_empty(db):
    """매출도 광고비도 없으면 빈 행을 만들지 않는다."""
    assert svc.compute_rocket_1p_summary_row(db, date(2026, 1, 1), date(2026, 1, 31)) is None


def test_missing_channel_disables_feature_quietly(db):
    db.query(Channel).delete()
    db.commit()
    assert svc.compute_rocket_1p_summary_row(db, date(2026, 7, 1), date(2026, 7, 31)) is None
    assert svc.compute_rocket_1p_daily_points(db, date(2026, 7, 1), date(2026, 7, 31)) == []


# ═══ 일자 추이 — 요약과 합계가 어긋나면 안 된다 ═══
def test_daily_points_sum_matches_summary_row(db):
    _settle(db, 1, "2026-07-24", 7581450)
    _settle(db, 2, "2026-07-31", -830500)
    _adcost(db, "2026-07-02", 400000)
    _report(db, "2026-07-24", 728232)
    db.commit()
    row = svc.compute_rocket_1p_summary_row(db, date(2026, 7, 1), date(2026, 7, 31))
    pts = svc.compute_rocket_1p_daily_points(db, date(2026, 7, 1), date(2026, 7, 31))
    assert sum(Decimal(p["revenue"]) for p in pts) == Decimal(row["revenue"])
    assert sum(Decimal(p["ad_spend"]) for p in pts) == Decimal(row["ad_spend"])
    assert all(p["net_profit"] is None for p in pts)
    assert all(p["channel_id"] == 5 for p in pts)


def test_ad_only_day_still_produces_a_point(db):
    """매출 없는 날의 광고비가 사라지면 요약 RoAS와 추이 RoAS가 어긋난다."""
    _report(db, "2026-07-02", 478847)
    db.commit()
    pts = svc.compute_rocket_1p_daily_points(db, date(2026, 7, 1), date(2026, 7, 31))
    assert len(pts) == 1
    assert pts[0]["date"] == "2026-07-02"
    assert Decimal(pts[0]["revenue"]) == 0
    assert Decimal(pts[0]["ad_spend"]) == Decimal("478847")


# ═══ ④ 매출 축 전환 (sell-in ↔ sell-through) ═══
# 라이브 실측 2026-08-04: 계산서 1,578,000 vs 판매(납품가) 3,885,820 — 두 배 넘게 다르다.
# 두 축은 **택일**이다. 더하면 같은 물건을 납품·판매 두 번 세는 이중계상.
def _sales(s, d, sku, qty, gmv, option_id=None):
    s.add(CoupangRocketSalesDaily(
        vendor_id=svc.ROCKET_1P_VENDOR_ID, option_id=option_id or f"opt{sku}",
        sku_id=str(sku), date=date.fromisoformat(d), qty=qty, revenue=Decimal(gmv)))


def _po_line(s, po_seq, sku, unit_price, qty=1):
    s.execute(text(
        "INSERT INTO coupang_rocket_purchase_order_item "
        "(purchase_order_seq, vendor_id, line_no, product_number, order_qty, "
        " unit_purchase_price, line_order_amount, line_supply_amount, line_vat) "
        "VALUES (:po, :v, 1, :sku, :q, :u, :amt, :sup, :vat)"),
        {"po": po_seq, "v": svc.ROCKET_1P_VENDOR_ID, "sku": str(sku), "q": qty,
         "u": unit_price, "amt": unit_price * qty,
         "sup": unit_price * qty / 1.1, "vat": unit_price * qty / 11})


def _create_promo_discount_table(s):
    """★`coupang_promo_discount_item`은 main의 models.py에 **없다**.

    D-CPP-10(제안서 엑셀 → 개당 할인액)은 브랜치 `claude/d-cpp-10-promo-file-ingest`에만 있고
    prod엔 마이그레이션 `c8e1a4b7d201`로 배포돼 있다 — **prod에는 있고 main에는 없는** 상태다.
    그래서 서비스는 테이블 존재를 확인하고 없으면 분담금을 **모름(None)**으로 둔다.
    이 헬퍼는 "있는 경우"를 재현한다(그 브랜치가 병합되면 이 헬퍼를 지우면 된다).
    """
    s.execute(text(
        "CREATE TABLE IF NOT EXISTS coupang_promo_discount_item ("
        " id INTEGER PRIMARY KEY, request_id VARCHAR(30) NOT NULL,"
        " product_number VARCHAR(30) NOT NULL, discount_type VARCHAR(10) NOT NULL,"
        " discount_value NUMERIC(12,4) NOT NULL)"))


def _master_cost(s, internal_sku, cost, product_number):
    s.execute(text("INSERT INTO product_master (internal_sku, product_name, cost_price) "
                   "VALUES (:i, :n, :c)"), {"i": internal_sku, "n": internal_sku, "c": cost})
    s.execute(text("INSERT INTO rocket_product_cost_map (product_number, internal_sku, status) "
                   "VALUES (:p, :i, 'confirmed')"), {"p": str(product_number), "i": internal_sku})


def test_basis_switch_changes_revenue(db):
    """★같은 날, 같은 채널인데 축에 따라 매출이 다르다 — 이게 이 기능의 존재 이유."""
    _settle(db, 1, "2026-08-04", 1578000)          # 계산서 축
    _po_line(db, 900, 111, 11362)                  # 납품단가
    _sales(db, "2026-08-04", 111, 342, 6536000)    # 판매 축(소비자가 GMV는 별개)
    db.commit()
    d1, d2 = date(2026, 8, 4), date(2026, 8, 4)
    a = svc.compute_rocket_1p_summary_row(db, d1, d2, svc.BASIS_SETTLEMENT)
    b = svc.compute_rocket_1p_summary_row(db, d1, d2, svc.BASIS_SALES)
    assert Decimal(a["revenue"]) == Decimal("1578000")
    assert Decimal(b["revenue"]) == Decimal(342) * Decimal("11362")   # 판매수량 × 납품단가
    assert a["revenue_basis"] == "settlement" and b["revenue_basis"] == "sales"


def test_sales_basis_uses_supply_price_not_consumer_gmv(db):
    """소비자가(GMV)는 쿠팡이 소비자에게 받은 돈이지 **우리 매출이 아니다**."""
    _po_line(db, 900, 111, 10000)
    _sales(db, "2026-08-04", 111, 10, 65360)       # GMV 65,360 ≠ 우리 매출
    db.commit()
    row = svc.compute_rocket_1p_summary_row(db, date(2026, 8, 4), date(2026, 8, 4), "sales")
    assert Decimal(row["revenue"]) == Decimal("100000")


def test_unknown_basis_falls_back_to_settlement(db):
    """오타 하나로 숫자가 두 배 바뀌면 안 된다 — 모르는 축은 추측하지 않고 정본으로."""
    assert svc.normalize_basis("selll") == svc.BASIS_SETTLEMENT
    assert svc.normalize_basis(None) == svc.BASIS_SETTLEMENT
    assert svc.normalize_basis("SALES") == svc.BASIS_SALES


def test_net_profit_suppressed_when_cost_coverage_is_low(db):
    """★원가가 일부에만 붙으면 순이익을 내지 않는다 — 27%짜리 표본에 마진을 곱하면 창작이다."""
    _po_line(db, 900, 111, 10000)
    _po_line(db, 901, 222, 10000)
    _master_cost(db, "OHI-A", 3000, 111)           # 111만 원가 있음
    _sales(db, "2026-08-04", 111, 10, 1)
    _sales(db, "2026-08-04", 222, 90, 1)           # 매출의 90%가 원가 미상
    db.commit()
    row = svc.compute_rocket_1p_summary_row(db, date(2026, 8, 4), date(2026, 8, 4), "sales")
    assert row["net_profit"] is None
    assert Decimal(row["cost_coverage"]) == Decimal("0.1000")


def test_net_profit_suppressed_when_promo_source_is_absent(db):
    """★분담금 원천이 없으면 0으로 접지 않고 **모름**으로 두고 순이익을 내지 않는다.

    0으로 접으면 "분담금이 없었다"가 되어 이익이 부풀어 보인다 — 원가 커버리지와 같은 원칙.
    """
    _po_line(db, 900, 111, 10000)
    _master_cost(db, "OHI-A", 3000, 111)
    _sales(db, "2026-08-04", 111, 10, 1)
    db.commit()
    row = svc.compute_rocket_1p_summary_row(db, date(2026, 8, 4), date(2026, 8, 4), "sales")
    assert Decimal(row["cost_coverage"]) == Decimal("1.0000")   # 원가는 다 붙었는데도
    assert row["promo_burden"] is None
    assert row["net_profit"] is None


def test_net_profit_emitted_when_cost_coverage_is_full(db):
    """커버리지가 임계 이상이면 스마트스토어와 같은 공식으로 순이익을 낸다."""
    _create_promo_discount_table(db)
    _po_line(db, 900, 111, 10000)
    _master_cost(db, "OHI-A", 3000, 111)
    _sales(db, "2026-08-04", 111, 10, 1)
    _report(db, "2026-08-04", 5000)
    db.commit()
    row = svc.compute_rocket_1p_summary_row(db, date(2026, 8, 4), date(2026, 8, 4), "sales")
    assert Decimal(row["cost_coverage"]) == Decimal("1.0000")
    rev, cost, ad = Decimal("100000"), Decimal("30000"), Decimal("5000")
    from app.services.profit_calculator import payable_vat
    expected = rev - cost - ad - payable_vat(rev, cost, Decimal("0"), ad)
    assert Decimal(row["net_profit"]) == expected
    assert Decimal(row["order_count"]) == 10      # 판매 축의 order_count = 판매수량


def test_promo_burden_is_subtracted_on_sales_basis(db):
    """분담금은 제안서에서 실측된 **아는 비용**이다 — 빼지 않으면 이익이 부풀어 보인다."""
    _po_line(db, 900, 111, 10000)
    _master_cost(db, "OHI-A", 3000, 111)
    _sales(db, "2026-08-04", 111, 10, 1)
    _create_promo_discount_table(db)
    db.execute(text("INSERT INTO coupang_rocket_promotion "
                    "(request_id, vendor_id, start_at, end_at) "
                    "VALUES ('686180', :v, '2026-08-01 00:00:00', '2026-08-15 23:59:59')"),
               {"v": svc.ROCKET_1P_VENDOR_ID})
    db.execute(text("INSERT INTO coupang_promo_discount_item "
                    "(request_id, product_number, discount_type, discount_value) "
                    "VALUES ('686180', '111', 'FIXED', 3000)"))
    db.commit()
    row = svc.compute_rocket_1p_summary_row(db, date(2026, 8, 4), date(2026, 8, 4), "sales")
    assert Decimal(row["promo_burden"]) == Decimal("30000")   # 10개 × 3,000


def test_promo_burden_ignores_out_of_window_promotions(db):
    _po_line(db, 900, 111, 10000)
    _sales(db, "2026-08-04", 111, 10, 1)
    _create_promo_discount_table(db)
    db.execute(text("INSERT INTO coupang_rocket_promotion "
                    "(request_id, vendor_id, start_at, end_at) "
                    "VALUES ('685840', :v, '2026-07-22 00:00:00', '2026-07-23 23:59:59')"),
               {"v": svc.ROCKET_1P_VENDOR_ID})
    db.execute(text("INSERT INTO coupang_promo_discount_item "
                    "(request_id, product_number, discount_type, discount_value) "
                    "VALUES ('685840', '111', 'FIXED', 4000)"))
    db.commit()
    row = svc.compute_rocket_1p_summary_row(db, date(2026, 8, 4), date(2026, 8, 4), "sales")
    assert Decimal(row["promo_burden"]) == Decimal("0")   # 원천은 있고 해당 기간이 아닐 뿐


def test_settlement_basis_is_untouched_by_sales_data(db):
    """★합격기준 ④ — 축을 추가해도 계산서 축 값은 한 원도 안 바뀐다."""
    _settle(db, 1, "2026-08-04", 1578000)
    _po_line(db, 900, 111, 10000)
    _sales(db, "2026-08-04", 111, 999, 1)
    db.commit()
    row = svc.compute_rocket_1p_summary_row(db, date(2026, 8, 4), date(2026, 8, 4))
    assert Decimal(row["revenue"]) == Decimal("1578000")
    assert row["net_profit"] is None and row["cost_coverage"] is None


def test_daily_points_follow_the_basis(db):
    _settle(db, 1, "2026-08-04", 1578000)
    _po_line(db, 900, 111, 10000)
    _sales(db, "2026-08-02", 111, 5, 1)
    db.commit()
    a = svc.compute_rocket_1p_daily_points(db, date(2026, 8, 1), date(2026, 8, 4))
    b = svc.compute_rocket_1p_daily_points(db, date(2026, 8, 1), date(2026, 8, 4), "sales")
    assert [(p["date"], Decimal(p["revenue"])) for p in a] == [("2026-08-04", Decimal("1578000"))]
    assert [(p["date"], Decimal(p["revenue"])) for p in b] == [("2026-08-02", Decimal("50000"))]
