# test_rg_net_channel_pnl.py — RG 채널을 «콘솔 net 축»으로 대시보드에 올리는 머니코드 (D-CPP-47).
#
# 무엇을 못 박는가:
#   1. `get_rg_total_by_account`가 **`ad_sales`를 빼고** 합산한다 (D-CPP-43 — 대시보드만 폐기된
#      D-16에 남아 있던 것을 맞춘 것). 정산 광고비는 PA 광고비의 «공제»라 차감하면 이중계상이다.
#   2. 광고비를 가르는 축은 **옵션ID의 정체**이지 `sell_type` 라벨이 아니다. 쿠팡은 판매방식마다
#      옵션ID를 따로 발급하므로(Jino 2026-08-22 윙 화면 실물) 한 옵션이 3P·RG 양쪽일 수 없다.
#   3. 원가 커버리지 게이트가 **두 조건**을 본다 — 비율(≥95%)과 «옵션축이 창 전체를 덮는가».
#      후자가 없으면 창의 절반이 비어도 비율은 100%가 나와 **순이익이 위로 부푼다**.
#   4. 채널 leaf가 3P/RG/1P **세 줄로 갈린다**.
#
# 라이브 API 호출 없음. 인메모리 SQLite.
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

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
from app.services.coupang.rg_net_revenue import (
    net_cost,
    net_revenue_by_account,
    option_axis_coverage,
    option_sell_route,
    rg_channel_for_account,
    split_wing_ad_spend,
)
from app.services.profit_calculator import (
    _classify_channel,
    get_rg_ad_settlement_by_account,
    get_rg_total_by_account,
)

_Z = Decimal(0)
ACC = "COUPANG_WING1"
VENDOR = "A01564720"
WIN = (date(2026, 8, 5), date(2026, 8, 6))  # 2일 창 — 커버리지 테스트를 짧게 하려고


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
        (2, "COUPANG_WING2", "주식회사 오하이테크", "3P", "marketplace"),
        (3, "COUPANG_RG1", "개인회사 오픽스", "RG", "marketplace"),
        (4, "COUPANG_RG2", "주식회사 오하이테크", "RG", "marketplace"),
        (5, "COUPANG_ROCKET", "주식회사 오하이테크", "1P", "consignment"),
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


def _seed_cost(db, vid, cost, pid=1):
    db.add(ProductMaster(id=pid, internal_sku=f"SKU{pid}", product_name=f"P{pid}",
                         cost_price=Decimal(str(cost))))
    db.add(ProductChannelMapping(channel_id=3, product_id=pid,
                                 channel_product_id=vid, is_active=True))


def _cost_master(db):
    from app.services.coupang.intelligence import _cost_master as cm
    return cm(db)


# ════════════════════════════════════════════════
# 1. D-CPP-43 — ad_sales는 차감하지 않는다
# ════════════════════════════════════════════════
def test_rg_total_excludes_ad_sales(db):
    """정산 총액에서 `ad_sales`가 빠진다 — 그게 D-CPP-43이고, 대시보드만 D-16에 남아 있었다."""
    _seed_fee(db, "sale_fee", 100_000)
    _seed_fee(db, "delivery", 50_000)
    _seed_fee(db, "warehousing", 30_000)
    _seed_fee(db, "storage", 1_000)
    _seed_fee(db, "return_shipping", 2_000)
    _seed_fee(db, "ad_sales", 4_158_578)   # ← 이게 빠져야 한다
    db.commit()

    total = get_rg_total_by_account(db, *WIN)[ACC]
    assert total == Decimal("183000"), "sale_fee+풀필먼트3+반품 = 183,000이어야 한다"

    ad = get_rg_ad_settlement_by_account(db, *WIN)[ACC]
    assert ad == Decimal("4158578"), "광고 공제액은 «표시용»으로 따로 나와야 한다"


def test_fulfillment_three_components_are_independent(db):
    """풀필먼트 3컴포넌트는 서로 독립이라 합산해도 이중계상이 아니다 (ref 17 §8).

    ★`totalFulfillmentFeeDeductionAmount`는 「배송비」뿐이지 풀필먼트 «합계»가 아니다 —
      그걸 합계로 읽으면 입출고·보관이 통째로 사라진다.
    """
    _seed_fee(db, "delivery", 130_599)
    _seed_fee(db, "warehousing", 75_489)
    _seed_fee(db, "storage", 168)
    db.commit()
    assert get_rg_total_by_account(db, *WIN)[ACC] == Decimal("206256")  # ref 17 §7 실측 J


# ════════════════════════════════════════════════
# 2. 광고비 — 옵션ID의 «정체»로 가른다 (라벨 아님)
# ════════════════════════════════════════════════
def test_route_uses_option_identity_not_sales_history(db):
    """그 창에 **안 팔린** 옵션도 정체로 귀속된다 — 판매 이력으로 가르면 미배분으로 샌다."""
    _seed_catalog(db, "RG_UNSOLD")
    _seed_rg_inventory(db, "RG_UNSOLD")     # 로켓창고에 있다 = RG. 판매 이력은 없다.
    _seed_catalog(db, "P3_UNSOLD")          # 카탈로그에만 있다 = 3P(RG 표시 없음)
    db.commit()

    route = option_sell_route(db, ACC)
    assert route["RG_UNSOLD"] == "RG"
    assert route["P3_UNSOLD"] == "3P", "로켓그로스가 아닌 것은 판매자 배송이다"


