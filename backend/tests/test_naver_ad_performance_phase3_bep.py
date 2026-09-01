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
    assert "최근에 관측된 값이 없습니다" in row["sentence"]


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


def test_market_bid_uses_binding_constraint_not_the_cheapest(client_and_session):
    """★시장가는 최근 관측일의 **최댓값**이다(리뷰 P2-5 교정).

    상한은 이미 최솟값(가장 빡빡한 소재)을 쓴다. 시장가까지 최솟값으로 잡으면 비교의 두 항이
    서로 반대 방향으로 낙관/보수가 되어 "살 만하다" 판정이 체계적으로 후해진다. 실제로 그
    순위를 사려면 기기·소재 중 가장 비싼 쪽을 지불해야 한다.
    """
    _client, db = client_and_session
    _seed(db)
    today = kst_today()
    for device, bid in (("PC", 1200), ("MOBILE", 2800)):
        db.add(NaverBidEstimateDaily(date=today, ad_id=AD_A, adgroup_id=GROUP_A,
                                     campaign_id=CAMPAIGN, device=device, position=4,
                                     bid=bid, is_floor=False))
    db.commit()
    out = perf_timeline_harness.build_bep_breakdown(db, campaign_id=CAMPAIGN)
    row = next(r for r in out["rows"] if r["cost_price"] is not None)
    assert row["market_bid"] == 2800          # 1200(PC)이 아니다
    assert row["market_bid_device"] == "MOBILE"
    assert row["market_bid_observed_on"] == today.isoformat()
    assert "모바일 4위" in row["sentence"]
    assert "관측)" in row["sentence"]          # 언제 본 값인지 화면이 말한다


def test_market_bid_prefers_latest_observation_day(client_and_session):
    """어제 비싸게 관측됐어도 오늘 값이 있으면 오늘 것을 쓴다(최댓값은 같은 날 안에서만)."""
    _client, db = client_and_session
    _seed(db)
    today = kst_today()
    db.add(NaverBidEstimateDaily(date=today - timedelta(days=2), ad_id=AD_A, adgroup_id=GROUP_A,
                                 campaign_id=CAMPAIGN, device="MOBILE", position=4,
                                 bid=9999, is_floor=False))
    db.add(NaverBidEstimateDaily(date=today, ad_id=AD_A, adgroup_id=GROUP_A,
                                 campaign_id=CAMPAIGN, device="MOBILE", position=4,
                                 bid=1500, is_floor=False))
    db.commit()
    out = perf_timeline_harness.build_bep_breakdown(db, campaign_id=CAMPAIGN)
    row = next(r for r in out["rows"] if r["cost_price"] is not None)
    assert row["market_bid"] == 1500
    assert row["market_bid_observed_on"] == today.isoformat()


# ══════════════════════════════════════════════════════════════════════════════
# D-NAO-283 — 출처(price_basis · logistics_basis)가 **실제 HTTP 응답까지** 나가는가
#
# ★적대 리뷰 1R P1 상환. 초판엔 이 배선을 지키는 테스트가 **한 개도 없었다**:
#   백엔드 테스트는 `NaverProductBep.price_basis`(DB 컬럼)를 직접 쿼리했고,
#   프론트 표면 테스트는 `../lib/api`를 통째로 vi.mock 해 **손으로 쓴 fixture**를 그렸다.
#   ⇒ 두 층은 각각 지켜졌는데 «둘을 잇는 한 줄»(직렬화)만 무보호였다.
#   bep_breakdown.build()와 _serialize_bep()에서 그 줄을 지워도 **1,346건 전건 초록**이었다.
#
# ★이건 이 저장소가 «이름까지 붙여 둔» 결함이다 — 교훈 #380(2026-08-31, 바로 직전 슬라이스):
#   *"두 층 각각은 지켜지는데 «둘을 잇는 한 줄»만 아무도 안 지킨다 … 표면 절단 변이는
#     «렌더 절단»만이 아니라 «배선 절단»까지여야 한다"*.
#   그 교훈을 커밋 메시지에 인용하면서 같은 구멍을 세 번째 자리에 남겼다.
#   ⇒ 여기서 지키는 것은 산식이 아니라 **JSON에 그 키가 실려 나가는가**다.
# ══════════════════════════════════════════════════════════════════════════════

def _basis_row(pid: str, *, logistics_basis: str, price_basis: str, logi: int = 1900):
    row = _bep_row(pid, sp=12900, cost=3100, rate="0.0589", logi=logi, name=f"출처 {pid}")
    row.logistics_basis = logistics_basis
    row.price_basis = price_basis
    return row


def test_http_bep_breakdown_carries_basis_fields(client_and_session):
    """★배선: /performance/bep-breakdown **HTTP 응답**에 출처 두 키가 실린다.

    build()에서 반환 줄을 지우는 변이가 여기서 죽는다(초판엔 그 변이가 생존했다)."""
    client, db = client_and_session
    _seed(db)
    db.add(NaverAdgroupProduct(adgroup_id=GROUP_A, campaign_id=CAMPAIGN,
                               mall_product_id="p-sibling", product_name="형제", ad_id="nad-sib"))
    db.add(_basis_row("p-sibling", logistics_basis="sibling", price_basis="meta", logi=-209))
    db.commit()

    res = client.get("/api/naver/ad/performance/bep-breakdown", params={"campaign_id": CAMPAIGN})
    assert res.status_code == 200
    rows = {r["product_name"]: r for r in res.json()["rows"]}
    sib = rows["출처 p-sibling"]
    assert sib["logistics_basis"] == "sibling"
    assert sib["price_basis"] == "meta"
    assert sib["logistics_cost"] == -209        # ★음수 물류비가 JSON까지 살아서 나간다
    # 실측 행은 「orders」로 나가야 한다 — None으로 뭉개지면 화면이 출처를 못 가른다.
    assert rows["[P_삭제금지]아이폰 강화유리"]["logistics_basis"] is None  # 시드는 미설정(마이그 직후 모양)


def test_http_bep_list_carries_basis_fields(client_and_session):
    """★배선: /bep 목록 **HTTP 응답**에도 출처가 실린다(_serialize_bep).

    성과뷰와 리포트는 서로 다른 엔드포인트를 쓴다 — 한쪽만 지키면 다른 쪽이 조용히 빈다."""
    client, db = client_and_session
    db.add(_basis_row("p-meta", logistics_basis="default", price_basis="meta"))
    db.add(_basis_row("p-own", logistics_basis="orders", price_basis="orders"))
    db.commit()

    res = client.get("/api/naver/ad/bep")
    assert res.status_code == 200
    rows = {r["channel_product_id"]: r for r in res.json()["rows"]}
    assert rows["p-meta"]["price_basis"] == "meta"
    assert rows["p-meta"]["logistics_basis"] == "default"
    assert rows["p-own"]["price_basis"] == "orders"
    assert rows["p-own"]["logistics_basis"] == "orders"
    assert rows["p-own"]["commission_basis"] == "delivery_case"


def test_http_bep_list_serves_negative_logistics(client_and_session):
    """★ⓐ의 결과가 API 경계를 넘는가 — 음수 물류비가 0이나 절댓값으로 뭉개지지 않는다."""
    client, db = client_and_session
    db.add(_basis_row("p-margin", logistics_basis="orders", price_basis="orders", logi=-1100))
    db.commit()

    res = client.get("/api/naver/ad/bep")
    row = next(r for r in res.json()["rows"] if r["channel_product_id"] == "p-margin")
    assert row["logistics_cost"] == -1100.0
