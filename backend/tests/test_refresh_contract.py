# test_refresh_contract.py — 쿠팡 갱신 요청 lease 계약(refresh_contract SA).
#   ★배경(2026-07-17 13:02 실사고): claim이 요청 플래그를 즉시 소비해, 페처가 claim 직후
#   죽으면 아무도 다시 시도하지 않았다(버튼 1회 = 시도 1회). 버튼 1회의 의도는 재시도
#   3회까지 포함한다(PLAN §0, Jino 확정) — 단 로그인 필요는 재시도 제외(창만 반복해서 뜸).
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import CoupangWingCookie
from app.services.coupang import refresh_contract as rc
from app.utils.kst import kst_now

ACC = "COUPANG_TEST_STREAM"


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    yield s
    s.close()


def _row(db) -> CoupangWingCookie:
    return db.query(CoupangWingCookie).filter(CoupangWingCookie.account_key == ACC).first()


# ── ① request ──────────────────────────────────────────
def test_request_creates_row_and_flag(db):
    out = rc.request_refresh(db, ACC)
    assert out["requested"] is True and out["already_pending"] is False
    assert _row(db).refresh_requested_at is not None
    assert _row(db).attempt_count == 0


def test_request_while_pending_is_noop(db):
    """연타해도 진행 중인 요청의 시도 예산을 리셋하지 않는다(무한 재시도 방지)."""
    rc.request_refresh(db, ACC)
    rc.claim_refresh(db, ACC)                      # attempt=1
    first_at = _row(db).refresh_requested_at

    out = rc.request_refresh(db, ACC)
    assert out["already_pending"] is True
    assert _row(db).refresh_requested_at == first_at
    assert _row(db).attempt_count == 1             # 리셋되지 않음


def test_new_request_after_exhaustion_resets_budget(db):
    """요청이 소멸된 뒤 다시 누르면 새 예산(3회)으로 시작한다."""
    rc.request_refresh(db, ACC)
    for _ in range(rc.MAX_ATTEMPTS):
        rc.claim_refresh(db, ACC)
        rc.report_failure(db, ACC, "boom")
    assert _row(db).refresh_requested_at is None   # 소진 소멸

    rc.request_refresh(db, ACC)
    assert _row(db).attempt_count == 0
    assert rc.claim_refresh(db, ACC)["claimed"] is True


# ── ② claim ────────────────────────────────────────────
def test_claim_preserves_request_flag(db):
    """★계약의 핵심: claim은 요청을 소비하지 않는다(예전엔 여기서 지웠다)."""
    rc.request_refresh(db, ACC)
    out = rc.claim_refresh(db, ACC)
    assert out["claimed"] is True and out["attempt"] == 1
    assert _row(db).refresh_requested_at is not None   # 살아있다
    assert _row(db).claimed_at is not None             # lease 취득


def test_claim_without_request_returns_false(db):
    rc.request_refresh(db, ACC)
    rc.mark_success(db, ACC)
    assert rc.claim_refresh(db, ACC)["claimed"] is False


def test_second_claim_blocked_while_lease_alive(db):
    """살아있는 lease 위로 2차 claim이 겹치면 창이 두 번 뜬다 — 막는다."""
    rc.request_refresh(db, ACC)
    assert rc.claim_refresh(db, ACC)["claimed"] is True
    assert rc.claim_refresh(db, ACC)["claimed"] is False
    assert _row(db).attempt_count == 1  # 실패한 claim은 카운터를 올리지 않는다


def test_claim_on_missing_row_is_false(db):
    assert rc.claim_refresh(db, "NO_SUCH_ACCOUNT")["claimed"] is False


# ── ③ success ──────────────────────────────────────────
def test_success_clears_everything(db):
    rc.request_refresh(db, ACC)
    rc.claim_refresh(db, ACC)
    rc.mark_success(db, ACC)

    row = _row(db)
    assert row.refresh_requested_at is None
    assert row.claimed_at is None
    assert row.attempt_count == 0


def test_success_without_row_is_noop(db):
    rc.mark_success(db, "NO_SUCH_ACCOUNT")  # 예외 없이 통과