def test_ad_split_sums_to_total_and_flags_uncatalogued(db):
    """세 버킷의 합 == 총액. 그리고 카탈로그에 없는 옵션은 **미배분으로 실토**된다."""
    _seed_catalog(db, "RG1"); _seed_rg_inventory(db, "RG1")
    _seed_catalog(db, "P31")
    _seed_ad(db, "RG1", 10_000)
    _seed_ad(db, "P31", 3_000)
    _seed_ad(db, "GHOST", 500)      # 카탈로그에 없다 — 상품 동기화가 밀렸다는 신호
    db.commit()

    ad = split_wing_ad_spend(db, *WIN, ACC, VENDOR)
    assert ad["rg"] == Decimal("10000")
    assert ad["p3"] == Decimal("3000")
    assert ad["unallocated"] == Decimal("500")
    assert ad["opt_unknown"] == 1
    assert ad["rg"] + ad["p3"] + ad["unallocated"] == ad["total"], "돈이 사라지면 안 된다"


def test_ad_split_ignores_sell_type_label(db):
    """`sell_type` 라벨이 3P여도 옵션이 RG면 RG로 간다 (D-CPP-43: 라벨은 판매경로가 아니다)."""
    _seed_catalog(db, "RG1"); _seed_rg_inventory(db, "RG1")
    _seed_ad(db, "RG1", 7_777, sell_type="3P")   # ← 라벨은 3P인데 실물은 RG
    db.commit()

    ad = split_wing_ad_spend(db, *WIN, ACC, VENDOR)
    assert ad["rg"] == Decimal("7777")
    assert ad["p3"] == _Z


# ════════════════════════════════════════════════
# 3. 매출·원가·커버리지
# ════════════════════════════════════════════════
def test_net_revenue_reads_summary_axis_rfm_only(db):
    _seed_summary(db, date(2026, 8, 5), 100_000, 10)
    _seed_summary(db, date(2026, 8, 6), 50_000, 5)
    _seed_summary(db, date(2026, 8, 5), 999_999, 99, rt="NORMAL")  # 3P는 안 섞인다
    db.commit()

    got = net_revenue_by_account(db, *WIN)[ACC]
    assert got["revenue"] == Decimal("150000")
    assert got["units"] == 15


def test_net_cost_coverage_is_revenue_based_not_option_count(db):
    """커버리지는 **매출 기준**이다 — 꼬리 옵션 20개와 주력 옵션 1개는 같은 사고가 아니다."""
    _seed_option(db, date(2026, 8, 5), "BIG", 900_000, 90)
    _seed_option(db, date(2026, 8, 5), "TAIL", 100_000, 10)
    _seed_cost(db, "BIG", 1_000, pid=1)      # 원가 있음
    db.commit()

    info = net_cost(db, *WIN, ACC, _cost_master(db))
    assert info["cost"] == Decimal("90000")           # 90개 × 1,000
    assert info["coverage"] == Decimal("0.9")          # 900,000 / 1,000,000
    assert info["options_costed"] == 1 and info["options_total"] == 2
    assert info["unmapped_revenue"] == Decimal("100000")


def test_net_cost_handles_negative_units(db):
    """환불 초과일은 정당하게 음수다 — 원가도 같이 되돌아와야 한다."""
    _seed_option(db, date(2026, 8, 5), "A", -20_000, -2)
    _seed_cost(db, "A", 1_000)
    db.commit()
    assert net_cost(db, *WIN, ACC, _cost_master(db))["cost"] == Decimal("-2000")


def test_option_axis_coverage_separates_missing_from_zero(db):
    """「안 받았다」와 「받았는데 0」을 가른다 — 둘을 뭉치면 원가 0이 조용히 이익을 부풀린다."""
    _seed_option(db, date(2026, 8, 5), "A", 0, 0)   # 받았는데 0
    db.commit()                                      # 08-06은 아예 없다
    cov = option_axis_coverage(db, *WIN, ACC)
    assert cov == {"days_total": 2, "days_covered": 1,
                   "first_date": date(2026, 8, 5), "last_date": date(2026, 8, 5),
                   "complete": False}


