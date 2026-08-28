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


def test_absent_confirmation_works_when_the_pin_is_broken(db_session):
    """★적대 리뷰 1R P1-1 — 끊긴 핀에서 화면이 시키는 길이 **실제로 열려 있다**.

    초판은 `picked_item_key`가 남아 있기만 해도 거부했다. 그런데 `_resolve_pins`는 핀이 끊길 때
    키를 **일부러 남긴다**(재업로드 안전망의 근거). 그래서 화면이 「다시 고르거나 「원가표에
    없음」을 확인한다」고 말하는 바로 그 상태에서 확인이 **항상 409**였다 — 이 슬라이스가
    고치려던 병(§0-E-1 ③)을 슬라이스 안에서 재생산한 자리다.
    """

    _reimport(db_session, _cost_rows())
    item = db_session.query(CostTableItem).one()
    recipe = _recipe(db_session, form_factor="flip")
    db_session.commit()
    R.pick_cost_table_item(db_session, recipe.id, item.id)
    db_session.commit()

    _reimport(db_session, _cost_rows(item_name="완전히 다른 품목"))
    db_session.expire_all()
    assert R.get_recipe(db_session, recipe.id).anomaly_flag == R.PIN_LOST

    after = R.confirm_cost_table_absent(db_session, recipe.id, "원가표에서 빠졌다")
    db_session.commit()

    assert after.absent_confirmed_at is not None
    assert after.absent_note == "원가표에서 빠졌다"
    # ★끊긴 핀은 함께 거둔다 — 안 거두면 `_pick_payload`가 `pin_lost`를 계속 내보내
    #   방금 사람이 한 판정이 화면에 안 뜬다(상태가 서로 모순이 된다).
    assert after.picked_item_key is None
    assert after.anomaly_flag != R.PIN_LOST
    assert R._pick_payload(after)["state"] == "absent"
    # 사라진 항목이 만든 구성도 함께 거둔다 — 「없음」이라 적힌 레시피가 그 구성으로
    # 계산되면 서로 모순인 상태다.
    assert len(after.lines) == 0


def test_absent_confirmation_still_refused_while_a_live_pick_stands(db_session):
    """가드를 좁혔다고 «살아 있는 픽»까지 뚫리면 안 된다 — 좁힘의 경계를 잰다."""

    _reimport(db_session, _cost_rows())
    item = db_session.query(CostTableItem).one()
    recipe = _recipe(db_session, form_factor="flip")
    db_session.commit()
    R.pick_cost_table_item(db_session, recipe.id, item.id)
    db_session.commit()

    with pytest.raises(Exception) as exc:
        R.confirm_cost_table_absent(db_session, recipe.id, "억지로")
    assert "픽" in str(exc.value)


def test_recipe_list_payload_carries_pick_state_for_every_row(client, db_session):
    """★적대 리뷰 1R P1-2의 백엔드 절반 — **목록** 응답이 행마다 픽 상태를 싣는다.

    계약 합격 19는 「**목록에** 표시되고」라고 썼다. 화면이 그리려면 목록 응답에 그 필드가
    있어야 하고, 이 단언이 그 계약(HTTP body)을 잠근다 — `recipe_payload`에서 `picked` 키가
    빠지면 화면은 조용히 침묵한다(교훈 #321의 자리).
    """

    looked = _recipe(db_session, product_name="사람이 확인한 것")
    _recipe(db_session, product_name="아무도 안 본 것")
    db_session.commit()
    client.post(
        f"/api/cost/recipes/{looked.id}/confirm-cost-table-absent",
        json={"note": "필름이 아니라 사입 상품이다"},
    )

    rows = client.get("/api/cost/recipes").json()["items"]
    by_name = {r["product_name"]: r for r in rows}
    assert by_name["사람이 확인한 것"]["picked"]["state"] == "absent"
    assert by_name["사람이 확인한 것"]["picked"]["absent_note"] == "필름이 아니라 사입 상품이다"
    assert by_name["아무도 안 본 것"]["picked"]["state"] == "none"


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


