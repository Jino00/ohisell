"""계약 D-CPP-62 S1 — 거짓 경고 제거 + 참고값 신선화.

## 이 파일이 재는 것

두 결함이 **같은 병의 두 얼굴**이다: 백엔드가 아는 사실이 화면에 안 닿는 것.

1. **거짓 「단가 없음」** — 표준원가는 `unit_price_ex_vat`를 읽고 없으면 ×1.1로 만들어 쓰는데,
   부자재 목록의 「현재 단가」는 `unit_price_inc_vat` 칸**만** 읽었다. 수동 입력은 사람이 준
   칸만 채우므로(의도된 설계), 「부가세 제외」로 넣은 단가가 목록에서 「단가 없음」으로
   떴다 — 최초 실측 17종, 이 세션이 6건을 더 넣은 뒤 **종 18개 · 단가 행 23개** — **값은 원가에 정상으로 들어가 있는데도.** prod 실증: `패키지 (flip)`이
   `ex=171 · inc=NULL`인데 레시피 34 breakdown엔 **188.10**으로 실려 있었다.
2. **얼어붙은 참고값** — `excel_ref_price`를 「비어 있을 때만」 채워, 첫 업로드 값이 영원히
   남았다. prod 실증 10종(`패키지` 98인데 파일은 171 등). 그리고 **「채택」이 그 낡은 값을
   단가로 굳혔다** — 그게 이 결함이 돈에 닿는 경로다.

## 단언의 성격

「함수가 값을 만드나」가 아니라 **「payload가 화면에 그 사실을 실어 보내나」**를 잰다.
그리고 **행위 불변**을 함께 잰다 — 규칙을 한 곳으로 모으는 리팩터가 계산 결과를 바꾸면
그건 고친 게 아니라 새로 깬 것이다(`test_derivation_refactor_does_not_change_amounts`).

## 변이 시험 (이 파일이 죽어야 하는 지점)

- `SC.resolve_inc_vat`이 `_round`한 값을 돌려주게 하면 → 금액 단언이 죽는다(이중 반올림).
- `material_payload`가 다시 `latest.unit_price_inc_vat`만 읽게 하면 → 파생 단언이 죽는다.
- `_upsert_materials`가 `setdefault`로 돌아가면 → 모순 보류 단언이 죽는다.
- **화면 배지**(`×1.1 파생`)를 지우는 변이는 여기서 못 잡는다 — `costMaterialsSurface.test.tsx`가 잡는다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import CostMaterial, CostMaterialPrice
from app.services.cost_menu import materials as M
from app.services.cost_menu import recipes as R
from app.services.cost_menu import standard_cost as SC


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    # ★prod 세션과 같은 설정 — `autoflush=True`면 「방금 만든 행이 안 보이는」 결함을 못 잡는다.
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
    try:
        yield db
    finally:
        db.close()


# ──────────────────────────────────────────────
# 1. 규칙 한 벌 — `resolve_inc_vat`
# ──────────────────────────────────────────────


def test_stored_inc_wins_and_is_not_marked_derived():
    inc, derived = SC.resolve_inc_vat(Decimal("100"), Decimal("107"))
    assert inc == Decimal("107")
    assert derived is False


def test_missing_inc_is_derived_from_ex():
    inc, derived = SC.resolve_inc_vat(Decimal("171"), None)
    assert inc == Decimal("188.10")
    assert derived is True


def test_no_price_at_all_is_none_not_zero():
    """★「없음」과 「0」을 같게 만들지 않는다(계약 금지선)."""

    inc, derived = SC.resolve_inc_vat(None, None)
    assert inc is None
    assert derived is False


def test_resolve_does_not_round_so_callers_can_round_last():
    """★★행위 불변의 핵심 — 여기서 반올림하면 `_round(inc × 수량)`이 이중 반올림이 된다.

    `166.35 × 1.1 = 182.985`. 먼저 반올림하면 182.99가 되고, 수량 3에서 결과가 갈린다.
    """

    inc, _ = SC.resolve_inc_vat(Decimal("166.35"), None)
    assert inc == Decimal("182.985"), "반올림하지 않은 값이어야 한다"


def test_derivation_refactor_does_not_change_amounts():
    """★★규칙을 한 곳으로 모은 것이 **계산 결과를 바꾸지 않았다**는 실증.

    이 값(548.96)은 리팩터 «전» 코드가 내던 값이다: `_round(166.35 × 1.1 × 3)`.
    `resolve_inc_vat`이 먼저 반올림하면 `_round(182.99 × 3) = 548.97`이 되어 이 단언이 죽는다.
    """

    result = SC.compute_standard_cost(
        [
            SC.RecipeLineInput(
                label="cleaning kit",
                quantity=Decimal("3"),
                unit_price_ex_vat=Decimal("166.35"),
                unit_price_inc_vat=None,
                price_status="ok",
            )
        ]
    )
    line = result.lines[0]
    assert line.inc_derived is True
    assert line.unit_price_inc_vat == Decimal("182.99")  # 단가는 반올림해 보인다
    assert line.amount_inc_vat == Decimal("548.96")  # 금액은 «마지막에» 반올림한다


# ──────────────────────────────────────────────
# 2. 화면에 닿는 층 — `material_payload`
# ──────────────────────────────────────────────


def _material(db, *, ex, inc, name="패키지 (flip)"):
    """★**DB를 거쳐 다시 읽는다** — 픽스처가 prod와 같아야 한다.

    in-memory `Decimal("171")`은 str하면 `"171"`인데 prod는 `Numeric(14,2)`라 `"171.00"`이다.
    거치지 않으면 이 파일은 **화면이 실제로 받는 문자열을 못 잰다** — 자릿수가 갈리는
    결함(파생값이 `188.100`으로 뜨던 것)이 정확히 그 틈에서 나왔다.
    """

    m = CostMaterial(name=name, status="approved", category="부자재")
    m.prices.append(
        CostMaterialPrice(
            source="manual",
            unit_price_ex_vat=ex,
            unit_price_inc_vat=inc,
            effective_date=date(2026, 1, 1),
        )
    )
    db.add(m)
    db.commit()
    db.expire_all()
    return db.query(CostMaterial).filter(CostMaterial.name == name).one()


def test_ex_only_price_is_shown_as_a_value_not_as_missing(db_session):
    """★★구판이 「단가 없음」이라 거짓말하던 바로 그 자리.

    prod의 파생 종 18개(단가 행 23개)가 전부 이 모양이다 — `ex`만 있고 `inc`는 NULL.
    그리고 그 18종은 **출처가 100% `manual`**이다(적대 리뷰 MF3이 픽스처의 `ledger` 편향을 잡았다).
    """

    m = _material(db_session, ex=Decimal("171"), inc=None)
    payload = M.material_payload(m, list(m.prices), [], "latest")

    assert payload["latest_price_inc_vat"] == "188.10", "화면이 값을 받아야 한다"
    assert payload["latest_price_inc_derived"] is True
    assert payload["latest_price_ex_vat"] == "171.00"


def test_stored_inc_is_not_labelled_derived(db_session):
    """★파생 표시가 «항상 켜지는» 장식이 되면 이 단언이 죽는다."""

    m = _material(db_session, ex=Decimal("100"), inc=Decimal("107"))
    payload = M.material_payload(m, list(m.prices), [], "latest")

    assert payload["latest_price_inc_vat"] == "107.00"
    assert payload["latest_price_inc_derived"] is False


def test_material_with_no_price_still_says_nothing_not_zero(db_session):
    m = CostMaterial(name="아직 단가 없는 종", status="approved", category="부자재")
    db_session.add(m)
    db_session.flush()

    payload = M.material_payload(m, [], [], "latest")
    assert payload["latest_price_inc_vat"] is None
    assert payload["latest_price_inc_derived"] is False


# ──────────────────────────────────────────────
# 3. 참고값 신선화 — 재업로드
# ──────────────────────────────────────────────

_FLIP_HEADER = (
    None, "모바일 필름-플립", None, "제품원가\n(+VAT)", "내부\n매입", "내부\n필름",
    "내부\n필름*매입", "외부\n매입", "외부\n필름", "외부\n필름*매입",
    "부착\n안내문", "스퀴즈\n6.5cm", "부자재\n(밀대외)", "알콜솜\n2EA",
    "비닐\n(9*18)", "비닐\n(12*22+4)", "패키지", "폼텍\n스티커",
)


def _rows(package_price, *, item_name="지문방지_내부3매+외부3매", total=3480.4, inner_film=600):
    """`parse_cost_table`이 먹는 최소 시트.

    ★`inner_film`(내부 필름 단가)을 따로 뺀 이유: **빈 칸 규칙은 «필름 라인»에서만 재진다.**
    비-필름 부자재는 값이 비면 `recipe_parser`가 라인 자체를 안 만들어(`recipe_parser.py:407`)
    `_upsert_materials`의 `if ref is not None` 가드에 **애초에 닿지 않는다.** 적대 리뷰 변이
    MB3이 그걸 잡았다 — 그 가드를 지워도 초판 테스트가 통과했다. 이름은 「빈 칸」인데 코드는
    빈 칸을 안 지나던 것이다.
    """

    return [
        (None, "*원가표_26년") + (None,) * 16,
        (None,) * 18,
        _FLIP_HEADER,
        (
            None, item_name, None, total, 3, inner_film, 1800, 3, 350, 1050,
            30, 80, 22, 60, 8, 10, package_price, 6,
        ),
    ]


def _film(db):
    return (
        db.query(CostMaterial)
        .filter(CostMaterial.name == "지문방지_내부3매+외부3매 · 내부 필름 (flip · 내부)")
        .one()
    )


def _reimport(db, rows):
    out = R.import_drafts(db, cost_rows=rows)
    db.commit()
    return out


def _package(db):
    return (
        db.query(CostMaterial)
        .filter(CostMaterial.name == "패키지 (flip)")
        .one()
    )


def test_reupload_refreshes_a_stale_reference_price(db_session):
    """★★prod의 10종이 얼어 있던 이유 — 구판은 여기서 아무것도 안 했다.

    변이 시험: `_upsert_materials`를 `elif m.excel_ref_price is None`으로 되돌리면 죽는다.
    """

    _reimport(db_session, _rows(98))
    assert _package(db_session).excel_ref_price == Decimal("98.00")

    out = _reimport(db_session, _rows(171))

    db_session.expire_all()
    assert _package(db_session).excel_ref_price == Decimal("171.00")
    # ★조용히 바뀌지 않는다 — 무엇이 바뀌었는지 응답이 말한다.
    refreshed = {r["name"]: r for r in out["material_refs"]["refreshed"]}
    assert refreshed["패키지 (flip)"]["old"] == "98.00"
    assert refreshed["패키지 (flip)"]["new"] == "171.00"


def test_unchanged_reference_price_is_not_reported_as_a_change(db_session):
    _reimport(db_session, _rows(171))
    out = _reimport(db_session, _rows(171))
    assert out["material_refs"]["refreshed_count"] == 0


def test_refresh_never_touches_the_unit_price(db_session):
    """★금지선 — 이 경로는 참고값(파일 미러)만 만지고 **단가는 절대 안 건드린다**."""

    _reimport(db_session, _rows(98))
    m = _package(db_session)
    m.prices.append(
        CostMaterialPrice(
            source="manual",
            unit_price_ex_vat=Decimal("171"),
            effective_date=date(2026, 8, 25),
        )
    )
    db_session.commit()

    _reimport(db_session, _rows(9999))

    db_session.expire_all()
    m = _package(db_session)
    assert len(m.prices) == 1
    assert m.prices[0].unit_price_ex_vat == Decimal("171.00"), "단가는 그대로여야 한다"
    assert m.excel_ref_price == Decimal("9999.00"), "참고값만 바뀐다"


def test_file_that_says_two_values_for_one_material_is_not_chosen_silently(db_session):
    """★★2026-08-27 태블릿 사고의 모양 — 중복 블록에서 «먼저 나온 것»이 조용히 이겼다.

    같은 규칙에서 하드보드지는 맞고 필름 단가는 틀렸다 — 맞은 쪽은 판단이 아니라 우연이었다.
    변이 시험: `seen`을 `wanted.setdefault`로 되돌리면 조용히 98이 이기고 이 단언이 죽는다.
    """

    # ★★씨앗을 **모순 후보 어느 쪽과도 다른 값**으로 둔다. 초판은 씨앗을 98로 뒀는데,
    #   그러면 「먼저 나온 값을 쓴다」 변이가 고르는 값(98)과 기존 값이 같아 **결과가 구별되지
    #   않았다** — 변이 M3이 실제로 SURVIVED 했다. 테스트가 초록인데 아무것도 안 지키던 것이다.
    _reimport(db_session, _rows(50))
    assert _package(db_session).excel_ref_price == Decimal("50.00")

    two_blocks = _rows(98) + _rows(171, item_name="지문방지_내부2매+외부2매", total=2667.5)[2:]
    out = _reimport(db_session, two_blocks)

    db_session.expire_all()
    # 어느 쪽도 안 고른다 ⇒ 옛 값이 그대로 남는다. 98이나 171이 되면 시스템이 «고른» 것이다.
    assert _package(db_session).excel_ref_price == Decimal("50.00"), "아무것도 안 고른다"
    conflicted = {c["name"]: c for c in out["material_refs"]["conflicted"]}
    assert "패키지 (flip)" in conflicted
    assert conflicted["패키지 (flip)"]["values"] == ["171.00", "98.00"]
    assert conflicted["패키지 (flip)"]["kept"] == "50.00"
    assert out["material_refs"]["refreshed_count"] == 0


def test_a_blank_cell_is_silence_not_a_conflicting_value(db_session):
    """★빈 칸은 «말 안 함»이지 «다른 값»이 아니다 — **필름 라인에서 잰다.**

    실증: 08-27 이전 파일의 태블릿 두 블록에서 `하드보드지`는 한쪽만 228이고 다른 쪽은 빈
    칸이었다 — Jino 확인 결과 **누락이지 모순이 아니었다**. 빈 칸을 모순으로 세면 그 종은
    영영 갱신되지 않는다.

    ★초판은 «패키지»(비-필름) 칸을 비워 이 규칙을 재려 했는데, 파서가 그런 라인을 아예
    안 만들어 **가드에 닿지 않았다**(적대 리뷰 MB3 SURVIVED). 필름 라인으로 옮겨 실제로
    `ref is None`이 도달하게 한다.
    """

    _reimport(db_session, _rows(98))
    assert _film(db_session).excel_ref_price == Decimal("600.00")

    # ★두 블록의 **품목 이름이 같아야** 같은 종을 가리킨다 — 이름이 다르면 필름 종도 갈려
    #   (`… · 내부 필름 (flip · 내부)`가 품목명을 앞에 달고 만들어진다) 빈 칸 규칙을 못 잰다.
    #   초판이 그 함정에 빠져 «다른 종»을 재고 있었다.
    blank_and_value = _rows(98, inner_film=None) + _rows(98, inner_film=650)[2:]
    out = _reimport(db_session, blank_and_value)

    db_session.expire_all()
    assert _film(db_session).excel_ref_price == Decimal("650.00"), "빈 칸이 갱신을 막으면 안 된다"
    assert out["material_refs"]["conflicted_count"] == 0


def test_a_blank_film_cell_does_not_erase_an_existing_reference_price(db_session):
    """★★빈 칸이 **값을 지우면** 안 된다 — 「파일에서 사라짐」은 계약 §4 S4의 확인 화면 몫이다.

    적대 리뷰 변이 MB2가 이 자리를 잡았다: 갱신 가드를 `ref_price is None` → `len(values) > 1`로
    바꾸면 **필름 참고값이 `600.00 → None`으로 조용히 지워지는데 185건이 전부 초록**이었다.
    조용한 소실은 미달이다.
    """

    _reimport(db_session, _rows(98))
    assert _film(db_session).excel_ref_price == Decimal("600.00")

    out = _reimport(db_session, _rows(98, inner_film=None))

    db_session.expire_all()
    assert _film(db_session).excel_ref_price == Decimal("600.00"), "빈 칸이 값을 지우면 안 된다"
    assert out["material_refs"]["refreshed_count"] == 0


def test_a_material_missing_from_the_file_keeps_its_reference_price(db_session):
    """★파일에서 사라진 것을 조용히 지우지 않는다 — 「사라짐」은 S4의 확인 화면 몫이다."""

    _reimport(db_session, _rows(171))
    assert _package(db_session).excel_ref_price == Decimal("171.00")

    # 패키지 열이 없는 시트(버디 계열 모양) — 그 종의 라인이 아예 안 나온다
    buddy = [
        (None, "*원가표_26년") + (None,) * 10,
        (None,) * 11,
        (None, "버디필름", None, "제품원가\n(+VAT)", "매입", "필름", "필름*매입",
         "부착\n안내문", "비닐\n(9*18)", "비닐\n(12*22+4)", "패키지"),
        (None, "10매", None, 2230.8, 10, 180, 1800, 30, 8, 13, 171),
    ]
    _reimport(db_session, buddy)

    db_session.expire_all()
    assert _package(db_session).excel_ref_price == Decimal("171.00"), "지우지 않는다"
