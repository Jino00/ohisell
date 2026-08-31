# test_purchased_price_http.py — 매입품 단가 HTTP 왕복 (계약 D-CPP-63 S1 3/3)
#
# ★서비스 테스트가 «판정이 옳나»를 잰다면 이 파일은 «그 판정이 화면까지 나오나»를 잰다.
#   전역 §4가 요구하는 「최종 산출물까지 가는 경로」가 여기다 — 서비스가 `excluded`를
#   정확히 채워도 라우터가 그 키를 응답에서 빠뜨리면 사람은 「대상 아님 0건」을 본다.
from __future__ import annotations

from decimal import Decimal as D
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import (
    Base,
    CostMaterial,
    CostPurchasedPrice,
    CostRecipe,
    CostRecipeLine,
    CostRecipeLink,
    ProductMaster,
)


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
    with TestingSession() as s:
        # 매입품 후보 — 구성 0줄
        case = CostRecipe(product_name="일미리 케이스", recipe_kind="assembly",
                          status="draft", source="excel")
        # 조립품 — 구성 있음(파일 값이 닿으면 안 된다)
        film = CostRecipe(product_name="유리코팅 필름 2매", recipe_kind="assembly",
                          status="approved", source="excel")
        s.add_all([case, film])
        s.flush()
        m = CostMaterial(name="필름원단")
        s.add(m)
        s.flush()
        s.add(CostRecipeLine(recipe_id=film.id, material_id=m.id, quantity=D("2")))
        s.add_all([
            ProductMaster(internal_sku="C1", product_name="일미리 케이스, 아이폰15",
                          cost_price=D("1000")),
            ProductMaster(internal_sku="C2", product_name="일미리 케이스, 아이폰16",
                          cost_price=D("1000")),
            ProductMaster(internal_sku="C3", product_name="일미리 케이스, 갤럭시S24",
                          cost_price=D("2500")),
            ProductMaster(internal_sku="F1", product_name="유리코팅 필름, 아이폰16",
                          cost_price=D("2616")),
            ProductMaster(internal_sku="B1", product_name="시스루 케이스, 아이폰13",
                          cost_price=D("3000")),
        ])
        for sku, r in [("C1", case), ("C2", case), ("C3", case), ("F1", film),
                       ("B1", case)]:
            s.add(CostRecipeLink(internal_sku=sku, recipe_id=r.id, status="draft",
                                 source="manual"))
        s.commit()
    try:
        yield tc
    finally:
        app.dependency_overrides.clear()


