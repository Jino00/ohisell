# rocket_po_changes.py — 1P 발주 «관측된 변화» 조회 Harness
#
# 계약: docs/contracts/CONTRACT_1p_po_status_history.md (Jino 승인 2026-08-28 13:33 KST)
# 적재는 `rocket_supplier_sync._po_change_events` / `_persist_po_change_events`가 한다.
# 이 모듈은 **읽기 전용**이다 — 이벤트 표에서 원장으로 되쓰는 경로를 만들지 않는다(§3 금지선).
#
# ★이 모듈의 규율 하나: **«우리가 본 것»만 말하고 «실제로 일어난 것»을 주장하지 않는다.**
#   · 모든 변화는 `prev_observed_at ~ observed_at` **구간**으로 낸다. 시점을 단정하는 필드를
#     만들지 않는다(「~에 확정됨」이 아니라 「~ 사이에 RP→PA 관측」).
#   · `first_seen`은 **출현**이지 전이가 아니다 — 「PA로 처음 관측됨」 ≠ 「RP에서 PA로 바뀌는 것을 봄」.
#     화면 문구도 「X로 들어옴」·「신규 발주 발생」을 쓰지 않는다(§3 금지선).
#   · 해석(주체·원인·「취소」)을 붙이지 않는다. `RP→PA`는 사실이고 「우리가 확정했다」는 해석이다.
#
# ★회차의 경계(§7-5 해소, 2026-08-28 실측): **한 회차 = 한 번의 push**다.
#   `tools/rocket_supplier_fetcher.py:1474` `_push_po`가 수집한 전 페이지를 **한 요청**에 담고
#   (`json={"pages": pages}`), 호출부도 `:2448` 한 곳뿐이다. 백엔드는 그 한 요청을 한 `kst_now()`로
#   묶어 적재하므로, `observed_at`의 최댓값 하나가 회차를 정확히 가른다.
from __future__ import annotations

import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    CoupangRocketPoChangeLog,
    CoupangRocketPoIngestRound,
    CoupangRocketPurchaseOrder,
)

log = logging.getLogger(__name__)

# 화면 문구 — 백엔드가 정본이다(프론트는 렌더만 한다).
# ★「확인」을 쓰지 않는다: 이 화면에서 「확인」은 「거래명세서확인」(사람이 눌러 RI→CI로 보내는
#   동작)이라, 관측·수집 축에 같은 낱말을 쓰면 «아직 안 누른 건»으로 읽힌다(§3 금지선).
FIELD_LABEL = {
    "purchase_order_status": "상태",
    "order_qty": "발주수량",
    "receiving_qty": "입고수량",
    "vendor_confirmed_qty": "확정수량",
    "sum_of_order_amount": "발주금액",
    "sum_of_receiving_amount": "입고금액",
    "sum_of_vendor_confirmed_amount": "확정금액",
}
_AMOUNT_FIELDS = {
    "sum_of_order_amount", "sum_of_receiving_amount", "sum_of_vendor_confirmed_amount",
}


def _iso(dt) -> str | None:
    return dt.isoformat(sep=" ", timespec="minutes") if dt else None


def history_start(db: Session, vendor_id: str | None = None) -> str | None:
    """이력이 시작된 시각(가장 오래된 관측). None이면 아직 한 회차도 안 쌓였다.

    ★화면이 이 값을 «자백»해야 한다 — 소급이 원리적으로 불가하므로(§7 전제 1), 이 값을 안 보이면
      「이력에 없음」이 「변화 없음」으로 읽힌다(원칙22: 미수집 ≠ 없음).
    """
    # ★vendor 필터를 탄다(적대 리뷰 1R P2-3): `latest_round`는 타는데 여기만 안 타면
    #   vendor가 늘었을 때 «남의 이력 시작일»이 내 화면에 뜬다.
    q = db.query(func.min(CoupangRocketPoChangeLog.observed_at))
    if vendor_id is not None:
        q = q.filter(CoupangRocketPoChangeLog.vendor_id == vendor_id)
    return _iso(q.scalar())


