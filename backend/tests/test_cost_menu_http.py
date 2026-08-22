# test_cost_menu_http.py — 원가 메뉴 API HTTP 왕복 (D-CPP-53 / 계약 A′ S1 합격기준)
#
# ★**HTTP body를 단언한다.** 서비스층 dict만 보면 못 잡는 사고가 이 저장소에서 실제로 났다
#   (교훈 #321, 2026-08-19: `response_model`이 선언 안 된 키를 응답에서 지워 서비스층 9건이
#   초록인데 배너가 통째로 안 떴다). 이 라우터는 `response_model`을 아예 안 쓰지만, «안 쓴다»가
#   나중에 뒤집힐 수 있으므로 경계를 테스트가 지킨다.
#
# ★합격 1의 기대값은 **prod 실측**이다(2026-08-22, `GET /api/import-cost/shipments/{1,2}`):
#     수입건 id=2 (SETR2607220324, 통관 2026-07-23) cleaning kits → 178.78 / 196.66
#     수입건 id=1 (SETR2608170216, 통관 2026-08-18) cleaning kits → 190.82 / 209.90
#   여기서는 그 두 로트를 **같은 배부 산술로 재현**해 단가 이력 2건이 실제로 서로 다른 값으로
#   실리는지 본다. prod 화면 관측(합격 1)은 이 테스트가 대신하지 않는다 — 테스트 통과는
#   합격이 아니다(계약 §7 머리말).
#
# ★합격 2(무해성)를 **같은 프로세스에서 before/after로** 잰다 — 「순수 추가」는 증거 없이는
#   주장일 뿐이다.
from __future__ import annotations

from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import CostMaterial, CostSetting, ProductMaster


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    tc = TestClient(app)
    tc.testing_session = TestingSession
    # ★create_all은 마이그레이션의 초기 행(`cost_setting` 2행 · `cleaning kit` 종)을 만들지
    #   않는다 — 그건 마이그가 하는 일이다. 테스트는 같은 «내용»을 손으로 세운다.
    #   (마이그레이션 자체의 파리티는 별도 실측으로 확인했다 — 커밋 메시지 참조.)
    with TestingSession() as s:
        s.add_all(
            [
                CostSetting(key="valuation_method", value="fifo", confirmed=False),
                CostSetting(key="standard_price_rule", value="latest", confirmed=True),
                CostMaterial(
                    name="cleaning kit", unit="ea", category="부자재",
                    status="unconfirmed", excel_label=None, match_rule="cleaning kit",
                ),
            ]
        )
        s.commit()
    yield tc
    app.dependency_overrides.clear()


