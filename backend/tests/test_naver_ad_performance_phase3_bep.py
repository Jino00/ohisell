# test_naver_ad_performance_phase3_bep.py — 성과뷰 Phase 3 ⑤BEP 구성(계획서 §4-ⓓ).
#
# 이 파일이 지키는 것(계획서 §6 Phase3 완료기준 4 · 원칙22):
#   · 원가 미입력 상품은 **추정치로 채우지 않는다** — 값은 전부 None, 사유는 문장으로.
#   · 화면에서 **뺄셈이 맞아야 한다** — 판매가−수수료−원가−물류비 = pre_vat_margin,
#     거기서 ÷1.1 한 것이 공헌이익. VAT 단계를 빼면 표가 안 맞고 표가 안 맞으면 못 믿는다.
#   · 상품 상한은 소재가 여럿이면 **가장 보수적인 값**(최솟값) — 최댓값을 쓰면 어떤 소재에선
#     이미 손해인 값을 "써도 된다"고 말하게 된다.
#   · 화면 문자열에 ID·내부 용어가 없다(D-NAO-103①②).
# 원칙22: SA 단위테스트만으론 라우터 500을 못 잡는다 → HTTP 왕복도 함께 건다.
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    NaverAdDaily,
    NaverAdgroupProduct,
    NaverBidEstimateDaily,
    NaverProductBep,
)
from app.services.naver_ad import bep_breakdown, perf_timeline_harness
from app.utils.kst import kst_today

CAMPAIGN = "cmp-shopping-1"
OTHER_CAMPAIGN = "cmp-shopping-2"
GROUP_A = "grp-a"
GROUP_B = "grp-b"
AD_A = "nad-a"
AD_B = "nad-b"
PID_OK = "p-with-cost"
PID_NO_COST = "p-without-cost"
NAVER_CHANNEL_ID = 6


@pytest.fixture
def client_and_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    seed = TestingSession()
    yield TestClient(app), seed
    seed.close()
    app.dependency_overrides.clear()


def _bep_row(pid: str, *, sp: int, cost: int, rate: str, logi: int, name: str) -> NaverProductBep:
    """bep_calculator와 **같은 산식**으로 스냅샷 한 행을 만든다(테스트가 산식을 새로 쓰지 않도록)."""
    sp_d, cost_d, rate_d, logi_d = Decimal(sp), Decimal(cost), Decimal(rate), Decimal(logi)
    has_cost = sp_d > 0 and cost_d > 0
    contribution = (
        (sp_d - sp_d * rate_d - cost_d - logi_d) / Decimal("1.1") if has_cost else Decimal("0")
    )
    bep = (sp_d / contribution).quantize(Decimal("0.0001")) if has_cost and contribution > 0 else None
    return NaverProductBep(
        channel_id=NAVER_CHANNEL_ID, channel_product_id=pid, product_name=name,
        selling_price=sp_d, cost_price=cost_d, commission_rate=rate_d, logistics_cost=logi_d,
        contribution_margin=contribution.quantize(Decimal("0.01")), bep_roas=bep,
        aggressiveness="standard",
        target_roas=(bep * Decimal("1.15")).quantize(Decimal("0.0001")) if bep else None,
        has_cost=has_cost, commission_basis="delivery_case",
    )


def _seed(db, *, with_second_ad: bool = False, market_bid: int | None = None) -> None:
    db.add(_bep_row(PID_OK, sp=12900, cost=3100, rate="0.0589", logi=3020, name="[P_삭제금지]아이폰 강화유리"))
    db.add(_bep_row(PID_NO_COST, sp=9900, cost=0, rate="0.0589", logi=1900, name="원가없는 상품"))
    db.add(NaverAdgroupProduct(
        adgroup_id=GROUP_A, campaign_id=CAMPAIGN, mall_product_id=PID_OK,
        product_name="아이폰 강화유리", ad_id=AD_A,
    ))
    db.add(NaverAdgroupProduct(
        adgroup_id=GROUP_A, campaign_id=CAMPAIGN, mall_product_id=PID_NO_COST,
        product_name="원가없는 상품", ad_id="nad-nocost",
    ))
    if with_second_ad:
        db.add(NaverAdgroupProduct(
            adgroup_id=GROUP_B, campaign_id=CAMPAIGN, mall_product_id=PID_OK,
            product_name="아이폰 강화유리", ad_id=AD_B,
        ))
    today = kst_today()
    # RPC 표본: 그룹 A는 넉넉하게(≥10클릭), 그룹 B는 더 낮은 RPC → 상한이 갈리게 만든다.
    for i in range(1, 4):
        d = today - timedelta(days=i)
        db.add(NaverAdDaily(ad_date=d, campaign_id=CAMPAIGN, adgroup_id=GROUP_A,
                            imp=1000, clk=20, cost=10000, conv_direct_amt=200000))
        if with_second_ad:
            db.add(NaverAdDaily(ad_date=d, campaign_id=CAMPAIGN, adgroup_id=GROUP_B,
                                imp=1000, clk=20, cost=10000, conv_direct_amt=60000))
    if market_bid is not None:
        db.add(NaverBidEstimateDaily(date=today, ad_id=AD_A, adgroup_id=GROUP_A,
                                     campaign_id=CAMPAIGN, device="MOBILE", position=4,
                                     bid=market_bid, is_floor=False))
    db.commit()