def latest_round(db: Session, vendor_id: str | None):
    """마지막 **수집 회차**의 시각. ★이벤트 표가 아니라 **원장**(`synced_at`)에서 읽는다.

    ★★왜 이벤트의 max(observed_at)가 아닌가(2026-08-28 구현 중 테스트가 잡은 결함):
      이번 수집에 변화가 0건이면 이벤트 표의 최신 시각은 **지난 회차**를 가리킨다. 그걸 「이번
      수집에서 달라진 것」으로 내면 **지난 변화를 이번 것처럼 보여주는** 화면이 된다 —
      「굳은 것을 산 것처럼 보여주지 않는다」(rocketPipelineTabs 정직성 규칙 3)의 정면 위반이다.
      회차는 «수집이 언제 돌았나»가 정하고, 그 회차의 이벤트가 0건이면 **0건이라고 말해야** 한다.
    ★한 회차 = 한 push다(§7-5 실측): `_push_po`가 전 페이지를 한 요청에 담고 호출부도 한 곳뿐이라
      `_upsert_po`가 그 회차의 `now` 하나로 전 행의 `synced_at`을 찍는다.
    """
    q = db.query(func.max(CoupangRocketPurchaseOrder.synced_at))
    if vendor_id is not None:
        q = q.filter(CoupangRocketPurchaseOrder.vendor_id == vendor_id)
    return q.scalar()


def round_result(db: Session, observed_at) -> dict:
    """그 회차의 적재 결과 — ★`dropped > 0`이면 화면이 「달라진 게 없다」고 말하면 안 된다.

    ★적대 리뷰 1R P1-1: 초판은 이 값을 로그와 페처 응답으로만 냈다. 조회 API가 원리적으로
      못 읽으니, 이벤트 적재가 통째로 실패한 회차에도 화면이 「이번 수집에서는 달라진 발주가
      없습니다」를 **적극적으로 단언**했다(전이가 실제로 있었는데도). 침묵보다 나쁘다.
    ★가장 유력한 발현 경로가 이 저장소의 상습 사고다 — 코드가 마이그레이션보다 먼저 배포되면
      매 회차 전량 drop이고 화면은 매번 「없습니다」다.
    """
    if observed_at is None:
        return {"records": None, "changes": None, "dropped": None, "error": None}
    r = (
        db.query(CoupangRocketPoIngestRound)
        .filter(CoupangRocketPoIngestRound.observed_at == observed_at).first()
    )
    if r is None:
        # 회차 기록 자체가 없다 — 배선 전 수집이거나 기록마저 실패한 회차다. 「0건」이 아니다.
        return {"records": None, "changes": None, "dropped": None, "error": None}
    return {"records": r.records, "changes": r.changes, "dropped": r.dropped, "error": r.error}


def _amount_of(db: Session, seqs: list[int]) -> dict[int, int]:
    """발주별 발주금액 — 「N건 · 금액」의 금액 쪽. 원장에서 읽는다(이벤트는 diff만 갖는다)."""
    if not seqs:
        return {}
    P = CoupangRocketPurchaseOrder
    return {
        r[0]: int(r[1] or 0)
        for r in db.query(P.purchase_order_seq, P.sum_of_order_amount)
        .filter(P.purchase_order_seq.in_(sorted(set(seqs)))).all()
    }


