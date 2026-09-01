# test_rg_sales_date_fees.py — RG 정산공제를 «판매일 축»으로 재귀속하는 계산 (계약 CONTRACT_rg_sales_date_axis).
#
# 무엇을 못 박는가 (전부 `rg_sales_date_fees.py` 본문 주석·계약 §4·§8이 근거다):
#   1. 물류비 단가는 `amount / billed_quantity`에서만 온다 — 0/NULL 수량은 단가 후보가 아니다.
#   2. 옵션·fee_type별로 **가장 최근 주기**의 단가를 쓴다.
#   3. 물류비는 VAT gross-up ×1.1 된다(옵션 row=VAT前, 계정 row=VAT後).
#   4. 요율은 «완결» 주기(recognition_date_to < asof)에서만 잰다. 완결 주기가 없으면 추정하지 않는다.
#   5. 매출 0인 완결 주기는 요율 분모에서 빠진다(0으로 안 나눈다).
#   6. 보관비·반품비는 판매일에 안 붙고 창에 일할 배분된다.
#   7. `ad_sales`는 어디에서도 차감되지 않는다(이중계상 방지, D-CPP-43).
#   8. 날짜별로 값이 달라진다(구 방식은 주기 겹침이라 통짜였다).
#   9. 선형성 — 창의 합 == 날짜별 합의 합(기간비용 제외).
#  10. `compute_rg_summary_row`의 이중 게이트(원가·수수료 각각 「모르면 안 낸다」).
#  11. 자백 칸(axis·coverage·unmapped_revenue·reconcile)이 실제로 채워진다.
#
# 라이브 API 호출 없음. 인메모리 SQLite. (픽스처·시딩 헬퍼는 test_rg_net_channel_pnl.py를 그대로 베꼈다.)
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    Channel,
    CoupangProductItem,
    CoupangRgInventory,
    CoupangRgSettlementFee,
    CoupangVendorItemSalesDaily,
    CoupangVendorSummaryDaily,
    ProductChannelMapping,
    ProductMaster,
)
from app.services.coupang.intelligence import compute_command_center
from app.services.coupang.rg_channel_pnl import compute_rg_summary_row
from app.services.coupang.rg_sales_date_fees import (
    daily_fees,
    ledger_total,
    period_fees,
    sale_fee_rate,
    sales_date_fees,
    unit_logistics_prices,
)

_Z = Decimal(0)
ACC = "COUPANG_WING1"
VENDOR = "A01564720"


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    _seed_channels(session)
    session.commit()
    yield session
    session.close()


def _seed_channels(db):
    for cid, code, company, sell_type, ctype in [
        (1, "COUPANG_WING1", "개인회사 오픽스", "3P", "marketplace"),
        (3, "COUPANG_RG1", "개인회사 오픽스", "RG", "marketplace"),
    ]:
        db.add(Channel(id=cid, name=code, code=code, platform="coupang",
                       company=company, sell_type=sell_type, channel_type=ctype))


def _seed_summary(db, day, gmv, units, *, rt="RFM", acc=ACC):
    db.add(CoupangVendorSummaryDaily(summary_date=day, account_key=acc,
                                     registration_type=rt, gmv=gmv, units_sold=units))


def _seed_option(db, day, vid, gmv, units, *, rt="RFM", acc=ACC, orders=1):
    db.add(CoupangVendorItemSalesDaily(sale_date=day, account_key=acc, vendor_item_id=vid,
                                       registration_type=rt, gmv=gmv, units_sold=units,
                                       total_orders=orders))


def _seed_catalog(db, vid, *, acc=ACC):
    db.add(CoupangProductItem(vendor_item_id=vid, account_key=acc, vendor_id=VENDOR,
                              seller_product_id="SP1", sale_price=Decimal("10000")))


def _seed_rg_inventory(db, vid, *, acc=ACC):
    db.add(CoupangRgInventory(vendor_item_id=vid, account_key=acc, vendor_id=VENDOR))


def _seed_cost(db, vid, cost, pid=1):
    db.add(ProductMaster(id=pid, internal_sku=f"SKU{pid}", product_name=f"P{pid}",
                         cost_price=Decimal(str(cost))))
    db.add(ProductChannelMapping(channel_id=3, product_id=pid,
                                 channel_product_id=vid, is_active=True))


def _cost_master(db):
    from app.services.coupang.intelligence import _cost_master as cm
    return cm(db)


def _seed_option_fee(db, vid, fee_type, amount, billed_qty, *,
                     acc=ACC, dfrom=date(2026, 8, 1), dto=date(2026, 8, 7)):
    """옵션 단위 정산 row(S6) — 단가 후보. `billed_quantity`가 있어야 후보가 된다."""
    db.add(CoupangRgSettlementFee(account_key=acc, recognition_date_from=dfrom,
                                  recognition_date_to=dto, fee_type=fee_type,
                                  vendor_item_id=vid, amount=Decimal(str(amount)),
                                  billed_quantity=billed_qty))


def _seed_account_fee(db, fee_type, amount, *, acc=ACC,
                      dfrom=date(2026, 8, 1), dto=date(2026, 8, 7)):
    """계정 단위 정산 row(status/api) — vendor_item_id='' sentinel."""
    db.add(CoupangRgSettlementFee(account_key=acc, recognition_date_from=dfrom,
                                  recognition_date_to=dto, fee_type=fee_type,
                                  vendor_item_id="", amount=Decimal(str(amount))))


def _open_cycle_end() -> date:
    """**아직 안 끝난** 정산주기의 종료일 — 「진행 중」을 뜻하는 시드에 쓴다.

    ★고정 날짜로 쓰면 안 된다. 완결 판정은 `rg_sales_date_fees.py:139`의
      `recognition_date_to < asof`이고, `asof`를 안 넘기면 `:321`·`:459`가
      `date.today()`로 채운다(`compute_rg_summary_row` 경로엔 `asof` 인자 자체가 없다).
      그래서 **고정 종료일은 그날이 지나는 순간 「진행 중」이 「완결」로 뒤집히고
      테스트가 자정에 빨개진다.**

    실제로 그렇게 터졌다 — 옛 시드 `date(2026, 8, 31)`이 2026-09-01 00:00 KST에 완결로
    넘어가며 이 파일 2건(`test_falls_back_to_ledger_axis_when_rate_unknown`·
    `test_commission_axis_requires_known_rate_even_with_full_fee_coverage`)이 실패했고,
    main을 포함해 **그날 도는 모든 브랜치의 CI가 빨강**이 됐다(2026-09-01 실측:
    main CI 3연속 실패 · PR #620이 그 때문에 머지 못 하고 반착지).

    ⇒ 주기의 «뜻»이 「오늘 기준 아직 안 끝났다」는 **상대적** 사실이므로 날짜도 상대적으로
      잡는다. 시계를 언제로 옮겨도 이 시드는 항상 「진행 중」이다.
    """
    return date.today() + timedelta(days=365)