# ──────────────────────────────────────────────
# D-CPP-62 S2 — 홈 탭 「할 일 인박스」의 분모 (원가표 항목 전건 인구조사)
# ──────────────────────────────────────────────
def test_cost_table_census_counts_every_item_and_marks_who_picked_it(client, db_session):
    """전건 목록 + 「누가 골랐나」 + 마지막 업로드 시각.

    ★레시피별 목록(`/recipes/{id}/cost-table-items`)으로는 못 얻는 숫자다 — 저건 폼팩터
    버킷만 준다. 첫 화면은 레시피를 하나도 안 고른 상태에서 「손을 기다리는 항목이 몇 건인가」를
    말해야 한다.
    """

    picked = _item(db_session, item_name="고른 항목", form_factor="flip")
    _item(db_session, item_name="안 고른 항목", form_factor="fold", row_number=43)
    _item(
        db_session,
        item_name="이상 있는 항목",
        form_factor=None,
        row_number=44,
        anomalies="price_conflict:패키지:320.0≠171.0",
    )
    recipe = _recipe(db_session)
    db_session.commit()
    client.post(
        f"/api/cost/recipes/{recipe.id}/pick-cost-table-item", json={"item_id": picked.id}
    )

    out = client.get("/api/cost/cost-table-items")
    assert out.status_code == 200
    body = out.json()
    assert body["total"] == 3
    assert body["picked_count"] == 1
    by_name = {r["item_name"]: r for r in body["items"]}
    assert by_name["고른 항목"]["picked"] is True
    assert by_name["고른 항목"]["picked_by_recipe_id"] == recipe.id
    assert by_name["안 고른 항목"]["picked"] is False
    assert by_name["안 고른 항목"]["picked_by_recipe_id"] is None
    # ★`anomalies`는 **원문 그대로** 실린다 — 「같은 사건이 두 줄에 서지 않게」 접는 규칙은
    #   화면의 규칙이고, 백엔드가 미리 접으면 규칙이 두 벌이 된다.
    assert by_name["이상 있는 항목"]["anomalies"] == "price_conflict:패키지:320.0≠171.0"
    # ★폼팩터 없는 항목(수입 완제품·매입품)도 «전건»에 들어간다 — 버킷으로 못 얻는 자리다.
    assert by_name["이상 있는 항목"]["form_factor"] is None
    assert body["last_uploaded_at"] is not None


def test_cost_table_census_is_empty_not_missing_when_nothing_uploaded(client):
    """0건은 «사실»이다 — 「안 올렸다」와 「엔드포인트가 없다」가 같은 화면이 되면 안 된다."""

    body = client.get("/api/cost/cost-table-items").json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["picked_count"] == 0
    assert body["last_uploaded_at"] is None


def test_cost_table_census_is_read_only(client, db_session):
    """★홈 탭 인박스가 매 렌더 이 GET을 부른다 — 그런데 «읽다가 쓰는» 코드는 렌더 한 번마다
    상태가 바뀐다. 이 테스트는 «아무것도 안 쓴다»를 직접 증명한다: 호출 여러 번에도 행 수·픽
    배정·이상 문구·업로드 시각이 전부 그대로여야 하고, 두 번째 호출의 응답도 첫 번째와
    바이트 단위로 같아야 한다(응답이 매 호출 달라지면 그건 어딘가 상태가 움직였다는 뜻이다).
    """

    picked = _item(db_session, item_name="고른 항목", form_factor="flip")
    unpicked = _item(
        db_session,
        item_name="이상 있는 항목",
        form_factor=None,
        row_number=44,
        anomalies="price_conflict:패키지:320.0≠171.0",
    )
    recipe = _recipe(db_session)
    db_session.commit()
    client.post(
        f"/api/cost/recipes/{recipe.id}/pick-cost-table-item", json={"item_id": picked.id}
    )

    before_items = db_session.query(CostTableItem).count()
    before_lines = db_session.query(CostTableItemLine).count()
    before_recipes = db_session.query(CostRecipe).count()
    before_anomalies = unpicked.anomalies
    before_uploaded_at = {it.id: it.uploaded_at for it in db_session.query(CostTableItem).all()}

    first = client.get("/api/cost/cost-table-items")
    second = client.get("/api/cost/cost-table-items")

    assert first.status_code == 200
    # ★같은 상태를 두 번 읽으면 같은 답이 나와야 한다 — 응답이 갈리면 첫 호출이 뭔가를
    #   건드렸다는 뜻이다(읽기 전용 주장을 응답 자체로 재현 가능하게 검증한다).
    assert first.json() == second.json()

    # ★DB를 직접 다시 읽어 «쓰기가 실제로 없었다»를 확인한다 — 응답이 같아도 매번 같은
    #   방식으로 잘못 쓰면(예: picked_item_id를 매번 같은 값으로 재대입) 응답 비교만으론
    #   못 잡는다.
    db_session.expire_all()
    assert db_session.query(CostTableItem).count() == before_items
    assert db_session.query(CostTableItemLine).count() == before_lines
    assert db_session.query(CostRecipe).count() == before_recipes
    refreshed = db_session.get(CostTableItem, unpicked.id)
    assert refreshed.anomalies == before_anomalies
    for it in db_session.query(CostTableItem).all():
        assert it.uploaded_at == before_uploaded_at[it.id]


