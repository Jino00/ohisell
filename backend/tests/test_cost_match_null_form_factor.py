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

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import CostRecipe, ProductChannelMapping, ProductMaster
from app.services.cost_menu import recipes as R
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


# ═══════════════════════════════════════════════════════════════════
# ★엔드투엔드 — 매칭 결과가 «사람이 읽는 자리»까지 간다
#
# 적대 리뷰 1R P1(2026-09-02): 위 6종은 전부 `_match_draft`를 **직접 호출**하는
# 유닛 테스트라, 그 결과가 `recipe.anomaly_flag`(배지)와 `recipe.note`(사유 문장)를
# 거쳐 화면까지 가는 경로를 **하나도 안 지켰다.** 리뷰어가 넣은 표면 절단 변이 2종이
# 관련 테스트 330개를 전건 초록인 채 통과했다:
#
#   M9   note["match_reason"]을 None으로 고정  → 사유 문장이 화면에서 사라짐
#   M10  `recipe.anomaly_flag = anomaly` 제거   → 매칭돼도 배지가 안 없어짐
#
# ★아프게도 **이 커밋이 고치는 결함이 정확히 그 모양**이다 — 「값은 소수점까지 맞는데
#   아무도 못 보는 자리에 갇혀 있었다」. 값을 만드는 것과 사람이 그걸 보는 것은
#   다른 질문이고, 후자를 재는 테스트는 여기 있어야 한다.
# ═══════════════════════════════════════════════════════════════════

_TARGET = "오하이 하이브리드 맥세이프 소다 케이스"

#: 「케이스」 섹션 — 제목이 곧 헤더이고 **폼팩터가 없다**(이 결함의 무대).
_CASE_ROWS = [
    (None, "케이스", None, "제품원가\n(US$)", "제품원가\n(KRW)", "뮬류비(ea)",
     "관세", "부가세", "상품원가", "부자재"),
    (None, "맥세이프 소다 케이스 포유", None, None, 4202, None, None, None, 3700, 120),
]

_MAPPING_ROWS = [
    ("상품명", "옵션명", "채널명", "카페24 품목코드1"),
    (_TARGET, "아이폰16프로", "자사몰 (cafe24)", "CAFE-CASE-1"),
]


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


def _seed_sku(db, cost_price="4202.0", sku="SKU-CASE-1", code="CAFE-CASE-1"):
    """`cost_price`가 엑셀 제품원가와 **정확히 같은** SKU 하나 — 그런데 못 붙던 자리다.

    ★매핑 정본의 채널코드 → `ProductChannelMapping` → `ProductMaster`로 이어져야
    `resolve_channel_codes`가 `cost_price`를 집어 온다(prod와 같은 다리다).
    """
    pm = ProductMaster(internal_sku=sku, product_name=_TARGET, cost_price=Decimal(cost_price))
    db.add(pm)
    db.flush()
    db.add(ProductChannelMapping(product_id=pm.id, channel_id=1, channel_product_id=code))
    db.flush()
    return sku


def _run(db):
    out = R.import_drafts(db, cost_rows=_CASE_ROWS, mapping_rows=_MAPPING_ROWS)
    db.flush()
    return out


def test_match_reason_reaches_the_recipe_note(db):
    """★사유 문장이 `recipe.note`에 실린다 — 화면이 「왜 붙었나」를 말할 수 있어야 한다."""
    _seed_sku(db)
    _run(db)
    r = db.query(CostRecipe).filter(CostRecipe.product_name == _TARGET).one()
    note = json.loads(r.note or "{}")
    assert note.get("match_reason"), "사유 문장이 비었다 — 화면이 이유를 못 말한다"
    assert "맥세이프 소다 케이스 포유" in note["match_reason"], note["match_reason"]
    assert note.get("cost_table_item") == "맥세이프 소다 케이스 포유"


