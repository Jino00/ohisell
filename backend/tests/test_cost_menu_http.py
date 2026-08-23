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
# N5 (2026-08-23) — 엑셀 참고값이 **응답에 실린다**
#
# ★발견: `recipe_parser.py`가 참고값을 「화면에 보이기만 하고」라고 적어 뒀는데, 실제로는
#   **어느 응답에도 안 실려** 프론트의 `grep excel_ref`가 0건이었다. prod 실측(2026-08-23)
#   단가 보유 종 **1/129** vs 참고값 보유 종 **128/129**인데 화면은 전 종에 대해
#   「원장 연결 또는 수동 입력 필요」라고만 말했다 — 할 일이 셋인데 둘만 제시했고, 빠진
#   셋째(채택)가 가장 싼 길이었다. **백엔드만 아는 사실은 없는 것과 같다.**
# ══════════════════════════════════════════════════════════════════
def test_material_payload_carries_the_excel_reference_price(client):
    mid = _kit_material_id(client)
    # 이 종엔 원래 참고값이 없다 — 「없으면 None」이 먼저 참이어야 한다(0으로 안 접는다).
    assert client.get(f"/api/cost/materials/{mid}").json()["excel_ref_price"] is None

    with client.testing_session() as s:
        s.query(CostMaterial).filter(CostMaterial.id == mid).one().excel_ref_price = D("168")
        s.commit()

    body = client.get(f"/api/cost/materials/{mid}").json()
    assert body["excel_ref_price"] == "168.00"
    # ★★참고값이 «단가 자리»에 앉으면 그게 계약 §3 위반이다 — 실려 오되 섞이지 않는다.
    assert body["latest_price_ex_vat"] is None
    assert body["latest_price_inc_vat"] is None
    assert body["latest_price_source"] is None
    assert body["lot_count"] == 0
    assert body["prices"] == []

    # 목록 경로도 같은 칸을 낸다 — 상세만 고치고 목록을 잊는 것이 흔한 반쪽 배선이다.
    listed = {m["id"]: m for m in client.get("/api/cost/materials").json()["items"]}
    assert listed[mid]["excel_ref_price"] == "168.00"


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


# ══════════════════════════════════════════════════════════════════
# ★적대 리뷰 1R P1 — 「보존된 값이 지금도 유효한가」를 조회 시점에 되묻는다
#
# 리뷰가 재현한 네 갈래는 **뿌리가 하나**다: 단가 행이 연결 시점 값을 복사해 두고 그 뒤
# 원장이 어떻게 되든 재검사하지 않았다. 그래서 여기 넷을 **한 가드로** 잰다 —
# ①reopen ②값 변경 ③수입건 삭제 ④라인 순서 정정(rowid 재사용).
#
# ★공통 전제: 이 절의 시나리오는 **수입건 1건만** 만든다. SQLite는 빈 테이블에서 rowid를
#   1부터 다시 주므로, 라인 교체(`_replace_lines`)가 **같은 id를 다른 행에 재사용**하는
#   것이 그때 결정적으로 재현된다(리뷰가 실증한 그 경로다).
# ══════════════════════════════════════════════════════════════════
def _link_kit(client, ship) -> tuple[int, int]:
    """kit 라인을 연결하고 (material_id, price_id)를 돌려준다."""
    mid = _kit_material_id(client)
    r = client.post(
        f"/api/cost/materials/{mid}/prices/link",
        json={"import_invoice_line_id": _kits_line(ship)["id"]},
    )
    assert r.status_code == 201, r.text
    return mid, r.json()["linked_price_id"]


