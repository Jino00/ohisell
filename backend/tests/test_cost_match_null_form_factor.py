# test_cost_match_null_form_factor.py — 폼팩터 «없는» 품목이 후보에 드는가 (D-CPP-64, 2026-09-02)
#
# ## 왜 이 파일이 생겼나
#
# `_match_draft`가 원가표 품목을 `form_factor` 칸에 넣고 **그 칸만** 뒤졌다. 그런데
# 케이스·그립톡·거치대·셀카봉·맥탭 카드케이스는 **폼팩터가 없는 물건**이라 `None` 칸에
# 들어가고, 그 제품들의 레시피는 옵션명에서 `bar`로 뽑힌다 ⇒ **두 칸이 영영 안 만난다.**
#
# 증상은 「값이 안 맞아서 매칭이 안 된다」가 아니었다 — **소수점까지 같은데도 0건**이었다:
#
#   r94  cost_price 2,222.0  ↔  「일반 소다 케이스 포유」      2,222.0   (SKU 39)
#   r93  cost_price 4,202.0  ↔  「맥세이프 소다 케이스 포유」  4,202.0   (SKU 32)
#   r36  cost_price 2,836.0  ↔  「사생활 지문방지 2매」        2,836.0   (SKU 15)
#
# ★그리고 하루 전 파서 수정으로 되살린 8행이 **바로 이 NULL 칸**에 있었다. 살려 놓고
#   아무도 못 보는 자리에 둔 셈이라, 그 수리의 효과가 통째로 잠겨 있었다.
#
# ## 이 파일이 지키는 것
#
# 후보를 «넓히는» 변경이라 진짜 위험은 **조용한 오매칭**이다. 그래서 넓힌 것만큼
# **판정이 안 느슨해졌는지**를 같이 지킨다 — `len(hits) == 1`일 때만 자동 연결이고,
# 둘 이상이면 종전대로 사람에게 넘어간다.
from __future__ import annotations

from decimal import Decimal

from app.services.cost_menu.recipe_parser import RecipeDraft
from app.services.cost_menu.recipes import _match_draft


def _draft(name, ff, total, *, section="케이스", kind="purchased", row=1):
    return RecipeDraft(
        section=section,
        item_name=name,
        form_factor=ff,
        recipe_kind=kind,
        lines=(),
        excel_total_inc_vat=Decimal(str(total)),
        row_number=row,
    )


def _pool(*drafts):
    out: dict = {}
    for d in drafts:
        out.setdefault(d.form_factor, []).append(d)
    return out


CASE = _draft("맥세이프 소다 케이스 포유", None, "4202.0")          # 폼팩터 없음
FILM = _draft("자가복원 고투명 EPU 3매", "bar", "3309.7",
              section="모바일 필름-아이폰,갤럭시", kind="assembly", row=26)


# ═══════════════════════════════════════════════════════════════════
# 이 변경의 본체 — NULL 칸이 후보에 든다
# ═══════════════════════════════════════════════════════════════════


def test_bar_recipe_matches_a_item_that_has_no_form_factor():
    """★`bar` 레시피가 폼팩터 없는 케이스 품목과 만난다 — 이게 막혀 있던 자리다."""
    m = _match_draft(
        form_factor="bar",
        cost_prices=[Decimal("4202.0")] * 32,
        drafts_by_form=_pool(CASE),
        sku_count=32,
    )
    assert m.draft is not None, "폼팩터 없는 품목이 후보에서 빠졌다"
    assert m.draft.item_name == "맥세이프 소다 케이스 포유"


def test_own_form_factor_bucket_still_wins_and_is_not_lost():
    """자기 칸도 그대로 본다(이 변경의 «안 건드린다» 쪽)."""
    m = _match_draft(
        form_factor="bar",
        cost_prices=[Decimal("3309.7")] * 5,
        drafts_by_form=_pool(FILM, CASE),
        sku_count=5,
    )
    assert m.draft is not None
    assert m.draft.item_name == "자가복원 고투명 EPU 3매"


def test_other_form_factor_bucket_is_still_not_borrowed():
    """★넓힌 것은 «NULL 칸»뿐이다 — `fold` 품목이 `bar` 레시피에 붙으면 안 된다.

    이걸 안 지키면 「후보를 넓혔다」가 「아무거나 붙인다」가 된다.
    """
    fold_item = _draft("지문방지_내부3매+외부3매", "fold", "6220.3",
                       section="모바일 필름-폴드", kind="assembly", row=44)
    m = _match_draft(
        form_factor="bar",
        cost_prices=[Decimal("6220.3")] * 3,
        drafts_by_form=_pool(fold_item),
        sku_count=3,
    )
    assert m.draft is None, "다른 폼팩터 칸을 빌려 왔다"
    assert "없다" in m.reason


# ═══════════════════════════════════════════════════════════════════
# ★후보를 넓혔어도 «판정»은 안 느슨해진다 — 조용한 오매칭 방지
# ═══════════════════════════════════════════════════════════════════


def test_two_candidates_across_the_two_buckets_go_to_a_human():
    """★자기 칸 1건 + NULL 칸 1건이 같은 값이면 **사람이 고른다** — 시스템이 안 고른다.

    후보를 넓히는 변경의 진짜 위험이 여기다. 넓힌 만큼 「하나만 걸릴 때만 붙인다」가
    지켜져야 하고, 안 지켜지면 조용한 오매칭이 된다.
    """
    same_value_bar = _draft("우연히 같은 값", "bar", "4202.0",
                            section="모바일 필름-아이폰,갤럭시", kind="assembly", row=9)
    m = _match_draft(
        form_factor="bar",
        cost_prices=[Decimal("4202.0")] * 10,
        drafts_by_form=_pool(CASE, same_value_bar),
        sku_count=10,
    )
    assert m.draft is None, "후보가 둘인데 시스템이 하나를 골랐다"
    assert "사람이 골라야" in m.reason
    assert len(m.candidates) == 2


def test_no_candidate_still_says_so():
    """값이 어디에도 없으면 종전대로 「없다」고 말한다."""
    m = _match_draft(
        form_factor="bar",
        cost_prices=[Decimal("99999.0")] * 3,
        drafts_by_form=_pool(CASE, FILM),
        sku_count=3,
    )
    assert m.draft is None
    assert "없다" in m.reason


def test_null_form_factor_recipe_does_not_double_count_its_own_bucket():
    """★폼팩터가 없는 «레시피»는 NULL 칸을 두 번 담지 않는다.

    합치는 코드가 조건 없이 더하면 같은 품목이 후보에 두 번 들어가 `len(hits) == 2`가 되고,
    **유일 매칭이던 것이 조용히 「후보 여럿」으로 뒤집힌다** — 고치려던 것과 정반대다.
    """
    m = _match_draft(
        form_factor=None,
        cost_prices=[Decimal("4202.0")] * 4,
        drafts_by_form=_pool(CASE),
        sku_count=4,
    )
    assert m.draft is not None, "NULL 칸이 중복돼 유일 매칭이 깨졌다"
    assert m.draft.item_name == "맥세이프 소다 케이스 포유"