# ════════════════════════════════════════════════
# 4. RG 행 — 순이익 공식과 커버리지 게이트
# ════════════════════════════════════════════════
def test_rg_row_full_net_when_cost_trustworthy(db):
    for d in (date(2026, 8, 5), date(2026, 8, 6)):
        _seed_summary(db, d, 50_000, 5)
        _seed_option(db, d, "RG1", 50_000, 5)
    _seed_catalog(db, "RG1"); _seed_rg_inventory(db, "RG1")
    _seed_cost(db, "RG1", 2_000)
    _seed_fee(db, "sale_fee", 8_000)
    _seed_ad(db, "RG1", 1_000)
    db.commit()

    row = compute_rg_summary_row(db, ACC, *WIN, _cost_master(db), VENDOR)
    # ★돈은 «문자열»이 아니라 Decimal로 비교한다 — Numeric(14,2) 컬럼은 "1000.00"으로,
    #   산술 결과는 "1000"으로 나온다. 값이 같은데 문자열이 달라 깨지는 테스트는 거짓 경보다.
    assert row["channel_id"] == 3
    assert Decimal(row["revenue"]) == Decimal("100000")
    assert Decimal(row["cost"]) == Decimal("20000")          # 10개 × 2,000
    assert Decimal(row["commission"]) == Decimal("8000")
    assert Decimal(row["ad_spend"]) == Decimal("1000")
    assert row["net_scope"] == "full"
    # 100,000 − 20,000 − 8,000 − 1,000 − payable_vat(=(100,000−29,000)/11)
    expected = Decimal("100000") - Decimal("29000") - (
        Decimal("100000") * Decimal("10") / Decimal("110")
        - Decimal("29000") * Decimal("10") / Decimal("110")
    )
    assert Decimal(row["net_profit"]) == expected
    assert row["revenue_basis"] == "console_net"
    assert row["option_axis_days"] == "2/2"


def test_rg_row_withholds_net_when_option_axis_incomplete(db):
    """★커버리지 «비율»은 100%인데 창의 절반이 비어 있는 경우 — 순이익을 내면 안 된다.

    이게 이 게이트가 두 조건을 보는 이유다. 08-05만 있고 08-06이 통째로 없으면, 원가는 하루치만
    세는데 매출은 요약축에서 이틀치를 전부 센다 → **순이익이 위로 부푼다**(조용히 틀리는 모양).
    """
    _seed_summary(db, date(2026, 8, 5), 50_000, 5)
    _seed_summary(db, date(2026, 8, 6), 50_000, 5)   # 매출은 이틀
    _seed_option(db, date(2026, 8, 5), "RG1", 50_000, 5)  # 옵션축은 하루뿐
    _seed_catalog(db, "RG1"); _seed_rg_inventory(db, "RG1")
    _seed_cost(db, "RG1", 2_000)
    _seed_ad(db, "RG1", 1_000)
    db.commit()

    row = compute_rg_summary_row(db, ACC, *WIN, _cost_master(db), VENDOR)
    assert Decimal(row["revenue"]) == Decimal("100000")
    assert row["net_profit"] is None, "원가를 못 믿으면 순이익을 내지 않는다"
    assert Decimal(row["cost"]) == _Z
    assert Decimal(row["net_basis_revenue"]) == _Z, "원가 미상 매출을 이익률 분모에 넣지 않는다"
    assert row["option_axis_days"] == "1/2"
    assert Decimal(row["ad_spend"]) == Decimal("1000"), "광고비는 확정 비용이라 하한 근거로 남는다"


def test_rg_row_none_when_nothing_happened(db):
    """매출도 광고비도 0이면 행을 만들지 않는다 — 빈 행을 화면에 두지 않는다."""
    assert compute_rg_summary_row(db, ACC, *WIN, {}, VENDOR) is None


def test_rg_channel_bridge_is_company_not_code(db):
    """account_key(`COUPANG_WING1`)와 RG 채널 code(`COUPANG_RG1`)는 문자열이 다르다.

    이 불일치가 라이브 결함의 원인이었다 — `ch.code in rg_by_account` 매칭이 실패해서
    RG 정산 수수료가 RG 행이 아니라 **3P 행**에서 빠지고 있었다.
    """
    assert rg_channel_for_account(db, "COUPANG_WING1").code == "COUPANG_RG1"
    assert rg_channel_for_account(db, "COUPANG_WING2").code == "COUPANG_RG2"
    assert rg_channel_for_account(db, "NOPE") is None


# ════════════════════════════════════════════════
# 5. 채널 3줄 분리
# ════════════════════════════════════════════════
@pytest.mark.parametrize("code,expected", [
    ("COUPANG_WING1", "개인회사 오픽스 · 쿠팡 3P(판매자배송)"),
    ("COUPANG_RG1", "개인회사 오픽스 · 쿠팡 로켓그로스"),
    ("COUPANG_ROCKET", "주식회사 오하이테크 · 쿠팡 로켓배송(1P)"),
])
def test_channel_leaf_splits_three_ways(db, code, expected):
    ch = db.query(Channel).filter(Channel.code == code).one()
    assert _classify_channel(ch)[1] == expected


def test_unclassified_coupang_channel_is_not_guessed(db):
    """sell_type이 비고 위탁도 아니면 **추측하지 않는다** — 분류 안 됨이 화면에 드러나야 한다."""
    ch = Channel(id=9, name="쿠팡 신규채널", code="COUPANG_NEW", platform="coupang",
                 company="개인회사 오픽스", sell_type=None, channel_type="marketplace")
    assert _classify_channel(ch)[1] == "개인회사 오픽스 · 쿠팡 신규채널"
