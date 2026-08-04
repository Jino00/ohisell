# test_mapping_relink_backfill.py — 매핑을 이으면 **과거 주문도** 연결된다 (2026-08-04)
#
# ★왜 이 계약이 필요한가(라이브 실증): 주문↔상품 연결은 수집 시점에만 일어나서, 화면에서
#   매핑을 손으로 이어도 이미 들어와 있던 주문은 계속 `product_id=NULL`(= 손익에서 원가 미상)로
#   남았다. 네이버 13563480014(맥세이프 이지 거울 카드지갑)은 07-19에 이었는데도 05-26~06-15
#   주문 27건 488,100원이 그대로였다. 종전엔 relink를 **엑셀 업로드 경로에서만** 불렀다.
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Channel, Order, ProductChannelMapping, ProductMaster
from app.services.sync_service import relink_unlinked_orders

NAVER_OPTION = "13563480014"       # 라이브에서 실제로 문제가 됐던 옵션ID
OTHER_OPTION = "99999999999"


def _make_engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return engine


def _seed(db):
    """네이버 채널 + 상품 1개 + **미연결 과거 주문 3건**(대상 2 + 무관 1)."""
    naver = Channel(name="네이버", code="NAVER", platform="naver")
    other = Channel(name="자사몰", code="CAFE24", platform="cafe24")
    db.add_all([naver, other])
    db.commit()

    p = ProductMaster(
        internal_sku="OHI-CARDWALLET",
        product_name="맥세이프 이지 거울 카드지갑",
        cost_price=Decimal("4100"),
    )
    db.add(p)
    db.commit()

    def _order(ch_id, option, num):
        return Order(
            channel_id=ch_id,
            product_id=None,
            order_number=num,
            platform_product_id=option,
            quantity=1,
            selling_price=Decimal("16900"),
            order_date=datetime(2026, 6, 1, 12, 0, 0),
            status="delivered",
        )

    db.add_all([
        _order(naver.id, NAVER_OPTION, "N-1"),
        _order(naver.id, NAVER_OPTION, "N-2"),
        _order(naver.id, OTHER_OPTION, "N-3"),      # 같은 채널·다른 옵션ID
        _order(other.id, NAVER_OPTION, "C-1"),      # 같은 옵션ID·다른 채널(우연 일치)
    ])
    db.commit()
    return {"naver": naver, "other": other, "p": p}


@pytest.fixture
def db():
    session = sessionmaker(bind=_make_engine())()
    yield session
    session.close()


@pytest.fixture
def client():
    engine = _make_engine()
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    seed_sess = TestingSession()
    seeded = {k: v.id for k, v in _seed(seed_sess).items()}
    seed_sess.close()

    def _override():
        s = TestingSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override
    yield TestClient(app), seeded, TestingSession
    app.dependency_overrides.clear()


def _unlinked(sess, channel_id, option):
    return (
        sess.query(Order)
        .filter(
            Order.channel_id == channel_id,
            Order.platform_product_id == option,
            Order.product_id.is_(None),
        )
        .count()
    )


# ── SA: relink_unlinked_orders 스코프 ──
def test_relink_scoped_by_option_id(db):
    s = _seed(db)
    db.add(ProductChannelMapping(
        product_id=s["p"].id, channel_id=s["naver"].id,
        channel_product_id=NAVER_OPTION, selling_price=Decimal("16900"),
    ))
    db.commit()

    linked = relink_unlinked_orders(
        db, channel_id=s["naver"].id, platform_product_id=NAVER_OPTION
    )
    assert linked == 2
    assert _unlinked(db, s["naver"].id, NAVER_OPTION) == 0
    # 같은 채널의 다른 옵션ID, 다른 채널의 같은 옵션ID는 건드리지 않는다
    assert _unlinked(db, s["naver"].id, OTHER_OPTION) == 1
    assert _unlinked(db, s["other"].id, NAVER_OPTION) == 1