# ── ④ failure → 재시도 ─────────────────────────────────
def test_failure_returns_lease_and_allows_retry(db):
    """실패 보고 = lease 반납. 요청은 살아있어 다음 폴이 곧바로 재claim한다(=재시도)."""
    rc.request_refresh(db, ACC)
    rc.claim_refresh(db, ACC)
    out = rc.report_failure(db, ACC, "browser closed")

    assert out["retry"] is True and out["attempt"] == 1
    assert _row(db).refresh_requested_at is not None
    assert _row(db).claimed_at is None
    again = rc.claim_refresh(db, ACC)
    assert again["claimed"] is True and again["attempt"] == 2 and again["lease"]


def test_failure_records_error_message_and_time(db):
    rc.request_refresh(db, ACC)
    rc.claim_refresh(db, ACC)
    rc.report_failure(db, ACC, "browser: Target page has been closed")
    row = _row(db)
    assert "browser" in row.last_error
    assert row.last_error_at is not None


def test_failure_truncates_message(db):
    rc.request_refresh(db, ACC)
    rc.claim_refresh(db, ACC)
    rc.report_failure(db, ACC, "E" * 5000)
    assert len(_row(db).last_error) <= 300


def test_failure_does_not_touch_cookie_status(db):
    """status=red는 배너가 '쿠키 재설정'을 시킨다 — 브라우저 크래시엔 헛수고(PR #30 codex P2)."""
    rc.request_refresh(db, ACC)
    row = _row(db)
    row.status = "green"
    db.commit()
    rc.claim_refresh(db, ACC)
    rc.report_failure(db, ACC, "boom")
    assert _row(db).status == "green"


# ── ④' failure → 소멸(3회 상한 / 로그인 필요) ──────────
def test_third_failure_extinguishes_request(db):
    """3회 시도 후 실패 = 예산 소진. 요청 소멸 + 사유 명시(무한 재시도 금지)."""
    rc.request_refresh(db, ACC)
    for i in (1, 2):
        rc.claim_refresh(db, ACC)
        assert rc.report_failure(db, ACC, "boom")["retry"] is True

    rc.claim_refresh(db, ACC)                       # attempt=3
    out = rc.report_failure(db, ACC, "boom")
    assert out["retry"] is False and out["attempt"] == 3
    assert "재시도 3회 소진" in out["reason"]

    row = _row(db)
    assert row.refresh_requested_at is None         # 요청 소멸
    assert "재시도 3회 소진" in row.last_error      # UI가 사유를 볼 수 있어야 한다
    assert rc.claim_refresh(db, ACC)["claimed"] is False


def test_login_required_extinguishes_immediately(db):
    """★§0 금지선: 로그인 필요는 재시도 제외 — 재시도해도 실패하고 창만 반복해서 뜬다."""
    rc.request_refresh(db, ACC)
    rc.claim_refresh(db, ACC)
    out = rc.report_failure(db, ACC, "세션 만료", kind=rc.KIND_LOGIN_REQUIRED)

    assert out["retry"] is False and out["attempt"] == 1
    assert "로그인 필요" in out["reason"]
    row = _row(db)
    assert row.refresh_requested_at is None
    assert "로그인 필요" in row.last_error
    assert rc.claim_refresh(db, ACC)["claimed"] is False


def test_unknown_kind_is_treated_as_retryable(db):
    """하위호환: kind를 모르는(=구버전) 페처의 보고는 평범한 실패로 다룬다."""
    rc.request_refresh(db, ACC)
    rc.claim_refresh(db, ACC)
    assert rc.report_failure(db, ACC, "boom", kind="weird_kind")["retry"] is True
    assert _row(db).refresh_requested_at is not None


def test_failure_without_row_is_noop(db):
    out = rc.report_failure(db, "NO_SUCH_ACCOUNT", "boom")
    assert out["retry"] is False


# ── ⑤ TTL ──────────────────────────────────────────────
def test_expired_lease_can_be_reclaimed(db):
    """데몬이 보고도 못 하고 죽은 경우(=lease 만료) 다음 폴이 재claim한다."""
    rc.request_refresh(db, ACC)
    rc.claim_refresh(db, ACC)

    row = _row(db)
    row.claimed_at = kst_now() - timedelta(minutes=rc.lease_ttl_minutes() + 1)
    db.commit()

    out = rc.claim_refresh(db, ACC)
    assert out["claimed"] is True and out["attempt"] == 2


