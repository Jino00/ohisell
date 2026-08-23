# test_rg_daily_pnl.py — RG «상품(옵션) 단위 일별 손익» 조립 (CONTRACT_2p_own_screens §1-A-4).
#
# 무엇을 못 박는가:
#   1. 옵션 행 + 계정 공통 행의 합이 `compute_rg_summary_row(...)`의 net_profit과 **원 단위로**
#      일치한다(보존식, 계약 §4 ⓓ) — 두 축(요약/옵션)이 갈리는 창에서도.
#   2. 광고만 돌고 매출이 없는 옵션도 옵션 행에 «음의 순이익»으로 나타난다(누락 금지).
#   3. 원가 커버리지 게이트가 미달이면 옵션·계정 공통 어디에도 net_profit이 없다(추정 금지).
#   4. 수수료 축이 원장으로 물러선 창에서는 옵션 행에 fee 분해·net_profit을 안 싣고, 원장
#      수수료 전액이 `fee_axis_fallback_gap`으로 간다 — 그래도 보존식은 원 단위로 맞는다.
#   5. 상품 행과 계정 공통 행이 섞이지 않는다(보관비·미배분 광고비는 옵션 행에 없다).
#
# 시딩 헬퍼는 test_rg_net_channel_pnl.py·test_rg_sales_date_fees.py를 그대로 베꼈다(같은 스키마).
# 라이브 API 호출 없음. 인메모리 SQLite.
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    Channel,
    CoupangAdOptionDaily,
    CoupangProductItem,
    CoupangRgInventory,
    CoupangRgSettlementFee,
    CoupangVendorItemSalesDaily,
    CoupangVendorSummaryDaily,
    ProductChannelMapping,
    ProductMaster,
)
from app.services.coupang.rg_channel_pnl import compute_rg_summary_row
from app.services.coupang.rg_daily_pnl import _conservation_diff, rg_option_pnl

_Z = Decimal(0)
ACC = "COUPANG_WING1"
VENDOR = "A01564720"
WIN = (date(2026, 8, 5), date(2026, 8, 6))


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


def _seed_ad(db, vid, spend, *, day=date(2026, 8, 5), sell_type="3P"):
    db.add(CoupangAdOptionDaily(report_date=day, vendor_id=VENDOR, sell_type=sell_type,
                                ad_option_id=vid, conv_option_id=vid,
                                ad_spend=Decimal(str(spend))))


def _seed_fee(db, fee_type, amount, *, acc=ACC,
              dfrom=date(2026, 8, 1), dto=date(2026, 8, 31)):
    db.add(CoupangRgSettlementFee(account_key=acc, recognition_date_from=dfrom,
                                  recognition_date_to=dto, fee_type=fee_type,
                                  vendor_item_id="", amount=Decimal(str(amount))))


def _seed_fee_option(db, fee_type, vid, amount, qty, *, acc=ACC,
                     dfrom=date(2026, 7, 27), dto=date(2026, 8, 3)):
    db.add(CoupangRgSettlementFee(account_key=acc, recognition_date_from=dfrom,
                                  recognition_date_to=dto, fee_type=fee_type,
                                  vendor_item_id=vid, amount=Decimal(str(amount)),
                                  billed_quantity=qty))


def _seed_sales_date_inputs(db, *, vid="RG1", unit_amount=2_000, unit_qty=10,
                            cycle_sale_fee=10_000, cycle_gmv=100_000):
    """판매일 축 정산공제의 입력 두 벌 — test_rg_net_channel_pnl.py와 동일."""
    _seed_summary(db, date(2026, 7, 30), cycle_gmv, 10)
    _seed_fee(db, "sale_fee", cycle_sale_fee, dfrom=date(2026, 7, 27), dto=date(2026, 8, 3))
    _seed_fee_option(db, "delivery", vid, unit_amount, unit_qty)


def _seed_cost(db, vid, cost, pid=1):
    db.add(ProductMaster(id=pid, internal_sku=f"SKU{pid}", product_name=f"P{pid}",
                         cost_price=Decimal(str(cost))))
    db.add(ProductChannelMapping(channel_id=3, product_id=pid,
                                 channel_product_id=vid, is_active=True))


def _cost_master(db):
    from app.services.coupang.intelligence import _cost_master as cm
    return cm(db)


def _by_vid(result):
    return {o["vendor_item_id"]: o for o in result["options"]}


