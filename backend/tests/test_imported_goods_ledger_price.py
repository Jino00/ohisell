"""계약 D-CPP-61 — 수입 완제품의 «원장 파생 단가» (합격 6 개통 + `DEFAULT_FORM_FACTOR` 자백).

## 이 파일이 재는 것

착수 실측이 밝힌 것: 수입 완제품 기계는 **파서에 이미 끝까지 깔려 있었다**
(`recipe_parser.IMPORTED_SECTIONS` → `recipe_kind="imported_goods"`). 그런데 prod
`cost_recipe` 100건이 **전부 `assembly`**였다. 끊은 것은 한 줄이다 — `_match_draft`가
`drafts_by_form.get(form_factor)`로 조회하는데, 초안은 `form_factor=None` 버킷에 쌓이고
조회 키는 `DEFAULT_FORM_FACTOR="bar"` 폴백 때문에 **절대 None이 안 된다.**

그래서 이 파일의 단언은 「함수가 값을 만드나」가 아니라 **「그 관절이 실제로 이어졌나,
그리고 사람이 누를 수 있는 경로가 끝까지 뚫려 있나」**다.

## 지키는 것

1. **픽이 종류를 옮긴다** — 그리고 되돌리면 함께 되돌아온다.
2. **수입 완제품은 «Σ의 퇴화형 1줄»로 선다** — 파서가 라인을 비워도 표준원가가 멈추지 않는다.
3. **개방의 폭이 «수입 완제품 종»에 묶여 있다** — 150건의 `product` 라인이 부자재 표면에
   쏟아지지 않고, 아무 종에나 붙지도 않는다.
4. **자동은 사람이 만든 짝의 «반복»만 가져간다** — 처음 보는 완제품 라인은 후보가 아니다.
5. **폴백이 자백된다** — 값은 그대로 두고 침묵만 없앤다.
6. **보드가 세 값을 나란히 낸다** — 원장 파생 · 엑셀 표준 · 격차.

★HTTP body를 단언한다 — 서비스층 dict만 보면 `response_model` 사고(교훈 #321)를 못 잡는다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    CostMaterial,
    CostMaterialPrice,
    CostRecipe,
    CostTableItem,
    CostTableItemLine,
    ImportInvoiceLine,
    ImportShipment,
)
from app.services.cost_menu import auto_refresh as AR
from app.services.cost_menu import materials as M
from app.services.cost_menu import recipes as R
from app.services.cost_menu.mapping_parser import (
    FORM_SOURCE_FALLBACK,
    FORM_SOURCE_RULE,
    propose_form_factor,
    propose_form_factor_with_source,
)


@pytest.fixture()
def _env():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    # ★prod 세션과 같은 설정 — `autoflush=True`로 두면 「방금 만든 행이 안 보이는」 결함을
    #   이 파일이 원리적으로 못 잡는다(교훈: 픽스처가 prod와 다르면 결함을 못 잡는다).
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    tc = TestClient(app)
    with TestingSession() as s:
        yield tc, s
    app.dependency_overrides.clear()


@pytest.fixture()
def client(_env):
    return _env[0]


@pytest.fixture()
def db_session(_env):
    return _env[1]


# ──────────────────────────────────────────────
# 픽스처
# ──────────────────────────────────────────────
GLASS = "12.2 CNY 강화유리"


def _imported_item(db, *, item_name=GLASS, total="3200.96") -> CostTableItem:
    """수입 완제품 원가표 항목 — **라인이 없다**(파서가 일부러 비운다)."""

    it = CostTableItem(
        section="오타오_강화유리필름",
        item_name=item_name,
        form_factor=None,
        recipe_kind="imported_goods",
        total_inc_vat=Decimal(total),
        row_number=7,
        anomalies="needs_manual_lines",
    )
    db.add(it)
    db.flush()
    return it


def _assembly_item(db) -> CostTableItem:
    it = CostTableItem(
        section="모바일 필름-플립",
        item_name="지문방지_내부3매+외부3매",
        form_factor="flip",
        recipe_kind="assembly",
        total_inc_vat=Decimal("3480.40"),
        row_number=42,
    )
    it.lines.append(
        CostTableItemLine(
            material_name="필름 (flip · 내부)",
            quantity=Decimal("3"),
            ref_price=Decimal("600"),
            source_column="필름",
        )
    )
    db.add(it)
    db.flush()
    return it


def _recipe(db, *, product_name="오하이 풀커버 강화유리", form_factor="bar") -> CostRecipe:
    r = CostRecipe(
        product_name=product_name,
        form_factor=form_factor,
        status="draft",
        source="excel",
        recipe_kind="assembly",
        anomaly_flag="no_recipe_match",
    )
    db.add(r)
    db.flush()
    return r


def _shipment(db, *, status="confirmed", hbl="SETR2608170216") -> ImportShipment:
    sh = ImportShipment(
        hbl_no=hbl,
        declaration_date=date(2026, 8, 18),
        status=status,
        fx_rate=Decimal("209.88"),
        currency="CNY",
        allocation_basis="amount",
    )
    db.add(sh)
    db.flush()
    return sh


def _line(
    db,
    shipment,
    *,
    line_type="product",
    item_name=GLASS,
    ex="2909.96",
    inc="3200.96",
    seq=1,
) -> ImportInvoiceLine:
    ln = ImportInvoiceLine(
        shipment_id=shipment.id,
        seq=seq,
        item_name=item_name,
        line_type=line_type,
        quantity=Decimal("500"),
        unit_price_foreign=Decimal("12.2"),
        unit_cost_ex_vat=Decimal(ex),
        unit_cost_inc_vat=Decimal(inc),
    )
    db.add(ln)
    db.flush()
    return ln


# ══════════════════════════════════════════════════════════════════
# 1. 픽이 종류를 옮긴다 — 차단의 관절
# ══════════════════════════════════════════════════════════════════
def test_pick_carries_the_recipe_kind_from_the_item(client, db_session):
    """★이것이 합격 6 차단의 관절이다.

    자동 매칭은 `form_factor` 버킷을 못 넘는다(초안 None ↔ 레시피 폴백 `bar`). 픽은 그
    버킷을 이미 횡단하고 있었으므로, 남은 것은 «고른 항목의 종류를 레시피에 옮기는 것»뿐이다.
    """

    recipe = _recipe(db_session)
    item = _imported_item(db_session)
    db_session.commit()

    assert recipe.recipe_kind == "assembly"  # 픽 전엔 조립형이다

    res = client.post(
        f"/api/cost/recipes/{recipe.id}/pick-cost-table-item", json={"item_id": item.id}
    )
    assert res.status_code == 200, res.text
    assert res.json()["recipe"]["recipe_kind"] == "imported_goods"

    db_session.expire_all()
    assert db_session.get(CostRecipe, recipe.id).recipe_kind == "imported_goods"


def test_unpick_returns_the_kind_to_assembly(client, db_session):
    """되돌린 뒤에도 「수입 완제품」이 남으면 구성 0줄짜리 레시피를 그렇게 부르게 된다."""

    recipe = _recipe(db_session)
    item = _imported_item(db_session)
    db_session.commit()
    client.post(f"/api/cost/recipes/{recipe.id}/pick-cost-table-item", json={"item_id": item.id})

    res = client.post(f"/api/cost/recipes/{recipe.id}/unpick-cost-table-item")
    assert res.status_code == 200, res.text
    assert res.json()["recipe"]["recipe_kind"] == "assembly"

    db_session.expire_all()
    assert db_session.get(CostRecipe, recipe.id).recipe_kind == "assembly"


def test_picking_an_assembly_item_does_not_turn_a_recipe_into_imported(
    client, db_session
):
    """반대 방향도 잠근다 — 종류는 «고른 항목»에서만 온다."""

    recipe = _recipe(db_session, form_factor="flip")
    item = _assembly_item(db_session)
    db_session.commit()

    res = client.post(f"/api/cost/recipes/{recipe.id}/pick-cost-table-item", json={"item_id": item.id})
    assert res.status_code == 200, res.text
    assert res.json()["recipe"]["recipe_kind"] == "assembly"


# ══════════════════════════════════════════════════════════════════
# 2. 수입 완제품은 «Σ의 퇴화형 1줄»로 선다
# ══════════════════════════════════════════════════════════════════
def test_picking_an_imported_item_creates_exactly_one_line(client, db_session):
    """파서가 라인을 비우므로 픽만으로는 구성이 0줄이고 표준원가가 멈춘다.

    ★항목 자신을 종 1개로 세우고 1줄로 잇는다 — 산술 분기도 `recipe_kind` 값도 안 늘린다.
    ★매수는 **1**이다. 2p·1매입을 파서가 추정하지 않는다(§2-2 · §9 [미상]).
    """

    recipe = _recipe(db_session)
    item = _imported_item(db_session)
    db_session.commit()

    client.post(f"/api/cost/recipes/{recipe.id}/pick-cost-table-item", json={"item_id": item.id})
    db_session.expire_all()

    r = db_session.get(CostRecipe, recipe.id)
    assert len(r.lines) == 1
    line = r.lines[0]
    assert line.quantity == Decimal("1")
    assert line.material.name == GLASS
    # ★이 카테고리가 「원장 `product` 라인을 붙일 수 있다」의 유일한 표지다.
    assert line.material.category == M.IMPORTED_GOODS_CATEGORY
    # 단가 행은 만들지 않는다 — 새 종은 「빈 칸」이지 0이 아니다(§0-E-7).
    assert line.material.prices == []


def test_the_species_is_not_marked_imported_without_a_pick(db_session):
    """표지가 서는 자리는 픽 하나뿐이다 — 그 앞엔 아무 문도 안 열려 있다."""

    m = R._material_for_name(db_session, "그냥 부자재")
    db_session.flush()
    assert m.category == "부자재"
    assert not M.IMPORTED_GOODS_CATEGORY == m.category


# ══════════════════════════════════════════════════════════════════
# 3. 개방의 폭 — 수입 완제품 종에만
# ══════════════════════════════════════════════════════════════════
def test_product_line_links_to_an_imported_species(client, db_session):
    """합격 6의 마지막 관문. 강화유리는 원장에서 `line_type='product'`다."""

    recipe = _recipe(db_session)
    item = _imported_item(db_session)
    db_session.commit()
    client.post(f"/api/cost/recipes/{recipe.id}/pick-cost-table-item", json={"item_id": item.id})

    sh = _shipment(db_session)
    ln = _line(db_session, sh)
    db_session.commit()

    species = db_session.query(CostMaterial).filter(CostMaterial.name == GLASS).one()
    res = client.post(
        f"/api/cost/materials/{species.id}/prices/link", json={"import_invoice_line_id": ln.id}
    )
    assert res.status_code == 201, res.text

    db_session.expire_all()
    price = (
        db_session.query(CostMaterialPrice)
        .filter(CostMaterialPrice.import_invoice_line_id == ln.id)
        .one()
    )
    assert price.source == "ledger"
    # ★원장이 확정 저장한 값을 **복사**한다 — 여기서 다시 산술하지 않는다.
    assert price.unit_price_ex_vat == Decimal("2909.96")
    assert price.unit_price_inc_vat == Decimal("3200.96")


def test_product_line_is_refused_for_a_normal_material(client, db_session):
    """개방은 «전면»이 아니다 — 부자재 종엔 종전대로 `material`만 붙는다."""

    plain = CostMaterial(name="필름 원단", status="approved", category="부자재")
    db_session.add(plain)
    sh = _shipment(db_session)
    ln = _line(db_session, sh)
    db_session.commit()

    res = client.post(
        f"/api/cost/materials/{plain.id}/prices/link", json={"import_invoice_line_id": ln.id}
    )
    assert res.status_code >= 400
    # 거부문이 «다음 수»를 말한다 — 왜 안 되는지만 말하면 사람이 갈 곳이 없다.
    assert "수입 완제품이 아니다" in res.text


def test_ledger_lines_hide_products_by_default_but_opt_in_shows_them(db_session):
    """prod `product` 라인 150건이 부자재 8건을 덮으면 부자재 표면이 못 쓰게 된다."""

    sh = _shipment(db_session)
    _line(db_session, sh, line_type="material", item_name="cleaning kits", seq=1)
    _line(db_session, sh, line_type="product", item_name=GLASS, seq=2)
    db_session.commit()

    default_names = [r["item_name"] for r in M.ledger_material_lines(db_session)]
    assert default_names == ["cleaning kits"]

    opted = M.ledger_material_lines(db_session, include_products=True)
    assert {r["item_name"] for r in opted} == {"cleaning kits", GLASS}
    # 화면이 둘을 갈라 그릴 수 있어야 한다 — 섞어 놓으면 사람이 「왜 여기 있나」를 묻는다.
    assert {r["line_type"] for r in opted} == {"material", "product"}


def test_a_linked_product_line_stays_visible_without_opt_in(client, db_session):
    """★어긋난 연결이 화면에서 사라지는 것이 1R P1-1이 고친 바로 그 병이다."""

    recipe = _recipe(db_session)
    item = _imported_item(db_session)
    db_session.commit()
    client.post(f"/api/cost/recipes/{recipe.id}/pick-cost-table-item", json={"item_id": item.id})
    sh = _shipment(db_session)
    ln = _line(db_session, sh)
    db_session.commit()
    species = db_session.query(CostMaterial).filter(CostMaterial.name == GLASS).one()
    client.post(f"/api/cost/materials/{species.id}/prices/link", json={"import_invoice_line_id": ln.id})
    db_session.commit()

    names = [r["item_name"] for r in M.ledger_material_lines(db_session)]
    assert GLASS in names, "붙여 둔 완제품 라인이 옵트인 없이도 보여야 한다"


# ══════════════════════════════════════════════════════════════════
# 4. 자동은 «반복»만 가져간다
# ══════════════════════════════════════════════════════════════════
def test_auto_refresh_ignores_a_product_line_no_human_ever_linked(db_session):
    """처음 보는 완제품 라인은 후보가 아니다 — 큐로도 안 간다.

    ★관할 밖을 큐에 쌓으면 사람이 매일 150건을 지나치게 되고 그러면 큐가 안 읽힌다.
    """

    sh = _shipment(db_session)
    _line(db_session, sh, line_type="product", item_name="처음 보는 완제품", seq=3)
    db_session.commit()

    assert AR._candidate_lines(db_session) == []


def test_auto_refresh_follows_a_product_line_whose_pair_a_human_made(db_session):
    """사람이 한 번 붙인 품목명이면 다음 로트를 자동이 따라온다."""

    species = CostMaterial(
        name=GLASS, status="approved", category=M.IMPORTED_GOODS_CATEGORY
    )
    db_session.add(species)
    old = _shipment(db_session, hbl="SETR2607220324")
    old_line = _line(db_session, old, seq=1)
    db_session.add(
        CostMaterialPrice(
            material_id=species.id,
            source="ledger",
            unit_price_ex_vat=Decimal("2909.96"),
            unit_price_inc_vat=Decimal("3200.96"),
            import_invoice_line_id=old_line.id,
            linked_item_name=GLASS,
            effective_date=date(2026, 7, 22),
        )
    )
    # 새 로트 — 아직 아무 종에도 안 붙었다.
    new = _shipment(db_session, hbl="SETR2609010001")
    new_line = _line(db_session, new, ex="3010.00", inc="3311.00", seq=1)
    db_session.commit()

    ids = [ln.id for ln in AR._candidate_lines(db_session)]
    assert new_line.id in ids
    assert old_line.id not in ids, "이미 붙은 라인은 후보가 아니다"


def test_material_lines_are_still_candidates(db_session):
    """부자재 경로가 회귀하지 않았다 — 개방이 기존 궤도를 좁히면 안 된다."""

    sh = _shipment(db_session)
    ln = _line(db_session, sh, line_type="material", item_name="cleaning kits", seq=1)
    db_session.commit()

    assert [x.id for x in AR._candidate_lines(db_session)] == [ln.id]


# ══════════════════════════════════════════════════════════════════
# 5. 폴백 자백 — 값은 그대로, 침묵만 없앤다
# ══════════════════════════════════════════════════════════════════
def test_fallback_is_reported_as_fallback():
    """★`bar`를 «내는» 양성 규칙이 하나도 없다 — prod bar 67건 전부가 이 폴백의 산물이다."""

    form, source = propose_form_factor_with_source("아이폰16프로", "투명")
    assert form == "bar"
    assert source == FORM_SOURCE_FALLBACK


def test_a_real_rule_is_reported_as_a_rule():
    form, source = propose_form_factor_with_source("오픽스 필름", "Z플립6")
    assert form == "flip"
    assert source == FORM_SOURCE_RULE


def test_the_fallback_value_itself_is_unchanged():
    """★**제거가 아니라 자백이다** (계약 §3 고유 금지선).

    bar 양성 규칙이 0개라 상수를 없애면 정당한 21건(승인 레시피 포함)까지 그룹 키가 흔들려
    재업로드 시 중복 레시피가 생긴다. 그래서 값은 잠근 채로 둔다.
    """

    assert propose_form_factor("아이폰16프로", "투명") == "bar"


# ══════════════════════════════════════════════════════════════════
# 6. 보드가 세 값을 나란히 낸다 — 합격 6이 지목한 표면
# ══════════════════════════════════════════════════════════════════
def test_board_carries_the_excel_standard_and_its_gap(client, db_session):
    """★현 「격차」 열은 표준원가 vs `cost_price`다 — 엑셀 표준과의 격차는 어느 열에도 없었다."""

    recipe = _recipe(db_session)
    item = _imported_item(db_session, total="3102.70")
    db_session.commit()
    client.post(f"/api/cost/recipes/{recipe.id}/pick-cost-table-item", json={"item_id": item.id})
    db_session.commit()

    res = client.get("/api/cost/board")
    assert res.status_code == 200, res.text
    body = res.json()
    # 링크가 없으면 SKU 행이 없다 — 그때도 응답 형태는 안 무너진다.
    for row in body["items"]:
        assert "excel_total_inc_vat" in row
        assert "excel_gap_pct" in row
        assert "recipe_kind" in row
        assert "form_source" in row


def test_board_gap_is_computed_against_the_excel_standard(db_session):
    """격차 산술 자체를 잠근다 — 두 값 다 VAT 포함 축이라 축이 섞이지 않는다."""

    recipe = _recipe(db_session)
    item = _imported_item(db_session, total="3102.70")
    db_session.commit()
    R.pick_cost_table_item(db_session, recipe.id, item.id)
    db_session.commit()

    saved = R._note_dict(db_session.get(CostRecipe, recipe.id))
    assert saved["excel_total_inc_vat"] == "3102.70"
    # 원장 파생 3,200.96 대비 +3.2% — 초안 실측값(계약 §0-2)과 같은 자리다.
    gap = (Decimal("3200.96") - Decimal("3102.70")) / Decimal("3102.70") * 100
    assert round(float(gap), 1) == 3.2
