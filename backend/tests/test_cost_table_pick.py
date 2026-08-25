"""계약 A′ 개정 4 (D-CPP-59) — 원가표 항목 저장 + 사람의 픽.

## 이 파일이 재는 것

개정 4 전까지 화면은 「후보 N건 — 사람이 고른다」라고 말했는데 **고를 길이 백엔드에 0건**
이었다(엔드포인트 17개 전수 확인). 그래서 이 파일의 단언은 「함수가 값을 만드나」가 아니라
**「사람이 누를 수 있는 경로가 실제로 있고, 그 경로가 상태를 바꾸나」**다.

## 테스트가 지키는 4가지 (계약 §7 합격 18~21)

1. 원가표 항목이 **저장된다** — 전에는 파싱 후 버려졌다.
2. 픽이 **재업로드 없이 즉시** 구성을 만든다.
3. 「원가표에 없음」이 **「아직 안 봄」과 구별되는 상태**로 기록된다.
4. **재업로드가 픽을 덮지 않고**, 핀 대상이 사라지거나 동명 2건이 되면 **말한다**(조용한 소실 금지).

★HTTP body를 단언한다 — 서비스층 dict만 보면 `response_model` 사고(교훈 #321)를 못 잡는다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    CostRecipe,
    CostRecipeLine,
    CostTableItem,
    CostTableItemLine,
)
from app.services.cost_menu import recipes as R


@pytest.fixture()
def _env():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    # ★prod 세션과 같은 설정이어야 한다 — `autoflush=True`로 두면 「방금 만든 행이 안 보이는」
    #   결함(교훈: 픽스처가 prod와 다르면 결함을 못 잡는다)을 이 파일이 원리적으로 못 잡는다.
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


def _item(
    db,
    *,
    section="모바일 필름-플립",
    item_name="지문방지_내부3매+외부3매",
    form_factor="flip",
    total="3480.40",
    lines=(("필름 (flip · 내부)", "3", "600"), ("부착 안내문 (flip)", "1", "40")),
    row_number=42,
    anomalies=None,
) -> CostTableItem:
    it = CostTableItem(
        section=section,
        item_name=item_name,
        form_factor=form_factor,
        recipe_kind="assembly",
        total_inc_vat=Decimal(total) if total is not None else None,
        row_number=row_number,
        anomalies=anomalies,
    )
    for name, qty, ref in lines:
        it.lines.append(
            CostTableItemLine(
                material_name=name,
                quantity=Decimal(qty),
                ref_price=Decimal(ref) if ref is not None else None,
                source_column=name,
            )
        )
    db.add(it)
    db.flush()
    return it


def _recipe(db, *, product_name="오픽스 Z플립 폴드 4매", form_factor="flip", note=None):
    r = CostRecipe(
        product_name=product_name,
        form_factor=form_factor,
        status="draft",
        source="excel",
        anomaly_flag="no_recipe_match",
        note=note,
    )
    db.add(r)
    db.flush()
    return r


# ──────────────────────────────────────────────
# 합격 18 — 고를 목록이 있고, 고르면 «즉시» 구성이 붙는다
# ──────────────────────────────────────────────
def test_cost_table_items_lists_the_form_factor_bucket(client, db_session):
    """목록이 그 폼팩터 전건을 준다 — 가격이 안 맞아도."""

    recipe = _recipe(db_session)
    _item(db_session)
    _item(db_session, item_name="사생활보호_외부2매", total="4100.00", row_number=43)
    # 다른 폼팩터는 안 섞인다.
    _item(db_session, section="모바일 필름-폴드", item_name="폴드용", form_factor="fold")
    db_session.commit()

    res = client.get(f"/api/cost/recipes/{recipe.id}/cost-table-items")
    assert res.status_code == 200
    body = res.json()
    names = [i["item_name"] for i in body["items"]]
    assert "지문방지_내부3매+외부3매" in names
    assert "사생활보호_외부2매" in names
    assert "폴드용" not in names
    # ★제안이 0건이어도 목록은 나온다 — 제안이 픽의 전제였다면 열쇠를 바꾼 의미가 없다.
    assert body["suggested_count"] == 0
    assert len(body["items"]) == 2


def test_cost_table_items_include_form_factor_none_items(client, db_session):
    """폼팩터 없는 수입 완제품 항목도 목록에 실린다 (계약 §0-E-11).

    ★이걸 빼면 화면이 계약 §0-E-11이 진단한 그 차단(수입 완제품은 어떤 레시피와도 못 만난다)을
    그대로 물려받는다 — 합격 6은 이번 범위 밖이지만 «목록에서 지우는 것»은 다른 문제다.
    """

    recipe = _recipe(db_session)
    _item(
        db_session,
        section="오타오_강화유리필름",
        item_name="Glass_Ip17Pro",
        form_factor=None,
        lines=(),
    )
    db_session.commit()

    body = client.get(f"/api/cost/recipes/{recipe.id}/cost-table-items").json()
    row = next(i for i in body["items"] if i["item_name"] == "Glass_Ip17Pro")
    assert row["form_factor"] is None
    # 구성 줄이 0인 사실도 숨기지 않는다 — 파서가 그 섹션에서 라인을 안 뽑기 때문이다.
    assert row["line_count"] == 0


def test_suggested_flag_marks_price_match_but_does_not_confirm(client, db_session):
    """가격 일치는 «제안 라벨»이지 확정이 아니다 (계약 §0-E-2)."""

    recipe = _recipe(db_session, note='{"cost_price_mode": "3480.40"}')
    _item(db_session)
    _item(db_session, item_name="다른 품목", total="9999.00")
    db_session.commit()

    body = client.get(f"/api/cost/recipes/{recipe.id}/cost-table-items").json()
    assert body["suggested_count"] == 1
    assert body["cost_price_mode"] == "3480.40"
    # 제안이 «맨 위»다 — 사람이 먼저 본다.
    assert body["items"][0]["item_name"] == "지문방지_내부3매+외부3매"
    assert body["items"][0]["suggested"] is True
    # ★그러나 픽은 아직 아무것도 안 됐다 — 제안이 자동 확정이 되면 §2-2가 뚫린다.
    assert body["items"][0]["picked"] is False
    recipe_body = client.get(f"/api/cost/recipes/{recipe.id}").json()
    assert recipe_body["picked"]["state"] == "none"
    assert recipe_body["line_count"] == 0


def test_pick_materializes_composition_immediately(client, db_session):
    """★핵심 — 픽 한 번에 구성이 붙는다. **재업로드가 필요 없다**(합격 18).

    안(가)를 기각한 이유가 이 단언이다: 픽 직후 화면이 안 움직이면 「안내한 길이 안 온다」의
    재생산이다.
    """

    recipe = _recipe(db_session)
    item = _item(db_session)
    db_session.commit()

    res = client.post(
        f"/api/cost/recipes/{recipe.id}/pick-cost-table-item", json={"item_id": item.id}
    )
    assert res.status_code == 200
    payload = res.json()["recipe"]
    assert payload["line_count"] == 2
    assert payload["picked"]["state"] == "picked"
    assert payload["picked"]["item_name"] == "지문방지_내부3매+외부3매"
    assert payload["picked"]["picked_at"] is not None
    # 픽은 승인이 아니다 — status는 draft 그대로여야 한다(계약 §0-E-7 금지선).
    assert payload["status"] == "draft"

    db_session.expire_all()
    lines = db_session.query(CostRecipeLine).filter(CostRecipeLine.recipe_id == recipe.id).all()
    assert {str(l.quantity) for l in lines} == {"3.000", "1.000"}


def test_pick_does_not_import_reference_prices_as_unit_prices(client, db_session):
    """참고값은 라인을 타고 단가가 되지 않는다 (§3 금지선 · §0-E-7).

    ★변이 시험: `_apply_item_lines`가 `ref_price`를 `cost_material_price`로 옮기면 이 단언이 죽는다.
    """

    from app.models import CostMaterialPrice

    recipe = _recipe(db_session)
    item = _item(db_session)
    db_session.commit()

    client.post(
        f"/api/cost/recipes/{recipe.id}/pick-cost-table-item", json={"item_id": item.id}
    )
    db_session.expire_all()
    assert db_session.query(CostMaterialPrice).count() == 0
    # 계산은 「단가 없음」으로 남는다 — 0으로 채우지 않는다(§2-7).
    body = client.get(f"/api/cost/recipes/{recipe.id}").json()
    assert body["standard"]["computable"] is False
    assert body["standard"]["std_cost_inc_vat"] is None


def test_pick_on_approved_recipe_is_refused(client, db_session):
    """승인분은 픽으로 갈아치우지 않는다 — 재수입이 승인분을 안 덮는 것과 같은 규율."""

    recipe = _recipe(db_session)
    recipe.status = "approved"
    item = _item(db_session)
    db_session.commit()

    res = client.post(
        f"/api/cost/recipes/{recipe.id}/pick-cost-table-item", json={"item_id": item.id}
    )
    assert res.status_code == 409


def test_unpick_removes_the_composition_it_created(client, db_session):
    recipe = _recipe(db_session)
    item = _item(db_session)
    db_session.commit()
    client.post(
        f"/api/cost/recipes/{recipe.id}/pick-cost-table-item", json={"item_id": item.id}
    )

    res = client.post(f"/api/cost/recipes/{recipe.id}/unpick-cost-table-item")
    assert res.status_code == 200
    payload = res.json()["recipe"]
    assert payload["line_count"] == 0
    assert payload["picked"]["state"] == "none"


# ──────────────────────────────────────────────
# 합격 19 — 「없음」과 「아직 안 봄」은 다른 상태다
# ──────────────────────────────────────────────
def test_absent_confirmation_is_distinguishable_from_never_looked(client, db_session):
    """★이 파일에서 가장 중요한 단언 하나.

    두 레시피가 **똑같이 구성 0줄**인데, 하나는 사람이 목록을 다 보고 「없다」고 판정했고
    하나는 아무도 안 봤다. 화면이 둘을 같은 모양으로 그리면 침묵이 판정으로 읽힌다.
    """

    looked = _recipe(db_session, product_name="사람이 확인한 것")
    never = _recipe(db_session, product_name="아무도 안 본 것")
    db_session.commit()

    res = client.post(
        f"/api/cost/recipes/{looked.id}/confirm-cost-table-absent",
        json={"note": "필름이 아니라 사입 상품이다"},
    )
    assert res.status_code == 200
    a = res.json()["recipe"]["picked"]
    b = client.get(f"/api/cost/recipes/{never.id}").json()["picked"]

    assert a["state"] == "absent"
    assert a["absent_confirmed_at"] is not None
    assert a["absent_note"] == "필름이 아니라 사입 상품이다"
    assert b["state"] == "none"
    assert b["absent_confirmed_at"] is None
    # 둘 다 구성은 0줄이다 — 그런데도 상태가 갈린다는 것이 요점이다.
    assert client.get(f"/api/cost/recipes/{looked.id}").json()["line_count"] == 0
    assert client.get(f"/api/cost/recipes/{never.id}").json()["line_count"] == 0


def test_absent_confirmation_is_refused_when_already_picked(client, db_session):
    """「골랐다」와 「없다」는 동시에 참일 수 없다."""

    recipe = _recipe(db_session)
    item = _item(db_session)
    db_session.commit()
    client.post(
        f"/api/cost/recipes/{recipe.id}/pick-cost-table-item", json={"item_id": item.id}
    )

    res = client.post(f"/api/cost/recipes/{recipe.id}/confirm-cost-table-absent", json={})
    assert res.status_code == 409


def test_picking_clears_a_previous_absent_confirmation(client, db_session):
    recipe = _recipe(db_session)
    item = _item(db_session)
    db_session.commit()
    client.post(f"/api/cost/recipes/{recipe.id}/confirm-cost-table-absent", json={})

    payload = client.post(
        f"/api/cost/recipes/{recipe.id}/pick-cost-table-item", json={"item_id": item.id}
    ).json()["recipe"]
    assert payload["picked"]["state"] == "picked"
    assert payload["picked"]["absent_confirmed_at"] is None


# ──────────────────────────────────────────────
# 합격 20 — 재업로드가 픽을 덮지 않고, 끊기면 말한다
# ──────────────────────────────────────────────
#: 실물 시트의 «레이아웃 (b)» — 섹션 제목 행이 곧 헤더 행이다(플립 계열의 실제 모양).
#: 열 위치·줄바꿈까지 `test_cost_menu_recipes.py`의 정본 재현과 같게 둔다 — 픽스처가 실물과
#: 다르면 파서가 실제로 무엇을 읽는지 이 파일이 못 잰다.
_FLIP_HEADER = (
    None, "모바일 필름-플립", None, "제품원가\n(+VAT)", "내부\n매입", "내부\n필름",
    "내부\n필름*매입", "외부\n매입", "외부\n필름", "외부\n필름*매입",
    "부착\n안내문", "스퀴즈\n6.5cm", "부자재\n(밀대외)", "알콜솜\n2EA",
    "비닐\n(9*18)", "비닐\n(12*22+4)", "패키지", "폼텍\n스티커",
)


def _cost_rows(item_name="지문방지_내부3매+외부3매", total=3480.4):
    """`parse_cost_table`이 먹는 최소 시트 — 플립 섹션 1개·품목 1개."""

    return [
        (None, "*원가표_25년") + (None,) * 16,
        (None,) * 18,
        _FLIP_HEADER,
        (
            None, item_name, None, total, 3, 600, 1800, 3, 350, 1050,
            30, 80, 22, 60, 8, 10, 98, 6,
        ),
    ]


def _reimport(db, rows):
    out = R.import_drafts(db, cost_rows=rows)
    db.commit()
    return out


def test_reimport_keeps_the_human_pick(db_session):
    """★합격 20 — 원가표를 다시 올려도 사람이 고른 구성이 그대로 남는다.

    ★변이 시험: `import_drafts`의 핀 건너뛰기를 지우면 `recipe.lines.clear()`가 돌아
    구성이 사라진다. 그러면 이 단언이 죽는다.
    """

    rows = _cost_rows()
    _reimport(db_session, rows)
    item = db_session.query(CostTableItem).one()
    recipe = _recipe(db_session, form_factor="flip")
    db_session.commit()
    R.pick_cost_table_item(db_session, recipe.id, item.id)
    before = len(R.get_recipe(db_session, recipe.id).lines)
    assert before > 0

    out = _reimport(db_session, rows)

    db_session.expire_all()
    after = R.get_recipe(db_session, recipe.id)
    assert len(after.lines) == before
    assert after.picked_item_key is not None
    assert out["skipped_pinned"] == 1
    assert out["pins"]["relinked"] == 1
    # ★핀이 «다시 이어졌다»는 것은 새 행 id를 가리킨다는 뜻이다 — 옛 행은 지워졌다.
    assert after.picked_item_id == db_session.query(CostTableItem).one().id


def test_reimport_flags_a_lost_pin_instead_of_silently_dropping_it(db_session):
    """★「조용한 소실은 미달이다」 — 핀 대상이 사라지면 **말한다**."""

    _reimport(db_session, _cost_rows())
    item = db_session.query(CostTableItem).one()
    recipe = _recipe(db_session, form_factor="flip")
    db_session.commit()
    R.pick_cost_table_item(db_session, recipe.id, item.id)

    # 다음 파일에서 그 품목이 사라졌다.
    out = _reimport(db_session, _cost_rows(item_name="완전히 다른 품목"))

    db_session.expire_all()
    after = R.get_recipe(db_session, recipe.id)
    assert after.anomaly_flag == R.PIN_LOST
    assert out["pins"]["lost"] == 1
    # ★구성은 **지우지 않는다** — 사람이 붙여 둔 것을 파일 한 번 올렸다고 없애지 않는다.
    assert len(after.lines) > 0
    reason = next(r for r in out["report"] if r["action"] == "skipped_pinned")["reason"]
    assert "없다" in reason


def test_reimport_flags_an_ambiguous_pin_and_does_not_choose(db_session):
    """동명 2건(§9-9① 폴드 중복 정의의 실례)이면 **시스템이 고르지 않는다**."""

    _reimport(db_session, _cost_rows())
    item = db_session.query(CostTableItem).one()
    recipe = _recipe(db_session, form_factor="flip")
    db_session.commit()
    R.pick_cost_table_item(db_session, recipe.id, item.id)

    # 같은 이름이 값만 달라 두 번 실린 파일 — 원가표에 실재하는 모양이다.
    dup = list(_cost_rows())
    dup.append(
        (
            None, "지문방지_내부3매+외부3매", None, 4604.6, 3, 450, 1350, 3, 250, 750,
            30, 80, 22, 60, 8, 10, 98, 6,
        )
    )
    out = _reimport(db_session, dup)

    db_session.expire_all()
    after = R.get_recipe(db_session, recipe.id)
    assert after.anomaly_flag == R.PIN_AMBIGUOUS
    assert after.picked_item_id is None
    assert out["pins"]["ambiguous"] == 1
    assert len(after.lines) > 0


def test_reimport_replaces_the_stored_cost_table_items(db_session):
    """항목 테이블은 «현재 단면»이다 — 지워진 품목이 목록에 남으면 사람이 없는 것을 고른다."""

    _reimport(db_session, _cost_rows())
    assert db_session.query(CostTableItem).count() == 1

    _reimport(db_session, _cost_rows(item_name="새 품목"))
    items = db_session.query(CostTableItem).all()
    assert [i.item_name for i in items] == ["새 품목"]
    # ★고아 라인이 남지 않는다 — 옛 항목의 줄이 살아남으면 픽이 «지워진 품목»의 구성을 붙인다.
    live_ids = {i.id for i in items}
    orphan_ids = {
        l.item_id
        for l in db_session.query(CostTableItemLine).all()
        if l.item_id not in live_ids
    }
    assert orphan_ids == set()
