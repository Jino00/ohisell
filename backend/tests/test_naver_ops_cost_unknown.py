# test_naver_ops_cost_unknown.py — «원가 모름»을 «원가 0원»으로 계산하지 않는다는 가드
#
# ★무엇을 고정하나(2026-08-06 라이브 발견): 손익 화면이 원가 미상 상품을 **이익률 94~96%**로
#   표시하고 있었다. 원인은 `cost_map.get(pid, 0)` — 원가를 못 찾으면 0원짜리 원가로 접혔다.
#   30일 실측: 6개 상품·공급가 매출 956,545원(전체의 2.5%)이 원가 0으로 계산됐고, 원가가 있는
#   상품들의 평균 원가율이 22.1%였으니 이익이 약 21만원 과대였다.
#   1위가 하필 당시 주력이던 폴드8 필름(13687558206, 30일 822,545원)이었다.
#
# ★이건 같은 날 광고비에 세운 원칙(«모름»은 0이 아니다, 교훈 #151)의 미적용 케이스였다.
#   광고비 pending과 같은 규율을 쓴다: **원가·이익·이익률 셋 다** 비운다. 셋 중 하나라도
#   숫자로 남기면 그 카드가 나머지를 부정한다 — 훑는 눈에는 큰 숫자가 이긴다.
#
# ★두 종류의 «모름»을 구분한다 — Jino가 할 조치가 다르기 때문이다.
#     unmapped  = product_channel_mapping에 활성 행이 없음 → 매핑을 이어야 한다
#     zero_cost = 매핑은 있는데 product_master.cost_price가 0/NULL → 원가를 입력해야 한다
#   라이브 분포(2026-08-06): 6개 중 5개가 unmapped, 활성 매핑 2,766개 중 127개가 cost_price=0.
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Channel, Order, ProductChannelMapping, ProductMaster
from app.routers.naver_ops import sales_summary
from app.utils.kst import kst_today

D = Decimal
NAVER_ID = 6


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


def _order(db, *, pid: str, name: str, price: str, qty: int = 1, no: str | None = None) -> None:
    """어제 주문 1건(days=7 창 안). 수수료는 단순화를 위해 0."""
    db.add(Order(
        channel_id=NAVER_ID, order_number=no or f"ORD-{pid}",
        platform_product_id=pid, platform_product_name=name,
        quantity=qty, selling_price=D(price), shipping_cost=D("0"),
        commission_amount=D("0"), status="DELIVERED",
        order_date=datetime.combine(kst_today() - timedelta(days=1), datetime.min.time())
        + timedelta(hours=12),
    ))
    db.commit()


def _mapped(db, *, pid: str, cost: str | None, master_id: int, active: bool = True) -> None:
    db.add(ProductMaster(id=master_id, internal_sku=f"SKU-{master_id}",
                         product_name=f"마스터 {master_id}",
                         cost_price=None if cost is None else D(cost)))
    db.add(ProductChannelMapping(product_id=master_id, channel_id=NAVER_ID,
                                 channel_product_id=pid, is_active=active))
    db.commit()


def _row(out: dict, pid: str) -> dict:
    return next(r for r in out["by_product"] if r["platform_id"] == pid)


# ── 상품별: «모름»은 0이 아니다 ──────────────────────────────────────

def test_unmapped_product_reports_unknown_not_zero_cost(db):
    """★핵심 회귀 — 매핑이 없는 상품은 원가 0원이 아니라 «모름»이다.

    종전엔 cost="0.00" · profit=매출−수수료 · profit_rate≈94%가 나왔다.
    """
    _order(db, pid="13687558206", name="폴드8 필름", price="110000")

    out = sales_summary(days=7, db=db)
    r = _row(out, "13687558206")

    assert r["cost"] is None            # 0원이 아니다
    assert r["profit"] is None          # 원가를 모르면 이익도 모른다
    assert r["profit_rate"] is None     # ★94.85%가 여기 있었다
    assert r["cost_known"] is False
    assert r["cost_unknown_kind"] == "unmapped"
    # 매출·수수료는 실측이므로 그대로 낸다 — 모르는 것만 비운다.
    assert r["revenue"] == "100000.00"


