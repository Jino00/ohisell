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
    CostTableItem,
    CostTableItemLine,
    ProductChannelMapping,
    ProductMaster,
)
from app.services.cost_menu import ledger_check as LC
from app.services.cost_menu import price_rule as PR
from app.services.cost_menu import standard_cost as SC
from app.services.cost_menu.mapping_parser import (
    FORM_SOURCE_FALLBACK,
    FORM_SOURCE_RULE,
    MappingParseResult,
    form_source_for,
    parse_mapping_table,
)
from app.services.cost_menu.materials import (
    IMPORTED_GOODS_CATEGORY,
    IMPORTED_GOODS_KIND,
    CostMenuConflict,
    CostMenuError,
    ledger_check,
)
from app.services.cost_menu.recipe_parser import (
    CLEANING_KIT_NAME,
    ParseResult,
    RecipeDraft,
    parse_cost_table,
)

#: 원가표 「제품원가(+VAT)」 ↔ `cost_price` 대조 허용 오차(원). 엑셀 부동소수 꼬리만 흡수한다.
MATCH_TOLERANCE = Decimal("0.05")

#: ★설정 행이 없을 때만 쓰는 **구현 기본값**이다 — 「지금 쓰는 규칙」이 아니다(D-CPP-60 §0-A).
#:  구판은 이 상수가 곧 규칙이었고 `cost_standard.price_rule`에도 이 값이 그대로 박혔다.
#:  그래서 `cost_setting.standard_price_rule`을 바꿔도 계산도, 저장된 규칙 이름도 안 바뀌었다 —
#:  **저장된 이름조차 「우리가 실제로 쓴 규칙」이 아니라 상수의 사본**이었다.
#:  이제 실제 규칙은 `price_rule.read_rule(db)`가 읽고, 그 값이 `cost_standard.price_rule`에 간다.
DEFAULT_PRICE_RULE = PR.DEFAULT_RULE

#: 엑셀 참고값을 단가로 채택할 때 남기는 출처 문구의 접두사. 이 문구가 있으면 그 단가가
#: 「사람이 엑셀을 보고 승인한 값」임을 화면·감사가 안다.
ADOPT_NOTE_PREFIX = "엑셀 참고값 채택"

# ──────────────────────────────────────────────
# 개정 4 (D-CPP-59) — pin: 사람이 고른 원가표 항목
# ──────────────────────────────────────────────
#: 핀이 가리키던 항목이 재업로드 후 **사라졌을 때** 세우는 깃발. 조용히 지우지 않는다 —
#: 조용한 소실은 계약 합격 20의 미달 조건이다.
PIN_LOST = "pin_lost"
#: 핀 자연 키에 해당하는 항목이 **2건 이상**이 됐을 때(§9-9① 폴드 동명 중복이 실례다).
#: 둘 중 하나를 시스템이 고르지 않는다(§0-E-7 금지선) — 사람에게 되묻는다.
PIN_AMBIGUOUS = "pin_ambiguous"


def pin_key(section: str, item_name: str, form_factor: Optional[str]) -> str:
    """`section\\x1Fitem_name\\x1Fform_factor` — `CostTableItem.natural_key`와 같은 규칙.

    구분자가 `\\x1F`인 이유는 품목명·섹션명에 공백·쉼표·슬래시가 흔하기 때문이다 — 흔한
    구분자를 쓰면 키가 조용히 어긋나고, 그 증상은 「재업로드 후 핀이 남의 항목을 가리킨다」로
    나타난다.
    """

    return "\x1f".join([section, item_name, form_factor or ""])


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
#: 레시피 `note` JSON 중 **원가 정본(구성)이 소유하는** 키. 매핑만 올렸을 때 이 키들을
#: 건드리면, 이미 잘 매칭돼 있던 레시피가 «구성 못 찾음»으로 퇴화한다.
_COST_OWNED_NOTE_KEYS = (
    "match_reason",
    "candidates",
    "cost_price_mode",
    "cost_table_item",
    "cost_table_section",
    "excel_total_inc_vat",
)
#: 매핑 정본(SKU·옵션)이 소유하는 키. 원가만 올렸을 때 건드리지 않는다.
_MAPPING_OWNED_NOTE_KEYS = ("sku_count", "option_count")


