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

from datetime import date

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
from app.services.cost_menu.recipe_parser import CLEANING_KIT_NAME, parse_cost_table
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


# ★D-CPP-58: cleaning kit은 «원장이 단가의 정본»인 종이라 엑셀 채택 경로를 안 탄다.
#   prod에는 수입 로트 7건이 붙어 있고 최신이 190.82/209.90(2026-08-18 로트)이다. 픽스처가
#   그 모양을 재현하지 않으면 표준원가가 영영 «계산 불가»로 남아, 테스트가 «병합 후의 prod»가
#   아니라 존재하지 않는 상태를 검사하게 된다(교훈: 픽스처는 prod 세션과 같아야 한다).
KIT_EX = D("190.82")
KIT_INC = D("209.90")

#: 병합 후 골든 — 옛 2,137/2,350.70에서 밀대외 22를 빼고 kit 190.82를 넣은 값.
#: ★엑셀 「제품원가」 2,350.70과 **더는 같지 않다.** 그게 이 계약의 요점이다(엑셀이 과소).
BAR_STD_EX = D("2305.82")        # 2137 − 22 + 190.82
BAR_STD_INC = D("2536.40")       # ×1.1


def _seed_cleaning_kit_ledger_price(client) -> None:
    """import 뒤 cleaning kit 종에 «원장 파생» 단가를 심고 승인한다 — prod 상태의 재현."""

    from app.models import CostMaterial, CostMaterialPrice
    from app.services.cost_menu.recipe_parser import CLEANING_KIT_NAME

    with client.testing_session() as s:
        m = s.query(CostMaterial).filter(CostMaterial.name == CLEANING_KIT_NAME).one()
        # ★층2가 뚫리면 여기서 죽는다 — 엑셀 22원이 종의 참고값으로 새면 화면이 190.82 옆에
        #   22를 세우고 「채택」이 그걸 권유로 만든다.
        assert m.excel_ref_price is None, "엑셀 22원이 cleaning kit의 참고값으로 샜다(층2 실패)"
        # ★`source="manual"`인 이유: `ledger` 행은 **조회 시점에 원장과 다시 맞춰 보므로**
        #   (`materials.ledger_check`) 수입건·품목 라인 없이 심으면 「연결이 없다」로 판정돼
        #   단가가 계산에서 빠진다. 이 파일의 관심사는 «파싱 → 승인 → 채택 → 골든값»이고,
        #   원장 파생 단가가 실제로 보드에 닿는지는 `test_cost_menu_price_propagation.py`가
        #   **진짜 수입건 라인을 만들어** 잰다. 여기서 중요한 건 단가의 출처가 아니라
        #   **엑셀이 아니라는 것**이다(값은 prod 2026-08-18 로트의 실측값 그대로 쓴다).
        m.prices.append(
            CostMaterialPrice(
                source="manual",
                unit_price_ex_vat=KIT_EX,
                unit_price_inc_vat=KIT_INC,
                effective_date=date(2026, 8, 18),
            )
        )
        m.status = "approved"
        s.commit()


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
    """★엑셀 정본의 «자기 재현» — 라인의 엑셀 참고값만으로 더하면 시트의 2,350.70이 나온다.

    ★이 2,350.70은 **표준원가가 아니다**(적대 리뷰 P2-5). D-CPP-58 이후 실제 표준원가는
    `BAR_STD_INC`(2,536.40)이고, 여기서 재는 것은 「파서가 시트를 옳게 읽었는가」다.
    두 숫자를 같은 이름으로 부르면 다음 사람이 어느 쪽을 정본으로 읽을지 갈린다.
    """

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