# ──────────────────────────────────────────────
# 원장 로트 2건 — prod 실건을 그대로 (계약 B 경로로 만든다)
# ──────────────────────────────────────────────
LOT_AUG = {  # id=1 SETR2608170216 · 통관 2026-08-18 · 신고환율 209.88
    "hbl_no": "SETR2608170216",
    "declaration_no": "15443-26-701565M",
    "declaration_date": "2026-08-18",
    "shipper_name": "SHENZHEN OTAO TECHNOLOGY LIMITED",
    "currency": "CNY",
    "fx_rate": "209.88",
    "declared_inv_value": "23100",
    "customs_value_krw": "4862657",
    "allocation_basis": "amount",
    "cost_lines": [
        {"seq": 1, "item_name": "OCEAN FREIGHT(해상운임)", "supply_amount": "156550", "tax_amount": "0", "is_costing": True},
        {"seq": 2, "item_name": "OVER WEIGHT CHARGES", "supply_amount": "18000", "tax_amount": "0", "is_costing": True},
        {"seq": 3, "item_name": "C/O(원산지증명서비용)", "supply_amount": "35000", "tax_amount": "0", "is_costing": True},
        {"seq": 4, "item_name": "신고비", "supply_amount": "44000", "tax_amount": "0", "is_costing": True},
        {"seq": 5, "item_name": "PICKUP CHARGE IN CHINA", "supply_amount": "18400", "tax_amount": "0", "is_costing": True},
        {"seq": 6, "item_name": "DOCUMENT FEE", "supply_amount": "25000", "tax_amount": "0", "is_costing": True},
        {"seq": 7, "item_name": "국내운송료 ( 라보 * 1 )", "supply_amount": "90000", "tax_amount": "9000", "is_costing": True},
        {"seq": 8, "item_name": "관세", "supply_amount": "249670", "tax_amount": "0", "is_costing": True},
        {"seq": 9, "item_name": "부가세", "supply_amount": "511230", "tax_amount": "0", "is_costing": False},
        {"seq": 10, "item_name": "통관수수료", "supply_amount": "25000", "tax_amount": "2500", "is_costing": True},
    ],
    "invoice_lines": [
        {"seq": 1, "item_name": "Privacy Glass_iP16 Pro 2ea", "quantity": "50", "unit_price_foreign": "19.2", "line_type": "product"},
        {"seq": 2, "item_name": "Privacy Glass_iP15 Pro 2ea", "quantity": "50", "unit_price_foreign": "19.2", "line_type": "product"},
        {"seq": 3, "item_name": "Glass_Ip17Pro", "quantity": "500", "unit_price_foreign": "12.2", "line_type": "product"},
        {"seq": 4, "item_name": "Glass_Ip16 Pro", "quantity": "350", "unit_price_foreign": "12.2", "line_type": "product"},
        {"seq": 5, "item_name": "Glass_Ip16 Plus", "quantity": "50", "unit_price_foreign": "12.2", "line_type": "product"},
        {"seq": 6, "item_name": "Glass_iP15", "quantity": "50", "unit_price_foreign": "12.2", "line_type": "product"},
        {"seq": 7, "item_name": "Glass_iP15 pro", "quantity": "200", "unit_price_foreign": "12.2", "line_type": "product"},
        {"seq": 8, "item_name": "Glass_iP14promax", "quantity": "50", "unit_price_foreign": "12.2", "line_type": "product"},
        {"seq": 9, "item_name": "Glass_iP13/13pro", "quantity": "50", "unit_price_foreign": "12.2", "line_type": "product"},
        {"seq": 10, "item_name": "Glass_iP15 promax", "quantity": "100", "unit_price_foreign": "12.2", "line_type": "product"},
        {"seq": 11, "item_name": "Glass_iP15 plus", "quantity": "50", "unit_price_foreign": "12.2", "line_type": "product"},
        {"seq": 12, "item_name": "Privacy Glass_iP14pro 2ea", "quantity": "50", "unit_price_foreign": "19.2", "line_type": "product"},
        {"seq": 13, "item_name": "Glass_iP13promax", "quantity": "50", "unit_price_foreign": "12.2", "line_type": "product"},
        {"seq": 14, "item_name": "Glass_iP12promax", "quantity": "50", "unit_price_foreign": "12.2", "line_type": "product"},
        {"seq": 15, "item_name": "cleaning kits", "quantity": "2400", "unit_price_foreign": "0.8", "line_type": "material"},
    ],
    # PL은 품목을 박스별로 쪼갠다 — 검산 ①은 라인 대 라인이 아니라 품목명 합계로 본다.
    "packing_lines": [
        {"seq": 1, "carton_range": "1", "item_name": "Privacy Glass_iP16 Pro 2ea", "quantity": "50"},
        {"seq": 2, "carton_range": "1", "item_name": "Privacy Glass_iP15 Pro 2ea", "quantity": "50"},
        {"seq": 3, "carton_range": "2-6", "item_name": "Glass_Ip17Pro", "quantity": "500"},
        {"seq": 4, "carton_range": "7-9", "item_name": "Glass_Ip16 Pro", "quantity": "300"},
        {"seq": 5, "carton_range": "10", "item_name": "Glass_Ip16 Plus", "quantity": "50"},
        {"seq": 6, "carton_range": "10", "item_name": "Glass_Ip16 Pro", "quantity": "50"},
        {"seq": 7, "carton_range": "11", "item_name": "Glass_iP15", "quantity": "50"},
        {"seq": 8, "carton_range": "12-13", "item_name": "Glass_iP15 pro", "quantity": "200"},
        {"seq": 9, "carton_range": "14", "item_name": "Glass_iP14promax", "quantity": "50"},
        {"seq": 10, "carton_range": "14", "item_name": "Glass_iP13/13pro", "quantity": "50"},
        {"seq": 11, "carton_range": "15", "item_name": "Glass_iP15 promax", "quantity": "100"},
        {"seq": 12, "carton_range": "16", "item_name": "Glass_iP15 plus", "quantity": "50"},
        {"seq": 13, "carton_range": "16", "item_name": "Privacy Glass_iP14pro 2ea", "quantity": "50"},
        {"seq": 14, "carton_range": "17", "item_name": "Glass_iP13promax", "quantity": "50"},
        {"seq": 15, "carton_range": "17", "item_name": "Glass_iP12promax", "quantity": "50"},
        {"seq": 16, "carton_range": "18-19", "item_name": "cleaning kits", "quantity": "2400"},
    ],
}

