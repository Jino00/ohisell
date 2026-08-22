# test_import_cost_http.py — 수입건 원장 HTTP 왕복 (D-CPP-48, 계약 §4 합격기준)
#
# ★**HTTP body를 단언한다.** 서비스층 dict만 보면 못 잡는 사고가 이 저장소에서 실제로 났다
#   (교훈 #321, 2026-08-19: `response_model`이 선언 안 된 키를 응답에서 지워 서비스층 9건이
#   초록인데 배너가 통째로 안 떴다). 이 라우터는 `response_model`을 아예 안 쓰지만, «안 쓴다»가
#   나중에 뒤집힐 수 있으므로 경계를 테스트가 지킨다.
#
# ★합격기준 ⓓ(`product_master.cost_price` 무변경)를 **같은 프로세스에서 before/after로** 잰다 —
#   「순수 추가」라는 주장은 증거 없이는 주장일 뿐이다.
from __future__ import annotations

from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import ProductMaster


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
    yield tc
    app.dependency_overrides.clear()


# ──────────────────────────────────────────────
# 실건 페이로드 — SETR2608170216 (2026-08-18)
# ──────────────────────────────────────────────
COST_LINES = [
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
]

_INV = [
    (1, "Privacy Glass_iP16 Pro 2ea", "50", "19.2", "7.55", "0.026664", "product"),
    (2, "Privacy Glass_iP15 Pro 2ea", "50", "19.2", "7.55", "0.026664", "product"),
    (3, "Glass_Ip17Pro", "500", "12.2", "74.0", "0.26664", "product"),
    (4, "Glass_Ip16 Pro", "350", "12.2", "52.75", "0.186312", "product"),
    (5, "Glass_Ip16 Plus", "50", "12.2", "7.45", "0.026664", "product"),
    (6, "Glass_iP15", "50", "12.2", "14.9", "0.053328", "product"),
    (7, "Glass_iP15 pro", "200", "12.2", "30.0", "0.106656", "product"),
    (8, "Glass_iP14promax", "50", "12.2", "7.6", "0.026664", "product"),
    (9, "Glass_iP13/13pro", "50", "12.2", "7.6", "0.026664", "product"),
    (10, "Glass_iP15 promax", "100", "12.2", "15.0", "0.053328", "product"),
    (11, "Glass_iP15 plus", "50", "12.2", "7.55", "0.026664", "product"),
    (12, "Privacy Glass_iP14pro 2ea", "50", "19.2", "7.55", "0.026664", "product"),
    (13, "Glass_iP13promax", "50", "12.2", "7.65", "0.026664", "product"),
    (14, "Glass_iP12promax", "50", "12.2", "7.65", "0.026664", "product"),
    # ★Jino 확인: "우리 제품에 들어가는 부품이야" → 판매 SKU가 아니라 부자재다.
    (15, "cleaning kits", "2400", "0.8", "33.0", "0.106656", "material"),
]
INVOICE_LINES = [
    {
        "seq": s, "item_name": n, "quantity": q, "unit_price_foreign": p,
        "gross_weight_kg": w, "cbm": c, "line_type": t,
    }
    for s, n, q, p, w, c, t in _INV
]

# PL은 품목을 박스별로 쪼갠다 — CI 15줄 vs PL 16줄(Ip16 Pro 350 = 300 + 50).
_PL = [
    (1, "1", "Privacy Glass_iP16 Pro 2ea", "50"),
    (2, "1", "Privacy Glass_iP15 Pro 2ea", "50"),
    (3, "2-6", "Glass_Ip17Pro", "500"),
    (4, "7-9", "Glass_Ip16 Pro", "300"),
    (5, "10", "Glass_Ip16 Plus", "50"),
    (6, "10", "Glass_Ip16 Pro", "50"),
    (7, "11", "Glass_iP15", "50"),
    (8, "12-13", "Glass_iP15 pro", "200"),
    (9, "14", "Glass_iP14promax", "50"),
    (10, "14", "Glass_iP13/13pro", "50"),
    (11, "15", "Glass_iP15 promax", "100"),
    (12, "16", "Glass_iP15 plus", "50"),
    (13, "16", "Privacy Glass_iP14pro 2ea", "50"),
    (14, "17", "Glass_iP13promax", "50"),
    (15, "17", "Glass_iP12promax", "50"),
    (16, "18-19", "cleaning kits", "2400"),
]
PACKING_LINES = [
    {"seq": s, "carton_range": c, "item_name": n, "quantity": q} for s, c, n, q in _PL
]

