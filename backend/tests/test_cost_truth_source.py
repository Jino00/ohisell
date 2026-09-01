# test_cost_truth_source.py — 계약 D-CPP-64 §4 S2 (정본 판별층)
#
# ## 이 파일이 지키는 것 셋 (합격기준과 1:1)
#
#   S2-① 963 전 SKU의 정본이 한 표에 선다 — 유형·정본값·현재값·격차가 **HTTP body에** 실린다
#   S2-② 보류는 사유와 «소관»을 말한다   — 빈 칸도 0도 아니다
#   S2-③ 정본 없음이 소관별로 갈라 보인다 — 매입가가 승인되면 **자동으로 승격**된다
#
# ★**HTTP body를 단언한다.** 서비스층 dict만 보면 라우터가 키를 지우는 사고를 못 잡는다
#   (교훈 #321 — 서비스층 9건 초록인데 화면엔 배너가 통째로 안 떴다).
# ★**표면 절단 변이를 상정하고 쓴다**: 라우터가 서비스를 안 부르면 · `census`가 빠지면 ·
#   `reason`/`owner`가 빈 문자열이 되면 · 보류의 정본값이 0으로 채워지면 여기서 죽어야 한다.
# ★분류 규칙의 기대값은 ref 118 §3의 실측 분해다 — 그 표를 재현 못 하면 이 층은 쓸모가 없다.
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    CostPurchasedPrice,
    CostRecipe,
    CostRecipeLine,
    CostRecipeLink,
    CostStandard,
    ProductMaster,
)
from app.services.cost_menu import truth_source as TS

RULE = "latest"


def _breakdown(n: int) -> str:
    """`cost_standard.breakdown` — 라인 수만 의미가 있다(ref 118 §6의 json_each와 같은 수)."""
    return json.dumps([{"label": f"line{i}", "amount_inc_vat": "1"} for i in range(n)])


