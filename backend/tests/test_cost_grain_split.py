# test_cost_grain_split.py — 계약 D-CPP-67 §4 S1·S2 (그레인 분할)
#
# ## 이 파일이 지키는 것 (합격기준과 1:1)
#
#   S1-① grain에 축이 선다 — 기존 레시피는 전건 `variant=""`이고 표시가 안 바뀐다
#   S1-② 변형 레시피가 계획표대로 서고 `link_count`가 표와 같다
#   S1-③ 새 레시피가 «자기» 원가표 줄을 픽했다
#   S1-④ SKU 귀속 근거가 링크 `note`에 남고 **HTTP payload로 나온다**
#   S1-⑤ 승인 뒤 보류가 스스로 풀린다 — `classify_group`을 **안 고치고**
#   S2-③ 태블릿까지 끝나면 보류 0 · `none_count` 불변
#   S4-② 92건 «밖»은 한 건도 안 움직인다
#   S4-③ **매핑 재업로드가 분할을 되돌리지 않는다**
#   §3   금지선 — 분할은 `cost_price`에 한 원도 안 쓴다
#
# ★**표면까지 간다.** 이 저장소가 여덟 번 밟은 자리가 「값은 맞는데 사람이 못 본다」다.
#   그래서 마지막 표면은 서비스 dict가 아니라 **HTTP body**(`/grain-split/preview`·
#   `/recipes/{id}` 링크 `note`·`/truth-board` census)다.
# ★**상정한 표면 절단 변이**: 라우터가 서비스를 안 부르면 · payload에서 `variant`가 빠지면 ·
#   링크 `note`가 안 실리면 · `import_drafts`의 분할 가드가 빠지면 여기서 죽어야 한다.
from __future__ import annotations

from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from datetime import date

from app.models import (
    CostMaterial,
    CostMaterialPrice,
    CostRecipe,
    CostRecipeLine,
    CostRecipeLink,
    CostStandard,
    CostTableItem,
    CostTableItemLine,
    ProductMaster,
)
from app.services.cost_menu import grain_split as GS

RULE = "latest"
MATTE = "오하이 빛반사, 지문방지 매트 필름 3매"
TABLET = "저반사 지문방지 PET 깨지지 않는 태블릿 액정보호필름 2매"
BLC6H = "강화유리코팅 고투명 6H 블루라이트 차단 액정보호필름 2매"
BLCPAPER = "종이질감 저반사 지문방지 블루라이트 차단 액정보호필름 2매"

# ── 계약 §0-D 표를 «테스트 쪽에서 다시» 적는다 ────────────────────────────
#   ★일부러 `GS.PLAN`을 읽지 않는다. 계획표를 소스에서 가져오면 「계획이 계획과 같다」는
#   동어반복이 되고, 표를 잘못 고쳐도 테스트가 같이 따라 움직여 못 잡는다.
FOLD_ROWS = [("외3+내3", 9, "6089.60"), ("외3", 9, "2666.00"),
             ("외3+내3+후2", 6, "7519.60"), ("외3+내3+후2+힌2", 6, "8017.00")]
FLIP_ROWS = [("외3+내3", 7, "3480.40"), ("외3", 7, "1412.40"),
             ("외3+내3+후2", 6, "4483.00"), ("외3+내3+후2+힌2", 5, "4883.00"),
             ("내3", 1, "2558.00")]
TABLET_ROWS = [("기본", 21, "4902.70"), ("플러스", 11, "5430.70"), ("울트라", 4, "7652.70")]

# ── D-CPP-68 §0-D의 6행 (2026-09-03 저술 · 라이브 실측 ref 125) ──────
#   ★비-BLC 표(TABLET_ROWS)와 «따로» 적는다 — 두 군의 울트라 규칙이 다르고,
#     그 다름이 이 파일이 지켜야 할 것이다.
BLC6H_ROWS = [("기본", 15, "4352.70"), ("플러스", 11, "4352.70"), ("울트라", 5, "5892.70")]
BLCPAPER_ROWS = [("기본", 26, "4352.70"), ("플러스", 12, "4572.70"), ("울트라", 6, "5892.70")]


def _blc_models(rows):
    """등급별 모델명. **기본은 크기 낱말이 하나도 없어야** 잔여로 들어간다(라이브가 그렇다).

    ★울트라 마지막 한 칸은 반드시 「갤럭시탭S10FE플러스」다 — 원가표 BLC 줄이
      `TabS10FE+`를 울트라 칸에 적어 두었고, 그 낱말이 「플러스」를 품고 있어서
      순서가 틀리면 조용히 플러스로 샌다. 이 픽스처가 그 자리를 지킨다.
    """
    out = {}
    for variant, count, _ in rows:
        if variant == "기본":
            out[variant] = [f"갤럭시탭B{i}" for i in range(count)]
        elif variant == "플러스":
            out[variant] = [f"갤럭시탭P{i}플러스" for i in range(count)]
        else:
            out[variant] = [f"갤럭시탭U{i}울트라" for i in range(count - 1)] + ["갤럭시탭S10FE플러스"]
    return out

