"""부자재 층 — DB ↔ 순수 SA 사이의 얇은 층 (D-CPP-53, 계약 A′ S1).

매칭 판단은 `matcher.py`(순수 SA)에 있다. **이 모듈은 그것을 부르고 결과를 담을 뿐** 자기
매칭 규칙을 갖지 않는다(계약 §2-6).

## 원장 → 단가의 규약

1. **확정(`confirmed`)된 수입건의 라인만** 소비한다. 계약 §9-8: 파서는 계약 B 소관이고,
   A′는 확정 라인만 보므로 파서 실패가 A′의 실패가 되지 않는다. draft 건의 단가는 아직
   «계산된 적 없는 값»이라 링크 대상이 아니다.
2. **단가는 복사한다 — 여기서 다시 산술하지 않는다.** 원장이 이미 확정 저장한
   `unit_cost_ex_vat`·`unit_cost_inc_vat` 두 값을 그대로 옮긴다(계약 B §2-2 계승).
   산술을 한 벌 더 만들면 그 벌이 원장보다 낡는다.
3. **연결은 사람이 한다.** 이 모듈의 `link_ledger_line`은 라우터의 POST에서만 불리고,
   그 POST는 화면의 「연결」 버튼이 부른다. 제안(`matcher.suggest`)은 그 버튼 옆에 이유를
   적어 줄 뿐 스스로 링크를 만들지 않는다(계약 §5-2).
4. **미입력은 «없음»이다.** 단가가 없는 라인은 링크 자체를 거부한다 — 0원짜리 단가 행이
   생기면 그게 「0원인가 미입력인가」 혼동의 재생산이다(계약 §2-7).
5. ★**보존한 값이 「최신」 자리를 공짜로 차지하지 않는다** (적대 리뷰 1R P1-1·P1-2).
   규약 2의 «근거 보존»은 유지하되, 조회할 때마다 그 행이 가리키는 원장 라인을 **다시 확인**한다
   (`ledger_check.py`: 있는가 / 확정인가 / 값이 같은가 / 같은 품목인가). 어긋난 행은
   ①`latest_price_*` 산정에서 **빠지고** ②화면에 **왜 빠졌는지**가 함께 나간다.
   왜냐하면 계약 §4의 기본 규칙은 `latest`=「최신 **확정** 로트」인데, 확정이 풀렸거나 값이
   달라졌거나 id가 다른 품목을 가리키게 된 행을 그대로 「최신 확정 로트 단가」라 부르면
   그게 계약 B `reopen`이 스스로 적어 둔 위험(*"낡은 단가가 「확정된 값」인 척 남는 것"*)을
   이 층이 되살리는 것이다. 지우지 않는 이유는 §2-7·§9-5와 같다 — **표본 부족·어긋남을
   숨기지 않고 말한다.**
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.models import (
    CostMaterial,
    CostMaterialPrice,
    CostRecipe,
    CostRecipeLine,
    CostSetting,
    ImportInvoiceLine,
    ImportShipment,
)
from app.services.cost_menu import ledger_check as LC
from app.services.cost_menu import price_rule as PR
from app.services.cost_menu.matcher import LedgerItem, MaterialRule, Suggestion, suggest

MATERIAL_STATUSES = ("unconfirmed", "approved")
PRICE_SOURCES = ("ledger", "manual")


class CostMenuError(ValueError):
    """호출자 잘못(입력 오류). 라우터가 4xx로 옮긴다."""


class CostMenuConflict(CostMenuError):
    """이미 있는 것을 또 만들려 할 때. 라우터가 409로 옮긴다."""


# ──────────────────────────────────────────────
# 직렬화 (계약 B 관례: `response_model` 없이 dict를 그대로 낸다 — 교훈 #321)
# ──────────────────────────────────────────────
def _d(v) -> str | None:
    """Decimal → 문자열. **None은 None으로 남긴다 — 0으로 접지 않는다**(계약 §2-7)."""
    return None if v is None else str(v)


def _date(v) -> str | None:
    return None if v is None else v.isoformat()


def ledger_check(p: CostMaterialPrice) -> LC.CheckResult:
    """★단가 행 1개를 **조회 시점에 원장과 다시 맞춰 본다** (적대 리뷰 1R P1).

    판정은 순수 SA(`ledger_check.py`)가 하고 이 함수는 사실만 떠다 준다 — 사본 두 벌을
    만들지 않는다(계약 §2-6).

    ★`p.invoice_line`이 `None`이면 그 라인은 **지금 없다**(수입건 삭제 등). FK에
    `ondelete="CASCADE"`가 걸려 있어도 이 앱의 SQLite 연결은 `PRAGMA foreign_keys=ON`을
    켜지 않아 실제로는 고아 행이 남는다 — 그래서 **화면이 대신 자백한다**(계약 §9-10 이월).
    """
    line = p.invoice_line
    fact = None
    if line is not None:
        ship = line.shipment
        fact = LC.LedgerFact(
            shipment_id=line.shipment_id,
            shipment_status=ship.status if ship is not None else None,
            item_name=line.item_name,
            unit_cost_ex_vat=line.unit_cost_ex_vat,
            unit_cost_inc_vat=line.unit_cost_inc_vat,
        )
    return LC.check(
        LC.PriceSnapshot(
            source=p.source,
            import_invoice_line_id=p.import_invoice_line_id,
            linked_item_name=p.linked_item_name,
            linked_shipment_id=p.linked_shipment_id,
            unit_price_ex_vat=p.unit_price_ex_vat,
            unit_price_inc_vat=p.unit_price_inc_vat,
        ),
        fact,
    )


def check_payload(c: LC.CheckResult) -> dict:
    """★판정을 **응답에 싣는다** — 백엔드만 아는 어긋남은 없는 것과 같다.

    `label`·`detail`을 그대로 내는 이유: 화면이 사유를 자기 말로 다시 지으면 두 벌이 되고,
    그 두 벌은 반드시 갈라진다(계약 §2-6과 같은 결).
    """
    return {
        "status": c.status,
        "ok": c.ok,
        "label": c.label,
        "detail": c.detail,
        "counts_as_evidence": c.counts_as_evidence,
        "refreshable": c.refreshable,
        "ledger_unit_price_ex_vat": _d(c.ledger_unit_price_ex_vat),
        "ledger_unit_price_inc_vat": _d(c.ledger_unit_price_inc_vat),
        "ledger_item_name": c.ledger_item_name,
    }


def price_payload(p: CostMaterialPrice, check: LC.CheckResult | None = None) -> dict:
    c = check if check is not None else ledger_check(p)
    return {
        "id": p.id,
        "material_id": p.material_id,
        "source": p.source,
        "import_invoice_line_id": p.import_invoice_line_id,
        "supplier": p.supplier,
        # 저장 시점 신원 스냅샷 — 「연결할 때 무엇을 봤나」가 화면에서 대조 가능해야 한다.
        "linked_item_name": p.linked_item_name,
        "linked_shipment_id": p.linked_shipment_id,
        "unit_price_ex_vat": _d(p.unit_price_ex_vat),
        "unit_price_inc_vat": _d(p.unit_price_inc_vat),
        "effective_date": _date(p.effective_date),
        "note": p.note,
        # ★로트 좌표를 단가 행에 실어 둔다 — 「이 단가는 어느 수입건에서 왔나」가 화면에서
        #   한 번에 보여야 추적이 끊기지 않는다.
        "shipment": _shipment_ref(p),
        # ★지금도 유효한가. 이 칸이 없으면 낡은 값이 「최신 확정 로트 단가」인 척 앉아 있는다.
        "ledger_check": check_payload(c),
    }


def _shipment_ref(p: CostMaterialPrice) -> dict | None:
    line = p.invoice_line
    if line is None or line.shipment is None:
        return None
    s = line.shipment
    return {
        "id": s.id,
        "hbl_no": s.hbl_no,
        "declaration_date": _date(s.declaration_date),
        "item_name": line.item_name,
        "quantity": _d(line.quantity),
    }


def material_payload(
    m: CostMaterial,
    prices: list[CostMaterialPrice],
    used_by: list[dict],
    rule: str,
) -> dict:
    """부자재 1종 + 단가 이력 + **사용처**.

    ★`used_by`는 **필수 인자다**(기본값을 두지 않는다). Jino 2026-08-24: *"각 부자재가 어느
    제품에 들어가는지도 나오면 좋겠고"*. 호출부가 7곳이라 기본값을 두면 그중 하나만 고쳐도
    타입이 통과하고, 그 화면만 「사용처 0건」이라 조용히 거짓말을 한다 — 이 저장소가 반복해
    밟은 「한쪽만 고친다」다. 필수로 두면 **빠뜨린 호출부가 즉시 터진다.**

    ★`latest_price_*`는 **로트가 하나도 없으면 None이다 — 0이 아니다**(계약 §2-7).
    ★`lot_count`를 같이 낸다: 「이 표준의 근거는 로트 N건」을 화면이 말해야 표본 부족이
      숨지 않는다(계약 §9-5). **원장과 어긋난 행은 여기 안 센다** — 어긋난 근거는 근거가
      아니고, 세면 「로트 2건이 받치는 표준」이라는 문장이 거짓말이 된다.
    ★`latest_price_*`는 **재검사를 통과한 행에서만** 고른다(적대 리뷰 1R P1-1). 어긋난 행은
      이력에 남되(근거 보존) 최신 자리를 못 차지한다. 전부 어긋나면 최신은 «없음»이고,
      `stale_count`가 **왜 없는지**를 화면에 말한다 — 조용히 비는 것과 다르다.

    ★`rule`은 **필수 인자다**(D-CPP-60 §0-A). 구판은 이 함수 «안»에 정렬 규칙을 복제해 갖고
      있었고 `cost_setting`을 안 읽었다 — 그래서 화면의 「최신 단가」는 계산과 같은 값을 내면서도
      **같은 규칙을 쓴다는 보장이 없었다.** 이제 둘 다 `price_rule.choose_price` 한 벌을 쓴다.
    ★`lot_price_min`/`lot_price_max`를 함께 낸다 — 계약 §4-⑥의 「관측 로트 구간」이고,
      그 폭이 곧 **재고 원장(C1)이 없어서 우리가 못 고르는 폭**이다. 화면이 그걸 자백한다.
    ★`price_conflict_*`는 §2-5의 자백: 채택은 `ledger`인데 더 늦은 `manual`이 있다.
    """
    ordered = sorted(
        prices,
        key=lambda p: (p.effective_date or date.min, p.id),
        reverse=True,
    )
    checks = {p.id: ledger_check(p) for p in ordered}
    choice = PR.choose_price(list(prices), rule)
    latest = choice.price
    return {
        "id": m.id,
        "name": m.name,
        "unit": m.unit,
        "category": m.category,
        "status": m.status,
        "excel_label": m.excel_label,
        # ★엑셀 원가 정본의 **참고값** — 단가가 아니다(계약 §3 금지선).
        #   ★왜 실어 보내나(2026-08-23 실측): prod에서 단가 보유 종은 **1/129**인데 참고값
        #   보유 종은 **128/129**다. 그런데 이 payload가 참고값을 안 실어서 화면은
        #   「단가 없음 — 원장 연결 또는 수동 입력 필요」라고만 말했다 — 할 일이 셋인데 둘만
        #   제시했고, **빠진 셋째(채택)가 가장 싼 길**이었다. 백엔드만 아는 사실은 없는 것과
        #   같다(`check_payload` docstring과 같은 결).
        #   ★`latest_price_*`엔 절대 안 섞는다 — 참고값이 단가 자리에 앉으면 그게 §3 위반이다.
        "excel_ref_price": _d(m.excel_ref_price),
        "match_rule": m.match_rule,
        "form_factor": m.form_factor,
        "part": m.part,
        "note": m.note,
        # 근거로 세는 로트 = 원장 파생 + 재검사 통과분.
        "lot_count": choice.lot_count,
        "price_count": len(ordered),
        # ★어긋난 연결 수. 0이 아니면 화면이 「최신 단가에서 왜 빠졌나」를 말해야 한다.
        "stale_count": choice.stale_count,
        "latest_price_ex_vat": _d(latest.unit_price_ex_vat) if latest else None,
        "latest_price_inc_vat": _d(latest.unit_price_inc_vat) if latest else None,
        "latest_price_source": latest.source if latest else None,
        # ★적용된 규칙을 **값과 함께** 낸다 — 「이 숫자가 어느 규칙의 산물인가」를 화면이
        #   말할 수 있어야 설정 변경이 화면에서 보인다(합격 ⑧).
        "price_rule": choice.rule,
        # ★관측 로트 구간(계약 §4-⑥). 로트가 1건이면 min==max이고 폭이 0이다 — 그것도 사실이다.
        "lot_price_min": _d(choice.lot_min),
        "lot_price_max": _d(choice.lot_max),
        "lot_price_has_span": choice.has_span,
        # ★§2-5 자백 — 채택은 `ledger`인데 더 늦은 `manual`이 있다.
        "price_conflict": choice.conflict,
        "price_conflict_price_id": choice.conflict_price_id,
        "prices": [price_payload(p, checks[p.id]) for p in ordered],
        # ★사용처 — 「이 부자재가 어느 제품에 들어가는가」. 없으면 빈 목록이고, 그건
        #   «아직 어느 레시피도 이 종을 안 쓴다»는 사실이지 미상이 아니다.
        "used_by": used_by,
        "used_by_count": len(used_by),
    }


def suggestion_payload(s: Suggestion) -> dict:
    return {
        "line_id": s.line_id,
        "item_name": s.item_name,
        "material_id": s.material_id,
        "reason": s.reason,
        "candidates": list(s.candidates),
        "ambiguous": s.is_ambiguous,
        "unmatched": s.is_unmatched,
    }


# ──────────────────────────────────────────────
# 조회
# ──────────────────────────────────────────────
def _materials_query(db: Session):
    return db.query(CostMaterial).options(
        selectinload(CostMaterial.prices)
        .selectinload(CostMaterialPrice.invoice_line)
        .selectinload(ImportInvoiceLine.shipment)
    )


def get_material(db: Session, material_id: int) -> CostMaterial:
    m = _materials_query(db).filter(CostMaterial.id == material_id).first()
    if m is None:
        raise CostMenuError(f"부자재 종 id={material_id}이 없다.")
    return m


def usage_map(db: Session, material_ids: list[int]) -> dict[int, list[dict]]:
    """종 → 그 종을 쓰는 레시피들. **한 번의 질의**로 전건을 모은다(N+1 금지).

    ★승인 여부를 함께 싣는다 — 「어느 제품에 들어가나」의 답은 «승인된 것»과 «초안»이
    섞여 있고, 그 둘은 계산에 들어가는지가 다르다(계약 §2-2).
    """

    if not material_ids:
        return {}
    rows = (
        db.query(CostRecipeLine, CostRecipe)
        .join(CostRecipe, CostRecipeLine.recipe_id == CostRecipe.id)
        .filter(CostRecipeLine.material_id.in_(material_ids))
        .order_by(CostRecipe.product_name, CostRecipe.form_factor)
        .all()
    )
    out: dict[int, list[dict]] = {}
    for line, recipe in rows:
        out.setdefault(line.material_id, []).append(
            {
                "recipe_id": recipe.id,
                "product_name": recipe.product_name,
                "form_factor": recipe.form_factor,
                "status": recipe.status,
                "quantity": _d(line.quantity),
            }
        )
    return out


def payload_with_usage(db: Session, m: CostMaterial) -> dict:
    """단일 종 payload — 사용처를 «여기서» 붙인다.

    ★라우터의 단일 종 응답 7곳이 전부 이 함수를 지나게 한다. 각자 `usage_map`을 부르면
    그중 하나가 빠지고, 그 화면만 사용처가 비어 보인다."""

    return material_payload(
        m, list(m.prices), usage_map(db, [m.id]).get(m.id, []), PR.read_rule(db)
    )


def list_materials(db: Session) -> list[dict]:
    rows = _materials_query(db).order_by(CostMaterial.name).all()
    usage = usage_map(db, [m.id for m in rows])
    rule = PR.read_rule(db)
    return [
        material_payload(m, list(m.prices), usage.get(m.id, []), rule) for m in rows
    ]


def ledger_material_lines(db: Session) -> list[dict]:
    """확정된 수입건의 `line_type='material'` 라인 전건 + 링크 상태 + 제안.

    ★**미매칭도 결과에 실린다.** 「원장에 부자재 라인이 있는데 어느 종에도 안 붙었다」가
    화면에 안 보이면 단가 이력이 조용히 비어 있게 된다(계약 §5-3 탭1: 「미매칭」 표면화).

    ★**이미 연결된 라인은 확정이 풀려도 목록에서 사라지지 않는다** (적대 리뷰 1R P1-1).
    초판은 `status=='confirmed'`만 실어서, 수입건을 `reopen`하면 그 라인이 목록에서 통째로
    빠지고 **어긋났다는 사실이 화면에서 아예 사라졌다** — 낡은 단가만 「최신」이라 앉아 있고
    원인은 안 보이는 상태다. 그래서 필터를 「확정됐거나 **우리가 이미 붙여 둔** 라인」으로
    넓히고, `shipment_status`를 행에 실어 화면이 「확정 해제됨」을 말하게 한다.
    (링크 생성은 여전히 확정 건에서만 가능하다 — `link_ledger_line` 참조.)
    """
    linked = {
        p.import_invoice_line_id: p
        for p in db.query(CostMaterialPrice)
        .options(
            selectinload(CostMaterialPrice.invoice_line).selectinload(
                ImportInvoiceLine.shipment
            )
        )
        .filter(CostMaterialPrice.import_invoice_line_id.isnot(None))
        .all()
    }
    linked_ids = [lid for lid in linked if lid is not None]
    visible = ImportShipment.status == "confirmed"
    if linked_ids:
        visible = or_(visible, ImportInvoiceLine.id.in_(linked_ids))
    rows = (
        db.query(ImportInvoiceLine)
        .join(ImportShipment, ImportInvoiceLine.shipment_id == ImportShipment.id)
        .options(selectinload(ImportInvoiceLine.shipment))
        .filter(ImportInvoiceLine.line_type == "material", visible)
        .order_by(ImportShipment.id.desc(), ImportInvoiceLine.seq)
        .all()
    )
    materials = db.query(CostMaterial).order_by(CostMaterial.name).all()
    rules = [
        MaterialRule(material_id=m.id, name=m.name, match_rule=m.match_rule)
        for m in materials
    ]
    sugg = {
        s.line_id: s
        for s in suggest(
            [LedgerItem(line_id=r.id, item_name=r.item_name) for r in rows], rules
        )
    }
    by_id = {m.id: m for m in materials}

    out: list[dict] = []
    for r in rows:
        p = linked.get(r.id)
        s = sugg[r.id]
        out.append(
            {
                "line_id": r.id,
                "shipment_id": r.shipment_id,
                "hbl_no": r.shipment.hbl_no,
                "declaration_date": _date(r.shipment.declaration_date),
                "item_name": r.item_name,
                "quantity": _d(r.quantity),
                "unit_cost_ex_vat": _d(r.unit_cost_ex_vat),
                "unit_cost_inc_vat": _d(r.unit_cost_inc_vat),
                "allocated_cost_krw": _d(r.allocated_cost_krw),
                "linked_material_id": p.material_id if p else None,
                "linked_material_name": (
                    by_id[p.material_id].name if p and p.material_id in by_id else None
                ),
                "linked_price_id": p.id if p else None,
                # ★수입건이 지금 확정 상태인가. 「확정 해제됨」이 안 보이면 낡은 단가가 왜
                #   최신 자리에서 빠졌는지 화면이 설명할 수 없다(적대 리뷰 1R P1-1).
                "shipment_status": r.shipment.status,
                # 붙어 있는 단가 행의 재검사 결과 — 없으면 None(아직 안 붙었다).
                "linked_price_check": check_payload(ledger_check(p)) if p else None,
                "suggestion": suggestion_payload(s),
            }
        )
    return out


def list_settings(db: Session) -> list[dict]:
    """설정 전건. **`confirmed`를 그대로 낸다** — 화면이 「미확인」을 자백하는 원료다(계약 §9-1)."""
    return [
        {
            "key": s.key,
            "value": s.value,
            "confirmed": bool(s.confirmed),
            "note": s.note,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        }
        for s in db.query(CostSetting).order_by(CostSetting.key).all()
    ]


# ──────────────────────────────────────────────
# 쓰기
# ──────────────────────────────────────────────
def _propagate(db: Session, material_id: int) -> list[int]:
    """단가·승인상태가 바뀐 뒤 **표준원가를 따라가게** 한다 (D-CPP-55, 계약 §6 S5 · §2-5).

    ★계약 §2 판단기준 5가 *"표준원가의 갱신 주체는 항상 원장이다 — 사람이 아니다"*라고 정했고
    Jino가 그것을 다시 못 박았다(2026-08-24: *"같은 부자재를 어디서 바꾸던지 그 부자재의 가격은
    모두 동일한거니까 그때부터는 모든 원가에 같이 적용되어야지"*). 그 전까지 이 모듈의 단가
    쓰기 4경로 어디에도 재계산이 없어서, **레시피 상세는 새 단가로 바뀌는데 표준원가 보드는
    옛 값이 남았다.**

    ★**이 함수는 트리거를 «부르기»만 한다** — 재계산 규칙 자체는 `recipes.recompute_for_material`
    한 벌뿐이다(사본을 두면 다음 경로가 또 한쪽만 고쳐진다).

    지연 임포트인 이유: `recipes`는 이 모듈을 모르고 이 모듈도 `recipes`를 모르는 편이 층 구조에
    맞다. 여기 한 곳에서만 필요하므로 순환 위험을 원천 차단한다.
    """

    from app.services.cost_menu import recipes as R  # 지연 임포트 — 위 docstring 참조

    return R.recompute_for_material(db, material_id)


def create_material(db: Session, **fields) -> CostMaterial:
    name = (fields.get("name") or "").strip()
    if not name:
        raise CostMenuError("부자재 종 이름이 비었다.")
    dup = db.query(CostMaterial).filter(CostMaterial.name == name).first()
    if dup is not None:
        raise CostMenuConflict(f"「{name}」은 이미 등록돼 있다(id={dup.id}).")
    m = CostMaterial(**{**fields, "name": name})
    db.add(m)
    db.flush()
    return m


def update_material(db: Session, material_id: int, **fields) -> CostMaterial:
    m = get_material(db, material_id)
    if "status" in fields and fields["status"] not in MATERIAL_STATUSES:
        raise CostMenuError(f"알 수 없는 status: {fields['status']}")
    if "name" in fields:
        name = (fields["name"] or "").strip()
        if not name:
            raise CostMenuError("부자재 종 이름이 비었다.")
        other = (
            db.query(CostMaterial)
            .filter(CostMaterial.name == name, CostMaterial.id != material_id)
            .first()
        )
        if other is not None:
            raise CostMenuConflict(f"「{name}」은 다른 종(id={other.id})의 이름이다.")
        fields["name"] = name
    for k, v in fields.items():
        setattr(m, k, v)
    db.flush()
    # 종 승인/해제는 **계산 결과를 바꾼다** — 미승인 종의 단가는 `_line_inputs`가 계산에서
    # 빼기 때문이다. 이름·라벨 변경은 산술에 안 닿으므로 필요 없는 쓰기를 만들지 않는다.
    if "status" in fields:
        _propagate(db, m.id)
    return m


def link_ledger_line(
    db: Session, material_id: int, line_id: int, note: str | None = None
) -> CostMaterialPrice:
    """원장 라인 1건을 부자재 종에 **사람이 확정 연결**한다 → `source='ledger'` 단가 행 1개.

    거부하는 경우와 그 이유:
    - 라인이 없다 / 부자재 라인이 아니다 → 잘못된 대상이다.
    - 수입건이 확정 전이다 → 단가가 아직 «계산된 적 없는 값»이다(계약 §9-8).
    - 단가가 비어 있다 → 0으로 채우지 않는다(계약 §2-7). 원장을 먼저 확정해야 한다.
    - 이미 이 종에 붙어 있다 → 같은 로트가 두 번 세지면 이력이 거짓말이 된다.
    """
    m = get_material(db, material_id)
    line = (
        db.query(ImportInvoiceLine)
        .options(selectinload(ImportInvoiceLine.shipment))
        .filter(ImportInvoiceLine.id == line_id)
        .first()
    )
    if line is None:
        raise CostMenuError(f"원장 라인 id={line_id}이 없다.")
    if line.line_type != "material":
        raise CostMenuError(
            f"원장 라인 id={line_id}은 부자재(`material`)가 아니라 `{line.line_type}`이다. "
            "원장에서 분류를 먼저 고쳐야 한다."
        )
    if line.shipment is None or line.shipment.status != "confirmed":
        raise CostMenuError(
            f"수입건 id={line.shipment_id}이 확정 전이다 — 확정된 로트의 단가만 쓴다(계약 §9-8)."
        )
    if line.unit_cost_ex_vat is None or line.unit_cost_inc_vat is None:
        raise CostMenuError(
            f"원장 라인 id={line_id}에 단가가 없다 — 0으로 채우지 않는다(계약 §2-7)."
        )
    dup = (
        db.query(CostMaterialPrice)
        .filter(CostMaterialPrice.import_invoice_line_id == line_id)
        .first()
    )
    if dup is not None:
        # ★409로 끝내지 않고 **다음 수를 말한다** (적대 리뷰 1R P1-2): 초판은 어긋난 연결이
        #   있어도 「이미 연결돼 있다」만 말해서, 원장이 스스로 고칠 길이 없었다.
        c = ledger_check(dup)
        if c.ok:
            hint = "이미 원장과 일치하는 연결이다 — 다시 붙일 이유가 없다."
        elif c.refreshable and dup.material_id == material_id:
            hint = f"어긋난 연결이다({c.label}) — 「갱신」으로 원장 값을 다시 옮긴다."
        else:
            hint = f"어긋난 연결이다({c.label}) — 「해제」한 뒤 사람이 다시 연결한다."
        raise CostMenuConflict(
            f"원장 라인 id={line_id}은 이미 부자재 id={dup.material_id}에 연결돼 있다(단가 행 "
            f"id={dup.id}). {hint}"
        )

    p = CostMaterialPrice(
        material_id=m.id,
        source="ledger",
        import_invoice_line_id=line.id,
        # 공급처는 원장 shipper에서 자동으로 온다(계약 §5-1 ★공급처).
        supplier=line.shipment.shipper_name,
        # ★신원 스냅샷 — id만으로는 rowid 재사용을 못 가른다(적대 리뷰 1R P1-2).
        linked_item_name=line.item_name,
        linked_shipment_id=line.shipment_id,
        # ★복사다 — 여기서 다시 산술하지 않는다(계약 B §2-2 계승).
        unit_price_ex_vat=line.unit_cost_ex_vat,
        unit_price_inc_vat=line.unit_cost_inc_vat,
        # 통관일. 없으면 ETA로 떨어지고 그것도 없으면 «없음»이다 — 오늘로 채우지 않는다.
        effective_date=line.shipment.declaration_date or line.shipment.eta,
        note=note,
    )
    db.add(p)
    db.flush()
    _propagate(db, m.id)  # D-CPP-55 경로 1
    return p


def refresh_ledger_price(
    db: Session, material_id: int, price_id: int
) -> tuple[CostMaterialPrice, LC.CheckResult]:
    """어긋난 `ledger` 단가 행을 **원장 현재값으로 다시 맞춘다** (적대 리뷰 1R P1-2).

    ★왜 필요한가: 환율을 정정해 재확정하면 원장은 198.91인데 저장된 행은 190.82로 남고,
    다시 연결하려 하면 유일 제약 때문에 409였다 — **원장이 스스로 고칠 길이 없었다.**

    거부하는 경우와 그 이유:
    - 수동 입력 행이다 → 원장 파생이 아니다. 사람 입력을 원장으로 덮으면 그게 창작이다.
    - 원장 라인이 없다 / 확정 전이다 → 옮겨 올 «확정된 값»이 지금 없다(계약 §9-8).
    - **품목이 달라졌다** → 갱신하면 다른 품목의 단가를 조용히 삼킨다. 사람이 해제·재연결한다.

    ★**옛 값을 버리지 않는다.** 덮어쓰기 전 값을 `note`에 한 줄로 남긴다 — 이 테이블의 존재
    이유가 근거 보존인데 갱신이 그 근거를 지우면 앞뒤가 안 맞는다(계약 §2-7·§9-5의 결).
    """
    p = (
        db.query(CostMaterialPrice)
        .options(
            selectinload(CostMaterialPrice.invoice_line).selectinload(
                ImportInvoiceLine.shipment
            )
        )
        .filter(
            CostMaterialPrice.id == price_id,
            CostMaterialPrice.material_id == material_id,
        )
        .first()
    )
    if p is None:
        raise CostMenuError(f"단가 행 id={price_id}이 이 종에 없다.")
    if p.source != "ledger":
        raise CostMenuError(
            f"단가 행 id={price_id}은 수동 입력이다 — 원장으로 덮지 않는다(계약 §4 하이브리드 ②)."
        )

    c = ledger_check(p)
    if not c.refreshable:
        raise CostMenuError(f"갱신할 수 없다 — {c.label}. {c.detail}")

    line = p.invoice_line
    if line is None or line.shipment is None:  # pragma: no cover - refreshable가 이미 막는다
        raise CostMenuError("원장 라인이 없다 — 갱신할 대상이 없다.")
    if line.unit_cost_ex_vat is None or line.unit_cost_inc_vat is None:
        raise CostMenuError(
            f"원장 라인 id={line.id}에 단가가 없다 — 0으로 채우지 않는다(계약 §2-7)."
        )

    if c.status != LC.STATUS_OK:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M KST")
        prev = (
            f"[{stamp} 갱신] 이전 저장값 "
            f"ex={_d(p.unit_price_ex_vat) or '—'} / inc={_d(p.unit_price_inc_vat) or '—'} "
            f"(원장 재계산으로 갱신)"
        )
        p.note = f"{p.note}\n{prev}" if p.note else prev

    p.unit_price_ex_vat = line.unit_cost_ex_vat
    p.unit_price_inc_vat = line.unit_cost_inc_vat
    p.supplier = line.shipment.shipper_name
    p.effective_date = line.shipment.declaration_date or line.shipment.eta
    p.linked_item_name = line.item_name
    p.linked_shipment_id = line.shipment_id
    db.flush()
    _propagate(db, p.material_id)  # D-CPP-55 경로 2
    return p, c


def add_manual_price(
    db: Session,
    material_id: int,
    *,
    unit_price_ex_vat: Decimal | None = None,
    unit_price_inc_vat: Decimal | None = None,
    supplier: str | None = None,
    effective_date: date | None = None,
    note: str | None = None,
) -> CostMaterialPrice:
    """국내 구매 부자재처럼 원장 파생이 원리적으로 불가한 종의 단가 (계약 §4 하이브리드 ②).

    ★둘 중 **하나도 안 주면 거부**한다 — 빈 단가 행은 「값이 있다」는 착시만 만든다.
    하나만 주면 나머지는 «없음»으로 남는다. **여기서 ×1.1을 하지 않는다**: 그 환산은
    원장의 규약(D-NAO-150 호환)이지 사람이 입력한 값에 대한 사실이 아니고, 사람이 어느
    기준으로 입력했는지를 모델이 추측하면 그게 창작이다.
    """
    m = get_material(db, material_id)
    if unit_price_ex_vat is None and unit_price_inc_vat is None:
        raise CostMenuError(
            "단가를 하나도 주지 않았다 — 빈 단가 행은 만들지 않는다(계약 §2-7)."
        )
    p = CostMaterialPrice(
        material_id=m.id,
        source="manual",
        import_invoice_line_id=None,
        supplier=supplier,
        unit_price_ex_vat=unit_price_ex_vat,
        unit_price_inc_vat=unit_price_inc_vat,
        effective_date=effective_date,
        note=note,
    )
    db.add(p)
    db.flush()
    _propagate(db, m.id)  # D-CPP-55 경로 3
    return p


def delete_price(db: Session, material_id: int, price_id: int) -> None:
    """연결·입력을 되돌린다 — 사람이 잘못 붙인 것을 사람이 뗀다."""
    p = (
        db.query(CostMaterialPrice)
        .filter(
            CostMaterialPrice.id == price_id,
            CostMaterialPrice.material_id == material_id,
        )
        .first()
    )
    if p is None:
        raise CostMenuError(f"단가 행 id={price_id}이 이 종에 없다.")
    db.delete(p)
    db.flush()
    # ★전파는 «올라갈 때»만이 아니라 «내려갈 때»도 돈다 — 단가가 사라지면 표준원가도
    #   「—」로 돌아가야 한다(0으로 채우지 않는다, 계약 §2-7 · 합격 15).
    _propagate(db, material_id)  # D-CPP-55 경로 4
