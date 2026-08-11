# test_search_term_exclusion_list.py — 제외 후보 리스트 생성기 (D-NAO-173 P1-②).
#
# ## 이 파일이 지키는 것
# ① 판정이 **연속 ROAS**인가 — 기존 judge의 «전환 유무» 이진 게이트로 되돌아가지 않는가.
#    (그 게이트는 ROAS 0.08과 1.65를 같은 칸에 넣는다. 이 스프린트가 존재하는 이유가 그것이다.)
# ② 기준선이 **상품별 BEP**인가 — 계정 단일 BEP(1.7 일괄)로 재면 판정이 뒤집힌다(실측: 1.223~5.81).
# ③ 빠진 것이 **세어져 나오는가** — fail-closed가 침묵이면 그냥 «안 보이는 손실»이다.
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    Channel,
    NaverAdgroupProduct,
    NaverProductBep,
    NaverSearchTermDaily,
)
from app.services.naver_ad import search_term_exclusion_list as stl

NOW = datetime(2026, 8, 11, 12, 0, 0)
AS_OF = NOW.date()
# 창은 성숙도 3일을 뺀 뒤의 30일 — 이 날짜는 그 창 한가운데다.
IN_WINDOW = AS_OF - timedelta(days=10)
IN_MATURITY = AS_OF - timedelta(days=1)  # 판정에서 빠져야 하는 최근 구간

CAMPAIGN = "cmp-1"
ADGROUP = "grp-1"


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Channel(id=6, name="네이버", code="naver", platform="naver"))
    session.commit()
    yield session
    session.close()


def _term(db, term: str, *, cost: int, amt: int, clk: int = 50, conv: int = 0,
          ad_date: date = IN_WINDOW, source: str = "shopping", adgroup_id: str = ADGROUP):
    db.add(NaverSearchTermDaily(
        ad_date=ad_date, campaign_id=CAMPAIGN, adgroup_id=adgroup_id, search_term=term,
        source=source, imp=clk * 20, clk=clk, cost=cost, rank_sum=0,
        conv_purchase_cnt=conv, conv_direct_cnt=conv, conv_purchase_amt=amt,
    ))


def _product(db, *, cpid: str, bep: str, margin: str = "3000", adgroup_id: str = ADGROUP,
             name: str = "테스트상품"):
    db.add(NaverProductBep(
        channel_id=6, channel_product_id=cpid, product_name=name,
        selling_price=Decimal("10000"), cost_price=Decimal("5000"),
        commission_rate=Decimal("0.05"), logistics_cost=Decimal("0"),
        contribution_margin=Decimal(margin), bep_roas=Decimal(bep), has_cost=True,
    ))
    db.add(NaverAdgroupProduct(
        campaign_id=CAMPAIGN, adgroup_id=adgroup_id, mall_product_id=cpid,
    ))


def _build(db, **kw):
    return stl.build_exclusion_list(db, now=NOW, **kw)


# ═══ ① 연속 ROAS 판정 ═══


def test_loss_term_with_conversions_still_becomes_a_candidate(db):
    """★핵심: 전환이 **있어도** ROAS가 BEP 아래면 후보다.

    기존 judge는 전환 1건만 있으면 무조건 보호한다(이진 게이트). 그 규칙 아래에서는
    30일 100,000원 쓰고 50,000원 판 검색어가 «살아 있는 증거»로 분류돼 영영 안 잘린다.
    """
    _product(db, cpid="p1", bep="1.7")
    _term(db, "적자검색어", cost=100_000, amt=50_000, conv=3)
    db.commit()

    out = _build(db)
    assert [c["search_term"] for c in out["candidates"]] == ["적자검색어"]
    c = out["candidates"][0]
    assert c["roas"] == pytest.approx(0.5)
    assert c["applied_bep"] == pytest.approx(1.7)
    assert c["conv_purchase_cnt"] == 3, "전환이 있었다는 사실 자체는 화면에 남아야 한다"


def test_profitable_term_is_kept_and_counted(db):
    _product(db, cpid="p1", bep="1.7")
    _term(db, "흑자검색어", cost=100_000, amt=300_000, conv=5)
    db.commit()

    out = _build(db)
    assert out["candidates"] == []
    assert out["buckets"]["profitable"]["terms"] == 1
    assert out["buckets"]["profitable"]["cost"] == 100_000


def test_boundary_is_not_a_candidate(db):
    """ROAS == BEP는 자르지 않는다(경계는 손실이 아니다)."""
    _product(db, cpid="p1", bep="2.0")
    _term(db, "경계검색어", cost=100_000, amt=200_000, conv=2)
    db.commit()
    assert _build(db)["candidates"] == []


# ═══ ② 상품별 BEP 기준선 ═══