def latest_round_changes(db: Session, vendor_id: str | None) -> dict:
    """★화면 표면: 「이번 수집에서 달라진 것」.

    「처음 본 발주」와 「상태가 바뀐 발주」를 **다른 묶음**으로 낸다 — 이 계약의 발단이 정확히
    그 둘의 혼동이었다(신규 유입을 「확정했기 때문」으로 발화, 2026-08-28).
    """
    at = latest_round(db, vendor_id)
    start = history_start(db, vendor_id)
    if at is None or start is None:
        return {
            "round_at": _iso(at),
            "history_start": start,
            "round": round_result(db, at),
            "first_seen": {"count": 0, "amount": 0, "rows": []},
            "changed": {"count": 0, "amount": 0, "rows": []},
            "note": "아직 관측 이력이 없습니다 — 다음 수집부터 쌓입니다.",
        }

    C = CoupangRocketPoChangeLog
    q = db.query(C).filter(C.observed_at == at)
    if vendor_id is not None:
        q = q.filter(C.vendor_id == vendor_id)
    evs = q.order_by(C.purchase_order_seq, C.field).all()

    first_seqs = [e.purchase_order_seq for e in evs if e.event == "first_seen"]
    # ★처음 본 발주는 «변화»에서 뺀다 — 같은 회차에 first_seen과 field_change가 같이 나올 수
    #   없지만(신규는 diff 대상이 없다), 방어적으로 가른다.
    # ★루프 «밖»에서 한 번만 만든다(적대 리뷰 1R P2-2): 안에 두면 첫 회차 약 2,700건에서
    #   2,700² ≈ 7.3M 비교가 된다.
    first_set = set(first_seqs)
    changed_map: dict[int, list] = {}
    for e in evs:
        if e.event != "field_change" or e.purchase_order_seq in first_set:
            continue
        changed_map.setdefault(e.purchase_order_seq, []).append(e)

    amt = _amount_of(db, first_seqs + list(changed_map))

    first_rows = [
        {
            "purchase_order_seq": s,
            "status_when_first_seen": next(
                (e.after_value for e in evs
                 if e.purchase_order_seq == s and e.event == "first_seen"), None),
            "order_amount": amt.get(s, 0),
            # ★문구: 「X로 들어옴」이 아니다. 우리가 그때 처음 봤을 뿐이다.
            "label": "처음 관측됨",
        }
        for s in sorted(set(first_seqs))
    ]

    changed_rows = []
    for s, es in sorted(changed_map.items()):
        prev = next((e.prev_observed_at for e in es if e.prev_observed_at), None)
        st = next((e for e in es if e.field == "purchase_order_status"), None)
        changed_rows.append({
            "purchase_order_seq": s,
            "order_amount": amt.get(s, 0),
            # 상태가 바뀌었으면 전이를, 아니면 None(수량·금액만 변한 건).
            "status_from": st.before_value if st else None,
            "status_to": st.after_value if st else None,
            # ★시점이 아니라 구간이다.
            "observed_from": _iso(prev),
            "observed_to": _iso(at),
            "fields": [
                {
                    "field": e.field,
                    "label": FIELD_LABEL.get(e.field, e.field),
                    "before": e.before_value,
                    "after": e.after_value,
                    "is_amount": e.field in _AMOUNT_FIELDS,
                    "delta": _delta(e.before_value, e.after_value),
                }
                for e in es
            ],
        })

    rr = round_result(db, at)
    return {
        "round_at": _iso(at),
        "history_start": start,
        # ★이 회차에 이벤트를 몇 건 버렸나. 0이 아니면 화면은 「달라진 게 없다」고 말하면 안 된다.
        "round": rr,
        "first_seen": {
            "count": len(first_rows),
            "amount": sum(r["order_amount"] for r in first_rows),
            "rows": first_rows,
        },
        "changed": {
            "count": len(changed_rows),
            "amount": sum(r["order_amount"] for r in changed_rows),
            "rows": changed_rows,
        },
        # 화면이 그대로 쓴다. 「확인」을 쓰지 않는다(§3).
        "note": (
            "「처음 관측됨」은 그 발주가 이번 수집에서 우리 눈에 처음 들어왔다는 뜻이지 "
            "«그때 발주가 생겼다»는 뜻이 아닙니다. 상태 변화도 «언제» 바뀌었는지는 모르고 "
            "«두 수집 사이에» 바뀐 것만 압니다."
        ),
    }


def _delta(before: str | None, after: str | None) -> int | None:
    """숫자 필드면 증감. 숫자가 아니면 None — 0으로 접지 않는다(원칙22)."""
    try:
        return int(float(after)) - int(float(before))
    except (TypeError, ValueError):
        return None


def po_history(db: Session, seq: int) -> dict:
    """★화면 표면: 발주 1건의 관측 이력 전체(시간순).

    「이 발주를 우리가 언제 확정했나」에 이 화면이 답한다 — 단 «시점»이 아니라 «구간»으로.
    """
    C = CoupangRocketPoChangeLog
    evs = (
        db.query(C).filter(C.purchase_order_seq == seq)
        .order_by(C.observed_at, C.id).all()
    )
    start = history_start(db)
    # ★「배선 전 발주」와 「그런 발주 없음」을 가른다(적대 리뷰 1R P1-2 곁가지).
    #   구판은 없는 발주번호에도 「이력은 …부터입니다」라고 답했다 — 모름을 아는 척한 것이다.
    known = (
        db.query(CoupangRocketPurchaseOrder.purchase_order_seq)
        .filter(CoupangRocketPurchaseOrder.purchase_order_seq == seq).first()
        is not None
    )
    rows = []
    for e in evs:
        rows.append({
            "event": e.event,
            "field": e.field or None,
            "label": FIELD_LABEL.get(e.field, e.field) if e.field else None,
            "before": e.before_value,
            "after": e.after_value,
            "observed_from": _iso(e.prev_observed_at),
            "observed_to": _iso(e.observed_at),
            "is_amount": e.field in _AMOUNT_FIELDS,
            "delta": _delta(e.before_value, e.after_value) if e.field else None,
        })
    return {
        "purchase_order_seq": seq,
        "rows": rows,
        "history_start": start,
        # ★이력 0건을 「변화 없음」으로 읽으면 안 된다. 배선 전 발주는 원리적으로 기록이 없다.
        "known_po": known,
        "empty_reason": (
            None if rows else
            "이 발주번호를 우리 원장에서 본 적이 없습니다." if not known else
            f"이력은 {start[:10]}부터입니다 — 그 전 변화는 기록이 없습니다." if start else
            "아직 관측 이력이 없습니다 — 다음 수집부터 쌓입니다."
        ),
    }