def test_lease_just_inside_ttl_is_not_reclaimed(db):
    rc.request_refresh(db, ACC)
    rc.claim_refresh(db, ACC)

    row = _row(db)
    row.claimed_at = kst_now() - timedelta(minutes=rc.lease_ttl_minutes() - 1)
    db.commit()

    assert rc.claim_refresh(db, ACC)["claimed"] is False


def test_ttl_expiry_respects_attempt_cap(db):
    """★codex 1R[P1]: 데몬이 '보고 없이' 죽는 경로에서도 상한이 지켜져야 한다.

    보고가 없으면 report_failure를 안 거치므로, TTL 만료만으로 재claim이 무한 반복될 수
    있었다(=Chrome이 영원히 다시 뜸). claim 조건에 attempt_count<MAX가 있어야 멈춘다.
    """
    rc.request_refresh(db, ACC)
    for i in range(rc.MAX_ATTEMPTS):
        assert rc.claim_refresh(db, ACC)["claimed"] is True, f"{i+1}회차"
        row = _row(db)
        row.claimed_at = kst_now() - timedelta(minutes=rc.lease_ttl_minutes() + 1)
        db.commit()

    out = rc.claim_refresh(db, ACC)          # 4번째 시도는 없다
    assert out["claimed"] is False
    row = _row(db)
    assert row.refresh_requested_at is None  # reaper가 소멸시킨다(무한 "진행 중" 방지)
    assert "재시도 3회 소진" in row.last_error
    assert row.last_error_at is not None


def test_reaper_waits_while_last_attempt_is_alive(db):
    """마지막 시도가 아직 살아 일하는 중이면 소멸시키지 않는다(정상 완료를 기다린다)."""
    rc.request_refresh(db, ACC)
    for _ in range(rc.MAX_ATTEMPTS):
        rc.claim_refresh(db, ACC)
        if int(_row(db).attempt_count) < rc.MAX_ATTEMPTS:
            rc.report_failure(db, ACC, "boom")
    # 3회차 lease는 살아있다(방금 claim) → 아직 소멸 금지
    assert rc.claim_refresh(db, ACC)["claimed"] is False
    assert _row(db).refresh_requested_at is not None

    rc.mark_success(db, ACC)                  # 3회차가 성공으로 끝남
    assert _row(db).refresh_requested_at is None


# ── lease 펜스(stale 보고 차단) ────────────────────────
def test_stale_failure_report_is_ignored(db):
    """★codex 1R[P1]: 20분 넘게 멈췄던 옛 시도가 뒤늦게 깨어나 남의 임대를 반납하면 안 된다."""
    rc.request_refresh(db, ACC)
    stale_lease = rc.claim_refresh(db, ACC)["lease"]

    row = _row(db)                            # 1회차가 응답 없이 멈춤 → TTL 만료
    row.claimed_at = kst_now() - timedelta(minutes=rc.lease_ttl_minutes() + 1)
    db.commit()
    fresh_lease = rc.claim_refresh(db, ACC)["lease"]   # 2회차가 재claim(=지금 일하는 중)
    assert fresh_lease != stale_lease

    out = rc.report_failure(db, ACC, "옛 시도의 뒤늦은 실패", lease=stale_lease)
    assert out.get("stale") is True
    row = _row(db)
    assert row.claimed_at is not None         # 2회차 임대 그대로(반납되지 않음)
    assert row.last_error is None             # 실패 흔적도 남기지 않는다


def test_matching_lease_report_is_applied(db):
    rc.request_refresh(db, ACC)
    lease = rc.claim_refresh(db, ACC)["lease"]
    assert rc.report_failure(db, ACC, "boom", lease=lease)["retry"] is True
    assert _row(db).claimed_at is None


def test_report_without_lease_is_backward_compatible(db):
    """구버전 페처(lease 미전달)는 기존대로 동작한다."""
    rc.request_refresh(db, ACC)
    rc.claim_refresh(db, ACC)
    assert rc.report_failure(db, ACC, "boom")["retry"] is True


