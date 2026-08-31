# test_purchased_price_service.py — 매입 완제품 단가 제안·확인 (계약 D-CPP-63 S1 2/3)
#
# ★이 파일이 지키는 것은 «값이 계산되나»가 아니라 «금지선이 구조로 서 있나»다.
#   계약 §3의 최대 사고는 **파일이 조립품(필름) 단가를 덮는 것**이고, 그 방어는
#   `confirm_group`의 서버측 재검사 하나에 걸려 있다 — 화면을 믿지 않는다는 그 한 줄이
#   빠져도 「제안 화면」은 멀쩡히 초록이다. 그래서 **화면이 보낸 목록을 그대로 쓰는**
#   변이를 잡는 테스트를 따로 둔다(전역 §4 「최종 표면까지 가는 경로를 끊는 변이」의 쓰기판).
from __future__ import annotations

from datetime import datetime
from decimal import Decimal as D

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import (
    Base,
    CostPurchasedPrice,
    CostRecipe,
    CostRecipeLine,
    CostRecipeLink,
    CostMaterial,
    ProductMaster,
)
from app.services.cost_menu import purchased_price as PP
from app.services.cost_menu.purchased_price_parser import parse_price_sheet


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield s
    finally:
        s.close()


# ── 픽스처 조립 ──────────────────────────────────────────────────────────────


def mk_recipe(db, name, *, kind="assembly", lines=0, status="draft") -> CostRecipe:
    r = CostRecipe(product_name=name, recipe_kind=kind, status=status, source="excel")
    db.add(r)
    db.flush()
    for i in range(lines):
        m = CostMaterial(name=f"{name}-자재{i}")
        db.add(m)
        db.flush()
        db.add(CostRecipeLine(recipe_id=r.id, material_id=m.id, quantity=D("1")))
    db.flush()
    return r


def mk_sku(db, sku, product_name, recipe, *, cost_price=D("0"), link_status="draft"):
    db.add(
        ProductMaster(
            internal_sku=sku, product_name=product_name, cost_price=cost_price
        )
    )
    if recipe is not None:
        db.add(
            CostRecipeLink(
                internal_sku=sku, recipe_id=recipe.id, status=link_status, source="manual"
            )
        )
    db.flush()


def sheet(*rows):
    """(상품명, 원가) 쌍 → 파서가 먹는 시트. 헤더는 08-07판 레이아웃."""
    out = [["상품명", "원가", "채널명", "카페24 품목코드1"]]
    for name, price in rows:
        out.append([name, price, "카페24", ""])
    return out


# ── 대상 판별 — 조립품·수입품은 구조로 빠진다 ────────────────────────────────


def test_assembly_recipe_is_excluded_not_priced(db):
    """★계약 §0-D 최대 사고: 파일이 필름 값을 덮는 것. 구성이 있으면 대상이 아니다."""
    film = mk_recipe(db, "오하이 강화유리 필름 2매", lines=3, status="approved")
    mk_sku(db, "SKU-FILM", "필름, 아이폰16", film, cost_price=D("2000"))

    p = PP.build_proposal(db, parse_price_sheet(sheet(("필름, 아이폰16", 4352.7))), "08-07판")

    assert p.groups == []
    assert [s.internal_sku for s in p.excluded] == ["SKU-FILM"]
    assert p.excluded[0].excluded_reason == PP.REASON_ASSEMBLY
    # 대상이 아니어도 «비교»는 보여준다 — 처분은 사람이 한다.
    assert p.excluded[0].current_cost_price == D("2000")


def test_imported_goods_is_excluded(db):
    imp = mk_recipe(db, "오타오 강화유리", kind="imported_goods", lines=1, status="approved")
    mk_sku(db, "SKU-IMP", "오타오 강화유리, 아이폰16", imp)

    p = PP.build_proposal(db, parse_price_sheet(sheet(("오타오 강화유리, 아이폰16", 3000))), "f")

    assert p.groups == []
    assert p.excluded[0].excluded_reason == PP.REASON_IMPORTED


def test_sku_without_recipe_is_excluded(db):
    mk_sku(db, "SKU-ORPHAN", "케이스, 아이폰16", None)
    p = PP.build_proposal(db, parse_price_sheet(sheet(("케이스, 아이폰16", 900))), "f")
    assert p.excluded[0].excluded_reason == PP.REASON_NO_RECIPE


# ── 묶음 = 레시피 × 단가. 묶음은 조작의 단위이지 값의 단위가 아니다 ──────────