def test_missing_cost_is_never_estimated(client_and_session):
    """원가 미입력 상품은 값이 아니라 사유가 나간다(완료기준 4)."""
    _client, db = client_and_session
    _seed(db)
    out = perf_timeline_harness.build_bep_breakdown(db, campaign_id=CAMPAIGN)
    row = next(r for r in out["rows"] if r["product_name"] == "원가없는 상품")
    assert row["cost_price"] is None
    assert row["bep_roas"] is None
    assert row["contribution_margin"] is None
    assert row["pre_vat_margin"] is None
    assert row["ceiling_bid"] is None
    assert "원가" in row["blocked_reason"]
    assert out["missing_cost_count"] == 1


def test_composition_arithmetic_adds_up(client_and_session):
    """판매가 − 수수료 − 원가 − 물류비 = pre_vat_margin, ÷1.1 = 공헌이익.

    화면이 이 네 숫자만 보여주고 VAT 단계를 빼면 뺄셈이 안 맞는다 — 그 순간 표를 못 믿는다.
    """
    _client, db = client_and_session
    _seed(db)
    out = perf_timeline_harness.build_bep_breakdown(db, campaign_id=CAMPAIGN)
    row = next(r for r in out["rows"] if r["cost_price"] is not None)
    assert (
        row["pre_vat_margin"]
        == row["selling_price"] - row["commission_won"] - row["cost_price"] - row["logistics_cost"]
    )
    assert out["vat_divisor"] == pytest.approx(1.1)
    assert row["contribution_margin"] == pytest.approx(
        row["pre_vat_margin"] / out["vat_divisor"], abs=1
    )
    # 손익분기 ROAS = 판매가 ÷ 공헌이익 (스냅샷 저장값과 일치해야 한다)
    assert row["bep_roas"] == pytest.approx(
        row["selling_price"] / row["contribution_margin"], abs=0.01
    )


def test_commission_won_matches_rate(client_and_session):
    _client, db = client_and_session
    _seed(db)
    out = perf_timeline_harness.build_bep_breakdown(db, campaign_id=CAMPAIGN)
    row = next(r for r in out["rows"] if r["cost_price"] is not None)
    assert row["commission_won"] == pytest.approx(
        row["selling_price"] * row["commission_rate"], abs=1
    )


def test_multiple_ads_take_the_most_conservative_ceiling(client_and_session):
    """소재가 여럿이면 상한은 **최솟값**. 최댓값을 쓰면 어떤 소재에선 이미 손해다."""
    _client, db = client_and_session
    _seed(db, with_second_ad=True)
    out = perf_timeline_harness.build_bep_breakdown(db, campaign_id=CAMPAIGN)
    row = next(r for r in out["rows"] if r["cost_price"] is not None)
    assert row["ad_count"] == 2
    assert row["ceiling_bid"] is not None
    # 그룹 B의 RPC(60000/60=1000)가 그룹 A(200000/60≈3333)보다 낮으므로 상한도 낮다.
    ceilings = {
        ad: perf_timeline_harness.bid_ceiling_calculator.compute_ceiling(
            db, ad, grp, CAMPAIGN, kst_today()
        )["ceiling_cpc"]
        for ad, grp in ((AD_A, GROUP_A), (AD_B, GROUP_B))
    }
    assert row["ceiling_bid"] == min(v for v in ceilings.values() if v)
    assert row["ceiling_bid"] < max(ceilings.values())
    assert "가장 빡빡한" in row["ceiling_basis"]


def test_market_bid_above_ceiling_does_not_declare_a_loss(client_and_session):
    """시장가가 상한 위여도 **'손해입니다'라고 단정하지 않는다**(원칙22 · 계획서 R1).

    이 상한의 RPC는 직접전환 매출만 세는 보수적 값이다(bid_ceiling_calculator._rpc_for).
    보수 상한 초과 = "보수적으로 보면 넘는다"이지 손실 확정이 아니다. 화면이 단정하면
    실제로는 남는 광고를 끄게 만든다 — 이 페이지가 만들 수 있는 가장 비싼 오독이다.
    """
    _client, db = client_and_session
    _seed(db, market_bid=999999)
    out = perf_timeline_harness.build_bep_breakdown(db, campaign_id=CAMPAIGN)
    row = next(r for r in out["rows"] if r["cost_price"] is not None)
    assert row["market_bid"] == 999999
    assert "보수적으로" in row["sentence"]
    assert "남는 선을 넘습니다" in row["sentence"]
    assert "손해" not in row["sentence"]
    assert "대체로 보수적입니다" in out["data_note"]


