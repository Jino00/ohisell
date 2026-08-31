# cost_price_history.py — `product_master.cost_price`가 움직인 사건을 남기는 **단일 출처**.
#
# ## 왜 이 파일이 여기 있나 (계약 D-CPP-64 §4 S1-①·②, 2026-08-31)
#
# `cost_price`를 쓰는 경로는 넷인데(ref 119 §3-1) **넷 다 이력을 안 남겼다.** 그래서
# 「수정이 실수 없이 됐나」에 답할 방법이 없었고, `updated_at`은 20일째 정지해 있어
# 대체재도 못 됐다(ref 118 §2-1). 값을 바꾸는 자리마다 insert를 손으로 적으면 **한 자리가
# 빠지고 그 자리만 조용히 이력이 없는** 상태가 된다 — 이 저장소가 반복해 겪은 모양이다
# (교훈: 「값이 도는 층과 사람이 읽는 층을 같이 지킨다」의 이력판). 그래서 **기록도 문지기도
# 이 모듈 한 벌**이고, 경로 이름조차 여기 상수로 둔다.
#
# ## 두 가지를 한다
#
#   1. `record_cost_price_change` — 값이 **실제로 바뀐 사건만** append 한다.
#   2. `REJECTION_SENTENCE` — 컷오버 경로 밖 쓰기를 거부할 때 사람이 읽는 **한 문장**.
#      API 응답과 화면이 같은 문장을 쓰게 하려는 것이다(문구가 두 벌이면 하나만 고쳐진다).
#
# ★DB에 commit 하지 않는다 — 부르는 쪽의 트랜잭션에 얹힌다. 값 변경과 이력이 **같은 커밋**에
#   들어가야 「값은 바뀌었는데 이력은 없다」가 원리적으로 불가능해진다.
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy.orm import Session

from app.models import CostPriceHistory

#: 값이 들어온 «문»의 어휘. ref 119 §3-1의 경로 목록과 같은 말을 쓴다.
#: ★자유 문자열을 허용하지 않는 이유: 경로 이름이 오타로 갈리면 「어느 문이 열려 있나」를
#:   세는 순간 조용히 틀린다 — 이 계약의 질문이 정확히 그것이다.
PATH_EXCEL_UPLOAD = "excel_upload"
PATH_MAPPING_INGEST = "mapping_ingest"
PATH_PRODUCT_CREATE = "product_create"
PATH_PRODUCT_UPDATE = "product_update"
#: S3에서 생긴다(정식 컷오버) · S4에서 생긴다(자동 추종). 지금은 아무도 안 쓰지만 어휘를
#: 미리 둔다 — 나중에 붙일 때 이름을 새로 발명하면 화면 필터가 갈린다.
PATH_CUTOVER = "cutover"
PATH_AUTO = "auto"

KNOWN_PATHS = frozenset(
    {
        PATH_EXCEL_UPLOAD,
        PATH_MAPPING_INGEST,
        PATH_PRODUCT_CREATE,
        PATH_PRODUCT_UPDATE,
        PATH_CUTOVER,
        PATH_AUTO,
    }
)

#: ★거부 사유 **한 문장**. 계약 §4 S1-②가 문언을 못 박았다 — 원칙을 그대로 말한다.
#:   API 응답(`detail`)과 제품 화면이 **이 상수 하나**를 쓴다. 프론트 사본은
#:   `frontend/src/lib/costPriceGate.ts`에 있고 테스트가 두 문자열의 일치를 지킨다.
REJECTION_SENTENCE = "원가는 원가 메뉴가 정본이다 — 정정은 원가 메뉴에서"