def test_one_recipe_two_prices_makes_two_groups_and_keeps_each_value(db):
    """★계약 §0-F의 요지 — r84 「일미리 케이스」 51 SKU가 922×29 / 2,400×22.

    한 묶음으로 뭉개면 29개의 922원과 22개의 2,400원이 하나의 거짓 숫자가 된다.
    """
    r84 = mk_recipe(db, "일미리 케이스", lines=0)
    mk_sku(db, "S1", "일미리 케이스, 아이폰15", r84)
    mk_sku(db, "S2", "일미리 케이스, 아이폰16", r84)
    mk_sku(db, "S3", "일미리 케이스, 갤럭시S24", r84)

    p = PP.build_proposal(
        db,
        parse_price_sheet(
            sheet(
                ("일미리 케이스, 아이폰15", 922),
                ("일미리 케이스, 아이폰16", 922),
                ("일미리 케이스, 갤럭시S24", 2400),
            )
        ),
        "08-07판",
    )

    assert len(p.groups) == 2
    big, small = p.groups[0], p.groups[1]
    assert (big.price, big.sku_count) == (D("922"), 2)
    assert (small.price, small.sku_count) == (D("2400"), 1)
    # SKU별 제 값이 각자 서 있다
    assert {s.internal_sku: s.file_price for s in big.skus} == {
        "S1": D("922"),
        "S2": D("922"),
    }
    assert p.target_sku_count == 3


def test_diff_against_current_cost_price_is_shown_not_applied(db):
    r = mk_recipe(db, "스트랩", lines=0)
    mk_sku(db, "S1", "스트랩, 41mm", r, cost_price=D("1500"))
    p = PP.build_proposal(db, parse_price_sheet(sheet(("스트랩, 41mm", 1200))), "f")

    s = p.groups[0].skus[0]
    assert s.current_cost_price == D("1500")
    assert s.diff == D("-300")
    # `cost_price`는 한 글자도 안 바뀐다 (계약 §3 금지선)
    assert db.get(ProductMaster, 1).cost_price == D("1500")


# ── 1원은 값이 아니라 공백이다 ───────────────────────────────────────────────


def test_placeholder_goes_to_blanks_never_to_a_group(db):
    r = mk_recipe(db, "시스루 케이스", lines=0)
    mk_sku(db, "S1", "시스루 케이스 블랙, 아이폰13", r)
    p = PP.build_proposal(db, parse_price_sheet(sheet(("시스루 케이스 블랙, 아이폰13", 1))), "f")

    assert p.groups == []
    assert len(p.blanks) == 1
    assert p.blanks[0].is_placeholder and p.blanks[0].file_price is None


def test_unmatched_file_rows_are_named_not_silently_dropped(db):
    """발견 0건과 「못 붙였다」가 같은 숫자로 보이면 안 된다(교훈 #123)."""
    p = PP.build_proposal(db, parse_price_sheet(sheet(("세상에 없는 상품", 900))), "f")
    assert p.unmatched == ["세상에 없는 상품"]
    assert p.counts()["unmatched_rows"] == 1


def test_ambiguous_name_is_not_auto_picked(db):
    r = mk_recipe(db, "케이스", lines=0)
    mk_sku(db, "S1", "같은이름", r)
    mk_sku(db, "S2", "같은이름", r)
    p = PP.build_proposal(db, parse_price_sheet(sheet(("같은이름", 900))), "f")

    assert p.groups == []
    assert {s.excluded_reason for s in p.excluded} == {PP.REASON_AMBIGUOUS}


# ── 쓰기 — 서버측 재검사가 금지선의 집행 지점이다 ───────────────────────────


def test_confirm_writes_approved_rows_with_provenance(db):
    r = mk_recipe(db, "일미리 케이스", lines=0)
    mk_sku(db, "S1", "일미리 케이스, 아이폰15", r)

    res = PP.confirm_group(
        db,
        internal_skus=["S1"],
        price=D("922"),
        source_file="ohisell_mapping_template_20260807.xlsx",
        source_names={"S1": "일미리 케이스, 아이폰15"},
        now=datetime(2026, 8, 31, 10, 0, 0),
    )
    db.flush()

    assert (res.written, res.skipped_count) == (1, 0)
    row = db.scalars(select(CostPurchasedPrice)).one()
    assert row.unit_price_inc_vat == D("922")
    assert row.source == "file"
    assert row.source_file == "ohisell_mapping_template_20260807.xlsx"
    assert row.source_product_name == "일미리 케이스, 아이폰15"
    assert row.approved_at is not None  # 클릭이 곧 확정


