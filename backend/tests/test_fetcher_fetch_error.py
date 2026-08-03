# test_fetcher_fetch_error.py — 나머지 Mac 페처 4종의 실패 보고 경로.
#   ad_cost(오픽스 광고)는 PR #30에서 먼저 고쳤고(test_ad_cost_fetch_error.py), 같은 구멍을
#   공유하던 나머지가 여기다: Wing 판매분석·RG 정산·오하이테크 광고·로켓 발주/정산.
#
#   ★구멍(PR #30과 동일): 페처가 갱신 요청을 claim한 뒤(=플래그 이미 clear) 죽으면 prod에
#   아무 흔적도 안 남는다. 성공에만 시각(last_success_at)이 있고 실패엔 짝이 없어서, UI는
#   "실패"와 "아직 진행 중"을 구분할 수단이 아예 없다 — 215초를 헛기다린 뒤 "Mac 응답 없음"
#   이라는 뭉뚱그린 문구만 낸다(진짜 원인은 화면 어디에도 안 나옴).
#
#   4종 전부 상태행이 coupang_wing_cookie 한 테이블에 산다(account_key로만 구분) → PR #30이
#   추가한 last_error_at 컬럼을 그대로 쓴다(신규 마이그 없음).
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import CoupangWingCookie
from app.services.coupang import (
    refresh_contract,
    ohitech_ad_sync,
    rg_settlement_sync,
    rocket_supplier_sync,
    vendor_summary_sync,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    yield s
    s.close()


# (이름, account_key, mark_fetch_error, mark_success, refresh_status) — 4종을 같은 계약으로 검증.
# 계정 키·함수는 SA에서 그대로 가져온다(하드코딩하면 SA와 조용히 어긋나 상태행을 못 찾는다).
_FETCHERS = [
    pytest.param(
        vendor_summary_sync._VS_ACCOUNT,
        vendor_summary_sync.mark_fetch_error,
        vendor_summary_sync._mark_heartbeat,
        vendor_summary_sync.refresh_status,
        id="wing_vendor_summary",
    ),
    pytest.param(
        rg_settlement_sync._RG_STATE_ACCOUNT,
        rg_settlement_sync.rg_mark_fetch_error,
        rg_settlement_sync.rg_mark_heartbeat,
        rg_settlement_sync.rg_refresh_status,
        id="rg_settlement",
    ),
    pytest.param(
        ohitech_ad_sync._OHITECH_AD_ACCOUNT,
        ohitech_ad_sync.mark_fetch_error,
        ohitech_ad_sync.mark_fetch_success,
        ohitech_ad_sync.refresh_status,
        id="ohitech_ad",
    ),
    pytest.param(
        rocket_supplier_sync._ROCKET_ACCOUNT,
        rocket_supplier_sync.mark_rocket_fetch_error,
        rocket_supplier_sync.mark_rocket_fetch_success,
        rocket_supplier_sync.rocket_refresh_status,
        id="rocket_supplier",
    ),
]


def _seed(db, account_key: str, *, status: str = "unknown") -> CoupangWingCookie:
    row = CoupangWingCookie(account_key=account_key, status=status)
    db.add(row)
    db.commit()
    return row


def _row(db, account_key: str) -> CoupangWingCookie:
    db.expire_all()
    return db.query(CoupangWingCookie).filter_by(account_key=account_key).one()


@pytest.mark.parametrize("account,mark_error,_mark_ok,_status", _FETCHERS)
def test_mark_fetch_error_records_message_and_time(db, account, mark_error, _mark_ok, _status):
    """페처 실패 보고 → last_error·last_error_at 기록. (실사고 메시지 그대로)"""
    _seed(db, account)
    mark_error(db, "browser: Target page, context or browser has been closed")

    row = _row(db, account)
    assert "browser" in (row.last_error or "")
    assert row.last_error_at is not None  # ← 짝이 없어 실패/진행중을 못 가르던 뿌리


@pytest.mark.parametrize("account,mark_error,_mark_ok,_status", _FETCHERS)
def test_mark_fetch_error_does_not_touch_cookie_status(db, account, mark_error, _mark_ok, _status):
    """★페처 실패는 status를 red로 만들지 않는다(PR #30 codex 1R[P2]와 동일 계약).

    red면 Layout 배너가 "쿠키 만료(재설정 필요)" + 재설정 CTA를 띄운다(Layout.tsx:201/206).
    브라우저 크래시는 쿠키 문제가 아니라 재설정해도 헛수고 — 07-17에 실제로 그 헛수고를 했다.
    지속 실패는 워치독이 last_success_at 경과로 잡는다(status 미의존).
    """
    _seed(db, account, status="unknown")
    mark_error(db, "browser: Target page has been closed")
    assert _row(db, account).status == "unknown"  # seed 그대로, red 아님

    row = _row(db, account)
    row.status = "green"
    db.commit()
    mark_error(db, "browser: closed again")
    assert _row(db, account).status == "green"  # 어떤 값이든 보존


@pytest.mark.parametrize("account,mark_error,_mark_ok,_status", _FETCHERS)
def test_mark_fetch_error_creates_state_row_if_missing(db, account, mark_error, _mark_ok, _status):
    """상태행이 아직 없어도(=성공 이력 0) 실패는 기록된다 — 첫 run이 죽는 경우가 정확히 그렇다."""
    mark_error(db, "boom")

    row = _row(db, account)
    assert row.last_error == "boom"
    assert row.last_error_at is not None


@pytest.mark.parametrize("account,mark_error,_mark_ok,_status", _FETCHERS)
def test_mark_fetch_error_truncates_to_column_limit(db, account, mark_error, _mark_ok, _status):
    """긴 스택트레이스가 와도 컬럼 한계(300)에서 자른다 — 저장 실패로 보고 자체가 날아가면 안 된다."""
    _seed(db, account)
    mark_error(db, "x" * 5000)
    assert len(_row(db, account).last_error) == 300


@pytest.mark.parametrize("account,_mark_error,mark_ok,_status", _FETCHERS)
def test_success_clears_error_trace(db, account, _mark_error, mark_ok, _status):
    """성공 push는 과거 실패 흔적을 지운다 — 안 지우면 오래된 실패가 화면에 계속 남는다.

    ★살아있는 요청(=버튼이 눌린 run) 안에서 검증한다(2026-08-03 codex P1). RG만 heartbeat의
    의미가 다르기 때문이다: 나머지 3종은 heartbeat가 곧 run 완료 신호지만, RG는 한 회차가 여러
    엑셀을 올려서 heartbeat가 **중간** 신호다. 그래서 RG는 요청이 없을 때 도착한 업로드로는
    실패 흔적을 지우지 않는다 — 안 그러면 클라 타임아웃된 업로드가 run 종료 뒤 뒤늦게 완주하며
    실패를 지우고, 프론트가 반쪽 정산 run을 "완료"로 읽는다(rg_mark_heartbeat 주석 참조).
    요청이 살아있는 이 시나리오에서는 4종의 동작이 동일해야 한다.
    """
    _seed(db, account)
    refresh_contract.request_refresh(db, account)
    _mark_error(db, "browser closed")
    assert _row(db, account).last_error_at is not None

    mark_ok(db)
    row = _row(db, account)
    assert row.last_error is None
    assert row.last_error_at is None
    assert row.last_success_at is not None


@pytest.mark.parametrize("account,mark_error,_mark_ok,status_fn", _FETCHERS)
def test_refresh_status_exposes_last_error_at(db, account, mark_error, _mark_ok, status_fn):
    """폴링이 읽는 status dict에 last_error_at이 실려야 프론트가 실패를 감지한다(이게 유일 경로)."""
    _seed(db, account)
    assert status_fn(db)["last_error_at"] is None  # 실패 기록 없음

    mark_error(db, "browser closed")
    st = status_fn(db)
    assert st["last_error_at"] is not None
    assert "browser" in (st["last_error"] or "")


@pytest.mark.parametrize("account,mark_error,_mark_ok,status_fn", _FETCHERS)
def test_refresh_status_has_last_error_at_key_when_row_missing(db, account, mark_error, _mark_ok, status_fn):
    """상태행이 없을 때도 키는 존재해야 한다 — 프론트가 undefined를 baseline으로 잡으면
    첫 실패를 '변화 없음'으로 오독한다."""
    assert status_fn(db)["last_error_at"] is None
