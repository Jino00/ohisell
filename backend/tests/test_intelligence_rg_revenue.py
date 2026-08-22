# test_intelligence_rg_revenue.py — 종합조망 RG 매출 축 (계약 ⓑ / D-CPP-49)
# 머니코드 fixture(인메모리 SQLite). 라이브 API 없음.
#
# ★2026-08-22 축 전환: 종합조망 RG 매출의 원천이 **gross 주문 원장(CoupangRgOrderItem)** →
#   **콘솔 net 옵션축(CoupangVendorItemSalesDaily, RFM)**으로 바뀌었다(계약 ⓑ).
#   왜: 대시보드 RG 행이 이미 콘솔 net 위에 서 있어서, 여기만 gross면 같은 화면이 RG 매출을
#   두 값으로 말한다(오픽스 30일 gross +11.8% 과대 — ref 89).
#   이 파일은 **바뀐 값**만 검사하지 않는다. 바뀌면서 생길 수 있는 사고 셋을 같이 검사한다:
#     ① gross 원장이 매출로 다시 새어 들어오지 않는가
#     ② 옵션축이 창을 못 덮을 때 화면이 **침묵하지 않는가**(0원과 미상이 같아 보이면 안 된다)
#     ③ 종합조망 RG 매출 == 대시보드 RG 행 매출 (계약 ⓑ 불변식 자체)
# D-3: net_profit = 3P_net + (RG_rev − RG_cost − rg_total)  [RG 정산 전액차감, D-16 일관]
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    Channel, CoupangProductItem, CoupangReturnItem, CoupangRgOrderItem,
    CoupangRgSettlementFee, CoupangVendorItemSalesDaily, CoupangVendorSummaryDaily, Order,
)
from app.services.coupang.intelligence import compute_command_center

_Z = Decimal(0)
WIN = (date(2026, 6, 1), date(2026, 6, 30))
OD = datetime(2026, 6, 5, 12, 0, 0)
SD = date(2026, 6, 5)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _ch(db, cid, code, company):
    db.add(Channel(id=cid, name=f"c{cid}", code=code, platform="coupang", company=company))


def _product(db, vid, account_key, vendor_id, supply=None):
    db.add(CoupangProductItem(
        vendor_item_id=vid, account_key=account_key, vendor_id=vendor_id,
        seller_product_id=f"SP_{vid}", item_name=f"opt{vid}", sale_price=_Z,
        supply_price=(Decimal(str(supply)) if supply is not None else None)))


def _rg_order(db, account_key, vendor_id, vid, unit, qty, oid):
    """gross 주문 원장 — **더 이상 종합조망 매출의 원천이 아니다**(진단값 revenue_rg_gross 전용)."""
    db.add(CoupangRgOrderItem(
        order_id=oid, vendor_item_id=vid, account_key=account_key, vendor_id=vendor_id,
        sales_quantity=qty, unit_sales_price=Decimal(str(unit)), paid_at=OD))


def _rg_net(db, account_key, vid, gmv, units, orders=1, sale_date=SD, reg="RFM"):
    """콘솔 net 옵션축 — 이게 종합조망 RG 매출의 원천이다(계약 ⓑ)."""
    db.add(CoupangVendorItemSalesDaily(
        sale_date=sale_date, account_key=account_key, vendor_item_id=vid,
        registration_type=reg, item_name=f"opt{vid}", gmv=gmv,
        units_sold=units, total_orders=orders))


def _rg_net_summary(db, account_key, gmv, units, sale_date=SD, reg="RFM"):
    """콘솔 net 요약축 — 대시보드 RG 행이 읽는 축. 커버리지 판정의 «분모 계정 목록»도 여기서 온다."""
    db.add(CoupangVendorSummaryDaily(
        summary_date=sale_date, account_key=account_key,
        registration_type=reg, gmv=gmv, units_sold=units))


def _three_p(db, channel_id, oid, vid, price):
    db.add(Order(channel_id=channel_id, order_number=oid, platform_product_id=vid,
                 quantity=1, selling_price=Decimal(str(price)), order_date=OD, status="delivered"))


def _rg_fee(db, account_key, fee_type, amount):
    db.add(CoupangRgSettlementFee(
        account_key=account_key, recognition_date_from=date(2026, 6, 1),
        recognition_date_to=date(2026, 6, 7), fee_type=fee_type, vendor_item_id="",
        amount=Decimal(str(amount))))


