# test_naver_ops_logistics_totals.py — naver_ops 이익회계의 택배비 SQL 경로 테스트.
# 가드 대상 3종:
#   ① N배송(3,020) vs 일반(1,900) 실단가 — 정액 1,900 회귀 방지
#   ② shipping_cost_paid 결측 시 delivery_attribute_type 폴백 (codex P1)
#      — 메인 엔진(order_delivery)은 3,020을 복구하는데 SQL만 1,900으로 떨어지면 두 엔진이 갈린다
#   ③ 잘린 raw_data에서 json_extract가 던져 엔드포인트가 500 나는 것 (codex P1 조사 중 발견)
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Order
from app.routers.naver_ops import logistics_totals

D = Decimal
NAVER = 6
START = datetime(2026, 7, 1)
END = datetime(2026, 7, 31, 23, 59, 59)
WHEN = datetime(2026, 7, 15, 10, 0, 0)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield s
    finally:
        s.close()


def _add(db, *, pkg, attr=None, paid=None, status="delivered", line="1",
         raw=None, when=WHEN, channel=NAVER):
    if raw is None:
        po = {"packageNumber": pkg}
        if attr:
            po["deliveryAttributeType"] = attr
        raw = json.dumps({"productOrder": po})
    db.add(Order(
        channel_id=channel, order_number=pkg, platform_product_id="p1",
        platform_order_line_id=f"{pkg}-{line}", quantity=1, selling_price=D("10000"),
        order_date=when, status=status, raw_data=raw,
        delivery_attribute_type=attr, shipping_cost_paid=paid,
    ))
    db.flush()


def test_normal_and_nbaesong_priced_separately(db):
    _add(db, pkg="A", attr="TODAY", paid=D("1900"))
    _add(db, pkg="B", attr="ARRIVAL_GUARANTEE", paid=D("3377"))
    assert logistics_totals(db, START, END) == (2, D("5277"))


def test_flat_rate_regression_guard(db):
    # N배송 3건이면 정액 1,900 시절엔 5,700이었다. 9,060이어야 한다.
    for i, p in enumerate("XYZ"):
        _add(db, pkg=p, attr="ARRIVAL_GUARANTEE", paid=D("3377"), line=str(i))
    count, total = logistics_totals(db, START, END)
    assert (count, total) == (3, D("10131"))
    assert total != D("5700")


def test_one_charge_per_package(db):
    for i in range(4):  # 한 패키지에 라인 4개
        _add(db, pkg="A", attr="ARRIVAL_GUARANTEE", paid=D("3377"), line=str(i))
    assert logistics_totals(db, START, END) == (1, D("3377"))


def test_mixed_package_takes_max(db):
    _add(db, pkg="A", attr="TODAY", paid=D("1900"), line="1")
    _add(db, pkg="A", attr="ARRIVAL_GUARANTEE", paid=D("3377"), line="2")
    assert logistics_totals(db, START, END) == (1, D("3377"))


# ── codex P1: 스냅샷 결측 시 배송속성 폴백 ─────────────────────────────────
def test_null_snapshot_falls_back_to_delivery_attribute(db):
    # shipping_cost_paid가 비어도 배송속성이 N배송이면 3,020 — 메인 엔진과 같은 답.
    _add(db, pkg="A", attr="ARRIVAL_GUARANTEE", paid=None)
    count, total = logistics_totals(db, START, END)
    assert (count, total) == (1, D("3377"))
    assert total != D("1900")  # 이 값이 나오면 두 엔진이 갈린 것


def test_null_snapshot_without_attribute_is_normal_rate(db):
    _add(db, pkg="A", attr=None, paid=None)
    assert logistics_totals(db, START, END) == (1, D("1900"))


# ── codex P1 조사 중 발견: 잘린 raw_data에서 json_extract가 던진다 ─────────
def test_truncated_raw_data_does_not_raise(db):
    # raw_data는 MAX_RAW_DATA_SIZE 초과 시 잘려 저장된다(라이브 실재). json_valid 가드가
    # 없으면 SQLite json_extract가 예외를 던져 엔드포인트가 통째로 500이 난다.
    _add(db, pkg="A", attr="TODAY", paid=D("1900"))
    _add(db, pkg="BROKEN", attr="ARRIVAL_GUARANTEE", paid=D("3377"),
         raw='{"productOrder": {"packageNum')  # 잘린 JSON
    count, total = logistics_totals(db, START, END)
    assert (count, total) == (2, D("5277"))  # 잘린 행은 order_number를 배송키로 쓴다


# ── 경계 ───────────────────────────────────────────────────────────────────
def test_cancelled_and_returned_excluded(db):
    _add(db, pkg="A", attr="TODAY", paid=D("1900"))
    _add(db, pkg="B", attr="ARRIVAL_GUARANTEE", paid=D("3377"), status="cancelled")
    _add(db, pkg="C", attr="ARRIVAL_GUARANTEE", paid=D("3377"), status="returned")
    assert logistics_totals(db, START, END) == (1, D("1900"))


def test_other_channels_excluded(db):
    _add(db, pkg="A", attr="TODAY", paid=D("1900"))
    _add(db, pkg="B", attr=None, paid=None, channel=1)
    assert logistics_totals(db, START, END) == (1, D("1900"))


def test_outside_window_excluded(db):
    _add(db, pkg="A", attr="TODAY", paid=D("1900"))
    _add(db, pkg="B", attr="ARRIVAL_GUARANTEE", paid=D("3377"),
         when=datetime(2026, 6, 1, 10, 0, 0))
    assert logistics_totals(db, START, END) == (1, D("1900"))


def test_empty_window_returns_zero(db):
    assert logistics_totals(db, START, END) == (0, D("0"))
