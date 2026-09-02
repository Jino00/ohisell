"""그레인 분할 — 한 레시피에 섞여 있던 «변형»을 갈라 세운다 (계약 D-CPP-67 S1·S2).

★무엇이 막혀 있었나. `CostStandard`가 `recipe_id + price_rule` 유니크라 **레시피당 계산값이
하나**다. 그런데 「상품명 × 폼팩터」 하나에 구성 여럿(매트 flip 5 · fold 4)·크기 여럿
(태블릿 3)이 들어 있어서, 그 묶음은 계산값 1개를 SKU 92개에 들이댈 수 없었고
`classify_group`이 「그레인 불일치 — 보류」로 세워 두고 있었다(ref 124).

★이 모듈이 하는 일은 **레시피를 만드는 것뿐이다.** 값은 한 원도 안 쓴다 —
구성 줄은 `recipes.pick_cost_table_item`이(원가표에서), 계산값은 `recipes.recompute`가,
`product_master.cost_price`는 오직 `cutover.execute`가 쓴다(계약 §3 금지선).
분할이 옳았다면 **기존 두 게이트가 스스로** 92건을 보류에서 꺼낸다 — 판정기를 새로 만들지
않는 것이 이 설계의 요점이다(계약 §2-3).

★**계획표(`PLAN`)는 승인된 결정이다.** 계약 §0-D 12행이 원문이고 Jino가 2026-09-02에
승인했다(§-1). 그래서 이 모듈은 계획을 «세우지» 않고 계획과 라이브가 **같은지 검사**한다 —
한 칸이라도 다르면 `execute`가 거부한다(Q3-B: *"미리보기가 표와 한 칸이라도 다르면 멈춘다"*).
알고리즘이 계획을 만들면 그 순간 승인 대상이 사라진다.

★**귀속은 값이 아니라 신호로 한다**(계약 §2-4). 1차 신호는 상품명이 말하는 것 —
매트는 구성 지문(`truth_source.composition_signature`), 태블릿은 크기 낱말. 현재
`cost_price`는 **2차 대조**일 뿐이다. 값으로 붙이는 매칭은 이 저장소가 세 번 데인 자리다
(2026-08-07 이름 매칭 36건 오판 · D-CPP-59가 「불신 컬럼에서 사람의 픽으로」 옮긴 이유).
두 신호가 어긋나는 SKU는 **자동으로 붙이지 않고 화면에 남긴다.**
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Optional

from sqlalchemy.orm import Session

from app.models import CostRecipe, CostRecipeLink, CostTableItem, ProductMaster

from . import recipes as RC
from .materials import CostMenuConflict, CostMenuError
from .truth_source import composition_signature

# ──────────────────────────────────────────────
# 1차 신호 — 상품명이 말하는 것
# ──────────────────────────────────────────────

#: 태블릿 크기 등급. **낱말 순서가 규칙의 일부다** — 「울트라」를 먼저 본다.
#: ★라이브 36건 전수(2026-09-02 ref 124 §2-3 확장 실측)로 세운 규칙이지 추론이 아니다:
#:   울트라 4 · 플러스 11 · 기본 21이 현재 원가 3묶음(7,652.70 / 5,430.70 / 4,902.70)과
#:   정확히 겹친다. 「뮤패드K10플러스」처럼 «제품 이름에 플러스가 든» 건도 실제로 플러스
#:   칸에 있어 낱말이 오탐이 아님을 확인했다.
TABLET_ULTRA_WORDS = ("울트라",)
TABLET_PLUS_WORDS = ("플러스", "13인치", "12.9")


def tablet_size_grade(product_name: Optional[str]) -> Optional[str]:
    """태블릿 상품명 → 크기 등급. 낱말이 없으면 `None`(= 사람이 지정해야 한다)."""

    name = product_name or ""
    if any(w in name for w in TABLET_ULTRA_WORDS):
        return "울트라"
    if any(w in name for w in TABLET_PLUS_WORDS):
        return "플러스"
    if name.strip():
        return "기본"
    return None


def composition_key(product_name: Optional[str]) -> tuple:
    """매트 상품명 → 구성 지문. `truth_source`의 것을 **그대로 쓴다**.

    ★지문 함수를 여기서 새로 쓰지 않는 이유: 분할의 옳음을 판정하는 게이트
    (`classify_group`의 `name_grain_kinds`)가 바로 그 함수를 쓴다. 둘이 갈라지면
    「분할했는데 게이트가 안 풀리는」 침묵이 생긴다.
    """

    return composition_signature(product_name)


# ──────────────────────────────────────────────
# 계획표 — 계약 §0-D 12행 (승인된 결정)
# ──────────────────────────────────────────────


@dataclass(frozen=True)
class VariantSpec:
    """한 변형이 무엇이고 어느 원가표 줄을 갖는가."""

    variant: str
    #: 1차 신호의 값 — 이 값이 나오는 SKU가 이 변형에 붙는다
    signal: tuple | str
    #: 원가표 항목 이름(`CostTableItem.item_name`) — **정확히 일치해야 한다**
    cost_table_item: str
    #: 계약 §0-D 표가 적은 SKU 수. 라이브가 이와 다르면 `execute`가 거부한다
    expected_skus: int
    #: 기존 레시피가 그대로 되는 변형(원가표 줄이 이미 픽돼 있다)
    is_base: bool = False


@dataclass(frozen=True)
class GroupPlan:
    product_name: str
    form_factor: str
    #: `"composition"` | `"tablet_size"`
    signal_kind: str
    cost_table_section: str
    variants: tuple[VariantSpec, ...]

    @property
    def key(self) -> tuple[str, str]:
        return (self.product_name, self.form_factor)


_MATTE = "오하이 빛반사, 지문방지 매트 필름 3매"
_TABLET = "저반사 지문방지 PET 깨지지 않는 태블릿 액정보호필름 2매"

#: ★계약 §0-D의 표 그대로다. **여기 숫자를 라이브에 맞춰 고치지 마라** — 어긋나면
#:   그것이 「멈춰서 Jino에게 보인다」의 발동 조건이다(Q3-B).
PLAN: tuple[GroupPlan, ...] = (
    GroupPlan(
        product_name=_MATTE,
        form_factor="fold",
        signal_kind="composition",
        cost_table_section="모바일 필름-폴드",
        variants=(
            VariantSpec("외3+내3", (("3", "3", "3"), ("내부", "외부")),
                        "지문방지_내부3매+외부3매", 9, is_base=True),
            VariantSpec("외3", (("3", "3"), ("외부",)),
                        "지문방지_외부3매", 9),
            VariantSpec("외3+내3+후2", (("2", "3", "3", "3"), ("후면", "내부", "외부")),
                        "지문방지_내부3매+외부3매+후면2", 6),
            VariantSpec("외3+내3+후2+힌2", (("2", "2", "3", "3", "3"), ("후면", "힌지", "내부", "외부")),
                        "지문방지_내부3매+외부3매+후면2+힌지2", 6),
        ),
    ),
    GroupPlan(
        product_name=_MATTE,
        form_factor="flip",
        signal_kind="composition",
        cost_table_section="모바일 필름-플립",
        variants=(
            VariantSpec("외3+내3", (("3", "3", "3"), ("내부", "외부")),
                        "지문방지_내부3매+외부3매", 7, is_base=True),
            VariantSpec("외3", (("3", "3"), ("외부",)),
                        "지문방지_외부3매", 7),
            VariantSpec("외3+내3+후2", (("2", "3", "3", "3"), ("후면", "내부", "외부")),
                        "지문방지_내부3매+외부3매+후면2", 6),
            VariantSpec("외3+내3+후2+힌2", (("2", "2", "3", "3", "3"), ("후면", "힌지", "내부", "외부")),
                        "지문방지_내부3매+외부3매+후면2+힌지2", 5),
            VariantSpec("내3", (("3", "3"), ("내부",)),
                        "지문방지_내부3매", 1),
        ),
    ),
    GroupPlan(
        product_name=_TABLET,
        form_factor="tablet",
        signal_kind="tablet_size",
        cost_table_section="태블릿 필름",
        variants=(
            VariantSpec("기본", "기본", "지문방지 PET 2매_기본", 21, is_base=True),
            VariantSpec("플러스", "플러스", "지문방지 PET 2매_플러스,12.9형,13인치", 11),
            VariantSpec("울트라", "울트라", "지문방지 PET 2매_울트라", 4),
        ),
    ),
)

_SIGNAL_FN: dict[str, Callable[[Optional[str]], object]] = {
    "composition": composition_key,
    "tablet_size": tablet_size_grade,
}


def plan_for(product_name: str, form_factor: Optional[str]) -> Optional[GroupPlan]:
    for p in PLAN:
        if p.product_name == product_name and p.form_factor == form_factor:
            return p
    return None


# ──────────────────────────────────────────────
# 미리보기 — 「계획과 라이브가 같은가」
# ──────────────────────────────────────────────


@dataclass
class _SkuRow:
    internal_sku: str
    product_name: Optional[str]
    cost_price: Optional[Decimal]
    signal: object
    variant: Optional[str]
    link_status: str
    note: str = ""
    conflict: Optional[str] = None


def _base_recipe(db: Session, plan: GroupPlan) -> Optional[CostRecipe]:
    """분할의 «출발» 레시피 — 이 묶음에서 아직 안 갈라진 것.

    ★분할 뒤에는 base 변형이 자기 이름을 갖는다(예 `외3+내3`). 그래서 여기서는
    `variant == ""`(아직 안 갈라짐)를 먼저 찾고, 없으면 base 변형 이름으로 찾는다 —
    **이 순서라야 두 번 실행해도 새 레시피를 또 만들지 않는다**(멱등).
    """

    base_variant = next((v.variant for v in plan.variants if v.is_base), "")
    for variant in ("", base_variant):
        hit = (
            db.query(CostRecipe)
            .filter(
                CostRecipe.product_name == plan.product_name,
                CostRecipe.form_factor == plan.form_factor,
                CostRecipe.variant == variant,
            )
            .first()
        )
        if hit is not None:
            return hit
    return None


def _group_recipes(db: Session, plan: GroupPlan) -> dict[str, CostRecipe]:
    rows = (
        db.query(CostRecipe)
        .filter(
            CostRecipe.product_name == plan.product_name,
            CostRecipe.form_factor == plan.form_factor,
        )
        .all()
    )
    return {r.variant: r for r in rows}


def _cost_table_item(db: Session, plan: GroupPlan, spec: VariantSpec) -> Optional[CostTableItem]:
    return (
        db.query(CostTableItem)
        .filter(
            CostTableItem.section == plan.cost_table_section,
            CostTableItem.item_name == spec.cost_table_item,
        )
        .first()
    )


def _collect_skus(db: Session, plan: GroupPlan, recipes: dict[str, CostRecipe]) -> list[_SkuRow]:
    """이 묶음의 SKU 전건 — **분할 전이든 후든 같은 모집단**이 나와야 한다.

    ★분할 «후»에도 미리보기가 옳게 서려면 이미 옮겨 간 링크까지 모아야 한다.
    한 레시피만 보면 실행 뒤 미리보기가 「9건이 사라졌다」고 거짓말한다.
    """

    signal_fn = _SIGNAL_FN[plan.signal_kind]
    by_signal = {v.signal: v.variant for v in plan.variants}

    ids = [r.id for r in recipes.values()]
    if not ids:
        return []
    links = (
        db.query(CostRecipeLink)
        .filter(CostRecipeLink.recipe_id.in_(ids))
        .order_by(CostRecipeLink.internal_sku)
        .all()
    )
    skus = [l.internal_sku for l in links]
    masters = {
        m.internal_sku: m
        for m in db.query(ProductMaster).filter(ProductMaster.internal_sku.in_(skus)).all()
    }

    rows: list[_SkuRow] = []
    for link in links:
        master = masters.get(link.internal_sku)
        name = master.product_name if master else None
        signal = signal_fn(name)
        rows.append(
            _SkuRow(
                internal_sku=link.internal_sku,
                product_name=name,
                cost_price=master.cost_price if master else None,
                signal=signal,
                variant=by_signal.get(signal),
                link_status=link.status,
            )
        )
    return rows


def _cross_check(rows: list[_SkuRow]) -> None:
    """2차 대조 — 1차 신호가 가른 묶음이 현재 원가와 «1:1»인가.

    ★값으로 «붙이지» 않는다. 값은 1차 신호가 옳았는지 되묻는 데만 쓴다(계약 §2-4).
    어긋나면 그 SKU에 `conflict`가 서고 실행이 거부된다 — 조용히 넘어가지 않는다.
    """

    prices_by_variant: dict[str, set] = {}
    for r in rows:
        if r.variant is None:
            continue
        prices_by_variant.setdefault(r.variant, set()).add(
            None if r.cost_price is None else Decimal(str(r.cost_price))
        )

    # ① 한 변형 안에서 현재 원가가 여러 종
    multi = {v for v, ps in prices_by_variant.items() if len(ps) > 1}
    # ② 서로 다른 변형이 같은 현재 원가 — 신호가 가른 것을 값이 부정한다
    seen: dict[object, str] = {}
    shared: set[str] = set()
    for variant, ps in prices_by_variant.items():
        for p in ps:
            if p in seen and seen[p] != variant:
                shared.add(variant)
                shared.add(seen[p])
            seen[p] = variant

    for r in rows:
        if r.variant is None:
            r.conflict = "1차 신호 없음 — 상품명이 변형을 말하지 않는다(사람이 지정해야 한다)"
        elif r.variant in multi:
            r.conflict = f"2차 대조 불일치 — 변형 「{r.variant}」 안에서 현재 원가가 여러 종이다"
        elif r.variant in shared:
            r.conflict = f"2차 대조 불일치 — 변형 「{r.variant}」가 다른 변형과 현재 원가를 공유한다"


def _note_for(row: _SkuRow, plan: GroupPlan) -> str:
    price = "없음" if row.cost_price is None else f"{Decimal(str(row.cost_price)):,}"
    if plan.signal_kind == "composition":
        sheets, words = row.signal  # type: ignore[misc]
        signal_text = f"구성 지문 매수{list(sheets)}·낱말{list(words)}"
    else:
        signal_text = f"기종명 크기 낱말 「{row.signal}」"
    return (
        f"D-CPP-67 분할 — 1차 신호: {signal_text} → 변형 「{row.variant}」 / "
        f"2차 대조: 현재 원가 {price}원 (변형 안에서 1종)"
    )


def preview(db: Session) -> dict:
    """클릭 «전»에 서는 것 — 계획표 12행과 라이브가 어디서 다른가.

    ★읽기 전용이다. 이 payload가 곧 Q3-B의 「멈추는 조건」의 재료이고, 화면과 실행이
    **같은 함수**를 본다(둘이 갈라지면 화면이 초록인데 실행이 다른 일을 한다).
    """

    groups: list[dict] = []
    all_ok = True

    for plan in PLAN:
        recipes = _group_recipes(db, plan)
        base = _base_recipe(db, plan)
        rows = _collect_skus(db, plan, recipes)
        _cross_check(rows)

        counted: dict[str, int] = {}
        for r in rows:
            if r.variant is not None and r.conflict is None:
                counted[r.variant] = counted.get(r.variant, 0) + 1

        variant_rows = []
        group_ok = base is not None and bool(rows)
        for spec in plan.variants:
            item = _cost_table_item(db, plan, spec)
            live = counted.get(spec.variant, 0)
            existing = recipes.get(spec.variant)
            ok = live == spec.expected_skus and item is not None
            if not ok:
                group_ok = False
            variant_rows.append(
                {
                    "variant": spec.variant,
                    "is_base": spec.is_base,
                    "cost_table_item": spec.cost_table_item,
                    "cost_table_item_id": item.id if item else None,
                    "cost_table_item_total": RC._d(item.total_inc_vat) if item else None,
                    "expected_skus": spec.expected_skus,
                    "live_skus": live,
                    "matches_plan": ok,
                    "recipe_id": existing.id if existing else None,
                    "recipe_status": existing.status if existing else None,
                    "reason": (
                        None
                        if ok
                        else (
                            "원가표에 그 줄이 없다"
                            if item is None
                            else f"계획 {spec.expected_skus}건 ↔ 라이브 {live}건 — 표와 다르다"
                        )
                    ),
                }
            )

        unassigned = [
            {
                "internal_sku": r.internal_sku,
                "product_name": r.product_name,
                "cost_price": RC._d(r.cost_price),
                "reason": r.conflict,
            }
            for r in rows
            if r.conflict is not None
        ]
        if unassigned:
            group_ok = False

        groups.append(
            {
                "product_name": plan.product_name,
                "form_factor": plan.form_factor,
                "signal_kind": plan.signal_kind,
                "base_recipe_id": base.id if base else None,
                "base_recipe_status": base.status if base else None,
                "sku_count": len(rows),
                "variants": variant_rows,
                "unassigned": unassigned,
                "matches_plan": group_ok,
                "reason": (
                    None
                    if group_ok
                    else ("출발 레시피를 못 찾았다" if base is None else "아래 행이 계획표와 다르다")
                ),
            }
        )
        all_ok = all_ok and group_ok

    return {
        "contract": "D-CPP-67",
        "groups": groups,
        "plan_sku_total": sum(v.expected_skus for p in PLAN for v in p.variants),
        "live_sku_total": sum(g["sku_count"] for g in groups),
        "safe_to_execute": all_ok,
        "sentence": (
            "계획표 12행과 라이브가 전부 같다 — 실행할 수 있다"
            if all_ok
            else "계획표와 다른 칸이 있다 — 실행은 거부된다. 다른 칸을 Jino에게 보여야 한다"
        ),
    }


# ──────────────────────────────────────────────
# 실행 — 레시피를 갈라 세운다 (값은 한 원도 안 쓴다)
# ──────────────────────────────────────────────


class GrainSplitRefused(CostMenuConflict):
    """계획표와 라이브가 다르다 — 멈추고 그 칸을 사람에게 보인다(계약 Q3-B)."""


def execute(db: Session, *, actor: str = "unknown", scope: str = "all") -> dict:
    """계획표 12행을 실재하는 레시피로 만든다.

    ★**미리보기가 계획표와 한 칸이라도 다르면 거부한다.** 이것이 Jino가 승인한 자동 진행의
    조건이다(계약 §-1 Q3-B). 「대부분 맞으니 되는 것만 하자」는 없다 — 표가 승인 대상이고,
    표와 다른 라이브는 표를 다시 볼 이유이지 넘어갈 이유가 아니다.
    ★**값에 안 닿는다.** `product_master.cost_price`는 이 함수의 어느 경로에서도 안 바뀐다
    (계약 §3 금지선). 갈라 세운 뒤 값을 옮기는 것은 D-CPP-64 S3의 컷오버 문이 한다.
    ★커밋은 **라우터가 한다** — `pick_cost_table_item`·`approve_recipe`와 같은 관례다.
    """

    if scope != "all":
        raise CostMenuError("scope는 지금 'all'만 지원한다 — 부분 실행은 계획표를 쪼갠다.")

    pre = preview(db)
    if not pre["safe_to_execute"]:
        raise GrainSplitRefused(
            "계획표(D-CPP-67 §0-D 12행)와 라이브가 다르다 — 실행을 거부한다. "
            "미리보기의 `matches_plan: false` 행과 `unassigned`를 Jino에게 보일 것."
        )

    created: list[dict] = []
    moved = 0
    renamed: list[dict] = []

    for plan in PLAN:
        recipes = _group_recipes(db, plan)
        base = _base_recipe(db, plan)
        assert base is not None  # preview가 이미 막았다
        rows = _collect_skus(db, plan, recipes)
        _cross_check(rows)

        base_spec = next(v for v in plan.variants if v.is_base)
        by_variant: dict[str, CostRecipe] = {}

        for spec in plan.variants:
            if spec.is_base:
                by_variant[spec.variant] = base
                continue
            existing = recipes.get(spec.variant)
            if existing is not None:
                by_variant[spec.variant] = existing
                continue

            item = _cost_table_item(db, plan, spec)
            assert item is not None  # preview가 이미 막았다
            recipe = CostRecipe(
                product_name=plan.product_name,
                form_factor=plan.form_factor,
                variant=spec.variant,
                status="draft",
                source="excel",
                recipe_kind=base.recipe_kind,
            )
            db.add(recipe)
            db.flush()
            RC.pick_cost_table_item(db, recipe.id, item.id)
            by_variant[spec.variant] = recipe
            created.append(
                {
                    "recipe_id": recipe.id,
                    "product_name": plan.product_name,
                    "form_factor": plan.form_factor,
                    "variant": spec.variant,
                    "cost_table_item": spec.cost_table_item,
                    "line_count": len(recipe.lines),
                }
            )

        # ── 링크 이동 — 이동 1건마다 근거가 남는다(계약 §2-6) ──────────────
        for row in rows:
            target = by_variant[row.variant]  # type: ignore[index]
            link = (
                db.query(CostRecipeLink)
                .filter(CostRecipeLink.internal_sku == row.internal_sku)
                .filter(CostRecipeLink.recipe_id.in_([r.id for r in by_variant.values()]))
                .first()
            )
            if link is None:
                continue
            note = _note_for(row, plan)
            if link.recipe_id != target.id:
                link.recipe_id = target.id
                moved += 1
            link.note = note

        # ── base 레시피에 이름을 준다 (마지막에 — 유니크 충돌을 안 만든다) ──
        if base.variant == "" and base_spec.variant:
            base.variant = base_spec.variant
            renamed.append(
                {"recipe_id": base.id, "form_factor": plan.form_factor, "variant": base_spec.variant}
            )

        # ── 승인 — 구성이 선 새 레시피만 ─────────────────────────────────
        for spec in plan.variants:
            if spec.is_base:
                continue
            recipe = by_variant[spec.variant]
            if recipe.status != "approved" and recipe.lines:
                RC.approve_recipe(db, recipe.id)

    db.flush()
    after = preview(db)
    return {
        "contract": "D-CPP-67",
        "actor": actor,
        "created_recipes": created,
        "renamed_base": renamed,
        "moved_links": moved,
        "preview_after": after,
    }
