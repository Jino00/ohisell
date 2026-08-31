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
    # ★값을 «못 박는다». 초판은 `or len(prices) == 2`를 달아 마지막 항이 항상 참이었고,
    #   단가를 통째로 바꿔도 이 줄은 안 잡았다(적대 리뷰 P2-8).
    prices = sorted(g["price"] for g in body["groups"])
    assert prices == ["2400.00", "922.00"]
    # ★돈은 문자열이다 — float면 원가 시스템에서 정밀도가 조용히 샌다(P2-7).
    assert all(isinstance(g["price"], str) for g in body["groups"])
    assert all(
        isinstance(s["file_price"], str) for g in body["groups"] for s in g["skus"]
    )
    # ★확인 전까지 아무 값도 안 써진다 (계약 §4 S1 첫 항목)
    with client.testing_session() as s:
        assert s.scalars(select(CostPurchasedPrice)).all() == []


def test_preview_carries_every_field_the_screen_needs(client):
    """★계약 §4 S1 둘째·셋째 항목의 «응답» 표면(적대 리뷰 M22 SURVIVED 회귀).

    화면 테스트는 픽스처로 렌더하므로 **라우터가 키를 빠뜨려도 초록이다** — 그러면 사람은
    「차이」 열이 통째로 빈 화면을 본다. 응답 계약을 여기서 못 박는다.
    """
    r = upload(client, xlsx([("일미리 케이스, 아이폰15", 922, "카페24")]))
    sku = r.json()["groups"][0]["skus"][0]
    for key in (
        "internal_sku", "product_name", "source_product_name", "file_price",
        "is_placeholder", "current_cost_price", "diff", "recipe_id",
        "recipe_name", "excluded_reason", "approved_price",
    ):
        assert key in sku, f"응답에 「{key}」가 없다 — 화면의 그 칸이 통째로 빈다"
    # SKU별 제 값 · 현재 원가 · 차이가 «나란히» 실린다
    assert sku["file_price"] == "922.00"
    assert sku["current_cost_price"] == "1000.00"
    assert sku["diff"] == "-78.00"


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


# ── 적대 리뷰 2R 회귀 — 표시 한 칸이 업로드 전체를 죽이면 안 된다 ────────────


def test_absurd_price_cell_does_not_500_the_whole_upload(client):
    """★2R가 잡은 자리 — 내가 P2-7을 고치며 «새로 만든» 결함이다.

    `Decimal("1e30").quantize(Decimal("0.01"))`은 컨텍스트 정밀도를 넘어 `InvalidOperation`을
    던진다. 초판(float 직렬화)에서는 「이상한 숫자가 화면에 뜬다」였는데, 2자리 문자열로
    바꾸면서 **이유 없는 500**이 됐다 — 같은 파일의 «정상 행»들까지 함께 죽는다.
    """
    r = upload(client, xlsx([
        ("일미리 케이스, 아이폰15", 922, "카페24"),
        ("일미리 케이스, 아이폰16", 1e30, "카페24"),
    ]))
    assert r.status_code == 200, r.text
    body = r.json()
    # 정상 행은 살아 있다
    assert body["counts"]["target_skus"] >= 1
    # 비정상 값도 «보이긴» 한다 — 조용히 사라지면 사람이 원인을 못 찾는다
    shown = [s["file_price"] for g in body["groups"] for s in g["skus"]]
    assert any(v is not None and "1" in v for v in shown)


def test_out_of_range_price_is_refused_so_it_cannot_poison_later_previews(client):
    """★독을 원장에 들이지 않는다.

    초판은 `confirm`이 `1e30`을 **200으로 받아** 저장했고, 그 값이 `approved_price` 경로로
    되살아나 **이후 모든 `/preview`를 영구히 죽였다**(보드는 `_money`를 안 지나 200이라
    「보드는 멀쩡한데 업로드만 죽는」 모양이 된다). 컬럼이 `Numeric(14,2)`니 담을 수 없는
    값은 애초에 확정하지 않는다.
    """
    r = client.post("/api/cost/purchased-prices/confirm", json={
        "internal_skus": ["C1"], "price": "1e30", "source_file": "f",
    })
    assert r.status_code == 200
    assert r.json()["written"] == 0
    assert "저장 한도" in r.json()["skipped"][0]["reason"]

    with client.testing_session() as s:
        assert s.scalars(select(CostPurchasedPrice)).all() == []

    # 그리고 그 뒤 정상 업로드가 여전히 산다
    assert upload(client, xlsx([("일미리 케이스, 아이폰15", 922, "카페24")])).status_code == 200


def test_note_travels_from_the_screen_through_http_into_the_ledger(client):
    """★이음매 자체를 지킨다 — 적대 리뷰 P2-1.

    이 PR이 닫은 결함은 「화면 → HTTP → 원장」 이음매가 «안 이어져 있었다»는 것이었다.
    그런데 이어 놓고도 그 이음매를 지키는 테스트가 없어서, 라우터가 `note`를 버리거나
    (`note=None`) wire 필드명을 바꿔도 백엔드 37개·프론트 1,234개가 전건 초록이었다.
    값을 만드는 층과 사람이 읽는 층 «사이»도 따로 지켜야 한다.
    """
    sent = "원가 메뉴 「매입품 단가」 화면에서 묶음 확인 클릭"
    r = client.post("/api/cost/purchased-prices/confirm", json={
        "internal_skus": ["C1"], "price": "922", "source_file": "08-07판",
        "source_names": {"C1": "일미리 케이스, 아이폰15"},
        "note": sent,
    })
    assert r.status_code == 200 and r.json()["written"] == 1

    with client.testing_session() as s:
        row = s.scalars(select(CostPurchasedPrice)).one()
        assert row.note == sent, "화면이 보낸 문구가 원장까지 그대로 닿아야 한다"
        assert row.source_product_name == "일미리 케이스, 아이폰15"
        assert row.source_file == "08-07판"


def test_ledger_note_is_null_when_the_caller_says_nothing(client):
    """호출자가 말 안 하면 원장도 말하지 않는다 — 백엔드가 대신 지어내지 않는다."""
    r = client.post("/api/cost/purchased-prices/confirm", json={
        "internal_skus": ["C1"], "price": "922", "source_file": "f",
    })
    assert r.status_code == 200 and r.json()["written"] == 1
    with client.testing_session() as s:
        assert s.scalars(select(CostPurchasedPrice)).one().note is None