# ════════════════════════════════════════════════
# 1. 단가 — billed_quantity가 없으면 후보가 아니다
# ════════════════════════════════════════════════
def test_unit_price_requires_billed_quantity(db):
    """`billed_quantity`가 NULL이거나 0인 옵션 row는 단가 후보에서 빠진다.

    NULL·0 둘 다 같은 이유로 배제된다 — 분모가 없으면 「건당 단가」라는 말 자체가 안 선다.
    그 옵션의 매출은 `unmapped_revenue`로 자백돼야지 조용히 0으로 채워지면 안 된다.
    """
    _seed_option_fee(db, "NULLQ", "delivery", 1_000, None)   # NULL — 후보 아님
    _seed_option_fee(db, "ZEROQ", "delivery", 1_000, 0)      # 0 — 후보 아님
    _seed_option_fee(db, "OK1", "delivery", 500, 10)         # 500/10 = 50원/개 — 유일한 후보
    db.commit()

    units = unit_logistics_prices(db, ACC)
    assert (ACC, "NULLQ") not in units, "billed_quantity NULL은 단가 후보가 아니다"
    assert (ACC, "ZEROQ") not in units, "billed_quantity 0은 단가 후보가 아니다"
    assert units[(ACC, "OK1")]["delivery"] == Decimal("50")


# ════════════════════════════════════════════════
# 2. 최근 주기 우선 — 같은 옵션·fee_type에 두 주기 단가가 있으면 최신 것
# ════════════════════════════════════════════════
def test_unit_price_uses_most_recent_cycle(db):
    """옛 주기 단가(100원)가 있어도 최신 주기 단가(80원)를 쓴다 — 쿠팡이 사이즈 재측정으로
    단가를 바꾸면 다음 정산부터 자동 반영돼야 한다(3P `option_fee_rates`와 같은 규율).
    """
    _seed_option_fee(db, "V1", "delivery", 1_000, 10,     # 100원/개, 옛 주기
                     dfrom=date(2026, 7, 1), dto=date(2026, 7, 7))
    _seed_option_fee(db, "V1", "delivery", 800, 10,       # 80원/개, 최신 주기
                     dfrom=date(2026, 8, 1), dto=date(2026, 8, 7))
    db.commit()

    units = unit_logistics_prices(db, ACC)
    assert units[(ACC, "V1")]["delivery"] == Decimal("80"), "최신 주기 단가를 써야 한다"
    assert units[(ACC, "V1")]["cycle_to"] == date(2026, 8, 7)


# ════════════════════════════════════════════════
# 3. VAT gross-up — 물류비는 ×1.1
# ════════════════════════════════════════════════
def test_logistics_applies_vat_grossup(db):
    """옵션 row 단가는 VAT前이라 판매일 축에 세울 때 ×1.1 gross-up 된다.

    단가 100원(delivery) × 수량 10 = 1,000원(VAT前) → ×1.1 = 1,100원(VAT後)이어야 한다.
    """
    _seed_option_fee(db, "V1", "delivery", 1_000, 10)   # 100원/개
    _seed_summary(db, date(2026, 8, 5), 50_000, 5)
    _seed_option(db, date(2026, 8, 5), "V1", 50_000, 10)
    db.commit()

    fees = sales_date_fees(db, ACC, date(2026, 8, 5), date(2026, 8, 5), reconcile=False)
    assert fees["logistics"] == Decimal("100") * 10 * Decimal("1.1")
    assert fees["logistics"] == Decimal("1100")


# ════════════════════════════════════════════════
# 4. 요율은 «완결» 주기에서만 — 진행 중 주기를 섞지 않는다
# ════════════════════════════════════════════════
def test_rate_only_from_completed_cycles(db):
    """`recognition_date_to < asof`인 주기만 요율 분모·분자에 들어간다. 진행 중 주기는 제외된다."""
    asof = date(2026, 8, 20)
    # 완결 주기 — 08-08~08-14: sale_fee 8,000 / gmv 100,000 → 8%
    _seed_account_fee(db, "sale_fee", 8_000, dfrom=date(2026, 8, 8), dto=date(2026, 8, 14))
    _seed_summary(db, date(2026, 8, 10), 100_000, 10)
    # 진행 중 주기(asof보다 늦게 끝남) — 섞이면 안 된다. 매우 높은 요율로 심었다.
    _seed_account_fee(db, "sale_fee", 90_000, dfrom=date(2026, 8, 15), dto=date(2026, 8, 21))
    _seed_summary(db, date(2026, 8, 16), 10_000, 1)
    db.commit()

    info = sale_fee_rate(db, ACC, asof)
    assert info["rate"] == Decimal("8000") / Decimal("100000")
    assert info["basis"] == "settled_rate"
    assert (date(2026, 8, 15), date(2026, 8, 21)) not in info["cycles"], \
        "진행 중 주기가 섞이면 안 된다"


def test_rate_unknown_without_fallback_when_no_completed_cycle(db):
    """완결 주기가 하나도 없으면 `rate=None`·`basis="rate_unknown"` — **기본 요율로 폴백하지 않는다**.

    3P는 기본 요율(default_rate)로 폴백하지만 RG엔 그런 근거값이 없다(계약 §8-4).
    """
    asof = date(2026, 8, 5)
    # 유일한 주기가 asof보다 늦게 끝난다 = 진행 중 → 완결 주기 0개
    _seed_account_fee(db, "sale_fee", 8_000, dfrom=date(2026, 8, 1), dto=date(2026, 8, 10))
    _seed_summary(db, date(2026, 8, 3), 100_000, 10)
    db.commit()

    info = sale_fee_rate(db, ACC, asof)
    assert info["rate"] is None
    assert info["basis"] == "rate_unknown"
    assert info["cycles"] == []


# ════════════════════════════════════════════════
# 5. 매출 0인 완결 주기는 분모에서 빠진다
# ════════════════════════════════════════════════
def test_zero_revenue_cycle_excluded_from_rate_denominator(db):
    """매출이 정확히 0인 완결 주기(재고 보유만 있고 안 팔린 주)는 요율의 근거가 될 수 없다 —
    분모가 0이라 나누기가 안 되고, 그 주기 자체는 정상이다(WING2엔 그런 주기가 흔하다).
    """
    asof = date(2026, 8, 20)
    # 매출 0 주기 — sale_fee가 있어도(정상 청구) 분모가 없어 스킵돼야 한다
    _seed_account_fee(db, "sale_fee", 500, dfrom=date(2026, 8, 1), dto=date(2026, 8, 7))
    # 매출 있는 완결 주기
    _seed_account_fee(db, "sale_fee", 8_000, dfrom=date(2026, 8, 8), dto=date(2026, 8, 14))
    _seed_summary(db, date(2026, 8, 10), 100_000, 10)
    db.commit()

    info = sale_fee_rate(db, ACC, asof)
    assert info["rate"] == Decimal("8000") / Decimal("100000"), \
        "매출 0 주기의 500원이 분자에 안 섞여야 한다"
    assert len(info["cycles"]) == 1


