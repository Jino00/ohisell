# test_rocket_invoice_confirm.py — 「거래명세서확인」(RI→CI) 실행 경로의 게이트·판정.
# 인메모리 SQLite fixture. supplier 호출 없음(이 모듈은 «결정»만 하고 실행은 Mac 페처다).
#
# 이 테스트가 지키는 계약(CONTRACT_1p_invoice_confirm_write, Jino 승인 2026-08-28 07:28 KST):
#   ① **토큰 없이 라이브 명령이 안 생긴다** — dry_run 기본 + CONFIRM_LIVE_WRITE 이중확인(§3)
#   ② **미리보기는 명령을 만들지 않는다** — 모달을 열고 닫기만 하면 명령 0건(§4 S1-3)
#   ③ **게이트 5종** — RI 아님·굳음·진행 중·결과 미상 잠금·원장에 없음은 전부 거부(§2)
#   ④ **재시도가 없다** — TTL 초과는 재임대가 아니라 unknown 종결이고, 그 뒤 잠긴다(§3)
#   ⑤ **unknown을 failed로 접지 않는다** — 「안 됐다」와 「갔는지 모른다」는 다른 사실이다(원칙22)
#   ⑥ **응답 body 원문을 보존한다** — success 불리언만 남기면 진단 재료가 사라진다(§3)
#   ⑦ **잠금 해제는 원장이 한다** — 재수집(synced_at 갱신)이 실상태를 본 뒤에만 풀린다
#
# ★★변이 표적(적대 리뷰용 — 이 파일이 죽여야 하는 것):
#   · `_gate`의 굳음 검사 제거 → test_stale_po_rejected
#   · `request_confirm`의 confirm 인자 제거 → test_live_requires_token
#   · `expire_stale_claims`를 재임대로 바꾸기 → test_ttl_expiry_locks_not_retries
#   · `_judge`의 unknown→failed 접기 → test_non_200_is_unknown_not_failed
#   · 사전 GET 게이트(button_absent) 무시 → test_button_absent_never_posts
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.clients.coupang._base import CoupangWriteValidationError
from app.database import Base
from app.models import CoupangRocketInvoiceConfirm, CoupangRocketPurchaseOrder
from app.services.coupang._write_guard import CoupangLiveWriteRejected
from app.services.coupang import rocket_invoice_confirm as svc

VID = "A01029796"
TOKEN = "CONFIRM_LIVE_WRITE"
LAST_SYNC = datetime(2026, 8, 28, 6, 40)   # KST naive — 마지막 수집
OLD_SYNC = datetime(2026, 8, 5, 16, 49)    # 굳은 PO


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _po(db, seq, status="RI", *, synced=LAST_SYNC, recv=230235):
    db.add(CoupangRocketPurchaseOrder(
        purchase_order_seq=seq, vendor_id=VID,
        sum_of_order_amount=recv, sum_of_vendor_confirmed_amount=recv,
        sum_of_receiving_amount=recv,
        order_qty=1, vendor_confirmed_qty=1, receiving_qty=1,
        purchase_order_status=status,
        po_created_at=datetime(2026, 8, 18, 3, 0),   # UTC naive
        synced_at=synced,                             # KST naive
        vendor_payment_seqs=[], sku_count=1,
    ))
    db.commit()


def _cmds(db):
    return db.query(CoupangRocketInvoiceConfirm).all()


# ──────────────────────────────────────────────
# ① 토큰 — 라이브 명령의 이중확인
# ──────────────────────────────────────────────
def test_live_requires_token(db):
    """토큰 없이는 명령이 **생기지 않는다**. 거부는 예외이고, 부작용이 0이어야 한다."""
    _po(db, 139791428)
    with pytest.raises(CoupangLiveWriteRejected):
        svc.request_confirm(db, 139791428, VID, confirm=None)
    assert _cmds(db) == []

    with pytest.raises(CoupangLiveWriteRejected):
        svc.request_confirm(db, 139791428, VID, confirm="아무거나")
    assert _cmds(db) == []