_SUFFIX = {
    "외3": "(외부액정3매)",
    "내3": "(내부액정3매)",
    "외3+내3": "(외부액정3매+내부액정3매)",
    "외3+내3+후2": "(외부액정3매+내부액정3매+후면2매)",
    "외3+내3+후2+힌2": "(외부액정3매+내부액정3매+후면2매+힌지2매)",
}
_TABLET_MODEL = {
    "기본": ["아이패드프로7세대(M4)11인치", "아이패드에어4/5세대10.9인치", "갤럭시탭S9",
             "갤럭시탭S8", "갤럭시탭S7", "갤럭시탭A9", "갤럭시탭A8", "갤럭시탭A7",
             "갤럭시탭S9FE", "갤럭시탭S7FE", "갤럭시탭S10FE", "갤럭시탭S11",
             "아이패드미니6/7세대8.3인치", "아이패드7/8/9세대10.2인치", "아이패드10세대10.9인치",
             "뮤패드P11", "뮤패드K13OLED", "뮤패드K11LTE", "아이패드프로3/4/5/6세대11인치",
             "아이패드에어6/7세대11인치", "갤럭시탭A7라이트"],
    "플러스": ["아이패드프로7세대(M4)13인치", "아이패드프로3/4/5/6세대12.9인치",
               "아이패드에어6/7세대13인치", "뮤패드K10플러스", "갤럭시탭S9플러스",
               "갤럭시탭S9FE플러스", "갤럭시탭S8플러스", "갤럭시탭S7플러스",
               "갤럭시탭S10플러스", "갤럭시탭S10FE플러스", "갤럭시탭A9플러스"],
    "울트라": ["갤럭시탭S9울트라", "갤럭시탭S8울트라", "갤럭시탭S11울트라", "갤럭시탭S10울트라"],
}

#: 원가표 줄 — (섹션, 항목명, 폼팩터, 상품원가). 계약 §0-D의 「원가표 대응 줄」 열이다.
COST_TABLE = [
    ("모바일 필름-폴드", "지문방지_내부3매+외부3매", "fold", "6186.40"),
    ("모바일 필름-폴드", "지문방지_외부3매", "fold", "2666.40"),
    ("모바일 필름-폴드", "지문방지_내부3매+외부3매+후면2", "fold", "7616.40"),
    ("모바일 필름-폴드", "지문방지_내부3매+외부3매+후면2+힌지2", "fold", "8016.80"),
    ("모바일 필름-플립", "지문방지_내부3매+외부3매", "flip", "3712.50"),
    ("모바일 필름-플립", "지문방지_외부3매", "flip", "1644.50"),
    ("모바일 필름-플립", "지문방지_내부3매+외부3매+후면2", "flip", "4482.50"),
    ("모바일 필름-플립", "지문방지_내부3매+외부3매+후면2+힌지2", "flip", "4882.90"),
    ("모바일 필름-플립", "지문방지_내부3매", "flip", "2557.50"),
    ("태블릿 필름", "지문방지 PET 2매_기본", "tablet", "5054.50"),
    ("태블릿 필름", "지문방지 PET 2매_플러스,12.9형,13인치", "tablet", "5582.50"),
    ("태블릿 필름", "지문방지 PET 2매_울트라", "tablet", "7804.50"),
    # D-CPP-68 — BLC 줄. ★기본과 플러스의 «총액이 같다»(4504.50). 그래도 종이 다르므로
    #   원가표가 두 줄로 두었고, 그 두 줄을 따르는 것이 「엑셀 정합」이다.
    ("태블릿 필름", "6H 강화유리코팅 BLC_기본", "tablet", "4504.50"),
    ("태블릿 필름", "6H 강화유리코팅 BLC_플러스,12.9형,13인치", "tablet", "4504.50"),
    ("태블릿 필름", "6H 강화유리코팅 BLC_울트라, TabS10FE+", "tablet", "6044.50"),
    ("태블릿 필름", "종이질감 PET 2매_기본_BLC", "tablet", "4504.50"),
    ("태블릿 필름", "종이질감 PET 2매_플러스,12.9형,13인치_BLC", "tablet", "4504.50"),
    ("태블릿 필름", "종이질감 PET 2매_울트라_BLC, TabS10FE+", "tablet", "6044.50"),
]


def _sku(prefix: str, n: int) -> str:
    return f"OHI-{prefix}{n:03d}"