def _assert_conservation(result):
    """보존식이 원 단위로 맞는지 — ok가 명시적으로 True/None인지까지 본다(공허한 >=0 금지)."""
    c = result["conservation"]
    if c["reference_net"] is None:
        assert c["ok"] is None, "reference_net이 없으면 ok도 판정불능(None)이어야 한다"
        return
    assert c["ok"] is True, f"보존식이 안 맞는다: {c}"
    assert Decimal(c["diff"]) == _Z


# ════════════════════════════════════════════════
# 1. 기본 — 두 게이트 통과, 단일 옵션
# ════════════════════════════════════════════════
def test_basic_option_row_and_conservation(db):
    """두 게이트(원가·수수료)가 다 통과하면 옵션 행이 값을 내고 보존식이 정확히 맞는다.

    시드는 test_rg_net_channel_pnl.test_rg_row_full_net_when_cost_trustworthy와 같다:
      물류비 = 200원(VAT前) × 10개 × 1.1 = 2,200원 · 수수료 = 100,000 × 10% = 10,000원
    """
    for d in (date(2026, 8, 5), date(2026, 8, 6)):
        _seed_summary(db, d, 50_000, 5)
        _seed_option(db, d, "RG1", 50_000, 5)
    _seed_catalog(db, "RG1"); _seed_rg_inventory(db, "RG1")
    _seed_cost(db, "RG1", 2_000)
    _seed_sales_date_inputs(db)
    _seed_ad(db, "RG1", 1_000)
    db.commit()

    result = rg_option_pnl(db, ACC, *WIN, _cost_master(db), VENDOR)
    assert result["cost_trustworthy"] is True
    assert result["fee_trustworthy"] is True
    assert result["commission_axis"] == "sales_date"

    rows = _by_vid(result)
    assert set(rows) == {"RG1"}
    row = rows["RG1"]
    assert Decimal(row["revenue"]) == Decimal("100000")
    assert Decimal(row["cost"]) == Decimal("20000")            # 10개 × 2,000
    assert Decimal(row["fee_logistics"]) == Decimal("2200")
    assert Decimal(row["fee_sale_fee"]) == Decimal("10000")
    assert Decimal(row["fee_total"]) == Decimal("12200")
    assert Decimal(row["ad_spend"]) == Decimal("1000")
    expected_net = Decimal("100000") - Decimal("20000") - Decimal("12200") - Decimal("1000")
    assert Decimal(row["net_profit"]) == expected_net

    # 상품 행과 계정 공통 행이 섞이지 않는다 — 보관비 없는 창이라 period_fees=0
    common = result["account_common"]
    assert Decimal(common["period_fees"]) == _Z
    assert Decimal(common["revenue_axis_gap"]) == _Z, "옵션축이 창을 다 덮으면 갭이 0이어야 한다"

    _assert_conservation(result)

    # 대시보드 행과 동일한 값을 재현(레퍼런스 직접 호출)
    ref = compute_rg_summary_row(db, ACC, *WIN, _cost_master(db), VENDOR)
    assert Decimal(result["conservation"]["reference_net"]) == Decimal(ref["net_profit"])


# ════════════════════════════════════════════════
# 2. 광고만 돌고 매출이 없는 옵션 — 누락되면 안 된다
# ════════════════════════════════════════════════
def test_ad_only_option_appears_as_negative_row(db):
    """RG로 귀속되는 광고비가 있는데 그 창에 판매가 0인 옵션도 옵션 행에 나타나야 한다.

    안 그러면 그 광고비만큼 옵션 합계가 계정 광고비(`split_wing_ad_spend`)보다 작아져
    보존식이 깨진다(§4 ⓓ).
    """
    for d in (date(2026, 8, 5), date(2026, 8, 6)):
        _seed_summary(db, d, 50_000, 5)
        _seed_option(db, d, "RG1", 50_000, 5)
    _seed_catalog(db, "RG1"); _seed_rg_inventory(db, "RG1")
    _seed_cost(db, "RG1", 2_000)
    _seed_sales_date_inputs(db)
    _seed_ad(db, "RG1", 1_000)
    # AD_ONLY — 카탈로그·RG재고에는 있지만(=RG로 귀속) 이 창에 판매 이력이 없다
    _seed_catalog(db, "AD_ONLY"); _seed_rg_inventory(db, "AD_ONLY")
    _seed_ad(db, "AD_ONLY", 300)
    db.commit()

    result = rg_option_pnl(db, ACC, *WIN, _cost_master(db), VENDOR)
    rows = _by_vid(result)
    assert "AD_ONLY" in rows, "광고만 돌고 매출이 없는 옵션도 행에 나타나야 한다"
    ad_only = rows["AD_ONLY"]
    assert Decimal(ad_only["revenue"]) == _Z
    assert Decimal(ad_only["ad_spend"]) == Decimal("300")
    assert Decimal(ad_only["net_profit"]) == Decimal("-300"), "매출 없이 광고비만큼 손실이어야 한다"

    _assert_conservation(result)