def test_confirm_refuses_assembly_even_when_the_screen_sends_it(db):
    """★★이 테스트가 계약 §3 금지선의 집행을 지킨다.

    화면이 조립품 SKU를 보내와도 서버가 다시 물어 거부해야 한다. 이 재검사를 빼고
    「화면이 보낸 목록을 그대로 쓰기」로 바꾸는 변이는, 제안 화면 테스트를 전부 통과한
    채로 필름 단가를 파일 값으로 덮는다.
    """
    film = mk_recipe(db, "필름 2매", lines=3, status="approved")
    mk_sku(db, "SKU-FILM", "필름 2매, 갤럭시탭S10", film, cost_price=D("4254"))

    res = PP.confirm_group(
        db, internal_skus=["SKU-FILM"], price=D("4352.7"), source_file="v3"
    )
    db.flush()

    assert res.written == 0
    assert res.skipped == [("SKU-FILM", PP.REASON_ASSEMBLY)]
    assert db.scalars(select(CostPurchasedPrice)).all() == []


def test_confirm_refuses_placeholder_price(db):
    r = mk_recipe(db, "스마트톡", lines=0)
    mk_sku(db, "S1", "스마트톡, 블랙", r)
    res = PP.confirm_group(db, internal_skus=["S1"], price=D("1"), source_file="f")
    db.flush()
    assert res.written == 0
    assert db.scalars(select(CostPurchasedPrice)).all() == []


def test_confirm_mixed_batch_writes_targets_and_reports_the_rest(db):
    ok = mk_recipe(db, "케이스", lines=0)
    film = mk_recipe(db, "필름", lines=2, status="approved")
    mk_sku(db, "S-OK", "케이스, 아이폰16", ok)
    mk_sku(db, "S-FILM", "필름, 아이폰16", film)

    res = PP.confirm_group(
        db, internal_skus=["S-OK", "S-FILM", "S-없음"], price=D("922"), source_file="f"
    )
    db.flush()

    assert res.written == 1
    assert dict(res.skipped) == {
        "S-FILM": PP.REASON_ASSEMBLY,
        "S-없음": PP.REASON_NO_SKU,
    }


# ── 보드 카운트 — 「보류」와 「아직 안 봤다」는 다른 사실이다 ─────────────────


def test_board_counts_separate_held_blank_from_unconfirmed(db):
    r = mk_recipe(db, "케이스", lines=0)
    film = mk_recipe(db, "필름", lines=2, status="approved")
    mk_sku(db, "A", "케이스, 1", r)
    mk_sku(db, "B", "케이스, 2", r)
    mk_sku(db, "C", "케이스, 3", r)
    mk_sku(db, "F", "필름, 1", film)  # 조립품은 «모수»에 안 든다

    db.add(
        CostPurchasedPrice(
            internal_sku="A",
            unit_price_inc_vat=D("922"),
            source="file",
            approved_at=datetime(2026, 8, 31, 10, 0),
        )
    )
    db.add(  # 사람이 「값이 없다」를 확인한 상태 — 미확인과 다르다
        CostPurchasedPrice(
            internal_sku="B",
            unit_price_inc_vat=None,
            source="manual",
            approved_at=datetime(2026, 8, 31, 10, 0),
        )
    )
    db.flush()

    c = PP.board_counts(db)
    assert c == {
        "candidates": 3,
        "grounded": 1,
        "held_blank": 1,
        "unconfirmed": 1,
    }


def test_unapproved_proposal_row_does_not_count_as_grounded(db):
    """`approved_at IS NULL`은 제안이지 확정이 아니다."""
    r = mk_recipe(db, "케이스", lines=0)
    mk_sku(db, "A", "케이스, 1", r)
    db.add(
        CostPurchasedPrice(
            internal_sku="A", unit_price_inc_vat=D("922"), source="file", approved_at=None
        )
    )
    db.flush()

    assert PP.board_counts(db)["grounded"] == 0
    assert PP.board_counts(db)["unconfirmed"] == 1