def test_badge_stops_saying_no_recipe_match(db):
    """★배지가 「연결할 게 없다」에서 **벗어난다** — 그게 이 수리가 바꾸는 화면이다.

    ⚠️매입품(`purchased`)은 매칭돼도 구성이 자동으로 안 붙으므로 배지가 `None`이
    아니라 `needs_manual_lines`가 된다. 그것이 **정확한 다음 문장**이다 —
    「연결할 게 없다」와 「연결됐고 구성만 확인하면 된다」는 사람에게 완전히 다른 말이다.
    `None`을 기대하면 이 테스트는 설계를 오해한 채 초록/빨강을 오간다.
    """
    _seed_sku(db)
    _run(db)
    r = db.query(CostRecipe).filter(CostRecipe.product_name == _TARGET).one()
    assert r.anomaly_flag != "no_recipe_match", "매칭됐는데 배지가 그대로다"
    assert r.anomaly_flag == "needs_manual_lines", r.anomaly_flag
    assert r.recipe_kind == "purchased"


def test_every_null_bucket_item_still_needs_a_human_pick(db):
    """★NULL 칸 품목은 **전부** 매입·수입품이라 매칭돼도 구성이 자동으로 안 붙는다.

    실측(2026-09-02 prod): `form_factor IS NULL`인 원가표 품목 16건 중
    `purchased` 11 · `imported_goods` 5 — **`assembly`는 0건**이다.
    ⇒ 이 수리가 여는 것은 「계산값이 선다」가 아니라 **「연결할 게 없다 → 연결됐고
    구성만 확인하면 된다」**이고, 그 다음 한 걸음은 사람의 픽이다.
    이 사실을 테스트가 말하지 않으면 다음 사람이 「매칭되면 계산된다」로 오해한다.
    """
    _seed_sku(db)
    _run(db)
    r = db.query(CostRecipe).filter(CostRecipe.product_name == _TARGET).one()
    assert r.recipe_kind == "purchased"
    assert r.anomaly_flag == "needs_manual_lines"
    assert len(r.lines) == 0, "매입품인데 구성이 자동으로 붙었다 — 픽 게이트가 열렸다"


def test_without_the_fix_the_badge_and_reason_say_it_did_not_match(db):
    """★반대 방향 — 값이 안 맞으면 배지·사유가 «못 붙었다»고 말한다.

    이 단언이 있어야 위 셋이 「항상 초록인 테스트」가 아님이 증명된다.
    """
    _seed_sku(db, cost_price="99999.0", sku="SKU-CASE-X")
    _run(db)
    r = db.query(CostRecipe).filter(CostRecipe.product_name == _TARGET).one()
    note = json.loads(r.note or "{}")
    assert r.anomaly_flag == "no_recipe_match"
    assert "없다" in (note.get("match_reason") or "")
    assert note.get("cost_table_item") is None
    assert len(r.lines) == 0


def test_candidate_list_only_carries_items_inside_the_tolerance(db):
    """★사람이 고를 목록에 «허용오차 밖» 품목이 섞이면 안 된다 (리뷰 P2 M11).

    후보를 넓힌 이번 변경 때문에 이 목록의 폭발반경도 같이 커졌다.
    """
    m = _match_draft(
        form_factor="bar",
        cost_prices=[Decimal("4202.0")] * 3,
        drafts_by_form=_pool(CASE, _draft("전혀 다른 값", None, "1.0", row=2)),
        sku_count=3,
    )
    assert m.draft is not None
    assert m.candidates == ["케이스/맥세이프 소다 케이스 포유"], m.candidates


def test_pool_merge_does_not_mutate_the_shared_bucket(db):
    """★`pool +=` 앨리어싱 방지 (리뷰 P2 M6) — 칸을 제자리에서 늘리면 호출마다 부푼다.

    실제 루프는 같은 `drafts_by_form`을 여러 그룹에 재사용하므로, 한 번 오염되면
    다음 그룹의 후보가 중복돼 **유일 매칭이 조용히 「후보 여럿」으로 뒤집힌다.**
    """
    pool = _pool(CASE, FILM)
    before = {k: list(v) for k, v in pool.items()}
    for _ in range(3):
        _match_draft(
            form_factor="bar",
            cost_prices=[Decimal("4202.0")] * 2,
            drafts_by_form=pool,
            sku_count=2,
        )
    assert {k: list(v) for k, v in pool.items()} == before, "공유 칸이 오염됐다"
    # 그리고 반복 호출해도 결과가 같아야 한다(부풀면 「후보 여럿」으로 뒤집힌다).
    m = _match_draft(form_factor="bar", cost_prices=[Decimal("4202.0")] * 2,
                     drafts_by_form=pool, sku_count=2)
    assert m.draft is not None and m.draft.item_name == "맥세이프 소다 케이스 포유"