# ════════════════════════════════════════════════
# 3. 원가 게이트 미달 — 추정하지 않는다
# ════════════════════════════════════════════════
def test_cost_gate_failure_yields_no_net_anywhere(db):
    """옵션축이 창을 못 덮으면(원가 게이트 미달) 옵션·계정 공통 어디에도 net이 없다.

    시드는 test_rg_net_channel_pnl.test_rg_row_withholds_net_when_option_axis_incomplete와 같다.
    """
    _seed_summary(db, date(2026, 8, 5), 50_000, 5)
    _seed_summary(db, date(2026, 8, 6), 50_000, 5)
    _seed_option(db, date(2026, 8, 5), "RG1", 50_000, 5)   # 옵션축은 하루뿐
    _seed_catalog(db, "RG1"); _seed_rg_inventory(db, "RG1")
    _seed_cost(db, "RG1", 2_000)
    _seed_ad(db, "RG1", 1_000)
    db.commit()

    result = rg_option_pnl(db, ACC, *WIN, _cost_master(db), VENDOR)
    assert result["cost_trustworthy"] is False
    rows = _by_vid(result)
    assert rows["RG1"]["net_profit"] is None
    assert rows["RG1"]["cost"] is None
    assert result["account_common"]["payable_vat"] is None
    c = result["conservation"]
    assert c["options_net_sum"] is None
    assert c["computed_total_net"] is None
    assert c["reference_net"] is None, "레퍼런스 행도 원가 게이트로 net=None이어야 한다"
    assert c["ok"] is None, "판정불능이지 미달(False)이 아니다 — 공허 단언 금지"


# ════════════════════════════════════════════════
# 4. 수수료 축 폴백 — 옵션 행에 분해 없음, 계정 공통이 전액 흡수
# ════════════════════════════════════════════════
def test_fee_axis_fallback_absorbs_into_account_common(db):
    """요율을 못 재면(완결 주기 없음) 옵션 행에 fee·net을 안 싣고, 원장 수수료 전액이
    `fee_axis_fallback_gap`으로 간다 — 그래도 보존식은 원 단위로 맞는다.
    """
    for d in (date(2026, 8, 5), date(2026, 8, 6)):
        _seed_summary(db, d, 50_000, 5)
        _seed_option(db, d, "RG1", 50_000, 5)
    _seed_catalog(db, "RG1"); _seed_rg_inventory(db, "RG1")
    _seed_cost(db, "RG1", 2_000)
    # 완결 주기 없음(진행 중) → rate=None → 수수료 축 폴백. 원가 게이트는 정상.
    _seed_fee(db, "sale_fee", 8_000, dfrom=date(2026, 8, 1), dto=date(2026, 8, 31))
    db.commit()

    result = rg_option_pnl(db, ACC, *WIN, _cost_master(db), VENDOR)
    assert result["cost_trustworthy"] is True
    assert result["fee_trustworthy"] is False
    assert result["commission_axis"] == "recognition_date"

    rows = _by_vid(result)
    row = rows["RG1"]
    assert row["fee_logistics"] is None
    assert row["fee_sale_fee"] is None
    assert row["fee_total"] is None
    assert row["net_profit"] is None, "원장 축에서는 옵션 단위 net을 내지 않는다"
    assert Decimal(row["cost"]) == Decimal("20000"), "원가는 여전히 실측값이 실린다(다른 게이트)"

    common = result["account_common"]
    assert Decimal(common["fee_axis_fallback_gap"]) == Decimal("8000"), \
        "원장 수수료 전액이 계정 공통 행으로 흡수돼야 한다"
    assert Decimal(common["period_fees"]) == _Z, "원장 축 폴백에서는 기간비용을 따로 안 낸다"

    _assert_conservation(result)
    ref = compute_rg_summary_row(db, ACC, *WIN, _cost_master(db), VENDOR)
    assert ref["net_profit"] is not None, "레퍼런스는 원가 게이트만 통과하면 net을 낸다(축만 물러섰다)"
    assert Decimal(result["conservation"]["reference_net"]) == Decimal(ref["net_profit"])


