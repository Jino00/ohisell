# test_rocket_promo_pnl.py — 프로모션 손익 엔진 Phase 2 (트랙 coupang-promo-pnl)
#
# 라이브 호출 없음. 값은 **prod 실측**(2026-07-28 SELECT) 기반이라 계산이 틀리면 눈에 띈다:
#   프로모션 687878 = 2026-07-24 00:01:00 ~ 07-26 23:59:59, 분담 100%, 개당 할인액 4,000
#   SKU 62178970: 창 내 62개 / 실현매출 1,057,800 / 납품단가 10,740 / cost_price 3,400
#   SKU 69411570: 창 내 19개 / 실현매출 302,100 / **발주 이력 0건·원가 매핑 없음**(미해결)
#   계정 Retail 광고비 07-24~26 = 728,232 + 748,517 + 793,557 = 2,270,306
#   coupang_ad_option_daily에 A01029796(Retail) **0행** → 옵션 귀속 불가
#
# 이 스위트가 지키는 불변식(원칙22): **모르는 값은 0이 아니라 None**이다. 납품가·원가·광고비 중
#   하나라도 미상이면 그 SKU/프로모션의 손익은 N/A로 남고 합계에서 빠지며, 빠진 사실이
#   unresolved_*/blockers로 올라온다.
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import (
    CoupangAdOptionDaily,
    CoupangAdReport,
    CoupangCoupon,
    CoupangCouponItem,
    CoupangRocketPromotion,
    CoupangRocketPurchaseOrderItem,
    CoupangRocketSalesDaily,
    ProductMaster,
    RocketProductCostMap,
)
from app.services.coupang import rocket_promo_pnl as pnl

_VENDOR = "A01029796"
_TOKEN = "test-ingest-token-pnl"

# prod 실측 판매(창 07-24~26) — (sku, option, date, qty, revenue)
_SALES = [
    ("62178970", "93373791456", date(2026, 7, 23), 18, "320200"),   # ★창 밖(하루 전)
    ("62178970", "93373791456", date(2026, 7, 24), 20, "328000"),
    ("62178970", "93373791456", date(2026, 7, 25), 22, "381800"),
    ("62178970", "93373791456", date(2026, 7, 26), 20, "348000"),
    ("62178970", "93373791456", date(2026, 7, 27), 15, "261000"),   # ★창 밖(하루 뒤)
    ("69411570", "94677850209", date(2026, 7, 24), 11, "174900"),
    ("69411570", "94677850209", date(2026, 7, 25), 3, "47700"),
    ("69411570", "94677850209", date(2026, 7, 26), 5, "79500"),
]
_AD_ACCOUNT = [(date(2026, 7, 24), "728232"), (date(2026, 7, 25), "748517"),
               (date(2026, 7, 26), "793557")]