def test_confirm_records_that_a_human_did_the_classification(db):
    """★대상 판별을 시스템이 못 한다는 사실이 근거의 일부다.

    prod 실측(2026-08-31): 구성 0줄 레시피에는 매입품과 조립품 필름이 섞여 있고
    `form_factor`가 둘 다 `bar`라 **가르는 DB 신호가 없다**. 그래서 값 옆에 「사람이
    분류했다」가 남아야 한다 — 남지 않으면 나중에 이 값이 시스템 판정처럼 읽힌다.
    """
    r = mk_recipe(db, "일미리 케이스", lines=0)
    mk_sku(db, "S1", "일미리 케이스, 아이폰15", r)

    PP.confirm_group(db, internal_skus=["S1"], price=D("922"), source_file="08-07판")
    db.flush()

    row = db.scalars(select(CostPurchasedPrice)).one()
    assert row.note and "사람" in row.note and "매입품" in row.note


def test_film_draft_with_no_lines_still_reaches_the_human_not_auto_written(db):
    """★★계약 §0-D 최대 사고의 «두 번째 얼굴».

    구성 «있는» 필름은 `REASON_ASSEMBLY`로 빠지지만(위 테스트), 구성이 아직 0줄인 필름
    초안은 빠지지 않는다 — prod의 r99·r11·r83·r66·r36이 그렇다. 그것들이 **자동으로
    써지지 않고 사람 앞 묶음으로 선다**는 것이 이 설계의 안전장치다. 만약 build_proposal이
    이 묶음을 «확정»으로 바꾸는 경로를 갖게 되면 필름 단가가 파일 값으로 덮인다.
    """
    film_draft = mk_recipe(db, "종이질감 저반사 지문방지 액정보호필름 2매", lines=0)
    mk_sku(db, "S-FILM", "종이질감 필름, 갤럭시탭S10", film_draft, cost_price=D("4254"))

    p = PP.build_proposal(
        db, parse_price_sheet(sheet(("종이질감 필름, 갤럭시탭S10", 4352.7))), "v3"
    )

    # 사람 앞에 선다 — 그리고 이름이 묶음의 얼굴이라 사람이 필름인 줄 안다
    assert len(p.groups) == 1
    assert "필름" in p.groups[0].recipe_name
    # 그러나 «아무것도 안 써졌다»
    assert db.scalars(select(CostPurchasedPrice)).all() == []
    assert db.get(ProductMaster, 1).cost_price == D("4254")


# ── 적대 리뷰 P1 회귀 (PR #595 1R) ──────────────────────────────────────────


def test_p1_1_sku_linked_to_both_empty_and_assembly_recipe_is_excluded(db):
    """★P1-1 — 조립품 차단이 «행 순서»에 걸려 있었다.

    한 SKU가 살아 있는 링크를 둘 갖는 것은 스키마가 허용한다(`cost_recipe_link`의 유니크는
    `(internal_sku, recipe_id)`이고 `_sync_links`는 승인 링크를 안 지운다). 초판은
    「먼저 만난 링크가 이긴다」라 **빈 레시피 링크가 먼저 오면 구성 3줄짜리 필름이 대상으로
    섰고**, `confirm_group`의 재검사도 같은 함수를 부르므로 같은 오답을 두 번 냈다.
    """
    empty = mk_recipe(db, "케이스 초안", lines=0)          # id 작음 → 먼저 온다
    film = mk_recipe(db, "유리코팅 필름", lines=3, status="approved")
    db.add(ProductMaster(internal_sku="F1", product_name="필름, 아이폰16",
                         cost_price=D("2616")))
    for r in (empty, film):
        db.add(CostRecipeLink(internal_sku="F1", recipe_id=r.id, status="draft",
                              source="manual"))
    db.flush()

    p = PP.build_proposal(db, parse_price_sheet(sheet(("필름, 아이폰16", 4352.7))), "v3")
    assert p.groups == []
    assert p.excluded[0].excluded_reason == PP.REASON_ASSEMBLY

    res = PP.confirm_group(db, internal_skus=["F1"], price=D("4352.7"), source_file="v3")
    db.flush()
    assert res.written == 0
    assert db.scalars(select(CostPurchasedPrice)).all() == []


def test_p1_1_two_empty_recipes_is_ambiguous_not_a_silent_pick(db):
    """후보 레시피가 둘이면 어느 쪽 단가인지 시스템이 못 고른다 — 고르지 않는다."""
    a = mk_recipe(db, "케이스 A", lines=0)
    b = mk_recipe(db, "케이스 B", lines=0)
    db.add(ProductMaster(internal_sku="X1", product_name="케이스, 아이폰16",
                         cost_price=D("900")))
    for r in (a, b):
        db.add(CostRecipeLink(internal_sku="X1", recipe_id=r.id, status="draft",
                              source="manual"))
    db.flush()

    p = PP.build_proposal(db, parse_price_sheet(sheet(("케이스, 아이폰16", 922))), "f")
    assert p.groups == []
    assert p.excluded[0].excluded_reason == PP.REASON_MULTI_RECIPE


