# 원가 미상 매출 표면화(2026-08-04, D-NAO-148) 계약 테스트.
#
# 가드 대상: 손익 엔진은 원가를 못 찾으면 **조용히 더하지 않았다**(`if pid in product_map`).
#   그래서 "원가 0원짜리 상품"과 "원가를 모르는 상품"이 손익에서 완전히 같은 모양이 됐고,
#   후자는 판 돈이 전부 이익으로 잡히는데 아무 경보도 안 울렸다.
#   라이브 실측(prod 최근 30일): 원가 미상 매출 1,310,900원 → 순이익 약 19만원 과대.
#
# 특히 지키는 것:
#   ① 원가 미상은 **매출을 따로 센다**(unmapped_revenue) — 세지 않으면 영원히 안 보인다.
#   ② **순이익은 바뀌지 않는다** — 이번 변경은 표시 전용이다. 원가를 추정해 채우면
#      "실측 원가"와 구분이 사라진다(LESSONS #117이 그 실패다).
#   ③ `cost_price=0`도 미상으로 본다 — NOT NULL default 0이라 미입력과 실제 0을 구분할 수 없다.
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base, Channel, Order, ProductMaster
from app.services.profit_calculator import calculate_daily_trend

D = Decimal
NAVER_CH = 6
WIN = (date(2026, 7, 1), date(2026, 7, 31))


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    s.add(Channel(id=NAVER_CH, name="네이버", code="NAVER", platform="naver", channel_type="own"))
    s.commit()
    try:
        yield s
    finally:
        s.close()


def _product(db, *, pid, cost):
    p = ProductMaster(id=pid, internal_sku=f"SKU{pid}", product_name=f"상품{pid}", cost_price=D(str(cost)))
    db.add(p)
    db.commit()
    return p


def _order(db, *, num, product_id, price="10000", qty=1):
    o = Order(
        channel_id=NAVER_CH, order_number=num, platform_product_id=f"opt-{num}",
        product_id=product_id, quantity=qty, selling_price=D(price),
        order_date=datetime(2026, 7, 5, 10, 0), status="delivered",
        commission_amount=D("0"),
    )
    db.add(o)
    db.commit()
    return o


def _row(db):
    rows = calculate_daily_trend(db, None, NAVER_CH, *WIN)
    return next(r for r in rows if r["date"] == "2026-07-05")


def test_mapped_product_with_cost_is_not_counted_as_unmapped(db):
    _product(db, pid=1, cost="3000")
    _order(db, num="A1", product_id=1)
    r = _row(db)
    assert D(r["cost"]) == D("3000")
    assert D(r["unmapped_revenue"]) == D("0")


def test_unlinked_order_counts_its_revenue_as_unmapped(db):
    """상품 연결이 없는 주문 — 원가는 0으로 남지만 매출이 '미상'으로 세어진다."""
    _order(db, num="A2", product_id=None)
    r = _row(db)
    assert D(r["cost"]) == D("0")
    assert D(r["unmapped_revenue"]) == D("10000")


def test_zero_cost_product_is_also_unmapped(db):
    """cost_price=0은 NOT NULL default 0 탓에 '미입력'과 구분되지 않는다 → 미상으로 본다."""
    _product(db, pid=2, cost="0")
    _order(db, num="A3", product_id=2)
    r = _row(db)
    assert D(r["unmapped_revenue"]) == D("10000")


def test_dangling_product_id_is_unmapped(db):
    """product_id는 있는데 마스터에 없는 행(끊긴 참조) — 라이브에 실제로 있었다."""
    _order(db, num="A4", product_id=999)
    r = _row(db)
    assert D(r["unmapped_revenue"]) == D("10000")


def test_net_profit_is_unchanged_by_this_feature(db):
    """★표시 전용이다 — 미상 매출이 있어도 순이익 계산식은 종전과 같다.

    원가를 추정해 채우면 이 단언이 깨진다(그게 하면 안 되는 일이다)."""
    _order(db, num="A5", product_id=None)
    r = _row(db)
    rev, cost = D(r["revenue"]), D(r["cost"])
    comm, ship, ad = D(r["commission"]), D(r["shipping"]), D(r["ad_spend"])
    fixed = D(r["fixed_cost"])
    gross = rev - cost - comm - ship - ad - fixed
    assert abs(D(r["net_profit"]) - gross / D("1.1")) < D("0.01")


def test_mixed_lines_split_correctly(db):
    _product(db, pid=3, cost="4000")
    _order(db, num="B1", product_id=3)
    _order(db, num="B2", product_id=None)
    r = _row(db)
    assert D(r["cost"]) == D("4000")
    assert D(r["unmapped_revenue"]) == D("10000")
    assert D(r["revenue"]) == D("20000")   # 미상분도 매출에는 그대로 들어간다
