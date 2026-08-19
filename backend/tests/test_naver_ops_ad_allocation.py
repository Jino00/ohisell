# test_naver_ops_ad_allocation.py — D-NAO-207 상품별 광고비·물류비 배분 가드
#
# ★무엇을 고정하나(2026-08-19 Jino 지적): 손익 화면의 상품 행이 「이익」이라는 이름으로
#   **매출총이익**을 내고 있었다. 라이브 실측(2026-08-18): 상품 행 이익 합 993,330원
#   (상품매출 대비 71.0%) vs 계정 실제 338,395원(총매출 대비 21.1%) — **50%p** 격차.
#   빠져 있던 것은 광고비 606,875원(원가의 1.8배)과 물류비 249,878원 전액.
#
# ★이 파일이 지키는 계약 셋:
#   ① **검산이 화면 안에 있다** — Σ(상품 이익) + 미상제외분 + 미배분 = 요약 이익.
#      «요약 − 배분합»으로 미배분을 정의했으므로 새 누수 사유가 생겨도 반드시 이 행에 뜬다.
#   ② **없는 조인을 지어내지 않는다** — 파워링크(소재=키워드)·디스플레이(상품 필드 없음)·
#      원장 창 밖은 추정 배분하지 않고 미배분으로 남긴다(D-NAO-194가 기각한 방식).
#   ③ **모호하면 고르지 않는다** — ad_id가 두 상품에 매핑되면 배분하지 않는다.
#
# ★HTTP 경계까지 본다(교훈 #321): 서비스층 dict가 초록인데 HTTP body에서 키가 사라진 사고가
#   바로 전날(D-NAO-204) 있었다. `response_model`이 없어도 **없다는 사실을 테스트가 고정**한다.
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import (
    AdCost, Channel, NaverAdCreativeDaily, NaverAdgroupProduct, Order,
    ProductChannelMapping, ProductMaster,
)
from app.routers.naver_ops import router, sales_summary
from app.utils.kst import kst_today

D = Decimal
NAVER_ID = 6
V = D("1.1")


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    s.add(Channel(id=NAVER_ID, code="NAVER", name="네이버 스마트스토어",
                  platform="naver", channel_type="own"))
    s.commit()
    yield s
    s.close()


def _yesterday():
    return kst_today() - timedelta(days=1)


def _order(db, *, pid, price, qty=1, no=None, ship="0", pkg=None, name=None):
    """어제 주문 1라인. `pkg`를 주면 raw_data에 packageNumber를 심어 같은 패키지로 묶는다."""
    raw = None
    if pkg:
        raw = '{"productOrder": {"packageNumber": "%s"}}' % pkg
    db.add(Order(
        channel_id=NAVER_ID, order_number=no or f"ORD-{pid}",
        platform_product_id=pid, platform_product_name=name or f"상품 {pid}",
        quantity=qty, selling_price=D(price), shipping_cost=D(ship),
        commission_amount=D("0"), status="DELIVERED", raw_data=raw,
        order_date=datetime.combine(_yesterday(), datetime.min.time()) + timedelta(hours=12),
    ))
    db.commit()


def _mapped(db, *, pid, cost, master_id):
    db.add(ProductMaster(id=master_id, internal_sku=f"SKU-{master_id}",
                         product_name=f"마스터 {master_id}", cost_price=D(cost)))
    db.add(ProductChannelMapping(product_id=master_id, channel_id=NAVER_ID,
                                 channel_product_id=pid, is_active=True))
    db.commit()


def _creative(db, *, ad_id, cost, ctype="SHOPPING", day=None):
    db.add(NaverAdCreativeDaily(ad_date=day or _yesterday(), ad_id=ad_id,
                                campaign_type=ctype, cost=cost))
    db.commit()


def _ad_product(db, *, ad_id, mall_pid, adgroup_id):
    db.add(NaverAdgroupProduct(adgroup_id=adgroup_id, mall_product_id=mall_pid, ad_id=ad_id))
    db.commit()


def _ad_cost(db, *, spend, source="naver_sa:test", day=None):
    db.add(AdCost(channel_id=NAVER_ID, ad_date=day or _yesterday(),
                  source=source, ad_spend=D(spend), ad_revenue=D("0")))
    db.commit()


def _row(out, pid):
    return next(r for r in out["by_product"] if r["platform_id"] == pid)


def _closes(out):
    """검산 등식이 성립하는가 — 이 파일 전체가 매번 확인하는 불변식."""
    rec = out["reconciliation"]
    total = (D(rec["sum_product_profit"]) + D(rec["unknown_cost_profit"])
             + D(rec["unallocated_profit"]))
    assert abs(total - D(rec["summary_profit"])) <= 1, rec
    assert rec["closes"] is True, rec


# ── ① 검산 등식 ─────────────────────────────────────────────────────