LOT_JUL = {  # id=2 SETR2607220324 · 통관 2026-07-23 · 신고환율 221.16 · 관세 귀속(D-CPP-50)
    # ★prod `GET /api/import-cost/shipments/2` 응답을 그대로 옮긴 것이다(2026-08-22 실측) —
    #   값을 지어내지 않는다. cleaning kits 178.78/196.66이 그 응답에 실려 있는 값이다.
    "hbl_no": "SETR2607220324",
    "declaration_no": "44598-26-650964M",
    "declaration_date": "2026-07-23",
    "eta": "2026-07-23",
    "shipper_name": "SHENZHEN OTAO TECHNOLOGY CO L",
    "invoice_no": "SO-WSOH-114",
    "currency": "CNY",
    "fx_rate": "221.16",
    "declared_inv_value": "10820",
    "customs_value_krw": "2407850",
    "carton_count": 11,
    "gross_weight_kg": "179.8",
    "cbm": "0.586608",
    "allocation_basis": "amount",
    "cost_lines": [
        {"seq": 1, "item_name": "관세", "supply_amount": "15200", "tax_amount": "0", "is_costing": True, "is_duty": True},
        {"seq": 2, "item_name": "부가세", "supply_amount": "242300", "tax_amount": "0", "is_costing": False},
        {"seq": 3, "item_name": "통관수수료", "supply_amount": "25000", "tax_amount": "2500", "is_costing": True},
    ],
    "invoice_lines": [
        # 유리 5.6% / cleaning kits **0%** — 부자재는 무관세다(실측). 세율이 있으면 관세는
        # 배부가 아니라 귀속이다(D-CPP-50): 그래서 kits가 관세를 떠안지 않는다.
        {"seq": 1, "item_name": "Glass_Ip16", "quantity": "100", "unit_price_foreign": "12.2", "line_type": "product", "gross_weight_kg": "14.8", "duty_rate": "0.056"},
        {"seq": 2, "item_name": "cleaning kits", "quantity": "12000", "unit_price_foreign": "0.8", "line_type": "material", "gross_weight_kg": "165.0", "duty_rate": "0"},
    ],
    "packing_lines": [
        {"seq": 1, "carton_range": "1", "item_name": "Glass_Ip16", "quantity": "100"},
        {"seq": 2, "carton_range": "2-11", "item_name": "cleaning kits", "quantity": "12000"},
    ],
}


def _make_confirmed_lot(client, body) -> dict:
    r = client.post("/api/import-cost/shipments", json=body)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    r = client.post(f"/api/import-cost/shipments/{sid}/confirm")
    assert r.status_code == 200, r.text
    assert r.json()["confirm_result"]["confirmed"] is True, r.json()["confirm_result"]
    return client.get(f"/api/import-cost/shipments/{sid}").json()


def _kits_line(ship: dict) -> dict:
    return next(x for x in ship["invoice_lines"] if x["item_name"] == "cleaning kits")


def _kit_material_id(client) -> int:
    items = client.get("/api/cost/materials").json()["items"]
    return next(m["id"] for m in items if m["name"] == "cleaning kit")