def _seed(db, *, unit_discount="4000", target_skus=("62178970", "69411570"),
          with_supply_69=False, with_cost_69=False, option_ad=False,
          cost_17pro="3400", supply_17pro="10740"):
    for sku, opt, d, qty, rev in _SALES:
        db.add(CoupangRocketSalesDaily(
            vendor_id=_VENDOR, option_id=opt, sku_id=sku, date=d,
            qty=qty, revenue=Decimal(rev), product_name=f"상품 {sku}",
            source="sales_analysis",
        ))
    for d, spend in _AD_ACCOUNT:
        db.add(CoupangAdReport(report_date=d, sell_type="Retail", vendor_id=_VENDOR,
                               ad_spend=Decimal(spend)))
    if option_ad:
        # 옵션 귀속 광고비가 생기면 엔진이 **코드 변경 없이** available=True로 전환된다.
        for d, spend in [(date(2026, 7, 24), "100000"), (date(2026, 7, 25), "50000")]:
            db.add(CoupangAdOptionDaily(
                report_date=d, vendor_id=_VENDOR, sell_type="Retail",
                ad_option_id="93373791456", conv_option_id="93373791456",
                ad_spend=Decimal(spend),
            ))
    if supply_17pro is not None:
        db.add(CoupangRocketPurchaseOrderItem(
            purchase_order_seq=137913049, vendor_id=_VENDOR, line_no=0,
            product_number="62178970", order_qty=9,
            unit_purchase_price=Decimal(supply_17pro)))
        # 더 오래된 발주(다른 단가) — "최신 발주 단가"를 쓰는지 확인용
        db.add(CoupangRocketPurchaseOrderItem(
            purchase_order_seq=133402531, vendor_id=_VENDOR, line_no=0,
            product_number="62178970", order_qty=127,
            unit_purchase_price=Decimal("9999")))
    if with_supply_69:
        db.add(CoupangRocketPurchaseOrderItem(
            purchase_order_seq=137913050, vendor_id=_VENDOR, line_no=0,
            product_number="69411570", order_qty=5,
            unit_purchase_price=Decimal("9000")))
    if cost_17pro is not None:
        db.add(RocketProductCostMap(product_number="62178970",
                                    internal_sku="OHI-TGLASS-IP17PRO", status="confirmed"))
        db.add(ProductMaster(internal_sku="OHI-TGLASS-IP17PRO",
                             product_name="강화유리 17프로", cost_price=Decimal(cost_17pro)))
    if with_cost_69:
        db.add(RocketProductCostMap(product_number="69411570",
                                    internal_sku="OHI-0497", status="confirmed"))
        db.add(ProductMaster(internal_sku="OHI-0497", product_name="S26U 지문방지",
                             cost_price=Decimal("2351")))
    db.add(CoupangRocketPromotion(
        request_id="687878", vendor_id=_VENDOR, contract_id="2385997",
        promotion_name="아이폰17프로_강화유리, S26울트라_지문",
        promotion_type="INSTANT_DISCOUNT", status="COMPLETE",
        start_at=datetime(2026, 7, 24, 0, 1, 0), end_at=datetime(2026, 7, 26, 23, 59, 59),
        share_ratio=Decimal("100"), budget_amount=Decimal("1000000"),
        applied_product_count=2,
        unit_discount_amount=Decimal(unit_discount) if unit_discount is not None else None,
        target_sku_ids=list(target_skus) if target_skus else None,
    ))
    db.commit()
    return db.query(CoupangRocketPromotion).one()


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


# ══════════════════════════════════════════════
# 창 조인 — 경계일 포함, 밖은 제외
# ══════════════════════════════════════════════
def test_window_includes_boundary_days_and_excludes_outside(db):
    promo = _seed(db)
    out = pnl.compute_promotion_pnl(db, promo, _VENDOR)
    assert out["window"] == {"from": "2026-07-24", "to": "2026-07-26", "days": 3}
    row = next(r for r in out["skus"] if r["sku_id"] == "62178970")
    # 07-23(18개)·07-27(15개)이 섞이면 62가 아니라 95가 된다.
    assert row["qty"] == 62
    assert row["realized_revenue"] == Decimal("1057800.00")
    assert row["sales_days"] == 3


# ══════════════════════════════════════════════
# 손익 공식 — prod 실값으로 자릿수까지 고정
# ══════════════════════════════════════════════
def test_resolved_sku_pnl_matches_prod_numbers(db):
    promo = _seed(db)
    out = pnl.compute_promotion_pnl(db, promo, _VENDOR)
    r = next(x for x in out["skus"] if x["sku_id"] == "62178970")
    assert r["supply_unit_price"] == Decimal("10740")      # 최신 발주(137913049) 단가
    assert r["supply_revenue"] == Decimal("665880.00")     # 10,740 × 62
    assert r["cost"] == Decimal("210800.00")               # 3,400 × 62
    assert r["funding"] == Decimal("248000.00")            # 4,000 × 62
    assert r["unit_contribution"] == Decimal("3340.00")    # 10,740 − 3,400 − 4,000
    assert r["bep_ad_spend"] == Decimal("207080.00")       # 665,880 − 210,800 − 248,000
    assert r["realized_unit_price"] == Decimal("17061.29")  # 1,057,800 / 62
    # ★진짜 BEP ROAS = 실현 소비자가 ÷ 개당 공헌이익 = 17,061.29 / 3,340
    assert r["bep_roas"] == Decimal("5.1082")
    assert r["resolved"] is True


def test_uses_latest_purchase_order_price_not_oldest(db):
    """납품가는 **최신 발주**(purchase_order_seq 최대) 단가여야 한다 — 9,999가 나오면 실패."""
    promo = _seed(db)
    out = pnl.compute_promotion_pnl(db, promo, _VENDOR)
    r = next(x for x in out["skus"] if x["sku_id"] == "62178970")
    assert r["supply_unit_price"] == Decimal("10740")