def test_reopen_makes_the_stored_price_confess_instead_of_posing_as_latest(client):
    """★①reopen — 원장은 단가를 지웠는데 화면이 낡은 값을 「최신」이라 부르면 안 된다.

    계약 B `reopen`이 스스로 적어 둔 이유: *"낡은 단가가 「확정된 값」인 척 남는 것이 이
    도메인에서 가장 위험하다."* A′가 그걸 되살리면 계약 B의 안전장치가 무효가 된다.
    """
    aug = _make_confirmed_lot(client, LOT_AUG)
    mid, price_id = _link_kit(client, aug)
    assert client.get(f"/api/cost/materials/{mid}").json()["latest_price_ex_vat"] == "190.82"

    assert client.post(f"/api/import-cost/shipments/{aug['id']}/reopen").status_code == 200

    body = client.get(f"/api/cost/materials/{mid}").json()
    p = next(x for x in body["prices"] if x["id"] == price_id)
    # 값은 **남는다**(근거 보존은 이 테이블의 존재 이유다)…
    assert p["unit_price_ex_vat"] == "190.82"
    # …그러나 「최신 확정 로트 단가」 자리는 못 차지한다.
    assert p["ledger_check"]["status"] == "unconfirmed"
    assert p["ledger_check"]["ok"] is False
    assert p["ledger_check"]["counts_as_evidence"] is False
    assert body["latest_price_ex_vat"] is None      # ★ "190.82"가 아니다
    assert body["lot_count"] == 0                   # 어긋난 근거는 근거가 아니다
    assert body["stale_count"] == 1                 # …대신 «몇 건이 어긋났나»를 말한다

    # ★그리고 그 사실이 원장 라인 목록에서도 사라지지 않는다(초판은 통째로 빠졌다).
    rows = client.get("/api/cost/ledger-material-lines").json()["items"]
    row = next(r for r in rows if r["line_id"] == p["import_invoice_line_id"])
    assert row["shipment_status"] == "draft"
    assert row["linked_price_check"]["status"] == "unconfirmed"


def test_recomputed_ledger_value_is_surfaced_and_refreshable(client):
    """★②값 변경 — 환율을 고쳐 재확정하면 원장은 새 값인데 저장 행은 옛 값이다.

    초판은 그 어긋남을 아무 데서도 말하지 않았고, 재연결은 유일 제약 때문에 409라
    **원장이 스스로 고칠 길이 없었다.** 지금은 ①어긋남을 자백하고 ②「갱신」이 길을 연다.
    """
    aug = _make_confirmed_lot(client, LOT_AUG)
    mid, price_id = _link_kit(client, aug)

    # 환율 정정 → 재확정. 라인 순서는 그대로이므로 같은 id가 같은 품목을 가리킨다.
    client.post(f"/api/import-cost/shipments/{aug['id']}/reopen")
    fixed = {**LOT_AUG, "fx_rate": "215.00"}
    assert client.put(f"/api/import-cost/shipments/{aug['id']}", json=fixed).status_code == 200
    again = client.post(f"/api/import-cost/shipments/{aug['id']}/confirm").json()
    assert again["confirm_result"]["confirmed"] is True
    new_ex = _kits_line(again)["unit_cost_ex_vat"]
    assert new_ex != "190.82", "환율을 고쳤는데 원장 단가가 그대로면 이 시나리오가 성립 안 한다"

    body = client.get(f"/api/cost/materials/{mid}").json()
    p = next(x for x in body["prices"] if x["id"] == price_id)
    assert p["ledger_check"]["status"] == "changed"
    assert p["unit_price_ex_vat"] == "190.82"                        # 저장값(근거)
    assert p["ledger_check"]["ledger_unit_price_ex_vat"] == new_ex   # 현 원장값 — 나란히 보인다
    assert body["latest_price_ex_vat"] is None                        # 어긋난 값은 최신이 아니다
    assert body["stale_count"] == 1

    # ★고칠 길 — 「갱신」이 원장 현재값을 다시 옮긴다(409로 막히지 않는다).
    r = client.post(f"/api/cost/materials/{mid}/prices/{price_id}/refresh")
    assert r.status_code == 200, r.text
    assert r.json()["was"]["status"] == "changed"      # 무엇이 어긋나 있었는지도 말한다
    after = r.json()["material"]
    p2 = next(x for x in after["prices"] if x["id"] == price_id)
    assert p2["unit_price_ex_vat"] == new_ex
    assert p2["ledger_check"]["status"] == "ok"
    assert after["latest_price_ex_vat"] == new_ex
    assert after["stale_count"] == 0
    # ★옛 값을 버리지 않는다 — 갱신이 근거를 지우면 이 테이블의 존재 이유와 앞뒤가 안 맞는다.
    assert "190.82" in (p2["note"] or "")