def _seed_group(s, *, recipe_id: int, form: str, rows, base_variant: str, start: int,
                product_name: str = None, models: dict = None):
    """base 레시피 1개 + 그 묶음의 SKU 전건. **분할 «전»의 prod 모양 그대로다.**"""
    pname = product_name or (MATTE if form != "tablet" else TABLET)
    mmap = models if models is not None else _TABLET_MODEL
    s.add(CostRecipe(id=recipe_id, product_name=pname,
                     form_factor=form, status="approved", recipe_kind="assembly", variant=""))
    s.add(CostStandard(recipe_id=recipe_id, price_rule=RULE,
                       std_cost_inc_vat=D("6220.30"), std_cost_ex_vat=D("6220.30"),
                       breakdown="[]"))
    # base 레시피에도 구성 줄이 있어야 승인 상태가 자연스럽다
    s.add(CostRecipeLine(recipe_id=recipe_id, material_id=None, quantity=D("1")))

    n = start
    for variant, count, price in rows:
        for i in range(count):
            sku = _sku(form[:2].upper(), n)
            n += 1
            if form == "tablet":
                model = mmap[variant][i]
                name = f"{pname}, {model}"
            else:
                dev = "갤럭시Z폴드" if form == "fold" else "갤럭시Z플립"
                name = f"{MATTE}, {dev}{i + 1} {_SUFFIX[variant]}"
            s.add(ProductMaster(internal_sku=sku, product_name=name, cost_price=D(price)))
            s.add(CostRecipeLink(internal_sku=sku, recipe_id=recipe_id, status="approved"))
    return n


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
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
        # ★부자재 «종»에 단가가 이미 있다 — prod가 그렇다(승인 22개 전건이 계산값을 갖는다,
        #   2026-09-02 실측). 단가가 없는 픽스처를 쓰면 새 레시피가 계산값 없이 서서
        #   「보류가 풀렸나」를 재는 이 파일이 다른 세계를 테스트하게 된다.
        for i, (section, item_name, form, total) in enumerate(COST_TABLE, start=1):
            s.add(CostTableItem(id=i, section=section, item_name=item_name, form_factor=form,
                                recipe_kind="assembly", total_inc_vat=D(total), row_number=i))
            # 항목명은 섹션 사이에서 겹친다(폴드·플립 둘 다 「지문방지_내부3매+외부3매」)
            # — 종 이름은 폼팩터까지 넣어 가른다. prod에서도 폴드·플립 필름은 다른 물건이다.
            # ★구성을 **3줄**로 둔다. 1줄이면 `g3_1_incomplete_single_line`
            #   (「구성이 1줄뿐이라 계산이 불완전하다 — 부자재 보강이 선행이다」)에 걸려
            #   분할과 무관한 이유로 보류가 남는다. prod의 이 항목들은 9~11줄이다.
            for j in range(3):
                mat_name = f"{form}·{item_name} 재료{j}"
                s.add(CostTableItemLine(item_id=i, material_name=mat_name, quantity=D("1")))
                mat = CostMaterial(name=mat_name, status="approved", category="부자재",
                                   excel_label=mat_name)
                mat.prices.append(
                    CostMaterialPrice(source="manual", unit_price_ex_vat=D(total) / 3,
                                      effective_date=date(2026, 8, 27))
                )
                s.add(mat)
        n = _seed_group(s, recipe_id=70, form="fold", rows=FOLD_ROWS, base_variant="외3+내3", start=1)
        n = _seed_group(s, recipe_id=69, form="flip", rows=FLIP_ROWS, base_variant="외3+내3", start=n)
        n = _seed_group(s, recipe_id=98, form="tablet", rows=TABLET_ROWS, base_variant="기본", start=n)
        n = _seed_group(s, recipe_id=11, form="tablet", rows=BLC6H_ROWS, base_variant="기본",
                        start=n, product_name=BLC6H, models=_blc_models(BLC6H_ROWS))
        _seed_group(s, recipe_id=99, form="tablet", rows=BLCPAPER_ROWS, base_variant="기본",
                    start=n, product_name=BLCPAPER, models=_blc_models(BLCPAPER_ROWS))

        # ── 92건 «밖» — S4-② 대조군. 손대면 안 되는 것들 ──────────────────
        s.add(CostRecipe(id=50, product_name="자가복원 고투명 EPU 3매", form_factor="bar",
                         status="approved", recipe_kind="assembly", variant=""))
        s.add(CostStandard(recipe_id=50, price_rule=RULE, std_cost_inc_vat=D("3275.80"),
                           std_cost_ex_vat=D("3275.80"), breakdown="[]"))
        s.add(ProductMaster(internal_sku="OHI-OUT001", product_name="자가복원 고투명 EPU 3매, 아이폰17",
                            cost_price=D("3275.80")))
        s.add(CostRecipeLink(internal_sku="OHI-OUT001", recipe_id=50, status="approved"))
        s.commit()
    yield tc
    app.dependency_overrides.clear()


# ─── 헬퍼 ────────────────────────────────────────────────────────────────
def _preview(client) -> dict:
    r = client.get("/api/cost/grain-split/preview")
    assert r.status_code == 200, r.text
    return r.json()


def _split(client, expect=200) -> dict:
    r = client.post("/api/cost/grain-split", json={"scope": "all", "actor": "test"})
    assert r.status_code == expect, r.text
    return r.json()


def _recipes(client) -> list[dict]:
    r = client.get("/api/cost/recipes")
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _group(prev: dict, form: str, product_name: str = None) -> dict:
    """★`form_factor`만으로 고르면 안 된다 — D-CPP-68 이후 태블릿 군이 **셋**이다.

    이름을 안 주면 그 폼팩터의 군이 둘 이상일 때 **일부러 터진다**. 조용히 첫 칸을
    집으면 「지문방지를 검사한다고 믿으면서 BLC를 검사하는」 테스트가 된다.
    """

    hit = [g for g in prev["groups"] if g["form_factor"] == form
           and (product_name is None or g["product_name"] == product_name)]
    assert hit, f"{form}/{product_name} 묶음이 미리보기에 없다"
    assert len(hit) == 1, f"{form} 묶음이 {len(hit)}개다 — product_name으로 지목할 것"
    return hit[0]


def _cost_prices(client) -> dict:
    with client.testing_session() as s:
        return {p.internal_sku: p.cost_price for p in s.query(ProductMaster).all()}


