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
        # ★★적대 리뷰 2R P1 — 「가격은 1종인데 상품명 구성이 갈리는」 묶음.
        #   `_seed`는 상품명을 `f"{name} · {sku}"`로 만들어 구성을 못 심으므로 직접 심는다.
        #   이 모양이 없으면 `truth_board()`가 `name_grain_kinds`를 계산해 `RecipeGroup`에
        #   실어 보내는 **배선 한 줄**을 지워도 전건 초록이다(리뷰어 변이 M-F 생존).
        s.add(CostRecipe(id=91, product_name="구성 혼재 필름", form_factor="fold",
                         status="approved", recipe_kind="assembly"))
        s.add(CostStandard(recipe_id=91, price_rule=RULE, std_cost_inc_vat=D("5000.0"),
                           std_cost_ex_vat=D("5000.0"), breakdown=_breakdown(8)))
        for sku, pname in (
            ("OHI-MIXA", "구성 혼재 필름 (외부액정3매+내부액정3매)"),
            ("OHI-MIXB", "구성 혼재 필름 (외부액정3매)"),
        ):
            # ★현재 원가는 **같다** — 가격 게이트만으로는 안 걸린다.
            s.add(ProductMaster(internal_sku=sku, product_name=pname, cost_price=D("4000.0")))
            s.add(CostRecipeLink(internal_sku=sku, recipe_id=91, status="approved"))

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
    # G1 — r69의 SKU 2건(가격 2종) + r91의 SKU 2건(가격은 1종인데 상품명 구성 2종).
    # ★뒤의 2건이 적대 리뷰 2R P1이 신설시킨 픽스처다 — 게이트 «둘»이 각각 한 묶음씩 잡는다.
    assert by_cause[TS.CAUSE_GRAIN_MISMATCH] == 4
    assert by_cause[TS.CAUSE_IMPORTED_SINGLE_LINE] == 2   # r40(breakdown) + r38(폴백)
    assert by_cause[TS.CAUSE_MATCH] == 1
    assert by_cause[TS.CAUSE_RESIDUAL] == 5           # r44·r45·r34·r35·r95
    assert body["census"]["cause_ref118"][TS.CAUSE_PARTS_299] == "G2"
    assert body["census"]["cause_ref118"][TS.CAUSE_GRAIN_MISMATCH] == "G1"


def test_name_grain_signal_actually_reaches_the_board(client):
    """★★적대 리뷰 2R P1 — 게이트가 «서 있는지»가 아니라 «실측이 닿는지»를 잰다.

    1R 수리는 `classify_group`에 `name_grain_kinds > 1` 게이트를 넣고 단위 테스트 2종으로
    지켰다. 그런데 리뷰어가 **배선 한 줄**만 끊었더니(`truth_board()`의 RecipeGroup 생성에서
    `name_grain_kinds=1` 고정) **58건이 전건 초록**이었다 — 게이트는 서 있는데 실제
    상품명이 거기 안 닿아도 아무도 안 운다.

    ★그 커밋 메시지가 *"「한 번 확인했다」와 「구조로 막힌다」는 다르다"*고 적어 놓고
    정작 구조의 절반(계산 로직)만 지키고 나머지 절반(그 계산이 실제로 쓰이는가)은
    안 지켰다. 이 테스트가 그 나머지 절반이다 — **HTTP body를 통해** 잰다.
    """
    body = _body(client)
    for sku in ("OHI-MIXA", "OHI-MIXB"):
        row = _row(body, sku)
        assert row["cause"] == TS.CAUSE_GRAIN_MISMATCH, (
            f"{sku}: 현재 원가는 같지만 상품명 구성이 갈린다 — 보류여야 한다"
        )
        assert row["truth_type"] == TS.TRUTH_HELD, sku
        assert row["truth_value"] is None, "정본값이 서면 컷오버가 집어간다"
        assert "우연히" in (row["reason"] or ""), "왜 막혔는지가 화면 문장에 남아야 한다"


