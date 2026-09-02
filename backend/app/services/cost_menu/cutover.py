"""컷오버 — `product_master.cost_price`를 **정본값으로 맞추는 유일한 문** (계약 D-CPP-64 §4 S3).

## 왜 «한 벌»인가

계약 §3-B가 「컷오버 경로 밖에서 `cost_price`를 쓰는 코드를 새로 만들지 않는다」를 금지선으로
못 박았다. 그 이전 상태(ref 119 §3-1)가 정확히 그 반대였기 때문이다 — 값을 쓰는 문이 넷인데
**넷 다 이력을 안 남겼고**, 그래서 「447건이 어긋난 채 감시기는 초록」이 재생산됐다.
그러니 이 파일이 문 하나이고, 이력은 `cost_price_history` 한 벌을 지난다.

## 이 모듈이 지키는 세 가지

1. **클라이언트가 보낸 값을 쓰지 않는다.** 실행 시점에 `truth_source.truth_board(db)`를 **다시
   돌려** 그 자리에서 계산된 정본값만 쓴다. 화면이 5분 전에 본 숫자를 그대로 실어 보내면
   그 사이 단가가 바뀌어도 옛 값이 굳는다 — 「보여준 값」과 「쓰는 값」이 갈리는 순간
   컷오버는 감사 불가능해진다.
2. **대상이 아닌 행은 원리적으로 못 쓴다.** 보류(`held`)·정본 없음(`none`)은 정본값이 `None`이라
   쓸 값 자체가 없고, 격차가 `MATCH_EPSILON` 미만인 행은 이미 일치다. 요청이 그런 SKU를
   지목해도 **건너뛰고 사유를 돌려준다** — 조용히 성공으로 세지 않는다.
3. **값 변경과 이력이 같은 커밋에 들어간다.** `record_cost_price_change`는 commit하지 않으므로
   (그 모듈 헤더) 「값은 바뀌었는데 이력은 없다」가 원리적으로 불가능하다.

## 무해성의 표면

계약 §4 S3 셋째 항목이 「클릭 없인 한 건도 안 움직인다」를 합격기준으로 둔다. 이 모듈은
**부르지 않으면 아무 일도 하지 않는다** — 스케줄러·훅·부팅 경로 어디에도 걸지 않는다.
그 0건을 이력 패널에서 확인하는 것이 그 항목의 증거다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from ...models import ProductMaster
from ..cost_price_history import PATH_CUTOVER, record_cost_price_change
from . import truth_source as TS

#: 요청이 대상을 고르는 방법. 셋 중 하나여야 한다 — 「아무것도 안 고름」이 「전건」으로
#: 해석되면 실수 클릭 한 번이 963 SKU를 움직인다.
SCOPE_ALL = "all"
SCOPE_SKUS = "skus"
SCOPE_CAUSE = "cause"

#: 건너뛴 사유 어휘. 자유 문자열을 쓰지 않는 이유는 `cost_price_history.KNOWN_PATHS`와 같다 —
#: 화면이 이 값으로 묶어 세는 순간 오타가 조용히 분모를 바꾼다.
SKIP_NOT_READY = "not_cutover_ready"
SKIP_UNKNOWN_SKU = "unknown_sku"

SKIP_SENTENCE = {
    SKIP_NOT_READY: "컷오버 대상이 아니다 — 정본이 없거나(보류·정본없음) 이미 일치한다",
    SKIP_UNKNOWN_SKU: "정본 판별표에 없는 SKU다 — `product_master`에 없거나 이름이 다르다",
}


def _ready_rows(board: dict) -> list[dict]:
    """정본 판별표에서 **컷오버 대상만** 골라낸다.

    ★판정을 여기서 다시 «발명»하지 않는다 — `truth_source`가 `cutover_ready_count`를 셀 때
      쓴 규칙과 **같은 규칙**이어야 한다. 두 벌이 되면 화면이 세는 278과 실제로 움직이는
      수가 갈리고, 그건 이 저장소가 반복해 밟은 「로직 두 벌」이다(교훈 #375).
    """
    out = []
    for r in board["items"]:
        if r["truth_type"] not in (TS.TRUTH_COMPUTED, TS.TRUTH_PURCHASED):
            continue
        gap = TS._dec(r["gap"])
        if gap is None or abs(gap) < TS.MATCH_EPSILON:
            continue
        out.append(r)
    return out


def preview(db: Session) -> dict:
    """컷오버 «전» 화면이 보여줄 것 — 무엇이 몇 건 · old→new · Σ격차.

    ★읽기 전용이다. 계약 §4 S3 첫째 항목이 요구하는 「클릭 «전»에 SKU 수·old→new·Σ격차가
      선다」의 재료가 이 payload다.
    """
    board = TS.truth_board(db)
    ready = _ready_rows(board)

    groups: dict[str, dict] = {}
    for r in ready:
        cause = r["cause"]
        g = groups.setdefault(
            cause,
            {
                "cause": cause,
                "cause_ref118": r.get("cause_ref118"),
                "reason": r.get("reason"),
                "sku_count": 0,
                "gap_sum": Decimal(0),
                "items": [],
            },
        )
        gap = TS._dec(r["gap"]) or Decimal(0)
        g["sku_count"] += 1
        g["gap_sum"] += gap
        g["items"].append(
            {
                "internal_sku": r["internal_sku"],
                "product_name": r["product_name"],
                "old_value": r["current_cost_price"],
                "new_value": r["truth_value"],
                "gap": r["gap"],
                "truth_label": r["truth_label"],
            }
        )

    ordered = sorted(groups.values(), key=lambda g: -abs(g["gap_sum"]))
    for g in ordered:
        g["gap_sum"] = str(g["gap_sum"])
        g["items"].sort(key=lambda i: i["internal_sku"])

    return {
        "groups": ordered,
        "total_sku_count": len(ready),
        "total_gap_sum": str(sum((TS._dec(r["gap"]) or Decimal(0)) for r in ready)),
        # ★컷오버로 «못» 고치는 것을 같은 payload에 싣는다. 이걸 빼면 화면이 「278건 하면
        #   끝」으로 읽히는데, 실제로는 681건이 정본 자체가 없어서 대상이 아니다.
        "not_eligible": {
            "held_count": board["census"]["held_count"],
            "none_count": board["census"]["none_count"],
            "sentence": (
                "보류·정본 없음은 컷오버 대상이 아니다 — 맞출 «정본»이 아직 없다. "
                "컷오버가 아니라 정본을 만드는 일(레시피 보강·매입가 승인)이 선행이다."
            ),
        },
    }


def execute(
    db: Session,
    *,
    scope: str,
    skus: Optional[Iterable[str]] = None,
    cause: Optional[str] = None,
    actor: Optional[str] = None,
) -> dict:
    """정본값을 `cost_price`에 쓴다. **부르지 않으면 아무 일도 일어나지 않는다.**

    ★`scope`가 어휘 밖이면 `ValueError` — 기본값으로 「전건」을 두지 않는다. 실수로 빈 요청이
      왔을 때 963 SKU가 움직이는 것이 이 문에서 가장 비싼 사고다.
    ★commit은 부르는 쪽(라우터)이 한다 — 값과 이력이 같은 트랜잭션에 있어야 한다.
    """
    if scope not in (SCOPE_ALL, SCOPE_SKUS, SCOPE_CAUSE):
        raise ValueError(
            f"알 수 없는 컷오버 범위 '{scope}' — 어휘는 "
            f"{sorted((SCOPE_ALL, SCOPE_SKUS, SCOPE_CAUSE))}다"
        )
    if scope == SCOPE_SKUS and not skus:
        raise ValueError("scope='skus'인데 SKU 목록이 비었다 — 전건으로 해석하지 않는다")
    if scope == SCOPE_CAUSE and not cause:
        raise ValueError("scope='cause'인데 사유 코드가 없다 — 전건으로 해석하지 않는다")

    board = TS.truth_board(db)
    ready_by_sku = {r["internal_sku"]: r for r in _ready_rows(board)}
    all_skus_on_board = {r["internal_sku"] for r in board["items"]}

    # 요청한 대상 목록을 만든다. ★대상 판정은 언제나 «지금» 계산된 표를 기준으로 한다.
    if scope == SCOPE_ALL:
        requested = list(ready_by_sku.keys())
    elif scope == SCOPE_CAUSE:
        requested = [s for s, r in ready_by_sku.items() if r["cause"] == cause]
    else:
        requested = list(dict.fromkeys(skus or []))

    products = {
        p.internal_sku: p
        for p in db.query(ProductMaster)
        .filter(ProductMaster.internal_sku.in_(requested))
        .all()
    } if requested else {}

    changed: list[dict] = []
    skipped: list[dict] = []
    gap_closed = Decimal(0)

    for sku in requested:
        row = ready_by_sku.get(sku)
        if row is None:
            reason = SKIP_UNKNOWN_SKU if sku not in all_skus_on_board else SKIP_NOT_READY
            skipped.append(
                {"internal_sku": sku, "skip_reason": reason, "sentence": SKIP_SENTENCE[reason]}
            )
            continue
        product = products.get(sku)
        if product is None:
            skipped.append(
                {
                    "internal_sku": sku,
                    "skip_reason": SKIP_UNKNOWN_SKU,
                    "sentence": SKIP_SENTENCE[SKIP_UNKNOWN_SKU],
                }
            )
            continue

        old_value = product.cost_price
        new_value = TS._dec(row["truth_value"])
        # ★`_ready_rows`가 이미 걸렀지만 한 번 더 본다 — 여기서 None이 통과하면 원가가
        #   NULL이 되고, 그건 「원가 0원」보다 나쁜 상태다(손익이 조용히 틀린다).
        if new_value is None:
            skipped.append(
                {
                    "internal_sku": sku,
                    "skip_reason": SKIP_NOT_READY,
                    "sentence": SKIP_SENTENCE[SKIP_NOT_READY],
                }
            )
            continue

        reason = (
            f"정본 컷오버 — {row['truth_label']}"
            f"({row.get('cause_ref118') or row['cause']})"
            f" · 근거 {row.get('reason') or '정본 판별표'}"
        )
        history = record_cost_price_change(
            db,
            internal_sku=sku,
            product_id=product.id,
            old_value=old_value,
            new_value=new_value,
            path=PATH_CUTOVER,
            actor=actor,
            reason=reason[:500],
        )
        if history is None:
            # 값이 같으면 이력이 안 생긴다 — 그러면 쓰지도 않는다(잡음 없는 이력의 대가).
            skipped.append(
                {
                    "internal_sku": sku,
                    "skip_reason": SKIP_NOT_READY,
                    "sentence": SKIP_SENTENCE[SKIP_NOT_READY],
                }
            )
            continue

        product.cost_price = new_value
        gap_closed += TS._dec(row["gap"]) or Decimal(0)
        changed.append(
            {
                "internal_sku": sku,
                "product_name": row["product_name"],
                "old_value": str(old_value) if old_value is not None else None,
                "new_value": str(new_value),
                "gap": row["gap"],
                "cause": row["cause"],
            }
        )

    changed.sort(key=lambda c: c["internal_sku"])
    skipped.sort(key=lambda s: s["internal_sku"])
    return {
        "scope": scope,
        "requested_count": len(requested),
        "changed_count": len(changed),
        "skipped_count": len(skipped),
        "gap_closed": str(gap_closed),
        "changed": changed,
        "skipped": skipped,
    }