# ═══════════════════════════════════════════════════════════════════
# S1-① 축이 서고, 안 갈라진 레시피의 표시는 한 글자도 안 바뀐다
# ═══════════════════════════════════════════════════════════════════
def test_variant_axis_exists_and_defaults_to_single_grain(client):
    rows = _recipes(client)
    assert rows, "레시피 목록이 비었다"
    for r in rows:
        assert "variant" in r, "payload에 variant가 없다 — 화면이 변형을 그릴 수 없다"
    assert all(r["variant"] == "" for r in rows), "분할 «전»엔 전건 단일 그레인이어야 한다"


# ═══════════════════════════════════════════════════════════════════
# S1-②③ 미리보기가 계획표 12행을 그대로 재현한다
# ═══════════════════════════════════════════════════════════════════
def test_preview_reproduces_the_approved_plan_table(client):
    prev = _preview(client)
    assert prev["safe_to_execute"] is True, prev["sentence"]
    # ★12행 몫 92 + D-CPP-68 몫 75(r11 31 · r99 44) = 167. 둘을 «따로» 적어 두어야
    #   나중에 한쪽만 틀어졌을 때 어느 쪽인지 보인다.
    assert prev["live_sku_total"] == 92 + 75
    assert prev["plan_sku_total"] == 92 + 75

    for form, rows, pname in (("fold", FOLD_ROWS, MATTE), ("flip", FLIP_ROWS, MATTE),
                              ("tablet", TABLET_ROWS, TABLET),
                              ("tablet", BLC6H_ROWS, BLC6H),
                              ("tablet", BLCPAPER_ROWS, BLCPAPER)):
        g = _group(prev, form, pname)
        assert g["matches_plan"] is True, g["reason"]
        assert g["unassigned"] == []
        got = {v["variant"]: v["live_skus"] for v in g["variants"]}
        assert got == {v: c for v, c, _ in rows}, f"{pname}/{form} SKU 수가 계획표와 다르다"
        for v in g["variants"]:
            assert v["cost_table_item_id"] is not None, f"{v['variant']}: 원가표 줄을 못 찾았다"
            assert v["matches_plan"] is True


# ═══════════════════════════════════════════════════════════════════
# ★멈추는 조건 — 계획표와 라이브가 다르면 «거부»한다 (Q3-B)
# ═══════════════════════════════════════════════════════════════════
def test_execute_refuses_when_live_differs_from_plan(client):
    """SKU 하나의 구성을 바꿔 라이브를 표와 다르게 만든다 — 실행이 서야 한다."""
    with client.testing_session() as s:
        row = (s.query(ProductMaster)
                 .filter(ProductMaster.product_name.like(f"{MATTE}, 갤럭시Z폴드%(외부액정3매)"))
                 .first())
        assert row is not None
        row.product_name = f"{MATTE}, 갤럭시Z폴드9 (외부액정3매+내부액정3매)"
        s.commit()

    prev = _preview(client)
    assert prev["safe_to_execute"] is False
    g = _group(prev, "fold")

    # ★2차 대조가 먼저 걸린다 — 옮겨진 SKU 탓에 「외3+내3」 안에 현재 원가가 두 종이 되고,
    #   그 두 종 중 하나를 「외3」이 이미 쓰고 있다. 신호가 가른 것을 값이 부정하는 자리라
    #   **두 변형 모두** 자동 귀속에서 빠지는 것이 옳다(계약 §2-4 — 어긋나면 안 붙인다).
    got = {v["variant"]: v["live_skus"] for v in g["variants"]}
    assert got["외3"] == 0 and got["외3+내3"] == 0, got
    assert got["외3+내3+후2"] == 6 and got["외3+내3+후2+힌2"] == 6, "무관한 변형까지 무너뜨렸다"
    reasons = {u["reason"] for u in g["unassigned"]}
    assert any("2차 대조 불일치" in r for r in reasons), reasons
    assert len(g["unassigned"]) == 18, "어긋난 두 변형의 SKU 전건이 화면에 서야 한다"
    bad = [v for v in g["variants"] if not v["matches_plan"]]
    assert bad and all(v["reason"] for v in bad), "다른 칸이 «왜» 다른지 말해야 한다"

    body = _split(client, expect=409)
    assert "거부" in body["detail"]
    with client.testing_session() as s:
        assert s.query(CostRecipe).count() == 6, "거부됐는데 레시피가 생겼다"


def test_execute_refuses_when_a_sku_has_no_signal(client):
    """1차 신호가 없는 SKU는 자동 귀속하지 않는다 — 그리고 그 SKU를 화면에 세운다."""
    with client.testing_session() as s:
        row = (s.query(ProductMaster)
                 .filter(ProductMaster.product_name.like(f"{MATTE}, 갤럭시Z플립%(내부액정3매)"))
                 .first())
        assert row is not None
        row.product_name = f"{MATTE}, 갤럭시Z플립9"   # 구성 낱말이 없다
        s.commit()

    prev = _preview(client)
    assert prev["safe_to_execute"] is False
    g = _group(prev, "flip")
    assert len(g["unassigned"]) == 1
    assert "1차 신호 없음" in g["unassigned"][0]["reason"]
    assert g["unassigned"][0]["internal_sku"]
    _split(client, expect=409)