def _seed(
    s,
    *,
    recipe_id: int,
    name: str,
    form: str | None,
    kind: str,
    status: str,
    calc: str | None,
    lines: int,
    skus: list[tuple[str, str]],
):
    """레시피 1건 + 링크 + 계산값 + SKU들을 한 번에 심는다. `skus` = [(sku, cost_price)]"""
    s.add(
        CostRecipe(
            id=recipe_id,
            product_name=name,
            form_factor=form,
            status=status,
            recipe_kind=kind,
        )
    )
    if calc is not None:
        s.add(
            CostStandard(
                recipe_id=recipe_id,
                price_rule=RULE,
                std_cost_inc_vat=D(calc),
                std_cost_ex_vat=D(calc),
                breakdown=_breakdown(lines),
            )
        )
    for sku, cp in skus:
        s.add(ProductMaster(internal_sku=sku, product_name=f"{name} · {sku}", cost_price=D(cp)))
        s.add(
            CostRecipeLink(
                internal_sku=sku,
                recipe_id=recipe_id,
                status="approved" if status == "approved" else "draft",
            )
        )


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    # ★prod 세션과 같은 설정(autoflush=False) — 픽스처가 prod와 다르면 결함을 못 잡는다.
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
        # G2 — 격차가 정확히 299.0 (ref 118 §3-1). 계산이 정본.
        _seed(s, recipe_id=50, name="자가복원 EPU 3매", form="bar", kind="assembly",
              status="approved", calc="3309.7", lines=9,
              skus=[("OHI-G2-1", "3010.7"), ("OHI-G2-2", "3010.7")])
        # G3-2 — r50의 형제. 현재값은 같은데(3010.7) 계산값이 다르다 ⇒ 계열 미분리.
        _seed(s, recipe_id=52, name="자가복원 EPU 3매", form="fold", kind="assembly",
              status="approved", calc="7870.3", lines=10,
              skus=[("OHI-G32-1", "3010.7")])
        # 형제인데 «계산값까지 같은» 짝 — G3-2가 아니다(r44·r45 힌지 필름의 모양).
        _seed(s, recipe_id=44, name="힌지 보호 필름", form="flip", kind="assembly",
              status="approved", calc="923.8", lines=8, skus=[("OHI-R44", "890.0")])
        _seed(s, recipe_id=45, name="힌지 보호 필름", form="fold", kind="assembly",
              status="approved", calc="923.8", lines=8, skus=[("OHI-R45", "890.0")])
        # ★★flip/fold 형제인데 «현재값도 계산값도 서로 다른» 짝 — 이것도 G3-2가 아니다.
        #   폼팩터 분리가 이미 `cost_price`에 반영돼 있으니 「미분리」가 아니다. 잔여 소액이다.
        #   ★이 모양이 픽스처에 없으면 「form_factor로 판별」 변이가 살아남는다(실측 B4):
        #   위 r44·r45는 계산값까지 같아서 `different_truth` 조건이 대신 걸러 주기 때문에
        #   판별식이 틀려도 초록이 된다. prod에선 이 짝(r34·r35)이 9건을 26건으로 만들었다.
        _seed(s, recipe_id=34, name="오픽스 외부+내부 4매", form="flip", kind="assembly",
              status="approved", calc="3911.4", lines=10, skus=[("OHI-R34", "3878.0")])
        _seed(s, recipe_id=35, name="오픽스 외부+내부 4매", form="fold", kind="assembly",
              status="approved", calc="6440.3", lines=10, skus=[("OHI-R35", "6406.0")])
        # G1 — 한 레시피에 현재값이 2종. 뺄셈이 성립하지 않는다.
        _seed(s, recipe_id=69, name="매트 필름 3매", form="flip", kind="assembly",
              status="approved", calc="3746.4", lines=10,
              skus=[("OHI-G1-1", "1412.4"), ("OHI-G1-2", "2666.0")])
        # G3-1 — 승인 imported_goods · 구성 1줄.
        _seed(s, recipe_id=40, name="EZ툴 강화유리 2매", form="bar", kind="imported_goods",
              status="approved", calc="3201.0", lines=1, skus=[("OHI-G31-1", "3500.0")])
        # ★`breakdown`이 없어서 «줄 수를 breakdown으로 못 세는» 레시피 — 폴백 경로.
        #   적대 리뷰 P2-2 수리(레시피 100건 lazy 순회 → 못 세는 것만 묶음 질의) 뒤,
        #   이 모양이 없으면 폴백이 통째로 죽어도 전건 초록이다(실측: 변이 생존).
        #   구성 줄이 1개이므로 G3-1로 판정돼야 한다 — 폴백이 0을 주면 잔여로 샌다.
        _seed(s, recipe_id=38, name="EZ툴 강화유리 3매", form="bar", kind="imported_goods",
              status="approved", calc="4100.0", lines=0, skus=[("OHI-FALLBACK", "3900.0")])
        s.flush()
        s.query(CostStandard).filter(CostStandard.recipe_id == 38).update(
            {"breakdown": None}, synchronize_session=False
        )
        s.add(CostRecipeLine(
            recipe_id=38, ledger_item_name="오타오_투명 강화유리 (New Ver.)", quantity=1,
            note="수입 완제품 — 원장 로트 단가가 이 종에 붙는다",
        ))
        # 일치 — 격차 0.4원.
        _seed(s, recipe_id=97, name="저반사 필름", form="fold", kind="assembly",
              status="approved", calc="3823.4", lines=8, skus=[("OHI-MATCH", "3823.0")])
        # ★MATCH_EPSILON(0.5원) «바로 바깥» — 격차 2.0원. 일치가 아니라 잔여여야 한다.
        #   적대 리뷰 P2-1: 이 모양이 없으면 임계를 0.5→5.0으로 흔들어도 전건 초록이다
        #   (0.5와 5.0 사이에 픽스처가 한 건도 없었다). 판정을 지배하는 상수는 경계에
        #   반례가 있어야 지켜진다 — 교훈 #381과 같은 결.
        _seed(s, recipe_id=95, name="임계 경계 필름", form="bar", kind="assembly",
              status="approved", calc="1002.0", lines=7, skus=[("OHI-EPS", "1000.0")])
        # draft 링크만 — 정본 없음(미분류).
        _seed(s, recipe_id=80, name="초안 레시피", form="bar", kind="assembly",
              status="draft", calc=None, lines=0, skus=[("OHI-DRAFT", "1000.0")])

        # 링크 자체 없음 — 세 갈래.
        s.add(ProductMaster(internal_sku="OHI-DUPE", product_name="[중복] 필름", cost_price=D("1")))
        s.add(ProductMaster(internal_sku="OHI-WEAR", product_name="리바이스 청바지 501", cost_price=D("2")))
        s.add(ProductMaster(internal_sku="OHI-SET", product_name="필름 2매 (2세트)", cost_price=D("3")))
        # 승인된 매입가가 붙은 SKU — S2-③ 자동 승격.
        s.add(ProductMaster(internal_sku="OHI-0887", product_name="매입 완제품", cost_price=D("45000")))
        s.add(CostPurchasedPrice(
            internal_sku="OHI-0887", unit_price_inc_vat=D("47000"),
            source="file", approved_at=datetime(2026, 8, 31, 2, 27, 57),
        ))
        # 제안일 뿐 승인 아님 — 정본이 되면 안 된다.
        s.add(ProductMaster(internal_sku="OHI-PROP", product_name="제안만 있는 매입품", cost_price=D("500")))
        s.add(CostPurchasedPrice(
            internal_sku="OHI-PROP", unit_price_inc_vat=D("900"), source="file", approved_at=None,
        ))
        s.commit()
    yield tc
    app.dependency_overrides.clear()