def _note_dict(recipe: CostRecipe) -> dict:
    """저장된 `note`를 dict로. 비었거나 깨졌으면 빈 dict — **추측해 채우지 않는다**."""

    if not recipe.note:
        return {}
    try:
        loaded = json.loads(recipe.note)
    except (ValueError, TypeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def import_drafts(
    db: Session,
    cost_rows: Optional[Iterable[Sequence[Any]]] = None,
    mapping_rows: Optional[Iterable[Sequence[Any]]] = None,
) -> dict:
    """엑셀 → 레시피·링크 «초안». 파일을 여는 것은 호출자(라우터)다.

    ★**둘 중 하나만 올려도 된다** (Jino 2026-08-24: *"여기서 둘중에 하나만도 업데이트가
    되게 해줘"*). 그런데 이건 게이트를 푸는 일이 아니라 **«안 건드릴 것»을 정하는 일**이다 —
    두 파일이 서로 다른 절반을 만들기 때문이다:

    ================  ==========================================================
    원가 정본         부자재 **종**(이름·엑셀 참고값) + **구성**(무엇이 몇 개)
    매핑 정본         레시피의 **(상품명 × 폼팩터)** 그룹 + **SKU 링크**
    ================  ==========================================================

    ★★**위험한 자리**: 초판 루프는 «매핑 그룹»을 축으로 돌면서 `recipe.lines.clear()`와
    `recipe.note` 통째 덮어쓰기를 했다. 매핑만 올리면 `drafts_by_form`이 비어
    `_match_draft`가 전건 `no_recipe_match`를 돌려주므로, **그대로 두면 이미 잘 매칭돼
    있던 레시피의 구성이 통째로 지워진다.** 그래서 소유권을 나눈다:

    * 원가만  → 종 upsert + **DB의 기존 레시피**를 축으로 구성 재매칭. SKU 링크·`sku_count`
      **불가침**(`_sync_links` 호출 안 함).
    * 매핑만  → 그룹·링크만 갱신. `recipe.lines`·구성 note 키 **보존**(`_COST_OWNED_NOTE_KEYS`).
    * 둘 다    → 종전 동작 그대로.

    ★어느 절반이 «그대로인지»를 결과에 실어 보낸다(`updated_halves`·`untouched`) —
    조용한 반쪽 갱신은 반쪽 갱신보다 나쁘다.
    """

    if cost_rows is None and mapping_rows is None:
        raise CostMenuError("원가 정본과 매핑 정본 중 최소 하나는 필요하다.")

    has_cost = cost_rows is not None
    has_mapping = mapping_rows is not None

    cost: Optional[ParseResult] = parse_cost_table(cost_rows) if has_cost else None
    mapping: Optional[MappingParseResult] = (
        parse_mapping_table(mapping_rows) if has_mapping else None
    )

    drafts_by_form: dict[Optional[str], list[RecipeDraft]] = {}
    if cost is not None:
        for d in cost.recipes:
            drafts_by_form.setdefault(d.form_factor, []).append(d)

    resolved = (
        resolve_channel_codes(db, (c for o in mapping.options for c in o.channel_codes))
        if mapping is not None
        else {}
    )

    materials = _upsert_materials(db, cost) if cost is not None else {}
    db.flush()

    # ── 개정 4 (D-CPP-59) — 원가표 항목을 «남긴다» ────────────────────────────
    #   여기까지 파싱해 놓고 버리던 것이 「고를 목록이 없다」의 원인이었다(계약 §0-E-1 ②).
    #   순서가 설계다: 항목을 갈아끼운 «뒤» 핀을 자연 키로 다시 잇고, 그 다음에 매칭 루프가
    #   돈다 — 루프는 핀이 붙은 레시피를 건너뛰므로(합격 20) 핀 복구가 먼저여야 한다.
    pin_report = {"relinked": 0, "lost": 0, "ambiguous": 0}
    cost_table_items = 0
    if cost is not None:
        cost_table_items = _replace_cost_table_items(db, cost)
        pin_report = _resolve_pins(db)

    created = updated = skipped_approved = unmatched = skipped_pinned = 0
    groups = mapping.groups() if mapping is not None else {}
    report: list[dict] = []

    if not has_mapping:
        # ── 원가만 ── 축을 «DB의 기존 레시피»로 바꾼다. 매핑이 없으니 새 그룹은 생기지
        #    않고, 링크는 손대지 않는다.
        out = _reimport_composition_only(db, drafts_by_form, materials)
        out.update(
            {
                "materials_seen": len(materials),
                "cost_table_recipes": cost.recipe_count,
                "cost_table_anomalies": cost.anomalies,
                "cost_table_items": cost_table_items,
                "pins": pin_report,
                "mapping_options": 0,
                "mapping_anomalies": [],
                "groups": 0,
                "updated_halves": ["구성"],
                "untouched": ["SKU 링크·옵션 수 — 매핑 정본을 안 올렸으므로 그대로 둔다"],
            }
        )
        return out

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

        # ★핀이 붙은 레시피는 가격 매칭이 건드리지 않는다 (계약 합격 20 · D-CPP-59).
        #   사람이 고른 구성을 «파일을 다시 올렸다»는 이유로 갈아치우면, 승인분을 덮지 않는
        #   규율과 같은 것을 픽에서 어기는 것이다. 핀의 재해석은 위 `_resolve_pins`가 이미
        #   끝냈고 소실·동명은 깃발로 서 있다.
        if recipe is not None and recipe.picked_item_key:
            skipped_pinned += 1
            report.append(
                {
                    "product_name": product_name,
                    "form_factor": form_factor,
                    "action": "skipped_pinned",
                    "reason": _pin_skip_reason(recipe),
                    "sku_count": len(skus),
                    "line_count": len(recipe.lines),
                    "anomaly_flag": recipe.anomaly_flag,
                }
            )
            _sync_links(db, recipe, skus)
            continue

        anomaly = None if match.draft else "no_recipe_match"
        if match.draft and match.draft.anomalies:
            anomaly = ",".join(match.draft.anomalies)[:40]

        is_new = recipe is None
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

        # ★매핑만 올린 «기존» 레시피는 구성이 원가 정본 소관이므로 손대지 않는다.
        #   새 레시피엔 보존할 구성이 애초에 없다(`no_recipe_match`가 사실이다).
        preserve_composition = has_cost is False and not is_new

        if not preserve_composition:
            recipe.anomaly_flag = anomaly
        note = {
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
            # ★폼팩터를 «어떻게» 얻었나 (계약 D-CPP-61 §4-Q2). 한 그룹 안에서 규칙이 한 번이라도
            #   걸렸으면 규칙 유래로 본다 — 폴백은 「아무 규칙도 안 걸렸다」는 뜻이라 그 그룹의
            #   근거가 하나라도 있으면 「추정」이라 부를 수 없다.
            "form_source": FORM_SOURCE_RULE
            if any(r.form_source == FORM_SOURCE_RULE for r in rows)
            else FORM_SOURCE_FALLBACK,
        }
        if preserve_composition:
            # ★★구성 소관 키를 **저장된 값으로 되돌린다**. 이 여섯 줄이 이 슬라이스의
            #   본체다 — 없으면 매핑만 올린 순간 모든 레시피가 「구성 못 찾음」이 되고,
            #   오늘 A1을 연 레시피 68이 정확히 여기 걸린다.
            saved = _note_dict(recipe)
            for key in _COST_OWNED_NOTE_KEYS:
                note[key] = saved.get(key)
        recipe.note = json.dumps(note, ensure_ascii=False)
        db.flush()

        # 구성 라인 — 매칭됐을 때만. 못 찾았으면 **비운다**(0원짜리 구성을 만들지 않는다).
        # ★단 «매핑만» 올린 기존 레시피는 예외다 — 비울 근거가 이번 입력에 없다.
        if preserve_composition:
            _sync_links(db, recipe, skus)
            report.append(
                {
                    "product_name": product_name,
                    "form_factor": form_factor,
                    "action": action,
                    "reason": "매핑만 갱신 — 구성은 원가 정본 소관이라 그대로 뒀다",
                    "sku_count": len(skus),
                    "line_count": len(recipe.lines),
                    "anomaly_flag": recipe.anomaly_flag,
                }
            )
            continue

        recipe.lines.clear()
        db.flush()
        if match.draft:
            for line in match.draft.lines:
                material = materials.get(line.key.display_name)
                if material is None:
                    continue
                # ★`db.add(CostRecipeLine(recipe_id=...))`가 아니라 **관계에 붙인다**
                #   (적대 리뷰 1R P2-1 채택). 지금은 이 뒤에서 `recipe.lines`를 다시 안 읽어
                #   무해하지만, **바로 이 모양이 이번 슬라이스에서 실제 결함을 냈다**
                #   (`adopt_excel_prices`가 낡은 `material.prices`로 계산해 보드만 빈 칸).
                #   같은 결함을 두 번 봤으면 «그 자리»가 아니라 «그 모양»을 고친다.
                recipe.lines.append(
                    CostRecipeLine(
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
        "skipped_pinned": skipped_pinned,
        "unmatched": unmatched,
        "materials_seen": len(materials),
        "cost_table_recipes": cost.recipe_count if cost is not None else 0,
        "cost_table_anomalies": cost.anomalies if cost is not None else [],
        "cost_table_items": cost_table_items,
        "pins": pin_report,
        "mapping_options": len(mapping.options),
        "mapping_anomalies": mapping.anomalies,
        "groups": len(groups),
        "report": report,
        # ★어느 절반이 움직였고 어느 절반이 그대로인지 — 조용한 반쪽 갱신은 반쪽보다 나쁘다.
        "updated_halves": ["구성", "SKU 링크"] if has_cost else ["SKU 링크"],
        "untouched": []
        if has_cost
        else ["구성·부자재 종 — 원가 정본을 안 올렸으므로 그대로 둔다"],
    }


def _reimport_composition_only(
    db: Session,
    drafts_by_form: dict[Optional[str], list[RecipeDraft]],
    materials: dict[str, CostMaterial],
) -> dict:
    """원가 정본만 올렸을 때 — **DB의 기존 레시피**를 축으로 구성만 다시 맞춘다.

    ★루프 축이 매핑 그룹이 아니라 DB다. 매핑이 없으면 «어떤 상품명 × 폼팩터가 있는가»를
    이번 입력이 말해 주지 않으므로, 이미 아는 것(DB)을 쓴다. 새 레시피는 만들지 않는다 —
    만들 근거가 이번 입력에 없다.
    ★`cost_price_mode`·`sku_count`는 **매핑 소관**이라 재계산하지 않고 저장된 값을 그대로
    쓴다(`_match_draft`가 그 둘을 입력으로 받는다). 링크는 아예 손대지 않는다.
    """

    updated = skipped_approved = unmatched = skipped_pinned = 0
    report: list[dict] = []

    for recipe in db.query(CostRecipe).order_by(CostRecipe.product_name).all():
        if recipe.status == "approved":
            skipped_approved += 1
            continue

        # ★핀은 가격 매칭보다 우선한다 (계약 §0-E-3 · 합격 20). 위 `_resolve_pins`가 이미
        #   자연 키로 다시 이어 놨고, 소실·동명이면 깃발이 서 있다 — 어느 경우든 구성을
        #   지우지 않는다. 여기서 `recipe.lines.clear()`로 흘러가면 사람이 고른 구성이
        #   파일 한 번 올렸다고 사라진다.
        if recipe.picked_item_key:
            skipped_pinned += 1
            report.append(
                {
                    "product_name": recipe.product_name,
                    "form_factor": recipe.form_factor,
                    "action": "skipped_pinned",
                    "reason": _pin_skip_reason(recipe),
                    "sku_count": _note_dict(recipe).get("sku_count") or 0,
                    "line_count": len(recipe.lines),
                    "anomaly_flag": recipe.anomaly_flag,
                }
            )
            continue

        saved = _note_dict(recipe)
        mode = saved.get("cost_price_mode")
        sku_count = saved.get("sku_count") or 0
        # ★저장된 mode가 없으면 매칭의 입력이 없다 — «못 찾았다»가 아니라 **판단 불가**라
        #   구성을 비우지 않고 건너뛴다(§2-7: 모르는 것을 0으로 만들지 않는다).
        if mode is None:
            report.append(
                {
                    "product_name": recipe.product_name,
                    "form_factor": recipe.form_factor,
                    "action": "skipped_no_basis",
                    "reason": "저장된 cost_price_mode가 없다 — 매핑 정본을 함께 올려야 판단할 수 있다",
                    "sku_count": sku_count,
                    "line_count": len(recipe.lines),
                    "anomaly_flag": recipe.anomaly_flag,
                }
            )
            continue

        match = _match_draft(
            form_factor=recipe.form_factor,
            cost_prices=[Decimal(str(mode))],
            drafts_by_form=drafts_by_form,
            sku_count=sku_count,
        )

        note = dict(saved)
        note.update(
            {
                "match_reason": match.reason,
                "candidates": match.candidates,
                "cost_price_mode": _d(match.cost_price_mode),
                "cost_table_item": match.draft.item_name if match.draft else None,
                "cost_table_section": match.draft.section if match.draft else None,
                "excel_total_inc_vat": _d(match.draft.excel_total_inc_vat)
                if match.draft
                else None,
            }
        )
        # ★매핑 소관 키는 저장된 값 그대로 — 위 `dict(saved)`가 이미 실어 왔다.
        for key in _MAPPING_OWNED_NOTE_KEYS:
            note[key] = saved.get(key)

        recipe.anomaly_flag = None if match.draft else "no_recipe_match"
        if match.draft and match.draft.anomalies:
            recipe.anomaly_flag = ",".join(match.draft.anomalies)[:40]
        recipe.note = json.dumps(note, ensure_ascii=False)
        db.flush()

        recipe.lines.clear()
        db.flush()
        if match.draft:
            for line in match.draft.lines:
                material = materials.get(line.key.display_name)
                if material is None:
                    continue
                recipe.lines.append(
                    CostRecipeLine(
                        material_id=material.id,
                        quantity=line.quantity,
                        note=f"원가표 열 「{line.source_column}」",
                    )
                )
        else:
            unmatched += 1

        updated += 1
        report.append(
            {
                "product_name": recipe.product_name,
                "form_factor": recipe.form_factor,
                "action": "updated",
                "reason": match.reason,
                "sku_count": sku_count,
                "line_count": len(match.draft.lines) if match.draft else 0,
                "anomaly_flag": recipe.anomaly_flag,
            }
        )

    return {
        "recipes_created": 0,
        "recipes_updated": updated,
        "skipped_approved": skipped_approved,
        "skipped_pinned": skipped_pinned,
        "unmatched": unmatched,
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
            ref = line.excel_ref_price
            # ★cleaning kit은 «원장이 단가의 정본»인 종이라 엑셀 참고값을 심지 않는다
            #   (D-CPP-58 층2). Jino 2026-08-25 11:36: *"cleaning kit의 단가가 맞는거고,
            #   부자재(밀대외) (기종) 이 단가가 잘못 들어가있는거야"*. 라인의 22는 엑셀 총계
            #   검산(`computed_ex_vat`)에 그대로 쓰이지만 **종의 참고값 자리로는 못 간다** —
            #   가면 화면이 190.82원 옆에 22원을 나란히 세우고, 「엑셀 참고값 채택」 버튼이
            #   있는 화면에서 그 나란함은 «권유»로 읽힌다.
            if name == CLEANING_KIT_NAME:
                ref = None
            wanted.setdefault(name, ref)
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


def _pin_skip_reason(recipe: CostRecipe) -> str:
    """재수입이 핀을 건너뛴 이유 — 깃발이 서 있으면 **그 사실을 그대로 말한다**.

    조용히 「건너뜀」으로만 적으면 소실·동명이 보고서에서 사라지고, 사람은 자기 픽이
    멀쩡한 줄 안다(계약 합격 20의 「조용한 소실은 미달」).
    """

    if recipe.anomaly_flag == PIN_LOST:
        return "사람이 고른 원가표 항목이 이번 파일에 없다 — 구성은 그대로 두고 재확인을 요청한다"
    if recipe.anomaly_flag == PIN_AMBIGUOUS:
        return "같은 이름의 원가표 항목이 2건 이상이다 — 시스템이 고르지 않는다, 다시 고른다"
    return "사람이 고른 원가표 항목이 붙어 있다 — 재수입이 픽을 덮지 않는다"


def _replace_cost_table_items(db: Session, cost: ParseResult) -> int:
    """파싱된 원가표 항목을 **통째로 갈아끼운다** (계약 §0-E-3).

    ★왜 증분이 아니라 교체인가: 이 테이블은 «업로드된 원가표의 현재 단면»이지 이력이 아니다.
    증분으로 두면 원가표에서 **지워진 품목이 목록에 영원히 남아** 사람이 없는 것을 고르게 된다.
    이력이 필요한 자리는 단가(`cost_material_price.effective_date`)이지 여기가 아니다.

    ★교체가 핀을 끊지 않는가 — 끊는다(FK가 `SET NULL`이다). 그래서 핀의 정본은 FK가 아니라
    **자연 키 스냅샷**(`picked_item_key`)이고, 교체 직후 `_resolve_pins`가 다시 잇는다.
    FK만 믿었다면 S1 적대 리뷰 1R P1(**rowid 재사용**)이 그대로 재발한다.

    ★`form_factor=None`인 수입 완제품 항목도 **빼지 않고 싣는다** — 합격 6은 이번 범위 밖이지만
    (계약 §0-E-11) 목록에서 지워 두면 다음 슬라이스가 「없다」고 오판한다. 구성 줄이 0인 것은
    파서가 그 섹션에서 라인을 안 뽑기 때문이고, 그 사실 자체가 화면에 보여야 한다.
    """

    db.query(CostTableItemLine).delete(synchronize_session=False)
    db.query(CostTableItem).delete(synchronize_session=False)
    db.flush()

    for draft in cost.recipes:
        item = CostTableItem(
            section=draft.section,
            item_name=draft.item_name,
            form_factor=draft.form_factor,
            recipe_kind=draft.recipe_kind,
            total_inc_vat=draft.excel_total_inc_vat,
            row_number=draft.row_number,
            anomalies=",".join(draft.anomalies)[:200] if draft.anomalies else None,
        )
        for line in draft.lines:
            item.lines.append(
                CostTableItemLine(
                    material_name=line.key.display_name,
                    quantity=line.quantity,
                    ref_price=line.excel_ref_price,
                    source_column=line.source_column or None,
                )
            )
        db.add(item)
    db.flush()
    return len(cost.recipes)


def _resolve_pins(db: Session) -> dict[str, int]:
    """항목 교체 후 핀을 자연 키로 다시 잇는다 — **조용히 잡지 않는다** (계약 합격 20).

    셋 중 하나로 끝난다: ①정확히 1건이면 다시 잇는다 ②0건이면 `pin_lost` ③2건 이상이면
    `pin_ambiguous`. ②③은 구성을 **지우지 않는다** — 사람이 붙여 둔 구성을 파일 한 번
    올렸다고 없애면 그게 「재수입이 승인분을 덮는다」와 같은 병이다. 깃발만 세워 되묻는다.
    """

    pinned = (
        db.query(CostRecipe)
        .filter(CostRecipe.picked_item_key.isnot(None))
        .all()
    )
    if not pinned:
        return {"relinked": 0, "lost": 0, "ambiguous": 0}

    by_key: dict[str, list[CostTableItem]] = {}
    for item in db.query(CostTableItem).all():
        by_key.setdefault(item.natural_key, []).append(item)

    out = {"relinked": 0, "lost": 0, "ambiguous": 0}
    for recipe in pinned:
        hits = by_key.get(recipe.picked_item_key or "", [])
        if len(hits) == 1:
            recipe.picked_item_id = hits[0].id
            if recipe.anomaly_flag in (PIN_LOST, PIN_AMBIGUOUS):
                recipe.anomaly_flag = None
            out["relinked"] += 1
        elif not hits:
            recipe.picked_item_id = None
            recipe.anomaly_flag = PIN_LOST
            out["lost"] += 1
        else:
            recipe.picked_item_id = None
            recipe.anomaly_flag = PIN_AMBIGUOUS
            out["ambiguous"] += 1
    db.flush()
    return out


def _material_for_name(
    db: Session, name: str, *, category: str = "부자재"
) -> CostMaterial:
    """픽이 만든 라인이 걸릴 종 — 없으면 `unconfirmed`로 만든다.

    ★단가 행은 만들지 않는다(계약 §0-E-7 금지선). 새 종은 「빈 칸」이지 0이 아니다.
    보통은 같은 업로드의 `_upsert_materials`가 이미 만들어 뒀고, 이 갈래는 항목이
    먼저 저장된 뒤 종이 지워진 경우(D-CPP-58 병합 같은 정리)에만 탄다.

    ★`category`가 오면 **기존 종에도 덮어쓴다** — 수입 완제품 픽이 「이 종은 원장 `product`
    라인을 붙일 수 있다」를 선언하는 유일한 자리이기 때문이다(계약 D-CPP-61 §4-Q1). 기본값
    호출에는 영향이 없다(기본이 곧 기존 값과 같은 `"부자재"`).
    """

    m = db.query(CostMaterial).filter(CostMaterial.name == name).first()
    if m is not None:
        if category != "부자재" and m.category != category:
            # ★**조용히 바꾸지 않는다** (적대 리뷰 1R P2-1). 이 한 줄이 §4-Q1이 「픽 하나뿐」
            #   이라고 못 박은 표지를 세우는 자리라, 이름이 겹치는 기존 부자재 종이 여기서
            #   수입 완제품이 되면 그 종에 원장 `product` 라인 문이 영구히 열린다. 사람의
            #   명시적 행위(픽)이므로 허용하되 **무엇이 언제 바뀌었는지 종에 남긴다** —
            #   나중에 「이 종이 왜 수입 완제품이지?」에 답할 수 있어야 한다.
            before = m.category
            m.category = category
            stamp = f"[{datetime.now():%Y-%m-%d %H:%M}] 픽으로 분류 변경: {before or '(없음)'} → {category}"
            m.note = f"{m.note}\n{stamp}" if m.note else stamp
            db.flush()
        return m
    m = CostMaterial(
        name=name,
        status="unconfirmed",
        category=category,
        excel_label=name,
        note="원가표 항목 픽(D-CPP-59)으로 생성 — 단가는 아직 없다",
    )
    db.add(m)
    db.flush()
    return m


def _apply_item_lines(db: Session, recipe: CostRecipe, item: CostTableItem) -> int:
    """저장된 원가표 항목의 구성 줄을 레시피 구성으로 실체화한다.

    ★`ref_price`는 **옮기지 않는다** — 라인은 「무엇이 몇 개」이고 단가는 종이 가진다.
    참고값이 라인을 타고 계산에 스미면 §3 금지선(엑셀에서 단가 자동 유입)이 뚫린다.

    ★★**수입 완제품은 «Σ의 퇴화형 1줄»이다** (계약 D-CPP-61 §4-Q1 β안). 파서가 그 섹션의
    라인을 일부러 비우므로(`recipe_parser.ASSEMBLY_SECTIONS` — 열이 부자재가 아니라 계수라
    낱칸으로 읽으면 「관세 0.056원짜리 부자재」 헛것이 생긴다) 픽만으로는 구성이 0줄이고
    표준원가가 「구성 라인이 없다」로 멈춘다. 그래서 **항목 자신을 종 1개로 세우고 1줄로
    잇는다** — 산술 분기도 `recipe_kind` 값도 늘리지 않고, 단가는 기존 사슬
    (`link_ledger_line` → `cost_material_price(source='ledger')` → `price_rule.choose_price`
    → D-CPP-60 자동 갱신)을 그대로 탄다. 매수(`quantity`)는 1로 세우고 **사람이 고친다** —
    파서가 2p·1매입을 추정하지 않는다(§2-2·§9 [미상]).
    """

    recipe.lines.clear()
    db.flush()

    if item.recipe_kind == IMPORTED_GOODS_KIND and not item.lines:
        material = _material_for_name(
            db, item.item_name, category=IMPORTED_GOODS_CATEGORY
        )
        recipe.lines.append(
            CostRecipeLine(
                material_id=material.id,
                quantity=Decimal("1"),
                note="수입 완제품 — 원장 로트 단가가 이 종에 붙는다(매수는 사람이 고친다)",
            )
        )
        db.flush()
        return 1

    for line in item.lines:
        material = _material_for_name(db, line.material_name)
        recipe.lines.append(
            CostRecipeLine(
                material_id=material.id,
                quantity=line.quantity,
                note=f"원가표 열 「{line.source_column}」" if line.source_column else None,
            )
        )
    db.flush()
    return len(item.lines)


def list_cost_table_items(db: Session, recipe_id: int) -> dict:
    """레시피 하나에 붙일 수 있는 **원가표 항목 전건 목록** (계약 합격 18).

    ★목록의 범위는 «그 레시피의 폼팩터»다 — 그리고 **폼팩터가 없는 항목(수입 완제품·매입품)도
    함께 싣는다.** 폼팩터 버킷만 보여 주면 계약 §0-E-11이 진단한 그 차단(수입 완제품 초안이
    어떤 레시피와도 못 만난다)을 화면이 그대로 물려받는다.

    ★`suggested`는 **제안일 뿐이다** — 가격 매칭(`_match_draft`와 같은 기준)이 걸린 항목에만
    참을 실어 목록 상단으로 올린다. 제안이 0건이어도 픽은 막히지 않는다(§0-E-7).
    """

    recipe = get_recipe(db, recipe_id)
    saved = _note_dict(recipe)
    mode = saved.get("cost_price_mode")
    mode_dec: Optional[Decimal] = None
    if mode is not None:
        try:
            mode_dec = Decimal(str(mode))
        except (ArithmeticError, ValueError):
            mode_dec = None

    items = (
        db.query(CostTableItem)
        .filter(
            (CostTableItem.form_factor == recipe.form_factor)
            | (CostTableItem.form_factor.is_(None))
        )
        .order_by(CostTableItem.section, CostTableItem.item_name)
        .all()
    )

    rows: list[dict] = []
    for item in items:
        suggested = (
            mode_dec is not None
            and item.form_factor == recipe.form_factor
            and item.total_inc_vat is not None
            and abs(item.total_inc_vat - mode_dec) <= MATCH_TOLERANCE
        )
        rows.append(
            {
                "id": item.id,
                "section": item.section,
                "item_name": item.item_name,
                "form_factor": item.form_factor,
                "recipe_kind": item.recipe_kind,
                "total_inc_vat": _d(item.total_inc_vat),
                "row_number": item.row_number,
                "anomalies": item.anomalies,
                "line_count": len(item.lines),
                "suggested": suggested,
                "picked": item.id == recipe.picked_item_id,
            }
        )
    rows.sort(key=lambda r: (not r["suggested"], r["section"], r["item_name"]))
    return {
        "recipe_id": recipe.id,
        "form_factor": recipe.form_factor,
        # ★제안의 «근거»를 같이 준다 — 화면이 「왜 이게 위에 있나」를 말할 수 있어야
        #   사람이 제안을 «확정»으로 오해하지 않는다(계약 §0-E-10-4).
        "cost_price_mode": _d(mode_dec),
        "suggested_count": sum(1 for r in rows if r["suggested"]),
        "items": rows,
    }


def pick_cost_table_item(db: Session, recipe_id: int, item_id: int) -> CostRecipe:
    """사람이 고른 원가표 항목을 레시피 구성으로 확정한다 (계약 합격 18).

    ★**픽은 승인이 아니다** — status는 `draft` 그대로다. 픽이 승인을 겸하면 §2-2가 사람 몫으로
    못 박은 확정 지점이 한 클릭에 접힌다.
    ★**승인된 레시피는 거부한다** — 재수입이 승인분을 덮지 않는 것과 같은 규율이다. 바꾸려면
    먼저 승인을 푼다(`unapprove`).
    """

    recipe = get_recipe(db, recipe_id)
    if recipe.status == "approved":
        raise CostMenuConflict(
            "승인된 레시피의 구성은 픽으로 바꾸지 않는다 — 먼저 승인을 해제한다."
        )
    item = db.query(CostTableItem).filter(CostTableItem.id == item_id).first()
    if item is None:
        raise CostMenuError("원가표 항목을 찾을 수 없다 — 원가 정본을 다시 올려야 할 수 있다.")

    line_count = _apply_item_lines(db, recipe, item)

    recipe.picked_item_id = item.id
    recipe.picked_item_key = item.natural_key
    recipe.picked_at = datetime.now()
    # ★★**픽이 종류를 옮긴다** (계약 D-CPP-61 §4-Q1). 이 두 줄이 합격 6 차단의 관절이다:
    #   파서는 수입 완제품 초안에 `recipe_kind="imported_goods"`를 이미 붙이는데, 자동 매칭이
    #   그 초안을 **원리적으로 못 만난다**(초안은 `drafts_by_form[None]`에 쌓이고 조회 키는
    #   `DEFAULT_FORM_FACTOR="bar"` 폴백 때문에 절대 None이 안 된다 — `recipes.py` `_match_draft`).
    #   그래서 prod 100건이 전부 `assembly`로 굳었다. 픽은 그 버킷을 이미 횡단하고 있었으므로
    #   (`list_cost_table_items`가 form_factor NULL 항목을 함께 싣는다) 남은 것은 **고른 항목의
    #   종류를 레시피에 옮기는 것**뿐이었다. 자동 매칭은 고치지 않는다 — 가격 기반 매칭을
    #   넓히는 것은 D-CPP-59가 「불신 컬럼에서 사람의 픽으로」 옮긴 결정의 역행이다.
    recipe.recipe_kind = item.recipe_kind
    # 픽은 「없음 확인」과 상호배타다 — 고른 순간 부재 판정은 사실이 아니게 된다.
    recipe.absent_confirmed_at = None
    recipe.absent_note = None
    recipe.anomaly_flag = item.anomalies[:40] if item.anomalies else None

    note = _note_dict(recipe)
    note.update(
        {
            "match_reason": f"사람이 고름 — 원가표 「{item.section}/{item.item_name}」",
            "cost_table_item": item.item_name,
            "cost_table_section": item.section,
            "excel_total_inc_vat": _d(item.total_inc_vat),
            "picked_by_human": True,
        }
    )
    recipe.note = json.dumps(note, ensure_ascii=False)
    # ★커밋은 **라우터가 한다** (적대 리뷰 1R P2-2 채택) — `approve_recipe`·`adopt_excel_prices`와
    #   같은 관례다. 서비스가 스스로 커밋하면 이 함수를 다른 경로가 재사용할 때 이중 커밋·
    #   부분 커밋의 자리가 생긴다.
    db.flush()
    return recipe


def unpick_cost_table_item(db: Session, recipe_id: int) -> CostRecipe:
    """픽을 되돌린다 — 구성 줄도 함께 지운다(픽이 만든 것이므로).

    ★되돌림 경로가 없으면 사람이 «잘못 고른 것»을 못 고치고, 그러면 픽 자체를 주저하게 된다.
    """

    recipe = get_recipe(db, recipe_id)
    if recipe.status == "approved":
        raise CostMenuConflict("승인된 레시피는 픽을 되돌리지 않는다 — 먼저 승인을 해제한다.")

    recipe.lines.clear()
    recipe.picked_item_id = None
    recipe.picked_item_key = None
    recipe.picked_at = None
    recipe.anomaly_flag = "no_recipe_match"
    # 픽이 옮겨 온 종류도 함께 되돌린다 — 되돌린 뒤에도 「수입 완제품」이 남으면 화면이
    # 구성 0줄짜리 레시피를 수입 완제품이라 부르게 되고, 그건 근거 없는 단정이다.
    recipe.recipe_kind = "assembly"

    note = _note_dict(recipe)
    note.update(
        {
            "match_reason": "픽을 되돌렸다 — 다시 고르거나 「원가표에 없음」을 확인한다",
            "cost_table_item": None,
            "cost_table_section": None,
            "excel_total_inc_vat": None,
            "picked_by_human": False,
        }
    )
    recipe.note = json.dumps(note, ensure_ascii=False)
    # ★커밋은 **라우터가 한다** (적대 리뷰 1R P2-2 채택) — `approve_recipe`·`adopt_excel_prices`와
    #   같은 관례다. 서비스가 스스로 커밋하면 이 함수를 다른 경로가 재사용할 때 이중 커밋·
    #   부분 커밋의 자리가 생긴다.
    db.flush()
    return recipe


def confirm_cost_table_absent(
    db: Session, recipe_id: int, note_text: Optional[str] = None
) -> CostRecipe:
    """「원가표에 없음」을 **사람이 명시적으로** 확인한다 (계약 합격 19).

    ★이 칸이 없으면 「사람이 목록을 다 보고 없다고 판정했다」와 「아직 아무도 안 봤다」가
    화면에서 **같은 모습**이 된다. 침묵을 판정으로 읽는 것이 이 저장소가 반복해 밟은 자리다.
    ★사유를 받는 이유: 비필름 48건이 같은 목록에 뜨므로(계약 §0-E-4) 「이 종은 애초에 필름이
    아니다」와 「필름인데 원가표에 없다」가 갈려 기록돼야 다음 사람이 다시 안 판단한다.
    """

    recipe = get_recipe(db, recipe_id)
    # ★**살아 있는 픽만** 막는다 (적대 리뷰 1R P1-1).
    #
    #   초판은 `picked_item_key`가 남아 있기만 해도 거부했다. 그런데 `_resolve_pins`는 핀이
    #   끊길 때(`pin_lost`·`pin_ambiguous`) `picked_item_id`만 비우고 **키는 남긴다** — 그게
    #   재업로드 안전망의 근거이기 때문이다. 그래서 화면이 「다시 고르거나 「원가표에 없음」을
    #   확인한다」고 말하는 바로 그 상태에서 확인이 **항상 409로 거부**됐다.
    #   ⇒ 이 슬라이스가 고치려던 병(§0-E-1 ③ 「화면이 없는 길을 지시한다」)을 슬라이스 «안»에서
    #   재생산한 것이다. 판정 기준을 「키가 있나」가 아니라 «지금 가리키는 항목이 있나»로 옮긴다.
    if recipe.picked_item_id is not None:
        raise CostMenuConflict(
            "이미 원가표 항목이 픽돼 있다 — 「없음」과 동시에 참일 수 없다. 먼저 픽을 되돌린다."
        )
    if recipe.picked_item_key is not None:
        # 끊긴 핀을 **사람이 「없음」으로 처분한다.** 그 항목은 이번 원가표에 없으므로 그것이
        # 만든 구성도 함께 거둔다 — 남겨 두면 「원가표에 없음」이라 적힌 레시피가 «사라진
        # 항목의 구성»으로 계산되는, 서로 모순인 상태가 된다.
        recipe.lines.clear()
        recipe.picked_item_key = None
        recipe.picked_at = None
        if recipe.anomaly_flag in (PIN_LOST, PIN_AMBIGUOUS):
            recipe.anomaly_flag = None
        db.flush()

    recipe.absent_confirmed_at = datetime.now()
    recipe.absent_note = (note_text or "").strip() or None

    note = _note_dict(recipe)
    note["match_reason"] = "원가표에 없음 — 사람이 확인함" + (
        f" ({recipe.absent_note})" if recipe.absent_note else ""
    )
    recipe.note = json.dumps(note, ensure_ascii=False)
    # ★커밋은 **라우터가 한다** (적대 리뷰 1R P2-2 채택) — `approve_recipe`·`adopt_excel_prices`와
    #   같은 관례다. 서비스가 스스로 커밋하면 이 함수를 다른 경로가 재사용할 때 이중 커밋·
    #   부분 커밋의 자리가 생긴다.
    db.flush()
    return recipe


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
def _latest_price(
    m: CostMaterial, rule: str
) -> tuple[Optional[CostMaterialPrice], str]:
    """`material_payload`와 **같은 규칙**으로 단가를 고른다.

    ★규칙 «본체»는 이제 `price_rule.choose_price` 한 벌뿐이다(D-CPP-60 §0-A). 이 함수는 그
    호출 껍데기다 — 구판은 여기와 `material_payload`에 정렬이 **복제**돼 있었고 둘 다
    `cost_setting`을 안 읽어서, `standard_price_rule` 선언이 계산에 닿지 않았다.

    ★`rule`은 **필수 인자**다 — 호출부가 `price_rule.read_rule(db)`로 읽어 넘긴다. 기본값을
    두면 이 경로만 설정을 안 읽는 상태로 되돌아간다(합격 ⑧).
    """

    choice = PR.choose_price(list(m.prices), rule)
    return choice.price, choice.status


def _line_inputs(recipe: CostRecipe, rule: str) -> list[SC.RecipeLineInput]:
    """구성 줄 → 계산기 입력. `rule`은 **필수**다 — 합격 ⑧의 배선이 여기를 지난다."""

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
        price, status = _latest_price(material, rule)
        # ★미승인 종의 단가는 쓰지 않는다(`CostMaterial` docstring · 계약 §2-2) — 추론을
        #   확인분으로 만들지 않는다.
        #
        # ★그러나 «못 쓴다»와 «없다»는 다르다(적대 리뷰 1R P1-1). 원장에서 온 178.78원이
        #   버젓이 있는데 초판은 그 값을 **버리고** `unconfirmed`로 보고했고, 그래서 화면이
        #   「단가 미확정」이라 말하며 실재하는 값을 「—」로 감췄다 — Jino를 **이미 있는 단가를
        #   입력하러** 보내는 문장이었다. 이제 값은 그대로 실어 보내고 상태만 바꾼다:
        #   화면이 「단가는 있다, 부자재 탭에서 승인」이라고 옳게 말할 수 있게.
        if material.status != "approved":
            out.append(
                SC.RecipeLineInput(
                    label=material.name,
                    quantity=line.quantity,
                    unit_price_ex_vat=price.unit_price_ex_vat if price else None,
                    unit_price_inc_vat=price.unit_price_inc_vat if price else None,
                    price_status=(
                        SC.STATUS_MATERIAL_UNAPPROVED
                        if price is not None
                        # 단가 자체가 없으면 «미승인»이 아니라 그냥 «없음»이다 — 승인하라고
                        # 시켜 봐야 승인할 값이 없다. 그 경우 원래 사유를 그대로 보고한다.
                        else status
                    ),
                    material_id=material.id,
                    price_source=price.source if price else None,
                    price_note=price.note if price else None,
                    # ★참고값은 «단가 자리»가 아니라 자기 칸으로 간다 — 화면이 「채택 전」이라고
                    #   말할 원료다. 합계엔 절대 안 들어간다(계약 §3 · SC 모듈 주석).
                    excel_ref_price=material.excel_ref_price,
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
                excel_ref_price=material.excel_ref_price,
            )
        )
    return out


def recompute(db: Session, recipe: CostRecipe) -> SC.StandardCostResult:
    """표준원가 재계산 + `cost_standard` 확정 저장(계약 §5-2: 쓰기 시점 계산).

    ★**미승인 레시피는 계산하지 않는다** — 승인 전 구성으로 「확정 표준원가」를 저장·표시하는
    것이 계약 §3 금지선이다. 그래서 미승인이면 기존 행을 지우고 결과만 돌려준다.
    """

    # ★설정을 읽는다 — 「우연히 일치」와 「실제로 읽는다」의 경계가 이 한 줄이다(합격 ⑧).
    #   모르는 규칙이면 `UnknownPriceRule`이 올라가 계산이 **멈춘다**(조용한 fallback 금지).
    rule = PR.read_rule(db)
    result = SC.compute_standard_cost(_line_inputs(recipe, rule))
    row = (
        db.query(CostStandard)
        .filter(
            CostStandard.recipe_id == recipe.id,
            # ★저장된 규칙 이름도 실제 규칙으로 조회한다 — 상수로 조회하면 설정을 바꾼 뒤
            #   옛 규칙 행을 찾지 못해 **행이 조용히 둘로 갈라진다.**
            CostStandard.price_rule == rule,
        )
        .first()
    )

    if recipe.status != "approved" or not result.computable:
        if row is not None:
            db.delete(row)
            # ★아래와 같은 이유 — 지운 행이 남아 있는 것처럼 보이면 다음 호출이 그것을 갱신한다.
            db.flush()
        return result

    if row is None:
        # ★저장되는 규칙 이름 = **실제로 쓴 규칙**이다. 구판은 상수 사본을 박아서
        #   `cost_standard.price_rule`이 「우리가 쓴 규칙」이 아니라 「상수의 값」이었다.
        row = CostStandard(recipe_id=recipe.id, price_rule=rule)
        db.add(row)
    row.std_cost_ex_vat = result.std_cost_ex_vat
    row.std_cost_inc_vat = result.std_cost_inc_vat
    row.breakdown = json.dumps(SC.breakdown_payload(result), ensure_ascii=False)
    row.computed_at = datetime.now()
    # ★**같은 트랜잭션 안에서 이 함수가 다시 불릴 수 있다** (D-CPP-55): 한 레시피가 여러 종을
    #   쓰므로 `recompute_for_material`이 종마다 돌면 같은 레시피에 두 번 닿는다. 여기서 flush를
    #   안 하면 **`autoflush=False` 세션에서 방금 add한 행이 다음 조회에 안 보여** 같은
    #   (recipe_id, price_rule)로 두 번째 행을 만들고 유일 제약에 걸린다(IntegrityError).
    #   prod 세션과 테스트 세션의 autoflush가 다를 수 있으므로 «둘 다에서 참»인 쪽으로 못 박는다
    #   (교훈: 테스트 픽스처가 prod 세션과 다르면 결함을 못 잡는다 — 여기선 반대로 테스트가 잡았다).
    db.flush()
    return result


def recompute_for_material(db: Session, material_id: int) -> list[int]:
    """이 종을 쓰는 **승인된** 레시피의 저장 표준원가를 전부 다시 계산한다 (D-CPP-55, 계약 §6 S5).

    ★**이것이 「단가가 바뀌면 모든 원가에 같이 적용된다」의 유일한 자리다.** 단가를 쓰는 경로가
    6개인데(계약 §6 S5 표) 경로마다 재계산 사본을 두면, 다음에 생기는 7번째 경로가 또 한쪽만
    고쳐진다 — 이 저장소가 아홉 번 밟은 병이다. 그래서 트리거는 **한 벌**이고 경로는 이것을
    부르기만 한다.

    ★**대상은 승인된 레시피뿐**이다 — 미승인 레시피의 「확정 표준원가」 저장은 계약 §3 금지선이고
    (`recompute()`가 스스로 막지만 여기서 미리 걸러 필요 없는 쓰기를 안 만든다), 승인 상태가
    바뀌는 경로(종 승인/해제)는 어차피 레시피 상태를 안 바꾸므로 이 필터로 충분하다.

    ★**컬렉션 만료가 이 함수의 절반이다.** 호출부는 방금 `db.add()`/`db.delete()`로 단가 행을
    건드렸는데, 이미 로드된 `material.prices`는 그것을 모른다. 그대로 계산하면
    **「단가 없음」으로 굳어** `cost_standard` 행이 지워진다 — `adopt_excel_prices`가
    이 파일 안에 이미 적어 둔 함정(위 `m.prices.append` 주석)과 **같은 함정**이다.
    그래서 계산 전에 그 종의 `prices`를 만료시킨다. 라인은 `material`을 selectin으로 물고
    있으므로 identity map을 통해 같은 객체가 갱신된다.

    반환값은 재계산된 레시피 id들 — 호출부·테스트가 «몇 개에 닿았나»를 셀 수 있게(빈 리스트도
    유효한 답이다: 그 종을 쓰는 승인 레시피가 없다는 사실).
    """

    return recompute_for_materials(db, [material_id])


def recompute_for_materials(db: Session, material_ids: Sequence[int]) -> list[int]:
    """여러 종을 **한 번에** — 같은 레시피를 두 번 재계산하지 않는다 (적대 리뷰 1R P2-1).

    채택은 한 번에 9종의 단가를 만드는데 종마다 따로 돌리면 그 9종을 다 쓰는 레시피가
    **아홉 번** 재계산된다(멱등이라 결과는 같지만 필요 없는 쓰기다). 종 집합을 먼저 모아
    레시피를 한 번만 고른다.
    """

    ids = [int(i) for i in dict.fromkeys(material_ids)]
    if not ids:
        return []

    for mid in ids:
        material = db.get(CostMaterial, mid)
        if material is not None:
            db.expire(material, ["prices"])

    recipes = (
        db.query(CostRecipe)
        .join(CostRecipeLine, CostRecipeLine.recipe_id == CostRecipe.id)
        .filter(
            CostRecipeLine.material_id.in_(ids),
            CostRecipe.status == "approved",
        )
        .distinct()
        .all()
    )
    for r in recipes:
        recompute(db, r)
    return [r.id for r in recipes]


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
    # ★채택은 «종»의 단가를 만든다 — 그 종을 공유하는 **다른 승인 레시피**도 함께 바뀐다
    #   (D-CPP-55 · 계약 §6 S5의 6번 경로). 초판은 자기 레시피만 재계산해서, 「채택 경로는
    #   이미 닫혔다」가 절반만 참이었다.
    adopted_ids: list[int] = []
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
        adopted_ids.append(m.id)

    db.flush()
    result = recompute(db, recipe)
    # 그 종을 쓰는 «다른» 승인 레시피들 — 이 레시피는 바로 위에서 이미 계산했다.
    # 종 전체를 한 번에 넘긴다(1R P2-1: 종마다 돌리면 같은 레시피를 아홉 번 재계산한다).
    also = set(recompute_for_materials(db, adopted_ids))
    also.discard(recipe.id)
    return {
        "adopted": adopted,
        "skipped_has_price": skipped_has_price,
        "skipped_no_ref": skipped_no_ref,
        "standard": standard_payload(result),
        # 「어디까지 번졌나」를 화면·테스트가 셀 수 있게 — 조용한 전파는 전파가 아니다.
        "also_recomputed_recipe_ids": sorted(also),
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
                # ★「엑셀 참고값(채택 전)」 열의 원료 — **단가가 아니다.** `std_cost_*`·
                #   `partial_*` 어디에도 안 더해진다(SC.StandardCostLine.usable 주석).
                "excel_ref_price": _d(ln.excel_ref_price),
            }
            for ln in result.lines
        ],
    }


def _pick_payload(recipe: CostRecipe) -> dict:
    """픽 상태 — **세 상태를 갈라서** 화면에 준다 (계약 합격 19 · D-CPP-59).

    `state`는 넷 중 하나다:
      `picked`      사람이 원가표 항목을 골랐다
      `absent`      사람이 「원가표에 없음」을 확인했다 (시각·사유 동반)
      `pin_lost` / `pin_ambiguous`  핀이 재업로드에서 끊겼다 — 되묻는 상태
      `none`        **아직 아무도 안 봤다** ← 이것이 `absent`와 구별되는 것이 이 칸의 존재 이유다
    """

    if recipe.anomaly_flag in (PIN_LOST, PIN_AMBIGUOUS):
        state = recipe.anomaly_flag
    elif recipe.picked_item_key:
        state = "picked"
    elif recipe.absent_confirmed_at is not None:
        state = "absent"
    else:
        state = "none"

    item = recipe.picked_item
    return {
        "state": state,
        "item_id": recipe.picked_item_id,
        "item_name": item.item_name if item else None,
        "section": item.section if item else None,
        "item_total_inc_vat": _d(item.total_inc_vat) if item else None,
        "picked_at": recipe.picked_at.isoformat() if recipe.picked_at else None,
        "absent_confirmed_at": (
            recipe.absent_confirmed_at.isoformat() if recipe.absent_confirmed_at else None
        ),
        "absent_note": recipe.absent_note,
    }


def recipe_payload(db: Session, recipe: CostRecipe, *, with_links: bool = False) -> dict:
    result = SC.compute_standard_cost(_line_inputs(recipe, PR.read_rule(db)))
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
        # ★레시피 «탭»이 폼팩터의 출처를 말할 수 있게 payload에 싣는다 (적대 리뷰 1R P1-2).
        #   보드에만 있으면 픽이 일어나는 그 탭에서 픽의 결과가 안 보인다.
        "form_source": (note or {}).get("form_source")
        or form_source_for(recipe.form_factor),
        "anomaly_flag": recipe.anomaly_flag,
        "approved_at": recipe.approved_at.isoformat() if recipe.approved_at else None,
        "match": note,
        "line_count": len(recipe.lines),
        # ── 개정 4 (D-CPP-59) — 픽·부재확인 상태 ────────────────────────────
        #   ★셋은 서로 다른 상태다: 픽됨 / 「없음」을 사람이 확인함 / **아직 아무도 안 봄**.
        #   셋째가 NULL로 표현되므로 화면이 그 셋을 갈라 그릴 수 있다(계약 합격 19).
        "picked": _pick_payload(recipe),
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

    rule = PR.read_rule(db)
    recipes = _recipes_query(db).all()
    standards = {
        s.recipe_id: s
        for s in db.query(CostStandard)
        .filter(CostStandard.price_rule == rule)
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
        result = SC.compute_standard_cost(_line_inputs(recipe, rule))
        saved = _note_dict(recipe)
        # ★엑셀 표준 — «대조값»이다(계약 A′ §3: 엑셀에서 단가가 계산에 유입되는 경로는 없다).
        #   합격 6이 요구하는 「세 값 나란히」의 가운데 값이고, 여기 말고는 화면 어디에도
        #   이 값이 서 있는 자리가 없었다(현 격차 열은 표준원가 vs `cost_price`다).
        excel_total = saved.get("excel_total_inc_vat")
        excel_dec: Optional[Decimal] = None
        if excel_total is not None:
            try:
                excel_dec = Decimal(str(excel_total))
            except (ArithmeticError, ValueError):
                excel_dec = None
        for link in sorted(by_recipe.get(recipe.id, []), key=lambda x: x.internal_sku):
            pm = masters.get(link.internal_sku)
            std_inc = std.std_cost_inc_vat if std else None
            current = pm.cost_price if pm else None
            gap = None
            if std_inc is not None and current not in (None, 0):
                gap = float((std_inc - current) / current * 100)
            # 원장 파생 ↔ 엑셀 표준의 격차. 둘 다 VAT 포함 축이라 축이 섞이지 않는다.
            excel_gap = None
            if std_inc is not None and excel_dec not in (None, 0):
                excel_gap = float((std_inc - excel_dec) / excel_dec * 100)
            rows.append(
                {
                    "internal_sku": link.internal_sku,
                    "product_name": pm.product_name if pm else None,
                    "recipe_id": recipe.id,
                    "recipe_product_name": recipe.product_name,
                    "form_factor": recipe.form_factor,
                    # ★폼팩터를 규칙으로 얻었나 폴백으로 단정했나 (계약 D-CPP-61 §4-Q2).
                    #   저장된 값이 없으면 **파생**한다(적대 리뷰 1R P1-2) — 안 그러면 이미
                    #   있는 100건은 매핑 정본을 다시 올려야 배지가 뜨고, 그건 §5가 「사람
                    #   단계 없이 판정 가능」이라 못 박은 합격 5와 어긋난다.
                    "form_source": saved.get("form_source")
                    or form_source_for(recipe.form_factor),
                    "recipe_kind": recipe.recipe_kind,
                    "recipe_status": recipe.status,
                    "link_status": link.status,
                    "std_cost_ex_vat": _d(std.std_cost_ex_vat) if std else None,
                    "std_cost_inc_vat": _d(std_inc),
                    # ★읽기 전용 대조값. 이 계약은 이 칸에 쓰지 않는다(§3 금지선).
                    "current_cost_price": _d(current),
                    "gap_pct": None if gap is None else round(gap, 2),
                    "excel_total_inc_vat": _d(excel_dec),
                    "excel_gap_pct": None if excel_gap is None else round(excel_gap, 2),
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