def test_live_with_token_creates_exactly_one_pending(db):
    _po(db, 139791428)
    out = svc.request_confirm(db, 139791428, VID, confirm=TOKEN, note="테스트")
    assert out["dry_run"] is False and out["executed"] is True
    rows = _cmds(db)
    assert len(rows) == 1
    r = rows[0]
    assert (r.purchase_order_seq, r.state, r.vendor_id) == (139791428, "pending", VID)
    # 요청 시점 금액을 남긴다 — 사후에 «무엇을 보고 눌렀나»를 재구성하는 근거.
    assert r.received_amount_at_request == 230235


# ──────────────────────────────────────────────
# ② 미리보기 — 열고 닫기만 하면 아무 일도 없다
# ──────────────────────────────────────────────
def test_preview_creates_no_command(db):
    """계약 §4 S1-3의 표면 조건. 미리보기가 명령을 만들면 모달을 여는 것만으로 돈이 움직인다."""
    _po(db, 139791428)
    out = svc.preview_confirm(db, 139791428, VID)
    assert out["dry_run"] is True
    assert out["method"] == "POST"
    assert out["path"] == "/scm/purchase/order/confirmInvoice?purchaseOrderSeq=139791428"
    # ★바디 없음이 실측이다(ref 106 §3) — 없는 바디를 지어내지 않는다.
    assert out["payload"] == {}
    assert out["irreversible"] is True
    assert _cmds(db) == []


# ──────────────────────────────────────────────
# ③ 게이트 5종
# ──────────────────────────────────────────────
def test_unknown_po_rejected(db):
    with pytest.raises(CoupangWriteValidationError):
        svc.preview_confirm(db, 999999, VID)


@pytest.mark.parametrize("status", ["CI", "PA", "RP"])
def test_non_ri_status_rejected(db, status):
    """확인 버튼은 RI에서만 뜬다(ref 106 §2: RI 3/3 · 대조군 0/3)."""
    _po(db, 140163784, status)
    with pytest.raises(CoupangWriteValidationError):
        svc.request_confirm(db, 140163784, VID, confirm=TOKEN)
    assert _cmds(db) == []


def test_stale_po_rejected(db):
    """★굳은 원장은 «지금 참인 상태»가 아니다 — 2026-08-27에 RI 12건 중 8건이 그랬다."""
    _po(db, 115340779, "RI", synced=OLD_SYNC)
    _po(db, 139791428, "RI", synced=LAST_SYNC)   # 마지막 수집일을 만들어 준다
    with pytest.raises(CoupangWriteValidationError) as e:
        svc.request_confirm(db, 115340779, VID, confirm=TOKEN)
    # ★사용자에게 나가는 문구다 — 「확인」을 쓰지 않는다(2026-08-28 Jino 지시).
    assert "지금 상태를 모릅니다" in str(e.value)
    assert "확인" not in str(e.value).replace("거래명세서확인", "")
    assert _cmds(db) == []


def test_open_command_blocks_second(db):
    """진행 중인 명령이 있으면 두 번째를 만들지 않는다 — 그게 곧 두 번 누르기다."""
    _po(db, 139791428)
    svc.request_confirm(db, 139791428, VID, confirm=TOKEN)
    with pytest.raises(CoupangWriteValidationError):
        svc.request_confirm(db, 139791428, VID, confirm=TOKEN)
    assert len(_cmds(db)) == 1


# ──────────────────────────────────────────────
# ④ 임대·TTL — 재시도가 없다
# ──────────────────────────────────────────────
def test_claim_is_single_and_atomic(db):
    _po(db, 139791428)
    svc.request_confirm(db, 139791428, VID, confirm=TOKEN)
    first = svc.claim_next(db, VID)
    assert first["claimed"] is True and first["purchase_order_seq"] == 139791428
    # 두 번째 폴은 가져갈 것이 없다(같은 명령을 두 페처가 나눠 갖지 못한다).
    assert svc.claim_next(db, VID)["claimed"] is False