def _cc(db, acc=None, win=WIN):
    return compute_command_center(db, win[0], win[1], acc)["account"]["summary"]


def test_rg_revenue_merged_into_total(db):
    """RG 매출(콘솔 net)이 summary 매출에 편입되고 revenue_rg/revenue_3p로 분해된다."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _product(db, "V1", "COUPANG_WING1", "A01564720")
    _rg_net(db, "COUPANG_WING1", "V1", 10000, 2)
    db.commit()
    s = _cc(db)
    assert s["revenue"] == Decimal("10000")
    assert s["revenue_rg"] == Decimal("10000")
    assert s["revenue_3p"] == _Z
    assert s["revenue_rg_basis"] == "console_net"


# ─────────────────────────────────────────────────────────────
# ★① gross 누수 — 이 테스트가 축 전환의 «본체»다
# ─────────────────────────────────────────────────────────────
def test_gross_ledger_is_not_revenue(db):
    """gross 주문 원장만 있고 옵션축이 없으면 RG 매출은 **0이다** — gross가 매출로 새면 안 된다.

    ★이 0은 「RG를 안 팔았다」가 아니라 「콘솔 net을 아직 못 받았다」는 뜻이고, 그래서 이 테스트는
      0만 보지 않고 **자백 필드가 그 사실을 말하는지**까지 본다. 자백 없이 0을 내는 것이
      이 변경의 가장 위험한 실패 모양이다(교훈 #123 — 「0건」과 「측정 안 됨」).
    """
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _product(db, "V1", "COUPANG_WING1", "A01564720")
    _rg_order(db, "COUPANG_WING1", "A01564720", "V1", 5000, 2, "RG1")  # gross 10,000
    db.commit()
    s = _cc(db, "COUPANG_WING1")
    assert s["revenue_rg"] == _Z                          # 매출로 안 샌다
    assert s["revenue_rg_gross"] == Decimal("10000")      # 진단값으로는 보인다
    assert s["rg_option_axis_complete"] is False          # 화면이 실토한다
    assert s["rg_option_axis_days"] == "0/30"


def test_gross_and_net_diverge_net_wins(db):
    """같은 창에 gross 10,000 · 콘솔 net 8,800(취소분 반영)이면 매출은 **8,800**이다."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _product(db, "V1", "COUPANG_WING1", "A01564720")
    _rg_order(db, "COUPANG_WING1", "A01564720", "V1", 5000, 2, "RG1")
    _rg_net(db, "COUPANG_WING1", "V1", 8800, 1)
    db.commit()
    s = _cc(db, "COUPANG_WING1")
    assert s["revenue_rg"] == Decimal("8800")
    assert s["revenue_rg_gross"] == Decimal("10000")


def test_non_rfm_option_rows_are_not_rg_revenue(db):
    """옵션축의 NORMAL(3P) 행은 RG 매출이 아니다 — registration_type 필터가 살아 있는가."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _product(db, "V1", "COUPANG_WING1", "A01564720")
    _rg_net(db, "COUPANG_WING1", "V1", 77000, 3, reg="NORMAL")
    db.commit()
    s = _cc(db, "COUPANG_WING1")
    assert s["revenue_rg"] == _Z


# ─────────────────────────────────────────────────────────────
# ★② 커버리지 자백
# ─────────────────────────────────────────────────────────────
def test_partial_option_axis_coverage_is_confessed(db):
    """창 3일 중 2일만 옵션축이 있으면 「2/3 · 미완」이라고 말한다."""
    win = (date(2026, 6, 4), date(2026, 6, 6))
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _product(db, "V1", "COUPANG_WING1", "A01564720")
    _rg_net(db, "COUPANG_WING1", "V1", 5000, 1, sale_date=date(2026, 6, 4))
    _rg_net(db, "COUPANG_WING1", "V1", 6000, 1, sale_date=date(2026, 6, 5))
    db.commit()
    s = _cc(db, "COUPANG_WING1", win)
    assert s["revenue_rg"] == Decimal("11000")
    assert s["rg_option_axis_days"] == "2/3"
    assert s["rg_option_axis_complete"] is False


def test_full_option_axis_coverage_is_complete(db):
    """창 전체가 덮이면 complete=True — 자백 장치가 «항상 미완»으로 굳지 않는지."""
    win = (date(2026, 6, 4), date(2026, 6, 5))
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _product(db, "V1", "COUPANG_WING1", "A01564720")
    _rg_net(db, "COUPANG_WING1", "V1", 5000, 1, sale_date=date(2026, 6, 4))
    _rg_net(db, "COUPANG_WING1", "V1", 6000, 1, sale_date=date(2026, 6, 5))
    db.commit()
    s = _cc(db, "COUPANG_WING1", win)
    assert s["rg_option_axis_days"] == "2/2"
    assert s["rg_option_axis_complete"] is True


def test_aggregate_view_coverage_follows_worst_account(db):
    """★전체합산(account=None) 뷰도 커버리지를 말한다 — 한 계정이 비면 합계는 부분치다.

    분모 계정 목록을 **요약축**에서 뽑는 이유가 여기 있다: 옵션축이 통째로 없는 계정을
    옵션축에서 뽑으면 목록에서 빠져 스스로를 「완전」이라 말한다.
    """
    win = (date(2026, 6, 4), date(2026, 6, 5))
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _ch(db, 2, "COUPANG_WING2", "오하이테크")
    _product(db, "V1", "COUPANG_WING1", "A01564720")
    _product(db, "V2", "COUPANG_WING2", "A01029796")
    # WING1은 이틀 다 있고, WING2는 요약축에만 있고 옵션축이 통째로 없다.
    for d in (date(2026, 6, 4), date(2026, 6, 5)):
        _rg_net(db, "COUPANG_WING1", "V1", 5000, 1, sale_date=d)
        _rg_net_summary(db, "COUPANG_WING1", 5000, 1, sale_date=d)
        _rg_net_summary(db, "COUPANG_WING2", 4000, 1, sale_date=d)
    db.commit()
    s = _cc(db, None, win)
    assert s["rg_option_axis_complete"] is False   # 침묵하지 않는다
    assert s["rg_option_axis_days"] == "0/2"       # 가장 나쁜 계정 기준


# ─────────────────────────────────────────────────────────────
# 기존 회계 규칙 — 축이 바뀌어도 공식은 그대로여야 한다
# ─────────────────────────────────────────────────────────────
def test_net_profit_d3_formula(db):
    """net_profit = RG_rev − RG_cost − rg_total (D-3). 단독 RG 옵션.

    ★원가는 **옵션축 net 수량**을 따른다 — 매출이 net인데 원가만 gross 수량이면 순이익이
      아래로 새고, 그건 조용히 틀린다. 여기 수량 2는 콘솔 net 수량이다.
    """
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _product(db, "V1", "COUPANG_WING1", "A01564720", supply=1000)  # 원가 1,000/개
    _rg_net(db, "COUPANG_WING1", "V1", 10000, 2)                   # net 10,000 / 수량 2
    _rg_fee(db, "COUPANG_WING1", "sale_fee", 800)                  # RG 정산 800
    db.commit()
    s = _cc(db)
    assert s["revenue"] == Decimal("10000")
    assert s["cost"] == Decimal("2000")
    assert s["net_profit_pre_rg"] == Decimal("8000")
    assert s["rg_settlement_total"] == Decimal("800")
    assert s["payable_vat"] == Decimal("654.55")
    assert s["net_profit"] == Decimal("6545.45")


def test_3p_plus_rg_combined(db):
    """3P + RG 합산 매출·순이익. 같은 계정 다른 옵션."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _product(db, "V3P", "COUPANG_WING1", "A01564720")
    _product(db, "VRG", "COUPANG_WING1", "A01564720")
    _three_p(db, 1, "O1", "V3P", 30000)          # 3P 30,000
    _rg_net(db, "COUPANG_WING1", "VRG", 10000, 2)  # RG net 10,000
    db.commit()
    s = _cc(db)
    assert s["revenue"] == Decimal("40000")
    assert s["revenue_3p"] == Decimal("30000")
    assert s["revenue_rg"] == Decimal("10000")


def test_same_vid_3p_and_rg_additive(db):
    """같은 vendor_item_id가 3P·RG 양쪽 판매 → 가산(이중계상 아님, 누락 아님)."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _product(db, "V1", "COUPANG_WING1", "A01564720")
    _three_p(db, 1, "O1", "V1", 30000)
    _rg_net(db, "COUPANG_WING1", "V1", 10000, 2)
    db.commit()
    s = _cc(db)
    assert s["revenue"] == Decimal("40000")
    assert s["revenue_rg"] == Decimal("10000")


def test_return_deduction_uses_3p_unit_price_not_blended(db):
    """Codex S3 P1#1: 같은 vid가 3P·RG 양쪽 판매 + 3P 반품 → 반품차감은 3P 단가(10,000) 기준.

    RG 매출을 섞은 가중평균을 쓰면 안 됨(반품은 3P 전용). 축이 net으로 바뀌어도 같다."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _product(db, "V1", "COUPANG_WING1", "A01564720")
    _three_p(db, 1, "O1", "V1", 10000)              # 3P 단가 10,000
    _rg_net(db, "COUPANG_WING1", "V1", 10000, 2)    # RG net 10,000 / 수량 2
    db.add(CoupangReturnItem(
        receipt_id="R1", vendor_item_id="V1", account_key="COUPANG_WING1",
        vendor_id="A01564720", order_id="O1", receipt_type="RETURN",
        cancel_count=1, withdrawn=False, requested_at=OD))         # 3P 반품 1개
    db.commit()
    s = _cc(db)
    assert s["return_deduction"] == Decimal("10000")  # 3P 단가×1 (NOT 혼합)


def test_rg_revenue_account_split(db):
    """RG 매출도 계정별 분리(account_key 필터) + 등가성 sum==전체."""
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    _ch(db, 2, "COUPANG_WING2", "오하이테크")
    _product(db, "V1", "COUPANG_WING1", "A01564720")
    _product(db, "V2", "COUPANG_WING2", "A01029796")
    _rg_net(db, "COUPANG_WING1", "V1", 10000, 2)  # 오픽스 RG net 10,000
    _rg_net(db, "COUPANG_WING2", "V2", 3000, 1)   # 오하이 RG net 3,000
    db.commit()
    w1 = _cc(db, "COUPANG_WING1")
    w2 = _cc(db, "COUPANG_WING2")
    tot = _cc(db, None)
    assert w1["revenue_rg"] == Decimal("10000")
    assert w2["revenue_rg"] == Decimal("3000")
    assert tot["revenue_rg"] == Decimal("13000")
    assert w1["revenue"] + w2["revenue"] == tot["revenue"]


# ─────────────────────────────────────────────────────────────
# ★③ 계약 ⓑ 불변식 — 종합조망 == 대시보드
# ─────────────────────────────────────────────────────────────
def test_command_center_rg_equals_dashboard_rg_row(db):
    """★계약 ⓑ 그 자체: 같은 창에서 종합조망 RG 매출 == 대시보드 RG 행 매출.

    두 화면은 **다른 축**을 읽는다(종합조망=옵션축, 대시보드=요약축) — 옵션 grain이 필요해서다.
    등가는 prod 147 계정-일 전건 실측으로 확인됐지만, 그건 «오늘의 데이터»에 대한 사실이지
    코드가 지키는 계약이 아니다. 이 테스트가 그 계약을 붙든다: 두 축이 같은 값을 담고 있으면
    두 화면의 숫자도 같아야 한다. 어느 한쪽이 축을 바꾸면 여기서 깨진다.
    """
    from app.services.coupang.rg_channel_pnl import compute_rg_summary_row

    win = (date(2026, 6, 4), date(2026, 6, 5))
    _ch(db, 1, "COUPANG_WING1", "오픽스")
    db.add(Channel(id=9, name="로켓그로스", code="COUPANG_RG1",
                   platform="coupang", company="오픽스", sell_type="RG"))
    _product(db, "V1", "COUPANG_WING1", "A01564720")
    for d, gmv in ((date(2026, 6, 4), 5000), (date(2026, 6, 5), 6000)):
        _rg_net(db, "COUPANG_WING1", "V1", gmv, 1, sale_date=d)
        _rg_net_summary(db, "COUPANG_WING1", gmv, 1, sale_date=d)
    db.commit()

    cc = _cc(db, "COUPANG_WING1", win)
    row = compute_rg_summary_row(
        db, "COUPANG_WING1", win[0], win[1], cost_master={}, vendor_id="A01564720")
    assert row is not None
    assert Decimal(row["revenue"]) == cc["revenue_rg"] == Decimal("11000")
