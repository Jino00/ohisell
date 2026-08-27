# rocket_invoice_confirm.py — 1P 「거래명세서확인」(RI→CI) 실행 Harness
#
# 계약: docs/contracts/CONTRACT_1p_invoice_confirm_write.md (Jino 승인 2026-08-28 07:28 KST)
# 정찰: docs/references/106_ri_confirm_recon_20260827.md
#
# ★이 모듈은 «결정»만 한다. supplier에 실제 요청을 보내는 것은 Mac 페처다(런타임 경계 D-1:
#   supplier는 Akamai 뒤라 백엔드 직접 호출 금지). 계약 §2 첫 줄이 그 분업을 못 박는다 —
#   실행기가 판단까지 가지면 거부·감사 로그가 두 런타임으로 갈라지고, Mac 사본은 저장소와
#   어긋난 채 돌 수 있다(교훈 #371: 머지 ≠ Mac 배포).
#
# ★★재시도가 없다(계약 §3 금지선). 임대는 1회뿐이고 TTL이 지나면 **재임대가 아니라 unknown 종결**
#   이다. `refresh_contract`의 lease 계약(실패 시 반납 → 다음 폴에서 자동 재claim, 3회 예산)을
#   여기 재사용하지 않는다 — 그건 읽기용 설계라 그대로 얹으면 같은 확인을 두 번 누른다.
#
# ★★★멱등성이 `[미상]`이다(이미 CI인 건에 같은 POST를 보내면 어떻게 되는지 호출 전례가 0건).
#   실험으로 답하지 않고 **사전 GET 게이트**로 상황 자체를 안 만든다: POST 직전 발주상세를
#   GET해 `id="btnConfirmInvoice"` 존재를 실측하고, 없으면 POST 없이 already_confirmed로 끝낸다.
#   버튼 렌더가 서버측 상태 게이트임은 실측됐다(RI 3/3 존재 · CI·PA·RP 0/3 — ref 106 §2).
from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from sqlalchemy.orm import Session

from app.clients.coupang._base import CoupangWriteValidationError
from app.models import CoupangRocketInvoiceConfirm, CoupangRocketPurchaseOrder
from app.services.coupang._write_guard import guarded_write
from app.services.coupang.rocket_pipeline import _kst_naive_date_str, _last_collection_day
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# supplier 쓰기 표면은 **이 경로 하나뿐**이다(계약 §3). 다른 supplier 상태 변경 경로를
# 이 모듈에 추가하지 않는다 — 추가하려면 별도 계약이다.
CONFIRM_PATH = "/scm/purchase/order/confirmInvoice"
OPERATION = "1P 거래명세서확인(RI→CI)"

# 열린 상태 = 아직 결과가 안 난 명령. 같은 PO에 이게 있으면 새 명령을 만들지 않는다.
OPEN_STATES = ("pending", "claimed")
# 종결 상태.
TERMINAL_STATES = ("succeeded", "already_confirmed", "failed", "unknown")

# 임대 TTL — 이 시간이 지나도록 보고가 없으면 **재임대가 아니라 unknown 종결**이다.
#   ★20분인 이유: 페처 1회 실행(Chrome 기동 + 로그인 복구 + 수집)이 실측 ~1분이고, 데몬이
#     죽었다 launchd가 재기동하는 최악의 경우를 덮되, 그보다 길면 Jino가 화면 앞에서 무한정
#     「진행 중」을 보게 된다. 짧게 잡아도 손해는 「unknown으로 잠기고 재수집 후 다시 누른다」뿐
#     — 길게 잡을 때의 손해(모르는 채 방치)보다 싸다.
LEASE_TTL_MINUTES = 20

# 응답 body 원문 보존 상한. 자르되 넉넉히 — supplier가 실패를 구조화해 주지 않아서
# (`alert(data)`) body가 유일한 진단 재료다(계약 §3: success 불리언만 남기는 코드 금지).
_BODY_MAX = 20000