# ════════════════════════════════════════════════
# 6. 보관비·반품비 — 판매일에 안 붙고 창에 일할 배분
# ════════════════════════════════════════════════
def test_period_fees_prorate_by_window_overlap(db):
    """보관비 주기가 창에 절반만 걸치면 절반만 온다 — 「그날 판 것」이 아니라 「그날 재고를
    갖고 있던 값」이기 때문이다(계약 §8-5).
    """
    # 주기 08-01~08-10 (10일), storage 1,000원 → 하루 100원
    _seed_account_fee(db, "storage", 1_000, dfrom=date(2026, 8, 1), dto=date(2026, 8, 10))
    db.commit()

    # 창이 주기의 뒷절반(08-06~08-10, 5일)만 덮는다
    half = period_fees(db, ACC, date(2026, 8, 6), date(2026, 8, 10))
    assert half == Decimal("1000") * 5 / 10 == Decimal("500")

    # 창이 주기 전체(10일)를 덮으면 전액
    full = period_fees(db, ACC, date(2026, 8, 1), date(2026, 8, 10))
    assert full == Decimal("1000")


def test_period_fees_do_not_attach_to_sales_date(db):
    """매출이 있는 날에도 storage·반품비는 `sales_date_fees`의 `logistics`·`sale_fee`가 아니라
    `period`로만 잡힌다 — 판매수량과 무관하게 일할 배분된다.
    """
    _seed_account_fee(db, "storage", 700, dfrom=date(2026, 8, 1), dto=date(2026, 8, 7))
    _seed_account_fee(db, "return_shipping", 70, dfrom=date(2026, 8, 1), dto=date(2026, 8, 7))
    _seed_summary(db, date(2026, 8, 5), 50_000, 5)
    db.commit()

    fees = sales_date_fees(db, ACC, date(2026, 8, 5), date(2026, 8, 5), reconcile=False)
    # 7일 주기 중 하루 → (700+70)/7 = 110
    assert fees["period"] == Decimal("770") / 7
    assert fees["logistics"] == _Z
    assert fees["sale_fee"] == _Z


# ════════════════════════════════════════════════
# 7. ad_sales는 어디서도 차감되지 않는다 (D-CPP-43)
# ════════════════════════════════════════════════
def test_ad_sales_never_deducted(db):
    """`ad_sales`는 `SALES_DATE_FEE_TYPES`에도 `PERIOD_FEE_TYPES`에도 없다 — 물류비·수수료·
    기간비용 어느 항으로도 안 들어간다. 정산 ad_sales는 PA 광고비의 «공제»라 또 빼면 이중계상이다.
    """
    _seed_account_fee(db, "sale_fee", 8_000, dfrom=date(2026, 8, 1), dto=date(2026, 8, 7))
    _seed_account_fee(db, "ad_sales", 999_999, dfrom=date(2026, 8, 1), dto=date(2026, 8, 7))
    _seed_summary(db, date(2026, 8, 5), 100_000, 10)
    db.commit()

    fees = sales_date_fees(db, ACC, date(2026, 8, 5), date(2026, 8, 5), asof=date(2026, 8, 20),
                           reconcile=False)
    # sale_fee 요율 실측 대상에도 ad_sales가 안 섞인다 — period·logistics도 0이어야 한다
    assert fees["period"] == _Z
    assert fees["logistics"] == _Z
    # 총액에 999,999가 흔적조차 없어야 한다
    assert fees["total"] < Decimal("100000"), "ad_sales가 섞이면 총액이 폭증한다"


# ════════════════════════════════════════════════
# 8. 날짜별로 값이 달라진다 (§4 ⓑ 회귀 가드)
# ════════════════════════════════════════════════
def test_daily_fees_differ_across_dates_in_same_cycle(db):
    """같은 정산주기를 덮는 두 날짜라도 그날 판 수량이 다르면 값이 달라져야 한다.

    구 방식(`get_rg_total_by_account`, 정산 인식일 창 겹침)은 같은 주기 안 어느 하루를 물어도
    같은 값이 나왔다(08-17~21 다섯 날 전부 153,058원) — 이 테스트가 그 회귀를 막는다.
    """
    _seed_option_fee(db, "V1", "delivery", 1_000, 10,     # 100원/개
                     dfrom=date(2026, 8, 1), dto=date(2026, 8, 7))
    _seed_option(db, date(2026, 8, 3), "V1", 10_000, 5)   # 5개 판매
    _seed_option(db, date(2026, 8, 4), "V1", 20_000, 20)  # 20개 판매 — 다른 값이어야 한다
    db.commit()

    daily = daily_fees(db, ACC, date(2026, 8, 3), date(2026, 8, 4), asof=date(2026, 8, 20))
    assert daily[date(2026, 8, 3)] != daily[date(2026, 8, 4)], \
        "같은 주기라도 판매수량이 다르면 값이 달라야 한다"
    assert daily[date(2026, 8, 3)] == Decimal("100") * 5 * Decimal("1.1")
    assert daily[date(2026, 8, 4)] == Decimal("100") * 20 * Decimal("1.1")


# ════════════════════════════════════════════════
# 9. 선형성 — 창의 합 == 날짜별 합의 합 (기간비용 제외)
# ════════════════════════════════════════════════
def test_window_total_equals_sum_of_daily_totals_excluding_period(db):
    """물류비(단가×수량)도 수수료(매출×요율)도 선형이라, 창 전체로 한 번 곱한 것과
    날짜별로 쪼개 더한 것이 같아야 한다. 기간비용(보관비 등)은 일할이라 이 등식에서 뺀다
    (모듈 docstring이 「그래서 날짜 루프를 돌지 않는다」고 명시한 바로 그 성질).
    """
    asof = date(2026, 8, 20)
    _seed_option_fee(db, "V1", "delivery", 1_000, 10)   # 100원/개
    _seed_account_fee(db, "sale_fee", 8_000, dfrom=date(2026, 8, 8), dto=date(2026, 8, 14))
    _seed_summary(db, date(2026, 8, 10), 100_000, 10)   # 완결 주기 요율 실측용 8%

    for d, gmv, qty in [(date(2026, 8, 3), 10_000, 5), (date(2026, 8, 4), 30_000, 15)]:
        _seed_summary(db, d, gmv, qty)
        _seed_option(db, d, "V1", gmv, qty)
    db.commit()

    window = sales_date_fees(db, ACC, date(2026, 8, 3), date(2026, 8, 4), asof=asof,
                             reconcile=False)
    daily = daily_fees(db, ACC, date(2026, 8, 3), date(2026, 8, 4), asof=asof)
    d1 = sales_date_fees(db, ACC, date(2026, 8, 3), date(2026, 8, 3), asof=asof, reconcile=False)
    d2 = sales_date_fees(db, ACC, date(2026, 8, 4), date(2026, 8, 4), asof=asof, reconcile=False)

    assert window["logistics"] == d1["logistics"] + d2["logistics"]
    assert window["sale_fee"] == d1["sale_fee"] + d2["sale_fee"]
    assert sum(daily.values()) == d1["total"] + d2["total"]