# ═══════════════════════════════════════════════════════════════════
# S1-②③④ 실행 — 레시피가 서고, 귀속 근거가 화면에 닿는다
# ═══════════════════════════════════════════════════════════════════
def test_split_creates_variant_recipes_and_moves_links(client):
    out = _split(client)
    # 12행이 만드는 9개 + D-CPP-68이 만드는 4개(r11 플러스·울트라 · r99 플러스·울트라)
    assert len(out["created_recipes"]) == 9 + 4, out["created_recipes"]
    #   base에 남는 것: 매트 fold 9 · flip 7 · 태블릿 21 · BLC6H 15 · BLC종이 26
    assert out["moved_links"] == (92 + 75) - (9 + 7 + 21 + 15 + 26), "base에 남을 SKU까지 옮겼다"
    assert {r["variant"] for r in out["renamed_base"]} == {"외3+내3", "기본"}

    rows = _recipes(client)
    assert len(rows) == 6 + (9 + 4)
    # ★키에 상품명을 넣는다 — 태블릿 군이 셋이라 (폼팩터, 변형)은 더 이상 고유하지 않다
    got = {(r["product_name"], r["form_factor"], r["variant"]): r["link_count"]
           for r in rows if r["form_factor"] != "bar"}
    expected = {(MATTE, "fold", v): c for v, c, _ in FOLD_ROWS}
    expected |= {(MATTE, "flip", v): c for v, c, _ in FLIP_ROWS}
    expected |= {(TABLET, "tablet", v): c for v, c, _ in TABLET_ROWS}
    expected |= {(BLC6H, "tablet", v): c for v, c, _ in BLC6H_ROWS}
    expected |= {(BLCPAPER, "tablet", v): c for v, c, _ in BLCPAPER_ROWS}
    assert got == expected, "레시피 탭의 link_count가 계획표와 다르다"

    # 새 레시피가 «자기» 원가표 줄을 픽했다
    picked = {(r["product_name"], r["variant"]): (r.get("picked") or {}).get("item_name")
              for r in rows if r["variant"] and r["form_factor"] != "bar"}
    assert picked[(MATTE, "외3")] == "지문방지_외부3매"
    assert picked[(TABLET, "울트라")] == "지문방지 PET 2매_울트라"
    # ★D-CPP-68 — BLC 두 군이 «자기» 줄을 집었는가. 이름이 비슷해 섞이기 쉬운 자리다.
    assert picked[(BLC6H, "울트라")] == "6H 강화유리코팅 BLC_울트라, TabS10FE+"
    assert picked[(BLC6H, "플러스")] == "6H 강화유리코팅 BLC_플러스,12.9형,13인치"
    assert picked[(BLCPAPER, "울트라")] == "종이질감 PET 2매_울트라_BLC, TabS10FE+"
    assert picked[(BLCPAPER, "플러스")] == "종이질감 PET 2매_플러스,12.9형,13인치_BLC"


def test_attribution_reason_reaches_the_screen(client):
    """S1-④ — 근거가 DB에만 살면 화면에서 감사할 수 없다. **HTTP payload**를 본다."""
    _split(client)
    rows = _recipes(client)
    target = next(r for r in rows if r["form_factor"] == "fold" and r["variant"] == "외3")
    r = client.get(f"/api/cost/recipes/{target['id']}")
    assert r.status_code == 200, r.text
    links = r.json()["links"]
    assert links and len(links) == 9
    for l in links:
        assert l["note"], "귀속 근거가 payload에 없다"
        assert "1차 신호" in l["note"] and "2차 대조" in l["note"]
        assert "외3" in l["note"]


# ═══════════════════════════════════════════════════════════════════
# S1-⑤ · S2-③ 보류가 «스스로» 풀린다 — 게이트를 안 고치고
# ═══════════════════════════════════════════════════════════════════
def test_hold_releases_itself_after_split(client):
    before = client.get("/api/cost/truth-board").json()["census"]
    assert before["by_cause"].get("g1_grain_mismatch") == 92 + 75
    assert before["held_count"] == 92 + 75
    none_before = before["none_count"]

    _split(client)

    after = client.get("/api/cost/truth-board").json()["census"]
    assert after["by_cause"].get("g1_grain_mismatch", 0) == 0, "분할했는데 보류가 안 풀렸다"
    assert after["held_count"] == 0
    assert after["none_count"] == none_before, "92건 밖의 「정본 없음」이 움직였다"


# ═══════════════════════════════════════════════════════════════════
# §3 금지선 — 분할은 값에 한 원도 안 쓴다
# ═══════════════════════════════════════════════════════════════════
def test_split_never_writes_cost_price(client):
    before = _cost_prices(client)
    _split(client)
    assert _cost_prices(client) == before, "분할이 cost_price를 건드렸다 — §3 금지선"
    from app.models import CostPriceHistory
    with client.testing_session() as s:
        assert s.query(CostPriceHistory).count() == 0, "분할이 값 이력을 남겼다"


# ═══════════════════════════════════════════════════════════════════
# S4-② 92건 «밖»은 한 건도 안 움직인다
# ═══════════════════════════════════════════════════════════════════
def test_outside_the_92_nothing_moves(client):
    def snapshot():
        body = client.get("/api/cost/truth-board").json()
        return {i["internal_sku"]: (i["truth_type"], i["truth_value"], i["cause"])
                for i in body["items"] if i["internal_sku"].startswith("OHI-OUT")}

    before = snapshot()
    assert before, "대조군이 비었다 — 이 테스트는 아무것도 안 지킨다"
    _split(client)
    assert snapshot() == before