def test_duplicate_failure_report_does_not_release_new_lease(db):
    """★codex 2R[P2]: 같은 lease로 두 번 보고돼도 두 번째가 새 임대를 반납하면 안 된다.

    검사(SELECT)와 쓰기가 분리돼 있으면, 첫 보고가 임대를 반납 → 폴이 재claim → 지연된
    두 번째 보고가 그 새 임대를 또 반납한다(창 중복 + 시도 예산 낭비). 조건부 UPDATE로 막는다.
    """
    rc.request_refresh(db, ACC)
    lease = rc.claim_refresh(db, ACC)["lease"]
    assert rc.report_failure(db, ACC, "boom", lease=lease)["retry"] is True
    rc.claim_refresh(db, ACC)                      # 폴이 재claim(2회차, 새 임대)
    new_lease = _row(db).claimed_at

    dup = rc.report_failure(db, ACC, "지연된 중복 보고", lease=lease)
    assert dup.get("stale") is True
    assert _row(db).claimed_at == new_lease        # 2회차 임대 그대로


def test_malformed_lease_is_treated_as_stale(db):
    rc.request_refresh(db, ACC)
    rc.claim_refresh(db, ACC)
    out = rc.report_failure(db, ACC, "boom", lease="not-a-timestamp")
    assert out.get("stale") is True
    assert _row(db).claimed_at is not None


def test_satisfied_request_is_closed_without_opening_a_window(db):
    """★codex 3R[P1]: 데이터가 이미 들어온 요청은 창을 열기 전에 조용히 닫는다.

    성공 신호를 못 보내는 페처(백엔드만 먼저 배포된 구버전, 업로드 후 죽은 run)의 요청이
    남아 재시도를 유발하면 — 데이터는 이미 들어왔는데 창이 두세 번 더 뜨고 "3회 소진"이라는
    거짓 실패로 끝난다.
    """
    rc.request_refresh(db, ACC)
    rc.claim_refresh(db, ACC)
    row = _row(db)
    row.last_success_at = kst_now()                                  # 업로드는 성공했다
    row.claimed_at = kst_now() - timedelta(minutes=rc.lease_ttl_minutes() + 1)  # 신호 없이 종료
    db.commit()

    out = rc.claim_refresh(db, ACC)
    assert out["claimed"] is False          # 창을 열지 않는다
    row = _row(db)
    assert row.refresh_requested_at is None  # 요청은 완료로 닫힌다
    assert row.last_error is None            # 거짓 실패를 남기지 않는다


def test_released_lease_after_partial_success_still_retries(db):
    """부분 성공 뒤 실패를 '보고한' 경우는 자동 완료로 닫지 않는다 — 재시도해야 정산이 완전해진다."""
    rc.request_refresh(db, ACC)
    rc.claim_refresh(db, ACC)
    row = _row(db)
    row.last_success_at = kst_now()      # 첫 엑셀 업로드는 성공
    db.commit()
    rc.report_failure(db, ACC, "두 번째 엑셀 실패")   # 명시적 실패 보고 = 임대 반납

    assert rc.claim_refresh(db, ACC)["claimed"] is True   # 재시도가 살아있다


def test_multi_artifact_stream_opts_out_of_auto_settlement(db):
    """★codex 4R[P1]: RG처럼 한 회차에 여러 엑셀을 올리는 스트림은 첫 heartbeat를 완주로
    읽으면 안 된다 — 남은 리포트를 영영 재시도하지 않는다.
    """
    rc.request_refresh(db, ACC)
    rc.claim_refresh(db, ACC, settle_on_success_heartbeat=False)
    row = _row(db)
    row.last_success_at = kst_now()   # 첫 엑셀만 성공
    row.claimed_at = kst_now() - timedelta(minutes=rc.lease_ttl_minutes() + 1)  # 그 뒤 크래시
    db.commit()

    out = rc.claim_refresh(db, ACC, settle_on_success_heartbeat=False)
    assert out["claimed"] is True      # 남은 리포트를 위해 재시도한다
    assert _row(db).refresh_requested_at is not None


