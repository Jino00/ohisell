# test_intelligence_seller_shipping.py — 종합조망 3P 판매자 배송비(한진) 차감 머니 테스트
# Jino 2026-06-15: 구 대시보드는 한진 배송비를 차감하나 종합조망은 누락 → 종합조망에 추가(계정 단위).
# 3P만 발생(RG/로켓=쿠팡 풀필먼트). 구 대시보드 _shipment_key dedup·HANJIN 단가 재사용(SoT).
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Channel, Order
from app.services.coupang.intelligence import (
    _agg_seller_shipping_3p,
    compute_command_center,
)

D = Decimal
WIN = (date(2026, 6, 1), date(2026, 6, 30))


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _ch(db, code, ctype="marketplace"):
    c = Channel(name=code, code=code, platform="coupang", channel_type=ctype,
                commission_rate=D("7.8"), company="오픽스")
    db.add(c)
    db.flush()
    return c.id


def _ord(db, cid, num, vid, box, price="10000", status="delivered", day=5):
    db.add(Order(
        channel_id=cid, order_number=num, platform_product_id=vid,
        selling_price=D(price), quantity=1, order_date=datetime(2026, 6, day, 10, 0),
        status=status, raw_data=json.dumps({"shipmentBoxId": box}),
    ))


def test_shipping_dedup_by_shipment_box(db):
    cid = _ch(db, "COUPANG_WING1")
    _ord(db, cid, "O1", "V1", "BOX1")
    _ord(db, cid, "O2", "V2", "BOX1")   # 같은 박스 → 배송 1회
    _ord(db, cid, "O3", "V3", "BOX2")   # 다른 박스
    db.commit()
    # 물리 배송 2건 × 1,900 = 3,800
    assert _agg_seller_shipping_3p(db, WIN[0], WIN[1], [cid])['cost'] == D("3800")


def test_shipping_excludes_cancelled(db):
    cid = _ch(db, "COUPANG_WING1")
    _ord(db, cid, "O1", "V1", "BOX1", status="cancelled")  # REVENUE_EXCLUDED
    db.commit()
    assert _agg_seller_shipping_3p(db, WIN[0], WIN[1], [cid])['cost'] == D("0")


def test_shipping_excludes_rg_marketplace_channel(db):
    # RG 채널은 marketplace 타입이지만 쿠팡 풀필먼트 → 한진비 없음 (codex P1: consignment 필터로 안 걸림)
    rg = _ch(db, "COUPANG_RG1")  # marketplace, 3P 코드 아님
    _ord(db, rg, "R1", "V1", "BOX1")
    db.commit()
    assert _agg_seller_shipping_3p(db, WIN[0], WIN[1], [rg])['cost'] == D("0")


def test_shipping_zero_when_no_orders(db):
    cid = _ch(db, "COUPANG_WING1")
    db.commit()
    assert _agg_seller_shipping_3p(db, WIN[0], WIN[1], [cid])['cost'] == D("0")


def test_command_center_subtracts_3p_shipping(db):
    cid = _ch(db, "COUPANG_WING1")
    _ord(db, cid, "O1", "V1", "BOX1", price="10000")
    db.commit()
    acc = compute_command_center(db, WIN[0], WIN[1])["account"]["summary"]
    assert D(str(acc["seller_shipping_3p"])) == D("1900")
    assert D(str(acc["shipping_income_3p"])) == D("0"), "이 주문엔 고객이 낸 배송비가 없다"
    # D-CPP-33 이후 체인: pre_shipping → −한진비 +배송수입 → pre_vat → −납부세액 → net
    pre_ship = D(str(acc["net_profit_pre_shipping"]))
    assert D(str(acc["net_profit_pre_vat"])) == pre_ship - D("1900") + D("0")
    assert D(str(acc["net_profit"])) == D(str(acc["net_profit_pre_vat"])) - D(str(acc["payable_vat"]))
    # 모든 축이 VAT 포함이면 결과는 공급가 기준 이익과 같다 — 그 항등식으로 검산(payable_vat 참조)
    assert abs(D(str(acc["net_profit"])) - (pre_ship - D("1900")) / D("1.1")) < D("0.01")