# ──────────────────────────────────────────────
# 판정 헬퍼
# ──────────────────────────────────────────────
def _po(db: Session, seq: int, vendor_id: str | None) -> CoupangRocketPurchaseOrder | None:
    q = db.query(CoupangRocketPurchaseOrder).filter(
        CoupangRocketPurchaseOrder.purchase_order_seq == seq
    )
    if vendor_id is not None:
        q = q.filter(CoupangRocketPurchaseOrder.vendor_id == vendor_id)
    return q.first()


def _rows_for(db: Session, seqs: list[int]) -> dict[int, list[CoupangRocketInvoiceConfirm]]:
    """PO별 명령 이력(최신 우선)."""
    out: dict[int, list[CoupangRocketInvoiceConfirm]] = {}
    if not seqs:
        return out
    C = CoupangRocketInvoiceConfirm
    for r in (
        db.query(C)
        .filter(C.purchase_order_seq.in_(sorted(set(seqs))))
        .order_by(C.requested_at.desc(), C.id.desc())
        .all()
    ):
        out.setdefault(r.purchase_order_seq, []).append(r)
    return out


# ★「원장에 아직 반영되지 않은 종결」 — 이 상태로 끝난 명령이 있으면 재수집 전까지 잠근다.
#   `failed`만 빠진다: `success == false`는 **아무 일도 안 일어났음이 확인된 유일한 상태**라
#   원장이 여전히 맞다. 나머지 셋은 원장의 `RI`가 이미 틀렸을 수 있는데, 굳음 검사
#   (`_last_collection_day` 기준)는 「수집일이 밀렸나」만 재므로 이 경우를 원리적으로 못 잡는다.
#   ⇒ 적대 리뷰 1R P1-3: 이걸 unknown에만 걸어 뒀더니 `succeeded` 직후에도 버튼이 살아 있어
#     **우리가 이미 틀렸다고 아는 원장**으로 두 번째 라이브 명령이 만들어졌다.
_UNREFLECTED_STATES = ("unknown", "succeeded", "already_confirmed")

_BLOCK_REASON = {
    "unknown": "결과 미상 — 재수집 전 재실행 불가",
    "succeeded": "확인 완료 — 재수집 반영 대기",
    "already_confirmed": "이미 처리됨 — 재수집 반영 대기",
}


def _unreflected_terminal(
    hist: list[CoupangRocketInvoiceConfirm], po: CoupangRocketPurchaseOrder | None
) -> CoupangRocketInvoiceConfirm | None:
    """재수집이 아직 실상태를 확인해 주지 않은 종결 명령.

    ★해제 판정을 이 표가 하지 않고 **원장이** 한다: 그 PO의 `synced_at`이 명령 종료 시각보다
      뒤면 재수집이 그 뒤의 진실을 봤다는 뜻이므로 풀린다. 별도 「해제」 쓰기 경로를 만들지
      않는 이유 — 해제를 사람이나 코드가 «선언»하는 순간 그게 곧 재시도 우회로가 된다.
    ★두 시각 다 KST naive다(`kst_now()` · 로켓 계열 `synced_at` 규약) — 비교가 성립한다.
      같은 행의 `po_created_at`은 UTC 저장이라 규약이 다르다. **여기서 그 컬럼을 쓰지 말 것.**
    """
    for r in hist:
        if r.state not in _UNREFLECTED_STATES:
            continue
        if po is None or po.synced_at is None or r.finished_at is None:
            return r
        if po.synced_at <= r.finished_at:
            return r
    return None


def _open_command(hist: list[CoupangRocketInvoiceConfirm]) -> CoupangRocketInvoiceConfirm | None:
    for r in hist:
        if r.state in OPEN_STATES:
            return r
    return None


