# test_cost_purchased_kind_wiring.py — `purchased`가 «값»에서 «사람이 보는 것»까지 가는가
#
# ## 왜 이 파일이 생겼나 (적대 리뷰 1R, 2026-09-01)
#
# `recipe_kind`에 세 번째 값 `purchased`를 넣은 커밋에서, 리뷰어가 변이 5종을 **살려냈다**.
# 다섯 다 「값은 맞는데 사람이 그걸 못 본다」 계열이었고, 관련 테스트 110개가 전건 초록이었다:
#
#   M4 · M12  `recipes._apply_item_lines`의 `purchased` 분기를 지워도 초록
#             → 픽해도 구성 0줄. **이 커밋이 고치겠다고 선언한 바로 그 결함**이 재발한다.
#   M2        `purchased_price._exclusion_reason`의 `REASON_PURCHASED` 분기를 지워도 초록
#             → 화면 사유가 조용히 사라진다.
#   M13       그 분기를 `line_count > 0` **뒤로** 옮겨도 초록
#             → 매입품이 「조립품 — 우리 계산이 정본」으로 잘못 읽힌다.
#   M10       파서 섹션 제목 판별에서 「다음 헤더의 col1 == 품목」 조건을 지워도 초록
#             → 앞 섹션의 **마지막 상품 행이 이상 표시도 없이 통째로 사라진다.**
#
# ★넷 다 **코드 주석이 위험을 이미 자백해 놓은 자리**였다("여기서 먼저 가른다", "섹션 끝
#   상품 행이 먹힌다"). 적어 두는 것으로는 안 막힌다 — 그게 이 파일이 있는 이유다.
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import CostRecipe, CostTableItem
from app.services.cost_menu import recipes as R
from app.services.cost_menu.materials import (
    IMPORTED_GOODS_CATEGORY,
    IMPORTED_GOODS_KIND,
    PURCHASED_CATEGORY,
    PURCHASED_KIND,
)
from app.services.cost_menu.purchased_price import (
    REASON_ASSEMBLY,
    REASON_IMPORTED,
    REASON_PURCHASED,
    _exclusion_reason,
)
from app.services.cost_menu.recipe_parser import parse_cost_table
from app.services.cost_menu.truth_source import (
    CAUSE_INCOMPLETE_SINGLE_LINE,
    OWNER_CUTOVER,
    TRUTH_COMPUTED,
    CAUSE_IMPORTED_SINGLE_LINE,
    CAUSE_PURCHASED_SINGLE_LINE,
    OWNER_DCPP63,
    OWNER_TRACK_A1A2,
    TRUTH_HELD,
    TRUTH_PURCHASED,
    RecipeGroup,
    classify_group,
)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    # ★prod 세션과 같은 설정 — autoflush=True면 「방금 만든 행이 안 보이는」 결함을 못 잡는다.
    S = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with S() as s:
        yield s


def _item(db, *, kind, name="맥세이프 소다 케이스 포유", section="케이스", total="4202.00"):
    it = CostTableItem(
        section=section,
        item_name=name,
        form_factor=None,
        recipe_kind=kind,
        total_inc_vat=Decimal(total),
        row_number=95,
    )
    db.add(it)
    db.flush()
    return it


def _recipe(db, *, product_name="오하이 맥세이프 소다 케이스"):
    r = CostRecipe(
        product_name=product_name,
        form_factor=None,
        status="draft",
        source="excel",
        anomaly_flag="no_recipe_match",
    )
    db.add(r)
    db.flush()
    return r


# ═══════════════════════════════════════════════════════════════════
# P1-1 (변이 M4·M12) — 픽하면 «구성 줄이 실제로 생긴다»
# ═══════════════════════════════════════════════════════════════════


def test_picking_a_purchased_item_creates_the_degenerate_single_line(db):
    """★`purchased` 픽이 구성 1줄을 만든다 — 0줄이면 영영 「정본 없음」에 갇힌다.

    이게 이 커밋의 존재 이유다. 분기가 `imported_goods`만 보던 초판으로 되돌아가면
    이 테스트가 죽는다.
    """
    recipe = _recipe(db)
    item = _item(db, kind=PURCHASED_KIND)
    db.flush()

    R.pick_cost_table_item(db, recipe.id, item.id)
    db.flush()
    db.refresh(recipe)

    assert len(recipe.lines) == 1, "매입품 픽이 구성을 안 만들었다 — 0줄이면 정본 없음에 갇힌다"