def test_ttl_expiry_locks_not_retries(db):
    """★TTL 초과는 **재임대가 아니라 unknown 종결**이다. 그리고 그 뒤로 잠긴다."""
    _po(db, 139791428)
    svc.request_confirm(db, 139791428, VID, confirm=TOKEN)
    svc.claim_next(db, VID)
    row = _cmds(db)[0]
    row.claimed_at = svc.kst_now() - timedelta(minutes=svc.LEASE_TTL_MINUTES + 1)
    db.commit()

    assert svc.expire_stale_claims(db) == 1
    assert _cmds(db)[0].state == "unknown"
    # 재임대되지 않는다.
    assert svc.claim_next(db, VID)["claimed"] is False
    # 그리고 사람이 다시 누르는 것도 막힌다 — 재수집 전까지.
    with pytest.raises(CoupangWriteValidationError) as e:
        svc.request_confirm(db, 139791428, VID, confirm=TOKEN)
    assert "결과를 모릅니다" in str(e.value)


def test_unknown_unlocks_only_after_recollection(db):
    """★잠금 해제는 이 표가 아니라 **원장**이 한다 — 재수집이 실상태를 본 뒤에만 풀린다."""
    _po(db, 139791428)
    svc.request_confirm(db, 139791428, VID, confirm=TOKEN)
    svc.claim_next(db, VID)
    svc.report_result(db, lease=_cmds(db)[0].lease, precheck="fetch_failed")
    assert _cmds(db)[0].state == "unknown"

    states = svc.confirm_states(db, [139791428], VID)
    assert states[139791428]["can_request"] is False
    assert "결과 미상" in states[139791428]["blocked_reason"]

    # 재수집이 그 PO를 다시 봤다 → 풀린다.
    po = db.query(CoupangRocketPurchaseOrder).first()
    po.synced_at = _cmds(db)[0].finished_at + timedelta(minutes=5)
    db.commit()
    assert svc.confirm_states(db, [139791428], VID)[139791428]["can_request"] is True


def _finish(db, seq, **kw):
    """명령 1건을 끝까지 돌린다(요청 → 임대 → 보고)."""
    svc.request_confirm(db, seq, VID, confirm=TOKEN)
    lease = svc.claim_next(db, VID)["lease"]
    svc.report_result(db, lease=lease, **kw)


@pytest.mark.parametrize(
    ("kw", "state", "reason_part"),
    [
        ({"precheck": "button_present", "http_status": 200,
          "response_body": '{"success": true}'}, "succeeded", "재수집 반영 대기"),
        ({"precheck": "button_absent"}, "already_confirmed", "재수집 반영 대기"),
    ],
)
def test_success_also_locks_until_recollection(db, kw, state, reason_part):
    """★적대 리뷰 1R P1-3: `succeeded` 뒤 원장의 `RI`는 **우리가 이미 틀렸다고 아는 값**이다.

    굳음 검사(`_last_collection_day` 기준)는 「수집일이 밀렸나」만 재므로 이 경우를 원리적으로
    못 잡는다 — 그래서 잠금을 unknown에만 걸면 같은 PO에 두 번째 라이브 명령이 만들어진다.
    """
    _po(db, 139791428)
    _finish(db, 139791428, **kw)
    assert _cmds(db)[0].state == state

    st = svc.confirm_states(db, [139791428], VID)[139791428]
    assert st["can_request"] is False
    assert reason_part in st["blocked_reason"]
    # API 층도 닫혀 있어야 한다 — 화면만 막으면 「누르고 나서 에러를 보는」 표면이 된다.
    with pytest.raises(CoupangWriteValidationError):
        svc.request_confirm(db, 139791428, VID, confirm=TOKEN)
    assert len(_cmds(db)) == 1

    # 재수집이 실상태를 본 뒤에만 풀린다.
    po = db.query(CoupangRocketPurchaseOrder).first()
    po.synced_at = _cmds(db)[0].finished_at + timedelta(minutes=5)
    db.commit()
    assert svc.confirm_states(db, [139791428], VID)[139791428]["can_request"] is True