def test_bep_is_per_adgroup_not_a_single_account_number(db):
    """★같은 ROAS라도 그룹의 상품 BEP에 따라 판정이 갈린다 — 계정 단일 BEP면 둘 다 같아진다."""
    _product(db, cpid="p-low", bep="1.2", adgroup_id="grp-low")
    _product(db, cpid="p-high", bep="3.0", adgroup_id="grp-high")
    _term(db, "동일검색어", cost=100_000, amt=200_000, adgroup_id="grp-low")   # ROAS 2.0 ≥ 1.2
    _term(db, "동일검색어", cost=100_000, amt=200_000, adgroup_id="grp-high")  # ROAS 2.0 < 3.0
    db.commit()

    out = _build(db)
    assert [c["adgroup_id"] for c in out["candidates"]] == ["grp-high"]


def test_multi_product_group_uses_the_lowest_bep(db):
    """★상품이 2개 이상인 그룹(실측 93개·광고비 40%)은 **가장 낮은 BEP**를 쓴다.

    가장 넘기 쉬운 기준을 통과하면 자르지 않는다 — fail-closed(계약 판단기준 7).
    max를 쓰면 자르는 쪽으로 기울고, 잘못 자르면 매출이 사라진다.
    """
    _product(db, cpid="p1", bep="1.2", name="저BEP")
    _product(db, cpid="p2", bep="3.0", name="고BEP")
    _term(db, "중간검색어", cost=100_000, amt=200_000)  # ROAS 2.0 — 1.2 이상, 3.0 미만
    db.commit()

    out = _build(db)
    assert out["candidates"] == [], "가장 낮은 BEP(1.2)를 넘었으므로 자르지 않는다"

    # 낮은 쪽도 못 넘으면 후보가 되고, 그때 «상품 2개»가 근거 문장에 드러난다.
    db.query(NaverSearchTermDaily).delete()
    _term(db, "확실한적자", cost=100_000, amt=50_000)
    db.commit()
    c = _build(db)["candidates"][0]
    assert c["bep_product_count"] == 2
    assert c["bep_source"] == "product_bep_min"
    assert "상품 2개" in c["reason"]


def test_no_bep_mapping_is_failclosed_but_visible(db):
    """★기준선이 없으면 자르지 않는다. 다만 **그 사실과 금액이 보인다**."""
    _term(db, "매핑없는검색어", cost=90_000, amt=0)
    db.commit()

    out = _build(db)
    assert out["candidates"] == []
    assert out["buckets"]["bep_unknown"] == {"terms": 1, "cost": 90_000}


# ═══ ③ 빠진 것이 세어져 나오는가 ═══


def test_powerlink_is_surfaced_as_undecidable_not_dropped(db):
    """★파워링크는 네이버가 검색어에 전환을 안 준다(근본 한계). 추정으로 메우지 않고 세워 둔다."""
    _product(db, cpid="p1", bep="1.7")
    _term(db, "파워링크검색어", cost=80_000, amt=0, source="expkeyword")
    db.commit()

    out = _build(db)
    assert out["candidates"] == []
    assert out["buckets"]["powerlink_undecidable"] == {"terms": 1, "cost": 80_000}


def test_low_sample_is_not_a_candidate_but_is_counted(db):
    """클릭 표본이 얇으면 «BEP 미달»이 아니라 «아직 판단할 만큼 안 썼다»다."""
    _product(db, cpid="p1", bep="1.7")
    _term(db, "저표본검색어", cost=50_000, amt=0, clk=2)
    db.commit()

    out = _build(db)
    assert out["candidates"] == []
    assert out["buckets"]["insufficient_sample"]["terms"] == 1


def test_cost_below_contribution_margin_is_low_sample(db):
    """비용이 공헌이익 한 개 값에도 못 미치면 아직 «확정 손해»라 부를 수 없다."""
    _product(db, cpid="p1", bep="1.7", margin="30000")
    _term(db, "소액검색어", cost=1_000, amt=0, clk=50)
    db.commit()
    assert _build(db)["buckets"]["insufficient_sample"]["terms"] == 1


def test_recent_days_are_excluded_and_the_exclusion_is_visible(db):
    """★성숙도로 뺀 구간이 **화면에 보인다**(계약 합격기준 4) — 조용히 빠지면 fail-closed가 아니라 은폐다."""
    _product(db, cpid="p1", bep="1.7")
    _term(db, "최근검색어", cost=70_000, amt=0, ad_date=IN_MATURITY)
    db.commit()

    out = _build(db)
    assert out["candidates"] == []
    assert out["maturity"]["lag_days"] == 3
    assert out["maturity"]["excluded_cost"] == 70_000
    assert out["buckets"]["maturity_excluded"]["terms"] == 1