def test_purchased_line_gets_the_purchased_category_not_the_imported_one(db):
    """★카테고리를 반드시 가른다 — 원장 연결의 유일한 표지이기 때문이다.

    `IMPORTED_GOODS_CATEGORY`를 국내 매입품에 붙이면 원장 product 라인이 새어 든다.
    코드 주석이 이 위험을 적어 뒀는데 지키는 테스트가 없었다(변이 M12 생존).
    """
    recipe = _recipe(db)
    item = _item(db, kind=PURCHASED_KIND)
    db.flush()
    R.pick_cost_table_item(db, recipe.id, item.id)
    db.flush()
    db.refresh(recipe)

    material = recipe.lines[0].material
    assert material.category == PURCHASED_CATEGORY
    assert material.category != IMPORTED_GOODS_CATEGORY, "매입품에 수입 카테고리가 붙었다"


def test_imported_still_gets_the_imported_category(db):
    """수입 완제품 경로는 안 건드린다(이 변경의 «안 건드린다» 쪽)."""
    recipe = _recipe(db, product_name="오타오 투명 강화유리")
    item = _item(db, kind=IMPORTED_GOODS_KIND, name="오타오_투명 강화유리 (New Ver.)",
                 section="오타오_강화유리필름", total="3102.70")
    db.flush()
    R.pick_cost_table_item(db, recipe.id, item.id)
    db.flush()
    db.refresh(recipe)

    assert len(recipe.lines) == 1
    assert recipe.lines[0].material.category == IMPORTED_GOODS_CATEGORY


def test_the_two_kinds_say_different_things_in_the_line_note(db):
    """★구성 줄의 note는 **사람이 읽는 표면**이다 — 뭉뚱그리면 왜 1줄인지 못 읽는다."""
    r1, r2 = _recipe(db, product_name="매입품"), _recipe(db, product_name="수입품")
    i1 = _item(db, kind=PURCHASED_KIND, name="맥세이프 그립톡")
    i2 = _item(db, kind=IMPORTED_GOODS_KIND, name="오타오_투명 강화유리 (New Ver.)")
    db.flush()
    R.pick_cost_table_item(db, r1.id, i1.id)
    R.pick_cost_table_item(db, r2.id, i2.id)
    db.flush()
    db.refresh(r1)
    db.refresh(r2)

    assert "매입" in (r1.lines[0].note or "")
    assert "원가표" in (r1.lines[0].note or "")
    assert "수입" in (r2.lines[0].note or "")
    assert "원장" in (r2.lines[0].note or "")
    assert r1.lines[0].note != r2.lines[0].note


# ═══════════════════════════════════════════════════════════════════
# P1-2 (변이 M2·M13) — 「대상 아님」의 **이유**가 종류마다 다르게 선다
# ═══════════════════════════════════════════════════════════════════


class _Link:
    def __init__(self, recipe_kind, line_count):
        self.recipe_kind = recipe_kind
        self.line_count = line_count


class _Fact:
    def __init__(self, *links):
        self.links = list(links)


def test_purchased_gets_its_own_exclusion_reason_not_the_assembly_one(db):
    """★매입품의 사유가 「조립품」으로 뭉개지면 안 된다 (변이 M2 — 분기 삭제)."""
    assert _exclusion_reason(_Fact(_Link(PURCHASED_KIND, 1))) == REASON_PURCHASED


def test_purchased_check_wins_over_line_count_regardless_of_order(db):
    """★★**순서가 곧 규칙이다** (변이 M13 — 순서만 바꿔도 초록이었다).

    매입품은 «구성 1줄»을 갖는다(퇴화형). `line_count > 0` 검사가 먼저 걸리면
    「조립품 — 우리 계산이 정본」이라는 **틀린 이유**가 화면에 선다. 둘 다 「파일 값 금지」라
    결과는 같지만, 사람이 읽는 이유가 달라진다.
    """
    reason = _exclusion_reason(_Fact(_Link(PURCHASED_KIND, 1)))
    assert reason == REASON_PURCHASED
    assert reason != REASON_ASSEMBLY
    # 세 종류가 서로 구별된다 — 하나로 뭉개지면 화면이 이유를 못 말한다.
    assert len({REASON_PURCHASED, REASON_ASSEMBLY, REASON_IMPORTED}) == 3
    assert _exclusion_reason(_Fact(_Link("assembly", 1))) == REASON_ASSEMBLY
    assert _exclusion_reason(_Fact(_Link(IMPORTED_GOODS_KIND, 1))) == REASON_IMPORTED