# ═══════════════════════════════════════════════════════════════════
# S4-③ ★매핑 재업로드가 분할을 되돌리지 않는다
# ═══════════════════════════════════════════════════════════════════
def test_reupload_does_not_undo_the_split(client):
    from app.services.cost_menu import recipes as RC

    _split(client)
    with client.testing_session() as s:
        before_recipes = s.query(CostRecipe).count()
        before_links = {l.internal_sku: l.recipe_id for l in s.query(CostRecipeLink).all()}

    # ★가짜 매핑 객체를 쓰지 않는다 — **진짜 파서를 통과한 행**이라야 「매핑 정본을 다시
    #   올렸다」를 재현한다. 옵션명이 폼팩터를 정하므로(mapping_parser `_OPTION_RULES`)
    #   폴드·플립·태블릿이 각각 제 묶음으로 간다.
    mapping_rows = [
        ("상품명", "옵션명", "채널명", "카페24 품목코드1"),
        (MATTE, "갤럭시Z폴드7 (외부액정3매)", "자사몰 (cafe24)", "CAFE-FOLD-1"),
        (MATTE, "갤럭시Z플립7 (외부액정3매)", "자사몰 (cafe24)", "CAFE-FLIP-1"),
        (TABLET, "갤럭시탭S9울트라", "자사몰 (cafe24)", "CAFE-TAB-1"),
    ]
    with client.testing_session() as s:
        out = RC.import_drafts(s, mapping_rows=mapping_rows)
        s.commit()

    assert out["skipped_split"] == 3, out
    actions = {r["action"] for r in out["report"]}
    assert actions == {"skipped_split"}, actions
    assert all("되돌리지" in r["reason"] for r in out["report"] if r["action"] == "skipped_split")

    with client.testing_session() as s:
        assert s.query(CostRecipe).count() == before_recipes, "재수입이 기본 레시피를 되살렸다"
        after_links = {l.internal_sku: l.recipe_id for l in s.query(CostRecipeLink).all()}
    assert after_links == before_links, "재수입이 SKU를 기본 레시피로 되돌렸다"


# ═══════════════════════════════════════════════════════════════════
# 멱등 — 두 번 눌러도 레시피가 또 생기지 않는다
# ═══════════════════════════════════════════════════════════════════
def test_split_is_idempotent(client):
    first = _split(client)
    second = _split(client)
    assert second["created_recipes"] == []
    assert second["moved_links"] == 0
    assert second["preview_after"]["safe_to_execute"] is True
    with client.testing_session() as s:
        assert s.query(CostRecipe).count() == 6 + (9 + 4)
    assert first["created_recipes"], "첫 실행이 아무것도 안 만들었다면 이 테스트는 공허하다"


# ═══════════════════════════════════════════════════════════════════
# ★계산값이 없는 변형 레시피가 «거짓말»을 하지 않는다
#
#   분할이 세운 레시피의 부자재 종에 단가가 없으면 계산값이 안 선다. 그때 그 SKU들은
#   「승인된 레시피인데 계산값이 없다 — 재계산이 선행이다」로 보류에 서야 한다.
#   종전 `truth_board`는 계산값 없는 레시피를 묶음에서 «버려서» 그 SKU가
#   **「레시피에 연결된 적이 없다」**로 분류됐다 — 링크가 멀쩡한데 화면이 거짓을 말한다.
#   ★이 테스트가 없으면 그 수정을 되돌려도 전건 초록이다(자체 변이 M8 생존으로 실측).
# ═══════════════════════════════════════════════════════════════════
def test_variant_without_standard_says_recompute_not_unlinked(client):
    from app.models import CostStandard

    _split(client)
    target = next(r for r in _recipes(client)
                  if r["form_factor"] == "fold" and r["variant"] == "외3")
    with client.testing_session() as s:
        s.query(CostStandard).filter_by(recipe_id=target["id"]).delete()
        s.commit()

    body = client.get("/api/cost/truth-board").json()
    rows = [i for i in body["items"] if i["recipe_id"] == target["id"]]
    assert len(rows) == 9, "링크가 있는데 표에서 사라졌다 — 「연결된 적이 없다」로 샜다"
    for i in rows:
        assert i["truth_type"] == "held"
        assert i["cause"] == "no_standard", i["cause"]
        assert "재계산이 선행" in i["reason"]
        assert "연결된 적이 없다" not in i["reason"]
    assert body["census"]["by_cause"].get("no_link_other", 0) == 0


