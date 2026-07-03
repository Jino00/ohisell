# test_product_connection_map.py — 상품 연결맵 매트릭스 + 인라인 편집 (트랙 S4, D-12)
# 인메모리 SQLite로 SA(build_connection_map)와 라우터(GET connection-map, PATCH mapping) 검증.
from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Channel, ProductChannelMapping, ProductMaster
from app.services.coupang.product_sync import _maybe_upsert_mapping
from app.services.product_connection_map import build_connection_map


def _make_engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db():
    session = sessionmaker(bind=_make_engine())()
    yield session
    session.close()


def _seed(db):
    """채널 2개 + 상품 2개 + 매핑 몇 개. 채널옵션ID 충돌 1건 심음."""
    wing = Channel(name="쿠팡 Wing2", code="COUPANG_WING2", platform="coupang", sell_type="3P")
    naver = Channel(name="네이버", code="NAVER", platform="naver")
    db.add_all([wing, naver])
    db.commit()

    p1 = ProductMaster(internal_sku="OHI-0001", product_name="필름 아이폰16", cost_price=Decimal("1000"))
    p2 = ProductMaster(internal_sku="OHI-0002", product_name="케이스 갤럭시", cost_price=Decimal("2000"))
    db.add_all([p1, p2])
    db.commit()

    db.add_all([
        # p1: wing=VII-A(정상), naver=NAV-1
        ProductChannelMapping(product_id=p1.id, channel_id=wing.id, channel_product_id="VII-A",
                              selling_price=Decimal("5000"), mapping_source="excel_master"),
        ProductChannelMapping(product_id=p1.id, channel_id=naver.id, channel_product_id="NAV-1",
                              selling_price=Decimal("5500"), mapping_source="auto_sync"),
        # p2: wing=VII-A(★충돌: 같은 채널·같은 옵션ID가 p1에도 있음)
        ProductChannelMapping(product_id=p2.id, channel_id=wing.id, channel_product_id="VII-A",
                              selling_price=Decimal("6000"), mapping_source="auto_sync"),
    ])
    db.commit()
    return {"wing": wing, "naver": naver, "p1": p1, "p2": p2}


# ── SA: build_connection_map ──
def test_matrix_rows_and_channels(db):
    s = _seed(db)
    cmap = build_connection_map(db)
    assert [c.channel_code for c in cmap.channels] == ["COUPANG_WING2", "NAVER"]
    assert cmap.total_products == 2
    assert cmap.shown_products == 2
    row1 = next(r for r in cmap.rows if r.internal_sku == "OHI-0001")
    assert row1.mapped_channel_count == 2
    assert row1.cells[s["wing"].id][0].channel_product_id == "VII-A"
    assert row1.cells[s["naver"].id][0].selling_price == Decimal("5500")


def test_conflict_flag_on_shared_option_id(db):
    s = _seed(db)
    cmap = build_connection_map(db)
    # VII-A가 p1·p2 양쪽에 걸림 → 두 셀 모두 conflict, 두 행 모두 has_conflict
    assert cmap.conflict_option_count == 1
    row1 = next(r for r in cmap.rows if r.internal_sku == "OHI-0001")
    row2 = next(r for r in cmap.rows if r.internal_sku == "OHI-0002")
    assert row1.cells[s["wing"].id][0].conflict is True
    assert row2.cells[s["wing"].id][0].conflict is True
    assert row1.has_conflict is True and row2.has_conflict is True
    # naver 셀은 충돌 아님
    assert row1.cells[s["naver"].id][0].conflict is False


def test_inactive_mapping_not_counted_as_conflict(db):
    s = _seed(db)
    # p2의 VII-A를 비활성화하면 충돌 해소(이중귀속 위험 없음)
    m = db.query(ProductChannelMapping).filter_by(
        product_id=s["p2"].id, channel_id=s["wing"].id
    ).first()
    m.is_active = False
    db.commit()
    cmap = build_connection_map(db)
    assert cmap.conflict_option_count == 0
    row1 = next(r for r in cmap.rows if r.internal_sku == "OHI-0001")
    assert row1.cells[s["wing"].id][0].conflict is False