# ════════════════════════════════════════════════
# 5b. 기간비용(보관비) — 옵션 행에 절대 섞이면 안 된다
# ════════════════════════════════════════════════
def test_period_fee_stays_in_account_common_not_option_rows(db):
    """보관비(storage)가 있는 창 — `period_fees`가 계정 공통 행에만 있고 옵션 행의
    fee_total에는 섞이지 않는다(§4 ⓒ — 물류비·수수료는 상품 행, 기간비용은 계정 공통 행).

    ★이 테스트가 없으면 「period_fees를 옵션 행에 배분」 변이가 `period_fees==0`인 다른
    시드에서는 조용히 살아남는다(가산값이 0이면 변형해도 값이 안 바뀐다) — 그래서 여기서는
    반드시 storage>0인 시드로 재현한다.
    """
    _seed_full_gate_pass(db)
    # 정산주기 07-27~08-03에 storage 700원 — sales_date_inputs와 같은 주기라 창(08-05~06)과
    # 안 겹치지만(일할 기준 겹침 0), period_fees는 **`date_from~date_to` 창** 기준이므로
    # 창과 겹치는 별도 주기로 심어야 실제로 0이 아닌 값이 나온다.
    _seed_fee(db, "storage", 700, dfrom=date(2026, 8, 1), dto=date(2026, 8, 7))
    db.commit()

    result = rg_option_pnl(db, ACC, *WIN, _cost_master(db), VENDOR)
    period = Decimal(result["account_common"]["period_fees"])
    assert period != _Z, "이 시드는 storage가 창(08-05~06)과 겹쳐 period_fees가 0이 아니어야 한다"

    row = _by_vid(result)["RG1"]
    # 옵션 행의 fee_total은 물류비(2,200)+수수료(10,000)만이어야 한다 — 기간비용이 안 섞인다.
    assert Decimal(row["fee_total"]) == Decimal("2200") + Decimal("10000")

    _assert_conservation(result)


# ════════════════════════════════════════════════
# 5. 매출 축 갭 — 요약축이 옵션축보다 큰 창
# ════════════════════════════════════════════════
def test_revenue_axis_gap_is_confessed_not_hidden(db):
    """요약축 매출이 옵션축 합보다 크면 그 차액이 `revenue_axis_gap`으로 자백돼야 한다
    (0으로 숨기지 않는다, §4 ⓔ 원칙과 같은 결).
    """
    for d in (date(2026, 8, 5), date(2026, 8, 6)):
        _seed_summary(db, d, 60_000, 6)          # 요약축 = 120,000
    _seed_option(db, date(2026, 8, 5), "RG1", 50_000, 5)   # 옵션축 = 50,000뿐(하루만, 08-06 없음)
    _seed_catalog(db, "RG1"); _seed_rg_inventory(db, "RG1")
    _seed_cost(db, "RG1", 2_000)
    _seed_ad(db, "RG1", 1_000)
    db.commit()

    result = rg_option_pnl(db, ACC, *WIN, _cost_master(db), VENDOR)
    # 원가 게이트도 미달이라(옵션축이 창을 못 덮음) net은 안 나지만, revenue_axis_gap은
    # 게이트와 무관하게 항상 계산·자백돼야 한다.
    gap = Decimal(result["account_common"]["revenue_axis_gap"])
    assert gap == Decimal("120000") - Decimal("50000")
    assert gap != _Z


def _seed_full_gate_pass(db):
    for d in (date(2026, 8, 5), date(2026, 8, 6)):
        _seed_summary(db, d, 50_000, 5)
        _seed_option(db, d, "RG1", 50_000, 5)
    _seed_catalog(db, "RG1"); _seed_rg_inventory(db, "RG1")
    _seed_cost(db, "RG1", 2_000)
    _seed_sales_date_inputs(db)
    _seed_ad(db, "RG1", 1_000)