# ════════════════════════════════════════════════
# 10. compute_rg_summary_row 게이트
# ════════════════════════════════════════════════
def test_falls_back_to_ledger_axis_when_rate_unknown(db):
    """요율을 못 재면 판매일 축을 «내지 않고 원장 축으로 물러선다» — 그리고 행이 그렇게 말한다.

    ★2026-08-22 라이브 실측이 이 처분을 정했다: `billed_quantity`가 07-27 이후 주기에만 있어서
      **하루 창은 커버리지 100%인데 30일 창은 91.9%**다. 「못 재면 순이익 없음」으로 두면
      대시보드 기본 창에서 RG 순이익이 통째로 「—」가 되는데, 원장 축은 «모름»이 아니라
      창이 넓을수록 정확해지는 **다른 축의 실측값**이라 그건 종전보다 덜 아는 화면이다.
    ⇒ 값은 원장 축으로 내되 `commission_axis`가 자백하고, 판매일 축 분해는 **싣지 않는다**
      (합이 안 맞는 분해는 「이 값이 저 셋으로 이뤄졌다」는 거짓말이 된다).
    """
    for d in (date(2026, 8, 5), date(2026, 8, 6)):
        _seed_summary(db, d, 50_000, 5)
        _seed_option(db, d, "RG1", 50_000, 5)
    _seed_catalog(db, "RG1"); _seed_rg_inventory(db, "RG1")
    _seed_cost(db, "RG1", 2_000)
    # 원장엔 값이 있는데 그 주기가 **아직 안 끝났다**(to ≥ asof) → 완결 주기 0개 → rate=None
    _seed_account_fee(db, "sale_fee", 8_000, dfrom=date(2026, 8, 1), dto=_open_cycle_end())
    db.commit()

    row = compute_rg_summary_row(db, ACC, date(2026, 8, 5), date(2026, 8, 6), _cost_master(db),
                                 VENDOR)
    assert row["commission_basis"] == "rate_unknown"
    assert row["commission_axis"] == "recognition_date", "못 잰 사실이 행에 남아야 한다"
    assert Decimal(row["commission"]) == Decimal("8000"), "원장 축 실측값으로 물러선다"
    assert row["commission_logistics"] is None, "합이 안 맞는 분해는 싣지 않는다"
    assert row["net_profit"] is not None, "원가 게이트가 통과했으면 순이익은 낸다(축만 물러섰다)"


def test_ledger_axis_when_logistics_coverage_below_threshold(db):
    """물류비 단가 커버리지가 `FEE_COVERAGE_MIN`(기본 0.95) 미만이면 요율이 멀쩡해도
    **판매일 축을 내지 않는다** — 단가를 모르는 매출 비중이 크면 물류비가 과소라 순이익이
    위로 부푼다. 그 창은 원장 축으로 물러서고 `commission_axis`가 그렇게 말한다.

    ★이 케이스는 원가 게이트도 같이 실패한다(옵션축이 창 7일 중 2일뿐) — 그래서
      `net_profit is None`은 **원가 게이트의 결과**다. 수수료 쪽 판정은 축·커버리지로 본다.
    """
    asof_win = (date(2026, 8, 15), date(2026, 8, 21))
    # 완결 요율은 확보(rate 실측 가능하게)
    _seed_account_fee(db, "sale_fee", 8_000, dfrom=date(2026, 8, 1), dto=date(2026, 8, 7))
    _seed_summary(db, date(2026, 8, 3), 100_000, 10)

    # 단가를 「아는」 옵션(V1, 매출 10%)과 「모르는」 옵션(V2, 매출 90%) — 커버리지 10%
    _seed_option_fee(db, "V1", "delivery", 1_000, 10, dfrom=date(2026, 8, 15), dto=date(2026, 8, 21))
    for d in asof_win:
        _seed_summary(db, d, 100_000, 10)
        _seed_option(db, d, "V1", 10_000, 1)
        _seed_option(db, d, "V2", 90_000, 9)
    _seed_catalog(db, "V1"); _seed_rg_inventory(db, "V1")
    _seed_catalog(db, "V2"); _seed_rg_inventory(db, "V2")
    _seed_cost(db, "V1", 500, pid=1)
    _seed_cost(db, "V2", 500, pid=2)
    db.commit()

    row = compute_rg_summary_row(db, ACC, *asof_win, _cost_master(db), VENDOR)
    assert Decimal(row["fee_coverage"]) < Decimal("0.95")
    assert row["commission_axis"] == "recognition_date", \
        "단가를 모르는 매출이 크면 판매일 축을 내지 않는다"
    assert row["commission_sale_fee"] is None, "판매일 축 분해는 그 축을 낼 때만 싣는다"
    assert row["net_profit"] is None, "원가 게이트(옵션축이 창을 못 덮음)가 순이익을 막는다"


def test_net_profit_present_when_both_gates_pass(db):
    """원가·수수료 게이트를 둘 다 통과하면 `net_profit`이 나오고 `commission_basis=="settled_rate"`."""
    win = (date(2026, 8, 15), date(2026, 8, 15))
    _seed_account_fee(db, "sale_fee", 8_000, dfrom=date(2026, 8, 1), dto=date(2026, 8, 7))
    _seed_summary(db, date(2026, 8, 3), 100_000, 10)   # 완결 주기 요율 8%

    _seed_option_fee(db, "RG1", "delivery", 1_000, 10, dfrom=date(2026, 8, 8), dto=date(2026, 8, 14))
    _seed_summary(db, date(2026, 8, 15), 50_000, 5)
    _seed_option(db, date(2026, 8, 15), "RG1", 50_000, 5)
    _seed_catalog(db, "RG1"); _seed_rg_inventory(db, "RG1")
    _seed_cost(db, "RG1", 2_000)
    db.commit()

    row = compute_rg_summary_row(db, ACC, *win, _cost_master(db), VENDOR)
    assert row["net_profit"] is not None
    assert row["commission_basis"] == "settled_rate"
    assert Decimal(row["cost"]) == Decimal("10000")   # 5개 × 2,000


def test_cost_and_net_survive_when_only_fee_axis_falls_back(db):
    """★수수료 축만 물러선 경우 — 원가도 순이익도 살아 있다. 두 판정은 서로 독립이다.

    원가 게이트(=원가를 붙인 매출 비율·옵션축이 창을 덮는가)와 수수료 축 판정(=요율·단가를
    쟀는가)은 **다른 것을 잰다.** 하나가 실패했다고 다른 하나의 값을 지우면 화면이
    「원가 0」·「순이익 —」으로 잘못 읽는다. 어긋난 것은 각자 자기 칸에서 말한다.
    """
    for d in (date(2026, 8, 5), date(2026, 8, 6)):
        _seed_summary(db, d, 50_000, 5)
        _seed_option(db, d, "RG1", 50_000, 5)
    _seed_catalog(db, "RG1"); _seed_rg_inventory(db, "RG1")
    _seed_cost(db, "RG1", 2_000)
    # 완결 주기 없음 → rate=None → 수수료 축 폴백. 원가 게이트는 정상(커버리지 100%, 창 완전).
    db.commit()

    row = compute_rg_summary_row(db, ACC, date(2026, 8, 5), date(2026, 8, 6), _cost_master(db),
                                 VENDOR)
    assert row["commission_axis"] == "recognition_date"
    assert Decimal(row["cost"]) == Decimal("20000"), \
        "원가 게이트가 통과했으면 cost는 0이 아니라 실측값이 실려야 한다"
    assert row["net_profit"] is not None, "수수료 축이 물러선 것이 순이익을 지우지 않는다"