def test_p1_3_duplicate_rows_for_one_sku_collapse_to_one(db):
    """★P1-3 — 「SKU N건」이 실은 «행 수»였다.

    계약 §0-B 실측이 944행 / 고유 상품명 940이라 중복은 실재한다. 접지 않으면 묶음 건수가
    부풀고 확정 시 원장에 같은 SKU의 행이 둘 쌓인다.
    """
    r = mk_recipe(db, "일미리 케이스", lines=0)
    mk_sku(db, "C1", "일미리 케이스, 아이폰15", r, cost_price=D("1000"))

    p = PP.build_proposal(db, parse_price_sheet(sheet(
        ("일미리 케이스, 아이폰15", 922),
        ("일미리 케이스, 아이폰15", 922),
    )), "08-07판")

    assert len(p.groups) == 1
    assert p.groups[0].sku_count == 1
    assert [s.internal_sku for s in p.groups[0].skus] == ["C1"]


def test_p1_3_same_sku_with_two_different_prices_is_a_conflict_not_two_groups(db):
    """단가가 갈리면 시스템이 고르지 않는다 — 「충돌」로 사람 앞에 세운다."""
    r = mk_recipe(db, "일미리 케이스", lines=0)
    mk_sku(db, "C1", "일미리 케이스, 아이폰15", r, cost_price=D("1000"))

    p = PP.build_proposal(db, parse_price_sheet(sheet(
        ("일미리 케이스, 아이폰15", 922),
        ("일미리 케이스, 아이폰15", 2400),
    )), "08-07판")

    assert p.groups == []
    assert p.excluded[0].excluded_reason == PP.REASON_PRICE_CONFLICT
    assert any("서로 다른 단가" in a for a in p.anomalies)


def test_p1_3_confirm_dedupes_repeated_skus(db):
    """화면이 접어서 보내리라 믿지 않는다 — 원장에 행이 둘 쌓이면 안 된다."""
    r = mk_recipe(db, "케이스", lines=0)
    mk_sku(db, "C1", "케이스, 아이폰16", r)

    res = PP.confirm_group(db, internal_skus=["C1", "C1"], price=D("922"),
                           source_file="f")
    db.flush()
    assert res.written == 1
    assert len(db.scalars(select(CostPurchasedPrice)).all()) == 1


def test_p2_4_unapproved_newer_row_does_not_hide_the_approved_one(db):
    """★M15 SURVIVED 회귀 — 승인 행 «위»에 더 최신 미승인 제안이 있어도 배지는 승인 값이다."""
    r = mk_recipe(db, "케이스", lines=0)
    mk_sku(db, "A", "케이스, 1", r)
    db.add(CostPurchasedPrice(internal_sku="A", unit_price_inc_vat=D("922"),
                              source="file", approved_at=datetime(2026, 8, 31, 10, 0),
                              created_at=datetime(2026, 8, 31, 10, 0)))
    db.add(CostPurchasedPrice(internal_sku="A", unit_price_inc_vat=D("999"),
                              source="file", approved_at=None,
                              created_at=datetime(2026, 8, 31, 11, 0)))
    db.flush()

    assert PP._load_approved_prices(db) == {"A": D("922")}
    assert PP.board_counts(db)["grounded"] == 1


def test_p2_1_board_and_badge_read_the_same_latest_price_logic(db):
    """★로직 두 벌 금지(계약 §2) — 같은 데이터에서 답이 갈리면 안 된다."""
    r = mk_recipe(db, "케이스", lines=0)
    mk_sku(db, "A", "케이스, 1", r)
    db.add(CostPurchasedPrice(internal_sku="A", unit_price_inc_vat=None,
                              source="manual", approved_at=datetime(2026, 8, 31, 10, 0)))
    db.flush()

    # 배지용(값 있는 것만) ↔ 보드용(보류 포함)이 같은 사실을 말한다
    assert PP._load_approved_prices(db) == {}
    assert PP.board_counts(db)["held_blank"] == 1
    assert PP.board_counts(db)["grounded"] == 0