# ══════════════════════════════════════════════════════════════════
# 합격 1 — 부자재 탭이 로트별 단가 이력 2건을 서로 다른 값으로 낸다
# ══════════════════════════════════════════════════════════════════
def test_two_lots_yield_two_distinct_price_rows(client):
    """★S1의 본체. 같은 부자재의 **두 로트 단가가 나란히, 서로 다른 값으로** 실린다."""
    aug = _make_confirmed_lot(client, LOT_AUG)
    jul = _make_confirmed_lot(client, LOT_JUL)
    mid = _kit_material_id(client)

    # 원장이 계산한 값 — 이 테스트가 정본으로 삼는 자리
    assert D(_kits_line(aug)["unit_cost_ex_vat"]) == D("190.82")
    assert D(_kits_line(jul)["unit_cost_ex_vat"]) == D("178.78")

    for ship in (aug, jul):
        r = client.post(
            f"/api/cost/materials/{mid}/prices/link",
            json={"import_invoice_line_id": _kits_line(ship)["id"]},
        )
        assert r.status_code == 201, r.text

    body = client.get(f"/api/cost/materials/{mid}").json()
    assert body["lot_count"] == 2
    ex = {p["effective_date"]: p["unit_price_ex_vat"] for p in body["prices"]}
    assert ex == {"2026-08-18": "190.82", "2026-07-23": "178.78"}
    # 두 값(ex/inc)을 다 저장한다 — 기준이 바뀌어도 재계산 없이 표시만 갈아끼운다(계약 §2-8)
    inc = {p["effective_date"]: p["unit_price_inc_vat"] for p in body["prices"]}
    assert inc == {"2026-08-18": "209.90", "2026-07-23": "196.66"}
    # 최신 로트가 «최신»이다 — `latest` 규칙(계약 §4)이 읽는 자리
    assert body["latest_price_ex_vat"] == "190.82"
    assert body["latest_price_source"] == "ledger"


def test_price_row_carries_its_lot_coordinates(client):
    """「이 단가가 어느 수입건에서 왔나」를 단가 행이 말한다 — 못 말하면 추적이 끊긴다."""
    aug = _make_confirmed_lot(client, LOT_AUG)
    mid = _kit_material_id(client)
    client.post(
        f"/api/cost/materials/{mid}/prices/link",
        json={"import_invoice_line_id": _kits_line(aug)["id"]},
    )
    p = client.get(f"/api/cost/materials/{mid}").json()["prices"][0]
    assert p["source"] == "ledger"
    assert p["shipment"]["hbl_no"] == "SETR2608170216"
    assert p["shipment"]["declaration_date"] == "2026-08-18"
    # 공급처는 원장 shipper에서 자동으로 온다(계약 §5-1 ★공급처)
    assert p["supplier"] == "SHENZHEN OTAO TECHNOLOGY LIMITED"


# ══════════════════════════════════════════════════════════════════
# 제안은 제안이다 — 확정은 사람 (계약 §2-2 · §5-2)
# ══════════════════════════════════════════════════════════════════
def test_listing_ledger_lines_does_not_link_anything(client):
    """★GET을 아무리 불러도 링크가 생기지 않는다. 이게 「확정은 사람」의 기계적 정의다."""
    _make_confirmed_lot(client, LOT_AUG)
    mid = _kit_material_id(client)
    for _ in range(3):
        rows = client.get("/api/cost/ledger-material-lines").json()["items"]
    assert len(rows) == 1
    row = rows[0]
    assert row["suggestion"]["material_id"] == mid      # 제안은 있다
    assert row["linked_material_id"] is None            # 링크는 없다
    assert row["linked_price_id"] is None
    assert client.get(f"/api/cost/materials/{mid}").json()["price_count"] == 0


def test_unmatched_ledger_line_is_surfaced_not_swallowed(client):
    """★미매칭이 응답에 실린다 — 안 보이면 단가 이력이 조용히 비어 있게 된다(계약 §5-3 탭1)."""
    _make_confirmed_lot(client, LOT_AUG)
    # 규칙을 어긋나게 바꾸면 그 라인은 미매칭이 된다
    mid = _kit_material_id(client)
    client.patch(f"/api/cost/materials/{mid}", json={"match_rule": "존재하지않는토큰"})
    row = client.get("/api/cost/ledger-material-lines").json()["items"][0]
    assert row["suggestion"]["material_id"] is None
    assert row["suggestion"]["unmatched"] is True
    assert "미매칭" in row["suggestion"]["reason"]