def test_failed_does_not_lock(db):
    """★`failed`(success=false)만 예외다 — 아무 일도 안 일어났음이 **확인된** 유일한 상태라
    원장이 여전히 맞다. 여기까지 잠그면 고칠 수 있는 실패가 재수집을 기다리게 된다."""
    _po(db, 139791428)
    _finish(db, 139791428, precheck="button_present", http_status=200,
            response_body='{"success": false}')
    assert _cmds(db)[0].state == "failed"
    assert svc.confirm_states(db, [139791428], VID)[139791428]["can_request"] is True


def test_late_report_preserves_body_and_keeps_state(db):
    """★적대 리뷰 1R P1-2: TTL이 먼저 지난 뒤 도착한 보고를 통째로 버리면
    ①응답 원문이 사라지고 ②감사 레코드가 「보고 없음」이라 거짓말한다."""
    _po(db, 139791428)
    svc.request_confirm(db, 139791428, VID, confirm=TOKEN)
    lease = svc.claim_next(db, VID)["lease"]
    row = _cmds(db)[0]
    row.claimed_at = svc.kst_now() - timedelta(minutes=svc.LEASE_TTL_MINUTES + 1)
    db.commit()
    svc.expire_stale_claims(db)
    assert _cmds(db)[0].state == "unknown"

    out = svc.report_result(db, lease=lease, precheck="button_present",
                            http_status=200, response_body='{"success":true}')
    assert out["accepted"] is False        # 상태는 안 바꾼다(재시도 금지·잠금 유지)
    assert out["recorded"] is True         # ★그러나 관측은 남는다
    row = _cmds(db)[0]
    assert row.state == "unknown"
    assert '{"success":true}' in (row.response_body or "")
    assert "지각 보고" in (row.error or "")


def test_unknown_lease_report_is_not_silently_dropped(db):
    """lease를 못 찾아도 «기록 안 됨»을 호출자에게 알린다 — 페처가 성공으로 오인하지 않게."""
    out = svc.report_result(db, lease="없는lease", precheck="button_present",
                            http_status=200, response_body='{"success":true}')
    assert out["accepted"] is False and out["recorded"] is False


# ──────────────────────────────────────────────
# ⑤ 판정 — unknown을 failed로 접지 않는다
# ──────────────────────────────────────────────
@pytest.mark.parametrize(
    ("precheck", "http", "body", "expect"),
    [
        # 사전 GET 게이트: 버튼이 없으면 POST를 안 보냈다 = 이미 처리됨
        ("button_absent", None, None, "already_confirmed"),
        # 사전 GET 실패는 「버튼 없음」이 아니다 — 아무것도 확인 못 했다
        ("fetch_failed", None, None, "unknown"),
        ("button_present", 200, '{"success": true}', "succeeded"),
        ("button_present", 200, '{"success": false}', "failed"),
        # ★비200·판독 불능·빈 본문은 전부 unknown. failed로 접으면 「다시 누르면 된다」로 읽힌다.
        ("button_present", 500, "Internal Server Error", "unknown"),
        ("button_present", 200, "<html>login</html>", "unknown"),
        ("button_present", 200, "", "unknown"),
        ("button_present", None, None, "unknown"),
        # success가 불리언이 아니면 못 읽은 것 — 참으로 접지 않는다
        ("button_present", 200, '{"success": "true"}', "unknown"),
    ],
)
def test_judge_matrix(precheck, http, body, expect):
    state, _note = svc._judge(precheck, http, body)
    assert state == expect


def test_non_200_is_unknown_not_failed(db):
    """계약 §2의 핵심 한 줄 — 「모른다」를 「안 됐다」로 바꾸면 그게 재시도 경로가 된다."""
    _po(db, 139791428)
    svc.request_confirm(db, 139791428, VID, confirm=TOKEN)
    svc.claim_next(db, VID)
    svc.report_result(
        db, lease=_cmds(db)[0].lease, precheck="button_present",
        http_status=502, response_body="<html>Bad Gateway</html>",
    )
    assert _cmds(db)[0].state == "unknown"


def test_button_absent_never_posts(db):
    """★사전 GET 게이트가 산 증거 — 버튼이 없었으면 http_status가 남지 않는다."""
    _po(db, 139791428)
    svc.request_confirm(db, 139791428, VID, confirm=TOKEN)
    svc.claim_next(db, VID)
    svc.report_result(db, lease=_cmds(db)[0].lease, precheck="button_absent",
                      precheck_http_status=200)
    row = _cmds(db)[0]
    assert row.state == "already_confirmed"
    assert row.http_status is None and row.response_body is None