def test_relink_ignores_inactive_mapping(db):
    s = _seed(db)
    db.add(ProductChannelMapping(
        product_id=s["p"].id, channel_id=s["naver"].id,
        channel_product_id=NAVER_OPTION, selling_price=Decimal("16900"),
        is_active=False,
    ))
    db.commit()
    assert relink_unlinked_orders(db, channel_id=s["naver"].id) == 0
    assert _unlinked(db, s["naver"].id, NAVER_OPTION) == 2


# ── 라우터: 화면에서 이으면 과거가 붙는다 ──
def test_add_mapping_backfills_past_orders(client):
    tc, s, Session = client
    r = tc.post(
        f"/api/products/{s['p']}/mappings",
        json={
            "channel_id": s["naver"],
            "channel_product_id": NAVER_OPTION,
            "selling_price": "16900",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["orders_linked"] == 2      # 사후 가시성: 화면이 말할 수 있어야 한다

    sess = Session()
    assert _unlinked(sess, s["naver"], NAVER_OPTION) == 0
    assert _unlinked(sess, s["naver"], OTHER_OPTION) == 1   # 무관한 주문은 그대로
    sess.close()


def test_inactive_mapping_links_nothing_until_reactivated(client):
    """비활성 매핑은 소급 연결하지 않는다 — `_auto_link_product`가 active만 보기 때문.

    ※생성 API(`MappingCreate`)엔 is_active가 없어 **비활성으로는 만들 수 없다**. 그래서
      비활성 경로는 편집(PATCH)으로만 도달하며, 여기서 그 경로를 고정한다.
    """
    tc, s, Session = client
    created = tc.post(
        f"/api/products/{s['p']}/mappings",
        json={
            "channel_id": s["naver"],
            "channel_product_id": "1356348001",   # 오타 — 아직 아무것도 안 붙는다
            "selling_price": "16900",
        },
    )
    assert created.status_code == 201, created.text
    mid = created.json()["id"]

    off = tc.patch(
        f"/api/products/{s['p']}/mappings/{mid}",
        json={"channel_product_id": NAVER_OPTION, "is_active": False},
    )
    assert off.status_code == 200, off.text
    assert off.json()["orders_linked"] == 0
    sess = Session()
    assert _unlinked(sess, s["naver"], NAVER_OPTION) == 2
    sess.close()

    on = tc.patch(f"/api/products/{s['p']}/mappings/{mid}", json={"is_active": True})
    assert on.status_code == 200, on.text
    assert on.json()["orders_linked"] == 2
    sess = Session()
    assert _unlinked(sess, s["naver"], NAVER_OPTION) == 0
    sess.close()


def test_patch_option_id_typo_fix_backfills(client):
    """오타로 잘못 넣은 옵션ID를 고치는 편집이 정확히 '이제 붙어야 하는' 상황이다."""
    tc, s, Session = client
    created = tc.post(
        f"/api/products/{s['p']}/mappings",
        json={
            "channel_id": s["naver"],
            "channel_product_id": "1356348001",   # 자리 하나 빠진 오타
            "selling_price": "16900",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["orders_linked"] == 0   # 오타라 아무것도 안 붙는다
    mid = created.json()["id"]

    fixed = tc.patch(
        f"/api/products/{s['p']}/mappings/{mid}",
        json={"channel_product_id": NAVER_OPTION},
    )
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["orders_linked"] == 2

    sess = Session()
    assert _unlinked(sess, s["naver"], NAVER_OPTION) == 0
    sess.close()


def test_list_response_has_no_orders_linked(client):
    """목록 조회는 소급 연결을 하지 않으므로 값이 없다(None) — 부작용 없는 읽기임을 고정."""
    tc, s, _ = client
    r = tc.get(f"/api/products?channel_id={s['naver']}")
    assert r.status_code == 200, r.text
    for p in r.json():
        for m in p.get("mappings", []):
            assert m.get("orders_linked") is None