def test_draft_lot_is_not_linkable(client):
    """확정 전 로트의 단가는 «계산된 적 없는 값»이다(계약 §9-8)."""
    r = client.post("/api/import-cost/shipments", json=LOT_AUG)
    sid = r.json()["id"]
    line_id = _kits_line(r.json())["id"]
    mid = _kit_material_id(client)
    resp = client.post(
        f"/api/cost/materials/{mid}/prices/link", json={"import_invoice_line_id": line_id}
    )
    assert resp.status_code == 400
    assert "확정 전" in resp.json()["detail"]
    assert client.get(f"/api/cost/materials/{mid}").json()["price_count"] == 0
    assert sid  # 로트는 그대로 draft로 남는다


def test_product_line_is_not_linkable_as_material(client):
    aug = _make_confirmed_lot(client, LOT_AUG)
    glass = next(x for x in aug["invoice_lines"] if x["item_name"] == "Glass_Ip17Pro")
    mid = _kit_material_id(client)
    r = client.post(
        f"/api/cost/materials/{mid}/prices/link", json={"import_invoice_line_id": glass["id"]}
    )
    assert r.status_code == 400
    assert "부자재" in r.json()["detail"]


def test_same_lot_cannot_be_counted_twice(client):
    """같은 로트가 두 번 세지면 이력이 거짓말이 된다."""
    aug = _make_confirmed_lot(client, LOT_AUG)
    mid = _kit_material_id(client)
    line_id = _kits_line(aug)["id"]
    assert client.post(
        f"/api/cost/materials/{mid}/prices/link", json={"import_invoice_line_id": line_id}
    ).status_code == 201
    dup = client.post(
        f"/api/cost/materials/{mid}/prices/link", json={"import_invoice_line_id": line_id}
    )
    assert dup.status_code == 409
    assert client.get(f"/api/cost/materials/{mid}").json()["lot_count"] == 1


# ══════════════════════════════════════════════════════════════════
# 미입력은 «없음»이다 — 0으로 채우지 않는다 (계약 §2-7)
# ══════════════════════════════════════════════════════════════════
def test_material_without_prices_reports_none_not_zero(client):
    mid = _kit_material_id(client)
    body = client.get(f"/api/cost/materials/{mid}").json()
    assert body["latest_price_ex_vat"] is None   # ★ "0" 이 아니다
    assert body["latest_price_inc_vat"] is None
    assert body["latest_price_source"] is None
    assert body["lot_count"] == 0
    assert body["prices"] == []


def test_manual_price_requires_at_least_one_value(client):
    mid = _kit_material_id(client)
    r = client.post(f"/api/cost/materials/{mid}/prices", json={"supplier": "조아테크"})
    assert r.status_code == 400
    assert "빈 단가 행" in r.json()["detail"]


def test_manual_price_does_not_invent_the_other_value(client):
    """★사람이 준 한 값만 남고 나머지는 «없음»이다 — 여기서 ×1.1을 하면 그게 창작이다."""
    mid = _kit_material_id(client)
    r = client.post(
        f"/api/cost/materials/{mid}/prices",
        json={"unit_price_ex_vat": "168", "supplier": "조아테크", "effective_date": "2026-08-01"},
    )
    assert r.status_code == 201, r.text
    p = r.json()["material"]["prices"][0]
    assert p["source"] == "manual"
    assert p["unit_price_ex_vat"] == "168.00"
    assert p["unit_price_inc_vat"] is None       # ★ "184.80"을 지어내지 않는다
    assert p["supplier"] == "조아테크"
    assert p["shipment"] is None                 # 원장 좌표가 없다고 «말한다»


def test_new_material_starts_unconfirmed(client):
    """새 종이 곧바로 승인분 행세를 하지 않는다(계약 §2-2)."""
    r = client.post("/api/cost/materials", json={"name": "플립 내부 원단", "unit": "매"})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "unconfirmed"