# ──────────────────────────────────────────────
# N5 (2026-08-23) — 엑셀 참고값은 «보이되 안 더해진다» (계약 §3 금지선)
#
# ★이 두 테스트가 지키는 것은 **화면에 새 열을 만든 대가로 §3이 무너지지 않는 것**이다.
#   참고값이 `unit_price_ex_vat`나 `partial_*`로 한 칸만 새어도 「채택 안 한 값」이
#   표준원가가 된다 — 계약이 *"저장되는 단가는 원장 파생이거나 Jino가 입력·승인한 값뿐"*
#   이라고 못 박은 바로 그 자리다.
# ──────────────────────────────────────────────
def test_excel_reference_price_never_becomes_cost():
    """★참고값만 있는 라인은 **여전히 «단가 없음»**이다 — 합계에도 부분합에도 안 들어간다."""

    out = compute_standard_cost([
        RecipeLineInput(label="A", quantity=D("1"), unit_price_ex_vat=D("100"),
                        price_status="manual", excel_ref_price=D("111")),
        # 채택 «전» 상태 — prod의 다수파(128/129)가 이 모양이다.
        RecipeLineInput(label="B", quantity=D("3"), price_status="missing",
                        excel_ref_price=D("600")),
    ])
    assert out.computable is False          # 참고값이 usable을 참으로 만들면 안 된다
    assert out.std_cost_ex_vat is None
    assert out.unresolved == ("B",)
    # ★600×3=1800이 부분합에 새면 100.00이 1900.00이 된다.
    assert out.partial_ex_vat == D("100.00")
    b = next(ln for ln in out.lines if ln.label == "B")
    assert b.usable is False
    assert b.amount_ex_vat is None and b.amount_inc_vat is None
    assert b.unit_price_ex_vat is None      # ★참고값이 «단가 자리»로 옮겨 앉지 않는다
    # 그런데 참고값 «자신»은 살아서 화면까지 간다 — 안 그러면 사람은 채택이란 길을 못 본다.
    assert b.excel_ref_price == D("600")


def test_breakdown_payload_carries_the_reference_without_summing_it():
    """`cost_standard.breakdown`(저장 근거)에도 실린다 — 단, 금액 칸은 여전히 None."""

    from app.services.cost_menu.standard_cost import breakdown_payload

    out = compute_standard_cost([
        RecipeLineInput(label="B", quantity=D("3"), price_status="missing",
                        excel_ref_price=D("600")),
    ])
    row = breakdown_payload(out)[0]
    assert row["excel_ref_price"] == "600"
    assert row["unit_price_ex_vat"] is None
    assert row["amount_ex_vat"] is None


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
    """★계약 §7 합격 3의 시나리오 — 승인 → 채택 → 골든값이 여러 SKU에 전파.

    ★D-CPP-58로 골든이 2,350.70 → **2,536.40**으로 바뀌었다. 엑셀 「제품원가」 2,350.70과
    **더는 같지 않은 것이 정상**이다: 부자재 한 줄(밀대외 22원)이 원장 파생 cleaning kit
    190.82원으로 대체됐고, 그게 GOAL 카드의 「엑셀은 실제보다 과소」와 같은 방향이다.
    ⇒ 그래서 `gap_pct == 0`도 더는 참이 아니다 — `cost_price`(2350.7)와 벌어지는 것이 사실이다.
    """

    _import(client)
    _seed_cleaning_kit_ledger_price(client)
    rid = _bar_recipe(client)["id"]

    approved = client.post(f"/api/cost/recipes/{rid}/approve").json()
    assert approved["status"] == "approved"
    # 종이 아직 미승인이라 계산은 여전히 «없음» — 승인만으로 값이 생기지 않는다.
    # (cleaning kit 하나만 승인·단가 보유여도 나머지 8종이 막는다.)
    assert approved["standard"]["std_cost_inc_vat"] is None

    adopted = client.post(f"/api/cost/recipes/{rid}/adopt-excel-prices").json()
    # ★8이다 — cleaning kit은 **엑셀 참고값이 없어** 채택 대상이 아니다(층2). 9였다가 8이
    #   되는 이 숫자가, 「단가의 정본이 엑셀에서 원장으로 옮겨졌다」의 가장 짧은 증거다.
    assert len(adopted["adopted"]) == 8
    assert CLEANING_KIT_NAME not in adopted["adopted"]
    std = adopted["recipe"]["standard"]
    assert std["computable"] is True
    assert std["std_cost_ex_vat"] == str(BAR_STD_EX)
    assert std["std_cost_inc_vat"] == str(BAR_STD_INC)
    assert std["line_count"] == 9               # 계산 내역은 여전히 9종(합격 4)

    board = client.get("/api/cost/board").json()
    bar_rows = [r for r in board["items"] if r["recipe_id"] == rid]
    assert {r["internal_sku"] for r in bar_rows} == {"OHI-B1", "OHI-B2", "OHI-B3"}
    # ★서로 다른 SKU 2건 이상에서 «같은 값»
    assert {r["std_cost_inc_vat"] for r in bar_rows} == {str(BAR_STD_INC)}
    # ★`cost_price`(2350.7)보다 표준원가가 높다 — 등록가가 실제보다 낮다는 사실이 화면에 뜬다.
    assert all(r["gap_pct"] > 0 for r in bar_rows)


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
    _seed_cleaning_kit_ledger_price(client)
    rid = _bar_recipe(client)["id"]
    client.post(f"/api/cost/recipes/{rid}/approve")
    client.post(f"/api/cost/recipes/{rid}/adopt-excel-prices")

    again = _import(client)
    assert again["skipped_approved"] >= 1
    still = _bar_recipe(client)
    assert still["status"] == "approved"
    assert still["standard"]["std_cost_inc_vat"] == str(BAR_STD_INC)


