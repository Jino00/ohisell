"""단가 행 ↔ 원장 라인 **재검사** — 순수 SA (D-CPP-53 / 계약 A′ S1, 적대 리뷰 1R P1-1·P1-2).

## 왜 이 모듈이 생겼나 (적대 리뷰 1R P1)

`cost_material_price`는 연결 시점의 원장 값을 **복사해 확정 저장**한다. 그 «근거 보존»은
정당한 의도다(모듈 `materials.py` 규약 2 — 원장이 재계산되면 이 행은 «그때 본 값»으로
남는다). 결함은 보존 자체가 아니라, **보존된 값이 아무 표시 없이 「최신 확정 로트 단가」
칸을 차지하고 어긋났다는 사실이 어느 표면에도 없었던 것**이다. 실제로 네 갈래로 샜다:

1. **reopen** — 원장은 단가를 지우는데(계약 B `ledger.reopen`: *"낡은 단가가 「확정된 값」인
   척 남는 것이 이 도메인에서 가장 위험하다"*) 이 층은 낡은 값을 계속 「최신」이라 불렀다.
2. **값 변경** — 환율 정정 후 재확정으로 원장이 198.91이 돼도 화면은 190.82 그대로.
3. **삭제** — `ondelete="CASCADE"`가 선언돼 있어도 앱 연결에 `PRAGMA foreign_keys=ON`이
   없어 SQLite가 FK를 강제하지 않는다 → 단가 행이 **고아로 살아남는다**.
4. **라인 순서 정정** — 계약 B `_replace_lines`가 라인을 지우고 다시 넣으면 **SQLite rowid가
   재사용**돼, `import_invoice_line_id=15`가 가리키던 cleaning kit이 다른 품목으로 바뀐다.

넷의 뿌리가 하나이므로 가드도 하나다: **조회 시점에 「지금도 그 라인이 있는가 / 확정인가 /
값이 같은가 / 같은 품목인가」를 되묻고, 어긋나면 화면이 스스로 자백한다.**

## 규약

- **판정은 여기서만 한다.** DB도 IO도 모른다 — 호출자가 사실(`LedgerFact`)을 떠다 준다
  (계약 §2-6: 산술·판단은 순수 SA 한 벌. 사본 두 벌은 «감시자가 감시 대상보다 낡는» 형태다).
- **`id`만 믿지 않는다** — rowid 재사용이 실증됐으므로 저장 시점의 **품목명**을 함께 대조한다.
- **어긋난 행을 지우지 않는다.** 근거 보존은 유지하고 «자격»만 뺀다
  (`counts_as_evidence=False` → `latest_price_*` 산정에서 빠진다). 왜냐하면 지우면 「무엇이
  어긋났었나」가 사라져 P1-1의 반대 방향으로 같은 병에 걸린다.
- **미확인은 «없음»이다** — 대조할 저장 스냅샷이 없으면 「대조 불가」라고 말하지, 조용히
  통과시키지도 억지로 미달 처리하지도 않는다(계약 §2-7의 결).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

# 판정 값. 「어긋남」을 한 단어로 접지 않는다 — 처방이 저마다 다르기 때문이다.
STATUS_MANUAL = "manual"  # 원장 파생이 아니다(사람이 입력) — 재검사 대상 자체가 아니다
STATUS_OK = "ok"
STATUS_MISSING = "missing"  # 원장 라인이 사라졌다(수입건 삭제 등) — 고아 행
STATUS_UNCONFIRMED = "unconfirmed"  # 수입건이 확정 해제됐다(reopen) — 원장은 단가를 지웠다
STATUS_ITEM_MISMATCH = "item_mismatch"  # 같은 id가 다른 품목을 가리킨다(rowid 재사용)
STATUS_CHANGED = "changed"  # 원장이 재확정돼 값이 달라졌다(환율 정정 등)


@dataclass(frozen=True)
class PriceSnapshot:
    """단가 행이 **저장 시점에 본 것**. 이 값들이 대조의 왼쪽이다."""

    source: str
    import_invoice_line_id: Optional[int]
    linked_item_name: Optional[str]
    linked_shipment_id: Optional[int]
    unit_price_ex_vat: Optional[Decimal]
    unit_price_inc_vat: Optional[Decimal]


@dataclass(frozen=True)
class LedgerFact:
    """**지금** 원장이 말하는 것. 라인이 없으면 호출자가 `None`을 준다."""

    shipment_id: Optional[int]
    shipment_status: Optional[str]
    item_name: Optional[str]
    unit_cost_ex_vat: Optional[Decimal]
    unit_cost_inc_vat: Optional[Decimal]


@dataclass(frozen=True)
class CheckResult:
    status: str
    ok: bool
    label: str
    detail: str
    #: `latest_price_*` 산정에 쓸 자격. 어긋난 행은 «보존은 되되 최신 자리는 못 차지한다».
    counts_as_evidence: bool
    #: 원장 값을 다시 복사해 고칠 수 있는가. 품목이 달라졌으면 **갱신이 아니라 사람의 재연결**이다.
    refreshable: bool
    #: 지금 원장 값(있으면) — 화면이 「저장값 vs 원장값」을 나란히 보여 주는 원료다.
    ledger_unit_price_ex_vat: Optional[Decimal] = None
    ledger_unit_price_inc_vat: Optional[Decimal] = None
    ledger_item_name: Optional[str] = None


def _eq(a: Optional[Decimal], b: Optional[Decimal]) -> bool:
    """둘 다 없으면 같다. 하나만 없으면 다르다. `Decimal`은 값으로 비교한다(190.82 == 190.820)."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return Decimal(a) == Decimal(b)