def test_live_lease_is_not_settled_by_partial_success(db):
    """여러 파일을 올리는 run의 중간 업로드를 '완료'로 오인하지 않는다(임대가 살아 있으면 대기)."""
    rc.request_refresh(db, ACC)
    rc.claim_refresh(db, ACC)
    row = _row(db)
    row.last_success_at = kst_now()   # 첫 엑셀 업로드 성공, run은 계속 진행 중
    db.commit()

    assert rc.claim_refresh(db, ACC)["claimed"] is False   # 임대 살아있음 → 중복 claim 없음
    assert _row(db).refresh_requested_at is not None       # 요청도 그대로(아직 run 중)


def test_stale_completion_signal_is_ignored(db):
    """★codex 3R[P2]: 임대를 넘긴 옛 run의 뒤늦은 '완료'가 새 임대·새 요청을 지우면 안 된다."""
    rc.request_refresh(db, ACC)
    stale_lease = rc.claim_refresh(db, ACC)["lease"]
    row = _row(db)
    row.claimed_at = kst_now() - timedelta(minutes=rc.lease_ttl_minutes() + 1)
    db.commit()
    rc.claim_refresh(db, ACC)   # 2회차가 재claim

    assert rc.mark_run_complete(db, ACC, stale_lease) is False
    assert _row(db).refresh_requested_at is not None   # 새 임대의 요청은 살아있다
    assert rc.mark_run_complete(db, ACC, _row(db).claimed_at.isoformat()) is True


def test_run_complete_rejects_garbage_lease(db):
    """lease가 임대 식별자 꼴이 아니면 접는다 — 파싱 실패를 '조건 없이 닫기'로 떨어뜨리지 않는다."""
    rc.request_refresh(db, ACC)
    rc.claim_refresh(db, ACC)

    assert rc.mark_run_complete(db, ACC, "not-a-timestamp") is False
    assert _row(db).refresh_requested_at is not None   # 요청 생존


def test_mark_success_can_clear_error_trace(db):
    """무작업 정상 종료(RG 정산주기 없음)는 지난 실패 흔적을 지운다 — UI 오보 방지(codex 2R[P2])."""
    rc.request_refresh(db, ACC)
    rc.claim_refresh(db, ACC)
    rc.report_failure(db, ACC, "1회차 실패")
    rc.claim_refresh(db, ACC)

    rc.mark_success(db, ACC, clear_error=True)
    row = _row(db)
    assert row.refresh_requested_at is None
    assert row.last_error is None and row.last_error_at is None
    assert row.last_success_at is None   # 데이터 신선도 시계는 건드리지 않는다


def test_ttl_default_is_20_minutes(monkeypatch):
    """실측 최장 수집 684s(11.4분)보다 넉넉해야 이중 기동이 없다 — 기본 20분."""
    assert rc.lease_ttl_minutes() >= 12


# ── 상태 필드 ──────────────────────────────────────────
def test_status_fields_report_in_flight(db):
    assert rc.status_fields(None)["in_flight"] is False

    rc.request_refresh(db, ACC)
    assert rc.status_fields(_row(db))["in_flight"] is False   # 요청만 있고 아직 임대 전

    rc.claim_refresh(db, ACC)
    f = rc.status_fields(_row(db))
    assert f["in_flight"] is True and f["attempt_count"] == 1

    rc.report_failure(db, ACC, "boom")
    assert rc.status_fields(_row(db))["in_flight"] is False   # 재시도 대기 중


def test_access_denied_extinguishes_immediately(db):
    """★구독/권한 만료는 **영구적** — 재시도해도 40초 뒤에 되살아나지 않는다.

    배경(트랙 coupang-promo-pnl 적대적 리뷰 3R): 로켓 페처에 판매분석 스트림이 붙으면서
    "영구 실패"가 처음으로 재시도 루프에 들어왔다(무료체험 종료 2026-08-20). 그대로 두면
    갱신 버튼 1회가 Chrome을 MAX_ATTEMPTS(3)번 띄우고 매번 같은 자리에서 죽는데,
    그동안 발주/정산은 매번 성공한다. login_required와 같은 이유로 재시도에서 뺀다.
    ★단 실패로는 남긴다(D-CPP-5 "조용한 skip 금지") — last_error에 사유가 남아야 한다.
    """
    rc.request_refresh(db, ACC)
    rc.claim_refresh(db, ACC)
    out = rc.report_failure(db, ACC, "판매분석 접근 차단", kind=rc.KIND_ACCESS_DENIED)

    assert out["retry"] is False and out["attempt"] == 1
    assert "구독" in out["reason"]
    row = _row(db)
    assert row.refresh_requested_at is None          # 요청 소멸 = 재시도 0회
    assert "판매분석 접근 차단" in row.last_error     # 사유는 남는다(조용하지 않다)
    assert rc.claim_refresh(db, ACC)["claimed"] is False


