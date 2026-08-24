# test_cost_menu_price_propagation.py — 단가 변경의 전파 (D-CPP-55 / 계약 A′ S5 · 합격 14~16)
#
# ★**이 파일의 존재 이유**: 계약 §2 판단기준 5가 *"표준원가의 갱신 주체는 항상 원장이다 —
#   사람이 아니다 … 로트 확정 이벤트가 단가 이력을 만들고 표준원가가 재계산된다"*라고 이미
#   정해 뒀는데, **합격기준 1~13 어디에도 그것을 재는 자리가 없어서** 구현이 그 판단기준을
#   어긴 채로 적대 리뷰·완료 QA를 통과했다(§0-D). 이 파일이 그 «자리»다.
#
# ★**전부 HTTP + 표준원가 보드를 통과한다.** 순수 함수를 아무리 촘촘히 잠가도
#   「그 출력이 픽셀이 되는 배선」은 안 잠긴다 — S4 1R에서 SURVIVED 8종이 전부 그 모양이었다.
#   그래서 여기서는 `recompute_for_material`을 직접 부르지 않고 **사람이 화면에서 하는 일**
#   (단가 추가·삭제·종 승인해제·엑셀 채택)을 API로 하고, **보드가 무엇을 말하는지**만 본다.
#   ⇒ 서비스층에서 `_propagate(...)` 호출 한 줄을 지우면 이 파일이 깨진다.
#
# ★픽스처는 **prod의 실제 모양**을 축소 재현한다(2026-08-24 실측): bar 필름 레시피 8개가
#   부자재 **8종**을 공유하고 필름 종만 제품별로 다르다. 여기서는 레시피 2개 × 공유 8종.
#   공유가 없으면 「모든 원가에 같이 적용」을 원리적으로 못 잰다.
from __future__ import annotations

from datetime import date
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

# 상품명 둘 — 같은 bar 폼팩터, 같은 부자재 8종, 다른 필름·다른 매수
NAME_A = "오하이 빛반사, 지문방지 매트 필름 3매"
NAME_B = "오하이 지문방지 PET 저반사 필름 2매"

# 엑셀 정본 산술 (부자재 8종 공용분 = 30+22+60+8+13+98+6+100 = 337)
SHARED = 337
A_EX = 600 * 3 + SHARED          # 2137  → inc 2350.70
B_EX = 600 * 2 + SHARED          # 1537  → inc 1690.70
SHARED_NAMES = (
    "부착 안내문", "부자재 (밀대외)", "알콜솜 2EA", "비닐 (9*18)",
    "비닐 (12*22+4)", "패키지", "폼텍 스티커", "부착 지그",
)


def _cost_sheet() -> list[tuple]:
    blank = (None,) * 18
    return [
        (None, "*원가표_25년") + (None,) * 16,
        blank,
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
    ]