def test_reconciliation_closes_with_shopping_ad(db):
    """Σ(상품) + 미배분 = 요약. 광고비가 상품에 붙어도 총합은 흔들리지 않는다."""
    _order(db, pid="P1", price="110000")
    _mapped(db, pid="P1", cost="30000", master_id=901)
    _creative(db, ad_id="nad-1", cost=50000)
    _ad_product(db, ad_id="nad-1", mall_pid="P1", adgroup_id="grp-1")
    _ad_cost(db, spend="50000")

    out = sales_summary(days=7, db=db)
    _closes(out)
    r = _row(out, "P1")
    assert r["ad_spend"] == "50000.00"      # 전액 이 상품에 귀속
    assert r["ad_allocated"] is True
    assert out["unallocated"]["ad_spend"] == "0.00"


def test_reconciliation_closes_when_cost_unknown(db):
    """★원가 미상 상품이 섞여도 등식이 닫힌다 — 미상분을 별도 항으로 빼 두기 때문이다.

    이 가드가 없으면 미상이 1건만 생겨도 Σ(상품)+미배분 ≠ 요약이 되고, 그 차이가
    «결함»인지 «미상»인지 화면에서 구분되지 않는다.
    """
    _order(db, pid="P1", price="110000")
    _mapped(db, pid="P1", cost="30000", master_id=901)
    _order(db, pid="P-NOCOST", price="55000")     # 매핑 없음 → 원가 미상
    _ad_cost(db, spend="10000")

    out = sales_summary(days=7, db=db)
    _closes(out)
    assert _row(out, "P-NOCOST")["profit"] is None
    assert D(out["reconciliation"]["unknown_cost_profit"]) != 0


# ── ② 없는 조인을 지어내지 않는다 ────────────────────────────────────

def test_powerlink_is_never_allocated(db):
    """파워링크(WEB_SITE)는 소재가 키워드라 상품 축이 원리적으로 없다 → 전액 미배분."""
    _order(db, pid="P1", price="110000")
    _mapped(db, pid="P1", cost="30000", master_id=901)
    _creative(db, ad_id="nad-web", cost=70000, ctype="WEB_SITE")
    # WEB_SITE 소재에 상품 매핑이 «있어도» 붙이지 않는다(캠페인 유형이 게이트다).
    _ad_product(db, ad_id="nad-web", mall_pid="P1", adgroup_id="grp-web")
    _ad_cost(db, spend="70000")

    out = sales_summary(days=7, db=db)
    _closes(out)
    assert _row(out, "P1")["ad_spend"] == "0.00"
    assert out["unallocated"]["ad_spend"] == "70000.00"


def test_display_ad_goes_unallocated(db):
    """디스플레이(GFA·ADVoost)는 소재 원장에 아예 없다 → 잔차로 미배분에 나타난다."""
    _order(db, pid="P1", price="110000")
    _mapped(db, pid="P1", cost="30000", master_id=901)
    _creative(db, ad_id="nad-1", cost=40000)
    _ad_product(db, ad_id="nad-1", mall_pid="P1", adgroup_id="grp-1")
    _ad_cost(db, spend="40000", source="naver_sa:x")
    _ad_cost(db, spend="15000", source="gfa:advoost")

    out = sales_summary(days=7, db=db)
    _closes(out)
    assert _row(out, "P1")["ad_spend"] == "40000.00"
    assert out["unallocated"]["ad_spend"] == "15000.00"   # 디스플레이만 남는다


def test_uncovered_dates_are_reported_not_zeroed(db):
    """★소재 원장이 없는 날은 «광고비 0»이 아니라 «배분 불가»다.

    원장 창 밖 구간을 0으로 접으면 화면이 자신 있게 틀린다 — 그 상품이 광고를 안 쓴
    것처럼 보이고 이익률이 다시 과대해진다.
    """
    _order(db, pid="P1", price="110000")
    _mapped(db, pid="P1", cost="30000", master_id=901)
    _ad_cost(db, spend="80000")               # 광고비는 있는데
    # 소재 원장(NaverAdCreativeDaily)은 한 행도 없다 → 그날은 못 덮는다

    out = sales_summary(days=7, db=db)
    _closes(out)
    assert _row(out, "P1")["ad_spend"] == "0.00"
    assert out["unallocated"]["ad_spend"] == "80000.00"
    assert str(_yesterday()) in out["ad_alloc"]["uncovered_dates"]


# ── ③ 모호하면 고르지 않는다 ─────────────────────────────────────────