def test_mapped_but_zero_cost_is_unknown_too(db):
    """매핑은 있는데 cost_price=0 — 이것도 «모름»이다(라이브 127개 매핑이 이 상태).

    "0원에 사왔다"는 뜻일 수가 없다. 조치는 unmapped와 달라서 종류를 나눠 낸다.
    """
    _order(db, pid="6891081964", name="애플워치 스트랩", price="22000")
    _mapped(db, pid="6891081964", cost="0", master_id=772)

    out = sales_summary(days=7, db=db)
    r = _row(out, "6891081964")

    assert r["cost"] is None
    assert r["profit"] is None
    assert r["profit_rate"] is None
    assert r["cost_unknown_kind"] == "zero_cost"


def test_null_cost_price_is_unknown(db):
    """cost_price가 NULL인 마스터도 «모름»(라이브 1건)."""
    _order(db, pid="P-NULL", name="원가 NULL", price="11000")
    _mapped(db, pid="P-NULL", cost=None, master_id=901)

    out = sales_summary(days=7, db=db)

    assert _row(out, "P-NULL")["cost_unknown_kind"] == "zero_cost"


def test_inactive_mapping_counts_as_unmapped(db):
    """비활성 매핑만 있으면 원가가 안 붙는다 — 결과가 같으므로 unmapped로 센다."""
    _order(db, pid="P-INACT", name="비활성 매핑", price="11000")
    _mapped(db, pid="P-INACT", cost="5000", master_id=902, active=False)

    out = sales_summary(days=7, db=db)

    assert _row(out, "P-INACT")["cost_unknown_kind"] == "unmapped"


def test_known_cost_is_unchanged(db):
    """★원가가 정상인 상품은 표시가 바뀌지 않는다(라이브 298개가 이쪽).

    이 가드가 없으면 «모름» 처리가 정상 상품까지 비우는 회귀를 못 잡는다.
    """
    _order(db, pid="P-OK", name="정상 상품", price="110000", qty=2)
    _mapped(db, pid="P-OK", cost="22000", master_id=903)

    out = sales_summary(days=7, db=db)
    r = _row(out, "P-OK")

    assert r["cost_known"] is True
    assert r["cost_unknown_kind"] is None
    assert r["cost"] == "40000.00"          # 22,000 × 2 ÷ 1.1
    assert r["profit"] == "60000.00"        # 100,000 − 0(수수료) − 40,000
    assert r["profit_rate"] == "60.00"


# ── 요약: 계속 계산하되 얼마가 비었는지 말한다 ──────────────────────

def test_summary_still_computes_and_surfaces_unknown_counts(db):
    """요약 이익은 **계속 낸다**(미상분 원가가 빠진 채) + 그 사실을 수치로 표면화.

    ★왜 요약까지 「—」로 만들지 않나: 미상은 매출의 2.5%인데 그것 때문에 나머지 97.5%를
      못 보게 하면 화면이 쓸모없어진다. 대신 얼마짜리 매출이 원가 없이 계산됐는지를 낸다.
    """
    _order(db, pid="P-OK", name="정상", price="110000")
    _mapped(db, pid="P-OK", cost="22000", master_id=903)
    _order(db, pid="P-UNMAP", name="매핑 없음", price="55000")
    _order(db, pid="P-ZERO", name="원가 0", price="33000")
    _mapped(db, pid="P-ZERO", cost="0", master_id=904)

    out = sales_summary(days=7, db=db)
    s = out["summary"]

    assert s["cost_unknown_products"] == 2
    assert s["cost_unknown_unmapped"] == 1
    assert s["cost_unknown_zero_cost"] == 1
    # 공급가 매출 (55,000 + 33,000) ÷ 1.1
    assert s["cost_unknown_revenue"] == "80000.00"
    # 요약 원가는 아는 것만 — 미상분이 빠져 있다는 사실은 위 카운트가 말한다.
    assert s["cost"] == "20000.00"
    assert s["profit"] is not None          # 요약은 「—」로 만들지 않는다


