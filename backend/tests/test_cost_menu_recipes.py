# test_cost_menu_recipes.py — 레시피 파싱·승인·표준원가 (D-CPP-53 / 계약 A′ S2)
#
# ★기대값은 **원가 정본 실측**이다(2026-08-23, 『MD_원가 계산_Jino_260822_Claude.xlsx』
#   「제품 원가표」 행 22, 일반 `bar` 섹션):
#       필름 600×3=1800 + 부착안내문 30 + 부자재(밀대외) 22 + 알콜솜 2EA 60
#       + 비닐(9*18) 8 + 비닐(12*22+4) 13 + 패키지 98 + 폼텍 스티커 6 + 부착 지그 100
#     = ex **2,137**  ⇒ ×1.1 = inc **2,350.70**   (부자재 **9종**)
#   엑셀 col3 「제품원가(+VAT)」도 2350.7이다 — 계약 §7 합격 3의 정본 대조값과 같다.
#
# ★링크 전파의 기대값도 실측이다: 표적 상품명 「오하이 빛반사, 지문방지 매트 필름 3매」의
#   `bar` 버킷은 prod에서 108 SKU 전부 `cost_price=2350.7`이었다(2026-08-23 교차 실측).
#   여기서는 그 구조를 축소 재현해 **서로 다른 SKU 2건 이상이 같은 값**을 갖는지 본다.
#
# ★**테스트 통과는 합격이 아니다**(계약 §7 머리말). prod 화면 관측은 이 파일이 대신하지 않는다.
from __future__ import annotations

from decimal import Decimal as D
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Channel, ProductChannelMapping, ProductMaster
from app.services.cost_menu.mapping_parser import parse_mapping_table, propose_form_factor
from app.services.cost_menu.recipe_parser import parse_cost_table
from app.services.cost_menu.standard_cost import (
    RecipeLineInput,
    compute_standard_cost,
)

TARGET = "오하이 빛반사, 지문방지 매트 필름 3매"


# ──────────────────────────────────────────────
# 정본 축소 재현 — 실제 시트의 «모양»을 그대로 따른다
# ──────────────────────────────────────────────
def cost_sheet_rows() -> list[tuple]:
    """행 20~27(일반 `bar`) + 행 29~30(플립)의 구조를 그대로. 열 위치·줄바꿈까지 실물과 같다."""

    blank = (None,) * 18
    return [
        (None, "*원가표_25년") + (None,) * 16,
        blank,
        # 레이아웃 (a): 섹션 제목 행 → 「품목」 헤더 행 → 데이터
        (None, "모바일 필름-아이폰,갤럭시") + (None,) * 16,
        (
            None, "품목", None, "제품원가\n(+VAT)", "매입", "필름", "필름*매입",
            "부착\n안내문", "부자재\n(밀대외)", "알콜솜\n2EA", "비닐\n(9*18)",
            "비닐\n(12*22+4)", "패키지", "폼텍\n스티커", "부착\n지그", None, None, None,
        ),
        (
            None, "지문방지필름 TPU 3매", None, 2350.7000000000003, 3, 600, 1800,
            30, 22, 60, 8, 13, 98, 6, 100, None, None, None,
        ),
        (
            None, "지문방지필름 PET 2매", None, 1690.7, 2, 600, 1200,
            30, 22, 60, 8, 13, 98, 6, 100, None, None, None,
        ),
        blank,
        # 레이아웃 (b): 섹션 제목이 곧 헤더 행
        (
            None, "모바일 필름-플립", None, "제품원가\n(+VAT)", "내부\n매입", "내부\n필름",
            "내부\n필름*매입", "외부\n매입", "외부\n필름", "외부\n필름*매입",
            "부착\n안내문", "스퀴즈\n6.5cm", "부자재\n(밀대외)", "알콜솜\n2EA",
            "비닐\n(9*18)", "비닐\n(12*22+4)", "패키지", "폼텍\n스티커",
        ),
        (
            None, "지문방지_내부3매+외부3매", None, 3480.4, 3, 600, 1800, 3, 350, 1050,
            30, 80, 22, 60, 8, 10, 98, 6,
        ),
    ]