def test_name_grain_gate_does_not_block_model_only_variation(client):
    """★과차단 반례 — 기종만 다른 정상 묶음은 막히면 안 된다.

    이 게이트가 EZ툴(아이폰15/16/17…)처럼 «기종만» 다른 묶음까지 막으면 D-CPP-66이
    통째로 무의미해진다. 픽스처의 r40(EZ툴, SKU 1건)·r50(bar, SKU 2건)이 그 반례다.
    """
    body = _body(client)
    assert _row(body, "OHI-G31-1")["truth_type"] == TS.TRUTH_COMPUTED
    for sku in ("OHI-G2-1", "OHI-G2-2"):
        assert _row(body, sku)["cause"] != TS.CAUSE_GRAIN_MISMATCH, sku


def test_grain_mismatch_stays_held_even_after_the_d_cpp_66_release(client):
    """★★D-CPP-66의 **안전장치**. 이게 이 개정에서 가장 비싼 자리다.

    D-CPP-66은 「수입 완제품」·「잔여」의 보류를 풀었다. 두 갈래 다 **위쪽
    `cost_price_kinds > 1` 분기를 이미 통과한 뒤**라, 한 계산값을 그 묶음 전건에 적용해도
    안전하다 — SKU들이 현재 원가를 1종만 갖기 때문이다.

    ★**그레인 불일치는 그 보장이 없다.** 한 레시피에 구성이 여러 개 섞여 있어(예: 태블릿
    기본/플러스/울트라, 매트 필름 외부3매/내부3+외부3/+후면2) 계산값이 하나뿐이다. 이걸
    풀면 **플러스 사이즈에 기본 사이즈 값이 박힌다** — 원가가 «지어진» 것이고, 이 계약이
    없애려는 병 그 자체다.

    라이브 실측(2026-09-02): 그런 SKU가 **92건**(태블릿 36 · 매트 30 · 매트 26)이다.
    """
    body = _body(client)
    grain = [r for r in body["items"] if r["cause"] == TS.CAUSE_GRAIN_MISMATCH]
    assert grain, "픽스처에 그레인 불일치가 없다 — 이 단언이 아무것도 안 지킨다"
    for r in grain:
        assert r["truth_type"] == TS.TRUTH_HELD, r["internal_sku"]
        assert r["truth_value"] is None, "정본값이 서면 컷오버가 집어간다"
        assert r["gap"] is None
        assert r["owner"] == TS.OWNER_TRACK_A2


def test_release_only_applies_where_the_group_has_one_current_cost(client):
    """★해제된 두 갈래가 «그레인 보장» 위에 서 있음을 규칙으로 못 박는다.

    정본이 선 행(계산값·매입가)은 **전건** 그 묶음의 현재 원가가 1종이어야 한다.
    이 불변식이 깨지면 D-CPP-66의 해제 근거가 통째로 무너진다.
    """
    body = _body(client)
    with client.testing_session() as s:
        from app.models import CostRecipeLink, ProductMaster

        for r in body["items"]:
            if r["truth_type"] not in (TS.TRUTH_COMPUTED,):
                continue
            rid = r["recipe_id"]
            if rid is None:
                continue
            skus = [
                x.internal_sku
                for x in s.query(CostRecipeLink).filter_by(recipe_id=rid, status="approved").all()
            ]
            kinds = {
                p.cost_price
                for p in s.query(ProductMaster).filter(ProductMaster.internal_sku.in_(skus)).all()
            }
            assert len(kinds) <= 1, (
                f"r{rid}: 계산값이 정본인데 그 묶음의 현재 원가가 {len(kinds)}종이다 — "
                "한 값을 전건에 박으면 서로 다른 구성에 같은 값이 들어간다"
            )