# ════════════════════════════════════════════════
# 11. 자백 칸이 실제로 채워진다
# ════════════════════════════════════════════════
def test_confession_fields_are_populated_when_computable(db):
    """`commission_axis`·`fee_coverage`·`fee_unmapped_revenue`·`settlement_reconcile_diff`가
    None이 아닌 케이스 — 값이 나올 수 있을 때 실제로 나오는지를 본다.
    """
    win = (date(2026, 8, 15), date(2026, 8, 15))
    # 완결 주기(요율 실측·보존식 대조 둘 다 이 주기 하나로 잡힌다 — asof 기준 「최근 완결」)
    _seed_account_fee(db, "sale_fee", 8_000, dfrom=date(2026, 8, 1), dto=date(2026, 8, 7))
    _seed_summary(db, date(2026, 8, 3), 100_000, 10)

    # 창(08-15) 안에 실제 판매가 있어야 `coverage`가 None이 아니다(revenue_total>0 필요).
    # RG1은 단가를 알고(커버리지 대상) UNK는 단가를 몰라 unmapped_revenue로 자백돼야 한다.
    _seed_option_fee(db, "RG1", "delivery", 500, 5, dfrom=date(2026, 8, 8), dto=date(2026, 8, 14))
    _seed_summary(db, date(2026, 8, 15), 50_000, 5)
    # ★단가 미상 몫은 임계(5%) «안»으로 둔다 — 넘으면 축이 원장으로 물러서서 이 케이스가
    #   「자백 칸이 채워지는 판매일 축」을 못 보게 된다(그 경로는 위 폴백 테스트가 본다).
    _seed_option(db, date(2026, 8, 15), "RG1", 48_000, 4)
    _seed_option(db, date(2026, 8, 15), "UNK", 2_000, 1)    # 단가 모름 → unmapped_revenue 자백 대상
    db.commit()

    fees = sales_date_fees(db, ACC, *win, asof=date(2026, 8, 20))
    assert fees["coverage"] is not None
    assert fees["unmapped_revenue"] >= _Z

    row = compute_rg_summary_row(db, ACC, *win, {}, VENDOR)
    assert row["commission_axis"] == "sales_date"
    assert row["fee_coverage"] is not None
    assert row["fee_unmapped_revenue"] is not None
    assert row["settlement_reconcile_diff"] is not None, \
        "완결 주기가 있으면 보존식 대조 diff가 나와야 한다(숨겨서 0/None으로 만들지 않는다)"


# ════════════════════════════════════════════════
# 12. 변이 주입 8종 보강 — PR #337 적대 리뷰가 살려 둔 것들 (2026-08-23)
#
# 위 테스트들이 값을 낸다는 것만 보고 «그 값이 옳은가»·«보존식이 정직한가»를 안 지켜서
# 8종 변이가 살아남았다. 각 테스트는 정확한 기대값을 단언한다(공허한 >=0류 금지).
# ════════════════════════════════════════════════

def test_sale_fee_uses_full_revenue_not_only_priced_portion(db):
    """M6 — 수수료 분모가 `revenue_total` → `revenue_priced`로 바뀌면 빨개진다.

    판매수수료는 정산 원장이 계정 매출 «전체»에 매기는 것이라 물류비 단가 커버리지와 무관하다.
    단가 미상 옵션을 임계(5%) 안으로 섞어도 수수료는 줄면 안 된다 — 줄면 그건 옵션축 커버리지가
    수수료 계산에 잘못 새어 들어온 것이다.
    """
    asof = date(2026, 8, 20)
    _seed_account_fee(db, "sale_fee", 8_000, dfrom=date(2026, 8, 1), dto=date(2026, 8, 7))
    _seed_summary(db, date(2026, 8, 3), 100_000, 10)   # 완결 주기 요율 8%

    _seed_option_fee(db, "RG1", "delivery", 500, 5,
                     dfrom=date(2026, 8, 8), dto=date(2026, 8, 14))  # 단가 100원/개(참고용, 안 씀)
    _seed_option(db, date(2026, 8, 15), "RG1", 96_000, 8)   # 단가 앎 — 매출의 96%
    _seed_option(db, date(2026, 8, 15), "UNK", 4_000, 1)    # 단가 모름 — 매출의 4%(임계 5% 안)
    db.commit()

    fees = sales_date_fees(db, ACC, date(2026, 8, 15), date(2026, 8, 15), asof=asof, reconcile=False)
    assert fees["revenue_total"] == Decimal("100000")
    assert fees["revenue_priced"] == Decimal("96000")
    assert fees["sale_fee"] == Decimal("8000"), \
        "수수료 = revenue_total(100,000) × 요율(8%) = 8,000이어야 한다"
    assert fees["sale_fee"] != Decimal("96000") * Decimal("0.08"), \
        "revenue_priced(96,000)만으로 계산한 7,680원이 나오면 안 된다"


def test_unmapped_revenue_confesses_exact_amount(db):
    """M7 — `unmapped_revenue` 자백이 삭제되면(항상 0) 빨개진다.

    기존 `>= 0` 단언은 그 변이를 못 잡는 공허한 단언이었다 — 여기선 정확한 합계를 요구한다.
    """
    _seed_option(db, date(2026, 8, 15), "UNK1", 3_000, 3)   # 단가 모름
    _seed_option(db, date(2026, 8, 15), "UNK2", 700, 1)     # 단가 모름 — 합쳐서 자백돼야 한다
    db.commit()

    fees = sales_date_fees(db, ACC, date(2026, 8, 15), date(2026, 8, 15), reconcile=False)
    assert fees["unmapped_revenue"] == Decimal("3700"), \
        "단가 모르는 두 옵션의 매출 합이 정확히 자백돼야 한다(0으로 삭제되면 안 된다)"


def test_ledger_total_excludes_ad_sales(db):
    """M9 — `ledger_total`에서 `ad_sales` 제외를 빼면(전부 합산) 빨개진다.

    `ledger_total`은 보존식 대조의 «실청구액» 기준이다 — 여기에 ad_sales가 섞이면 대조
    자체가 이중계상 여부를 가리는 게 아니라 이중계상을 만들게 된다.
    """
    _seed_account_fee(db, "sale_fee", 8_000, dfrom=date(2026, 8, 1), dto=date(2026, 8, 7))
    _seed_account_fee(db, "ad_sales", 5_000, dfrom=date(2026, 8, 1), dto=date(2026, 8, 7))
    db.commit()

    total = ledger_total(db, ACC, date(2026, 8, 1), date(2026, 8, 7))
    assert total == Decimal("8000"), "ad_sales는 원장 대조 총액에서도 빠져야 한다(D-CPP-43)"