# ══════════════════════════════════════════════
# 미상은 0이 아니다 (원칙22)
# ══════════════════════════════════════════════
def test_sku_without_supply_price_is_unresolved_not_zero(db):
    """69411570은 prod에서 발주 이력 0건·원가 매핑 없음 → **합계에서 빠지고** 사유가 올라온다."""
    promo = _seed(db)
    out = pnl.compute_promotion_pnl(db, promo, _VENDOR)
    r = next(x for x in out["skus"] if x["sku_id"] == "69411570")
    assert r["resolved"] is False
    assert r["supply_unit_price"] is None and r["supply_revenue"] is None
    assert r["cost_price"] is None and r["cost"] is None
    assert r["bep_ad_spend"] is None and r["bep_roas"] is None
    assert any("납품가 미상" in w for w in r["unresolved_reasons"])
    assert any("원가 미상" in w for w in r["unresolved_reasons"])

    t = out["totals"]
    assert t["unresolved_sku_ids"] == ["69411570"]
    assert t["unresolved_qty"] == 19
    assert t["resolved_sku_count"] == 1
    # 합계는 해결분만 — 19개·302,100원이 섞이면 실패
    assert t["qty"] == 62 and t["realized_revenue"] == Decimal("1057800.00")
    # 전체 판매량은 따로 보인다(손익 소계를 판매량으로 오독하지 않도록)
    assert t["qty_all"] == 81 and t["realized_revenue_all"] == Decimal("1359900.00")
    assert any("미해결 SKU" in b for b in out["blockers"])


def test_both_skus_resolved_totals_add_up(db):
    promo = _seed(db, with_supply_69=True, with_cost_69=True)
    out = pnl.compute_promotion_pnl(db, promo, _VENDOR)
    t = out["totals"]
    assert t["resolved_sku_count"] == 2 and t["unresolved_sku_ids"] == []
    assert t["qty"] == 81 == t["qty_all"]
    assert t["supply_revenue"] == Decimal("836880.00")     # 665,880 + 9,000×19
    assert t["cost"] == Decimal("255469.00")               # 210,800 + 2,351×19
    assert t["funding"] == Decimal("324000.00")            # 4,000 × 81
    assert t["bep_ad_spend"] == Decimal("257411.00")
    # 프로모션 단위 BEP ROAS = Σ실현매출 ÷ Σ공헌이익 (SKU별 평균이 아니다)
    assert t["bep_roas"] == (Decimal("1359900") / Decimal("257411")).quantize(Decimal("0.0001"))


def test_missing_unit_discount_blocks_funding_without_zeroing(db):
    promo = _seed(db, unit_discount=None)
    out = pnl.compute_promotion_pnl(db, promo, _VENDOR)
    assert out["unit_discount_missing"] is True
    assert all(r["funding"] is None for r in out["skus"])
    assert all(r["resolved"] is False for r in out["skus"])
    assert out["totals"]["bep_ad_spend"] is None
    assert any("개당 할인액 미입력" in b for b in out["blockers"])


def test_missing_target_skus_returns_blocker_without_guessing(db):
    """적용 상품 목록이 API에 없으므로, 미지정이면 **추정하지 않고** 멈춘다."""
    promo = _seed(db, target_skus=None)
    out = pnl.compute_promotion_pnl(db, promo, _VENDOR)
    assert out["target_sku_missing"] is True
    assert out["skus"] == [] and out["totals"] is None
    assert any("대상 SKU 미지정" in b for b in out["blockers"])


def test_missing_window_returns_blocker(db):
    promo = _seed(db)
    promo.start_at = None
    db.commit()
    out = pnl.compute_promotion_pnl(db, promo, _VENDOR)
    assert out["window"] is None and out["totals"] is None
    assert any("행사기간" in b for b in out["blockers"])