def test_cutover_ready_sums_only_computed_and_purchased_with_gap(client):
    """「즉시 가능」의 정의 — 정본이 서 있고 격차가 있는 것만."""
    body = _body(client)
    ready = body["census"]["cutover_ready_count"]
    # ★D-CPP-66에서 수입 완제품·잔여의 보류가 풀려 대상이 늘었다. 개수를 손으로 세지 않고
    #   «정의»로 다시 센다 — 손으로 적은 수는 규칙이 바뀔 때마다 갱신 대상이 되고,
    #   그러면 이 단언이 「정의를 지키는 것」이 아니라 「숫자를 따라가는 것」이 된다.
    expect = [
        r for r in body["items"]
        if r["truth_type"] in (TS.TRUTH_COMPUTED, TS.TRUTH_PURCHASED)
        and r["gap"] is not None and abs(D(r["gap"])) >= TS.MATCH_EPSILON
    ]
    assert ready == len(expect)
    assert D(body["census"]["cutover_gap_sum"]) == sum(D(r["gap"]) for r in expect)
    # 일치·보류·정본없음은 한 건도 안 섞였다.
    assert all(r["truth_type"] not in (TS.TRUTH_HELD, TS.TRUTH_NONE) for r in expect)


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
    total = D(body["census"]["cutover_gap_sum"])
    assert total == sum(
        D(r["gap"]) for r in body["items"]
        if r["truth_type"] in (TS.TRUTH_COMPUTED, TS.TRUTH_PURCHASED)
        and r["gap"] is not None and abs(D(r["gap"])) >= TS.MATCH_EPSILON
    )
    assert D(row["gap"]) not in (total,), "일치 행 하나가 합계 전부가 되어선 안 된다"


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
    # ★D-CPP-66(2026-09-02 Jino 지시)로 **보류가 풀렸다** — 수입 완제품의 정본은
    #   수입원장의 로트 단가이고 그 값이 구성 1줄에 이미 실려 있다.
    assert row["truth_type"] == TS.TRUTH_COMPUTED
    assert row["cause"] == TS.CAUSE_IMPORTED_SINGLE_LINE
    assert "결함이 아니라 종류" in row["reason"], "「보강 필요」로 읽히면 안 된다"
    assert row["owner"] == TS.OWNER_CUTOVER
    assert row["truth_value"] is not None, "정본값이 서야 컷오버가 가능하다"


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
        # ★D-CPP-66 — 잔여도 보류가 풀렸다(원인을 몰라도 정본은 계산값이다).
        assert _row(body, sku)["truth_type"] == TS.TRUTH_COMPUTED, sku


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
    assert row["truth_type"] == TS.TRUTH_COMPUTED  # D-CPP-66에서 수입 완제품 보류 해제


def test_match_epsilon_boundary_is_pinned(client):
    """★일치 임계(0.5원)가 «값»으로 고정돼 있다 — 적대 리뷰 P2-1.

    격차 2.0원은 일치가 **아니다.** 이 픽스처가 없으면 `MATCH_EPSILON`을 0.5→5.0으로
    흔들어도 전건 초록이었다(리뷰어 실측) — 0.5와 5.0 사이에 반례가 한 건도 없었기 때문이다.
    임계는 두 곳에서 쓰인다(분류의 「일치」 · 컷오버 대상의 하한). 둘 다 여기서 지켜진다.
    """
    body = _body(client)
    row = _row(body, "OHI-EPS")
    assert row["cause"] == TS.CAUSE_RESIDUAL, "격차 2.0원이 「일치」로 판정됐다 — 임계가 넓어졌다"
    assert row["truth_type"] == TS.TRUTH_COMPUTED  # D-CPP-66에서 잔여 보류 해제
    assert row["truth_value"] is not None
    # ★임계가 5.0으로 넓어지면 이 행은 「일치」가 되어 **컷오버 대상에서 빠진다.**
    #   그러니 「대상에 «들어있다»」를 단언하는 것이 곧 임계를 지키는 것이다
    #   (보류 해제 전에는 「안 들어있다」로 같은 것을 지켰다 — 방향만 뒤집혔다).
    assert abs(D(row["gap"])) >= TS.MATCH_EPSILON
    ready_skus = {
        r["internal_sku"] for r in body["items"]
        if r["truth_type"] in (TS.TRUTH_COMPUTED, TS.TRUTH_PURCHASED)
        and r["gap"] is not None and abs(D(r["gap"])) >= TS.MATCH_EPSILON
    }
    assert "OHI-EPS" in ready_skus
    assert "OHI-MATCH" not in ready_skus, "격차 0.4원은 대상이 아니다"
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
