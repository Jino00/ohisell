# test_cost_menu_matcher.py — 원장 품목명 ↔ 부자재 종 매칭, 순수 SA (D-CPP-53 / 계약 A′ §5-2)
#
# ★이 파일이 지키는 것은 «매칭이 되는가»가 아니라 **«자동으로 확정하지 않는가»**다.
#   계약 §2-2·§5-2: 파서의 품목명 규칙은 **제안까지**고 확정은 사람이다. 추론을 확인분과
#   동일시한 것이 08-10 71건 사고이고(교훈 #204), 그 병이 이 층에서 재발하는 경로는
#   「후보가 여럿인데 하나를 골라 버린다」와 「빈 규칙이 전건을 잡는다」 둘이다.
from __future__ import annotations

from app.services.cost_menu.matcher import (
    LedgerItem,
    MaterialRule,
    matches,
    normalize,
    suggest,
    suggest_one,
)

KIT = MaterialRule(material_id=1, name="cleaning kit", match_rule="cleaning kit")
WIPE = MaterialRule(material_id=2, name="알콜솜", match_rule="wipe alcohol")


def test_normalize_folds_case_and_whitespace():
    assert normalize("  Cleaning   KITS ") == "cleaning kits"


def test_prod_real_item_name_matches_kit_rule():
    """실측(2026-08-22 prod): 원장 부자재 라인은 `cleaning kits` 2건이고 규칙이 둘 다 잡는다.

    복수형 `kits`는 규칙 `cleaning kit`의 부분 문자열이라 별도 처리가 필요 없다.
    """
    assert matches("cleaning kits", KIT) is True


def test_all_tokens_must_be_present():
    """토큰이 **전부** 있어야 후보다 — 하나만 겹치는 것은 매칭이 아니다."""
    assert matches("alcohol pad", WIPE) is False   # wipe 없음
    assert matches("alcohol wipe pad", WIPE) is True


def test_empty_rule_does_not_match_everything():
    """★빈 규칙이 전건을 잡으면 그 순간 전건이 «자동 매칭»된다 — 이 함수가 막는 유일한 사고."""
    empty = MaterialRule(material_id=9, name="   ", match_rule="   ")
    assert empty.tokens == ()
    assert matches("cleaning kits", empty) is False
    assert matches("", empty) is False


def test_rule_falls_back_to_material_name():
    """규칙이 비면 종 이름 자체가 규칙이다(계약 §5-1 ★원단 결정 ③ — 이름이 매칭이 닿는 자리)."""
    no_rule = MaterialRule(material_id=3, name="cleaning kit", match_rule=None)
    assert no_rule.tokens == ("cleaning", "kit")
    assert matches("cleaning kits", no_rule) is True


def test_single_hit_is_a_suggestion_with_a_reason():
    s = suggest_one(LedgerItem(line_id=15, item_name="cleaning kits"), [KIT, WIPE])
    assert s.material_id == 1
    assert s.candidates == (1,)
    assert s.is_ambiguous is False and s.is_unmatched is False
    # ★이유를 «말한다» — 사람이 확정을 판단할 근거가 화면에 있어야 승인이 도장 찍기가 안 된다.
    assert "cleaning kit" in s.reason


def test_ambiguous_never_picks_one():
    """★후보 2종이면 **고르지 않는다.** 최고점을 뽑는 순간 그게 자동 확정이다."""
    dup = MaterialRule(material_id=7, name="cleaning kit (구형)", match_rule="cleaning")
    s = suggest_one(LedgerItem(line_id=15, item_name="cleaning kits"), [KIT, dup])
    assert s.material_id is None
    assert s.is_ambiguous is True
    assert set(s.candidates) == {1, 7}
    assert "사람이 확정" in s.reason


def test_unmatched_says_so_instead_of_being_silent():
    """★발견 0건과 실행 안 됨이 같은 숫자로 보이면 안 된다(교훈 #123)."""
    s = suggest_one(LedgerItem(line_id=99, item_name="Glass_Ip17Pro"), [KIT, WIPE])
    assert s.material_id is None
    assert s.is_unmatched is True
    assert "미매칭" in s.reason


def test_suggest_preserves_input_order_and_covers_every_line():
    items = [
        LedgerItem(line_id=15, item_name="cleaning kits"),
        LedgerItem(line_id=17, item_name="cleaning kits"),
        LedgerItem(line_id=99, item_name="뭔가 다른 것"),
    ]
    out = suggest(items, [KIT])
    assert [s.line_id for s in out] == [15, 17, 99]
    assert [s.material_id for s in out] == [1, 1, None]


def test_matcher_module_is_pure():
    """★DB·IO를 임포트하지 않는다 — 산술·판단이 두 벌 생기지 않게 하는 구조 조건(계약 §2-6).

    (`allocator.py`와 같은 규약. 여기서 sqlalchemy가 새어 들어오면 라우터·테스트가 임포트하는
    «한 벌»이라는 전제가 깨진다.)
    """
    import app.services.cost_menu.matcher as mod

    src = open(mod.__file__, encoding="utf-8").read()
    for banned in ("sqlalchemy", "app.models", "app.database", "requests", "open("):
        assert banned not in src, banned