def _body(client) -> dict:
    r = client.get("/api/cost/truth-board")
    assert r.status_code == 200, r.text
    return r.json()


def _row(body: dict, sku: str) -> dict:
    hit = [i for i in body["items"] if i["internal_sku"] == sku]
    assert hit, f"{sku} 행이 표에 없다 — 「빠짐없이 실린다」가 깨졌다"
    return hit[0]


# ═══════════════════════════════════════════════════════════════════
# S2-① 963 전 SKU가 한 표에 선다
# ═══════════════════════════════════════════════════════════════════


def test_every_product_master_row_is_on_the_board(client):
    """★전건이 실린다 — 안 실리면 「계산 안 되는 것」이 조용히 사라진다."""
    body = _body(client)
    with client.testing_session() as s:
        total = s.query(ProductMaster).count()
    assert body["sku_count"] == total
    assert len(body["items"]) == total
    census = body["census"]["by_truth_type"]
    assert sum(census.values()) == total, "집계가 행 수와 안 맞는다"


def test_row_carries_the_four_columns_the_contract_names(client):
    """계약 §4 S2-① — 정본 유형·정본값·현재값·격차가 **한 행에** 있다."""
    row = _row(_body(client), "OHI-G2-1")
    assert row["truth_type"] == TS.TRUTH_COMPUTED
    assert row["truth_label"] == "계산값"
    assert D(row["truth_value"]) == D("3309.7")
    assert D(row["current_cost_price"]) == D("3010.7")
    assert D(row["gap"]) == D("299.0")


def test_census_is_comparable_to_ref118_buckets(client):
    """★상단 집계가 ref 118 §3 분해와 «대조 가능»해야 한다 — G 이름표가 실려야 그게 된다."""
    body = _body(client)
    by_cause = body["census"]["by_cause"]
    assert by_cause[TS.CAUSE_PARTS_299] == 2          # G2  — r50의 SKU 2건
    assert by_cause[TS.CAUSE_FAMILY_NOT_SPLIT] == 1   # G3-2 — r52
    assert by_cause[TS.CAUSE_GRAIN_MISMATCH] == 2     # G1  — r69의 SKU 2건
    assert by_cause[TS.CAUSE_IMPORTED_SINGLE_LINE] == 2   # r40(breakdown) + r38(폴백)
    assert by_cause[TS.CAUSE_MATCH] == 1
    assert by_cause[TS.CAUSE_RESIDUAL] == 5           # r44·r45·r34·r35·r95
    assert body["census"]["cause_ref118"][TS.CAUSE_PARTS_299] == "G2"
    assert body["census"]["cause_ref118"][TS.CAUSE_GRAIN_MISMATCH] == "G1"


def test_cutover_ready_sums_only_computed_and_purchased_with_gap(client):
    """「즉시 가능」의 정의 — 정본이 서 있고 격차가 있는 것만."""
    body = _body(client)
    ready = body["census"]["cutover_ready_count"]
    # G2 2건 + G3-2 1건 + 매입가 1건(45000→47000). 일치·보류·정본없음은 안 센다.
    assert ready == 4
    assert D(body["census"]["cutover_gap_sum"]) == D("299.0") * 2 + D("4859.6") + D("2000")