def test_ignored_cost_mapping_counts_as_resolved_zero_cost(db):
    """'ignored' = 원가 0으로 **결정된** 것(샘플/증정) — 미매핑과 다르다."""
    promo = _seed(db, target_skus=("69411570",), with_supply_69=True)
    db.add(RocketProductCostMap(product_number="69411570", internal_sku=None, status="ignored"))
    db.commit()
    out = pnl.compute_promotion_pnl(db, promo, _VENDOR)
    r = out["skus"][0]
    assert r["resolved"] is True and r["cost"] == Decimal("0.00")
    assert r["unit_contribution"] == Decimal("5000.00")   # 9,000 − 0 − 4,000


def test_negative_contribution_yields_no_bep_roas(db):
    """공헌이익 ≤ 0이면 본전 ROAS는 **존재하지 않는다** — 음수를 내지 않고 None."""
    promo = _seed(db, cost_17pro="9000")   # 10,740 − 9,000 − 4,000 = −2,260
    out = pnl.compute_promotion_pnl(db, promo, _VENDOR)
    r = next(x for x in out["skus"] if x["sku_id"] == "62178970")
    assert r["unit_contribution"] == Decimal("-2260.00")
    assert r["bep_roas"] is None
    assert out["totals"]["bep_roas"] is None
    assert r["bep_ad_spend"] == Decimal("-140120.00")     # BEP 광고비는 음수로 정직하게 남는다


# ══════════════════════════════════════════════
# 광고비 — 옵션 귀속 불가를 0으로 접지 않는다
# ══════════════════════════════════════════════
def test_ad_unavailable_keeps_net_profit_na_and_reports_account_upper_bound(db):
    promo = _seed(db)
    out = pnl.compute_promotion_pnl(db, promo, _VENDOR)
    assert out["ad"]["available"] is False
    assert out["ad"]["attributed"] is None
    assert out["ad"]["account_window_spend"] == Decimal("2270306")   # prod 실측 3일 합
    t = out["totals"]
    assert t["ad_cost"] is None
    assert t["net_profit"] is None            # ★0으로 접으면 여기가 207,080이 되어 흑자로 보인다
    assert t["net_profit_lower_bound"] == Decimal("-2063226.00")     # 207,080 − 2,270,306
    assert any("광고비 옵션 귀속 불가" in b for b in out["blockers"])


def test_ad_option_level_enables_net_profit_without_code_change(db):
    promo = _seed(db, option_ad=True)
    out = pnl.compute_promotion_pnl(db, promo, _VENDOR)
    assert out["ad"]["available"] is True
    assert out["ad"]["attributed"] == Decimal("150000")
    t = out["totals"]
    assert t["ad_cost"] == Decimal("150000")
    assert t["net_profit"] == Decimal("57080.00")      # 207,080 − 150,000
    assert t["net_profit_lower_bound"] is None         # 실값이 있으면 상한 프록시는 안 낸다


def test_ad_option_query_ignores_other_vendor_and_selltype(db):
    promo = _seed(db)
    db.add(CoupangAdOptionDaily(
        report_date=date(2026, 7, 24), vendor_id="A01564720", sell_type="3P",
        ad_option_id="93373791456", conv_option_id="93373791456",
        ad_spend=Decimal("999999")))
    db.commit()
    out = pnl.compute_promotion_pnl(db, promo, _VENDOR)
    assert out["ad"]["available"] is False   # 다른 계정·판매방식 행이 새어 들어오면 실패


# ══════════════════════════════════════════════
# 신선도 — 롤링 창 결손 + 구독 체험 경고
# ══════════════════════════════════════════════
def test_freshness_finds_gaps_sorted_by_expiry(db, monkeypatch):
    monkeypatch.setattr(pnl, "_WINDOW_DAYS", 5)
    for d in (date(2026, 7, 24), date(2026, 7, 27)):
        db.add(CoupangRocketSalesDaily(vendor_id=_VENDOR, option_id="X", sku_id="S",
                                       date=d, qty=1, revenue=Decimal("100")))
    db.commit()
    fr = pnl.compute_sales_freshness(db, _VENDOR, today=date(2026, 7, 28))
    # 창은 **끝(어제)에서부터** 5일 = [07-23, 07-27]. 결손 = 07-23·07-25·07-26.
    assert fr["window"] == {"from": "2026-07-23", "to": "2026-07-27", "days": 5}
    assert [m["date"] for m in fr["missing_dates"]] == \
        ["2026-07-23", "2026-07-25", "2026-07-26"]
    # 만료 임박순(창 시작에 가까울수록 먼저 밀려난다). 07-23은 오늘이 마지막 기회(D-0).
    assert [m["days_until_expiry"] for m in fr["missing_dates"]] == [0, 2, 3]
    assert fr["latest_date"] == "2026-07-27" and fr["stale_days"] == 0