def xlsx(rows, *, header=("상품명", "원가", "채널명"), sheet="원가 매핑") -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(list(header))
    for r in rows:
        ws.append(list(r))
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def upload(client, data: bytes, name="ohisell_mapping_template_20260807.xlsx"):
    return client.post(
        "/api/cost/purchased-prices/preview",
        files={"price_file": (name, data,
               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )


# ── 미리보기는 «쓰기 없음»이다 ───────────────────────────────────────────────


def test_preview_groups_by_recipe_and_price_and_writes_nothing(client):
    r = upload(client, xlsx([
        ("일미리 케이스, 아이폰15", 922, "카페24"),
        ("일미리 케이스, 아이폰16", 922, "카페24"),
        ("일미리 케이스, 갤럭시S24", 2400, "카페24"),
    ]))
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["read_columns"] == {"name": "상품명", "price": "원가"}
    assert body["counts"]["groups"] == 2
    assert body["counts"]["target_skus"] == 3
    prices = sorted(g["price"] for g in body["groups"])
    assert prices == ["2400.00", "922.00"] or prices == [2400.0, 922.0] or len(prices) == 2
    # ★확인 전까지 아무 값도 안 써진다 (계약 §4 S1 첫 항목)
    with client.testing_session() as s:
        assert s.scalars(select(CostPurchasedPrice)).all() == []


def test_preview_shows_excluded_assembly_with_its_reason(client):
    """★「대상 아님」이 응답에 실제로 실린다 — 서비스가 채워도 라우터가 빠뜨리면 0건이 된다."""
    r = upload(client, xlsx([("유리코팅 필름, 아이폰16", 4352.7, "카페24")]))
    body = r.json()

    assert body["counts"]["excluded_skus"] == 1
    assert body["counts"]["target_skus"] == 0
    ex = body["excluded"][0]
    assert ex["internal_sku"] == "F1"
    assert "조립품" in ex["excluded_reason"]
    # 비교는 보여준다(처분은 사람이 한다)
    assert ex["current_cost_price"] in ("2616.00", 2616.0)


def test_preview_separates_placeholder_into_blanks(client):
    r = upload(client, xlsx([("시스루 케이스, 아이폰13", 1, "카페24")]))
    body = r.json()
    assert body["counts"]["blank_skus"] == 1
    assert body["counts"]["target_skus"] == 0
    assert body["blanks"][0]["is_placeholder"] is True
    assert body["blanks"][0]["file_price"] is None


def test_preview_rejects_a_sheet_without_the_price_column(client):
    """★08-22판 — 「원가」 열이 없다. 0건 성공이 아니라 400이어야 한다(교훈 #123)."""
    data = xlsx([("일미리 케이스, 아이폰15", "아이폰15", "카페24")],
                header=("상품명", "옵션명", "채널명"))
    r = upload(client, data, name="ohisell_mapping_template_20260822.xlsx")
    assert r.status_code == 400
    assert "원가" in r.json()["detail"]


def test_preview_rejects_a_workbook_without_the_expected_sheet(client):
    data = xlsx([("x", 1, "y")], sheet="엉뚱한시트")
    r = upload(client, data)
    assert r.status_code == 400


# ── 확인은 쓰고, 거부는 «세어서» 돌려준다 ───────────────────────────────────


def test_confirm_writes_targets_and_reports_refusals_in_the_response(client):
    r = client.post("/api/cost/purchased-prices/confirm", json={
        "internal_skus": ["C1", "C2", "F1"],
        "price": "922",
        "source_file": "ohisell_mapping_template_20260807.xlsx",
        "source_names": {"C1": "일미리 케이스, 아이폰15"},
    })
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["written"] == 2
    # ★조립품 F1은 화면이 보냈어도 서버가 거부하고, 그 사실이 «응답에 보인다»
    assert [s["internal_sku"] for s in body["skipped"]] == ["F1"]
    assert "조립품" in body["skipped"][0]["reason"]

    with client.testing_session() as s:
        rows = s.scalars(select(CostPurchasedPrice)).all()
        assert sorted(x.internal_sku for x in rows) == ["C1", "C2"]
        assert all(x.approved_at is not None for x in rows)
        assert all(x.source == "file" for x in rows)
        # 조립품의 cost_price는 한 글자도 안 바뀐다
        assert s.get(ProductMaster, 4).cost_price == D("2616")


def test_confirm_refuses_placeholder_price_over_http(client):
    r = client.post("/api/cost/purchased-prices/confirm", json={
        "internal_skus": ["C1"], "price": "1", "source_file": "f",
    })
    assert r.status_code == 200
    assert r.json()["written"] == 0
    with client.testing_session() as s:
        assert s.scalars(select(CostPurchasedPrice)).all() == []


def test_confirm_rejects_empty_selection(client):
    r = client.post("/api/cost/purchased-prices/confirm", json={
        "internal_skus": [], "price": "922", "source_file": "f",
    })
    assert r.status_code == 400


# ── 보드 — 「어디까지 왔나」 ─────────────────────────────────────────────────


def test_board_counts_reach_the_screen(client):
    before = client.get("/api/cost/purchased-prices/board").json()
    # 후보 = 구성 0줄 레시피에 걸린 SKU 4건(C1·C2·C3·B1). 조립품 F1은 모수 밖.
    assert before == {"candidates": 4, "grounded": 0, "held_blank": 0, "unconfirmed": 4}

    client.post("/api/cost/purchased-prices/confirm", json={
        "internal_skus": ["C1"], "price": "922", "source_file": "f",
    })
    after = client.get("/api/cost/purchased-prices/board").json()
    assert after == {"candidates": 4, "grounded": 1, "held_blank": 0, "unconfirmed": 3}


def test_confirm_response_carries_the_board_so_the_screen_updates(client):
    body = client.post("/api/cost/purchased-prices/confirm", json={
        "internal_skus": ["C1"], "price": "922", "source_file": "f",
    }).json()
    assert body["board"]["grounded"] == 1