def test_no_unknown_cost_reports_zero_counts(db):
    """전부 원가가 있으면 카운트가 0 — 배너가 뜨지 않아야 한다(거짓 경보 방지)."""
    _order(db, pid="P-OK", name="정상", price="110000")
    _mapped(db, pid="P-OK", cost="22000", master_id=903)

    out = sales_summary(days=7, db=db)

    assert out["summary"]["cost_unknown_products"] == 0
    assert out["summary"]["cost_unknown_revenue"] == "0.00"


def test_same_product_multiple_orders_counted_once(db):
    """같은 상품이 여러 번 팔려도 «미상 상품 수»는 1이다(라인 수가 아니라 상품 수)."""
    _order(db, pid="P-UNMAP", name="매핑 없음", price="55000", no="ORD-1")
    _order(db, pid="P-UNMAP", name="매핑 없음", price="55000", no="ORD-2")

    out = sales_summary(days=7, db=db)

    assert out["summary"]["cost_unknown_products"] == 1
    assert out["summary"]["cost_unknown_revenue"] == "100000.00"   # 매출은 합산


# ── 근본 수정: 원가 후보가 갈리면 고르지 않는다 (2026-08-06 적대 리뷰 P1) ──

def test_ambiguous_cost_is_unknown_not_silently_picked(db):
    """★★한 옵션ID에 활성 매핑이 둘이고 원가가 다르면 «모름»이다 — 조용히 고르지 않는다.

    종전 `min(costed, key=lambda x: x[1])`는 정렬 키가 **원가가 아니라 product_master.id**라,
    "원가가 맞는 쪽"이 아니라 "id가 작은 쪽"을 뽑았다. 그 값은 cost_map에 들어가므로
    «모름» 배너에도 안 잡혀 **화면이 자신 있게 틀렸다.**
    라이브(2026-08-06): 네이버 이중 활성 매핑 22건 — 원가가 갈리는 건 0건이라 아직 안 터졌을 뿐.
    """
    _order(db, pid="P-AMB", name="이중 매핑", price="110000")
    _mapped(db, pid="P-AMB", cost="9000", master_id=10)    # id 작음 — 종전엔 이게 뽑혔다
    _mapped(db, pid="P-AMB", cost="15000", master_id=55)   # 실제 정확한 원가

    out = sales_summary(days=7, db=db)
    r = _row(out, "P-AMB")

    assert r["cost"] is None                       # 9,000원이 여기 있었다
    assert r["profit"] is None
    assert r["cost_unknown_kind"] == "ambiguous"   # 사유가 셋째 종류
    assert out["summary"]["cost_unknown_ambiguous"] == 1
    assert out["summary"]["cost_unknown_products"] == 1


def test_same_cost_on_duplicate_mappings_is_still_known(db):
    """중복 매핑이어도 **원가가 같으면** 모호하지 않다 — 거짓 경보를 내지 않는다.

    라이브 22건이 전부 이 상태다(원가 distinct=1). 이 가드가 없으면 배포 즉시 22건이
    «모름»으로 뒤집혀 멀쩡한 손익이 사라진다.
    """
    _order(db, pid="P-DUP", name="중복이나 동일원가", price="110000")
    _mapped(db, pid="P-DUP", cost="22000", master_id=11)
    _mapped(db, pid="P-DUP", cost="22000", master_id=56)

    out = sales_summary(days=7, db=db)
    r = _row(out, "P-DUP")

    assert r["cost_known"] is True
    assert r["cost"] == "20000.00"      # 22,000 ÷ 1.1
    assert out["summary"]["cost_unknown_products"] == 0


def test_negative_cost_never_becomes_known(db):
    """★음수 원가는 «모름»이다 — 종전엔 fallback이 음수를 골라 "원가 있음"으로 나갔다.

    원가가 음수면 이익에서 빼는 게 아니라 **더한다** — 과대의 방향이 뒤집힌 채 커진다.
    (스키마에도 ge=0을 걸었지만, 이미 들어와 있는 값은 여기서 죽어야 한다.)
    """
    _order(db, pid="P-NEG", name="음수 원가", price="110000")
    _mapped(db, pid="P-NEG", cost="-500", master_id=12)

    out = sales_summary(days=7, db=db)
    r = _row(out, "P-NEG")

    assert r["cost"] is None
    assert r["profit"] is None
    assert r["cost_unknown_kind"] == "zero_cost"   # 양수 후보 없음 = 원가 입력 필요