def _as_decimal(value) -> Optional[Decimal]:
    """비교 가능한 Decimal로. 못 바꾸면 None(그러면 «모른다»로 취급해 기록한다)."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def values_differ(old, new) -> bool:
    """«바뀌었나»를 **수치로** 판정한다.

    ★`Decimal("300") != Decimal("300.00")`은 파이썬에선 False지만 문자열 비교로는 다르다.
      이 함수가 없으면 `Numeric(12,2)` 왕복만으로 «변경» 행이 매일 쌓인다 — 이력이 시끄러워지면
      아무도 안 읽고, 안 읽는 이력은 없는 것과 같다.
    ★한쪽이 None이면(신규 생성) **사건이다** — 「없음 → 값」은 변경이다.
    """
    o, n = _as_decimal(old), _as_decimal(new)
    if o is None and n is None:
        return False
    if o is None or n is None:
        return True
    return o != n


def record_cost_price_change(
    db: Session,
    *,
    internal_sku: str,
    old_value,
    new_value,
    path: str,
    actor: Optional[str] = None,
    reason: Optional[str] = None,
    product_id: Optional[int] = None,
) -> Optional[CostPriceHistory]:
    """값이 실제로 바뀐 사건만 append 하고 그 행을 돌려준다. 안 바뀌었으면 None.

    ★commit 하지 않는다(모듈 헤더). 부르는 쪽이 값 변경과 **같은 커밋**에 넣는다.
    ★`path`가 어휘 밖이면 `ValueError` — 조용히 통과시키지 않는다. 오타 난 경로 이름은
      「어느 문이 열려 있나」를 세는 순간 틀린 답을 만든다.
    """
    if path not in KNOWN_PATHS:
        raise ValueError(
            f"알 수 없는 cost_price 변경 경로 '{path}' — "
            f"어휘는 {sorted(KNOWN_PATHS)}다(services/cost_price_history.py)"
        )
    if not values_differ(old_value, new_value):
        return None

    row = CostPriceHistory(
        internal_sku=internal_sku,
        product_id=product_id,
        old_value=_as_decimal(old_value),
        new_value=_as_decimal(new_value),
        path=path,
        actor=actor,
        reason=reason,
    )
    db.add(row)
    return row


def list_cost_price_history(
    db: Session,
    *,
    limit: int = 100,
    internal_sku: Optional[str] = None,
    path: Optional[str] = None,
) -> dict:
    """이력 조회 payload. **비어 있는 이유를 같이 낸다.**

    ★0건은 「원가가 안 바뀌었다」가 아니라 대개 **「이력이 아직 시작 안 됐다」**다(이 표는
      소급이 불가하다 — 마이그레이션 이전의 변경은 영영 모른다). 둘을 같은 빈 목록으로 내면
      화면이 「이상 없음」이라고 읽는다. 이 저장소가 반복해 당한 형태다(교훈 #123).
    """
    q = db.query(CostPriceHistory)
    if internal_sku:
        q = q.filter(CostPriceHistory.internal_sku == internal_sku)
    if path:
        q = q.filter(CostPriceHistory.path == path)
    rows = (
        q.order_by(CostPriceHistory.created_at.desc(), CostPriceHistory.id.desc())
        .limit(limit)
        .all()
    )

    # 「언제부터 재기 시작했나」 — 필터와 무관한 **표 전체**의 첫 행이다. 화면이 이 시각을
    # 대고 「그 전은 모른다」고 말한다.
    first = (
        db.query(CostPriceHistory)
        .order_by(CostPriceHistory.created_at.asc(), CostPriceHistory.id.asc())
        .first()
    )
    total = db.query(CostPriceHistory).count()

    empty_reason: Optional[str] = None
    if not rows:
        if total == 0:
            empty_reason = (
                "이력이 아직 한 건도 없다 — 이 표는 배포 시점부터 쌓이고 그 이전의 변경은 "
                "기록이 없다(소급 불가). 「원가가 안 바뀌었다」는 뜻이 아니다."
            )
        else:
            empty_reason = "이 조건에 맞는 이력이 없다 — 다른 SKU·경로에는 기록이 있다."

    return {
        "items": [
            {
                "id": h.id,
                "internal_sku": h.internal_sku,
                "product_id": h.product_id,
                "old_value": None if h.old_value is None else str(h.old_value),
                "new_value": str(h.new_value),
                "path": h.path,
                "actor": h.actor,
                "reason": h.reason,
                "created_at": h.created_at.isoformat() if h.created_at else None,
            }
            for h in rows
        ],
        "total": total,
        #: 이력이 시작된 시각(UTC ISO). 소급이 불가하므로 화면이 이걸 자백한다.
        "started_at": (
            first.created_at.isoformat() if first and first.created_at else None
        ),
        "empty_reason": empty_reason,
    }