def test_deleted_shipment_leaves_an_orphan_and_the_screen_says_so(client):
    """★③삭제 — FK에 CASCADE가 걸려 있어도 이 앱은 `PRAGMA foreign_keys=ON`을 안 켠다.

    그래서 단가 행이 **고아로 살아남는다**. 전역 PRAGMA는 이번 슬라이스 밖이므로(계약 §9-10
    이월) 재검사가 그 고아를 「원장 라인 없음」으로 **표면화**하는 것이 이 슬라이스의 처방이다.
    """
    aug = _make_confirmed_lot(client, LOT_AUG)
    mid, price_id = _link_kit(client, aug)
    client.post(f"/api/import-cost/shipments/{aug['id']}/reopen")
    assert client.delete(f"/api/import-cost/shipments/{aug['id']}").status_code == 200

    body = client.get(f"/api/cost/materials/{mid}").json()
    p = next(x for x in body["prices"] if x["id"] == price_id)
    assert p["ledger_check"]["status"] == "missing"     # ★고아라고 «말한다»
    assert p["shipment"] is None
    assert p["linked_item_name"] == "cleaning kits"     # 무엇에 붙어 있었는지는 남는다
    assert body["latest_price_ex_vat"] is None
    assert body["lot_count"] == 0
    assert body["stale_count"] == 1
    # 갱신은 못 한다 — 옮겨 올 원장 값 자체가 없다. 처방은 「해제」다.
    r = client.post(f"/api/cost/materials/{mid}/prices/{price_id}/refresh")
    assert r.status_code == 400
    assert "원장 라인 없음" in r.json()["detail"]


def test_rowid_reuse_is_caught_by_the_stored_item_name(client):
    """★④라인 순서 정정 — `_replace_lines`가 지우고 다시 넣으면 **rowid가 재사용**된다.

    리뷰 실증: `import_invoice_line_id=15`가 가리키던 cleaning kit이 Glass_iP12promax로
    바뀐다. **id만 믿으면 화면의 「근거」가 다른 품목을 가리킨다** — 그래서 저장 시점
    품목명을 함께 두고 대조한다.
    """
    aug = _make_confirmed_lot(client, LOT_AUG)
    mid, price_id = _link_kit(client, aug)
    line_id = _kits_line(aug)["id"]

    # 서류의 **순서만** 정정한다 — 마지막 두 줄(seq 14 Glass_iP12promax · seq 15 kits)을 뒤집는다.
    reordered = {**LOT_AUG, "invoice_lines": [
        *LOT_AUG["invoice_lines"][:13],
        LOT_AUG["invoice_lines"][14],   # kits가 먼저 들어간다
        LOT_AUG["invoice_lines"][13],   # Glass_iP12promax가 나중에 → 옛 kits의 id를 받는다
    ]}
    client.post(f"/api/import-cost/shipments/{aug['id']}/reopen")
    assert client.put(f"/api/import-cost/shipments/{aug['id']}", json=reordered).status_code == 200
    again = client.post(f"/api/import-cost/shipments/{aug['id']}/confirm").json()
    assert again["confirm_result"]["confirmed"] is True

    now_at_that_id = next(x for x in again["invoice_lines"] if x["id"] == line_id)
    assert now_at_that_id["item_name"] != "cleaning kits", (
        "이 시나리오는 rowid 재사용을 전제한다 — 재사용이 안 일어났으면 전제가 깨진 것이다"
    )

    body = client.get(f"/api/cost/materials/{mid}").json()
    p = next(x for x in body["prices"] if x["id"] == price_id)
    assert p["ledger_check"]["status"] == "item_mismatch"
    assert p["ledger_check"]["ledger_item_name"] == now_at_that_id["item_name"]
    assert "cleaning kits" in p["ledger_check"]["detail"]   # 연결 당시 품목을 말한다
    assert body["latest_price_ex_vat"] is None
    assert body["stale_count"] == 1
    # ★갱신으로 «삼키지» 않는다 — 다른 품목의 단가를 조용히 받아들이면 그게 더 나쁘다.
    r = client.post(f"/api/cost/materials/{mid}/prices/{price_id}/refresh")
    assert r.status_code == 400
    assert "다른 품목" in r.json()["detail"]