def test_cleaning_kit_excel_label_is_unconfirmed(client):
    """★계약 §9-3: 엑셀 대응 항목 불명은 **비워 두고 화면이 자백**한다 — 억지 라벨 금지."""
    mid = _kit_material_id(client)
    assert client.get(f"/api/cost/materials/{mid}").json()["excel_label"] is None


# ══════════════════════════════════════════════════════════════════
# 설정 — 「법정 기본값」과 「우리 신고값」은 다른 사실이다 (계약 §9-1)
# ══════════════════════════════════════════════════════════════════
def test_valuation_method_is_reported_as_unconfirmed(client):
    items = {s["key"]: s for s in client.get("/api/cost/settings").json()["items"]}
    assert items["valuation_method"]["value"] == "fifo"
    assert items["valuation_method"]["confirmed"] is False   # ★자백 배지의 원료
    assert items["standard_price_rule"]["value"] == "latest"
    assert items["standard_price_rule"]["confirmed"] is True


# ══════════════════════════════════════════════════════════════════
# 합격 2 — 무해성: `product_master.cost_price` 변화 0건
# ══════════════════════════════════════════════════════════════════
def test_cost_price_is_untouched_by_the_whole_flow(client):
    """★「순수 추가」는 증거 없이는 주장일 뿐이다 — 같은 프로세스에서 before/after로 잰다."""
    with client.testing_session() as s:
        s.add_all(
            [
                ProductMaster(internal_sku="SKU-A", product_name="가", cost_price=D("1234")),
                ProductMaster(internal_sku="SKU-B", product_name="나", cost_price=D("0")),
            ]
        )
        s.commit()

    def snapshot():
        with client.testing_session() as s:
            return sorted(
                (p.internal_sku, str(p.cost_price))
                for p in s.query(ProductMaster).all()
            )

    before = snapshot()

    aug = _make_confirmed_lot(client, LOT_AUG)
    jul = _make_confirmed_lot(client, LOT_JUL)
    mid = _kit_material_id(client)
    for ship in (aug, jul):
        client.post(
            f"/api/cost/materials/{mid}/prices/link",
            json={"import_invoice_line_id": _kits_line(ship)["id"]},
        )
    client.post("/api/cost/materials", json={"name": "알콜솜"})
    client.post(f"/api/cost/materials/{mid}/prices", json={"unit_price_ex_vat": "168"})
    client.patch(f"/api/cost/materials/{mid}", json={"status": "approved"})
    client.get("/api/cost/materials")
    client.get("/api/cost/ledger-material-lines")

    assert snapshot() == before, "cost_price가 바뀌었다 — 금지선 위반(계약 §3)"


def test_cost_menu_router_never_touches_cost_price():
    """★소스 수준 가드: 이 층의 어느 파일도 `cost_price`를 **식별자로 쓰지 않는다**(S1 범위).

    문자열·주석이 아니라 **AST의 이름·속성**만 본다 — 주석에서 금지선을 «설명»하는 것과
    코드에서 그 컬럼을 «만지는» 것은 다른 사실이고, 문자열 검색은 둘을 못 가른다.

    S2·S3에서 «읽기 대조»가 들어오면 이 테스트가 그 자리를 알려 주고, 그때 이 단언을
    「쓰지 않는다」로 좁히면 된다 — 쓰기는 어느 슬라이스에서도 금지선이다(계약 §3).
    """
    import ast

    import app.routers.cost_menu as R
    import app.services.cost_menu.materials as MM
    import app.services.cost_menu.matcher as MT

    for mod in (R, MM, MT):
        tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
        names = {
            n.attr if isinstance(n, ast.Attribute) else n.id
            for n in ast.walk(tree)
            if isinstance(n, (ast.Attribute, ast.Name))
        }
        assert "cost_price" not in names, mod.__name__
        # `ProductMaster`를 임포트하지도 않는다 — 그 테이블을 아예 모르는 것이 S1의 경계다.
        imported = {
            a.name
            for n in ast.walk(tree)
            if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in n.names
        }
        assert "ProductMaster" not in imported, mod.__name__