def test_query_filter(db):
    _seed(db)
    cmap = build_connection_map(db, q="아이폰")
    assert cmap.total_products == 1
    assert cmap.rows[0].internal_sku == "OHI-0001"
    # 코드로도 검색
    assert build_connection_map(db, q="OHI-0002").rows[0].internal_sku == "OHI-0002"


def test_limit_reports_total(db):
    _seed(db)
    cmap = build_connection_map(db, limit=1)
    assert cmap.total_products == 2  # 전체는 2
    assert cmap.shown_products == 1  # 반환은 1(잘림 표면화)
    assert len(cmap.rows) == 1


# ── 라우터: GET /connection-map, PATCH mapping ──
@pytest.fixture
def client():
    engine = _make_engine()
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    seed_sess = TestingSession()
    seeded_obj = _seed(seed_sess)
    # 세션 닫히면 ORM 객체가 detached → id를 int로 미리 추출
    seeded = {k: v.id for k, v in seeded_obj.items()}
    seed_sess.close()

    def _override():
        s = TestingSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override
    yield TestClient(app), seeded
    app.dependency_overrides.clear()


def test_get_connection_map_route(client):
    tc, s = client
    resp = tc.get("/api/products/connection-map")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_products"] == 2
    assert data["conflict_option_count"] == 1
    assert {c["channel_code"] for c in data["channels"]} == {"COUPANG_WING2", "NAVER"}
    # cells 키는 JSON에서 문자열(channel_id)
    row1 = next(r for r in data["rows"] if r["internal_sku"] == "OHI-0001")
    wing_cells = row1["cells"][str(s["wing"])]
    assert wing_cells[0]["conflict"] is True


def test_patch_mapping_edits_fields_and_marks_manual(client):
    tc, s = client
    # p1의 naver 매핑(auto_sync)을 편집
    resp = tc.get("/api/products/connection-map")
    row1 = next(r for r in resp.json()["rows"] if r["internal_sku"] == "OHI-0001")
    mid = row1["cells"][str(s["naver"])][0]["mapping_id"]

    r = tc.patch(
        f"/api/products/{s['p1']}/mappings/{mid}",
        json={"selling_price": "7777", "channel_product_name": "새이름"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["selling_price"] == "7777" or float(body["selling_price"]) == 7777
    assert body["channel_product_name"] == "새이름"
    assert body["mapping_source"] == "manual"  # 수동 편집 표시


def test_patch_uniqueness_guard_409(client):
    tc, s = client
    # p2에 naver 'DUP' 매핑을 만든 뒤, p1의 naver 매핑을 'DUP'로 바꾸려 하면 충돌(409)
    resp = tc.get("/api/products/connection-map")
    row1 = next(r for r in resp.json()["rows"] if r["internal_sku"] == "OHI-0001")
    naver_mid = row1["cells"][str(s["naver"])][0]["mapping_id"]
    tc.post(
        f"/api/products/{s['p2']}/mappings",
        json={"channel_id": s["naver"], "channel_product_id": "DUP", "selling_price": "1"},
    )
    r = tc.patch(
        f"/api/products/{s['p1']}/mappings/{naver_mid}",
        json={"channel_product_id": "DUP"},
    )
    assert r.status_code == 409, r.text


def test_patch_404_for_missing(client):
    tc, s = client
    r = tc.patch(f"/api/products/{s['p1']}/mappings/999999", json={"selling_price": "1"})
    assert r.status_code == 404


# ── 자동동기화 가드: manual 매핑 보호 ──
def test_auto_sync_skips_manual_mapping(db):
    s = _seed(db)
    m = db.query(ProductChannelMapping).filter_by(
        product_id=s["p1"].id, channel_id=s["naver"].id
    ).first()
    m.mapping_source = "manual"
    db.commit()
    # 자동동기화가 같은 (channel, cpid)에 다른 값을 쓰려 해도 스킵되어야 함
    detail = {}
    item = {"externalVendorSku": "OHI-0001", "vendorItemId": "NAV-1",
            "itemName": "덮어쓰기시도", "salePrice": 99999}
    changed = _maybe_upsert_mapping(db, s["naver"], detail, item)
    assert changed is False
    db.refresh(m)
    assert m.channel_product_name != "덮어쓰기시도"
    assert m.selling_price == Decimal("5500")