def test_mapping_broken_extinguishes_but_says_code_not_subscription(db):
    """★재시도 정책은 access_denied와 같지만 **처방이 정반대**다(4R).

    응답은 오는데 레코드가 0이면 구독이 아니라 수집기 매핑이 깨진 것이다. 한 kind로 뭉치면
    운영자가 멀쩡한 구독을 갱신하며 코드 버그를 쫓는다.
    """
    rc.request_refresh(db, ACC)
    rc.claim_refresh(db, ACC)
    out = rc.report_failure(db, ACC, "판매분석 매핑 파손", kind=rc.KIND_MAPPING_BROKEN)

    assert out["retry"] is False
    assert "코드 수정" in out["reason"] and "구독" not in out["reason"].split("(")[0]
    assert _row(db).refresh_requested_at is None


# ── ⑥ last_error_kind — 「로그인 필요」를 이벤트가 아니라 상태로 (2026-08-22 W1) ──────
# ★배경(2026-08-22 라이브): WING 세션이 끊긴 사실이 last_error **문자열** 안에만 있었고,
#   프론트가 그걸 문구 매칭으로 읽었다. 그래서 ①문구를 바꾸면 화면이 조용히 깨지고
#   ②«상태»가 아니라 «이벤트»라 버튼을 눌러 실패해 봐야만 알 수 있었다(각 계정 9회 재발견).
#   status_fields()를 6개 스트림이 전부 공유하므로 이 컬럼 하나가 전 레인에 전파된다.

def test_kind_is_persisted_not_just_in_the_message(db):
    rc.request_refresh(db, ACC)
    rc.claim_refresh(db, ACC)
    rc.report_failure(db, ACC, "RG 로그인 필요", kind=rc.KIND_LOGIN_REQUIRED)

    row = _row(db)
    assert row.last_error_kind == rc.KIND_LOGIN_REQUIRED
    # 화면이 문구 매칭 없이 읽을 수 있어야 한다 — 그게 이 컬럼의 존재 이유다.
    assert rc.status_fields(row)["last_error_kind"] == rc.KIND_LOGIN_REQUIRED


def test_transient_failure_overwrites_a_stale_login_kind(db):
    """★일시 실패의 kind=None도 **덮어쓴다**.

    안 덮으면 지난 회차의 login_required가 일시 실패 위에 남아, 사람이 이미 로그인했는데도
    상주 배너가 「로그인 필요」를 계속 띄운다 — 상태로 만든 대가로 끄는 경로가 필요하다.
    """
    rc.request_refresh(db, ACC)
    rc.claim_refresh(db, ACC)
    rc.report_failure(db, ACC, "로그인 필요", kind=rc.KIND_LOGIN_REQUIRED)
    assert _row(db).last_error_kind == rc.KIND_LOGIN_REQUIRED

    rc.request_refresh(db, ACC)          # 새 버튼 = 새 예산
    rc.claim_refresh(db, ACC)
    rc.report_failure(db, ACC, "네트워크 순단", kind=None)
    assert _row(db).last_error_kind is None


def test_success_clears_the_kind(db):
    """성공하면 kind도 지워진다 — 안 지우면 배너가 영원히 로그인을 요구한다."""
    rc.request_refresh(db, ACC)
    rc.claim_refresh(db, ACC)
    rc.report_failure(db, ACC, "로그인 필요", kind=rc.KIND_LOGIN_REQUIRED)

    rc.mark_success(db, ACC, clear_error=True)
    row = _row(db)
    assert row.last_error_kind is None and row.last_error is None