def test_freshness_window_reproduces_live_viewable_period(db):
    """★라이브 앵커: 2026-07-28에 서버가 준 `viewable period [2026-06-01 ~ 2026-07-27]`을
    기본 상수(57일)로 그대로 재현해야 한다. 여기가 어긋나면 아직 메울 수 있는 날을
    '이미 만료'로 읽거나 그 반대가 된다."""
    fr = pnl.compute_sales_freshness(db, _VENDOR, today=date(2026, 7, 28))
    assert fr["window"]["from"] == "2026-06-01"
    assert fr["window"]["to"] == "2026-07-27"


def test_freshness_reports_stale_when_collection_stops(db, monkeypatch):
    monkeypatch.setattr(pnl, "_WINDOW_DAYS", 10)
    db.add(CoupangRocketSalesDaily(vendor_id=_VENDOR, option_id="X", sku_id="S",
                                   date=date(2026, 7, 22), qty=1, revenue=Decimal("100")))
    db.commit()
    fr = pnl.compute_sales_freshness(db, _VENDOR, today=date(2026, 7, 28))
    assert fr["stale_days"] == 5   # 어제(07-27) 대비 5일 뒤처짐
    assert fr["missing_count"] == 9


def test_subscription_trial_warning_fires_at_d7(db):
    warn = pnl.compute_sales_freshness(db, _VENDOR, today=date(2026, 8, 13))
    assert warn["subscription"]["days_left"] == 7 and warn["subscription"]["warn"] is True
    quiet = pnl.compute_sales_freshness(db, _VENDOR, today=date(2026, 8, 12))
    assert quiet["subscription"]["warn"] is False
    over = pnl.compute_sales_freshness(db, _VENDOR, today=date(2026, 8, 21))
    assert over["subscription"]["expired"] is True and over["subscription"]["days_left"] == -1


# ══════════════════════════════════════════════
# RG 쿠폰 — used_amount NULL은 '미수집'이지 0이 아니다
# ══════════════════════════════════════════════
def test_rg_coupons_list_marks_pending_used_amount(db):
    db.add(CoupangCoupon(account_key="COUPANG_WING1", vendor_id="A0001",
                         coupon_kind="INSTANT", coupon_id="94177420",
                         promotion_name="S26U 즉시할인", status="EXPIRED",
                         start_at=datetime(2026, 7, 2), end_at=datetime(2026, 7, 3),
                         used_amount=Decimal("156000"), used_amount_source="wing_ui"))
    db.add(CoupangCoupon(account_key="COUPANG_WING1", vendor_id="A0001",
                         coupon_kind="INSTANT", coupon_id="93654161",
                         promotion_name="미수집 쿠폰", status="APPLIED",
                         start_at=datetime(2026, 7, 5)))
    db.add(CoupangCoupon(account_key="COUPANG_WING1", vendor_id="A0001",
                         coupon_kind="DOWNLOAD", coupon_id="88888888",
                         promotion_name="다운로드 쿠폰(다른 축)"))
    db.add(CoupangCouponItem(account_key="COUPANG_WING1", vendor_id="A0001",
                             coupon_item_id="i1", coupon_id="94177420",
                             vendor_item_id="95536607339"))
    db.commit()
    out = pnl.list_rg_coupons(db)
    assert out["count"] == 2                        # DOWNLOAD는 제외(D-CPP-3은 INSTANT 축)
    ids = {c["coupon_id"]: c for c in out["coupons"]}
    assert ids["94177420"]["used_amount"] == Decimal("156000")
    assert ids["94177420"]["used_amount_pending"] is False
    assert ids["94177420"]["option_count"] == 1
    assert ids["93654161"]["used_amount"] is None
    assert ids["93654161"]["used_amount_pending"] is True    # ★0이 아니다
    assert out["pending_count"] == 1