def test_ambiguous_ad_mapping_is_not_allocated(db):
    """ad_id 하나가 두 상품에 매핑되면 **어느 쪽도 고르지 않는다**.

    테이블 unique는 (adgroup_id, mall_product_id)라 구조상 가능하다. 아무거나 고르면
    그 상품의 이익이 조용히 틀리고, 둘 다 붙이면 광고비가 이중 계상된다.
    """
    _order(db, pid="P1", price="110000")
    _mapped(db, pid="P1", cost="30000", master_id=901)
    _order(db, pid="P2", price="110000", no="ORD-2")
    _mapped(db, pid="P2", cost="30000", master_id=902)
    _creative(db, ad_id="nad-dup", cost=60000)
    _ad_product(db, ad_id="nad-dup", mall_pid="P1", adgroup_id="grp-a")
    _ad_product(db, ad_id="nad-dup", mall_pid="P2", adgroup_id="grp-b")   # 같은 소재, 다른 상품
    _ad_cost(db, spend="60000")

    out = sales_summary(days=7, db=db)
    _closes(out)
    assert _row(out, "P1")["ad_spend"] == "0.00"
    assert _row(out, "P2")["ad_spend"] == "0.00"
    assert out["ad_alloc"]["ambiguous_ads"] == 1
    assert out["ad_alloc"]["ambiguous_cost"] == "60000.00"
    assert out["unallocated"]["ad_spend"] == "60000.00"


# ── ④ 물류비 배분 ───────────────────────────────────────────────────

def test_single_product_package_gets_full_logistics(db):
    """단일 상품 패키지는 «비례»가 아니라 전액 실측 귀속(라이브 103건 중 100건)."""
    _order(db, pid="P1", price="110000")
    _mapped(db, pid="P1", cost="30000", master_id=901)

    out = sales_summary(days=7, db=db)
    _closes(out)
    assert _row(out, "P1")["logistics"] == "1727.27"     # 1,900 ÷ 1.1
    assert out["unallocated"]["logistics"] == "0.00"


def test_multi_product_package_splits_by_revenue_without_leak(db):
    """다상품 패키지는 상품매출 비례 — 그리고 **합이 패키지 단가와 정확히 같다**.

    마지막 라인에 잔여를 몰아주므로 반올림 누수가 0이다(누수가 있으면 요약과 갈린다).
    """
    _order(db, pid="P1", price="110000", no="ORD-M", pkg="PKG-1")
    _order(db, pid="P2", price="55000", no="ORD-M2", pkg="PKG-1")
    _mapped(db, pid="P1", cost="10000", master_id=901)
    _mapped(db, pid="P2", cost="10000", master_id=902)

    out = sales_summary(days=7, db=db)
    _closes(out)
    got = D(_row(out, "P1")["logistics"]) + D(_row(out, "P2")["logistics"])
    assert abs(got - (D("1900") / V)) <= D("0.01")        # 패키지 1건분, 누수 없음
    # 매출 2:1 → 물류비도 2:1 쪽이 크다
    assert D(_row(out, "P1")["logistics"]) > D(_row(out, "P2")["logistics"])


def test_delivery_revenue_lands_on_the_product_row(db):
    """고객배송비는 이미 라인 단위 — 상품 행 매출에 들어가고 총매출 축이 카드와 같아진다."""
    _order(db, pid="P1", price="110000", ship="3300")
    _mapped(db, pid="P1", cost="30000", master_id=901)

    out = sales_summary(days=7, db=db)
    _closes(out)
    r = _row(out, "P1")
    assert r["revenue"] == "100000.00"
    assert r["delivery_revenue"] == "3000.00"
    assert r["revenue_total"] == "103000.00"
    assert out["summary"]["revenue"] == "103000.00"       # 표 합계 = 카드


# ── ⑤ HTTP 경계 (교훈 #321) ─────────────────────────────────────────

def test_new_keys_survive_the_http_boundary(db):
    """★서비스층 dict가 아니라 **실제 HTTP body**에 키가 실리는지 본다.

    바로 전날(D-NAO-204) `response_model`이 새 키를 응답에서 지워 배너가 통째로 숨은
    사고가 있었다. 이 라우터엔 지금 `response_model`이 없는데, **없다는 사실 자체를**
    테스트가 고정한다 — 나중에 누가 붙이면 여기서 빨간불이 난다.
    """
    _order(db, pid="P1", price="110000")
    _mapped(db, pid="P1", cost="30000", master_id=901)
    _creative(db, ad_id="nad-1", cost=50000)
    _ad_product(db, ad_id="nad-1", mall_pid="P1", adgroup_id="grp-1")
    _ad_cost(db, spend="50000")

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    res = TestClient(app).get("/api/naver/ops/sales-summary?days=7")

    assert res.status_code == 200
    body = res.json()
    for key in ("unallocated", "ad_alloc", "reconciliation"):
        assert key in body, f"HTTP body에서 {key}가 사라졌다"
    assert body["reconciliation"]["closes"] is True
    assert body["unallocated"]["profit"] is not None
    row = next(r for r in body["by_product"] if r["platform_id"] == "P1")
    for key in ("ad_spend", "logistics", "revenue_total", "delivery_revenue", "ad_allocated"):
        assert key in row, f"HTTP body의 상품 행에서 {key}가 사라졌다"
    assert body["summary"]["ad_allocated"] == "50000.00"
    assert body["summary"]["ad_unallocated"] == "0.00"