# ──────────────────────────────────────────────
# 설계 Q6 — 구성 한 줄의 «종»을 사람이 바꾼다
#
# ★이 경로가 없어서 prod 레시피 45·97(SKU 8개)이 각 196.9원 과대인 채로 «고칠 길 없이»
#   남아 있었다. 구성이 바뀌는 길은 업로드 통짜 재생성과 픽뿐이었고 둘 다 「이 한 줄만」을
#   못 한다. 병의 정체는 「단가가 틀렸다」가 아니라 「구성이 엉뚱한 종을 가리킨다」이다
#   (Jino 2026-08-27 22:44 *"같은 부자재가 다른 값이 아니고, 다른 부자재인거지"*).
# ──────────────────────────────────────────────


def _mat(db, name, *, form_factor=None, status="approved") -> CostMaterial:
    m = CostMaterial(
        name=name, status=status, category="부자재", form_factor=form_factor
    )
    db.add(m)
    db.flush()
    return m


def _recipe_with_one_line(db, material, *, product_name="오픽스 Z플립 폴드 4매"):
    # ★`product_name`을 인자로 받는 이유: `cost_recipe`에 (상품명, 폼팩터) unique가 걸려 있어
    #   한 테스트에서 레시피를 둘 만들려면 이름이 갈려야 한다. 그 제약 자체가 이 저장소의
    #   설계다(한 상품·한 폼팩터 = 한 레시피).
    r = _recipe(db, product_name=product_name)
    line = CostRecipeLine(recipe_id=r.id, material_id=material.id, quantity=Decimal("1"))
    db.add(line)
    db.flush()
    return r, line


def _swap(client, recipe_id, line_id, material_id):
    return client.patch(
        f"/api/cost/recipes/{recipe_id}/lines/{line_id}/material",
        json={"material_id": material_id},
    )


def test_swap_points_the_line_at_another_material(client, db_session):
    """★prod 45·97을 고치는 바로 그 조작 — fold 종을 가리키던 줄이 flip 종을 가리키게."""

    fold = _mat(db_session, "패키지 (fold)", form_factor="fold")
    flip = _mat(db_session, "패키지 (flip)", form_factor="flip")
    recipe, line = _recipe_with_one_line(db_session, fold)
    db_session.commit()

    res = _swap(client, recipe.id, line.id, flip.id)
    assert res.status_code == 200, res.text

    db_session.expire_all()
    assert db_session.get(CostRecipeLine, line.id).material_id == flip.id


def test_swap_leaves_an_audit_stamp_saying_what_moved_where(client, db_session):
    """★근거 보존 — 없으면 나중에 「이 폴드 레시피는 왜 flip 종을 쓰지?」에 못 답한다."""

    fold = _mat(db_session, "패키지 (fold)", form_factor="fold")
    flip = _mat(db_session, "패키지 (flip)", form_factor="flip")
    recipe, line = _recipe_with_one_line(db_session, fold)
    db_session.commit()

    _swap(client, recipe.id, line.id, flip.id)

    db_session.expire_all()
    note = db_session.get(CostRecipeLine, line.id).note or ""
    assert "종 교체" in note
    assert "패키지 (fold)" in note, "어디서 왔는지"
    assert "패키지 (flip)" in note, "어디로 갔는지"
    assert "KST" in note, "prod가 UTC라 라벨 없는 시각은 읽는 사람을 속인다"