def _mapping_sheet() -> list[tuple]:
    header = (
        "상품명", "옵션명", "채널명", "카페24 품목코드1", "카페24 품목코드2",
        "채널명", "스스 품목코드1", "채널명", "쿠팡 옵션ID 1", "쿠팡 옵션ID 2",
    )

    def row(name, option, cafe):
        return (name, option, "자사몰 (cafe24)", cafe, None,
                "네이버 스마트스토어", None, "COUPANG", None, None)

    return [
        header,
        row(NAME_A, "아이폰16프로", "CAFE-A1"),
        row(NAME_A, "아이폰15", "CAFE-A2"),
        row(NAME_B, "아이폰16프로", "CAFE-B1"),
        row(NAME_B, "갤럭시S24", "CAFE-B2"),
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
    # ★prod 세션과 같은 설정(autoflush=False) — 다르면 결함을 못 잡는다.
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
        s.add(Channel(id=1, name="자사몰", code="CAFE24", platform="cafe24"))
        s.flush()
        seed = [
            ("OHI-A1", f"{NAME_A}, 아이폰16프로", D("2350.7"), "CAFE-A1"),
            ("OHI-A2", f"{NAME_A}, 아이폰15", D("2350.7"), "CAFE-A2"),
            ("OHI-B1", f"{NAME_B}, 아이폰16프로", D("1690.7"), "CAFE-B1"),
            ("OHI-B2", f"{NAME_B}, 갤럭시S24", D("1690.7"), "CAFE-B2"),
        ]
        for sku, name, cost, cafe in seed:
            pm = ProductMaster(internal_sku=sku, product_name=name, cost_price=cost)
            s.add(pm)
            s.flush()
            s.add(ProductChannelMapping(
                product_id=pm.id, channel_id=1, channel_product_id=cafe,
                selling_price=D("10000"), is_active=True))
        s.commit()
    yield tc
    app.dependency_overrides.clear()


# ──────────────────────────────────────────────
# 헬퍼 — 화면이 하는 일만 한다
# ──────────────────────────────────────────────
def _import(client) -> dict:
    xl = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    r = client.post(
        "/api/cost/recipes/import",
        files={
            "cost_file": ("cost.xlsx", _xlsx(_cost_sheet(), "제품 원가표"), xl),
            "mapping_file": ("map.xlsx", _xlsx(_mapping_sheet(), "원가 매핑"), xl),
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _recipe(client, product_name: str) -> dict:
    items = client.get("/api/cost/recipes").json()["items"]
    hits = [i for i in items if i["product_name"] == product_name and i["form_factor"] == "bar"]
    assert len(hits) == 1, [(i["product_name"], i["form_factor"]) for i in items]
    return hits[0]


def _material(client, name_starts: str) -> dict:
    mats = client.get("/api/cost/materials").json()["items"]
    hits = [m for m in mats if m["name"].startswith(name_starts)]
    assert len(hits) == 1, [m["name"] for m in mats]
    return hits[0]


def _board_std(client, recipe_id: int) -> set:
    """이 레시피에 링크된 SKU들의 **보드 표시값**(저장 행). None이면 「—」로 뜬다."""

    board = client.get("/api/cost/board").json()
    rows = [r for r in board["items"] if r["recipe_id"] == recipe_id]
    assert rows, f"보드에 recipe_id={recipe_id} 행이 없다 — 링크가 안 걸렸다"
    return {r["std_cost_inc_vat"] for r in rows}


def _board_rows(client, recipe_id: int) -> list[dict]:
    board = client.get("/api/cost/board").json()
    return [r for r in board["items"] if r["recipe_id"] == recipe_id]


def _ready(client) -> tuple[int, int]:
    """A·B 둘 다 승인 + 단가 채택까지 — 「이미 계산되고 있는 상태」를 만든다."""

    _import(client)
    ra, rb = _recipe(client, NAME_A)["id"], _recipe(client, NAME_B)["id"]
    for rid in (ra, rb):
        assert client.post(f"/api/cost/recipes/{rid}/approve").status_code == 200
        assert client.post(f"/api/cost/recipes/{rid}/adopt-excel-prices").status_code == 200
    assert _board_std(client, ra) == {"2350.70"}
    assert _board_std(client, rb) == {"1690.70"}
    return ra, rb


# ──────────────────────────────────────────────
# 합격 14 — 한 곳에서 바꾼 단가가 «모든» 원가에 닿는다
# ──────────────────────────────────────────────
def test_shared_material_price_change_reaches_every_approved_recipe(client):
    """★계약 §7 합격 14 — Jino 원문: *"같은 부자재를 어디서 바꾸던지 그 부자재의 가격은 모두
    동일한거니까 그때부터는 모든 원가에 같이 적용되어야지"*.

    개정 전에는 **레시피 상세만** 새 값으로 바뀌고 보드는 옛 값이 남았다 — 같은 값이 한
    화면에선 보이고 다른 화면에선 안 보이는 상태(§0-D).
    """

    ra, rb = _ready(client)
    pkg = _material(client, "패키지")

    # 화면에서 「패키지」 단가를 98 → 198로 올린다(수동 단가 행 추가 = 경로 3).
    r = client.post(f"/api/cost/materials/{pkg['id']}/prices",
                    json={"unit_price_ex_vat": "198", "unit_price_inc_vat": "217.80",
                          "effective_date": date.today().isoformat()})
    assert r.status_code == 201, r.text

    # ⇒ **두 레시피 모두** 보드에서 +100(ex) 만큼 오른다. 한쪽만 오르면 그게 이 계약이 고친 병이다.
    assert _board_std(client, ra) == {f"{(A_EX + 100) * 1.1:.2f}"}   # 2460.70
    assert _board_std(client, rb) == {f"{(B_EX + 100) * 1.1:.2f}"}   # 1800.70

    # 같은 레시피의 SKU들은 여전히 «같은 값»이다(전파가 SKU 단위로 갈라지지 않는다).
    assert len(_board_rows(client, ra)) == 2
    assert len(_board_rows(client, rb)) == 2


def test_only_the_shared_material_moves_both_recipes(client):
    """공유가 아닌 종(제품별 필름)을 바꾸면 **그 레시피만** 움직인다 — 전파가 번지기만 하면
    그것도 결함이다(「모두 같이」는 «같은 종을 쓰는 것끼리»라는 뜻이다)."""

    ra, rb = _ready(client)
    film_a = _material(client, "지문방지필름 TPU 3매")

    r = client.post(f"/api/cost/materials/{film_a['id']}/prices",
                    json={"unit_price_ex_vat": "700",
                          "effective_date": date.today().isoformat()})
    assert r.status_code == 201, r.text

    assert _board_std(client, ra) == {f"{(A_EX + 100 * 3) * 1.1:.2f}"}   # 2680.70
    assert _board_std(client, rb) == {"1690.70"}                          # 불변


# ──────────────────────────────────────────────
# 합격 15 — 내려갈 때도 돈다 (0으로 채우지 않는다)
# ──────────────────────────────────────────────
def test_deleting_the_last_price_returns_board_to_dash_not_zero(client):
    """★단가를 지우면 표준원가도 «없음»으로 돌아간다. **0이 아니다**(계약 §2-7)."""

    ra, rb = _ready(client)
    pkg = _material(client, "패키지")
    detail = client.get(f"/api/cost/materials/{pkg['id']}").json()
    assert len(detail["prices"]) == 1, detail["prices"]
    pid = detail["prices"][0]["id"]

    r = client.delete(f"/api/cost/materials/{pkg['id']}/prices/{pid}")
    assert r.status_code == 200, r.text

    for rid in (ra, rb):
        rows = _board_rows(client, rid)
        assert {x["std_cost_inc_vat"] for x in rows} == {None}
        assert {x["std_cost_ex_vat"] for x in rows} == {None}
        # 빈 칸이 조용하지 않게 — «왜 없는지»가 함께 나간다.
        assert all(x["reason"] for x in rows)
        assert all("패키지" in x["reason"] for x in rows), [x["reason"] for x in rows]


def test_unapproving_a_material_returns_board_to_dash(client):
    """★종 승인을 해제하면(경로 5) 그 종을 쓰는 모든 표준원가가 «없음»으로 돌아간다 —
    미승인 종의 단가는 계산에 쓰지 않는다(계약 §2-2)."""

    ra, rb = _ready(client)
    pkg = _material(client, "패키지")

    r = client.patch(f"/api/cost/materials/{pkg['id']}", json={"status": "unconfirmed"})
    assert r.status_code == 200, r.text

    for rid in (ra, rb):
        assert _board_std(client, rid) == {None}

    # 되돌리면 다시 돌아온다 — 한 방향으로만 도는 전파는 전파가 아니다.
    assert client.patch(f"/api/cost/materials/{pkg['id']}",
                        json={"status": "approved"}).status_code == 200
    assert _board_std(client, ra) == {"2350.70"}
    assert _board_std(client, rb) == {"1690.70"}


def test_rename_does_not_trigger_useless_recompute(client):
    """이름 변경은 산술에 안 닿는다 — 필요 없는 쓰기를 만들지 않는다(계약 §6 S5)."""

    ra, _rb = _ready(client)
    pkg = _material(client, "패키지")
    before = client.get("/api/cost/board").json()
    stamp = {r["internal_sku"]: r["std_cost_inc_vat"] for r in before["items"]}

    assert client.patch(f"/api/cost/materials/{pkg['id']}",
                        json={"name": "패키지 (bar) v2"}).status_code == 200

    after = client.get("/api/cost/board").json()
    assert {r["internal_sku"]: r["std_cost_inc_vat"] for r in after["items"]} == stamp
    assert _board_std(client, ra) == {"2350.70"}


# ──────────────────────────────────────────────
# 경로 6 — 채택도 «자기 레시피»를 넘어 번진다
# ──────────────────────────────────────────────
def test_adopting_one_recipe_completes_another_that_shares_materials(client):
    """★「채택 경로는 이미 닫혀 있었다」가 **절반만 참**이었다 — 채택은 «종»의 단가를 만드는데
    초판은 자기 레시피만 재계산했다(계약 §6 S5 표 6번).

    시나리오: B는 자기 필름 단가를 사람이 직접 넣어 승인까지 마쳤지만 **공유 8종이 비어** 아직
    계산이 안 된다. 그 상태에서 **A를 채택**하면 공유 8종에 단가가 생기고 ⇒ **B가 완성된다.**
    """

    _import(client)
    ra, rb = _recipe(client, NAME_A)["id"], _recipe(client, NAME_B)["id"]
    client.post(f"/api/cost/recipes/{ra}/approve")
    client.post(f"/api/cost/recipes/{rb}/approve")

    film_b = _material(client, "지문방지필름 PET 2매")
    assert client.post(f"/api/cost/materials/{film_b['id']}/prices",
                       json={"unit_price_ex_vat": "600"}).status_code == 201
    assert client.patch(f"/api/cost/materials/{film_b['id']}",
                        json={"status": "approved"}).status_code == 200
    # 공유 8종이 아직 비어 있어 B는 계산 불가다.
    assert _board_std(client, rb) == {None}

    out = client.post(f"/api/cost/recipes/{ra}/adopt-excel-prices").json()
    # A는 자기 필름 + 공유 8종 = 9종을 채택한다.
    assert len(out["adopted"]) == 9
    # ★그 채택이 **B까지** 재계산했다는 자백이 응답에 실린다(조용한 전파는 전파가 아니다).
    assert out["also_recomputed_recipe_ids"] == [rb]

    assert _board_std(client, ra) == {"2350.70"}
    assert _board_std(client, rb) == {"1690.70"}


# ──────────────────────────────────────────────
# 금지선 — 전파가 §3을 뚫지 않는다
# ──────────────────────────────────────────────
def test_propagation_never_computes_an_unapproved_recipe(client):
    """★계약 §3 금지선: 미승인 레시피의 «확정 표준원가»는 어떤 경로로도 저장되지 않는다.
    전파가 그 문을 여는 뒷길이 되면 안 된다."""

    _import(client)
    ra, rb = _recipe(client, NAME_A)["id"], _recipe(client, NAME_B)["id"]
    client.post(f"/api/cost/recipes/{ra}/approve")
    client.post(f"/api/cost/recipes/{ra}/adopt-excel-prices")

    # B는 승인하지 않았다. A 채택으로 공유 8종에 단가가 생겼고, B의 필름도 채워 준다.
    film_b = _material(client, "지문방지필름 PET 2매")
    client.post(f"/api/cost/materials/{film_b['id']}/prices", json={"unit_price_ex_vat": "600"})
    client.patch(f"/api/cost/materials/{film_b['id']}", json={"status": "approved"})

    # ⇒ 「값은 전부 있다」. 그래도 미승인이므로 보드는 빈 칸 + 사유다.
    rows = _board_rows(client, rb)
    assert {x["std_cost_inc_vat"] for x in rows} == {None}
    assert all("미승인" in (x["reason"] or "") for x in rows), [x["reason"] for x in rows]
    assert _board_std(client, ra) == {"2350.70"}


def test_price_change_never_touches_cost_price(client):
    """★계약 §3 금지선 — 전파가 `product_master.cost_price`를 건드리지 않는다."""

    ra, _rb = _ready(client)
    pkg = _material(client, "패키지")

    def _cost_prices() -> dict:
        board = client.get("/api/cost/board").json()
        return {r["internal_sku"]: r["current_cost_price"] for r in board["items"]}

    before = _cost_prices()
    client.post(f"/api/cost/materials/{pkg['id']}/prices",
                json={"unit_price_ex_vat": "198", "effective_date": date.today().isoformat()})
    client.patch(f"/api/cost/materials/{pkg['id']}", json={"status": "unconfirmed"})
    assert _cost_prices() == before
    assert before["OHI-A1"] == "2350.70"
    assert _board_std(client, ra) == {None}  # 표준원가는 움직였다 — 대조값만 안 움직였다


# ──────────────────────────────────────────────
# 회귀 — 한 레시피가 여러 종을 쓰므로 트리거가 같은 레시피에 여러 번 닿는다
# ──────────────────────────────────────────────
def test_repeated_recompute_of_the_same_recipe_does_not_duplicate_rows(client):
    """★`cost_standard`는 (레시피 × price_rule)당 1행이다. 트리거가 종마다 도는데 한 레시피가
    9종을 쓰므로 같은 레시피에 아홉 번 닿는다 — 조회가 방금 만든 행을 못 보면 두 번째 행을
    만들고 유일 제약에 걸린다(구현 중 실제로 터졌다: `autoflush=False` 세션).
    """

    ra, rb = _ready(client)
    # 여러 종을 연달아 바꿔 트리거를 반복 발화시킨다.
    for name in SHARED_NAMES:
        m = _material(client, name)
        r = client.post(f"/api/cost/materials/{m['id']}/prices",
                        json={"unit_price_ex_vat": "1",
                              "effective_date": date.today().isoformat()})
        assert r.status_code == 201, r.text

    # 8종이 전부 1원 ⇒ ex = 필름분 + 8 · inc = ×1.1
    assert _board_std(client, ra) == {f"{(1800 + 8) * 1.1:.2f}"}
    assert _board_std(client, rb) == {f"{(1200 + 8) * 1.1:.2f}"}
    assert len(_board_rows(client, ra)) == 2


def test_price_without_effective_date_never_becomes_latest(client):
    """★**전파가 돌아도 「최신」이 안 되면 사람 눈엔 아무 일도 안 일어난다.**

    `_latest_price`는 `(effective_date, id)` 내림차순으로 고르고 `effective_date=None`은 맨 뒤로
    간다(«모름»을 오늘로 지어내지 않는다 — 계약 §2-7). 그래서 **채택분이 이미 있는 종에**
    날짜 없는 단가를 넣으면 그 값은 이력에만 남고 계산에는 영영 안 쓰인다.

    ⇒ 이 동작 자체는 옳다. 문제는 **화면이 발효일을 안 보내던 것**이었고(구현 중 실측:
    `CostPage.tsx`의 「+ 수동 단가 입력」이 단가·공급처만 보냈다), 그대로 두면 합격 14가
    «API로는 되는데 Jino가 화면에서 하면 안 되는» 상태가 된다 — 이 계약이 고치는 병 그 자체다.
    이 테스트는 **왜 화면이 날짜를 반드시 보내야 하는지**를 잠근다.
    """

    ra, _rb = _ready(client)
    pkg = _material(client, "패키지")

    r = client.post(f"/api/cost/materials/{pkg['id']}/prices",
                    json={"unit_price_ex_vat": "9999"})
    assert r.status_code == 201, r.text

    # 이력엔 2건이 남는다 — 값을 버리지 않는다.
    assert len(client.get(f"/api/cost/materials/{pkg['id']}").json()["prices"]) == 2
    # 그러나 계산은 «날짜 있는» 채택분(98원)을 계속 쓴다.
    assert _board_std(client, ra) == {"2350.70"}