# ──────────────────────────────────────────────
# ⑥ 원문 보존
# ──────────────────────────────────────────────
def test_response_body_is_preserved_verbatim(db):
    """success 불리언만 남기는 코드는 계약 §3 금지선 — supplier가 구조화 에러를 안 준다."""
    _po(db, 139791428)
    svc.request_confirm(db, 139791428, VID, confirm=TOKEN)
    svc.claim_next(db, VID)
    body = '{"success": false, "message": "이미 확인된 발주입니다"}'
    svc.report_result(db, lease=_cmds(db)[0].lease, precheck="button_present",
                      http_status=200, response_body=body)
    row = _cmds(db)[0]
    assert row.response_body == body
    assert row.state == "failed"


def test_report_rejects_unknown_or_settled_lease(db):
    """stale 보고 차단 — 내 임대에 대해서만 보고할 수 있고, 종결된 명령은 덮이지 않는다."""
    _po(db, 139791428)
    svc.request_confirm(db, 139791428, VID, confirm=TOKEN)
    lease = svc.claim_next(db, VID)["lease"]
    assert svc.report_result(db, lease="없는lease")["accepted"] is False
    assert svc.report_result(db, lease=lease, precheck="button_present",
                             http_status=200, response_body='{"success": true}')["accepted"] is True
    # 두 번째 보고는 거부 — 종결 상태를 덮어쓰지 않는다.
    again = svc.report_result(db, lease=lease, precheck="button_present", http_status=500)
    assert again["accepted"] is False
    assert _cmds(db)[0].state == "succeeded"


# ──────────────────────────────────────────────
# ⑦ 화면이 읽는 표면
# ──────────────────────────────────────────────
def test_confirm_states_blocks_stale_rows(db):
    """★적대 리뷰 1R P2-1: 굳은 행 방어가 라우터·화면에만 있고 **테스트가 0중**이었다.

    판정을 서비스로 모았으니 여기서 지킨다 — 화면이 버튼을 띄우고 나서 400을 맞는 것은
    나쁜 표면이고, 다음 리팩터가 「중복이네」 하고 한 겹을 지워도 이 테스트가 막는다.
    """
    _po(db, 115340779, "RI", synced=OLD_SYNC)   # 굳음
    _po(db, 139791428, "RI", synced=LAST_SYNC)  # 신선(마지막 수집일을 만든다)
    states = svc.confirm_states(db, [115340779, 139791428], VID)
    assert states[115340779]["can_request"] is False
    assert states[115340779]["blocked_reason"] == "재수집 후 확인 가능"
    assert states[139791428]["can_request"] is True


def test_confirm_states_explains_why_blocked(db):
    """버튼만 사라지면 사람은 이유를 모른다 — 사유가 같이 나와야 한다."""
    _po(db, 139791428)
    assert svc.confirm_states(db, [139791428], VID)[139791428] == {
        "state": None, "command_id": None, "can_request": True, "blocked_reason": None,
    }
    svc.request_confirm(db, 139791428, VID, confirm=TOKEN)
    st = svc.confirm_states(db, [139791428], VID)[139791428]
    assert st["can_request"] is False and st["blocked_reason"] == "진행 중"


def test_history_surfaces_body_presence(db):
    """이력이 화면에 나가는 표면. 감사 레코드가 DB에만 있고 화면에 없으면 미달이다(§4 S2)."""
    _po(db, 139791428)
    svc.request_confirm(db, 139791428, VID, confirm=TOKEN)
    svc.claim_next(db, VID)
    svc.report_result(db, lease=_cmds(db)[0].lease, precheck="button_present",
                      http_status=200, response_body='{"success": true}')
    hist = svc.recent_history(db, VID)
    assert hist["total"] == 1
    row = hist["rows"][0]
    assert row["state"] == "succeeded"
    assert row["has_response_body"] is True
    assert row["purchase_order_seq"] == 139791428