def test_purchased_reason_names_the_real_source(db):
    """사유 문장이 «어디가 정본인지»를 말한다 — 「대상 아님」만으로는 다음 행동이 안 정해진다."""
    assert "매입" in REASON_PURCHASED
    assert "원가표" in REASON_PURCHASED


# ═══════════════════════════════════════════════════════════════════
# P1-3 (변이 M10) — 섹션 경계에서 «마지막 상품 행»이 사라지지 않는다
# ═══════════════════════════════════════════════════════════════════


def _row(*cells):
    return [None, *cells]


# layout a — 제목 행이 헤더와 «따로» 있는 섹션 (필름)
_FILM_TITLE = _row("모바일 필름-아이폰,갤럭시")
_FILM_HEADER = _row("품목", None, "제품원가\n(+VAT)", "매입", "필름", "필름*매입", "부착\n안내문")
_FILM_ROW = _row("자가복원 고투명 EPU 3매", None, 3275.8, 3, 800, 2400, 30)
# layout b — 제목이 곧 헤더인 섹션 (케이스·악세서리 — `PURCHASED_SECTIONS`가 전부 이 모양)
_CASE_HEADER = _row(
    "케이스", None, "제품원가\n(US$)", "제품원가\n(KRW)", "뮬류비(ea)", "관세", "부가세",
    "상품원가", "부자재",
)
_CASE_ROW = _row("맥세이프 소다 케이스 포유", None, None, 4202, None, None, None, 3700, 120)


def test_last_row_of_a_section_survives_when_a_layout_b_section_follows():
    """★★layout a 섹션의 **마지막 상품 행**이 layout b 헤더 때문에 사라지면 안 된다.

    코드 주석이 이 위험을 지목해 놨는데(「섹션 끝 상품 행이 먹힌다」) 지키는 테스트가 없었다.
    제목 판별을 「다음 유효 행이 헤더이기만 하면」으로 완화하면 `_FILM_ROW`(3,275.8원)가
    **이상 표시도 없이** 사라진다 — 파서의 8행 소실과 정확히 같은 모양의 결함이다.

    그리고 `PURCHASED_SECTIONS` 4개가 전부 layout b라, 그 앞에 오는 어떤 섹션도 이 위험에 있다.
    """
    r = parse_cost_table([_FILM_TITLE, _FILM_HEADER, _FILM_ROW, _CASE_HEADER, _CASE_ROW])

    names = [d.item_name for d in r.recipes]
    assert "자가복원 고투명 EPU 3매" in names, (
        "layout b 섹션이 뒤따르자 앞 섹션의 마지막 상품 행이 사라졌다 — 조용한 소실"
    )
    assert "맥세이프 소다 케이스 포유" in names
    assert r.recipe_count == 2, names

    by_name = {d.item_name: d for d in r.recipes}
    # 사라지지 «않는» 것만으로는 부족하다 — 값도 섹션도 제 것이어야 한다.
    assert by_name["자가복원 고투명 EPU 3매"].excel_total_inc_vat == Decimal("3275.8")
    assert by_name["자가복원 고투명 EPU 3매"].section == "모바일 필름-아이폰,갤럭시"
    assert by_name["자가복원 고투명 EPU 3매"].recipe_kind == "assembly"
    assert by_name["맥세이프 소다 케이스 포유"].section == "케이스"
    assert by_name["맥세이프 소다 케이스 포유"].recipe_kind == PURCHASED_KIND


def test_section_title_row_is_still_not_a_data_row():
    """반대 방향도 지킨다 — 제목 행이 데이터로 새면 수입 5건이 케이스로 분류된다(실사고)."""
    imported_title = _row("오타오_강화유리필름", None, "환율", 200)
    imported_header = _row(
        "품목", None, "제품원가\n(CNY)", "제품원가\n(KRW)", "뮬류비", "관세", "부가세", "상품원가"
    )
    imported_row = _row("오타오_투명 강화유리 (New Ver.)", None, 12.2, 2440, 0.1, 0.056, 0.1, 3102.704)

    r = parse_cost_table([_CASE_HEADER, _CASE_ROW, imported_title, imported_header, imported_row])
    names = [d.item_name for d in r.recipes]
    assert "오타오_강화유리필름" not in names, "섹션 제목이 상품으로 잡혔다"
    assert r.recipe_count == 2, names
    by_name = {d.item_name: d for d in r.recipes}
    assert by_name["오타오_투명 강화유리 (New Ver.)"].recipe_kind == IMPORTED_GOODS_KIND
    assert by_name["오타오_투명 강화유리 (New Ver.)"].excel_total_inc_vat == Decimal("3102.704")


