"""원가 정본 판별층 — SKU마다 «정본이 무엇인가»를 답한다 (계약 D-CPP-64 §4 S2).

★**이 층은 쓰기가 없다.** `product_master.cost_price`를 읽어 «대조»만 한다. 컷오버(쓰기)는
S3가 신설하는 경로 한 벌의 몫이고, 이 파일에 `.cost_price =` 대입이 생기면 계약 §3-B의
「컷오버 경로 밖에서 `cost_price`를 쓰는 코드를 새로 만들지 않는다」를 어기는 것이다.

★**왜 필요한가** (ref 119 §2-2): 감시기가 「엑셀 하나 ↔ `cost_price`」를 대조하는 한
「447건이 어긋난 채 감시기는 초록」(ref 118 §0-E)이 구조적으로 재생산된다. 매입품엔 계산값이
**원리적으로 없으므로** 정본은 SKU마다 다르고, 그래서 감시기보다 **먼저** 「이 SKU의 정본이
무엇인가」를 답하는 층이 있어야 한다. 이 파일이 그 층이다.

★**정본의 원천은 셋뿐이다** (계약 §2-1): 승인 레시피의 `cost_standard`(조립품) · 승인된
`cost_purchased_price`(매입품) · 둘 다 없으면 **「정본 없음」을 그대로 표시**한다.
분류를 «추측»해서 정본을 지정하지 않는다 — draft 78개 레시피의 `recipe_kind`는 전건
기본값이라(ref 119 §2-1) 그 위에서 추정하면 2026-08-10 71건 사고(추론=확인 동일시)의 재판이다.

★**없음 ≠ 0** (계약 §3-B): 정본이 없거나 보류인 행의 정본값·격차는 `None`이다. 0으로 채우면
「원가가 0원인 상품」과 「정본을 모르는 상품」이 화면에서 같은 얼굴이 된다.

★**승인된 매입가는 `purchased_price.load_approved_prices()`가 단일 출처다.** 여기서 다시
질의하지 않는다 — `approved_at IS NOT NULL` + 「최신 1건」 규칙이 두 벌이 되는 순간
카드와 보드가 다른 답을 내고, 그건 이 저장소가 이미 겪은 병이다(계약 §2-2 · 교훈 #375).

분류 규칙의 출처는 ref 118 §3(447건 원인별 분해)이고, 이 파일은 그 **임시 진단 질의를 층으로
옮긴 것**이다. 숫자를 박아 두지 않고 매번 라이브에서 다시 센다 — 그래야 D-CPP-63이 매입가를
승인하거나 레시피가 보강될 때 표가 **따라 움직인다**(계약 §4 S2 「숫자가 움직인다」).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ...models import (
    CostRecipe,
    CostRecipeLine,
    CostRecipeLink,
    CostStandard,
    ProductMaster,
)
from . import price_rule as PR
from .purchased_price import load_approved_prices

# ──────────────────────────────────────────────
# 정본 유형 — 화면의 첫 칸
# ──────────────────────────────────────────────
TRUTH_COMPUTED = "computed"    # 계산값이 정본
TRUTH_PURCHASED = "purchased"  # 매입가가 정본
TRUTH_HELD = "held"            # 보류 — 정본을 지금 못 정한다
TRUTH_NONE = "none"            # 정본 없음 — 원천이 아직 없다

TRUTH_LABELS = {
    TRUTH_COMPUTED: "계산값",
    TRUTH_PURCHASED: "매입가",
    TRUTH_HELD: "보류",
    TRUTH_NONE: "정본 없음",
}

# ──────────────────────────────────────────────
# 판정에 쓰는 상수 — 전부 ref 118에 출처가 있다
# ──────────────────────────────────────────────

#: ref 118 §3-1 — 엑셀이 세지 않는 부자재 4종의 합.
#: cleaning kit 209.9[ledger] + 알콜솜 2EA 66.0 + 비닐(9*18) 8.8 + 비닐(12*22+4) 14.3
PARTS_GAP = Decimal("299.0")
PARTS_GAP_EPSILON = Decimal("0.01")

#: ref 118 §2 — 「일치」로 보는 폭. 0.5원 미만이면 반올림 잔차다.
MATCH_EPSILON = Decimal("0.5")

#: 링크 없는 SKU를 가르는 표지. `product_master.category`는 963 중 940이 NULL이라
#: 분류에 못 쓴다(ref 119 §2-1) — 그래서 상품명의 표지를 본다. 규칙을 사유 문장에
#: 그대로 실어 화면에서 감사 가능하게 둔다.
DUPE_MARKER = "[중복]"
APPAREL_MARKERS = ("리바이스", "청바지")

# ──────────────────────────────────────────────
# 사유 코드 — ref 118 §3의 G 분해를 그대로 옮긴다
# ──────────────────────────────────────────────
CAUSE_MATCH = "match"
CAUSE_PARTS_299 = "g2_parts_299"
CAUSE_FAMILY_NOT_SPLIT = "g3_2_family_not_split"
CAUSE_GRAIN_MISMATCH = "g1_grain_mismatch"
CAUSE_IMPORTED_SINGLE_LINE = "g3_1_imported_single_line"
CAUSE_RESIDUAL = "g3_residual"
CAUSE_NO_STANDARD = "no_standard"
CAUSE_TWO_GROUNDS = "two_grounds"
CAUSE_PURCHASED_APPROVED = "purchased_approved"
CAUSE_DRAFT_LINK = "draft_link"
CAUSE_NO_LINK_DUPE = "no_link_dupe"
CAUSE_NO_LINK_APPAREL = "no_link_apparel"
CAUSE_NO_LINK_OTHER = "no_link_other"

#: ref 118 §3 표의 「원인」 열 — 화면 집계를 그 표와 나란히 놓기 위한 이름표.
CAUSE_REF118 = {
    CAUSE_GRAIN_MISMATCH: "G1",
    CAUSE_PARTS_299: "G2",
    CAUSE_IMPORTED_SINGLE_LINE: "G3-1",
    CAUSE_FAMILY_NOT_SPLIT: "G3-2",
    CAUSE_RESIDUAL: "G3-3·4·5",
    CAUSE_MATCH: "(일치)",
}

#: 소관 — 「이 행을 움직일 수 있는 곳」. 빈 칸을 남기지 않는다(계약 §4 S2-2).
OWNER_TRACK_A2 = "트랙 A2 — 그레인 정의"
OWNER_TRACK_A1A2 = "트랙 A1·A2 — 레시피·구성"
OWNER_DCPP63 = "D-CPP-63 — 매입가 근거"
OWNER_DCPP63_OR_A1A2 = "D-CPP-63(매입품) 또는 트랙 A1·A2(조립 초안) — 아직 안 갈렸다"
OWNER_NONE = "소관 없음"
OWNER_CUTOVER = "계약 D-CPP-64 S3 — 컷오버"

#: ★계약 §1 표는 G3-1 58건의 소관을 「1줄 레시피 보강 · 트랙 A2」로 적었다. 그런데 실측은
#: 다른 것을 가리킨다 — 세 레시피(r39·40·41)는 전부 `status='approved'` +
#: `recipe_kind='imported_goods'`이고, 구성 줄의 note가 스스로 *"수입 완제품 — 원장 로트
#: 단가가 이 종에 붙는다"*라고 적어 뒀으며 `picked_by_human: true`다. 즉 **구성이 1줄인 것은
#: 결함이 아니라 종류이고, 보강할 부자재가 애초에 없다.**
#: ⇒ 버킷(보류)은 계약대로 두되 — 옮기면 §4 합격기준의 숫자가 바뀌고 그건 새 계약이다 —
#:   **화면의 사유는 실측을 말한다.** 화면이 「레시피 보강 필요」라고 하면 이 트랙이 없애려는
#:   바로 그 병(화면이 거짓을 말함)을 이 층이 만드는 것이기 때문이다(계약 §2-7).
G31_CONTRACT_NOTE = (
    "⚠️ 계약 §1 표는 이 묶음을 「1줄 레시피 보강 · 소관 트랙 A2」로 적었으나, "
    "실측은 매입품을 가리킨다(승인 recipe_kind=imported_goods · 승인된 매입가 보유 0건)"
)


@dataclass(frozen=True)
class RecipeGroup:
    """승인 레시피 1건과 그에 매달린 SKU들의 사실 — 판정의 입력.

    grain이 «레시피»인 이유: `cost_standard`가 레시피당 1행이라 계산값이 레시피 단위로만
    존재한다(ref 118 §3-4). SKU마다 다른 `cost_price`를 한 값과 비교하는 것이 성립하지
    않는다는 것 자체가 G1의 정의다.
    """

    recipe_id: int
    product_name: str
    form_factor: Optional[str]
    recipe_kind: str
    skus: tuple[str, ...]
    cost_price_kinds: int
    cost_price_min: Optional[Decimal]
    std_cost_inc_vat: Optional[Decimal]
    line_count: int

    @property
    def gap(self) -> Optional[Decimal]:
        """정본(계산값) − 현재값. 비교가 성립할 때만 값이 있다."""
        if self.std_cost_inc_vat is None or self.cost_price_min is None:
            return None
        if self.cost_price_kinds > 1:
            # ★G1 — 뺄셈이 성립하지 않는다. `None`이 정답이고 0이 아니다.
            return None
        return self.std_cost_inc_vat - self.cost_price_min


def _family_not_split(group: RecipeGroup, siblings: tuple[RecipeGroup, ...]) -> Optional[RecipeGroup]:
    """계열 미분리(ref 118 §3-2)의 형제를 찾는다 — 없으면 None.

    ★판별식은 «form_factor가 flip/fold인가»가 **아니다.** 그렇게 가르면 26건이 잡히는데
    실제 G3-2는 9건이다(2026-09-01 prod 실측). 병의 모양은 폼팩터가 아니라 이것이다:
    **같은 상품명 형제와 현재 원가는 같은데 계산값이 다르다** — `cost_price`가 폼팩터
    분리를 안 따라간 것. r44·r45(힌지 필름)는 현재 원가도 계산값도 서로 같아서
    (890.0 / 923.8) 정상적으로 걸러진다.
    """

    if group.cost_price_min is None or group.std_cost_inc_vat is None:
        return None
    for sib in siblings:
        if sib.recipe_id == group.recipe_id:
            continue
        if sib.cost_price_min is None or sib.std_cost_inc_vat is None:
            continue
        same_current = sib.cost_price_min == group.cost_price_min
        different_truth = abs(sib.std_cost_inc_vat - group.std_cost_inc_vat) >= MATCH_EPSILON
        if same_current and different_truth:
            return sib
    return None


def classify_group(
    group: RecipeGroup,
    siblings: tuple[RecipeGroup, ...],
    *,
    has_approved_purchase: bool = False,
) -> tuple[str, str, str, str]:
    """승인 레시피 묶음 → (사유코드, 정본유형, 사유 문장, 소관). **순수 함수.**

    판정 순서가 곧 규칙이다 — ref 118 §3의 분해를 재현하려면 이 순서여야 한다:
    근거 둘 → G1 → 일치 → G3-1 → G2 → G3-2 → 잔여.
    """

    if has_approved_purchase:
        # ★원천이 둘이면 시스템이 고르지 않는다(계약 §2-1). 지금 라이브에선 0건이지만,
        #   D-CPP-63이 진행되면 생길 수 있는 자리다. 조용히 한쪽을 고르면 그 선택이
        #   화면에 안 보인 채 손익으로 흘러간다.
        return (
            CAUSE_TWO_GROUNDS,
            TRUTH_HELD,
            "근거가 둘이다 — 승인 레시피의 계산값과 승인된 매입가가 함께 있다. "
            "어느 쪽이 정본인지는 사람이 정한다(시스템이 고르지 않는다)",
            OWNER_DCPP63,
        )

    if group.std_cost_inc_vat is None:
        return (
            CAUSE_NO_STANDARD,
            TRUTH_HELD,
            "승인된 레시피인데 계산값이 없다 — 재계산이 선행이다",
            OWNER_TRACK_A1A2,
        )

    if group.cost_price_kinds > 1:
        return (
            CAUSE_GRAIN_MISMATCH,
            TRUTH_HELD,
            f"그레인 불일치 — 레시피는 계산값 1개인데 이 묶음의 SKU가 현재 원가를 "
            f"{group.cost_price_kinds}종 갖고 있다. 격차를 뺄셈으로 재는 것 자체가 성립하지 않는다",
            OWNER_TRACK_A2,
        )

    gap = group.gap
    assert gap is not None  # cost_price_kinds == 1 이고 std가 있으면 항상 값이 있다

    if abs(gap) < MATCH_EPSILON:
        return (
            CAUSE_MATCH,
            TRUTH_COMPUTED,
            f"계산값과 현재 원가가 일치한다(격차 {gap:+.1f}원)",
            OWNER_CUTOVER,
        )

    if group.line_count == 1 and (group.recipe_kind or "") == "imported_goods":
        return (
            CAUSE_IMPORTED_SINGLE_LINE,
            TRUTH_HELD,
            "수입 완제품 — 구성이 1줄인 것은 결함이 아니라 종류다(원장 로트 단가가 그 한 줄이다). "
            f"격차 {gap:+.1f}원이지만 이 묶음은 레시피마다 격차 방향이 달라 계약이 판정을 보류했다. "
            + G31_CONTRACT_NOTE,
            OWNER_DCPP63,
        )

    if group.line_count == 1:
        return (
            CAUSE_IMPORTED_SINGLE_LINE,
            TRUTH_HELD,
            f"구성이 1줄뿐이라 계산이 불완전하다(격차 {gap:+.1f}원) — 부자재 보강이 선행이다",
            OWNER_TRACK_A1A2,
        )

    if abs(abs(gap) - PARTS_GAP) < PARTS_GAP_EPSILON:
        return (
            CAUSE_PARTS_299,
            TRUTH_COMPUTED,
            f"엑셀이 부자재 4종을 안 세고 있다 — 격차가 정확히 {gap:+.1f}원"
            "(cleaning kit 209.9 + 알콜솜 2EA 66.0 + 비닐 8.8 + 비닐 14.3 = 299.0). 계산이 정본",
            OWNER_CUTOVER,
        )

    sib = _family_not_split(group, siblings)
    if sib is not None:
        return (
            CAUSE_FAMILY_NOT_SPLIT,
            TRUTH_COMPUTED,
            f"계열 미분리 — 형제 레시피 r{sib.recipe_id}"
            f"({sib.form_factor or '-'})와 현재 원가가 같은데({group.cost_price_min}) "
            f"계산값은 다르다({group.std_cost_inc_vat} vs {sib.std_cost_inc_vat}). "
            f"현재 원가가 폼팩터 분리를 안 따라갔다 — 계산이 정본(격차 {gap:+.1f}원)",
            OWNER_CUTOVER,
        )

    return (
        CAUSE_RESIDUAL,
        TRUTH_HELD,
        f"잔여 격차 {gap:+.1f}원 — 단일 원인으로 설명되지 않는다. 개별 확인이 선행이다",
        OWNER_TRACK_A1A2,
    )


def classify_ungrounded(
    product_name: str,
    *,
    has_draft_link: bool,
) -> tuple[str, str, str, str]:
    """승인 근거가 없는 SKU → (사유코드, 정본유형, 사유 문장, 소관). **순수 함수.**

    ref 119 §2의 갈래를 그대로 옮긴다. 「정본 없음」이 한 덩어리로 보이면 소관이 사라지고,
    소관이 사라지면 아무도 안 움직인다.
    """

    if has_draft_link:
        return (
            CAUSE_DRAFT_LINK,
            TRUTH_NONE,
            "초안(draft) 레시피에만 연결돼 있다 — 승인된 근거가 아직 없다. "
            "매입품인지 조립품인지는 `recipe_kind`가 전건 기본값이라 아직 안 갈렸다"
            "(추측으로 정하지 않는다)",
            OWNER_DCPP63_OR_A1A2,
        )

    if DUPE_MARKER in (product_name or ""):
        return (
            CAUSE_NO_LINK_DUPE,
            TRUTH_NONE,
            f"상품명에 「{DUPE_MARKER}」 표지가 있다 — 정리 대상이지 원가를 세울 대상이 아니다",
            OWNER_NONE,
        )

    if any(m in (product_name or "") for m in APPAREL_MARKERS):
        return (
            CAUSE_NO_LINK_APPAREL,
            TRUTH_NONE,
            "의류 — 다른 사업이다. 이 원가 체계의 대상이 아니다"
            f"(판별: 상품명에 {'·'.join(APPAREL_MARKERS)} 포함)",
            OWNER_NONE,
        )

    return (
        CAUSE_NO_LINK_OTHER,
        TRUTH_NONE,
        "레시피에 연결된 적이 없다 — 세트·멀티팩 변형이거나 신기종이다. 레시피를 먼저 세워야 한다",
        OWNER_TRACK_A1A2,
    )


# ──────────────────────────────────────────────
# DB 얇은 층 — 위의 순수 판정을 라이브 사실에 적용한다
# ──────────────────────────────────────────────


def _breakdown_line_count(std: Optional[CostStandard]) -> Optional[int]:
    """계산값이 몇 줄로 이루어졌나. ref 118 §6이 쓴 `json_each(breakdown)`와 같은 수.

    `None`은 «breakdown으로는 못 센다»는 뜻이다 — 그때만 `cost_recipe_line`을 세는
    폴백으로 넘어간다. 0을 돌려주면 「줄이 없다」와 「못 셌다」가 같은 값이 되고,
    `line_count == 1`이 G3-1의 판별식이라 그 혼동이 곧 오분류다.
    """
    if std is None or not std.breakdown:
        return None
    try:
        parsed = json.loads(std.breakdown)
    except (ValueError, TypeError):
        return None
    return len(parsed) if isinstance(parsed, list) else None


def _dec(v) -> Optional[Decimal]:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _s(v: Optional[Decimal]) -> Optional[str]:
    """Decimal → 문자열. JSON에 float로 흘리지 않는다(원 단위가 흔들린다)."""
    return None if v is None else str(v)


def truth_board(db: Session) -> dict:
    """963 전 SKU의 정본 판별 — 계약 D-CPP-64 §4 S2의 표.

    반환은 `response_model` 없이 그대로 HTTP body가 된다(라우터 docstring 교훈 #321).
    """

    rule = PR.read_rule(db)

    masters = db.query(ProductMaster).all()
    links = db.query(CostRecipeLink).all()
    recipes = {r.id: r for r in db.query(CostRecipe).all()}
    standards = {
        s.recipe_id: s
        for s in db.query(CostStandard).filter(CostStandard.price_rule == rule).all()
    }
    # ★단일 출처 — 여기서 `approved_at`을 다시 해석하지 않는다(§2-2).
    approved_purchase = load_approved_prices(db)

    by_sku_links: dict[str, list[CostRecipeLink]] = {}
    for l in links:
        by_sku_links.setdefault(l.internal_sku, []).append(l)

    master_by_sku = {m.internal_sku: m for m in masters}

    # 1) 승인 레시피 + 계산값이 있는 SKU 묶음 = ref 118의 450
    grounded_skus: dict[int, list[str]] = {}
    for sku, ls in by_sku_links.items():
        if sku not in master_by_sku:
            continue
        for l in ls:
            r = recipes.get(l.recipe_id)
            if r is None or r.status != "approved":
                continue
            if l.recipe_id not in standards:
                continue
            grounded_skus.setdefault(l.recipe_id, []).append(sku)
            break

    # ★줄 수는 `breakdown`으로 세고, 못 세는 레시피만 «한 번의 묶음 질의»로 폴백한다
    #   (적대 리뷰 P2-2). 종전엔 레시피 100건 전부에 lazy `recipe.lines`를 건드려
    #   호출당 106개 SQL이 나갔는데, 그 폴백은 breakdown이 없을 때만 쓰는 값이었다.
    #   `/api/cost/truth-board`는 원가 화면을 열 때마다 불리므로 레시피가 늘면 그대로 는다.
    fallback_needed = [
        rid
        for rid in grounded_skus
        if _breakdown_line_count(standards.get(rid)) is None
    ]
    fallback_counts: dict[int, int] = {}
    if fallback_needed:
        rows = (
            db.query(CostRecipeLine.recipe_id, func.count(CostRecipeLine.id))
            .filter(CostRecipeLine.recipe_id.in_(fallback_needed))
            .group_by(CostRecipeLine.recipe_id)
            .all()
        )
        fallback_counts = {rid: int(n) for rid, n in rows}

    groups: dict[int, RecipeGroup] = {}
    for rid, skus in grounded_skus.items():
        r = recipes[rid]
        std = standards.get(rid)
        prices = [
            _dec(master_by_sku[s].cost_price)
            for s in skus
            if master_by_sku[s].cost_price is not None
        ]
        distinct = {p for p in prices if p is not None}
        groups[rid] = RecipeGroup(
            recipe_id=rid,
            product_name=r.product_name,
            form_factor=r.form_factor,
            recipe_kind=r.recipe_kind or "assembly",
            skus=tuple(sorted(skus)),
            cost_price_kinds=len(distinct),
            cost_price_min=min(distinct) if distinct else None,
            std_cost_inc_vat=_dec(std.std_cost_inc_vat) if std else None,
            line_count=(
                _breakdown_line_count(std)
                if _breakdown_line_count(std) is not None
                else fallback_counts.get(rid, 0)
            ),
        )

    families: dict[str, list[RecipeGroup]] = {}
    for g in groups.values():
        families.setdefault(g.product_name, []).append(g)

    rows: list[dict] = []
    grounded_set: set[str] = set()

    for rid in sorted(groups):
        g = groups[rid]
        siblings = tuple(families.get(g.product_name, ()))
        for sku in g.skus:
            grounded_set.add(sku)
            pm = master_by_sku[sku]
            current = _dec(pm.cost_price)
            cause, truth_type, reason, owner = classify_group(
                g, siblings, has_approved_purchase=sku in approved_purchase
            )
            if truth_type == TRUTH_COMPUTED:
                truth_value = g.std_cost_inc_vat
            else:
                # ★보류의 정본값은 «없음»이다. 계산값을 실어 두면 화면이 「이 값으로
                #   갈아타면 된다」고 말하는 셈이 된다(계약 §2-6·§3-B).
                truth_value = None
            gap = (
                truth_value - current
                if truth_value is not None and current is not None
                else None
            )
            rows.append(
                {
                    "internal_sku": sku,
                    "product_name": pm.product_name,
                    "truth_type": truth_type,
                    "truth_label": TRUTH_LABELS[truth_type],
                    "truth_value": _s(truth_value),
                    "current_cost_price": _s(current),
                    "gap": _s(gap),
                    "cause": cause,
                    "cause_ref118": CAUSE_REF118.get(cause),
                    "reason": reason,
                    "owner": owner,
                    "recipe_id": rid,
                    "recipe_product_name": g.product_name,
                    "form_factor": g.form_factor,
                    "recipe_kind": g.recipe_kind,
                    "computed_value": _s(g.std_cost_inc_vat),
                }
            )

    # 2) 나머지 — 승인 근거가 없다. 매입가가 승인돼 있으면 «승격»된다(계약 §4 S2-3).
    for sku, pm in master_by_sku.items():
        if sku in grounded_set:
            continue
        current = _dec(pm.cost_price)
        purchased = approved_purchase.get(sku)
        if purchased is not None:
            truth_value = _dec(purchased)
            gap = (
                truth_value - current
                if truth_value is not None and current is not None
                else None
            )
            rows.append(
                {
                    "internal_sku": sku,
                    "product_name": pm.product_name,
                    "truth_type": TRUTH_PURCHASED,
                    "truth_label": TRUTH_LABELS[TRUTH_PURCHASED],
                    "truth_value": _s(truth_value),
                    "current_cost_price": _s(current),
                    "gap": _s(gap),
                    "cause": CAUSE_PURCHASED_APPROVED,
                    "cause_ref118": None,
                    "reason": "승인된 매입가가 있다 — 매입품의 정본은 매입가다"
                    "(계산값은 원리적으로 없다)",
                    "owner": OWNER_CUTOVER,
                    "recipe_id": None,
                    "recipe_product_name": None,
                    "form_factor": None,
                    "recipe_kind": None,
                    "computed_value": None,
                }
            )
            continue

        has_draft = any(
            recipes.get(l.recipe_id) is not None
            and recipes[l.recipe_id].status != "approved"
            for l in by_sku_links.get(sku, [])
        )
        cause, truth_type, reason, owner = classify_ungrounded(
            pm.product_name, has_draft_link=has_draft
        )
        rows.append(
            {
                "internal_sku": sku,
                "product_name": pm.product_name,
                "truth_type": truth_type,
                "truth_label": TRUTH_LABELS[truth_type],
                "truth_value": None,
                "current_cost_price": _s(current),
                "gap": None,
                "cause": cause,
                "cause_ref118": None,
                "reason": reason,
                "owner": owner,
                "recipe_id": None,
                "recipe_product_name": None,
                "form_factor": None,
                "recipe_kind": None,
                "computed_value": None,
            }
        )

    rows.sort(key=lambda r: r["internal_sku"])

    by_type: dict[str, int] = {k: 0 for k in TRUTH_LABELS}
    by_cause: dict[str, int] = {}
    for r in rows:
        by_type[r["truth_type"]] += 1
        by_cause[r["cause"]] = by_cause.get(r["cause"], 0) + 1

    # ★컷오버 대상 = 정본이 계산값·매입가인데 격차가 «의미 있게» 있는 것. 「즉시 가능」의 정의다.
    #   ★임계는 분류와 **같은 `MATCH_EPSILON`**이어야 한다 — 「격차 ≠ 0」으로 쓰면 일치로
    #   판정된 행(격차 0.4원 같은 반올림 잔차)이 컷오버 대상에 섞이고, 그러면 ref 118 §3의
    #   「즉시 가능 278 + 일치 3」 분해와 대조가 안 된다. 두 임계가 갈리면 같은 행이 한 곳에선
    #   일치고 다른 곳에선 대상이 된다(이 저장소가 겪은 「로직 두 벌」의 임계값판).
    cutover_ready = []
    for r in rows:
        if r["truth_type"] not in (TRUTH_COMPUTED, TRUTH_PURCHASED):
            continue
        g = _dec(r["gap"])
        if g is None or abs(g) < MATCH_EPSILON:
            continue
        cutover_ready.append(r)
    cutover_gap_sum = sum((_dec(r["gap"]) or Decimal(0)) for r in cutover_ready)

    return {
        "items": rows,
        "sku_count": len(rows),
        "price_rule": rule,
        "census": {
            "by_truth_type": by_type,
            "by_cause": by_cause,
            "cause_ref118": CAUSE_REF118,
            "cutover_ready_count": len(cutover_ready),
            "cutover_gap_sum": str(cutover_gap_sum),
            "matched_count": by_cause.get(CAUSE_MATCH, 0),
            "held_count": by_type[TRUTH_HELD],
            "none_count": by_type[TRUTH_NONE],
        },
        # ★이 층이 답하지 못하는 것을 화면이 알게 둔다(계약 §3-B 「없음 ≠ 0」의 집계판).
        "caveats": [
            "이 표는 읽기 전용이다 — `cost_price`를 한 건도 바꾸지 않는다(쓰기는 S3 컷오버 몫).",
            "초안 레시피에 걸린 SKU는 매입품·조립품이 아직 안 갈렸다 — `recipe_kind`가 전건 "
            "기본값이라 추측으로 정하지 않는다(ref 119 §2-1).",
            G31_CONTRACT_NOTE,
        ],
    }