def _norm(name: Optional[str]) -> Optional[str]:
    return None if name is None else " ".join(name.split()).casefold()


def check(snapshot: PriceSnapshot, fact: Optional[LedgerFact]) -> CheckResult:
    """단가 행 1개의 «지금도 유효한가»를 판정한다.

    우선순위: 없음 > 확정 해제 > 품목 불일치 > 값 변경 > 정상. **더 근본적인 어긋남이
    덜 근본적인 것을 가린다** — 라인이 사라졌는데 「값이 다르다」고 말하면 처방이 틀린다.
    """
    if snapshot.source != "ledger":
        return CheckResult(
            status=STATUS_MANUAL,
            ok=True,
            label="수동 입력",
            detail="사람이 입력·확인한 값이다 — 원장 대조 대상이 아니다(계약 §4 하이브리드 ②).",
            counts_as_evidence=True,
            refreshable=False,
        )

    # 수입건이 통째로 사라졌는데 라인만 남은 경우도 «없음»이다 — 「확정이 풀렸다」로 읽으면
    # 처방(「원장에서 다시 확정해라」)이 존재하지 않는 대상을 가리킨다.
    if snapshot.import_invoice_line_id is None or fact is None or fact.shipment_status is None:
        return CheckResult(
            status=STATUS_MISSING,
            ok=False,
            label="원장 라인 없음",
            detail=(
                "이 단가가 나온 원장 라인이 지금은 없다(수입건 삭제 또는 라인 교체) — "
                "근거가 사라진 값이므로 「최신 단가」로 세지 않는다. 해제하고 다시 연결한다."
            ),
            counts_as_evidence=False,
            refreshable=False,
        )

    if fact.shipment_status != "confirmed":
        return CheckResult(
            status=STATUS_UNCONFIRMED,
            ok=False,
            label="수입건 확정 해제됨",
            detail=(
                f"수입건이 확정 상태가 아니다(status={fact.shipment_status}) — 원장은 그때 "
                "단가를 지웠다. 이 값은 «지난 확정의 잔상»이므로 「최신 단가」로 세지 않는다. "
                "원장에서 다시 확정한 뒤 「갱신」을 누른다."
            ),
            counts_as_evidence=False,
            refreshable=False,
            ledger_item_name=fact.item_name,
        )

    if snapshot.linked_item_name is not None and _norm(snapshot.linked_item_name) != _norm(
        fact.item_name
    ):
        return CheckResult(
            status=STATUS_ITEM_MISMATCH,
            ok=False,
            label="다른 품목을 가리킨다",
            detail=(
                f"연결 당시 품목은 「{snapshot.linked_item_name}」인데 지금 그 라인은 "
                f"「{fact.item_name}」이다 — 라인이 지워지고 다시 들어가면서 id가 재사용됐다. "
                "값을 덮어쓰지 않는다(그러면 다른 품목의 단가를 조용히 삼킨다) — "
                "해제하고 사람이 다시 연결한다."
            ),
            counts_as_evidence=False,
            refreshable=False,
            ledger_item_name=fact.item_name,
            ledger_unit_price_ex_vat=fact.unit_cost_ex_vat,
            ledger_unit_price_inc_vat=fact.unit_cost_inc_vat,
        )

    if not _eq(snapshot.unit_price_ex_vat, fact.unit_cost_ex_vat) or not _eq(
        snapshot.unit_price_inc_vat, fact.unit_cost_inc_vat
    ):
        return CheckResult(
            status=STATUS_CHANGED,
            ok=False,
            label="원장 값이 달라졌다",
            detail=(
                f"저장값 {_show(snapshot.unit_price_ex_vat)} / 현 원장값 "
                f"{_show(fact.unit_cost_ex_vat)} (VAT 제외) — 원장이 재계산됐다(환율 정정 등). "
                "저장값은 근거로 남기되 「최신 단가」로는 세지 않는다. 「갱신」이 원장 값을 다시 옮긴다."
            ),
            counts_as_evidence=False,
            refreshable=True,
            ledger_item_name=fact.item_name,
            ledger_unit_price_ex_vat=fact.unit_cost_ex_vat,
            ledger_unit_price_inc_vat=fact.unit_cost_inc_vat,
        )

    detail = "원장 라인이 지금도 확정 상태이고 값·품목이 저장값과 같다."
    if snapshot.linked_item_name is None:
        # 이 컬럼이 생기기 전에 만들어진 행. 값·확정 여부는 대조했으므로 미달이 아니지만,
        # «품목 동일성은 못 봤다»는 사실을 삼키지 않는다(계약 §2-7의 결).
        detail += " (저장 시점 품목명이 없어 품목 동일성은 대조하지 못했다 — 구 연결)"
    return CheckResult(
        status=STATUS_OK,
        ok=True,
        label="원장과 일치",
        detail=detail,
        counts_as_evidence=True,
        refreshable=True,
        ledger_item_name=fact.item_name,
        ledger_unit_price_ex_vat=fact.unit_cost_ex_vat,
        ledger_unit_price_inc_vat=fact.unit_cost_inc_vat,
    )


def _show(v: Optional[Decimal]) -> str:
    """**「없음」은 「0」이 아니다**(계약 §2-7) — 문구에서도 그렇다."""
    return "—" if v is None else str(v)