def _gate(
    db: Session, seq: int, vendor_id: str | None
) -> tuple[CoupangRocketPurchaseOrder, list[CoupangRocketInvoiceConfirm]]:
    """명령 생성 전 검사 5종. 통과 못 하면 CoupangWriteValidationError(→400).

    ★여기서 막는 것은 «우리 원장으로 알 수 있는 것»뿐이다. 「지금 정말 누를 수 있는가」의
      정본은 여전히 사전 GET(버튼 존재)이고, 그건 페처가 POST 직전에 잰다(계약 §2).
    """
    po = _po(db, seq, vendor_id)
    if po is None:
        raise CoupangWriteValidationError(f"발주 {seq}: 원장에 없습니다.")
    if po.purchase_order_status != "RI":
        raise CoupangWriteValidationError(
            f"발주 {seq}: 상태가 RI(거래명세서확인요청)가 아닙니다 — 현재 "
            f"{po.purchase_order_status or '모름'}. 확인 버튼은 RI에서만 뜹니다."
        )
    last_day = _last_collection_day(db, vendor_id)
    synced_day = _kst_naive_date_str(po.synced_at)
    if last_day is not None and synced_day != last_day:
        raise CoupangWriteValidationError(
            f"발주 {seq}: 상태가 굳었습니다(마지막 확인 {synced_day or '모름'}, "
            f"마지막 수집 {last_day}). 「미종결 발주 재수집」을 먼저 돌리세요 — "
            "굳은 원장은 «지금 참인 상태»가 아니라 «마지막으로 본 상태»입니다."
        )
    hist = _rows_for(db, [seq]).get(seq, [])
    open_cmd = _open_command(hist)
    if open_cmd is not None:
        raise CoupangWriteValidationError(
            f"발주 {seq}: 이미 진행 중인 확인 명령이 있습니다"
            f"(#{open_cmd.id}, {open_cmd.state}, 요청 {open_cmd.requested_at:%Y-%m-%d %H:%M} KST)."
        )
    unref = _unreflected_terminal(hist, po)
    if unref is not None:
        if unref.state == "unknown":
            raise CoupangWriteValidationError(
                f"발주 {seq}: 직전 명령 #{unref.id}의 **결과를 모릅니다**"
                f"({unref.error or '사유 미상'}). 요청이 supplier에 갔는지 확인되지 않았으므로 "
                "재수집으로 실상태를 확인하기 전에는 다시 실행할 수 없습니다(재시도 금지)."
            )
        raise CoupangWriteValidationError(
            f"발주 {seq}: 직전 명령 #{unref.id}가 **{_BLOCK_REASON[unref.state]}** 상태입니다. "
            "원장의 상태(RI)는 그 명령 이전에 본 값이라 지금 실행 근거가 못 됩니다 — "
            "재수집이 실상태를 확인한 뒤에 다시 보세요."
        )
    return po, hist


# ──────────────────────────────────────────────
# ① 사람이 쓰는 입구 — 미리보기 / 실행
# ──────────────────────────────────────────────
def preview_confirm(db: Session, seq: int, vendor_id: str | None) -> dict:
    """dry-run 미리보기. **supplier로 아무것도 나가지 않는다** — 명령도 만들지 않는다.

    화면 모달이 「무엇을 보낼 것인가」를 사람에게 보이는 표면이고, 이 응답이 그 원문이다.
    """
    po, _hist = _gate(db, seq, vendor_id)
    out = guarded_write(
        operation=OPERATION,
        method="POST",
        path=f"{CONFIRM_PATH}?purchaseOrderSeq={seq}",
        # ★바디 없음이 실측이다(ref 106 §3: `$.post(url, callback)` — 2번째 인자가 함수라
        #   jQuery가 data를 생략한다). 없는 바디를 지어내지 않는다.
        payload={},
        sa_call=lambda: None,   # dry_run이라 호출되지 않는다
        dry_run=True,
    )
    out["purchase_order_seq"] = seq
    out["received_amount"] = int(po.sum_of_receiving_amount or 0)
    out["po_status"] = po.purchase_order_status
    out["irreversible"] = True
    out["irreversible_note"] = (
        "supplier에 거래명세서확인을 전송합니다 — 되돌릴 수 없습니다. "
        "CI(거래명세서 확인)에서 RI로 되돌리는 경로가 supplier 화면에 없습니다."
    )
    return out