# ═══════════════════════════════════════════════════════════════════
# P1-4 — 정본 판별층이 `purchased`를 «안다»
#
# 리뷰가 잡은 진짜 코드 결함이다: `classify_group`이 `line_count == 1`을 볼 때
# `imported_goods`만 특례로 두고 `purchased`는 일반 분기로 흘려, 화면에
# **「부자재 보강이 선행이다」라는 틀린 지시**를 냈다. 매입품은 1줄이 정상 모양이라
# 보강할 부자재가 원리적으로 없다 — 있지도 않은 작업을 사람에게 시키는 말이다.
#
# ★그리고 이 테스트는 **내가 코드를 고친 «뒤에» 빠뜨렸다가 변이 주입에서 살아남아
#   되돌아온 것**이다. 고침과 그것을 지키는 단언은 같은 커밋에 있어야 한다.
# ═══════════════════════════════════════════════════════════════════


def _group(kind, *, line_count=1, std="1200", cur="1000"):
    return RecipeGroup(
        recipe_id=999,
        product_name="테스트",
        form_factor=None,
        recipe_kind=kind,
        skus=("SKU1",),
        cost_price_kinds=1,
        cost_price_min=Decimal(cur),
        std_cost_inc_vat=Decimal(std),
        line_count=line_count,
    )


def test_purchased_single_line_is_not_called_an_incomplete_recipe():
    """★1줄인 매입품에 「부자재 보강이 선행이다」라고 말하면 안 된다."""
    cause, truth, reason, owner = classify_group(_group(PURCHASED_KIND), ())

    assert cause == CAUSE_PURCHASED_SINGLE_LINE
    assert cause != CAUSE_IMPORTED_SINGLE_LINE, "수입품 사유로 뭉개졌다"
    # ★단언은 «틀린 지시»를 잡는다 — 낱말 「부자재 보강」이 아니라 그 **명령형**이다.
    #   매입품 문장은 「부자재 보강 «대상이 아니고»」라고 그 낱말을 부정으로 쓴다.
    assert "부자재 보강이 선행이다" not in reason, "매입품에 있지도 않은 보강 작업을 시키고 있다"
    assert "대상이 아니" in reason, "왜 1줄이어도 되는지를 말해야 한다"
    assert "매입" in reason and "원가표" in reason


def test_purchased_truth_is_the_purchase_price_not_the_computed_value():
    """정본 «유형»이 갈려야 컷오버가 계산값을 잘못 밀지 않는다."""
    _, truth, _, owner = classify_group(_group(PURCHASED_KIND), ())
    assert truth == TRUTH_PURCHASED
    assert owner == OWNER_DCPP63


def test_imported_and_assembly_single_line_paths_are_unchanged():
    """★1줄짜리 세 종류가 **서로 다른 답**을 낸다 — D-CPP-66 이후 판이 바뀐 자리.

    · 수입 완제품  → 계산값이 정본(원장 로트 단가). **보류 해제됨**
    · 국내 매입품  → 매입가가 정본
    · 조립품      → 진짜로 계산이 불완전하다(부자재 미보강). **여전히 보류**

    ★셋이 같은 사유 코드를 쓰면 화면이 사유로 묶어 세는 순간 조용히 틀린다 —
      그래서 조립품에 `CAUSE_INCOMPLETE_SINGLE_LINE`을 따로 뒀다.
    """
    c_imp, t_imp, r_imp, o_imp = classify_group(_group(IMPORTED_GOODS_KIND), ())
    assert (c_imp, t_imp, o_imp) == (CAUSE_IMPORTED_SINGLE_LINE, TRUTH_COMPUTED, OWNER_CUTOVER)
    assert "원장" in r_imp

    c_asm, t_asm, r_asm, o_asm = classify_group(_group("assembly"), ())
    assert (c_asm, t_asm, o_asm) == (
        CAUSE_INCOMPLETE_SINGLE_LINE,
        TRUTH_HELD,
        OWNER_TRACK_A1A2,
    )
    assert "부자재 보강이 선행이다" in r_asm
    assert c_imp != c_asm, "수입품과 조립품이 같은 사유 코드를 쓰면 집계가 섞인다"


def test_the_three_single_line_kinds_do_not_collapse_into_one_message():
    """★셋이 같은 문장을 내면 화면은 「1줄이다」만 말하고 «왜»를 못 말한다."""
    reasons = {
        classify_group(_group(k), ())[2]
        for k in (PURCHASED_KIND, IMPORTED_GOODS_KIND, "assembly")
    }
    assert len(reasons) == 3, reasons