def test_matched_row_is_not_a_cutover_target(client):
    """★회귀 — 일치(격차 0.4원)가 「즉시 가능」에 섞이면 ref 118의 「278 + 일치 3」과 대조가 깨진다.

    초판이 `gap != 0`으로 셌다가 이 행이 대상에 들어갔다. 컷오버 임계는 분류 임계와 같아야 한다.
    """
    body = _body(client)
    row = _row(body, "OHI-MATCH")
    assert row["cause"] == TS.CAUSE_MATCH
    assert D(row["gap"]) != 0, "격차 자체는 0이 아니다 — 그런데도 대상이 아니어야 한다"
    assert abs(D(row["gap"])) < TS.MATCH_EPSILON
    assert body["census"]["matched_count"] == 1
    # 「즉시 가능」 합계에 이 행의 격차가 안 실렸다.
    assert D(body["census"]["cutover_gap_sum"]) == D("299.0") * 2 + D("4859.6") + D("2000")


# ═══════════════════════════════════════════════════════════════════
# S2-② 보류는 사유와 소관을 말한다
# ═══════════════════════════════════════════════════════════════════


def test_grain_mismatch_is_held_with_sentence_and_owner(client):
    row = _row(_body(client), "OHI-G1-1")
    assert row["truth_type"] == TS.TRUTH_HELD
    assert row["cause"] == TS.CAUSE_GRAIN_MISMATCH
    assert "그레인 불일치" in row["reason"]
    assert "2종" in row["reason"], "몇 종인지 말해야 사유가 문장이다"
    assert row["owner"] == TS.OWNER_TRACK_A2


def test_held_rows_never_carry_a_truth_value_or_gap(client):
    """★없음 ≠ 0 — 보류에 계산값을 실으면 화면이 「이 값으로 갈아타라」고 말하는 셈이다."""
    body = _body(client)
    for row in body["items"]:
        if row["truth_type"] in (TS.TRUTH_HELD, TS.TRUTH_NONE):
            assert row["truth_value"] is None, row
            assert row["gap"] is None, row


def test_every_row_has_a_non_empty_reason_and_owner(client):
    """★빈 칸도 0도 아니다 — 한 행이라도 비면 그 행은 화면에서 침묵한다."""
    for row in _body(client)["items"]:
        assert row["reason"] and row["reason"].strip(), row
        assert row["owner"] and row["owner"].strip(), row


def test_imported_single_line_says_it_is_a_kind_not_a_defect(client):
    """★실측이 계약 §1 표와 어긋난 자리 — 화면은 실측을 말해야 한다.

    r39·40·41은 승인 `imported_goods`이고 구성 줄 note가 *"수입 완제품 — 원장 로트 단가가
    이 종에 붙는다"*라고 적혀 있다. 「레시피 보강 필요」로 띄우면 화면이 거짓을 말한다.
    """
    row = _row(_body(client), "OHI-G31-1")
    assert row["truth_type"] == TS.TRUTH_HELD
    assert row["cause"] == TS.CAUSE_IMPORTED_SINGLE_LINE
    assert "결함이 아니라 종류" in row["reason"]
    assert row["owner"] == TS.OWNER_DCPP63
    assert "계약 §1" in row["reason"], "계약과 어긋난다는 사실이 화면에 남아야 한다"


def test_board_caveats_reach_the_body(client):
    """자백 문구가 서비스층에만 있고 HTTP로 안 나오면 아무도 못 본다."""
    body = _body(client)
    assert body["caveats"], "caveats가 body에서 사라졌다"
    joined = " ".join(body["caveats"])
    assert "읽기 전용" in joined
    assert "recipe_kind" in joined


# ═══════════════════════════════════════════════════════════════════
# S2-③ 정본 없음이 소관별로 갈라 보인다 / 매입가 자동 승격
# ═══════════════════════════════════════════════════════════════════


def test_approved_purchase_price_promotes_the_sku(client):
    """★계약 §4 S2-③ — D-CPP-63이 매입가를 승인하면 이 표에 «자동으로» 나타난다."""
    row = _row(_body(client), "OHI-0887")
    assert row["truth_type"] == TS.TRUTH_PURCHASED
    assert row["truth_label"] == "매입가"
    assert row["truth_value"] == "47000.00"
    assert D(row["gap"]) == D("2000")
    assert row["cause"] == TS.CAUSE_PURCHASED_APPROVED