def request_confirm(
    db: Session,
    seq: int,
    vendor_id: str | None,
    *,
    confirm: str | None,
    note: str | None = None,
) -> dict:
    """라이브 명령 1건 생성. **모달 「실행」 클릭이 유일한 생성점**이다(계약 §3).

    guarded_write가 `CONFIRM_LIVE_WRITE` 토큰을 검사하고(없으면 403) 라이브 경로에 WARNING
    감사 로그를 남긴다. 여기서 «실행»되는 것은 supplier 호출이 아니라 **명령 적재**다 —
    실제 POST는 Mac 페처가 사전 GET 게이트를 통과한 뒤에만 보낸다.
    """
    po, _hist = _gate(db, seq, vendor_id)

    def _insert() -> dict:
        row = CoupangRocketInvoiceConfirm(
            purchase_order_seq=seq,
            vendor_id=po.vendor_id,
            state="pending",
            requested_at=kst_now(),
            requested_note=(note or None) and str(note)[:200],
            received_amount_at_request=int(po.sum_of_receiving_amount or 0),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        log.warning(
            "[rocket-confirm] 명령 적재 #%d — PO %s (입고액 %s원). 되돌릴 수 없는 회계 확정 요청.",
            row.id, seq, row.received_amount_at_request,
        )
        return {"command_id": row.id, "state": row.state}

    out = guarded_write(
        operation=OPERATION,
        method="POST",
        path=f"{CONFIRM_PATH}?purchaseOrderSeq={seq}",
        payload={},
        sa_call=_insert,
        dry_run=False,
        confirm=confirm,
    )
    return out


# ──────────────────────────────────────────────
# ② 페처가 쓰는 입구 — 임대 / 보고
# ──────────────────────────────────────────────
def expire_stale_claims(db: Session) -> int:
    """TTL 넘은 임대를 **unknown으로 종결**한다. 재임대가 아니다(계약 §3).

    왜 unknown인가: 임대는 됐는데 보고가 없으면 «POST가 나갔는지 모른다». 「안 나갔다」로
    가정해 재시도하면 같은 확인을 두 번 누르게 되고, 그 결과가 무엇인지는 `[미상]`이다.
    """
    C = CoupangRocketInvoiceConfirm
    cutoff = kst_now() - timedelta(minutes=LEASE_TTL_MINUTES)
    rows = (
        db.query(C)
        .filter(C.state == "claimed", C.claimed_at.isnot(None), C.claimed_at < cutoff)
        .all()
    )
    for r in rows:
        r.state = "unknown"
        r.finished_at = kst_now()
        r.error = (
            f"임대 TTL {LEASE_TTL_MINUTES}분 초과 — 페처 보고 없음. "
            "요청이 supplier에 갔는지 확인되지 않았습니다(재시도하지 않습니다)."
        )
        log.warning("[rocket-confirm] #%d TTL 초과 → unknown (PO %s)", r.id, r.purchase_order_seq)
    if rows:
        db.commit()
    return len(rows)


def pending_status(db: Session, vendor_id: str | None) -> dict:
    """페처 폴링용 — 대기 중인 확인 명령이 있는가. 가벼운 GET(창 안 뜸)."""
    expire_stale_claims(db)
    C = CoupangRocketInvoiceConfirm
    q = db.query(C).filter(C.state == "pending")
    if vendor_id is not None:
        q = q.filter(C.vendor_id == vendor_id)
    n = q.count()
    return {"pending": n, "requested": n > 0}


def claim_next(db: Session, vendor_id: str | None) -> dict:
    """대기 명령 1건을 임대. **한 번에 1건**(배치 금지 — 계약 §3).

    ★임대는 1회뿐이다. 실패해도 반납하지 않는다 — 반납은 곧 재시도이기 때문이다.
    """
    expire_stale_claims(db)
    C = CoupangRocketInvoiceConfirm
    q = db.query(C).filter(C.state == "pending")
    if vendor_id is not None:
        q = q.filter(C.vendor_id == vendor_id)
    row = q.order_by(C.requested_at.asc(), C.id.asc()).first()
    if row is None:
        return {"claimed": False}
    lease = uuid.uuid4().hex[:32]
    # 원자적 전이 — 두 페처가 동시에 폴해도 하나만 가져간다.
    n = (
        db.query(C)
        .filter(C.id == row.id, C.state == "pending")
        .update(
            {"state": "claimed", "lease": lease, "claimed_at": kst_now()},
            synchronize_session=False,
        )
    )
    db.commit()
    if n != 1:
        return {"claimed": False}
    log.warning(
        "[rocket-confirm] #%d 임대 — PO %s (lease %s). 사전 GET 통과 시에만 POST.",
        row.id, row.purchase_order_seq, lease,
    )
    return {
        "claimed": True,
        "command_id": row.id,
        "purchase_order_seq": row.purchase_order_seq,
        "lease": lease,
        "confirm_path": f"{CONFIRM_PATH}?purchaseOrderSeq={row.purchase_order_seq}",
        "lease_ttl_minutes": LEASE_TTL_MINUTES,
    }


def report_result(
    db: Session,
    *,
    lease: str,
    precheck: str | None = None,
    precheck_http_status: int | None = None,
    http_status: int | None = None,
    response_body: str | None = None,
    error: str | None = None,
) -> dict:
    """페처 보고 → 종결 상태 확정. **상태 판정은 백엔드가 한다**(페처는 관측값만 보고).

    판정 규칙(계약 §2·§4):
      · precheck == "button_absent"                  → already_confirmed (POST 안 보냄)
      · precheck == "fetch_failed"                   → unknown (POST 안 보냄, 그래도 잠근다)
      · http 200 ∧ body의 success == true            → succeeded
      · http 200 ∧ body의 success == false           → failed  («안 일어났다»가 확정된 경우만)
      · 그 외(비200·JSON 판독 불능·본문 없음)         → unknown
    ★unknown을 failed로 접지 않는 것이 이 함수의 요점이다 — 접으면 「안 됐으니 다시」로 읽히고,
      그게 곧 두 번 누르는 경로다.
    """
    C = CoupangRocketInvoiceConfirm
    row = db.query(C).filter(C.lease == lease).first()
    if row is None:
        # stale 보고 차단 — 내 임대에 대해서만 보고할 수 있다.
        # ★그래도 원문은 로그에 남긴다: 되돌릴 수 없는 쓰기의 관측을 통째로 버리지 않는다.
        log.error(
            "[rocket-confirm] 알 수 없는 lease의 보고 — 폐기하지 않고 로그에 남긴다: "
            "precheck=%s http=%s body=%.500s",
            precheck, http_status, response_body or "",
        )
        return {"accepted": False, "recorded": False,
                "reason": "알 수 없는 lease(만료됐거나 없는 명령)"}
    if row.state != "claimed":
        # ★★지각 보고(적대 리뷰 1R P1-2). TTL이 먼저 지나 unknown으로 닫힌 뒤에 페처가
        #   실제 결과를 들고 오는 경로다 — Mac sleep/wake 앞에서 TTL 20분은 짧아 **정상적으로
        #   발생한다.** 구판은 이걸 통째로 버려서 세 가지가 동시에 깨졌다: ①`{"success":true}`
        #   원문이 어디에도 안 남고(§3 금지선: 원문 보존 없이 완료 처리 금지) ②감사 레코드가
        #   「페처 보고 없음」이라고 **거짓말**하고 ③페처는 200을 받아 성공으로 오인했다.
        #   ⇒ **상태는 바꾸지 않는다**(재시도 금지·잠금 유지). 관측만 덧붙인다.
        stamp = kst_now().strftime("%Y-%m-%d %H:%M:%S")
        late = (
            f"\n[지각 보고 {stamp} KST] precheck={precheck} http={http_status}\n"
            f"{(response_body or '')[:_BODY_MAX]}"
        )
        row.response_body = ((row.response_body or "") + late)[: _BODY_MAX * 2]
        row.error = (
            (row.error or "") + f" / ★지각 보고 도착({stamp} KST) — 상태 {row.state} 유지, 원문 보존"
        )[:500]
        db.commit()
        log.error(
            "[rocket-confirm] #%d 지각 보고 — 상태 %s 유지, 응답 원문만 보존(PO %s · http=%s)",
            row.id, row.state, row.purchase_order_seq, http_status,
        )
        return {"accepted": False, "recorded": True,
                "reason": f"이미 종결된 명령입니다({row.state})"}

    body = (response_body or "")[:_BODY_MAX] or None
    state, note = _judge(precheck, http_status, response_body)

    row.precheck = precheck
    row.precheck_http_status = precheck_http_status
    row.http_status = http_status
    row.response_body = body
    row.state = state
    row.finished_at = kst_now()
    row.error = (error or note or None) and str(error or note)[:500]
    db.commit()
    log.warning(
        "[rocket-confirm] #%d 종결 → %s (PO %s · precheck=%s · http=%s)",
        row.id, state, row.purchase_order_seq, precheck, http_status,
    )
    return {"accepted": True, "command_id": row.id, "state": state}


def _judge(precheck: str | None, http_status: int | None, body: str | None) -> tuple[str, str | None]:
    """관측값 → 종결 상태. 순수 함수(테스트가 여기를 직접 겨눈다)."""
    if precheck == "button_absent":
        return "already_confirmed", (
            "사전 GET에 확인 버튼이 없었습니다 — 이미 처리된 건입니다. POST를 보내지 않았습니다."
        )
    if precheck == "fetch_failed":
        return "unknown", (
            "사전 GET 실패로 «지금 누를 수 있는가»를 확인하지 못했습니다. POST를 보내지 않았습니다."
        )
    if http_status != 200:
        return "unknown", f"응답 코드 {http_status if http_status is not None else '없음'} — 결과를 판정할 수 없습니다."
    ok = _success_flag(body)
    if ok is True:
        return "succeeded", None
    if ok is False:
        return "failed", "supplier가 success=false로 응답했습니다(요청은 반영되지 않았습니다)."
    return "unknown", (
        "응답 본문에서 success를 읽지 못했습니다 — 반영 여부를 판정할 수 없습니다. "
        "원문은 감사 레코드에 보존돼 있습니다."
    )


def _success_flag(body: str | None) -> bool | None:
    """응답 본문의 `success` 판독. 못 읽으면 None — **None을 False로 접지 않는다**(원칙22)."""
    if not body:
        return None
    import json

    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001 — supplier가 JSON이 아닌 것을 줄 수 있다(에러 페이지 등)
        return None
    if isinstance(data, dict) and isinstance(data.get("success"), bool):
        return bool(data["success"])
    return None


# ──────────────────────────────────────────────
# ③ 화면이 쓰는 입구 — 행 상태 / 이력
# ──────────────────────────────────────────────
def confirm_states(db: Session, seqs: list[int], vendor_id: str | None) -> dict[int, dict]:
    """RI 큐 행마다 「버튼을 띄울 수 있는가」와 그 근거.

    ★화면이 «왜 못 누르는지»를 말할 수 있어야 한다 — 버튼만 사라지면 사람은 이유를 모른다.
    """
    expire_stale_claims(db)
    hist_map = _rows_for(db, seqs)
    # ★굳음 판정을 여기서 «같이» 한다(적대 리뷰 1R P2-1). 전엔 라우터가 응답을 덧칠해
    #   고쳤는데, 그러면 방어가 서비스·라우터·화면 3겹이 되고 **어느 것도 테스트가 없었다**
    #   (변이 D11·D12가 각각 단독으로 살아남았다). 판정은 `_gate`와 **같은 근거**를 쓴다.
    last_day = _last_collection_day(db, vendor_id)
    po_map: dict[int, CoupangRocketPurchaseOrder] = {}
    if seqs:
        q = db.query(CoupangRocketPurchaseOrder).filter(
            CoupangRocketPurchaseOrder.purchase_order_seq.in_(sorted(set(seqs)))
        )
        if vendor_id is not None:
            q = q.filter(CoupangRocketPurchaseOrder.vendor_id == vendor_id)
        po_map = {p.purchase_order_seq: p for p in q.all()}

    out: dict[int, dict] = {}
    for seq in seqs:
        hist = hist_map.get(seq, [])
        po = po_map.get(seq)
        last = hist[0] if hist else None
        open_cmd = _open_command(hist)
        unref = _unreflected_terminal(hist, po)
        if open_cmd is not None:
            out[seq] = {
                "state": open_cmd.state,
                "command_id": open_cmd.id,
                "can_request": False,
                "blocked_reason": "진행 중",
            }
        elif unref is not None:
            out[seq] = {
                "state": unref.state,
                "command_id": unref.id,
                "can_request": False,
                "blocked_reason": _BLOCK_REASON[unref.state],
            }
        elif last_day is not None and _kst_naive_date_str(
            po.synced_at if po is not None else None
        ) != last_day:
            # 굳은 원장은 «마지막으로 본 상태»다 — 실행 근거가 못 된다(`_gate`와 같은 판정).
            out[seq] = {
                "state": last.state if last else None,
                "command_id": last.id if last else None,
                "can_request": False,
                "blocked_reason": "재수집 후 확인 가능",
            }
        else:
            out[seq] = {
                "state": last.state if last else None,
                "command_id": last.id if last else None,
                "can_request": True,
                "blocked_reason": None,
            }
        if last is not None:
            out[seq]["last_finished_at"] = (
                last.finished_at.isoformat(sep=" ", timespec="minutes")
                if last.finished_at else None
            )
            out[seq]["last_error"] = last.error
    return out


def recent_history(db: Session, vendor_id: str | None, limit: int = 50) -> dict:
    """탭 안 실행 이력 — 감사 레코드가 DB에만 있고 화면에 없으면 미달이다(계약 §4 S2)."""
    expire_stale_claims(db)
    C = CoupangRocketInvoiceConfirm
    q = db.query(C)
    if vendor_id is not None:
        q = q.filter(C.vendor_id == vendor_id)
    rows = q.order_by(C.requested_at.desc(), C.id.desc()).limit(limit).all()
    return {
        "rows": [
            {
                "command_id": r.id,
                "purchase_order_seq": r.purchase_order_seq,
                "state": r.state,
                "requested_at": r.requested_at.isoformat(sep=" ", timespec="seconds"),
                "received_amount_at_request": r.received_amount_at_request,
                "precheck": r.precheck,
                "http_status": r.http_status,
                # ★본문 «유무»를 화면이 말한다. 원문 전체를 목록에 싣지 않는 이유는 길이지,
                #   버려서가 아니다 — 원문은 감사 레코드에 그대로 있다.
                "has_response_body": bool(r.response_body),
                "response_excerpt": (r.response_body or "")[:200] or None,
                "finished_at": (
                    r.finished_at.isoformat(sep=" ", timespec="seconds") if r.finished_at else None
                ),
                "error": r.error,
            }
            for r in rows
        ],
        "total": q.count(),
        "limit": limit,
        "lease_ttl_minutes": LEASE_TTL_MINUTES,
    }