def test_stale_link_409_tells_the_person_what_to_do(client):
    """어긋난 연결 위에 다시 연결하면 여전히 409지만, **다음 수**를 말한다."""
    aug = _make_confirmed_lot(client, LOT_AUG)
    mid, _ = _link_kit(client, aug)
    line_id = _kits_line(aug)["id"]
    client.post(f"/api/import-cost/shipments/{aug['id']}/reopen")
    client.put(f"/api/import-cost/shipments/{aug['id']}", json={**LOT_AUG, "fx_rate": "215.00"})
    client.post(f"/api/import-cost/shipments/{aug['id']}/confirm")

    r = client.post(
        f"/api/cost/materials/{mid}/prices/link", json={"import_invoice_line_id": line_id}
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "원장 값이 달라졌다" in detail       # 무엇이 어긋났는지
    assert "갱신" in detail                     # 무엇을 하면 되는지


def test_manual_price_is_not_dragged_into_the_ledger_check(client):
    """수동 입력은 원장 대조 대상이 아니다 — 「어긋났다」고 말하면 그게 거짓말이다."""
    mid = _kit_material_id(client)
    r = client.post(f"/api/cost/materials/{mid}/prices", json={"unit_price_ex_vat": "168"})
    price_id = r.json()["price_id"]
    p = r.json()["material"]["prices"][0]
    assert p["ledger_check"]["status"] == "manual"
    assert p["ledger_check"]["counts_as_evidence"] is True
    # 원장으로 덮지 않는다 — 사람이 입력한 값을 시스템이 갈아치우면 그게 창작이다.
    bad = client.post(f"/api/cost/materials/{mid}/prices/{price_id}/refresh")
    assert bad.status_code == 400
    assert "수동 입력" in bad.json()["detail"]


# ══════════════════════════════════════════════════════════════════
# ★적대 리뷰 1R P2 채택분 — 「코드는 옳은데 불변식이 무방비」인 자리들
# ══════════════════════════════════════════════════════════════════
def test_draft_lot_lines_are_not_listed(client):
    """★P2-2: 확정 전 로트의 라인이 목록에 실리면 안 된다(계약 §9-8).

    변이 주입: `materials.py`의 `ImportShipment.status == "confirmed"` 필터를 지우면
    이 단언이 깨진다 — 초판은 그 변이가 살아남았다.
    """
    r = client.post("/api/import-cost/shipments", json=LOT_AUG)
    assert r.status_code == 201
    assert client.get("/api/cost/ledger-material-lines").json()["items"] == []


def test_lot_count_counts_lots_not_rows(client):
    """★P2-3: `lot_count`가 수동 입력까지 세면 「이 표준의 근거는 로트 N건」이 거짓말이 된다.

    변이 주입: `p.source == "ledger"` 조건을 지우면 아래 `lot_count == 1`이 2가 된다.
    """
    aug = _make_confirmed_lot(client, LOT_AUG)
    mid, _ = _link_kit(client, aug)
    client.post(f"/api/cost/materials/{mid}/prices", json={"unit_price_ex_vat": "168"})

    body = client.get(f"/api/cost/materials/{mid}").json()
    assert body["price_count"] == 2      # 행은 둘
    assert body["lot_count"] == 1        # ★로트는 하나
    assert body["stale_count"] == 0


# ══════════════════════════════════════════════════════════════════
# ★적대 리뷰 2R P2 채택분 — 저장해 둔 `linked_shipment_id`를 판정에 쓴다(P2-a) ·
#   구 연결(신원 스냅샷 없음)이 값 변경에서도 스스로 자백한다(P2-b)
#
# ★둘 다 **GET 응답의 HTTP body**로 잰다(dict가 아니라) — 서비스층 판정이 옳아도
#   `check_payload()`의 직렬화가 한 줄 빠지면 화면은 여전히 옛 값을 「최신」으로 읽는다
#   (교훈 #321과 같은 모양의 구멍). `/refresh`의 400은 서비스 함수를 직접 부르므로 이
#   직렬화 구멍을 못 잡는다 — 그래서 GET 바디를 따로 본다.
# ══════════════════════════════════════════════════════════════════
def test_shipment_identity_mismatch_reaches_the_http_body_and_blocks_refresh(client):
    """★P2-a — 품목명이 우연히 같아도(둘 다 「cleaning kits」) **다른 수입건**을 가리키게
    되면 「값 변경」이 아니라 신원 불일치다.

    재현: `import_invoice_line_id`가 rowid 재사용으로 **다른 수입건**의 라인을 가리키게 된
    상태를 직접 흉내낸다(저장된 신원 스냅샷은 연결 당시 그대로 두고, id가 가리키는 실제
    라인만 다른 수입건 것으로 바꾼다) — 리뷰가 재현한 «수입건 삭제 후 다른 HBL이 rowid를
    물려받는다»와 관측 결과가 같다.
    """
    aug = _make_confirmed_lot(client, LOT_AUG)
    jul = _make_confirmed_lot(client, LOT_JUL)
    mid, price_id = _link_kit(client, aug)
    other_line_id = _kits_line(jul)["id"]

    with client.testing_session() as s:
        from app.models import CostMaterialPrice

        p = s.get(CostMaterialPrice, price_id)
        p.import_invoice_line_id = other_line_id
        s.commit()

    body = client.get(f"/api/cost/materials/{mid}").json()
    p = next(x for x in body["prices"] if x["id"] == price_id)
    check = p["ledger_check"]
    assert check["status"] == "item_mismatch", check   # ★changed로 새면 안 된다
    assert check["refreshable"] is False               # ★HTTP body까지 온 값
    assert check["counts_as_evidence"] is False
    assert body["latest_price_ex_vat"] is None
    assert body["stale_count"] == 1

    r = client.post(f"/api/cost/materials/{mid}/prices/{price_id}/refresh")
    assert r.status_code == 400
    assert "다른 서류" in r.json()["detail"]


def test_legacy_row_without_identity_snapshot_refuses_refresh_over_http_when_value_differs(
    client,
):
    """★P2-b — 신원 스냅샷이 없는 구 연결에서 값이 다르면 «모른다»를 «가격이 바뀐 것»으로
    접지 않는다. HTTP 응답까지 `refreshable=False`가 와야 화면의 「갱신」 버튼이 실제로
    숨는다 — 화면은 이 JSON 말고 다른 걸 보지 않는다.
    """
    aug = _make_confirmed_lot(client, LOT_AUG)
    jul = _make_confirmed_lot(client, LOT_JUL)
    mid, price_id = _link_kit(client, aug)
    other_line_id = _kits_line(jul)["id"]  # 값이 다른 라인(178.78 vs 190.82)

    # 마이그 이전 행(신원 스냅샷 없음)을 흉내낸 뒤, id가 다른 값을 가리키게 한다.
    with client.testing_session() as s:
        from app.models import CostMaterialPrice

        p = s.get(CostMaterialPrice, price_id)
        p.linked_item_name = None
        p.linked_shipment_id = None
        p.import_invoice_line_id = other_line_id
        s.commit()

    body = client.get(f"/api/cost/materials/{mid}").json()
    p = next(x for x in body["prices"] if x["id"] == price_id)
    check = p["ledger_check"]
    assert check["status"] == "changed"
    assert check["refreshable"] is False   # ★핵심 — 신원 불명인데 갱신을 열어 두지 않는다
    assert "구 연결" in check["detail"]

    r = client.post(f"/api/cost/materials/{mid}/prices/{price_id}/refresh")
    assert r.status_code == 400
    assert "구 연결" in r.json()["detail"]