def test_unapproved_purchase_price_is_not_a_truth(client):
    """★`approved_at IS NULL`은 제안이지 확정이 아니다 — 정본이 되면 안 된다."""
    row = _row(_body(client), "OHI-PROP")
    assert row["truth_type"] == TS.TRUTH_NONE
    assert row["truth_value"] is None


def test_ungrounded_splits_by_owner(client):
    body = _body(client)
    draft = _row(body, "OHI-DRAFT")
    assert draft["truth_type"] == TS.TRUTH_NONE
    assert draft["cause"] == TS.CAUSE_DRAFT_LINK
    assert draft["owner"] == TS.OWNER_DCPP63_OR_A1A2
    assert "추측으로 정하지 않는다" in draft["reason"]

    dupe = _row(body, "OHI-DUPE")
    assert dupe["cause"] == TS.CAUSE_NO_LINK_DUPE
    assert dupe["owner"] == TS.OWNER_NONE

    wear = _row(body, "OHI-WEAR")
    assert wear["cause"] == TS.CAUSE_NO_LINK_APPAREL
    assert wear["owner"] == TS.OWNER_NONE

    other = _row(body, "OHI-SET")
    assert other["cause"] == TS.CAUSE_NO_LINK_OTHER
    assert other["owner"] == TS.OWNER_TRACK_A1A2


# ═══════════════════════════════════════════════════════════════════
# 판별식 자체 — ref 118을 재현하는가
# ═══════════════════════════════════════════════════════════════════


def test_family_rule_needs_different_calc_not_just_same_form_factor(client):
    """★`form_factor`만으로 가르면 틀린다 — prod에서 9건이 26건이 됐다.

    두 가지 «아닌» 모양을 함께 잰다:
      · r44·r45 — 현재값도 계산값도 서로 같다(폼팩터가 원가를 안 가른다)
      · r34·r35 — 현재값도 계산값도 서로 «다르다»(이미 갈라져 있다). ★이 짝이 없으면
        「form_factor로 판별」 변이가 살아남는다 — r44·r45만으로는 `different_truth`
        조건이 대신 걸러 줘서 판별식이 틀려도 초록이 된다.
    """
    body = _body(client)
    assert _row(body, "OHI-G32-1")["cause"] == TS.CAUSE_FAMILY_NOT_SPLIT
    for sku in ("OHI-R44", "OHI-R45", "OHI-R34", "OHI-R35"):
        assert _row(body, sku)["cause"] == TS.CAUSE_RESIDUAL, sku
        assert _row(body, sku)["truth_type"] == TS.TRUTH_HELD, sku


def test_line_count_falls_back_to_recipe_lines_when_breakdown_is_missing(client):
    """★`breakdown`으로 줄 수를 못 세는 레시피는 `cost_recipe_line`으로 센다 — 적대 리뷰 P2-2.

    P2-2 수리로 「레시피 전건 lazy 순회」를 「못 세는 것만 묶음 질의」로 바꿨다. 그 폴백이
    통째로 죽어도(빈 dict) 종전 픽스처는 전건 초록이었다 — 모든 픽스처가 breakdown을
    갖고 있어 폴백 경로를 한 번도 안 밟았기 때문이다(교훈 #381과 같은 모양).
    """
    row = _row(_body(client), "OHI-FALLBACK")
    assert row["cause"] == TS.CAUSE_IMPORTED_SINGLE_LINE, (
        "breakdown이 없는 1줄 레시피가 G3-1로 안 잡혔다 — 폴백이 0을 줬다는 뜻이다"
    )
    assert row["truth_type"] == TS.TRUTH_HELD


def test_match_epsilon_boundary_is_pinned(client):
    """★일치 임계(0.5원)가 «값»으로 고정돼 있다 — 적대 리뷰 P2-1.

    격차 2.0원은 일치가 **아니다.** 이 픽스처가 없으면 `MATCH_EPSILON`을 0.5→5.0으로
    흔들어도 전건 초록이었다(리뷰어 실측) — 0.5와 5.0 사이에 반례가 한 건도 없었기 때문이다.
    임계는 두 곳에서 쓰인다(분류의 「일치」 · 컷오버 대상의 하한). 둘 다 여기서 지켜진다.
    """
    body = _body(client)
    row = _row(body, "OHI-EPS")
    assert row["cause"] == TS.CAUSE_RESIDUAL, "격차 2.0원이 「일치」로 판정됐다 — 임계가 넓어졌다"
    assert row["truth_type"] == TS.TRUTH_HELD
    assert row["truth_value"] is None
    # 컷오버 대상에도 안 들어간다(보류이므로). 임계가 넓어지면 여기서도 갈린다.
    assert all(
        r["internal_sku"] != "OHI-EPS"
        for r in body["items"]
        if r["truth_type"] in (TS.TRUTH_COMPUTED, TS.TRUTH_PURCHASED)
    )
    assert body["census"]["matched_count"] == 1, "일치는 r97 하나뿐이어야 한다"