def test_floor_market_bid_is_not_quoted(client_and_session):
    """is_floor 행은 시세가 무의미하다는 표식 — '시장가'라고 인용하면 거짓이다."""
    _client, db = client_and_session
    _seed(db)
    db.add(NaverBidEstimateDaily(date=kst_today(), ad_id=AD_A, adgroup_id=GROUP_A,
                                 campaign_id=CAMPAIGN, device="MOBILE", position=4,
                                 bid=50, is_floor=True))
    db.commit()
    out = perf_timeline_harness.build_bep_breakdown(db, campaign_id=CAMPAIGN)
    row = next(r for r in out["rows"] if r["cost_price"] is not None)
    assert row["market_bid"] is None


def test_stale_market_bid_is_not_quoted(client_and_session):
    """오래된 관측을 '지금 시장가'라고 부르지 않는다."""
    _client, db = client_and_session
    _seed(db)
    old = kst_today() - timedelta(days=bep_breakdown.MARKET_BID_MAX_AGE_DAYS + 1)
    db.add(NaverBidEstimateDaily(date=old, ad_id=AD_A, adgroup_id=GROUP_A,
                                 campaign_id=CAMPAIGN, device="MOBILE", position=4,
                                 bid=2280, is_floor=False))
    db.commit()
    out = perf_timeline_harness.build_bep_breakdown(db, campaign_id=CAMPAIGN)
    row = next(r for r in out["rows"] if r["cost_price"] is not None)
    assert row["market_bid"] is None
    assert "관측되지 않았습니다" in row["sentence"]


def test_campaign_filter_excludes_other_campaign_products(client_and_session):
    _client, db = client_and_session
    _seed(db)
    db.add(NaverAdgroupProduct(adgroup_id="grp-z", campaign_id=OTHER_CAMPAIGN,
                               mall_product_id="p-other", product_name="다른 광고 상품",
                               ad_id="nad-z"))
    db.add(_bep_row("p-other", sp=5000, cost=1000, rate="0.0589", logi=1900, name="다른 광고 상품"))
    db.commit()
    out = perf_timeline_harness.build_bep_breakdown(db, campaign_id=CAMPAIGN)
    assert all(r["product_name"] != "다른 광고 상품" for r in out["rows"])


def test_no_linked_products_is_a_sentence_not_an_error(client_and_session):
    """매핑이 없는 캠페인(예: 파워링크)은 빈 표가 아니라 이유를 말한다."""
    _client, db = client_and_session
    _seed(db)
    out = perf_timeline_harness.build_bep_breakdown(db, campaign_id="cmp-no-mapping")
    assert out["rows"] == []
    assert "연결된 판매 상품이 없어" in out["data_note"]


def test_screen_strings_have_no_ids_or_internal_terms(client_and_session):
    """D-NAO-103①② — 화면 문자열에 ID·내부 용어가 없다."""
    _client, db = client_and_session
    _seed(db, market_bid=2280)
    out = perf_timeline_harness.build_bep_breakdown(db, campaign_id=CAMPAIGN)
    blob = " ".join(
        f"{r['sentence']} {r['ceiling_basis']} {r['blocked_reason']} {r['product_name']}"
        for r in out["rows"]
    ) + out["data_note"]
    for banned in (CAMPAIGN, GROUP_A, AD_A, "cmp-", "grp-", "nad-", "D-NAO", "bep_roas", "RPC"):
        assert banned not in blob, banned


def test_http_roundtrip(client_and_session):
    """라우터 레이어 500을 SA 단위테스트가 못 잡는다(원칙22)."""
    client, db = client_and_session
    _seed(db, market_bid=2280)
    res = client.get("/api/naver/ad/performance/bep-breakdown", params={"campaign_id": CAMPAIGN})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["campaign_id"] == CAMPAIGN
    assert body["missing_cost_count"] == 1
    assert len(body["rows"]) == 2


def test_http_only_actionable_false_includes_unlinked_products(client_and_session):
    client, db = client_and_session
    _seed(db)
    db.add(_bep_row("p-unlinked", sp=7000, cost=2000, rate="0.0589", logi=1900, name="광고에 없는 상품"))
    db.commit()
    res = client.get("/api/naver/ad/performance/bep-breakdown",
                     params={"only_actionable": "false"})
    assert res.status_code == 200, res.text
    names = [r["product_name"] for r in res.json()["rows"]]
    assert "광고에 없는 상품" in names