def test_reconciliation_diff_is_exact_not_hidden_as_zero(db):
    """M10 — 보존식 `diff`가 0으로 숨겨지면 빨개진다.

    완결 두 주기의 요율이 다르면(8,000/100,000=8% vs 9,000/100,000=9%) 최근 주기에서
    실측하는 요율은 두 주기를 섞은 8.5%가 되고, 그 주기 자체의 실청구액(9,000)과는
    정확히 −500원(=8,500−9,000) 어긋나야 한다 — 그 어긋남이 살아 있어야 한다.
    """
    asof = date(2026, 8, 20)
    _seed_account_fee(db, "sale_fee", 8_000, dfrom=date(2026, 8, 1), dto=date(2026, 8, 7))
    _seed_summary(db, date(2026, 8, 3), 100_000, 10)
    _seed_account_fee(db, "sale_fee", 9_000, dfrom=date(2026, 8, 8), dto=date(2026, 8, 14))
    _seed_summary(db, date(2026, 8, 10), 100_000, 10)
    _seed_option(db, date(2026, 8, 10), "RG1", 100_000, 10)   # 최근 완결 주기의 옵션축 매출
    db.commit()

    # 조회 창은 아무 날이나 상관없다 — reconciliation은 «최근 완결 주기»를 독립적으로 재계산한다.
    fees = sales_date_fees(db, ACC, date(2026, 8, 20), date(2026, 8, 20), asof=asof)
    rec = fees["reconciliation"]
    assert rec is not None
    assert rec["diff"] == Decimal("-500"), \
        "8.5% 혼합요율×100,000=8,500 − 실청구 9,000 = −500이어야 한다"
    assert rec["diff"] != Decimal("0")


def test_reconciliation_field_is_none_without_completed_cycle(db):
    """M16(모듈층) — 완결 주기가 없을 때 `reconciliation` 자체를 "0"류 값으로 채우면 빨개진다."""
    _seed_summary(db, date(2026, 8, 15), 50_000, 5)
    _seed_option(db, date(2026, 8, 15), "RG1", 50_000, 5)
    db.commit()

    fees = sales_date_fees(db, ACC, date(2026, 8, 15), date(2026, 8, 15))
    assert fees["reconciliation"] is None, "완결 주기가 없으면 reconciliation은 None이어야 한다"


def test_total_includes_period_fee_exactly(db):
    """M12 — `total`에서 기간비용(`period`)이 누락되면 빨개진다."""
    asof = date(2026, 8, 20)
    _seed_account_fee(db, "sale_fee", 8_000, dfrom=date(2026, 8, 1), dto=date(2026, 8, 7))
    _seed_summary(db, date(2026, 8, 3), 100_000, 10)   # 완결 주기 요율 8%

    _seed_option_fee(db, "RG1", "delivery", 1_000, 10,
                     dfrom=date(2026, 8, 1), dto=date(2026, 8, 7))   # 단가 100원/개
    _seed_account_fee(db, "storage", 700, dfrom=date(2026, 8, 15), dto=date(2026, 8, 21))  # 7일 주기

    _seed_summary(db, date(2026, 8, 15), 50_000, 5)
    _seed_option(db, date(2026, 8, 15), "RG1", 50_000, 5)
    db.commit()

    fees = sales_date_fees(db, ACC, date(2026, 8, 15), date(2026, 8, 15), asof=asof, reconcile=False)
    assert fees["period"] == Decimal("100"), "700/7(겹친 1일)이어야 한다"
    assert fees["logistics"] == Decimal("550"), "100원×5개×1.1이어야 한다"
    assert fees["sale_fee"] == Decimal("4000"), "50,000×8%여야 한다"
    assert fees["total"] == Decimal("4650")
    assert fees["total"] == fees["logistics"] + fees["sale_fee"] + fees["period"], \
        "total은 세 항의 합이어야 한다(기간비용을 빼면 안 된다)"


def test_commission_axis_requires_known_rate_even_with_full_fee_coverage(db):
    """M14 — `compute_rg_summary_row` 게이트에서 `rate is not None`을 빼면 빨개진다.

    ★단가는 다 알아서(물류비 커버리지 100%) 그 게이트는 통과하는데 요율만 못 잰(완결 주기
    없음) 시드다. 기존 폴백 테스트는 커버리지도 같이 실패하는 시드라 이 조각 하나만 빠져도
    안 잡혔다 — 여기서는 커버리지를 100%로 고정해 `rate is not None` 단독의 효과를 가른다.
    """
    for d in (date(2026, 8, 5), date(2026, 8, 6)):
        _seed_summary(db, d, 50_000, 5)
        _seed_option(db, d, "RG1", 50_000, 5)
    _seed_option_fee(db, "RG1", "delivery", 1_000, 10, dfrom=date(2026, 8, 1), dto=date(2026, 8, 7))
    _seed_catalog(db, "RG1"); _seed_rg_inventory(db, "RG1")
    _seed_cost(db, "RG1", 2_000)
    # 진행 중 주기(아직 안 끝남, to ≥ asof) → 완결 주기 0개 → rate=None. 단가는 이미 100% 안다.
    _seed_account_fee(db, "sale_fee", 8_000, dfrom=date(2026, 8, 1), dto=_open_cycle_end())
    db.commit()

    row = compute_rg_summary_row(db, ACC, date(2026, 8, 5), date(2026, 8, 6), _cost_master(db), VENDOR)
    assert row["commission_basis"] == "rate_unknown"
    assert row["commission_axis"] == "recognition_date", \
        "요율을 못 재면 물류비 커버리지가 100%여도 판매일 축을 내면 안 된다"


def test_in_progress_cycle_seed_survives_clock_advance(db, monkeypatch):
    """★시계를 10년 앞으로 돌려도 「진행 중 주기」 시드는 여전히 진행 중이어야 한다.

    **왜 이 테스트가 있나 — 「오늘 초록」이 이 결함을 못 잡았기 때문이다.**
    2026-09-01 00:00 KST에 이 파일이 실제로 터졌다: 「진행 중」을 뜻하던 고정 시드
    `dto=date(2026, 8, 31)`이 그 순간 «완결»로 넘어가 위 두 테스트가 `rate_unknown`
    대신 `settled_rate`를 받았고, main을 포함해 **그날 도는 모든 브랜치의 CI가 멈췄다**
    (PR #620이 그 때문에 머지 못 하고 반착지했다). 터지기 전날까지 전건 초록이었다 —
    즉 회귀망이 «시각»을 한 번도 안 재고 있었다.

    그래서 여기서는 **시계를 실제로 옮긴다**. 소스(`rg_sales_date_fees.date`)와 이 파일의
    `date`를 같이 갈아끼워야 한다 — 진짜로 날이 바뀌면 둘 다 함께 움직이므로, 한쪽만
    옮기면 현실에 없는 상태를 재게 된다.

    누가 `_open_cycle_end()`를 다시 고정 날짜로 되돌리면 이 테스트는 **그 커밋에서 즉시**
    빨개진다 — 다음 자정이 아니라.
    """
    import sys

    from app.services.coupang import rg_sales_date_fees as _fees_mod

    # ★값을 «패치 전에» 굳힌다 — 패치 후 `date.today()`를 부르면 이 클래스 자신을 다시 타 무한재귀다.
    _fake_today = date(date.today().year + 10, 1, 15)

    class _FutureDate(date):
        @classmethod
        def today(cls):
            return _fake_today

    monkeypatch.setattr(_fees_mod, "date", _FutureDate)
    monkeypatch.setattr(sys.modules[__name__], "date", _FutureDate)

    for d in (date(2026, 8, 5), date(2026, 8, 6)):
        _seed_summary(db, d, 50_000, 5)
        _seed_option(db, d, "RG1", 50_000, 5)
    _seed_catalog(db, "RG1"); _seed_rg_inventory(db, "RG1")
    _seed_cost(db, "RG1", 2_000)
    _seed_account_fee(db, "sale_fee", 8_000, dfrom=date(2026, 8, 1), dto=_open_cycle_end())
    db.commit()

    row = compute_rg_summary_row(db, ACC, date(2026, 8, 5), date(2026, 8, 6), _cost_master(db),
                                 VENDOR)
    assert row["commission_basis"] == "rate_unknown", \
        "시계가 10년 흘러도 이 시드는 「진행 중」이어야 한다 — 고정 날짜로 되돌린 순간 여기서 잡힌다"
    assert row["commission_axis"] == "recognition_date"