def test_swap_on_approved_recipe_is_refused(client, db_session):
    """★승인은 「이 원가가 맞다」는 확정이다 — 구성이 바뀌면 그 숫자가 승인 없이 달라진다."""

    fold = _mat(db_session, "패키지 (fold)", form_factor="fold")
    flip = _mat(db_session, "패키지 (flip)", form_factor="flip")
    recipe, line = _recipe_with_one_line(db_session, fold)
    recipe.status = "approved"
    db_session.commit()

    res = _swap(client, recipe.id, line.id, flip.id)
    assert res.status_code == 409, res.text

    db_session.expire_all()
    assert db_session.get(CostRecipeLine, line.id).material_id == fold.id, "안 바뀐다"


def test_swap_refuses_a_line_that_belongs_to_another_recipe(client, db_session):
    """★남의 레시피 줄을 이 경로로 바꾸는 길을 열지 않는다."""

    fold = _mat(db_session, "패키지 (fold)", form_factor="fold")
    flip = _mat(db_session, "패키지 (flip)", form_factor="flip")
    mine, _mine_line = _recipe_with_one_line(db_session, fold)
    other, other_line = _recipe_with_one_line(
        db_session, fold, product_name="오하이 갤럭시Z 힌지 보호 필름 2매"
    )
    db_session.commit()
    assert other.id != mine.id

    res = _swap(client, mine.id, other_line.id, flip.id)
    assert res.status_code == 400, res.text

    db_session.expire_all()
    assert db_session.get(CostRecipeLine, other_line.id).material_id == fold.id


def test_swap_refuses_a_ledger_priced_line(client, db_session):
    """★원장에서 단가가 오는 줄은 종을 안 가리킨다 — 다른 축이다(계약 D-CPP-61)."""

    flip = _mat(db_session, "패키지 (flip)", form_factor="flip")
    recipe = _recipe(db_session)
    line = CostRecipeLine(
        recipe_id=recipe.id,
        material_id=None,
        ledger_item_name="2.5D Clear Glass 2ea",
        quantity=Decimal("1"),
    )
    db_session.add(line)
    db_session.commit()

    res = _swap(client, recipe.id, line.id, flip.id)
    assert res.status_code == 409, res.text

    db_session.expire_all()
    kept = db_session.get(CostRecipeLine, line.id)
    assert kept.material_id is None
    assert kept.ledger_item_name == "2.5D Clear Glass 2ea"


def test_swap_refuses_an_unknown_material(client, db_session):
    fold = _mat(db_session, "패키지 (fold)", form_factor="fold")
    recipe, line = _recipe_with_one_line(db_session, fold)
    db_session.commit()

    res = _swap(client, recipe.id, line.id, 999999)
    assert res.status_code == 400, res.text

    db_session.expire_all()
    assert db_session.get(CostRecipeLine, line.id).material_id == fold.id


def test_swapping_to_the_same_material_leaves_no_stamp(client, db_session):
    """★감사 흔적을 오염시키지 않는다 — 같은 종으로 바꾸는 것은 사건이 아니다."""

    fold = _mat(db_session, "패키지 (fold)", form_factor="fold")
    recipe, line = _recipe_with_one_line(db_session, fold)
    db_session.commit()

    res = _swap(client, recipe.id, line.id, fold.id)
    assert res.status_code == 200, res.text

    db_session.expire_all()
    assert db_session.get(CostRecipeLine, line.id).note is None


def test_swap_does_not_invent_a_price(client, db_session):
    """★계약 §3 금지선 — 종을 바꾸면 그 종의 «있는» 단가가 따라올 뿐이다.

    변이 시험: 서비스가 단가 행을 만들면 이 단언이 죽는다.
    """

    fold = _mat(db_session, "패키지 (fold)", form_factor="fold")
    flip = _mat(db_session, "패키지 (flip)", form_factor="flip")
    recipe, line = _recipe_with_one_line(db_session, fold)
    db_session.commit()
    before = db_session.query(CostMaterialPrice).count()

    _swap(client, recipe.id, line.id, flip.id)

    db_session.expire_all()
    assert db_session.query(CostMaterialPrice).count() == before, "단가를 지어내지 않는다"