# ════════════════════════════════════════════════
# 5c. 미배분 광고비 — 보존식에서 제외된다(대시보드 행에 안 실리는 돈)
# ════════════════════════════════════════════════
def test_ad_unallocated_is_informational_and_excluded_from_conservation(db):
    """카탈로그에도 판매 이력에도 없는 옵션(GHOST)에 쓴 광고비는 `ad_unallocated`로
    자백되지만, 그 돈은 **어느 채널 행에도 안 실리는 돈**이라(`rg_channel_pnl` 주석과 같은
    규율) 보존식(§4 ⓓ)에서 빼면 안 된다 — 대시보드 RG 행 자체가 그 돈을 안 빼기 때문이다.
    """
    _seed_full_gate_pass(db)
    _seed_ad(db, "GHOST", 500)   # 카탈로그에 없다 → 미배분
    db.commit()

    result = rg_option_pnl(db, ACC, *WIN, _cost_master(db), VENDOR)
    unalloc = Decimal(result["account_common"]["ad_unallocated"])
    assert unalloc == Decimal("500"), "미배분 광고비가 정확히 자백돼야 한다"
    assert result["account_common"]["ad_unallocated_options"] == 1

    rows = _by_vid(result)
    assert "GHOST" not in rows, "미배분 옵션은 어느 채널로도 귀속 안 됐으니 RG 옵션 행이 아니다"

    # 대시보드 RG 행 자체가 이 500원을 안 뺀다 — 그러니 보존식도 이 500원과 무관하게 맞아야 한다.
    _assert_conservation(result)


# ════════════════════════════════════════════════
# 5d. 보존식 diff — 순수 함수 단위 테스트(변이 7 봉쇄)
# ════════════════════════════════════════════════
def test_conservation_diff_helper_computes_real_subtraction():
    """`_conservation_diff`가 «진짜 뺄셈」인지 — 일부러 어긋난 두 값을 넣어 확인한다.

    ★이 테스트가 없으면 「diff를 항상 0으로 반환」하는 변이가, 정직한 조립 경로(항상 0을
    내는 것이 정상이다)와 **어떤 정상 입력에서도 구별되지 않는다.** 그래서 여기서는 조립을
    거치지 않고 헬퍼를 직접 불러 일부러 다른 두 값을 넣는다.
    """
    diff, ok = _conservation_diff(Decimal("100"), Decimal("90"))
    assert diff == Decimal("10"), "10이어야 한다 — 0으로 하드코딩되면 이 값이 안 나온다"
    assert ok is False

    diff2, ok2 = _conservation_diff(Decimal("50"), Decimal("50"))
    assert diff2 == _Z
    assert ok2 is True


def test_conservation_diff_helper_none_is_undecidable():
    diff, ok = _conservation_diff(None, Decimal("100"))
    assert diff is None and ok is None
    diff, ok = _conservation_diff(Decimal("100"), None)
    assert diff is None and ok is None


# ════════════════════════════════════════════════
# 5d-2. 결함 A 회귀 — prod 08-21 「완전 일치인데 ⚠️ 어긋남」 (2026-08-23)
# ════════════════════════════════════════════════
def test_conservation_diff_sub_won_residue_is_ok():
    """★결함 A 회귀 — 요율 곱셈·VAT 나눗셈 사슬이 남기는 극미 잔차는 「일치」다.

    prod 08-21 실측: 대시보드와 상품 행 소계가 소수점까지 같은데도 배지가 「⚠️ 어긋남」을
    단정했다. 원인은 옛 판정 `diff == ZERO`가 극미 잔차를 0이 아니라고 본 것 — 계약 §4 ⓓ
    문언은 「원 단위 일치」이므로 판정도 그 단위로 해야 한다.

    ★잔차 자릿수는 `1E-20`으로 둔다(1E-25가 아니다) — 기준값 `53803.497`(8자리)에 1E-25를
    더하면 총 유효자리가 Decimal 기본 컨텍스트(prec=28)를 넘어 **덧셈 시점에 잔차 자체가
    반올림으로 소실**되어 이 테스트가 소스가 아니라 파이썬 Decimal 컨텍스트를 검사하게 된다
    (직접 확인: `Decimal("53803.497")+Decimal("1E-25")` → 잔차 소실). 1E-20이면 23자리로
    prec=28 안에 들어와 잔차가 그대로 보존된다 — 여전히 원 단위론 무의미한 극미값이다.
    """
    residue = Decimal("1E-20")
    diff, ok = _conservation_diff(Decimal("53803") + residue, Decimal("53803"))
    assert ok is True, "1E-20급 잔차는 원 단위로는 일치 — ok=True여야 한다"
    assert diff == residue, "diff 자체는 반올림 없이 원본 잔차 그대로여야 한다(드리프트 은폐 금지)"