def test_settlement_reconcile_diff_stays_none_without_completed_cycle(db):
    """M16 — `reconciliation`이 없을 때(None) 화면 자백 칸을 "0"으로 채우면 빨개진다.

    완결 주기가 아예 없으면 `settlement_reconcile_diff`는 **None**이어야 한다 — 0은
    「맞았다」로 읽히는데 여긴 애초에 잰 것이 없다.
    """
    for d in (date(2026, 8, 5), date(2026, 8, 6)):
        _seed_summary(db, d, 50_000, 5)
        _seed_option(db, d, "RG1", 50_000, 5)
    _seed_catalog(db, "RG1"); _seed_rg_inventory(db, "RG1")
    _seed_cost(db, "RG1", 2_000)
    # RG 정산 원장 row가 아예 없다 → 완결 주기 0개 → reconciliation 자체가 None.
    db.commit()

    row = compute_rg_summary_row(db, ACC, date(2026, 8, 5), date(2026, 8, 6), _cost_master(db), VENDOR)
    assert row is not None
    assert row["settlement_reconcile_diff"] is None, \
        "완결 주기가 없으면 diff는 None이어야 한다 — '0' 문자열로 채우면 안 된다"
    assert row["settlement_reconcile_cycle"] is None
    assert row["settlement_reconcile_pct"] is None


def test_rg_settlement_axis_falls_back_when_rate_unknown_in_command_center(db):
    """M18 — `intelligence`의 `rg_settlement_axis`를 항상 "sales_date"로 바꾸면 빨개진다.

    단가는 다 알아 물류비 커버리지가 100%인데(RG1 단가 기재) 요율을 잴 완결 주기가 아예
    없는 창이다 — `compute_command_center`도 대시보드(`rg_channel_pnl`)와 **같은 규칙**으로
    원장 축("recognition_date")으로 물러서야 한다(D-CPP-47이 고쳤던, 두 화면이 같은 계정을
    다른 금액으로 빼는 병의 재발 방지).
    """
    _seed_summary(db, date(2026, 8, 15), 50_000, 5)
    _seed_option(db, date(2026, 8, 15), "RG1", 50_000, 5)
    _seed_option_fee(db, "RG1", "delivery", 500, 5, dfrom=date(2026, 8, 1), dto=date(2026, 8, 7))
    # sale_fee 원장 row가 아예 없다 → 완결 주기 0개 → rate=None.
    db.commit()

    cc = compute_command_center(db, date(2026, 8, 15), date(2026, 8, 15), account=ACC)
    s = cc["account"]["summary"]
    assert s["rg_fee_basis"] == "rate_unknown"
    assert s["rg_settlement_axis"] == "recognition_date", \
        "요율을 못 재면 판매일 축을 내면 안 된다 — 항상 sales_date이면 안 된다"


# ════════════════════════════════════════════════
# 13. 2R NEW P1 변이 5종 보강 (2026-08-23, 위임 세션)
#
# 적대 리뷰 2R이 보고: 우연코드 P1 2건(커버리지 분모 «화면이 싣는 매출» · flip_status
# «실제 차감 기준»)을 고치면서 추가된 테스트는 0건이었고, 그 3줄(rg_channel_pnl.py 호출부 1줄·
# intelligence.py 호출부 1줄·intelligence.py flip_status 1줄)에 대한 5종 변이가 전건 생존했다.
# 아래 4개(M1·M2·M3/M4·M6)가 백엔드 몫이다. M5(프론트 표면)는 rgSettlementAxisSurface.test.tsx.
# ════════════════════════════════════════════════

def test_compute_rg_summary_row_wires_revenue_reference_into_coverage(db):
    """M1 — `rg_channel_pnl.compute_rg_summary_row`의 «호출부 제거» — `sales_date_fees(...)`에
    `revenue_reference=revenue`를 안 넘기면(함수 자체는 그대로 살아 있다) 빨개진다.

    옵션축(60,000, 전부 단가 앎)이 요약축(100,000)보다 작은 창을 심는다. 호출부가
    `revenue_reference`를 넘기면 분모가 100,000이 되어 커버리지 60%·게이트 미달로
    원장 축 폴백이 나온다. 인자를 빼면 분모가 옵션축 자기 자신(60,000)이 되어 커버리지가
    거짓으로 100%가 되고 §4 ⓔ 게이트가 침묵한다 — 적대 리뷰 1R P1-1이 잡았던 바로 그 결손이
    호출부만 지워도 재발한다는 것을 이 테스트가 보인다.
    """
    win = (date(2026, 8, 15), date(2026, 8, 15))
    _seed_account_fee(db, "sale_fee", 8_000, dfrom=date(2026, 8, 1), dto=date(2026, 8, 7))
    _seed_summary(db, date(2026, 8, 3), 100_000, 10)   # 완결 주기 요율 8%

    _seed_option_fee(db, "RG1", "delivery", 500, 5, dfrom=date(2026, 8, 8), dto=date(2026, 8, 14))
    _seed_summary(db, win[0], 100_000, 10)              # 요약축(호출부가 화면에 싣는 매출)
    _seed_option(db, win[0], "RG1", 60_000, 6)          # 옵션축 — 요약축보다 작다(전부 단가는 앎)
    db.commit()

    row = compute_rg_summary_row(db, ACC, *win, _cost_master(db), VENDOR)
    assert Decimal(row["fee_coverage"]) == Decimal("0.6000"), \
        "분모가 요약축(100,000)이어야 60%가 나온다 — 옵션축(60,000)만 쓰면 100%로 거짓 완전해진다"
    assert row["commission_axis"] == "recognition_date", \
        "커버리지 60%는 임계(95%) 미달이라 판매일 축을 내면 안 된다"
    assert row["fee_unmapped_revenue"] == "40000"