def test_round_cap_reports_what_it_left_out(db):
    """★상한 초과분을 조용히 자르면 화면이 «이게 전부»라고 거짓말한다."""
    _product(db, cpid="p1", bep="1.7")
    for i in range(5):
        _term(db, f"적자{i}", cost=10_000 * (i + 1), amt=0)
    db.commit()

    out = _build(db, round_cap=2)
    assert len(out["candidates"]) == 2
    assert out["buckets"]["capped_out"]["terms"] == 3
    assert out["buckets"]["capped_out"]["cost"] == 10_000 + 20_000 + 30_000


def test_candidates_are_sorted_by_cost_desc(db):
    """광고비 큰 순 — 소량씩 자를 때 무엇을 먼저 볼지가 이 정렬이다(계약 판단기준 4)."""
    _product(db, cpid="p1", bep="1.7")
    for cost in (30_000, 90_000, 60_000):
        _term(db, f"적자{cost}", cost=cost, amt=0)
    db.commit()
    assert [c["cost"] for c in _build(db)["candidates"]] == [90_000, 60_000, 30_000]


def test_whitelisted_term_is_flagged_not_hidden(db):
    """★상품 핵심어는 **표시만** 한다 — 사람이 보는 리스트에서 조용히 빼면 광고비가 가장 큰
    구간(지문방지·강화유리 계열)이 통째로 안 보인다."""
    _product(db, cpid="p1", bep="1.7")
    _term(db, "아이폰케이스싼거", cost=100_000, amt=0)
    db.commit()

    out = _build(db)
    assert len(out["candidates"]) == 1
    assert out["candidates"][0]["whitelisted"] is True


# ═══ 행 하나가 «실행 가능»한가 ═══


def test_every_candidate_carries_what_the_contract_requires(db):
    """계약 합격기준 1 — 검색어·비용·클릭·전환·전환매출·ROAS·적용BEP·사유·되돌림 방법."""
    _product(db, cpid="p1", bep="1.7")
    _term(db, "적자검색어", cost=100_000, amt=30_000, conv=1)
    db.commit()

    c = _build(db)["candidates"][0]
    for key in ("search_term", "cost", "clk", "conv_purchase_cnt", "conv_purchase_amt",
                "roas", "applied_bep", "reason", "revert_howto", "adgroup_id"):
        assert c.get(key) is not None, f"{key}가 비어 있으면 사람이 실행할 수 없다"
    assert "콘솔" in c["revert_howto"]


def test_freshness_stamp_is_present(db):
    """★입력이 언제 것인지 없으면 낡은 데이터로 자를 수 있다(계약 §3 금지선)."""
    _product(db, cpid="p1", bep="1.7")
    _term(db, "적자검색어", cost=100_000, amt=0)
    db.commit()

    f = _build(db)["freshness"]
    assert f["latest_ad_date"] == IN_WINDOW.isoformat()
    assert f["lag_days"] == 10


def test_campaign_filter_narrows_the_round(db):
    _product(db, cpid="p1", bep="1.7")
    _term(db, "적자검색어", cost=100_000, amt=0)
    db.add(NaverSearchTermDaily(
        ad_date=IN_WINDOW, campaign_id="cmp-other", adgroup_id=ADGROUP,
        search_term="다른캠페인검색어", source="shopping", imp=1, clk=50, cost=50_000,
        rank_sum=0, conv_purchase_cnt=0, conv_direct_cnt=0, conv_purchase_amt=0,
    ))
    db.commit()

    out = _build(db, campaign_id=CAMPAIGN)
    assert [c["search_term"] for c in out["candidates"]] == ["적자검색어"]


def test_zero_cost_terms_are_not_in_scope(db):
    """비용이 0이면 자를 것이 없다 — 노출만 된 검색어까지 세면 총계가 부풀어 판단을 흐린다."""
    _product(db, cpid="p1", bep="1.7")
    _term(db, "무비용검색어", cost=0, amt=0)
    db.commit()
    out = _build(db)
    assert out["totals"]["terms"] == 0 and out["candidates"] == []


def test_route_returns_list_with_names(db):
    """배선 — 라우터가 실제로 리스트를 돌려주고 캠페인/그룹 이름을 붙이는가."""
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app
    from app.models import NaverEntity

    _product(db, cpid="p1", bep="1.7")
    _term(db, "적자검색어", cost=100_000, amt=0)
    db.add(NaverEntity(entity_type="campaign", entity_id=CAMPAIGN, name="01. 갤럭시_지문방지_TPU",
                       campaign_id=CAMPAIGN))
    db.add(NaverEntity(entity_type="adgroup", entity_id=ADGROUP, name="폴드8_사생활",
                       campaign_id=CAMPAIGN))
    db.commit()

    app.dependency_overrides[get_db] = lambda: db
    try:
        r = TestClient(app).get("/api/naver/ad/search-term/exclusion-list")
        assert r.status_code == 200
        body = r.json()
        assert body["candidates"][0]["campaign_name"] == "01. 갤럭시_지문방지_TPU"
        assert body["candidates"][0]["adgroup_name"] == "폴드8_사생활"
    finally:
        app.dependency_overrides.clear()