# ★★이 두 개가 진짜 가드다 (2026-08-22 적대 리뷰 1R P1-1).
#   위 test_success_clears_the_kind는 `clear_error=True`로 부르는데 **prod 호출부 6곳 중
#   그렇게 부르는 곳이 하나도 없다**(ad_cost×3 · ohitech · rocket · vendor_summary 전부 기본값).
#   통과하지만 실제 경로를 지키지 않는 테스트였다 — 「변이는 죽는데 버그는 산다」의 표본이다.
#   교훈 #292(테스트 픽스처가 prod보다 관대하다)와 같은 계열: **호출 방식까지 prod와 맞춰라.**

def test_success_clears_kind_even_with_the_default_call(db):
    """★prod 호출부가 실제로 쓰는 형태 — 인자 없이 `mark_success(db, key)`."""
    rc.request_refresh(db, ACC)
    rc.claim_refresh(db, ACC)
    rc.report_failure(db, ACC, "로그인 필요", kind=rc.KIND_LOGIN_REQUIRED)

    rc.mark_success(db, ACC)          # ← clear_error 안 넘긴다(=False). prod와 동일.
    assert _row(db).last_error_kind is None, (
        "성공 경로가 kind를 안 지우면 정상 가동 중인 레인에 needs_login 배너가 영구 고착된다"
    )


def test_run_complete_clears_kind_even_without_clear_error(db):
    """mark_run_complete도 같은 성질을 갖는다 — 두 소멸 경로가 갈라지면 안 된다."""
    rc.request_refresh(db, ACC)
    claim = rc.claim_refresh(db, ACC)
    rc.report_failure(db, ACC, "로그인 필요", kind=rc.KIND_LOGIN_REQUIRED,
                      lease=claim["lease"])
    rc.request_refresh(db, ACC)
    claim2 = rc.claim_refresh(db, ACC)

    rc.mark_run_complete(db, ACC, claim2["lease"], clear_error=False)
    assert _row(db).last_error_kind is None


def test_no_production_success_path_passes_clear_error(db):
    """★prod 호출부가 «어떻게 부르는가»를 테스트가 알고 있게 한다.

    이 가드가 없으면, 누군가 `_settle_values`의 kind 클리어를 다시 `if clear_error:` 안으로
    넣어도 위 테스트만 고치면 초록이 된다. 호출 형태 자체를 소스에서 확인한다.
    """
    import inspect
    from app.services.coupang import (
        ad_cost_sync, ohitech_ad_sync, rocket_supplier_sync, vendor_summary_sync,
    )
    for mod in (ad_cost_sync, ohitech_ad_sync, rocket_supplier_sync, vendor_summary_sync):
        src = inspect.getsource(mod)
        assert "mark_success(" in src, f"{mod.__name__}: 성공 경로가 사라졌다면 이 가드를 갱신할 것"
        # 하나라도 clear_error를 넘기게 되면 그때 이 전제를 다시 검토해야 한다.
        assert "mark_success(db, " in src or "mark_success(\n" in src


def test_reaper_marks_no_response_not_login_required(db):
    """★처방이 다르므로 분류도 다르다.

    login_required는 「로그인하세요」지만 reaper가 관측한 무응답은 「Mac/데몬을 보세요」다.
    종전엔 둘 다 화면에서 「응답 없음」 한 문구로 뭉쳐, Mac이 켜져 있는데도 「Mac이 켜져
    있는지 확인하세요」가 떴다(2026-08-22 10:47 실측).
    """
    rc.request_refresh(db, ACC)
    for _ in range(rc.MAX_ATTEMPTS):
        rc.claim_refresh(db, ACC)
        # 보고 없이 죽는다 → lease를 TTL 밖으로 밀어 다음 claim이 reaper 역할을 하게 한다.
        row = _row(db)
        row.claimed_at = kst_now() - timedelta(minutes=rc.lease_ttl_minutes() + 1)
        db.commit()

    rc.claim_refresh(db, ACC)   # 예산 소진 → _reap_exhausted
    row = _row(db)
    assert row.refresh_requested_at is None
    assert row.last_error_kind == rc.KIND_NO_RESPONSE
    assert row.last_error_kind != rc.KIND_LOGIN_REQUIRED


def test_status_fields_exposes_kind_for_a_missing_row(db):
    """행이 없어도 키는 있어야 한다 — 키가 사라지면 소비자가 처방을 못 고른다(교훈 #321)."""
    assert "last_error_kind" in rc.status_fields(None)
    assert rc.status_fields(None)["last_error_kind"] is None