def mapping_sheet_rows() -> list[tuple]:
    """상품명 하나가 bar·flip 옵션을 **함께** 담는 실물 구조(계약 §0-B)."""

    header = (
        "상품명", "옵션명", "채널명", "카페24 품목코드1", "카페24 품목코드2",
        "채널명", "스스 품목코드1", "채널명", "쿠팡 옵션ID 1", "쿠팡 옵션ID 2",
    )
    def row(option, cafe, naver):
        return (TARGET, option, "자사몰 (cafe24)", cafe, None,
                "네이버 스마트스토어", naver, "COUPANG", None, None)
    return [
        header,
        row("아이폰16프로", "CAFE-BAR-1", "NV-BAR-1"),
        row("아이폰15", "CAFE-BAR-2", "NV-BAR-2"),
        row("갤럭시S24", "CAFE-BAR-3", None),
        row("갤럭시Z플립7 (외부액정3매+내부액정3매)", "CAFE-FLIP-1", None),
    ]


def _xlsx(rows: list[tuple], sheet_name: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    for r in rows:
        ws.append(list(r))
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    # ★prod 세션과 같은 설정이어야 한다(교훈: 픽스처가 prod와 다르면 결함을 못 잡는다).
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

    with TestingSession() as s:
        s.add(Channel(id=1, name="자사몰", code="CAFE24", platform="cafe24"))
        s.add(Channel(id=2, name="스마트스토어", code="NAVER", platform="naver"))
        s.flush()
        # bar 3종은 2350.7 · flip 1종은 3480.4 — prod의 «cost_price가 폼팩터를 가른다»를 재현
        seed = [
            ("OHI-B1", f"{TARGET}, 아이폰16프로", D("2350.7"), "CAFE-BAR-1", "NV-BAR-1"),
            ("OHI-B2", f"{TARGET}, 아이폰15", D("2350.7"), "CAFE-BAR-2", "NV-BAR-2"),
            ("OHI-B3", f"{TARGET}, 갤럭시S24", D("2350.7"), "CAFE-BAR-3", None),
            ("OHI-F1", f"{TARGET}, 갤럭시Z플립7", D("3480.4"), "CAFE-FLIP-1", None),
        ]
        for sku, name, cost, cafe, naver in seed:
            pm = ProductMaster(internal_sku=sku, product_name=name, cost_price=cost)
            s.add(pm)
            s.flush()
            s.add(ProductChannelMapping(
                product_id=pm.id, channel_id=1, channel_product_id=cafe,
                selling_price=D("10000"), is_active=True))
            if naver:
                s.add(ProductChannelMapping(
                    product_id=pm.id, channel_id=2, channel_product_id=naver,
                    selling_price=D("10000"), is_active=True))
        s.commit()
    yield tc
    app.dependency_overrides.clear()


def _import(client) -> dict:
    r = client.post(
        "/api/cost/recipes/import",
        files={
            "cost_file": ("cost.xlsx", _xlsx(cost_sheet_rows(), "제품 원가표"),
                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "mapping_file": ("map.xlsx", _xlsx(mapping_sheet_rows(), "원가 매핑"),
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _bar_recipe(client) -> dict:
    items = client.get("/api/cost/recipes").json()["items"]
    hits = [i for i in items if i["form_factor"] == "bar" and i["product_name"] == TARGET]
    assert len(hits) == 1, [i["product_name"] for i in items]
    return hits[0]


# ──────────────────────────────────────────────
# 1. 순수 SA — 산술이 정본과 같은가
# ──────────────────────────────────────────────
def test_parser_reproduces_cost_table_total():
    """행 22의 라인 합 × 1.1 이 엑셀 「제품원가(+VAT)」와 일치한다 — 파서의 자기 검산."""

    res = parse_cost_table(cost_sheet_rows())
    tgt = next(r for r in res.recipes if r.item_name == "지문방지필름 TPU 3매")
    assert tgt.form_factor == "bar"
    assert len(tgt.lines) == 9, [l.key.label for l in tgt.lines]   # 계약 §7 합격 4의 「9종」
    assert tgt.computed_ex_vat == D("2137")
    assert tgt.anomalies == ()


def test_standard_cost_golden_value():
    """정본 대조값 2,350.70 — 계약 §7 합격 3."""

    res = parse_cost_table(cost_sheet_rows())
    tgt = next(r for r in res.recipes if r.item_name == "지문방지필름 TPU 3매")
    out = compute_standard_cost([
        RecipeLineInput(label=l.key.display_name, quantity=l.quantity,
                        unit_price_ex_vat=l.excel_ref_price, price_status="manual")
        for l in tgt.lines
    ])
    assert out.computable is True
    assert out.std_cost_ex_vat == D("2137.00")
    assert out.std_cost_inc_vat == D("2350.70")


def test_unresolved_price_is_none_not_zero():
    """★단가 미확정은 «없음»이지 0이 아니다(계약 §2-7) — 이 구분이 무너지면 0원 원가가 샌다."""

    out = compute_standard_cost([
        RecipeLineInput(label="A", quantity=D("1"), unit_price_ex_vat=D("100"), price_status="manual"),
        RecipeLineInput(label="B", quantity=D("2"), price_status="missing"),
    ])
    assert out.computable is False
    assert out.std_cost_ex_vat is None      # 0이 아니다
    assert out.unresolved == ("B",)
    assert out.partial_ex_vat == D("100.00")   # 부분합은 «부분»이라는 이름으로만 존재한다
    # ★사유는 «무엇이 없는지»를 말한다. 사유별로 나뉘어 나오므로 「미확정 N건」처럼 뭉치지
    #   않는다 — 처분이 사유마다 다르기 때문이다(적대 리뷰 1R P1-1).
    assert "단가 없음" in out.reason and "B" in out.reason


def test_empty_recipe_is_not_zero_cost():
    out = compute_standard_cost([])
    assert out.computable is False and out.std_cost_ex_vat is None


def test_derived_column_is_not_read_as_quantity():
    """⚠️`필름*매입`(유도값)을 수량으로 읽으면 안 된다 — 파서에서 가장 깨지기 쉬운 자리."""

    res = parse_cost_table(cost_sheet_rows())
    tgt = next(r for r in res.recipes if r.item_name == "지문방지필름 TPU 3매")
    film = [l for l in tgt.lines if l.is_film]
    assert len(film) == 1
    assert film[0].quantity == D("3")          # 매입 3이지 1800이 아니다
    assert all("필름*매입" not in l.source_column for l in tgt.lines)


def test_both_sheet_layouts_parse():
    """섹션 제목이 별도 행인 (a)와 헤더 행 자체인 (b) — 둘 다 읽어야 한다."""

    res = parse_cost_table(cost_sheet_rows())
    assert {"모바일 필름-아이폰,갤럭시", "모바일 필름-플립"} <= set(res.sections_seen)
    flip = next(r for r in res.recipes if r.item_name == "지문방지_내부3매+외부3매")
    assert flip.form_factor == "flip"
    parts = {l.key.part for l in flip.lines if l.is_film}
    assert parts == {"내부", "외부"}


@pytest.mark.parametrize("option,expected", [
    ("아이폰16프로", "bar"),
    ("갤럭시Z플립7FE", "flip"),
    ("갤럭시Z폴드SE", "fold"),
    ("갤럭시Z트라이폴드", "trifold"),      # ★「폴드」보다 먼저 걸려야 한다
    ("갤럭시탭S10울트라", "tablet"),
])
def test_form_factor_proposal(option, expected):
    assert propose_form_factor(TARGET, option) == expected


def test_mapping_groups_split_by_form_factor():
    res = parse_mapping_table(mapping_sheet_rows())
    groups = res.groups()
    assert (TARGET, "bar") in groups and (TARGET, "flip") in groups
    assert len(groups[(TARGET, "bar")]) == 3


# ──────────────────────────────────────────────
# 2. HTTP 왕복 — 화면이 실제로 받는 body를 단언한다
# ──────────────────────────────────────────────
def test_import_creates_drafts_only(client):
    out = _import(client)
    assert out["recipes_created"] == 2          # bar · flip
    assert out["groups"] == 2

    recipe = _bar_recipe(client)
    assert recipe["status"] == "draft"
    assert recipe["line_count"] == 9
    assert recipe["match"]["cost_price_mode"] == "2350.70"
    assert recipe["match"]["cost_table_item"] == "지문방지필름 TPU 3매"

    # ★승인 전에는 계산하지 않는다(계약 §2-2) — 빈 칸이고 0이 아니다.
    assert recipe["standard"]["computable"] is False
    assert recipe["standard"]["std_cost_inc_vat"] is None

    # ★단가 행을 만들지 않았다(계약 §3) — 종은 생겼지만 값은 «참고»뿐이다.
    mats = client.get("/api/cost/materials").json()["items"]
    made = [m for m in mats if "(bar)" in m["name"]]
    assert made and all(m["price_count"] == 0 for m in made)
    assert all(m["status"] == "unconfirmed" for m in made)


def test_approve_then_adopt_reaches_golden_value(client):
    """★계약 §7 합격 3의 시나리오 — 승인 → 채택 → 2,350.70 이 여러 SKU에 전파."""

    _import(client)
    rid = _bar_recipe(client)["id"]

    approved = client.post(f"/api/cost/recipes/{rid}/approve").json()
    assert approved["status"] == "approved"
    # 종이 아직 미승인이라 계산은 여전히 «없음» — 승인만으로 값이 생기지 않는다.
    assert approved["standard"]["std_cost_inc_vat"] is None

    adopted = client.post(f"/api/cost/recipes/{rid}/adopt-excel-prices").json()
    assert len(adopted["adopted"]) == 9
    std = adopted["recipe"]["standard"]
    assert std["computable"] is True
    assert std["std_cost_ex_vat"] == "2137.00"
    assert std["std_cost_inc_vat"] == "2350.70"
    assert std["line_count"] == 9               # 계산 내역 9종(합격 4)

    board = client.get("/api/cost/board").json()
    bar_rows = [r for r in board["items"] if r["recipe_id"] == rid]
    assert {r["internal_sku"] for r in bar_rows} == {"OHI-B1", "OHI-B2", "OHI-B3"}
    # ★서로 다른 SKU 2건 이상에서 «같은 값»
    assert {r["std_cost_inc_vat"] for r in bar_rows} == {"2350.70"}
    assert all(r["gap_pct"] == 0.0 for r in bar_rows)


def test_board_shows_uncomputed_rows_with_reason(client):
    """미승인 SKU도 빠짐없이 실리고 «왜 없는지»를 말한다 — 조용히 사라지면 커버리지 착시다."""

    _import(client)
    board = client.get("/api/cost/board").json()
    assert board["sku_count"] == 4
    flip = [r for r in board["items"] if r["internal_sku"] == "OHI-F1"]
    assert len(flip) == 1
    assert flip[0]["std_cost_inc_vat"] is None
    assert flip[0]["reason"]


def test_reimport_does_not_overwrite_approved(client):
    """★재수입이 승인분을 덮지 않는다 — 덮으면 승인의 의미가 사라진다."""

    _import(client)
    rid = _bar_recipe(client)["id"]
    client.post(f"/api/cost/recipes/{rid}/approve")
    client.post(f"/api/cost/recipes/{rid}/adopt-excel-prices")

    again = _import(client)
    assert again["skipped_approved"] >= 1
    still = _bar_recipe(client)
    assert still["status"] == "approved"
    assert still["standard"]["std_cost_inc_vat"] == "2350.70"


def test_adopt_does_not_overwrite_existing_price(client):
    """★원장 파생 단가를 엑셀 값으로 덮지 않는다(계약 §2-1)."""

    _import(client)
    rid = _bar_recipe(client)["id"]
    mats = client.get("/api/cost/materials").json()["items"]
    victim = next(m for m in mats if m["name"].startswith("패키지"))
    client.post(f"/api/cost/materials/{victim['id']}/prices",
                json={"unit_price_ex_vat": "1234", "note": "원장/수동 선점"})

    client.post(f"/api/cost/recipes/{rid}/approve")
    out = client.post(f"/api/cost/recipes/{rid}/adopt-excel-prices").json()
    assert victim["name"] in out["skipped_has_price"]
    after = client.get(f"/api/cost/materials/{victim['id']}").json()
    assert after["latest_price_ex_vat"] == "1234.00"


def test_unapprove_removes_stored_standard(client):
    _import(client)
    rid = _bar_recipe(client)["id"]
    client.post(f"/api/cost/recipes/{rid}/approve")
    client.post(f"/api/cost/recipes/{rid}/adopt-excel-prices")
    assert _bar_recipe(client)["standard"]["std_cost_inc_vat"] == "2350.70"

    client.post(f"/api/cost/recipes/{rid}/unapprove")
    board = client.get("/api/cost/board").json()
    assert all(r["std_cost_inc_vat"] is None for r in board["items"])


def test_import_rejects_wrong_sheet_name(client):
    """★엉뚱한 시트를 조용히 읽고 「0건」으로 끝나지 않는다 — 사용자는 그걸 성공으로 읽는다."""

    r = client.post(
        "/api/cost/recipes/import",
        files={
            "cost_file": ("cost.xlsx", _xlsx(cost_sheet_rows(), "딴시트"), "application/x"),
            "mapping_file": ("map.xlsx", _xlsx(mapping_sheet_rows(), "원가 매핑"), "application/x"),
        },
    )
    assert r.status_code == 400
    assert "제품 원가표" in r.json()["detail"]


def test_cost_price_is_never_written(client):
    """★금지선 — `product_master.cost_price`는 읽기만 한다."""

    before = {}
    with client.testing_session() as s:
        for pm in s.query(ProductMaster).all():
            before[pm.internal_sku] = pm.cost_price

    _import(client)
    rid = _bar_recipe(client)["id"]
    client.post(f"/api/cost/recipes/{rid}/approve")
    client.post(f"/api/cost/recipes/{rid}/adopt-excel-prices")

    with client.testing_session() as s:
        after = {pm.internal_sku: pm.cost_price for pm in s.query(ProductMaster).all()}
    assert after == before


# ──────────────────────────────────────────────
# 3. 종 승인 게이트 — 적대 리뷰 1R P1-1이 연 구멍
# ──────────────────────────────────────────────
# 초판은 이 가드에 테스트가 **0건**이었다(변이 M9 SURVIVED). 그리고 가드가 도는 방식 자체가
# 틀렸다 — 단가가 실재하는데도 값을 버리고 `unconfirmed`로 보고해, 화면이 「단가 미확정」이라
# 말하며 178.78원을 「—」로 감췄다. **사유가 틀리면 사람이 틀린 일을 한다**(이미 있는 단가를
# 입력하러 간다). 아래 3건이 그 두 가지를 한꺼번에 못 박는다.
def _recipe_with_priced_material(client, *, material_status: str):
    """단가가 «실재하는» 종 1개짜리 레시피를 세운다. `adopt` 경로를 안 탄다 — 그 경로만
    테스트하면 종 승인 게이트가 영영 안 밟힌다(그게 초판의 구멍이었다)."""

    from datetime import date

    from app.models import CostMaterial, CostMaterialPrice, CostRecipe, CostRecipeLine

    with client.testing_session() as s:
        m = CostMaterial(name="cleaning kit", status=material_status, category="부자재")
        m.prices.append(
            CostMaterialPrice(
                source="manual",
                unit_price_ex_vat=D("178.78"),
                unit_price_inc_vat=D("196.66"),
                effective_date=date(2026, 7, 23),
            )
        )
        s.add(m)
        s.flush()
        r = CostRecipe(product_name="원장파생 제품", form_factor="bar",
                       status="draft", source="manual")
        s.add(r)
        s.flush()
        s.add(CostRecipeLine(recipe_id=r.id, material_id=m.id, quantity=D("1")))
        s.commit()
        return r.id


def test_unapproved_material_blocks_computation(client):
    """★미승인 종의 단가는 계산에 안 쓴다 — 이 가드가 사라지면 이 테스트가 죽어야 한다(M9)."""

    rid = _recipe_with_priced_material(client, material_status="unconfirmed")
    client.post(f"/api/cost/recipes/{rid}/approve")
    std = client.get(f"/api/cost/recipes/{rid}").json()["standard"]
    assert std["computable"] is False
    assert std["std_cost_inc_vat"] is None


def test_unapproved_material_says_the_right_reason(client):
    """★사유가 「단가 없음」이면 안 된다 — 단가는 **있다**. 사람을 틀린 일로 보내면 안 된다."""

    rid = _recipe_with_priced_material(client, material_status="unconfirmed")
    client.post(f"/api/cost/recipes/{rid}/approve")
    std = client.get(f"/api/cost/recipes/{rid}").json()["standard"]

    line = std["lines"][0]
    assert line["price_status"] == "material_unapproved"
    # ★실재하는 값을 감추지 않는다
    assert line["unit_price_ex_vat"] == "178.78"
    # ★무엇을 해야 하는지까지 말한다
    assert "부자재 종 미승인" in std["reason"]
    assert "승인" in std["reason"]
    assert "단가 없음" not in std["reason"]


def test_approving_the_material_unblocks_computation(client):
    """승인하면 막힘이 풀린다 — 화면이 시킨 일이 실제로 통해야 한다."""

    rid = _recipe_with_priced_material(client, material_status="approved")
    client.post(f"/api/cost/recipes/{rid}/approve")
    std = client.get(f"/api/cost/recipes/{rid}").json()["standard"]
    assert std["computable"] is True
    assert std["std_cost_inc_vat"] == "196.66"


def test_missing_price_is_not_reported_as_unapproved(client):
    """★단가가 아예 없으면 「승인하라」고 시키면 안 된다 — 승인할 값이 없다."""

    from app.models import CostMaterial, CostRecipe, CostRecipeLine

    with client.testing_session() as s:
        m = CostMaterial(name="단가없는종", status="unconfirmed", category="부자재")
        s.add(m)
        s.flush()
        r = CostRecipe(product_name="P", form_factor="bar", status="draft", source="manual")
        s.add(r)
        s.flush()
        s.add(CostRecipeLine(recipe_id=r.id, material_id=m.id, quantity=D("1")))
        s.commit()
        rid = r.id

    client.post(f"/api/cost/recipes/{rid}/approve")
    std = client.get(f"/api/cost/recipes/{rid}").json()["standard"]
    assert std["lines"][0]["price_status"] == "missing"
    assert "단가 없음" in std["reason"]
    assert "부자재 종 미승인" not in std["reason"]


def test_column_roles_are_classified():
    """열 역할 분류를 못 박는다 — 초판은 이 불변식을 주석으로만 주장했다(적대 리뷰 1R M10).

    ★현재는 `qty`와 `derived`가 둘 다 라인 생성에서 건너뛰어져 순서를 바꿔도 «결과»가 같다.
    그래서 결과만 보는 테스트로는 이 분류를 지킬 수 없다 — 분류를 **직접** 단언한다.
    """

    from app.services.cost_menu.recipe_parser import _classify_columns

    header = (None, None, None, None, "내부\n매입", "내부\n필름", "내부\n필름*매입", "패키지")
    roles = {(c.label, c.role, c.part) for c in _classify_columns(header, start=4)}
    assert ("내부 매입", "qty", "내부") in roles
    assert ("내부 필름", "price", "내부") in roles
    assert ("내부 필름*매입", "derived", "내부") in roles      # ★유도값은 수량이 아니다
    assert ("패키지", "flat", None) in roles