# ══════════════════════════════════════════════
# 회계축 불변 — 신규 엔진은 기존 회계에서 참조 0
# ══════════════════════════════════════════════
def test_pnl_engine_is_not_referenced_by_accounting_code():
    """이 레이어는 net_profit·종합조망을 바꾸지 않는다 — 회계 엔진이 이 모듈을 부르면 실패."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    offenders = []
    allowed = {"rocket_promo_pnl.py", "overview.py"}
    for p in root.rglob("*.py"):
        if p.name in allowed:
            continue
        if "rocket_promo_pnl" in p.read_text(encoding="utf-8"):
            offenders.append(str(p.relative_to(root)))
    assert offenders == [], f"회계/기타 코드가 손익 엔진을 참조한다: {offenders}"


# ══════════════════════════════════════════════
# 라우트
# ══════════════════════════════════════════════
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AD_INGEST_TOKEN", _TOKEN)
    from app.main import app

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        s = TestingSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_get_db
    seed = TestingSession()
    yield TestClient(app), seed
    seed.close()
    app.dependency_overrides.clear()


def test_promo_pnl_route_shape(client):
    c, seed = client
    _seed(seed)
    r = c.get("/api/overview/rocket-promo-pnl")
    assert r.status_code == 200
    body = r.json()
    assert body["promotion_count"] == 1
    card = body["promotions"][0]
    assert card["request_id"] == "687878"
    # Decimal은 문자열로 직렬화(금액 정밀도 보존 — overview 라우터 규약)
    assert card["totals"]["bep_ad_spend"] == "207080.00"
    assert card["totals"]["bep_roas"] == "5.1082"
    assert card["totals"]["net_profit"] is None
    assert "freshness" in body and "rg_coupons" in body
    assert body["freshness"]["subscription"]["free_trial_end"] == "2026-08-20"


def test_promo_pnl_route_filters_by_request_id(client):
    c, seed = client
    _seed(seed)
    assert c.get("/api/overview/rocket-promo-pnl?request_id=687878").json()["promotion_count"] == 1
    assert c.get("/api/overview/rocket-promo-pnl?request_id=999999").json()["promotion_count"] == 0


def _patch(c, body):
    return c.patch("/api/coupang/ops/rocket/promotion/687878/unit-discount",
                   json=body, headers={"X-Ingest-Token": _TOKEN})


def test_patch_sets_target_skus_and_dedupes(client):
    c, seed = client
    _seed(seed, target_skus=None)
    r = _patch(c, {"target_sku_ids": [" 62178970 ", "69411570", "62178970", ""]})
    assert r.status_code == 200
    assert r.json()["target_sku_ids"] == ["62178970", "69411570"]


def test_patch_partial_update_does_not_clobber_other_field(client):
    """할인액만 고칠 때 SKU가 지워지면 안 된다(그 반대도) — 수기 입력 두 칸의 독립성."""
    c, seed = client
    _seed(seed)
    assert _patch(c, {"unit_discount_amount": 5000}).json()["target_sku_ids"] == \
        ["62178970", "69411570"]
    r = _patch(c, {"target_sku_ids": ["62178970"]})
    assert r.json()["unit_discount_amount"] == "5000.00"
    assert r.json()["target_sku_ids"] == ["62178970"]


def test_patch_empty_list_clears_target_skus(client):
    c, seed = client
    _seed(seed)
    assert _patch(c, {"target_sku_ids": []}).json()["target_sku_ids"] is None
    assert _patch(c, {"target_sku_ids": None}).json()["target_sku_ids"] is None


def test_patch_rejects_bad_target_skus(client):
    c, seed = client
    _seed(seed)
    assert _patch(c, {"target_sku_ids": "62178970"}).status_code == 400        # 배열 아님
    assert _patch(c, {"target_sku_ids": ["x" * 31]}).status_code == 400        # 길이 초과(자르지 않음)
    assert _patch(c, {"target_sku_ids": [{"a": 1}]}).status_code == 400        # 원소 타입
    assert _patch(c, {"target_sku_ids": ["x"] * 201}).status_code == 400       # 개수 상한
    assert _patch(c, {}).status_code == 400                                    # 두 키 다 없음


def test_patch_still_requires_token(client):
    c, seed = client
    _seed(seed)
    r = c.patch("/api/coupang/ops/rocket/promotion/687878/unit-discount",
                json={"target_sku_ids": ["62178970"]})
    assert r.status_code == 401