# ═══════════════════════════════════════════════════════════════════
# ★적대 리뷰 1R P1-1 — 태블릿 «잔여 등급»이 거짓말하지도 침묵하지도 않는다
#
#   리뷰어 재현: 태블릿 SKU 하나를 크기 낱말이 **하나도 없는** 신규 기종명으로 바꾸고
#   현재 원가는 「기본」 군과 같게 두면, 초판은 `safe_to_execute: True` · `unassigned: []`로
#   **완전히 조용했고**, 링크 note에는 「크기 낱말 「기본」」이라는 **없는 근거**가 적혔다.
#
#   ★처방은 «막기»가 아니라 «말하기»다 — 라이브 21건 전건이 낱말 없이 기본에 든다
#   (원가표가 `_기본`/`_플러스`/`_울트라` 셋으로 생겼다). 막으면 정상 21건이 전부 선다.
#   그래서 ①근거 문장이 사실을 말하고 ②잔여가 «몇 건인지» 화면에 선다.
# ═══════════════════════════════════════════════════════════════════
def test_tablet_residual_is_counted_not_silent(client):
    prev = _preview(client)
    g = _group(prev, "tablet", TABLET)
    base = next(v for v in g["variants"] if v["variant"] == "기본")
    # 라이브 21건 전건이 낱말 없이 기본에 든다 — 그 사실이 «수»로 보여야 한다
    assert base["residual_skus"] == 21, base
    assert g["residual_total"] == 21
    # 잔여 = 태블릿 21 + BLC6H 기본 15 + BLC종이 기본 26 (셋 다 «크기 낱말 없음»이 정상)
    assert prev["residual_total"] == 21 + 15 + 26
    assert "잔여" in (g["residual_sentence"] or "")
    # 울트라·플러스는 낱말이 실제로 걸린다 — 잔여가 아니다
    for v in g["variants"]:
        if v["variant"] in ("울트라", "플러스"):
            assert v["residual_skus"] == 0, v


def test_tablet_residual_note_does_not_invent_a_word(client):
    """★근거 문장이 «있지도 않은 낱말»을 있다고 적지 않는다."""
    _split(client)
    target = next(r for r in _recipes(client)
                  if r["product_name"] == TABLET and r["variant"] == "기본")
    body = client.get(f"/api/cost/recipes/{target['id']}").json()
    notes = [l["note"] for l in body["links"]]
    assert notes and all(n for n in notes)
    for n in notes:
        assert "잔여" in n, n
        # 초판이 적던 거짓 문장이 다시 나오면 안 된다
        assert "크기 낱말 「기본」" not in n, n
    # 울트라는 낱말이 실제로 걸렸으므로 잔여라고 적으면 안 된다
    ultra = next(r for r in _recipes(client)
                 if r["product_name"] == TABLET and r["variant"] == "울트라")
    ubody = client.get(f"/api/cost/recipes/{ultra['id']}").json()
    for l in ubody["links"]:
        assert "울트라 낱말" in l["note"], l["note"]
        assert "잔여" not in l["note"], l["note"]


def test_unknown_tablet_model_is_visible_as_residual(client):
    """★리뷰어의 재현 그대로 — 낱말 없는 신규 기종명 + 같은 원가.

    막지는 않는다(잔여는 정상 경로다). 그러나 **잔여 수가 늘고**, 그 SKU의 근거 문장이
    「낱말이 안 걸렸다」를 말한다. 초판은 둘 다 없었다.
    """
    with client.testing_session() as s:
        row = (s.query(ProductMaster)
                 .filter(ProductMaster.product_name.like(f"{TABLET}, 아이패드프로7세대%11인치"))
                 .first())
        assert row is not None
        row.product_name = f"{TABLET}, 뉴패드X100"   # 크기 낱말 0개
        s.commit()

    prev = _preview(client)
    g = _group(prev, "tablet", TABLET)
    base = next(v for v in g["variants"] if v["variant"] == "기본")
    assert base["residual_skus"] == 21          # 여전히 21 — 이 SKU도 잔여였다
    assert base["live_skus"] == 21              # 개수 게이트는 안 걸린다(리뷰어 지적 그대로)
    assert prev["safe_to_execute"] is True      # 막지 않는다 — 잔여는 정상 등급이다

    _split(client)
    target = next(r for r in _recipes(client)
                  if r["product_name"] == TABLET and r["variant"] == "기본")
    body = client.get(f"/api/cost/recipes/{target['id']}").json()
    hit = [l for l in body["links"] if "뉴패드" in (l["note"] or "") or l["internal_sku"]]
    note = next(l["note"] for l in body["links"] if l["note"])
    assert "잔여" in note
    # ★이 SKU가 «어느 낱말로도» 안 걸렸다는 사실이 화면에 남는다
    assert "안 걸렸다" in note, note


def test_empty_product_name_has_no_signal_at_all(client):
    """상품명이 비면 잔여도 아니다 — 「신호 없음」이라 자동 귀속을 거부한다."""
    with client.testing_session() as s:
        row = (s.query(ProductMaster)
                 .filter(ProductMaster.product_name.like(f"{TABLET}, 갤럭시탭S9울트라%"))
                 .first())
        assert row is not None
        row.product_name = "   "
        s.commit()
    prev = _preview(client)
    assert prev["safe_to_execute"] is False
    g = _group(prev, "tablet", TABLET)
    assert any("1차 신호 없음" in (u["reason"] or "") for u in g["unassigned"]), g["unassigned"]
    _split(client, expect=409)


# ═══════════════════════════════════════════════════════════════════
# D-CPP-68 — Jino 판정 둘 (2026-09-03)
#
#   ★이 묶음이 지켜야 할 것은 «통과»가 아니라 **좁혔지 없애지 않았다**는 사실이다.
#     가드를 지우면 아래 3·4번이 초록으로 남고, 그러면 이 파일은 거짓말을 한다.
# ═══════════════════════════════════════════════════════════════════
def _row(sku, variant, price, *, source=GS.SOURCE_RESIDUAL):
    return GS._SkuRow(internal_sku=sku, product_name=f"{sku} 이름", cost_price=D(price),
                      signal=variant, signal_source=source, variant=variant,
                      link_status="approved")