SHIPMENT = {
    "hbl_no": "SETR2608170216",
    "declaration_no": "15443-26-701565M",
    "declaration_date": "2026-08-18",
    "eta": "2026-08-18",
    "shipper_name": "SHENZHEN OTAO TECHNOLOGY LIMITED",
    "invoice_no": "SO-WSOH-116-115-114",
    "vessel": "HANSUNG INCHEON / 3594E",
    "currency": "CNY",
    "fx_rate": "209.88",
    "declared_inv_value": "23100",
    "customs_value_krw": "4862657",
    "carton_count": 19,
    "gross_weight_kg": "287.8",
    "cbm": "1.013232",
    "allocation_basis": "amount",
    "cost_lines": COST_LINES,
    "invoice_lines": INVOICE_LINES,
    "packing_lines": PACKING_LINES,
}


def _create(client, **overrides):
    body = {**SHIPMENT, **overrides}
    r = client.post("/api/import-cost/shipments", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _line(payload, name, key="invoice_lines"):
    return next(x for x in payload[key] if x["item_name"] == name)


# ──────────────────────────────────────────────
# 합격기준 ⓐ~ⓒ — HTTP body에서 직접
# ──────────────────────────────────────────────
def test_confirm_passes_all_three_checks(client):
    """ⓐ 검산 3종(수량합·INV총액·배부잔액0)이 응답 본문에 통과로 실린다."""
    ship = _create(client)
    r = client.post(f"/api/import-cost/shipments/{ship['id']}/confirm")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["confirm_result"]["confirmed"] is True, body["confirm_result"]
    rec = body["reconcile"]
    assert rec["passed"] is True
    keys = {c["key"]: c for c in rec["checks"]}
    assert set(keys) == {"quantity", "invoice_total", "allocation"}
    for k, c in keys.items():
        assert c["status"] == "ok", (k, c)
    assert body["status"] == "confirmed"
    assert body["confirmed_at"] is not None
    # 배부 잔액 0이 응답에 명시된다
    assert body["allocation"]["unallocated_krw"] == "0"
    assert body["allocation"]["pool_krw"] == "661620"


def test_acceptance_unit_costs_in_http_body(client):
    """ⓑ Ip17Pro 단가 두 값이 **저장된 뒤 재조회한 응답**에 실린다."""
    ship = _create(client)
    client.post(f"/api/import-cost/shipments/{ship['id']}/confirm")
    body = client.get(f"/api/import-cost/shipments/{ship['id']}").json()

    ip17 = _line(body, "Glass_Ip17Pro")
    assert abs(D(ip17["unit_cost_ex_vat"]) - D("2910")) <= D("1"), ip17
    assert abs(D(ip17["unit_cost_inc_vat"]) - D("3201")) <= D("1"), ip17
    assert ip17["goods_amount_krw"] is not None
    assert ip17["allocated_cost_krw"] is not None


def test_acceptance_material_line_and_allocation(client):
    """ⓒ cleaning kits가 `material`로 저장되고 배부액이 응답에 실린다."""
    ship = _create(client)
    client.post(f"/api/import-cost/shipments/{ship['id']}/confirm")
    body = client.get(f"/api/import-cost/shipments/{ship['id']}").json()

    kits = _line(body, "cleaning kits")
    assert kits["line_type"] == "material"
    assert abs(D(kits["allocated_cost_krw"]) - D("54992")) <= D("1"), kits
    # 판매 SKU 라인은 product 그대로다 — 분류가 뭉개지지 않는다
    assert _line(body, "Glass_Ip17Pro")["line_type"] == "product"


def test_actual_vat_is_surfaced_separately(client):
    """×1.1 규약과 실제 세액의 차이를 화면이 볼 수 있어야 한다 — 응답에 별 필드로 나온다."""
    ship = _create(client)
    body = client.get(f"/api/import-cost/shipments/{ship['id']}").json()
    # 값으로 비교한다 — DB Numeric(16,2)를 거치면 "522730.00"으로 돌아온다.
    assert D(body["actual_vat_krw"]) == D("522730")


# ──────────────────────────────────────────────
# 합격기준 ⓓ — cost_price 무변경 (순수 추가 증거)
# ──────────────────────────────────────────────
def test_product_master_cost_price_untouched(client):
    """ⓓ 원장을 만들고 확정해도 `product_master.cost_price`가 한 행도 안 움직인다."""
    Session = client.testing_session
    with Session() as db:
        db.add_all([
            ProductMaster(internal_sku="OHI-TGLASS-IP17PRO", product_name="아이폰17프로 강화유리",
                          cost_price=D("3102.70")),
            ProductMaster(internal_sku="OHI-KIT-CLEAN", product_name="클리닝킷",
                          cost_price=D("0")),
        ])
        db.commit()
        before = sorted((p.internal_sku, str(p.cost_price)) for p in db.query(ProductMaster).all())

    ship = _create(client)
    # 매핑까지 걸어 둔다 — 「연결했으니 값도 따라 움직이지 않나」를 실제로 반증한다.
    body = {**SHIPMENT, "invoice_lines": [
        {**ln, "internal_sku": "OHI-TGLASS-IP17PRO"} if ln["item_name"] == "Glass_Ip17Pro"
        else {**ln, "internal_sku": "OHI-KIT-CLEAN"} if ln["item_name"] == "cleaning kits"
        else ln
        for ln in INVOICE_LINES
    ]}
    r = client.put(f"/api/import-cost/shipments/{ship['id']}", json=body)
    assert r.status_code == 200, r.text
    assert client.post(f"/api/import-cost/shipments/{ship['id']}/confirm").json()[
        "confirm_result"]["confirmed"] is True

    with Session() as db:
        after = sorted((p.internal_sku, str(p.cost_price)) for p in db.query(ProductMaster).all())
    assert after == before, f"cost_price가 움직였다: {before} -> {after}"


# ──────────────────────────────────────────────
# 합격기준 ⓔ — 원본 서류 왕복
# ──────────────────────────────────────────────
def test_document_upload_and_download_roundtrip(client):
    """ⓔ 업로드한 원본이 바이트 단위로 그대로 돌아온다(한글 파일명 포함)."""
    ship = _create(client)
    blob = b"\xd0\xcf\x11\xe0" + b"OLE2-ish payload" * 8
    name = "8월17일선적 통관서류.xls"
    r = client.post(
        f"/api/import-cost/shipments/{ship['id']}/documents",
        params={"doc_type": "ci"},
        files={"file": (name, blob, "application/vnd.ms-excel")},
    )
    assert r.status_code == 201, r.text
    doc_id = r.json()["id"]
    assert r.json()["size_bytes"] == len(blob)

    listed = client.get(f"/api/import-cost/shipments/{ship['id']}").json()["documents"]
    assert [d["id"] for d in listed] == [doc_id]
    assert listed[0]["filename"] == name

    dl = client.get(f"/api/import-cost/shipments/{ship['id']}/documents/{doc_id}")
    assert dl.status_code == 200
    assert dl.content == blob
    # 비ASCII 파일명이 RFC 5987로 실린다 — latin-1 헤더에 그냥 넣으면 500이 난다.
    assert "filename*=UTF-8''" in dl.headers["content-disposition"]


def test_empty_document_rejected(client):
    ship = _create(client)
    r = client.post(
        f"/api/import-cost/shipments/{ship['id']}/documents",
        params={"doc_type": "expense"},
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert r.status_code == 400


# ──────────────────────────────────────────────
# 금지선 — 검산 미통과 건은 확정될 수 없다
# ──────────────────────────────────────────────
def test_confirm_refused_when_quantities_mismatch(client):
    """PL 수량을 1개 줄이면 확정이 거부되고 **상태가 안 바뀐다**."""
    bad_pl = [dict(p) for p in PACKING_LINES]
    bad_pl[2]["quantity"] = "499"
    ship = _create(client, packing_lines=bad_pl)

    r = client.post(f"/api/import-cost/shipments/{ship['id']}/confirm")
    assert r.status_code == 200
    body = r.json()
    assert body["confirm_result"]["confirmed"] is False
    assert body["status"] == "draft"
    assert body["confirmed_at"] is None
    qty = next(c for c in body["reconcile"]["checks"] if c["key"] == "quantity")
    assert qty["status"] == "mismatch"
    assert qty["rows"], "어긋난 품목이 응답에 실려야 사람이 고칠 수 있다"
    # 단가가 저장되지 않았다 — 미통과 건의 계산 결과는 남으면 안 된다
    assert all(ln["unit_cost_ex_vat"] is None for ln in body["invoice_lines"])


def test_confirm_refused_when_invoice_total_mismatches(client):
    ship = _create(client, declared_inv_value="23000")
    body = client.post(f"/api/import-cost/shipments/{ship['id']}/confirm").json()
    assert body["confirm_result"]["confirmed"] is False
    assert body["status"] == "draft"
    tot = next(c for c in body["reconcile"]["checks"] if c["key"] == "invoice_total")
    assert tot["status"] == "mismatch"


def test_confirmed_shipment_cannot_be_edited(client):
    ship = _create(client)
    client.post(f"/api/import-cost/shipments/{ship['id']}/confirm")
    r = client.put(f"/api/import-cost/shipments/{ship['id']}", json=SHIPMENT)
    assert r.status_code == 409
    assert client.delete(f"/api/import-cost/shipments/{ship['id']}").status_code == 409


def test_reopen_clears_computed_values(client):
    """확정을 풀면 계산 결과가 **지워진다** — 낡은 단가가 확정값인 척 남으면 안 된다."""
    ship = _create(client)
    client.post(f"/api/import-cost/shipments/{ship['id']}/confirm")
    body = client.post(f"/api/import-cost/shipments/{ship['id']}/reopen").json()
    assert body["status"] == "draft"
    assert body["confirmed_at"] is None
    for ln in body["invoice_lines"]:
        assert ln["unit_cost_ex_vat"] is None
        assert ln["unit_cost_inc_vat"] is None
        assert ln["allocated_cost_krw"] is None
        assert ln["goods_amount_krw"] is None


def test_duplicate_hbl_rejected(client):
    _create(client)
    r = client.post("/api/import-cost/shipments", json=SHIPMENT)
    assert r.status_code == 409


def test_is_costing_is_required_not_defaulted(client):
    """`is_costing`에 기본값을 두지 않는다 — 빠뜨리면 422로 막힌다.

    기본 True였다면 부가세 라인을 빠뜨린 요청이 조용히 통과해 통관비가 부풀었을 자리다.
    """
    bad = [dict(c) for c in COST_LINES]
    bad[8].pop("is_costing")
    r = client.post("/api/import-cost/shipments", json={**SHIPMENT, "cost_lines": bad})
    assert r.status_code == 422


def test_basis_comparison_reproduces_the_decision(client):
    """D-CPP-48 ①의 근거(금액·중량·부피 수렴, 수량만 이탈)를 API가 매 건 재현한다."""
    ship = _create(client)
    body = client.get(f"/api/import-cost/shipments/{ship['id']}/basis-comparison").json()
    got = {}
    for entry in body["comparison"]:
        assert entry["available"] is True, entry
        assert entry["unallocated_krw"] == "0"
        kits = next(l for l in entry["lines"] if l["item_name"] == "cleaning kits")
        got[entry["basis"]] = D(kits["allocated_cost_krw"])
    assert got["amount"] < D("60000")
    assert got["quantity"] > D("380000")


def test_list_endpoint_shape(client):
    _create(client)
    body = client.get("/api/import-cost/shipments").json()
    assert body["count"] == 1
    item = body["items"][0]
    assert item["hbl_no"] == "SETR2608170216"
    assert item["line_count"] == 15
    assert item["status"] == "draft"
    # 목록엔 상세를 싣지 않는다 — payload가 커지면 목록 화면이 느려진다
    assert "invoice_lines" not in item


def test_unknown_shipment_is_404(client):
    assert client.get("/api/import-cost/shipments/99999").status_code == 404


# ──────────────────────────────────────────────
# 적대 리뷰 P1-1 회귀 (HTTP 층)
# ──────────────────────────────────────────────
def test_confirm_refused_when_no_cost_document(client):
    """★통관경비서를 한 줄도 안 넣은 건은 **확정되지 않는다.**

    초판은 `pool=0 · allocated=0`을 「미배분 0원 = 통과」로 접어 confirmed=True를 냈고,
    통관비 661,620원이 통째로 빠진 단가(2,560.54원)가 확정으로 저장됐다. 화면엔 초록
    「전항 통과」가 떠서 사람이 구분할 방법이 없었다(적대 리뷰 P1-1 재현 ②).
    """
    ship = _create(client, cost_lines=[])
    body = client.post(f"/api/import-cost/shipments/{ship['id']}/confirm").json()

    assert body["confirm_result"]["confirmed"] is False
    assert body["status"] == "draft"
    assert body["reconcile"]["passed"] is False
    alloc = next(c for c in body["reconcile"]["checks"] if c["key"] == "allocation")
    assert alloc["status"] == "missing", alloc
    # 단가가 저장되지 않았다 — 통관비 빠진 값이 「확정」으로 남으면 안 된다
    assert all(ln["unit_cost_ex_vat"] is None for ln in body["invoice_lines"])


def test_confirm_refused_when_every_cost_line_is_non_costing(client):
    """전 라인이 «원가성 아님»이어도 같다 — 부가세만 넣고 확정하는 경로를 막는다."""
    only_vat = [{**c, "is_costing": False} for c in COST_LINES]
    ship = _create(client, cost_lines=only_vat)
    body = client.post(f"/api/import-cost/shipments/{ship['id']}/confirm").json()
    assert body["confirm_result"]["confirmed"] is False
    alloc = next(c for c in body["reconcile"]["checks"] if c["key"] == "allocation")
    assert alloc["status"] == "missing"


def test_allocation_check_is_missing_when_allocation_errors(client):
    """배부가 예외로 못 돌면 검산이 «전항 통과»라고 말하지 않는다(재현 ③).

    중량 결측 + weight 기준 → `allocate()`가 AllocationError. 초판은 그 상태에서도
    allocation 체크가 `ok`였다.
    """
    no_weight = [{**ln, "gross_weight_kg": None} for ln in INVOICE_LINES]
    ship = _create(client, allocation_basis="weight", invoice_lines=no_weight)
    body = client.get(f"/api/import-cost/shipments/{ship['id']}").json()

    assert body["allocation"] is None
    assert body["allocation_error"]
    assert body["reconcile"]["passed"] is False
    alloc = next(c for c in body["reconcile"]["checks"] if c["key"] == "allocation")
    assert alloc["status"] == "missing"
    assert alloc["actual"] is None


# ──────────────────────────────────────────────
# 살아남은 변이 #13·#14 — 배부기준 기본값이 무테스트였다
# ──────────────────────────────────────────────
def test_allocation_basis_defaults_to_amount(client):
    """요청이 basis를 생략하면 «금액»이다 — D-CPP-48 ①의 기본값.

    변이 #13(`ShipmentIn.allocation_basis` 기본을 quantity로)이 40건 전부 초록으로
    살아남았다. 뒤집히면 Ip17Pro가 2,910 → 2,724가 되는데 아무도 안 잰다.
    """
    body = {k: v for k, v in SHIPMENT.items() if k != "allocation_basis"}
    r = client.post("/api/import-cost/shipments", json=body)
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["allocation_basis"] == "amount"
    assert created["allocation"]["basis"] == "amount"

    client.post(f"/api/import-cost/shipments/{created['id']}/confirm")
    detail = client.get(f"/api/import-cost/shipments/{created['id']}").json()
    ip17 = _line(detail, "Glass_Ip17Pro")
    assert abs(D(ip17["unit_cost_ex_vat"]) - D("2910")) <= D("1")


def test_server_fallback_basis_is_amount(client):
    """서버층 fallback(`build_reconcile`의 `or "amount"`)도 금액이다 — 변이 #14의 짝."""
    from app.services.import_cost import ledger
    from app.models import ImportShipment

    ship = _create(client)
    Session = client.testing_session
    with Session() as db:
        row = db.query(ImportShipment).filter(ImportShipment.id == ship["id"]).one()
        row.allocation_basis = ""          # 빈 값 → fallback 경로를 탄다
        db.commit()
        row = ledger.get_shipment(db, ship["id"])
        _, result, _ = ledger.build_reconcile(row)
    assert result is not None
    assert result.basis == "amount"


# ──────────────────────────────────────────────
# 살아남은 변이 #12 — 수입건 교차 문서 접근
# ──────────────────────────────────────────────
def test_document_of_another_shipment_is_not_reachable(client):
    """다른 수입건의 문서 id로는 못 꺼낸다 — 코드는 맞았으나 아무도 안 재고 있었다."""
    a = _create(client)
    b = _create(client, hbl_no="SETR-OTHER-0001")
    up = client.post(
        f"/api/import-cost/shipments/{a['id']}/documents",
        params={"doc_type": "ci"},
        files={"file": ("a.xlsx", b"AAAA", "application/octet-stream")},
    )
    doc_id = up.json()["id"]

    assert client.get(f"/api/import-cost/shipments/{a['id']}/documents/{doc_id}").status_code == 200
    assert client.get(f"/api/import-cost/shipments/{b['id']}/documents/{doc_id}").status_code == 404
    assert client.delete(f"/api/import-cost/shipments/{b['id']}/documents/{doc_id}").status_code == 404
    # 삭제가 거부됐으니 원래 건에서는 여전히 살아 있다
    assert client.get(f"/api/import-cost/shipments/{a['id']}/documents/{doc_id}").status_code == 200


def test_list_does_not_load_document_blobs(client):
    """목록 API가 파일 본문을 SELECT하지 않는다(P2-1).

    `document_count`를 세려고 BLOB까지 읽으면 목록 한 번에 수백 MB가 올라온다.
    `content`가 deferred라 실제로 안 읽히는지를 **발생한 SQL로** 잰다.
    """
    from sqlalchemy import event

    ship = _create(client)
    client.post(
        f"/api/import-cost/shipments/{ship['id']}/documents",
        params={"doc_type": "ci"},
        files={"file": ("big.xlsx", b"X" * 4096, "application/octet-stream")},
    )

    seen: list[str] = []
    engine = client.testing_session.kw["bind"]

    @event.listens_for(engine, "before_cursor_execute")
    def _cap(conn, cursor, statement, params, context, executemany):
        seen.append(statement)

    try:
        r = client.get("/api/import-cost/shipments")
        assert r.status_code == 200
        assert r.json()["items"][0]["document_count"] == 1
    finally:
        event.remove(engine, "before_cursor_execute", _cap)

    doc_selects = [s for s in seen if "import_document" in s and "SELECT" in s.upper()]
    assert doc_selects, "문서 테이블을 아예 안 읽었다면 count가 어디서 나왔는지 확인해야 한다"
    # ★`content_type`이 아니라 **`content` 컬럼 그 자체**만 잡는다 — 단어경계가 없으면
    #   `import_document.content_type`에 걸려 거짓 실패가 난다(첫 작성 시 실제로 그랬다).
    import re as _re

    blob_reads = [s for s in doc_selects if _re.search(r"import_document\.content\b", s)]
    assert not blob_reads, f"목록 조회가 BLOB 컬럼을 읽었다: {blob_reads}"

    # 다운로드 경로에서는 반대로 **반드시** 읽혀야 한다 — deferred가 기능을 죽이지 않았다는 증거.
    listed = client.get(f"/api/import-cost/shipments/{ship['id']}").json()["documents"]
    dl = client.get(
        f"/api/import-cost/shipments/{ship['id']}/documents/{listed[0]['id']}"
    )
    assert dl.status_code == 200
    assert dl.content == b"X" * 4096