def test_breakdown_line_carries_its_own_line_id(client, db_session):
    """★화면이 「이 줄의 종을 바꾼다」를 부르려면 줄 id가 **라인을 타고** 와야 한다.

    이게 없으면 화면은 `standard.lines[i]` ↔ `recipe.lines[i]`를 **인덱스로 짝지어야** 하고,
    그 암묵 불변식은 계산기에 라인 필터가 하나만 생겨도 조용히 깨져 **엉뚱한 줄의 종이
    바뀐다.** 변이 시험: `_line_inputs`에서 `line_id=line.id`를 지우면 죽는다.
    """

    fold = _mat(db_session, "패키지 (fold)", form_factor="fold")
    recipe, line = _recipe_with_one_line(db_session, fold)
    db_session.commit()

    body = client.get(f"/api/cost/recipes/{recipe.id}").json()
    rows = body["standard"]["lines"]
    assert len(rows) == 1
    assert rows[0]["line_id"] == line.id, "인덱스가 아니라 id로 닿는다"
    assert rows[0]["material_id"] == fold.id


def test_ledger_line_also_carries_its_line_id(client, db_session):
    """★원장 줄도 id를 싣는다 — 안 실으면 화면이 그 줄만 인덱스로 세게 되고,
    그러면 「대부분 id, 가끔 인덱스」라는 최악의 절충이 된다(그 틈이 곧 어긋남이다)."""

    recipe = _recipe(db_session)
    line = CostRecipeLine(
        recipe_id=recipe.id,
        material_id=None,
        ledger_item_name="2.5D Clear Glass 2ea",
        quantity=Decimal("1"),
    )
    db_session.add(line)
    db_session.commit()

    rows = client.get(f"/api/cost/recipes/{recipe.id}").json()["standard"]["lines"]
    assert rows[0]["line_id"] == line.id
    assert rows[0]["material_id"] is None


def test_swap_moves_the_standard_cost_by_the_price_difference(client, db_session):
    """★★끝까지 간다 — 종을 바꾸면 **표준원가 숫자가 실제로 움직인다.**

    prod 45·97이 각 196.9원 과대인 그 산술을 그대로 재현한다: 320원짜리 fold 패키지를
    171원짜리 flip 패키지로 바꾸면 −149 ex, ×1.1 = **−163.9 inc**.

    ★이 테스트가 없으면 「종은 바뀌었는데 계산이 옛 종을 본다」를 아무도 못 잡는다 —
    이 저장소가 반복해 밟은 「만드는 층 ≠ 닿는 층」이 정확히 그 모양이다.
    """

    fold = _mat(db_session, "패키지 (fold)", form_factor="fold")
    flip = _mat(db_session, "패키지 (flip)", form_factor="flip")
    for m, ex in ((fold, "320"), (flip, "171")):
        m.prices.append(
            CostMaterialPrice(
                source="manual",
                unit_price_ex_vat=Decimal(ex),
                effective_date=date(2026, 8, 27),
            )
        )
    recipe, line = _recipe_with_one_line(db_session, fold)
    db_session.commit()

    before = client.get(f"/api/cost/recipes/{recipe.id}").json()["standard"]
    assert before["computable"] is True
    assert before["std_cost_ex_vat"] == "320.00"

    res = _swap(client, recipe.id, line.id, flip.id)
    assert res.status_code == 200, res.text

    after = res.json()["recipe"]["standard"]
    assert after["std_cost_ex_vat"] == "171.00", "계산이 새 종을 본다"
    assert after["std_cost_inc_vat"] == "188.10", "×1.1 파생도 새 값 기준"
    # 응답만 맞고 DB가 안 바뀌는 경우를 막는다 — 다시 조회해 대조한다.
    fresh = client.get(f"/api/cost/recipes/{recipe.id}").json()["standard"]
    assert fresh["std_cost_ex_vat"] == "171.00"