def test_equal_cost_table_totals_are_not_a_conflict():
    """Jino 원문: *"두 옵션이 가격이 같아. 대조불가가 아니라 각각의 옵션에 단가를 붙이는거지."*

    r11이 정확히 이 모양이다 — 원가표가 「기본」과 「플러스」를 총액 4,504.50으로 «같게»
    두었고 현재 원가도 같다. 값으로 가를 수 없는 게 정상이지 충돌이 아니다.
    """

    rows = [_row("A1", "기본", "4352.70"), _row("A2", "플러스", "4352.70")]
    GS._cross_check(rows, {"기본": D("4504.50"), "플러스": D("4504.50")})
    assert [r.conflict for r in rows] == [None, None]


def test_different_cost_table_totals_still_conflict():
    """★가드가 살아 있다 — 원가표 총액이 «다른» 두 변형이 현재 원가를 공유하면 충돌이다.

    이미 실행된 12행이 전부 이 세계에 있다(값이 서로 다르다). 이 테스트가 빨개지면
    D-CPP-68이 가드를 좁힌 게 아니라 «없앤» 것이다.
    """

    rows = [_row("B1", "기본", "4352.70"), _row("B2", "플러스", "4352.70")]
    GS._cross_check(rows, {"기본": D("5054.50"), "플러스": D("5582.50")})
    assert all(r.conflict and "현재 원가를 공유" in r.conflict for r in rows), rows


def test_unknown_totals_still_conflict():
    """원가표 총액을 «모르면» 구판 그대로 충돌로 센다 — 모름을 통과로 바꾸지 않는다."""

    rows = [_row("C1", "기본", "4352.70"), _row("C2", "플러스", "4352.70")]
    GS._cross_check(rows, None)
    assert all(r.conflict for r in rows), rows


def test_jino_decided_sku_is_not_requestioned():
    """Jino 원문: *"초기 원가표 단계에서는 엑셀표가 기준이야. 그러니까 원가표대로 울트라가 맞지"*

    `OHI-0074`는 현재 원가가 플러스 값인데 원가표는 울트라 칸에 적었다. 사람이
    「원가표가 맞다」고 판정했으므로 2차 대조가 그 SKU에게 **되묻지 않는다** —
    되물을 상대(현재 원가)가 틀렸다고 이미 판정됐기 때문이다.
    """

    assert GS.DECIDED_BY_JINO.get("OHI-0074") == "울트라"
    rows = [_row("OHI-0074", "울트라", "4572.70"), _row("D2", "울트라", "5892.70")]
    GS._cross_check(rows, {"울트라": D("6044.50")})
    assert [r.conflict for r in rows] == [None, None], rows

    # ★판정 목록에 «없는» SKU였다면 여전히 충돌이다 — 예외가 목록에 매여 있음을 증명한다
    rows2 = [_row("D1", "울트라", "4572.70"), _row("D2", "울트라", "5892.70")]
    GS._cross_check(rows2, {"울트라": D("6044.50")})
    assert all(r.conflict and "여러 종" in r.conflict for r in rows2), rows2


def test_s10fe_ultra_rule_does_not_leak_to_non_blc():
    """★비-BLC 군의 등급이 한 글자도 안 바뀐다.

    원가표에서 `TabS10FE+`는 BLC 줄(45·51·54)에만 있다. 전역으로 고쳤다면
    **이미 승인·컷오버된** `OHI-0111`(r108 `variant=플러스`)이 조용히 울트라로 옮겨 간다.
    """

    name = f"{TABLET}, 갤럭시탭S10FE플러스"
    assert GS.tablet_size_grade_with_source(name)[0] == "플러스"
    assert GS.tablet_size_grade_blc_with_source(name)[0] == "울트라"
    assert "OHI-0111" not in GS.DECIDED_BY_JINO


def test_decided_sku_note_does_not_claim_a_cross_check_it_skipped():
    """★판정 SKU의 근거 문장이 「변형 안에서 1종」이라 말하면 **거짓**이다.

    그 묶음은 실제로 2종이고, 그 SKU를 값 집합에서 뺐기 때문에 1종처럼 보였을 뿐이다.
    이 세션의 첫 prod 배포가 정확히 그렇게 적었고 라이브에서 잡혔다 —
    적대 리뷰 1R P1-1(「있지도 않은 근거를 적지 않는다」)의 재발이다.
    """

    plan = GS.plan_for("종이질감 저반사 지문방지 블루라이트 차단 액정보호필름 2매", "tablet")
    assert plan is not None
    decided = _row("OHI-0074", "울트라", "4572.70", source=GS.SOURCE_S10FE_ULTRA)
    plain = _row("OTHER", "울트라", "5892.70", source=GS.SOURCE_ULTRA)

    n_decided = GS._note_for(decided, plan)
    assert "변형 안에서 1종" not in n_decided, n_decided
    assert "하지 않았다" in n_decided and "엑셀표가 기준" in n_decided, n_decided
    # 판정 «아닌» SKU는 구판 문장 그대로여야 한다 — 예외가 전건으로 새면 안 된다
    assert "변형 안에서 1종" in GS._note_for(plain, plan)
