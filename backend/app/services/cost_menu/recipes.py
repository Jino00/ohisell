"""레시피·링크·표준원가 — DB 층 (계약 A′ S2, D-CPP-53).

`recipe_parser`·`mapping_parser`·`standard_cost`가 순수 SA이고, 이 파일이 그것들을 부르는
**얇은 DB 층**이다(계약 B의 `allocator.py` ↔ `ledger.py`와 같은 형태, 계약 §2-6).

## 이 층이 푸는 진짜 문제 — 두 엑셀은 서로를 모른다

원가표는 **「품목」**으로 적혀 있고(「지문방지필름 TPU 3매」), 매핑 정본은 **「상품명」**으로
적혀 있다(「오하이 빛반사, 지문방지 매트 필름 3매」). **두 파일 어디에도 둘을 잇는 열이 없다.**
그런데 레시피의 키는 「상품명 × 폼팩터」다(§0-B). 그래서 이 층이 둘을 잇는다.

잇는 방법은 **독립 신호 2개의 교차**다:

  ① 폼팩터  — 옵션명에서 제안(`mapping_parser.propose_form_factor`)
  ② 금액    — 그 묶음 SKU들의 `product_master.cost_price` 최빈값 ↔ 원가표 「제품원가(+VAT)」

①은 문자열에서, ②는 DB의 숫자에서 온다 — 서로를 참조하지 않으므로 **일치는 우연이 아니다.**
실측(2026-08-23): 표적 상품명의 `bar` 버킷 108 SKU가 전부 `cost_price=2350.7`이고, 원가표
「지문방지필름 TPU 3매」의 제품원가(+VAT)가 **2350.7**이다.

★그래도 **제안이다.** 후보가 0건이거나 2건 이상이면 구성을 붙이지 않고 사유를 실어 올린다.
확정은 화면에서 사람이 한다(계약 §2-2) — 08-10 71건 사고가 「추론을 확인분과 동일시」였다.

⚠️ `cost_price`는 **읽기만** 한다(§3 금지선). 원가로 채택하지 않고, 「어느 레시피에 붙는가」를
제안하는 **신원 단서**로만 쓴다. 쓰기는 이 파일 어디에도 없다.

## 재수입은 승인분을 덮지 않는다

같은 (상품명, 폼팩터)가 이미 **`approved`**면 **건너뛴다.** 왜냐하면 덮으면 Jino가 승인한
구성이 «파일을 다시 올렸다»는 이유로 조용히 바뀌고, 승인의 의미가 사라지기 때문이다.
`draft`만 갱신한다. 건너뛴 것은 `skipped_approved`로 세어 화면이 말한다.

## 단가는 여기서 오지 않는다

이 층은 `cost_material`을 만들되 **`cost_material_price` 행은 만들지 않는다.** 엑셀 숫자는
`excel_ref_price`(참고값)에만 들어간다. 단가가 되는 것은 Jino가 「채택」을 눌렀을 때
(`adopt_excel_prices`)뿐이고, 그때 생기는 행은 `source='manual'`이며 `note`에 출처가 남는다.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy.orm import Session, selectinload

from app.models import (
    CostMaterial,
    CostMaterialPrice,
    CostRecipe,
    CostRecipeLine,
    CostRecipeLink,
    CostStandard,
    ProductChannelMapping,
    ProductMaster,
)
from app.services.cost_menu import ledger_check as LC
from app.services.cost_menu import standard_cost as SC
from app.services.cost_menu.mapping_parser import MappingParseResult, parse_mapping_table
from app.services.cost_menu.materials import CostMenuConflict, CostMenuError, ledger_check
from app.services.cost_menu.recipe_parser import (
    ParseResult,
    RecipeDraft,
    parse_cost_table,
)

#: 원가표 「제품원가(+VAT)」 ↔ `cost_price` 대조 허용 오차(원). 엑셀 부동소수 꼬리만 흡수한다.
MATCH_TOLERANCE = Decimal("0.05")

DEFAULT_PRICE_RULE = "latest"

#: 엑셀 참고값을 단가로 채택할 때 남기는 출처 문구의 접두사. 이 문구가 있으면 그 단가가
#: 「사람이 엑셀을 보고 승인한 값」임을 화면·감사가 안다.
ADOPT_NOTE_PREFIX = "엑셀 참고값 채택"


def _d(v: Optional[Decimal]) -> Optional[str]:
    return None if v is None else str(v)


# ──────────────────────────────────────────────
# 채널코드 → SKU (저장소 표준 조인 — 새로 발명하지 않는다)
# ──────────────────────────────────────────────
def resolve_channel_codes(db: Session, codes: Iterable[str]) -> dict[str, dict]:
    """`channel_product_id` → {internal_sku, product_name, cost_price}.

    ★`coupang_ops.py`·`profit_calculator.py`·`diag_bridge.py`가 이미 쓰는 조인 그대로다 —
    같은 다리를 두 벌 놓지 않는다.
    ★`cost_price`는 **읽기 전용**이고 여기서만 읽힌다(제안 근거용).
    """

    wanted = [c for c in dict.fromkeys(codes) if c]
    if not wanted:
        return {}

    out: dict[str, dict] = {}
    CHUNK = 500  # SQLite 변수 상한(999)에 안 닿게 자른다
    for i in range(0, len(wanted), CHUNK):
        chunk = wanted[i : i + CHUNK]
        rows = (
            db.query(
                ProductChannelMapping.channel_product_id,
                ProductMaster.internal_sku,
                ProductMaster.product_name,
                ProductMaster.cost_price,
            )
            .join(ProductMaster, ProductChannelMapping.product_id == ProductMaster.id)
            .filter(ProductChannelMapping.channel_product_id.in_(chunk))
            .all()
        )
        for code, sku, name, cost in rows:
            # 같은 코드가 여러 행이면 첫 행을 쓴다 — 커버리지·충돌 판정은 이 계약 밖이다
            # (소관: track_product-connection-map.md, 계약 §5-1 ★링크 결정의 경계).
            out.setdefault(
                str(code),
                {
                    "internal_sku": sku,
                    "product_name": name,
                    "cost_price": cost,
                },
            )
    return out


# ──────────────────────────────────────────────
# 매칭 — 원가표 품목 ↔ (상품명 × 폼팩터)
# ──────────────────────────────────────────────
class _Match:
    __slots__ = ("draft", "reason", "candidates", "cost_price_mode", "sku_count")

    def __init__(
        self,
        draft: Optional[RecipeDraft],
        reason: str,
        candidates: Sequence[str],
        cost_price_mode: Optional[Decimal],
        sku_count: int,
    ) -> None:
        self.draft = draft
        self.reason = reason
        self.candidates = list(candidates)
        self.cost_price_mode = cost_price_mode
        self.sku_count = sku_count


def _match_draft(
    *,
    form_factor: str,
    cost_prices: list[Decimal],
    drafts_by_form: dict[Optional[str], list[RecipeDraft]],
    sku_count: int,
) -> _Match:
    if not cost_prices:
        return _Match(None, "SKU에 도달하지 못했다(채널코드 미매칭)", [], None, sku_count)

    mode, _count = Counter(cost_prices).most_common(1)[0]
    pool = drafts_by_form.get(form_factor, [])
    hits = [
        d
        for d in pool
        if d.excel_total_inc_vat is not None
        and abs(d.excel_total_inc_vat - mode) <= MATCH_TOLERANCE
    ]
    names = [f"{d.section}/{d.item_name}" for d in hits]

    if len(hits) == 1:
        return _Match(
            hits[0],
            f"폼팩터 {form_factor}(옵션명) × cost_price {mode} 일치 — 원가표 「{hits[0].item_name}」",
            names,
            mode,
            sku_count,
        )
    if not hits:
        return _Match(
            None,
            f"원가표에 폼팩터 {form_factor} · 제품원가 {mode} 인 품목이 없다",
            [],
            mode,
            sku_count,
        )
    return _Match(
        None,
        f"후보 {len(hits)}건 — 사람이 골라야 한다(cost_price {mode})",
        names,
        mode,
        sku_count,
    )


# ──────────────────────────────────────────────
# 수입(import) — 초안 만들기
# ──────────────────────────────────────────────
def import_drafts(
    db: Session,
    cost_rows: Iterable[Sequence[Any]],
    mapping_rows: Iterable[Sequence[Any]],
) -> dict:
    """두 엑셀의 행 목록 → 레시피·링크 «초안». 파일을 여는 것은 호출자(라우터)다."""

    cost: ParseResult = parse_cost_table(cost_rows)
    mapping: MappingParseResult = parse_mapping_table(mapping_rows)

    drafts_by_form: dict[Optional[str], list[RecipeDraft]] = {}
    for d in cost.recipes:
        drafts_by_form.setdefault(d.form_factor, []).append(d)

    resolved = resolve_channel_codes(
        db, (c for o in mapping.options for c in o.channel_codes)
    )

    materials = _upsert_materials(db, cost)
    db.flush()

    created = updated = skipped_approved = unmatched = 0
    groups = mapping.groups()
    report: list[dict] = []

    for (product_name, form_factor), rows in sorted(groups.items()):
        skus: dict[str, Optional[Decimal]] = {}
        for row in rows:
            for code in row.channel_codes:
                hit = resolved.get(code)
                if hit:
                    skus.setdefault(hit["internal_sku"], hit["cost_price"])

        prices = [p for p in skus.values() if p is not None]
        match = _match_draft(
            form_factor=form_factor,
            cost_prices=prices,
            drafts_by_form=drafts_by_form,
            sku_count=len(skus),
        )

        recipe = (
            db.query(CostRecipe)
            .filter(
                CostRecipe.product_name == product_name,
                CostRecipe.form_factor == form_factor,
            )
            .first()
        )

        if recipe is not None and recipe.status == "approved":
            skipped_approved += 1
            report.append(
                {
                    "product_name": product_name,
                    "form_factor": form_factor,
                    "action": "skipped_approved",
                    "reason": "이미 승인된 레시피다 — 재수입이 승인분을 덮지 않는다",
                    "sku_count": len(skus),
                }
            )
            continue

        anomaly = None if match.draft else "no_recipe_match"
        if match.draft and match.draft.anomalies:
            anomaly = ",".join(match.draft.anomalies)[:40]

        if recipe is None:
            recipe = CostRecipe(
                product_name=product_name,
                form_factor=form_factor,
                status="draft",
                source="excel",
                recipe_kind=match.draft.recipe_kind if match.draft else "assembly",
            )
            db.add(recipe)
            created += 1
            action = "created"
        else:
            updated += 1
            action = "updated"

        recipe.anomaly_flag = anomaly
        recipe.note = json.dumps(
            {
                "match_reason": match.reason,
                "candidates": match.candidates,
                "cost_price_mode": _d(match.cost_price_mode),
                "cost_table_item": match.draft.item_name if match.draft else None,
                "cost_table_section": match.draft.section if match.draft else None,
                "excel_total_inc_vat": _d(match.draft.excel_total_inc_vat)
                if match.draft
                else None,
                "sku_count": len(skus),
                "option_count": len(rows),
            },
            ensure_ascii=False,
        )
        db.flush()

        # 구성 라인 — 매칭됐을 때만. 못 찾았으면 **비운다**(0원짜리 구성을 만들지 않는다).
        recipe.lines.clear()
        db.flush()
        if match.draft:
            for line in match.draft.lines:
                material = materials.get(line.key.display_name)
                if material is None:
                    continue
                db.add(
                    CostRecipeLine(
                        recipe_id=recipe.id,
                        material_id=material.id,
                        quantity=line.quantity,
                        note=f"원가표 열 「{line.source_column}」",
                    )
                )
        else:
            unmatched += 1

        _sync_links(db, recipe, skus)
        report.append(
            {
                "product_name": product_name,
                "form_factor": form_factor,
                "action": action,
                "reason": match.reason,
                "sku_count": len(skus),
                "line_count": len(match.draft.lines) if match.draft else 0,
                "anomaly_flag": anomaly,
            }
        )

    return {
        "recipes_created": created,
        "recipes_updated": updated,
        "skipped_approved": skipped_approved,
        "unmatched": unmatched,
        "materials_seen": len(materials),
        "cost_table_recipes": cost.recipe_count,
        "cost_table_anomalies": cost.anomalies,
        "mapping_options": len(mapping.options),
        "mapping_anomalies": mapping.anomalies,
        "groups": len(groups),
        "report": report,
    }


def _upsert_materials(db: Session, cost: ParseResult) -> dict[str, CostMaterial]:
    """부자재 종을 이름으로 upsert. **기존 종의 값을 덮지 않는다.**

    ★`excel_ref_price`는 비어 있을 때만 채운다 — Jino가 고쳐 둔 값을 재수입이 되돌리면
    사람의 결정이 파일에 지는 것이고, 그건 이 계약이 세우려는 방향의 반대다.
    """

    wanted: dict[str, Optional[Decimal]] = {}
    meta: dict[str, tuple[Optional[str], Optional[str], bool]] = {}
    for draft in cost.recipes:
        for line in draft.lines:
            name = line.key.display_name
            wanted.setdefault(name, line.excel_ref_price)
            meta.setdefault(name, (line.key.form_factor, line.key.part, line.is_film))

    if not wanted:
        return {}

    existing = {
        m.name: m
        for m in db.query(CostMaterial).filter(CostMaterial.name.in_(list(wanted))).all()
    }
    out: dict[str, CostMaterial] = dict(existing)
    for name, ref_price in wanted.items():
        form_factor, part, is_film = meta[name]
        m = existing.get(name)
        if m is None:
            m = CostMaterial(
                name=name,
                # ★새 종은 `unconfirmed`다 — 만들자마자 승인분 행세를 하지 않는다(계약 §2-2).
                status="unconfirmed",
                category="원단" if is_film else "부자재",
                excel_label=name,
                excel_ref_price=ref_price,
                form_factor=form_factor,
                part=part,
                note="원가 정본 엑셀에서 구성 파싱으로 생성 — 단가는 아직 없다",
            )
            db.add(m)
            out[name] = m
        elif m.excel_ref_price is None and ref_price is not None:
            m.excel_ref_price = ref_price
    return out


def _sync_links(db: Session, recipe: CostRecipe, skus: dict[str, Optional[Decimal]]) -> None:
    """SKU 링크 초안 — **승인된 링크는 건드리지 않는다**(승인분 보호는 레시피와 같은 규율)."""

    have = {
        l.internal_sku: l
        for l in db.query(CostRecipeLink).filter(CostRecipeLink.recipe_id == recipe.id).all()
    }
    for sku in skus:
        if sku in have:
            continue
        db.add(
            CostRecipeLink(
                internal_sku=sku,
                recipe_id=recipe.id,
                status="draft",
                source="excel",
            )
        )
    for sku, link in have.items():
        if sku not in skus and link.status != "approved":
            db.delete(link)


# ──────────────────────────────────────────────
# 단가 해결 → 표준원가
# ──────────────────────────────────────────────
def _latest_price(m: CostMaterial) -> tuple[Optional[CostMaterialPrice], str]:
    """`material_payload`와 **같은 규칙**으로 최신 단가를 고른다.

    ★재검사를 통과한 행에서만 고른다 — 어긋난 행은 이력에 남되 최신 자리를 못 차지한다
    (S1 적대 리뷰 1R P1-1). 규칙이 두 벌이 되면 화면의 「최신 단가」와 계산이 갈린다.
    """

    from datetime import date as _date

    ordered = sorted(
        m.prices, key=lambda p: (p.effective_date or _date.min, p.id), reverse=True
    )
    if not ordered:
        return None, LC.STATUS_MISSING
    for p in ordered:
        check = ledger_check(p)
        if check.counts_as_evidence:
            return p, (LC.STATUS_MANUAL if p.source == "manual" else LC.STATUS_OK)
    return None, ledger_check(ordered[0]).status


def _line_inputs(recipe: CostRecipe) -> list[SC.RecipeLineInput]:
    out: list[SC.RecipeLineInput] = []
    for line in recipe.lines:
        material = line.material
        if material is None:
            out.append(
                SC.RecipeLineInput(
                    label=line.ledger_item_name or "(종 없음)",
                    quantity=line.quantity,
                    price_status=LC.STATUS_MISSING,
                    ledger_item_name=line.ledger_item_name,
                )
            )
            continue
        price, status = _latest_price(material)
        # ★미승인 종의 단가는 쓰지 않는다(계약 §2-2) — 추론을 확인분으로 만들지 않는다.
        if material.status != "approved":
            out.append(
                SC.RecipeLineInput(
                    label=material.name,
                    quantity=line.quantity,
                    price_status=LC.STATUS_UNCONFIRMED,
                    material_id=material.id,
                )
            )
            continue
        out.append(
            SC.RecipeLineInput(
                label=material.name,
                quantity=line.quantity,
                unit_price_ex_vat=price.unit_price_ex_vat if price else None,
                unit_price_inc_vat=price.unit_price_inc_vat if price else None,
                price_status=status,
                material_id=material.id,
                price_source=price.source if price else None,
                price_note=price.note if price else None,
            )
        )
    return out


def recompute(db: Session, recipe: CostRecipe) -> SC.StandardCostResult:
    """표준원가 재계산 + `cost_standard` 확정 저장(계약 §5-2: 쓰기 시점 계산).

    ★**미승인 레시피는 계산하지 않는다** — 승인 전 구성으로 「확정 표준원가」를 저장·표시하는
    것이 계약 §3 금지선이다. 그래서 미승인이면 기존 행을 지우고 결과만 돌려준다.
    """

    result = SC.compute_standard_cost(_line_inputs(recipe))
    row = (
        db.query(CostStandard)
        .filter(
            CostStandard.recipe_id == recipe.id,
            CostStandard.price_rule == DEFAULT_PRICE_RULE,
        )
        .first()
    )

    if recipe.status != "approved" or not result.computable:
        if row is not None:
            db.delete(row)
        return result

    if row is None:
        row = CostStandard(recipe_id=recipe.id, price_rule=DEFAULT_PRICE_RULE)
        db.add(row)
    row.std_cost_ex_vat = result.std_cost_ex_vat
    row.std_cost_inc_vat = result.std_cost_inc_vat
    row.breakdown = json.dumps(SC.breakdown_payload(result), ensure_ascii=False)
    row.computed_at = datetime.now()
    return result


# ──────────────────────────────────────────────
# 승인·채택
# ──────────────────────────────────────────────
def approve_recipe(db: Session, recipe_id: int) -> CostRecipe:
    recipe = get_recipe(db, recipe_id)
    if not recipe.lines:
        raise CostMenuError("구성이 비어 있다 — 빈 레시피는 승인할 수 없다.")
    recipe.status = "approved"
    recipe.approved_at = datetime.now()
    for link in db.query(CostRecipeLink).filter(CostRecipeLink.recipe_id == recipe.id):
        if link.status == "draft":
            link.status = "approved"
    db.flush()
    recompute(db, recipe)
    return recipe


def unapprove_recipe(db: Session, recipe_id: int) -> CostRecipe:
    """승인 취소 — 저장된 표준원가도 함께 사라진다(미승인은 「계산 안 함」이다)."""

    recipe = get_recipe(db, recipe_id)
    recipe.status = "draft"
    recipe.approved_at = None
    db.flush()
    recompute(db, recipe)
    return recipe


def adopt_excel_prices(db: Session, recipe_id: int, note: Optional[str] = None) -> dict:
    """이 레시피가 쓰는 종들의 **엑셀 참고값을 `manual` 단가로 채택**한다.

    ★이것이 계약 §3이 허용한 유일한 경로다 — *"저장되는 단가는 원장 파생이거나 Jino가
    입력·승인한 값뿐"*. Jino가 화면에서 이 버튼을 누르는 행위가 곧 «입력·승인»이고,
    생기는 행은 `source='manual'`이며 `note`에 출처가 남는다.

    ★**이미 단가가 있는 종은 건드리지 않는다** — 원장 파생 단가를 엑셀 값으로 덮으면
    계약 §2-1(단가는 항상 원장에서 파생)을 정면으로 어긴다.
    ★참고값이 없는 종은 **건너뛴다**(0으로 채우지 않는다, §2-7).
    """

    recipe = get_recipe(db, recipe_id)
    adopted, skipped_has_price, skipped_no_ref = [], [], []
    today = datetime.now().date()

    for line in recipe.lines:
        m = line.material
        if m is None:
            continue
        if m.prices:
            skipped_has_price.append(m.name)
            continue
        if m.excel_ref_price is None:
            skipped_no_ref.append(m.name)
            continue
        # ★`db.add(CostMaterialPrice(material_id=...))`가 아니라 **관계에 붙인다.**
        #   전자는 이미 로드된 `m.prices` 컬렉션을 갱신하지 않아, 바로 뒤 `recompute()`가
        #   «단가 없음»으로 계산하고 `cost_standard` 행을 안 만든다. 그러면 화면의 레시피
        #   상세(조회 시점 계산)에는 2,350.70이 뜨는데 **보드(저장 행 조회)만 빈 칸**이 되어,
        #   같은 값이 한 화면에선 보이고 다른 화면에선 안 보인다. 이 테스트가 그걸 잡았다.
        m.prices.append(
            CostMaterialPrice(
                source="manual",
                unit_price_ex_vat=m.excel_ref_price,
                # ★inc는 유도값이다(×1.1, D-NAO-150 규약) — 「실제로 낸 부가세」가 아니다.
                unit_price_inc_vat=(m.excel_ref_price * SC.VAT_MULTIPLIER).quantize(
                    Decimal("0.01")
                ),
                effective_date=today,
                note=(
                    f"{ADOPT_NOTE_PREFIX} — 원가 정본 엑셀 「{m.excel_label or m.name}」"
                    + (f" · {note}" if note else "")
                ),
            )
        )
        # 종도 함께 승인된다 — 사람이 이 값을 보고 눌렀기 때문이다(계약 §2-2의 «확인분»).
        if m.status != "approved":
            m.status = "approved"
        adopted.append(m.name)

    db.flush()
    result = recompute(db, recipe)
    return {
        "adopted": adopted,
        "skipped_has_price": skipped_has_price,
        "skipped_no_ref": skipped_no_ref,
        "standard": standard_payload(result),
    }


# ──────────────────────────────────────────────
# 조회 payload
# ──────────────────────────────────────────────
def _recipes_query(db: Session):
    return db.query(CostRecipe).options(
        selectinload(CostRecipe.lines)
        .selectinload(CostRecipeLine.material)
        .selectinload(CostMaterial.prices)
    )


def get_recipe(db: Session, recipe_id: int) -> CostRecipe:
    r = _recipes_query(db).filter(CostRecipe.id == recipe_id).first()
    if r is None:
        raise CostMenuError(f"레시피 {recipe_id}이 없다.")
    return r


def standard_payload(result: SC.StandardCostResult) -> dict:
    """★`std_cost_*`가 None인 것과 0인 것은 다르다 — `reason`이 왜 없는지를 말한다."""

    return {
        "computable": result.computable,
        "std_cost_ex_vat": _d(result.std_cost_ex_vat),
        "std_cost_inc_vat": _d(result.std_cost_inc_vat),
        "reason": result.reason,
        "unresolved": list(result.unresolved),
        "partial_ex_vat": _d(result.partial_ex_vat),
        "partial_inc_vat": _d(result.partial_inc_vat),
        "line_count": result.line_count,
        "lines": [
            {
                "label": ln.label,
                "quantity": _d(ln.quantity),
                "unit_price_ex_vat": _d(ln.unit_price_ex_vat),
                "unit_price_inc_vat": _d(ln.unit_price_inc_vat),
                "amount_ex_vat": _d(ln.amount_ex_vat),
                "amount_inc_vat": _d(ln.amount_inc_vat),
                "price_status": ln.price_status,
                "inc_derived": ln.inc_derived,
                "price_source": ln.price_source,
                "price_note": ln.price_note,
                "material_id": ln.material_id,
                "usable": ln.usable,
            }
            for ln in result.lines
        ],
    }


def recipe_payload(db: Session, recipe: CostRecipe, *, with_links: bool = False) -> dict:
    result = SC.compute_standard_cost(_line_inputs(recipe))
    try:
        note = json.loads(recipe.note) if recipe.note else None
    except (TypeError, ValueError):
        note = {"raw": recipe.note}

    payload = {
        "id": recipe.id,
        "product_name": recipe.product_name,
        "form_factor": recipe.form_factor,
        "status": recipe.status,
        "source": recipe.source,
        "recipe_kind": recipe.recipe_kind,
        "anomaly_flag": recipe.anomaly_flag,
        "approved_at": recipe.approved_at.isoformat() if recipe.approved_at else None,
        "match": note,
        "line_count": len(recipe.lines),
        "standard": standard_payload(result),
        "link_count": db.query(CostRecipeLink)
        .filter(CostRecipeLink.recipe_id == recipe.id)
        .count(),
    }
    if with_links:
        payload["links"] = [
            {"internal_sku": l.internal_sku, "status": l.status, "source": l.source}
            for l in db.query(CostRecipeLink)
            .filter(CostRecipeLink.recipe_id == recipe.id)
            .order_by(CostRecipeLink.internal_sku)
            .all()
        ]
    return payload


def list_recipes(db: Session, *, form_factor: Optional[str] = None) -> list[dict]:
    q = _recipes_query(db)
    if form_factor:
        q = q.filter(CostRecipe.form_factor == form_factor)
    rows = q.order_by(CostRecipe.product_name, CostRecipe.form_factor).all()
    return [recipe_payload(db, r) for r in rows]


def board(db: Session) -> dict:
    """표준원가 보드 — **SKU별** 행(계약 §5-3 탭3).

    ★`cost_price`는 **대조 표시**다(계약 §5-4) — 읽기만 하고 격차를 보여준다. 덮어쓰기는
    계약 C의 Jino 승인 지점이다.
    ★미승인·미계산 SKU도 **빠짐없이 실린다** — 안 보이면 「계산 안 되는 것」이 조용히 사라지고,
    그게 커버리지 착시의 출발점이다.
    """

    recipes = _recipes_query(db).all()
    standards = {
        s.recipe_id: s
        for s in db.query(CostStandard)
        .filter(CostStandard.price_rule == DEFAULT_PRICE_RULE)
        .all()
    }
    links = db.query(CostRecipeLink).all()
    by_recipe: dict[int, list[CostRecipeLink]] = {}
    for l in links:
        by_recipe.setdefault(l.recipe_id, []).append(l)

    skus = [l.internal_sku for l in links]
    masters: dict[str, ProductMaster] = {}
    CHUNK = 500
    for i in range(0, len(skus), CHUNK):
        for pm in (
            db.query(ProductMaster)
            .filter(ProductMaster.internal_sku.in_(skus[i : i + CHUNK]))
            .all()
        ):
            masters[pm.internal_sku] = pm

    rows: list[dict] = []
    for recipe in recipes:
        std = standards.get(recipe.id)
        result = SC.compute_standard_cost(_line_inputs(recipe))
        for link in sorted(by_recipe.get(recipe.id, []), key=lambda x: x.internal_sku):
            pm = masters.get(link.internal_sku)
            std_inc = std.std_cost_inc_vat if std else None
            current = pm.cost_price if pm else None
            gap = None
            if std_inc is not None and current not in (None, 0):
                gap = float((std_inc - current) / current * 100)
            rows.append(
                {
                    "internal_sku": link.internal_sku,
                    "product_name": pm.product_name if pm else None,
                    "recipe_id": recipe.id,
                    "recipe_product_name": recipe.product_name,
                    "form_factor": recipe.form_factor,
                    "recipe_status": recipe.status,
                    "link_status": link.status,
                    "std_cost_ex_vat": _d(std.std_cost_ex_vat) if std else None,
                    "std_cost_inc_vat": _d(std_inc),
                    # ★읽기 전용 대조값. 이 계약은 이 칸에 쓰지 않는다(§3 금지선).
                    "current_cost_price": _d(current),
                    "gap_pct": None if gap is None else round(gap, 2),
                    # 왜 값이 없는지 — 빈 칸이 조용하지 않게(§2-7).
                    "reason": None if std else (result.reason or "레시피 미승인 — 계산 안 함"),
                }
            )

    computed = sum(1 for r in rows if r["std_cost_inc_vat"] is not None)
    return {
        "items": rows,
        "sku_count": len(rows),
        "computed_count": computed,
        "uncomputed_count": len(rows) - computed,
        "recipe_count": len(recipes),
        "approved_recipe_count": sum(1 for r in recipes if r.status == "approved"),
    }