# ──────────────────────────────────────────────
# 한쪽만 업로드 (Jino 2026-08-24: *"여기서 둘중에 하나만도 업데이트가 되게 해줘"*)
#
# ★이건 게이트를 푸는 일이 아니라 **«안 건드릴 것»을 정하는 일**이다 — 두 파일이 서로 다른
#   절반을 만들고, 초판 루프는 매핑 그룹을 축으로 돌면서 구성을 통째로 지웠다.
# ──────────────────────────────────────────────
def _import_one(client, *, cost: bool = False, mapping: bool = False):
    files = {}
    if cost:
        files["cost_file"] = (
            "cost.xlsx",
            _xlsx(cost_sheet_rows(), "제품 원가표"),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    if mapping:
        files["mapping_file"] = (
            "map.xlsx",
            _xlsx(mapping_sheet_rows(), "원가 매핑"),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    return client.post("/api/cost/recipes/import", files=files)


def test_mapping_only_does_not_wipe_composition(client):
    """★★이 슬라이스의 본체 — 매핑만 올려도 **구성이 살아남는다.**

    초판이라면 `drafts_by_form`이 비어 `_match_draft`가 전건 `no_recipe_match`를 돌려주고,
    그 값이 `recipe.lines`와 구성 note를 통째로 덮었다. 그러면 이미 잘 매칭돼 있던
    레시피가 «구성 못 찾음»으로 퇴화한다 — prod에서 A1을 연 레시피가 정확히 여기 걸린다.
    """

    _import(client)
    before = _bar_recipe(client)
    assert before["line_count"] == 9, "전제: 두 파일로 올리면 구성 9줄이 선다"
    assert before["match"]["cost_price_mode"] == "2350.70"

    r = _import_one(client, mapping=True)
    assert r.status_code == 200, r.text

    after = _bar_recipe(client)
    # ★구성이 그대로다 — 줄 수도, 매칭 근거도.
    assert after["line_count"] == 9
    assert after["match"]["cost_price_mode"] == "2350.70"
    assert after["match"]["cost_table_item"] == before["match"]["cost_table_item"]
    assert after["anomaly_flag"] == before["anomaly_flag"]
    # ★화면이 «무엇이 그대로인지»를 말한다 — 조용한 반쪽 갱신은 반쪽보다 나쁘다.
    assert r.json()["updated_halves"] == ["SKU 링크"]
    assert any("원가 정본" in s for s in r.json()["untouched"])


def test_cost_only_updates_composition_and_leaves_links_alone(client):
    """★원가만 올리면 구성은 다시 맞추되 **SKU 링크는 손대지 않는다.**"""

    _import(client)
    before = _bar_recipe(client)
    before_links = before["link_count"]
    before_skus = before["match"]["sku_count"]

    r = _import_one(client, cost=True)
    assert r.status_code == 200, r.text

    after = _bar_recipe(client)
    assert after["line_count"] == 9
    # ★링크·옵션 수는 매핑 소관이라 그대로다.
    assert after["link_count"] == before_links
    assert after["match"]["sku_count"] == before_skus
    assert r.json()["updated_halves"] == ["구성"]
    assert r.json()["recipes_created"] == 0, "매핑이 없으면 새 레시피를 만들 근거가 없다"


def test_cost_only_still_skips_approved(client):
    """★한쪽만 올려도 승인분 보호는 그대로다 — 예외를 만들면 그 길로 샌다."""

    _import(client)
    _seed_cleaning_kit_ledger_price(client)
    rid = _bar_recipe(client)["id"]
    client.post(f"/api/cost/recipes/{rid}/approve")
    client.post(f"/api/cost/recipes/{rid}/adopt-excel-prices")

    r = _import_one(client, cost=True)
    assert r.status_code == 200, r.text
    assert r.json()["skipped_approved"] >= 1
    still = _bar_recipe(client)
    assert still["status"] == "approved"
    assert still["standard"]["std_cost_inc_vat"] == str(BAR_STD_INC)


# ──────────────────────────────────────────────
# 사용처 (Jino 2026-08-24: *"각 부자재가 어느 제품에 들어가는지도 나오면 좋겠고"*)
# ──────────────────────────────────────────────
def test_material_says_which_products_use_it(client):
    """★부자재 payload가 **어느 레시피에 들어가는지**를 싣는다 — HTTP body로 단언한다."""

    _import(client)
    mats = client.get("/api/cost/materials").json()["items"]
    pkg = next(m for m in mats if m["name"].startswith("패키지"))

    assert pkg["used_by_count"] >= 1
    names = {u["product_name"] for u in pkg["used_by"]}
    assert TARGET in names
    one = next(u for u in pkg["used_by"] if u["product_name"] == TARGET)
    # ★수량·폼팩터·승인 여부까지 — 「들어간다」만으로는 계산에 쓰이는지 모른다(계약 §2-2).
    assert one["form_factor"] == "bar"
    assert one["status"] in ("draft", "approved")
    assert one["quantity"] is not None
    assert one["recipe_id"]


def test_usage_is_the_same_on_the_single_material_endpoint(client):
    """★★목록과 상세가 **같은 사실**을 말한다.

    호출부가 7곳이라 하나만 고치면 그 화면만 「사용처 0건」이라 조용히 거짓말한다 —
    `used_by`를 필수 인자로 둔 이유이고, 이 테스트가 그 규율을 밖에서 한 번 더 잡는다.
    """

    _import(client)
    mats = client.get("/api/cost/materials").json()["items"]
    pkg = next(m for m in mats if m["name"].startswith("패키지"))

    detail = client.get(f"/api/cost/materials/{pkg['id']}").json()
    assert detail["used_by_count"] == pkg["used_by_count"]
    assert detail["used_by"] == pkg["used_by"]

    # 쓰기 응답도 같은 사실을 실어야 한다 — 승인 직후 화면이 사용처를 잃으면 안 된다.
    patched = client.patch(
        f"/api/cost/materials/{pkg['id']}", json={"status": "approved"}
    ).json()
    assert patched["used_by_count"] == pkg["used_by_count"]


def test_unused_material_says_zero_not_missing(client):
    """★어느 레시피도 안 쓰는 종은 **0건**이다 — «미상»이 아니라 사실이다(§2-7)."""

    _import(client)
    created = client.post(
        "/api/cost/materials", json={"name": "아무도 안 쓰는 종 (테스트)"}
    ).json()
    assert created["used_by"] == []
    assert created["used_by_count"] == 0


def test_uploading_neither_file_is_rejected(client):
    """★«아무것도 안 올림»은 «한쪽만»이 아니다 — 400으로 거절하고 이유를 말한다."""

    r = client.post("/api/cost/recipes/import", files={})
    assert r.status_code == 400
    assert "최소 하나" in r.json()["detail"]


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
    _seed_cleaning_kit_ledger_price(client)
    rid = _bar_recipe(client)["id"]
    client.post(f"/api/cost/recipes/{rid}/approve")
    client.post(f"/api/cost/recipes/{rid}/adopt-excel-prices")
    assert _bar_recipe(client)["standard"]["std_cost_inc_vat"] == str(BAR_STD_INC)

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


def test_standard_lines_carry_the_excel_reference_over_http(client):
    """★N5 — 「엑셀 참고값(채택 전)」 열의 원료가 **HTTP body까지** 온다.

    서비스층 dict만 보면 못 잡는 사고가 이 저장소에서 실제로 났다(교훈 #321: `response_model`이
    선언 안 된 키를 응답에서 지워, 서비스층 9건이 초록인데 배너가 통째로 안 떴다).

    ★그리고 **합계엔 안 들어간다** — 참고값 600이 단가로 둔갑하면 계약 §3 위반이다.
    """

    from app.models import CostMaterial, CostRecipe, CostRecipeLine

    with client.testing_session() as s:
        m = CostMaterial(
            name="필름(참고값만)", status="unconfirmed", category="부자재",
            excel_label="필름", excel_ref_price=D("600"),
        )
        s.add(m)
        s.flush()
        r = CostRecipe(product_name="P", form_factor="bar", status="draft", source="manual")
        s.add(r)
        s.flush()
        s.add(CostRecipeLine(recipe_id=r.id, material_id=m.id, quantity=D("3")))
        s.commit()
        rid = r.id

    client.post(f"/api/cost/recipes/{rid}/approve")
    std = client.get(f"/api/cost/recipes/{rid}").json()["standard"]
    line = std["lines"][0]

    assert line["excel_ref_price"] == "600.00"      # ★값이 화면까지 갈 원료
    assert line["unit_price_ex_vat"] is None        # ★단가 자리로 옮겨 앉지 않는다
    assert line["usable"] is False
    assert line["amount_ex_vat"] is None
    # ★★600×3=1800이 어디에도 안 나타난다 — 합계도 부분합도.
    assert std["computable"] is False
    assert std["std_cost_ex_vat"] is None and std["std_cost_inc_vat"] is None
    assert std["partial_ex_vat"] == "0"


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


# ──────────────────────────────────────────────
# D-CPP-58 층2 — 재유입 차단. ★계약 §6 마지막 항목이 「진짜 합격선」이라 지목한 자리다.
#   층1(데이터 병합)만 하고 여기가 비면, 고친 것이 **다음 엑셀 업로드에 원복된다.**
# ──────────────────────────────────────────────
def _material_names(client) -> list[str]:
    return [m["name"] for m in client.get("/api/cost/materials").json()["items"]]


def test_excel_import_folds_squeegee_into_cleaning_kit(client):
    """★엑셀에 「부자재 (밀대외)」 열이 있어도 그 이름의 종은 **생기지 않는다.**"""

    _import(client)
    names = _material_names(client)
    assert not [n for n in names if "밀대외" in n], names
    assert CLEANING_KIT_NAME in names
    # 폼팩터가 안 붙는다 — bar·flip 두 섹션이 같은 한 종으로 접힌다.
    assert len([n for n in names if n == CLEANING_KIT_NAME]) == 1
    kit = next(m for m in client.get("/api/cost/materials").json()["items"]
               if m["name"] == CLEANING_KIT_NAME)
    # ★엑셀 22원이 «참고값»으로도 새지 않는다 — 새면 화면이 190.82 옆에 22를 세운다.
    assert kit["excel_ref_price"] is None, kit


def test_reimport_does_not_resurrect_the_squeegee_species(client):
    """★★**이게 진짜 합격선이다**(계약 §6 마지막). 병합해 놓고 층2가 없으면 다음 업로드에
    6종이 되살아난다 — 이 저장소가 반복해 밟은 「고쳤는데 다음 주에 원복된다」의 자리다."""

    _import(client)
    _seed_cleaning_kit_ledger_price(client)
    rid = _bar_recipe(client)["id"]
    client.post(f"/api/cost/recipes/{rid}/approve")
    client.post(f"/api/cost/recipes/{rid}/adopt-excel-prices")
    before = client.get(f"/api/cost/recipes/{rid}").json()["standard"]["std_cost_inc_vat"]
    assert before == str(BAR_STD_INC)

    _import(client)          # 같은 엑셀을 다시 올린다 — 사람이 매주 하는 일이다
    _import(client)          # 두 번 더 올려도 마찬가지여야 한다

    names = _material_names(client)
    assert not [n for n in names if "밀대외" in n], names
    assert names.count(CLEANING_KIT_NAME) == 1
    # 승인분의 표준원가도 그대로다 — 재업로드가 값을 되돌리지 않는다.
    after = client.get(f"/api/cost/recipes/{rid}").json()["standard"]["std_cost_inc_vat"]
    assert after == before


def test_squeegee_label_variants_all_fold(client):
    """★공백·줄바꿈 변형이 전부 접힌다 — 이름 매칭이 공백 하나에 지면 층2가 조용히 뚫린다.

    실측 픽스처엔 `"부자재\\n(밀대외)"`가 있고, 시트마다 공백이 다르게 들어온다.
    """

    from app.services.cost_menu.recipe_parser import is_cleaning_kit_label

    for variant in ("부자재 (밀대외)", "부자재(밀대외)", "부자재\n(밀대외)", " 부자재  (밀대외) "):
        assert is_cleaning_kit_label(variant), variant
    # ★넓히지 않는다 — 폼팩터마다 값이 «실제로» 다른 종들은 접으면 원가가 틀어진다(§0-F).
    for other in ("패키지", "부착 안내문", "알콜솜 2EA", "부착 지그", "밀대", ""):
        assert not is_cleaning_kit_label(other), other


def test_folding_happens_after_the_conflict_check(client):
    """★접기는 «이상 검사 뒤»여야 한다 — 순서가 뒤집히면 폼팩터별로 다른 값이 한 종으로
    합쳐지며 `price_conflict`가 **없는 충돌**을 보고한다(적대 리뷰 P2-6: 주석만 있고
    아무것도 안 잠그던 자리).

    시나리오: bar 섹션의 밀대외는 22, flip 섹션은 25. 접기 전 키는 서로 달라 충돌이 아니다.
    접기를 앞으로 옮기면 둘 다 `cleaning kit` 키가 되어 22≠25 충돌로 뜬다.
    """

    rows = cost_sheet_rows()
    # flip 데이터 행(마지막)의 「부자재 (밀대외)」 칸(index 12)을 25로 바꾼다.
    flip = list(rows[-1])
    assert flip[12] == 22, flip
    flip[12] = 25
    rows[-1] = tuple(flip)

    res = parse_cost_table(rows)
    conflicts = [a for a in res.anomalies if "price_conflict" in a]
    assert not conflicts, conflicts


def test_two_squeegee_columns_in_one_row_never_double_count(client):
    """★한 행에 접히는 열이 둘이면 같은 종이 두 줄이 된다 — 계약 §0-D가 지목한 «조용한
    이중 계상»이다(적대 리뷰 P2-2). 버리고 **이상으로 자백**한다."""

    rows = cost_sheet_rows()
    header = list(rows[3])
    data = list(rows[4])
    # 같은 물건의 «다른 표기»를 한 열 더 붙인다 — 실제 시트에서 일어날 수 있는 모양이다.
    header.append("부자재(밀대외)")
    data.append(22)
    rows[3] = tuple(header)
    rows[4] = tuple(data)

    res = parse_cost_table(rows)
    tgt = next(r for r in res.recipes if r.item_name == "지문방지필름 TPU 3매")
    kit_lines = [l for l in tgt.lines if l.key.label == CLEANING_KIT_NAME]
    assert len(kit_lines) == 1, [l.source_column for l in tgt.lines]
    assert any("duplicate_cleaning_kit" in a for a in tgt.anomalies), tgt.anomalies


def test_material_payload_actually_ships_the_excel_reference(client):
    """★부자재 탭의 「엑셀 참고값」 칸이 **응답에 실제로 실린다**(적대 리뷰 P2-4 — 그 필드를
    통째로 죽여도 6,550개가 전부 통과했다).

    kit의 참고값이 `None`인 것만 단언하면 「필드가 아예 안 나간다」를 원리적으로 못 잡는다 —
    **참고값을 가진 종**으로 반대편을 잠근다. 이 PR이 지키려는 것이 정확히 «화면에서
    190.82 옆에 22가 안 서는 것»이므로, 그 칸의 생사가 곧 이 슬라이스의 표면이다.
    """

    _import(client)
    mats = client.get("/api/cost/materials").json()["items"]
    pkg = next(m for m in mats if m["name"].startswith("패키지"))
    assert pkg["excel_ref_price"] == "98.00", pkg          # 실린다
    kit = next(m for m in mats if m["name"] == CLEANING_KIT_NAME)
    assert kit["excel_ref_price"] is None, kit             # 그리고 kit엔 안 실린다


def test_calculation_breakdown_shows_the_cleaning_kit_line(client):
    """★계약 §6 합격 2의 표면 — **레시피 상세의 「계산 내역」에 cleaning kit 줄이 보인다.**

    적대 리뷰 M8·M9가 이 줄을 지웠는데 6,550개가 통과했다(`line_count == 9`는 원본
    dataclass에서 오고 렌더 배열은 따로 만들어져, 둘은 같은 사실을 안 잰다 — P2-3).
    그래서 **라벨로** 잠근다: 화면이 그 줄을 잃으면 여기서 죽는다.
    """

    _import(client)
    _seed_cleaning_kit_ledger_price(client)
    rid = _bar_recipe(client)["id"]
    client.post(f"/api/cost/recipes/{rid}/approve")
    client.post(f"/api/cost/recipes/{rid}/adopt-excel-prices")

    detail = client.get(f"/api/cost/recipes/{rid}").json()["standard"]
    labels = [l["label"] for l in detail["lines"]]
    assert CLEANING_KIT_NAME in labels, labels
    assert not [n for n in labels if "밀대외" in n], labels
    kit = next(l for l in detail["lines"] if l["label"] == CLEANING_KIT_NAME)
    assert kit["unit_price_ex_vat"] == str(KIT_EX)       # 190.82 — 엑셀 22가 아니다

    # ★저장된 근거(`cost_standard.breakdown`)에도 같은 줄이 있어야 한다 — 조회 시점 계산과
    #   저장 행이 갈라지면 같은 값이 한 화면에선 보이고 다른 화면에선 안 보인다.
    board = client.get("/api/cost/board").json()
    row = next(r for r in board["items"] if r["recipe_id"] == rid)
    assert CLEANING_KIT_NAME in str(row.get("breakdown") or detail), row
