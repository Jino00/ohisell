# test_scheduler_rg_settlement_green_while_dead.py — P5(green-while-dead 크론 수리)
#
# ★존재 이유(2026-07-27 WING2 조사): `sync_coupang_rg_settlement_job`/`auto_download_rg_settlement_job`은
#   SA가 fail-soft로 돌려주는 {"status": "auth_error", ...} 같은 dict를 log.info로만 찍고 삼켰다 —
#   잡 자체는 예외를 던지지 않으므로 APScheduler 리스너가 EVENT_JOB_EXECUTED로 기록해 잡이
#   "50일간 ok"로 green이었다(실제로는 죽은 서버측 쿠키 경로, 양 계정 red). 이 파일은 SA 결과의
#   status가 실패를 뜻하면 잡 자체가 예외를 던져(다른 쿠팡 RG 잡들의 `_coupang_failed` 관례와
#   동일하게) EVENT_JOB_ERROR로 표면화되는지 고정한다.
from __future__ import annotations

import pytest

from app.services import scheduler_service
from app.services.coupang import rg_settlement_sync


class _FakeSession:
    """실제 DB를 건드리지 않는 세션 스텁 — SA가 몽키패치돼 db를 실제로 쓰지 않는다."""

    def close(self) -> None:
        return None


@pytest.fixture()
def fake_db(monkeypatch):
    monkeypatch.setattr(scheduler_service, "SessionLocal", lambda: _FakeSession())


# ── ① sync_coupang_rg_settlement_job: status != ok → raise ──────────────
def test_rg_settlement_job_raises_when_one_account_auth_error(monkeypatch, fake_db):
    def fake_sync(db, account_key):
        if account_key == "COUPANG_WING2":
            return {"synced": 0, "account_key": account_key, "period": "2026-07-01~2026-07-27",
                     "status": "auth_error", "error": "cookie expired"}
        return {"synced": 3, "account_key": account_key, "period": "2026-07-01~2026-07-27",
                 "status": "ok"}

    monkeypatch.setattr(rg_settlement_sync, "sync_rg_settlement", fake_sync)

    with pytest.raises(Exception):
        scheduler_service.sync_coupang_rg_settlement_job()


def test_rg_settlement_job_raises_on_read_error_and_parse_error(monkeypatch, fake_db):
    """auth_error뿐 아니라 read_error·parse_error(SA가 실제로 돌려주는 나머지 실패값)도 실패."""
    calls = {"n": 0}

    def fake_sync(db, account_key):
        calls["n"] += 1
        status = "read_error" if calls["n"] == 1 else "parse_error"
        return {"synced": 0, "account_key": account_key, "period": "x", "status": status,
                "error": "boom"}

    monkeypatch.setattr(rg_settlement_sync, "sync_rg_settlement", fake_sync)

    with pytest.raises(Exception):
        scheduler_service.sync_coupang_rg_settlement_job()


def test_rg_settlement_job_does_not_raise_when_all_ok(monkeypatch, fake_db):
    def fake_sync(db, account_key):
        return {"synced": 2, "account_key": account_key, "period": "x", "status": "ok"}

    monkeypatch.setattr(rg_settlement_sync, "sync_rg_settlement", fake_sync)

    scheduler_service.sync_coupang_rg_settlement_job()  # 예외 없이 완주해야 한다


# ── ② auto_download_rg_settlement_job: 같은 패턴 — 단 no_periods는 정상 무동작 ──
def test_auto_download_job_raises_when_one_account_auth_error(monkeypatch, fake_db):
    def fake_auto(db, vendor_id_map):
        return [
            {"account_key": "COUPANG_WING1", "requested": 0, "completed": 0, "ingested": 0,
             "errors": ["세션 만료"], "status": "auth_error"},
            {"account_key": "COUPANG_WING2", "requested": 1, "completed": 1, "ingested": 1,
             "errors": [], "status": "ok"},
        ]

    monkeypatch.setattr(rg_settlement_sync, "auto_download_all", fake_auto)

    with pytest.raises(Exception):
        scheduler_service.auto_download_rg_settlement_job()


def test_auto_download_job_raises_on_all_failed_and_partial(monkeypatch, fake_db):
    calls = {"n": 0}

    def fake_auto(db, vendor_id_map):
        calls["n"] += 1
        status = "all_failed" if calls["n"] == 1 else "partial"
        return [{"account_key": "COUPANG_WING1", "requested": 1, "completed": 0, "ingested": 0,
                 "errors": ["boom"], "status": status}]

    monkeypatch.setattr(rg_settlement_sync, "auto_download_all", fake_auto)

    with pytest.raises(Exception):
        scheduler_service.auto_download_rg_settlement_job()
    with pytest.raises(Exception):
        scheduler_service.auto_download_rg_settlement_job()


def test_auto_download_job_no_periods_is_benign_not_a_failure(monkeypatch, fake_db):
    """status/api가 아직 정산 기간을 못 채운 정상 상태(no_periods)는 실패가 아니다 — 매일 시끄러운
    거짓 실패를 만들지 않기 위해 ok와 함께 '정상'으로 취급한다(auth_error/all_failed/partial/failed만 실패)."""
    def fake_auto(db, vendor_id_map):
        return [{"account_key": "COUPANG_WING1", "requested": 0, "completed": 0, "ingested": 0,
                 "errors": [], "status": "no_periods"}]

    monkeypatch.setattr(rg_settlement_sync, "auto_download_all", fake_auto)

    scheduler_service.auto_download_rg_settlement_job()  # 예외 없이 완주해야 한다


def test_auto_download_job_does_not_raise_when_all_ok(monkeypatch, fake_db):
    def fake_auto(db, vendor_id_map):
        return [
            {"account_key": "COUPANG_WING1", "requested": 1, "completed": 1, "ingested": 1,
             "errors": [], "status": "ok"},
            {"account_key": "COUPANG_WING2", "requested": 0, "completed": 0, "ingested": 0,
             "errors": [], "status": "no_periods"},
        ]

    monkeypatch.setattr(rg_settlement_sync, "auto_download_all", fake_auto)

    scheduler_service.auto_download_rg_settlement_job()  # 예외 없이 완주해야 한다