def test_grain_mismatch_wins_over_the_299_rule(client):
    """판정 «순서»가 곧 규칙이다 — 비교가 불성립하면 299 규칙을 물어볼 수 없다."""
    g = TS.RecipeGroup(
        recipe_id=1, product_name="x", form_factor="bar", recipe_kind="assembly",
        skus=("a", "b"), cost_price_kinds=2, cost_price_min=D("1000"),
        std_cost_inc_vat=D("1299"), line_count=9,
    )
    cause, truth, _reason, _owner = TS.classify_group(g, ())
    assert cause == TS.CAUSE_GRAIN_MISMATCH
    assert truth == TS.TRUTH_HELD
    assert g.gap is None, "G1의 격차는 0이 아니라 «없음»이다"


def test_two_grounds_is_held_not_silently_chosen(client):
    """근거가 둘이면 시스템이 고르지 않는다(계약 §2-1)."""
    g = TS.RecipeGroup(
        recipe_id=1, product_name="x", form_factor="bar", recipe_kind="imported_goods",
        skus=("a",), cost_price_kinds=1, cost_price_min=D("1000"),
        std_cost_inc_vat=D("1299"), line_count=1,
    )
    cause, truth, reason, _owner = TS.classify_group(g, (), has_approved_purchase=True)
    assert cause == TS.CAUSE_TWO_GROUNDS
    assert truth == TS.TRUTH_HELD
    assert "사람이 정한다" in reason


def test_approved_recipe_without_standard_is_held_not_crashing(client):
    g = TS.RecipeGroup(
        recipe_id=1, product_name="x", form_factor=None, recipe_kind="assembly",
        skus=("a",), cost_price_kinds=1, cost_price_min=D("1000"),
        std_cost_inc_vat=None, line_count=0,
    )
    cause, truth, _reason, _owner = TS.classify_group(g, ())
    assert cause == TS.CAUSE_NO_STANDARD
    assert truth == TS.TRUTH_HELD


# ═══════════════════════════════════════════════════════════════════
# 이 층은 쓰기가 없다 (계약 §3-B)
# ═══════════════════════════════════════════════════════════════════


def test_reading_the_board_changes_no_cost_price(client):
    """★판별하러 간 층이 값을 바꾸면 이 계약이 잡으려는 병을 이 계약이 만든다."""
    with client.testing_session() as s:
        before = {p.internal_sku: p.cost_price for p in s.query(ProductMaster).all()}
    _body(client)
    _body(client)
    with client.testing_session() as s:
        after = {p.internal_sku: p.cost_price for p in s.query(ProductMaster).all()}
    assert before == after


def test_service_module_has_no_cost_price_assignment():
    """★ref 119 §6의 재현 명령을 테스트로 굳힌다 — 이 파일에 대입이 생기면 죽는다.

    ★**문자열 grep이 아니라 AST로 본다.** grep판은 원리적으로 헐겁다 — 양쪽으로 틀린다:
    docstring에 적은 «설명»(「`.cost_price =` 대입 0건」)을 위반으로 읽고(실제로 초판이 그렇게
    빨갛게 떴다), 반대로 `setattr(pm, "cost_price", v)`처럼 점 표기가 아닌 진짜 쓰기는 놓친다.
    AST는 주석·문자열을 아예 안 보고 대입 «구문»만 본다.
    """
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(TS.__file__).read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for t in targets:
            if isinstance(t, ast.Attribute) and t.attr == "cost_price":
                offenders.append(f"line {t.lineno}: 속성 대입")
        # `setattr(obj, "cost_price", ...)` — 점 표기를 우회하는 길도 막는다.
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "setattr":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                if node.args[1].value == "cost_price":
                    offenders.append(f"line {node.lineno}: setattr")
    assert not offenders, f"정본 판별층에 cost_price 쓰기가 생겼다: {offenders}"