def test_compute_command_center_wires_revenue_reference_into_coverage(db):
    """M2 — `intelligence.compute_command_center`의 «호출부 제거» — `sales_date_fees(...)`에
    `revenue_reference=_rg_rev_ref`를 안 넘기면 빨개진다. M1과 같은 시드·같은 결손을
    종합조망 진입점에서 검사한다 — D-CPP-47이 고쳤던 「두 화면이 같은 계정을 다른 금액으로
    잰다」 병이 이 두 호출부 중 하나만 지워져도 재발한다.
    """
    win = (date(2026, 8, 15), date(2026, 8, 15))
    _seed_account_fee(db, "sale_fee", 8_000, dfrom=date(2026, 8, 1), dto=date(2026, 8, 7))
    _seed_summary(db, date(2026, 8, 3), 100_000, 10)

    _seed_option_fee(db, "RG1", "delivery", 500, 5, dfrom=date(2026, 8, 8), dto=date(2026, 8, 14))
    _seed_summary(db, win[0], 100_000, 10)
    _seed_option(db, win[0], "RG1", 60_000, 6)
    db.commit()

    cc = compute_command_center(db, *win, account=ACC)
    s = cc["account"]["summary"]
    assert s["rg_fee_coverage"] == Decimal("0.6"), \
        "종합조망도 같은 분모(요약축 100,000)를 써야 한다"
    assert s["rg_settlement_axis"] == "recognition_date", \
        "종합조망도 같은 규칙 — 커버리지 60%(임계 미달)면 원장 축으로 물러서야 한다"
    assert s["rg_fee_unmapped_revenue"] == Decimal("40000")


def test_sales_date_fees_denominator_uses_revenue_reference(db):
    """M3 — `sales_date_fees`가 `revenue_reference`를 인자로 받되 분모 계산에서 무시하면
    (`denom`이 항상 `revenue_total`이면) 빨개진다.

    옵션축(60,000, 전부 단가 앎)보다 큰 요약축(100,000)을 직접 넘긴다. denom이
    `revenue_reference`를 반영하면 커버리지는 60%(100,000분의 60,000)여야 한다 —
    revenue_total(60,000)로만 나누면 옵션축이 자기 자신과 나뉘어 거짓으로 100%가 나온다.
    """
    _seed_option_fee(db, "RG1", "delivery", 500, 5)   # 100원/개
    _seed_option(db, date(2026, 8, 15), "RG1", 60_000, 6)
    db.commit()

    fees = sales_date_fees(db, ACC, date(2026, 8, 15), date(2026, 8, 15),
                           revenue_reference=Decimal("100000"), reconcile=False)
    assert fees["revenue_total"] == Decimal("60000")
    assert fees["revenue_priced"] == Decimal("60000")
    assert fees["coverage"] == Decimal("0.6"), \
        "denom이 revenue_reference(100,000)를 반영해야 한다 — revenue_total(60,000)로 재면 1.0이 나온다"


def test_unmapped_revenue_counts_axis_gap_beyond_unpriced_options(db):
    """M4 — `sales_date_fees`의 `unmapped_revenue` 재계산 블록
    (`if revenue_reference is not None: unmapped_revenue = max(ZERO, denom - revenue_priced)`)이
    삭제되면 빨개진다.

    두 종류의 결손을 함께 심는다: ①옵션축 «안»에 있지만 단가를 모르는 옵션(UNK, 10,000)
    ②옵션축에 아예 없는 매출(요약축에만 있는 몫 — revenue_reference 100,000 중 옵션축 합
    70,000을 넘는 30,000). 블록이 복원해야 하는 것은 **둘의 합(40,000)**이다 — 옵션 루프만
    남으면(블록이 삭제되면) ①(10,000)만 자백되고 ②는 조용히 사라진다.
    """
    _seed_option_fee(db, "RG1", "delivery", 500, 5)   # 100원/개
    _seed_option(db, date(2026, 8, 15), "RG1", 60_000, 6)     # 단가 앎
    _seed_option(db, date(2026, 8, 15), "UNK", 10_000, 1)     # 단가 모름(옵션축 안 결손 ①)
    db.commit()

    fees = sales_date_fees(db, ACC, date(2026, 8, 15), date(2026, 8, 15),
                           revenue_reference=Decimal("100000"), reconcile=False)
    assert fees["revenue_total"] == Decimal("70000")
    assert fees["revenue_priced"] == Decimal("60000")
    assert fees["unmapped_revenue"] == Decimal("40000"), \
        "옵션축 안 결손(10,000) + 옵션축 밖 결손(30,000) = 40,000이어야 한다 — " \
        "루프만 남으면(블록 삭제) 10,000만 남는다"


def test_flip_status_reflects_actual_deduction_not_ledger_row_presence(db):
    """M6 — `intelligence.py`의 `rg_flip_status`가 `len(rg_fees) > 0`(정산 원장 row의 유무)으로
    되돌아가면 빨개진다. `rg_settlement.summary`의 `has_data`·`flip_status`·`deducted`도
    같은 규칙을 따라야 한다(2R 회귀4 「쌍둥이 칸」 — 하나만 고치면 같은 화면이 서로 다른
    차감 여부를 말한다).

    주기 롤오버 시나리오: 요율·단가는 «옛 완결 주기»(08-01~08-14)에서 왔고, 조회 창(08-15)에
    겹치는 정산 원장 row는 **0건**이다(`rg_fees` 비어 있음). 그런데 판매일 축은 옛 요율·단가로
    여전히 값을 낸다(`rg_deducted=4,550`). 실제로 차감했으면 원장 row가 없어도 「반영됨」이라고
    말해야 한다 — 원장 row 유무가 아니라 **실제 차감 여부**가 기준이다(적대 리뷰 1R P1-2).
    """
    win = (date(2026, 8, 15), date(2026, 8, 15))
    _seed_account_fee(db, "sale_fee", 8_000, dfrom=date(2026, 8, 1), dto=date(2026, 8, 7))
    _seed_summary(db, date(2026, 8, 3), 100_000, 10)   # 완결 주기 요율 8%
    _seed_option_fee(db, "RG1", "delivery", 500, 5, dfrom=date(2026, 8, 8), dto=date(2026, 8, 14))

    # 조회 창(08-15) — 겹치는 정산 원장 row는 하나도 없다. 그런데 판매는 있었다.
    _seed_summary(db, win[0], 50_000, 5)
    _seed_option(db, win[0], "RG1", 50_000, 5)
    db.commit()

    cc = compute_command_center(db, *win, account=ACC)
    s = cc["account"]["summary"]
    rg = cc["rg_settlement"]["summary"]

    assert s["rg_settlement_axis"] == "sales_date", \
        "이 창은 요율·단가를 둘 다 알아 판매일 축을 낼 수 있어야 한다"
    deducted = s["rg_settlement_deducted"]
    assert deducted == Decimal("4550"), "물류비 550 + 수수료 4,000 + 기간비용 0"

    assert s["rg_flip_status"] == "applied_ex_ad", \
        "원장 row 유무가 아니라 실제 차감 여부(rg_deducted != 0)로 판정해야 한다"
    assert rg["flip_status"] == "applied_ex_ad"
    assert rg["has_data"] is True, "차감이 일어났으면 has_data도 True여야 한다(쌍둥이 칸)"
    assert rg["deducted"] == deducted, \
        "rg_settlement.summary.deducted는 account_sum.rg_settlement_deducted와 같은 값이어야 한다"
    # 원장 축 값(non_ad_deducted)은 이 창에 겹치는 원장이 없으므로 0이다 — 판매일 축 값(위)과
    # 실제로 분리돼 있는지 확인한다(둘을 합쳐 하나로 되돌리면 이 부등식이 깨진다).
    assert rg["non_ad_deducted"] == _Z
    assert deducted != rg["non_ad_deducted"]