def test_conservation_diff_exact_zero_is_ok():
    diff, ok = _conservation_diff(Decimal("100000"), Decimal("100000"))
    assert diff == _Z
    assert ok is True


def test_conservation_diff_one_won_is_not_ok():
    """1원 차이는 여전히 「어긋남」이어야 한다 — 임계가 원 단위 미만 잔차만 흡수한다."""
    diff, ok = _conservation_diff(Decimal("100001"), Decimal("100000"))
    assert diff == Decimal("1")
    assert ok is False


def test_conservation_diff_half_won_is_not_ok():
    """0.5원은 반올림 규칙(ROUND_HALF_UP)상 1원으로 올라가 「어긋남」이어야 한다."""
    diff, ok = _conservation_diff(Decimal("100000.5"), Decimal("100000"))
    assert diff == Decimal("0.5")
    assert ok is False


def test_conservation_diff_sub_half_won_is_ok():
    """0.4원은 반올림하면 0이 되어 「일치」여야 한다 — 0.5원(불일치)과의 경계 확인."""
    diff, ok = _conservation_diff(Decimal("100000.4"), Decimal("100000"))
    assert diff == Decimal("0.4")
    assert ok is True


# ════════════════════════════════════════════════
# 5e. 정본 대조는 «실제 호출»이어야 한다 — 값 비교로는 못 잡는 변이
# ════════════════════════════════════════════════
def test_reference_net_actually_calls_compute_rg_summary_row(db):
    """★변이 8b(필수) 봉쇄 — 「호출부 제거, 자기 계산값을 reference_net으로 씀」.

    ★왜 «값 비교»로는 이 변이를 못 잡는가: 조립이 정직하면 `computed_total_net`은 **정의상**
    `compute_rg_summary_row(...)`의 net_profit과 같아진다(그게 보존식의 목적이다) — 그러니
    호출을 지우고 `reference_net`을 `computed_total_net`으로 바꿔치기해도 **정상 입력에서는
    똑같은 숫자가 나온다.** 값만 비교하는 테스트는 이 변이를 원리적으로 못 잡는다(계약이 정확히
    예견한 상황: "자기 자신과 대조하면 언제나 0이다"). 그래서 여기서는 **호출 자체를 스파이**한다
    — `rg_channel_pnl.compute_rg_summary_row`가 실제로 불렸는지 확인한다.
    """
    _seed_full_gate_pass(db)
    db.commit()

    with patch(
        "app.services.coupang.rg_daily_pnl.compute_rg_summary_row",
        wraps=compute_rg_summary_row,
    ) as spy:
        result = rg_option_pnl(db, ACC, *WIN, _cost_master(db), VENDOR)

    assert spy.called, "KILLED — compute_rg_summary_row가 실제로 호출돼야 한다(자기 재계산 금지)"
    # 호출 인자도 화면이 쓰는 것과 같은 창·계정이어야 한다(엉뚱한 인자로 불러도 무의미하다).
    call_args = spy.call_args
    assert call_args.args[1] == ACC
    assert call_args.args[2] == WIN[0] and call_args.args[3] == WIN[1]
    assert Decimal(result["conservation"]["reference_net"]) == Decimal(
        compute_rg_summary_row(db, ACC, *WIN, _cost_master(db), VENDOR)["net_profit"]
    )


# ════════════════════════════════════════════════
# 6. 자백 필드가 실제로 채워진다
# ════════════════════════════════════════════════
def test_confession_fields_are_populated(db):
    _seed_full_gate_pass(db)
    db.commit()
    result = rg_option_pnl(db, ACC, *WIN, _cost_master(db), VENDOR)
    for field in ("rate", "rate_basis", "rate_cycles", "fee_coverage", "cost_coverage",
                  "option_axis_days", "option_axis_complete", "cost_trustworthy",
                  "fee_trustworthy", "reconciliation", "commission_axis"):
        assert field in result, f"{field}가 반환에서 빠졌다"
    assert result["option_axis_days"] == "2/2"
    assert result["rate_basis"] == "settled_rate"
